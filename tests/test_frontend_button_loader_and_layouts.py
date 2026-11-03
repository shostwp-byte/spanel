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
WAF_JSX = SRC / "pages" / "Waf.jsx"
FILES_JSX = SRC / "pages" / "Files.jsx"
BRAND_CSS = SRC / "brand.css"
FILE_MANAGER_CSS = SRC / "file-manager.css"
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


def test_access_logs_and_log_viewer_deduplicated_across_roles():
    """Ensure Log Viewer menu duplication is removed and Access & Error Logs appears once per role."""
    app_src = APP_JSX.read_text(encoding="utf-8")

    assert "Log Viewer" not in app_src
    assert "['log_viewer'" not in app_src
    assert "['/log-viewer', 'access-logs']" in app_src

    access_log_items = re.findall(r"\['access-logs',\s*'Access & Error Logs',\s*FileText\]", app_src)
    assert len(access_log_items) == 3, (
        f"Expected 1 Access & Error Logs nav item per role (3 total), found {len(access_log_items)}"
    )

    # Admin's Access & Error Logs must live under System Info & Files, not duplicated in Admin Tools
    admin_tools_block = re.search(
        r"\{\s*key:\s*'admin_tools',\s*title:\s*'Admin Tools',\s*items:\s*\[([\s\S]*?)\]\}",
        app_src,
    )
    assert admin_tools_block is not None
    assert "'access-logs'" not in admin_tools_block.group(1)

    # App.jsx updateWafAccessLogFilters must not call undefined loadWafAccessLogs
    updater_block = re.search(r"function updateWafAccessLogFilters\([\s\S]{0,220}?\n  \}", app_src)
    assert updater_block is not None
    assert "loadWafAccessLogs" not in updater_block.group(0)


def test_access_logs_and_waf_websites_use_flat_single_card_tables():
    """Ensure Access & Error Logs and WAF Websites use flat single-card tables without nested borders."""
    waf_src = WAF_JSX.read_text(encoding="utf-8")
    brand_css = BRAND_CSS.read_text(encoding="utf-8")

    # Access & Error Logs flattened into 1 card + clean table
    assert "pkg-list-card user-mgmt-card access-logs-card" in waf_src
    assert "pkg-clean-table user-clean-table access-logs-clean-table" in waf_src
    assert "access-log-panel" not in waf_src
    assert "access-log-table-wrap" not in waf_src

    # WAF Websites list flattened into 1 card + clean table without per-row bordered cards
    assert "pkg-list-card user-mgmt-card waf-websites-card" in waf_src
    assert "pkg-clean-table user-clean-table waf-websites-clean-table" in waf_src
    assert "waf-overview-row" not in waf_src

    # Inline toolbar selects override global width: 100%, and verdict cells have no pill border
    select_block = re.search(r"\.access-log-select\s*\{([^}]*)\}", brand_css)
    assert select_block is not None
    assert "width: auto !important" in select_block.group(1)

    verdict_block = re.search(r"\.access-logs-clean-table\s+\.access-log-verdict\s*\{([^}]*)\}", brand_css)
    assert verdict_block is not None
    assert "border: none !important" in verdict_block.group(1)
    assert "background: transparent !important" in verdict_block.group(1)


def test_vi_locale_covers_logs_and_waf_keys_once():
    """Ensure new Vietnamese locale keys for Access & Error Logs and WAF appear once and avoid keptInEnglish."""
    vi_src = VI_JS.read_text(encoding="utf-8")
    keys = re.findall(r'^\s*"((?:\\.|[^"\\])*)"\s*:', vi_src, re.M)
    kept_block = vi_src.split("export const keptInEnglish = [", 1)[1]
    kept = set(re.findall(r'"((?:\\.|[^"\\])*)"', kept_block))

    for expected_key in (
        "Access & Error Logs",
        "Web Application Firewall",
        "Filter IP, path, domain...",
        "{n} entries",
        "50 / page",
        "100 / page",
        "200 / page",
        "500 / page",
        "Filter websites...",
        "Bad Bots",
        "Allow",
        "Upload Files",
        "Drag & drop files here, or click to select files",
        "Supports selecting and uploading multiple files sequentially",
        "Cannot close while uploading",
        "Window closing is locked until the upload queue finishes.",
        "Uploading {current} of {total}...",
        "Completed {done} / {total} file(s)",
        "{n} file(s) ready ({size})",
        "Upload {n} file(s)",
        "Uploading ({done}/{total})...",
        "Retry failed ({n})",
        "Uploading files...",
        "Uploaded {n} file(s) successfully.",
        "Uploaded {done} of {total} file(s) ({failed} failed).",
        "Drop files here to add to upload queue",
        "Folders cannot be uploaded directly — please select files or upload a .zip/.tar.gz archive.",
        "Remove from queue",
    ):
        assert expected_key not in kept, f"{expected_key!r} is in keptInEnglish"
        count = keys.count(expected_key)
        assert count == 1, f"Expected key {expected_key!r} once in vi.js, found {count}"


def test_file_manager_multi_upload_drag_drop_sequential_and_close_lock():
    """Verify multi-file selection, drag-and-drop, sequential XHR progress, and non-closable modal while uploading."""
    files_src = FILES_JSX.read_text(encoding="utf-8")
    fm_css = FILE_MANAGER_CSS.read_text(encoding="utf-8")

    # 1. Multi-file input inside dedicated Upload modal
    assert "renderUploadModalDialog" in files_src
    assert re.search(r'<input[\s\S]{0,180}?ref=\{uploadModalInputRef\}[\s\S]{0,120}?type="file"[\s\S]{0,60}?multiple', files_src)

    # 2. Drag-and-drop handlers on both dropzone and File Manager workspace
    for handler in (
        "handleDropzoneDragEnter",
        "handleDropzoneDragOver",
        "handleDropzoneDragLeave",
        "handleDropzoneDrop",
        "handleWorkspaceDragEnter",
        "handleWorkspaceDrop",
        "da-upload-dropzone",
        "da-fm-drop-overlay",
    ):
        assert handler in files_src, f"Missing {handler} in Files.jsx"

    # 3. Real per-file sequential XHR progress
    assert "new XMLHttpRequest()" in files_src
    assert "xhr.upload.addEventListener('progress'" in files_src
    assert "for (const item of itemsToRun)" in files_src
    assert "await uploadSingleFileWithProgress(item, uploadDir)" in files_src

    # 4. Strict close lock across closeUploadModal, Escape key, beforeunload, backdrop, X, and footer Close
    close_fn = re.search(r"function closeUploadModal\(\)\s*\{([\s\S]{0,260}?)\n  \}", files_src)
    assert close_fn is not None
    assert "if (isUploading || isUploadingRef.current) return;" in close_fn.group(1)
    assert "window.addEventListener('beforeunload', handleBeforeUnload)" in files_src
    assert "if (e.key === 'Escape')" in files_src
    assert "Window closing is locked until the upload queue finishes." in files_src

    # 5. CSS rules present in file-manager.css
    for selector in (".da-upload-dialog", ".da-upload-dropzone", ".da-upload-bar-fill", ".da-fm-drop-overlay"):
        assert selector in fm_css, f"Missing {selector} in file-manager.css"


def test_disk_quota_card_i18n_and_reconciling_ui():
    """Verify DiskQuotaCard localized hints, reconciling spinner state, and vi.js keys."""
    card_src = (SRC / "components" / "DiskQuotaCard.jsx").read_text(encoding="utf-8")
    vi_src = VI_JS.read_text(encoding="utf-8")
    keys = re.findall(r'^\s*"((?:\\.|[^"\\])*)"\s*:', vi_src, re.M)
    kept_block = vi_src.split("export const keptInEnglish =", 1)[1]
    kept = set(re.findall(r'"((?:\\.|[^"\\])*)"', kept_block))

    for token in (
        "formatQuotaHint",
        "quota.reconciling",
        "quota.last_error",
        "quota?.state === 'needs-quotaon'",
        "Switching on...",
    ):
        assert token in card_src, f"Missing {token} in DiskQuotaCard.jsx"

    for expected_key in (
        "Kernel",
        "Switching on...",
        "Build the quota files and switch on kernel disk quota enforcement for {mount} now?",
        "Install the quota tools and add user quota to /etc/fstab for {mount}? Nothing is remounted or rebooted; if a reboot is needed, SPanel will finish switching kernel quota on automatically after reboot.",
        "Initializing quota files and switching on kernel quota on {mount}...",
        "Kernel quota enforces each account's disk and inode limits on {mount}.",
        "Quota options are mounted on {mount}; enable kernel quota to build the quota files and switch it on.",
        "Quota options are in /etc/fstab for {mount}. Reboot the server (or run mount -o remount {mount}); SPanel will automatically finish switching kernel quota on.",
        "/ is XFS: add rootflags=uquota to GRUB_CMDLINE_LINUX in /etc/default/grub, run update-grub and reboot.",
        "Only the panel's own accounting applies; PHP and SFTP can write past the limit.",
        "Kernel quota needs ext4 or xfs; {mount} is on {fstype}.",
    ):
        assert expected_key not in kept, f"{expected_key!r} is in keptInEnglish"
        count = keys.count(expected_key)
        assert count == 1, f"Expected key {expected_key!r} once in vi.js, found {count}"
