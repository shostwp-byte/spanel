#!/usr/bin/env bash
set -euo pipefail

SPANEL_GITHUB="${SPANEL_GITHUB:-https://github.com/shostwp-byte/spanel}"
SPANEL_REPO_SLUG="${SPANEL_GITHUB#*github.com/}"
SPANEL_REPO_SLUG="${SPANEL_REPO_SLUG%.git}"
INSTALLER_REL_PATH="${INSTALLER_REL_PATH:-installer/install.sh}"

tmp_dir="$(mktemp -d)"
trap 'cd /; rm -rf "$tmp_dir"' EXIT

if [[ -n "${SPANEL_VERSION:-}" ]]; then
  echo "==> Installing SPanel ${SPANEL_VERSION} from ${SPANEL_GITHUB}"
  archive_url="${SPANEL_GITHUB}/archive/refs/tags/${SPANEL_VERSION}.tar.gz"
else
  branch="${SPANEL_BRANCH:-main}"
  echo "==> Installing SPanel (v1.0.8 - branch ${branch}) from ${SPANEL_GITHUB}"
  archive_url="${SPANEL_GITHUB}/archive/refs/heads/${branch}.tar.gz"
fi

if ! curl -fsSL "$archive_url" -o "$tmp_dir/source.tar.gz" 2>/dev/null; then
  if ! curl -fsSL "${SPANEL_GITHUB}/archive/refs/heads/1.0.8.tar.gz" -o "$tmp_dir/source.tar.gz" 2>/dev/null; then
    echo "==> Downloading latest HEAD archive from ${SPANEL_GITHUB}"
    curl -fsSL "${SPANEL_GITHUB}/archive/HEAD.tar.gz" -o "$tmp_dir/source.tar.gz"
  fi
fi

tar -xzf "$tmp_dir/source.tar.gz" -C "$tmp_dir"
source_dir="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[[ -n "$source_dir" && -d "$source_dir" ]] || {
  echo "ERROR: Could not extract SPanel source archive." >&2
  exit 1
}
cd "$source_dir"
export npm_config_legacy_peer_deps=true
export NPM_CONFIG_LEGACY_PEER_DEPS=true
export npm_config_audit=false
export npm_config_fund=false
chmod +x "$INSTALLER_REL_PATH" installer/update.sh installer/rescue-firewall.sh
bash "$INSTALLER_REL_PATH" "$@"
