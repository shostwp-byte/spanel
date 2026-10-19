import { useCallback, useEffect, useRef, useState } from 'react';
import { Terminal as XTerminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

// A pasted script is queued line by line; these caps keep a stray paste of a
// whole file from flooding the helper boundary.
const MAX_PASTE_CHARS = 64 * 1024;
const MAX_QUEUED_COMMANDS = 200;

function normalizeOutput(value) {
  return String(value || '').replace(/\r?\n/g, '\r\n');
}

// xterm hands pasted text to onData as a single chunk with CR/LF separators.
// Every segment but the last was newline-terminated, so it is a complete
// command; the last one stays on the prompt and remains editable.
function splitPastedText(text) {
  const normalized = String(text || '')
    .replace(/\r\n?/g, '\n')
    .replace(/\t/g, ' ');
  // Keep newlines and printable characters; anything else would corrupt the
  // line editor or smuggle escape sequences into the command.
  const cleaned = Array.from(normalized)
    .filter(ch => ch === '\n' || (ch >= ' ' && ch.charCodeAt(0) !== 127))
    .join('');
  return cleaned.split('\n');
}

function isRunnableLine(line) {
  const trimmed = line.trim();
  return trimmed.length > 0 && !trimmed.startsWith('#');
}

function shortPath(path) {
  const parts = String(path || '').split('/').filter(Boolean);
  if (parts.length >= 2) return `${parts.at(-2)}/${parts.at(-1)}`;
  return parts.at(-1) || '~';
}

function terminalWebSocketUrl(apiBase, websiteId) {
  const base = String(apiBase || '/api');
  const path = `/terminal/ws/${websiteId}`;
  if (/^https?:\/\//i.test(base)) {
    const url = new URL(base);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.pathname = `${url.pathname.replace(/\/$/, '')}${path}`;
    url.search = '';
    url.hash = '';
    return url.toString();
  }
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${window.location.host}${base.replace(/\/$/, '')}${path}`;
}

export function Terminal({ websiteId, apiBase = '/api' }) {
  const containerRef = useRef(null);
  const termRef = useRef(null);
  const fitRef = useRef(null);
  const wsRef = useRef(null);
  const lineRef = useRef('');
  const cursorRef = useRef(0);
  const promptRef = useRef('$ ');
  const promptVisibleRef = useRef(false);
  const runningRef = useRef(false);
  const historyRef = useRef([]);
  const historyIndexRef = useRef(-1);
  // Commands waiting to run. The websocket protocol is one command at a time,
  // so a pasted script is queued here and drained on each "exit" message.
  const queueRef = useRef([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);

  const redrawLine = useCallback(() => {
    const term = termRef.current;
    if (!term) return;
    term.write('\r\x1b[K');
    term.write(promptRef.current + lineRef.current);
    const back = lineRef.current.length - cursorRef.current;
    if (back > 0) term.write('\b'.repeat(back));
  }, []);

  // Show the prompt again, restoring any text the user had typed (or the
  // trailing fragment of a paste that had no final newline).
  const writePrompt = useCallback(() => {
    if (lineRef.current) redrawLine();
    else termRef.current?.write(promptRef.current);
    promptVisibleRef.current = true;
  }, [redrawLine]);

  const rememberCommand = useCallback((command) => {
    if (!command.trim()) return;
    historyRef.current = [command, ...historyRef.current.filter(item => item !== command)].slice(0, 50);
  }, []);

  const sendCommand = useCallback((command) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    runningRef.current = true;
    promptVisibleRef.current = false;
    historyIndexRef.current = -1;
    ws.send(JSON.stringify({ type: 'input', data: command }));
  }, []);

  // Run the next queued command, echoing it so the transcript reads like a
  // real shell session. Falls back to the prompt when the queue is empty.
  const runNextQueued = useCallback(() => {
    const next = queueRef.current.shift();
    if (next === undefined) {
      writePrompt();
      return;
    }
    termRef.current?.write(`${promptRef.current}${next}\r\n`);
    sendCommand(next);
  }, [sendCommand, writePrompt]);

  const enqueueCommands = useCallback((commands) => {
    const term = termRef.current;
    if (commands.length === 0) {
      writePrompt();
      return;
    }
    if (queueRef.current.length + commands.length > MAX_QUEUED_COMMANDS) {
      term?.write(`\r\n\x1b[31mToo many pasted commands (max ${MAX_QUEUED_COMMANDS}).\x1b[0m\r\n`);
      writePrompt();
      return;
    }
    commands.forEach(rememberCommand);
    queueRef.current.push(...commands);
    if (!runningRef.current) runNextQueued();
  }, [rememberCommand, runNextQueued, writePrompt]);

  const handlePaste = useCallback((text) => {
    const term = termRef.current;
    if (text.length > MAX_PASTE_CHARS) {
      term?.write(`\r\n\x1b[31mPaste is too large (max ${MAX_PASTE_CHARS} characters).\x1b[0m\r\n`);
      writePrompt();
      return;
    }

    const segments = splitPastedText(text);
    const commands = [];
    segments.forEach((segment, index) => {
      lineRef.current = lineRef.current.slice(0, cursorRef.current) + segment + lineRef.current.slice(cursorRef.current);
      cursorRef.current += segment.length;
      // Every segment except the last was terminated by a newline, so it is a
      // complete command line.
      if (index < segments.length - 1) {
        if (isRunnableLine(lineRef.current)) commands.push(lineRef.current);
        lineRef.current = '';
        cursorRef.current = 0;
      }
    });

    if (commands.length === 0) {
      // Single-line paste: just extend the current input line.
      redrawLine();
      return;
    }
    // Clear the half-drawn input line before the queue echoes each command.
    term?.write('\r\x1b[K');
    enqueueCommands(commands);
  }, [enqueueCommands, redrawLine, writePrompt]);

  const disconnect = useCallback(() => {
    wsRef.current?.close(1000);
    wsRef.current = null;
    setConnected(false);
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    const ws = new WebSocket(terminalWebSocketUrl(apiBase, websiteId));
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setError(null);
      lineRef.current = '';
      cursorRef.current = 0;
      runningRef.current = false;
      promptVisibleRef.current = false;
      queueRef.current = [];
      termRef.current?.write('\x1b[1;32mConnected\x1b[0m\r\n');
    };

    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        termRef.current?.write(normalizeOutput(event.data));
        return;
      }

      if (msg.type === 'output') {
        termRef.current?.write(normalizeOutput(msg.data));
      } else if (msg.type === 'exit') {
        runningRef.current = false;
        if (Number(msg.code) !== 0) {
          termRef.current?.write(`\r\n\x1b[33m[exit code: ${msg.code}]\x1b[0m\r\n`);
          // Stop a pasted batch on the first failure: the following lines
          // usually assume the failed one worked (a failed `cd`, for example).
          if (queueRef.current.length) {
            const skipped = queueRef.current.length;
            queueRef.current = [];
            termRef.current?.write(`\x1b[33m[stopped: ${skipped} pasted line(s) skipped]\x1b[0m\r\n`);
          }
          writePrompt();
        } else {
          runNextQueued();
        }
      } else if (msg.type === 'cwd') {
        promptRef.current = `${shortPath(msg.data)} $ `;
        if (!runningRef.current && !promptVisibleRef.current) writePrompt();
      } else if (msg.type === 'clear') {
        termRef.current?.clear();
      } else if (msg.type === 'error') {
        termRef.current?.write(`\x1b[31m${normalizeOutput(msg.data)}\x1b[0m\r\n`);
        writePrompt();
      }
    };

    ws.onclose = (event) => {
      setConnected(false);
      wsRef.current = null;
      if (event.code !== 1000) {
        const detail = event.reason ? `${event.code}: ${event.reason}` : `code ${event.code}`;
        setError(`Disconnected (${detail})`);
        termRef.current?.write(`\r\n\x1b[31mDisconnected (${detail})\x1b[0m\r\n`);
      }
    };

    ws.onerror = () => {
      setError('Connection failed');
      setConnected(false);
    };
  }, [apiBase, websiteId, runNextQueued, writePrompt]);

  useEffect(() => {
    if (!containerRef.current) return undefined;

    const term = new XTerminal({
      cursorBlink: true,
      cursorStyle: 'block',
      fontSize: 14,
      fontFamily: 'Consolas, "Cascadia Code", "Fira Code", monospace',
      scrollback: 2000,
      theme: {
        background: '#0f1720',
        foreground: '#e6edf5',
        cursor: '#0065bb',
        selectionBackground: '#1d4c7a',
        black: '#0f1720',
        red: '#ef4444',
        green: '#22c55e',
        yellow: '#eab308',
        blue: '#60a5fa',
        magenta: '#c084fc',
        cyan: '#2dd4bf',
        white: '#e5e7eb',
        brightBlack: '#6b7280',
        brightRed: '#f87171',
        brightGreen: '#4ade80',
        brightYellow: '#facc15',
        brightBlue: '#93c5fd',
        brightMagenta: '#d8b4fe',
        brightCyan: '#5eead4',
        brightWhite: '#ffffff',
      },
    });

    const fit = new FitAddon();
    fitRef.current = fit;
    term.loadAddon(fit);
    term.open(containerRef.current);
    termRef.current = term;

    let resizeFrame = 0;
    const fitTerminal = () => {
      resizeFrame = 0;
      fit.fit();
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
      }
    };
    const scheduleFit = () => {
      if (resizeFrame) window.cancelAnimationFrame(resizeFrame);
      resizeFrame = window.requestAnimationFrame(fitTerminal);
    };
    const resizeObserver = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(scheduleFit)
      : null;

    resizeObserver?.observe(containerRef.current);
    scheduleFit();
    const onResize = scheduleFit;
    window.addEventListener('resize', onResize);

    term.onData((data) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        if (data === '\r' || data === '\n') term.write('\r\n\x1b[31mNot connected.\x1b[0m\r\n');
        return;
      }

      // Anything longer than one character that is not an escape sequence is a
      // paste (or a fast IME commit): route it through the paste handler so
      // embedded newlines become separate commands instead of literal text.
      if (data.length > 1 && !data.startsWith('\x1b')) {
        handlePaste(data);
        return;
      }

      if (data === '\r' || data === '\n') {
        const command = lineRef.current;
        term.write('\r\n');
        rememberCommand(command);
        historyIndexRef.current = -1;
        lineRef.current = '';
        cursorRef.current = 0;
        if (runningRef.current) {
          // A command is still running; queue this one instead of racing it.
          if (isRunnableLine(command)) queueRef.current.push(command);
        } else {
          sendCommand(command);
        }
        return;
      }

      if (data === '\u007f' || data === '\b') {
        if (cursorRef.current > 0) {
          lineRef.current = lineRef.current.slice(0, cursorRef.current - 1) + lineRef.current.slice(cursorRef.current);
          cursorRef.current -= 1;
          redrawLine();
        }
        return;
      }

      if (data === '\x1b[A') {
        if (historyIndexRef.current < historyRef.current.length - 1) {
          historyIndexRef.current += 1;
          lineRef.current = historyRef.current[historyIndexRef.current] || '';
          cursorRef.current = lineRef.current.length;
          redrawLine();
        }
        return;
      }

      if (data === '\x1b[B') {
        if (historyIndexRef.current > 0) {
          historyIndexRef.current -= 1;
          lineRef.current = historyRef.current[historyIndexRef.current] || '';
        } else {
          historyIndexRef.current = -1;
          lineRef.current = '';
        }
        cursorRef.current = lineRef.current.length;
        redrawLine();
        return;
      }

      if (data === '\x1b[D') {
        if (cursorRef.current > 0) {
          cursorRef.current -= 1;
          term.write('\b');
        }
        return;
      }

      if (data === '\x1b[C') {
        if (cursorRef.current < lineRef.current.length) {
          term.write(lineRef.current[cursorRef.current]);
          cursorRef.current += 1;
        }
        return;
      }

      if (data === '\x03') {
        term.write('^C\r\n');
        lineRef.current = '';
        cursorRef.current = 0;
        if (queueRef.current.length) {
          term.write(`\x1b[33m[cancelled: ${queueRef.current.length} queued line(s)]\x1b[0m\r\n`);
          queueRef.current = [];
        }
        writePrompt();
        return;
      }

      if (data >= ' ') {
        lineRef.current = lineRef.current.slice(0, cursorRef.current) + data + lineRef.current.slice(cursorRef.current);
        cursorRef.current += data.length;
        redrawLine();
      }
    });

    connect();

    return () => {
      window.removeEventListener('resize', onResize);
      resizeObserver?.disconnect();
      if (resizeFrame) window.cancelAnimationFrame(resizeFrame);
      disconnect();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [connect, disconnect, handlePaste, redrawLine, rememberCommand, sendCommand, writePrompt]);

  return (
    <div className="terminal-wrapper">
      <div className="terminal-toolbar">
        <span className="terminal-status">
          {connected ? <span className="status-connected">Connected</span> : error ? <span className="status-error">{error}</span> : <span className="status-disconnected">Disconnected</span>}
        </span>
        {connected ? (
          <button onClick={disconnect} className="terminal-btn disconnect">Disconnect</button>
        ) : (
          <button onClick={connect} className="terminal-btn connect">Connect</button>
        )}
      </div>
      <div className="terminal-container">
        <div ref={containerRef} className="terminal-fit" />
      </div>
      <style>{`
        /* A terminal stays dark in both themes; only its chrome follows the brand. */
        .terminal-wrapper { display:flex; flex-direction:column; height:100%; background:#0f1720; border:1px solid var(--border, #26313d); border-radius:8px; overflow:hidden; }
        .terminal-toolbar { display:flex; justify-content:space-between; align-items:center; padding:8px 12px; background:#182029; border-bottom:1px solid #26313d; }
        .terminal-status { font-size:13px; font-family:Consolas, monospace; }
        .status-connected { color:#4ade80; }
        .status-disconnected { color:#9fb0c0; }
        .status-error { color:#f87171; }
        .terminal-btn { padding:4px 12px; border:0; border-radius:4px; font-size:12px; cursor:pointer; }
        .terminal-btn.connect { background:#0065bb; color:white; }
        .terminal-btn.disconnect { background:#dc2626; color:white; }
        .terminal-container { flex:1; min-height:0; padding:8px; overflow:hidden; }
        .terminal-fit { width:100%; height:100%; min-height:0; overflow:hidden; }
        .terminal-fit .xterm { width:100%; height:100%; }
        .terminal-fit .xterm-viewport { overflow-y:auto !important; }
      `}</style>
    </div>
  );
}
