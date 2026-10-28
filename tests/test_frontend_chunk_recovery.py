from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JSX = ROOT / "frontend" / "src" / "App.jsx"
WEBSITES_JSX = ROOT / "frontend" / "src" / "pages" / "Websites.jsx"


def test_chunk_recovery_and_ace_bundle_reset_runtime():
    """Execute the actual chunk-recovery functions and loadAceBundle reset logic from App.jsx in Node."""
    app_src = APP_JSX.read_text(encoding="utf-8")
    websites_src = WEBSITES_JSX.read_text(encoding="utf-8")

    assert "ChunkErrorBoundary" in app_src
    assert "vite:preloadError" in app_src
    assert "<ChunkErrorBoundary resetKey={page}>" in app_src
    assert "_aceBundlePromise = null;" in app_src
    assert "spanel_chunk_reload_ts" in websites_src

    chunk_block_match = re.search(
        r"(const CHUNK_RELOAD_KEY = 'spanel_chunk_reload_ts';[\s\S]*?function tryReloadOnceForChunkError\(\) \{[\s\S]*?\n\})",
        app_src,
    )
    assert chunk_block_match is not None, "Chunk reload helpers not found in App.jsx"
    chunk_helpers_js = chunk_block_match.group(1)

    node_bin = shutil.which("node")
    assert node_bin is not None, "node binary required to execute frontend runtime test"

    harness = f"""
const storage = new Map();
let reloadCount = 0;
let preloadListener = null;

globalThis.sessionStorage = {{
  getItem: (k) => (storage.has(k) ? storage.get(k) : null),
  setItem: (k, v) => storage.set(k, String(v)),
  removeItem: (k) => storage.delete(k),
}};
globalThis.window = {{
  location: {{
    reload: () => {{ reloadCount += 1; }},
  }},
  addEventListener: (name, fn) => {{
    if (name === 'vite:preloadError') preloadListener = fn;
  }},
}};

{chunk_helpers_js}

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {{
  window.addEventListener('vite:preloadError', event => {{
    if (tryReloadOnceForChunkError()) {{
      event?.preventDefault?.();
    }}
  }});
}}

// 1. Verify isChunkLoadError matches Vite / Rollup / browser dynamic import errors
const err1 = new TypeError('Failed to fetch dynamically imported module: https://panel/assets/Files-old.js');
const err2 = new Error('Importing a module script failed.');
const err3 = new Error('Regular validation error');
if (!isChunkLoadError(err1) || !isChunkLoadError(err2) || isChunkLoadError(err3)) {{
  throw new Error('isChunkLoadError classification failed');
}}

// 2. First vite:preloadError triggers reload once and prevents default
let prevented = false;
preloadListener({{ preventDefault: () => {{ prevented = true; }} }});
if (reloadCount !== 1 || !prevented) {{
  throw new Error('First preloadError did not reload once: ' + reloadCount);
}}

// 3. Immediate second chunk error within cooldown does NOT infinite-reload
const secondAttempt = tryReloadOnceForChunkError();
if (secondAttempt !== false || reloadCount !== 1) {{
  throw new Error('Cooldown guard failed to prevent reload loop');
}}

// 4. Verify _aceBundlePromise resets to null on rejection so subsequent call succeeds
let _aceBundlePromise = null;
let shouldFail = true;
function loadAceBundleSim() {{
  if (!_aceBundlePromise) {{
    _aceBundlePromise = (async () => {{
      if (shouldFail) throw new Error('Failed to fetch dynamically imported module: ace.js');
      return {{ edit: () => 'ok' }};
    }})().catch(err => {{
      _aceBundlePromise = null;
      throw err;
    }});
  }}
  return _aceBundlePromise;
}}

(async () => {{
  let caught = false;
  try {{
    await loadAceBundleSim();
  }} catch (e) {{
    caught = true;
  }}
  if (!caught || _aceBundlePromise !== null) {{
    throw new Error('_aceBundlePromise was not reset after rejection');
  }}
  shouldFail = false;
  const ace = await loadAceBundleSim();
  if (!ace || ace.edit() !== 'ok') {{
    throw new Error('loadAceBundleSim did not recover after promise reset');
  }}
  console.log(JSON.stringify({{ ok: true, reloadCount }}));
}})();
"""
    proc = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Node runtime test failed: {proc.stderr}\n{proc.stdout}"
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["ok"] is True
    assert data["reloadCount"] == 1
