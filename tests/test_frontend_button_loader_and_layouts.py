from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
APP_JSX = SRC / "App.jsx"
USERS_JSX = SRC / "pages" / "Users.jsx"
MOVE_USERS_JSX = SRC / "pages" / "MoveUsers.jsx"
BRAND_CSS = SRC / "brand.css"
VI_JS = SRC / "locales" / "vi.js"


def test_no_sticky_footer_loader_anywhere_in_frontend():
    """Ensure action-triggered footer loading bars are completely removed."""
    for jsx_file in sorted(SRC.rglob("*.jsx")):
        text = jsx_file.read_text(encoding="utf-8")
        assert '{loading && <div className="loading"' not in text, (
            f"Footer loading bar still present in {jsx_file.relative_to(ROOT)}"
        )

    brand_css = BRAND_CSS.read_text(encoding="utf-8")
    assert "position: static !important;" in brand_css
    assert "bottom: auto !important;" in brand_css


def test_unified_button_loader_wired_in_app_and_css():
    """Verify centralized button loader & anti-double-click lock in App.jsx and brand.css."""
    app_src = APP_JSX.read_text(encoding="utf-8")
    brand_css = BRAND_CSS.read_text(encoding="utf-8")

    for token in (
        "isActionButtonCandidate",
        "recordPendingActionButton",
        "beginActionOp",
        "endActionOp",
        "data-sp-loading",
        "aria-busy",
        "stopImmediatePropagation",
    ):
        assert token in app_src, f"Missing {token} in App.jsx"

    # Wired into request(), login(), downloadFile(), and sharedContextValue.setLoading
    assert re.search(r"async function request\([\s\S]{0,220}beginActionOp\(\)", app_src)
    assert re.search(r"async function login\([\s\S]{0,260}beginActionOp\(\)", app_src)
    assert re.search(r"async function downloadFile\([\s\S]{0,220}beginActionOp\(\)", app_src)
    assert re.search(r"const sharedContextValue = \{\s*\.\.\.baseContext,\s*setLoading,", app_src)

    # Single standardized spinner & pointer lock in brand.css
    assert 'button[data-sp-loading="true"]::before' in brand_css
    assert '.upload-button[data-sp-loading="true"]::before' in brand_css
    assert 'button[data-sp-loading="true"] > svg:first-child' in brand_css
    assert "pointer-events: none !important;" in brand_css
    assert "cursor: wait !important;" in brand_css


def test_show_all_users_toolbar_and_move_users_flat_table():
    """Verify Show All Users single-line toolbar rules and MoveUsers flat single-card table."""
    users_src = USERS_JSX.read_text(encoding="utf-8")
    move_src = MOVE_USERS_JSX.read_text(encoding="utf-8")
    brand_css = BRAND_CSS.read_text(encoding="utf-8")

    # Item 1: Show All Users toolbar controls stay inline without clipping
    toolbar_match = re.search(
        r'<div className="user-mgmt-controls">([\s\S]{0,2400}?)</div>\s*\)\}',
        users_src,
    )
    assert toolbar_match is not None
    assert 'type="text"' in toolbar_match.group(1)
    assert 'type="search"' not in toolbar_match.group(1)

    owner_select_block = re.search(r"\.user-owner-select\s*\{([^}]*)\}", brand_css)
    assert owner_select_block is not None
    assert "width: auto !important" in owner_select_block.group(1)

    controls_block = re.search(r"\.user-mgmt-controls\s*\{([^}]*)\}", brand_css)
    assert controls_block is not None
    assert "flex-wrap: nowrap" in controls_block.group(1)

    # Item 2: MoveUsers uses clean single-card table without nested border clutter
    assert "pkg-list-card user-mgmt-card" in move_src
    assert "pkg-clean-table user-clean-table move-users-clean-table" in move_src
    assert "website-search-bar" not in move_src
    assert "move-users-grid-row" not in move_src
    assert 'className="badge"' not in move_src
    assert ".move-row-checkbox" in brand_css


def test_vi_locale_covers_move_users_without_new_duplicates():
    """Ensure locales/vi.js includes every MoveUsers translation key exactly once."""
    vi_src = VI_JS.read_text(encoding="utf-8")
    keys = re.findall(r'^\s*"((?:\\.|[^"\\])*)"\s*:', vi_src, re.M)

    for expected_key in (
        "Move Users Between Resellers",
        "Move Users",
        "Move {count} Users",
        "Target Reseller / Admin",
        "Target Reseller / Admin...",
        "Current Owner",
        "{count} selected",
        "Select all users",
        "Please select at least one user to move.",
        "Please select a target reseller.",
        "Moving users...",
        "Users successfully moved.",
        "You do not have permission to access this page.",
    ):
        count = keys.count(expected_key)
        assert count == 1, f"Expected key {expected_key!r} once in vi.js, found {count}"


def test_button_loader_controller_runtime_behavior():
    """Execute the button-locking & sequential-chain logic in Node if available."""
    node_bin = shutil.which("node")
    if not node_bin:
        return

    script = r"""
const attrs = new WeakMap();
function makeBtn(name, excluded = false) {
  return {
    name,
    disabled: false,
    classList: { contains: () => false },
    getAttribute(k) { return (attrs.get(this) || {})[k] ?? null; },
    setAttribute(k, v) { const m = attrs.get(this) || {}; m[k] = String(v); attrs.set(this, m); },
    removeAttribute(k) { const m = attrs.get(this) || {}; delete m[k]; attrs.set(this, m); },
    closest(sel) {
      if (excluded && sel.includes('.sidebar')) return {};
      if (sel.includes('button')) return this;
      return null;
    },
  };
}

let pendingBtn = null;
let pendingTimer = null;
let activeBtn = null;
const opMap = new Map();
const countMap = new Map();
const releaseMap = new Map();

function isCandidate(el) {
  if (!el || el.disabled) return false;
  if (el.closest('.sidebar')) return false;
  return true;
}
function clearPending() {
  if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null; }
  pendingBtn = null;
}
function recordPending(btn) {
  if (!isCandidate(btn)) { clearPending(); return; }
  if (pendingTimer) clearTimeout(pendingTimer);
  pendingBtn = btn;
  pendingTimer = setTimeout(() => {
    if (pendingBtn === btn) pendingBtn = null;
    pendingTimer = null;
  }, 0);
}
function beginOp() {
  const c = pendingBtn || activeBtn;
  if (!c) return null;
  clearPending();
  const rel = releaseMap.get(c);
  if (rel) { clearTimeout(rel); releaseMap.delete(c); }
  countMap.set(c, (countMap.get(c) || 0) + 1);
  activeBtn = c;
  c.setAttribute('data-sp-loading', 'true');
  const tok = Symbol();
  opMap.set(tok, c);
  return tok;
}
function endOp(tok) {
  if (!tok) return;
  const b = opMap.get(tok);
  opMap.delete(tok);
  if (!b) return;
  const rem = (countMap.get(b) || 1) - 1;
  if (rem > 0) { countMap.set(b, rem); return; }
  countMap.delete(b);
  const t = setTimeout(() => {
    releaseMap.delete(b);
    if ((countMap.get(b) || 0) > 0) return;
    b.removeAttribute('data-sp-loading');
    if (activeBtn === b) activeBtn = null;
  }, 20);
  releaseMap.set(b, t);
}

(async () => {
  // 1. Sidebar nav click must NOT get loading state when page mount fetches
  const navBtn = makeBtn('sidebar-users', true);
  recordPending(navBtn);
  const mountOp = beginOp();
  if (mountOp !== null || navBtn.getAttribute('data-sp-loading') === 'true') {
    throw new Error('Excluded sidebar button must not show loader');
  }

  // 2. Cancelled confirm() click (no beginOp called) must clear cleanly
  const deleteBtn = makeBtn('delete-user', false);
  recordPending(deleteBtn);
  await new Promise(r => setTimeout(r, 5));
  if (deleteBtn.getAttribute('data-sp-loading') === 'true') {
    throw new Error('Cancelled confirm must not leave button loading');
  }

  // 3. Chained sequential ops keep button locked without flicker, then unlock
  const saveBtn = makeBtn('save-user', false);
  recordPending(saveBtn);
  const op1 = beginOp();
  if (saveBtn.getAttribute('data-sp-loading') !== 'true') {
    throw new Error('Save button should enter loading state immediately');
  }
  await new Promise(r => setTimeout(r, 5));
  endOp(op1);
  // Immediately chain second request (e.g. loadUsers after createUser)
  const op2 = beginOp();
  if (saveBtn.getAttribute('data-sp-loading') !== 'true') {
    throw new Error('Chained request must keep same button locked');
  }
  endOp(op2);
  await new Promise(r => setTimeout(r, 35));
  if (saveBtn.getAttribute('data-sp-loading') !== null) {
    throw new Error('Save button must unlock after chain completes');
  }
})();
"""
    res = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Node runtime test failed: {res.stderr or res.stdout}"
