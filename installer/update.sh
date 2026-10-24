#!/usr/bin/env bash
# Update SPanel from GitHub.
#
# By default this script downloads the newest stable release zip to a temporary
# directory, syncs the source into /opt/spanel, rebuilds the frontend, refreshes
# helper scripts, restarts the API, and reloads nginx for customer vhosts. A git
# checkout is only used for --branch or --skip-pull development workflows.
#
# Usage:
#   sudo bash installer/update.sh
#   sudo bash installer/update.sh --branch main
#   sudo bash installer/update.sh --tag v1.0.57
#   sudo bash installer/update.sh --help

set -euo pipefail

log()  { echo ""; echo "==> $1"; }
fail() { echo "ERROR: $1" >&2; exit 1; }

# Skip expensive, idempotent steps when the inputs that drive them have not
# changed since the last successful update. State lives in /var/lib/spanel so
# it survives everything except a fresh install.
SPANEL_UPDATE_STATE_DIR=/var/lib/spanel/update-state

# The one apt wrapper this script uses. It was called and never defined: the
# PHP MySQL repair below ran `apt_get install -y php<v>-mysql`, bash answered
# "apt_get: command not found", and the `|| log WARNING` around it turned that
# into a line nobody reads. So the repair for a server that lost its MySQL
# extension has never once worked - on the very machine where it mattered it
# printed a warning and moved on.
#
# Same body as install.sh's: an update runs on a live server, where
# unattended-upgrades may be holding the dpkg lock, and failing on that would
# abandon the update halfway.
apt_get() {
  local waited=0
  while fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock >/dev/null 2>&1; do
    if (( waited == 0 )); then
      echo "Waiting for another package manager to finish..."
    fi
    if (( waited >= 300 )); then
      fail "Timed out after 5 minutes waiting for the dpkg lock."
    fi
    sleep 5
    waited=$(( waited + 5 ))
  done
  DEBIAN_FRONTEND=noninteractive apt-get "$@"
}

# `dpkg -s` exits 0 for a package that was removed but kept its config files
# ("deinstall ok config-files"), whose binaries are gone. Ask for the state.
pkg_installed() {
  [[ "$(dpkg-query -W -f='${Status}' "$1" 2>/dev/null)" == "install ok installed" ]]
}

fingerprint() {
  # fingerprint <path> [path ...] -> one sha256 over the contents of every
  # file under those paths (paths are stable, so hashing them too is fine).
  # Missing paths are skipped, not an error: if one later appears the hash
  # changes and the step re-runs. Returns non-zero only when nothing exists.
  local existing=() p
  for p in "$@"; do [[ -e "$p" ]] && existing+=("$p"); done
  [[ ${#existing[@]} -gt 0 ]] || return 1
  # Hash the content hashes only, not the "  <name>" sha256sum prints after
  # them: the same files under a different prefix (a release temp dir, or
  # /usr/local/sbin vs the source tree) must fingerprint the same.
  find "${existing[@]}" -type f -exec sha256sum {} + 2>/dev/null \
    | awk '{print $1}' | LC_ALL=C sort | sha256sum | awk '{print $1}'
}

step_inputs_changed() {
  # step_inputs_changed <name> <path> [path ...]
  # true (0) when the fingerprint differs from the stored one (or is forced).
  local name="$1"; shift
  [[ "${SPANEL_FORCE_FULL_UPDATE:-}" == "1" ]] && return 0
  local now stored=""
  now="$(fingerprint "$@")" || return 0
  [[ -n "$now" ]] || return 0
  [[ -f "$SPANEL_UPDATE_STATE_DIR/$name" ]] && stored="$(cat "$SPANEL_UPDATE_STATE_DIR/$name" 2>/dev/null)"
  [[ "$now" != "$stored" ]]
}

step_mark_done() {
  # step_mark_done <name> <path> [path ...] - call only after the step succeeded
  local name="$1"; shift
  local now; now="$(fingerprint "$@")" || return 0
  mkdir -p "$SPANEL_UPDATE_STATE_DIR"
  printf '%s' "$now" >"$SPANEL_UPDATE_STATE_DIR/$name"
}

usage() {
  cat <<'USAGE'
Usage:
  sudo bash installer/update.sh [--release]
  sudo bash installer/update.sh --tag v1.0.57
  sudo bash installer/update.sh --branch main

Options:
  --release          Update to the newest matching release tag (default).
  --tag TAG          Pin an exact release tag.
  --branch NAME      Update from a remote branch.
  --channel MODE     Set update mode: release, tag, or branch.
  --remote NAME      Git remote to fetch from or create on clone (default: origin).
  --skip-pull        Sync the current SOURCE_DIR without fetching/checking out.
  --app-dir DIR      Production deployment dir (default: /opt/spanel).
  -h, --help         Show this help.

Environment:
  APP_DIR, SOURCE_DIR, REPO_URL, GIT_REMOTE, UPDATE_CHANNEL, BRANCH,
  RELEASE_TAG, RELEASE_PATTERN, RELEASE_ZIP_URL, SKIP_PULL.

Notes:
  The release channel selects the newest matching release tag and downloads a
  zip archive. Use --tag or UPDATE_CHANNEL=tag with RELEASE_TAG to pin a tag.
  Use RELEASE_ZIP_URL with {tag} only for non-GitHub archive URLs.
USAGE
}

require_arg() {
  local opt="$1" value="${2-}"
  [[ -n "$value" && "$value" != --* ]] || fail "$opt requires a value"
}

for arg in "$@"; do
  if [[ "$arg" == "-h" || "$arg" == "--help" ]]; then
    usage
    exit 0
  fi
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root"
  exit 1
fi

if [[ -z "${SPANEL_UPDATE_STABLE_COPY:-}" && -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  _original_script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
  _stable_copy="$(mktemp /tmp/spanel-update.XXXXXX.sh)"
  cp "$_original_script" "$_stable_copy"
  chmod 0700 "$_stable_copy"
  SPANEL_UPDATE_STABLE_COPY="$_stable_copy" \
    SPANEL_UPDATE_ORIGINAL_SCRIPT="$_original_script" \
    exec /bin/bash "$_stable_copy" "$@"
fi

cleanup_stable_copy() {
  rm -f "${SPANEL_UPDATE_STABLE_COPY:-}" "${SPANEL_UPDATE_PREVIOUS_COPY:-}" 2>/dev/null || true
}
trap cleanup_stable_copy EXIT

# --- Config ----------------------------------------------------------------
APP_DIR="${APP_DIR:-/opt/spanel}"                 # Production deployment dir
DEFAULT_SOURCE_DIR="/opt/spanel-source"           # Dev/branch checkout dir only

# Resolve where THIS script lives. If it's inside a real git checkout we use
# that. Otherwise we fall back to /opt/spanel-source so users running the
# script from the deploy dir still get a usable workflow.
_SCRIPT_SOURCE="${SPANEL_UPDATE_ORIGINAL_SCRIPT:-${BASH_SOURCE[0]:-}}"
if [[ -n "$_SCRIPT_SOURCE" && -f "$_SCRIPT_SOURCE" ]]; then
  _SCRIPT_DIR="$(cd "$(dirname "$_SCRIPT_SOURCE")/.." && pwd)"
else
  _SCRIPT_DIR="$DEFAULT_SOURCE_DIR"
fi
if [[ -d "$_SCRIPT_DIR/.git" ]]; then
  SOURCE_DIR="${SOURCE_DIR:-$_SCRIPT_DIR}"
else
  SOURCE_DIR="${SOURCE_DIR:-$DEFAULT_SOURCE_DIR}"
fi

REPO_URL="${REPO_URL:-https://github.com/shostwp-byte/spanel.git}"
GIT_REMOTE="${GIT_REMOTE-origin}"                 # remote name in the local checkout
UPDATE_CHANNEL="${UPDATE_CHANNEL-release}"        # release, branch, or tag
BRANCH="${BRANCH-main}"                           # used when UPDATE_CHANNEL=branch
RELEASE_TAG="${RELEASE_TAG-}"                     # used when UPDATE_CHANNEL=tag
RELEASE_PATTERN="${RELEASE_PATTERN:-v[0-9]*.[0-9]*.[0-9]*}"
RELEASE_ZIP_URL="${RELEASE_ZIP_URL:-}"             # optional archive URL template with {tag}
SKIP_PULL="${SKIP_PULL:-false}"
UPDATE_STATE_FILE="${UPDATE_STATE_FILE:-/var/lib/spanel/update-status.json}"
RELEASE_WORK_DIR="${RELEASE_WORK_DIR:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --channel) require_arg "$1" "${2-}"; UPDATE_CHANNEL="$2"; shift 2 ;;
    --release) UPDATE_CHANNEL="release"; shift ;;
    --branch) require_arg "$1" "${2-}"; UPDATE_CHANNEL="branch"; BRANCH="$2"; shift 2 ;;
    --tag) require_arg "$1" "${2-}"; UPDATE_CHANNEL="tag"; RELEASE_TAG="$2"; shift 2 ;;
    --remote) require_arg "$1" "${2-}"; GIT_REMOTE="$2"; shift 2 ;;
    --skip-pull) SKIP_PULL="true"; shift ;;
    --app-dir) require_arg "$1" "${2-}"; APP_DIR="$2"; shift 2 ;;
    -h|--help)
      usage
      exit 0 ;;
    *) fail "Unknown arg: $1" ;;
  esac
done

# --- Validate config --------------------------------------------------------
require_git_ref_name() {
  local kind="$1" value="$2"
  [[ -n "$value" ]] || fail "$kind cannot be empty"
  [[ "$value" != -* ]] || fail "$kind must not start with '-'"
  case "$kind" in
    BRANCH)
      git check-ref-format --branch "$value" >/dev/null 2>&1 \
        || fail "BRANCH has invalid git ref characters: $value"
      ;;
    RELEASE_TAG)
      git check-ref-format "refs/tags/$value" >/dev/null 2>&1 \
        || fail "RELEASE_TAG has invalid git ref characters: $value"
      ;;
  esac
}

[[ -n "$REPO_URL" ]] || fail "REPO_URL cannot be empty"
[[ -n "$GIT_REMOTE" ]] || fail "GIT_REMOTE cannot be empty"
[[ "$GIT_REMOTE" != -* ]] || fail "GIT_REMOTE must not start with '-'"
if ! [[ "$GIT_REMOTE" =~ ^[A-Za-z0-9._-]+$ ]]; then
  fail "GIT_REMOTE must match [A-Za-z0-9._-]+ (got: $GIT_REMOTE)"
fi
if [[ "$UPDATE_CHANNEL" != "release" && "$UPDATE_CHANNEL" != "branch" && "$UPDATE_CHANNEL" != "tag" ]]; then
  fail "UPDATE_CHANNEL must be release|branch|tag (got: $UPDATE_CHANNEL)"
fi
[[ "$SKIP_PULL" == "true" || "$SKIP_PULL" == "false" ]] || fail "SKIP_PULL must be true|false"
if [[ "$UPDATE_CHANNEL" == "branch" ]]; then
  require_git_ref_name BRANCH "$BRANCH"
fi
if [[ "$UPDATE_CHANNEL" == "tag" ]]; then
  require_git_ref_name RELEASE_TAG "$RELEASE_TAG"
fi
if [[ "$UPDATE_CHANNEL" == "release" && -n "$RELEASE_TAG" ]]; then
  echo "INFO: ignoring RELEASE_TAG in release channel; use --tag to pin ${RELEASE_TAG}."
fi

env_get() {
  local file="$APP_DIR/backend/.env" key="$1"
  [[ -f "$file" ]] || return 0
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$file"
}

env_set_default() {
  local file="$APP_DIR/backend/.env" key="$1" value="$2"
  if ! grep -q "^${key}=" "$file"; then
    printf '%s=%s\n' "$key" "$value" >>"$file"
  fi
}

env_set() {
  # Replace a value, or add it if the key is new. Written through a temporary
  # file and copied back so the .env keeps its owner and its 0640 mode.
  local file="$APP_DIR/backend/.env" key="$1" value="$2" tmp
  [[ -f "$file" ]] || return 0
  tmp="$(mktemp)"
  KEY="$key" VALUE="$value" awk '
    BEGIN { key = ENVIRON["KEY"]; value = ENVIRON["VALUE"]; written = 0 }
    index($0, key "=") == 1 { if (!written) { print key "=" value; written = 1 } next }
    { print }
    END { if (!written) print key "=" value }
  ' "$file" >"$tmp"
  cat "$tmp" >"$file"
  rm -f "$tmp"
}

ensure_git_remote() {
  if git remote get-url "$GIT_REMOTE" >/dev/null 2>&1; then
    return 0
  fi
  log "Adding git remote ${GIT_REMOTE} -> ${REPO_URL}"
  git remote add "$GIT_REMOTE" "$REPO_URL"
}

latest_release_tag() {
  git ls-remote --tags --refs "$REPO_URL" "refs/tags/${RELEASE_PATTERN}" \
    | awk '{ sub("refs/tags/", "", $2); print $2 }' \
    | sort -V \
    | tail -n 1
}

release_archive_url() {
  local tag="$1" repo="$REPO_URL" base
  if [[ -n "$RELEASE_ZIP_URL" ]]; then
    printf '%s\n' "${RELEASE_ZIP_URL//\{tag\}/$tag}"
    return 0
  fi
  case "$repo" in
    https://github.com/*)
      base="${repo%.git}"
      ;;
    git@github.com:*)
      base="https://github.com/${repo#git@github.com:}"
      base="${base%.git}"
      ;;
    *)
      fail "Cannot derive release zip URL from REPO_URL. Set RELEASE_ZIP_URL or use --branch."
      ;;
  esac
  printf '%s/archive/refs/tags/%s.zip\n' "$base" "$tag"
}

download_release_source() {
  local tag="$1" archive extract_dir archive_url
  RELEASE_WORK_DIR="$(mktemp -d /tmp/spanel-release-update.XXXXXX)"
  archive="${RELEASE_WORK_DIR}/spanel-release.zip"
  extract_dir="${RELEASE_WORK_DIR}/extract"
  archive_url="$(release_archive_url "$tag")"
  log "Downloading release ${tag}"
  curl -fL --connect-timeout 10 --max-time 300 "$archive_url" -o "$archive"
  mkdir -p "$extract_dir"
  unzip -q "$archive" -d "$extract_dir"
  SOURCE_DIR="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  [[ -n "$SOURCE_DIR" && -d "$SOURCE_DIR/backend" && -d "$SOURCE_DIR/frontend" ]] \
    || fail "Release archive does not contain backend/frontend source"
}

reset_worktree_to_ref() {
  local ref="$1" branch_name="${2-}"
  if [[ -n "$branch_name" ]]; then
    git checkout -f -B "$branch_name" "$ref"
  else
    git checkout -f --detach "$ref"
  fi
  git reset --hard "$ref"
}

current_panel_version() {
  if [[ -f "$APP_DIR/VERSION" ]]; then
    tr -d '[:space:]' <"$APP_DIR/VERSION"
    return 0
  fi
  sed -nE 's/^APP_VERSION = "([^"]+)"/\1/p' "$APP_DIR/backend/app/core/version.py" 2>/dev/null | head -n 1
}

write_update_state() {
  local status="$1" ref="${2:-}" message="${3:-}" now current latest
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  current="$(current_panel_version)"
  latest="${ref#v}"
  [[ "$latest" == "$ref" ]] && latest=""
  install -d -m 0750 "$(dirname "$UPDATE_STATE_FILE")"
  python3 - "$UPDATE_STATE_FILE" "$status" "$ref" "$latest" "$current" "$message" "$now" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
status, ref, latest, current, message, now = sys.argv[2:8]
try:
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except Exception:
    state = {}

if current:
    state["current_version"] = current
if latest:
    state["latest_tag"] = ref
    state["latest_version"] = latest
state["last_update_status"] = status
if ref:
    state["last_update_ref"] = ref
if message:
    state["last_update_message"] = message
if status in {"checking", "updating"}:
    state["last_update_started_at"] = now
elif status in {"completed", "failed"}:
    state["last_update_finished_at"] = now
path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  if id -u spanel >/dev/null 2>&1; then
    chown spanel:spanel "$UPDATE_STATE_FILE" 2>/dev/null || true
  fi
  chmod 0640 "$UPDATE_STATE_FILE" 2>/dev/null || true
}

# Write progress markers (percent / phase / message) into the update state file
# without touching the existing status/version fields. Safe to call at any time.
update_progress() {
  local percent="$1" phase="$2" message="${3:-}"
  install -d -m 0750 "$(dirname "$UPDATE_STATE_FILE")"
  python3 - "$UPDATE_STATE_FILE" "$percent" "$phase" "$message" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
percent, phase, message = sys.argv[2:5]
try:
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except Exception:
    state = {}
try:
    state["progress_percent"] = int(percent)
except (ValueError, TypeError):
    state["progress_percent"] = 0
if phase:
    state["progress_phase"] = phase
if message:
    state["progress_message"] = message
path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  if id -u spanel >/dev/null 2>&1; then
    chown spanel:spanel "$UPDATE_STATE_FILE" 2>/dev/null || true
  fi
  chmod 0640 "$UPDATE_STATE_FILE" 2>/dev/null || true
}

UPDATE_REF=""
cleanup_release_work_dir() {
  if [[ -n "${RELEASE_WORK_DIR:-}" && -d "$RELEASE_WORK_DIR" ]]; then
    rm -rf -- "$RELEASE_WORK_DIR"
  fi
}

finish_update_script() {
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    local cur_pct=0
    if [[ -f "$UPDATE_STATE_FILE" ]]; then
      cur_pct="$(python3 -c 'import json, sys
try:
    print(json.load(open(sys.argv[1])).get("progress_percent", 0))
except Exception:
    print(0)
' "$UPDATE_STATE_FILE" 2>/dev/null || true)"
    fi
    update_progress "${cur_pct:-0}" "failed" "Update failed with exit code ${rc}" || true
    write_update_state "failed" "${UPDATE_REF:-}" "Update failed with exit code ${rc}" || true
  fi
  cleanup_release_work_dir
  cleanup_stable_copy
}
trap finish_update_script EXIT

# Trap ERR so any failing command records a failed progress marker. With
# `set -e` the EXIT trap above performs the actual state write; this is a
# secondary safety net for non-fatal paths that call `|| true`/return codes.
trap 'update_progress "${progress_percent:-0}" "failed" "Update failed at phase: ${progress_phase:-unknown}" 2>/dev/null || true' ERR

detect_server_ip() {
  hostname -I 2>/dev/null | awk '{print $1}' || true
}

remove_filebrowser_runtime() {
  systemctl disable --now filebrowser 2>/dev/null || true
  rm -f /etc/systemd/system/filebrowser.service
  rm -rf /etc/systemd/system/filebrowser.service.d
  rm -rf /etc/filebrowser /var/lib/filebrowser
  rm -f /usr/local/bin/filebrowser
  sed -i '/^FILEBROWSER_PORT=/d' "$APP_DIR/backend/.env" 2>/dev/null || true
  systemctl daemon-reload
}

ensure_panel_https() {
  # The panel takes an admin password and hands out a session token, so it has
  # no business answering in the clear. A server with no domain has nothing a
  # certificate authority will sign, so it gets a self-signed certificate: a
  # warning the operator clicks through once beats a password on the wire.
  local panel_port="$1" server_ip="$2" cert key host mode live_dir had_https="no"
  cert="$(env_get PANEL_SSL_CERT)"
  key="$(env_get PANEL_SSL_KEY)"
  host="$(env_get PANEL_DOMAIN)"
  mode="$(env_get PANEL_SSL_MODE)"
  [[ -n "$cert" && -n "$key" && -f "$cert" && -f "$key" ]] && had_https="yes"

  # If the panel answers on a domain that already has a real certificate, use
  # it: a name the browser trusts beats one it warns about, and there is nothing
  # to choose between when the panel's own hostname is the domain in question.
  live_dir="${LETSENCRYPT_LIVE_DIR:-/etc/letsencrypt/live}/${host}"
  if [[ -n "$host" && -f "${live_dir}/fullchain.pem" && -f "${live_dir}/privkey.pem" \
        && "$mode" != "letsencrypt" && "$mode" != "domain" ]]; then
    # Already serving exactly this certificate: the installer got it for the
    # panel's own hostname and, before 1.0.169, did not record the mode. Only
    # the record is missing; there is nothing to switch and nothing to say.
    if [[ "$had_https" == "yes" ]] && cmp -s "${live_dir}/fullchain.pem" "$cert"; then
      env_set PANEL_SSL_MODE "letsencrypt"
      return 0
    fi
    log "Panel domain ${host} already has a certificate; using it"
    install -d -o root -g spanel -m 0750 /etc/spanel
    install -m 0640 -o root -g spanel "${live_dir}/fullchain.pem" /etc/spanel/panel-fullchain.pem
    install -m 0640 -o root -g spanel "${live_dir}/privkey.pem" /etc/spanel/panel-privkey.pem
    env_set PANEL_SSL_CERT "/etc/spanel/panel-fullchain.pem"
    env_set PANEL_SSL_KEY "/etc/spanel/panel-privkey.pem"
    env_set PANEL_SSL_MODE "domain"
    env_set PANEL_URL "https://${host}:${panel_port}"
    env_set ALLOWED_ORIGINS "https://${host}:${panel_port}"
    PANEL_SWITCHED_TO_HTTPS="https://${host}:${panel_port}"
    [[ "$had_https" == "yes" ]] && PANEL_WAS_HTTP="no" || PANEL_WAS_HTTP="yes"
    return 0
  fi

  if [[ -n "$cert" && -n "$key" && -f "$cert" && -f "$key" ]]; then
    return 0
  fi
  host="${host:-${server_ip:-127.0.0.1}}"
  echo "==> Panel has no certificate; generating a self-signed one"
  install -d -o root -g spanel -m 0750 /etc/spanel
  local san="DNS:${host}"
  [[ "$host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && san="IP:${host}"
  [[ -n "$server_ip" && "$server_ip" != "$host" ]] && san="${san},IP:${server_ip}"
  if ! openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
      -keyout /etc/spanel/panel-selfsigned-privkey.pem \
      -out /etc/spanel/panel-selfsigned-fullchain.pem \
      -subj "/CN=${host}" -addext "subjectAltName=${san}" >/dev/null 2>&1; then
    echo "    Could not generate a certificate; leaving the panel on HTTP" >&2
    return 0
  fi
  chown root:spanel /etc/spanel/panel-selfsigned-fullchain.pem /etc/spanel/panel-selfsigned-privkey.pem
  chmod 0640 /etc/spanel/panel-selfsigned-fullchain.pem /etc/spanel/panel-selfsigned-privkey.pem
  env_set PANEL_SSL_CERT "/etc/spanel/panel-selfsigned-fullchain.pem"
  env_set PANEL_SSL_KEY "/etc/spanel/panel-selfsigned-privkey.pem"
  env_set PANEL_SSL_MODE "selfsigned"
  env_set PANEL_URL "https://${host}:${panel_port}"
  env_set ALLOWED_ORIGINS "https://${host}:${panel_port}"
  PANEL_SWITCHED_TO_HTTPS="https://${host}:${panel_port}"
  PANEL_WAS_HTTP="yes"
}

panel_https_note() {
  # What the certificate step changed, in words that are true for this server:
  # "self-signed" only when it is, "http:// will not load" only when the panel
  # was on http:// before. It used to say both after every switch, including
  # a switch to the domain's real certificate on a panel already on HTTPS.
  echo ""
  if [[ "${PANEL_WAS_HTTP:-yes}" == "yes" ]]; then
    echo "  The panel now answers over HTTPS only:"
    echo "      ${PANEL_SWITCHED_TO_HTTPS}"
    echo "  The old http:// address will not load."
  else
    echo "  The panel now uses the certificate of its domain:"
    echo "      ${PANEL_SWITCHED_TO_HTTPS}"
  fi
  if [[ "$(env_get PANEL_SSL_MODE)" == "selfsigned" ]]; then
    echo "  The certificate is self-signed, so the browser warns once; to replace it"
    echo "  with a real one, point a domain at this server and use Panel settings -> SSL."
  else
    echo "  It is a real certificate, so the browser shows no warning."
  fi
}

write_tools_nginx_config() {
  local panel_port panel_domain panel_cert panel_key php_version server_ip host api_scheme tools_scheme pma_secure ssl_block
  panel_port="$(env_get PANEL_PORT)"; panel_port="${panel_port:-2222}"
  panel_domain="$(env_get PANEL_DOMAIN)"
  panel_cert="$(env_get PANEL_SSL_CERT)"
  panel_key="$(env_get PANEL_SSL_KEY)"
  php_version="${PHP_DEFAULT:-8.4}"
  server_ip="$(detect_server_ip)"
  host="${panel_domain:-$server_ip}"
  api_scheme="http"; tools_scheme="http"; pma_secure="false"; ssl_block=""
  if [[ -n "$panel_cert" && -n "$panel_key" && -f "$panel_cert" && -f "$panel_key" ]]; then
    api_scheme="https"; tools_scheme="https"; pma_secure="true"
    printf -v ssl_block '\n    listen 443 ssl http2 default_server;\n    ssl_certificate %s;\n    ssl_certificate_key %s;' "$panel_cert" "$panel_key"
  fi
  cat >/etc/nginx/conf.d/00-spanel-tools.conf <<NGINX
server {
    listen 80 default_server;${ssl_block}
    server_name _;
    client_max_body_size 1100M;

    # Panel certificates are issued through this, so the panel no longer has to
    # stop nginx to prove it owns its own hostname.
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/spanel-acme;
        default_type text/plain;
        try_files \$uri =404;
        access_log off;
        auth_basic off;
    }

    location = /phpmyadmin { return 301 /phpmyadmin/; }
    location /phpmyadmin/ { alias /usr/share/phpmyadmin/; index index.php; try_files \$uri \$uri/ =404; }
    location ~ ^/phpmyadmin/(.+\.php)$ {
        alias /usr/share/phpmyadmin/\$1;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME /usr/share/phpmyadmin/\$1;
        fastcgi_param SCRIPT_NAME /phpmyadmin/\$1;
        fastcgi_pass unix:/run/php/php${php_version}-fpm.sock;
        fastcgi_read_timeout 300;
    }
}
NGINX
  sed -i -E "/api\/databases\/phpmyadmin-sso/s#'[^']+/api/databases/phpmyadmin-sso/'#'${api_scheme}://127.0.0.1:${panel_port}/api/databases/phpmyadmin-sso/'#" /usr/share/phpmyadmin/spanel-signon.php 2>/dev/null || true
  sed -i -E "s#('secure' => )(true|false)#\1${pma_secure}#" /etc/phpmyadmin/conf.d/spanel-signon.php /usr/share/phpmyadmin/spanel-signon.php 2>/dev/null || true
  [[ -n "$host" ]] && sed -i -E "/PmaAbsoluteUri/s#'https?://[^']+/phpmyadmin/'#'${tools_scheme}://${host}/phpmyadmin/'#" /etc/phpmyadmin/conf.d/spanel-signon.php 2>/dev/null || true
}

configure_fastcgi_cache() {
  install -d -o www-data -g www-data -m 0755 /var/cache/nginx/spanel-fastcgi
  find /var/cache/nginx/spanel-fastcgi -mindepth 1 -delete
  cat >/etc/nginx/conf.d/00-spanel-fastcgi-cache.conf <<'NGINX'
fastcgi_cache_path /var/cache/nginx/spanel-fastcgi levels=1:2 keys_zone=SPANEL_FASTCGI:32m inactive=2h max_size=1g use_temp_path=off;
fastcgi_cache_key "$scheme$request_method$host$request_uri";
NGINX
}

# WebSocket upgrade map, shared by every proxied vhost. Without it a
# `proxy_set_header Connection $connection_upgrade` in a site config makes
# nginx fail to start, so this has to exist before any proxy vhost is written.
configure_proxy_upgrade_map() {
  cat >/etc/nginx/conf.d/00-spanel-upgrade-map.conf <<'NGINX'
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
NGINX
}

migrate_nginx_wordpress_csp_worker_src() {
  python3 - <<'PY'
from pathlib import Path

roots = [Path("/etc/nginx/conf.d"), Path("/etc/nginx/sites-enabled")]
needle = "worker-src 'self' blob:;"
anchor = "frame-src 'self' https: blob:;"

for root in roots:
    if not root.exists():
        continue
    for path in sorted(root.glob("*.conf")):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")
        if "Content-Security-Policy" not in text or needle in text:
            continue
        if anchor in text:
            new_text = text.replace(anchor, f"{anchor} {needle}")
        else:
            new_text = text.replace("object-src", f"{needle} object-src")
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            print(f"Updated CSP worker-src in {path}")
PY
}

migrate_user_backup_dir_owner() {
  # The users/ directory under the backup root has never been created by the
  # installer, so on every panel out there it belongs to whichever process
  # happened to reach it first. Where that was a root one it is root:root, the
  # panel cannot make an account directory inside it, and every scheduled
  # backup fails with "[Errno 13] Permission denied" naming a path the
  # operator has no reason to read as a permissions problem.
  local root_dir="${BACKUP_ROOT:-/var/backups/spanel}"
  local users_dir="${root_dir}/users"
  [[ -d "$root_dir" ]] || return 0
  id -u spanel >/dev/null 2>&1 || return 0

  local owner=""
  if [[ -d "$users_dir" ]]; then
    owner="$(stat -c '%U:%G' "$users_dir" 2>/dev/null || true)"
  fi
  install -d -m 0750 -o spanel -g spanel "$users_dir"
  install -d -m 0750 -o spanel -g spanel "${users_dir}/restore"
  if [[ -n "$owner" && "$owner" != "spanel:spanel" ]]; then
    # Hand over what is already in there too. These are the panel's own
    # archives; leaving them root-owned would mean backups run but retention
    # never prunes, which fills the disk quietly instead of loudly.
    chown -R spanel:spanel "$users_dir"
    echo "    backups: ${users_dir} was ${owner}, now owned by the panel"
  fi
}

# Cron lines written before the PHP pinning fix call a bare `php`, which
# resolves through /etc/alternatives to the newest installed version instead of
# the version the website runs on, and carry shell-quoted redirections such as
# '>/dev/null' that never redirect anything. Repair both in place.
migrate_protect_php_from_autoremove() {
  # Repairs what we shipped. Every panel installed before this release told
  # unattended-upgrades to remove unused dependencies, and every PHP
  # extension qualifies: nothing depends on them. One customer lost
  # php8.3-mysql overnight and three WordPress sites with it.
  #
  # Both halves matter. Clearing the setting stops the next removal; marking
  # the packages manual protects them from a plain `apt autoremove` typed by
  # hand, and from the setting being turned back on.
  local conf=/etc/apt/apt.conf.d/51spanel-unattended-upgrades
  if [[ -f "$conf" ]] && grep -q 'Remove-Unused-Dependencies "true"' "$conf"; then
    sed -i 's|Unattended-Upgrade::Remove-Unused-Dependencies "true";|Unattended-Upgrade::Remove-Unused-Dependencies "false";|' "$conf"
    echo "    apt: stopped unattended-upgrades removing \"unused\" packages"
  fi

  local php_packages=()
  mapfile -t php_packages < <(
    dpkg-query -W -f='${Package} ${Status}\n' 'php8.*' 2>/dev/null \
      | awk '$2 == "install" && $3 == "ok" && $4 == "installed" { print $1 }'
  )
  if [[ ${#php_packages[@]} -gt 0 ]]; then
    apt-mark manual "${php_packages[@]}" >/dev/null 2>&1 || true
    echo "    apt: ${#php_packages[@]} PHP package(s) held back from autoremove"
  fi
}

migrate_site_cron_php_binary() {
  local db_path="$APP_DIR/backend/spanel.db"
  [[ -f "$db_path" ]] || return 0
  local py_bin="$APP_DIR/backend/.venv/bin/python"
  [[ -x "$py_bin" ]] || py_bin="python3"
  PYTHONPATH="$APP_DIR/backend" SPANEL_DB_PATH="$db_path" "$py_bin" - <<'PY'
import os
import subprocess
from pathlib import Path

try:
    from app.core.database import SessionLocal
    from app.models.entities import Website
    from app.services import cron as cron_service

    # First, if any legacy user spool crontabs exist in /var/spool/cron/crontabs,
    # migrate their spanel entries into /etc/cron.d/spanel-user-<user> via add_cron
    spool_dir = Path("/var/spool/cron/crontabs")
    with SessionLocal() as db:
        websites_by_domain = {
            (w.domain or "").lower(): w for w in db.query(Website).all()
        }
        if spool_dir.is_dir():
            for spool in sorted(spool_dir.iterdir()):
                if not spool.is_file() or spool.name in ("root", "www-data"):
                    continue
                try:
                    raw_lines = spool.read_text(encoding="utf-8", errors="ignore").splitlines()
                except OSError:
                    continue
                migrated_any = False
                for raw_line in raw_lines:
                    if "# spanel:" not in raw_line:
                        continue
                    parsed = cron_service._parse_cron_line(0, raw_line)
                    dom = raw_line.rsplit("# spanel:", 1)[-1].strip().lower()
                    site = websites_by_domain.get(dom)
                    if site and parsed.get("schedule") and parsed.get("command"):
                        try:
                            cron_service.add_cron(site, parsed["schedule"], parsed["command"])
                            migrated_any = True
                        except Exception:
                            pass
                if migrated_any:
                    subprocess.run(["crontab", "-r", "-u", spool.name], check=False, capture_output=True)
        # Enforce full spanel-cron-jail (/bin/sh -c '...') confinement across all websites
        cron_service.enforce_all_cron_confinement(db)
except Exception as exc:
    print(f"WARNING: cron confinement migration notice: {exc}")
PY
}

ensure_terminal_tools() {
  local missing=()
  command -v composer >/dev/null 2>&1 || missing+=(composer)
  command -v zip >/dev/null 2>&1 || missing+=(zip)
  command -v unzip >/dev/null 2>&1 || missing+=(unzip)
  if [[ ${#missing[@]} -gt 0 ]]; then
    log "Installing terminal/file-manager tools: ${missing[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get update --allow-releaseinfo-change
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
  fi
}

harden_existing_panel_users() {
  local user home_dir site_dir secret
  getent group spanel-sftp >/dev/null || return 0
  chown root:root /home
  chmod 0711 /home
  chmod a-s /home 2>/dev/null || true
  chmod -t /home 2>/dev/null || true
  while IFS= read -r user; do
    [[ -n "$user" ]] || continue
    case "$user" in
      root|daemon|bin|sys|sync|games|man|lp|mail|news|uucp|proxy|www-data|backup|list|irc|_apt|nobody|spanel|spanel-sites|spanel-sftp|mysql|redis|nginx)
        continue ;;
    esac
    id -u "$user" >/dev/null 2>&1 || continue
    getent group "$user" >/dev/null || groupadd "$user" 2>/dev/null || true
    usermod -aG "$user" www-data 2>/dev/null || true
    home_dir="/home/$user"
    usermod --home "$home_dir" --shell /usr/sbin/nologin --gid "$user" "$user" 2>/dev/null || true
    mkdir -p "$home_dir"
    chown "root:$user" "$home_dir"
    chmod 0751 "$home_dir"
    chmod a-s "$home_dir" 2>/dev/null || true
    chmod -t "$home_dir" 2>/dev/null || true
    if command -v setfacl >/dev/null 2>&1; then
      setfacl -b -k "$home_dir" 2>/dev/null || true
    fi
    find "$home_dir" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null | while IFS= read -r -d '' site_dir; do
      [[ "$(basename "$site_dir")" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]] || continue
      chown -R "$user:spanel-sites" "$site_dir" 2>/dev/null || true
      if command -v setfacl >/dev/null 2>&1; then
        setfacl -Rb "$site_dir" 2>/dev/null || true
        find "$site_dir" -type d -exec setfacl -k {} + 2>/dev/null || true
      fi
      # The modes are the owner's: the file manager lets them make a script
      # 755, an upload folder 777 or a secret 600. This sweep once reset every
      # file of every site to 644 on each update that touched this script,
      # undoing those choices on customer servers (operator, 2026-09-27). It
      # only exists to move a site off the pre-v1.0.106 layout - folders 2750,
      # files 0640 - so it runs on a site still on that layout, whose own
      # folder is 2750, and never again once the folder is 755.
      [[ "$(stat -c '%a' "$site_dir" 2>/dev/null)" == "2750" ]] || continue
      find "$site_dir" -type d -exec chmod 755 {} + 2>/dev/null || true
      find "$site_dir" -type d -exec chmod a-s {} + 2>/dev/null || true
      find "$site_dir" -type d -exec chmod -t {} + 2>/dev/null || true
      find "$site_dir" -type f -exec chmod 644 {} + 2>/dev/null || true
      for secret in wp-config.php .env .my.cnf; do
        find "$site_dir" -type f -name "$secret" -exec chmod 640 {} + 2>/dev/null || true
      done
    done
    if [[ -d "/var/lib/php/uploads/$user" ]]; then
      chown "$user:spanel-sites" "/var/lib/php/uploads/$user" 2>/dev/null || true
      chmod 2700 "/var/lib/php/uploads/$user" 2>/dev/null || true
      chmod g+s "/var/lib/php/uploads/$user" 2>/dev/null || true
    fi
  done < <(getent group spanel-sftp | awk -F: '{gsub(",", "\n", $4); print $4}')
}

install_panel_runtime() {
  local env_file="$APP_DIR/backend/.env"
  [[ -f "$env_file" ]] || return 0
  local panel_port panel_url server_ip sshd_config sshd_backup
  panel_port="$(env_get PANEL_PORT)"
  panel_port="${panel_port:-2222}"
  server_ip="$(detect_server_ip)"
  panel_url="$(env_get PANEL_URL)"
  panel_url="${panel_url:-http://${server_ip:-127.0.0.1}:${panel_port}}"

  env_set_default PANEL_PORT "$panel_port"
  env_set_default PANEL_URL "$panel_url"
  env_set_default PANEL_DOMAIN ""
  env_set_default PANEL_SSL_CERT ""
  env_set_default PANEL_SSL_KEY ""
  env_set_default PANEL_SSL_MODE ""
  ensure_panel_https "$panel_port" "$server_ip"
  env_set_default FRONTEND_DIST "$APP_DIR/frontend/dist"
  env_set_default REDIS_URL "redis://localhost:6379/0"
  env_set_default RATE_LIMIT_BACKEND "redis"
  if [[ -z "$(env_get ALLOWED_ORIGINS)" ]]; then
    env_set_default ALLOWED_ORIGINS "$panel_url"
  fi

  remove_filebrowser_runtime

  getent group spanel-sites >/dev/null || groupadd --system spanel-sites
  getent group spanel-sftp >/dev/null || groupadd --system spanel-sftp
  if ! command -v setfacl >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update --allow-releaseinfo-change
    DEBIAN_FRONTEND=noninteractive apt-get install -y acl
  fi
  usermod -aG spanel-sites spanel 2>/dev/null || true
  usermod -aG spanel-sites www-data 2>/dev/null || true
  # A recursive chown and ACL clean-up over every file of every site, plus the
  # one-time move of pre-v1.0.106 sites to 755/644. It retrofits old installs
  # to the current ownership policy; once that policy (which lives in this
  # script) is applied it does not drift, so re-run it only when the script
  # itself changed. It never touches the modes of a site already on 755/644.
  if step_inputs_changed user-hardening "${SPANEL_UPDATE_ORIGINAL_SCRIPT:-$SOURCE_DIR/installer/update.sh}"; then
    harden_existing_panel_users
    step_mark_done user-hardening "${SPANEL_UPDATE_ORIGINAL_SCRIPT:-$SOURCE_DIR/installer/update.sh}"
  else
    echo "  (site directory permissions already match this release; skipping the sweep)"
  fi
  install -d -o root -g spanel -m 2775 /etc/nginx/conf.d
  chmod g+s /etc/nginx/conf.d 2>/dev/null || true
  install -d -o root -g spanel -m 2775 /etc/nginx/spanel/custom
  chmod g+s /etc/nginx/spanel/custom 2>/dev/null || true
  install -d -o spanel -g spanel -m 0750 /var/lib/spanel /var/lib/spanel/geoip
  # DirectAdmin import staging dirs
  install -d -o spanel -g spanel -m 0750 /home/admin/spanel_backups/da
  install -d -o spanel -g spanel -m 0750 /var/lib/spanel/da-import
  install -d -o spanel -g spanel -m 0750 /var/lib/spanel/import-stage
  if [[ -f "$SOURCE_DIR/installer/files/spanel-memory-guard" ]]; then
    install -m 0755 -o root -g root "$SOURCE_DIR/installer/files/spanel-memory-guard" /usr/local/sbin/spanel-memory-guard
    # Re-run every update: the servers that need this most are the ones
    # already deployed, and it is idempotent.
    /usr/local/sbin/spanel-memory-guard || true
  fi
  if command -v sshd >/dev/null 2>&1; then
    sshd_config="/etc/ssh/sshd_config"
    sshd_backup="${sshd_config}.spanel.bak"
    getent group spanel-sftp-site >/dev/null || groupadd --system spanel-sftp-site
    # Root owns the chroot roots and everything above them. Deliberately not
    # under /var/lib/spanel, which belongs to the panel account.
    install -d -o root -g root -m 0755 /var/lib/spanel-sftp
    install -d -o root -g root -m 0755 /run/sshd
    rm -f /etc/ssh/sshd_config.d/99-spanel-sftp.conf 2>/dev/null || true
    touch "$sshd_config"
    cp "$sshd_config" "$sshd_backup"
    sed -i '/^# BEGIN SPANEL SFTP USERS$/,/^# END SPANEL SFTP USERS$/d' "$sshd_config"
    cat >>"$sshd_config" <<'SSHD'
# BEGIN SPANEL SFTP USERS
# Allow SPanel Linux users to log in with SFTP using their panel password.
# SSH shells are intentionally disabled; /home/%u is a root-owned chroot.
Match Group spanel-sftp
    PasswordAuthentication yes
    ChrootDirectory /home/%u
    ForceCommand internal-sftp -d /
    PermitTTY no
    X11Forwarding no
    AllowTcpForwarding no
    PermitTunnel no

# Per-website sub-accounts. Same lockdown, a narrower chroot: one root-owned
# directory holding a bind mount of exactly one site. The chroot root is NOT
# under /var/lib/spanel - that directory belongs to the panel account, and a
# chroot root the confined account can write is not a boundary at all.
#
# This block must stay AFTER the one above. sshd applies every matching block
# in order and lets later ones win; a sub-account is not a member of
# spanel-sftp, so in practice only one of the two ever applies.
Match Group spanel-sftp-site
    PasswordAuthentication yes
    ChrootDirectory /var/lib/spanel-sftp/%u
    ForceCommand internal-sftp -d /
    PermitTTY no
    X11Forwarding no
    AllowTcpForwarding no
    PermitTunnel no
# END SPANEL SFTP USERS
SSHD
    if sshd -t; then
      systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
    else
      cp "$sshd_backup" "$sshd_config"
      echo "WARNING: invalid SSHD configuration; skipped SPanel SFTP password block"
    fi
  fi

  cat >/usr/local/sbin/spanel-api-start <<STARTER
#!/usr/bin/env bash
# app.serve builds the uvicorn server in Python: the same options the command
# line used to take, plus one certificate per hostname. The panel is therefore
# reachable on every domain on this machine that has a certificate, instead of
# only on the one PANEL_DOMAIN names.
#
# Trusted forwarders: only the local Nginx (127.0.0.1) is allowed to set
# X-Forwarded-For / X-Forwarded-Proto. Anything else (direct hits on
# the configured panel port) cannot spoof the audit log IP or the login rate-limit key.
set -euo pipefail
cd ${APP_DIR}/backend
exec ${APP_DIR}/backend/.venv/bin/python -m app.serve
STARTER
  chmod 0755 /usr/local/sbin/spanel-api-start
  mkdir -p /etc/systemd/system/spanel-api.service.d
  cat >/etc/systemd/system/spanel-api.service.d/20-panel-port.conf <<SERVICE
[Service]
WorkingDirectory=${APP_DIR}/backend
EnvironmentFile=
EnvironmentFile=${APP_DIR}/backend/.env
Environment=HOME=${APP_DIR}
ExecStart=
ExecStart=/usr/local/sbin/spanel-api-start
SupplementaryGroups=www-data spanel-sites
ProtectHome=false
ReadWritePaths=
ReadWritePaths=${APP_DIR} /home /var/backups/spanel /etc/nginx/conf.d /etc/nginx/spanel/custom /tmp /var/lib/spanel /home/admin/spanel_backups/da /var/lib/spanel/da-import /var/lib/spanel/import-stage
SERVICE
  cat >/etc/systemd/system/spanel-backup-scheduler.service <<SERVICE
[Unit]
Description=SPanel scheduled backup runner
After=network.target mariadb.service

[Service]
Type=oneshot
User=spanel
Group=spanel
SupplementaryGroups=www-data spanel-sites
WorkingDirectory=${APP_DIR}/backend
EnvironmentFile=${APP_DIR}/backend/.env
Environment=HOME=${APP_DIR}
Environment=SPANEL_USE_HELPER=true
ExecStart=${APP_DIR}/backend/.venv/bin/python -m app.services.backup_scheduler
NoNewPrivileges=false
ProtectSystem=false
ProtectHome=false
ReadWritePaths=/home /var/backups/spanel /etc/nginx/conf.d /etc/nginx/spanel/custom /tmp /var/lib/spanel ${APP_DIR} /home/admin/spanel_backups/da /var/lib/spanel/da-import /var/lib/spanel/import-stage
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SERVICE
  cat >/etc/systemd/system/spanel-backup-scheduler.timer <<'SERVICE'
[Unit]
Description=Run SPanel scheduled backups every minute

[Timer]
OnBootSec=90s
OnUnitActiveSec=60s
AccuracySec=15s
Persistent=true

[Install]
WantedBy=timers.target
SERVICE
  cat >/etc/systemd/system/spanel-malware-scheduler.service <<SERVICE
[Unit]
Description=SPanel weekly malware scan runner
After=network.target clamav-daemon.service

[Service]
Type=oneshot
# The runner blocks until the scan it starts finishes (a whole-server scan
# can take hours). Without this, systemd's 90s default start timeout kills it
# and the scan lands in 'interrupted'.
TimeoutStartSec=infinity
User=spanel
Group=spanel
SupplementaryGroups=www-data spanel-sites
WorkingDirectory=${APP_DIR}/backend
EnvironmentFile=${APP_DIR}/backend/.env
Environment=HOME=${APP_DIR}
Environment=SPANEL_USE_HELPER=true
ExecStart=${APP_DIR}/backend/.venv/bin/python -m app.services.malware_schedule
NoNewPrivileges=false
ProtectSystem=false
ProtectHome=false
ReadWritePaths=${APP_DIR} /home ${BACKUP_ROOT:-/var/backups/spanel} /tmp /var/lib/spanel
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SERVICE

  cat >/etc/systemd/system/spanel-malware-scheduler.timer <<'SERVICE'
[Unit]
Description=Ask every quarter of an hour whether the weekly malware scan is due

[Timer]
# Often enough that a server asleep at the appointed hour still scans when it
# comes back, while the runner itself refuses to start twice in one window.
OnBootSec=5min
OnUnitActiveSec=15min
AccuracySec=1min
Persistent=true

[Install]
WantedBy=timers.target
SERVICE

  # The Notifications addon's watcher. It exits at once while the addon is
  # off, so installing it everywhere costs one short Python start every five
  # minutes and nothing else.
  cat >/etc/systemd/system/spanel-notify.service <<SERVICE
[Unit]
Description=SPanel notifications watcher (services, disk, firewall, certificates)
After=network.target

[Service]
Type=oneshot
TimeoutStartSec=15min
User=spanel
Group=spanel
SupplementaryGroups=www-data spanel-sites
WorkingDirectory=${APP_DIR}/backend
EnvironmentFile=${APP_DIR}/backend/.env
Environment=HOME=${APP_DIR}
Environment=SPANEL_USE_HELPER=true
ExecStart=${APP_DIR}/backend/.venv/bin/python -m app.services.notify_watch
NoNewPrivileges=false
ProtectSystem=false
ProtectHome=false
ReadWritePaths=${APP_DIR} /var/lib/spanel /tmp
PrivateTmp=true
SERVICE

  cat >/etc/systemd/system/spanel-notify.timer <<'SERVICE'
[Unit]
Description=Run the SPanel notifications watcher every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
SERVICE

  # spanel-helper refuses to run unless SUDO_USER names the panel account, so
  # this unit sets it. The sibling boot units (firewall, blocklist) do the same:
  # the helper then runs as root here with no real sudo in front of it.
  cat >/etc/systemd/system/spanel-autotune.service <<'SERVICE'
[Unit]
Description=Auto tune SPanel PHP-FPM pools and MariaDB for this VPS
After=network-online.target mariadb.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=SUDO_USER=spanel
ExecStart=/usr/local/sbin/spanel-helper php-fpm-retune
ExecStart=/usr/local/sbin/spanel-helper mariadb-retune
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
SERVICE
  systemctl daemon-reload
  systemctl enable spanel-autotune.service >/dev/null 2>&1 || true
  # Keep the clock honest for TOTP: the spanel-timesync timer steps the clock
  # from an HTTPS Date header when UDP 123 is blocked. An update never changes
  # the server timezone - that stays the operator's call (PANEL_TIMEZONE at
  # install time, or `timedatectl set-timezone` by hand).
  cat >/etc/systemd/system/spanel-timesync.service <<'SERVICE'
[Unit]
Description=Correct the SPanel server clock when NTP cannot reach the network
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=SUDO_USER=spanel
ExecStart=/usr/local/sbin/spanel-helper time-sync
RemainAfterExit=no
SERVICE
  cat >/etc/systemd/system/spanel-timesync.timer <<'SERVICE'
[Unit]
Description=Check the SPanel server clock at boot and hourly

[Timer]
OnBootSec=45s
OnUnitActiveSec=1h
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
SERVICE
  systemctl daemon-reload
  systemctl enable spanel-timesync.timer >/dev/null 2>&1 || true
  rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf 2>/dev/null || true
  rm -f /etc/nginx/sites-enabled/spanel.conf /etc/nginx/sites-available/spanel.conf 2>/dev/null || true
  write_tools_nginx_config
  if [[ -f /usr/share/phpmyadmin/spanel-signon.php ]]; then
    local scheme="http"
    if [[ -n "$(env_get PANEL_SSL_CERT)" && -n "$(env_get PANEL_SSL_KEY)" ]]; then
      scheme="https"
    fi
    sed -i -E "/api\/databases\/phpmyadmin-sso/s#'[^']+/api/databases/phpmyadmin-sso/'#'${scheme}://127.0.0.1:${panel_port}/api/databases/phpmyadmin-sso/'#" /usr/share/phpmyadmin/spanel-signon.php || true
  fi
}

panel_healthcheck() {
  local port
  port="$(env_get PANEL_PORT)"
  port="${port:-2222}"
  curl -kfsS --connect-timeout 2 --max-time 5 "https://127.0.0.1:${port}/api/health" >/dev/null 2>&1 \
    || curl -fsS --connect-timeout 2 --max-time 5 "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1
}

remove_panel_auto_update_timer() {
  systemctl disable --now spanel-auto-update.timer 2>/dev/null || true
  rm -f /etc/systemd/system/spanel-auto-update.service /etc/systemd/system/spanel-auto-update.timer
  systemctl daemon-reload >/dev/null 2>&1 || true
}

refresh_spanel_mariadb_grants() {
  local defaults_file="$APP_DIR/.my.cnf"
  local mysql_bin
  mysql_bin="$(command -v mariadb || command -v mysql || true)"
  [[ -n "$mysql_bin" ]] || return 0
  local password
  password=""
  if [[ -f "$defaults_file" ]]; then
    password="$(awk -F= '
      /^\[client\]/ { in_client=1; next }
      /^\[/ { in_client=0 }
      in_client && $1 == "password" {
        value=$0; sub(/^[^=]*=/, "", value); gsub(/^"|"$/, "", value); print value; exit
      }
    ' "$defaults_file")"
  fi
  if [[ -z "$password" ]]; then
    password="$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)"
  fi
  "$mysql_bin" <<SQL
CREATE USER IF NOT EXISTS 'spanel'@'localhost' IDENTIFIED BY '${password}';
ALTER USER 'spanel'@'localhost' IDENTIFIED BY '${password}';
GRANT ALL PRIVILEGES ON *.* TO 'spanel'@'localhost' WITH GRANT OPTION;
FLUSH PRIVILEGES;
SQL
  cat >"$defaults_file" <<MYCNF
[client]
user=spanel
password="${password}"
host=localhost

[mysqldump]
user=spanel
password="${password}"
host=localhost
MYCNF
  chown spanel:spanel "$defaults_file"
  chmod 0600 "$defaults_file"
}

ensure_panel_runtime_ownership() {
  id -u spanel >/dev/null 2>&1 || return 0
  # Everything under backend/ goes to spanel EXCEPT .venv. This script runs as
  # root and does `source .venv/bin/activate`, `.venv/bin/pip install` and
  # `.venv/bin/python -m py_compile` with the cwd set to backend/ - so a
  # spanel-owned venv (or a spanel-writable backend/ that `python -m` picks up
  # via sys.path) is a file the confined account writes and root executes. That
  # bypasses every argv check in spanel-helper, which is the whole point of the
  # sudo trampoline. Root keeps the venv; the service only reads and executes it.
  if [[ -d "$APP_DIR/backend" ]]; then
    find "$APP_DIR/backend" -path "$APP_DIR/backend/.venv" -prune \
      -o -exec chown spanel:spanel {} + 2>/dev/null || true
    # Reclaim it on an upgrade from a build that handed it to spanel.
    [[ -d "$APP_DIR/backend/.venv" ]] && chown -R root:root "$APP_DIR/backend/.venv" 2>/dev/null || true
  fi
  # Same rule for the frontend, and this is the call site that matters: this
  # function runs before build_frontend, so the `npm run build` below executes a
  # tree this line has already handed over. package.json's "build" is
  # `vite build`, which npm resolves to node_modules/.bin/vite, so root loads
  # the whole vite/rolldown/lightningcss graph - two compiled .node addons
  # included - out of node_modules. The rsync keeps node_modules across updates
  # (protect filter below), so anything written there stays written. Only this
  # script ever creates node_modules; spanel just needs to read it.
  if [[ -d "$APP_DIR/frontend" ]]; then
    find "$APP_DIR/frontend" -path "$APP_DIR/frontend/node_modules" -prune \
      -o -exec chown spanel:spanel {} + 2>/dev/null || true
    # Reclaim it on an upgrade from a build that handed it to spanel.
    [[ -d "$APP_DIR/frontend/node_modules" ]] && chown -R root:root "$APP_DIR/frontend/node_modules" 2>/dev/null || true
  fi
  [[ -f "$APP_DIR/.my.cnf" ]] && chown spanel:spanel "$APP_DIR/.my.cnf" 2>/dev/null || true
  [[ -f "$APP_DIR/.my.cnf" ]] && chmod 0600 "$APP_DIR/.my.cnf" 2>/dev/null || true
  [[ -d /var/lib/spanel ]] && chown spanel:spanel /var/lib/spanel 2>/dev/null || true
  [[ -d /var/lib/spanel/geoip ]] && chown -R spanel:spanel /var/lib/spanel/geoip 2>/dev/null || true
  [[ -d /var/lib/spanel/assets ]] && chown -R spanel:spanel /var/lib/spanel/assets 2>/dev/null || true
  [[ -f "$APP_DIR/backend/.env" ]] && chmod 0640 "$APP_DIR/backend/.env"
}

# --- Snapshot the SQLite DB before doing anything ---------------------------
backup_db() {
  local db_path="$APP_DIR/backend/spanel.db"
  if [[ ! -f "$db_path" ]]; then
    return 0
  fi
  local snap_dir="${BACKUP_ROOT:-/var/backups/spanel}/db-snapshots"
  install -d -m 0750 "$snap_dir"
  if id -u spanel >/dev/null 2>&1; then
    chown spanel:spanel "$snap_dir" 2>/dev/null || true
  fi
  local stamp
  stamp=$(date -u +%Y%m%d-%H%M%S)
  local snap="$snap_dir/spanel-$stamp.db"
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$db_path" ".backup '$snap'"
  else
    cp -a "$db_path" "$snap"
  fi
  echo "DB snapshot saved: $snap"
  # Keep the 10 most recent snapshots.
  ls -1t "$snap_dir"/spanel-*.db 2>/dev/null | tail -n +11 | xargs -r rm -f
}

if [[ -n "${SPANEL_UPDATE_STAGE2:-}" ]]; then
  log "Continuing with the updater shipped in this release"
else
  log "Backing up SQLite DB before update"
  backup_db
  write_update_state "checking" "" "Checking for SPanel releases"
  update_progress 5 "checking" "Backing up SQLite DB before update"
fi

# --- Fetch source -----------------------------------------------------------
if [[ "$SKIP_PULL" == "true" ]]; then
  if [[ "$(readlink -f "$SOURCE_DIR")" == "$(readlink -f "$APP_DIR")" ]]; then
    fail "SOURCE_DIR ($SOURCE_DIR) and APP_DIR ($APP_DIR) must be different."
  fi
  UPDATE_REF="${SPANEL_UPDATE_REF_OVERRIDE:-local:${SOURCE_DIR}}"
  write_update_state "updating" "$UPDATE_REF" "Syncing SPanel from ${SOURCE_DIR}"
else
  case "$UPDATE_CHANNEL" in
    release)
      log "Checking latest release from ${REPO_URL}"
      RELEASE_TAG="$(latest_release_tag || true)"
      HEAD_SHA="$(git ls-remote "$REPO_URL" "refs/heads/${BRANCH:-main}" 2>/dev/null | awk 'NR==1 { print $1 }' || true)"
      TAG_SHA=""
      if [[ -n "$RELEASE_TAG" ]]; then
        TAG_SHA="$(git ls-remote "$REPO_URL" "refs/tags/${RELEASE_TAG}^{}" "refs/tags/${RELEASE_TAG}" 2>/dev/null | tail -n 1 | awk '{ print $1 }' || true)"
      fi
      if [[ -n "$RELEASE_TAG" && ( -z "$HEAD_SHA" || "$HEAD_SHA" == "$TAG_SHA" ) ]]; then
        UPDATE_REF="$RELEASE_TAG"
        write_update_state "updating" "$UPDATE_REF" "Updating SPanel from ${UPDATE_REF}"
        download_release_source "$RELEASE_TAG"
        echo "Release: ${RELEASE_TAG}"
      else
        log "Pulling latest branch ${BRANCH:-main} from ${REPO_URL}"
        RELEASE_WORK_DIR="$(mktemp -d /tmp/spanel-release-update.XXXXXX)"
        archive="${RELEASE_WORK_DIR}/spanel-release.zip"
        extract_dir="${RELEASE_WORK_DIR}/extract"
        base="${REPO_URL%.git}"
        curl -fL --connect-timeout 10 --max-time 300 "${base}/archive/HEAD.zip" -o "$archive"
        mkdir -p "$extract_dir"
        unzip -q "$archive" -d "$extract_dir"
        SOURCE_DIR="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
        [[ -n "$SOURCE_DIR" && -d "$SOURCE_DIR/backend" && -d "$SOURCE_DIR/frontend" ]] || fail "Archive does not contain backend/frontend source"
        UPDATE_REF="v$(cat "$SOURCE_DIR/VERSION" 2>/dev/null | tr -d '[:space:]' || echo '1.0.7')"
        write_update_state "updating" "$UPDATE_REF" "Updating SPanel from ${UPDATE_REF}"
      fi
      ;;
    tag)
      [[ -n "$RELEASE_TAG" ]] || fail "--tag requires a release tag"
      HEAD_SHA="$(git ls-remote "$REPO_URL" "refs/heads/${BRANCH:-main}" 2>/dev/null | awk 'NR==1 { print $1 }' || true)"
      TAG_SHA="$(git ls-remote "$REPO_URL" "refs/tags/${RELEASE_TAG}^{}" "refs/tags/${RELEASE_TAG}" 2>/dev/null | tail -n 1 | awk '{ print $1 }' || true)"
      if [[ -n "${SPANEL_FORCE_TAG:-}" || -z "$HEAD_SHA" || "$HEAD_SHA" == "$TAG_SHA" ]]; then
        UPDATE_REF="$RELEASE_TAG"
        write_update_state "updating" "$UPDATE_REF" "Updating SPanel from ${UPDATE_REF}"
        update_progress 15 "fetching" "Downloading release ${RELEASE_TAG}"
        download_release_source "$RELEASE_TAG"
        echo "Release: ${RELEASE_TAG}"
      else
        log "Tag ${RELEASE_TAG} is behind ${BRANCH:-main}; pulling latest branch ${BRANCH:-main} from ${REPO_URL}"
        RELEASE_WORK_DIR="$(mktemp -d /tmp/spanel-release-update.XXXXXX)"
        archive="${RELEASE_WORK_DIR}/spanel-release.zip"
        extract_dir="${RELEASE_WORK_DIR}/extract"
        base="${REPO_URL%.git}"
        curl -fL --connect-timeout 10 --max-time 300 "${base}/archive/HEAD.zip" -o "$archive"
        mkdir -p "$extract_dir"
        unzip -q "$archive" -d "$extract_dir"
        SOURCE_DIR="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
        [[ -n "$SOURCE_DIR" && -d "$SOURCE_DIR/backend" && -d "$SOURCE_DIR/frontend" ]] || fail "Archive does not contain backend/frontend source"
        UPDATE_REF="v$(cat "$SOURCE_DIR/VERSION" 2>/dev/null | tr -d '[:space:]' || echo '1.0.7')"
        write_update_state "updating" "$UPDATE_REF" "Updating SPanel from ${UPDATE_REF}"
      fi
      ;;
    branch)
      if [[ "$(readlink -f "$SOURCE_DIR")" == "$(readlink -f "$APP_DIR")" ]]; then
        fail "SOURCE_DIR ($SOURCE_DIR) and APP_DIR ($APP_DIR) must be different."
      fi
      if [[ ! -d "$SOURCE_DIR/.git" ]]; then
        if [[ -e "$SOURCE_DIR" && -n "$(ls -A "$SOURCE_DIR" 2>/dev/null)" ]]; then
          source_backup="${SOURCE_DIR}.release-archive-$(date -u +%Y%m%d-%H%M%S)"
          log "Archiving non-git release source to ${source_backup}"
          cd /
          mv "$SOURCE_DIR" "$source_backup"
        fi
        log "Cloning ${REPO_URL} to ${SOURCE_DIR} with remote ${GIT_REMOTE}"
        git clone -o "$GIT_REMOTE" "$REPO_URL" "$SOURCE_DIR"
      fi
      cd "$SOURCE_DIR"
      ensure_git_remote
      remote_branch="${GIT_REMOTE}/${BRANCH}"
      log "Pulling latest from ${remote_branch}"
      update_progress 15 "fetching" "Pulling latest from ${remote_branch}"
      git fetch --force --prune "$GIT_REMOTE" "+refs/heads/${BRANCH}:refs/remotes/${GIT_REMOTE}/${BRANCH}" --tags \
        || git fetch --force --prune "$GIT_REMOTE" "+refs/heads/${BRANCH}:refs/remotes/${GIT_REMOTE}/${BRANCH}"
      UPDATE_REF="$remote_branch"
      write_update_state "updating" "$UPDATE_REF" "Updating SPanel from ${UPDATE_REF}"
      reset_worktree_to_ref "$remote_branch" "$BRANCH"
      echo "HEAD: $(git rev-parse --short HEAD) - $(git log -1 --pretty=%s)"
      ;;
    *)
      fail "Unsupported UPDATE_CHANNEL: $UPDATE_CHANNEL"
      ;;
  esac
fi

# --- Validate ---------------------------------------------------------------
[[ -d "$SOURCE_DIR/backend"  ]] || fail "Missing $SOURCE_DIR/backend"
[[ -d "$SOURCE_DIR/frontend" ]] || fail "Missing $SOURCE_DIR/frontend"

# /usr/local/sbin/spanel-update is the launcher (installer/files/spanel-update):
# it fetches this file from the ref being installed and runs it, so an update
# always runs the newest steps. Installed here, as soon as the new source can
# be read, so the next run has it whatever fails later. A source without the
# launcher (an old branch) leaves a copy of this file, as before.
install_update_command() {
  if [[ -f "$SOURCE_DIR/installer/files/spanel-update" ]]; then
    install -m 0755 -o root -g root "$SOURCE_DIR/installer/files/spanel-update" /usr/local/sbin/spanel-update
  elif [[ -f "$SOURCE_DIR/installer/update.sh" ]]; then
    install -m 0755 -o root -g root "$SOURCE_DIR/installer/update.sh" /usr/local/sbin/spanel-update
  fi
}
install_update_command

# bash reads a script as it runs, which is why this one re-execs a copy of
# itself out of /tmp. That copy is the updater from the release before this
# one, so anything an update changes about updating itself only takes effect
# the next time somebody updates. The release that moved the panel onto its own
# start-up script showed how that ends: the new panel ran under the old command
# line, and SNI stayed off, until the server was updated a second time.
#
# The new source is on disk and has just been checked, so hand over to its
# updater here - once, guarded by SPANEL_UPDATE_STAGE2. SOURCE_DIR is passed
# along already prepared, so the second stage fetches nothing again.
if [[ -z "${SPANEL_UPDATE_STAGE2:-}" && -f "$SOURCE_DIR/installer/update.sh" ]] \
  && ! cmp -s "$SOURCE_DIR/installer/update.sh" "${SPANEL_UPDATE_STABLE_COPY:-/nonexistent}"; then
  log "The updater changed in this release; continuing with the new one"
  update_progress 20 "syncing" "Handing over to the updater from ${UPDATE_REF:-the new release}"
  stage2_copy="$(mktemp /tmp/spanel-update-stage2.XXXXXX.sh)"
  cp "$SOURCE_DIR/installer/update.sh" "$stage2_copy"
  chmod 0700 "$stage2_copy"
  # The command line was consumed by the argument loop at the top, so the
  # second stage takes its configuration from the environment. exec replaces
  # this process, so the EXIT trap here never runs: the second stage is handed
  # everything this one would have cleaned up.
  SPANEL_UPDATE_STAGE2=1 \
  SPANEL_UPDATE_STABLE_COPY="$stage2_copy" \
  SPANEL_UPDATE_PREVIOUS_COPY="${SPANEL_UPDATE_STABLE_COPY:-}" \
  SPANEL_UPDATE_ORIGINAL_SCRIPT="$SOURCE_DIR/installer/update.sh" \
  SPANEL_UPDATE_REF_OVERRIDE="${UPDATE_REF:-}" \
  RELEASE_WORK_DIR="${RELEASE_WORK_DIR:-}" \
  SOURCE_DIR="$SOURCE_DIR" \
  SKIP_PULL=true \
    exec /bin/bash "$stage2_copy"
fi

# --- Sync code into APP_DIR -------------------------------------------------
log "Syncing source to $APP_DIR"
mkdir -p "$APP_DIR"

if command -v rsync >/dev/null 2>&1; then
  # --filter='protect ...' keeps the destination file even when --delete
  # would otherwise remove it because the source side doesn't have it. We use
  # this for runtime artefacts that the installer creates: .env, .venv,
  # spanel.db, .my.cnf.
  rsync -a --delete \
    --filter='protect /.env' \
    --filter='protect /.venv' \
    --filter='protect /.venv/**' \
    --filter='protect /spanel.db' \
    --filter='protect /.my.cnf' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    "$SOURCE_DIR/backend/" "$APP_DIR/backend/"
  rsync -a --delete \
    --filter='protect /node_modules' \
    --filter='protect /node_modules/**' \
    --filter='protect /dist' \
    --filter='protect /dist/**' \
    --filter='protect /.vite' \
    --filter='protect /.vite/**' \
    "$SOURCE_DIR/frontend/" "$APP_DIR/frontend/"
else
  cp -r "$SOURCE_DIR/backend/."  "$APP_DIR/backend/"
  cp -r "$SOURCE_DIR/frontend/." "$APP_DIR/frontend/"
fi
if [[ -f "$SOURCE_DIR/VERSION" ]]; then
  install -m 0644 "$SOURCE_DIR/VERSION" "$APP_DIR/VERSION"
fi
ensure_panel_runtime_ownership

log "Removing deprecated panel auto update timer"
remove_panel_auto_update_timer

# Defensive: if .env still doesn't exist (e.g. fresh deploy syncing on top of
# nothing), leave a clear error message.
if [[ ! -f "$APP_DIR/backend/.env" ]]; then
  fail "$APP_DIR/backend/.env is missing. Run installer/install.sh first or restore .env from backup."
fi
log "Installing direct panel runtime"
update_progress 25 "syncing" "Syncing source into ${APP_DIR}"
install_panel_runtime
log "Configuring Nginx FastCGI cache"
configure_fastcgi_cache
configure_proxy_upgrade_map
ensure_terminal_tools
venv_needs_recreate=false
if [[ ! -x "$APP_DIR/backend/.venv/bin/uvicorn" ]]; then
  venv_needs_recreate=true
elif ! head -n1 "$APP_DIR/backend/.venv/bin/uvicorn" 2>/dev/null | grep -Fq "$APP_DIR/backend/.venv"; then
  venv_needs_recreate=true
fi
if [[ "$venv_needs_recreate" == "true" ]]; then
  log "Recreating Python virtualenv (missing or stale path)"
  rm -rf "$APP_DIR/backend/.venv"
  python3 -m venv "$APP_DIR/backend/.venv"
fi

# --- Refresh helper + sudoers (idempotent) ---------------------------------
if [[ -f "$SOURCE_DIR/installer/files/spanel-helper.sh" ]]; then
  log "Refreshing /usr/local/sbin/spanel-helper and /etc/sudoers.d/spanel"
  update_progress 40 "runtime" "Refreshing panel helper and runtime"
  if id -u spanel >/dev/null 2>&1; then
    install -m 0750 -o root -g spanel "$SOURCE_DIR/installer/files/spanel-helper.sh" /usr/local/sbin/spanel-helper
    sed -i "s#^APP_DIR=\"/opt/spanel\"#APP_DIR=\"${APP_DIR}\"#" /usr/local/sbin/spanel-helper
    if [[ -f "$SOURCE_DIR/installer/files/spanel-cron-jail.sh" ]]; then
      install -m 0755 -o root -g root "$SOURCE_DIR/installer/files/spanel-cron-jail.sh" /usr/local/bin/spanel-cron-jail
    fi
    if [[ -f "$SOURCE_DIR/installer/files/spanel_webshells.yar" ]]; then
      install -d -m 0755 -o root -g spanel /etc/spanel/yara
      install -m 0644 -o root -g spanel "$SOURCE_DIR/installer/files/spanel_webshells.yar" /etc/spanel/yara/spanel_webshells.yar
    fi
    if [[ -f "$SOURCE_DIR/installer/files/spanel-watchdog.service" && -f "$SOURCE_DIR/installer/files/spanel-watchdog.timer" ]]; then
      install -m 0644 -o root -g root "$SOURCE_DIR/installer/files/spanel-watchdog.service" /etc/systemd/system/spanel-watchdog.service
      sed -i "s#/opt/spanel#${APP_DIR}#g" /etc/systemd/system/spanel-watchdog.service
      install -m 0644 -o root -g root "$SOURCE_DIR/installer/files/spanel-watchdog.timer" /etc/systemd/system/spanel-watchdog.timer
      systemctl daemon-reload >/dev/null 2>&1 || true
      systemctl enable --now spanel-watchdog.timer >/dev/null 2>&1 || true
    fi
    install -m 0440 -o root -g root  "$SOURCE_DIR/installer/files/spanel-sudoers"   /etc/sudoers.d/spanel
    visudo -c -f /etc/sudoers.d/spanel >/dev/null
    sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper wp --info >/dev/null
    # The retune output is a function of the helper's logic and this machine's
    # RAM/CPU; neither moves between two updates of the same release. Skip the
    # pair (and the autotune unit, which just runs the same two) when the
    # helper is unchanged. `spanel-autotune.service` stays enabled for boot.
    if step_inputs_changed autotune "$SOURCE_DIR/installer/files/spanel-helper.sh"; then
      sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper php-fpm-retune >/dev/null || \
        echo "  (warning: could not retune existing PHP-FPM pools; site refresh will retry later)"
      sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper mariadb-retune >/dev/null || \
        echo "  (warning: could not retune MariaDB; update will continue with existing settings)"
      step_mark_done autotune "$SOURCE_DIR/installer/files/spanel-helper.sh"
    else
      echo "  (PHP-FPM and MariaDB tuning unchanged for this release; skipping the retune)"
    fi
    systemctl enable spanel-autotune.service >/dev/null 2>&1 || true
    # clamd is only wanted by scan-on-upload, and that is off unless someone
    # turned it on. maldet - which runs every scheduled, on-demand and
    # real-time scan - calls `clamscan` and never opens clamd's socket, so an
    # idle daemon is a second resident copy of the same ~1 GB of signatures.
    # A live 8 GB server collected 16 OOM kills in 7 days with both loaded,
    # clamd in 9 of them and nginx taken down twice as collateral.
    #
    # The helper refuses to remove it when maldet is absent (scans would fall
    # back to clamdscan and need the daemon) or when apt would take the engine
    # with it, so this is safe to run unconditionally.
    if pkg_installed clamav-daemon && ! grep -q '"malware_scan_on_upload": *true' /var/lib/spanel/panel-settings.json 2>/dev/null; then
      log "Removing clamav-daemon (nothing uses it; the scan engine stays)"
      sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper clamav-daemon-remove >/dev/null ||         echo "  (warning: could not remove clamav-daemon; it is still holding memory)"
    fi
    # clamd ships limits sized for mail attachments: anything over MaxFileSize
    # comes back OK without being read, so a padded upload was waved through.
    # Re-applied every update - the servers still on the stock 25 MB are the
    # ones already deployed. Root cannot call the helper directly (it refuses
    # anything but sudo from 'spanel'), and a bare `|| true` here would hide
    # that refusal exactly the way it hid the memory guard's.
    if [[ -f /etc/clamav/clamd.conf ]]; then
      sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper clamav-tune >/dev/null ||         echo "  (warning: could not raise clamd's scan limits; large uploads stay unscanned)"
    fi
    # The helper is fresh now, so the clock unit can run without flashing
    # up as a failed unit first.
    systemctl reset-failed spanel-timesync.service >/dev/null 2>&1 || true
    systemctl start spanel-timesync.timer >/dev/null 2>&1 || true
    systemctl start spanel-timesync.service >/dev/null 2>&1 || \
      echo "  (warning: could not sync the clock now; the timer will retry)"
  else
    echo "  (spanel user not found; skipping helper refresh - run install.sh first)"
  fi
fi

if [[ -f "$SOURCE_DIR/installer/rescue-firewall.sh" ]]; then
  log "Refreshing firewall rescue command"
  install -m 0755 -o root -g root "$SOURCE_DIR/installer/rescue-firewall.sh" /usr/local/sbin/spanel-rescue-firewall
  ln -sfn /usr/local/sbin/spanel-rescue-firewall /usr/local/sbin/spanel-rescue-ufw-blocklist
fi

if [[ -f "$SOURCE_DIR/change_IP.sh" ]]; then
  log "Refreshing panel IP change command"
  install -m 0755 -o root -g root "$SOURCE_DIR/change_IP.sh" /usr/local/sbin/spanel-change-ip
fi

log "Refreshing panel update command"
install_update_command

if [[ -f "$SOURCE_DIR/installer/files/spanelctl" ]]; then
  log "Refreshing SSH menu command: spanel"
  install -m 0755 -o root -g root "$SOURCE_DIR/installer/files/spanelctl" /usr/local/sbin/spanel
  ln -sfn /usr/local/sbin/spanel /usr/local/sbin/spanelctl
  sed -i "s#APP_DIR=\"\${APP_DIR:-/opt/spanel}\"#APP_DIR=\"\${APP_DIR:-${APP_DIR}}\"#" /usr/local/sbin/spanel /usr/local/sbin/spanelctl 2>/dev/null || true
fi

log "Ensuring Nginx ModSecurity WAF engine is installed"
if id -u spanel >/dev/null 2>&1; then
  sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper waf-install || \
    echo "WARNING: WAF engine installation failed; continuing without ModSecurity."
  sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper certbot-auto-renew-install >/dev/null 2>&1 || true
  sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper firewall-blocklist-timer-install >/dev/null 2>&1 || true
  # Existing servers get the per-hostname certificate copies on this update, so
  # the panel opens on every domain here and not only on PANEL_DOMAIN.
  sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper panel-sni-sync >/dev/null 2>&1 || true
  # Vhosts were rewritten above; put the IPv6 listen directives back if the
  # switch is on. A no-op on the servers that never turned it on.
  sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper ipv6-apply >/dev/null 2>&1 || true
else
  echo "  (spanel user not found; skipping WAF install - run install.sh first)"
fi

# --- Malware scanner: retrofit LMD on servers that already run the scanner --
# The scanner is opt-in; only touch a server whose admin turned it on. LMD adds
# fast site-root scans, a daily incremental scan and named malware families on
# top of ClamAV. A failure here is not fatal - the panel has an Install button.
if id -u spanel >/dev/null 2>&1 \
   && grep -q '"malware_scan_enabled":[[:space:]]*true' /var/lib/spanel/panel-settings.json 2>/dev/null \
   && [[ ! -x /usr/local/sbin/maldet ]]; then
  log "Installing Linux Malware Detect (scanner is enabled on this server)"
  sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper maldet-install || \
    echo "  (LMD install failed - use the Install LMD button on the Malware page)"
fi
if id -u spanel >/dev/null 2>&1 && [[ -x /usr/local/sbin/maldet ]]; then
  sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper maldet-update-sigs >/dev/null 2>&1 || true
  # rfxn's installer enables maldet.service (the Level 2 monitor). The panel
  # owns that switch - keep it off unless the admin turned real-time on.
  if ! grep -q '"malware_realtime_enabled":[[:space:]]*true' /var/lib/spanel/panel-settings.json 2>/dev/null; then
    systemctl disable --now maldet >/dev/null 2>&1 || true
  fi
fi

# --- Firewall: move IP blocking from UFW/Nginx to iptables + ipset ---------
log "Migrating firewall to iptables + ipset"
if ! command -v ipset >/dev/null 2>&1 || ! command -v iptables >/dev/null 2>&1; then
  if ! DEBIAN_FRONTEND=noninteractive apt-get install -y iptables ipset >/dev/null 2>&1; then
    # Seen in the field: a stuck libsystemd-shared pin leaves libipset13 (and
    # so ipset) "not going to be installed" - apt itself suggests the fix.
    # Older installs never got iptables/ipset in this state; every update run
    # is a chance to repair it instead of leaving the firewall unenforced
    # forever behind a WARNING nobody reads.
    echo "  iptables/ipset install failed; repairing broken apt dependencies"
    DEBIAN_FRONTEND=noninteractive apt-get --fix-broken install -y >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y iptables ipset >/dev/null 2>&1 || \
      echo "WARNING: could not install iptables/ipset; firewall migration skipped."
  fi
fi
if id -u spanel >/dev/null 2>&1 && command -v ipset >/dev/null 2>&1; then
  # firewall-migrate imports surviving UFW rules, removes UFW and the Nginx
  # geo-map blocklist, then applies the iptables chain and ipsets.
  sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper firewall-migrate || \
    echo "WARNING: firewall migration failed; run 'spanel-rescue-firewall' if the server is unreachable."
  sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper firewall-blocklist-run >/dev/null 2>&1 || true
else
  echo "  (skipping firewall migration; spanel user or ipset missing)"
fi

# --- Restore ownership so spanel user can read/write the deploy ------------
if id -u spanel >/dev/null 2>&1; then
  ensure_panel_runtime_ownership
fi

# --- Backend ---------------------------------------------------------------
log "Updating backend dependencies"
update_progress 55 "backend" "Updating backend dependencies"
cd "$APP_DIR/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
# pip resolves the whole requirements file on every run even when nothing moved;
# skip it unless requirements.txt changed or the venv was just rebuilt.
if [[ "$venv_needs_recreate" == "true" ]] || step_inputs_changed backend-deps requirements.txt; then
  pip install --upgrade pip
  pip install -r requirements.txt
  step_mark_done backend-deps requirements.txt
else
  log "Backend dependencies unchanged since last update; skipping pip install"
fi

log "Refreshing MariaDB grants"
refresh_spanel_mariadb_grants

log "Running database migrations"
update_progress 65 "backend" "Running database migrations"
if id -u spanel >/dev/null 2>&1; then
  # Run migrations as the spanel user so the SQLite file ownership stays correct.
  sudo -u spanel "$APP_DIR/backend/.venv/bin/python" -c \
    "from app.core.database import run_migrations; run_migrations()"
else
  python -c "from app.core.database import run_migrations; run_migrations()"
fi
systemctl enable --now spanel-backup-scheduler.timer >/dev/null 2>&1 || true
systemctl enable --now spanel-malware-scheduler.timer >/dev/null 2>&1 || true
systemctl enable --now spanel-notify.timer >/dev/null 2>&1 || true

SITE_REFRESH_INPUTS=(
  "$SOURCE_DIR/installer/files/spanel-helper.sh"
  "$SOURCE_DIR/backend/app/services"
  "$SOURCE_DIR/backend/app/templates"
)
if ! step_inputs_changed site-refresh "${SITE_REFRESH_INPUTS[@]}"; then
  log "Managed site config unchanged since last update; skipping the per-site refresh"
elif id -u spanel >/dev/null 2>&1; then
  log "Refreshing managed site configuration"
  sudo -u spanel env HOME="$APP_DIR" SPANEL_USE_HELPER=true "$APP_DIR/backend/.venv/bin/python" - <<'PY'
from app.core.database import SessionLocal
from app.models.entities import Website
from app.services import nginx, site_users, waf

with SessionLocal() as db:
    websites = db.query(Website).all()
    try:
        result = nginx.sync_http_flood_zones(websites)
        if result.returncode != 0:
            print(f"WARNING: could not refresh HTTP flood zones: {result.stderr or result.stdout}")
    except Exception as exc:
        print(f"WARNING: could not refresh HTTP flood zones: {exc}")
    # One nginx test and reload for the whole pass instead of one per site.
    # nginx re-parses the entire configuration on every test, ModSecurity
    # rules included, so on a box with CRS enabled and 22 sites this was 22
    # full parses of 18,503 rules - which is what ran a swapless 8 GB server
    # out of memory partway through an update and took nginx down with it.
    # A failure still rolls every vhost written in the batch back.
    with nginx.deferred_reload():
        for website in websites:
            try:
                if website.linux_user:
                    runtime_php_version = website.php_version if (website.app_type or "wordpress") in {"wordpress", "php"} else None
                    site_users.ensure_site_runtime(website.domain, website.root_path, runtime_php_version, website.linux_user)
                    site_users.ensure_document_root(
                        website.root_path,
                        getattr(website, "document_root", "public_html") or "public_html",
                        website.linux_user,
                    )
                # No fix_site_permissions here: it resets every file of the
                # site to 644, undoing modes the owner chose, on each update
                # that changed a service (operator, 2026-09-27).
                # ensure_site_runtime above already puts the ownership right.
                result = waf.sync_website_rules(website)
                if result.returncode != 0:
                    print(f"WARNING: could not refresh WAF rules for {website.domain}: {result.stderr or result.stdout}")
                if getattr(website, "nginx_config_mode", "managed") != "managed":
                    website.nginx_config_mode = "managed"
                    db.commit()
                app_type = website.app_type or "wordpress"
                runtime_php_version = website.php_version if app_type in {"wordpress", "php"} else None
                nginx.rewrite_vhost(
                    website.domain,
                    website.root_path,
                    app_type=app_type,
                    php_version=website.php_version,
                    custom_directives=website.nginx_custom or "",
                    php_fpm_socket_override=site_users.site_php_fpm_socket(website.linux_user, website.root_path, runtime_php_version),
                    waf_enabled=website.waf_enabled,
                    http_flood_enabled=website.http_flood_enabled,
                    http_flood_config=website.http_flood_config or "",
                    document_root=getattr(website, "document_root", "public_html") or "public_html",
                    rewrite_mode=getattr(website, "nginx_rewrite_mode", "none") or "none",
                )
            except Exception as exc:
                print(f"WARNING: could not refresh permissions for {website.domain}: {exc}")
    try:
        result = nginx.sync_http_flood_zones(websites)
        if result.returncode != 0:
            print(f"WARNING: could not refresh HTTP flood zones: {result.stderr or result.stdout}")
    except Exception as exc:
        print(f"WARNING: could not refresh HTTP flood zones: {exc}")
PY
  step_mark_done site-refresh "${SITE_REFRESH_INPUTS[@]}"
fi

# Clear what deleted websites left on disk. A Let's Encrypt renewal config for a
# site nobody hosts wakes certbot.timer twice a day and starts failing the day
# the domain stops pointing here, which is how a site removed months ago becomes
# a permanently failed unit with no obvious cause.
#
# Everything is copied into /root/spanel-removed before it is deleted: these are
# customer certificates, and "unreferenced" is a strong inference rather than a
# certainty. The helper refuses outright if the panel cannot say which domains
# are live, so a failed query cannot turn into a delete.
if id -u spanel >/dev/null 2>&1; then
  log "Clearing orphaned certificates and configs"
  sudo -u spanel env HOME="$APP_DIR" SPANEL_USE_HELPER=true "$APP_DIR/backend/.venv/bin/python" - <<'PY' || log "WARNING: orphan cleanup did not complete"
from app.core.database import SessionLocal
from app.services import orphans

with SessionLocal() as db:
    try:
        print("  " + orphans.describe(orphans.clean(db)))
    except Exception as exc:
        print(f"  WARNING: orphan cleanup skipped: {exc}")
PY
fi

# journald ships with no size limit and falls back to 10% of the filesystem;
# btmp has no logrotate rule on Ubuntu at all. Measured on a live server: a
# 2.7G journal and 130M of btmp, against 51M for every nginx log combined,
# nearly all of it SSH password-guessing noise. Existing installs need this as
# much as new ones, so update.sh applies it too.
#
# Self-gating: the files are only written when their content differs, and
# journald is only restarted when its drop-in actually changed - an update
# should not bounce the logging daemon for nothing.
JOURNALD_DROPIN=/etc/systemd/journald.conf.d/99-spanel-size.conf
JOURNALD_WANTED=$(cat <<'JOURNALD'
# Managed by SPanel.
[Journal]
SystemMaxUse=500M
SystemKeepFree=1G
MaxRetentionSec=2week
JOURNALD
)
if [[ "$(cat "$JOURNALD_DROPIN" 2>/dev/null)" != "$JOURNALD_WANTED" ]]; then
  log "Capping journald size"
  mkdir -p /etc/systemd/journald.conf.d
  printf '%s\n' "$JOURNALD_WANTED" >"$JOURNALD_DROPIN"
  systemctl restart systemd-journald 2>/dev/null || true
  journalctl --vacuum-size=500M >/dev/null 2>&1 || true
fi

BTMP_RULE=/etc/logrotate.d/btmp
BTMP_WANTED=$(cat <<'BTMP'
# Managed by SPanel.
/var/log/btmp {
    su root root
    missingok
    weekly
    create 0660 root utmp
    rotate 4
    compress
    notifempty
}
BTMP
)
if [[ "$(cat "$BTMP_RULE" 2>/dev/null)" != "$BTMP_WANTED" ]]; then
  log "Adding btmp log rotation"
  printf '%s\n' "$BTMP_WANTED" >"$BTMP_RULE"
  chmod 644 "$BTMP_RULE"
fi

# Repair for servers where PHP's MySQL extension went missing. Seen for real:
# php8.3-mysql in dpkg state "rc" - removed, config files left behind - so
# /etc/php/8.3/mods-available still held mysqli.ini while the matching .so was
# gone. WordPress on that version could not reach its database at all, and
# `wp core update` failed with "missing the MySQL extension".
#
# phpenmod alone does NOT fix this, and is actively harmful here: with the .so
# absent it still writes the conf.d symlink, exits 0, and every later `php`
# invocation prints three "Unable to load dynamic library" warnings. So check
# for the .so first and reinstall the package when it is missing; only then
# enable the modules.
for php_ini_dir in /etc/php/*/; do
  php_ver="$(basename "$php_ini_dir")"
  [[ -d "/etc/php/${php_ver}/cli/conf.d" ]] || continue
  command -v "php${php_ver}" >/dev/null 2>&1 || continue
  if "php${php_ver}" -m 2>/dev/null | grep -qx mysqli; then
    continue
  fi

  php_ext_dir="$("php${php_ver}" -i 2>/dev/null | sed -n 's/^extension_dir => \([^ ]*\).*/\1/p' | head -1)"
  if [[ -n "$php_ext_dir" && ! -f "${php_ext_dir}/mysqli.so" ]]; then
    log "PHP ${php_ver} is missing the MySQL extension; installing php${php_ver}-mysql"
    # Keep the reason. Sending it all to /dev/null is how "apt_get: command
    # not found" stayed invisible for as long as it did; an operator reading
    # "could not install" has no idea whether the package is unavailable, the
    # lock is held, or the script is broken.
    if ! php_mysql_log="$(apt_get install -y "php${php_ver}-mysql" 2>&1)"; then
      log "WARNING: could not install php${php_ver}-mysql; WordPress on PHP ${php_ver} will not reach its database"
      echo "$php_mysql_log" | tail -5 | sed 's/^/    /'
    fi
  fi

  # Only enable what is actually present, so a failed install cannot leave
  # dangling symlinks behind.
  if [[ -n "$php_ext_dir" && -f "${php_ext_dir}/mysqli.so" ]]; then
    for mod in mysqlnd mysqli pdo_mysql; do
      phpenmod -v "$php_ver" "$mod" 2>/dev/null || true
    done
    systemctl reload "php${php_ver}-fpm" 2>/dev/null || true
  fi
done

log "Compiling backend modules"
python -m py_compile \
  app/main.py \
  app/api/auth.py \
  app/api/users.py \
  app/api/websites.py \
  app/api/databases.py \
  app/api/maintenance.py \
  app/api/packages.py \
  app/api/firewall.py \
  app/api/services.py \
  app/api/updates.py \
  app/api/waf.py \
  app/api/panel_settings.py \
  app/api/terminal.py \
  app/services/firewall.py \
  app/services/nginx.py \
  app/services/panel_urls.py \
  app/services/panel_settings.py \
  app/services/updates.py \
  app/services/waf.py \
  app/services/mariadb.py \
  app/services/wordpress.py \
  app/services/file_manager.py \
  app/services/backup.py \
  app/services/backup_scheduler.py \
  app/services/storage_quota.py \
  app/services/site_users.py \
  app/services/cron.py \
  app/services/php.py \
  app/schemas/schemas.py \
  app/seed.py
deactivate

log "Restarting spanel-api"
mkdir -p /etc/systemd/system/spanel-api.service.d
cat >/etc/systemd/system/spanel-api.service.d/10-spanel-helper.conf <<'SERVICE'
[Service]
NoNewPrivileges=false
ProtectSystem=false
RestrictSUIDSGID=false
CapabilityBoundingSet=~
SystemCallFilter=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
# The terminal jail runs `unshare --mount` to give a tenant a /home containing
# only their own directory. A sudo'd child inherits this process's namespace
# restriction, so with the unit's RestrictNamespaces=true the unshare failed
# with EPERM and every helper call that goes through terminal-exec returned
# 500 - the file manager could list a directory but could not read or save a
# single file. `mnt` permits that one namespace and leaves the other seven
# denied.
#
# This file is the list of what the helper needs from the sandbox. The jail was
# added without adding its requirement here, which is how it shipped broken.
RestrictNamespaces=mnt
SERVICE
systemctl daemon-reload
systemctl restart spanel-api

# --- Frontend --------------------------------------------------------------
update_progress 80 "frontend" "Building frontend"
cd "$APP_DIR/frontend"

FRONTEND_INPUTS=(src public index.html package.json package-lock.json)
FRONTEND_FP="$(fingerprint "${FRONTEND_INPUTS[@]}" || true)"
FRONTEND_REBUILT=0
if [[ -f dist/index.html && -f dist/.build-fingerprint && -n "$FRONTEND_FP" && "$(cat dist/.build-fingerprint 2>/dev/null)" == "$FRONTEND_FP" ]] && ! step_inputs_changed frontend "${FRONTEND_INPUTS[@]}"; then
  log "Frontend unchanged since last update; keeping the built bundle"
else
  FRONTEND_REBUILT=1
  log "Building frontend"
  if ! command -v node >/dev/null 2>&1; then
    log "WARNING: Node.js not found, installing..."
    DEBIAN_FRONTEND=noninteractive apt-get update --allow-releaseinfo-change
    DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs npm
  fi
  echo "Node version: $(node --version)"
  echo "npm version: $(npm --version)"

  rm -rf dist .vite node_modules/.vite

  # Reinstall only when the dependency set actually moved, not just its mtime.
  if [[ ! -d node_modules ]] || step_inputs_changed frontend-deps package.json; then
    log "Installing npm dependencies..."
    rm -rf node_modules package-lock.json
    npm install
    step_mark_done frontend-deps package.json
  fi
  log "Building frontend with VITE_API_URL=/api..."
  VITE_API_URL=/api npm run build 2>&1 || { log "BUILD FAILED"; exit 1; }

  [[ -f dist/index.html ]] || fail "Frontend build failed: dist/index.html missing"
  HASHED=$(grep -oE 'index-[a-zA-Z0-9_-]+\.js' dist/index.html | head -n1 || true)
  echo "Built bundle: ${HASHED:-unknown}"
  [[ -n "$FRONTEND_FP" ]] && printf '%s' "$FRONTEND_FP" >dist/.build-fingerprint
  step_mark_done frontend "${FRONTEND_INPUTS[@]}"
fi

# Make sure nginx (www-data) can read the built bundle.
chmod o+rX "$APP_DIR" "$APP_DIR/frontend" 2>/dev/null || true
chmod -R o+rX "$APP_DIR/frontend/dist" 2>/dev/null || true

# The API scans dist/assets at start, so a fresh bundle (new hashed filenames)
# needs one more restart. An unchanged bundle does not.
if [[ "$FRONTEND_REBUILT" == "1" ]]; then
  log "Restarting spanel-api after frontend build"
  systemctl restart spanel-api
fi

# --- Reload Nginx ----------------------------------------------------------
log "Reloading nginx"
update_progress 92 "restarting" "Restarting services and reloading nginx"
migrate_nginx_wordpress_csp_worker_src
migrate_protect_php_from_autoremove
migrate_user_backup_dir_owner
migrate_site_cron_php_binary
nginx -t
systemctl reload nginx

# --- Did turning HTTPS on actually work? -----------------------------------
if [[ -n "${PANEL_SWITCHED_TO_HTTPS:-}" ]]; then
  panel_port_now="$(env_get PANEL_PORT)"; panel_port_now="${panel_port_now:-2222}"
  https_ok="no"
  for _ in {1..20}; do
    if curl -kfsS --connect-timeout 2 --max-time 5 "https://127.0.0.1:${panel_port_now}/api/health" >/dev/null 2>&1; then
      https_ok="yes"; break
    fi
    sleep 1
  done
  if [[ "$https_ok" != "yes" ]]; then
    # An operator locked out of the panel cannot fix the panel. Put it back.
    log "Panel did not come up over HTTPS; reverting to HTTP"
    env_set PANEL_SSL_CERT ""
    env_set PANEL_SSL_KEY ""
    env_set PANEL_SSL_MODE ""
    env_set PANEL_URL "http://$(detect_server_ip):${panel_port_now}"
    env_set ALLOWED_ORIGINS "http://$(detect_server_ip):${panel_port_now}"
    PANEL_SWITCHED_TO_HTTPS=""
    systemctl restart spanel-api
  fi
fi

# --- Health check ----------------------------------------------------------
log "Health check"
update_progress 98 "healthcheck" "Running API health check"
for _ in {1..20}; do
  if panel_healthcheck; then
    echo "API is healthy."
    echo ""
    echo "Update completed."
    echo "If the browser still shows the old UI, hard refresh (Ctrl + Shift + R)."
    if [[ -n "${PANEL_SWITCHED_TO_HTTPS:-}" ]]; then
      panel_https_note
    fi
    write_update_state "completed" "${UPDATE_REF:-}" "Update completed"
    update_progress 100 "completed" "Update completed"
    exit 0
  fi
  sleep 1
done

echo "API did not respond. Check logs:"
echo "  journalctl -u spanel-api -n 100 --no-pager"
write_update_state "failed" "${UPDATE_REF:-}" "API health check failed after update"
update_progress 0 "failed" "API health check failed after update"
exit 1
