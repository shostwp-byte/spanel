#!/usr/bin/env bash
# Emergency recovery for SPanel hosts that became unreachable because of a bad
# firewall state (oversized blocklist, wrong deny rule, leftover UFW rules).
#
# It takes a backup, tears every SPanel/UFW filter rule out of the kernel, then
# rebuilds a minimal iptables + ipset firewall that keeps SSH, the panel port
# and the web/mail ports open.

set -euo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run this script as root." >&2; exit 1; }

ts="$(date +%Y%m%d-%H%M%S)"
backup_dir="/root/spanel-firewall-rescue-${ts}"
env_file="/opt/spanel/backend/.env"
rules_file="/var/lib/spanel/firewall/rules.tsv"
blocklist_file="/var/lib/spanel/firewall-blocklists.current"

mkdir -p "$backup_dir"
iptables-save  >"${backup_dir}/iptables-save.txt"  2>&1 || true
ip6tables-save >"${backup_dir}/ip6tables-save.txt" 2>&1 || true
ipset save     >"${backup_dir}/ipset-save.txt"     2>&1 || true
[[ -f "$rules_file" ]] && cp "$rules_file" "${backup_dir}/rules.tsv" || true
if command -v ufw >/dev/null 2>&1; then
  ufw status numbered >"${backup_dir}/ufw-status-numbered.txt" 2>&1 || true
  tar -C /etc -czf "${backup_dir}/ufw-etc-backup.tar.gz" ufw 2>/dev/null || true
fi

echo "Backup saved in ${backup_dir}"

ssh_port="$(sshd -T 2>/dev/null | awk '$1 == "port" { print $2; exit }' || true)"
ssh_port="${ssh_port:-22}"
panel_port=""
if [[ -f "$env_file" ]]; then
  panel_port="$(awk -F= '$1 == "PANEL_PORT" {print $2; exit}' "$env_file" | tr -d '"' || true)"
fi
panel_port="${panel_port:-2222}"

echo "Removing UFW rules (if any) ..."
if command -v ufw >/dev/null 2>&1; then
  ufw --force disable >/dev/null 2>&1 || true
  ufw --force reset   >/dev/null 2>&1 || true
  systemctl disable --now ufw >/dev/null 2>&1 || true
fi

echo "Removing the SPanel firewall chain and sets ..."
for ipt in iptables ip6tables; do
  command -v "$ipt" >/dev/null 2>&1 || continue
  while "$ipt" -C INPUT -j SPANEL-INPUT 2>/dev/null; do
    "$ipt" -D INPUT -j SPANEL-INPUT || break
  done
  "$ipt" -F SPANEL-INPUT 2>/dev/null || true
  "$ipt" -X SPANEL-INPUT 2>/dev/null || true
  "$ipt" -P INPUT ACCEPT 2>/dev/null || true
done
if command -v ipset >/dev/null 2>&1; then
  for name in spanel-allow4 spanel-allowp4 spanel-deny4 spanel-denyp4 spanel-block4 \
              spanel-allow6 spanel-allowp6 spanel-deny6 spanel-denyp6 spanel-block6; do
    ipset destroy "$name" 2>/dev/null || true
  done
fi

echo "Clearing the URL blocklist cache ..."
: >"$blocklist_file" 2>/dev/null || true

echo "Resetting SPanel rules to protected ports only ..."
mkdir -p "$(dirname "$rules_file")"
: >"$rules_file"
chmod 0640 "$rules_file"
printf 'enabled\n' >"$(dirname "$rules_file")/state"

if command -v ipset >/dev/null 2>&1 && [[ -x /usr/local/sbin/spanel-helper ]]; then
  echo "Reapplying the SPanel firewall ..."
  SUDO_USER=spanel /usr/local/sbin/spanel-helper firewall-apply || \
    echo "WARNING: spanel-helper firewall-apply failed; INPUT is left at ACCEPT."
else
  echo "ipset or spanel-helper missing; leaving INPUT policy at ACCEPT."
fi

echo ""
echo "Protected ports kept open: ${ssh_port}, ${panel_port}, 80, 443, 465, 587"
echo "Firewall rescue complete. Verify with:"
echo "  iptables -L SPANEL-INPUT -n --line-numbers"
echo "  spanel-helper firewall-status   # as: SUDO_USER=spanel spanel-helper firewall-status"
