import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function McpPage({ request, API, isAdmin, currentUser, setNotice, mcpAddonInstalled: mcpAddonInstalledProp }) {
  const {
    loading, setLoading, setError,
    copyText, mcpAddonInstalled: mcpAddonInstalledCtx,
    mcpDraft, setMcpDraft, mcpNewToken, setMcpNewToken,
  } = useAppContext();
  const mcpAddonInstalled = mcpAddonInstalledProp ?? mcpAddonInstalledCtx;
  
  const [mcpTokens, setMcpTokens] = useState([]);

  useEffect(() => { loadMcpTokens(); }, []);

  async function loadMcpTokens() {
    const data = await request('/mcp/tokens');
    if (data) setMcpTokens(Array.isArray(data) ? data : []);
  }

  async function createMcpToken() {
    const name = (mcpDraft.name || '').trim();
    if (!name) return;
    const data = await request('/mcp/tokens', {
      method: 'POST',
      body: JSON.stringify({
        name,
        expires_in_days: Number(mcpDraft.expires_in_days || 90),
        can_write: !!mcpDraft.can_write,
      }),
    }, t('Creating token...'));
    if (data) {
      // Shown once and never again: the server keeps only a hash. It stays on
      // screen until the person dismisses it rather than disappearing on the
      // next render, because there is no way to get it back.
      setMcpNewToken(data.token);
      setMcpDraft({ name: '', expires_in_days: 90, can_write: false });
      setNotice(t('Token created. Copy it now - it is not shown again.'));
      await loadMcpTokens();
    }
  }

  async function revokeMcpToken(token) {
    if (!window.confirm(`Revoke "${token.name}"? Any assistant using it stops working immediately.`)) return;
    const data = await request(`/mcp/tokens/${token.id}`, { method: 'DELETE' }, t('Revoking...'));
    if (data) {
      setNotice(t('Token revoked.'));
      await loadMcpTokens();
    }
  }

  function renderMcpTokenRow(token, showOwner = false) {
    const revoked = !!token.revoked_at;
    const dead = revoked || token.expired;
    return <div className="backup-item" key={token.id}>
      <span>
        {token.name}
        {showOwner && <> - <strong>{token.username}</strong></>}
        <span className={`badge ${token.can_write ? '' : 'ok'}`}>
          {token.can_write ? 'can act' : 'read only'}
        </span>
        {revoked && <span className="badge">revoked</span>}
        {!revoked && token.expired && <span className="badge">expired</span>}
        <small>
          <code>{token.prefix}…</code>
          {dead ? '' : ` · expires ${new Date(token.expires_at).toLocaleDateString()}`}
          {token.last_used_at
            ? ` · last used ${new Date(token.last_used_at).toLocaleString()}`
            : ' · never used'}
        </small>
      </span>
      <div className="actions">
        {!revoked && <button className="mini danger" disabled={!!loading}
          onClick={() => revokeMcpToken(token)}><Trash2 size={14}/>{t('Revoke')}</button>}
      </div>
    </div>;
  }

  function renderMcp() {
    const endpoint = `${window.location.origin}/api/mcp`;
    const mine = mcpTokens.filter(token => token.user_id === currentUser?.id);
    const others = mcpTokens.filter(token => token.user_id !== currentUser?.id);
    const selfSigned = !window.location.protocol.startsWith('https');

    // The real token while it is still on screen, the placeholder once it has
    // been dismissed. Somebody who has just made a token wants to paste a
    // finished command, not paste one and then go hunting for the value to
    // substitute - and since the panel can never show the token again, the
    // window in which this can help is exactly the window in which it is up.
    const bearer = mcpNewToken || 'YOUR_TOKEN';
    const claudeCode = `claude mcp add --transport http spanel ${endpoint} \\\n  --header "Authorization: Bearer ${bearer}"`;
    const cursor = JSON.stringify({
      mcpServers: {
        spanel: { url: endpoint, headers: { Authorization: `Bearer ${bearer}` } },
      },
    }, null, 2);
    const vscode = JSON.stringify({
      servers: {
        spanel: { type: 'http', url: endpoint, headers: { Authorization: `Bearer ${bearer}` } },
      },
    }, null, 2);

    return <>
      <div className="section">
        <div className="section-title">
          <div>
            <h2>{t('AI assistants (MCP)')}</h2>
            <p className="hint">
              {t('Give Claude Code, Cursor or VS Code a token and it can read and operate the panel with exactly your own permissions - nothing more.')}
            </p>
          </div>
          <button className="secondary-light" disabled={!!loading} onClick={loadMcpTokens}>
            <RefreshCw size={14}/>{t('Refresh')}</button>
        </div>

        {!mcpAddonInstalled && <div className="addon-notes">
          <strong><AlertCircle size={13}/>{t('The addon is off')}</strong>
          <ul><li>{t('Nothing answers on this address until an administrator installs')}{' '}<strong>{t('AI assistants (MCP)')}</strong> on the Addons page. Existing tokens are kept
            while it is off.</li></ul>
        </div>}

        {selfSigned && <div className="addon-notes">
          <strong><AlertCircle size={13}/>{t('This panel needs a real certificate')}</strong>
          <ul><li>{t('MCP clients refuse a self-signed certificate, so no assistant will connect until the panel has one. Install it under Panel settings → SSL.')}</li></ul>
        </div>}

        <div className="mcp-endpoint">
          <span>{t('Endpoint')}</span>
          <code>{endpoint}</code>
          <button className="mini secondary-light" onClick={() => copyText(endpoint, 'Endpoint copied.')}>
            <Copy size={13}/>{t('Copy')}</button>
        </div>
      </div>

      <div className="section">
        <div className="section-title"><div><h2>{t('New token')}</h2><p className="hint">{t('A token acts as you. It is shown once, when you create it.')}</p></div></div>
        <div className="form-row">
          <input
            placeholder={t('What is it for - laptop, work desktop')}
            value={mcpDraft.name}
            maxLength={100}
            onChange={event => setMcpDraft(prev => ({ ...prev, name: event.target.value }))}
          />
          <select
            value={mcpDraft.expires_in_days}
            aria-label={t('Expires in')}
            onChange={event => setMcpDraft(prev => ({ ...prev, expires_in_days: event.target.value }))}>
            <option value="7">{t('Expires in 7 days')}</option>
            <option value="30">{t('Expires in 30 days')}</option>
            <option value="90">{t('Expires in 90 days')}</option>
            <option value="365">{t('Expires in a year')}</option>
          </select>
          <label className="check-line">
            <input
              type="checkbox"
              checked={!!mcpDraft.can_write}
              onChange={event => setMcpDraft(prev => ({ ...prev, can_write: event.target.checked }))}
            />
            <span>{t('Allow actions')}</span>
          </label>
          <button disabled={!mcpDraft.name.trim() || !!loading} onClick={createMcpToken}>
            <KeyRound size={14}/>{t('Create')}</button>
        </div>
        <p className="hint">{t('Without')}{' '}<strong>{t('Allow actions')}</strong> the token can only read, and the assistant is not
          even shown the tools that change anything. Leave it off unless you want the assistant to
          act.
        </p>

        {mcpNewToken && <div className="mcp-secret">
          <div>
            <strong>{t('Copy this now. It is not shown again.')}</strong>
            <code>{mcpNewToken}</code>
          </div>
          <div className="actions">
            <button className="mini" onClick={() => copyText(mcpNewToken, 'Token copied. It is not shown again.')}><Copy size={13}/>{t('Copy')}</button>
            <button className="mini secondary-light" onClick={() => setMcpNewToken('')}>{t('Done')}</button>
          </div>
        </div>}
      </div>

      <div className="section">
        <div className="section-title"><div><h2>{t('Your tokens')}</h2></div></div>
        {mine.length === 0
          ? <EmptyState icon={KeyRound} message={t('No tokens yet.')} />
          : <div className="backup-list">{mine.map(token => renderMcpTokenRow(token))}</div>}
      </div>

      {isAdmin && others.length > 0 && <div className="section">
        <div className="section-title"><div><h2>{t('Everyone else\'s tokens')}</h2><p className="hint">{t('Every key to this server, and who holds it. You can revoke any of them.')}</p></div></div>
        <div className="backup-list">{others.map(token => renderMcpTokenRow(token, true))}</div>
      </div>}

      <div className="section">
        <div className="section-title"><div><h2>{t('Connecting a client')}</h2><p className="hint">
          {mcpNewToken
            ? <>Your new token is already filled in below - copy one and paste it straight in.
                {t('Once you dismiss the token above these go back to saying YOUR_TOKEN, because the panel cannot show it to you a second time.')}</>
            : <>{t('Create a token above and it appears in these ready to copy. Otherwise replace YOUR_TOKEN yourself.')}</>}
        </p></div></div>
        {[['Claude Code', claudeCode], ['Cursor - .cursor/mcp.json', cursor],
          ['VS Code - .vscode/mcp.json', vscode]].map(([label, snippet]) => (
          <div className="mcp-snippet" key={label}>
            <div className="mcp-snippet-head">
              <strong>{label}</strong>
              <button className="mini secondary-light" onClick={() => copyText(snippet, 'Configuration copied.')}>
                <Copy size={13}/>{t('Copy')}</button>
            </div>
            <pre>{snippet}</pre>
          </div>
        ))}
      </div>
    </>;
  }
  return renderMcp();
}
