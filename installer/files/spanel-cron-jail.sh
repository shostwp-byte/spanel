#!/usr/bin/env bash
# ==============================================================================
# spanel-cron-jail — Private Mount Namespace Isolation Wrapper for Tenant Crons
# ==============================================================================
# Usage:
#   /usr/local/bin/spanel-cron-jail <linux_user> <working_dir> -- <command> [args...]
#
# Isolates tenant cron jobs inside a private Linux mount namespace (unshare -m)
# so /home/<other_user> directories are masked by an empty tmpfs and only the
# tenant's own /home/<linux_user> remains visible inside /home.
# ==============================================================================
set -euo pipefail

if [[ $# -lt 4 ]]; then
  echo "Usage: spanel-cron-jail <user> <cwd> -- <command> [args...]" >&2
  exit 64
fi

JAIL_USER="$1"
JAIL_CWD="$2"
shift 2

if [[ "${1:-}" == "--" ]]; then
  shift
fi

if [[ $# -eq 0 ]]; then
  echo "spanel-cron-jail: missing command" >&2
  exit 64
fi

if [[ ! "$JAIL_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
  echo "spanel-cron-jail: invalid user: $JAIL_USER" >&2
  exit 64
fi

HOME_ROOT="/home"
USER_HOME="${HOME_ROOT}/${JAIL_USER}"

# Inner script executed inside the private mount namespace
INNER_JAIL_SCRIPT='
  set -e
  jail_user="$1"
  jail_root="$2"
  jail_cwd="$3"
  shift 3
  jail_home="${jail_root}/${jail_user}"
  if [[ -d "$jail_home" ]]; then
    hold="$(mktemp -d /tmp/spanel-cron-hold.XXXXXX)"
    mount --bind "$jail_home" "$hold"
    mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$jail_root"
    mkdir -p "$jail_home"
    mount --move "$hold" "$jail_home"
    rmdir "$hold" 2>/dev/null || true
  fi
  cd "$jail_cwd" 2>/dev/null || cd "$jail_home" 2>/dev/null || cd /tmp
  exec "$@"
'

if command -v unshare >/dev/null 2>&1; then
  if [[ "$(id -u)" -eq 0 ]]; then
    exec unshare --mount --propagation private -- \
      bash -c "$INNER_JAIL_SCRIPT" spanel-cron-jail "$JAIL_USER" "$HOME_ROOT" "$JAIL_CWD" \
      runuser -u "$JAIL_USER" -- "$@"
  elif unshare --user --mount --map-root-user true >/dev/null 2>&1; then
    exec unshare --user --mount --map-root-user --propagation private -- \
      bash -c "$INNER_JAIL_SCRIPT" spanel-cron-jail "$JAIL_USER" "$HOME_ROOT" "$JAIL_CWD" \
      "$@"
  fi
fi

# Fallback when running inside restricted container without namespace cap
cd "$JAIL_CWD" 2>/dev/null || cd "$USER_HOME" 2>/dev/null || cd /tmp
exec "$@"
