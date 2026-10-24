#!/usr/bin/env bash
# ==============================================================================
# spanel-cron-jail — Root-Enforced Private Mount Namespace Wrapper for Crons
# ==============================================================================
# Usage (invoked as root from /etc/cron.d/spanel-user-<user>):
#   /usr/local/bin/spanel-cron-jail <linux_user> <working_dir> -- <command> [args...]
#
# Security Model:
#   1. Must be invoked by root from /etc/cron.d/spanel-user-<user> (owned by
#      root:root 0644, immutable by the tenant).
#   2. Creates a private mount namespace (unshare --mount --propagation private)
#      as root — does NOT rely on unprivileged user namespaces (which Ubuntu
#      24.04 AppArmor blocks by default and which would grant CAP_SYS_ADMIN
#      inside the namespace).
#   3. Replaces /home with a tmpfs containing only /home/<linux_user>, hiding
#      all other tenants' home directories.
#   4. Drops privileges via `runuser -u <linux_user>` BEFORE changing into the
#      tenant's working directory or executing the command, so the tenant
#      process runs with non-root UID and cannot unmount the tmpfs.
# ==============================================================================
set -euo pipefail

if [[ $# -lt 4 ]]; then
  echo "Usage: spanel-cron-jail <user> <cwd> -- <command> [args...]" >&2
  exit 64
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "spanel-cron-jail: must be executed as root from /etc/cron.d" >&2
  exit 77
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

case "$JAIL_USER" in
  root|daemon|bin|sys|sync|games|man|lp|mail|news|uucp|proxy|www-data|backup|list|irc|_apt|nobody|spanel|spanel-sites|spanel-sftp|spanel-sftp-site|mysql|redis|nginx)
    echo "spanel-cron-jail: reserved system user refused: $JAIL_USER" >&2
    exit 64
    ;;
esac

HOME_ROOT="/home"
USER_HOME="${HOME_ROOT}/${JAIL_USER}"

if [[ ! -d "$USER_HOME" || -L "$USER_HOME" ]]; then
  echo "spanel-cron-jail: user home directory does not exist or is a symlink: $USER_HOME" >&2
  exit 64
fi

RESOLVED_USER_HOME="$(realpath -e -- "$USER_HOME" 2>/dev/null || true)"
RESOLVED_CWD="$(realpath -m -- "$JAIL_CWD" 2>/dev/null || true)"
if [[ -z "$RESOLVED_USER_HOME" || -z "$RESOLVED_CWD" ]]; then
  echo "spanel-cron-jail: could not resolve working directory: $JAIL_CWD" >&2
  exit 64
fi
if [[ "$RESOLVED_CWD" != "$RESOLVED_USER_HOME" && "$RESOLVED_CWD" != "${RESOLVED_USER_HOME}/"* ]]; then
  echo "spanel-cron-jail: working directory must be inside ${RESOLVED_USER_HOME}: $JAIL_CWD" >&2
  exit 64
fi
JAIL_CWD="$RESOLVED_CWD"

# Inner script executed as root inside the private mount namespace, then
# immediately drops privileges to $jail_user before cd and exec.
INNER_JAIL_SCRIPT='
  set -e
  jail_user="$1"
  jail_root="$2"
  jail_cwd="$3"
  shift 3
  jail_home="${jail_root}/${jail_user}"
  if [[ -d "$jail_home" ]]; then
    hold="$(mktemp -d /run/spanel-cron-hold.XXXXXX 2>/dev/null || mktemp -d /tmp/spanel-cron-hold.XXXXXX)"
    chmod 0700 "$hold"
    mount --bind "$jail_home" "$hold"
    mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$jail_root"
    mkdir -p "$jail_home"
    mount --move "$hold" "$jail_home"
    rmdir "$hold" 2>/dev/null || true
  fi
  exec runuser -u "$jail_user" -- env -i \
    HOME="$jail_home" \
    USER="$jail_user" \
    LOGNAME="$jail_user" \
    PATH="/usr/local/bin:/usr/bin:/bin" \
    bash -c '\''cd "$1" 2>/dev/null || cd "$HOME" 2>/dev/null || cd /tmp; shift; exec "$@"'\'' \
    spanel-cron-exec "$jail_cwd" "$@"
'

exec unshare --mount --propagation private -- \
  bash -c "$INNER_JAIL_SCRIPT" spanel-cron-jail "$JAIL_USER" "$HOME_ROOT" "$JAIL_CWD" "$@"
