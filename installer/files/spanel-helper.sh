#!/usr/bin/env bash
# /usr/local/sbin/spanel-helper
#
# Root-privileged trampoline for the SPanel API daemon.
# This is the ONLY code that runs as root for the daemon.
# Installed by install.sh as root:root mode 0750, callable only by user
# 'spanel' through sudo (see /etc/sudoers.d/spanel).
#
# Every operation here is the trust boundary. Validate aggressively.

set -euo pipefail

if [[ "${SUDO_USER:-}" != "spanel" && "${SUDO_USER:-}" != "root" ]]; then
  echo "spanel-helper must be invoked by user 'spanel' via sudo" >&2
  exit 2
fi

# Reset PATH so an attacker cannot ship a shadow binary in spanel's PATH.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

# sudo keeps the caller's working directory, which for the API is the
# spanel-owned backend/. `python3 - <<PY` puts the working directory first
# on sys.path, so a planted json.py there would run as root. Start every
# command from / and tell Python 3.11+ not to add the directory at all.
cd /
PYTHONSAFEPATH=1
export PYTHONSAFEPATH

ALLOWED_SERVICES=(nginx mariadb redis-server fail2ban clamav-daemon exim4 dovecot rspamd pdns maldet docker php5.6-fpm php7.4-fpm php8.0-fpm php8.1-fpm php8.2-fpm php8.3-fpm php8.4-fpm php8.5-fpm spanel-api)
ALLOWED_ACTIONS=(start stop restart reload status is-active is-enabled)
HOME_ROOT="/home"
NGINX_CONF_DIR="/etc/nginx/conf.d"
PHP_CONF_DIRS=(/etc/php/{5.6,7.4,8.0,8.1,8.2,8.3,8.4,8.5}/fpm/conf.d)
SPANEL_SITES_GROUP="spanel-sites"
# Default permissions for everything inside a site tree. 0644/0755 is what every
# hosting panel gives a customer, and what PHP applications, SFTP clients and
# their documentation assume. Sites stay separated by the PHP-FPM open_basedir
# of each pool, by the SFTP chroot and by the panel terminal, not by these bits.
SITE_FILE_MODE="0644"
SITE_DIR_MODE="0755"
# Files that carry database credentials are kept off the default mode.
SITE_SECRET_FILES=(wp-config.php .env .my.cnf)
SPANEL_SFTP_GROUP="spanel-sftp"
# Per-website SFTP sub-accounts. A separate group because they get a different
# sshd Match block: the panel user's own account chroots to its whole home,
# these chroot to one root-owned directory holding a bind mount of one site.
#
# The chroot roots deliberately do NOT live under /var/lib/spanel, which is
# owned by the panel account. OpenSSH refuses a chroot whose parents are
# writable by anyone but root, and it is right to: a chroot root the confined
# account can write is not a boundary at all.
SPANEL_SFTP_SITE_GROUP="spanel-sftp-site"
SPANEL_SFTP_CHROOT_ROOT="/var/lib/spanel-sftp"
# One directory per certificate, so the panel can answer a TLS handshake with
# the certificate for whichever hostname the browser asked for. The panel runs
# as 'spanel' and cannot read /etc/letsencrypt, hence the copies.
PANEL_SNI_DIR="/etc/spanel/sni"
# IPv6 is off until somebody turns it on, and this file is what says so. It is
# the only truth: the panel reads it, the tools vhost is rendered from it, and
# an update re-applies it. World readable on purpose - it holds no secret and
# the panel runs as 'spanel'.
PANEL_IPV6_MARKER="/etc/spanel/ipv6-enabled"
MALWARE_JOBS_DIR="/var/lib/spanel/malware-scan-jobs"
# Left out of a whole-server scan: kernel filesystems that are not files at
# all, read-only squashfs images, package caches that are re-downloadable, and
# the signature database itself. Scanning them costs hours and finds nothing.
MALWARE_SCAN_PRUNE=(/proc /sys /dev /run /snap /var/lib/docker /var/lib/lxcfs /var/lib/clamav /var/cache/apt/archives)
APP_DIR="/opt/spanel"
ENV_FILE="${APP_DIR}/backend/.env"
DEFAULT_PANEL_PORT="2222"
SOURCE_DIR="/opt/spanel-source"
UPDATE_SCRIPT="/usr/local/sbin/spanel-update"
SPANEL_DATA_DIR="/var/lib/spanel"
BACKUP_ROOT="/var/backups/spanel"
FIREWALL_BLOCKLIST_URLS="${SPANEL_DATA_DIR}/firewall-blocklists.urls"
FIREWALL_BLOCKLIST_WORK="${SPANEL_DATA_DIR}/firewall-blocklists.current"
FIREWALL_DIR="${SPANEL_DATA_DIR}/firewall"
FIREWALL_RULES_FILE="${FIREWALL_DIR}/rules.tsv"
FIREWALL_STATE_FILE="${FIREWALL_DIR}/state"
FIREWALL_CHAIN="SPANEL-INPUT"
FIREWALL_PROTECTED_PORTS=(22 80 443 465 587)
# Ports an addon opens while it is on: one file per addon, "<port> <tcp|udp>" a line.
FIREWALL_ADDON_PORTS_DIR="${FIREWALL_DIR}/addon-ports"
# DNS Manager (PowerDNS). The zones live in PDNS_DB, which nothing here removes.
PDNS_CONF="/etc/powerdns/pdns.d/zz-spanel.conf"
PDNS_DB="/var/lib/powerdns/pdns.sqlite3"
PDNS_SCHEMA="/usr/share/pdns-backend-sqlite3/schema/schema.sqlite3.sql"
PDNS_API_KEY_FILE="/etc/spanel/pdns-api.key"
PDNS_API_PORT="8053"
PDNS_INSTALL_LOG="/var/log/spanel-dns-install.log"
NGINX_SPANEL_DIR="/etc/nginx/spanel"
NGINX_BLOCKLIST_DIR="$NGINX_SPANEL_DIR"
NGINX_BLOCKLIST_CONF="/etc/nginx/conf.d/spanel-ip-blocklist.conf"
NGINX_BLOCKLIST_RULES="${NGINX_SPANEL_DIR}/ip-blocklist-geo.conf"
NGINX_BLOCKLIST_SERVER_CONF="${NGINX_SPANEL_DIR}/ip-blocklist-server.conf"
NGINX_CUSTOM_DIR="${NGINX_SPANEL_DIR}/custom"
NGINX_HTTP_FLOOD_CONF="/etc/nginx/conf.d/00-spanel-http-flood.conf"
NGINX_HTTP_FLOOD_LEGACY_CONF="/etc/nginx/conf.d/spanel-http-flood.conf"
NGINX_HTTP_FLOOD_ZONES="${NGINX_BLOCKLIST_DIR}/http-flood-zones.conf"
NGINX_HTTP_FLOOD_SERVER_CONF="${NGINX_BLOCKLIST_DIR}/http-flood-server.conf"
PHP_FPM_DEFAULT_WORKER_MB=128
PHP_FPM_DEFAULT_REQUEST_TERMINATE_TIMEOUT=300
# Per-account PHP-FPM masters. Every hosting account gets one php-fpm master
# per PHP version, run as spanel-php<ver>-fpm@<user>.service with
# Slice=user-<uid>.slice, so the master and every worker it forks are born
# under the account's CPU/memory/IO/tasks limits instead of being moved there
# by a later scan. Pools live in ${SPANEL_PHP_FPM_ROOT}/<ver>/<user>/pool.d and
# listen on ${SPANEL_PHP_RUN_ROOT}/<user>/<pool>.sock. The distro php<ver>-fpm
# master stays for phpMyAdmin and the panel tools, and still serves any pool in
# /etc/php/<ver>/fpm/pool.d that has not moved (or could not move) yet.
# backend/app/services/site_users.py names the same paths; a test holds the two
# together.
SPANEL_PHP_FPM_ROOT="/etc/spanel/php-fpm"
SPANEL_PHP_RUN_ROOT="/run/spanel-php"
SYSTEMD_UNIT_DIR="/etc/systemd/system"
PHP_ETC_ROOT="/etc/php"
PHP_LOG_DIR="/var/log/php"
PHP_FPM_SBIN_DIR="/usr/sbin"
# Both socket layouts, as they appear in a vhost's fastcgi_pass.
PHP_FPM_SOCKET_GREP='(/run/php/[A-Za-z0-9._-]+|/run/spanel-php/[a-z0-9_-]+/[A-Za-z0-9._-]+)\.sock'
MARIADB_TUNING_CONF="/etc/mysql/mariadb.conf.d/90-spanel-tuning.cnf"

deny() { echo "spanel-helper: $*" >&2; exit 1; }

# Every apt call in this file goes through here, by shadowing the command
# rather than by editing thirty-eight call sites and hoping the next one
# remembers. `command apt-get` is the real binary; without it this recurses.
#
# A freshly-booted Ubuntu runs unattended-upgrades and apt-daily in its first
# minutes and holds the dpkg locks while it does. apt does not wait - it exits
# immediately with "Could not get lock /var/lib/dpkg/lock-frontend". install.sh
# learned this long ago and has had a wrapper ever since; the helper never got
# one, so every addon the panel installs could fail for six seconds' bad luck.
# The operator saw "500 internal server error" and nothing else.
#
# Not reachable from the two OS-upgrade paths that run `bash -lc '...apt-get
# ...'` detached: a new shell does not inherit this. Those are deliberately
# fire-and-forget and already tolerate a busy lock by running again tomorrow.
release_stuck_apt_locks() {
  local min_age="${1:-120}" pid
  local stale_pids=()
  while IFS= read -r pid; do
    [[ -n "$pid" ]] && stale_pids+=("$pid")
  done < <(ps -eo pid=,etimes=,comm= 2>/dev/null | awk -v age="$min_age" '$3 ~ /^(apt-get|apt|dpkg|unattended-upgr)$/ && $2 >= age {print $1}')
  if [[ ${#stale_pids[@]} -gt 0 ]]; then
    echo "Cleaning up stuck package manager process(es): ${stale_pids[*]}..." >&2
    kill -TERM "${stale_pids[@]}" 2>/dev/null || true
    sleep 2
    kill -KILL "${stale_pids[@]}" 2>/dev/null || true
    sleep 1
    DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none dpkg --configure -a --force-confdef --force-confold </dev/null >/dev/null 2>&1 || true
  fi
}

apt_locks_busy() {
  if command -v fuser >/dev/null 2>&1; then
    fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock >/dev/null 2>&1 && return 0
  fi
  pgrep -x 'apt-get|apt|dpkg' >/dev/null 2>&1 && return 0
  return 1
}

apt-get() {
  export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none
  release_stuck_apt_locks 120
  local waited=0
  while apt_locks_busy; do
    if (( waited == 0 )); then
      echo "Waiting for another package manager to finish..." >&2
    fi
    if (( waited >= 90 )); then
      release_stuck_apt_locks 0
      break
    fi
    sleep 3
    waited=$(( waited + 3 ))
  done
  local apt_opts=(
    -o DPkg::Lock::Timeout=120
    -o Dpkg::Options::=--force-confdef
    -o Dpkg::Options::=--force-confold
  )
  if ! command apt-get "${apt_opts[@]}" "$@" </dev/null; then
    DEBIAN_FRONTEND=noninteractive dpkg --configure -a --force-confdef --force-confold </dev/null >/dev/null 2>&1 || true
    command apt-get "${apt_opts[@]}" "$@" </dev/null
  fi
}


# `dpkg -s <pkg>` exits 0 for a package that has been REMOVED but still has
# its config files on disk ("deinstall ok config-files"). Every caller here
# means "is this usable", and for a removed package the answer is no - the
# binaries are gone.
#
# This bit on a live server: after clamav-daemon was removed, turning
# scan-on-upload back on would have skipped `apt-get install` (dpkg -s said
# yes) and then failed on `systemctl enable --now clamav-daemon`, because
# /usr/sbin/clamd no longer existed. The feature would have been broken on
# exactly the machines that had removed the daemon.
pkg_installed() {
  [[ "$(dpkg-query -W -f='${Status}' "$1" 2>/dev/null)" == "install ok installed" ]]
}


ensure_spanel_data_dir() {
  install -d -o spanel -g spanel -m 0750 "$SPANEL_DATA_DIR"
}

ensure_nginx_conf_dir_writable() {
  install -d -o root -g root -m 0755 "$NGINX_BLOCKLIST_DIR"
  if getent group spanel >/dev/null 2>&1; then
    install -d -o root -g spanel -m 2775 "$NGINX_CONF_DIR"
    install -d -o root -g spanel -m 2775 "$NGINX_CUSTOM_DIR"
    chmod g+s "$NGINX_CONF_DIR" 2>/dev/null || true
    chmod g+s "$NGINX_CUSTOM_DIR" 2>/dev/null || true
  else
    install -d -o root -g root -m 0755 "$NGINX_CONF_DIR"
    install -d -o root -g root -m 0755 "$NGINX_CUSTOM_DIR"
  fi
}

file_has_nul() {
  local path="$1"
  python3 - "$path" <<'PY'
import sys

with open(sys.argv[1], "rb") as handle:
    data = handle.read()
sys.exit(0 if b"\0" in data else 1)
PY
}

env_get() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE"
}

env_set() {
  local key="$1" value="$2" escaped
  [[ -f "$ENV_FILE" ]] || deny "$ENV_FILE not found"
  escaped="$(printf '%s' "$value" | sed -e 's/[&|]/\\&/g')"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

detect_ip() {
  hostname -I 2>/dev/null | awk '{print $1}' || true
}

is_ipv4() {
  local value="$1" part
  local -a parts
  [[ "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  IFS=. read -r -a parts <<<"$value"
  for part in "${parts[@]}"; do
    (( 10#$part >= 0 && 10#$part <= 255 )) || return 1
  done
}

is_domain() {
  [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]]
}

require_panel_scheme() {
  [[ "$1" == "http" || "$1" == "https" ]] || deny "invalid panel scheme: $1"
}

require_panel_host() {
  local host="$1"
  if is_domain "$host" || is_ipv4 "$host" || [[ "$host" == "localhost" ]]; then
    return 0
  fi
  deny "invalid panel host: $host"
}

allow_panel_port() {
  # Panel/SSH/web ports are always derived from the environment, so re-applying
  # the iptables chain is enough to open a newly selected panel port.
  #
  # This must never fail the caller (panel-ssl-install, panel-url-set, ...):
  # a firewall problem here is not the SSL/URL change failing. `|| true`
  # alone does not guarantee that - firewall_apply (via firewall_require_tools)
  # calls deny(), which does `exit`, and exit in a plain command tears down
  # this whole process before `|| true` ever gets a status to swallow. Run it
  # in a subshell so that exit only ends the subshell.
  local port="$1"
  require_port "$port"
  ( firewall_apply ) >/dev/null 2>&1 || true
}

schedule_panel_restart() {
  local unit
  systemctl daemon-reload || true
  if command -v systemd-run >/dev/null 2>&1; then
    unit="spanel-api-delayed-restart-$(date +%s)"
    systemd-run --unit="$unit" --on-active=2s /bin/systemctl restart spanel-api >/dev/null 2>&1 || true
  else
    (sleep 2; systemctl restart spanel-api >/dev/null 2>&1 || true) >/dev/null 2>&1 &
  fi
}

refresh_tools_nginx() {
  local port cert key domain host api_scheme tools_scheme pma_secure ssl_block php_version
  port="$(env_get PANEL_PORT)"; port="${port:-$DEFAULT_PANEL_PORT}"
  cert="$(env_get PANEL_SSL_CERT)"; key="$(env_get PANEL_SSL_KEY)"
  domain="$(env_get PANEL_DOMAIN)"; host="${domain:-$(detect_ip)}"
  php_version="${PHP_DEFAULT:-8.4}"
  api_scheme="http"; tools_scheme="http"; pma_secure="false"; ssl_block=""
  local v6_http="" v6_https=""
  if ipv6_is_enabled; then
    # default_server is per address:port, so [::]:80 may carry it as well.
    v6_http=$'\n    listen [::]:80 default_server;'
    v6_https=$'\n    listen [::]:443 ssl http2 default_server;'
  fi
  if [[ -n "$cert" && -n "$key" && -f "$cert" && -f "$key" ]]; then
    api_scheme="https"; tools_scheme="https"; pma_secure="true"
    printf -v ssl_block '\n    listen 443 ssl http2 default_server;%s\n    ssl_certificate %s;\n    ssl_certificate_key %s;' "$v6_https" "$cert" "$key"
  fi
  rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf 2>/dev/null || true
  ensure_nginx_conf_dir_writable
  firewall_purge_nginx_blocklist 2>/dev/null || true
  write_http_flood_nginx_conf 2>/dev/null || true
  cat >/etc/nginx/conf.d/00-spanel-tools.conf <<NGINX
server {
    listen 80 default_server;${v6_http}${ssl_block}
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
    location ~ ^/phpmyadmin/(.+\.php)$ { alias /usr/share/phpmyadmin/\$1; include fastcgi_params; fastcgi_param SCRIPT_FILENAME /usr/share/phpmyadmin/\$1; fastcgi_param SCRIPT_NAME /phpmyadmin/\$1; fastcgi_pass unix:/run/php/php${php_version}-fpm.sock; fastcgi_read_timeout 300; }
}
NGINX
  sed -i -E "/api\/databases\/phpmyadmin-sso/s#'[^']+/api/databases/phpmyadmin-sso/'#'${api_scheme}://127.0.0.1:${port}/api/databases/phpmyadmin-sso/'#" /usr/share/phpmyadmin/spanel-signon.php 2>/dev/null || true
  sed -i -E "s#('secure' => )(true|false)#\1${pma_secure}#" /etc/phpmyadmin/conf.d/spanel-signon.php /usr/share/phpmyadmin/spanel-signon.php /etc/phpmyadmin/conf.d/spanel-remote-servers.php 2>/dev/null || true
  [[ -n "$host" ]] && sed -i -E "/PmaAbsoluteUri/s#'https?://[^']+/phpmyadmin/'#'${tools_scheme}://${host}/phpmyadmin/'#" /etc/phpmyadmin/conf.d/spanel-signon.php 2>/dev/null || true
  nginx -t
  systemctl reload nginx || true
}

# phpMyAdmin servers 2..99 are the panel's remote MySQL hosts (server 1 is the
# local MariaDB, configured by spanel-signon.php). The panel sends the list as
# INDEX,HOST,PORT,TLS,CA tuples; nothing free-form reaches the PHP file.
PMA_CONF_DIR="/etc/phpmyadmin/conf.d"
PMA_REMOTE_SERVERS_CONF="${PMA_CONF_DIR}/spanel-remote-servers.php"
PMA_REMOTE_CA_DIR="/etc/phpmyadmin/spanel-remote-ca"
PMA_REMOTE_SPEC_RE='^([0-9]{1,2}),([A-Za-z0-9][A-Za-z0-9.:-]{0,252}),([0-9]{1,5}),([01]),([01])$'

write_phpmyadmin_remote_ca() {
  # CA bundle for one remote host, PEM on stdin. Only what openssl parses as
  # certificates is written back out, so nothing else sent here reaches disk.
  local index="$1" payload out
  [[ -d "$PMA_CONF_DIR" ]] || deny "phpMyAdmin is not installed"
  payload="$(mktemp)"
  head -c 65536 >"$payload"
  install -d -o root -g www-data -m 0750 "$PMA_REMOTE_CA_DIR"
  out="$(mktemp "${PMA_REMOTE_CA_DIR}/.ca-${index}.XXXXXX")"
  if ! openssl crl2pkcs7 -nocrl -certfile "$payload" 2>/dev/null | openssl pkcs7 -print_certs -out "$out" 2>/dev/null \
      || ! grep -q -- "-----BEGIN CERTIFICATE-----" "$out"; then
    rm -f "$payload" "$out"
    deny "phpmyadmin-remote-ca: stdin is not a PEM certificate"
  fi
  rm -f "$payload"
  chown root:www-data "$out"
  chmod 0640 "$out"
  mv -f "$out" "${PMA_REMOTE_CA_DIR}/${index}.pem"
}

write_phpmyadmin_remote_servers() {
  local spec index host port tls ca secure tmp name f
  local -a keep=()
  if [[ ! -d "$PMA_CONF_DIR" ]]; then
    echo "phpMyAdmin is not installed; nothing to configure"
    return 0
  fi
  secure="false"
  grep -Eq "'secure' => true" "${PMA_CONF_DIR}/spanel-signon.php" 2>/dev/null && secure="true"
  tmp="$(mktemp "${PMA_CONF_DIR}/.spanel-remote-servers.XXXXXX")"
  printf '%s\n' "<?php" "// Managed by SPanel (spanel-helper phpmyadmin-remote-servers): the panel's" \
    "// remote MySQL hosts. Server 1 is the local MariaDB. Do not edit." >"$tmp"
  for spec in "$@"; do
    if [[ ! "$spec" =~ $PMA_REMOTE_SPEC_RE ]]; then
      rm -f "$tmp"; deny "phpmyadmin-remote-servers: invalid server spec"
    fi
    # Decimal, so "08" can neither become a bad PHP octal literal nor miss 8.pem.
    index="$((10#${BASH_REMATCH[1]}))"; host="${BASH_REMATCH[2]}"; port="${BASH_REMATCH[3]}"
    tls="${BASH_REMATCH[4]}"; ca="${BASH_REMATCH[5]}"
    if (( 10#$index < 2 || 10#$index > 99 || 10#$port < 1 || 10#$port > 65535 )); then
      rm -f "$tmp"; deny "phpmyadmin-remote-servers: index or port out of range"
    fi
    cat >>"$tmp" <<PHP
\$cfg['Servers'][${index}] = [
    'verbose' => 'Remote MySQL ${host}:${port}',
    'host' => '${host}',
    'port' => '${port}',
    'auth_type' => 'signon',
    'SignonSession' => 'SPanelPmaSignon',
    'SignonURL' => '/phpmyadmin/spanel-signon.php',
    'SignonCookieParams' => [
        'lifetime' => 0,
        'path' => '/',
        'domain' => '',
        'secure' => ${secure},
        'httponly' => true,
        'samesite' => 'Lax',
    ],
    'AllowNoPassword' => false,
PHP
    if [[ "$tls" == "1" ]]; then
      printf '%s\n' "    'ssl' => true," "    'ssl_verify' => true," >>"$tmp"
      if [[ "$ca" == "1" ]]; then
        if [[ ! -f "${PMA_REMOTE_CA_DIR}/${index}.pem" ]]; then
          rm -f "$tmp"; deny "phpmyadmin-remote-servers: no CA for server ${index}; send it with phpmyadmin-remote-ca first"
        fi
        printf '%s\n' "    'ssl_ca' => '${PMA_REMOTE_CA_DIR}/${index}.pem'," >>"$tmp"
        keep+=("${index}.pem")
      else
        printf '%s\n' "    'ssl_ca_path' => '/etc/ssl/certs'," >>"$tmp"
      fi
    fi
    printf '%s\n' "];" >>"$tmp"
  done
  if command -v php >/dev/null 2>&1 && ! php -l "$tmp" >/dev/null 2>&1; then
    rm -f "$tmp"; deny "phpmyadmin-remote-servers: generated config does not parse"
  fi
  chown root:www-data "$tmp"
  chmod 0640 "$tmp"
  mv -f "$tmp" "$PMA_REMOTE_SERVERS_CONF"
  if [[ -d "$PMA_REMOTE_CA_DIR" ]]; then
    for f in "$PMA_REMOTE_CA_DIR"/*.pem; do
      [[ -e "$f" ]] || continue
      name="$(basename -- "$f")"
      [[ " ${keep[*]:-} " == *" ${name} "* ]] || rm -f -- "$f"
    done
  fi
  echo "phpMyAdmin remote servers: $#"
}

configure_unattended_upgrades() {
  local enabled="$1" mode="$2" reboot="$3" origins
  [[ "$enabled" == "on" || "$enabled" == "off" ]] || deny "enabled must be on/off"
  [[ "$mode" == "security" || "$mode" == "all" ]] || deny "mode must be security/all"
  [[ "$reboot" == "on" || "$reboot" == "off" ]] || deny "auto reboot must be on/off"

  DEBIAN_FRONTEND=noninteractive apt-get update --allow-releaseinfo-change
  DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades apt-listchanges

  if [[ "$enabled" == "off" ]]; then
    cat >/etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Unattended-Upgrade "0";
APT
    systemctl disable --now unattended-upgrades.service 2>/dev/null || true
    echo "OS auto updates disabled"
    return 0
  fi

  origins='        "${distro_id}:${distro_codename}-security";'
  if [[ "$mode" == "all" ]]; then
    origins='        "${distro_id}:${distro_codename}";
        "${distro_id}:${distro_codename}-updates";
        "${distro_id}:${distro_codename}-security";'
  fi

  cat >/etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
APT
  # Remove-Unused-Dependencies stays false. It is not a tidiness setting on
  # a web server: PHP extensions, and anything else nothing depends on, are
  # "unused" by apt's definition. On 2026-09-23 it removed php8.3-mysql
  # overnight and every WordPress site on that interpreter answered
  # "missing the MySQL extension" until someone noticed. Ubuntu's own default
  # is false; we had it true.
  cat >/etc/apt/apt.conf.d/51spanel-unattended-upgrades <<APT
Unattended-Upgrade::Allowed-Origins {
${origins}
};
Unattended-Upgrade::Remove-Unused-Dependencies "false";
Unattended-Upgrade::Automatic-Reboot "$([[ "$reboot" == "on" ]] && echo true || echo false)";
Unattended-Upgrade::Automatic-Reboot-Time "03:00";
APT
  systemctl enable --now unattended-upgrades.service 2>/dev/null || true
  echo "OS auto updates enabled (${mode}, reboot=${reboot})"
}

run_os_update_now() {
  export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none
  apt-get update --allow-releaseinfo-change
  apt-get \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold \
    upgrade -y
}

run_os_update() {
  local unit="spanel-os-update"
  if systemctl is-active --quiet "${unit}.service"; then
    echo "OS update is already running: ${unit}.service"
    return 0
  fi
  if command -v systemd-run >/dev/null 2>&1; then
    systemd-run \
      --unit="$unit" \
      --collect \
      --description="Update OS packages for SPanel" \
      /bin/bash -lc 'export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none; apt-get update --allow-releaseinfo-change; apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade -y'
    echo "OS update started: ${unit}.service"
    echo "Check progress: journalctl -u ${unit}.service -f"
    return 0
  fi
  nohup /bin/bash -lc 'export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none; apt-get update --allow-releaseinfo-change; apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade -y' \
    >/var/log/spanel-os-update.log 2>&1 &
  echo "OS update started in background. Log: /var/log/spanel-os-update.log"
}

CORE_STACK_BLACKLIST_FILE="/etc/apt/apt.conf.d/52spanel-core-stack-blacklist"

# The unit to reload after a core stack package changed version. Empty where
# the package's own postinst already restarts the daemon (MariaDB, Redis,
# PowerDNS) or there is no daemon (openssl).
core_stack_reload_unit() {
  case "$1" in
    nginx) echo nginx ;;
    exim4-daemon-heavy) echo exim4 ;;
    dovecot-core) echo dovecot ;;
    rspamd) echo rspamd ;;
    fail2ban) echo fail2ban ;;
    *) echo "" ;;
  esac
}

# Package-Blacklist entries for one core stack package: the binary packages
# that come from the same source and move together. unattended-upgrades
# matches each entry as a Python regex anchored at the start of the name.
core_stack_blacklist_patterns() {
  case "$1" in
    nginx) printf '%s\n' '^nginx$' '^nginx-(common|core|full|light|extras)$' '^libnginx-mod-' ;;
    mariadb-server) printf '%s\n' '^mariadb-' '^libmariadb' '^mysql-common$' ;;
    redis-server) printf '%s\n' '^redis-(server|tools)$' ;;
    pdns-server) printf '%s\n' '^pdns-' ;;
    exim4-daemon-heavy) printf '%s\n' '^exim4' ;;
    dovecot-core) printf '%s\n' '^dovecot-' ;;
    rspamd) printf '%s\n' '^rspamd$' ;;
    openssl) printf '%s\n' '^openssl$' '^libssl3' ;;
    fail2ban) printf '%s\n' '^fail2ban$' ;;
    *) return 1 ;;
  esac
}

# The apt.conf.d snippet that keeps unattended-upgrades off the packages the
# panel patches itself, so exactly one mechanism upgrades them: the panel's,
# inside its maintenance window and with nginx -t before and after.
render_core_blacklist() {
  local pkg pattern
  echo "// Managed by SPanel. These packages are patched by the panel's scheduled"
  echo "// core stack patching (maintenance window, nginx -t before and after);"
  echo "// turning that off in Updates removes this file."
  echo "Unattended-Upgrade::Package-Blacklist {"
  for pkg in "$@"; do
    while IFS= read -r pattern; do
      [[ -n "$pattern" ]] && printf '    "%s";\n' "$pattern"
    done < <(core_stack_blacklist_patterns "$pkg")
  done
  echo "};"
}

set_core_stack_blacklist() {
  local mode="${1:-}" pkg tmp
  shift || true
  case "$mode" in
    off)
      [[ $# -eq 0 ]] || deny "usage: updates-core-blacklist off"
      rm -f "$CORE_STACK_BLACKLIST_FILE"
      echo "core stack blacklist removed"
      ;;
    on)
      [[ $# -ge 1 ]] || deny "usage: updates-core-blacklist on <pkg> [pkg...]"
      for pkg in "$@"; do
        core_stack_blacklist_patterns "$pkg" >/dev/null || deny "core stack package not allowed: $pkg"
      done
      install -d -m 0755 /etc/apt/apt.conf.d
      tmp="$(mktemp "${CORE_STACK_BLACKLIST_FILE}.XXXXXX")"
      render_core_blacklist "$@" >"$tmp"
      chmod 0644 "$tmp"
      mv -f "$tmp" "$CORE_STACK_BLACKLIST_FILE"
      echo "core stack blacklist written: $*"
      ;;
    *)
      deny "usage: updates-core-blacklist <on|off> [pkg...]"
      ;;
  esac
}

upgrade_core_stack_packages() {
  export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none
  local valid_pkgs=() pkg need_nginx_check=0 unit after
  local changed=() reloaded=()
  declare -A before=()
  for pkg in "$@"; do
    case "$pkg" in
      nginx)
        valid_pkgs+=("nginx")
        need_nginx_check=1
        ;;
      mariadb-server|redis-server|pdns-server|exim4-daemon-heavy|dovecot-core|rspamd|openssl|fail2ban)
        valid_pkgs+=("$pkg")
        ;;
      *)
        deny "core stack package not allowed: $pkg"
        ;;
    esac
  done
  [[ ${#valid_pkgs[@]} -gt 0 ]] || deny "no core stack packages specified"

  if [[ "$need_nginx_check" -eq 1 ]] && command -v nginx >/dev/null 2>&1; then
    nginx -t >/dev/null 2>&1 || deny "nginx -t pre-upgrade configuration test failed"
  fi

  for pkg in "${valid_pkgs[@]}"; do
    before[$pkg]="$(dpkg-query -W -f='${Version}' "$pkg" 2>/dev/null || true)"
  done

  # Holds are not used any more (they block security updates too); undo any
  # an older panel left behind so --only-upgrade can move these packages.
  apt-mark unhold "${valid_pkgs[@]}" >/dev/null 2>&1 || true
  apt-get update --allow-releaseinfo-change -qq >/dev/null 2>&1 || true
  local upgrade_rc=0
  apt-get install --only-upgrade -y "${valid_pkgs[@]}" || upgrade_rc=$?
  [[ "$upgrade_rc" -eq 0 ]] || deny "apt-get --only-upgrade failed for core stack packages"

  for pkg in "${valid_pkgs[@]}"; do
    after="$(dpkg-query -W -f='${Version}' "$pkg" 2>/dev/null || true)"
    if [[ -n "${before[$pkg]}" && "$after" != "${before[$pkg]}" ]]; then
      changed+=("$pkg")
    fi
  done
  echo "upgraded=$(IFS=,; echo "${changed[*]:-}")"

  if [[ "$need_nginx_check" -eq 1 ]] && command -v nginx >/dev/null 2>&1; then
    if ! nginx -t >/dev/null 2>&1; then
      # Leave the running nginx alone: a reload with a config the new
      # binary rejects would take every site down.
      echo "post_check=failed:nginx"
      deny "nginx -t post-upgrade configuration test failed; nginx was not reloaded"
    fi
  fi

  for pkg in "${changed[@]:-}"; do
    [[ -n "$pkg" ]] || continue
    unit="$(core_stack_reload_unit "$pkg")"
    [[ -n "$unit" ]] || continue
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
      systemctl reload "$unit" >/dev/null 2>&1 || systemctl restart "$unit" >/dev/null 2>&1 || true
      reloaded+=("$unit")
    fi
  done
  echo "reloaded=$(IFS=,; echo "${reloaded[*]:-}")"
  echo "post_check=ok"
}

ensure_cron_jail_binary() {
  local src="${APP_DIR}/installer/files/spanel-cron-jail.sh"
  local dst="/usr/local/bin/spanel-cron-jail"
  if [[ -f "$src" ]]; then
    install -o root -g root -m 0755 "$src" "$dst" 2>/dev/null || true
  fi
}


PANEL_SNAPSHOT_DIR="${BACKUP_ROOT:-/var/backups/spanel}/panel-snapshots"
PANEL_DB_BACKUP_DIR="${SPANEL_DATA_DIR}/db-backups"
DEFAULT_SNAPSHOT_KEEP=5
# The units that open spanel.db. All of them are stopped while the file is
# swapped: a oneshot still holding the old file would write into a WAL that
# no longer belongs to the database on disk.
SPANEL_DB_TIMERS=(spanel-backup-scheduler.timer spanel-malware-scheduler.timer spanel-notify.timer spanel-watchdog.timer spanel-maintenance.timer)
SPANEL_DB_ONESHOTS=(spanel-backup-scheduler.service spanel-malware-scheduler.service spanel-notify.service spanel-watchdog.service spanel-maintenance.service)
# What spanel-update rewrites outside the code tree, relative to /. A rollback
# that restores the code but keeps the new units or the new tools vhost is
# half a rollback.
PANEL_SNAPSHOT_SYSTEM_GLOBS=(
  "etc/systemd/system/spanel-*.service"
  "etc/systemd/system/spanel-*.timer"
  "etc/systemd/system/spanel-api.service.d"
  "etc/nginx/conf.d/00-spanel-*.conf"
  "etc/sudoers.d/spanel"
  "usr/local/sbin/spanel"
  "usr/local/sbin/spanel-api-start"
  "usr/local/sbin/spanel-memory-guard"
  "usr/local/sbin/spanel-change-ip"
  "usr/local/bin/spanel-cron-jail"
)

panel_current_version() {
  local ver="1.0.0"
  if [[ -f "${APP_DIR}/backend/app/core/version.py" ]]; then
    ver="$(awk -F'"' '/APP_VERSION/ {print $2; exit}' "${APP_DIR}/backend/app/core/version.py" 2>/dev/null || echo "1.0.0")"
  fi
  [[ -n "$ver" ]] && echo "$ver" || echo "1.0.0"
}

# The panel's SQLite file, from DATABASE_URL in .env. Only an absolute
# sqlite:/// path with plain characters is accepted.
panel_db_path() {
  local url path
  url="$(env_get DATABASE_URL)"
  if [[ "$url" =~ ^sqlite:///(/[A-Za-z0-9._/-]+\.db)$ ]]; then
    path="${BASH_REMATCH[1]}"
  else
    path="${APP_DIR}/backend/spanel.db"
  fi
  [[ "$path" != *..* ]] || deny "invalid database path"
  echo "$path"
}

# The system python and its stdlib only (-I: no user site, no cwd on the path).
# Never the panel's venv: that would be panel-writable code running as root.
sqlite_online_copy() {
  /usr/bin/python3 -I - "$1" "$2" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close()
src.close()
PY
}

sqlite_integrity_ok() {
  /usr/bin/python3 -I - "$1" <<'PY'
import sqlite3, sys
try:
    conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30)
    row = conn.execute("PRAGMA integrity_check").fetchone()
    tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    conn.close()
except sqlite3.Error as exc:
    print(f"integrity_check: {exc}", file=sys.stderr)
    sys.exit(1)
if not row or row[0] != "ok" or tables == 0:
    print(f"integrity_check: {row[0] if row else 'no answer'} ({tables} tables)", file=sys.stderr)
    sys.exit(1)
PY
}

sqlite_alembic_revision() {
  /usr/bin/python3 -I - "$1" <<'PY' 2>/dev/null || true
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    print(",".join(r[0] for r in conn.execute("SELECT version_num FROM alembic_version")))
except sqlite3.Error:
    pass
PY
}

# Replace the panel database with <src>, safely:
#   1. integrity_check the replacement before anything is stopped;
#   2. stop the API and every timer/oneshot that opens the database;
#   3. keep the current database (read through its WAL) as
#      <db>.pre-restore-<ts>, so nothing is ever lost by a restore;
#   4. drop -wal/-shm (they belong to the old file) and move the copy in;
#   5. start what was running again.
swap_panel_db() {
  local src="$1" db ts timer tmp
  local -a active_timers=()
  db="$(panel_db_path)"
  [[ -f "$src" ]] || deny "database copy not found"
  sqlite_integrity_ok "$src" || deny "the database copy failed PRAGMA integrity_check; nothing was changed"
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  tmp="${db}.restore-${ts}"
  install -m 0640 -o spanel -g spanel "$src" "$tmp"

  for timer in "${SPANEL_DB_TIMERS[@]}"; do
    if systemctl is-active --quiet "$timer" 2>/dev/null; then
      active_timers+=("$timer")
    fi
  done
  systemctl stop "${SPANEL_DB_TIMERS[@]}" >/dev/null 2>&1 || true
  systemctl stop spanel-api >/dev/null 2>&1 || true
  # A oneshot already running is given a minute to finish, never killed: the
  # maintenance runner may be half-way through apt, the backup runner through
  # an archive. One still running after that keeps the old file open and
  # writes there - a lost status line, not a damaged database (SQLite locks
  # by inode, and the copy moved in below is a new one).
  local deadline=$((SECONDS + 60)) unit
  for unit in "${SPANEL_DB_ONESHOTS[@]}"; do
    while systemctl is-active --quiet "$unit" 2>/dev/null && (( SECONDS < deadline )); do
      sleep 2
    done
  done

  if [[ -f "$db" ]]; then
    sqlite_online_copy "$db" "${db}.pre-restore-${ts}" || cp -a "$db" "${db}.pre-restore-${ts}"
    chown spanel:spanel "${db}.pre-restore-${ts}" 2>/dev/null || true
    chmod 0640 "${db}.pre-restore-${ts}" 2>/dev/null || true
  fi
  rm -f "${db}-wal" "${db}-shm"
  mv -f "$tmp" "$db"
  # Keep the three newest pre-restore copies.
  ls -1t "${db}".pre-restore-* 2>/dev/null | tail -n +4 | xargs -r rm -f || true

  systemctl start spanel-api >/dev/null 2>&1 || true
  for timer in "${active_timers[@]:-}"; do
    [[ -n "$timer" ]] && systemctl start "$timer" >/dev/null 2>&1 || true
  done
  echo "database_restored=${db} previous=${db}.pre-restore-${ts}"
}

panel_snapshot_prune() {
  local keep="$1" s
  local old_snaps=()
  while IFS= read -r s; do
    [[ -n "$s" ]] && old_snaps+=("$s")
  done < <(ls -1t "${PANEL_SNAPSHOT_DIR}"/snapshot-*.tar.gz 2>/dev/null | tail -n +"$((keep + 1))" || true)
  for s in "${old_snaps[@]:-}"; do
    [[ -n "$s" ]] || continue
    rm -f "$s" "${s%.tar.gz}.json" 2>/dev/null || true
  done
}

panel_snapshot_create() {
  local reason="${1:-manual}" keep="${2:-$DEFAULT_SNAPSHOT_KEEP}"
  [[ "$reason" =~ ^[a-zA-Z0-9_-]{1,32}$ ]] || reason="manual"
  [[ "$keep" =~ ^[0-9]{1,2}$ ]] || deny "snapshot keep must be a number"
  (( keep >= 1 && keep <= 20 )) || deny "snapshot keep must be between 1 and 20"
  install -d -o root -g spanel -m 0750 "$PANEL_SNAPSHOT_DIR"
  local ver ts snap_id work tar_path meta_path size_bytes db revision="" glob path
  local -a system_paths=()
  ver="$(panel_current_version)"
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  snap_id="snapshot-v${ver}-${ts}"
  tar_path="${PANEL_SNAPSHOT_DIR}/${snap_id}.tar.gz"
  meta_path="${PANEL_SNAPSHOT_DIR}/${snap_id}.json"
  work="$(mktemp -d /tmp/spanel-snap.XXXXXX)"

  mkdir -p "$work/backend" "$work/frontend" "$work/sbin"
  [[ -d "${APP_DIR}/backend/app" ]] && cp -a "${APP_DIR}/backend/app" "$work/backend/app"
  [[ -d "${APP_DIR}/backend/alembic" ]] && cp -a "${APP_DIR}/backend/alembic" "$work/backend/alembic"
  [[ -f "${APP_DIR}/backend/requirements.txt" ]] && cp -a "${APP_DIR}/backend/requirements.txt" "$work/backend/requirements.txt"
  [[ -d "${APP_DIR}/frontend/dist" ]] && cp -a "${APP_DIR}/frontend/dist" "$work/frontend/dist"
  [[ -f "/usr/local/sbin/spanel-helper" ]] && cp -a "/usr/local/sbin/spanel-helper" "$work/sbin/spanel-helper"
  [[ -f "/usr/local/sbin/spanel-update" ]] && cp -a "/usr/local/sbin/spanel-update" "$work/sbin/spanel-update"

  # The database through SQLite's online backup API, which reads committed
  # pages from the WAL too: a plain cp of a WAL-mode file can miss them.
  # Taken before spanel-update runs any migration, so restoring it is what
  # reverses one (Alembic downgrades are not written for this schema).
  db="$(panel_db_path)"
  if [[ -f "$db" ]]; then
    sqlite_online_copy "$db" "$work/backend/spanel.db.bak" || cp -a "$db" "$work/backend/spanel.db.bak"
    revision="$(sqlite_alembic_revision "$work/backend/spanel.db.bak")"
  fi

  # The virtualenv is ~160 MB, too much to keep five copies of. Its exact
  # package set is recorded instead; a rollback reinstalls it when it differs.
  if [[ -x "${APP_DIR}/backend/.venv/bin/python" ]]; then
    "${APP_DIR}/backend/.venv/bin/python" -m pip freeze --all >"$work/backend/pip-freeze.txt" 2>/dev/null || rm -f "$work/backend/pip-freeze.txt"
  fi

  shopt -s nullglob
  for glob in "${PANEL_SNAPSHOT_SYSTEM_GLOBS[@]}"; do
    for path in /$glob; do
      [[ -e "$path" && ! -L "$path" ]] && system_paths+=("${path#/}")
    done
  done
  shopt -u nullglob
  if [[ ${#system_paths[@]} -gt 0 ]]; then
    tar -C / -cpf "$work/system.tar" "${system_paths[@]}" 2>/dev/null || rm -f "$work/system.tar"
  fi

  tar -czf "$tar_path" -C "$work" .
  rm -rf "$work"
  chmod 0640 "$tar_path"
  chown root:spanel "$tar_path" 2>/dev/null || true
  size_bytes="$(stat -c %s "$tar_path" 2>/dev/null || echo 0)"

  cat >"$meta_path" <<EOF
{
  "id": "${snap_id}",
  "version": "${ver}",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "size_bytes": ${size_bytes},
  "reason": "${reason}",
  "db_revision": "${revision}",
  "has_db": $([[ -n "$revision" || -f "$db" ]] && echo true || echo false),
  "has_system_files": $([[ ${#system_paths[@]} -gt 0 ]] && echo true || echo false)
}
EOF
  chmod 0640 "$meta_path"
  chown root:spanel "$meta_path" 2>/dev/null || true

  panel_snapshot_prune "$keep"
  echo "${snap_id}"
}

# Put the system files of a snapshot back. The current versions are kept
# aside first; if nginx refuses the restored set, they go back.
panel_snapshot_restore_system() {
  local work="$1" saved path
  local -a paths=()
  [[ -f "$work/system.tar" ]] || return 0
  while IFS= read -r path; do
    [[ -n "$path" && "$path" != /* && "$path" != *..* ]] && paths+=("$path")
  done < <(tar -tf "$work/system.tar" 2>/dev/null)
  [[ ${#paths[@]} -gt 0 ]] || return 0
  saved="$work/system-current.tar"
  tar -C / -cpf "$saved" --ignore-failed-read "${paths[@]}" 2>/dev/null || true
  tar -C / -xpf "$work/system.tar"
  # Units the newer release added and the snapshot's release does not know:
  # left in place they would start code that is no longer there.
  local unit_file unit
  local -A in_snapshot=()
  for path in "${paths[@]}"; do
    in_snapshot["$path"]=1
  done
  # Only when the snapshot did record units, and never the API itself.
  if [[ -n "${in_snapshot[etc/systemd/system/spanel-api.service]:-}" ]]; then
    shopt -s nullglob
    for unit_file in /etc/systemd/system/spanel-*.service /etc/systemd/system/spanel-*.timer; do
      [[ "$unit_file" == /etc/systemd/system/spanel-api.service ]] && continue
      if [[ -z "${in_snapshot[${unit_file#/}]:-}" ]]; then
        unit="$(basename "$unit_file")"
        systemctl disable --now "$unit" >/dev/null 2>&1 || true
        rm -f "$unit_file"
      fi
    done
    shopt -u nullglob
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
  if command -v nginx >/dev/null 2>&1 && ! nginx -t >/dev/null 2>&1; then
    echo "restored nginx files fail nginx -t; keeping the current ones" >&2
    [[ -f "$saved" ]] && tar -C / -xpf "$saved" 2>/dev/null || true
    systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  visudo -c -f /etc/sudoers.d/spanel >/dev/null 2>&1 || {
    [[ -f "$saved" ]] && tar -C / -xpf "$saved" etc/sudoers.d/spanel 2>/dev/null || true
  }
}

panel_snapshot_restore_venv() {
  local work="$1" current
  [[ -f "$work/backend/pip-freeze.txt" && -x "${APP_DIR}/backend/.venv/bin/python" ]] || return 0
  current="$(mktemp)"
  "${APP_DIR}/backend/.venv/bin/python" -m pip freeze --all >"$current" 2>/dev/null || true
  if ! cmp -s "$current" "$work/backend/pip-freeze.txt"; then
    echo "Python packages differ from the snapshot; reinstalling the recorded set" >&2
    "${APP_DIR}/backend/.venv/bin/python" -m pip install --no-input -q -r "$work/backend/pip-freeze.txt" >&2 \
      || echo "pip could not reinstall the recorded packages; the panel may need: spanel-update" >&2
  fi
  rm -f "$current"
}

# Record the outcome of a restore that ran detached from the API, so the
# Updates page does not show "rolling back" forever. The directory belongs to
# the panel account: never follow a link it planted, write a fresh file and
# rename it over the old name.
panel_update_state_set() {
  local status="$1" message="$2" snap="$3"
  /usr/bin/python3 -I - "${SPANEL_DATA_DIR}/update-status.json" "$status" "$message" "$snap" <<'PY' || true
import json, os, sys, tempfile, time
path, status, message, snap = sys.argv[1:5]
state = {}
if os.path.isfile(path) and not os.path.islink(path):
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = {}
state.update({"last_update_status": status, "progress_phase": status, "progress_percent": 100,
              "progress_message": message, "last_update_message": message, "rollback_snapshot_id": snap,
              "last_update_finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".update-status.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.chmod(tmp, 0o640)
os.replace(tmp, path)
PY
  chown spanel:spanel "${SPANEL_DATA_DIR}/update-status.json" 2>/dev/null || true
}

panel_snapshot_restore() {
  local snap_id="${1:-}" restore_db="" now="" arg
  [[ "$snap_id" =~ ^snapshot-[A-Za-z0-9._-]+$ ]] || deny "invalid snapshot id: $snap_id"
  shift || true
  for arg in "$@"; do
    case "$arg" in
      --restore-db) restore_db=1 ;;
      --now) now=1 ;;
      *) deny "usage: panel-snapshot-restore <snapshot-id> [--restore-db] [--now]" ;;
    esac
  done
  local tar_path="${PANEL_SNAPSHOT_DIR}/${snap_id}.tar.gz"
  [[ -f "$tar_path" ]] || deny "snapshot not found: $snap_id"

  # From the API this runs inside spanel-api's own cgroup, and stopping
  # spanel-api (to swap the database, or to restart it) would kill this
  # very process half-way. Run the restore in a unit of its own instead.
  if [[ -z "$now" ]] && command -v systemd-run >/dev/null 2>&1; then
    systemd-run --unit="spanel-snapshot-restore-$(date +%s)" --collect \
      --description="SPanel snapshot restore ${snap_id}" \
      --on-active=2s \
      --setenv=SUDO_USER=spanel \
      /usr/local/sbin/spanel-helper panel-snapshot-restore "$snap_id" ${restore_db:+--restore-db} --now >/dev/null
    echo "scheduled=${snap_id}"
    return 0
  fi

  local work
  work="$(mktemp -d /tmp/spanel-restore.XXXXXX)"
  tar -xzf "$tar_path" -C "$work"

  if [[ -d "$work/backend/app" ]]; then
    rm -rf "${APP_DIR}/backend/app.prev" 2>/dev/null || true
    [[ -d "${APP_DIR}/backend/app" ]] && mv "${APP_DIR}/backend/app" "${APP_DIR}/backend/app.prev"
    cp -a "$work/backend/app" "${APP_DIR}/backend/app"
    rm -rf "${APP_DIR}/backend/app.prev" 2>/dev/null || true
    chown -R spanel:spanel "${APP_DIR}/backend/app" 2>/dev/null || true
  fi
  if [[ -d "$work/backend/alembic" ]]; then
    rm -rf "${APP_DIR}/backend/alembic" 2>/dev/null || true
    cp -a "$work/backend/alembic" "${APP_DIR}/backend/alembic"
    chown -R spanel:spanel "${APP_DIR}/backend/alembic" 2>/dev/null || true
  fi
  if [[ -d "$work/frontend/dist" ]]; then
    rm -rf "${APP_DIR}/frontend/dist.prev" 2>/dev/null || true
    [[ -d "${APP_DIR}/frontend/dist" ]] && mv "${APP_DIR}/frontend/dist" "${APP_DIR}/frontend/dist.prev"
    cp -a "$work/frontend/dist" "${APP_DIR}/frontend/dist"
    rm -rf "${APP_DIR}/frontend/dist.prev" 2>/dev/null || true
    chown -R spanel:spanel "${APP_DIR}/frontend/dist" 2>/dev/null || true
  fi
  if [[ -f "$work/sbin/spanel-helper" ]]; then
    cp -a "$work/sbin/spanel-helper" "/usr/local/sbin/spanel-helper.new"
    chmod 0750 "/usr/local/sbin/spanel-helper.new"
    chown root:spanel "/usr/local/sbin/spanel-helper.new" 2>/dev/null || true
    mv -f "/usr/local/sbin/spanel-helper.new" "/usr/local/sbin/spanel-helper"
  fi
  if [[ -f "$work/sbin/spanel-update" ]]; then
    cp -a "$work/sbin/spanel-update" "/usr/local/sbin/spanel-update.new"
    chmod 0755 "/usr/local/sbin/spanel-update.new"
    chown root:root "/usr/local/sbin/spanel-update.new" 2>/dev/null || true
    mv -f "/usr/local/sbin/spanel-update.new" "/usr/local/sbin/spanel-update"
  fi
  panel_snapshot_restore_system "$work"
  panel_snapshot_restore_venv "$work"

  if [[ -n "$restore_db" && -f "$work/backend/spanel.db.bak" ]]; then
    # swap_panel_db stops and starts spanel-api itself.
    swap_panel_db "$work/backend/spanel.db.bak"
  else
    systemctl restart spanel-api >/dev/null 2>&1 || true
  fi

  rm -rf "$work"
  rm -rf /var/lib/spanel/update-state 2>/dev/null || true
  systemctl reload nginx >/dev/null 2>&1 || true
  panel_update_state_set "rolled_back" "Restored snapshot ${snap_id}${restore_db:+ (database included)}." "$snap_id"
  echo "restored=${snap_id}"
}

panel_snapshot_delete() {
  local snap_id="${1:-}"
  [[ "$snap_id" =~ ^snapshot-[A-Za-z0-9._-]+$ ]] || deny "invalid snapshot id: $snap_id"
  rm -f "${PANEL_SNAPSHOT_DIR}/${snap_id}.tar.gz" "${PANEL_SNAPSHOT_DIR}/${snap_id}.json" 2>/dev/null || true
  echo "deleted=${snap_id}"
}

# ---- panel self-backup (disaster recovery) ----------------------------------
# One encrypted archive of everything that makes this server this panel and is
# not in the per-account backups: the panel database and .env (SECRET_KEY
# decrypts every stored secret), /etc/spanel, certificates, the nginx/PHP/mail
# configuration the panel wrote, DKIM keys and the PowerDNS zones.
#
# It is encrypted here, as root, with a passphrase only root can read
# (PANEL_DR_KEY_FILE). The panel account uploads the result but cannot open
# it: the archive holds secrets - the audit key, DKIM private keys - that the
# panel account itself is not meant to be able to read back from a bucket.
PANEL_DR_DIR="${BACKUP_ROOT:-/var/backups/spanel}/panel-dr"
PANEL_DR_KEY_FILE="/etc/spanel/dr-passphrase"
PANEL_DR_KEEP_LOCAL=3
# Relative to /. Globs are expanded; what does not exist is skipped.
PANEL_DR_PATHS=(
  "opt/spanel/backend/.env"
  "etc/spanel"
  "var/lib/spanel"
  "etc/nginx/nginx.conf"
  "etc/nginx/conf.d"
  "etc/nginx/spanel"
  "etc/php/*/fpm/pool.d"
  "etc/php/*/fpm/conf.d/*spanel*"
  "etc/php/*/cli/conf.d/*spanel*"
  "etc/exim4"
  "etc/dovecot"
  "etc/rspamd/local.d"
  "etc/rspamd/override.d"
  "etc/spanel-mail-tls"
  "etc/powerdns"
  "etc/letsencrypt"
  "etc/mysql/mariadb.conf.d/90-spanel-tuning.cnf"
  "etc/ssh/sshd_config.d"
  "etc/sudoers.d/spanel"
  "etc/systemd/system/spanel-*.service"
  "etc/systemd/system/spanel-*.timer"
  "var/spool/cron/crontabs"
)
# Never in the archive: the passphrase that encrypts it, bulky caches and
# copies that are themselves backups.
PANEL_DR_EXCLUDES=(
  "etc/spanel/dr-passphrase"
  "var/lib/spanel/db-backups"
  "var/lib/spanel/geoip"
  "var/lib/spanel/malware-scan-jobs"
  "var/lib/spanel/da-import"
  "var/lib/spanel/import-stage"
  "*.sock"
)

# The include list, relative to <root> (/ on a server; a test points it at a
# fixture tree). The app directory is taken from APP_DIR when it differs.
panel_dr_include_paths() {
  local root="${1:-/}" glob path app_rel
  app_rel="${APP_DIR#/}"
  shopt -s nullglob
  for glob in "${PANEL_DR_PATHS[@]}"; do
    glob="${glob/opt\/spanel/$app_rel}"
    for path in "${root%/}"/$glob; do
      [[ -e "$path" && ! -L "$path" ]] || continue
      printf '%s\n' "${path#"${root%/}"/}"
    done
  done
  shopt -u nullglob
}

panel_dr_fingerprint() {
  /usr/bin/python3 -I - "$PANEL_DR_KEY_FILE" <<'PY'
import hashlib, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    secret = handle.readline().strip()
print(hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16])
PY
}

panel_dr_key_status() {
  if [[ -s "$PANEL_DR_KEY_FILE" ]]; then
    echo "initialised=yes"
    echo "fingerprint=$(panel_dr_fingerprint)"
  else
    echo "initialised=no"
  fi
}

# Generate the passphrase and print it, once. It is not printed again by
# anything: the panel shows it to the administrator, who must keep a copy
# off this server - without it the archive cannot be opened on a new one.
panel_dr_key_init() {
  local mode="${1:-}" tmp
  if [[ -s "$PANEL_DR_KEY_FILE" && "$mode" != "--rotate" ]]; then
    deny "the panel self-backup passphrase is already set; rotate it explicitly"
  fi
  [[ -z "$mode" || "$mode" == "--rotate" ]] || deny "usage: panel-dr-key-init [--rotate]"
  install -d -o root -g spanel -m 0750 /etc/spanel
  tmp="$(mktemp /etc/spanel/.dr-passphrase.XXXXXX)"
  chmod 0600 "$tmp"
  /usr/bin/python3 -I -c 'import secrets; print(secrets.token_urlsafe(30))' >"$tmp"
  [[ -s "$tmp" ]] || { rm -f "$tmp"; deny "could not generate a passphrase"; }
  chown root:root "$tmp"
  mv -f "$tmp" "$PANEL_DR_KEY_FILE"
  echo "passphrase=$(head -n 1 "$PANEL_DR_KEY_FILE")"
  echo "fingerprint=$(panel_dr_fingerprint)"
}

# Encrypt <plain> to <out> in the panel's backup format: OpenSSL
# AES-256-CBC with PBKDF2-HMAC-SHA256 (100000 rounds, "Salted__" header),
# then an SPNLMAC1 + HMAC-SHA256 trailer over header and ciphertext, keyed
# with bytes 48-80 of the same PBKDF2 output. backup.decrypt_backup_archive
# and `spanelctl dr-restore` both open it. The passphrase never appears on a
# command line: openssl reads it from the root-only file.
panel_dr_encrypt() {
  local plain="$1" out="$2"
  openssl enc -aes-256-cbc -salt -pbkdf2 -iter 100000 -md sha256 \
    -pass file:"$PANEL_DR_KEY_FILE" -in "$plain" -out "$out"
  /usr/bin/python3 -I - "$out" "$PANEL_DR_KEY_FILE" <<'PY'
import hashlib, hmac, sys
path, keyfile = sys.argv[1], sys.argv[2]
with open(keyfile, encoding="utf-8") as handle:
    secret = handle.readline().strip()
with open(path, "rb") as handle:
    header = handle.read(16)
    if header[:8] != b"Salted__":
        sys.exit("openssl did not write a salted header")
    key = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), header[8:16], 100000, dklen=80)
    mac = hmac.new(key[48:80], digestmod=hashlib.sha256)
    mac.update(header)
    while chunk := handle.read(1024 * 1024):
        mac.update(chunk)
with open(path, "ab") as handle:
    handle.write(b"SPNLMAC1" + mac.digest())
PY
}

# PowerDNS data the way its backend holds it, plus a plain zone-file export
# that any backend can load (pdnsutil load-zone).
panel_dr_dump_pdns() {
  local out="$1" dbname zone
  mkdir -p "$out/zones"
  if [[ -f "$PDNS_DB" ]]; then
    sqlite_online_copy "$PDNS_DB" "$out/pdns.sqlite3" && echo "gsqlite3" >"$out/backend"
  elif grep -qs '^launch=.*gmysql' /etc/powerdns/pdns.conf /etc/powerdns/pdns.d/*.conf 2>/dev/null; then
    dbname="$(awk -F= '/^gmysql-dbname=/{print $2; exit}' /etc/powerdns/pdns.conf /etc/powerdns/pdns.d/*.conf 2>/dev/null || true)"
    dbname="${dbname:-pdns}"
    [[ "$dbname" =~ ^[A-Za-z0-9_]{1,64}$ ]] || dbname="pdns"
    if mysqldump --single-transaction --routines "$dbname" >"$out/pdns.sql" 2>/dev/null; then
      echo "gmysql:${dbname}" >"$out/backend"
    else
      rm -f "$out/pdns.sql"
    fi
  fi
  if command -v pdnsutil >/dev/null 2>&1; then
    while IFS= read -r zone; do
      zone="${zone%.}"
      is_domain "$zone" || continue
      pdnsutil list-zone "$zone" >"$out/zones/${zone}.zone" 2>/dev/null || rm -f "$out/zones/${zone}.zone"
    done < <(pdnsutil list-all-zones 2>/dev/null)
  fi
}

panel_dr_archive() {
  [[ -s "$PANEL_DR_KEY_FILE" ]] || deny "set the panel self-backup passphrase first (panel-dr-key-init)"
  local host ts work name plain out manifest db exclude
  local -a includes=() tar_excludes=()
  host="$(hostname -f 2>/dev/null || hostname)"
  host="$(printf '%s' "$host" | tr -c 'A-Za-z0-9.-' '-' | cut -c1-63)"
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  name="panel-dr-${host}-${ts}"
  install -d -o root -g spanel -m 0750 "$PANEL_DR_DIR"
  work="$(mktemp -d /var/tmp/spanel-dr.XXXXXX)"
  chmod 0700 "$work"
  PANEL_DR_WORK="$work"   # removed by the EXIT trap set in the command case

  mkdir -p "$work/db" "$work/pdns"
  db="$(panel_db_path)"
  [[ -f "$db" ]] && sqlite_online_copy "$db" "$work/db/spanel.db"
  panel_dr_dump_pdns "$work/pdns"

  while IFS= read -r path; do
    [[ -n "$path" ]] && includes+=("$path")
  done < <(panel_dr_include_paths /)
  for exclude in "${PANEL_DR_EXCLUDES[@]}"; do
    tar_excludes+=("--exclude=${exclude}")
  done
  # The live database is in db/ as an online copy; the file itself may be
  # mid-write.
  tar_excludes+=("--exclude=${db#/}" "--exclude=${db#/}-wal" "--exclude=${db#/}-shm")
  if [[ ${#includes[@]} -gt 0 ]]; then
    tar -C / -cpf "$work/files.tar" "${tar_excludes[@]}" --ignore-failed-read "${includes[@]}" 2>/dev/null || true
  fi

  /usr/bin/python3 -I - "$work" "$host" "$(panel_current_version)" "${includes[@]}" <<'PY'
import json, os, sqlite3, sys, time
work, host, version, includes = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
revision = ""
db = os.path.join(work, "db", "spanel.db")
if os.path.isfile(db):
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        revision = ",".join(r[0] for r in conn.execute("SELECT version_num FROM alembic_version"))
        conn.close()
    except sqlite3.Error:
        pass
backend = ""
backend_file = os.path.join(work, "pdns", "backend")
if os.path.isfile(backend_file):
    backend = open(backend_file, encoding="utf-8").read().strip()
zones = sorted(f[:-5] for f in os.listdir(os.path.join(work, "pdns", "zones")) if f.endswith(".zone")) \
    if os.path.isdir(os.path.join(work, "pdns", "zones")) else []
manifest = {
    "kind": "spanel_panel_dr",
    "format": 1,
    "hostname": host,
    "panel_version": version,
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "db_revision": revision,
    "has_database": os.path.isfile(db),
    "pdns_backend": backend,
    "dns_zones": len(zones),
    "paths": includes,
}
with open(os.path.join(work, "manifest.json"), "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2)
PY

  plain="$work/${name}.tar.gz"
  tar -C "$work" -czf "$plain" manifest.json db pdns $([[ -f "$work/files.tar" ]] && echo files.tar)
  out="${PANEL_DR_DIR}/${name}.spdr"
  manifest="${PANEL_DR_DIR}/${name}.json"
  panel_dr_encrypt "$plain" "$out.tmp"
  mv -f "$out.tmp" "$out"
  # The manifest beside the archive is plain and holds no secret: what is in
  # it, for the page and for a person choosing which archive to restore.
  /usr/bin/python3 -I - "$work/manifest.json" "$out" "$manifest" <<'PY'
import hashlib, json, os, sys
src, archive, dst = sys.argv[1:4]
data = json.load(open(src, encoding="utf-8"))
digest = hashlib.sha256()
with open(archive, "rb") as handle:
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
data.update({"archive": os.path.basename(archive), "size": os.path.getsize(archive), "sha256": digest.hexdigest()})
with open(dst, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
PY
  chown root:spanel "$out" "$manifest"
  chmod 0640 "$out" "$manifest"

  ls -1t "${PANEL_DR_DIR}"/panel-dr-*.spdr 2>/dev/null | tail -n +"$((PANEL_DR_KEEP_LOCAL + 1))" | while IFS= read -r old; do
    rm -f "$old" "${old%.spdr}.json"
  done || true
  echo "archive=${out}"
  echo "manifest=${manifest}"
}

# Hot database backups (taken by the watchdog every 6 h and on demand) live
# in PANEL_DB_BACKUP_DIR as spanel-<UTC stamp>-<reason>.db.
panel_db_restore() {
  local name="${1:-}" mode="${2:-}" src
  [[ "$name" =~ ^spanel-[0-9]{8}T[0-9]{6}Z-[a-z0-9_-]{1,24}\.db$ ]] || deny "invalid database backup name"
  src="${PANEL_DB_BACKUP_DIR}/${name}"
  [[ -f "$src" && ! -L "$src" ]] || deny "database backup not found: $name"
  sqlite_integrity_ok "$src" || deny "the backup failed PRAGMA integrity_check; nothing was changed"
  if [[ "$mode" == "--now" ]] || ! command -v systemd-run >/dev/null 2>&1; then
    swap_panel_db "$src"
    return 0
  fi
  [[ -z "$mode" ]] || deny "usage: panel-db-restore <backup-name> [--now]"
  # Same reason as panel-snapshot-restore: stopping spanel-api from inside
  # spanel-api's cgroup would kill this process before the swap.
  systemd-run --unit="spanel-db-restore-$(date +%s)" --collect \
    --description="SPanel database restore ${name}" \
    --on-active=2s \
    --setenv=SUDO_USER=spanel \
    /usr/local/sbin/spanel-helper panel-db-restore "$name" --now >/dev/null
  echo "scheduled=${name}"
}

run_panel_update() {
  [[ -f "$UPDATE_SCRIPT" ]] || deny "missing $UPDATE_SCRIPT"
  local unit="spanel-panel-update"
  if systemctl is-active --quiet "${unit}.service"; then
    echo "Panel update is already running: ${unit}.service"
    return 0
  fi
  if command -v systemd-run >/dev/null 2>&1; then
    systemd-run \
      --unit="$unit" \
      --collect \
      --description="SPanel Safe Transactional Online Update" \
      --property="Environment=SOURCE_DIR=${SOURCE_DIR}" \
      --property="Environment=APP_DIR=${APP_DIR}" \
      --property="Environment=REPO_URL=${REPO_URL:-https://github.com/shostwp-byte/spanel.git}" \
      --property="Environment=GIT_REMOTE=${GIT_REMOTE:-origin}" \
      --property="Environment=UPDATE_CHANNEL=${UPDATE_CHANNEL:-release}" \
      --property="Environment=BRANCH=${BRANCH:-main}" \
      --property="Environment=RELEASE_TAG=${RELEASE_TAG:-}" \
      --property="Environment=RELEASE_PATTERN=${RELEASE_PATTERN:-v[0-9]*.[0-9]*.[0-9]*}" \
      --property="Environment=SKIP_PULL=${SKIP_PULL:-false}" \
      /bin/bash "$UPDATE_SCRIPT"
    echo "Panel update started: ${unit}.service"
    echo "Check progress: journalctl -u ${unit}.service -f"
    return 0
  fi
  nohup env \
    SOURCE_DIR="$SOURCE_DIR" \
    APP_DIR="$APP_DIR" \
    REPO_URL="${REPO_URL:-https://github.com/shostwp-byte/spanel.git}" \
    GIT_REMOTE="${GIT_REMOTE:-origin}" \
    UPDATE_CHANNEL="${UPDATE_CHANNEL:-release}" \
    BRANCH="${BRANCH:-main}" \
    RELEASE_TAG="${RELEASE_TAG:-}" \
    RELEASE_PATTERN="${RELEASE_PATTERN:-v[0-9]*.[0-9]*.[0-9]*}" \
    SKIP_PULL="${SKIP_PULL:-false}" \
    /bin/bash "$UPDATE_SCRIPT" \
    >/var/log/spanel-panel-update.log 2>&1 &
  echo "Panel update started in background. Log: /var/log/spanel-panel-update.log"
}

write_modsec_base_conf() {
  install -d -o root -g root -m 0755 /etc/nginx/modsec /etc/nginx/modsec/sites
  {
    [[ -f /etc/modsecurity/modsecurity.conf ]] && echo "Include /etc/modsecurity/modsecurity.conf"
    echo "SecRuleEngine On"
    # Request bodies are not buffered: uploads on a shared host are large and
    # frequent, and buffering them costs memory on every PHP site at once.
    #
    # The consequence is easy to miss and was live for months: with body access
    # off the nginx connector never runs phase 2 at all, so a phase:2 rule is
    # silently dead - it loads, it shows as enabled, and it never matches.
    # Every rule SPanel ships is therefore phase:1, which sees the URI and the
    # query string. test_waf_rules_are_phase_1 enforces that.
    #
    # Turning this on is what a payload-inspecting rule set (OWASP CRS) needs,
    # and it must arrive together with CRS's exclusion tuning: on its own it
    # would make the traversal rule match "../" inside any post body a customer
    # saves.
    echo "SecRequestBodyAccess Off"
  } >/etc/nginx/modsec/spanel-base.conf
}

write_modsec_main_conf() {
  write_waf_default_rules
  write_modsec_base_conf
  touch /etc/nginx/modsec/spanel-custom.conf
  {
    echo "Include /etc/nginx/modsec/spanel-base.conf"
    echo "Include /etc/nginx/modsec/spanel-default.conf"
    echo "Include /etc/nginx/modsec/spanel-custom.conf"
  } >/etc/nginx/modsec/spanel-main.conf
}

write_waf_default_rules() {
  install -d -o root -g root -m 0755 /etc/nginx/modsec
  cat >/etc/nginx/modsec/spanel-default.conf <<'RULES'
# SPanel default WAF rules: lightweight WordPress, Laravel, and PHP probes only.
SecRule REQUEST_URI "@rx (?i)(?:/\.env(?:\.|$)|/\.user\.ini(?:\.|$)|/\.git/|/composer\.(?:json|lock)(?:$|[?])|/(?:phpinfo|info)\.php(?:$|[?])|/(?:config|database|db)\.php\.(?:bak|old|save|txt)(?:$|[?]))" "id:1001301,phase:1,deny,status:403,log,msg:'SPanel blocked PHP sensitive file probe'"
SecRule REQUEST_URI|ARGS "@rx (?i)(?:\.\./|\.\.\\|%2e%2e%2f|%252e%252e%252f)" "id:1001302,phase:1,deny,status:403,log,msg:'SPanel blocked PHP path traversal'"
SecRule REQUEST_URI "@rx (?i)(?:/(?:c99|r57|shell|cmd|wso)\.php(?:$|[?])|/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin\.php(?:$|[?]))" "id:1001303,phase:1,deny,status:403,log,msg:'SPanel blocked PHP runtime probe'"
SecRule REQUEST_URI "@rx (?i)(?:/\.env(?:\.|$)|/artisan(?:$|[?])|/server\.php(?:$|[?])|/storage/logs/[^?]*\.log(?:$|[?])|/bootstrap/cache/[^?]*\.php(?:$|[?]))" "id:1001201,phase:1,deny,status:403,log,msg:'SPanel blocked Laravel sensitive path'"
SecRule REQUEST_URI "@rx (?i)(?:/_ignition/execute-solution(?:$|[?]))" "id:1001202,phase:1,deny,status:403,log,msg:'SPanel blocked Laravel Ignition RCE probe'"
SecRule REQUEST_URI "@rx (?i)(?:/wp-config\.php(?:\.|$|[?])|/wp-content/(?:uploads|cache|upgrade)/[^?]*\.php(?:$|[?])|/wp-admin/includes/[^?]*\.php(?:$|[?])|/wp-includes/[^?]*\.php(?:$|[?]))" "id:1001101,phase:1,deny,status:403,log,msg:'SPanel blocked WordPress sensitive path'"
SecRule ARGS:author "@rx ^[0-9]+$" "id:1001103,phase:1,deny,status:403,log,msg:'SPanel blocked WordPress author enumeration'"
SecRule REQUEST_URI "@rx (?i)(?:/wp-admin/install\.php(?:$|[?])|/wp-admin/setup-config\.php(?:$|[?]))" "id:1001104,phase:1,deny,status:403,log,msg:'SPanel blocked WordPress installer probe'"
RULES
}

ORPHAN_ARCHIVE_ROOT=/root/spanel-removed

orphan_live_domains() {
  # The panel owns the truth about which domains exist, so it hands the list in
  # on stdin rather than the helper guessing from the filesystem it is about to
  # delete from. Anything that is not a valid domain is dropped, not trusted.
  local line
  while IFS= read -r line; do
    line="$(printf '%s' "$line" | tr -d '[:space:]' | tr 'A-Z' 'a-z')"
    [[ -n "$line" ]] || continue
    is_domain "$line" || continue
    printf '%s\n' "$line"
  done
}

orphan_cert_covers_live() {
  # A lineage named for a dead site can still carry a live name as a SAN, and
  # deleting it would take that live site's HTTPS down.
  local cert="$1" live_file="$2" san
  [[ -f "$cert" ]] || return 1
  while read -r san; do
    [[ -n "$san" ]] || continue
    grep -qxF "$san" "$live_file" && return 0
  done < <( { openssl x509 -ext subjectAltName -noout -in "$cert" 2>/dev/null || true; } \
            | grep -oE 'DNS:[^,]+' | sed 's/DNS://g; s/ //g' )
  return 1
}

cleanup_orphans() {
  # Remove what is left on disk for websites this panel no longer has.
  #
  # Everything is copied into /root/spanel-removed first. These are customer
  # certificates and configuration: "unreferenced" is a strong inference, not a
  # certainty, and an admin who removed a site by accident should be able to get
  # it back. Nothing here is ever deleted without a copy.
  local mode="${1:-clean}" live_file stamp archive panel_domain name base
  local sock vhost_socks php_version removed_pools
  local -i certs=0 rules=0 baks=0 manual=0 sni=0 pools=0
  live_file="$(mktemp)"
  orphan_live_domains >"$live_file"
  panel_domain="$(env_get PANEL_DOMAIN)"
  [[ -n "$panel_domain" ]] && printf '%s\n' "$panel_domain" >>"$live_file"
  # An empty list almost certainly means the caller failed, not that the server
  # hosts nothing. Refuse rather than delete everything on the machine.
  if [[ ! -s "$live_file" ]]; then
    rm -f "$live_file"
    deny "refusing to clean orphans: no live domains were supplied"
  fi

  stamp="$(date -u +%Y%m%d-%H%M%S)"
  archive="${ORPHAN_ARCHIVE_ROOT}/orphans-${stamp}"
  [[ "$mode" == "clean" ]] && install -d -m 0700 "$archive"

  is_live() { grep -qxF "$1" "$live_file"; }

  # 1. Let's Encrypt lineages for sites that are gone. These are the ones that
  #    matter: the renewal config keeps waking certbot.timer and starts failing
  #    the day the domain stops pointing here.
  for conf in /etc/letsencrypt/renewal/*.conf; do
    [[ -f "$conf" ]] || continue
    name="$(basename "$conf" .conf)"
    is_domain "$name" || continue
    is_live "$name" && continue
    orphan_cert_covers_live "/etc/letsencrypt/live/${name}/cert.pem" "$live_file" && continue
    echo "cert	${name}"
    certs+=1
    if [[ "$mode" == "clean" ]]; then
      install -d -m 0700 "${archive}/certs"
      cp -a "$conf" "${archive}/certs/" 2>/dev/null || true
      tar czhf "${archive}/certs/${name}.tar.gz" -C /etc/letsencrypt/live "$name" 2>/dev/null || true
      certbot delete --cert-name "$name" --non-interactive >/dev/null 2>&1 || true
    fi
  done

  # 2. Per-site WAF rule files. delete_waf_site_rules has the check that matters
  #    - a file a running vhost still names must never go - so reuse it.
  for f in /etc/nginx/modsec/sites/*.conf; do
    [[ -f "$f" ]] || continue
    name="$(basename "$f" .conf)"
    is_domain "$name" || continue
    is_live "$name" && continue
    echo "waf-rules	${name}"
    rules+=1
    if [[ "$mode" == "clean" ]]; then
      install -d -m 0700 "${archive}/waf"
      cp -a "$f" "${archive}/waf/" 2>/dev/null || true
      delete_waf_site_rules "$name" >/dev/null 2>&1 || true
    fi
  done

  # 3. Vhost backups nginx never reads. Only for domains with no vhost left.
  for f in /etc/nginx/conf.d/*.conf.bak*; do
    [[ -f "$f" ]] || continue
    base="$(basename "$f")"
    name="${base%%.conf.bak*}"
    is_domain "$name" || continue
    is_live "$name" && continue
    [[ -f "/etc/nginx/conf.d/${name}.conf" ]] && continue
    echo "vhost-backup	${base}"
    baks+=1
    if [[ "$mode" == "clean" ]]; then
      install -d -m 0700 "${archive}/vhost"
      cp -a "$f" "${archive}/vhost/" 2>/dev/null || true
      rm -f "$f"
    fi
  done

  # 4. Uploaded certificates for sites that are gone.
  for d in /etc/nginx/spanel/ssl/sites/*/; do
    [[ -d "$d" ]] || continue
    name="$(basename "$d")"
    is_domain "$name" || continue
    is_live "$name" && continue
    echo "manual-ssl	${name}"
    manual+=1
    if [[ "$mode" == "clean" ]]; then
      install -d -m 0700 "${archive}/manual-ssl"
      tar czf "${archive}/manual-ssl/${name}.tar.gz" -C /etc/nginx/spanel/ssl/sites "$name" 2>/dev/null || true
      remove_manual_ssl "$name" >/dev/null 2>&1 || true
    fi
  done

  # 5. SNI copies the panel serves on :2222. sync_panel_sni_certificates drops
  #    copies whose source is gone, so this only reports what it will clear.
  for d in "$PANEL_SNI_DIR"/*/; do
    [[ -d "$d" ]] || continue
    name="$(basename "$d")"
    is_domain "$name" || continue
    is_live "$name" && continue
    echo "sni-copy	${name}"
    sni+=1
  done

  # 6. PHP-FPM pools nothing can reach. A pool is only ever addressed through
  #    its socket, so a socket named by no vhost is a pool no request can be
  #    routed to. These are not inert: pm.max_children divides the memory
  #    budget across the pool count, so dead pools quietly shrink every living
  #    site's worker allowance. One server carried 25 of them against 22 live
  #    sites and had been cut to 6 workers per site because of it.
  vhost_socks="$(mktemp)"
  cat /etc/nginx/conf.d/*.conf 2>/dev/null \
    | grep -oE "$PHP_FPM_SOCKET_GREP" | sort -u >"$vhost_socks" || true
  removed_pools=""
  # No sockets at all means nginx is unconfigured or unreadable, not that every
  # pool is dead. Same refusal as the empty live-domain list above.
  if [[ -s "$vhost_socks" ]]; then
    while IFS= read -r f; do
      [[ -f "$f" ]] || continue
      sock="$(php_pool_listen "$f" || true)"
      [[ "$sock" == /run/php/*.sock || "$sock" == "$SPANEL_PHP_RUN_ROOT"/*.sock ]] || continue
      grep -qxF "$sock" "$vhost_socks" && continue
      base="$(basename "$f")"
      echo "php-pool	${base}"
      pools+=1
      if [[ "$mode" == "clean" ]]; then
        install -d -m 0700 "${archive}/php-pools"
        cp -a "$f" "${archive}/php-pools/" 2>/dev/null || true
        rm -f "$f"
        removed_pools+="${f}"$'\n'
      fi
    done <<<"$(managed_php_pool_files)"
  fi
  rm -f "$vhost_socks"

  if [[ "$mode" == "clean" ]]; then
    sync_panel_sni_certificates >/dev/null 2>&1 || true
    if nginx -t >/dev/null 2>&1; then
      systemctl reload nginx >/dev/null 2>&1 || true
    fi
    if (( pools > 0 )); then
      # Distro masters reload; an account master whose last pool went stops.
      reload_php_pool_owners <<<"$removed_pools"
      # The pool count is an input to pm.max_children, so the sites that are
      # left are entitled to the share the dead pools were holding.
      retune_php_fpm_pools >/dev/null 2>&1 || true
    fi
    rmdir "$archive" 2>/dev/null || true
  fi
  rm -f "$live_file"
  echo "summary	certs=${certs} waf-rules=${rules} vhost-backups=${baks} manual-ssl=${manual} sni-copies=${sni} php-pools=${pools}"
  [[ "$mode" == "clean" && -d "$archive" ]] && echo "archive	${archive}"
  return 0
}

CRS_MODE_FILE=/etc/nginx/modsec/spanel-crs-mode
CRS_CONF=/etc/nginx/modsec/spanel-crs.conf
CRS_AUDIT_LOG=/var/log/nginx/spanel-modsec-audit.log

crs_rules_dir() {
  # Debian/Ubuntu ship the rules under one of these; the setup file sits either
  # beside them or one level up.
  local dir
  for dir in /usr/share/modsecurity-crs/rules /etc/modsecurity/crs/rules /usr/local/owasp-crs/rules; do
    [[ -d "$dir" ]] && { echo "$dir"; return 0; }
  done
  return 1
}

crs_setup_file() {
  local f
  for f in /etc/modsecurity/crs/crs-setup.conf /usr/share/modsecurity-crs/crs-setup.conf \
           /etc/modsecurity/crs/crs-setup.conf.example /usr/share/modsecurity-crs/crs-setup.conf.example; do
    [[ -f "$f" ]] && { echo "$f"; return 0; }
  done
  return 1
}

install_waf_crs() {
  export DEBIAN_FRONTEND=noninteractive
  if ! crs_rules_dir >/dev/null; then
    apt-get update -y || true
    apt-get install -y modsecurity-crs || deny "could not install modsecurity-crs"
  fi
  crs_rules_dir >/dev/null || deny "modsecurity-crs installed but no rules directory found"
  echo "OWASP CRS rules: $(crs_rules_dir)"
  echo "OWASP CRS setup: $(crs_setup_file || echo 'none - using built-in defaults')"
}

write_crs_conf() {
  # mode: detect | block
  #
  # CRS scores a request across many rules and acts only when the total crosses
  # a threshold, unlike SPanel's own rules which deny on a single match.
  #
  # Detect mode puts the threshold out of reach so 949110 never refuses
  # anything, and adds a SPanel rule that reads the same score and only logs.
  #
  # That extra rule is not decoration. Individual CRS rules score silently -
  # measured on a live server, a request that 949110 blocks with a 403 in block
  # mode produces exactly one log line, from 949110 itself. Raise the threshold
  # and the logging goes with it, so the obvious form of detect mode observes
  # nothing at all.
  #
  # Two things that look like alternatives and are not. SecRuleUpdateActionById
  # on 949110: libmodsecurity answers "action has not expected to be used with
  # UpdateActionByID" and the rejected directive takes the rest of the rule set
  # with it. SecRuleEngine DetectionOnly: it would also stop SPanel's own rules
  # denying on that site, trading real protection for observation.
  #
  # Where to read the results: the audit log configured below. That is where the
  # verdict lands, together with every CRS rule that contributed to the score -
  # on a live server, one SQLi probe recorded 942100, 942190 and 942360 next to
  # the SPanel line saying the score reached 15. The nginx error_log is the
  # wrong place to look, and the shared /var/log/nginx/error.log doubly so,
  # since each vhost writes to its own.
  local mode="$1" rules setup
  rules="$(crs_rules_dir)" || deny "OWASP CRS is not installed"
  setup="$(crs_setup_file || true)"
  install -d -o root -g root -m 0755 /etc/nginx/modsec
  # The audit log is opened by the nginx worker, so it has to exist and be
  # writable by it before the config is loaded.
  local nginx_user
  nginx_user="$(awk '$1=="user"{gsub(/;/,"",$2); print $2; exit}' /etc/nginx/nginx.conf 2>/dev/null)"
  [[ -n "$nginx_user" ]] || nginx_user=www-data
  touch "$CRS_AUDIT_LOG"
  chown "${nginx_user}:adm" "$CRS_AUDIT_LOG" 2>/dev/null || true
  chmod 0640 "$CRS_AUDIT_LOG"
  {
    echo "# SPanel OWASP CRS include - generated, do not edit"
    echo "# mode: ${mode}"
    # CRS needs request bodies; without them it sees only the URL and the rule
    # set is largely decorative.
    echo "SecRequestBodyAccess On"
    echo "SecRequestBodyLimit 13107200"
    echo "SecRequestBodyNoFilesLimit 131072"
    # Anything over the limit is inspected as far as it goes and then passed.
    # Rejecting instead would turn every large media upload into a 413.
    echo "SecRequestBodyLimitAction ProcessPartial"
    # Record what matched. Without this there is no audit log at all on this
    # machine: Debian's modsecurity.conf is not shipped by the nginx connector
    # package, so nothing configures one.
    echo "SecAuditEngine RelevantOnly"
    echo "SecAuditLogParts ABIJDEFHZ"
    echo "SecAuditLogType Serial"
    echo "SecAuditLog ${CRS_AUDIT_LOG}"
    [[ -n "$setup" ]] && echo "Include ${setup}"
    if [[ "$mode" == "detect" ]]; then
      echo "SecAction \"id:900110,phase:1,nolog,pass,t:none,setvar:tx.inbound_anomaly_score_threshold=1000000,setvar:tx.outbound_anomaly_score_threshold=1000000\""
    else
      echo "SecAction \"id:900110,phase:1,nolog,pass,t:none,setvar:tx.inbound_anomaly_score_threshold=5,setvar:tx.outbound_anomaly_score_threshold=4\""
    fi
    echo "SecAction \"id:900000,phase:1,nolog,pass,t:none,setvar:tx.blocking_paranoia_level=1\""
    echo "Include ${rules}/*.conf"
    if [[ "$mode" == "detect" ]]; then
      # After the rules, so the score is final. 5 and 4 are the thresholds block
      # mode uses, so this reports exactly what block mode would have refused.
      echo "SecRule TX:ANOMALY_SCORE \"@ge 5\" \"id:1009001,phase:2,pass,log,auditlog,msg:'SPanel CRS detect: inbound score %{tx.anomaly_score}, block mode would have refused this request'\""
      echo "SecRule TX:OUTBOUND_ANOMALY_SCORE \"@ge 4\" \"id:1009002,phase:4,pass,log,auditlog,msg:'SPanel CRS detect: outbound score %{tx.outbound_anomaly_score}, block mode would have refused this response'\""
    fi
  } >"${CRS_CONF}.tmp"
  install -m 0644 -o root -g root "${CRS_CONF}.tmp" "$CRS_CONF"
  rm -f "${CRS_CONF}.tmp"
  printf '%s\n' "$mode" >"$CRS_MODE_FILE"
  chmod 0644 "$CRS_MODE_FILE"
}

set_waf_crs_mode() {
  local mode="$1"
  case "$mode" in
    off|detect|block) ;;
    *) deny "usage: waf-crs-mode <off|detect|block>" ;;
  esac
  if [[ "$mode" == "off" ]]; then
    rm -f "$CRS_CONF"
    printf 'off\n' >"$CRS_MODE_FILE"
    chmod 0644 "$CRS_MODE_FILE"
    echo "OWASP CRS disabled"
    return 0
  fi
  install_waf_crs >/dev/null
  write_crs_conf "$mode"
  echo "OWASP CRS mode: ${mode}"
}

nginx_memory_pss_mb() {
  # PSS, not RSS. nginx parses the rule set in the master and the workers fork,
  # so those pages are shared: summing RSS across processes counts them once per
  # worker and overstates the cost several times over. Reading smaps_rollup for
  # another user's processes needs root, which is why this lives in the helper.
  python3 - <<'PY' 2>/dev/null || echo 0
import os, re
pss = 0
for pid in os.listdir('/proc'):
    if not pid.isdigit():
        continue
    try:
        if open(f'/proc/{pid}/comm').read().strip() != 'nginx':
            continue
        roll = open(f'/proc/{pid}/smaps_rollup').read()
    except OSError:
        continue
    m = re.search(r'^Pss:\s+(\d+) kB', roll, re.M)
    if m:
        pss += int(m.group(1))
print(pss // 1024)
PY
}

waf_crs_status() {
  local mode="off"
  [[ -f "$CRS_MODE_FILE" ]] && mode="$(tr -d '[:space:]' <"$CRS_MODE_FILE")"
  echo "mode=${mode}"
  echo "nginx_pss_mb=$(nginx_memory_pss_mb)"
  echo "ram_available_mb=$(free -m | awk '/^Mem:/{print $7}')"
  echo "ram_total_mb=$(free -m | awk '/^Mem:/{print $2}')"
  echo "installed=$(crs_rules_dir >/dev/null && echo yes || echo no)"
  echo "conf=$([[ -f "$CRS_CONF" ]] && echo yes || echo no)"
  echo "rule_files=$( { crs_rules_dir >/dev/null && ls "$(crs_rules_dir)"/*.conf 2>/dev/null | wc -l; } || echo 0)"
  echo "sites_including=$(grep -lF "Include ${CRS_CONF}" /etc/nginx/modsec/sites/*.conf 2>/dev/null | wc -l)"
}

save_waf_custom_rules() {
  install -d -o root -g root -m 0755 /etc/nginx/modsec
  write_waf_default_rules
  local tmp
  tmp="$(mktemp)"
  cat >"$tmp"
  if file_has_nul "$tmp"; then
    rm -f "$tmp"
    deny "WAF rules cannot contain NUL bytes"
  fi
  if [[ $(wc -c <"$tmp") -gt 65536 ]]; then
    rm -f "$tmp"
    deny "WAF custom rules must be 64 KB or smaller"
  fi
  # Every site's rule file includes this one, so a rule nginx refuses would
  # fail the next reload for all of them. Keep the old file until nginx has
  # accepted the new one.
  local target=/etc/nginx/modsec/spanel-custom.conf backup=""
  if [[ -f "$target" ]]; then
    backup="${target}.bak.$(date +%s)"
    cp "$target" "$backup"
  fi
  install -m 0644 -o root -g root "$tmp" "$target"
  rm -f "$tmp"
  write_modsec_main_conf
  if ! nginx -t; then
    if [[ -n "$backup" ]]; then mv -f "$backup" "$target"; else : >"$target"; fi
    deny "Nginx rejected the WAF custom rules; the previous rules are still in place"
  fi
  rm -f "$backup" 2>/dev/null || true
  systemctl reload nginx
  echo "WAF custom rules saved"
}

DA_BACKUP_DIR="/home/admin/spanel_backups/da"
DA_IMPORT_UNIT="spanel-da-import"

start_da_import() {
  # Run the import as its own systemd unit rather than a thread inside
  # spanel-api. An import of a few GB takes minutes, and anything that restarts
  # the API during one used to kill it halfway and leave a half-created
  # account behind, with the job record - an in-memory dict - gone too.
  local archive="$1" force="$2"
  [[ "$archive" != *$'\n'* ]] || deny "invalid archive path"
  case "$archive" in
    "${DA_BACKUP_DIR}"/*) : ;;
    *) deny "archive must be inside ${DA_BACKUP_DIR}" ;;
  esac
  [[ "$archive" != *".."* ]] || deny "archive path may not contain .."
  [[ -f "$archive" ]] || deny "archive not found: $archive"
  case "$force" in
    force|noforce) : ;;
    *) deny "usage: da-import-start <archive> <force|noforce>" ;;
  esac
  if systemctl is-active --quiet "${DA_IMPORT_UNIT}.service"; then
    deny "an import is already running"
  fi
  systemctl reset-failed "${DA_IMPORT_UNIT}.service" >/dev/null 2>&1 || true
  systemd-run --unit="${DA_IMPORT_UNIT}" --collect \
    --uid=spanel --gid=spanel \
    -p WorkingDirectory="${APP_DIR}/backend" \
    -p EnvironmentFile="${APP_DIR}/backend/.env" \
    -p Environment=PYTHONPATH="${APP_DIR}/backend" \
    -p Environment=HOME="${APP_DIR}" \
    -p Environment=SPANEL_USE_HELPER=true \
    -p NoNewPrivileges=false \
    "${APP_DIR}/backend/.venv/bin/python" \
    "${APP_DIR}/backend/app/services/da_import_run.py" "$archive" "$force" >/dev/null \
    || deny "could not start the import unit"
  systemctl show "${DA_IMPORT_UNIT}.service" -p InvocationID --value
}

da_import_status() {
  # The unit is the job record. Nothing is held in the API's memory, so this
  # still answers after spanel-api has been restarted.
  local unit="${DA_IMPORT_UNIT}.service"
  echo "active=$(systemctl is-active "$unit" 2>/dev/null)"
  echo "result=$(systemctl show "$unit" -p Result --value 2>/dev/null)"
  echo "exit=$(systemctl show "$unit" -p ExecMainStatus --value 2>/dev/null)"
  echo "invocation=$(systemctl show "$unit" -p InvocationID --value 2>/dev/null)"
  echo "---log---"
  journalctl -u "$unit" --no-pager -n 200 -o cat 2>/dev/null \
    | grep -viE "CryptographyDeprecation|TripleDES|^ *\"(cipher|class)\":" | tail -60
}

ensure_da_backup_dir() {
  # The panel runs as spanel, and /home/admin is root:admin 0751 - spanel can
  # traverse it but cannot create anything in it. So on a server where this
  # directory does not already exist, every DirectAdmin import call died on
  # PermissionError: the upload, and the listing behind the page itself, both
  # mkdir here. The user saw "Internal server error" and an upload that went
  # nowhere, with no trace in the log.
  #
  # Root creates it and hands the group to spanel, matching how BACKUP_ROOT is
  # owned. Existing servers already have the directory and are unaffected.
  [[ -d /home/admin ]] || install -d -m 0755 -o root -g root /home/admin
  install -d -m 0750 -o root -g spanel /home/admin/spanel_backups
  install -d -m 0770 -o root -g spanel "$DA_BACKUP_DIR"
  echo "$DA_BACKUP_DIR"
}

delete_waf_site_rules() {
  # Deleting a website used to leave /etc/nginx/modsec/sites/<domain>.conf
  # behind for ever. Harmless to serve, but it hides real state: a rule fix
  # looks half-applied because stale files still carry the old text, and the
  # directory fills with names nobody hosts.
  local domain="$1" target loaded
  require_domain "$domain"
  target="/etc/nginx/modsec/sites/${domain}.conf"
  # A vhost still pointing at this file would make `nginx -t` fail on the next
  # reload and take every site on the box down with it. Never remove a file
  # something still references, whatever the caller believes.
  #
  # Ask nginx what it actually loads rather than grepping conf.d: the directory
  # is full of .conf.bak copies nginx never reads, and matching those refused
  # every legitimate cleanup.
  if ! loaded="$(nginx -T 2>/dev/null)"; then
    deny "refusing to delete WAF rules for ${domain}: nginx config could not be read"
  fi
  if grep -qF "modsecurity_rules_file ${target}" <<<"$loaded"; then
    deny "refusing to delete WAF rules for ${domain}: a vhost still references them"
  fi
  rm -f "$target" "${target}".bak.*
  echo "Removed WAF rules for ${domain}"
}

save_waf_site_rules() {
  local domain="$1" tmp target backup=""
  require_domain "$domain"
  install -d -o root -g root -m 0755 /etc/nginx/modsec /etc/nginx/modsec/sites
  write_modsec_base_conf
  tmp="$(mktemp)"
  cat >"$tmp"
  if file_has_nul "$tmp"; then
    rm -f "$tmp"
    deny "WAF rules cannot contain NUL bytes"
  fi
  if [[ $(wc -c <"$tmp") -gt 163840 ]]; then
    rm -f "$tmp"
    deny "WAF site rules must be 160 KB or smaller"
  fi
  target="/etc/nginx/modsec/sites/${domain}.conf"
  # The site file includes the global custom rules; Include on a missing file
  # fails nginx -t, so it must exist even when nobody has written a rule yet.
  [[ -f /etc/nginx/modsec/spanel-custom.conf ]] || install -m 0644 -o root -g root /dev/null /etc/nginx/modsec/spanel-custom.conf
  if [[ -f "$target" ]]; then
    backup="${target}.bak.$(date +%s)"
    cp "$target" "$backup"
  fi
  install -m 0644 -o root -g root "$tmp" "$target"
  rm -f "$tmp"
  if ! nginx -t; then
    if [[ -n "$backup" && -f "$backup" ]]; then
      mv -f "$backup" "$target"
    else
      rm -f "$target"
    fi
    deny "Nginx rejected WAF site rules"
  fi
  rm -f "$backup" 2>/dev/null || true
  systemctl reload nginx
  echo "WAF site rules saved: ${domain}"
}

install_waf_engine() {
  export DEBIAN_FRONTEND=noninteractive
  if ! pkg_installed libnginx-mod-http-modsecurity; then
    apt-get update --allow-releaseinfo-change
    apt-get install -y libnginx-mod-http-modsecurity modsecurity-crs libmodsecurity3 || \
      apt-get install -y libnginx-mod-http-modsecurity libmodsecurity3
  fi
  install -d -o root -g root -m 0755 /etc/nginx/modsec /etc/nginx/modsec/sites
  write_waf_default_rules
  touch /etc/nginx/modsec/spanel-custom.conf
  if [[ -f /etc/modsecurity/modsecurity.conf-recommended && ! -f /etc/modsecurity/modsecurity.conf ]]; then
    cp /etc/modsecurity/modsecurity.conf-recommended /etc/modsecurity/modsecurity.conf
  fi
  if [[ -f /etc/modsecurity/modsecurity.conf ]]; then
    sed -i -E 's/^SecRuleEngine .*/SecRuleEngine On/' /etc/modsecurity/modsecurity.conf
  fi
  if [[ -f /usr/share/nginx/modules-available/mod-http-modsecurity.conf ]]; then
    install -d /etc/nginx/modules-enabled
    ln -sfn /usr/share/nginx/modules-available/mod-http-modsecurity.conf /etc/nginx/modules-enabled/50-mod-http-modsecurity.conf
  fi
  write_modsec_main_conf
  write_http_flood_nginx_conf
  nginx -t
  systemctl reload nginx
  echo "WAF engine installed with SPanel lightweight WordPress/Laravel/PHP rules."
}

# clamd ships limits sized for mail attachments, not for a hosting panel's
# file manager. Two of them decide whether an upload is really examined:
#
#   StreamMaxLength - a hard refusal. An INSTREAM body over this is rejected
#                     mid-send, which the panel sees as a broken pipe.
#   MaxFileSize     - not a refusal at all. clamd answers OK on a larger file
#                     without reading it, so "clean" and "never looked" are
#                     indistinguishable from the outside. Padding a payload
#                     past the default 25 MB walked it straight through.
#   MaxScanSize     - total bytes examined per file, archives expanded. Left
#                     at twice MaxFileSize so a large archive is not silently
#                     truncated halfway.
#
# 256 MB covers the uploads a hosting customer actually makes - plugin bundles,
# theme archives, site backups - while staying well inside the clamd memory
# ceiling the memory guard sets (2048 MB floor).
CLAMD_CONF="/etc/clamav/clamd.conf"
CLAMD_MAX_FILE_SIZE="256M"
CLAMD_MAX_SCAN_SIZE="512M"

tune_clamd_limits() {
  [[ -f "$CLAMD_CONF" ]] || { echo "clamd.conf is not present; nothing to tune"; return 0; }

  local changed=0 key value
  for pair in "MaxFileSize ${CLAMD_MAX_FILE_SIZE}"               "MaxScanSize ${CLAMD_MAX_SCAN_SIZE}"               "StreamMaxLength ${CLAMD_MAX_FILE_SIZE}"; do
    key="${pair%% *}"
    value="${pair##* }"
    if grep -qE "^[[:space:]]*${key}[[:space:]]" "$CLAMD_CONF"; then
      # Already the value we want? Leave the file alone so an update does not
      # restart clamd for nothing.
      if grep -qE "^[[:space:]]*${key}[[:space:]]+${value}[[:space:]]*$" "$CLAMD_CONF"; then
        continue
      fi
      sed -i -E "s|^[[:space:]]*${key}[[:space:]].*$|${key} ${value}|" "$CLAMD_CONF"
    else
      printf '%s %s
' "$key" "$value" >>"$CLAMD_CONF"
    fi
    changed=1
  done

  if [[ "$changed" -eq 1 ]]; then
    if systemctl is-active --quiet clamav-daemon 2>/dev/null; then
      systemctl restart clamav-daemon || echo "WARNING: clamav-daemon did not restart" >&2
    fi
    echo "clamd limits set to MaxFileSize ${CLAMD_MAX_FILE_SIZE}, MaxScanSize ${CLAMD_MAX_SCAN_SIZE}, StreamMaxLength ${CLAMD_MAX_FILE_SIZE}"
  else
    echo "clamd limits already set"
  fi
}

# The engine is `clamscan` plus the signature database. The resident daemon is
# a separate decision, below.
#
# maldet - which is what every scheduled, on-demand and real-time scan actually
# runs - calls `clamscan` with both signature sets on the command line. It
# never opens clamd's socket. Installing the daemon alongside it therefore buys
# nothing and costs a second resident copy of the same ~1 GB of signatures.
#
# That cost was not theoretical. On a live 8 GB server, 16 OOM kills in 7 days:
#
#   Sep 18 22:13  clamd     1.56 GB       Sep 21 01:40  clamd     1.14 GB
#   Sep 18 22:18  clamscan  0.87 GB       Sep 21 01:40  clamscan  1.05 GB
#   Sep 18 22:18  nginx     0.69 GB       Sep 21 15:50  clamd     2.17 GB
#                                         Sep 21 15:50  nginx     0.71 GB
#
# clamd in 9 of the 16, twice taking nginx down with it. clamscan stayed at
# ~1 GB throughout - the size of the signature set, and unavoidable.
install_clamav_engine() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update --allow-releaseinfo-change
  if ! pkg_installed clamav; then
    apt-get install -y clamav
  fi
  # Triggers an initial signature database refresh in the background.
  freshclam >/dev/null 2>&1 || true
  echo "ClamAV engine installed (clamscan + signatures; no resident daemon)."
}

# The resident daemon, installed only when something scans one file at a time.
#
# Loading the signature set costs ~23 s and ~1 GB per invocation, measured. A
# batch scan pays that once for thousands of files and does not care. Scanning
# each upload pays it per file, which is why that feature - and only that
# feature - wants clamd.
install_clamav_daemon() {
  export DEBIAN_FRONTEND=noninteractive
  if ! pkg_installed clamav; then
    install_clamav_engine
  fi
  if ! pkg_installed clamav-daemon; then
    apt-get update --allow-releaseinfo-change
    apt-get install -y clamav-daemon || deny "could not install clamav-daemon"
  fi
  install -d -o clamav -g clamav -m 0755 /run/clamav 2>/dev/null || true
  systemctl enable --now clamav-daemon
  tune_clamd_limits
  # Give the new daemon the same ceiling the guard sets for an existing one,
  # so it cannot grow to the 2.17 GB that took nginx down twice.
  [[ -x /usr/local/sbin/spanel-memory-guard ]] && /usr/local/sbin/spanel-memory-guard >/dev/null 2>&1
  echo "clamav-daemon installed and running."
}

remove_clamav_daemon() {
  # Removing the daemon must never take the engine with it: maldet needs
  # `clamscan` and /var/lib/clamav, and a machine without maldet falls back to
  # `clamdscan`, which needs the daemon. Both are checked before anything is
  # removed.
  if ! pkg_installed clamav-daemon; then
    echo "clamav-daemon is not installed; nothing to remove."
    return 0
  fi
  if [[ ! -x "$MALDET_BIN" ]]; then
    deny "maldet is not installed, so scans would fall back to clamdscan and need this daemon"
  fi

  export DEBIAN_FRONTEND=noninteractive
  # Ask apt what it would do first. If the answer includes the engine, stop.
  local plan
  plan="$(apt-get -s remove clamav-daemon 2>/dev/null | grep -E '^Remv ' || true)"
  if grep -qE '^Remv (clamav|clamav-base|clamav-freshclam) ' <<<"$plan"; then
    deny "removing clamav-daemon here would also remove the scan engine; left alone"
  fi

  systemctl disable --now clamav-daemon 2>/dev/null || true
  systemctl disable --now clamav-daemon.socket 2>/dev/null || true
  apt-get remove -y clamav-daemon || deny "could not remove clamav-daemon"

  # Deliberately no autoremove: it decides for itself what else is unused and
  # has no way of knowing maldet calls clamscan.
  command -v clamscan >/dev/null 2>&1 || echo "WARNING: clamscan is gone; scanning will not work" >&2
  echo "clamav-daemon removed. The engine (clamscan + signatures) is untouched."
}

# --- fail2ban ---------------------------------------------------------------
#
# Optional. SSH on a public address collects a few hundred password attempts a
# day without anyone noticing: a live customer server showed 679 failed root
# logins in 24 hours, from six sources, with nothing in the way.
#
# Two things decide whether this works at all.
#
# **The ban action is pinned.** Debian's fail2ban picks one by probing the
# machine, and a server that once had ufw keeps 18 ufw-* chains in iptables
# long after the package is gone. Picked on that evidence, every ban would be
# handed to a firewall that is not running: the log says "Ban 1.2.3.4" and the
# address carries on connecting. iptables-multiport is named here so nothing
# is inferred.
#
# **The install proves a ban lands.** Writing config and starting a service
# says nothing about whether bans reach the kernel. install_fail2ban bans a
# documentation address, looks for it in iptables, and unbans it. If it is not
# there the install fails loudly rather than leaving a service that looks
# healthy and protects nothing.
#
# SPanel's own chain returns rather than accepts, so packets it allows fall
# through to fail2ban's chain further down INPUT. The two do not fight.
FAIL2BAN_JAIL_LOCAL=/etc/fail2ban/jail.local
# TEST-NET-1 (RFC 5737). Never routed, so banning it can inconvenience nobody.
FAIL2BAN_PROBE_IP=192.0.2.1

fail2ban_ssh_unit() {
  # Debian and Ubuntu call it ssh.service; the fail2ban default filter matches
  # _SYSTEMD_UNIT=sshd.service and therefore sees nothing on either. Both names
  # resolve - sshd.service is an alias - so ask systemd for the canonical Id,
  # which is what the journal records.
  local name
  for name in ssh sshd; do
    if [[ "$(systemctl show -p LoadState --value "$name" 2>/dev/null)" == "loaded" ]]; then
      systemctl show -p Id --value "$name" 2>/dev/null
      return 0
    fi
  done
  echo "sshd.service"
}

fail2ban_local_addresses() {
  # Never ban the machine itself. Loopback plus every global address it holds.
  {
    echo "127.0.0.1/8"
    echo "::1"
    ip -o addr show scope global 2>/dev/null | awk '{print $4}'
  } | sort -u | tr '\n' ' '
}

write_fail2ban_jail() {
  local ignore unit
  ignore="$(fail2ban_local_addresses)"
  unit="$(fail2ban_ssh_unit)"
  install -d -m 0755 /etc/fail2ban
  cat >"$FAIL2BAN_JAIL_LOCAL" <<EOF
# Managed by SPanel. Edits here are replaced when the addon is reinstalled.
[DEFAULT]
# Pinned, not detected. See the note in spanel-helper about ufw leftovers.
banaction = iptables-multiport
banaction_allports = iptables-allports
backend = systemd
ignoreip = ${ignore}
bantime = 1h
# An hour, not fail2ban's usual ten minutes. The stock window is sized for a
# fast brute force - fifty passwords a minute - and what actually knocks on a
# hosting server paces itself under that. Measured on a live panel: two hosts
# each reached exactly five attempts, spaced eight to fifteen minutes apart,
# and neither ever filled a ten-minute window:
#
#   36.140.150.164   01:54  02:02  02:12  02:22  02:33
#   185.240.215.110  01:54  02:01  02:16  02:32  02:32
#
# At an hour both are caught. The cost is that an admin who fumbles a password
# five times in an hour is locked out for one - and the jail only blocks port
# 22, so the panel is still there to release them.
findtime = 1h
maxretry = 5

[sshd]
enabled = true
port = ssh
# Named, not defaulted. The stock filter looks for sshd.service and Debian
# calls the unit ssh.service. The plus sign is a disjunction in fail2ban,
# so the _COMM branch keeps the filter sighted even where the unit name is
# wrong. No backticks in here: this heredoc is unquoted, so prose in it runs.
journalmatch = _SYSTEMD_UNIT=${unit} + _COMM=sshd
# A host that keeps coming back stays out for longer each time, up to a week.
bantime.increment = true
bantime.maxtime = 1w
EOF
  chmod 0644 "$FAIL2BAN_JAIL_LOCAL"
}

fail2ban_ban_reaches_the_kernel() {
  # Ban an unroutable address, look for it, unban it. The point is not whether
  # fail2ban says it banned something - it always does - but whether the rule
  # is in iptables afterwards.
  local found=1 saved
  fail2ban-client set sshd banip "$FAIL2BAN_PROBE_IP" >/dev/null 2>&1 || true
  sleep 1
  # Captured, not piped into grep -q: that exits on the first match, the
  # producer takes SIGPIPE, and under `set -o pipefail` the pipeline reports
  # 141 - a match read as a miss. Which here would fail an install that
  # actually worked.
  saved="$(iptables-save 2>/dev/null || true)"
  if [[ "$saved" == *"$FAIL2BAN_PROBE_IP"* ]]; then
    found=0
  fi
  fail2ban-client set sshd unbanip "$FAIL2BAN_PROBE_IP" >/dev/null 2>&1 || true
  return "$found"
}

fail2ban_filter_sees_the_journal() {
  # The other half of "is this actually protecting anything": a jail can ban on
  # command and still never notice an attack, if its journalmatch selects
  # nothing.
  #
  # The match has to be evaluated the way fail2ban evaluates it, and that is
  # not how journalctl reads the same string. In fail2ban a `+` between match
  # groups is a DISJUNCTION - "unit is this OR the process is called sshd" -
  # while `journalctl A B` ANDs them. Checking with journalctl's meaning
  # reports a blind filter on a machine whose filter sees everything, which is
  # how this function first got written and how it was wrong.
  #
  # So: either branch producing entries is enough. sshd on a live machine logs
  # constantly - accepted logins, disconnects, refusals - so both branches
  # silent means the match is wrong, not that the server is quiet.
  local unit
  unit="$(fail2ban_ssh_unit)"
  [[ -n "$(journalctl _SYSTEMD_UNIT="$unit" --since '24 hours ago' -q --no-pager 2>/dev/null | head -c 1)" ]] && return 0
  [[ -n "$(journalctl _COMM=sshd --since '24 hours ago' -q --no-pager 2>/dev/null | head -c 1)" ]] && return 0
  return 1
}

install_fail2ban() {
  export DEBIAN_FRONTEND=noninteractive
  if ! pkg_installed fail2ban; then
    apt-get update --allow-releaseinfo-change
    apt-get install -y fail2ban || deny "could not install fail2ban"
  fi
  write_fail2ban_jail
  systemctl enable fail2ban >/dev/null 2>&1 || true
  systemctl restart fail2ban || deny "fail2ban did not start"

  # Give it a moment to read the jail and open its socket.
  local waited=0
  while [[ $waited -lt 30 ]]; do
    fail2ban-client ping >/dev/null 2>&1 && break
    sleep 1
    waited=$((waited + 1))
  done
  fail2ban-client ping >/dev/null 2>&1 || deny "fail2ban is running but not answering its socket"

  fail2ban_ban_reaches_the_kernel     || deny "fail2ban is running but a test ban never reached iptables - check banaction in $FAIL2BAN_JAIL_LOCAL"

  fail2ban_filter_sees_the_journal     || deny "fail2ban is running but its sshd jail matches a journal unit with no entries ($(fail2ban_ssh_unit)) - it would never see a failed login"

  echo "fail2ban installed: sshd jail active, a test ban reached iptables, and the filter is reading $(fail2ban_ssh_unit)."
}

remove_fail2ban() {
  # Stop protecting, keep everything: the jail config, the ban database and
  # the package all stay, so turning the addon back on is instant and no
  # history is lost.
  if ! pkg_installed fail2ban; then
    echo "fail2ban is not installed; nothing to stop."
    return 0
  fi
  systemctl disable --now fail2ban >/dev/null 2>&1 || true
  echo "fail2ban stopped and disabled. Config and ban history kept."
}

# ---- DNS Manager (PowerDNS, optional) -----------------------------------------
# The panel edits zones through PowerDNS's HTTP API on loopback, with the key in
# PDNS_API_KEY_FILE (root:spanel 0640): no call to this helper per record.
# Turning the addon off stops the service and closes port 53; the zone database,
# the key and the package all stay, so turning it on again serves every zone.

dns_listen_addresses() {
  # Loopback for the panel's own checks, then every global address. Never the
  # wildcard: systemd-resolved holds 127.0.0.53:53 and a 0.0.0.0 bind collides.
  local list="127.0.0.1" addr
  while read -r addr; do
    [[ -n "$addr" ]] && list+=", ${addr}"
  done < <(ip -o addr show scope global 2>/dev/null | awk '{ split($4, a, "/"); print a[1] }')
  printf '%s\n' "$list"
}

dns_port_53_users() {
  # Whatever listens on port 53 apart from PowerDNS and resolved's stub.
  local listing
  listing="$( { ss -H -lnup 'sport = :53'; ss -H -lntp 'sport = :53'; } 2>/dev/null || true)"
  printf '%s\n' "$listing" | grep -o 'users:(("[^"]*"' | sed 's/^users:(("//; s/"$//' \
    | grep -v -x -e 'systemd-resolve' -e 'pdns_server' | sort -u | tr '\n' ' ' || true
}

dns_api_answers() {
  curl -fsS -m 3 -H "X-API-Key: $1" "http://127.0.0.1:${PDNS_API_PORT}/api/v1/servers/localhost" >/dev/null 2>&1
}

# Zone transfers to secondary nameservers: off unless the panel turned them
# on (dns-transfers). On, PowerDNS sends NOTIFY for primary zones and answers
# AXFR only to the addresses a zone's ALLOW-AXFR-FROM lists, loopback else.
PDNS_TRANSFERS_STATE="/etc/powerdns/spanel-transfers"

dns_transfer_lines() {
  if [[ "$1" == "on" ]]; then
    printf '%s\n' "# Zone transfers: only to each zone's ALLOW-AXFR-FROM (set in the panel)." \
      "disable-axfr=no" "allow-axfr-ips=127.0.0.1/32,::1/128" "primary=yes"
  else
    printf '%s\n' "# Authoritative only: no zone transfers." "disable-axfr=yes"
  fi
}

dns_transfers() {
  # dns-transfers <on|off>: rewrite the transfer lines of pdns.conf and
  # restart PowerDNS; the previous file comes back if it does not start.
  local state="$1" backup key
  [[ "$state" == "on" || "$state" == "off" ]] || deny "usage: dns-transfers <on|off>"
  [[ -f "$PDNS_CONF" ]] || deny "DNS Manager is not installed"
  backup="$(mktemp)"
  cp -a "$PDNS_CONF" "$backup"
  python3 - "$PDNS_CONF" <<'PY' || { rm -f "$backup"; deny "could not rewrite ${PDNS_CONF}"; }
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    lines = handle.read().splitlines()
drop = ("disable-axfr=", "allow-axfr-ips=", "primary=", "master=", "# Zone transfers:", "# Authoritative only:")
kept = [line for line in lines if not line.startswith(drop)]
with open(path, "w", encoding="utf-8") as handle:
    handle.write("\n".join(kept) + "\n")
PY
  dns_transfer_lines "$state" >>"$PDNS_CONF"
  if ! systemctl restart pdns 2>/dev/null; then
    cp -a "$backup" "$PDNS_CONF"
    rm -f "$backup"
    systemctl restart pdns 2>/dev/null || true
    deny "PowerDNS did not start with zone transfers ${state}; the previous configuration is back"
  fi
  rm -f "$backup"
  printf '%s\n' "$state" >"$PDNS_TRANSFERS_STATE"
  key="$(tr -d '[:space:]' <"$PDNS_API_KEY_FILE" 2>/dev/null || true)"
  local waited=0
  while (( waited < 20 )); do
    dns_api_answers "$key" && break
    sleep 1
    waited=$((waited + 1))
  done
  echo "Zone transfers ${state}."
}

install_dns() {
  export DEBIAN_FRONTEND=noninteractive
  local others
  others="$(dns_port_53_users)"
  if [[ -n "${others// /}" ]]; then
    deny "port 53 is already in use by: ${others% } - stop it before installing DNS Manager"
  fi
  # PowerDNS already running on a setup SPanel did not write is somebody's
  # DNS: rewriting its configuration would take their zones off the air.
  if systemctl is-active --quiet pdns 2>/dev/null && [[ ! -f "$PDNS_CONF" ]]; then
    deny "PowerDNS is already running here with its own configuration - DNS Manager will not take it over"
  fi

  if ! pkg_installed pdns-server || ! pkg_installed pdns-backend-sqlite3; then
    # Keep apt from starting PowerDNS on its stock settings while it installs:
    # those bind 0.0.0.0:53, collide with systemd-resolved, and the failed start
    # in the package script fails the whole install.
    local policy="/usr/sbin/policy-rc.d" saved="" status=0
    if [[ -e "$policy" ]]; then
      saved="${policy}.spanel-dns"
      mv -f "$policy" "$saved"
    fi
    printf '#!/bin/sh\nexit 101\n' >"$policy"
    chmod 0755 "$policy"
    # apt's output goes to a log, so a failure below reads as one sentence
    # in the panel instead of a screen of "Restarting services...".
    apt-get update --allow-releaseinfo-change >>"$PDNS_INSTALL_LOG" 2>&1 || true
    # Without the recommended pdns-backend-bind: its pdns.d/bind.conf carries
    # bind-* settings that are fatal to a server not loading that backend.
    apt-get install -y --no-install-recommends pdns-server pdns-backend-sqlite3 >>"$PDNS_INSTALL_LOG" 2>&1 || status=$?
    rm -f "$policy"
    if [[ -n "$saved" ]]; then
      mv -f "$saved" "$policy"
    fi
    if [[ $status -ne 0 ]]; then
      deny "could not install PowerDNS (apt-get exited ${status}; see ${PDNS_INSTALL_LOG})"
    fi
  fi
  # Installed some other way (by hand, or by an earlier version of this addon
  # that let apt add its recommendations): set its bind.conf aside. PowerDNS
  # only reads *.conf there, so the renamed file is kept but inert.
  if [[ -f /etc/powerdns/pdns.d/bind.conf ]]; then
    mv -f /etc/powerdns/pdns.d/bind.conf /etc/powerdns/pdns.d/bind.conf.spanel-disabled
  fi

  # The zone database: made once, never replaced.
  install -d -o pdns -g pdns -m 0750 /var/lib/powerdns
  if [[ ! -s "$PDNS_DB" ]]; then
    [[ -f "$PDNS_SCHEMA" ]] || deny "the PowerDNS SQLite schema is missing: ${PDNS_SCHEMA}"
    python3 - "$PDNS_DB" "$PDNS_SCHEMA" <<'PY' || deny "could not create the PowerDNS database"
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
with open(sys.argv[2], encoding="utf-8") as schema:
    connection.executescript(schema.read())
connection.commit()
connection.close()
PY
  fi
  chown pdns:pdns "$PDNS_DB"
  chmod 0640 "$PDNS_DB"

  install -d -o root -g spanel -m 0750 /etc/spanel
  if [[ ! -s "$PDNS_API_KEY_FILE" ]]; then
    # Made 0640 while still empty, then filled: the key is never readable by
    # anyone else, even for a moment.
    install -m 0640 -o root -g spanel /dev/null "$PDNS_API_KEY_FILE"
    openssl rand -hex 32 >"$PDNS_API_KEY_FILE"
  fi
  chown root:spanel "$PDNS_API_KEY_FILE"
  chmod 0640 "$PDNS_API_KEY_FILE"

  local key listen
  key="$(tr -d '[:space:]' <"$PDNS_API_KEY_FILE")"
  listen="$(dns_listen_addresses)"
  install -d -m 0755 /etc/powerdns/pdns.d
  cat >"$PDNS_CONF" <<EOF
# Managed by SPanel (DNS Manager addon); rewritten each time the addon is installed.
launch=gsqlite3
gsqlite3-database=${PDNS_DB}
# Loopback and this server's own addresses. One that has gone after an IP
# change is skipped rather than stopping the whole server.
local-address=${listen}
local-address-nonexist-fail=no
local-port=53
# The panel edits zones through this API, on loopback only.
api=yes
api-key=${key}
webserver=yes
webserver-address=127.0.0.1
webserver-port=${PDNS_API_PORT}
webserver-allow-from=127.0.0.1/32
version-string=anonymous
$(dns_transfer_lines "$(cat "$PDNS_TRANSFERS_STATE" 2>/dev/null || echo off)")
EOF
  chown root:pdns "$PDNS_CONF"
  chmod 0640 "$PDNS_CONF"

  systemctl enable pdns >/dev/null 2>&1 || true
  systemctl restart pdns 2>/dev/null || deny "PowerDNS did not start - journalctl -u pdns -n 50 says why"
  local waited=0
  while (( waited < 20 )); do
    dns_api_answers "$key" && break
    sleep 1
    waited=$((waited + 1))
  done
  dns_api_answers "$key" || deny "PowerDNS is running but its API does not answer on 127.0.0.1:${PDNS_API_PORT}"

  # REFUSED is an answer: it proves PowerDNS itself holds port 53, not merely
  # that the process is alive.
  local reply
  reply="$(dig +time=2 +tries=1 @127.0.0.1 spanel-probe.invalid SOA 2>/dev/null || true)"
  [[ "$reply" == *"status: REFUSED"* ]] || deny "PowerDNS is running but does not answer DNS queries on port 53"

  install -d -m 0750 "$FIREWALL_ADDON_PORTS_DIR"
  printf '53 tcp\n53 udp\n' >"$FIREWALL_ADDON_PORTS_DIR/dns.ports"
  firewall_apply >/dev/null
  echo "DNS Manager installed: PowerDNS answers on ${listen}, and port 53 is open over TCP and UDP."
}

remove_dns() {
  systemctl disable --now pdns >/dev/null 2>&1 || true
  rm -f "$FIREWALL_ADDON_PORTS_DIR/dns.ports"
  firewall_apply >/dev/null
  echo "DNS Manager stopped: PowerDNS is off and port 53 is closed. The zones are kept in ${PDNS_DB}."
}

dns_status() {
  local installed=no running=no api=no port_open=no listen=""
  if pkg_installed pdns-server; then installed=yes; fi
  if systemctl is-active --quiet pdns 2>/dev/null; then running=yes; fi
  if [[ -s "$PDNS_API_KEY_FILE" ]] && dns_api_answers "$(tr -d '[:space:]' <"$PDNS_API_KEY_FILE")"; then api=yes; fi
  if [[ -f "$FIREWALL_ADDON_PORTS_DIR/dns.ports" ]]; then port_open=yes; fi
  if [[ -f "$PDNS_CONF" ]]; then
    listen="$(awk -F= '$1 == "local-address" { print $2; exit }' "$PDNS_CONF")"
  fi
  echo "installed=${installed}"
  echo "running=${running}"
  echo "api=${api}"
  echo "port_open=${port_open}"
  echo "listen=${listen}"
}

# ---- Email (Exim + Dovecot + webmail, optional) --------------------------------
# The same design as OPanel's Email addon. Exim receives and sends, Dovecot
# stores mail and serves IMAP/POP3, Rspamd (with an Unbound resolver of its
# own) filters spam, and the SPanel webmail runs behind nginx on port 2096. A
# mailbox lives in its owner's home, /home/<user>/mail/<domain>/<name>, so it
# counts toward the account's disk space and goes into its backups.
#
# The panel owns the state: mail-configure takes the server-wide settings and
# the relays, mail-sync the domains, mailboxes, forwarders and relay routes.
# Nothing here is edited by hand.
#
# Turning the addon off stops the three services and closes the ports. The
# mail, the mailbox list, the DKIM keys and the packages all stay.

MAIL_EXIM_CONF="/etc/exim4/exim4.conf"
MAIL_EXIM_DIR="/etc/exim4/spanel"
MAIL_DOVECOT_CONF="/etc/dovecot/dovecot.conf"
MAIL_DOVECOT_DIR="/etc/dovecot/spanel"
MAIL_TLS_DIR="/etc/spanel-mail-tls"
MAIL_TLS_SCRIPT="/usr/local/sbin/spanel-mail-tls"
MAIL_INSTALL_LOG="/var/log/spanel-mail-install.log"
MAIL_MARKER="# Managed by SPanel (Email addon)."
WEBMAIL_HOME="/opt/SPanel-webmail"
WEBMAIL_REPO="https://github.com/shostwp-byte/spanel.git"
WEBMAIL_ENV="/etc/SPanel-webmail.env"
WEBMAIL_UNIT="/etc/systemd/system/spanel-webmail.service"
WEBMAIL_PORT="8096"
WEBMAIL_PUBLIC_PORT="2096"
WEBMAIL_NGINX="/etc/nginx/conf.d/00-spanel-webmail.conf"
WEBMAIL_SSO_KEY_FILE="/etc/spanel/webmail-sso.key"
WEBMAIL_MASTER_USER="spanel-webmail"
# Rspamd, the spam filter. The panel reads its history through the controller
# on loopback with the key in MAIL_RSPAMD_KEY_FILE (root:spanel 0640).
MAIL_RSPAMD_LOCAL="/etc/rspamd/local.d"
MAIL_RSPAMD_OVERRIDE="/etc/rspamd/override.d"
MAIL_RSPAMD_KEY_FILE="/etc/spanel/rspamd-controller.key"
MAIL_RSPAMD_REDIS_DB="14"
MAIL_SIEVE_DIR="/etc/dovecot/spanel-sieve"
# What mail-configure last saved (relay passwords apart), root only.
MAIL_SETTINGS_FILE="/etc/exim4/spanel/settings.json"
MAIL_UNBOUND_PORT="5335"
MAIL_UNBOUND_CONF="/etc/unbound/unbound.conf.d/spanel-mail.conf"
# Renders and checks what the Email commands below write (installer/files/
# spanel_mailtool.py, installed root-owned by install.sh and update.sh).
MAIL_TOOL_DIR="/usr/local/lib/spanel"
MAIL_TOOL="${MAIL_TOOL_DIR}/spanel_mailtool.py"
# A mailbox's autoresponder, written by mail-sync into the mailbox's home.
MAIL_VACATION_SCRIPT="spanel-vacation.sieve"
# Messages moved in and out of Junk wait here for Rspamd to learn them.
MAIL_LEARN_SPOOL="/var/spool/spanel-learn"
MAIL_QUOTA_WARNING="${MAIL_TOOL_DIR}/mail-quota-warning"

mail_port_taken() {
  # "<port> (<program>)" for each mail port some other program already holds.
  local port owner
  for port in 25 465 587 143 993 110 995 "$WEBMAIL_PUBLIC_PORT" "$WEBMAIL_PORT"; do
    owner="$(ss -H -lntp "sport = :${port}" 2>/dev/null | grep -o 'users:(("[^"]*"' | head -n1 | sed 's/^users:(("//; s/"$//' || true)"
    [[ -n "$owner" ]] || continue
    case "${port}:${owner}" in
      25:exim4|465:exim4|587:exim4|143:dovecot|993:dovecot|110:dovecot|995:dovecot) continue ;;
      "${WEBMAIL_PUBLIC_PORT}:nginx") continue ;;
      "${WEBMAIL_PORT}:python"*) systemctl is-active --quiet spanel-webmail && continue ;;
    esac
    printf '%s (%s) ' "$port" "$owner"
  done
}

mail_install_packages() {
  local missing=() pkg
  for pkg in exim4-daemon-heavy dovecot-core dovecot-imapd dovecot-pop3d dovecot-lmtpd dovecot-sieve rspamd unbound dns-root-data git python3-venv; do
    pkg_installed "$pkg" || missing+=("$pkg")
  done
  [[ ${#missing[@]} -gt 0 ]] || return 0
  export DEBIAN_FRONTEND=noninteractive
  # Keep apt from starting Exim, Dovecot, Rspamd and Unbound on their stock
  # settings while they install: this addon writes their configuration before
  # any runs, and a stock Unbound would take port 53 from PowerDNS.
  local policy="/usr/sbin/policy-rc.d" saved="" status=0
  if [[ -e "$policy" ]]; then
    saved="${policy}.spanel-mail"
    mv -f "$policy" "$saved"
  fi
  printf '#!/bin/sh\nexit 101\n' >"$policy"
  chmod 0755 "$policy"
  apt-get update --allow-releaseinfo-change >>"$MAIL_INSTALL_LOG" 2>&1 || true
  apt-get install -y --no-install-recommends "${missing[@]}" >>"$MAIL_INSTALL_LOG" 2>&1 || status=$?
  rm -f "$policy"
  if [[ -n "$saved" ]]; then
    mv -f "$saved" "$policy"
  fi
  [[ $status -eq 0 ]] || deny "could not install Exim, Dovecot and Rspamd (apt-get exited ${status}; see ${MAIL_INSTALL_LOG})"
}

mail_write_tls_script() {
  cat >"$MAIL_TLS_SCRIPT" <<'SCRIPT'
#!/usr/bin/env bash
# Installed by SPanel (Email addon). Copies the panel's certificate to where
# Exim, Dovecot and the webmail port read it, then the certificates of the
# mail domains' own hosts (mail.<domain>, answered by SNI), and reloads what
# needs it when anything changed. Also certbot's deploy hook for those.
set -euo pipefail
env_file="/opt/spanel/backend/.env"
tls_dir="/etc/spanel-mail-tls"
value() { sed -nE "s/^$1=//p" "$env_file" 2>/dev/null | tail -n1 | tr -d '"'; }
reload=no
status=0
cert="$(value PANEL_SSL_CERT)"
key="$(value PANEL_SSL_KEY)"
if [[ "$cert" != /* || "$key" != /* || ! -f "$cert" || ! -f "$key" ]]; then
  echo "spanel-mail-tls: the panel has no certificate configured" >&2
  status=1
elif cmp -s "$cert" "$tls_dir/fullchain.pem" && cmp -s "$key" "$tls_dir/privkey.pem"; then
  :
else
  # The panel replaces the certificate and then the key. Caught between the
  # two, the pair does not match: wait for the key rather than break TLS.
  cert_pub="$(openssl x509 -in "$cert" -noout -pubkey 2>/dev/null || true)"
  key_pub="$(openssl pkey -in "$key" -pubout 2>/dev/null || true)"
  if [[ -z "$cert_pub" || "$cert_pub" != "$key_pub" ]]; then
    echo "spanel-mail-tls: certificate and key do not match yet; leaving the current pair" >&2
  else
    install -d -o root -g Debian-exim -m 0750 "$tls_dir"
    install -m 0640 -o root -g Debian-exim "$cert" "$tls_dir/fullchain.pem.new"
    install -m 0640 -o root -g Debian-exim "$key" "$tls_dir/privkey.pem.new"
    mv -f "$tls_dir/fullchain.pem.new" "$tls_dir/fullchain.pem"
    mv -f "$tls_dir/privkey.pem.new" "$tls_dir/privkey.pem"
    echo "spanel-mail-tls: now using ${cert}"
    reload=yes
  fi
fi
# The mail domains' own certificates (mail-hosts-apply registers them).
if [[ -f /etc/exim4/spanel/tls_hosts && -f /usr/local/lib/spanel/spanel_mailtool.py ]]; then
  sni="$(python3 -I /usr/local/lib/spanel/spanel_mailtool.py sni-apply /etc/exim4/spanel/tls_hosts \
    /etc/letsencrypt/live "$tls_dir" /etc/exim4/spanel /etc/dovecot/spanel/sni.conf || echo failed)"
  if [[ "$sni" == "changed" ]]; then reload=yes; fi
fi
# Exim reads the files for each connection; Dovecot and nginx need a reload.
if [[ "$reload" == "yes" ]]; then
  if systemctl is-active --quiet dovecot; then systemctl reload dovecot || true; fi
  if systemctl is-active --quiet nginx && nginx -t >/dev/null 2>&1; then systemctl reload nginx || true; fi
fi
exit "$status"
SCRIPT
  chmod 0755 "$MAIL_TLS_SCRIPT"

  cat >/etc/systemd/system/spanel-mail-tls.service <<UNIT
[Unit]
Description=SPanel: give Exim, Dovecot and the webmail the panel's certificate
# The panel's .env changes for many reasons; each is a cheap no-op here, and a
# burst of them must not trip the start limit and stop the watching.
StartLimitIntervalSec=0

[Service]
Type=oneshot
ExecStart=${MAIL_TLS_SCRIPT}
UNIT
  cat >/etc/systemd/system/spanel-mail-tls.path <<'UNIT'
[Unit]
Description=SPanel: watch the panel's certificate for the mail services

[Path]
PathChanged=/etc/spanel/panel-fullchain.pem
PathChanged=/etc/spanel/panel-privkey.pem
PathChanged=/etc/spanel/panel-selfsigned-fullchain.pem
PathChanged=/opt/spanel/backend/.env
Unit=spanel-mail-tls.service

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
}

mail_write_settings_default() {
  # What mail-configure last saved, without the relay passwords. Made on the
  # first install; mail_write_exim_conf reads it, so a reinstall keeps the
  # administrator's limits, relays and spam settings.
  local hostname="$1"
  install -d -o root -g Debian-exim -m 0750 "$MAIL_EXIM_DIR"
  python3 - "$MAIL_SETTINGS_FILE" "$hostname" <<'PY'
import json
import os
import sys

path, hostname = sys.argv[1:3]
try:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
if not isinstance(data, dict):
    data = {}
data["hostname"] = hostname
fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2, sort_keys=True)
os.replace(path + ".new", path)
PY
}

mail_write_exim_conf() {
  # The whole of Exim's configuration, from the settings mail-configure saved:
  # rate limits, message size, the spam filter, the relays. Which domains,
  # mailboxes, forwarders and routes exist is in the maps mail-sync writes.
  local tmp
  tmp="$(mktemp "${MAIL_EXIM_CONF}.XXXXXX")"
  [[ -f "$MAIL_TOOL" ]] || deny "${MAIL_TOOL} is missing - run the SPanel update again"
  python3 - "$MAIL_SETTINGS_FILE" "$MAIL_EXIM_DIR" "$MAIL_TLS_DIR" "$tmp" "$MAIL_TOOL_DIR" <<'PY' || { rm -f "$tmp"; deny "could not write the Exim configuration"; }
import json
import sys

settings_path, M, tls_dir, out_path, tool_dir = sys.argv[1:6]
try:
    with open(settings_path, encoding="utf-8") as handle:
        raw = json.load(handle)
except (OSError, ValueError):
    raw = {}
raw = raw if isinstance(raw, dict) else {}


def whole(key, low, high, fallback):
    try:
        value = int(raw.get(key, fallback))
    except (TypeError, ValueError):
        return fallback
    return value if low <= value <= high else fallback


host = str(raw.get("hostname") or "localhost.localdomain")
auth_rate = whole("auth_rate_per_hour", 0, 100000, 300)
local_rate = whole("local_rate_per_hour", 0, 100000, 300)
domain_rate = whole("domain_rate_per_hour", 0, 100000, 500)
max_mb = whole("max_message_mb", 1, 200, 50)
spam = raw.get("spam_enabled", True) is not False

# The domain a message is sent for: the one it is DKIM-signed for, else the
# envelope sender's. The relay is chosen by it.
SEND = "${if def:acl_m_dkim{$acl_m_dkim}{${lc:$sender_address_domain}}}"
# dkim_domains maps a domain to its signing key's file (dkim/<value>.pem),
# dkim_selectors to the selector that key is published under: the two change
# together when a DKIM rotation switches keys (mail-sync).
DKIM = r"""  dkim_domain = ${lookup{$acl_m_dkim}lsearch{@@M@@/dkim_domains}{$acl_m_dkim}{}}
  dkim_selector = ${lookup{$acl_m_dkim}lsearch{@@M@@/dkim_selectors}{$value}{spanel}}
  dkim_private_key = ${lookup{$acl_m_dkim}lsearch{@@M@@/dkim_domains}{@@M@@/dkim/$value.pem}{0}}
  dkim_canon = relaxed
  dkim_strict = false
"""
# A relay's login: relay_auth names the relay for the host Exim is talking to,
# relay_credentials holds its user name and password base64-encoded, so no
# character in them needs escaping. The client_send lists use ";" because ":"
# is inside the expansion; a literal ^ is doubled for PLAIN.
CRED = r"${sg{${base64d:${lookup{${lookup{$host}lsearch{@@M@@/relay_auth}{$value}{none}}.@@PART@@}lsearch{@@M@@/relay_credentials}}}}{\N\^\N}{^^}}"
USER, PASS = CRED.replace("@@PART@@", "user"), CRED.replace("@@PART@@", "pass")

conf = r"""# Managed by SPanel (Email addon). Rewritten whenever the mail settings change:
# change them in the panel, not here. Domains, mailboxes, forwarders and relay
# routes are the maps in @@M@@, written by mail-sync.

primary_hostname = @@HOST@@
qualify_domain = @@HOST@@
smtp_banner = $smtp_active_hostname ESMTP

domainlist local_domains = @ : localhost : localhost.localdomain
domainlist virtual_domains = lsearch;@@M@@/domains
domainlist relay_to_domains =
hostlist   relay_from_hosts = <; 127.0.0.1 ; ::1

acl_smtp_mail = acl_check_mail
acl_smtp_rcpt = acl_check_rcpt
acl_smtp_data = acl_check_data
acl_not_smtp = acl_check_not_smtp

spamd_address = 127.0.0.1 11333 variant=rspamd

daemon_smtp_ports = 25 : 465 : 587
tls_on_connect_ports = 465
tls_advertise_hosts = *
# A client that asks for mail.<domain> by SNI gets that domain's own
# certificate (tls_certs and tls_keys, written by spanel-mail-tls); anyone
# else the panel's. A lookup, never a path built from what the client sent.
tls_certificate = ${lookup{${lc:$tls_in_sni}}lsearch{@@M@@/tls_certs}{$value}{@@TLS@@/fullchain.pem}}
tls_privatekey = ${lookup{${lc:$tls_in_sni}}lsearch{@@M@@/tls_keys}{$value}{@@TLS@@/privkey.pem}}

never_users = root
host_lookup =
message_size_limit = @@MAXMB@@M
smtp_accept_max = 100
smtp_accept_max_per_host = 20
ignore_bounce_errors_after = 2d
timeout_frozen_after = 7d
# PHP's mail() may set its own envelope sender (-f). A message is only ever
# signed for a domain of the calling account - see acl_check_not_smtp.
untrusted_set_sender = *
local_from_check = false
log_file_path = /var/log/exim4/%slog
log_selector = +smtp_protocol_error +smtp_syntax_error +tls_cipher +tls_sni +incoming_port +outgoing_port
spool_directory = /var/spool/exim4
keep_environment =
add_environment = <; PATH=/bin:/usr/bin

begin acl

acl_check_mail:
  deny    message = A HELO or EHLO greeting is required
          condition = ${if def:sender_helo_name {no}{yes}}
  # A signed-in mailbox sends only as an address at one of its account's
  # domains, so one customer cannot send - or have signed - mail as another.
  deny    message = This mailbox cannot send as <$sender_address>
          authenticated = *
          !condition = ${if inlisti{${lc:$sender_address_domain}}{${lookup{${lc:$authenticated_id}}lsearch{@@M@@/senders}{$value}{}}}}
  accept

acl_check_rcpt:
  accept  hosts = :
          control = dkim_disable_verify

  deny    message = Restricted characters in address
          domains = +local_domains : +virtual_domains
          local_parts = ^[.] : ^.*[@%!/|]

  deny    message = Restricted characters in address
          domains = ! +local_domains : ! +virtual_domains
          local_parts = ^[./|] : ^.*[@%!] : ^.*/\\.\\./
@@AUTHRATE@@
  accept  authenticated = *
          control = submission/sender_retain
          control = dkim_disable_verify

  # The submission ports are for signed-in mail apps only.
  deny    message = Authentication required
          condition = ${if or{{eq{$received_port}{587}}{eq{$received_port}{465}}}}

  # Anyone else - loopback included - only reaches mailboxes here.
  require message = Relay not permitted
          domains = +local_domains : +virtual_domains

  require verify = recipient

  accept

acl_check_data:
@@HOLD@@
  # The From: address must be on one of the account's domains too; a message
  # that passes is DKIM-signed for that domain on its way out.
  deny    message = The From address is not one of your mail domains
          authenticated = *
          !condition = ${if inlisti{${lc:${domain:$h_from:}}}{${lookup{${lc:$authenticated_id}}lsearch{@@M@@/senders}{$value}{}}}}
  accept  authenticated = *
          set acl_m_dkim = ${lc:${domain:$h_from:}}
@@SPAM@@
  accept

acl_check_not_smtp:
  # Mail from a website's PHP (sendmail): limited per Linux user, and signed
  # only for a domain the calling account owns.
@@LOCALRATE@@
  warn    condition = ${if inlisti{${lc:${domain:$h_from:}}}{${lookup{$sender_ident}lsearch{@@M@@/local_senders}{$value}{}}}}
          set acl_m_dkim = ${lc:${domain:$h_from:}}
  accept

begin routers

# Outgoing relays (smarthosts). relay_routes maps a sending domain to a relay,
# "*" to the server's default relay and "direct" to delivery without one;
# relay_hosts and relay_transports say where each relay is and how to reach it.
relay:
  driver = manualroute
  domains = ! +local_domains : ! +virtual_domains
  condition = ${if !eq{${lookup{@@SEND@@}lsearch*{@@M@@/relay_routes}{$value}{direct}}}{direct}}
  address_data = ${lookup{@@SEND@@}lsearch*{@@M@@/relay_routes}}
  route_data = ${lookup{$address_data}lsearch{@@M@@/relay_hosts}}
  transport = ${lookup{$address_data}lsearch{@@M@@/relay_transports}{$value}{relay_smtp}}
  host_find_failed = defer
  same_domain_copy_routing = yes
  no_more

dnslookup:
  driver = dnslookup
  domains = ! +local_domains : ! +virtual_domains
  transport = remote_smtp
  ignore_target_hosts = <; 0.0.0.0 ; 127.0.0.0/8 ; ::1
  no_more

# Forwarders. An address that is also a mailbox lists itself among the
# targets; Exim hands an address redirected to itself on to the next router,
# which delivers the copy.
virtual_forward:
  driver = redirect
  domains = +virtual_domains
  data = ${lookup{${lc:$local_part@$domain}}lsearch{@@M@@/aliases}}
  forbid_file
  forbid_pipe
  allow_fail
  allow_defer

virtual_mailbox:
  driver = accept
  domains = +virtual_domains
  local_part_suffix = +*
  local_part_suffix_optional
  condition = ${lookup{${lc:$local_part}@${lc:$domain}}lsearch{@@M@@/mailboxes}{yes}{no}}
  transport = dovecot_lmtp

virtual_catchall:
  driver = redirect
  domains = +virtual_domains
  data = ${lookup{${lc:$domain}}lsearch{@@M@@/catchall}}
  forbid_file
  forbid_pipe

virtual_unknown:
  driver = redirect
  domains = +virtual_domains
  allow_fail
  data = :fail: No such mailbox here

system_aliases:
  driver = redirect
  domains = +local_domains
  allow_fail
  allow_defer
  data = ${lookup{$local_part}lsearch{/etc/aliases}}
  forbid_file
  forbid_pipe

root_mail:
  driver = accept
  domains = +local_domains
  local_parts = root
  transport = root_spool

local_user:
  driver = accept
  domains = +local_domains
  check_local_user
  transport = local_spool
  cannot_route_message = Unknown user

begin transports

remote_smtp:
  driver = smtp
  helo_data = @@HOST@@
@@DKIM@@
# One transport for each way of reaching a relay. A relay with a login is in
# relay_auth by its host, which is what makes Exim sign in to it - and only
# ever over TLS whose certificate checks out, unless the relay is "no TLS".
relay_smtp:
  driver = smtp
  helo_data = @@HOST@@
  hosts_require_tls = *
  tls_verify_certificates = system
  tls_verify_hosts = *
  tls_sni = $host
  hosts_require_auth = ${lookup{$host}lsearch{@@M@@/relay_auth}{*}{}}
@@DKIM@@
relay_smtps:
  driver = smtp
  helo_data = @@HOST@@
  protocol = smtps
  hosts_require_tls = *
  tls_verify_certificates = system
  tls_verify_hosts = *
  tls_sni = $host
  hosts_require_auth = ${lookup{$host}lsearch{@@M@@/relay_auth}{*}{}}
@@DKIM@@
relay_smtp_plain:
  driver = smtp
  helo_data = @@HOST@@
  hosts_require_auth = ${lookup{$host}lsearch{@@M@@/relay_auth}{*}{}}
@@DKIM@@
dovecot_lmtp:
  driver = lmtp
  socket = /run/dovecot/lmtp-exim
  batch_max = 200

local_spool:
  driver = appendfile
  file = /var/mail/$local_part_data
  delivery_date_add
  envelope_to_add
  return_path_add
  group = mail
  mode = 0660
  mode_fail_narrower = false

root_spool:
  driver = appendfile
  file = /var/mail/root
  user = mail
  group = mail
  mode = 0600
  delivery_date_add
  envelope_to_add
  return_path_add

begin retry

*   *   F,2h,15m; G,16h,1h,1.5; F,4d,6h

begin rewrite

begin authenticators

# Dovecot checks mailbox passwords. Only offered over TLS, or on loopback where
# the webmail talks to it. A webmail single sign-on logs in as "mailbox*master";
# what counts as the sender is the mailbox.
dovecot_plain:
  driver = dovecot
  public_name = PLAIN
  server_socket = /run/dovecot/auth-client
  server_set_id = ${sg{$auth1}{\N\*.*$\N}{}}
  server_advertise_condition = ${if or{{def:tls_in_cipher}{match_ip{$sender_host_address}{<; 127.0.0.1 ; ::1}}}}

dovecot_login:
  driver = dovecot
  public_name = LOGIN
  server_socket = /run/dovecot/auth-client
  server_set_id = ${sg{$auth1}{\N\*.*$\N}{}}
  server_advertise_condition = ${if or{{def:tls_in_cipher}{match_ip{$sender_host_address}{<; 127.0.0.1 ; ::1}}}}

# Signing in to a relay: PLAIN first, LOGIN for relays that offer only that.
relay_plain:
  driver = plaintext
  public_name = PLAIN
  client_send = <; ^@@USER@@^@@PASS@@

relay_login:
  driver = plaintext
  public_name = LOGIN
  client_send = <; ; @@USER@@ ; @@PASS@@
"""

# The sending limits, refusing or (auto-hold) freezing: the mail tool's.
sys.path.insert(0, tool_dir)
import spanel_mailtool  # noqa: E402

rate_acls = spanel_mailtool.render_rate_acls(auth_rate, local_rate, domain_rate,
                                             raw.get("auto_hold_on_rate_exceed", True) is not False)
auth_block, local_block, hold_block = rate_acls["rcpt"], rate_acls["not_smtp"], rate_acls["data"]
spam_block = ""
if spam:
    spam_block = r"""
  # Rspamd: SPF, DKIM and DMARC checks, DNS blocklists, greylisting and Bayes.
  accept  condition = ${if >{$message_size}{20M}}
  warn    remove_header = X-Spam-Status : X-Spam-Score : X-Spam-Flag : X-Spam-Bar : X-Spam-Report
  # ":true" - if Rspamd does not answer, mail is delivered unscanned.
  warn    spam = nobody:true
  defer   message = Greylisted, please try again later
          condition = ${if eq{$spam_action}{greylist}}
  defer   message = Please try again later
          condition = ${if eq{$spam_action}{soft reject}}
  deny    message = This message was rejected as spam (score $spam_score)
          condition = ${if eq{$spam_action}{reject}}
  warn    condition = ${if def:spam_score}
          add_header = X-Spam-Score: $spam_score
  warn    condition = ${if or{{eq{$spam_action}{add header}}{eq{$spam_action}{rewrite subject}}}}
          add_header = X-Spam-Status: Yes, score=$spam_score
"""

conf = (conf.replace("@@AUTHRATE@@", auth_block)
            .replace("@@HOLD@@", hold_block)
            .replace("@@LOCALRATE@@", local_block)
            .replace("@@SPAM@@", spam_block)
            .replace("@@DKIM@@", DKIM)
            .replace("@@USER@@", USER)
            .replace("@@PASS@@", PASS)
            .replace("@@SEND@@", SEND)
            .replace("@@HOST@@", host)
            .replace("@@MAXMB@@", str(max_mb))
            .replace("@@TLS@@", tls_dir)
            .replace("@@M@@", M))
with open(out_path, "w", encoding="utf-8") as handle:
    handle.write(conf)
PY
  chmod 0644 "$tmp"
  if ! exim4 -C "$tmp" -bV >/dev/null 2>>"$MAIL_INSTALL_LOG"; then
    rm -f "$tmp"
    deny "the Exim configuration SPanel wrote does not load (see ${MAIL_INSTALL_LOG})"
  fi
  mv -f "$tmp" "$MAIL_EXIM_CONF"
  awk -F' = ' '$1 == "primary_hostname" { print $2; exit }' "$MAIL_EXIM_CONF" >/etc/mailname
}

mail_ensure_maps() {
  # Every map Exim and Dovecot read exists, even empty: a missing one is a
  # lookup error, and a lookup error defers mail.
  install -d -o root -g Debian-exim -m 0750 "$MAIL_EXIM_DIR" "$MAIL_EXIM_DIR/dkim"
  install -d -o root -g dovecot -m 0750 "$MAIL_DOVECOT_DIR"
  local list
  for list in domains mailboxes aliases catchall senders local_senders dkim_domains dkim_selectors \
      relay_routes relay_hosts relay_transports relay_auth relay_credentials ratelimits \
      tls_hosts tls_certs tls_keys; do
    [[ -f "$MAIL_EXIM_DIR/$list" ]] || install -m 0640 -o root -g Debian-exim /dev/null "$MAIL_EXIM_DIR/$list"
  done
  [[ -f /etc/exim4/spanel_ratelimits ]] || install -m 0640 -o root -g Debian-exim /dev/null /etc/exim4/spanel_ratelimits
  for list in users denied; do
    [[ -f "$MAIL_DOVECOT_DIR/$list" ]] || install -m 0640 -o root -g dovecot /dev/null "$MAIL_DOVECOT_DIR/$list"
  done
  # The single-relay files of the first version of this addon.
  rm -f "$MAIL_EXIM_DIR"/relay-router "$MAIL_EXIM_DIR"/relay-transport "$MAIL_EXIM_DIR"/relay-auth \
    "$MAIL_EXIM_DIR"/relay-credentials "$MAIL_EXIM_DIR"/relay-info "$MAIL_EXIM_DIR"/spam-acl
}

mail_write_unbound_conf() {
  # Rspamd's own recursive resolver. Spamhaus and the URI blocklists answer
  # "blocked" to queries from public resolvers (8.8.8.8, 1.1.1.1 - what a VPS
  # usually has), which silently turns the DNS blocklists off. It listens on
  # a port of its own, so the system's resolver and PowerDNS are left alone.
  install -d -o root -g root -m 0755 /etc/unbound/unbound.conf.d
  cat >"$MAIL_UNBOUND_CONF" <<EOF
${MAIL_MARKER} A resolver for Rspamd only.
server:
  interface: 127.0.0.1
  port: ${MAIL_UNBOUND_PORT}
  do-ip6: no
  access-control: 127.0.0.0/8 allow
  access-control: 0.0.0.0/0 refuse
  hide-identity: yes
  hide-version: yes
  prefetch: yes
EOF
  chmod 0644 "$MAIL_UNBOUND_CONF"
  # The package's hook registers 127.0.0.1 as the system's resolver where
  # resolvconf is installed; this resolver is Rspamd's alone.
  systemctl disable --now unbound-resolvconf.service >/dev/null 2>&1 || true
  systemctl mask unbound-resolvconf.service >/dev/null 2>&1 || true
  # The DNSSEC trust anchor the package's config points at is made on the
  # service's first start - which the install held back, so a stock Unbound
  # could not take port 53 from PowerDNS. Made here instead.
  # The package's own start hook copies it from dns-root-data.
  if [[ ! -s /var/lib/unbound/root.key && -x /usr/libexec/unbound-helper ]]; then
    /usr/libexec/unbound-helper root_trust_anchor_update >>"$MAIL_INSTALL_LOG" 2>&1 || true
  fi
  if command -v unbound-checkconf >/dev/null 2>&1; then
    unbound-checkconf >>"$MAIL_INSTALL_LOG" 2>&1 || deny "the Unbound configuration SPanel wrote does not load (see ${MAIL_INSTALL_LOG})"
  fi
}

mail_unbound_answers() {
  # A real recursion, not a root server's address: those come from Unbound's
  # built-in hints and prove nothing about reaching the internet's DNS.
  # An address in the answer, not just output: on a timeout dig +short prints
  # ";; communications error ..." to stdout.
  local answer
  answer="$(dig +short +time=4 +tries=1 -p "$MAIL_UNBOUND_PORT" @127.0.0.1 example.com A 2>/dev/null || true)"
  [[ "$answer" =~ (^|$'\n')[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}($|$'\n') ]]
}

mail_write_rspamd_resolver() {
  # "local": Rspamd asks Unbound. "system": the provider blocks DNS to anyone
  # but its own resolvers, so Rspamd asks those - and Spamhaus refuses them,
  # which the panel says.
  if [[ "$1" == "local" ]]; then
    cat >"$MAIL_RSPAMD_LOCAL/options.inc" <<EOF
${MAIL_MARKER} DNS blocklists need a resolver of their own.
dns {
  nameserver = ["127.0.0.1:${MAIL_UNBOUND_PORT}"];
}
EOF
  else
    cat >"$MAIL_RSPAMD_LOCAL/options.inc" <<EOF
${MAIL_MARKER} This server cannot reach DNS servers directly, so Rspamd
# uses the system's resolver; DNS blocklists such as Spamhaus refuse it.
EOF
  fi
  chmod 0644 "$MAIL_RSPAMD_LOCAL/options.inc"
  printf '%s\n' "$1" >"$MAIL_EXIM_DIR/resolver"
}

mail_write_dovecot_conf() {
  local tmp listen="*"
  # "::" on a machine without IPv6 stops Dovecot from starting at all.
  if [[ -s /proc/net/if_inet6 ]]; then listen="*, ::"; fi
  if [[ -f "$MAIL_DOVECOT_CONF" && ! -f "${MAIL_DOVECOT_CONF}.spanel-orig" ]] \
     && ! grep -qF "$MAIL_MARKER" "$MAIL_DOVECOT_CONF"; then
    cp -a "$MAIL_DOVECOT_CONF" "${MAIL_DOVECOT_CONF}.spanel-orig"
  fi
  tmp="$(mktemp "${MAIL_DOVECOT_CONF}.XXXXXX")"
  cat >"$tmp" <<EOF
${MAIL_MARKER} Rewritten each time the addon is installed:
# edits here are lost. The mailboxes are in ${MAIL_DOVECOT_DIR}/users.
# The distribution's own file is kept as dovecot.conf.spanel-orig.

protocols = imap pop3 lmtp
listen = ${listen}
login_greeting = Ready.

# Passwords only over TLS; loopback (the webmail) counts as secure.
disable_plaintext_auth = yes
auth_mechanisms = plain login
auth_username_format = %Lu
# The webmail's single sign-on: "mailbox*${WEBMAIL_MASTER_USER}" with the master password.
auth_master_user_separator = *

ssl = required
ssl_cert = <${MAIL_TLS_DIR}/fullchain.pem
ssl_key = <${MAIL_TLS_DIR}/privkey.pem
ssl_dh = </usr/share/dovecot/dh.pem
ssl_min_protocol = TLSv1.2
ssl_prefer_server_ciphers = yes

# Each mailbox is stored as the Linux user that owns it, in that user's home.
mail_location = maildir:~/Maildir
first_valid_uid = 1000
mail_plugins = \$mail_plugins quota

namespace inbox {
  inbox = yes
  mailbox Drafts {
    auto = subscribe
    special_use = \\Drafts
  }
  mailbox Sent {
    auto = subscribe
    special_use = \\Sent
  }
  mailbox Junk {
    auto = subscribe
    special_use = \\Junk
  }
  mailbox Trash {
    auto = subscribe
    special_use = \\Trash
  }
}

passdb {
  driver = passwd-file
  args = ${MAIL_DOVECOT_DIR}/masters
  master = yes
  pass = yes
}
# Suspended mailboxes: refused before any password is checked. It comes after
# the master passdb, whose pass = yes looks the mailbox up here as well, so a
# single sign-on cannot open a suspended mailbox either.
passdb {
  driver = passwd-file
  args = username_format=%Lu ${MAIL_DOVECOT_DIR}/denied
  deny = yes
}
passdb {
  driver = passwd-file
  args = scheme=SHA512-CRYPT ${MAIL_DOVECOT_DIR}/users
}
userdb {
  driver = passwd-file
  args = ${MAIL_DOVECOT_DIR}/users
}

plugin {
  quota = count:Mailbox
  quota_vsizes = yes
  quota_grace = 10%%
  # Mail the spam filter marked goes to Junk, before anything of the mailbox's own.
  sieve_before = ${MAIL_SIEVE_DIR}/spam-to-junk.sieve
  # Then the mailbox's autoresponder, which mail-sync writes into its home
  # from the panel's list; a mailbox without one has no such file.
  sieve_before2 = ~/${MAIL_VACATION_SCRIPT}

  # Spam training: moved into Junk is spam, moved out of it (not to Trash) ham.
  sieve_plugins = sieve_imapsieve sieve_extprograms
  sieve_global_extensions = +vnd.dovecot.pipe +vnd.dovecot.environment
  sieve_pipe_bin_dir = ${MAIL_SIEVE_DIR}/bin
  imapsieve_mailbox1_name = Junk
  imapsieve_mailbox1_causes = COPY APPEND
  imapsieve_mailbox1_before = file:${MAIL_SIEVE_DIR}/learn-spam.sieve
  imapsieve_mailbox2_name = *
  imapsieve_mailbox2_from = Junk
  imapsieve_mailbox2_causes = COPY
  imapsieve_mailbox2_before = file:${MAIL_SIEVE_DIR}/learn-ham.sieve

  # The mailbox is told when it is filling up.
  quota_warning = storage=95%% quota-warning 95 %u
  quota_warning2 = storage=80%% quota-warning 80 %u
}

protocol imap {
  mail_plugins = \$mail_plugins imap_quota imap_sieve
  mail_max_userip_connections = 20
}

# Run as user dovecot. Every mailbox's processes run as its owner, so the
# socket is open to all; the script checks what it is asked and warns a
# mailbox at most once a day per level.
service quota-warning {
  executable = script ${MAIL_QUOTA_WARNING}
  user = dovecot
  unix_listener quota-warning {
    mode = 0666
  }
}
protocol lmtp {
  postmaster_address = postmaster@%d
  mail_plugins = \$mail_plugins sieve
}
# A full mailbox is refused at RCPT, so the sender hears it at once.
lmtp_rcpt_check_quota = yes

service auth {
  unix_listener auth-client {
    mode = 0660
    group = Debian-exim
  }
}
service lmtp {
  unix_listener lmtp-exim {
    mode = 0660
    group = Debian-exim
  }
}
service imap-login {
  inet_listener imap {
    port = 143
  }
  inet_listener imaps {
    port = 993
    ssl = yes
  }
}
service pop3-login {
  inet_listener pop3 {
    port = 110
  }
  inet_listener pop3s {
    port = 995
    ssl = yes
  }
}

# The certificate of each mail domain's own host (mail.<domain>), by SNI;
# written by spanel-mail-tls.
!include_try ${MAIL_DOVECOT_DIR}/sni.conf
EOF
  chmod 0644 "$tmp"
  if ! doveconf -c "$tmp" -n >/dev/null 2>>"$MAIL_INSTALL_LOG"; then
    rm -f "$tmp"
    deny "the Dovecot configuration SPanel wrote does not load (see ${MAIL_INSTALL_LOG})"
  fi
  mv -f "$tmp" "$MAIL_DOVECOT_CONF"
}

mail_env_value() {
  [[ -f "$WEBMAIL_ENV" ]] || return 0
  sed -nE "s/^$1=//p" "$WEBMAIL_ENV" | tail -n1
}

mail_install_webmail() {
  local ref auth_secret sso_secret master_password
  ref="$(env_get WEBMAIL_REF)"
  ref="${ref:-main}"
  [[ "$ref" =~ ^[A-Za-z0-9._/-]{1,100}$ ]] || deny "invalid WEBMAIL_REF: $ref"
  if [[ -d "$WEBMAIL_HOME" && ! -f "$WEBMAIL_HOME/.spanel" ]]; then
    deny "${WEBMAIL_HOME} already exists and was not set up by SPanel - the Email addon will not take it over"
  fi
  id -u SPanel-webmail >/dev/null 2>&1 \
    || useradd --system --home-dir "$WEBMAIL_HOME" --no-create-home --shell /usr/sbin/nologin --user-group SPanel-webmail
  install -d -o root -g root -m 0755 "$WEBMAIL_HOME"
  touch "$WEBMAIL_HOME/.spanel"
  if [[ -d "$WEBMAIL_HOME/src/.git" ]]; then
    git -C "$WEBMAIL_HOME/src" fetch --quiet --depth 1 origin "$ref" >>"$MAIL_INSTALL_LOG" 2>&1 \
      || deny "could not fetch the webmail from ${WEBMAIL_REPO} (see ${MAIL_INSTALL_LOG})"
    git -C "$WEBMAIL_HOME/src" reset --quiet --hard FETCH_HEAD >>"$MAIL_INSTALL_LOG" 2>&1 \
      || deny "could not update the webmail checkout (see ${MAIL_INSTALL_LOG})"
  else
    rm -rf "$WEBMAIL_HOME/src"
    git clone --quiet --depth 1 --branch "$ref" "$WEBMAIL_REPO" "$WEBMAIL_HOME/src" >>"$MAIL_INSTALL_LOG" 2>&1 \
      || deny "could not download the webmail from ${WEBMAIL_REPO} (see ${MAIL_INSTALL_LOG})"
  fi
  if [[ ! -x "$WEBMAIL_HOME/venv/bin/python" ]]; then
    python3 -m venv "$WEBMAIL_HOME/venv" >>"$MAIL_INSTALL_LOG" 2>&1 \
      || deny "could not create the webmail's Python environment (see ${MAIL_INSTALL_LOG})"
  fi
  "$WEBMAIL_HOME/venv/bin/pip" install --quiet --disable-pip-version-check \
    -r "$WEBMAIL_HOME/src/backend/requirements.txt" >>"$MAIL_INSTALL_LOG" 2>&1 \
    || deny "could not install the webmail's Python packages (see ${MAIL_INSTALL_LOG})"
  install -d -o SPanel-webmail -g SPanel-webmail -m 0750 "$WEBMAIL_HOME/data"

  # Secrets survive a reinstall: a new AUTH_SECRET would sign everybody out,
  # and a new master password must reach Dovecot and the webmail together.
  auth_secret="$(mail_env_value AUTH_SECRET)"
  [[ -n "$auth_secret" ]] || auth_secret="$(openssl rand -hex 32)"
  sso_secret="$(mail_env_value SSO_SECRET)"
  [[ -n "$sso_secret" ]] || sso_secret="$(openssl rand -hex 32)"
  master_password="$(mail_env_value SSO_MASTER_PASSWORD)"
  [[ -n "$master_password" ]] || master_password="$(openssl rand -hex 24)"

  # Each file is made with its final mode while still empty, then filled.
  install -m 0640 -o root -g SPanel-webmail /dev/null "${WEBMAIL_ENV}.new"
  cat >"${WEBMAIL_ENV}.new" <<EOF
${MAIL_MARKER} Rewritten each time the addon is installed.
AUTH_SECRET=${auth_secret}
# Dovecot and Exim on this server, over loopback.
IMAP_HOST=127.0.0.1
IMAP_PORT=143
IMAP_SECURE=false
SMTP_HOST=127.0.0.1
SMTP_PORT=587
SMTP_SECURE=false
ENABLE_CADDY_AUTOMATION=false
# Single sign-on from SPanel.
SSO_SECRET=${sso_secret}
SSO_MASTER_USER=${WEBMAIL_MASTER_USER}
SSO_MASTER_PASSWORD=${master_password}
SSO_MASTER_SEPARATOR=*
EOF
  mv -f "${WEBMAIL_ENV}.new" "$WEBMAIL_ENV"

  install -d -o root -g spanel -m 0750 /etc/spanel
  install -m 0640 -o root -g spanel /dev/null "${WEBMAIL_SSO_KEY_FILE}.new"
  printf '%s\n' "$sso_secret" >"${WEBMAIL_SSO_KEY_FILE}.new"
  mv -f "${WEBMAIL_SSO_KEY_FILE}.new" "$WEBMAIL_SSO_KEY_FILE"

  install -m 0640 -o root -g dovecot /dev/null "${MAIL_DOVECOT_DIR}/masters.new"
  printf '%s:{SHA512-CRYPT}%s\n' "$WEBMAIL_MASTER_USER" \
    "$(printf '%s\n' "$master_password" | openssl passwd -6 -stdin)" >"${MAIL_DOVECOT_DIR}/masters.new"
  mv -f "${MAIL_DOVECOT_DIR}/masters.new" "${MAIL_DOVECOT_DIR}/masters"

  cat >"$WEBMAIL_UNIT" <<UNIT
[Unit]
Description=SPanel Webmail (Email addon)
After=network-online.target dovecot.service exim4.service
Wants=network-online.target

[Service]
Type=simple
User=SPanel-webmail
Group=SPanel-webmail
WorkingDirectory=${WEBMAIL_HOME}/src/backend
EnvironmentFile=${WEBMAIL_ENV}
Environment=DATA_DIR=${WEBMAIL_HOME}/data
Environment=HOST=127.0.0.1
Environment=PORT=${WEBMAIL_PORT}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=${WEBMAIL_HOME}/venv/bin/python ${WEBMAIL_HOME}/src/backend/main.py
Restart=always
RestartSec=5
PrivateTmp=true
ProtectSystem=full
NoNewPrivileges=true
ReadWritePaths=${WEBMAIL_HOME}/data

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
}

mail_webmail_proxy_block() {
  # The location blocks every webmail server block shares.
  cat <<'NGINX'
    client_max_body_size 50m;

    # The webmail's own administration (domains, S3, a separate password) is
    # not for customers: SPanel manages all of it.
    location ^~ /admin { return 404; }
    location ^~ /api/admin/ { return 404; }

    location / {
        proxy_pass http://127.0.0.1:8096;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        # Overwritten, not appended: the webmail reads the last address.
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
NGINX
}

mail_write_webmail_nginx() {
  local conf off
  # Server blocks set aside when the addon was last turned off come back.
  for off in /etc/nginx/conf.d/00-spanel-webmail*.conf.off; do
    [[ -f "$off" ]] || continue
    mv -f "$off" "${off%.off}"
  done
  conf="$WEBMAIL_NGINX"
  {
    printf '%s Rewritten each time the addon is installed.\n' "$MAIL_MARKER"
    printf '# The webmail on the panel'"'"'s own name and certificate.\n'
    printf 'server {\n'
    printf '    listen %s ssl;\n' "$WEBMAIL_PUBLIC_PORT"
    printf '    server_name _;\n'
    printf '    ssl_certificate %s/fullchain.pem;\n' "$MAIL_TLS_DIR"
    printf '    ssl_certificate_key %s/privkey.pem;\n' "$MAIL_TLS_DIR"
    printf '    ssl_protocols TLSv1.2 TLSv1.3;\n'
    mail_webmail_proxy_block
    printf '}\n'
  } >"$conf"
  nginx_ipv6_apply >/dev/null 2>&1 || true
  if ! nginx -t >>"$MAIL_INSTALL_LOG" 2>&1; then
    rm -f "$conf"
    deny "nginx refused the webmail's server block on port ${WEBMAIL_PUBLIC_PORT} (see ${MAIL_INSTALL_LOG})"
  fi
  systemctl reload nginx
}

mail_first_line() {
  # The greeting a TCP service on loopback sends first, or nothing.
  timeout 5 bash -c "exec 3<>/dev/tcp/127.0.0.1/$1; head -n1 <&3" 2>/dev/null | tr -d '\r' || true
}

install_mail() {
  local hostname="$1" other taken greeting waited
  require_domain "$hostname"
  for other in postfix sendmail opensmtpd; do
    if systemctl is-active --quiet "$other" 2>/dev/null; then
      deny "${other} is already running here - stop it before installing the Email addon"
    fi
  done
  taken="$(mail_port_taken)"
  if [[ -n "${taken// /}" ]]; then
    deny "these mail ports are already in use: ${taken% } - stop what holds them before installing the Email addon"
  fi

  mail_install_packages
  mail_ensure_maps
  mail_write_settings_default "$hostname"

  mail_write_tls_script
  "$MAIL_TLS_SCRIPT" >>"$MAIL_INSTALL_LOG" 2>&1 \
    || deny "the panel has no certificate the mail server could use - set up the panel's HTTPS first"
  systemctl enable --now spanel-mail-tls.path >/dev/null 2>&1 || true

  mail_write_exim_conf
  mail_write_sieve
  mail_write_quota_warning
  mail_write_dovecot_conf
  mail_write_learn_service
  mail_write_unbound_conf
  mail_write_rspamd_conf
  mail_install_webmail

  systemctl enable dovecot exim4 spanel-webmail >/dev/null 2>&1 || true
  systemctl restart dovecot 2>>"$MAIL_INSTALL_LOG" || deny "Dovecot did not start - journalctl -u dovecot -n 50 says why"
  systemctl restart exim4 2>>"$MAIL_INSTALL_LOG" || deny "Exim did not start - journalctl -u exim4 -n 50 says why"
  systemctl restart spanel-webmail 2>>"$MAIL_INSTALL_LOG" || deny "the webmail did not start - journalctl -u spanel-webmail -n 50 says why"
  mail_apply_spam_service

  # Proof, not process lists: each service has to answer.
  waited=0
  greeting=""
  while (( waited < 20 )); do
    greeting="$(mail_first_line 25)"
    [[ "$greeting" == 220* ]] && break
    sleep 1
    waited=$((waited + 1))
  done
  [[ "$greeting" == 220* ]] || deny "Exim is running but does not answer SMTP on port 25"
  greeting="$(mail_first_line 143)"
  [[ "$greeting" == "* OK"* ]] || deny "Dovecot is running but does not answer IMAP on port 143"
  [[ -S /run/dovecot/lmtp-exim ]] || deny "Dovecot is running but Exim's delivery socket /run/dovecot/lmtp-exim is missing"
  waited=0
  while (( waited < 40 )); do
    curl -fsS -m 3 "http://127.0.0.1:${WEBMAIL_PORT}/api/auth/me" >/dev/null 2>&1 && break
    sleep 1
    waited=$((waited + 1))
  done
  curl -fsS -m 3 "http://127.0.0.1:${WEBMAIL_PORT}/api/auth/me" >/dev/null 2>&1 \
    || deny "the webmail is running but does not answer on 127.0.0.1:${WEBMAIL_PORT} - journalctl -u spanel-webmail -n 50 says why"

  mail_write_webmail_nginx

  install -d -m 0750 "$FIREWALL_ADDON_PORTS_DIR"
  printf '25 tcp\n465 tcp\n587 tcp\n143 tcp\n993 tcp\n110 tcp\n995 tcp\n%s tcp\n' "$WEBMAIL_PUBLIC_PORT" \
    >"$FIREWALL_ADDON_PORTS_DIR/mail.ports"
  firewall_apply >/dev/null
  echo "Email installed: Exim answers on 25/465/587, Dovecot on 143/993/110/995, and the webmail on port ${WEBMAIL_PUBLIC_PORT}. Mail server name: ${hostname}."
}

remove_mail() {
  local conf
  systemctl disable --now rspamd >/dev/null 2>&1 || true
  systemctl disable --now unbound >/dev/null 2>&1 || true
  systemctl disable --now spanel-webmail >/dev/null 2>&1 || true
  systemctl disable --now exim4 >/dev/null 2>&1 || true
  systemctl disable --now dovecot >/dev/null 2>&1 || true
  systemctl disable --now spanel-mail-tls.path >/dev/null 2>&1 || true
  systemctl disable --now spanel-mail-learn.timer >/dev/null 2>&1 || true
  for conf in /etc/nginx/conf.d/00-spanel-webmail*.conf; do
    [[ -f "$conf" ]] || continue
    mv -f "$conf" "${conf}.off"
  done
  if nginx -t >/dev/null 2>&1; then systemctl reload nginx >/dev/null 2>&1 || true; fi
  rm -f "$FIREWALL_ADDON_PORTS_DIR/mail.ports"
  firewall_apply >/dev/null
  echo "Email stopped: Exim, Dovecot, Rspamd and the webmail are off and the mail ports are closed. Mail, mailboxes, DKIM keys and settings are kept."
}

mail_status() {
  local installed=no exim=no dovecot=no webmail=no port_open=no hostname="" rspamd=no unbound=no spam_filter=no
  if pkg_installed exim4-daemon-heavy && pkg_installed dovecot-imapd && [[ -x "$WEBMAIL_HOME/venv/bin/python" ]]; then
    installed=yes
  fi
  if systemctl is-active --quiet exim4 2>/dev/null; then exim=yes; fi
  if systemctl is-active --quiet dovecot 2>/dev/null; then dovecot=yes; fi
  if systemctl is-active --quiet spanel-webmail 2>/dev/null; then webmail=yes; fi
  if systemctl is-active --quiet rspamd 2>/dev/null; then rspamd=yes; fi
  if systemctl is-active --quiet unbound 2>/dev/null; then unbound=yes; fi
  if [[ -f "$FIREWALL_ADDON_PORTS_DIR/mail.ports" ]]; then port_open=yes; fi
  if [[ -f "$MAIL_EXIM_CONF" ]]; then
    hostname="$(awk -F' = ' '$1 == "primary_hostname" { print $2; exit }' "$MAIL_EXIM_CONF")"
    if grep -q '^  warn    spam = nobody:true' "$MAIL_EXIM_CONF"; then spam_filter=yes; fi
  fi
  echo "installed=${installed}"
  echo "exim=${exim}"
  echo "dovecot=${dovecot}"
  echo "webmail=${webmail}"
  echo "port_open=${port_open}"
  echo "hostname=${hostname}"
  echo "rspamd=${rspamd}"
  echo "unbound=${unbound}"
  echo "spam_filter=${spam_filter}"
  echo "resolver=$(cat "$MAIL_EXIM_DIR/resolver" 2>/dev/null || echo local)"
}

mail_sync() {
  # The whole mail state, as JSON on stdin, from the panel:
  #   {"domains": [{"domain", "catch_all"}],
  #    "mailboxes": [{"local", "domain", "user", "hash", "quota_mb", "enabled"}],
  #    "forwarders": [{"address", "to": [...]}],
  #    "senders": {mailbox: [domains it may send as]},
  #    "local_senders": {linux user: [domains its PHP mail is signed for]},
  #    "relay_routes": {domain: relay id or "direct"}, "default_relay": id,
  #    "autoresponders": [{"address", "subject", "body", "start", "end", "days"}]}
  # Every field is checked again here and nothing is written unless all of it
  # passes. Writes what Exim and Dovecot read, makes missing mail directories
  # and DKIM keys, writes each mailbox's autoresponder (a Sieve vacation in
  # its home), and prints each domain's DKIM public key. Neither service
  # needs a reload: both notice a changed file on the next lookup.
  [[ -f "$MAIL_EXIM_CONF" ]] || deny "the Email addon is not installed"
  local payload status=0
  # The maps below name each domain's DKIM key by file and selector; an Exim
  # configuration from before that would sign with the wrong domain.
  if ! grep -q 'dkim_selectors' "$MAIL_EXIM_CONF"; then
    mail_ensure_maps
    mail_write_exim_conf
    mail_exim_reload || true
  fi
  payload="$(mktemp)"
  head -c 8000000 >"$payload"
  python3 - "$payload" "$MAIL_EXIM_DIR" "$MAIL_DOVECOT_DIR" "$MAIL_TOOL_DIR" "$MAIL_VACATION_SCRIPT" <<'PY' || status=$?
import base64
import grp
import json
import os
import pwd
import re
import stat
import subprocess
import sys

payload_path, exim_dir, dovecot_dir, tool_dir, vacation_name = sys.argv[1:6]

DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
LOCAL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
REMOTE_RE = re.compile(r"^[A-Za-z0-9._%+=-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")
RELAY_RE = re.compile(r"^(?:direct|[a-z0-9][a-z0-9-]{0,31})$")
HASH_RE = re.compile(r"^\{SHA512-CRYPT\}\$6\$(rounds=[0-9]{4,9}\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}$")
RESERVED = {
    "root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy",
    "www-data", "backup", "list", "irc", "_apt", "nobody", "spanel", "spanel-sites", "spanel-sftp",
    "spanel-sftp-site", "mysql", "redis", "nginx",
}


def fail(message):
    print(f"spanel-helper: mail-sync: {message}", file=sys.stderr)
    sys.exit(1)


try:
    with open(payload_path, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    fail("expects the mail state as JSON on stdin")
if not isinstance(data, dict):
    fail("expects a JSON object")


def linux_user(value, what):
    user = str(value or "")
    if not USER_RE.fullmatch(user) or user in RESERVED or user.startswith("sftp_"):
        fail(f"invalid owner for {what}: {user!r}")
    try:
        account = pwd.getpwnam(user)
    except KeyError:
        fail(f"no Linux user {user} for {what}")
    if account.pw_uid < 1000 or account.pw_dir != f"/home/{user}":
        fail(f"{user} is not a panel account")
    return account


def remote(value):
    value = str(value or "").strip()
    if not REMOTE_RE.fullmatch(value) or ".." in value or len(value) > 254:
        fail(f"invalid address: {value!r}")
    return value.lower()


def domain_list(values, owner):
    clean = []
    for value in values or []:
        value = str(value or "").strip().lower()
        if not DOMAIN_RE.fullmatch(value):
            fail(f"invalid domain for {owner}: {value!r}")
        if value not in clean:
            clean.append(value)
    return clean


sys.path.insert(0, tool_dir)
try:
    import spanel_mailtool
except ImportError:
    fail(f"{tool_dir}/spanel_mailtool.py is missing - run the SPanel update again")

domains, catchall, selectors = [], {}, {}
for item in data.get("domains") or []:
    name = str((item or {}).get("domain") or "").strip().lower()
    if not DOMAIN_RE.fullmatch(name):
        fail(f"invalid domain: {name!r}")
    if name in domains:
        fail(f"domain listed twice: {name}")
    domains.append(name)
    target = str(item.get("catch_all") or "").strip()
    if target:
        catchall[name] = remote(target)
    # The selector its mail is signed with; "spanel" is the key every domain
    # had before DKIM rotation.
    try:
        selectors[name] = spanel_mailtool.check_selector(item.get("dkim_selector") or "spanel")
    except spanel_mailtool.ToolError as exc:
        fail(str(exc))
domain_set = set(domains)

boxes, seen = [], set()
for item in data.get("mailboxes") or []:
    local = str(item.get("local") or "")
    domain = str(item.get("domain") or "")
    secret = str(item.get("hash") or "")
    if not LOCAL_RE.fullmatch(local) or ".." in local:
        fail(f"invalid mailbox name: {local!r}")
    if domain not in domain_set:
        fail(f"mailbox outside a mail domain: {local}@{domain}")
    if not HASH_RE.fullmatch(secret):
        fail(f"invalid password hash for {local}@{domain}")
    account = linux_user(item.get("user"), f"{local}@{domain}")
    quota = item.get("quota_mb") or 0
    if not isinstance(quota, int) or not 0 <= quota <= 1048576:
        fail(f"invalid quota for {local}@{domain}")
    address = f"{local}@{domain}"
    if address in seen:
        fail(f"{address} is listed twice")
    seen.add(address)
    boxes.append({
        "address": address, "local": local, "domain": domain, "user": account.pw_name,
        "uid": account.pw_uid, "gid": account.pw_gid, "hash": secret, "quota": quota,
        "enabled": bool(item.get("enabled", True)),
    })

aliases = {}
for item in data.get("forwarders") or []:
    address = remote(item.get("address"))
    local, _, domain = address.partition("@")
    if domain not in domain_set or not LOCAL_RE.fullmatch(local):
        fail(f"forwarder outside a mail domain: {address}")
    targets = [remote(target) for target in (item.get("to") or [])]
    if not 1 <= len(targets) <= 50:
        fail(f"forwarder {address} needs 1 to 50 destinations")
    if address in aliases:
        fail(f"forwarder listed twice: {address}")
    if address in seen and address not in targets:
        # The mailbox keeps a copy: Exim hands an address redirected to itself
        # on to the mailbox router.
        targets.append(address)
    aliases[address] = targets

senders = {}
for key, values in (data.get("senders") or {}).items():
    if key not in seen:
        fail(f"sender that is not a mailbox: {key!r}")
    senders[key] = domain_list(values, key)
local_senders = {}
for key, values in (data.get("local_senders") or {}).items():
    local_senders[linux_user(key, "PHP mail").pw_name] = domain_list(values, key)

routes = []
for name, value in (data.get("relay_routes") or {}).items():
    name, value = str(name).lower(), str(value or "")
    if name not in domain_set or not RELAY_RE.fullmatch(value):
        fail(f"invalid relay route {name!r}: {value!r}")
    routes.append(f"{name}: {value}")
default_relay = str(data.get("default_relay") or "")
if default_relay:
    if not RELAY_RE.fullmatch(default_relay) or default_relay == "direct":
        fail(f"invalid default relay {default_relay!r}")
    routes.append(f"*: {default_relay}")

# Autoresponders, rendered (and checked again) by the mail tool.
vacations = {}
if data.get("autoresponders"):
    try:
        vacations = spanel_mailtool.vacation_scripts(data.get("autoresponders"))
    except spanel_mailtool.ToolError as exc:
        fail(str(exc))
    for address in vacations:
        if address not in seen:
            fail(f"autoresponder for {address}, which is not a mailbox")

exim_gid = grp.getgrnam("Debian-exim").gr_gid
dovecot_gid = grp.getgrnam("dovecot").gr_gid
spanel_gid = grp.getgrnam("spanel").gr_gid


def open_dir(parent_fd, name, uid, gid, mode):
    """A directory under parent_fd, made if missing, never through a symlink.

    Levels below a mailbox belong to its owner, who could swap one for a link
    to anywhere; opening relative to the parent with O_NOFOLLOW, then changing
    ownership through the descriptor, means root only ever touches what it
    opened.
    """
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    os.fchown(fd, uid, gid)
    os.fchmod(fd, mode)
    return fd


def put_vacation(box_fd, box):
    """The mailbox's autoresponder script, or none. The mailbox directory is
    its owner's, who could plant a link or a FIFO under either name: nothing
    is followed or read that is not a plain file, and a failure is that
    mailbox's alone - it never stops the sync of everyone else."""
    script = vacations.get(box["address"])
    try:
        if script is None:
            for name in (vacation_name, vacation_name[:-len(".sieve")] + ".svbin"):
                try:
                    os.unlink(name, dir_fd=box_fd)
                except FileNotFoundError:
                    pass
            return
        wanted = script.encode()
        try:
            fd = os.open(vacation_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=box_fd)
            try:
                if stat.S_ISREG(os.fstat(fd).st_mode) and os.read(fd, len(wanted) + 1) == wanted:
                    return
            finally:
                os.close(fd)
        except OSError:
            pass
        tmp = "." + vacation_name + ".new"
        try:
            os.unlink(tmp, dir_fd=box_fd)
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=box_fd)
        try:
            os.fchown(fd, box["uid"], spanel_gid)
            os.fchmod(fd, 0o640)
            os.write(fd, wanted)
        finally:
            os.close(fd)
        os.replace(tmp, vacation_name, src_dir_fd=box_fd, dst_dir_fd=box_fd)
    except OSError as exc:
        print(f"spanel-helper: mail-sync: autoresponder of {box['address']} not written: {exc}", file=sys.stderr)


for box in boxes:
    # /home/<user> is root's (the SFTP chroot); mail/ and mail/<domain>/ are
    # root's too, so the customer cannot rearrange them. From the mailbox down
    # the directories are the owner's, with group spanel (setgid) so the panel
    # can read them for backups and disk usage.
    home_fd = os.open(f"/home/{box['user']}", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fds = [home_fd]
    try:
        fds.append(open_dir(fds[-1], "mail", 0, spanel_gid, 0o751))
        fds.append(open_dir(fds[-1], box["domain"], 0, spanel_gid, 0o751))
        fds.append(open_dir(fds[-1], box["local"], box["uid"], spanel_gid, 0o2750))
        fds.append(open_dir(fds[-1], "Maildir", box["uid"], spanel_gid, 0o2750))
        for part in ("cur", "new", "tmp"):
            os.close(open_dir(fds[-1], part, box["uid"], spanel_gid, 0o2750))
        put_vacation(fds[3], box)
    finally:
        for fd in reversed(fds):
            os.close(fd)

dkim = {}
for domain in domains:
    # "spanel" is dkim/<domain>.pem, as before rotation; another selector's
    # key is dkim/<selector>/<domain>.pem.
    key_path = os.path.join(exim_dir, "dkim", spanel_mailtool.dkim_key_name(domain, selectors[domain]) + ".pem")
    os.makedirs(os.path.dirname(key_path), mode=0o750, exist_ok=True)
    os.chown(os.path.dirname(key_path), 0, exim_gid)
    if not os.path.exists(key_path):
        fd = os.open(key_path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        os.fchown(fd, 0, exim_gid)
        os.close(fd)
        subprocess.run(["openssl", "genrsa", "-out", key_path + ".new", "2048"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.chown(key_path + ".new", 0, exim_gid)
        os.chmod(key_path + ".new", 0o640)
        os.replace(key_path + ".new", key_path)
    der = subprocess.run(["openssl", "rsa", "-in", key_path, "-pubout", "-outform", "DER"],
                         check=True, capture_output=True).stdout
    dkim[domain] = base64.b64encode(der).decode()


def write(path, lines, gid):
    try:
        before = os.stat(path).st_mtime_ns // 1_000_000_000
    except FileNotFoundError:
        before = None
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        os.fchown(fd, 0, gid)
        os.fchmod(fd, 0o640)
        os.write(fd, "".join(line + "\n" for line in lines).encode())
    finally:
        os.close(fd)
    os.replace(tmp, path)
    # Dovecot reloads its passwd-files when the mtime (in whole seconds) or
    # the size changed. A new password is a hash of the same length, so a
    # rewrite in the same second as the last one would never be seen.
    if before is not None and os.stat(path).st_mtime_ns // 1_000_000_000 <= before:
        bumped = (before + 1) * 1_000_000_000
        os.utime(path, ns=(bumped, bumped))


E = lambda name: os.path.join(exim_dir, name)  # noqa: E731
write(E("domains"), [f"{d}: {d}" for d in domains], exim_gid)
write(E("mailboxes"), [f"{b['address']}: {b['user']}" for b in sorted(boxes, key=lambda b: b["address"])], exim_gid)
write(E("aliases"), [f"{a}: {', '.join(t)}" for a, t in sorted(aliases.items())], exim_gid)
write(E("catchall"), [f"{d}: {t}" for d, t in sorted(catchall.items())], exim_gid)
write(E("senders"), [f"{a}: {' : '.join(d)}" for a, d in sorted(senders.items()) if d], exim_gid)
write(E("local_senders"), [f"{u}: {' : '.join(d)}" for u, d in sorted(local_senders.items()) if d], exim_gid)
dkim_domain_lines, dkim_selector_lines = spanel_mailtool.dkim_maps({d: selectors[d] for d in dkim})
write(E("dkim_domains"), dkim_domain_lines, exim_gid)
write(E("dkim_selectors"), dkim_selector_lines, exim_gid)
write(E("relay_routes"), sorted(routes), exim_gid)

users, denied = [], []
for box in sorted(boxes, key=lambda b: b["address"]):
    home = f"/home/{box['user']}/mail/{box['domain']}/{box['local']}"
    users.append(f"{box['address']}:{box['hash']}:{box['uid']}:{box['gid']}::{home}::userdb_quota_rule=*:storage={box['quota']}M")
    if not box["enabled"]:
        denied.append(f"{box['address']}:")
previously_denied = set()
try:
    with open(os.path.join(dovecot_dir, "denied"), encoding="utf-8") as handle:
        previously_denied = {line.split(":", 1)[0] for line in handle if line.strip()}
except OSError:
    pass
write(os.path.join(dovecot_dir, "users"), users, dovecot_gid)
write(os.path.join(dovecot_dir, "denied"), denied, dovecot_gid)
# A mailbox suspended just now loses the IMAP and POP3 sessions it still has
# open; new sign-ins are refused by the deny passdb.
for line in denied:
    address = line.split(":", 1)[0]
    if address not in previously_denied:
        subprocess.run(["doveadm", "kick", address], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

print(json.dumps({"domains": domains, "mailboxes": len(boxes), "forwarders": len(aliases), "dkim": dkim,
                  "autoresponders": len(vacations)}))
PY
  rm -f "$payload"
  [[ $status -eq 0 ]] || exit "$status"
  # Cheap, and a second chance if the path unit missed a certificate change.
  "$MAIL_TLS_SCRIPT" >/dev/null 2>&1 || true
}

mail_write_sieve() {
  # Readable by every mailbox owner: Sieve runs as the mail's Linux user.
  install -d -o root -g root -m 0755 "$MAIL_SIEVE_DIR"
  cat >"$MAIL_SIEVE_DIR/spam-to-junk.sieve" <<'SIEVE'
# Managed by SPanel (Email addon). Mail the spam filter marked goes to Junk.
require ["fileinto", "mailbox"];
if header :contains "X-Spam-Status" "Yes" {
  fileinto :create "Junk";
  stop;
}
SIEVE
  chmod 0644 "$MAIL_SIEVE_DIR/spam-to-junk.sieve"
  # Spam training (IMAPSieve): a message moved into Junk is learnt as spam,
  # one moved out of it (anywhere but Trash) as ham. The pipe programs run as
  # the mailbox's owner and only queue the message for spanel-mail-learn.
  cat >"$MAIL_SIEVE_DIR/learn-spam.sieve" <<'SIEVE'
# Managed by SPanel (Email addon). Moved into Junk: Rspamd learns it as spam.
require ["vnd.dovecot.pipe", "copy", "imapsieve"];
pipe :copy "spanel-learn-spam";
SIEVE
  cat >"$MAIL_SIEVE_DIR/learn-ham.sieve" <<'SIEVE'
# Managed by SPanel (Email addon). Moved out of Junk: Rspamd learns it as ham,
# unless it went to Trash.
require ["vnd.dovecot.pipe", "copy", "imapsieve", "environment", "variables"];
if environment :matches "imap.mailbox" "*" {
  set "mailbox" "${1}";
}
if string "${mailbox}" "Trash" {
  stop;
}
pipe :copy "spanel-learn-ham";
SIEVE
  install -d -o root -g root -m 0755 "$MAIL_SIEVE_DIR/bin"
  local kind
  for kind in spam ham; do
    printf '#!/bin/sh\n# Managed by SPanel (Email addon). Runs as the mailbox owner: queues the message.\nexec /usr/bin/python3 -I %s learn-enqueue %s\n' \
      "$MAIL_TOOL" "$kind" >"$MAIL_SIEVE_DIR/bin/spanel-learn-${kind}"
    chmod 0755 "$MAIL_SIEVE_DIR/bin/spanel-learn-${kind}"
  done
  chmod 0644 "$MAIL_SIEVE_DIR"/learn-spam.sieve "$MAIL_SIEVE_DIR"/learn-ham.sieve
  # Compiled here, as root: the mailbox owners could not write the result.
  sievec "$MAIL_SIEVE_DIR/spam-to-junk.sieve" >>"$MAIL_INSTALL_LOG" 2>&1 \
    || deny "could not compile the spam Sieve script (see ${MAIL_INSTALL_LOG})"
  chmod 0644 "$MAIL_SIEVE_DIR"/spam-to-junk.svbin
  # The learn scripts need the pipe extension, which only the IMAPSieve
  # configuration enables: Dovecot compiles them itself when first used.
  rm -f "$MAIL_SIEVE_DIR"/learn-spam.svbin "$MAIL_SIEVE_DIR"/learn-ham.svbin
}

mail_write_learn_service() {
  # The spool mailbox owners queue messages into (they can add files but not
  # list or read anyone else's), and the root service that hands them to
  # Rspamd's controller with its key.
  install -d -o root -g root -m 1733 "$MAIL_LEARN_SPOOL"
  cat >/etc/systemd/system/spanel-mail-learn.service <<UNIT
[Unit]
Description=SPanel: hand messages moved in and out of Junk to Rspamd
After=rspamd.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I ${MAIL_TOOL} learn-process ${MAIL_LEARN_SPOOL} ${MAIL_RSPAMD_KEY_FILE}
UNIT
  # Every minute rather than on a change of the directory: a file a mailbox
  # owner leaves there must not make the service run back to back.
  cat >/etc/systemd/system/spanel-mail-learn.timer <<UNIT
[Unit]
Description=SPanel: hand the spam-training queue to Rspamd every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=15s

[Install]
WantedBy=timers.target
UNIT
  rm -f /etc/systemd/system/spanel-mail-learn.path
  systemctl daemon-reload
  systemctl enable --now spanel-mail-learn.timer >/dev/null 2>&1 || true
}

mail_write_quota_warning() {
  # Dovecot runs this as user dovecot when a mailbox crosses 80% or 95% of its
  # quota; the mail tool checks the mailbox exists and mails it once a day.
  install -d -o root -g root -m 0755 "$MAIL_TOOL_DIR"
  printf '#!/bin/sh\n# Managed by SPanel (Email addon): warns a mailbox that is filling up.\nexec /usr/bin/python3 -I %s quota-warning "$@"\n' \
    "$MAIL_TOOL" >"$MAIL_QUOTA_WARNING"
  chmod 0755 "$MAIL_QUOTA_WARNING"
  install -d -o dovecot -g dovecot -m 0750 /var/lib/spanel-mail /var/lib/spanel-mail/quota-warnings
}

mail_rspamd_group() {
  if getent group _rspamd >/dev/null; then printf '_rspamd'; else printf 'root'; fi
}

mail_write_rspamd_conf() {
  local key group header rewrite reject greylist subject_tag
  group="$(mail_rspamd_group)"
  install -d -o root -g spanel -m 0750 /etc/spanel
  if [[ ! -s "$MAIL_RSPAMD_KEY_FILE" ]]; then
    # Made 0640 while still empty, then filled.
    install -m 0640 -o root -g spanel /dev/null "$MAIL_RSPAMD_KEY_FILE"
    openssl rand -hex 24 >"$MAIL_RSPAMD_KEY_FILE"
  fi
  chown root:spanel "$MAIL_RSPAMD_KEY_FILE"
  chmod 0640 "$MAIL_RSPAMD_KEY_FILE"
  key="$(tr -d '[:space:]' <"$MAIL_RSPAMD_KEY_FILE")"
  read -r header rewrite reject greylist subject_tag < <(python3 - "$MAIL_SETTINGS_FILE" <<'PY'
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        raw = json.load(handle)
except (OSError, ValueError):
    raw = {}
raw = raw if isinstance(raw, dict) else {}


def score(key, fallback):
    try:
        value = float(raw.get(key, fallback))
    except (TypeError, ValueError):
        return fallback
    return value if 1 <= value <= 100 else fallback


header = score("spam_header_score", 6.0)
reject = score("spam_reject_score", 15.0)
if reject <= header:
    reject = header + 1
rewrite = score("spam_rewrite_score", min(reject - 0.5, header + 2.0))
if rewrite < header or rewrite >= reject:
    rewrite = min(reject - 0.5, header + 2.0)
raw_tag = str(raw.get("spam_subject_tag") or "[SPAM]").strip()
tag = re.sub(r'[^A-Za-z0-9_\-\[\]()*:! ]+', '', raw_tag)[:32].strip() or "[SPAM]"
print(f"{header:g}", f"{rewrite:g}", f"{reject:g}", "yes" if raw.get("greylisting", True) is not False else "no", tag)
PY
)
  [[ -n "${subject_tag:-}" ]] || subject_tag="[SPAM]"
  install -d -m 0755 "$MAIL_RSPAMD_LOCAL" "$MAIL_RSPAMD_OVERRIDE"

  # override.d, not local.d: local.d would merge with the stock secure_ip list,
  # which lets anything on loopback - customers' PHP included - in without a
  # password.
  install -m 0640 -o root -g "$group" /dev/null "$MAIL_RSPAMD_OVERRIDE/worker-controller.inc"
  cat >"$MAIL_RSPAMD_OVERRIDE/worker-controller.inc" <<EOF
${MAIL_MARKER}
# The panel reads Rspamd's history and statistics here with the key in ${MAIL_RSPAMD_KEY_FILE}.
bind_socket = "127.0.0.1:11334";
password = "${key}";
enable_password = "${key}";
secure_ip = [];
EOF
  cat >"$MAIL_RSPAMD_LOCAL/worker-normal.inc" <<EOF
${MAIL_MARKER}
# Exim asks this worker about each message from outside.
bind_socket = "127.0.0.1:11333";
count = 1;
EOF
  # Exim talks to the normal worker directly; the milter proxy is not needed.
  cat >"$MAIL_RSPAMD_OVERRIDE/worker-proxy.inc" <<EOF
${MAIL_MARKER}
enabled = false;
EOF
  cat >"$MAIL_RSPAMD_LOCAL/redis.conf" <<EOF
${MAIL_MARKER}
# Greylisting, the Bayes filter and the scan history keep their state in the
# server's own Redis, in a database of Rspamd's own (the panel uses 0).
servers = "127.0.0.1:6379";
db = "${MAIL_RSPAMD_REDIS_DB}";
EOF
  cat >"$MAIL_RSPAMD_LOCAL/history_redis.conf" <<EOF
${MAIL_MARKER}
nrows = 2000;
EOF
  cat >"$MAIL_RSPAMD_LOCAL/actions.conf" <<EOF
${MAIL_MARKER}
reject = ${reject};
add_header = ${header};
greylist = $([[ "$greylist" == "yes" ]] && echo 4 || echo null);
rewrite_subject = ${rewrite};
subject = "${subject_tag} %s";
EOF
  cat >"$MAIL_RSPAMD_LOCAL/greylist.conf" <<EOF
${MAIL_MARKER}
enabled = $([[ "$greylist" == "yes" ]] && echo true || echo false);
EOF
  cat >"$MAIL_RSPAMD_LOCAL/classifier-bayes.conf" <<EOF
${MAIL_MARKER}
autolearn = true;
EOF
  # Exim signs outgoing mail itself; Rspamd only scans what arrives.
  cat >"$MAIL_RSPAMD_LOCAL/dkim_signing.conf" <<EOF
${MAIL_MARKER}
enabled = false;
EOF
  cat >"$MAIL_RSPAMD_LOCAL/arc.conf" <<EOF
${MAIL_MARKER}
enabled = false;
EOF
  mail_write_rspamd_resolver "$(cat "$MAIL_EXIM_DIR/resolver" 2>/dev/null || echo local)"
  local list
  for list in spanel-allow-senders spanel-allow-domains spanel-block-senders spanel-block-domains; do
    [[ -f "$MAIL_RSPAMD_LOCAL/${list}.map" ]] || install -m 0644 -o root -g root /dev/null "$MAIL_RSPAMD_LOCAL/${list}.map"
  done
  # The allowlist & blocklist: envelope sender or From header, as an address or a domain.
  # A score rather than an early "accept": Rspamd leaves early verdicts out of
  # its history, and an allowed/blocked message must still show in the log.
  cat >"$MAIL_RSPAMD_LOCAL/multimap.conf" <<EOF
${MAIL_MARKER}
SPANEL_ALLOW_SENDER {
  type = "from";
  filter = "email:addr";
  map = "file://${MAIL_RSPAMD_LOCAL}/spanel-allow-senders.map";
  score = -50.0;
  description = "Sender on the SPanel allowlist";
}
SPANEL_ALLOW_SENDER_MIME {
  type = "header";
  header = "From";
  filter = "email:addr";
  map = "file://${MAIL_RSPAMD_LOCAL}/spanel-allow-senders.map";
  score = -50.0;
  description = "From address on the SPanel allowlist";
}
SPANEL_ALLOW_DOMAIN {
  type = "from";
  filter = "email:domain";
  map = "file://${MAIL_RSPAMD_LOCAL}/spanel-allow-domains.map";
  score = -50.0;
  description = "Sender domain on the SPanel allowlist";
}
SPANEL_ALLOW_DOMAIN_MIME {
  type = "header";
  header = "From";
  filter = "email:domain";
  map = "file://${MAIL_RSPAMD_LOCAL}/spanel-allow-domains.map";
  score = -50.0;
  description = "From domain on the SPanel allowlist";
}
SPANEL_BLOCK_SENDER {
  type = "from";
  filter = "email:addr";
  map = "file://${MAIL_RSPAMD_LOCAL}/spanel-block-senders.map";
  score = 50.0;
  description = "Sender on the SPanel blocklist";
}
SPANEL_BLOCK_SENDER_MIME {
  type = "header";
  header = "From";
  filter = "email:addr";
  map = "file://${MAIL_RSPAMD_LOCAL}/spanel-block-senders.map";
  score = 50.0;
  description = "From address on the SPanel blocklist";
}
SPANEL_BLOCK_DOMAIN {
  type = "from";
  filter = "email:domain";
  map = "file://${MAIL_RSPAMD_LOCAL}/spanel-block-domains.map";
  score = 50.0;
  description = "Sender domain on the SPanel blocklist";
}
SPANEL_BLOCK_DOMAIN_MIME {
  type = "header";
  header = "From";
  filter = "email:domain";
  map = "file://${MAIL_RSPAMD_LOCAL}/spanel-block-domains.map";
  score = 50.0;
  description = "From domain on the SPanel blocklist";
}
EOF
  chmod 0644 "$MAIL_RSPAMD_LOCAL"/worker-normal.inc "$MAIL_RSPAMD_OVERRIDE"/worker-proxy.inc \
    "$MAIL_RSPAMD_LOCAL"/redis.conf "$MAIL_RSPAMD_LOCAL"/history_redis.conf "$MAIL_RSPAMD_LOCAL"/greylist.conf \
    "$MAIL_RSPAMD_LOCAL"/actions.conf "$MAIL_RSPAMD_LOCAL"/multimap.conf "$MAIL_RSPAMD_LOCAL"/classifier-bayes.conf \
    "$MAIL_RSPAMD_LOCAL"/dkim_signing.conf "$MAIL_RSPAMD_LOCAL"/arc.conf
  if ! rspamadm configtest >>"$MAIL_INSTALL_LOG" 2>&1; then
    deny "the Rspamd configuration SPanel wrote does not load (see ${MAIL_INSTALL_LOG})"
  fi
}

mail_spam_enabled() {
  python3 - "$MAIL_SETTINGS_FILE" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        raw = json.load(handle)
except (OSError, ValueError):
    raw = {}
print("no" if isinstance(raw, dict) and raw.get("spam_enabled") is False else "yes")
PY
}

mail_apply_spam_service() {
  # The filter on: Unbound and Rspamd running and answering. Off: stopped.
  local waited
  if [[ "$(mail_spam_enabled)" == "yes" ]]; then
    systemctl enable unbound rspamd >/dev/null 2>&1 || true
    systemctl restart unbound 2>>"$MAIL_INSTALL_LOG" || deny "Unbound did not start - journalctl -u unbound -n 50 says why"
    waited=0
    while (( waited < 3 )); do
      mail_unbound_answers && break
      sleep 2
      waited=$((waited + 1))
    done
    if mail_unbound_answers; then
      mail_write_rspamd_resolver local
    else
      # Many providers let DNS out only to their own resolvers, as they do
      # port 25. Unbound is no use then: off, and Rspamd asks the system's.
      systemctl disable --now unbound >/dev/null 2>&1 || true
      mail_write_rspamd_resolver system
    fi
    if systemctl is-active --quiet rspamd; then
      systemctl reload rspamd 2>>"$MAIL_INSTALL_LOG" || systemctl restart rspamd
    else
      systemctl restart rspamd 2>>"$MAIL_INSTALL_LOG" || deny "Rspamd did not start - journalctl -u rspamd -n 50 says why"
    fi
    waited=0
    while (( waited < 60 )); do
      mail_rspamd_answers && break
      sleep 1
      waited=$((waited + 1))
    done
    mail_rspamd_answers || deny "Rspamd is running but does not answer on 127.0.0.1:11333 and 11334"
  else
    systemctl disable --now rspamd unbound >/dev/null 2>&1 || true
  fi
}

mail_configure() {
  # The server-wide mail settings and the relays, as JSON on stdin:
  #   {"auth_rate_per_hour", "local_rate_per_hour", "max_message_mb",
  #    "spam_enabled", "spam_header_score", "spam_reject_score", "greylisting",
  #    "allow_senders": [...], "allow_domains": [...],
  #    "relays": [{"id", "host", "port", "tls", "username", "password"}]}
  # Saves them (passwords apart, in relay_credentials), then rewrites Exim's
  # and Rspamd's configuration and reloads them. Which domain uses which relay
  # is mail-sync's.
  [[ -f "$MAIL_EXIM_CONF" ]] || deny "the Email addon is not installed"
  local payload status=0 previous
  payload="$(mktemp)"
  head -c 2000000 >"$payload"
  previous="$(mktemp)"
  cp -a "$MAIL_SETTINGS_FILE" "$previous" 2>/dev/null || : >"$previous"
  python3 - "$payload" "$MAIL_SETTINGS_FILE" "$MAIL_EXIM_DIR" "$MAIL_RSPAMD_LOCAL" <<'PY' || status=$?
import base64
import grp
import ipaddress
import json
import os
import re
import sys

payload_path, settings_path, exim_dir, rspamd_dir = sys.argv[1:5]
HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
ADDRESS_RE = re.compile(r"^[A-Za-z0-9._%+=-]{1,64}@[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
TRANSPORT = {"starttls": "relay_smtp", "ssl": "relay_smtps", "none": "relay_smtp_plain"}


def fail(message):
    print(f"spanel-helper: {message}", file=sys.stderr)
    sys.exit(1)


try:
    with open(payload_path, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    fail("mail-configure expects its settings as JSON on stdin")
if not isinstance(data, dict):
    fail("mail-configure expects a JSON object")
try:
    with open(settings_path, encoding="utf-8") as handle:
        saved = json.load(handle)
except (OSError, ValueError):
    saved = {}
saved = saved if isinstance(saved, dict) else {}


def whole(key, low, high):
    try:
        value = int(data.get(key))
    except (TypeError, ValueError):
        fail(f"{key} must be a whole number")
    if not low <= value <= high:
        fail(f"{key} must be between {low} and {high}")
    return value


def score(key):
    try:
        value = float(data.get(key))
    except (TypeError, ValueError):
        fail(f"{key} must be a number")
    if not 1 <= value <= 100:
        fail(f"{key} must be between 1 and 100")
    return value


def whole_opt(key, low, high, fallback):
    if data.get(key) is None:
        return fallback
    return whole(key, low, high)


def score_opt(key, fallback):
    if data.get(key) is None:
        return fallback
    return score(key)


header_val = score("spam_header_score")
reject_val = score("spam_reject_score")
if reject_val <= header_val:
    fail("the reject score must be higher than the Junk score")
rewrite_val = score_opt("spam_rewrite_score", min(reject_val - 0.5, header_val + 2.0))
if not (header_val <= rewrite_val < reject_val):
    rewrite_val = min(reject_val - 0.5, header_val + 2.0)
raw_subject_tag = str(data.get("spam_subject_tag") or saved.get("spam_subject_tag") or "[SPAM]").strip()
subject_tag = re.sub(r'[^A-Za-z0-9_\-\[\]()*:! ]+', '', raw_subject_tag)[:32].strip() or "[SPAM]"

domain_rate_default = whole_opt("domain_rate_per_hour", 0, 100000, int(saved.get("domain_rate_per_hour", 500) or 500))
raw_domain_limits = data.get("domain_rate_limits")
if raw_domain_limits is None:
    raw_domain_limits = saved.get("domain_rate_limits") or {}
if not isinstance(raw_domain_limits, dict):
    fail("domain_rate_limits must be an object mapping domain to hourly rate")
domain_rate_limits = {}
ratelimit_lines = []
for dom_key, dom_val in sorted(raw_domain_limits.items()):
    dom_clean = str(dom_key).strip().lower()
    if not DOMAIN_RE.fullmatch(dom_clean):
        fail(f"invalid domain in domain_rate_limits: {dom_key!r}")
    try:
        lim_int = int(dom_val)
    except (TypeError, ValueError):
        fail(f"invalid rate limit for {dom_clean}")
    if not 1 <= lim_int <= 100000:
        fail(f"rate limit for {dom_clean} must be between 1 and 100000")
    domain_rate_limits[dom_clean] = lim_int
    ratelimit_lines.append(f"{dom_clean}: {lim_int}")
if domain_rate_default > 0:
    ratelimit_lines.append(f"*: {domain_rate_default}")

settings = {
    "hostname": saved.get("hostname") or "localhost.localdomain",
    "auth_rate_per_hour": whole("auth_rate_per_hour", 0, 100000),
    "local_rate_per_hour": whole("local_rate_per_hour", 0, 100000),
    "domain_rate_per_hour": domain_rate_default,
    "auto_hold_on_rate_exceed": bool(data.get("auto_hold_on_rate_exceed", saved.get("auto_hold_on_rate_exceed", True))),
    "domain_rate_limits": domain_rate_limits,
    "max_message_mb": whole("max_message_mb", 1, 200),
    "spam_enabled": bool(data.get("spam_enabled", True)),
    "spam_header_score": header_val,
    "spam_rewrite_score": rewrite_val,
    "spam_reject_score": reject_val,
    "spam_subject_tag": subject_tag,
    "greylisting": bool(data.get("greylisting", True)),
}

senders = [str(item).strip().lower() for item in data.get("allow_senders") or []]
domains = [str(item).strip().lower() for item in data.get("allow_domains") or []]
block_senders = [str(item).strip().lower() for item in data.get("block_senders") or []]
block_domains = [str(item).strip().lower() for item in data.get("block_domains") or []]
if len(senders) > 5000 or len(domains) > 5000 or len(block_senders) > 5000 or len(block_domains) > 5000:
    fail("the allowlist and blocklist are limited to 5000 senders and 5000 domains each")
for item in senders:
    if not ADDRESS_RE.fullmatch(item):
        fail(f"invalid sender address on the allowlist: {item!r}")
for item in domains:
    if not DOMAIN_RE.fullmatch(item):
        fail(f"invalid domain on the allowlist: {item!r}")
for item in block_senders:
    if not ADDRESS_RE.fullmatch(item):
        fail(f"invalid sender address on the blocklist: {item!r}")
for item in block_domains:
    if not DOMAIN_RE.fullmatch(item):
        fail(f"invalid domain on the blocklist: {item!r}")

relays = data.get("relays") or []
if not isinstance(relays, list) or len(relays) > 20:
    fail("at most 20 relays")
hosts, transports, auth, credentials = [], [], [], []
seen_ids, seen_auth_hosts = set(), set()
for relay in relays:
    if not isinstance(relay, dict):
        fail("a relay must be an object")
    rid = str(relay.get("id") or "")
    host = str(relay.get("host") or "").strip().lower().rstrip(".")
    tls = str(relay.get("tls") or "starttls")
    try:
        port = int(relay.get("port") or 587)
    except (TypeError, ValueError):
        port = 0
    if not ID_RE.fullmatch(rid) or rid == "direct" or rid in seen_ids:
        fail(f"invalid relay id {rid!r}")
    seen_ids.add(rid)
    try:
        ipaddress.IPv4Address(host)
        if tls != "none":
            fail("a relay reached over TLS is named, not an IP address: its certificate is checked against the name")
    except ValueError:
        if not HOST_RE.fullmatch(host):
            fail(f"invalid relay host {host!r}")
    if not 1 <= port <= 65535 or tls not in TRANSPORT:
        fail(f"invalid port or TLS for relay {rid}")
    hosts.append(f"{rid}: {host}::{port} byname")
    transports.append(f"{rid}: {TRANSPORT[tls]}")
    user, secret = str(relay.get("username") or ""), str(relay.get("password") or "")
    if user:
        for label, value in (("user name", user), ("password", secret)):
            if not value or len(value) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in value) \
                    or value != value.strip():
                fail(f"invalid {label} for relay {rid}")
        if host in seen_auth_hosts:
            fail(f"two relays sign in to {host}: one host carries one login")
        seen_auth_hosts.add(host)
        auth.append(f"{host}: {rid}")
        credentials.append(f"{rid}.user: {base64.b64encode(user.encode()).decode()}")
        credentials.append(f"{rid}.pass: {base64.b64encode(secret.encode()).decode()}")

gid = grp.getgrnam("Debian-exim").gr_gid


def write(path, text, mode, group):
    fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchown(fd, 0, group)
        os.fchmod(fd, mode)
        os.write(fd, text.encode())
    finally:
        os.close(fd)
    os.replace(path + ".new", path)


def lines(items):
    return "".join(item + "\n" for item in items)


write(os.path.join(exim_dir, "relay_hosts"), lines(hosts), 0o640, gid)
write(os.path.join(exim_dir, "relay_transports"), lines(transports), 0o640, gid)
write(os.path.join(exim_dir, "relay_auth"), lines(auth), 0o640, gid)
write(os.path.join(exim_dir, "relay_credentials"), lines(credentials), 0o640, gid)
write(os.path.join(exim_dir, "ratelimits"), lines(ratelimit_lines), 0o640, gid)
if os.path.isdir("/etc/exim4"):
    write("/etc/exim4/spanel_ratelimits", lines(ratelimit_lines), 0o640, gid)
write(os.path.join(rspamd_dir, "spanel-allow-senders.map"), lines(sorted(set(senders))), 0o644, 0)
write(os.path.join(rspamd_dir, "spanel-allow-domains.map"), lines(sorted(set(domains))), 0o644, 0)
write(os.path.join(rspamd_dir, "spanel-block-senders.map"), lines(sorted(set(block_senders))), 0o644, 0)
write(os.path.join(rspamd_dir, "spanel-block-domains.map"), lines(sorted(set(block_domains))), 0o644, 0)
settings["relays"] = sorted(seen_ids)
write(settings_path, json.dumps(settings, indent=2, sort_keys=True), 0o600, 0)
print(f"{len(hosts)} relays")
PY
  rm -f "$payload"
  if [[ $status -ne 0 ]]; then
    rm -f "$previous"
    exit "$status"
  fi
  if ! ( mail_write_exim_conf ) || ! ( mail_write_rspamd_conf ); then
    # A setting the services refuse leaves the previous ones in force.
    cp -a "$previous" "$MAIL_SETTINGS_FILE"
    rm -f "$previous"
    ( mail_write_exim_conf ) >/dev/null 2>&1 || true
    ( mail_write_rspamd_conf ) >/dev/null 2>&1 || true
    deny "the mail server refused these settings (see ${MAIL_INSTALL_LOG}); the previous ones are still in force"
  fi
  rm -f "$previous"
  mail_apply_spam_service
  if systemctl is-active --quiet exim4; then
    systemctl reload exim4 2>/dev/null || systemctl restart exim4
    # Mail that waited under the old settings is tried again now.
    setsid exim4 -qf >/dev/null 2>&1 < /dev/null &
  fi
  echo "Mail settings applied."
}

mail_reconfigure() {
  # An update brought a newer helper: Exim's and Dovecot's configuration and
  # the Sieve scripts are written again from what is saved, so a server
  # installed before a feature gets it without reinstalling the addon.
  # Nothing to do where the addon was never installed.
  if [[ ! -f "$MAIL_EXIM_CONF" ]] || ! grep -qF "$MAIL_MARKER" "$MAIL_EXIM_CONF"; then
    echo "The Email addon is not installed; nothing to reconfigure."
    return 0
  fi
  mail_ensure_maps
  mail_write_tls_script
  "$MAIL_TLS_SCRIPT" >/dev/null 2>&1 || true
  mail_write_exim_conf
  mail_write_sieve
  mail_write_quota_warning
  mail_write_dovecot_conf
  mail_write_learn_service
  if systemctl is-active --quiet dovecot; then systemctl reload dovecot 2>/dev/null || systemctl restart dovecot; fi
  if systemctl is-active --quiet exim4; then systemctl reload exim4 2>/dev/null || systemctl restart exim4; fi
  echo "Mail configuration rewritten."
}

require_dkim_selector() {
  [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || deny "invalid DKIM selector: $1"
}

mail_dkim_key() {
  # The key file of a domain's selector: "spanel" is dkim/<domain>.pem, the
  # key every domain had before rotation; any other is dkim/<selector>/<domain>.pem.
  if [[ "$2" == "spanel" ]]; then
    printf '%s/dkim/%s.pem' "$MAIL_EXIM_DIR" "$1"
  else
    printf '%s/dkim/%s/%s.pem' "$MAIL_EXIM_DIR" "$2" "$1"
  fi
}

mail_dkim_active_selector() {
  awk -F': ' -v d="$1" '$1 == d { print $2; exit }' "$MAIL_EXIM_DIR/dkim_selectors" 2>/dev/null || true
}

mail_dkim() {
  # The DKIM public key (base64, one line) of a domain's selector for its DNS
  # record, making the key pair first when there is none. A rotation makes
  # the next selector's key this way, long before Exim signs with it.
  local domain="$1" selector="${2:-spanel}" key tmp
  require_domain "$domain"
  require_dkim_selector "$selector"
  key="$(mail_dkim_key "$domain" "$selector")"
  install -d -o root -g Debian-exim -m 0750 "$MAIL_EXIM_DIR/dkim" "$(dirname "$key")"
  if [[ ! -f "$key" ]]; then
    tmp="$(mktemp "$(dirname "$key")/.${domain}.XXXXXX")"
    chown root:Debian-exim "$tmp"
    chmod 0640 "$tmp"
    openssl genrsa -out "$tmp" 2048 >/dev/null 2>&1 || { rm -f "$tmp"; deny "could not create a DKIM key for ${domain}"; }
    chown root:Debian-exim "$tmp"
    chmod 0640 "$tmp"
    mv -f "$tmp" "$key"
  fi
  openssl rsa -in "$key" -pubout -outform DER 2>/dev/null | base64 -w0
  echo
}

mail_dkim_drop() {
  # A retired (or abandoned) selector's key goes. Never the one Exim signs with.
  local domain="$1" selector="$2" key
  require_domain "$domain"
  require_dkim_selector "$selector"
  if [[ "$(mail_dkim_active_selector "$domain")" == "$selector" ]] \
     || { [[ "$selector" == "spanel" ]] && [[ -z "$(mail_dkim_active_selector "$domain")" ]]; }; then
    deny "${selector} is the key ${domain} is signed with"
  fi
  key="$(mail_dkim_key "$domain" "$selector")"
  rm -f -- "$key"
  # A selector's own directory goes once empty; never dkim/ itself.
  if [[ "$selector" != "spanel" ]]; then rmdir -- "$(dirname "$key")" 2>/dev/null || true; fi
  echo "removed DKIM key ${selector} of ${domain}"
}

mail_dkim_export() {
  # Every DKIM key of a domain, for its owner's backup: {"selector": "PEM"}.
  local domain="$1"
  require_domain "$domain"
  python3 - "$MAIL_EXIM_DIR/dkim" "$domain" <<'PY'
import json
import os
import re
import sys

base, domain = sys.argv[1:3]
SELECTOR_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
keys = {}
candidates = [("spanel", os.path.join(base, f"{domain}.pem"))]
try:
    names = sorted(os.listdir(base))
except OSError:
    names = []
for name in names:
    if SELECTOR_RE.fullmatch(name) and os.path.isdir(os.path.join(base, name)):
        candidates.append((name, os.path.join(base, name, f"{domain}.pem")))
for selector, path in candidates:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        continue
    with os.fdopen(fd, encoding="ascii", errors="replace") as handle:
        text = handle.read(20000)
    if "PRIVATE KEY-----" in text:
        keys[selector] = text
print(json.dumps(keys))
PY
}

mail_dkim_import() {
  # A DKIM private key from a backup, PEM on stdin, for a domain's selector:
  # checked to be an RSA key of 1024 to 4096 bits, then put in place and its
  # public key printed, as mail-dkim does.
  local domain="$1" selector="$2" key tmp bits
  require_domain "$domain"
  require_dkim_selector "$selector"
  key="$(mail_dkim_key "$domain" "$selector")"
  install -d -o root -g Debian-exim -m 0750 "$MAIL_EXIM_DIR/dkim" "$(dirname "$key")"
  tmp="$(mktemp "$(dirname "$key")/.${domain}.XXXXXX")"
  chown root:Debian-exim "$tmp"
  chmod 0640 "$tmp"
  head -c 16384 >"$tmp"
  bits="$(openssl rsa -in "$tmp" -noout -text 2>/dev/null | sed -nE 's/^Private-Key: \(([0-9]+) bit.*/\1/p' | head -n1 || true)"
  if [[ ! "$bits" =~ ^[0-9]+$ ]] || (( bits < 1024 || bits > 4096 )) || ! openssl rsa -in "$tmp" -check -noout >/dev/null 2>&1; then
    rm -f "$tmp"
    deny "not an RSA private key of 1024 to 4096 bits"
  fi
  mv -f "$tmp" "$key"
  openssl rsa -in "$key" -pubout -outform DER 2>/dev/null | base64 -w0
  echo
}

mail_log_tail() {
  local lines="$1"
  [[ "$lines" =~ ^[0-9]{1,4}$ ]] && (( lines >= 1 && lines <= 5000 )) || deny "invalid line count: $lines"
  tail -n "$lines" /var/log/exim4/mainlog 2>/dev/null || true
}

mail_queue_count() {
  exim4 -bpc 2>/dev/null || echo 0
}

require_queue_id() {
  # Exim's message ids: the 4.97+ form (Ubuntu 24.04) and the older one.
  [[ "$1" =~ ^[0-9A-Za-z]{6}-([0-9A-Za-z]{11}-[0-9A-Za-z]{4}|[0-9A-Za-z]{6}-[0-9A-Za-z]{2})$ ]] \
    || deny "invalid message id: $1"
}

mail_queue_list() {
  # The queue as `exim -bp` prints it, at most 4 MB of it; the panel parses it.
  exim4 -bp 2>/dev/null | head -c 4000000 || true
}

mail_queue_show() {
  # One message's envelope and headers (exim -Mvh), never its body.
  require_queue_id "$1"
  exim4 -Mvh "$1" 2>/dev/null | head -c 200000 || deny "no message ${1} in the queue"
}

mail_queue_action() {
  # retry | freeze | thaw | delete, for up to 100 messages.
  local action="$1" id flag
  shift
  [[ $# -ge 1 && $# -le 100 ]] || deny "usage: mail-queue-action <retry|freeze|thaw|delete> <id>..."
  for id in "$@"; do require_queue_id "$id"; done
  case "$action" in
    freeze) flag=-Mf ;;
    thaw) flag=-Mt ;;
    delete) flag=-Mrm ;;
    retry)
      # A delivery can take minutes: in the background, as exim4 -qf does.
      setsid timeout 600 exim4 -M "$@" >/dev/null 2>&1 < /dev/null &
      echo "retrying $# message(s)"
      return 0
      ;;
    *) deny "invalid queue action: $action" ;;
  esac
  # Exim says per message what it did (or that the message is gone).
  exim4 "$flag" "$@" 2>&1 | head -c 20000 || true
}

mail_queue_retry_all() {
  # Every message, frozen ones included, tried again now.
  setsid timeout 1800 exim4 -qff >/dev/null 2>&1 < /dev/null &
  echo "retrying the whole queue"
}

mail_rspamd_log() {
  local lines="$1"
  [[ "$lines" =~ ^[0-9]{1,4}$ ]] && (( lines >= 1 && lines <= 5000 )) || deny "invalid line count: $lines"
  tail -n "$lines" /var/log/rspamd/rspamd.log 2>/dev/null || true
}

mail_purge_domain() {
  # A mail domain removed in the panel: its mail, from its owner's home, and
  # its DKIM key. mail/ and mail/<domain>/ are root's, so the path cannot be
  # redirected; rm does not follow links inside it.
  local user="$1" domain="$2" dir
  require_linux_user "$user"
  require_domain "$domain"
  dir="/home/${user}/mail/${domain}"
  if [[ -L "$dir" ]]; then
    rm -f -- "$dir"
  elif [[ -d "$dir" ]]; then
    rm -rf --one-file-system -- "$dir"
  fi
  rm -f -- "$MAIL_EXIM_DIR/dkim/${domain}.pem" "$MAIL_EXIM_DIR"/dkim/*/"${domain}.pem"
  echo "mail data removed: ${domain}"
}

mail_rspamd_answers() {
  local key
  key="$(tr -d '[:space:]' <"$MAIL_RSPAMD_KEY_FILE" 2>/dev/null)"
  [[ -n "$key" ]] || return 1
  [[ "$(curl -fsS -m 3 -H "Password: ${key}" http://127.0.0.1:11334/ping 2>/dev/null)" == pong* ]] \
    && [[ -n "$(ss -H -lnt 'sport = :11333' 2>/dev/null)" ]]
}

mail_exim_reload() {
  exim4 -bV >/dev/null 2>>"$MAIL_INSTALL_LOG" || return 1
  if systemctl is-active --quiet exim4; then
    systemctl reload exim4 2>/dev/null || systemctl restart exim4
  fi
}

mail_relay_test() {
  # Send one message to the given address now, and print what Exim logged
  # for it: delivered, deferred with the remote server's answer, or failed.
  # The log never holds the smarthost password.
  local to="$1" hostname msgid qid
  require_email "$to"
  [[ -f "$MAIL_EXIM_CONF" ]] || deny "the Email addon is not installed"
  hostname="$(awk -F' = ' '$1 == "primary_hostname" { print $2; exit }' "$MAIL_EXIM_CONF")"
  msgid="spanel-test-$(date +%s)-$(openssl rand -hex 4)@${hostname}"
  printf 'From: SPanel <postmaster@%s>\nTo: <%s>\nSubject: SPanel mail test\nDate: %s\nMessage-ID: <%s>\n\nThis is a test message sent from the SPanel on %s.\n' \
    "$hostname" "$to" "$(date -R)" "$msgid" "$hostname" \
    | exim4 -odq -oi -f "postmaster@${hostname}" -- "$to" >/dev/null 2>&1 || true
  qid="$(grep -a "id=${msgid}" /var/log/exim4/mainlog | tail -n1 | awk '{ print $3 }')"
  [[ -n "$qid" ]] || deny "Exim did not accept the test message"
  # -M delivers now even if an earlier failure set a retry time: a test after
  # fixing a password must try again, not report the old failure.
  timeout 90 exim4 -M "$qid" >/dev/null 2>&1 || true
  # Everything Exim said about it after accepting it: the remote server's own
  # answer ("535 ... not accepted") is on the authenticator lines, not the
  # delivery line.
  grep -a " ${qid} " /var/log/exim4/mainlog | grep -avE " (<=|Completed|removed by)" | tail -n 8 | cut -c1-600
}

mail_box_path() {
  # /home/<user>/mail/<domain>/<name>, checked piece by piece.
  local user="$1" domain="$2" name="$3"
  require_linux_user "$user"
  require_domain "$domain"
  [[ "$name" =~ ^[a-z0-9]([a-z0-9._-]{0,62}[a-z0-9])?$ && "$name" != *..* ]] || deny "invalid mailbox name: $name"
  printf '/home/%s/mail/%s/%s' "$user" "$domain" "$name"
}

mail_delete_box() {
  # The mailbox's mail goes with it. Its parents are root's, so the path
  # cannot be redirected; rm does not follow links inside it.
  local box
  box="$(mail_box_path "$1" "$2" "$3")"
  if [[ -L "$box" ]]; then
    rm -f -- "$box"
  elif [[ -d "$box" ]]; then
    rm -rf --one-file-system -- "$box"
  fi
  rmdir -- "/home/$1/mail/$2" 2>/dev/null || true
  echo "deleted ${3}@${2}"
}

mail_import() {
  # Put a mailbox's mail back from a backup, replacing what it holds now.
  # The staged copy is the panel's; it is copied into a directory only root
  # can enter, made the owner's there, and only then renamed into place, so
  # the owner never gets a directory root is still writing into.
  local user="$1" domain="$2" name="$3" src_arg="$4" box src parent work
  box="$(mail_box_path "$user" "$domain" "$name")"
  case "$src_arg" in
    /var/lib/spanel/import-stage/*) : ;;
    *) deny "staged source must be under /var/lib/spanel/import-stage" ;;
  esac
  [[ ! -L "$src_arg" ]] || deny "staged source cannot be a symlink"
  src=$(readlink -e -- "$src_arg") || deny "staged source not found"
  case "$src/" in
    /var/lib/spanel/import-stage/*/) : ;;
    *) deny "staged source escaped the import staging area" ;;
  esac
  [[ -d "$src" ]] || deny "staged source is not a directory"
  [[ "$(stat -c '%U' -- "$src")" == "spanel" ]] || deny "staged source must be owned by spanel"
  id -u "$user" >/dev/null 2>&1 || deny "no Linux user $user"
  parent="$(dirname "$box")"
  install -d -o root -g spanel -m 0751 "/home/${user}/mail" "$parent"
  work="$(mktemp -d "${parent}/.restore-XXXXXX")"
  chmod 0700 "$work"
  cp -a --no-preserve=ownership -- "$src/." "$work/"
  find "$work" \( -type l -o -type b -o -type c -o -type p -o -type s \) -delete 2>/dev/null || true
  # Modes first and the top directory's owner last: until then only root can
  # enter, so nothing inside can be swapped for a link while root works on it.
  find "$work" -mindepth 1 -type d -exec chmod 2750 {} +
  find "$work" -type f -exec chmod 0640 {} +
  find "$work" -mindepth 1 -exec chown -h "${user}:spanel" {} +
  chown "${user}:spanel" "$work"
  chmod 2750 "$work"
  if [[ -e "$box" || -L "$box" ]]; then
    rm -rf --one-file-system -- "$box"
  fi
  mv -T -- "$work" "$box"
  echo "restored ${name}@${domain}"
}

mail_webmail_host() {
  # webmail.<domain> on ports 80/443, with its own Let's Encrypt certificate.
  local domain="$1" email="${2:-}" name conf args rc=0
  require_domain "$domain"
  name="webmail.${domain}"
  conf="/etc/nginx/conf.d/00-spanel-webmail-${domain}.conf"
  [[ -f "$WEBMAIL_NGINX" ]] || deny "the Email addon is not installed"
  install -d -o root -g spanel -m 0755 /var/www/spanel-acme/.well-known/acme-challenge
  if [[ ! -f "/etc/letsencrypt/live/${name}/fullchain.pem" ]]; then
    cat >"$conf" <<NGINX
${MAIL_MARKER} webmail.${domain}, waiting for its certificate.
server {
    listen 80;
    server_name ${name};
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/spanel-acme;
        default_type text/plain;
        try_files \$uri =404;
    }
    location / { return 404; }
}
NGINX
    nginx_ipv6_apply >/dev/null 2>&1 || true
    if ! nginx -t >/dev/null 2>&1; then
      rm -f "$conf"
      deny "nginx refused the server block for ${name}"
    fi
    systemctl reload nginx
    args=(certonly --webroot -w /var/www/spanel-acme --cert-name "$name" -d "$name"
          --non-interactive --agree-tos --keep-until-expiring --deploy-hook "systemctl reload nginx")
    if [[ -n "$email" ]]; then
      require_email "$email"
      args+=(--email "$email")
    else
      args+=(--register-unsafely-without-email)
    fi
    certbot "${args[@]}" >>"$MAIL_INSTALL_LOG" 2>&1 || rc=$?
    if [[ ! -f "/etc/letsencrypt/live/${name}/fullchain.pem" ]]; then
      rm -f "$conf"
      nginx -t >/dev/null 2>&1 && systemctl reload nginx
      deny "no certificate for ${name} (certbot exited ${rc}) - point ${name} at this server first"
    fi
  fi
  {
    printf '%s webmail.%s\n' "$MAIL_MARKER" "$domain"
    printf 'server {\n    listen 80;\n    server_name %s;\n' "$name"
    printf '    location ^~ /.well-known/acme-challenge/ {\n        root /var/www/spanel-acme;\n        default_type text/plain;\n        try_files $uri =404;\n    }\n'
    printf '    location / { return 301 https://$host$request_uri; }\n}\n'
    printf 'server {\n    listen 443 ssl;\n    server_name %s;\n' "$name"
    printf '    ssl_certificate /etc/letsencrypt/live/%s/fullchain.pem;\n' "$name"
    printf '    ssl_certificate_key /etc/letsencrypt/live/%s/privkey.pem;\n' "$name"
    printf '    ssl_protocols TLSv1.2 TLSv1.3;\n'
    mail_webmail_proxy_block
    printf '}\n'
  } >"$conf"
  nginx_ipv6_apply >/dev/null 2>&1 || true
  if ! nginx -t >/dev/null 2>&1; then
    rm -f "$conf"
    nginx -t >/dev/null 2>&1 && systemctl reload nginx
    deny "nginx refused the server block for ${name}"
  fi
  systemctl reload nginx
  echo "https://${name}"
}

mail_webmail_host_remove() {
  local domain="$1"
  require_domain "$domain"
  rm -f "/etc/nginx/conf.d/00-spanel-webmail-${domain}.conf" "/etc/nginx/conf.d/00-spanel-webmail-${domain}.conf.off"
  if nginx -t >/dev/null 2>&1; then systemctl reload nginx; fi
  echo "removed webmail.${domain}"
}

mail_webmail_hosts() {
  # The domains whose webmail.<domain> is served, one per line.
  local conf domain
  for conf in /etc/nginx/conf.d/00-spanel-webmail-*.conf; do
    [[ -f "$conf" ]] || continue
    grep -q "listen 443 ssl" "$conf" || continue
    domain="${conf#/etc/nginx/conf.d/00-spanel-webmail-}"
    printf '%s\n' "${domain%.conf}"
  done
}

# ---- A mail domain's own hosts: mail., mta-sts., autoconfig., autodiscover. ----
# One Let's Encrypt certificate named mail.<domain> covers the hosts the panel
# turned on (the same webroot flow as webmail.<domain>). nginx serves them;
# Exim and Dovecot answer mail.<domain> with it by SNI (tls_hosts, read by
# spanel-mail-tls, which certbot also runs on every renewal).
MAIL_HOSTS_DOCS="/var/www/spanel-mailhosts"
MAIL_TLS_HOSTS="${MAIL_EXIM_DIR}/tls_hosts"

mail_hosts_conf() {
  printf '/etc/nginx/conf.d/00-spanel-mailhosts-%s.conf' "$1"
}

mail_cert_covers() {
  # Whether the live certificate <name> covers every host in the comma list.
  local cert="/etc/letsencrypt/live/$1/fullchain.pem" host answer
  [[ -f "$cert" ]] || return 1
  for host in ${2//,/ }; do
    answer="$(openssl x509 -in "$cert" -noout -checkhost "$host" 2>/dev/null || true)"
    [[ "$answer" == *"does match"* ]] || return 1
  done
}

mail_hosts_register() {
  # "<host>: <certificate name>" in tls_hosts, or the host taken out ("" name).
  local host="$1" name="$2" tmp
  install -d -o root -g Debian-exim -m 0750 "$MAIL_EXIM_DIR"
  [[ -f "$MAIL_TLS_HOSTS" ]] || install -m 0640 -o root -g Debian-exim /dev/null "$MAIL_TLS_HOSTS"
  tmp="$(mktemp "${MAIL_TLS_HOSTS}.XXXXXX")"
  awk -F': ' -v h="$host" '$1 != h' "$MAIL_TLS_HOSTS" >"$tmp" || true
  [[ -z "$name" ]] || printf '%s: %s\n' "$host" "$name" >>"$tmp"
  chown root:Debian-exim "$tmp"
  chmod 0640 "$tmp"
  mv -f "$tmp" "$MAIL_TLS_HOSTS"
}

mail_hosts_apply() {
  # A mail domain's hosts, as JSON on stdin (checked by the mail tool):
  #   {"domain", "hosts": ["mail", "mta-sts", "autoconfig", "autodiscover"], "email",
  #    "mta_sts": {"mode": "testing"|"enforce"|"none", "max_age"} or null}
  # Issues (or extends) the certificate, writes what the hosts serve and the
  # nginx server blocks, and registers mail.<domain> for SNI. Prints the
  # names served.
  [[ -f "$WEBMAIL_NGINX" ]] || deny "the Email addon is not installed"
  [[ -f "$MAIL_TOOL" ]] || deny "${MAIL_TOOL} is missing - run the SPanel update again"
  local payload domain cert_name email names conf docs tmp host rc=0 had_tls=no
  local -a args
  payload="$(mktemp)"
  head -c 200000 >"$payload"
  # The tool's own sentence says what is wrong with the request.
  read -r domain cert_name email names < <(python3 -I "$MAIL_TOOL" mail-hosts-fields <"$payload") \
    || { rm -f "$payload"; exit 1; }
  require_domain "$domain"
  conf="$(mail_hosts_conf "$domain")"
  docs="${MAIL_HOSTS_DOCS}/${domain}"
  install -d -o root -g spanel -m 0755 /var/www/spanel-acme/.well-known/acme-challenge
  install -d -o root -g root -m 0755 "$MAIL_HOSTS_DOCS" "$docs"
  [[ -f "$conf" ]] && grep -q 'listen 443 ssl' "$conf" && had_tls=yes
  tmp="$(mktemp)"
  if ! mail_cert_covers "$cert_name" "$names"; then
    # Port 80 for the challenge first, for every name.
    python3 -I "$MAIL_TOOL" mailhosts-nginx "$docs" http <"$payload" >"$tmp" || { rm -f "$payload" "$tmp"; deny "could not write the mail hosts' server block"; }
    [[ "$had_tls" == "yes" ]] && cp -a "$conf" "${conf}.previous"
    install -m 0644 "$tmp" "$conf"
    nginx_ipv6_apply >/dev/null 2>&1 || true
    if ! nginx -t >/dev/null 2>&1; then
      if [[ "$had_tls" == "yes" ]]; then mv -f "${conf}.previous" "$conf"; else rm -f "$conf"; fi
      rm -f "$payload" "$tmp"
      deny "nginx refused the server block for the mail hosts of ${domain}"
    fi
    systemctl reload nginx
    args=(certonly --webroot -w /var/www/spanel-acme --cert-name "$cert_name" --expand
          --non-interactive --agree-tos --keep-until-expiring --deploy-hook "$MAIL_TLS_SCRIPT")
    for host in ${names//,/ }; do args+=(-d "$host"); done
    if [[ "$email" != "-" ]]; then
      require_email "$email"
      args+=(--email "$email")
    else
      args+=(--register-unsafely-without-email)
    fi
    certbot "${args[@]}" >>"$MAIL_INSTALL_LOG" 2>&1 || rc=$?
    if ! mail_cert_covers "$cert_name" "$names"; then
      if [[ "$had_tls" == "yes" ]]; then mv -f "${conf}.previous" "$conf"; else rm -f "$conf"; fi
      nginx -t >/dev/null 2>&1 && systemctl reload nginx
      rm -f "$payload" "$tmp"
      deny "no certificate for ${names//,/, } (certbot exited ${rc}) - point each of them at this server first"
    fi
    rm -f "${conf}.previous"
  fi
  # What the hosts serve: the MTA-STS policy, autoconfig and autodiscover.
  python3 -I "$MAIL_TOOL" mailhost-docs "$docs" <"$payload" >/dev/null || { rm -f "$payload" "$tmp"; exit 1; }
  python3 -I "$MAIL_TOOL" mailhosts-nginx "$docs" https <"$payload" >"$tmp" || { rm -f "$payload" "$tmp"; deny "could not write the mail hosts' server block"; }
  cp -a "$conf" "${conf}.previous" 2>/dev/null || true
  install -m 0644 "$tmp" "$conf"
  rm -f "$payload" "$tmp"
  nginx_ipv6_apply >/dev/null 2>&1 || true
  if ! nginx -t >/dev/null 2>&1; then
    if [[ -f "${conf}.previous" ]]; then mv -f "${conf}.previous" "$conf"; else rm -f "$conf"; fi
    nginx -t >/dev/null 2>&1 && systemctl reload nginx
    deny "nginx refused the server block for the mail hosts of ${domain}"
  fi
  rm -f "${conf}.previous"
  systemctl reload nginx
  mail_hosts_register "mail.${domain}" "$cert_name"
  "$MAIL_TLS_SCRIPT" >/dev/null 2>&1 || true
  printf '%s\n' "${names//,/ }"
}

mail_hosts_remove() {
  # The hosts of a mail domain stop being served; their certificate goes.
  local domain="$1"
  require_domain "$domain"
  rm -f "$(mail_hosts_conf "$domain")" "$(mail_hosts_conf "$domain").previous"
  rm -rf --one-file-system -- "${MAIL_HOSTS_DOCS:?}/${domain}"
  if [[ -f "$MAIL_TLS_HOSTS" ]]; then mail_hosts_register "mail.${domain}" ""; fi
  certbot delete --non-interactive --cert-name "mail.${domain}" >>"$MAIL_INSTALL_LOG" 2>&1 || true
  rm -rf --one-file-system -- "${MAIL_TLS_DIR:?}/mail.${domain}"
  "$MAIL_TLS_SCRIPT" >/dev/null 2>&1 || true
  if nginx -t >/dev/null 2>&1; then systemctl reload nginx; fi
  echo "mail hosts of ${domain} removed"
}

fail2ban_status() {
  if ! pkg_installed fail2ban && ! command -v fail2ban-client >/dev/null 2>&1 && ! [[ -x /usr/bin/fail2ban-client ]]; then
    echo "installed=no"; echo "running=no"; echo "jails="; echo "banned=0"; echo "banaction="
    return 0
  fi
  echo "installed=yes"
  local is_running=no
  if systemctl is-active --quiet fail2ban 2>/dev/null || pgrep -f fail2ban-server >/dev/null 2>&1; then
    is_running=yes
  fi
  echo "running=${is_running}"
  local banact=""
  if [[ -f "$FAIL2BAN_JAIL_LOCAL" ]]; then
    banact="$(awk -F= '/^banaction[[:space:]]*=/ {gsub(/[ \t]/,"",$2); print $2; exit}' "$FAIL2BAN_JAIL_LOCAL" 2>/dev/null || true)"
  fi
  [[ -z "$banact" ]] && banact="iptables-multiport"
  echo "banaction=${banact}"

  local jails="" banned=0 total_failed=0 jail count fcount
  jails="$(fail2ban-client status 2>/dev/null | awk -F: '/Jail list/ {gsub(/[ \t]/,"",$2); print $2}' || true)"
  if [[ -z "$jails" && "$is_running" == "yes" ]]; then
    jails="sshd"
  fi
  echo "jails=${jails}"
  for jail in ${jails//,/ }; do
    count="$(fail2ban-client status "$jail" 2>/dev/null | awk -F: '/Currently banned/ {gsub(/[ \t]/,"",$2); print $2}' || true)"
    if [[ "$count" =~ ^[0-9]+$ ]]; then
      banned=$((banned + count))
    fi
    fcount="$(fail2ban-client status "$jail" 2>/dev/null | awk -F: '/Total failed/ {gsub(/[ \t]/,"",$2); print $2}' || true)"
    if [[ "$fcount" =~ ^[0-9]+$ ]]; then
      total_failed=$((total_failed + fcount))
    fi
  done
  echo "banned=${banned}"
  if [[ "$is_running" == "yes" ]]; then
    local saved_rules=""
    saved_rules="$(iptables-save 2>/dev/null || true)"
    if [[ "$saved_rules" == *"f2b-"* || "$saved_rules" == *"fail2ban"* ]] || fail2ban_ban_reaches_the_kernel; then
      echo "bans_reach_kernel=yes"
    else
      echo "bans_reach_kernel=no"
    fi
  else
    echo "bans_reach_kernel=no"
  fi
  echo "ssh_unit=$(fail2ban_ssh_unit || echo ssh.service)"
  if fail2ban_filter_sees_the_journal; then
    echo "filter_sees_journal=yes"
  else
    echo "filter_sees_journal=no"
  fi
  echo "total_failed=${total_failed}"
}

fail2ban_banned_list() {
  local jail
  for jail in ${1//,/ }; do
    fail2ban-client status "$jail" 2>/dev/null \
      | awk -F: '/Banned IP list/ {gsub(/^[ \t]+/,"",$2); print $2}' \
      | tr ' ' '\n' | grep -E '^[0-9a-fA-F:.]+$' || true
  done
}

# --- Linux Malware Detect (LMD / maldet) ------------------------------------
MALDET_BIN="/usr/local/sbin/maldet"
MALDET_HOME="/usr/local/maldetect"
MALDET_CONF="${MALDET_HOME}/conf.maldet"
MALDET_TARBALL_URL="https://www.rfxn.com/downloads/maldetect-current.tar.gz"

maldet_write_conf() {
  # Panel-owned settings on top of whatever the rfxn installer shipped. The
  # panel drives scheduling and never auto-quarantines, so its cron is off.
  [[ -f "$MALDET_CONF" ]] || return 0
  local key val kv
  for kv in \
    "quarantine_hits=0" "quarantine_clean=0" "quarantine_suspend_user=0" \
    "scan_clamscan=1" "scan_ignore_root=0" "scan_find_h10k_alert=0" \
    "autoupdate_signatures=1" "autoupdate_version=1" "cron_daily_scan=0" \
    "email_alert=0" "default_monitor_mode=users"
  do
    key="${kv%%=*}"; val="${kv#*=}"
    if grep -qE "^${key}=" "$MALDET_CONF"; then
      sed -i -E "s#^${key}=.*#${key}=\"${val}\"#" "$MALDET_CONF"
    else
      printf '%s="%s"\n' "$key" "$val" >>"$MALDET_CONF"
    fi
  done
  # The panel owns the schedule; disarm the installer's daily cron.
  [[ -f /etc/cron.daily/maldet ]] && chmod a-x /etc/cron.daily/maldet
  maldet_write_ignores
  return 0
}

# Paths a server scan must not walk. A scan of "/" otherwise reads the scan
# engine's own signature database, and a signature database is a file full of
# malware patterns: /var/lib/clamav/rfxn.yara was reported INFECTED by every
# server scan because it is the file that defines what "infected" means. The
# rest is storage rather than code - InnoDB pages and compressed archives are
# never executed and the engine cannot usefully read inside either, so they
# only add gigabytes of I/O and false matches on stored text.
MALDET_IGNORE_PATHS=(
  /usr/local/maldetect
  /usr/local/sbin/maldet
  /var/lib/clamav
  /var/lib/mysql
  /var/lib/spanel/backups
  /proc
  /sys
  /dev
  /run
)

maldet_write_ignores() {
  # Append-only: an admin's own entries are never removed, and re-running this
  # on every update is a no-op once the paths are present.
  local f="${MALDET_HOME}/ignore_paths" p
  [[ -d "$MALDET_HOME" ]] || return 0
  for p in "${MALDET_IGNORE_PATHS[@]}" /home/*/spanel_backups; do
    [[ "$p" == /home/*/spanel_backups && ! -d "$p" ]] && continue
    grep -qxF "$p" "$f" 2>/dev/null || printf '%s\n' "$p" >>"$f"
  done
  return 0
}

install_maldet_engine() {
  export DEBIAN_FRONTEND=noninteractive
  # clamscan is the scan engine; the resident daemon is deliberately not
  # enabled - maldet runs clamscan one-shot so the ~1.3GB of signatures are
  # only resident during a scan.
  if ! command -v clamscan >/dev/null 2>&1; then
    apt-get update --allow-releaseinfo-change
    apt-get install -y clamav || deny "could not install the clamav package (scan engine)"
  fi
  freshclam >/dev/null 2>&1 || true
  command -v wget >/dev/null 2>&1 || command -v curl >/dev/null 2>&1 || apt-get install -y wget
  # inotifywait is what maldet's Level 2 monitor runs; Debian does not ship it.
  command -v inotifywait >/dev/null 2>&1 || apt-get install -y inotify-tools || true

  if [[ ! -x "$MALDET_BIN" ]]; then
    local tmp tarball
    tmp="$(mktemp -d /tmp/spanel-maldet.XXXXXX)"
    tarball="${tmp}/maldetect.tar.gz"
    if command -v wget >/dev/null 2>&1; then
      wget -q --timeout=30 -O "$tarball" "$MALDET_TARBALL_URL" || { rm -rf "$tmp"; deny "could not download LMD from rfxn.com (offline? use the panel button later)"; }
    else
      curl -fsSL --connect-timeout 15 --max-time 120 "$MALDET_TARBALL_URL" -o "$tarball" || { rm -rf "$tmp"; deny "could not download LMD from rfxn.com (offline? use the panel button later)"; }
    fi
    [[ "$(file -b "$tarball" 2>/dev/null)" == *[Gg]zip* ]] || { rm -rf "$tmp"; deny "the LMD download is not a gzip archive"; }
    tar -xzf "$tarball" -C "$tmp" || { rm -rf "$tmp"; deny "could not unpack the LMD archive"; }
    local srcdir
    srcdir="$(find "$tmp" -maxdepth 1 -type d -name 'maldetect-*' | head -n1)"
    [[ -n "$srcdir" && -x "$srcdir/install.sh" ]] || { rm -rf "$tmp"; deny "LMD archive layout not recognised"; }
    ( cd "$srcdir" && ./install.sh ) || { rm -rf "$tmp"; deny "the LMD installer failed"; }
    rm -rf "$tmp"
  fi
  [[ -x "$MALDET_BIN" ]] || deny "maldet is still not present after install"
  maldet_write_conf
  # The rfxn installer enables maldet.service (the inotify monitor). SPanel
  # owns Level 2: keep it off until the admin turns it on.
  systemctl disable --now maldet >/dev/null 2>&1 || true
  "$MALDET_BIN" -u --force >/dev/null 2>&1 || true
  echo "LMD installed at ${MALDET_HOME}; ClamAV engine present (daemon not enabled)."
}

maldet_monitor_running() {
  local pid
  if [[ -f "${MALDET_HOME}/tmp/inotifywait.pid" ]]; then
    pid="$(cat "${MALDET_HOME}/tmp/inotifywait.pid" 2>/dev/null)"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && return 0
  fi
  # Fallback: any inotifywait watching /home is maldet's monitor.
  pgrep -f "inotifywait.*(/home|maldet)" >/dev/null 2>&1
}

write_inotify_sysctl() {
  cat >/etc/sysctl.d/60-spanel-inotify.conf <<'SYSCTL'
# Raised by SPanel so the LMD real-time monitor can watch every site file.
fs.inotify.max_user_watches = 524288
fs.inotify.max_user_instances = 1024
SYSCTL
  sysctl --system >/dev/null 2>&1 || true
}

run_maldet_scan() {
  # run_maldet_scan <job-id> <all|recent> <days> <path>...
  local job="$1" mode="$2" days="$3"; shift 3
  [[ "$job" =~ ^[0-9a-f]{8,64}$ ]] || deny "invalid scan job id"
  [[ "$mode" == "all" || "$mode" == "recent" ]] || deny "scan mode must be all|recent"
  [[ "$days" =~ ^[0-9]{1,4}$ ]] || deny "scan days must be an integer"
  [[ -x "$MALDET_BIN" ]] || deny "maldet is not installed"
  install -d -o spanel -g spanel -m 0750 "$MALWARE_JOBS_DIR"
  local out="${MALWARE_JOBS_DIR}/${job}.maldet.out"
  local rep="${MALWARE_JOBS_DIR}/${job}.maldet.report"
  rm -f "$out" "$rep"

  local -a targets=()
  local p resolved
  for p in "$@"; do
    resolved="$(readlink -m -- "$p")"
    case "$resolved" in
      /) targets+=("/") ;;
      /home|/home/*) [[ -d "$resolved" ]] && targets+=("$resolved") ;;
      *) deny "scan path must be / or under /home: $resolved" ;;
    esac
  done
  [[ ${#targets[@]} -gt 0 ]] || deny "no valid scan path"

  local -a co=()
  # A whole-machine scan skips the kernel/pkg-cache noise; a /home scan does not
  # need it. maldet takes one -co per override.
  if [[ " ${targets[*]} " == *" / "* ]]; then
    local ignore
    ignore="$(printf '%s\n' "${MALWARE_SCAN_PRUNE[@]}" | paste -sd, -)"
    co=(-co "scan_ignore=${ignore}")
  fi

  # Foreground on purpose: -b daemonises and the helper would return before the
  # report exists. The panel already runs this call in a background job.
  local rc=0
  if [[ "$mode" == "recent" ]]; then
    nice -n 19 ionice -c3 "$MALDET_BIN" "${co[@]}" -r "${targets[0]}" "$days" >"$out" 2>&1 || rc=$?
  else
    nice -n 19 ionice -c3 "$MALDET_BIN" "${co[@]}" -a "${targets[@]}" >"$out" 2>&1 || rc=$?
  fi

  local scanid
  scanid="$(grep -oE '[0-9]{6}-[0-9]{4}\.[0-9]+' "$out" | head -n1)"
  if [[ -z "$scanid" && -f "${MALDET_HOME}/sess/session.last" ]]; then
    scanid="$(cat "${MALDET_HOME}/sess/session.last" 2>/dev/null)"
  fi
  # The report body is the session file maldet wrote, not `maldet -e` output.
  : >"$rep"
  if [[ -n "$scanid" && -f "${MALDET_HOME}/sess/session.${scanid}" ]]; then
    cat "${MALDET_HOME}/sess/session.${scanid}" >>"$rep"
  fi
  chown spanel:spanel "$out" "$rep" 2>/dev/null || true
  chmod 0640 "$out" "$rep" 2>/dev/null || true
  printf 'scanid=%s\n' "${scanid:-none}"
  printf 'exit=%s\n' "$rc"
}

maldet_scan_progress() {
  # maldet_scan_progress <job-id>: how far a running maldet scan has got.
  # With the ClamAV engine maldet prints nothing between "scan ... in
  # progress" and the end - four hours on a whole server - so the panel sat at
  # 0%. clamscan reads maldet's file list one line at a time; the read offset
  # of that list, in /proc/<pid>/fdinfo (root only), says how many files are
  # behind it. Prints numbers only, never a path.
  local job="$1"
  [[ "$job" =~ ^[0-9a-f]{8,64}$ ]] || deny "invalid scan job id"
  local out="${MALWARE_JOBS_DIR}/${job}.maldet.out"
  local stage="none" total="" scanned=0 pid="" list clam fd pos
  if [[ -f "$out" ]]; then
    stage="starting"
    grep -q 'building file list' "$out" && stage="listing"
    total="$(grep -oE 'found [0-9]+ files' "$out" | tail -n1 | tr -dc '0-9' || true)"
    [[ -n "$total" ]] && stage="scanning"
    grep -qE 'processing scan results|scan completed' "$out" && stage="results"
    pid="$(grep -oE '^maldet\([0-9]+\)' "$out" | head -n1 | tr -dc '0-9' || true)"
  fi
  if [[ "$stage" == "scanning" && -n "$pid" ]]; then
    list="${MALDET_HOME}/tmp/.find.${pid}"
    for clam in $(pgrep -P "$pid" -x clamscan 2>/dev/null || true); do
      for fd in /proc/"$clam"/fd/*; do
        [[ "$(readlink "$fd" 2>/dev/null || true)" == "$list" ]] || continue
        pos="$(awk '/^pos:/ {print $2}' "/proc/${clam}/fdinfo/${fd##*/}" 2>/dev/null || true)"
        if [[ "$pos" =~ ^[0-9]+$ ]]; then
          scanned="$(head -c "$pos" "$list" 2>/dev/null | wc -l || true)"
        fi
      done
    done
  fi
  printf 'stage=%s\ntotal=%s\nscanned=%s\n' "$stage" "${total:-0}" "${scanned:-0}"
}

# The PHP extensions the panel offers to install, one apt package each
# (php<version>-<name>). The list is the boundary, not a convenience: the same
# repository carries php<v>-cgi and libapache2-mod-php<v>, which bring Apache
# and a second web server onto the ports nginx owns. backend/app/services/php.py
# keeps the same list for the page; a test holds the two together.
PHP_EXTENSION_WHITELIST=(apcu bcmath bz2 curl enchant gd gmp igbinary imagick imap intl ldap mailparse mbstring memcached mongodb msgpack mysql opcache pgsql redis soap sqlite3 ssh2 tidy uuid xml xsl yaml zip)

install_php_extension() {
  # install_php_extension <version> <extension>
  local version="$1" ext="$2"
  require_php_version "$version"
  [[ "$ext" =~ ^[a-z0-9]{2,20}$ ]] || deny "invalid PHP extension name"
  [[ " ${PHP_EXTENSION_WHITELIST[*]} " == *" ${ext} "* ]] || deny "PHP extension not offered by the panel: $ext"
  [[ -f "/etc/php/${version}/fpm/php-fpm.conf" ]] || deny "PHP ${version} is not installed"
  local pkg="php${version}-${ext}"
  export DEBIAN_FRONTEND=noninteractive
  if pkg_installed "$pkg"; then
    echo "$pkg is already installed."
  else
    if ! apt-cache show "$pkg" >/dev/null 2>&1; then
      apt-get update --allow-releaseinfo-change >/dev/null 2>&1 || true
      apt-cache show "$pkg" >/dev/null 2>&1 || deny "$pkg is not in the package repositories"
    fi
    apt-get install -y "$pkg" || deny "apt-get could not install $pkg"
    # Out of autoremove's reach, as install.sh does for the default set.
    apt-mark manual "$pkg" >/dev/null 2>&1 || true
  fi
  # A module whose .so did not land is reported, not hidden: PHP would print
  # "Unable to load dynamic library" on every run.
  # Read whole, then matched: `| grep -q` exits at the first match and, under
  # pipefail, the SIGPIPE it leaves php with would turn "found" into "not".
  local load_errors
  load_errors="$("php${version}" -v 2>&1 | grep -i 'unable to load' || true)"
  if [[ -n "$load_errors" ]]; then
    head -n 3 <<<"$load_errors" >&2
    deny "$pkg is installed but PHP ${version} cannot load it"
  fi
  systemctl reload "php${version}-fpm" 2>/dev/null || systemctl restart "php${version}-fpm"
  reload_user_php_fpm_masters "$version"
  echo "$pkg is ready; php${version}-fpm reloaded."
}

install_php_version() {
  local version="$1"
  export DEBIAN_FRONTEND=noninteractive
  require_php_version "$version"
  if [[ -f /etc/php/"$version"/fpm/php-fpm.conf ]]; then
    echo "PHP $version is already installed; ensuring SPanel extension set..."
  fi
  if ! apt-cache show "php${version}-fpm" >/dev/null 2>&1; then
    if ! grep -q "ondrej/php" /etc/apt/sources.list.d/*.list 2>/dev/null; then
      echo "Adding ondrej/php PPA for PHP $version..."
      apt-get update --allow-releaseinfo-change
      apt-get install -y software-properties-common || true
      add-apt-repository -y ppa:ondrej/php 2>/dev/null || true
    fi
    apt-get update --allow-releaseinfo-change
  fi
  echo "Installing PHP $version..."
  local packages=(
    "php${version}-fpm"
    "php${version}-cli"
    "php${version}-mysql"
    "php${version}-sqlite3"
    "php${version}-curl"
    "php${version}-gd"
    "php${version}-mbstring"
    "php${version}-xml"
    "php${version}-zip"
    "php${version}-opcache"
    "php${version}-intl"
    "php${version}-bcmath"
    "php${version}-redis"
    "php${version}-imagick"
    "php${version}-imap"
  )
  local available_packages=() missing_packages=() package
  for package in "${packages[@]}"; do
    if apt-cache show "$package" >/dev/null 2>&1; then
      available_packages+=("$package")
    else
      missing_packages+=("$package")
    fi
  done
  if [[ ${#missing_packages[@]} -gt 0 ]]; then
    echo "Skipping PHP packages not available in repo: ${missing_packages[*]}"
  fi
  [[ ${#available_packages[@]} -gt 0 ]] || deny "No package found for PHP ${version}"
  apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install -y "${available_packages[@]}" || { echo "Failed to install PHP $version"; return 1; }
  install_ioncube_loader "$version"
  # Enable and start/reload PHP-FPM
  systemctl enable "php${version}-fpm" 2>/dev/null || true
  if systemctl is-active --quiet "php${version}-fpm" 2>/dev/null; then
    "php-fpm${version}" -t >/dev/null 2>&1 && systemctl reload "php${version}-fpm" 2>/dev/null || systemctl restart "php${version}-fpm" 2>/dev/null || true
  else
    systemctl start "php${version}-fpm" 2>/dev/null || true
  fi
  echo "PHP $version installed successfully"
}

upgrade_php_version() {
  local version="$1"
  export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none
  require_php_version "$version"
  [[ -f "/etc/php/${version}/fpm/php-fpm.conf" ]] || deny "PHP ${version} is not installed"
  apt-get update --allow-releaseinfo-change -qq >/dev/null 2>&1 || true
  local installed_pkgs=() pkg
  while IFS= read -r pkg; do
    [[ -n "$pkg" ]] && installed_pkgs+=("$pkg")
  done < <(dpkg-query -W -f='${Package}\t${Status}\n' "php${version}-*" "php${version}" 2>/dev/null | awk -F'\t' '$2 == "install ok installed" {print $1}')
  if [[ ${#installed_pkgs[@]} -gt 0 ]]; then
    apt-mark unhold "${installed_pkgs[@]}" >/dev/null 2>&1 || true
    apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install --only-upgrade -y "${installed_pkgs[@]}" || \
      deny "Failed to upgrade PHP ${version} packages"
  fi
  if command -v "php-fpm${version}" >/dev/null 2>&1; then
    "php-fpm${version}" -t >/dev/null 2>&1 || deny "php-fpm${version} configuration test failed after upgrade"
  fi
  if systemctl is-active --quiet "php${version}-fpm" 2>/dev/null; then
    systemctl reload "php${version}-fpm" 2>/dev/null || systemctl restart "php${version}-fpm"
  else
    systemctl start "php${version}-fpm" 2>/dev/null || true
  fi
  reload_user_php_fpm_masters "$version"
  echo "PHP ${version} packages safely upgraded and php${version}-fpm reloaded"
}

harden_php_security() {
  local version="$1" mode="${2:-strict}" conf_dir target tmp disabled_funcs
  require_php_version "$version"
  conf_dir="/etc/php/${version}/fpm/conf.d"
  [[ -d "$conf_dir" ]] || deny "PHP FPM config directory not found: $conf_dir"
  target="${conf_dir}/98-spanel-security.ini"
  case "$mode" in
    off|disable|disabled|0)
      rm -f -- "$target"
      if command -v "php-fpm${version}" >/dev/null 2>&1; then
        "php-fpm${version}" -t >/dev/null 2>&1 || true
      fi
      systemctl reload "php${version}-fpm" 2>/dev/null || systemctl restart "php${version}-fpm" 2>/dev/null || true
      reload_user_php_fpm_masters "$version"
      echo "PHP ${version} anti-PHP2SHELL hardening disabled"
      return 0
      ;;
    balanced)
      disabled_funcs="passthru,system,pcntl_exec,pcntl_fork,posix_kill,posix_mkfifo,posix_setpgid,posix_setsid,posix_setuid,show_source,highlight_file"
      ;;
    strict|on|enable|enabled|1|*)
      disabled_funcs="exec,passthru,shell_exec,system,proc_open,popen,pcntl_exec,pcntl_fork,posix_kill,posix_mkfifo,posix_setpgid,posix_setsid,posix_setuid,show_source,highlight_file"
      ;;
  esac
  tmp="$(mktemp "${conf_dir}/.98-spanel-security.ini.XXXXXX")" || deny "cannot create temporary PHP security config"
  cat >"$tmp" <<INI
; Managed by SPanel Smart Security Guard (Anti-PHP2SHELL & RCE Hardening)
; Applied to PHP-FPM web pools only; PHP-CLI stays unrestricted for Composer/WP-CLI.
cgi.fix_pathinfo = 0
expose_php = Off
allow_url_include = Off
enable_dl = Off
disable_functions = ${disabled_funcs}
INI
  chown root:root "$tmp"
  chmod 0644 "$tmp"
  mv -f -- "$tmp" "$target"
  if command -v "php-fpm${version}" >/dev/null 2>&1; then
    if ! "php-fpm${version}" -t >/dev/null 2>&1; then
      rm -f -- "$target"
      deny "php-fpm${version} configuration test failed when applying security hardening"
    fi
  fi
  if systemctl is-active --quiet "php${version}-fpm" 2>/dev/null; then
    systemctl reload "php${version}-fpm" 2>/dev/null || systemctl restart "php${version}-fpm"
  fi
  reload_user_php_fpm_masters "$version"
  echo "PHP ${version} anti-PHP2SHELL hardening (${mode}) applied: ${target}"
}

uninstall_php_version() {
  local version="$1"
  export DEBIAN_FRONTEND=noninteractive
  require_php_version "$version"
  if [[ "$version" == "8.4" ]]; then
    deny "cannot uninstall default PHP version 8.4"
  fi
  if [[ ! -f /etc/php/"$version"/fpm/php-fpm.conf ]] && ! dpkg -l "php${version}-*" >/dev/null 2>&1; then
    echo "PHP $version is not installed; nothing to remove."
    return 0
  fi
  shopt -s nullglob
  local pools=(/etc/php/"$version"/fpm/pool.d/spanel-*.conf "$SPANEL_PHP_FPM_ROOT/$version"/*/pool.d/spanel-*.conf)
  shopt -u nullglob
  if [[ ${#pools[@]} -gt 0 ]]; then
    deny "cannot uninstall PHP ${version}: ${#pools[@]} website pool(s) still use this PHP version"
  fi
  echo "Stopping php${version}-fpm..."
  systemctl disable --now "php${version}-fpm" 2>/dev/null || true
  # Per-account masters of this version have no pool left (checked above);
  # anything still registered is leftover.
  local leftover
  for leftover in "$SPANEL_PHP_FPM_ROOT/$version"/*/; do
    [[ -d "$leftover" ]] || continue
    systemctl disable --now "$(user_php_fpm_unit "$version" "$(basename "$leftover")")" >/dev/null 2>&1 || true
  done
  rm -rf -- "${SPANEL_PHP_FPM_ROOT:?}/${version}" "${SYSTEMD_UNIT_DIR}/spanel-php${version}-fpm@.service" "${SYSTEMD_UNIT_DIR}/spanel-php${version}-fpm@"*.service.d
  systemctl daemon-reload >/dev/null 2>&1 || true
  echo "Removing PHP $version packages..."
  local installed_pkgs=() pkg
  while IFS= read -r pkg; do
    [[ -n "$pkg" ]] && installed_pkgs+=("$pkg")
  done < <(dpkg-query -W -f='${Package}\t${Status}\n' "php${version}-*" "php${version}" 2>/dev/null | awk -F'\t' '$2 == "install ok installed" {print $1}')
  if [[ ${#installed_pkgs[@]} -gt 0 ]]; then
    apt-get remove --purge -y "${installed_pkgs[@]}" || deny "Failed to uninstall PHP $version"
  fi
  rm -rf "/etc/php/${version}"
  echo "PHP $version uninstalled successfully"
}

install_ioncube_loader() {
  local version="$1" arch url tmp archive loader target_dir target loader_ini_dir
  require_php_version "$version"
  arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  case "$arch" in
    amd64|x86_64)
      url="https://downloads.ioncube.com/loader_downloads/ioncube_loaders_lin_x86-64.tar.gz"
      ;;
    *)
      echo "Skipping ionCube Loader: unsupported architecture ${arch}"
      return 0
      ;;
  esac

  apt-get install -y ca-certificates curl tar >/dev/null
  tmp="$(mktemp -d)" || deny "cannot create ionCube temporary directory"
  archive="${tmp}/ioncube_loaders.tar.gz"
  if ! curl -fsSL --connect-timeout 10 --max-time 300 "$url" -o "$archive"; then
    rm -rf -- "$tmp"
    deny "failed to download ionCube Loader"
  fi
  if ! tar -xzf "$archive" -C "$tmp"; then
    rm -rf -- "$tmp"
    deny "failed to unpack ionCube Loader"
  fi
  loader="${tmp}/ioncube/ioncube_loader_lin_${version}.so"
  if [[ ! -f "$loader" ]]; then
    rm -rf -- "$tmp"
    echo "Skipping ionCube Loader: no loader found for PHP ${version}"
    return 0
  fi

  target_dir="/usr/local/ioncube"
  target="${target_dir}/ioncube_loader_lin_${version}.so"
  install -d -o root -g root -m 0755 "$target_dir"
  install -m 0644 -o root -g root "$loader" "$target"
  rm -rf -- "$tmp"

  for loader_ini_dir in /etc/php/"$version"/cli/conf.d /etc/php/"$version"/fpm/conf.d; do
    [[ -d "$loader_ini_dir" ]] || continue
    printf 'zend_extension=%s\n' "$target" >"${loader_ini_dir}/00-ioncube.ini"
    chown root:root "${loader_ini_dir}/00-ioncube.ini"
    chmod 0644 "${loader_ini_dir}/00-ioncube.ini"
  done

  if command -v "php${version}" >/dev/null 2>&1; then
    if ! grep -qi 'ionCube' <<<"$("php${version}" -v 2>&1 || true)"; then
      rm -f /etc/php/"$version"/cli/conf.d/00-ioncube.ini /etc/php/"$version"/fpm/conf.d/00-ioncube.ini
      deny "ionCube Loader failed to load for PHP ${version}"
    fi
  fi
  echo "ionCube Loader enabled for PHP ${version}"
}

validate_php_config_file() {
  local file="$1" line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    case "$line" in *$'\r'*) deny "PHP config contains a carriage return" ;; esac
    [[ "$line" == *"="* ]] || deny "invalid PHP config line: $line"
    key="$(printf '%s' "${line%%=*}" | xargs)"
    value="$(printf '%s' "${line#*=}" | xargs)"
    case "$key" in
      display_errors)
        [[ "$value" == "On" || "$value" == "Off" ]] || deny "invalid display_errors value"
        ;;
      memory_limit|upload_max_filesize|post_max_size)
        [[ "$value" =~ ^[0-9]{1,6}[KMG]?$ ]] || deny "invalid PHP size value for $key"
        ;;
      max_execution_time|max_input_time)
        [[ "$value" =~ ^[0-9]{1,4}$ ]] || deny "invalid integer value for $key"
        (( 10#$value >= 1 && 10#$value <= 3600 )) || deny "$key out of range"
        ;;
      max_input_vars)
        [[ "$value" =~ ^[0-9]{1,7}$ ]] || deny "invalid integer value for $key"
        (( 10#$value >= 100 && 10#$value <= 1000000 )) || deny "max_input_vars out of range"
        ;;
      *)
        deny "unsupported PHP config directive: $key"
        ;;
    esac
  done <"$file"
}

validate_php_tune_file() {
  # A separate allowlist from the panel's PHP config page: these are the keys
  # the tuner is allowed to size from the machine, and nothing else reaches a
  # file that root writes into PHP's configuration directory.
  local file="$1" line key value
  while IFS= read -r line; do
    line="${line%%;*}"
    line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -n "$line" ]] || continue
    [[ "$line" == *=* ]] || deny "invalid PHP tuning line: $line"
    key="$(printf '%s' "${line%%=*}" | sed 's/[[:space:]]*$//')"
    value="$(printf '%s' "${line#*=}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    case "$key" in
      memory_limit|realpath_cache_size)
        [[ "$value" =~ ^[0-9]{1,6}[KkMmGg]?$ ]] || deny "invalid size for $key"
        ;;
      realpath_cache_ttl|opcache.memory_consumption|opcache.interned_strings_buffer|opcache.max_accelerated_files|opcache.revalidate_freq)
        [[ "$value" =~ ^[0-9]{1,7}$ ]] || deny "invalid integer for $key"
        ;;
      opcache.enable|opcache.enable_cli|opcache.validate_timestamps|opcache.save_comments)
        [[ "$value" =~ ^[01]$ ]] || deny "$key must be 0 or 1"
        ;;
      opcache.jit)
        # Named modes, or the four-digit form PHP also accepts.
        [[ "$value" =~ ^(disable|off|on|tracing|function|[0-9]{4})$ ]] || deny "invalid opcache.jit value"
        ;;
      opcache.jit_buffer_size)
        [[ "$value" =~ ^[0-9]{1,6}[KkMmGg]?$ ]] || deny "invalid size for opcache.jit_buffer_size"
        ;;
      expose_php|zlib.output_compression)
        [[ "$value" =~ ^(On|Off|0|1)$ ]] || deny "$key must be On or Off"
        ;;
      *)
        deny "unsupported PHP tuning directive: $key"
        ;;
    esac
  done <"$file"
}

write_php_tune() {
  local version="$1" conf_dir target tmp size
  require_php_version "$version"
  conf_dir="/etc/php/${version}/fpm/conf.d"
  [[ -d "$conf_dir" ]] || deny "PHP FPM config directory not found: $conf_dir"
  target="${conf_dir}/95-spanel-tune.ini"
  tmp="$(mktemp "${conf_dir}/.95-spanel-tune.ini.XXXXXX")" || deny "cannot create temporary PHP tuning file"
  if ! cat >"$tmp"; then
    rm -f -- "$tmp"
    deny "failed to read PHP tuning file"
  fi
  size="$(wc -c <"$tmp" | tr -d '[:space:]')"
  if (( size <= 0 || size > 8192 )); then
    rm -f -- "$tmp"
    deny "PHP tuning file size out of range"
  fi
  validate_php_tune_file "$tmp"
  chown root:root "$tmp"
  chmod 0644 "$tmp"
  mv -f -- "$tmp" "$target"
  # The CLI reads its own directory; opcache settings there are harmless and
  # realpath cache helps WP-CLI too.
  if [[ -d "/etc/php/${version}/cli/conf.d" ]]; then
    install -m 0644 -o root -g root "$target" "/etc/php/${version}/cli/conf.d/95-spanel-tune.ini"
  fi
  systemctl reload "php${version}-fpm" 2>/dev/null || systemctl restart "php${version}-fpm"
  reload_user_php_fpm_masters "$version"
  echo "PHP ${version} tuned: ${target}"
}

write_php_opcache_switch() {
  # Its own file, read after the tuning one and before the administrator's:
  # regenerating the tuning file must not turn opcache back on for somebody who
  # turned it off deliberately.
  local version="$1" enabled="$2" conf_dir target
  require_php_version "$version"
  [[ "$enabled" =~ ^[01]$ ]] || deny "opcache switch must be 0 or 1"
  conf_dir="/etc/php/${version}/fpm/conf.d"
  [[ -d "$conf_dir" ]] || deny "PHP FPM config directory not found: $conf_dir"
  target="${conf_dir}/96-spanel-opcache.ini"
  cat >"$target" <<INI
; Generated by SPanel. OPcache on/off for PHP ${version}.
; Read after 95-spanel-tune.ini, so this wins over the tuner.
opcache.enable = ${enabled}
INI
  chown root:root "$target"
  chmod 0644 "$target"
  if [[ -d "/etc/php/${version}/cli/conf.d" ]]; then
    install -m 0644 -o root -g root "$target" "/etc/php/${version}/cli/conf.d/96-spanel-opcache.ini"
  fi
  systemctl reload "php${version}-fpm" 2>/dev/null || systemctl restart "php${version}-fpm"
  reload_user_php_fpm_masters "$version"
  echo "OPcache PHP ${version}: $([[ "$enabled" == "1" ]] && echo enabled || echo disabled)"
}

write_php_config() {
  local version="$1" conf_dir target tmp size
  require_php_version "$version"
  conf_dir="/etc/php/${version}/fpm/conf.d"
  target="${conf_dir}/99-spanel.ini"
  [[ -d "$conf_dir" ]] || deny "PHP FPM config directory not found: $conf_dir"
  tmp="$(mktemp "${conf_dir}/.99-spanel.ini.XXXXXX")" || deny "cannot create temporary PHP config"
  if ! cat >"$tmp"; then
    rm -f -- "$tmp"
    deny "failed to read PHP config"
  fi
  size="$(wc -c <"$tmp" | tr -d '[:space:]')"
  if (( size <= 0 || size > 8192 )); then
    rm -f -- "$tmp"
    deny "PHP config size out of range"
  fi
  validate_php_config_file "$tmp"
  chown root:root "$tmp"
  chmod 0644 "$tmp"
  mv -f -- "$tmp" "$target"
  systemctl restart "php${version}-fpm"
  reload_user_php_fpm_masters "$version"
  echo "PHP ${version} config updated: ${target}"
}

waf_status() {
  echo "ModSecurity module:"
  if grep -qi modsecurity <<<"$(nginx -V 2>&1 || true)" || [[ -e /etc/nginx/modules-enabled/50-mod-http-modsecurity.conf ]]; then
    echo "  installed"
  else
    echo "  not installed"
  fi
  echo "Rules file:"
  [[ -f /etc/nginx/modsec/spanel-main.conf ]] && echo "  /etc/nginx/modsec/spanel-main.conf" || echo "  missing"
  echo "Default rules:"
  [[ -f /etc/nginx/modsec/spanel-default.conf ]] && echo "  /etc/nginx/modsec/spanel-default.conf" || echo "  missing"
  echo "Custom rules:"
  [[ -f /etc/nginx/modsec/spanel-custom.conf ]] && echo "  /etc/nginx/modsec/spanel-custom.conf" || echo "  missing"
  echo "Managed profile:"
  echo "  SPanel built-in lightweight WordPress/Laravel/PHP rules"
  echo "Timers:"
  systemctl list-timers apt-daily-upgrade.timer --no-pager 2>/dev/null || true
}

audit_log() {
  local quoted="" arg
  for arg in "$@"; do
    printf -v quoted '%s %q' "$quoted" "$arg"
  done
  if command -v logger >/dev/null 2>&1; then
    logger -t spanel-helper -- "cmd=${cmd:-unknown}${quoted}"
  fi
}

##############################################################################
# Firewall engine: iptables + ipset
#
# Single source of truth is $FIREWALL_RULES_FILE (TSV) plus the parsed URL
# blocklist in $FIREWALL_BLOCKLIST_WORK. Every apply rebuilds the SPANEL-INPUT
# chain and reloads the ipsets from those files, so the runtime state can
# always be recreated from disk (including after a reboot).
#
# Chain layout (jumped to from INPUT position 1):
#   lo                              -> RETURN   (fall through to other tools)
#   allow sets (ip, ip+port)        -> RETURN
#   deny sets (ip, ip+port)         -> DROP
#   URL blocklist set               -> DROP
#   ESTABLISHED,RELATED             -> RETURN
#   protected + user open ports     -> RETURN
#   ICMP / ICMPv6                   -> RETURN
#   [when enabled] everything else  -> DROP
#
# RETURN (not ACCEPT) keeps the chain cooperative: fail2ban and any other
# INPUT rules still get to inspect packets SPanel allows.
##############################################################################

FW_SETS_V4=(spanel-allow4 spanel-allowp4 spanel-deny4 spanel-denyp4 spanel-block4)
FW_SETS_V6=(spanel-allow6 spanel-allowp6 spanel-deny6 spanel-denyp6 spanel-block6)

firewall_require_tools() {
  command -v iptables >/dev/null 2>&1 || deny "iptables is not installed"
  command -v ipset >/dev/null 2>&1 || deny "ipset is not installed (apt-get install -y ipset)"
}

firewall_has_ipv6() {
  command -v ip6tables >/dev/null 2>&1 && ip6tables -S INPUT >/dev/null 2>&1
}

ensure_firewall_dir() {
  ensure_spanel_data_dir
  install -d -o root -g root -m 0750 "$FIREWALL_DIR"
  [[ -f "$FIREWALL_RULES_FILE" ]] || { : >"$FIREWALL_RULES_FILE"; chmod 0640 "$FIREWALL_RULES_FILE"; }
  [[ -f "$FIREWALL_STATE_FILE" ]] || printf 'enabled\n' >"$FIREWALL_STATE_FILE"
}

firewall_state() {
  ensure_firewall_dir
  local value
  value="$(head -n 1 "$FIREWALL_STATE_FILE" 2>/dev/null | tr -d '[:space:]')"
  [[ "$value" == "disabled" ]] && echo "disabled" || echo "enabled"
}

firewall_set_state() {
  ensure_firewall_dir
  [[ "$1" == "enabled" || "$1" == "disabled" ]] || deny "invalid firewall state: $1"
  printf '%s\n' "$1" >"$FIREWALL_STATE_FILE"
}

# Ports that must never be closed by the panel: SSH (from sshd), the panel
# port, and the standard web/mail ports.
firewall_protected_ports() {
  local panel_port ssh_ports
  panel_port="$(env_get PANEL_PORT)"; panel_port="${panel_port:-$DEFAULT_PANEL_PORT}"
  ssh_ports="$(sshd -T 2>/dev/null | awk '$1 == "port" { print $2 }' || true)"
  if [[ -z "$ssh_ports" ]]; then
    # `sshd -T` fails on a config it cannot validate. Falling back to the raw
    # config keeps a custom SSH port from being closed on us.
    ssh_ports="$(awk 'tolower($1) == "port" && $2 ~ /^[0-9]+$/ { print $2 }' \
      /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null || true)"
  fi
  {
    printf '%s\n' "${FIREWALL_PROTECTED_PORTS[@]}"
    printf '%s\n' $ssh_ports
    printf '%s\n' "$panel_port"
  } | grep -E '^[0-9]{1,5}$' | sort -un
}

# ---- ipset ----------------------------------------------------------------

firewall_set_spec() {
  # echo the create spec for a set name
  case "$1" in
    *allowp4|*denyp4) echo "hash:net,port family inet hashsize 1024 maxelem 262144" ;;
    *allowp6|*denyp6) echo "hash:net,port family inet6 hashsize 1024 maxelem 262144" ;;
    *4) echo "hash:net family inet hashsize 4096 maxelem 1048576" ;;
    *6) echo "hash:net family inet6 hashsize 4096 maxelem 1048576" ;;
    *) deny "unknown ipset: $1" ;;
  esac
}

firewall_ensure_sets() {
  local name
  for name in "${FW_SETS_V4[@]}"; do
    # shellcheck disable=SC2046
    ipset create "$name" $(firewall_set_spec "$name") -exist
  done
  if firewall_has_ipv6; then
    for name in "${FW_SETS_V6[@]}"; do
      # shellcheck disable=SC2046
      ipset create "$name" $(firewall_set_spec "$name") -exist
    done
  fi
}

# Load a set atomically: fill a temporary set, then swap it in. Keeps the
# running firewall consistent even while a 100k-entry blocklist is loading.
firewall_load_set() {
  local name="$1" spec tmp file entry
  spec="$(firewall_set_spec "$name")"
  tmp="${name}-tmp"
  file="$(mktemp)"
  {
    printf 'create %s %s\n' "$tmp" "$spec"
    printf 'flush %s\n' "$tmp"
    # A blocklist can run to hundreds of thousands of addresses, and bash reads
    # them one at a time; awk does the same work in a fraction of the time, and
    # this runs on every firewall change.
    awk -v set="$tmp" 'NF { print "add " set " " $0 " -exist" }'
  } >"$file"
  ipset destroy "$tmp" 2>/dev/null || true
  if ! ipset restore -! <"$file"; then
    rm -f "$file"
    ipset destroy "$tmp" 2>/dev/null || true
    deny "failed to load firewall set $name"
  fi
  rm -f "$file"
  # shellcheck disable=SC2046
  ipset create "$name" $spec -exist
  ipset swap "$tmp" "$name"
  ipset destroy "$tmp" 2>/dev/null || true
}

firewall_is_ipv6() {
  [[ "$1" == *:* ]]
}

# ---- rules file -----------------------------------------------------------
# TSV columns: id <TAB> action <TAB> ip <TAB> port <TAB> protocol
# ip is empty for port-only rules; port is empty for whole-host rules.

firewall_rules() {
  ensure_firewall_dir
  grep -E '^[0-9]+\b' "$FIREWALL_RULES_FILE" 2>/dev/null || true
}

firewall_next_id() {
  local max
  max="$(firewall_rules | cut -f1 | sort -n | tail -n 1)"
  echo $(( ${max:-0} + 1 ))
}

firewall_add_rule() {
  local action="$1" ip="$2" port="${3:-}" protocol="${4:-tcp}" id existing
  case "$action" in allow|deny) ;; *) deny "invalid firewall action: $action" ;; esac
  if [[ -n "$ip" ]]; then
    ip="$(require_ip_or_cidr_normalized "$ip")"
  fi
  if [[ -n "$port" ]]; then
    require_port "$port"
    require_proto "$protocol"
  else
    protocol=""
  fi
  [[ -n "$ip" || -n "$port" ]] || deny "a firewall rule needs an IP or a port"
  if [[ -z "$ip" && "$action" == "deny" ]]; then
    deny "closing a port for every source is not supported; the firewall denies unlisted ports already"
  fi
  if [[ -z "$ip" && -n "$port" ]]; then
    local protected
    protected="$(firewall_protected_ports | tr '\n' ' ')"
    case " $protected " in *" $port "*) echo "Port ${port} is already open as a protected panel port"; return 0 ;; esac
  fi
  existing="$(firewall_rules | awk -F'\t' -v a="$action" -v i="$ip" -v p="$port" -v pr="$protocol" '$2==a && $3==i && $4==p && $5==pr { print $1; exit }')"
  if [[ -n "$existing" ]]; then
    echo "Rule already exists (#${existing})"
    firewall_apply >/dev/null
    return 0
  fi
  id="$(firewall_next_id)"
  printf '%s\t%s\t%s\t%s\t%s\n' "$id" "$action" "$ip" "$port" "$protocol" >>"$FIREWALL_RULES_FILE"
  firewall_apply >/dev/null
  echo "Rule #${id} added"
}

firewall_delete_rule() {
  local id="$1" tmp
  [[ "$id" =~ ^[0-9]+$ ]] || deny "invalid rule id: $id"
  [[ -n "$(firewall_rules | awk -F'\t' -v id="$id" '$1 == id')" ]] || deny "rule #${id} not found"
  tmp="$(mktemp)"
  firewall_rules | awk -F'\t' -v id="$id" '$1 != id' >"$tmp"
  install -m 0640 -o root -g root "$tmp" "$FIREWALL_RULES_FILE"
  rm -f "$tmp"
  firewall_apply >/dev/null
  echo "Rule #${id} deleted"
}

# ---- apply ----------------------------------------------------------------

firewall_sync_sets() {
  local rules
  rules="$(firewall_rules)"
  firewall_ensure_sets

  local want_v6=0
  firewall_has_ipv6 && want_v6=1

  # Manual allow/deny rules
  local action ip port protocol entry
  local -a allow4=() allow6=() allowp4=() allowp6=() deny4=() deny6=() denyp4=() denyp6=()
  while IFS=$'\t' read -r _id action ip port protocol; do
    [[ -n "$ip" ]] || continue
    if [[ -n "$port" ]]; then
      entry="${ip},${protocol}:${port}"
      if firewall_is_ipv6 "$ip"; then
        [[ "$action" == "allow" ]] && allowp6+=("$entry") || denyp6+=("$entry")
      else
        [[ "$action" == "allow" ]] && allowp4+=("$entry") || denyp4+=("$entry")
      fi
    else
      if firewall_is_ipv6 "$ip"; then
        [[ "$action" == "allow" ]] && allow6+=("$ip") || deny6+=("$ip")
      else
        [[ "$action" == "allow" ]] && allow4+=("$ip") || deny4+=("$ip")
      fi
    fi
  done <<<"$rules"

  printf '%s\n' "${allow4[@]:-}"  | firewall_load_set spanel-allow4
  printf '%s\n' "${allowp4[@]:-}" | firewall_load_set spanel-allowp4
  printf '%s\n' "${deny4[@]:-}"   | firewall_load_set spanel-deny4
  printf '%s\n' "${denyp4[@]:-}"  | firewall_load_set spanel-denyp4
  if (( want_v6 )); then
    printf '%s\n' "${allow6[@]:-}"  | firewall_load_set spanel-allow6
    printf '%s\n' "${allowp6[@]:-}" | firewall_load_set spanel-allowp6
    printf '%s\n' "${deny6[@]:-}"   | firewall_load_set spanel-deny6
    printf '%s\n' "${denyp6[@]:-}"  | firewall_load_set spanel-denyp6
  fi

  # URL blocklists
  local block4 block6
  block4="$(mktemp)"; block6="$(mktemp)"
  if [[ -s "$FIREWALL_BLOCKLIST_WORK" ]]; then
    grep -v ':' "$FIREWALL_BLOCKLIST_WORK" | sed '/^[[:space:]]*$/d' >"$block4" || true
    grep ':'    "$FIREWALL_BLOCKLIST_WORK" | sed '/^[[:space:]]*$/d' >"$block6" || true
  fi
  firewall_load_set spanel-block4 <"$block4"
  (( want_v6 )) && firewall_load_set spanel-block6 <"$block6"
  rm -f "$block4" "$block6"
}

firewall_apply_family() {
  local ipt="$1" fam="$2" state="$3"
  local sa sap sd sdp sb icmp
  if [[ "$fam" == "6" ]]; then
    sa=spanel-allow6; sap=spanel-allowp6; sd=spanel-deny6; sdp=spanel-denyp6; sb=spanel-block6
    icmp="ipv6-icmp"
  else
    sa=spanel-allow4; sap=spanel-allowp4; sd=spanel-deny4; sdp=spanel-denyp4; sb=spanel-block4
    icmp="icmp"
  fi

  "$ipt" -N "$FIREWALL_CHAIN" 2>/dev/null || true
  "$ipt" -F "$FIREWALL_CHAIN"

  "$ipt" -A "$FIREWALL_CHAIN" -i lo -j RETURN
  "$ipt" -A "$FIREWALL_CHAIN" -m conntrack --ctstate INVALID -j DROP

  "$ipt" -A "$FIREWALL_CHAIN" -m set --match-set "$sa" src -j RETURN
  "$ipt" -A "$FIREWALL_CHAIN" -p tcp -m set --match-set "$sap" src,dst -j RETURN
  "$ipt" -A "$FIREWALL_CHAIN" -p udp -m set --match-set "$sap" src,dst -j RETURN

  "$ipt" -A "$FIREWALL_CHAIN" -m set --match-set "$sd" src -j DROP
  "$ipt" -A "$FIREWALL_CHAIN" -p tcp -m set --match-set "$sdp" src,dst -j DROP
  "$ipt" -A "$FIREWALL_CHAIN" -p udp -m set --match-set "$sdp" src,dst -j DROP
  "$ipt" -A "$FIREWALL_CHAIN" -m set --match-set "$sb" src -j DROP

  # Below the deny sets, not above them. With this RETURN first, a blocked
  # address kept using any connection it had already opened: the packets were
  # ESTABLISHED, so they left the chain before reaching the DROP. An attacker
  # holding an HTTP keep-alive carried on for as long as it liked, and the
  # operator watched an address they had just blocked go on hitting the access
  # log. Seen in the field: 140.245.105.90 kept POSTing /wp-login.php down two
  # open connections for half an hour after the /19 containing it was denied.
  #
  # The cost is a few ipset lookups per packet on established connections.
  # ipset hashes are O(1), so that is not a throughput question - it is the
  # price of "blocked" meaning blocked.
  "$ipt" -A "$FIREWALL_CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN

  # ICMPv6 carries neighbour discovery; dropping it breaks IPv6 entirely.
  "$ipt" -A "$FIREWALL_CHAIN" -p "$icmp" -j RETURN

  local port
  while read -r port; do
    [[ -n "$port" ]] || continue
    "$ipt" -A "$FIREWALL_CHAIN" -p tcp --dport "$port" -j RETURN
    if [[ "$port" == "443" ]]; then
      "$ipt" -A "$FIREWALL_CHAIN" -p udp --dport 443 -j RETURN
    fi
  done < <(firewall_protected_ports)

  # Ports an addon opens while it is on - DNS Manager's 53, over TCP and UDP.
  # The addon writes its file when installed and deletes it when removed, so
  # turning it off closes the port and the operator's own rules never change.
  local addon_port addon_proto
  while read -r addon_port addon_proto; do
    [[ "$addon_port" =~ ^[0-9]+$ && "$addon_proto" =~ ^(tcp|udp)$ ]] || continue
    "$ipt" -A "$FIREWALL_CHAIN" -p "$addon_proto" --dport "$addon_port" -j RETURN
  done < <(cat "$FIREWALL_ADDON_PORTS_DIR"/*.ports 2>/dev/null || true)

  local _id action ip proto
  while IFS=$'\t' read -r _id action ip port proto; do
    [[ "$action" == "allow" && -z "$ip" && -n "$port" ]] || continue
    "$ipt" -A "$FIREWALL_CHAIN" -p "${proto:-tcp}" --dport "$port" -j RETURN
  done <<<"$(firewall_rules)"

  if [[ "$state" == "enabled" ]]; then
    "$ipt" -A "$FIREWALL_CHAIN" -j DROP
    "$ipt" -C INPUT -j "$FIREWALL_CHAIN" 2>/dev/null || "$ipt" -I INPUT 1 -j "$FIREWALL_CHAIN"
  else
    # Keep the chain (and its counters) around but stop consulting it.
    while "$ipt" -C INPUT -j "$FIREWALL_CHAIN" 2>/dev/null; do
      "$ipt" -D INPUT -j "$FIREWALL_CHAIN" || break
    done
  fi
}

firewall_kill_blocked_connections() {
  # A DROP rule stops packets; it does not close a socket. Without this, an
  # address that has just been blocked keeps an established connection sitting
  # there until some timeout notices - the attacker's requests stop arriving,
  # but nginx still holds the worker, and nothing tells the operator the block
  # took effect. Blocking is meant to be the answer to an attack in progress,
  # so the connection has to go now.
  #
  # Walk the open sockets rather than the sets: the URL blocklist holds six
  # figures of addresses and a server holds hundreds of connections, so testing
  # each peer against the sets is the cheap direction. `ipset test` is O(1).
  command -v ss >/dev/null 2>&1 || return 0
  command -v ipset >/dev/null 2>&1 || return 0

  # Address-only sets. The denyp/blockp sets are keyed on ip,port, so testing
  # them with an address alone always misses - it would be one wasted process
  # per connection per set. A connection blocked by an ip,port rule still
  # stops carrying traffic; it just is not closed early.
  local peer ip set killed=0
  local -a sets=(spanel-deny4 spanel-block4)
  if firewall_has_ipv6; then
    sets+=(spanel-deny6 spanel-block6)
  fi

  while read -r peer; do
    [[ -n "$peer" ]] || continue
    # peer is addr:port, and an IPv6 address arrives as [::1]:443.
    ip="${peer%:*}"
    ip="${ip#[}"
    ip="${ip%]}"
    [[ -n "$ip" ]] || continue
    # Never a loopback peer. The filter chain RETURNs for -i lo before any deny
    # set is consulted, so a loopback connection is by definition not one the
    # firewall is blocking - killing it can only be collateral damage.
    #
    # It was. Public blocklists carry reserved ranges, and spanel-block4 on a
    # live server holds 127.0.0.1 among its 186k entries. So every block ran
    # `ss -K dst 127.0.0.1` and closed every local connection on the machine:
    # MariaDB over TCP, Redis, phpMyAdmin, and - how it was found - the panel's
    # own reply to whoever had just asked for the block, which reached them as
    # "Connection reset by peer" after the rule had already been added.
    case "$ip" in
      127.*|::1) continue ;;
    esac
    for set in "${sets[@]}"; do
      if ipset test "$set" "$ip" >/dev/null 2>&1; then
        # Best effort: -K needs CONFIG_INET_DIAG_DESTROY, which not every
        # kernel has. A failure here leaves the DROP rule doing its job.
        ss -K dst "$ip" >/dev/null 2>&1 && killed=$((killed + 1))
        break
      fi
    done
  done < <(ss -tnH state established 2>/dev/null | awk '{print $NF}' | sort -u)

  [[ "$killed" -gt 0 ]] && echo "Closed ${killed} connection(s) from blocked addresses"
  return 0
}

firewall_apply() {
  local state
  firewall_require_tools
  ensure_firewall_dir
  state="$(firewall_state)"
  firewall_sync_sets
  firewall_apply_family iptables 4 "$state"
  if firewall_has_ipv6; then
    firewall_apply_family ip6tables 6 "$state"
  fi
  firewall_write_boot_unit
  # Never let a Docker guard failure abort the main firewall apply.
  install_docker_firewall_guard || echo "WARNING: could not apply the Docker inbound guard" >&2
  if [[ "$state" == "enabled" ]]; then
    firewall_kill_blocked_connections || true
  fi
  echo "Firewall applied (${state})"
}

firewall_flush() {
  local ipt name
  for ipt in iptables ip6tables; do
    command -v "$ipt" >/dev/null 2>&1 || continue
    while "$ipt" -C INPUT -j "$FIREWALL_CHAIN" 2>/dev/null; do
      "$ipt" -D INPUT -j "$FIREWALL_CHAIN" || break
    done
    "$ipt" -F "$FIREWALL_CHAIN" 2>/dev/null || true
    "$ipt" -X "$FIREWALL_CHAIN" 2>/dev/null || true
  done
  if command -v ipset >/dev/null 2>&1; then
    for name in "${FW_SETS_V4[@]}" "${FW_SETS_V6[@]}"; do
      ipset destroy "$name" 2>/dev/null || true
    done
  fi
  echo "Firewall rules removed from the running kernel"
}

firewall_write_boot_unit() {
  local unit=/etc/systemd/system/spanel-firewall.service tmp
  tmp="$(mktemp)"
  cat >"$tmp" <<'UNIT'
[Unit]
Description=SPanel firewall (iptables + ipset)
After=network-pre.target
Wants=network-pre.target
Before=network.target nginx.service

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=SUDO_USER=spanel
ExecStart=/usr/local/sbin/spanel-helper firewall-apply
ExecStop=/usr/local/sbin/spanel-helper firewall-flush

[Install]
WantedBy=multi-user.target
UNIT
  if ! cmp -s "$tmp" "$unit" 2>/dev/null; then
    install -m 0644 -o root -g root "$tmp" "$unit"
    systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  rm -f "$tmp"
  systemctl is-enabled spanel-firewall.service >/dev/null 2>&1 \
    || systemctl enable spanel-firewall.service >/dev/null 2>&1 || true
}

# ---- status ---------------------------------------------------------------

firewall_set_count() {
  ipset list "$1" -t 2>/dev/null | awk -F': ' '/Number of entries/ { print $2; exit }'
}

firewall_status() {
  ensure_firewall_dir
  local state active="no" protected
  state="$(firewall_state)"
  if command -v iptables >/dev/null 2>&1 && iptables -C INPUT -j "$FIREWALL_CHAIN" 2>/dev/null; then
    active="yes"
  fi
  protected="$(firewall_protected_ports | tr '\n' ',' | sed 's/,$//')"

  echo "Status: ${state}"
  echo "Engine: iptables + ipset"
  echo "Chain active: ${active}"
  echo "IPv6: $(firewall_has_ipv6 && echo yes || echo no)"
  echo "Default incoming: deny (unlisted ports)"
  echo "Protected ports (tcp): ${protected}"
  echo ""
  echo "Rules:"
  if [[ -n "$(firewall_rules)" ]]; then
    firewall_rules | awk -F'\t' '{
      target = ($4 == "") ? "any port" : $4 "/" $5
      src = ($3 == "") ? "any" : $3
      printf "  [%s] %-5s %-22s from %s\n", $1, toupper($2), target, src
    }'
  else
    echo "  (none)"
  fi
  echo ""
  echo "Sets:"
  local name count
  for name in "${FW_SETS_V4[@]}"; do
    count="$(firewall_set_count "$name")"
    [[ -n "$count" ]] && printf '  %-18s %s entries\n' "$name" "$count"
  done
  if firewall_has_ipv6; then
    for name in "${FW_SETS_V6[@]}"; do
      count="$(firewall_set_count "$name")"
      [[ -n "$count" ]] && printf '  %-18s %s entries\n' "$name" "$count"
    done
  fi
}

firewall_list_json() {
  ensure_firewall_dir
  local protected state active="false"
  state="$(firewall_state)"
  protected="$(firewall_protected_ports | tr '\n' ' ')"
  if command -v iptables >/dev/null 2>&1 && iptables -C INPUT -j "$FIREWALL_CHAIN" 2>/dev/null; then
    active="true"
  fi
  firewall_rules | python3 -c '
import json, sys

rules = []
for line in sys.stdin:
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 5 or not parts[0].isdigit():
        continue
    rid, action, ip, port, proto = parts[:5]
    rules.append({
        "id": int(rid),
        "number": int(rid),
        "action": action.upper(),
        "to": f"{port}/{proto}" if port else "any",
        "from": ip or "any",
        "port": port or None,
        "protocol": proto or None,
        "ip": ip or None,
        "zone": "UserZone",
        "protected": False,
    })

protected = [p for p in sys.argv[1].split() if p.isdigit()]
for port in protected:
    rules.append({
        "id": 0,
        "number": 0,
        "action": "ALLOW",
        "to": f"{port}/tcp",
        "from": "any",
        "port": port,
        "protocol": "tcp",
        "ip": None,
        "zone": "PanelZone",
        "protected": True,
    })

print(json.dumps({
    "state": sys.argv[2],
    "active": sys.argv[3] == "true",
    "engine": "iptables+ipset",
    "rules": rules,
}))
' "$protected" "$state" "$active"
}

# ---- legacy cleanup -------------------------------------------------------

# Copy any surviving UFW user rules into the new rules file so an upgrade does
# not silently close ports the admin opened by hand.
firewall_import_ufw_rules() {
  command -v ufw >/dev/null 2>&1 || return 0
  local protected imported=0
  protected="$(firewall_protected_ports | tr '\n' ' ')"
  while IFS=$'\t' read -r action ip port proto; do
    [[ -n "$action" ]] || continue
    if [[ -z "$ip" && -n "$port" ]]; then
      case " $protected " in *" $port "*) continue ;; esac
    fi
    # Subshell: firewall_add_rule calls deny() on bad input, which exits.
    if ( firewall_add_rule "$action" "$ip" "$port" "$proto" ) >/dev/null 2>&1; then
      imported=$((imported + 1))
    fi
  done < <(ufw status numbered 2>/dev/null | python3 -c '
import re, sys

# "ufw status numbered" lines look like:
#   [ 1] 22/tcp        ALLOW IN    Anywhere        # spanel:PanelZone
#   [ 3] 3306/tcp      ALLOW IN    10.0.0.5
# Application profiles ("Nginx Full"), v6 duplicates and OUT rules are skipped:
# the ports they cover are protected ports in the new chain.
LINE = re.compile(
    r"^(?:\[\s*\d+\]\s*)?(.+?)\s{2,}(ALLOW|DENY)(?:\s+(IN|OUT))?\s{2,}(.+?)\s*$",
    re.I,
)

for raw in sys.stdin:
    line = raw.rstrip()
    m = LINE.match(line)
    if not m:
        continue
    target, action, direction, source = m.group(1).strip(), m.group(2).lower(), (m.group(3) or "IN").upper(), m.group(4).strip()
    if direction != "IN":
        continue
    source = re.sub(r"\s*#.*$", "", source).strip()
    if "(v6)" in target or "(v6)" in source:
        continue
    if source.lower().startswith("anywhere"):
        source = ""
    port, proto = "", "tcp"
    pm = re.match(r"^(\d{1,5})(?:/(tcp|udp))?$", target)
    if pm:
        port, proto = pm.group(1), pm.group(2) or "tcp"
    elif target.lower() != "anywhere":
        # Application profile or port range: not portable to a single rule.
        continue
    if not port and not source:
        continue
    print("\t".join([action, source, port, proto]))
')
  [[ "$imported" -gt 0 ]] && echo "Imported ${imported} rule(s) from UFW" || true
  return 0
}

firewall_purge_ufw() {
  command -v ufw >/dev/null 2>&1 || return 0
  echo "Removing UFW ..."
  ufw --force disable >/dev/null 2>&1 || true
  ufw --force reset >/dev/null 2>&1 || true
  systemctl disable --now ufw >/dev/null 2>&1 || true
  systemctl mask ufw >/dev/null 2>&1 || true
  DEBIAN_FRONTEND=noninteractive apt-get purge -y ufw >/dev/null 2>&1 || true
  rm -rf /etc/ufw /lib/ufw 2>/dev/null || true
  rm -f /etc/systemd/system/spanel-firewall-blocklist.service \
        /etc/systemd/system/spanel-firewall-blocklist.timer 2>/dev/null || true
  systemctl daemon-reload >/dev/null 2>&1 || true
}

# Strip the Nginx geo-map blocklist from every managed vhost. The
# ip-blocklist-server.conf stub is kept (emptied) so any hand-written vhost
# that still includes it does not break Nginx.
firewall_purge_nginx_blocklist() {
  install -d -o root -g root -m 0755 "$NGINX_SPANEL_DIR"
  cat >"$NGINX_BLOCKLIST_SERVER_CONF" <<'CONF'
# Managed by SPanel. IP blocking moved to iptables + ipset; this file is kept
# empty so older vhosts that still include it keep loading.
CONF
  chown root:root "$NGINX_BLOCKLIST_SERVER_CONF"
  chmod 0644 "$NGINX_BLOCKLIST_SERVER_CONF"

  local changed=0 conf
  for conf in "$NGINX_CONF_DIR"/*.conf; do
    [[ -f "$conf" ]] || continue
    if grep -q 'ip-blocklist-server\.conf' "$conf"; then
      sed -i '/ip-blocklist-server\.conf/d' "$conf"
      changed=1
    fi
  done
  [[ -f "$NGINX_BLOCKLIST_CONF" || -f "$NGINX_BLOCKLIST_RULES" ]] && changed=1
  rm -f "$NGINX_BLOCKLIST_CONF" "$NGINX_BLOCKLIST_RULES" 2>/dev/null || true
  if (( changed )) && nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || true
  fi
  return 0
}

# A server that updated straight from a release older than the iptables
# firewall ran that release's updater, which knew nothing of it: UFW still
# filters, ipset may be missing, and the panel's chain was never applied -
# "on" as a setting, off in fact. This does what the missing update step
# would have: install the tools, take over from UFW, apply.
firewall_repair() {
  if ! command -v ipset >/dev/null 2>&1 || ! command -v iptables >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 update --allow-releaseinfo-change >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y iptables ipset >/dev/null 2>&1 \
      || deny "could not install iptables and ipset (apt-get install -y iptables ipset)"
  fi
  firewall_migrate
}

firewall_migrate() {
  firewall_require_tools
  local first_run=0
  [[ -f "$FIREWALL_STATE_FILE" ]] || first_run=1
  ensure_firewall_dir
  firewall_import_ufw_rules || true

  if (( first_run )); then
    # Inherit whatever was enforcing traffic before the migration. Switching a
    # box that had no active firewall to default-deny would cut off services
    # SPanel does not know about (mail, game servers, custom daemons). A box
    # with blocklist URLs configured was relying on IP blocking through Nginx,
    # so that one is switched on.
    local was_enforcing=0
    if command -v ufw >/dev/null 2>&1 && grep -qi 'active' <<<"$(ufw status 2>/dev/null | head -n 1 || true)"; then
      was_enforcing=1
    fi
    if [[ -s "$FIREWALL_BLOCKLIST_URLS" ]]; then
      was_enforcing=1
    fi
    if (( was_enforcing )); then
      firewall_set_state enabled
    else
      firewall_set_state disabled
      echo "No active firewall detected; rules are staged but not enforced."
      echo "Turn them on from the panel Firewall page or: spanel-helper firewall-enable"
    fi
  fi

  firewall_purge_ufw
  firewall_purge_nginx_blocklist
  firewall_apply
}

require_url() {
  local value="$1"
  [[ "$value" =~ ^https?://[^[:space:]]+$ ]] || deny "invalid URL: $value"
}

firewall_blocklist_urls() {
  ensure_spanel_data_dir
  touch "$FIREWALL_BLOCKLIST_URLS"
  sed '/^[[:space:]]*$/d' "$FIREWALL_BLOCKLIST_URLS" | sort -u
}

firewall_blocklist_write_timer() {
  local changed=0 tmp
  tmp="$(mktemp)"
  cat >"$tmp" <<'SERVICE'
[Unit]
Description=Refresh SPanel IP blocklists (iptables + ipset)
After=network-online.target spanel-firewall.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=SUDO_USER=spanel
ExecStart=/usr/local/sbin/spanel-helper firewall-blocklist-run
SERVICE
  if ! cmp -s "$tmp" /etc/systemd/system/spanel-blocklist.service 2>/dev/null; then
    install -m 0644 -o root -g root "$tmp" /etc/systemd/system/spanel-blocklist.service
    changed=1
  fi
  cat >"$tmp" <<'TIMER'
[Unit]
Description=Refresh SPanel IP blocklists daily

[Timer]
OnCalendar=*-*-* 01:00:00
RandomizedDelaySec=1800
Persistent=true

[Install]
WantedBy=timers.target
TIMER
  if ! cmp -s "$tmp" /etc/systemd/system/spanel-blocklist.timer 2>/dev/null; then
    install -m 0644 -o root -g root "$tmp" /etc/systemd/system/spanel-blocklist.timer
    changed=1
  fi
  rm -f "$tmp"
  # Retire the Nginx-era units.
  if [[ -f /etc/systemd/system/spanel-firewall-blocklist.timer ]]; then
    systemctl disable --now spanel-firewall-blocklist.timer >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/spanel-firewall-blocklist.service \
          /etc/systemd/system/spanel-firewall-blocklist.timer
    changed=1
  fi
  (( changed )) && systemctl daemon-reload >/dev/null 2>&1
  systemctl enable --now spanel-blocklist.timer >/dev/null 2>&1 || true
  return 0
}

write_http_flood_nginx_conf() {
  ensure_nginx_conf_dir_writable
  if [[ ! -f "$NGINX_HTTP_FLOOD_ZONES" ]]; then
    cat >"$NGINX_HTTP_FLOOD_ZONES" <<'CONF'
# Managed by SPanel. Shared zones for per-website HTTP flood protection.
map $cookie_spanel_http_flood_ok $spanel_http_flood_key {
    default $binary_remote_addr;
    1 "";
}
limit_conn_zone $spanel_http_flood_key zone=spanel_conn_flood:10m;
CONF
  fi
  cat >"$NGINX_HTTP_FLOOD_CONF" <<'CONF'
# Managed by SPanel. Shared zones for per-website HTTP flood protection.
include /etc/nginx/spanel/http-flood-zones.conf;
CONF
  rm -f "$NGINX_HTTP_FLOOD_LEGACY_CONF" "$NGINX_HTTP_FLOOD_SERVER_CONF" 2>/dev/null || true
  chown root:root "$NGINX_HTTP_FLOOD_CONF" "$NGINX_HTTP_FLOOD_ZONES"
  chmod 0644 "$NGINX_HTTP_FLOOD_CONF" "$NGINX_HTTP_FLOOD_ZONES"
}

save_http_flood_zones() {
  local tmp backup=""
  ensure_nginx_conf_dir_writable
  tmp="$(mktemp)"
  cat >"$tmp"
  if [[ $(wc -c <"$tmp") -gt 131072 ]]; then
    rm -f "$tmp"
    deny "HTTP flood zones are too large"
  fi
  if file_has_nul "$tmp"; then
    rm -f "$tmp"
    deny "HTTP flood zones cannot contain NUL bytes"
  fi
  if [[ -f "$NGINX_HTTP_FLOOD_ZONES" ]]; then
    backup="${NGINX_HTTP_FLOOD_ZONES}.bak.$(date +%s)"
    cp "$NGINX_HTTP_FLOOD_ZONES" "$backup"
  fi
  install -m 0644 -o root -g root "$tmp" "$NGINX_HTTP_FLOOD_ZONES"
  rm -f "$tmp"
  write_http_flood_nginx_conf
  if ! nginx -t; then
    if [[ -n "$backup" && -f "$backup" ]]; then
      mv -f "$backup" "$NGINX_HTTP_FLOOD_ZONES"
    else
      cat >"$NGINX_HTTP_FLOOD_ZONES" <<'CONF'
# Managed by SPanel. Shared zones for per-website HTTP flood protection.
map $cookie_spanel_http_flood_ok $spanel_http_flood_key {
    default $binary_remote_addr;
    1 "";
}
limit_conn_zone $spanel_http_flood_key zone=spanel_conn_flood:10m;
CONF
    fi
    deny "Nginx rejected HTTP flood zones"
  fi
  rm -f "$backup" 2>/dev/null || true
  systemctl reload nginx
  echo "HTTP flood zones saved"
}

firewall_blocklist_status() {
  ensure_spanel_data_dir
  touch "$FIREWALL_BLOCKLIST_URLS"
  echo "URLs:"
  if [[ -s "$FIREWALL_BLOCKLIST_URLS" ]]; then
    firewall_blocklist_urls | sed 's/^/  /'
  else
    echo "  (none)"
  fi
  echo ""
  echo "Engine:"
  echo "  iptables + ipset"
  echo "Sets:"
  printf '  spanel-block4      %s entries\n' "$(firewall_set_count spanel-block4)"
  if firewall_has_ipv6; then
    printf '  spanel-block6      %s entries\n' "$(firewall_set_count spanel-block6)"
  fi
  echo ""
  echo "Networks:"
  if [[ -s "$FIREWALL_BLOCKLIST_WORK" ]]; then
    local total shown
    total="$(sed '/^[[:space:]]*$/d' "$FIREWALL_BLOCKLIST_WORK" | wc -l | tr -d '[:space:]')"
    shown=50
    echo "  ${total} network(s), showing first ${shown}:"
    sed '/^[[:space:]]*$/d' "$FIREWALL_BLOCKLIST_WORK" | head -n "$shown" | sed 's/^/  /'
    if (( total > shown )); then
      echo "  ... $((total - shown)) more"
    fi
  else
    echo "  (none)"
  fi
  echo ""
  echo "Timer:"
  systemctl is-enabled spanel-blocklist.timer 2>/dev/null || true
  systemctl list-timers spanel-blocklist.timer --no-pager 2>/dev/null || true
}

firewall_blocklist_run() {
  ensure_spanel_data_dir
  ensure_firewall_dir
  touch "$FIREWALL_BLOCKLIST_URLS"
  local tmp fetched count url
  tmp="$(mktemp)"
  fetched="$(mktemp)"
  while IFS= read -r url; do
    [[ -n "$url" ]] || continue
    require_url "$url"
    curl -fsSL --connect-timeout 10 --max-time 60 "$url" >>"$fetched" || echo "WARNING: could not fetch $url" >&2
    printf '\n' >>"$fetched"
  done < <(firewall_blocklist_urls)
  python3 - "$fetched" "$tmp" <<'PY'
import ipaddress
import re
import sys

seen = set()
networks = []
for raw in open(sys.argv[1], encoding="utf-8", errors="ignore"):
    line = re.split(r"[\s#;,]+", raw.strip(), 1)[0]
    if not line:
        continue
    try:
        value = str(ipaddress.ip_network(line, strict=False))
    except ValueError:
        continue
    if value not in seen:
        seen.add(value)
        networks.append(value)

with open(sys.argv[2], "w", encoding="utf-8") as handle:
    for value in networks:
        handle.write(value + "\n")
PY
  install -m 0640 -o root -g root "$tmp" "$FIREWALL_BLOCKLIST_WORK"
  count="$(sed '/^[[:space:]]*$/d' "$FIREWALL_BLOCKLIST_WORK" | wc -l | tr -d '[:space:]')"
  rm -f "$tmp" "$fetched"
  firewall_apply >/dev/null
  firewall_blocklist_write_timer
  echo "IP blocklist refreshed: ${count} network(s) loaded into ipset"
}

firewall_blocklist_add_url() {
  local url="$1"
  require_url "$url"
  ensure_spanel_data_dir
  touch "$FIREWALL_BLOCKLIST_URLS"
  if ! grep -Fxq -- "$url" "$FIREWALL_BLOCKLIST_URLS"; then
    printf '%s\n' "$url" >>"$FIREWALL_BLOCKLIST_URLS"
  fi
  sort -u -o "$FIREWALL_BLOCKLIST_URLS" "$FIREWALL_BLOCKLIST_URLS"
  firewall_blocklist_write_timer
  echo "IP blocklist URL added"
}

firewall_blocklist_delete_url() {
  local url="$1"
  require_url "$url"
  ensure_spanel_data_dir
  touch "$FIREWALL_BLOCKLIST_URLS"
  grep -Fxv -- "$url" "$FIREWALL_BLOCKLIST_URLS" >"${FIREWALL_BLOCKLIST_URLS}.tmp" || true
  mv -f "${FIREWALL_BLOCKLIST_URLS}.tmp" "$FIREWALL_BLOCKLIST_URLS"
  firewall_blocklist_write_timer
  echo "IP blocklist URL removed"
}

write_ssl_auto_renew_timer() {
  cat >/etc/systemd/system/spanel-ssl-auto-renew.service <<SERVICE
[Unit]
Description=Renew SPanel SSL certificates that expire within 10 days
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=SUDO_USER=spanel
ExecStart=/usr/local/sbin/spanel-helper certbot-renew-soon 10
SERVICE
  cat >/etc/systemd/system/spanel-ssl-auto-renew.timer <<TIMER
[Unit]
Description=Check SPanel SSL certificates daily

[Timer]
OnCalendar=*-*-* 01:30:00
Persistent=true

[Install]
WantedBy=timers.target
TIMER
  systemctl daemon-reload
  systemctl enable --now spanel-ssl-auto-renew.timer >/dev/null 2>&1 || true
}

copy_panel_live_certificate() {
  local domain="$1"
  [[ -n "$domain" ]] || return 0
  [[ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" && -f "/etc/letsencrypt/live/${domain}/privkey.pem" ]] || return 0
  install -d -o root -g spanel -m 0750 /etc/spanel
  install -m 0640 -o root -g spanel "/etc/letsencrypt/live/${domain}/fullchain.pem" /etc/spanel/panel-fullchain.pem
  install -m 0640 -o root -g spanel "/etc/letsencrypt/live/${domain}/privkey.pem" /etc/spanel/panel-privkey.pem
  if [[ -f "$ENV_FILE" ]]; then
    env_set PANEL_SSL_CERT "/etc/spanel/panel-fullchain.pem"
    env_set PANEL_SSL_KEY "/etc/spanel/panel-privkey.pem"
  fi
}

install_manual_ssl() {
  local domain="$1" base tmpdir
  require_domain "$domain"
  base="/etc/nginx/spanel/ssl/sites/${domain}"
  tmpdir="$(mktemp -d /tmp/spanel-manual-ssl.XXXXXX)"
  trap 'rm -rf "$tmpdir"' RETURN
  local payload_file="$tmpdir/payload.json"
  cat >"$payload_file"
  python3 - "$tmpdir" "$payload_file" <<'PY'
import json
import pathlib
import sys

tmpdir = pathlib.Path(sys.argv[1])
payload_file = pathlib.Path(sys.argv[2])
data = json.loads(payload_file.read_text(encoding="utf-8"))
parts = {
    "cert.crt": data.get("certificate", ""),
    "privkey.key": data.get("private_key", ""),
}
ca_bundle = data.get("ca_bundle", "")
if ca_bundle:
    parts["ca.crt"] = ca_bundle
for name, content in parts.items():
    if not content or "\x00" in content:
        raise SystemExit(f"invalid {name}")
    (tmpdir / name).write_text(content, encoding="utf-8")
PY
  install -d -o root -g spanel -m 0750 "$base"
  install -m 0640 -o root -g spanel "$tmpdir/cert.crt" "$base/cert.crt"
  install -m 0640 -o root -g spanel "$tmpdir/privkey.key" "$base/privkey.key"
  if [[ -f "$tmpdir/ca.crt" ]]; then
    install -m 0640 -o root -g spanel "$tmpdir/ca.crt" "$base/ca.crt"
    cat "$tmpdir/cert.crt" "$tmpdir/ca.crt" >"$tmpdir/fullchain.crt"
    install -m 0640 -o root -g spanel "$tmpdir/fullchain.crt" "$base/fullchain.crt"
  else
    rm -f "$base/ca.crt"
    install -m 0640 -o root -g spanel "$tmpdir/cert.crt" "$base/fullchain.crt"
  fi
  echo "Manual SSL installed for ${domain}"
}

remove_manual_ssl() {
  local domain="$1" base
  require_domain "$domain"
  base="/etc/nginx/spanel/ssl/sites/${domain}"
  rm -f "$base/cert.crt" "$base/privkey.key" "$base/ca.crt" "$base/fullchain.crt"
  rmdir "$base" 2>/dev/null || true
  echo "Manual SSL removed for ${domain}"
}

install_certbot_dns_cloudflare() {
  export DEBIAN_FRONTEND=noninteractive
  if pkg_installed python3-certbot-dns-cloudflare; then
    echo "certbot dns-cloudflare plugin already installed"
    return 0
  fi
  apt-get update --allow-releaseinfo-change
  apt-get install -y python3-certbot-dns-cloudflare
  echo "certbot dns-cloudflare plugin installed"
}

cloudflare_ssl_issue() {
  # Zone comes from argv (validated); the literal "*." is built here, never
  # taken from the caller. The token arrives on stdin so it never lands in a
  # process list or the sudo log.
  local zone="$1" email="${2:-}" ini token args
  require_domain "$zone"
  [[ -n "$email" ]] && require_email "$email"
  command -v certbot >/dev/null 2>&1 || deny "certbot is not installed"
  pkg_installed python3-certbot-dns-cloudflare \
    || deny "certbot dns-cloudflare plugin is not installed (run certbot-dns-cloudflare-install)"
  token="$(cat)"
  [[ "$token" =~ ^[A-Za-z0-9_.~-]{20,200}$ ]] || deny "cloudflare API token is missing or malformed"
  install -d -o root -g root -m 0700 /etc/spanel /etc/spanel/cloudflare
  ini="/etc/spanel/cloudflare/${zone}.ini"
  ( umask 077; printf 'dns_cloudflare_api_token = %s\n' "$token" >"$ini" )
  chown root:root "$ini"; chmod 0600 "$ini"
  args=(certonly --dns-cloudflare --dns-cloudflare-credentials "$ini"
    --dns-cloudflare-propagation-seconds 30 --cert-name "$zone"
    -d "$zone" -d "*.${zone}" --non-interactive --agree-tos --keep-until-expiring)
  if [[ -n "$email" ]]; then
    args+=(--email "$email")
  else
    args+=(--register-unsafely-without-email)
  fi
  certbot "${args[@]}"
  # The panel may be reachable on this zone now, and renewals must keep the
  # per-hostname copies fresh.
  install_sni_renewal_hook
  sync_panel_sni_certificates >/dev/null
  echo "Wildcard certificate ready: ${zone} and *.${zone}"
}

ssl_cert_info() {
  # Read-only: the panel runs as 'spanel' and cannot open /etc/letsencrypt/live,
  # so it asks the helper for a borrowed certificate's expiry and names.
  local name="$1" cert not_after sans
  require_domain "$name"
  if [[ -f "/etc/letsencrypt/live/${name}/cert.pem" ]]; then
    cert="/etc/letsencrypt/live/${name}/cert.pem"
  elif [[ -f "/etc/nginx/spanel/ssl/sites/${name}/cert.crt" ]]; then
    cert="/etc/nginx/spanel/ssl/sites/${name}/cert.crt"
  else
    deny "no certificate on this server for ${name}"
  fi
  not_after="$(openssl x509 -enddate -noout -in "$cert" 2>/dev/null | cut -d= -f2- || true)"
  sans="$( { openssl x509 -ext subjectAltName -noout -in "$cert" 2>/dev/null || true; } \
    | grep -oE 'DNS:[^,]+' | sed 's/DNS://g;s/ //g' | paste -sd, - || true)"
  if [[ -z "$sans" ]]; then
    sans="$( { openssl x509 -subject -noout -in "$cert" 2>/dev/null || true; } \
      | grep -oE 'CN ?= ?[^,/]+' | sed -E 's/CN ?= ?//' || true)"
  fi
  printf 'not_after=%s\n' "$not_after"
  printf 'sans=%s\n' "$sans"
}

delete_ssl_cert() {
  # A website that is gone should take its certificate with it. Left behind,
  # the renewal config still wakes certbot.timer twice a day and starts failing
  # the moment the domain stops pointing here - which is how a deleted site
  # turns into a permanently failed unit nobody can explain.
  local name="$1" panel_domain removed=0
  require_domain "$name"
  # The panel serves :2222 on a certificate that may well belong to a hosted
  # domain (panel-ssl-use-domain). Deleting that one drops every admin onto a
  # browser warning at the next login, so refuse even when asked directly.
  panel_domain="$(env_get PANEL_DOMAIN)"
  if [[ -n "$panel_domain" && "$panel_domain" == "$name" ]]; then
    deny "refusing to delete ${name}: the panel is served on this certificate"
  fi
  if [[ -d "/etc/letsencrypt/live/${name}" || -f "/etc/letsencrypt/renewal/${name}.conf" ]]; then
    certbot delete --cert-name "$name" --non-interactive >/dev/null 2>&1 \
      || deny "certbot could not delete the certificate for ${name}"
    echo "Deleted Let's Encrypt certificate ${name}"
    removed=1
  fi
  if [[ -d "/etc/nginx/spanel/ssl/sites/${name}" ]]; then
    remove_manual_ssl "$name" >/dev/null
    echo "Removed uploaded certificate for ${name}"
    removed=1
  fi
  [[ "$removed" -eq 1 ]] || echo "No certificate on this server for ${name}"
  # Stop answering for the domain on :2222 as well; the sync drops SNI copies
  # whose source certificate no longer exists.
  sync_panel_sni_certificates >/dev/null
}

renew_ssl_soon() {
  local days="${1:-10}" seconds cert cert_name checked=0 renewed=0 panel_domain
  [[ "$days" =~ ^[0-9]+$ && "$days" -ge 1 && "$days" -le 30 ]] || deny "usage: certbot-renew-soon [1-30 days]"
  write_ssl_auto_renew_timer
  if ! command -v certbot >/dev/null 2>&1; then
    echo "certbot is not installed"
    return 0
  fi
  seconds=$((days * 86400))
  shopt -s nullglob
  for cert in /etc/letsencrypt/live/*/cert.pem; do
    [[ -f "$cert" ]] || continue
    cert_name="$(basename "$(dirname "$cert")")"
    [[ "$cert_name" == "README" ]] && continue
    checked=$((checked + 1))
    if ! openssl x509 -checkend "$seconds" -noout -in "$cert" >/dev/null 2>&1; then
      echo "Renewing certificate: ${cert_name}"
      if certbot renew --cert-name "$cert_name" --quiet --force-renewal \
        --deploy-hook "systemctl reload nginx || true; systemctl restart spanel-api || true"; then
        renewed=$((renewed + 1))
      else
        echo "WARNING: could not renew ${cert_name}" >&2
      fi
    fi
  done
  shopt -u nullglob
  panel_domain="$(env_get PANEL_DOMAIN)"
  copy_panel_live_certificate "$panel_domain"
  if [[ "$renewed" -gt 0 ]]; then
    systemctl reload nginx >/dev/null 2>&1 || true
    systemctl restart spanel-api >/dev/null 2>&1 || true
  fi
  echo "SSL auto-renew checked ${checked} certificate(s); renewed ${renewed} certificate(s) within ${days} day(s)."
}

# ---- managed application runtimes ------------------------------------------
# A site app runs as a systemd unit whose name the panel never gets to choose:
# it is derived here from the site user and site root, so a caller cannot aim
# start/stop/logs at nginx, mariadb, or another tenant's unit.

ensure_proxy_upgrade_map() {
  # `map` only works at http level, so a proxied vhost needs this file to exist
  # before nginx will even load. The installer writes it too, but the panel must
  # not depend on the installer having run since the feature shipped: without it
  # the first proxy vhost fails `nginx -t` and gets rolled back.
  local target=/etc/nginx/conf.d/00-spanel-upgrade-map.conf
  [[ -s "$target" ]] && return 0
  cat >"$target" <<'NGINX'
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
NGINX
  chown root:root "$target"
  chmod 0644 "$target"
}

require_app_name() {
  [[ "$1" =~ ^[a-z0-9]([a-z0-9_-]{0,30}[a-z0-9])?$ ]] || deny "invalid app name: $1"
}

app_unit_name() {
  local user="$1" name="$2"
  printf 'spanel-app-%s-%s' "$user" "$name"
}

app_env_file() {
  # Deliberately outside the customer's home. Inside it the file was reachable
  # through the file manager, went into every site backup, and the permission
  # hardening pass in update.sh handed ownership back to the site user on the
  # next update. Nothing but root needs to read it: systemd loads
  # EnvironmentFile before dropping privileges, and the docker CLI runs as root.
  local user="$1" name="$2"
  printf '%s/apps/%s-%s.env' "$SPANEL_DATA_DIR" "$user" "$name"
}

app_compose_file() {
  local user="$1" name="$2"
  printf '%s/apps/%s-%s.compose.yml' "$SPANEL_DATA_DIR" "$user" "$name"
}

write_app_compose_file() {
  # The panel generates this from the customer's imported file, so it should
  # never contain any of the keys below. Checking anyway means a bug in the
  # generator cannot quietly hand a container the whole host.
  local target="$1" line tmp
  install -d -o root -g root -m 0700 "${SPANEL_DATA_DIR}/apps"
  tmp="${target}.spanel-tmp"
  install -m 0600 -o root -g root /dev/null "$tmp"
  cat >"$tmp"
  if grep -Eq '^[[:space:]]*(privileged|network_mode|pid|ipc|userns_mode|devices|cgroup_parent|volumes_from|build|env_file):' "$tmp"; then
    rm -f "$tmp"
    deny "generated compose contains a forbidden key"
  fi
  if grep -Eq '^[[:space:]]*-[[:space:]]*"?/' "$tmp"; then
    rm -f "$tmp"
    deny "generated compose mounts a host path"
  fi
  # The long form of the same thing: the panel only ever writes tmpfs this way.
  if grep -Eq '^[[:space:]]*(type:[[:space:]]*"?bind|source:[[:space:]]*"?/)' "$tmp"; then
    rm -f "$tmp"
    deny "generated compose mounts a host path"
  fi
  grep -q '^services:' "$tmp" || { rm -f "$tmp"; deny "generated compose has no services"; }
  mv -f "$tmp" "$target"
  chown root:root "$target"
  chmod 0600 "$target"
}

ensure_compose_bind_dirs() {
  local compose_file="$1" user="$2" app_dir="$3" line source target
  # The file was generated by the panel, so the shape is known: every bind mount
  # is a list entry "- ./path:/inside". Sources were already checked for `..` and
  # for absolute paths before they got here; check again rather than trust that.
  while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ "$line" == "- ./"*":/"* ]] || continue
    source="${line#- ./}"
    source="${source%%:*}"
    [[ -n "$source" && "$source" != *".."* ]] || continue
    target="${app_dir}/${source}"
    if [[ ! -e "$target" ]]; then
      install -d -m 0750 "$target"
      harden_site_dir "$target" "$user"
    elif [[ -d "$target" && "$(stat -c %U "$target")" == "root" ]]; then
      # Docker got there first on an earlier deploy and made it root-owned, so
      # the container could not write into its own mount. Take it back.
      harden_site_dir "$target" "$user"
    fi
  done < "$compose_file"
}

write_compose_app_unit() {
  local unit_path="$1" app_label="$2" user="$3" app_dir="$4" compose_file="$5" project="$6"
  local identifier
  identifier="$(basename "$unit_path" .service)"
  # Runs as root because it drives the Docker socket. Every container inside the
  # generated file is capped, capability-stripped and published on loopback only.
  cat >"$unit_path" <<UNIT
[Unit]
Description=SPanel compose application ${app_label} (${user})
After=network-online.target docker.service
Requires=docker.service

[Service]
Type=exec
WorkingDirectory=${app_dir}
ExecStartPre=-/usr/bin/docker compose -f ${compose_file} --project-directory ${app_dir} -p ${project} down --remove-orphans
ExecStart=/usr/bin/docker compose -f ${compose_file} --project-directory ${app_dir} -p ${project} up --remove-orphans
ExecStop=/usr/bin/docker compose -f ${compose_file} --project-directory ${app_dir} -p ${project} down
Restart=always
RestartSec=5
TimeoutStartSec=600
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${identifier}

[Install]
WantedBy=multi-user.target
UNIT
}

app_container_name() {
  local user="$1" name="$2"
  printf 'spanel-%s-%s' "$user" "$name"
}

app_directory() {
  # Apps live side by side under the owner's home, one directory each. The
  # customer's SFTP is chrooted to that home, so they can upload code without
  # the app being tied to any website.
  local user="$1" name="$2"
  printf '%s/%s/apps/%s' "$HOME_ROOT" "$user" "$name"
}

ipv6_available() {
  # A global address, not the loopback and not a link-local one: those cannot
  # carry traffic from outside, so listening on them would prove nothing.
  #
  # Read it all before testing it. `ip | grep -q` looks equivalent and is not:
  # iproute2 flushes one line per address, so with two addresses grep can exit
  # on the first while ip is still writing the second, and under pipefail the
  # SIGPIPE that follows becomes the answer. A server with more than one IPv6
  # address was intermittently told it had none.
  local found
  found="$(ip -6 -o addr show scope global 2>/dev/null || true)"
  [[ -n "$found" ]]
}

ipv6_global_addresses() {
  ip -6 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1
}

ipv6_is_enabled() {
  [[ -f "$PANEL_IPV6_MARKER" ]]
}

# Add or remove the IPv6 twin of every listen directive SPanel manages. The
# 443 lines are written by certbot, not by the panel, so this works on the
# files as they are rather than re-rendering them.
nginx_ipv6_rewrite() {
  local mode="$1"
  python3 - "$mode" <<'PY'
import glob
import re
import sys

mode = sys.argv[1]
# SPanel owns everything in conf.d; the distribution's own vhosts are not here.
# certbot writes "listen 443 ssl; # managed by Certbot", so the trailing
# comment has to be allowed or every HTTPS vhost would be skipped.
listen_v4 = re.compile(r"^(\s*)listen\s+((?:\d{1,3}\.){3}\d{1,3}:)?(\d+)([^;]*);\s*(#.*)?$")
listen_v6 = re.compile(r"^\s*listen\s+\[::\]:")
changed = []

for path in sorted(glob.glob("/etc/nginx/conf.d/*.conf")):
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    out = []
    for index, line in enumerate(lines):
        if listen_v6.match(line):
            # Drop the ones we added; "off" keeps nothing, "on" re-adds below.
            continue
        out.append(line)
        if mode != "on":
            continue
        match = listen_v4.match(line)
        if not match:
            continue
        indent, address, port, rest, _comment = match.groups()
        if address and not address.startswith("0.0.0.0"):
            # A vhost pinned to one IPv4 address has no IPv6 counterpart to
            # guess, so leave it exactly as the operator wrote it.
            continue
        out.append(f"{indent}listen [::]:{port}{rest};")
    if out != lines:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(out) + "\n")
        changed.append(path)

for path in changed:
    print(path)
PY
}

# Apply whatever the marker says, with every touched file restored if nginx
# refuses the result. Silent no-op when the switch is off and nothing carries
# an IPv6 listen line already.
nginx_ipv6_apply() {
  local mode backup file
  mode="off"
  ipv6_is_enabled && mode="on"
  backup="$(mktemp -d /tmp/spanel-ipv6-backup.XXXXXX)"
  for file in /etc/nginx/conf.d/*.conf; do
    [[ -f "$file" ]] || continue
    cp -a "$file" "${backup}/$(basename "$file")"
  done
  nginx_ipv6_rewrite "$mode" >/dev/null
  if ! nginx -t >/dev/null 2>&1; then
    for file in "$backup"/*.conf; do
      [[ -f "$file" ]] || continue
      cp -a "$file" "/etc/nginx/conf.d/$(basename "$file")"
    done
    rm -rf "$backup"
    return 1
  fi
  rm -rf "$backup"
  systemctl reload nginx
}

malware_scan_file_list() {
  # One find over the machine, with the noise pruned. Ten seconds on a normal
  # VPS, and it buys an exact total so the panel can show real progress.
  local args=() path
  for path in "${MALWARE_SCAN_PRUNE[@]}"; do
    args+=(-path "$path" -prune -o)
  done
  find / "${args[@]}" -type f -print 2>/dev/null
}

run_malware_server_scan() {
  local job="$1" list log total rc
  [[ "$job" =~ ^[0-9a-f]{8,64}$ ]] || deny "invalid scan job id"
  command -v clamdscan >/dev/null 2>&1 || deny "clamdscan is not installed"
  systemctl is-active --quiet clamav-daemon || deny "clamav-daemon is not running"
  install -d -o spanel -g spanel -m 0750 "$MALWARE_JOBS_DIR"
  list="${MALWARE_JOBS_DIR}/${job}.files"
  log="${MALWARE_JOBS_DIR}/${job}.scan.log"
  rm -f "$list" "$log"

  malware_scan_file_list >"$list"
  total="$(wc -l <"$list")"
  # The panel reads its progress out of this file while the scan runs, so the
  # total has to be in there rather than only in the exit output.
  printf 'total=%s\n' "$total" >"$log"
  chown spanel:spanel "$list" "$log"
  chmod 0640 "$list" "$log"

  # --fdpass hands clamd an open descriptor, which is the only way it reads
  # files its own user cannot. Niced hard: a scan must never be the reason a
  # website goes slow.
  rc=0
  nice -n 19 ionice -c3 clamdscan --fdpass --stdout --no-summary --file-list="$list" >>"$log" 2>&1 || rc=$?
  rm -f "$list"
  printf 'total=%s\n' "$total"
  printf 'exit=%s\n' "$rc"
}

install_sni_cert_pair() {
  local domain="$1" cert="$2" key="$3" target="${PANEL_SNI_DIR}/${domain}"
  install -d -o root -g spanel -m 0750 "$target"
  install -m 0640 -o root -g spanel "$cert" "${target}/fullchain.pem"
  install -m 0640 -o root -g spanel "$key" "${target}/privkey.pem"
}

sync_panel_sni_certificates() {
  # Every certificate on this machine, copied where the panel can read it.
  # Manual uploads are written after Let's Encrypt on purpose: when a domain
  # has both, the uploaded one is what nginx serves, so it is what the panel
  # should serve too.
  local live_dir manual_dir domain target keep served_domain
  local -a served=()
  install -d -o root -g spanel -m 0750 /etc/spanel "$PANEL_SNI_DIR"
  for live_dir in /etc/letsencrypt/live/*/; do
    [[ -f "${live_dir}fullchain.pem" && -f "${live_dir}privkey.pem" ]] || continue
    domain="$(basename "$live_dir")"
    is_domain "$domain" || continue
    install_sni_cert_pair "$domain" "${live_dir}fullchain.pem" "${live_dir}privkey.pem"
    served+=("$domain")
  done
  for manual_dir in /etc/nginx/spanel/ssl/sites/*/; do
    [[ -f "${manual_dir}fullchain.crt" && -f "${manual_dir}privkey.key" ]] || continue
    domain="$(basename "$manual_dir")"
    is_domain "$domain" || continue
    install_sni_cert_pair "$domain" "${manual_dir}fullchain.crt" "${manual_dir}privkey.key"
    served+=("$domain")
  done
  # A certificate that is gone must stop being served, or the panel would keep
  # answering for a domain this server no longer hosts.
  for target in "$PANEL_SNI_DIR"/*/; do
    [[ -d "$target" ]] || continue
    domain="$(basename "$target")"
    keep=0
    for served_domain in ${served[@]+"${served[@]}"}; do
      [[ "$served_domain" == "$domain" ]] && { keep=1; break; }
    done
    (( keep == 1 )) || rm -rf -- "$target"
  done
  printf '%s\n' ${served[@]+"${served[@]}"}
}

install_sni_renewal_hook() {
  # The panel reads these copies again whenever they change on disk, so a
  # renewal needs no restart - only a fresh copy.
  install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
  cat >/etc/letsencrypt/renewal-hooks/deploy/spanel-sni-certs <<'HOOK'
#!/usr/bin/env bash
# Installed by SPanel. Keeps the panel's per-hostname certificate copies fresh.
set -euo pipefail
# RENEWED_LINEAGE is set by certbot to the live directory that just changed.
[[ -n "${RENEWED_LINEAGE:-}" ]] || exit 0
domain="$(basename "$RENEWED_LINEAGE")"
[[ "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$ ]] || exit 0
[[ -f "${RENEWED_LINEAGE}/fullchain.pem" && -f "${RENEWED_LINEAGE}/privkey.pem" ]] || exit 0
install -d -o root -g spanel -m 0750 /etc/spanel /etc/spanel/sni "/etc/spanel/sni/${domain}"
install -m 0640 -o root -g spanel "${RENEWED_LINEAGE}/fullchain.pem" "/etc/spanel/sni/${domain}/fullchain.pem"
install -m 0640 -o root -g spanel "${RENEWED_LINEAGE}/privkey.pem" "/etc/spanel/sni/${domain}/privkey.pem"
HOOK
  chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/spanel-sni-certs
}

install_panel_cert_renewal_hook() {
  # A certificate the panel borrowed from a website is a copy, and copies go
  # stale: certbot renews the website's lineage every couple of months and the
  # panel would keep serving the expired one until someone noticed.
  install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
  cat >/etc/letsencrypt/renewal-hooks/deploy/spanel-panel-cert <<'HOOK'
#!/usr/bin/env bash
# Installed by SPanel. Refreshes the panel's copy of a website certificate.
set -euo pipefail
env_file="/opt/spanel/backend/.env"
[[ -f "$env_file" ]] || exit 0
mode="$(sed -nE 's/^PANEL_SSL_MODE=//p' "$env_file" | tail -n1 | tr -d '"')"
domain="$(sed -nE 's/^PANEL_DOMAIN=//p' "$env_file" | tail -n1 | tr -d '"')"
[[ "$mode" == "domain" && -n "$domain" ]] || exit 0
# RENEWED_LINEAGE is set by certbot to the live directory that just changed.
[[ "${RENEWED_LINEAGE:-}" == "/etc/letsencrypt/live/${domain}" ]] || exit 0
install -d -o root -g spanel -m 0750 /etc/spanel
install -m 0640 -o root -g spanel "${RENEWED_LINEAGE}/fullchain.pem" /etc/spanel/panel-fullchain.pem
install -m 0640 -o root -g spanel "${RENEWED_LINEAGE}/privkey.pem" /etc/spanel/panel-privkey.pem
systemctl restart spanel-api || true
HOOK
  chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/spanel-panel-cert
}

require_backup_path() {
  # These commands write and read files as root at a path the panel chose, which
  # is only safe while that path cannot leave the backup tree — directly, through
  # .., or through a symlinked parent.
  local target="$1" parent real_parent
  [[ -n "$target" ]] || deny "empty backup path"
  [[ "$target" != *$'\n'* ]] || deny "invalid backup path"
  [[ "$target" != *".."* ]] || deny "backup path may not contain .."
  case "$target" in
    "${BACKUP_ROOT}"/*) : ;;
    *) deny "backup path must be under ${BACKUP_ROOT}" ;;
  esac
  # -o spanel, not -o root. `install -d` rewrites the owner of a directory
  # that already exists, so the root:spanel this used to set would take
  # ${BACKUP_ROOT} away from the panel the first time an operator exported an
  # application - 0750 with the group gives read and traverse, not write, and
  # from then on every website backup fails to create its own directory. The
  # installer and spanelctl both make this spanel:spanel; this now agrees.
  install -d -m 0750 -o spanel -g spanel "$BACKUP_ROOT"
  parent="$(dirname "$target")"
  [[ -d "$parent" ]] || deny "backup directory does not exist: $parent"
  real_parent="$(readlink -f "$parent")" || deny "cannot resolve $parent"
  case "$real_parent" in
    "${BACKUP_ROOT}"|"${BACKUP_ROOT}"/*) : ;;
    *) deny "backup directory escapes ${BACKUP_ROOT}" ;;
  esac
}

ensure_user_backup_dir() {
  # ${BACKUP_ROOT}/users was never created by the installer, so it belonged to
  # whichever process reached it first. On a server where that was a root one
  # it came out root:root, and from then on every scheduled account backup
  # failed with "[Errno 13] Permission denied" on the account directory one
  # level below - the panel owns ${BACKUP_ROOT} but cannot write inside a
  # root-owned child of it.
  #
  # The caller passes a single name, never a path, and this builds the path
  # itself: nothing a compromised API process sends can point this chown
  # somewhere else.
  local name="$1" users_dir
  [[ "$name" =~ ^[A-Za-z0-9._-]{3,64}$ ]] || deny "invalid backup directory name: $name"
  [[ "$name" != *".."* ]] || deny "invalid backup directory name: $name"

  users_dir="${BACKUP_ROOT}/users"
  install -d -m 0750 -o spanel -g spanel "$BACKUP_ROOT"
  install -d -m 0750 -o spanel -g spanel "$users_dir"
  # Not the whole tree: only the container. Archives already sitting in an
  # account's directory are handed over with it, because the panel is what
  # prunes them, but the sweep stops at ${BACKUP_ROOT}/users.
  install -d -m 0750 -o spanel -g spanel "${users_dir}/${name}"
  chown -R spanel:spanel "${users_dir}/${name}"
  printf '%s' "${users_dir}/${name}"
}

ensure_app_directory() {
  local user="$1" name="$2" apps_root target
  require_linux_user "$user"
  require_app_name "$name"
  apps_root="$(printf '%s/%s/apps' "$HOME_ROOT" "$user")"
  target="$(app_directory "$user" "$name")"
  [[ -d "${HOME_ROOT}/${user}" ]] || deny "home directory missing for $user"
  ensure_sites_group
  install -d -m 0750 "$apps_root"
  install -d -m 0750 "$target"
  harden_site_dir "$apps_root" "$user"
  harden_site_dir "$target" "$user"
  printf '%s' "$target"
}

remove_legacy_app_units() {
  # Units and containers from the release where an app was named after the
  # website it hung off. Nothing else would ever clean them up.
  local user="$1" name="$2" legacy unit stale
  for legacy in /etc/systemd/system/spanel-app-"${user}"-*-"${name}".service; do
    [[ -f "$legacy" ]] || continue
    unit="$(basename "$legacy")"
    systemctl disable --now "$unit" 2>/dev/null || true
    rm -f "$legacy"
  done
  if command -v docker >/dev/null 2>&1; then
    # The trailing `|| true` matters: under `set -o pipefail` a grep that
    # matches nothing fails the pipeline, and `set -e` then kills the helper
    # without printing anything at all.
    stale="$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E "^spanel-${user}-[0-9a-f]{8}-${name}$" || true)"
    for legacy in $stale; do
      docker rm -f "$legacy" >/dev/null 2>&1 || true
    done
  fi
  rm -f "${SPANEL_DATA_DIR}/apps/${user}"-*-"${name}.env"
}

require_app_port() {
  [[ "$1" =~ ^[0-9]{4,5}$ ]] || deny "invalid app port: $1"
  (( $1 >= 21000 && $1 <= 21999 )) || deny "app port outside the managed range: $1"
}

require_container_port() {
  [[ "$1" =~ ^[0-9]{1,5}$ ]] || deny "invalid container port: $1"
  (( $1 >= 1 && $1 <= 65535 )) || deny "container port out of range: $1"
}

require_app_memory() {
  [[ "$1" =~ ^[0-9]{2,5}$ ]] || deny "invalid memory limit: $1"
  (( $1 >= 64 && $1 <= 16384 )) || deny "memory limit out of range: $1"
}

require_app_cpus() {
  [[ "$1" =~ ^[0-9]{1,2}(\.[0-9])?$ ]] || deny "invalid cpu limit: $1"
}

require_node_major() {
  [[ "$1" =~ ^[1-9][0-9]$ ]] || deny "invalid node major version: $1"
}

require_docker_image() {
  # Registry/name[:tag][@digest]. No whitespace and no leading dash, so the
  # reference can never be read by docker as a flag.
  [[ "$1" =~ ^[a-z0-9][a-z0-9._/-]{0,159}(:[A-Za-z0-9._-]{1,127})?(@sha256:[a-f0-9]{64})?$ ]] \
    || deny "invalid container image reference: $1"
  case "$1" in
    -*|*..*) deny "invalid container image reference: $1" ;;
  esac
}

resolve_node_bin_dir() {
  local major="$1" sys_major=""
  if [[ -x "/opt/spanel/node/${major}/bin/node" ]]; then
    printf '/opt/spanel/node/%s/bin' "$major"
    return 0
  fi
  if [[ -x /usr/bin/node ]]; then
    sys_major="$(/usr/bin/node -p 'process.versions.node.split(".")[0]' 2>/dev/null || true)"
    if [[ "$sys_major" == "$major" ]]; then
      printf '/usr/bin'
      return 0
    fi
  fi
  return 1
}

write_app_env_file() {
  # Environment arrives on stdin as KEY=value lines, and only root ever reads
  # the result, so the site user cannot edit a value past validation.
  local target="$1" line tmp
  install -d -o root -g root -m 0700 "${SPANEL_DATA_DIR}/apps"
  tmp="${target}.spanel-tmp"
  install -m 0600 -o root -g root /dev/null "$tmp"
  while IFS= read -r line; do
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" =~ ^[A-Z_][A-Z0-9_]*= ]] || deny "invalid environment line (expected KEY=value)"
    printf '%s\n' "$line" >>"$tmp"
  done
  mv -f "$tmp" "$target"
  chown root:root "$target"
  chmod 0600 "$target"
}

write_node_app_unit() {
  local unit_path="$1" app_label="$2" user="$3" app_dir="$4" env_file="$5"
  local port="$6" memory="$7" node_major="$8" app_exec="${9}" app_arg="${10}"
  local bin_dir exec_start identifier
  bin_dir="$(resolve_node_bin_dir "$node_major")" \
    || deny "Node ${node_major} is not installed; run node-install ${node_major} first"
  case "$app_exec" in
    node) exec_start="${bin_dir}/node ${app_arg}" ;;
    npm)  exec_start="${bin_dir}/npm run --silent ${app_arg}" ;;
    npx)  exec_start="${bin_dir}/npx --yes ${app_arg}" ;;
    yarn)
      if [[ -x "${bin_dir}/yarn" ]]; then
        exec_start="${bin_dir}/yarn ${app_arg}"
      elif [[ -x /usr/local/bin/yarn ]]; then
        exec_start="/usr/local/bin/yarn ${app_arg}"
      else
        deny "yarn is not installed; use npm instead"
      fi
      ;;
    *) deny "invalid start command: $app_exec" ;;
  esac
  identifier="$(basename "$unit_path" .service)"
  cat >"$unit_path" <<UNIT
[Unit]
Description=SPanel application ${app_label} (${user})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${user}
Group=${user}
WorkingDirectory=${app_dir}
EnvironmentFile=-${env_file}
# HOST is forced so a misconfigured app cannot bind a public interface. The
# firewall is the second layer here, not the only one.
Environment=HOST=127.0.0.1
Environment=NODE_ENV=production
Environment=PORT=${port}
Environment=HOME=${HOME_ROOT}/${user}
Environment=PATH=${bin_dir}:/usr/local/bin:/usr/bin:/bin
ExecStart=${exec_start}
Restart=always
RestartSec=5
MemoryAccounting=yes
MemoryMax=${memory}M
TasksMax=256
LimitNOFILE=8192
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${app_dir}
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${identifier}

[Install]
WantedBy=multi-user.target
UNIT
}

write_python_app_unit() {
  local unit_path="$1" app_label="$2" user="$3" app_dir="$4" env_file="$5"
  local port="$6" memory="$7" app_exec="${8}" app_arg="${9}"
  local venv_dir="${app_dir}/.venv" exec_start identifier
  if [[ ! -x "${venv_dir}/bin/python" ]]; then
    command -v python3 >/dev/null 2>&1 || deny "python3 is not installed on the server"
    runuser -u "$user" -- python3 -m venv "$venv_dir" 2>/dev/null \
      || deny "failed to initialize Python virtualenv in ${venv_dir}; install python3-venv"
  fi
  case "$app_exec" in
    uvicorn)
      exec_start="${venv_dir}/bin/python -m uvicorn ${app_arg} --host 127.0.0.1 --port ${port}"
      ;;
    gunicorn)
      exec_start="${venv_dir}/bin/python -m gunicorn ${app_arg} --bind 127.0.0.1:${port}"
      ;;
    python)
      exec_start="${venv_dir}/bin/python ${app_arg}"
      ;;
    *) deny "invalid Python start command: $app_exec" ;;
  esac
  identifier="$(basename "$unit_path" .service)"
  cat >"$unit_path" <<UNIT
[Unit]
Description=SPanel Python application ${app_label} (${user})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${user}
Group=${user}
WorkingDirectory=${app_dir}
EnvironmentFile=-${env_file}
Environment=HOST=127.0.0.1
Environment=PORT=${port}
Environment=PYTHONUNBUFFERED=1
Environment=VIRTUAL_ENV=${venv_dir}
Environment=HOME=${HOME_ROOT}/${user}
Environment=PATH=${venv_dir}/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=${exec_start}
Restart=always
RestartSec=5
MemoryAccounting=yes
MemoryMax=${memory}M
TasksMax=256
LimitNOFILE=8192
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${app_dir}
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${identifier}

[Install]
WantedBy=multi-user.target
UNIT
}

write_docker_app_unit() {
  local unit_path="$1" app_label="$2" user="$3" container="$4" app_dir="$5" env_file="$6"
  local port="$7" memory="$8" image="$9" container_port="${10}" cpus="${11}"
  local uid gid identifier
  uid="$(id -u "$user")" || deny "cannot resolve uid for $user"
  gid="$(id -g "$user")" || deny "cannot resolve gid for $user"
  identifier="$(basename "$unit_path" .service)"
  # The unit runs as root because it talks to the Docker socket, but the
  # container is the site user, has no capabilities, cannot gain privileges and
  # publishes only on loopback. The customer never gets socket access - that
  # would be equivalent to handing out root.
  cat >"$unit_path" <<UNIT
[Unit]
Description=SPanel container ${app_label} (${user})
After=network-online.target docker.service
Requires=docker.service

[Service]
Type=exec
ExecStartPre=-/usr/bin/docker rm -f ${container}
ExecStart=/usr/bin/docker run --rm --name ${container} \\
  --user ${uid}:${gid} \\
  --publish 127.0.0.1:${port}:${container_port} \\
  --env-file ${env_file} \\
  --env PORT=${container_port} \\
  --env HOST=0.0.0.0 \\
  --volume ${app_dir}:/app \\
  --workdir /app \\
  --memory ${memory}m \\
  --memory-swap ${memory}m \\
  --cpus ${cpus} \\
  --pids-limit 256 \\
  --cap-drop ALL \\
  --security-opt no-new-privileges \\
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 \\
  ${image}
ExecStop=/usr/bin/docker stop --time 20 ${container}
Restart=always
RestartSec=5
TimeoutStartSec=300
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${identifier}

[Install]
WantedBy=multi-user.target
UNIT
}

write_docker_daemon_config() {
  install -d -m 0755 /etc/docker
  # Log rotation is not optional on a shared host: an unbounded container log
  # fills the disk and takes every other site down with it.
  cat >/etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" },
  "live-restore": true,
  "no-new-privileges": true,
  "default-address-pool": [ { "base": "172.31.0.0/16", "size": 24 } ]
}
JSON
}

install_docker_firewall_guard() {
  # Docker publishes ports by DNAT in PREROUTING and allows them through its own
  # FORWARD chain, so a published port never passes through SPANEL-INPUT. Panel
  # apps only ever publish on loopback, but a container started by hand could
  # publish on 0.0.0.0 and be reachable while the firewall looks enabled.
  # DOCKER-USER is the one chain Docker leaves to the operator.
  command -v iptables >/dev/null 2>&1 || return 0
  command -v docker >/dev/null 2>&1 || return 0
  local iface
  iface="$(ip route show default 2>/dev/null | awk '/^default/{print $5; exit}')"
  [[ -n "$iface" ]] || return 0
  iptables -w -N DOCKER-USER 2>/dev/null || true
  iptables -w -D DOCKER-USER -i "$iface" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN 2>/dev/null || true
  iptables -w -D DOCKER-USER -i "$iface" -j DROP 2>/dev/null || true
  iptables -w -I DOCKER-USER 1 -i "$iface" -j DROP
  iptables -w -I DOCKER-USER 1 -i "$iface" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
}

install_docker_engine() {
  if command -v docker >/dev/null 2>&1; then
    write_docker_daemon_config
    systemctl restart docker >/dev/null 2>&1 || true
    install_docker_firewall_guard
    echo "Docker is already installed"
    return 0
  fi
  local distro codename arch
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu) distro=ubuntu ;;
    debian) distro=debian ;;
    *) deny "unsupported distribution for Docker install: ${ID:-unknown}" ;;
  esac
  codename="${VERSION_CODENAME:-}"
  [[ -n "$codename" ]] || deny "cannot determine distribution codename"
  arch="$(dpkg --print-architecture)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update --allow-releaseinfo-change
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${distro}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$arch" "$distro" "$codename" >/etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  write_docker_daemon_config
  systemctl enable --now docker
  install_docker_firewall_guard
  echo "Docker installed"
}

install_node_major() {
  local major="$1" arch tmp url latest
  require_node_major "$major"
  if [[ -x "/opt/spanel/node/${major}/bin/node" ]]; then
    echo "Node ${major} is already installed"
    return 0
  fi
  case "$(dpkg --print-architecture)" in
    amd64) arch=x64 ;;
    arm64) arch=arm64 ;;
    *) deny "unsupported architecture for Node install" ;;
  esac
  latest="$(curl -fsSL https://nodejs.org/dist/index.json | SPANEL_NODE_MAJOR="$major" python3 -c 'import json, os, sys
major = os.environ["SPANEL_NODE_MAJOR"]
names = [item["version"] for item in json.load(sys.stdin) if item["version"].startswith("v" + major + ".")]
print(names[0] if names else "")' || true)"
  [[ "$latest" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || deny "no Node ${major} release found upstream"
  url="https://nodejs.org/dist/${latest}/node-${latest}-linux-${arch}.tar.xz"
  tmp="$(mktemp -d)"
  curl -fsSL "$url" -o "${tmp}/node.tar.xz" || { rm -rf "$tmp"; deny "cannot download ${url}"; }
  install -d -m 0755 /opt/spanel/node
  rm -rf "/opt/spanel/node/${major}.tmp"
  install -d -m 0755 "/opt/spanel/node/${major}.tmp"
  tar -xJf "${tmp}/node.tar.xz" -C "/opt/spanel/node/${major}.tmp" --strip-components=1
  rm -rf "$tmp"
  rm -rf "/opt/spanel/node/${major}"
  mv "/opt/spanel/node/${major}.tmp" "/opt/spanel/node/${major}"
  chown -R root:root "/opt/spanel/node/${major}"
  echo "Node ${latest} installed to /opt/spanel/node/${major}"
}

list_installed_node_majors() {
  local dir
  [[ -d /opt/spanel/node ]] || return 0
  for dir in /opt/spanel/node/*; do
    [[ -x "$dir/bin/node" ]] || continue
    basename "$dir"
  done
}

is_in() {
  local needle="$1"; shift
  local x
  for x in "$@"; do [[ "$x" == "$needle" ]] && return 0; done
  return 1
}

is_allowed_service() {
  local service="$1" php_version=""
  if is_in "$service" "${ALLOWED_SERVICES[@]}"; then
    return 0
  fi
  if [[ "$service" =~ ^php([0-9]+\.[0-9]+)-fpm$ ]]; then
    php_version="${BASH_REMATCH[1]}"
    [[ -f "/etc/php/${php_version}/fpm/php-fpm.conf" ]] && return 0
  fi
  # A hosting account's own PHP-FPM master, once SPanel has set it up.
  if [[ "$service" =~ ^spanel-php(5\.6|7\.4|8\.[0-5])-fpm@([a-z_][a-z0-9_-]{2,31})$ ]]; then
    [[ -f "${SPANEL_PHP_FPM_ROOT}/${BASH_REMATCH[1]}/${BASH_REMATCH[2]}/php-fpm.conf" ]] && return 0
  fi
  return 1
}

require_safe_path() {
  local prefix="$1" path="$2"
  # Reject path traversal components, newlines, and empty input. Bash strings
  # cannot carry NUL bytes, so there is no separate NUL pattern here.
  # Note: we cannot use `*..*` as a glob because that would also reject
  # legitimate filenames that just happen to contain a dot adjacent to a dot
  # via Bash's pattern matching quirks; instead we match the `..` only when
  # it actually forms a path component.
  case "$path" in
    *$'\n'*) deny "unsafe path: $path" ;;
    "") deny "empty path" ;;
    "..") deny "path traversal not allowed" ;;
    "../"*|*"/.."|*"/../"*) deny "path traversal not allowed" ;;
  esac
  local resolved
  resolved=$(readlink -m "$path") || deny "cannot resolve $path"
  case "$resolved/" in
    "$prefix"/*) ;;
    *) deny "path outside $prefix: $resolved" ;;
  esac
  echo "$resolved"
}

require_domain() {
  local d="$1"
  [[ "$d" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]] \
    || deny "invalid domain: $d"
}

require_email() {
  local e="$1"
  [[ "$e" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] \
    || deny "invalid email: $e"
}

require_port() {
  [[ "$1" =~ ^[0-9]{1,5}$ ]] || deny "invalid port: $1"
  (( $1 >= 1 && $1 <= 65535 )) || deny "port out of range: $1"
}

require_tail_lines() {
  [[ "$1" =~ ^[0-9]{1,4}$ ]] || deny "invalid log line count: $1"
  (( $1 >= 1 && $1 <= 5000 )) || deny "log line count out of range: $1"
}

require_proto() {
  [[ "$1" == "tcp" || "$1" == "udp" ]] || deny "invalid protocol: $1"
}

require_php_version() {
  [[ "$1" =~ ^(5\.6|7\.4|8\.0|8\.1|8\.2|8\.3|8\.4|8\.5)$ ]] || deny "invalid PHP version: $1"
}

require_linux_user() {
  [[ "$1" =~ ^[a-z_][a-z0-9_-]{2,31}$ ]] || deny "invalid panel Linux user: $1"
  case "$1" in
    root|daemon|bin|sys|sync|games|man|lp|mail|news|uucp|proxy|www-data|backup|list|irc|_apt|nobody|spanel|spanel-sites|spanel-sftp|spanel-sftp-site|mysql|redis|nginx)
      deny "reserved panel Linux user: $1" ;;
    # The sftp_ namespace belongs to per-website sub-accounts. Keeping the two
    # apart is what lets require_sftp_sub_user prove that no sub-account verb
    # can ever be aimed at a panel user, or the reverse.
    sftp_*)
      deny "reserved panel Linux user prefix: $1" ;;
  esac
}

managed_root_depth() {
  # How many leading components of a path under a user's home make up a managed
  # root. A website root is <domain>; an application root is apps/<name>. Both
  # keep operations inside a named subtree instead of the bare home directory.
  local relative="$1" first second
  first="${relative%%/*}"
  if [[ "$first" == "apps" ]]; then
    [[ "$relative" == */* ]] || deny "application path is missing an application name"
    second="${relative#*/}"
    second="${second%%/*}"
    require_app_name "$second"
    printf '2'
    return 0
  fi
  require_site_domain_segment "$first"
  printf '1'
}

require_site_domain_segment() {
  [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]] \
    || deny "invalid site domain path segment: $1"
}

read_site_logs_many() {
  # read_site_logs_many <access|error> <lines> <domain>...
  # One spawn for every site's log instead of one sudo round-trip each. Output
  # is per-domain, each block introduced by a US (0x1f) record separator so the
  # panel can split it without guessing.
  local kind="$1" lines="$2"; shift 2
  [[ "$kind" == "access" || "$kind" == "error" ]] || deny "invalid log kind: $kind"
  require_tail_lines "$lines"
  local domain path
  for domain in "$@"; do
    require_domain "$domain"
    path="/var/log/nginx/${domain}.${kind}.log"
    printf '\x1f%s\n' "$domain"
    if [[ -f "$path" && ! -L "$path" ]]; then
      tail -n "$lines" -- "$path" 2>/dev/null || true
    else
      printf 'SPANEL_LOG_MISSING\n'
    fi
  done
}

read_site_log() {
  local domain="$1" kind="$2" lines="$3" path resolved
  require_domain "$domain"
  [[ "$kind" == "access" || "$kind" == "error" ]] || deny "invalid log kind: $kind"
  require_tail_lines "$lines"
  path="/var/log/nginx/${domain}.${kind}.log"
  resolved=$(readlink -m "$path") || deny "cannot resolve log path"
  case "$resolved" in
    /var/log/nginx/*) ;;
    *) deny "log path outside /var/log/nginx: $resolved" ;;
  esac
  echo "SPANEL_LOG_PATH=$resolved" >&2
  if [[ ! -f "$resolved" ]]; then
    echo "SPANEL_LOG_MISSING=1" >&2
    return 0
  fi
  tail -n "$lines" -- "$resolved"
}

require_managed_path() {
  local path="$1" user="${2:-}"
  local resolved first_part relative domain_part
  resolved=$(require_safe_path "$HOME_ROOT" "$path")
  if [[ -n "$user" ]]; then
    require_linux_user "$user"
    case "$resolved/" in
      "$HOME_ROOT/$user/"*)
        relative="${resolved#${HOME_ROOT}/${user}/}"
        managed_root_depth "$relative" >/dev/null
        ;;
      *) deny "path is not owned by panel Linux user $user: $resolved" ;;
    esac
  else
    case "$resolved/" in
      "$HOME_ROOT"/*/*)
        first_part="${resolved#${HOME_ROOT}/}"
        first_part="${first_part%%/*}"
        require_linux_user "$first_part"
        relative="${resolved#${HOME_ROOT}/${first_part}/}"
        managed_root_depth "$relative" >/dev/null
        ;;
      *) deny "path outside managed site roots: $resolved" ;;
    esac
  fi
  echo "$resolved"
}

require_bound_managed_path() {
  local user="$1" root="$2" path="$3"
  local normalized_root normalized target target_relative root_relative root_depth
  require_linux_user "$user"
  case "$root" in
    *$'\n'*) deny "unsafe root: $root" ;;
    "") deny "empty root" ;;
    "..") deny "root traversal not allowed" ;;
    "../"*|*"/.."|*"/../"*) deny "root traversal not allowed" ;;
  esac
  [[ "$root" == /* ]] || deny "root must be absolute: $root"
  normalized_root=$(python3 -c 'import os, sys; print(os.path.normpath(sys.argv[1]))' "$root") || deny "cannot normalize $root"
  case "$normalized_root/" in
    "$HOME_ROOT/$user/"*) ;;
    *) deny "root is not owned by panel Linux user $user: $normalized_root" ;;
  esac
  root_relative="${normalized_root#${HOME_ROOT}/${user}/}"
  root_depth="$(managed_root_depth "$root_relative")"
  if [[ "$root_depth" == "1" ]]; then
    [[ "$root_relative" == */* ]] && deny "site root must be a direct domain path: $normalized_root"
  else
    [[ "$root_relative" == */*/* ]] && deny "application root must be apps/<name>: $normalized_root"
  fi

  case "$path" in
    *$'\n'*) deny "unsafe path: $path" ;;
    "") deny "empty path" ;;
    "..") deny "path traversal not allowed" ;;
    "../"*|*"/.."|*"/../"*) deny "path traversal not allowed" ;;
  esac
  [[ "$path" == /* ]] || deny "path must be absolute: $path"
  normalized=$(python3 -c 'import os, sys; print(os.path.normpath(sys.argv[1]))' "$path") || deny "cannot normalize $path"
  case "$normalized/" in
    "$normalized_root"|"$normalized_root/"*) ;;
    *) deny "path outside expected site root: $normalized" ;;
  esac
  target="$normalized"
  target_relative="${target#${HOME_ROOT}/${user}/}"
  [[ "$target" == "$normalized_root" || "$target_relative" == */* ]] || deny "refusing to operate on a panel user home"
  echo "$target"
}

delete_no_follow() {
  local user="$1" root="$2" target="$3"
  python3 - "$user" "$root" "$target" <<'PY'
import os
import stat
import sys

user, root, target = sys.argv[1:4]
base = f"/home/{user}"
root = os.path.normpath(root)
target = os.path.normpath(target)

# A website root is /home/<user>/<domain>; an application root is one level
# deeper, /home/<user>/apps/<name>. Anything else is not a managed tree.
if os.path.dirname(root) not in (base, os.path.join(base, "apps")):
    raise SystemExit("invalid site root")
if target != root and not target.startswith(root + os.sep):
    raise SystemExit("target outside site root")

rel = os.path.relpath(target, base)
if rel.startswith("..") or rel == ".":
    raise SystemExit("target outside site root")

def open_child(parent_fd, name):
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)

base_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    parent_fd = base_fd
    close_parent = False
    parts = rel.split(os.sep)
    for part in parts[:-1]:
        next_fd = open_child(parent_fd, part)
        if close_parent:
            os.close(parent_fd)
        parent_fd = next_fd
        close_parent = True

    leaf = parts[-1]

    def remove_entry(dir_fd, name):
        st = os.lstat(name, dir_fd=dir_fd)
        if stat.S_ISDIR(st.st_mode):
            child_fd = open_child(dir_fd, name)
            try:
                for entry in os.listdir(child_fd):
                    remove_entry(child_fd, entry)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=dir_fd)
        else:
            os.unlink(name, dir_fd=dir_fd)

    remove_entry(parent_fd, leaf)
finally:
    try:
        if 'parent_fd' in locals() and parent_fd != base_fd:
            os.close(parent_fd)
    finally:
        os.close(base_fd)
PY
}

require_terminal_cwd() {
  local path="$1" user="$2" resolved
  require_linux_user "$user"
  resolved=$(require_safe_path "$HOME_ROOT" "$path")
  case "$resolved" in
    "$HOME_ROOT/$user"|"$HOME_ROOT/$user"/*) ;;
    *) deny "terminal cwd is not owned by panel Linux user $user: $resolved" ;;
  esac
  [[ -d "$resolved" ]] || deny "terminal cwd is not a directory: $resolved"
  echo "$resolved"
}

# The open_basedir a PHP interpreter started for a site user must carry.
#
# One definition, three callers: terminal-exec, the wp-site verb, and (mirrored
# in Python, because it renders a crontab line) services/cron.py. It used to
# live inline in terminal-exec only, which is how wp-site and cron came to
# start unconfined interpreters while the terminal was confined.
#
# The boundary is the tenant's own home, not one site root: a customer with
# several sites still has to work across them, and the leak being closed is
# between customers. /var/lib/php/{sessions,uploads}/<user> match the FPM pool.
# /tmp and /usr/share/php are what composer and PEAR-era libraries expect. A
# caller that runs a phar appends that tool's directory itself.
site_open_basedir() {
  local user="$1"
  require_linux_user "$user"
  printf '%s' "$HOME_ROOT/$user:/var/lib/php/sessions/$user:/var/lib/php/uploads/$user:/tmp:/usr/share/php"
}

require_terminal_path_args() {
  local user="$1" cwd="$2" arg resolved
  shift 2
  require_linux_user "$user"
  for arg in "$@"; do
    case "$arg" in
      ""|"-"*|"--") continue ;;
      *$'\n'*|".."|"../"*|*"/.."|*"/../"*) deny "terminal path argument escapes user home: $arg" ;;
    esac
    if [[ "$arg" = /* ]]; then
      resolved=$(readlink -m -- "$arg") || deny "cannot resolve terminal path: $arg"
    else
      resolved=$(readlink -m -- "$cwd/$arg") || deny "cannot resolve terminal path: $arg"
    fi
    case "$resolved/" in
      "$HOME_ROOT/$user/"*) ;;
      *) deny "terminal path argument is outside panel user home: $arg" ;;
    esac
  done
}

require_terminal_download_args() {
  local user="$1" cwd="$2" arg value expect_output=0
  shift 2
  for arg in "$@"; do
    case "${arg,,}" in
      file://*) deny "terminal URL argument uses local file scheme: $arg" ;;
    esac
    if (( expect_output )); then
      require_terminal_path_args "$user" "$cwd" "$arg"
      expect_output=0
      continue
    fi
    case "$arg" in
      --output=*|--output-document=*|-O=*)
        value="${arg#*=}"
        require_terminal_path_args "$user" "$cwd" "$value"
        ;;
      -o|-O|--output|--output-document)
        expect_output=1
        ;;
      http://*|https://*|ftp://*|ftps://*|sftp://*)
        ;;
      -*|"")
        ;;
      *)
        require_terminal_path_args "$user" "$cwd" "$arg"
        ;;
    esac
  done
  (( expect_output == 0 )) || deny "terminal download output path is missing"
}

ensure_sites_group() {
  getent group "$SPANEL_SITES_GROUP" >/dev/null || groupadd --system "$SPANEL_SITES_GROUP"
  usermod -aG "$SPANEL_SITES_GROUP" spanel 2>/dev/null || true
  usermod -aG "$SPANEL_SITES_GROUP" www-data 2>/dev/null || true
}

ensure_sftp_group() {
  getent group "$SPANEL_SFTP_GROUP" >/dev/null || groupadd --system "$SPANEL_SFTP_GROUP"
}

clear_path_acl() {
  local target="$1"
  if command -v setfacl >/dev/null 2>&1; then
    setfacl -b -k "$target" 2>/dev/null || true
  fi
}

protect_site_secret_file() {
  local target="$1" name
  name="$(basename -- "$target")"
  local secret
  for secret in "${SITE_SECRET_FILES[@]}"; do
    if [[ "$name" == "$secret" ]]; then
      chmod 0640 "$target" 2>/dev/null || true
      return 0
    fi
  done
  return 0
}

protect_site_secret_tree() {
  local target="$1" secret
  for secret in "${SITE_SECRET_FILES[@]}"; do
    find "$target" -type f -name "$secret" -exec chmod 0640 {} + 2>/dev/null || true
  done
  return 0
}

harden_site_dir() {
  local target="$1" user="$2"
  chown "$user:$SPANEL_SITES_GROUP" "$target"
  clear_path_acl "$target"
  chmod "$SITE_DIR_MODE" "$target"
  chmod a-s "$target" 2>/dev/null || true
  chmod -t "$target" 2>/dev/null || true
}

harden_site_file() {
  local target="$1" user="$2"
  chown "$user:$SPANEL_SITES_GROUP" "$target"
  clear_path_acl "$target"
  chmod "$SITE_FILE_MODE" "$target"
  chmod a-s "$target" 2>/dev/null || true
  chmod -t "$target" 2>/dev/null || true
  protect_site_secret_file "$target"
}

harden_site_dir_path() {
  local root="$1" target="$2" user="$3" relative current part
  ensure_sites_group
  require_linux_user "$user"
  root=$(readlink -m "$root") || deny "cannot resolve $root"
  target=$(readlink -m "$target") || deny "cannot resolve $target"
  case "$target" in
    "$root"|"$root"/*) ;;
    *) deny "directory path outside site root: $target" ;;
  esac
  [[ -d "$target" ]] || deny "site directory does not exist: $target"
  harden_site_dir_if_foreign "$root" "$user"
  [[ "$target" == "$root" ]] && return 0
  relative="${target#${root}/}"
  current="$root"
  IFS='/' read -r -a root_parts <<< "$relative"
  for part in "${root_parts[@]}"; do
    current="$current/$part"
    [[ -d "$current" ]] || deny "site directory does not exist: $current"
    harden_site_dir_if_foreign "$current" "$user"
  done
}

# A folder that already belongs to the site keeps its mode - saving a file
# inside a 777 upload folder must not make the folder 755. One that is new or
# belongs to someone else (made by root, or by the user outside the site
# group) is brought to the site's owner, group and default mode.
harden_site_dir_if_foreign() {
  local target="$1" user="$2"
  [[ "$(stat -c '%U:%G' -- "$target")" == "$user:$SPANEL_SITES_GROUP" ]] && return 0
  harden_site_dir "$target" "$user"
}

ensure_panel_user_home() {
  local user="$1" home_dir="$HOME_ROOT/$1"
  ensure_sites_group
  ensure_sftp_group
  require_linux_user "$user"
  getent group "$user" >/dev/null || groupadd "$user"
  usermod -aG "$user" www-data 2>/dev/null || true
  chown root:root "$HOME_ROOT"
  chmod 0711 "$HOME_ROOT"
  chmod a-s "$HOME_ROOT" 2>/dev/null || true
  chmod -t "$HOME_ROOT" 2>/dev/null || true
  if ! id -u "$user" >/dev/null 2>&1; then
    useradd --create-home --home-dir "$home_dir" --shell /usr/sbin/nologin --gid "$user" "$user"
  fi
  usermod --home "$home_dir" --shell /usr/sbin/nologin --gid "$user" "$user" 2>/dev/null || true
  usermod -aG "$SPANEL_SFTP_GROUP" "$user" 2>/dev/null || true
  mkdir -p "$home_dir"
  chown "root:$user" "$home_dir"
  chmod 0751 "$home_dir"
  chmod a-s "$home_dir" 2>/dev/null || true
  chmod -t "$home_dir" 2>/dev/null || true
  clear_path_acl "$home_dir"
}

set_panel_user_password() {
  local user="$1" password
  require_linux_user "$user"
  id -u "$user" >/dev/null 2>&1 || deny "panel Linux user does not exist: $user"
  password="$(cat)"
  password="${password%$'\n'}"
  [[ ${#password} -ge 12 && ${#password} -le 72 ]] || deny "password must be 12-72 characters"
  case "$password" in
    *:*|*$'\r'*|*$'\n'*) deny "password cannot contain ':', carriage returns or newlines" ;;
  esac
  printf '%s:%s\n' "$user" "$password" | chpasswd
  passwd -u "$user" >/dev/null 2>&1 || true
}

delete_panel_user_runtime() {
  local user="$1"
  require_linux_user "$user"
  for dir in /etc/php/*/fpm/pool.d; do
    [[ -d "$dir" ]] || continue
    for pool_file in "$dir"/spanel-${user}.conf "$dir"/spanel-${user}-*.conf; do
      [[ -f "$pool_file" ]] || continue
      rm -f "$pool_file"
      local php_version
      php_version="$(echo "$dir" | awk -F/ '{print $4}')"
      systemctl reload "php${php_version}-fpm" 2>/dev/null || true
    done
  done
  # The account's own PHP-FPM masters, one per PHP version it used.
  local user_fpm_dir user_fpm_ver user_fpm_unit
  for user_fpm_dir in "$SPANEL_PHP_FPM_ROOT"/*/"$user"; do
    [[ -d "$user_fpm_dir" ]] || continue
    user_fpm_ver="$(basename "$(dirname "$user_fpm_dir")")"
    user_fpm_unit="$(user_php_fpm_unit "$user_fpm_ver" "$user")"
    systemctl disable --now "$user_fpm_unit" >/dev/null 2>&1 || true
    rm -rf -- "$user_fpm_dir" "${SYSTEMD_UNIT_DIR}/${user_fpm_unit}.d"
    rmdir -- "$(dirname "$user_fpm_dir")" 2>/dev/null || true
    systemctl daemon-reload >/dev/null 2>&1 || true
  done
  crontab -r -u "$user" 2>/dev/null || true
  rm -f "/etc/cron.d/spanel-user-${user}" 2>/dev/null || true
  local old_uid
  old_uid="$(id -u "$user" 2>/dev/null || true)"
  rm -f "/etc/spanel/limits/${user}.env" 2>/dev/null || true
  if [[ "$old_uid" =~ ^[0-9]+$ && "$old_uid" -ge 1000 ]]; then
    rm -rf "/etc/systemd/system/user-${old_uid}.slice.d" 2>/dev/null || true
  fi
  # Ask, then insist. An SFTP session or a PHP worker still winding down is
  # enough to make the userdel below refuse.
  pkill -u "$user" 2>/dev/null || true
  pkill -KILL -u "$user" 2>/dev/null || true
  # -f is not belt and braces. userdel decides whether an account is "in use"
  # by scanning for processes owned by its uid, and that scan races with the
  # kill above - measured on a live server, deleting an account that had just
  # held an SFTP session left the passwd entry behind while `|| true` reported
  # success. Never add -r: the home is removed explicitly below, and -r would
  # take a directory this function has not checked.
  userdel -f "$user" 2>/dev/null || true
  # ensure_panel_user_home puts www-data in this group so the web server can
  # read the site. groupdel then refuses it as "has other members", which left
  # one orphan group behind per deleted account, silently, for as long as this
  # function has existed.
  gpasswd -d www-data "$user" >/dev/null 2>&1 || true
  groupdel "$user" 2>/dev/null || true
  rm -rf "$HOME_ROOT/$user" 2>/dev/null || true
  rm -rf "/var/lib/php/sessions/$user" 2>/dev/null || true
  rm -rf "/var/lib/php/uploads/$user" 2>/dev/null || true
}

ensure_sftp_site_group() {
  getent group "$SPANEL_SFTP_SITE_GROUP" >/dev/null || groupadd --system "$SPANEL_SFTP_SITE_GROUP"
  install -d -o root -g root -m 0755 "$SPANEL_SFTP_CHROOT_ROOT"
}

# Sub-account names are generated by the panel and always carry the sftp_
# prefix. Requiring it here is what makes it impossible to aim any of these
# verbs at a panel user, a site user or a system account: the names simply
# cannot collide, whatever the caller sends.
require_sftp_sub_user() {
  [[ "$1" =~ ^sftp_[a-z0-9_]{3,26}$ ]] || deny "invalid SFTP sub-account: $1"
}

sftp_account_chroot() {
  require_sftp_sub_user "$1"
  printf '%s/%s' "$SPANEL_SFTP_CHROOT_ROOT" "$1"
}

sftp_account_mount_unit() {
  systemd-escape -p --suffix=mount "$1"
}

# Bind the one website this account may reach into a root-owned chroot.
#
# The account shares the site owner's uid on purpose: files it uploads then
# carry exactly the ownership the owner's own upload would produce, so PHP-FPM
# keeps write access and WordPress can still update itself in place. That makes
# the chroot the only boundary, which is why every directory above it is
# root-owned and why the account gets no shell and no exec of any kind.
ensure_sftp_account() {
  local owner="$1" sub="$2" site_path="$3"
  local target chroot domain where unit owner_uid owner_gid
  require_linux_user "$owner"
  require_sftp_sub_user "$sub"
  id -u "$owner" >/dev/null 2>&1 || deny "panel Linux user does not exist: $owner"

  target=$(require_managed_path "$site_path" "$owner")
  [[ -d "$target" ]] || deny "site directory does not exist: $target"
  # Exactly one component below the home: the website root itself. apps/<name>
  # resolves to depth 2 and is refused, and so is anything deeper - a
  # sub-account is scoped to a site, not to a directory inside one.
  local relative
  relative="${target#${HOME_ROOT}/${owner}/}"
  [[ "$(managed_root_depth "$relative")" == "1" && "$relative" != */* ]] \
    || deny "SFTP sub-accounts are scoped to a website root: $target"
  domain="$relative"
  require_site_domain_segment "$domain"

  ensure_sftp_site_group
  chroot=$(sftp_account_chroot "$sub")
  where="$chroot/$domain"

  owner_uid=$(id -u "$owner")
  owner_gid=$(id -g "$owner")
  if ! id -u "$sub" >/dev/null 2>&1; then
    # -o: share the owner's uid. -M: no home of its own. -d /: the home inside
    # the chroot is the chroot root. Created locked; a password arrives on the
    # next verb.
    useradd -o -u "$owner_uid" -g "$owner_gid" -M -d / -s /usr/sbin/nologin "$sub"
  else
    usermod -o -u "$owner_uid" -g "$owner_gid" -d / -s /usr/sbin/nologin "$sub" 2>/dev/null || true
  fi
  usermod -aG "$SPANEL_SFTP_SITE_GROUP" "$sub" 2>/dev/null || true

  # Root owns the chroot and everything above it, and nothing else may write
  # it. sshd refuses to chroot otherwise, and the refusal is the point.
  install -d -o root -g root -m 0755 "$chroot"
  install -d -o root -g root -m 0755 "$where"

  unit=$(sftp_account_mount_unit "$where")
  cat >"/etc/systemd/system/$unit" <<UNIT
[Unit]
Description=SPanel SFTP chroot for $sub ($domain)
After=local-fs.target

[Mount]
What=$target
Where=$where
Type=none
Options=bind,nosuid,nodev

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable --now "$unit" >/dev/null 2>&1 \
    || deny "could not bind $target into the SFTP chroot for $sub"
  mountpoint -q "$where" || deny "SFTP chroot bind mount is not active for $sub"
}

set_sftp_account_password() {
  local sub="$1" password
  require_sftp_sub_user "$sub"
  id -u "$sub" >/dev/null 2>&1 || deny "SFTP sub-account does not exist: $sub"
  password="$(cat)"
  password="${password%$'\n'}"
  [[ ${#password} -ge 12 && ${#password} -le 72 ]] || deny "password must be 12-72 characters"
  case "$password" in
    *:*|*$'\r'*|*$'\n'*) deny "password cannot contain ':', carriage returns or newlines" ;;
  esac
  printf '%s:%s\n' "$sub" "$password" | chpasswd
  passwd -u "$sub" >/dev/null 2>&1 || true
}

set_sftp_account_lock() {
  local sub="$1" action="$2"
  require_sftp_sub_user "$sub"
  id -u "$sub" >/dev/null 2>&1 || return 0
  case "$action" in
    lock) usermod -L "$sub" 2>/dev/null || true ;;
    unlock) usermod -U "$sub" 2>/dev/null || true ;;
    *) deny "usage: sftp-account-lock <sub-account> <lock|unlock>" ;;
  esac
  sftp_account_kill_sessions "$sub"
}

# The account shares the owner's uid, so `pkill -u` would kill the customer's
# PHP workers and cron jobs as well. Match sshd's own process title instead,
# which carries the login name rather than the uid.
sftp_account_kill_sessions() {
  local sub="$1"
  require_sftp_sub_user "$sub"
  pkill -f "^sshd: ${sub}([@ ]|$)" 2>/dev/null || true
}

delete_sftp_account() {
  local sub="$1" chroot where unit
  require_sftp_sub_user "$sub"
  chroot=$(sftp_account_chroot "$sub")
  sftp_account_kill_sessions "$sub"

  if [[ -d "$chroot" ]]; then
    for where in "$chroot"/*; do
      [[ -d "$where" ]] || continue
      unit=$(sftp_account_mount_unit "$where")
      systemctl disable --now "$unit" >/dev/null 2>&1 || true
      rm -f "/etc/systemd/system/$unit"
      # A session that is still attached holds the mount. Detach it rather than
      # leaving the bind in place; the kernel drops it when the last reference
      # goes, and the account is gone by then either way.
      mountpoint -q "$where" && { umount "$where" 2>/dev/null || umount -l "$where" 2>/dev/null || true; }
      rmdir "$where" 2>/dev/null || true
    done
    systemctl daemon-reload
    rmdir "$chroot" 2>/dev/null || true
  fi

  # -f is required, not defensive. userdel decides whether an account is "in
  # use" by scanning for processes owned by its UID, and this account shares
  # the site owner's UID - so a customer with any PHP-FPM worker or cron job
  # running makes a plain userdel exit 8 and leave the passwd entry behind.
  # Measured on a live server: plain userdel refused, -f removed it and left
  # the owner, their home and their files untouched.
  #
  # Never add -r. The home recorded for this account is "/" - it is the chroot
  # root, which is what sshd chdirs to - and -r would aim a recursive delete at
  # it. -f alone does not touch the home directory.
  userdel -f "$sub" 2>/dev/null || true
}

site_root_hash() {
  printf '%s' "$1" | sha256sum | awk '{print substr($1, 1, 12)}'
}

site_php_pool_glob() {
  local user="$1" target="$2"
  require_linux_user "$user"
  target=$(readlink -m "$target") || deny "cannot resolve $target"
  printf 'spanel-%s-%s-*' "$user" "$(site_root_hash "$target")"
}

# --- PHP-FPM pool naming (site_users.php_fpm_pool_name / php_fpm_socket) -----
php_pool_name() {
  # php_pool_name <user> <resolved-site-root> <php-version>
  printf 'spanel-%s-%s-%s\n' "$1" "$(site_root_hash "$2")" "${3//./_}"
}

user_php_fpm_unit() {
  # user_php_fpm_unit <php-version> <user>
  printf 'spanel-php%s-fpm@%s.service\n' "$1" "$2"
}

user_php_fpm_dir() {
  # user_php_fpm_dir <php-version> <user>
  printf '%s/%s/%s\n' "$SPANEL_PHP_FPM_ROOT" "$1" "$2"
}

user_php_fpm_socket() {
  # user_php_fpm_socket <user> <pool-name>
  printf '%s/%s/%s.sock\n' "$SPANEL_PHP_RUN_ROOT" "$1" "$2"
}

legacy_php_pool_file() {
  # legacy_php_pool_file <php-version> <pool-name>
  printf '%s/%s/fpm/pool.d/%s.conf\n' "$PHP_ETC_ROOT" "$1" "$2"
}

# Every SPanel pool file on the box, one per line: pools of the per-account
# masters first, then pools the distro master still serves.
managed_php_pool_files() {
  local f
  for f in "$SPANEL_PHP_FPM_ROOT"/*/*/pool.d/spanel-*.conf "$PHP_ETC_ROOT"/*/fpm/pool.d/spanel-*.conf; do
    [[ -f "$f" ]] && printf '%s\n' "$f"
  done
  return 0
}

php_pool_file_is_legacy() {
  [[ "$1" == "$PHP_ETC_ROOT"/* ]]
}

php_pool_file_version() {
  local f="$1"
  if php_pool_file_is_legacy "$f"; then
    f="${f#"$PHP_ETC_ROOT"/}"
  else
    f="${f#"$SPANEL_PHP_FPM_ROOT"/}"
  fi
  printf '%s\n' "${f%%/*}"
}

php_pool_file_user() {
  awk -F= '/^[[:space:]]*user[[:space:]]*=/ { gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit }' "$1"
}

php_pool_listen() {
  awk -F= '/^[[:space:]]*listen[[:space:]]*=/ { gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit }' "$1"
}

# The systemd unit serving a pool file: the distro master for a legacy pool,
# the account's own master otherwise.
php_pool_file_unit() {
  local f="$1" ver user
  ver="$(php_pool_file_version "$f")"
  if php_pool_file_is_legacy "$f"; then
    printf 'php%s-fpm.service\n' "$ver"
    return 0
  fi
  user="${f#"$SPANEL_PHP_FPM_ROOT"/"$ver"/}"
  user="${user%%/*}"
  user_php_fpm_unit "$ver" "$user"
}

# Pool names, counted once. A pool that is moving between the two layouts
# exists in both for a moment and is still one pool to size against.
php_fpm_unique_pool_count() {
  managed_php_pool_files | awk -F/ '{print $NF}' | sort -u | grep -c . || true
}

# Writes stdin to <path> when it differs from what is there; prints "changed"
# when it wrote, so callers know whether systemd needs a daemon-reload.
write_file_if_changed() {
  local path="$1" mode="$2" tmp
  tmp="$(mktemp "${path%/*}/.spanel-write.XXXXXX")" || deny "cannot write ${path}"
  cat >"$tmp"
  if [[ -f "$path" ]] && cmp -s "$tmp" "$path"; then
    rm -f -- "$tmp"
    return 0
  fi
  chmod "$mode" "$tmp"
  mv -f -- "$tmp" "$path"
  echo changed
}

render_user_php_fpm_unit() {
  # render_user_php_fpm_unit <php-version>: the template, one per PHP version.
  # No RuntimeDirectory=: two versions of one account share the socket
  # directory, and systemd would delete it under the other when one stops.
  local ver="$1"
  cat <<UNIT
# Generated by SPanel. One PHP ${ver} FPM master per hosting account; the
# instance name is the Linux user. Slice=user-<uid>.slice comes from the
# instance drop-in, so the master and its workers start inside the account's
# resource limits. The distro php${ver}-fpm.service is left alone.
[Unit]
Description=SPanel PHP ${ver} FastCGI Process Manager for %i
Documentation=man:php-fpm${ver}(8)
After=network.target
ConditionPathExists=${SPANEL_PHP_FPM_ROOT}/${ver}/%i/php-fpm.conf
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=notify
Environment=PHP_INI_SCAN_DIR=${PHP_ETC_ROOT}/${ver}/fpm/conf.d
ExecStartPre=/usr/bin/install -d -m 0755 -o root -g root ${SPANEL_PHP_RUN_ROOT}
ExecStartPre=/usr/bin/install -d -m 0750 -o root -g www-data ${SPANEL_PHP_RUN_ROOT}/%i
ExecStartPre=${PHP_FPM_SBIN_DIR}/php-fpm${ver} --test --fpm-config ${SPANEL_PHP_FPM_ROOT}/${ver}/%i/php-fpm.conf
ExecStart=${PHP_FPM_SBIN_DIR}/php-fpm${ver} --nodaemonize --fpm-config ${SPANEL_PHP_FPM_ROOT}/${ver}/%i/php-fpm.conf
ExecReload=/bin/kill -USR2 \$MAINPID
Restart=on-failure
RestartSec=2s

[Install]
WantedBy=multi-user.target
UNIT
}

render_user_php_fpm_slice_dropin() {
  # render_user_php_fpm_slice_dropin <uid>
  cat <<DROPIN
# Generated by SPanel: run this account's PHP inside its own slice.
[Service]
Slice=user-${1}.slice
DROPIN
}

render_user_php_fpm_conf() {
  # render_user_php_fpm_conf <php-version> <user>
  local ver="$1" user="$2" dir
  dir="$(user_php_fpm_dir "$ver" "$user")"
  cat <<CONF
; Generated by SPanel: PHP ${ver} FPM master for ${user}.
; Pools are the files in ${dir}/pool.d. php.ini and conf.d are the
; distro's (PHP_INI_SCAN_DIR in the unit), so PHP settings stay server-wide.
[global]
pid = ${SPANEL_PHP_RUN_ROOT}/${user}/php${ver}-fpm.pid
error_log = ${PHP_LOG_DIR}/spanel-fpm-${user}-${ver}.log
log_level = notice
emergency_restart_threshold = 10
emergency_restart_interval = 1m
process_control_timeout = 10s
include = ${dir}/pool.d/*.conf
CONF
}

render_php_pool_conf() {
  # render_php_pool_conf <pool-name> <user> <site-root> <listen-socket>
  # Sizing comes from calculate_php_fpm_pool_tuning, which must run first.
  local pool_name="$1" user="$2" target="$3" listen="$4"
  # Per-user dirs for sessions/uploads. Sharing /tmp across pools lets one
  # site read another's session files (mode 0600 helps but only inside the
  # same uid; uploads land world-writable on tmpfs). Using 0700 dirs owned
  # by the pool's Linux user contains the data inside the site's trust
  # boundary - and, being owned by that user, counts against its disk quota.
  local sess_dir="/var/lib/php/sessions/${user}"
  local upload_dir="/var/lib/php/uploads/${user}"
  cat <<POOL
[${pool_name}]
user = ${user}
group = ${user}
listen = ${listen}
listen.owner = www-data
listen.group = www-data
listen.mode = 0660
listen.backlog = 64
; SPanel auto-tunes these values from RAM, CPU and managed pool count.
; Optional overrides: SPANEL_PHP_FPM_WORKER_MB, SPANEL_PHP_FPM_MAX_CHILDREN,
; SPANEL_PHP_FPM_IDLE_TIMEOUT, SPANEL_PHP_FPM_MAX_REQUESTS,
; SPANEL_PHP_FPM_REQUEST_TERMINATE_TIMEOUT.
pm = ${PHP_FPM_PM_MODE}
pm.max_children = ${PHP_FPM_MAX_CHILDREN}
pm.process_idle_timeout = ${PHP_FPM_PROCESS_IDLE_TIMEOUT}s
pm.max_requests = ${PHP_FPM_MAX_REQUESTS}
request_terminate_timeout = ${PHP_FPM_REQUEST_TERMINATE_TIMEOUT}s
request_slowlog_timeout = 5s
slowlog = /var/log/php/spanel-slow-${pool_name}.log
chdir = /
php_admin_value[open_basedir] = ${target}:${sess_dir}:${upload_dir}:/usr/share/php
php_admin_value[upload_tmp_dir] = ${upload_dir}
php_admin_value[session.save_path] = ${sess_dir}
POOL
}

ensure_php_fpm_logrotate() {
  # The per-account masters log beside the slowlogs, which never had a rule.
  # copytruncate: nothing to signal, and one rule covers every master.
  local rule
  rule="$(cat <<ROTATE
# Managed by SPanel.
${PHP_LOG_DIR}/spanel-fpm-*.log ${PHP_LOG_DIR}/spanel-slow-*.log {
    su root root
    weekly
    rotate 4
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
}
ROTATE
)"
  [[ -d /etc/logrotate.d ]] || return 0
  printf '%s\n' "$rule" | write_file_if_changed /etc/logrotate.d/spanel-php-fpm 0644 >/dev/null
}

# Brings the account's master for one PHP version in line with its pool.d:
# stopped and removed when there is no pool left, otherwise written, tested
# and started or reloaded. Returns non-zero when the master is not serving,
# which the caller turns into a fallback to the distro master.
sync_user_php_fpm_master() {
  local ver="$1" user="$2" dir unit dropin_dir uid daemon_reload=0 needs_restart=0
  local pools=()
  dir="$(user_php_fpm_dir "$ver" "$user")"
  unit="$(user_php_fpm_unit "$ver" "$user")"
  dropin_dir="${SYSTEMD_UNIT_DIR}/${unit}.d"
  shopt -s nullglob
  pools=("$dir"/pool.d/*.conf)
  shopt -u nullglob
  if (( ${#pools[@]} == 0 )); then
    systemctl disable --now "$unit" >/dev/null 2>&1 || true
    rm -rf -- "$dir" "$dropin_dir"
    rmdir -- "${SPANEL_PHP_FPM_ROOT}/${ver}" 2>/dev/null || true
    systemctl daemon-reload >/dev/null 2>&1 || true
    return 0
  fi
  [[ -x "${PHP_FPM_SBIN_DIR}/php-fpm${ver}" ]] || return 1
  uid="$(id -u "$user" 2>/dev/null || true)"
  [[ "$uid" =~ ^[0-9]+$ ]] || return 1
  (( uid >= 1000 )) || return 1
  install -d -m 0755 "$dropin_dir"
  if [[ "$(render_user_php_fpm_unit "$ver" | write_file_if_changed "${SYSTEMD_UNIT_DIR}/spanel-php${ver}-fpm@.service" 0644)" == changed ]]; then
    daemon_reload=1
  fi
  if [[ "$(render_user_php_fpm_slice_dropin "$uid" | write_file_if_changed "${dropin_dir}/10-spanel-slice.conf" 0644)" == changed ]]; then
    # Slice= only takes effect on a fresh start.
    daemon_reload=1
    needs_restart=1
  fi
  render_user_php_fpm_conf "$ver" "$user" | write_file_if_changed "${dir}/php-fpm.conf" 0644 >/dev/null
  ensure_php_fpm_logrotate || true
  if (( daemon_reload )); then
    systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  # Test before touching a running master: USR2 with a broken config takes
  # down every site of the account, a refused test takes down none.
  PHP_INI_SCAN_DIR="${PHP_ETC_ROOT}/${ver}/fpm/conf.d" \
    "${PHP_FPM_SBIN_DIR}/php-fpm${ver}" --test --fpm-config "${dir}/php-fpm.conf" >/dev/null 2>&1 || return 1
  if systemctl is-active --quiet "$unit" 2>/dev/null; then
    if (( needs_restart )); then
      systemctl restart "$unit" >/dev/null 2>&1 || return 1
    else
      systemctl reload "$unit" >/dev/null 2>&1 || systemctl restart "$unit" >/dev/null 2>&1 || return 1
    fi
  else
    systemctl enable "$unit" >/dev/null 2>&1 || true
    # restart, not start: it also clears a failed state left by an earlier try.
    systemctl restart "$unit" >/dev/null 2>&1 || return 1
  fi
  systemctl is-active --quiet "$unit" 2>/dev/null
}

# Reloads every running per-account master of one PHP version, for changes
# they share with the distro master: php.ini, conf.d, extensions, binaries.
reload_user_php_fpm_masters() {
  local ver="$1" dir user unit
  for dir in "$SPANEL_PHP_FPM_ROOT/$ver"/*/; do
    [[ -f "${dir}php-fpm.conf" ]] || continue
    user="$(basename "$dir")"
    unit="$(user_php_fpm_unit "$ver" "$user")"
    systemctl is-active --quiet "$unit" 2>/dev/null || continue
    systemctl reload "$unit" >/dev/null 2>&1 || systemctl restart "$unit" >/dev/null 2>&1 || true
  done
  return 0
}

# Reload (or for per-account masters, re-sync) the units serving the pool
# files named on stdin, each once.
reload_php_pool_owners() {
  local f unit ver user seen=" "
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    unit="$(php_pool_file_unit "$f")"
    [[ "$seen" == *" ${unit} "* ]] && continue
    seen+="${unit} "
    ver="$(php_pool_file_version "$f")"
    if php_pool_file_is_legacy "$f"; then
      systemctl reload "php${ver}-fpm" 2>/dev/null || true
    else
      user="${unit#spanel-php"${ver}"-fpm@}"
      user="${user%.service}"
      sync_user_php_fpm_master "$ver" "$user" || \
        echo "warning: ${unit} is not running; see journalctl -u ${unit}" >&2
    fi
  done
  return 0
}

# Pools still in the distro pool.d whose site already runs on its account's
# master, once no vhost points at the old socket any more. Until the vhost is
# rewritten the old pool is what serves the site, so it stays.
prune_legacy_php_pools() {
  local refs f name ver user sock versions=" " removed=0
  refs="$(mktemp)"
  cat "$NGINX_CONF_DIR"/*.conf 2>/dev/null | grep -oE "$PHP_FPM_SOCKET_GREP" | sort -u >"$refs" || true
  # No sockets at all means nginx is unconfigured or unreadable, not that no
  # site needs its pool. Same refusal as the orphan cleanup.
  if [[ ! -s "$refs" ]]; then
    rm -f -- "$refs"
    return 0
  fi
  for f in "$PHP_ETC_ROOT"/*/fpm/pool.d/spanel-*.conf; do
    [[ -f "$f" ]] || continue
    name="$(basename "$f" .conf)"
    ver="$(php_pool_file_version "$f")"
    user="$(php_pool_file_user "$f" || true)"
    [[ -n "$user" ]] || continue
    [[ -f "$(user_php_fpm_dir "$ver" "$user")/pool.d/${name}.conf" ]] || continue
    sock="$(php_pool_listen "$f" || true)"
    if [[ -n "$sock" ]] && grep -qxF "$sock" "$refs"; then
      continue
    fi
    rm -f -- "$f"
    echo "retired legacy pool ${name} (PHP ${ver})"
    removed=$((removed + 1))
    [[ "$versions" == *" ${ver} "* ]] || versions+="${ver} "
  done
  rm -f -- "$refs"
  for ver in $versions; do
    systemctl reload "php${ver}-fpm" 2>/dev/null || true
  done
  return 0
}

positive_int_or_default() {
  local value="${1:-}" default="$2" min="${3:-1}" max="${4:-}"
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    value="$default"
  fi
  if (( value < min )); then
    value="$min"
  fi
  if [[ -n "$max" ]] && (( value > max )); then
    value="$max"
  fi
  printf '%s\n' "$value"
}

php_fpm_tuning_value() {
  local key="$1" default="$2" value=""
  if [[ -v "$key" ]]; then
    value="${!key}"
  fi
  if [[ -z "$value" ]]; then
    value="$(env_get "$key" 2>/dev/null || true)"
  fi
  printf '%s\n' "${value:-$default}"
}

php_fpm_total_memory_mb() {
  local total
  total="$(awk '/^MemTotal:/ { print int($2 / 1024); exit }' /proc/meminfo 2>/dev/null || true)"
  positive_int_or_default "$total" 1024 256 1048576
}

php_fpm_cpu_count() {
  local count
  count="$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
  positive_int_or_default "$count" 1 1 256
}

php_fpm_pool_count() {
  local current_pool="${1:-}" count files
  count="$(php_fpm_unique_pool_count)"
  [[ "$count" =~ ^[0-9]+$ ]] || count=0
  # A pool about to be written counts too - unless it is already on disk in
  # either layout under the same name. Read whole, then matched: `| grep -q`
  # under pipefail turns an early match into a failure.
  files="$(managed_php_pool_files)"
  if [[ -n "$current_pool" && ! -f "$current_pool" && $'\n'"$files"$'\n' != *"/$(basename "$current_pool")"$'\n'* ]]; then
    count=$((count + 1))
  fi
  if (( count < 1 )); then
    count=1
  fi
  printf '%s\n' "$count"
}

calculate_php_fpm_pool_tuning() {
  local current_pool="${1:-}" total_mb php_budget_mb cpu_count pool_count worker_mb
  local global_children pool_children cpu_cap profile_cap forced_children idle_default requests_default
  local active_pool_divisor pool_floor
  total_mb="$(php_fpm_total_memory_mb)"
  cpu_count="$(php_fpm_cpu_count)"
  pool_count="$(php_fpm_pool_count "$current_pool")"
  worker_mb="$(positive_int_or_default "$(php_fpm_tuning_value SPANEL_PHP_FPM_WORKER_MB "$PHP_FPM_DEFAULT_WORKER_MB")" "$PHP_FPM_DEFAULT_WORKER_MB" 32 1024)"
  # Workers come off total RAM, not off RAM minus a reserve for MariaDB and
  # friends. Three separate things already hold this number down: worker_mb
  # bills a worker at 128 MB when a measured WordPress worker is nearer 24 MB
  # RSS, pm=ondemand means a worker exists only while it is serving, and
  # cpu_cap/profile_cap below are what actually bound a small box. Taking a
  # further 20-45% off the top on top of all three left every server about a
  # third short of what it could serve - a 6 GB box was sized at 34 workers
  # when the caps would have allowed 48.
  php_budget_mb="$total_mb"
  if (( php_budget_mb < worker_mb )); then
    php_budget_mb="$worker_mb"
  fi

  global_children=$((php_budget_mb / worker_mb))
  (( global_children >= 1 )) || global_children=1

  active_pool_divisor=1
  while (( active_pool_divisor * active_pool_divisor < pool_count )); do
    active_pool_divisor=$((active_pool_divisor + 1))
  done
  pool_children=$((global_children / active_pool_divisor))
  (( pool_children >= 1 )) || pool_children=1

  cpu_cap=$((cpu_count * 4))
  if (( total_mb >= 3072 )); then
    cpu_cap=$((cpu_count * 6))
  fi
  if (( total_mb >= 8192 )); then
    cpu_cap=$((cpu_count * 8))
  fi
  (( cpu_cap >= 2 )) || cpu_cap=2
  (( cpu_cap <= 96 )) || cpu_cap=96

  if (( total_mb <= 1024 )); then
    pool_floor=1
    profile_cap=4
    idle_default=10
    requests_default=300
  elif (( total_mb <= 2048 )); then
    pool_floor=2
    profile_cap=8
    idle_default=15
    requests_default=400
  elif (( total_mb <= 4096 )); then
    pool_floor=3
    profile_cap=14
    idle_default=20
    requests_default=500
  elif (( total_mb <= 8192 )); then
    pool_floor=4
    profile_cap=24
    idle_default=30
    requests_default=750
  else
    pool_floor=6
    profile_cap=48
    idle_default=45
    requests_default=1000
  fi

  (( pool_children >= pool_floor )) || pool_children="$pool_floor"
  (( pool_children <= cpu_cap )) || pool_children="$cpu_cap"
  (( pool_children <= profile_cap )) || pool_children="$profile_cap"
  forced_children="$(php_fpm_tuning_value SPANEL_PHP_FPM_MAX_CHILDREN "")"
  if [[ -n "$forced_children" ]]; then
    pool_children="$(positive_int_or_default "$forced_children" "$pool_children" 1 512)"
  fi

  PHP_FPM_PM_MODE="ondemand"
  PHP_FPM_MAX_CHILDREN="$pool_children"
  PHP_FPM_PROCESS_IDLE_TIMEOUT="$(positive_int_or_default "$(php_fpm_tuning_value SPANEL_PHP_FPM_IDLE_TIMEOUT "$idle_default")" "$idle_default" 5 300)"
  PHP_FPM_MAX_REQUESTS="$(positive_int_or_default "$(php_fpm_tuning_value SPANEL_PHP_FPM_MAX_REQUESTS "$requests_default")" "$requests_default" 50 10000)"
  PHP_FPM_REQUEST_TERMINATE_TIMEOUT="$(positive_int_or_default "$(php_fpm_tuning_value SPANEL_PHP_FPM_REQUEST_TERMINATE_TIMEOUT "$PHP_FPM_DEFAULT_REQUEST_TERMINATE_TIMEOUT")" "$PHP_FPM_DEFAULT_REQUEST_TERMINATE_TIMEOUT" 30 3600)"
}

php_fpm_set_directive() {
  local file="$1" key="$2" value="$3" key_re
  key_re="${key//./\\.}"
  if grep -Eq "^[;[:space:]]*${key_re}[[:space:]]*=" "$file"; then
    sed -i -E "s|^[;[:space:]]*${key_re}[[:space:]]*=.*|${key} = ${value}|" "$file"
  else
    printf '%s = %s\n' "$key" "$value" >>"$file"
  fi
}

apply_php_fpm_tuning_to_pool_file() {
  local pool_file="$1"
  php_fpm_set_directive "$pool_file" "pm" "$PHP_FPM_PM_MODE"
  php_fpm_set_directive "$pool_file" "pm.max_children" "$PHP_FPM_MAX_CHILDREN"
  php_fpm_set_directive "$pool_file" "pm.process_idle_timeout" "${PHP_FPM_PROCESS_IDLE_TIMEOUT}s"
  php_fpm_set_directive "$pool_file" "pm.max_requests" "$PHP_FPM_MAX_REQUESTS"
  php_fpm_set_directive "$pool_file" "request_terminate_timeout" "${PHP_FPM_REQUEST_TERMINATE_TIMEOUT}s"
}

apply_saved_user_limits_to_pool_file() {
  local pool_file="$1" u_name="$2"
  local limits_file="/etc/spanel/limits/${u_name}.env"
  [[ -f "$limits_file" && -f "$pool_file" ]] || return 0
  local cpu_pct=0 mem_high=0 mem_max=0 tasks_max=0 max_c per_worker_mb max_spare
  cpu_pct="$(awk -F= '$1=="CPU_PCT"{print $2; exit}' "$limits_file" 2>/dev/null || echo 0)"
  mem_high="$(awk -F= '$1=="MEM_HIGH_MB"{print $2; exit}' "$limits_file" 2>/dev/null || echo 0)"
  mem_max="$(awk -F= '$1=="MEM_MAX_MB"{print $2; exit}' "$limits_file" 2>/dev/null || echo 0)"
  tasks_max="$(awk -F= '$1=="TASKS_MAX"{print $2; exit}' "$limits_file" 2>/dev/null || echo 0)"
  [[ "$cpu_pct" =~ ^[0-9]+$ ]] || cpu_pct=0
  [[ "$mem_high" =~ ^[0-9]+$ ]] || mem_high=0
  [[ "$mem_max" =~ ^[0-9]+$ ]] || mem_max=0
  [[ "$tasks_max" =~ ^[0-9]+$ ]] || tasks_max=0

  max_c="${PHP_FPM_MAX_CHILDREN:-8}"
  [[ "$max_c" =~ ^[0-9]+$ ]] || max_c=8
  if (( tasks_max > 0 && tasks_max < max_c * 2 )); then
    max_c=$(( tasks_max / 2 ))
    (( max_c < 2 )) && max_c=2
  fi
  if (( mem_max > 0 )); then
    local mem_cap=$(( mem_max / 64 ))
    (( mem_cap < 2 )) && mem_cap=2
    (( mem_cap < max_c )) && max_c="$mem_cap"
    per_worker_mb=$(( mem_max / 2 ))
    (( per_worker_mb < 128 )) && per_worker_mb=128
    (( per_worker_mb > 1024 )) && per_worker_mb=1024
    php_fpm_set_directive "$pool_file" "php_admin_value[memory_limit]" "${per_worker_mb}M"
  fi
  php_fpm_set_directive "$pool_file" "pm.max_children" "$max_c"
  php_fpm_set_directive "$pool_file" "listen.backlog" "64"
  if (( cpu_pct > 0 || mem_max > 0 || mem_high > 0 || tasks_max > 0 )); then
    max_spare=2
    (( max_c < max_spare )) && max_spare="$max_c"
    php_fpm_set_directive "$pool_file" "pm" "dynamic"
    php_fpm_set_directive "$pool_file" "pm.start_servers" "1"
    php_fpm_set_directive "$pool_file" "pm.min_spare_servers" "1"
    php_fpm_set_directive "$pool_file" "pm.max_spare_servers" "$max_spare"
  fi
}

ensure_php_fpm_cgroup_dropin() {
  local fpm_ver="$1" dropin_dir
  [[ "$fpm_ver" =~ ^[0-9]+\.[0-9]+$ ]] || return 0
  dropin_dir="${SYSTEMD_UNIT_DIR}/php${fpm_ver}-fpm.service.d"
  install -d -m 0755 "$dropin_dir"
  cat >"${dropin_dir}/50-spanel-cgroup.conf" <<'DROPIN'
[Service]
ExecStartPost=-/usr/local/sbin/spanel-helper cgroup-classify-workers
ExecReload=-/usr/local/sbin/spanel-helper cgroup-classify-workers
DROPIN
  chmod 0644 "${dropin_dir}/50-spanel-cgroup.conf"
}

# Whole disk under / ("/dev/sda" for "/dev/sda1"), or nothing when / is not
# on a plain block device (LVM, a container overlay). cgroup IO limits are
# per device, and the panel's limits mean "the disk the sites live on".
tenant_io_base_device() {
  local root_dev base_dev
  root_dev="$(findmnt -no SOURCE / 2>/dev/null | head -n1 || true)"
  if [[ "$root_dev" == /dev/nvme* || "$root_dev" == /dev/mmcblk* ]]; then
    base_dev="$(echo "$root_dev" | sed -E 's/p[0-9]+$//')"
  elif [[ "$root_dev" == /dev/* ]]; then
    base_dev="$(echo "$root_dev" | sed -E 's/[0-9]+$//')"
  else
    base_dev=""
  fi
  [[ -n "$base_dev" && -b "$base_dev" ]] || base_dev=""
  printf '%s\n' "$base_dev"
}

# io.max line for a cgroup: "<maj:min> rbps=.. wbps=.. riops=.. wiops=..",
# MB/s and IOPS from the limits file, 0 meaning no limit ("max").
render_io_max_line() {
  local majmin="$1" r_mbps="${2:-0}" w_mbps="${3:-0}" r_iops="${4:-0}" w_iops="${5:-0}" v
  for v in r_mbps w_mbps r_iops w_iops; do
    [[ "${!v}" =~ ^[0-9]+$ ]] || printf -v "$v" '0'
  done
  [[ "$majmin" =~ ^[0-9]+:[0-9]+$ ]] || return 1
  printf '%s rbps=%s wbps=%s riops=%s wiops=%s\n' "$majmin" \
    "$( (( r_mbps > 0 )) && echo $(( r_mbps * 1048576 )) || echo max )" \
    "$( (( w_mbps > 0 )) && echo $(( w_mbps * 1048576 )) || echo max )" \
    "$( (( r_iops > 0 )) && echo "$r_iops" || echo max )" \
    "$( (( w_iops > 0 )) && echo "$w_iops" || echo max )"
}

# Whether a process with this /proc/<pid>/cgroup content still needs moving
# into user-<uid>.slice/spanel.scope. A process already anywhere inside the
# account's slice - its own PHP-FPM master and workers, an SSH session - is
# under the slice's limits; moving it out of a systemd unit's cgroup would
# only leave systemd with a unit it no longer controls.
tenant_process_needs_scope() {
  local cg_current="$1" uid="$2"
  [[ "$cg_current" == *"spanel-app-"* ]] && return 1
  [[ "$cg_current" == *"/user-${uid}.slice/"* ]] && return 1
  return 0
}

# What still runs outside the account's slice gets moved in after the fact:
# pools left on the distro php-fpm master, cron jobs (cron.service), anything
# started by root on the account's behalf. PHP served by the per-account
# masters never needs this - it starts inside the slice.
classify_all_tenant_workers() {
  [[ -d /sys/fs/cgroup/user.slice ]] || return 0
  echo "+cpu +memory +io +pids" >/sys/fs/cgroup/user.slice/cgroup.subtree_control 2>/dev/null || true
  local pid uid uname comm slice_unit cg_slice cg_scope cg_current limits_file io_dev io_majmin io_line
  local cpu_pct mem_high mem_max tasks_max io_r io_w iops_r iops_w
  local prepared=" "
  io_dev="$(tenant_io_base_device)"
  io_majmin=""
  if [[ -n "$io_dev" ]]; then
    io_majmin="$(lsblk -dno MAJ:MIN "$io_dev" 2>/dev/null | tr -d '[:space:]' || true)"
  fi
  while read -r pid uid uname comm; do
    [[ "$pid" =~ ^[0-9]+$ && "$uid" =~ ^[0-9]+$ ]] || continue
    (( uid >= 1000 && uid < 65000 )) || continue
    [[ "$uname" == "spanel" ]] && continue
    limits_file="/etc/spanel/limits/${uname}.env"
    if [[ ! -f "$limits_file" ]]; then
      case "$comm" in
        php-fpm*|php*|lsphp*) ;;
        *) continue ;;
      esac
    fi
    cg_current="$(cat "/proc/${pid}/cgroup" 2>/dev/null || true)"
    tenant_process_needs_scope "$cg_current" "$uid" || continue
    slice_unit="user-${uid}.slice"
    cg_slice="/sys/fs/cgroup/user.slice/${slice_unit}"
    cg_scope="${cg_slice}/spanel.scope"
    # Limits are written once per account per scan, not once per process.
    if [[ "$prepared" != *" ${uid} "* ]]; then
      prepared+="${uid} "
      if [[ ! -d "$cg_slice" ]]; then
        systemctl start "$slice_unit" >/dev/null 2>&1 || mkdir -p "$cg_slice" 2>/dev/null || true
      fi
      echo "+cpu +memory +io +pids" >"${cg_slice}/cgroup.subtree_control" 2>/dev/null || true
      mkdir -p "$cg_scope" 2>/dev/null || true
      if [[ -f "$limits_file" ]]; then
        cpu_pct="$(awk -F= '$1=="CPU_PCT"{print $2; exit}' "$limits_file" 2>/dev/null || echo 0)"
        mem_high="$(awk -F= '$1=="MEM_HIGH_MB"{print $2; exit}' "$limits_file" 2>/dev/null || echo 0)"
        mem_max="$(awk -F= '$1=="MEM_MAX_MB"{print $2; exit}' "$limits_file" 2>/dev/null || echo 0)"
        tasks_max="$(awk -F= '$1=="TASKS_MAX"{print $2; exit}' "$limits_file" 2>/dev/null || echo 0)"
        io_r="$(awk -F= '$1=="IO_READ_MBPS"{print $2; exit}' "$limits_file" 2>/dev/null || echo 0)"
        io_w="$(awk -F= '$1=="IO_WRITE_MBPS"{print $2; exit}' "$limits_file" 2>/dev/null || echo 0)"
        iops_r="$(awk -F= '$1=="IOPS_READ"{print $2; exit}' "$limits_file" 2>/dev/null || echo 0)"
        iops_w="$(awk -F= '$1=="IOPS_WRITE"{print $2; exit}' "$limits_file" 2>/dev/null || echo 0)"
        if [[ "$cpu_pct" =~ ^[0-9]+$ ]] && (( cpu_pct > 0 )); then
          echo "$(( cpu_pct * 1000 )) 100000" >"${cg_scope}/cpu.max" 2>/dev/null || true
        fi
        if [[ "$mem_high" =~ ^[0-9]+$ ]] && (( mem_high > 0 )); then
          echo "$(( mem_high * 1048576 ))" >"${cg_scope}/memory.high" 2>/dev/null || true
        fi
        if [[ "$mem_max" =~ ^[0-9]+$ ]] && (( mem_max > 0 )); then
          echo "$(( mem_max * 1048576 ))" >"${cg_scope}/memory.max" 2>/dev/null || true
        fi
        if [[ "$tasks_max" =~ ^[0-9]+$ ]] && (( tasks_max > 0 )); then
          echo "$tasks_max" >"${cg_scope}/pids.max" 2>/dev/null || true
        fi
        if [[ -n "$io_majmin" ]] && io_line="$(render_io_max_line "$io_majmin" "$io_r" "$io_w" "$iops_r" "$iops_w")"; then
          echo "$io_line" >"${cg_scope}/io.max" 2>/dev/null || true
        fi
      fi
    fi
    if [[ -w "${cg_scope}/cgroup.procs" ]]; then
      echo "$pid" >"${cg_scope}/cgroup.procs" 2>/dev/null || true
    fi
  done < <(ps -eo pid=,uid=,user:32=,comm= 2>/dev/null || true)
}

# The distro masters only need the classify hook while they still serve a
# tenant pool; the per-account masters start in the slice on their own.
legacy_php_pool_versions() {
  local f ver versions=" "
  for f in "$PHP_ETC_ROOT"/*/fpm/pool.d/spanel-*.conf; do
    [[ -f "$f" ]] || continue
    ver="$(php_pool_file_version "$f")"
    [[ "$versions" == *" ${ver} "* ]] || versions+="${ver} "
  done
  printf '%s\n' "$versions"
}

retune_php_fpm_pools() {
  local pool_file php_version pool_user count=0 files
  files="$(managed_php_pool_files)"
  while IFS= read -r pool_file; do
    [[ -n "$pool_file" && -f "$pool_file" ]] || continue
    calculate_php_fpm_pool_tuning "$pool_file"
    apply_php_fpm_tuning_to_pool_file "$pool_file"
    pool_user="$(php_pool_file_user "$pool_file" || true)"
    if [[ -n "$pool_user" ]]; then
      ensure_php_runtime_dirs "$pool_user"
      apply_saved_user_limits_to_pool_file "$pool_file" "$pool_user"
      usermod -aG "$pool_user" www-data 2>/dev/null || true
    fi
    count=$((count + 1))
  done <<<"$files"
  for php_version in $(legacy_php_pool_versions); do
    ensure_php_fpm_cgroup_dropin "$php_version"
  done
  reload_php_pool_owners <<<"$files"
  # Boot runs this too, which makes it the place a pool left behind by an
  # interrupted move is finally retired.
  prune_legacy_php_pools >/dev/null || true
  classify_all_tenant_workers
  echo "Retuned ${count} SPanel PHP-FPM pool(s)."
}

mariadb_tuning_value() {
  local key="$1" default="$2" value=""
  if [[ -v "$key" ]]; then
    value="${!key}"
  fi
  if [[ -z "$value" ]]; then
    value="$(env_get "$key" 2>/dev/null || true)"
  fi
  printf '%s\n' "${value:-$default}"
}

mariadb_megabytes() {
  local value="${1:-}" default="$2" number unit
  if [[ "$value" =~ ^([0-9]+)([KkMmGg]?)$ ]]; then
    number="${BASH_REMATCH[1]}"
    unit="${BASH_REMATCH[2]}"
    case "$unit" in
      [Kk]) printf '%s\n' $(((number + 1023) / 1024)) ;;
      [Gg]) printf '%s\n' $((number * 1024)) ;;
      *) printf '%s\n' "$number" ;;
    esac
    return 0
  fi
  printf '%s\n' "$default"
}

calculate_mariadb_tuning() {
  local total_mb cpu_count buffer_default buffer_mb log_file_mb tmp_mb max_connections thread_cache
  local table_open_cache open_files_limit packet_mb io_capacity
  total_mb="$(php_fpm_total_memory_mb)"
  cpu_count="$(php_fpm_cpu_count)"

  if (( total_mb <= 1024 )); then
    buffer_default=$((total_mb * 22 / 100))
    max_connections=35
    thread_cache=16
    table_open_cache=512
    tmp_mb=32
    packet_mb=64
  elif (( total_mb <= 2048 )); then
    buffer_default=$((total_mb * 25 / 100))
    max_connections=50
    thread_cache=24
    table_open_cache=512
    tmp_mb=48
    packet_mb=64
  elif (( total_mb <= 4096 )); then
    buffer_default=$((total_mb * 28 / 100))
    max_connections=80
    thread_cache=32
    table_open_cache=1024
    tmp_mb=64
    packet_mb=96
  elif (( total_mb <= 8192 )); then
    buffer_default=$((total_mb * 32 / 100))
    max_connections=120
    thread_cache=48
    table_open_cache=1024
    tmp_mb=96
    packet_mb=128
  else
    buffer_default=$((total_mb * 36 / 100))
    max_connections=180
    thread_cache=64
    table_open_cache=2048
    tmp_mb=128
    packet_mb=128
  fi

  (( buffer_default >= 128 )) || buffer_default=128
  (( buffer_default <= total_mb * 45 / 100 )) || buffer_default=$((total_mb * 45 / 100))
  buffer_mb="$(mariadb_megabytes "$(mariadb_tuning_value SPANEL_MARIADB_BUFFER_POOL_SIZE "${buffer_default}M")" "$buffer_default")"
  buffer_mb="$(positive_int_or_default "$buffer_mb" "$buffer_default" 128 "$((total_mb * 60 / 100))")"

  max_connections="$(positive_int_or_default "$(mariadb_tuning_value SPANEL_MARIADB_MAX_CONNECTIONS "$max_connections")" "$max_connections" 20 1000)"
  thread_cache="$(positive_int_or_default "$(mariadb_tuning_value SPANEL_MARIADB_THREAD_CACHE_SIZE "$thread_cache")" "$thread_cache" 8 256)"
  table_open_cache="$(positive_int_or_default "$(mariadb_tuning_value SPANEL_MARIADB_TABLE_OPEN_CACHE "$table_open_cache")" "$table_open_cache" 256 65535)"
  tmp_mb="$(mariadb_megabytes "$(mariadb_tuning_value SPANEL_MARIADB_TMP_TABLE_SIZE "${tmp_mb}M")" "$tmp_mb")"
  tmp_mb="$(positive_int_or_default "$tmp_mb" 64 16 512)"
  packet_mb="$(mariadb_megabytes "$(mariadb_tuning_value SPANEL_MARIADB_MAX_ALLOWED_PACKET "${packet_mb}M")" "$packet_mb")"
  packet_mb="$(positive_int_or_default "$packet_mb" 64 16 512)"
  log_file_mb=$((buffer_mb / 4))
  log_file_mb="$(positive_int_or_default "$(mariadb_megabytes "$(mariadb_tuning_value SPANEL_MARIADB_LOG_FILE_SIZE "${log_file_mb}M")" "$log_file_mb")" "$log_file_mb" 64 1024)"
  io_capacity=$((cpu_count * 200))
  io_capacity="$(positive_int_or_default "$(mariadb_tuning_value SPANEL_MARIADB_IO_CAPACITY "$io_capacity")" "$io_capacity" 200 4000)"
  open_files_limit=$((table_open_cache * 2 + max_connections + 512))
  open_files_limit="$(positive_int_or_default "$(mariadb_tuning_value SPANEL_MARIADB_OPEN_FILES_LIMIT "$open_files_limit")" "$open_files_limit" 2048 200000)"

  MARIADB_INNODB_BUFFER_POOL_SIZE="${buffer_mb}M"
  MARIADB_INNODB_LOG_FILE_SIZE="${log_file_mb}M"
  MARIADB_MAX_CONNECTIONS="$max_connections"
  MARIADB_THREAD_CACHE_SIZE="$thread_cache"
  MARIADB_TABLE_OPEN_CACHE="$table_open_cache"
  MARIADB_TMP_TABLE_SIZE="${tmp_mb}M"
  MARIADB_MAX_ALLOWED_PACKET="${packet_mb}M"
  MARIADB_INNODB_IO_CAPACITY="$io_capacity"
  MARIADB_OPEN_FILES_LIMIT="$open_files_limit"
}

write_mariadb_tuning() {
  calculate_mariadb_tuning
  install -d -o root -g root -m 0755 "$(dirname "$MARIADB_TUNING_CONF")"
  cat >"$MARIADB_TUNING_CONF" <<MYSQL
# SPanel auto-tunes MariaDB for small and medium VPS plans.
# Optional overrides in ${ENV_FILE}: SPANEL_MARIADB_BUFFER_POOL_SIZE,
# SPANEL_MARIADB_MAX_CONNECTIONS, SPANEL_MARIADB_THREAD_CACHE_SIZE,
# SPANEL_MARIADB_TABLE_OPEN_CACHE, SPANEL_MARIADB_TMP_TABLE_SIZE,
# SPANEL_MARIADB_MAX_ALLOWED_PACKET, SPANEL_MARIADB_LOG_FILE_SIZE,
# SPANEL_MARIADB_IO_CAPACITY, SPANEL_MARIADB_OPEN_FILES_LIMIT.
[mysqld]
innodb_buffer_pool_size = ${MARIADB_INNODB_BUFFER_POOL_SIZE}
innodb_log_file_size = ${MARIADB_INNODB_LOG_FILE_SIZE}
innodb_flush_log_at_trx_commit = 2
innodb_flush_method = O_DIRECT
innodb_io_capacity = ${MARIADB_INNODB_IO_CAPACITY}
max_connections = ${MARIADB_MAX_CONNECTIONS}
thread_cache_size = ${MARIADB_THREAD_CACHE_SIZE}
table_open_cache = ${MARIADB_TABLE_OPEN_CACHE}
tmp_table_size = ${MARIADB_TMP_TABLE_SIZE}
max_heap_table_size = ${MARIADB_TMP_TABLE_SIZE}
max_allowed_packet = ${MARIADB_MAX_ALLOWED_PACKET}
skip_name_resolve = 1
slow_query_log = 1
slow_query_log_file = /var/log/mysql/spanel-slow.log
long_query_time = 2

[server]
open_files_limit = ${MARIADB_OPEN_FILES_LIMIT}
MYSQL
}

ensure_mariadb_slow_log() {
  local log_dir="/var/log/mysql" log_file="/var/log/mysql/spanel-slow.log" log_group="mysql"
  getent group adm >/dev/null 2>&1 && log_group="adm"
  install -d -o mysql -g "$log_group" -m 0750 "$log_dir"
  touch "$log_file"
  chown mysql:"$log_group" "$log_file"
  chmod 0640 "$log_file"
}

retune_mariadb() {
  write_mariadb_tuning
  ensure_mariadb_slow_log
  mariadbd --help --verbose >/dev/null
  systemctl restart mariadb
  echo "Retuned MariaDB: innodb_buffer_pool_size=${MARIADB_INNODB_BUFFER_POOL_SIZE}, max_connections=${MARIADB_MAX_CONNECTIONS}, table_open_cache=${MARIADB_TABLE_OPEN_CACHE}."
}

# --- disk quota ---------------------------------------------------------------
# Kernel user quota on the filesystem that holds /home. The panel's own
# accounting (storage_quota.py) measures what it can see and refuses panel
# uploads past the limit; it cannot stop PHP or SFTP writing. A kernel quota
# can, and counts every file the account owns on that filesystem - its sites,
# and its PHP session/upload directories when /var/lib/php is on the same one.
#
# Turning it on installs the quota tools and adds the mount options to
# /etc/fstab. It never remounts and never reboots: options added to fstab take
# effect at the administrator's next remount or reboot, and the status says so.
QUOTA_HOME="/home"
FSTAB_FILE="/etc/fstab"

quota_mount_field() {
  findmnt -no "$1" -T "$QUOTA_HOME" 2>/dev/null | head -n1 | tr -d '[:space:]' || true
}

# Whether a mount option string turns user quota on.
quota_options_active() {
  [[ ",$1," =~ ,(usrquota|usrjquota=[^,]+|uquota|uqnoenforce|quota), ]]
}

quota_wanted_options() {
  # quota_wanted_options <fstype>: journaled quota on ext4 (no quotacheck after
  # a crash), plain user quota on xfs.
  case "$1" in
    ext4) echo "usrjquota=aquota.user,jqfmt=vfsv1" ;;
    xfs) echo "usrquota" ;;
    *) return 1 ;;
  esac
}

fstab_quota_options() {
  # fstab_quota_options <fstab> <mountpoint>: the options column of its entry.
  awk -v mp="$2" '$1 !~ /^#/ && $2 == mp { print $4; exit }' "$1"
}

fstab_with_quota_options() {
  # fstab_with_quota_options <fstab> <mountpoint> <fstype>: the fstab with the
  # quota options added to that entry, on stdout. Exit 2 when the mount point
  # has no entry (nothing is guessed), 0 otherwise; idempotent.
  local fstab="$1" mp="$2" opts
  opts="$(quota_wanted_options "$3")" || return 1
  awk -v mp="$mp" -v add="$opts" '
    BEGIN { found = 0 }
    $1 !~ /^#/ && $2 == mp && NF >= 4 && !found {
      found = 1
      if ("," $4 "," !~ /,(usrquota|usrjquota=[^,]+|uquota|quota),/) {
        $4 = ($4 == "defaults" ? "defaults," add : $4 "," add)
        line = $1
        for (i = 2; i <= NF; i++) line = line "\t" $i
        print line
        next
      }
    }
    { print }
    END { if (!found) exit 2 }
  ' "$fstab"
}

quota_state() {
  # quota_state <fstype> <mountpoint> <options-active:yes|no> <in-fstab:yes|no> <quota-on:yes|no>
  local fstype="$1" mp="$2" active="$3" fstab="$4" on="$5"
  case "$fstype" in
    ext4|xfs) ;;
    *) echo unsupported; return 0 ;;
  esac
  if [[ "$active" == yes ]]; then
    if [[ "$fstype" == xfs || "$on" == yes ]]; then
      echo active
    else
      echo needs-quotaon
    fi
    return 0
  fi
  # XFS takes quota options only at mount time, and for / that is the kernel
  # command line, not fstab.
  if [[ "$fstype" == xfs && "$mp" == / ]]; then
    echo kernel-cmdline-required
  elif [[ "$fstab" == yes ]]; then
    echo remount-required
  else
    echo not-configured
  fi
}

quota_status() {
  local mp fstype device options active=no fstab=no on=no tools=no state hint=""
  mp="$(quota_mount_field TARGET)"
  fstype="$(quota_mount_field FSTYPE)"
  device="$(quota_mount_field SOURCE)"
  options="$(quota_mount_field OPTIONS)"
  quota_options_active "$options" && active=yes
  if [[ -n "$mp" && -f "$FSTAB_FILE" ]] && quota_options_active "$(fstab_quota_options "$FSTAB_FILE" "$mp")"; then
    fstab=yes
  fi
  command -v setquota >/dev/null 2>&1 && tools=yes
  if [[ "$tools" == yes && "$active" == yes && "$fstype" == ext4 ]]; then
    [[ "$(quotaon -pu "$mp" 2>/dev/null || true)" == *" is on"* ]] && on=yes
  fi
  state="$(quota_state "$fstype" "$mp" "$active" "$fstab" "$on")"
  case "$state" in
    active) hint="Kernel quota enforces each account's disk and inode limits on ${mp}." ;;
    needs-quotaon) hint="Quota options are mounted on ${mp}; enable kernel quota again to build the quota files and switch it on." ;;
    remount-required) hint="Quota options are in /etc/fstab for ${mp}. Remount it (mount -o remount ${mp}) or reboot, then enable again." ;;
    kernel-cmdline-required) hint="/ is XFS: add rootflags=uquota to GRUB_CMDLINE_LINUX in /etc/default/grub, run update-grub and reboot." ;;
    not-configured) hint="Only the panel's own accounting applies; PHP and SFTP can write past the limit." ;;
    unsupported) hint="Kernel quota needs ext4 or xfs; ${mp:-/home} is on ${fstype:-an unknown filesystem}." ;;
  esac
  printf 'mode=%s\n' "$([[ "$state" == active ]] && echo kernel || echo app)"
  printf 'state=%s\nmount=%s\nfstype=%s\ndevice=%s\ntools=%s\nmounted_options=%s\nfstab_options=%s\nquota_on=%s\nhint=%s\n' \
    "$state" "$mp" "$fstype" "$device" "$tools" "$active" "$fstab" "$on" "$hint"
}

quota_enable() {
  local mp fstype options state backup tmp
  mp="$(quota_mount_field TARGET)"
  fstype="$(quota_mount_field FSTYPE)"
  options="$(quota_mount_field OPTIONS)"
  [[ -n "$mp" ]] || deny "cannot find the filesystem holding ${QUOTA_HOME}"
  case "$fstype" in
    ext4|xfs) ;;
    *) deny "kernel quota needs ext4 or xfs; ${mp} is ${fstype:-unknown}" ;;
  esac
  if ! command -v setquota >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y quota >/dev/null || deny "apt-get could not install the quota tools"
  fi
  if ! quota_options_active "$options"; then
    if [[ "$fstype" == xfs && "$mp" == / ]]; then
      quota_status
      return 0
    fi
    if ! quota_options_active "$(fstab_quota_options "$FSTAB_FILE" "$mp")"; then
      tmp="$(mktemp)"
      local rc=0
      fstab_with_quota_options "$FSTAB_FILE" "$mp" "$fstype" >"$tmp" || rc=$?
      if (( rc != 0 )); then
        rm -f -- "$tmp"
        (( rc == 2 )) && deny "${mp} has no entry in ${FSTAB_FILE}; add the quota options by hand"
        deny "could not prepare ${FSTAB_FILE}"
      fi
      # Refuse anything findmnt cannot parse: a broken fstab is a server that
      # does not boot.
      if [[ "$(findmnt --help 2>&1 || true)" == *--verify* ]] && ! findmnt --verify --tab-file "$tmp" >/dev/null 2>&1; then
        rm -f -- "$tmp"
        deny "the edited ${FSTAB_FILE} did not verify; nothing was changed"
      fi
      backup="${FSTAB_FILE}.spanel-$(date +%Y%m%d%H%M%S)"
      cp -p -- "$FSTAB_FILE" "$backup"
      cat "$tmp" >"$FSTAB_FILE"
      rm -f -- "$tmp"
      systemctl daemon-reload >/dev/null 2>&1 || true
      logger -t spanel-helper -- "quota: added user quota options for ${mp} to ${FSTAB_FILE} (backup ${backup})" 2>/dev/null || true
      echo "backup=${backup}"
    fi
    quota_status
    return 0
  fi
  if [[ "$fstype" == ext4 ]]; then
    # Journaled quota: build the quota file once, then switch it on. -m keeps
    # the filesystem mounted read-write while it is counted.
    if [[ ! -f "${mp%/}/aquota.user" ]]; then
      quotacheck -cum "$mp" || deny "quotacheck failed on ${mp}"
    fi
    if [[ "$(quotaon -pu "$mp" 2>/dev/null || true)" != *" is on"* ]]; then
      quotaon -u "$mp" || deny "quotaon failed on ${mp} (is the quota_v2 kernel module available? linux-modules-extra-$(uname -r))"
    fi
  fi
  quota_status
}

quota_limits() {
  # quota_limits <storage_mb> <inode_limit>: "block-soft block-hard inode-soft
  # inode-hard" for setquota. Blocks are KiB; soft is 95% of hard, so the
  # account is warned (grace period) before writes start failing. 0 = no limit.
  local mb="${1:-0}" inodes="${2:-0}" bhard bsoft ihard isoft
  [[ "$mb" =~ ^[0-9]+$ ]] || mb=0
  [[ "$inodes" =~ ^[0-9]+$ ]] || inodes=0
  bhard=$(( mb * 1024 ))
  bsoft=$(( bhard * 95 / 100 ))
  ihard="$inodes"
  isoft=$(( ihard * 95 / 100 ))
  printf '%s %s %s %s\n' "$bsoft" "$bhard" "$isoft" "$ihard"
}

apply_kernel_quota() {
  # apply_kernel_quota <user> <storage_mb> <inode_limit>: no-op unless user
  # quota is mounted on the filesystem holding /home.
  local user="$1" mp limits
  command -v setquota >/dev/null 2>&1 || return 0
  quota_options_active "$(quota_mount_field OPTIONS)" || return 0
  mp="$(quota_mount_field TARGET)"
  [[ -n "$mp" ]] || return 0
  limits="$(quota_limits "$2" "$3")"
  # shellcheck disable=SC2086 # four numbers, split on purpose
  setquota -u "$user" $limits "$mp" >/dev/null 2>&1 || \
    echo "warning: setquota failed for ${user} on ${mp}" >&2
}

# --- clock --------------------------------------------------------------------
# Cheap VPS hosts routinely block outbound UDP 123, so systemd-timesyncd and
# chrony never converge and the clock drifts until TOTP logins stop matching.
# When the kernel clock is not disciplined, read the time from an HTTPS Date
# header instead - it travels over the same 443 the panel already needs - and
# step the clock to it.
TIME_HTTP_SOURCES=(https://time.google.com https://www.cloudflare.com https://www.google.com)
CLOCK_SKEW_THRESHOLD=2   # seconds; a smaller drift is left alone

clock_is_synchronized() {
  [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" == "yes" ]]
}

http_date_epoch() {
  # Print epoch seconds from the first reachable source's Date header.
  local url header
  for url in "$@"; do
    header="$(curl -sI --max-time 8 -H 'Cache-Control: no-cache' "$url" 2>/dev/null \
      | tr -d '\r' | awk -F': ' 'tolower($1) == "date" { print $2; exit }')"
    [[ -n "$header" ]] || continue
    date -u -d "$header" +%s 2>/dev/null && return 0
  done
  return 1
}

time_sync() {
  local before target skew
  if clock_is_synchronized; then
    echo "clock already disciplined by NTP; nothing to do"
    return 0
  fi
  target="$(http_date_epoch "${TIME_HTTP_SOURCES[@]}" || true)"
  if [[ -z "$target" ]]; then
    echo "no HTTPS time source was reachable; leaving the clock as is (the timer will retry)"
    return 0
  fi
  # Sample the local clock now, after the fetch, so curl round-trip latency
  # is not counted as skew.
  before="$(date -u +%s)"
  skew=$(( target - before ))
  if (( skew < 0 )); then skew=$(( -skew )); fi
  if (( skew < CLOCK_SKEW_THRESHOLD )); then
    echo "clock is within ${skew}s of the HTTPS time source; left as is"
    return 0
  fi
  date -u -s "@${target}" >/dev/null || deny "could not step the system clock"
  hwclock --systohc 2>/dev/null || true
  logger -t spanel-helper -- "time-sync: stepped the clock by ${skew}s (HTTPS Date header)"
  echo "clock stepped by ${skew}s from an HTTPS Date header; now $(date -u +%FT%TZ)"
}

delete_site_php_pools() {
  local user="$1" target="$2" glob dir pool_file php_version removed=""
  glob="$(site_php_pool_glob "$user" "$target")"
  # Pools of the account's own masters, then any the distro master still has.
  for dir in "$SPANEL_PHP_FPM_ROOT"/*/"$user"/pool.d "$PHP_ETC_ROOT"/*/fpm/pool.d; do
    [[ -d "$dir" ]] || continue
    for pool_file in "$dir"/$glob.conf; do
      [[ -f "$pool_file" ]] || continue
      rm -f "$pool_file"
      removed+="${pool_file}"$'\n'
    done
  done
  # A master whose last pool went is stopped and removed by the re-sync.
  [[ -n "$removed" ]] && reload_php_pool_owners <<<"$removed"
  return 0
}

# Writes the site's pool for its account's own master and brings that master
# up. When the master cannot serve a pool that is new to it - a new site, a
# PHP version change, or the move of an existing site off the distro master -
# the pool goes to the distro master instead, as before per-account masters,
# and site_users.php_fpm_socket() follows: it names the account socket only
# while the account's pool file exists.
ensure_php_pool() {
  local user="$1" target="$2" php_version="$3"
  [[ "$php_version" != "none" ]] || return 0
  require_linux_user "$user"
  require_php_version "$php_version"
  target=$(readlink -m "$target") || deny "cannot resolve $target"
  local pool_name user_dir pool_file legacy_file unit previous=""
  pool_name="$(php_pool_name "$user" "$target" "$php_version")"
  user_dir="$(user_php_fpm_dir "$php_version" "$user")"
  pool_file="${user_dir}/pool.d/${pool_name}.conf"
  legacy_file="$(legacy_php_pool_file "$php_version" "$pool_name")"
  unit="$(user_php_fpm_unit "$php_version" "$user")"
  ensure_php_runtime_dirs "$user"
  # The distro copy of a pool that moved is NOT retired here: callers rewrite
  # the vhost after this returns, and nginx may only reload at the end of a
  # whole batch (update.sh's site refresh). php-fpm-legacy-prune does it once
  # nginx is on the new socket.
  calculate_php_fpm_pool_tuning "$pool_file"
  install -d -m 0755 "$SPANEL_PHP_FPM_ROOT" "${SPANEL_PHP_FPM_ROOT}/${php_version}" "$user_dir" "${user_dir}/pool.d"
  if [[ -f "$pool_file" ]]; then
    previous="$(mktemp)"
    cp -p -- "$pool_file" "$previous"
  fi
  render_php_pool_conf "$pool_name" "$user" "$target" "$(user_php_fpm_socket "$user" "$pool_name")" >"$pool_file"
  chmod 0644 "$pool_file"
  apply_saved_user_limits_to_pool_file "$pool_file" "$user"
  if sync_user_php_fpm_master "$php_version" "$user"; then
    [[ -z "$previous" ]] || rm -f -- "$previous"
    return 0
  fi

  if [[ -n "$previous" ]]; then
    # The site was already served by this master and its vhost names the
    # account socket: moving it now would point nginx at nothing. Put the
    # pool back as it was and report.
    mv -f -- "$previous" "$pool_file"
    sync_user_php_fpm_master "$php_version" "$user" || true
    deny "${unit} did not accept the updated pool ${pool_name}; the previous pool was restored (journalctl -u ${unit})"
  fi

  rm -f -- "$pool_file"
  sync_user_php_fpm_master "$php_version" "$user" || true
  logger -t spanel-helper -- "php-fpm: ${unit} did not start; ${pool_name} stays on php${php_version}-fpm" 2>/dev/null || true
  echo "warning: ${unit} did not start; ${pool_name} is served by php${php_version}-fpm instead (journalctl -u ${unit})" >&2
  render_php_pool_conf "$pool_name" "$user" "$target" "/run/php/${pool_name}.sock" >"$legacy_file"
  chmod 0644 "$legacy_file"
  apply_saved_user_limits_to_pool_file "$legacy_file" "$user"
  ensure_php_fpm_cgroup_dropin "$php_version"
  systemctl reload "php${php_version}-fpm"
  classify_all_tenant_workers
}

ensure_php_runtime_dirs() {
  local user="$1"
  local sess_dir="/var/lib/php/sessions/${user}"
  local upload_dir="/var/lib/php/uploads/${user}"
  ensure_sites_group
  require_linux_user "$user"
  install -d -o root -g "$SPANEL_SITES_GROUP" -m 0750 /var/log/php 2>/dev/null || true
  install -d -o "$user" -g "$user" -m 0700 "$sess_dir"
  # PHP keeps uploaded files in this directory before WordPress renames them
  # into wp-content/uploads. Keep the directory private to the site user, but
  # make it setgid spanel-sites so moved uploads remain readable by nginx.
  install -d -o "$user" -g "$SPANEL_SITES_GROUP" -m 2700 "$upload_dir"
  chmod g+s "$upload_dir" 2>/dev/null || true
}

# Ownership and ACLs for a site tree, and nothing else about its modes: they
# are the owner's to choose in the file manager - a 755 script, a 777 upload
# folder, a 600 secret. This is what runs after anything the owner did not
# ask to reset permissions with: a file manager operation, a new cron job, a
# PHP version change, an update. Resetting modes there undid those choices
# without a word (operator, 2026-09-27). The special bits the file manager
# refuses are still taken off: setuid, setgid on a file, the sticky bit.
own_site_tree() {
  local target="$1" user="$2"
  ensure_sites_group
  require_linux_user "$user"
  chown -R "$user:$SPANEL_SITES_GROUP" "$target"
  if command -v setfacl >/dev/null 2>&1; then
    setfacl -Rb "$target" 2>/dev/null || true
    if [[ -d "$target" ]]; then
      find "$target" -type d -exec setfacl -k {} + 2>/dev/null || true
    fi
  fi
  find "$target" ! -type l -perm /4000 -exec chmod u-s {} + 2>/dev/null || true
  find "$target" -type f -perm /2000 -exec chmod g-s {} + 2>/dev/null || true
  find "$target" ! -type l -perm /1000 -exec chmod -t {} + 2>/dev/null || true
}

# The same, then every folder 755 and every file 644: only for an explicit
# request - the fix-permissions button, a WordPress install, a restore, a
# DirectAdmin import - or for a tree the panel has just put there.
fix_site_tree() {
  local target="$1" user="$2"
  own_site_tree "$target" "$user"
  if [[ -d "$target" ]]; then
    find "$target" -type d -exec chmod 755 {} +
    find "$target" -type d -exec chmod a-s {} + 2>/dev/null || true
    find "$target" -type d -exec chmod -t {} + 2>/dev/null || true
    find "$target" -type f -exec chmod 644 {} +
    protect_site_secret_tree "$target"
  else
    harden_site_file "$target" "$user"
  fi
}

require_ip_or_cidr() {
  [[ "$1" =~ ^[0-9a-fA-F.:/]+$ ]] || deny "invalid IP/CIDR: $1"
}

# Validate and canonicalise an address/network before it reaches ipset. The
# value ends up in an `ipset restore` script, so nothing but a normalised
# network is ever allowed through.
require_ip_or_cidr_normalized() {
  local value="$1" normalized
  require_ip_or_cidr "$value"
  normalized="$(python3 - "$value" <<'PY' 2>/dev/null || true
import ipaddress
import sys

try:
    print(ipaddress.ip_network(sys.argv[1], strict=False))
except ValueError:
    sys.exit(1)
PY
)"
  [[ -n "$normalized" ]] || deny "invalid IP/CIDR: $value"
  [[ "$normalized" =~ ^[0-9a-fA-F.:]+/[0-9]{1,3}$ ]] || deny "invalid IP/CIDR: $value"
  printf '%s' "$normalized"
}

cmd="${1:-}"
shift || true
audit_log "$@"

case "$cmd" in

  # ---- systemctl --------------------------------------------------------
  systemctl)
    [[ $# -ge 2 ]] || deny "usage: systemctl <service> <action>"
    service="$1"; action="$2"
    is_allowed_service "$service" || deny "service not allowed: $service"
    is_in "$action" "${ALLOWED_ACTIONS[@]}" || deny "action not allowed: $action"
    if [[ "$action" == "stop" && ( "$service" == "spanel-api" || "$service" == "redis-server" ) ]]; then
      deny "refusing to stop panel-critical service: $service"
    fi
    exec systemctl "$action" "$service"
    ;;

  daemon-reload)
    exec systemctl daemon-reload
    ;;

  # ---- nginx ------------------------------------------------------------
  nginx-test)
    exec nginx -t
    ;;

  nginx-reload)
    nginx -t
    exec systemctl reload nginx
    ;;
  nginx-custom-write)
    [[ $# -eq 1 ]] || deny "usage: nginx-custom-write <domain>"
    domain="$1"
    require_domain "$domain"
    ensure_nginx_conf_dir_writable
    target="${NGINX_CUSTOM_DIR}/${domain}.conf"
    tmp="${target}.tmp.$$"
    cat >"$tmp"
    if file_has_nul "$tmp"; then
      rm -f "$tmp"
      deny "custom nginx include contains NUL byte"
    fi
    install -m 0664 -o root -g spanel "$tmp" "$target"
    rm -f "$tmp"
    ;;
  nginx-custom-delete)
    [[ $# -eq 1 ]] || deny "usage: nginx-custom-delete <domain>"
    domain="$1"
    require_domain "$domain"
    rm -f "${NGINX_CUSTOM_DIR}/${domain}.conf"
    ;;

  fastcgi-cache-clear)
    [[ $# -eq 0 ]] || deny "usage: fastcgi-cache-clear"
    install -d -o www-data -g www-data -m 0755 /var/cache/nginx/spanel-fastcgi
    find /var/cache/nginx/spanel-fastcgi -mindepth 1 -delete
    ;;

  # ---- updates ----------------------------------------------------------
  updates-status)
    echo "SPanel release status:"
    if [[ -f "${SPANEL_DATA_DIR}/update-status.json" ]]; then
      cat "${SPANEL_DATA_DIR}/update-status.json"
    else
      echo "No update status file found."
    fi
    echo ""
    echo "APT upgradable packages:"
    apt list --upgradable 2>/dev/null | sed -n '1,60p' || true
    echo ""
    echo "Unattended upgrades:"
    systemctl is-enabled unattended-upgrades.service 2>/dev/null || true
    systemctl is-active unattended-upgrades.service 2>/dev/null || true
    echo ""
    echo "OS update service:"
    systemctl is-active spanel-os-update.service 2>/dev/null | sed 's/^inactive$/idle/' || true
    journalctl -u spanel-os-update.service -n 16 --no-pager 2>/dev/null | grep -v "Failed to open /run/systemd/transient" || true
    echo ""
    echo "Panel update service:"
    systemctl is-active spanel-panel-update.service 2>/dev/null | sed 's/^inactive$/idle/' || true
    journalctl -u spanel-panel-update.service -n 16 --no-pager 2>/dev/null | grep -v "Failed to open /run/systemd/transient" || true
    echo ""
    echo "Panel update log:"
    if command -v journalctl >/dev/null 2>&1 && systemctl cat spanel-panel-update.service >/dev/null 2>&1; then
      journalctl -u spanel-panel-update.service -n 60 --no-pager 2>/dev/null | grep -v "Failed to open /run/systemd/transient" || true
    fi
    if [[ ! -s /dev/stdin ]]; then :; fi
    if [[ -f /var/log/spanel-panel-update.log ]]; then
      echo "--- /var/log/spanel-panel-update.log (tail) ---"
      tail -n 60 /var/log/spanel-panel-update.log 2>/dev/null || true
    fi
    ;;

  updates-os-run)
    run_os_update
    ;;

  updates-core-stack-patch)
    [[ $# -ge 1 ]] || deny "usage: updates-core-stack-patch <pkg> [pkg...]"
    upgrade_core_stack_packages "$@"
    ;;

  updates-core-blacklist)
    [[ $# -ge 1 ]] || deny "usage: updates-core-blacklist <on|off> [pkg...]"
    set_core_stack_blacklist "$@"
    ;;

  updates-os-auto)
    [[ $# -eq 3 ]] || deny "usage: updates-os-auto <on|off> <security|all> <on|off>"
    configure_unattended_upgrades "$1" "$2" "$3"
    ;;

  updates-panel-run)
    run_panel_update
    ;;

  panel-snapshot-create)
    [[ $# -le 2 ]] || deny "usage: panel-snapshot-create [reason] [keep]"
    panel_snapshot_create "${1:-manual}" "${2:-$DEFAULT_SNAPSHOT_KEEP}"
    ;;

  panel-snapshot-restore)
    [[ $# -ge 1 && $# -le 3 ]] || deny "usage: panel-snapshot-restore <snapshot-id> [--restore-db] [--now]"
    panel_snapshot_restore "$@"
    ;;

  panel-snapshot-delete)
    [[ $# -eq 1 ]] || deny "usage: panel-snapshot-delete <snapshot-id>"
    panel_snapshot_delete "$1"
    ;;

  panel-db-restore)
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: panel-db-restore <backup-name> [--now]"
    panel_db_restore "$1" "${2:-}"
    ;;

  panel-dr-key-status)
    [[ $# -eq 0 ]] || deny "usage: panel-dr-key-status"
    panel_dr_key_status
    ;;

  panel-dr-key-init)
    [[ $# -le 1 ]] || deny "usage: panel-dr-key-init [--rotate]"
    panel_dr_key_init "${1:-}"
    ;;

  panel-dr-archive)
    [[ $# -eq 0 ]] || deny "usage: panel-dr-archive"
    PANEL_DR_WORK=""
    trap '[[ -n "${PANEL_DR_WORK:-}" ]] && rm -rf "$PANEL_DR_WORK"' EXIT
    panel_dr_archive
    ;;

  # ---- WAF --------------------------------------------------------------
  waf-status)
    waf_status
    ;;

  waf-install)
    install_waf_engine
    ;;

  # ---- ClamAV malware scanning (optional) -------------------------------
  fail2ban-install)
    [[ $# -eq 0 ]] || deny "usage: fail2ban-install"
    install_fail2ban
    ;;

  fail2ban-remove)
    [[ $# -eq 0 ]] || deny "usage: fail2ban-remove"
    remove_fail2ban
    ;;

  fail2ban-status)
    [[ $# -eq 0 ]] || deny "usage: fail2ban-status"
    fail2ban_status
    ;;

  # ---- DNS Manager (PowerDNS, optional) ---------------------------------
  dns-install)
    [[ $# -eq 0 ]] || deny "usage: dns-install"
    install_dns
    ;;

  dns-remove)
    [[ $# -eq 0 ]] || deny "usage: dns-remove"
    remove_dns
    ;;

  dns-status)
    [[ $# -eq 0 ]] || deny "usage: dns-status"
    dns_status
    ;;

  dns-transfers)
    [[ $# -eq 1 ]] || deny "usage: dns-transfers <on|off>"
    dns_transfers "$1"
    ;;

  # ---- Email (Exim + Dovecot + webmail, optional) -------------------------
  mail-install)
    [[ $# -eq 1 ]] || deny "usage: mail-install <mail-hostname>"
    install_mail "$1"
    ;;

  mail-remove)
    [[ $# -eq 0 ]] || deny "usage: mail-remove"
    remove_mail
    ;;

  mail-status)
    [[ $# -eq 0 ]] || deny "usage: mail-status"
    mail_status
    ;;

  mail-sync)
    [[ $# -eq 0 ]] || deny "usage: mail-sync < mailboxes.json"
    mail_sync
    ;;

  mail-delete-box)
    [[ $# -eq 3 ]] || deny "usage: mail-delete-box <site-user> <domain> <name>"
    mail_delete_box "$1" "$2" "$3"
    ;;

  mail-import)
    [[ $# -eq 4 ]] || deny "usage: mail-import <site-user> <domain> <name> <staged-dir>"
    mail_import "$1" "$2" "$3" "$4"
    ;;

  mail-webmail-host)
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: mail-webmail-host <domain> [email]"
    mail_webmail_host "$1" "${2:-}"
    ;;

  mail-webmail-host-remove)
    [[ $# -eq 1 ]] || deny "usage: mail-webmail-host-remove <domain>"
    mail_webmail_host_remove "$1"
    ;;

  mail-webmail-hosts)
    [[ $# -eq 0 ]] || deny "usage: mail-webmail-hosts"
    mail_webmail_hosts
    ;;

  mail-hosts-apply)
    [[ $# -eq 0 ]] || deny "usage: mail-hosts-apply < hosts.json"
    mail_hosts_apply
    ;;

  mail-hosts-remove)
    [[ $# -eq 1 ]] || deny "usage: mail-hosts-remove <domain>"
    mail_hosts_remove "$1"
    ;;

  mail-configure)
    [[ $# -eq 0 ]] || deny "usage: mail-configure < settings.json"
    mail_configure
    ;;

  mail-reconfigure)
    [[ $# -eq 0 ]] || deny "usage: mail-reconfigure"
    mail_reconfigure
    ;;

  mail-dkim)
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: mail-dkim <domain> [selector]"
    mail_dkim "$1" "${2:-spanel}"
    ;;

  mail-dkim-drop)
    [[ $# -eq 2 ]] || deny "usage: mail-dkim-drop <domain> <selector>"
    mail_dkim_drop "$1" "$2"
    ;;

  mail-dkim-export)
    [[ $# -eq 1 ]] || deny "usage: mail-dkim-export <domain>"
    mail_dkim_export "$1"
    ;;

  mail-dkim-import)
    [[ $# -eq 2 ]] || deny "usage: mail-dkim-import <domain> <selector> < key.pem"
    mail_dkim_import "$1" "$2"
    ;;

  mail-log)
    [[ $# -eq 1 ]] || deny "usage: mail-log <lines>"
    mail_log_tail "$1"
    ;;

  mail-queue)
    [[ $# -eq 0 ]] || deny "usage: mail-queue"
    mail_queue_count
    ;;

  mail-queue-list)
    [[ $# -eq 0 ]] || deny "usage: mail-queue-list"
    mail_queue_list
    ;;

  mail-queue-show)
    [[ $# -eq 1 ]] || deny "usage: mail-queue-show <id>"
    mail_queue_show "$1"
    ;;

  mail-queue-action)
    [[ $# -ge 2 ]] || deny "usage: mail-queue-action <retry|freeze|thaw|delete> <id>..."
    mail_queue_action "$@"
    ;;

  mail-queue-retry-all)
    [[ $# -eq 0 ]] || deny "usage: mail-queue-retry-all"
    mail_queue_retry_all
    ;;

  mail-rspamd-log)
    [[ $# -eq 1 ]] || deny "usage: mail-rspamd-log <lines>"
    mail_rspamd_log "$1"
    ;;

  mail-purge-domain)
    [[ $# -eq 2 ]] || deny "usage: mail-purge-domain <site-user> <domain>"
    mail_purge_domain "$1" "$2"
    ;;

  mail-relay-test)
    [[ $# -eq 1 ]] || deny "usage: mail-relay-test <address>"
    mail_relay_test "$1"
    ;;

  fail2ban-banned)
    [[ $# -le 1 ]] || deny "usage: fail2ban-banned [jail]"
    fail2ban_banned_list "${1:-sshd}"
    ;;

  fail2ban-ban)
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: fail2ban-ban <ip> [jail]"
    [[ "$1" =~ ^[0-9a-fA-F:./]+$ ]] || deny "not an address: $1"
    jail_name="${2:-sshd}"
    [[ "$jail_name" =~ ^[a-z0-9_-]{1,32}$ ]] || deny "invalid jail: $jail_name"
    fail2ban-client set "$jail_name" banip "$1" >/dev/null 2>&1 || true
    ;;

  fail2ban-unban)
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: fail2ban-unban <ip> [jail]"
    [[ "$1" =~ ^[0-9a-fA-F:./]+$ ]] || deny "not an address: $1"
    if [[ -n "${2:-}" ]]; then
      fail2ban-client set "$2" unbanip "$1" >/dev/null 2>&1 || fail2ban-client unban "$1" >/dev/null 2>&1 || deny "could not unban $1"
    else
      fail2ban-client unban "$1" >/dev/null 2>&1 || fail2ban-client set sshd unbanip "$1" >/dev/null 2>&1 || deny "could not unban $1"
    fi
    ;;

  clamav-tune)
    [[ $# -eq 0 ]] || deny "usage: clamav-tune"
    tune_clamd_limits
    ;;

  clamav-daemon-install)
    [[ $# -eq 0 ]] || deny "usage: clamav-daemon-install"
    install_clamav_daemon
    ;;

  clamav-daemon-remove)
    [[ $# -eq 0 ]] || deny "usage: clamav-daemon-remove"
    remove_clamav_daemon
    ;;

  clamav-install)
    install_clamav_engine
    ;;

  clamav-status)
    if command -v clamd >/dev/null 2>&1 || command -v clamscan >/dev/null 2>&1; then
      installed=1
    else
      installed=0
    fi
    if systemctl is-active --quiet clamav-daemon 2>/dev/null; then
      running=1
    else
      running=0
    fi
    echo "installed=${installed} running=${running}"
    ;;

  clamav-start)
    install -d -o clamav -g clamav -m 0755 /run/clamav 2>/dev/null || true
    systemctl enable --now clamav-daemon
    echo "clamav-daemon started"
    ;;

  clamav-stop)
    systemctl disable --now clamav-daemon 2>/dev/null || systemctl stop clamav-daemon
    echo "clamav-daemon stopped"
    ;;

  # ---- Linux Malware Detect (LMD) --------------------------------------
  maldet-install)
    [[ $# -eq 0 ]] || deny "usage: maldet-install"
    install_maldet_engine
    ;;

  maldet-status)
    if [[ -x "$MALDET_BIN" ]]; then echo "installed=1"; else echo "installed=0"; fi
    if maldet_monitor_running; then echo "monitor=1"; else echo "monitor=0"; fi
    maldet_sig_file="${MALDET_HOME}/sigs/maldet.sigs.ver"
    if [[ -f "$maldet_sig_file" ]]; then
      echo "sig_version=$(cat "$maldet_sig_file" 2>/dev/null || echo unknown)"
      echo "sig_updated=$(date -u -r "$maldet_sig_file" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo '')"
    else
      echo "sig_version=unknown"
      echo "sig_updated="
    fi
    ;;

  maldet-update-sigs)
    [[ $# -eq 0 ]] || deny "usage: maldet-update-sigs"
    [[ -x "$MALDET_BIN" ]] || deny "maldet is not installed"
    # Update runs this on every release, so it is also where an install that
    # predates the ignore list picks it up.
    maldet_write_conf
    "$MALDET_BIN" -u --force 2>&1 || true
    freshclam >/dev/null 2>&1 || true
    echo "signatures updated"
    ;;

  maldet-scan)
    # maldet-scan <job-id> <all|recent> <days> <path>...
    [[ $# -ge 4 ]] || deny "usage: maldet-scan <job-id> <all|recent> <days> <path>..."
    run_maldet_scan "$@"
    ;;

  maldet-progress)
    # maldet-progress <job-id>
    [[ $# -eq 1 ]] || deny "usage: maldet-progress <job-id>"
    maldet_scan_progress "$1"
    ;;

  maldet-report)
    [[ $# -eq 1 ]] || deny "usage: maldet-report <scanid>"
    [[ "$1" =~ ^[0-9]{6}-[0-9]{4}\.[0-9]+$ ]] || deny "invalid scanid"
    [[ -x "$MALDET_BIN" ]] || deny "maldet is not installed"
    [[ -f "${MALDET_HOME}/sess/session.${1}" ]] && cat "${MALDET_HOME}/sess/session.${1}" || true
    ;;

  maldet-monitor)
    [[ $# -eq 1 ]] || deny "usage: maldet-monitor <start|stop|status>"
    [[ -x "$MALDET_BIN" ]] || deny "maldet is not installed"
    case "$1" in
      start)
        command -v inotifywait >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; apt-get install -y inotify-tools >/dev/null 2>&1; }
        command -v inotifywait >/dev/null 2>&1 || deny "inotify-tools is required for real-time protection and could not be installed"
        write_inotify_sysctl
        grep -qE '^default_monitor_mode=' "$MALDET_CONF" 2>/dev/null || printf 'default_monitor_mode="users"\n' >>"$MALDET_CONF"
        # Run the monitor through maldet.service: its children stay in the unit's
        # cgroup, so `systemctl stop` cleans them all up. A bare `maldet -b -m`
        # daemonises outside systemd and orphans its inotifywait.
        systemctl enable maldet >/dev/null 2>&1 || true
        systemctl restart maldet >/dev/null 2>&1 || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
          maldet_monitor_running && break
          sleep 1
        done
        maldet_monitor_running && echo "monitor started" || deny "monitor did not start"
        ;;
      stop)
        systemctl disable --now maldet >/dev/null 2>&1 || true
        systemctl reset-failed maldet >/dev/null 2>&1 || true
        "$MALDET_BIN" --kill-monitor >/dev/null 2>&1 || true
        # Kill the supervisor first (it respawns inotifywait), then the watcher.
        pkill -f 'maldet .*--monitor' >/dev/null 2>&1 || true
        sleep 1
        pkill -f 'inotifywait .*maldetect/sess/inotify' >/dev/null 2>&1 || true
        sleep 1
        pkill -9 -f 'inotifywait .*maldetect/sess/inotify' >/dev/null 2>&1 || true
        rm -f "${MALDET_HOME}/tmp/inotifywait.pid" 2>/dev/null || true
        maldet_monitor_running && deny "monitor still running after stop" || echo "monitor stopped"
        ;;
      status)
        if maldet_monitor_running; then echo "running=1"; else echo "running=0"; fi
        echo "watches=$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo 0)"
        ;;
      *) deny "usage: maldet-monitor <start|stop|status>" ;;
    esac
    ;;

  waf-update)
    write_modsec_main_conf
    nginx -t
    systemctl reload nginx
    echo "SPanel lightweight WAF rules refreshed"
    ;;

  waf-default-rules)
    write_waf_default_rules
    exec cat /etc/nginx/modsec/spanel-default.conf
    ;;

  waf-custom-rules)
    touch /etc/nginx/modsec/spanel-custom.conf
    exec cat /etc/nginx/modsec/spanel-custom.conf
    ;;

  waf-custom-save)
    save_waf_custom_rules
    ;;
  waf-site-rules)
    [[ $# -eq 1 ]] || deny "usage: waf-site-rules <domain>"
    require_domain "$1"
    exec cat "/etc/nginx/modsec/sites/${1}.conf"
    ;;
  waf-site-save)
    [[ $# -eq 1 ]] || deny "usage: waf-site-save <domain>"
    save_waf_site_rules "$1"
    ;;
  waf-site-delete)
    [[ $# -eq 1 ]] || deny "usage: waf-site-delete <domain>"
    delete_waf_site_rules "$1"
    ;;
  da-backup-dir-ensure)
    [[ $# -eq 0 ]] || deny "usage: da-backup-dir-ensure"
    ensure_da_backup_dir
    ;;
  user-backup-dir-ensure)
    [[ $# -eq 1 ]] || deny "usage: user-backup-dir-ensure <name>"
    ensure_user_backup_dir "$1"
    ;;
  da-import-start)
    [[ $# -eq 2 ]] || deny "usage: da-import-start <archive> <force|noforce>"
    start_da_import "$1" "$2"
    ;;
  da-import-status)
    [[ $# -eq 0 ]] || deny "usage: da-import-status"
    da_import_status
    ;;
  orphans-scan)
    [[ $# -eq 0 ]] || deny "usage: orphans-scan  (live domains on stdin)"
    cleanup_orphans scan
    ;;
  orphans-clean)
    [[ $# -eq 0 ]] || deny "usage: orphans-clean  (live domains on stdin)"
    cleanup_orphans clean
    ;;
  waf-crs-install)
    [[ $# -eq 0 ]] || deny "usage: waf-crs-install"
    install_waf_crs
    ;;
  waf-crs-mode)
    [[ $# -eq 1 ]] || deny "usage: waf-crs-mode <off|detect|block>"
    set_waf_crs_mode "$1"
    ;;
  waf-crs-status)
    [[ $# -eq 0 ]] || deny "usage: waf-crs-status"
    waf_crs_status
    ;;
  http-flood-zones-save)
    [[ $# -eq 0 ]] || deny "usage: http-flood-zones-save"
    save_http_flood_zones
    ;;

  # ---- PHP installation --------------------------------------------------
  php-install)
    [[ $# -eq 1 ]] || deny "usage: php-install <version>"
    install_php_version "$1"
    ;;

  php-safe-upgrade)
    [[ $# -eq 1 ]] || deny "usage: php-safe-upgrade <version>"
    upgrade_php_version "$1"
    ;;

  php-security-harden)
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: php-security-harden <version> [strict|balanced|off]"
    harden_php_security "$1" "${2:-strict}"
    ;;

  php-uninstall)
    [[ $# -eq 1 ]] || deny "usage: php-uninstall <version>"
    uninstall_php_version "$1"
    ;;

  php-ext-install)
    [[ $# -eq 2 ]] || deny "usage: php-ext-install <version> <extension>"
    install_php_extension "$1" "$2"
    ;;

  php-opcache-set)
    [[ $# -eq 2 ]] || deny "usage: php-opcache-set <php-version> <0|1>"
    write_php_opcache_switch "$1" "$2"
    ;;

  php-tune-write)
    [[ $# -eq 1 ]] || deny "usage: php-tune-write <php-version>"
    write_php_tune "$1"
    ;;

  php-pools-retune)
    # Pool sizes are decided when a pool is written. A server that gained RAM
    # keeps the old numbers until every site happens to be touched; this walks
    # them all and rewrites each one against the machine as it is now.
    [[ $# -eq 0 ]] || deny "usage: php-pools-retune"
    retuned=0
    pool_files="$(managed_php_pool_files)"
    while IFS= read -r pool_file; do
      [[ -n "$pool_file" && -f "$pool_file" ]] || continue
      calculate_php_fpm_pool_tuning "$pool_file"
      php_fpm_set_directive "$pool_file" "pm" "$PHP_FPM_PM_MODE"
      php_fpm_set_directive "$pool_file" "pm.max_children" "$PHP_FPM_MAX_CHILDREN"
      php_fpm_set_directive "$pool_file" "pm.process_idle_timeout" "${PHP_FPM_PROCESS_IDLE_TIMEOUT}s"
      php_fpm_set_directive "$pool_file" "pm.max_requests" "$PHP_FPM_MAX_REQUESTS"
      php_fpm_set_directive "$pool_file" "request_terminate_timeout" "${PHP_FPM_REQUEST_TERMINATE_TIMEOUT}s"
      echo "$(basename "$pool_file"): pm.max_children=${PHP_FPM_MAX_CHILDREN} idle=${PHP_FPM_PROCESS_IDLE_TIMEOUT}s max_requests=${PHP_FPM_MAX_REQUESTS}"
      retuned=$((retuned + 1))
    done <<<"$pool_files"
    reload_php_pool_owners <<<"$pool_files"
    echo "retuned ${retuned} pool(s)"
    ;;

  php-fpm-legacy-prune)
    # Run after vhosts were rewritten to the per-account sockets (update.sh
    # does, right after its site refresh): drops the distro-master copies of
    # pools that moved and that no vhost names any more.
    [[ $# -eq 0 ]] || deny "usage: php-fpm-legacy-prune"
    prune_legacy_php_pools
    ;;

  php-config-write)
    [[ $# -eq 1 ]] || deny "usage: php-config-write <version>"
    write_php_config "$1"
    ;;

  php-fpm-retune)
    [[ $# -eq 0 ]] || deny "usage: php-fpm-retune"
    retune_php_fpm_pools
    ;;

  mariadb-retune)
    [[ $# -eq 0 ]] || deny "usage: mariadb-retune"
    retune_mariadb
    ;;

  time-sync)
    [[ $# -eq 0 ]] || deny "usage: time-sync"
    time_sync
    ;;

  time-status)
    [[ $# -eq 0 ]] || deny "usage: time-status"
    if clock_is_synchronized; then echo "synchronized=yes"; else echo "synchronized=no"; fi
    echo "timezone=$(timedatectl show -p Timezone --value 2>/dev/null)"
    now="$(date -u +%s)"
    ref="$(http_date_epoch "${TIME_HTTP_SOURCES[@]}" || true)"
    if [[ -n "$ref" ]]; then
      echo "skew_seconds=$(( ref - now ))"
      echo "reference=https-date"
    else
      echo "skew_seconds="
      echo "reference=none"
    fi
    ;;

  # ---- panel runtime ----------------------------------------------------
  panel-url-set)
    [[ $# -eq 3 ]] || deny "usage: panel-url-set <http|https> <host> <port>"
    scheme="$1"; host="$2"; port="$3"
    require_panel_scheme "$scheme"
    require_panel_host "$host"
    require_port "$port"
    env_set PANEL_PORT "$port"
    env_set PANEL_URL "${scheme}://${host}:${port}"
    env_set ALLOWED_ORIGINS "${scheme}://${host}:${port}"
    if is_domain "$host"; then
      env_set PANEL_DOMAIN "$host"
    else
      env_set PANEL_DOMAIN ""
    fi
    if [[ "$scheme" == "http" ]]; then
      env_set PANEL_SSL_CERT ""
      env_set PANEL_SSL_KEY ""
    fi
    allow_panel_port "$port"
    refresh_tools_nginx
    schedule_panel_restart
    echo "Panel URL: ${scheme}://${host}:${port}"
    ;;

  malware-scan-server)
    # Scan the whole machine. The panel owns the job bookkeeping; this only
    # runs the scanner and leaves its output where the panel can read it.
    [[ $# -eq 1 ]] || deny "usage: malware-scan-server <job-id>"
    run_malware_server_scan "$1"
    ;;

  malware-scan-server-estimate)
    [[ $# -eq 0 ]] || deny "usage: malware-scan-server-estimate"
    printf 'total=%s\n' "$(malware_scan_file_list | wc -l)"
    ;;

  ipv6-status)
    [[ $# -eq 0 ]] || deny "usage: ipv6-status"
    if ipv6_available; then
      echo "available=yes"
    else
      echo "available=no"
    fi
    if ipv6_is_enabled; then
      echo "enabled=yes"
    else
      echo "enabled=no"
    fi
    echo "addresses=$(ipv6_global_addresses | paste -sd, -)"
    ;;

  ipv6-enable)
    [[ $# -eq 0 ]] || deny "usage: ipv6-enable"
    ipv6_available || deny "this server has no global IPv6 address"
    install -d -o root -g spanel -m 0750 /etc/spanel
    : >"$PANEL_IPV6_MARKER"
    chmod 0644 "$PANEL_IPV6_MARKER"
    if ! nginx_ipv6_apply; then
      # nginx_ipv6_apply already put every file back the way it found it.
      rm -f "$PANEL_IPV6_MARKER"
      deny "nginx refused the IPv6 configuration; nothing was changed"
    fi
    refresh_tools_nginx
    schedule_panel_restart
    echo "IPv6 enabled: $(ipv6_global_addresses | paste -sd, -)"
    ;;

  ipv6-disable)
    [[ $# -eq 0 ]] || deny "usage: ipv6-disable"
    rm -f "$PANEL_IPV6_MARKER"
    nginx_ipv6_apply || deny "nginx refused the configuration without IPv6"
    refresh_tools_nginx
    schedule_panel_restart
    echo "IPv6 disabled"
    ;;

  ipv6-apply)
    # Re-apply the switch after an update rewrote the vhosts. No-op when off.
    [[ $# -eq 0 ]] || deny "usage: ipv6-apply"
    if ipv6_is_enabled && ! ipv6_available; then
      # The address went away with the switch left on; do not hand nginx a
      # socket it cannot bind.
      rm -f "$PANEL_IPV6_MARKER"
      nginx_ipv6_apply || deny "nginx refused the configuration without IPv6"
      echo "IPv6 is no longer available on this server; the switch was turned off"
      exit 0
    fi
    nginx_ipv6_apply || deny "nginx refused the IPv6 configuration"
    ipv6_is_enabled && echo "IPv6 applied" || echo "IPv6 is off"
    ;;

  panel-sni-sync)
    # Refresh the certificates the panel can answer a handshake with. Safe to
    # run at any time: it only copies what is already on the machine.
    [[ $# -eq 0 ]] || deny "usage: panel-sni-sync"
    install_sni_renewal_hook
    sync_panel_sni_certificates
    ;;

  panel-ssl-domains)
    # /etc/letsencrypt/live is root-only, so the panel cannot see for itself
    # which of its websites already have a certificate it could borrow.
    [[ $# -eq 0 ]] || deny "usage: panel-ssl-domains"
    for live_dir in /etc/letsencrypt/live/*/; do
      [[ -f "${live_dir}fullchain.pem" && -f "${live_dir}privkey.pem" ]] || continue
      basename "$live_dir"
    done
    ;;

  panel-ssl-selfsigned)
    # The panel should never be reachable in the clear, and a brand new server
    # has no domain and no certificate authority that will vouch for its IP.
    # A self-signed certificate warns the browser once; plain HTTP does not warn
    # anybody while it hands over the admin password.
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: panel-ssl-selfsigned <hostname-or-ip> <port>"
    host="$1"; port="${2:-2222}"
    require_port "$port"
    [[ "$host" =~ ^[A-Za-z0-9.:_-]{1,253}$ ]] || deny "invalid panel hostname: $host"
    install -d -o root -g spanel -m 0750 /etc/spanel
    san="DNS:${host}"
    [[ "$host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && san="IP:${host}"
    server_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [[ -n "$server_ip" && "$server_ip" != "$host" ]] && san="${san},IP:${server_ip}"
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
      -keyout /etc/spanel/panel-selfsigned-privkey.pem \
      -out /etc/spanel/panel-selfsigned-fullchain.pem \
      -subj "/CN=${host}" -addext "subjectAltName=${san}" >/dev/null 2>&1 \
      || deny "could not generate a self-signed certificate"
    chown root:spanel /etc/spanel/panel-selfsigned-fullchain.pem /etc/spanel/panel-selfsigned-privkey.pem
    chmod 0640 /etc/spanel/panel-selfsigned-fullchain.pem /etc/spanel/panel-selfsigned-privkey.pem
    env_set PANEL_SSL_CERT "/etc/spanel/panel-selfsigned-fullchain.pem"
    env_set PANEL_SSL_KEY "/etc/spanel/panel-selfsigned-privkey.pem"
    env_set PANEL_SSL_MODE "selfsigned"
    env_set PANEL_URL "https://${host}:${port}"
    env_set ALLOWED_ORIGINS "https://${host}:${port}"
    allow_panel_port "$port"
    refresh_tools_nginx
    schedule_panel_restart
    echo "Panel is on a self-signed certificate: https://${host}:${port}"
    ;;

  panel-ssl-use-domain)
    # A domain hosted here that already has a real certificate is a better
    # answer than a self-signed one, and than asking a certificate authority
    # for a second certificate covering the same name.
    [[ $# -eq 2 ]] || deny "usage: panel-ssl-use-domain <domain> <port>"
    domain="$1"; port="$2"
    require_domain "$domain"
    require_port "$port"
    live_dir="/etc/letsencrypt/live/${domain}"
    [[ -f "${live_dir}/fullchain.pem" && -f "${live_dir}/privkey.pem" ]] \
      || deny "no certificate for ${domain}; issue SSL for that website first"
    install -d -o root -g spanel -m 0750 /etc/spanel
    install -m 0640 -o root -g spanel "${live_dir}/fullchain.pem" /etc/spanel/panel-fullchain.pem
    install -m 0640 -o root -g spanel "${live_dir}/privkey.pem" /etc/spanel/panel-privkey.pem
    env_set PANEL_DOMAIN "$domain"
    env_set PANEL_SSL_CERT "/etc/spanel/panel-fullchain.pem"
    env_set PANEL_SSL_KEY "/etc/spanel/panel-privkey.pem"
    env_set PANEL_SSL_MODE "domain"
    env_set PANEL_URL "https://${domain}:${port}"
    env_set ALLOWED_ORIGINS "https://${domain}:${port}"
    install_panel_cert_renewal_hook
    install_sni_renewal_hook
    sync_panel_sni_certificates >/dev/null
    allow_panel_port "$port"
    refresh_tools_nginx
    schedule_panel_restart
    echo "Panel now uses the certificate of ${domain}: https://${domain}:${port}"
    ;;

  panel-ssl-install)
    [[ $# -ge 2 && $# -le 3 ]] || deny "usage: panel-ssl-install <domain> <port> [email]"
    domain="$1"; port="$2"; email="${3:-}"
    require_domain "$domain"
    require_port "$port"
    # Webroot, not standalone: standalone needs port 80 to itself, which meant
    # stopping nginx — every website on the box went down for the ten seconds
    # certbot spent talking to Let's Encrypt, to issue a certificate for the
    # panel. The default vhost serves the challenge instead.
    install -d -o root -g spanel -m 0755 /var/www/spanel-acme/.well-known/acme-challenge
    certbot_args=(certonly --webroot -w /var/www/spanel-acme
      -d "$domain" \
      --agree-tos \
      --non-interactive \
      --keep-until-expiring \
      --deploy-hook "install -d -o root -g spanel -m 0750 /etc/spanel && install -m 0640 -o root -g spanel /etc/letsencrypt/live/${domain}/fullchain.pem /etc/spanel/panel-fullchain.pem && install -m 0640 -o root -g spanel /etc/letsencrypt/live/${domain}/privkey.pem /etc/spanel/panel-privkey.pem")
    if [[ -n "$email" ]]; then
      require_email "$email"
      certbot_args+=(--email "$email")
    else
      certbot_args+=(--register-unsafely-without-email)
    fi
    # certbot exits 1 for "certificate not yet due for renewal" even with
    # --keep-until-expiring - that is certbot telling us it did nothing, not
    # that anything is wrong. Pressing the button a second time (or reinstalling
    # the panel) hits this every time after the first real issue. The only
    # thing that actually matters is whether a usable certificate exists on
    # disk afterwards; if certbot failed and there still isn't one, that is a
    # real failure and must propagate.
    certbot "${certbot_args[@]}" || {
      rc=$?
      [[ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]] || exit "$rc"
    }
    install -d -o root -g spanel -m 0750 /etc/spanel
    install -m 0640 -o root -g spanel "/etc/letsencrypt/live/${domain}/fullchain.pem" /etc/spanel/panel-fullchain.pem
    install -m 0640 -o root -g spanel "/etc/letsencrypt/live/${domain}/privkey.pem" /etc/spanel/panel-privkey.pem
    env_set PANEL_DOMAIN "$domain"
    env_set PANEL_PORT "$port"
    env_set PANEL_SSL_CERT "/etc/spanel/panel-fullchain.pem"
    env_set PANEL_SSL_KEY "/etc/spanel/panel-privkey.pem"
    env_set PANEL_SSL_MODE letsencrypt
    env_set PANEL_URL "https://${domain}:${port}"
    env_set ALLOWED_ORIGINS "https://${domain}:${port}"
    if [[ -n "$email" ]]; then
      env_set SSL_EMAIL "$email"
    fi
    allow_panel_port "$port"
    refresh_tools_nginx
    schedule_panel_restart
    echo "Panel SSL enabled: https://${domain}:${port}"
    ;;

  # ---- certbot ----------------------------------------------------------
  certbot-issue)
    [[ $# -ge 1 ]] || deny "usage: certbot-issue <domain> [alias-domain ...] [email]"
    domain="$1"; shift
    email=""
    domains=("$domain")
    require_domain "$domain"
    while [[ $# -gt 0 ]]; do
      if [[ "$1" == *@* ]]; then
        [[ $# -eq 1 ]] || deny "email must be the final certbot-issue argument"
        email="$1"
        shift
        break
      fi
      require_domain "$1"
      domains+=("$1")
      shift
    done
    install -d -o root -g spanel -m 0755 /var/www/spanel-acme/.well-known/acme-challenge
    if [[ -f "/etc/nginx/conf.d/${domain}.conf" ]]; then
      if grep -q "/var/lib/spanel/acme-challenges" "/etc/nginx/conf.d/${domain}.conf"; then
        cp -a "/etc/nginx/conf.d/${domain}.conf" "/etc/nginx/conf.d/${domain}.conf.bak"
        sed -i 's#/var/lib/spanel/acme-challenges#/var/www/spanel-acme#g' "/etc/nginx/conf.d/${domain}.conf"
        nginx -t && systemctl reload nginx
      elif ! grep -q "well-known/acme-challenge" "/etc/nginx/conf.d/${domain}.conf"; then
        cp -a "/etc/nginx/conf.d/${domain}.conf" "/etc/nginx/conf.d/${domain}.conf.bak"
        python3 - "$domain" <<'PY'
from pathlib import Path
import sys

domain = sys.argv[1]
path = Path(f"/etc/nginx/conf.d/{domain}.conf")
content = path.read_text(encoding="utf-8")
block = """\

    # SPANEL ACME CHALLENGE
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/spanel-acme;
        default_type text/plain;
        try_files $uri =404;
        access_log off;
        auth_basic off;
    }
"""
marker = "    client_max_body_size"
if marker in content:
    line_end = content.find("\n", content.find(marker))
    content = content[: line_end + 1] + block + content[line_end + 1 :]
else:
    content = content.replace("\n    location / {", block + "\n    location / {", 1)
path.write_text(content, encoding="utf-8")
PY
        nginx -t && systemctl reload nginx
      fi
    fi
    # --allow-subset-of-names: the panel now always asks for www.<domain> too
    # (nginx always listens on it) - a domain with no working www DNS record
    # must not turn a working bare-domain issuance into a total failure.
    args=(certonly --webroot -w /var/www/spanel-acme --cert-name "$domain" --non-interactive --agree-tos --expand --keep-until-expiring --allow-subset-of-names)
    for cert_domain in "${domains[@]}"; do
      args+=(-d "$cert_domain")
    done
    if [[ -n "$email" ]]; then
      require_email "$email"
      args+=(--email "$email")
    else
      args+=(--register-unsafely-without-email)
    fi
    # certbot exits 1 for "certificate not yet due for renewal" even with
    # --keep-until-expiring, even though nothing is wrong - it just means the
    # existing certificate is still good and it changed nothing. set -e would
    # otherwise abort here, before the nginx --install step ever runs, on
    # every re-issue request (a second click on "Cai SSL", --expand adding a
    # domain to an already-fresh cert, a panel reinstall). Only a call that
    # leaves no usable certificate on disk is a real failure.
    certbot "${args[@]}" || {
      rc=$?
      [[ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]] || exit "$rc"
    }
    # install --nginx is only ever pointed at the primary domain, never the
    # aliases/redirects. render_vhost() always puts both "$domain" and
    # "www.$domain" in the SAME server block's server_name line (see
    # nginx._server_names), so certbot's nginx plugin matching on "$domain"
    # already wires up www too - no need to ask for it separately. Asking for
    # a redirect-domain alias here is what broke: it has no server block of
    # its own (that is SPanel's own job - see _append_certbot_redirect_vhosts,
    # which reuses this same certificate file once the panel re-renders the
    # vhost), so certbot's nginx plugin fell back to the first server block
    # it could find - the default_server tools vhost - and cloned it under
    # the alias's name, serving the wrong certificate and exposing whatever
    # that vhost has (phpMyAdmin) under an unrelated domain. Reproduced live,
    # twice: once for a redirect domain with no DNS here at all, and again
    # for one whose DNS was fixed and made it into the certificate - the
    # missing server block, not a missing SAN, was the actual cause both times.
    install_args=(install --nginx --cert-name "$domain" --non-interactive --redirect --expand -d "$domain")
    rc=0
    certbot "${install_args[@]}" || rc=$?
    # The panel can be opened on this domain now, so it needs the certificate.
    install_sni_renewal_hook
    sync_panel_sni_certificates >/dev/null
    exit "$rc"
    ;;

  certbot-renew)
    # certbot spreads scheduled renewals over up to eight minutes so every
    # server on earth does not call Let's Encrypt at midnight. That is right for
    # the timer and wrong here: somebody pressed a button and is watching.
    exec certbot renew --quiet --no-random-sleep-on-renew
    ;;
  certbot-renew-soon)
    [[ $# -le 1 ]] || deny "usage: certbot-renew-soon [days]"
    renew_ssl_soon "${1:-10}"
    ;;
  certbot-auto-renew-install)
    write_ssl_auto_renew_timer
    echo "SSL auto-renew timer installed"
    ;;
  manual-ssl-install)
    [[ $# -eq 1 ]] || deny "usage: manual-ssl-install <domain>"
    install_manual_ssl "$1"
    sync_panel_sni_certificates >/dev/null
    ;;
  manual-ssl-remove)
    [[ $# -eq 1 ]] || deny "usage: manual-ssl-remove <domain>"
    remove_manual_ssl "$1"
    sync_panel_sni_certificates >/dev/null
    ;;
  certbot-delete)
    [[ $# -eq 1 ]] || deny "usage: certbot-delete <domain>"
    delete_ssl_cert "$1"
    ;;
  certbot-dns-cloudflare-install)
    [[ $# -eq 0 ]] || deny "usage: certbot-dns-cloudflare-install"
    install_certbot_dns_cloudflare
    ;;
  cloudflare-ssl-issue)
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: cloudflare-ssl-issue <zone> [email]"
    cloudflare_ssl_issue "$1" "${2:-}"
    ;;
  ssl-cert-info)
    [[ $# -eq 1 ]] || deny "usage: ssl-cert-info <cert-name>"
    ssl_cert_info "$1"
    ;;

  # ---- firewall (iptables + ipset) ---------------------------------------
  # The ufw-* / nginx-blocklist-* names are kept as aliases so an API process
  # that has not been restarted yet keeps working during an update.
  firewall-status|ufw-status)
    firewall_status
    ;;
  firewall-list|ufw-list)
    firewall_list_json
    ;;
  firewall-enable|ufw-enable)
    # Saved as on only when it can be applied: without ipset the setting said
    # on while nothing filtered.
    firewall_require_tools
    firewall_set_state enabled
    firewall_apply
    ;;
  firewall-disable|ufw-disable)
    firewall_set_state disabled
    firewall_apply
    ;;
  firewall-reload|ufw-reload)
    firewall_apply
    ;;
  firewall-apply)
    firewall_apply
    ;;
  firewall-flush)
    firewall_flush
    ;;
  firewall-migrate)
    firewall_migrate
    ;;
  firewall-repair)
    firewall_repair
    ;;
  firewall-allow-port|ufw-allow-port)
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: firewall-allow-port <port> [proto]"
    firewall_add_rule allow "" "$1" "${2:-tcp}"
    ;;
  firewall-panel-allow-port|ufw-panel-allow-port)
    [[ $# -eq 1 ]] || deny "usage: firewall-panel-allow-port <port>"
    allow_panel_port "$1"
    echo "Panel port ${1} is open"
    ;;
  firewall-allow-ip|ufw-allow-ip)
    [[ $# -ge 1 && $# -le 3 ]] || deny "usage: firewall-allow-ip <ip> [port] [proto]"
    firewall_add_rule allow "$1" "${2:-}" "${3:-tcp}"
    ;;
  firewall-deny-ip|ufw-deny-ip)
    [[ $# -ge 1 && $# -le 3 ]] || deny "usage: firewall-deny-ip <ip> [port] [proto]"
    firewall_add_rule deny "$1" "${2:-}" "${3:-tcp}"
    ;;
  firewall-delete|ufw-delete)
    [[ $# -eq 1 && "$1" =~ ^[0-9]+$ ]] || deny "usage: firewall-delete <rule-id>"
    firewall_delete_rule "$1"
    ;;
  firewall-blocklist-status|nginx-blocklist-status|ufw-blocklist-status)
    firewall_blocklist_status
    ;;
  firewall-blocklist-timer-install|nginx-blocklist-timer-install|ufw-blocklist-timer-install)
    firewall_blocklist_write_timer
    echo "IP blocklist timer installed"
    ;;
  firewall-blocklist-add|nginx-blocklist-add|ufw-blocklist-add)
    [[ $# -eq 1 ]] || deny "usage: firewall-blocklist-add <url>"
    firewall_blocklist_add_url "$1"
    ;;
  firewall-blocklist-delete|nginx-blocklist-delete|ufw-blocklist-delete)
    [[ $# -eq 1 ]] || deny "usage: firewall-blocklist-delete <url>"
    firewall_blocklist_delete_url "$1"
    ;;
  firewall-blocklist-run|nginx-blocklist-run|ufw-blocklist-run)
    [[ $# -eq 0 ]] || deny "usage: firewall-blocklist-run"
    firewall_blocklist_run
    ;;

  # ---- filesystem -------------------------------------------------------
  chown-www)
    [[ $# -eq 1 ]] || deny "usage: chown-www <path>"
    target=$(require_managed_path "$1")
    chown -R www-data:www-data "$target"
    find "$target" -type d -exec chmod 755 {} +
    find "$target" -type d -exec chmod a-s {} + 2>/dev/null || true
    find "$target" -type d -exec chmod -t {} + 2>/dev/null || true
    find "$target" -type f -exec chmod 644 {} +
    protect_site_secret_tree "$target"
    ;;

  fix-permissions)
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: fix-permissions <path> [site-user]"
    target=$(require_managed_path "$1" "${2:-}")
    if [[ $# -eq 2 ]]; then
      fix_site_tree "$target" "$2"
      exit 0
    fi
    chown -R www-data:www-data "$target"
    if command -v setfacl >/dev/null 2>&1; then
      setfacl -Rb "$target" 2>/dev/null || true
      find "$target" -type d -exec setfacl -k {} + 2>/dev/null || true
    fi
    find "$target" -type d -exec chmod 755 {} +
    find "$target" -type d -exec chmod a-s {} + 2>/dev/null || true
    find "$target" -type d -exec chmod -t {} + 2>/dev/null || true
    find "$target" -type f -exec chmod 644 {} +
    protect_site_secret_tree "$target"
    ;;

  site-path-fix)
    # After a file manager operation (save, rename, move, copy, extract, new
    # file or folder): the owner and group are put right, the modes are not
    # touched. What is new was created with 644/755 already.
    [[ $# -eq 2 ]] || deny "usage: site-path-fix <path> <site-user>"
    target=$(require_managed_path "$1" "$2")
    own_site_tree "$target" "$2"
    ;;

  site-chmod)
    # Apply an explicit mode to one entry inside a managed site tree.
    # Runs as root on purpose: the site user is not a member of the site group,
    # so a chmod performed as that user has its setgid bit cleared by the
    # kernel, which would silently break group inheritance on site folders.
    [[ $# -eq 4 ]] || deny "usage: site-chmod <site-user> <site-root> <absolute-path> <mode>"
    user="$1"; root_arg="$2"; path_arg="$3"; mode_arg="$4"
    require_linux_user "$user"
    # Five digits is the fully explicit form the panel sends, so chmod applies
    # the special digit on directories instead of preserving the existing one.
    [[ "$mode_arg" =~ ^[0-7]{3,5}$ ]] || deny "invalid mode: $mode_arg"
    target=$(require_bound_managed_path "$user" "$root_arg" "$path_arg")
    [[ -L "$target" ]] && deny "refusing to chmod a symlink: $target"
    [[ -e "$target" ]] || deny "path not found: $target"
    [[ "$(stat -c '%U' "$target")" == "$user" ]] || deny "path is not owned by $user: $target"
    chmod "$mode_arg" -- "$target"
    ;;

  site-document-root-ensure)
    [[ $# -eq 3 ]] || deny "usage: site-document-root-ensure <site-user> <site-root> <relative-path>"
    user="$1"; root_arg="$2"; rel_arg="$3"
    ensure_sites_group
    require_linux_user "$user"
    root_target=$(require_managed_path "$root_arg" "$user")
    [[ "$rel_arg" =~ ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$ ]] || deny "unsafe relative path: $rel_arg"
    case "$rel_arg" in
      ""|"/"|/*|*$'\n'*|"."|".."|"./"*|"../"*|*"/."|*"/.."|*"/./"*|*"/../"*) deny "unsafe relative path: $rel_arg" ;;
    esac
    target=$(require_safe_path "$root_target" "$root_target/$rel_arg")
    mkdir -p -- "$target"
    harden_site_dir_path "$root_target" "$target" "$user"
    ;;

  site-file-write)
    [[ $# -eq 3 || $# -eq 4 ]] || deny "usage: site-file-write <site-user> <site-root> <relative-path> [0644|0640]"
    user="$1"; root_arg="$2"; rel_arg="$3"; mode_arg="${4:-0644}"
    require_linux_user "$user"
    [[ "$mode_arg" == "0644" || "$mode_arg" == "0640" ]] || deny "invalid file mode: $mode_arg"
    root_target=$(require_managed_path "$root_arg" "$user")
    case "$rel_arg" in
      ""|"/"|/*|*$'\n'*|".."|"../"*|*"/.."|*"/../"*) deny "unsafe relative path: $rel_arg" ;;
    esac
    target=$(require_safe_path "$root_target" "$root_target/$rel_arg")
    [[ -d "$target" ]] && deny "cannot write a directory: $target"
    [[ -L "$target" ]] && deny "refusing to write through a symlink: $target"
    parent=$(dirname -- "$target")
    runuser -u "$user" -- mkdir -p -- "$parent"
    harden_site_dir_path "$root_target" "$parent" "$user"
    existing_mode=""
    if [[ -e "$target" ]]; then
      existing_mode=$(stat -c '%a' -- "$target")
    fi
    base=$(basename -- "$target")
    tmp="$parent/.${base}.spanel-write-$$"
    rm -f -- "$tmp"
    cat >"$tmp"
    chown "$user:$SPANEL_SITES_GROUP" "$tmp"
    if [[ -n "$existing_mode" && "$mode_arg" == "0644" ]]; then
      # Saving a file keeps the mode it had: a 755 script stays executable, a
      # 600 secret stays private. An explicit 0640 (wp-config.php) still wins.
      chmod "$existing_mode" "$tmp"
      chmod u-s,g-s,-t "$tmp"
    else
      chmod "$mode_arg" "$tmp"
    fi
    mv -f -- "$tmp" "$target"
    ;;

  site-file-install)
    [[ $# -eq 4 ]] || deny "usage: site-file-install <site-user> <site-root> <relative-path> <staged-path>"
    user="$1"; root_arg="$2"; rel_arg="$3"; staged_arg="$4"
    require_linux_user "$user"
    root_target=$(require_managed_path "$root_arg" "$user")
    case "$rel_arg" in
      ""|"/"|/*|*$'\n'*|".."|"../"*|*"/.."|*"/../"*) deny "unsafe relative path: $rel_arg" ;;
    esac
    target=$(require_safe_path "$root_target" "$root_target/$rel_arg")
    [[ ! -L "$target" ]] || deny "refusing to write through a symlink: $target"
    [[ "$staged_arg" == /tmp/spanel-upload-* ]] || deny "invalid staged upload path"
    [[ ! -L "$staged_arg" ]] || deny "staged upload cannot be a symlink"
    staged=$(readlink -e -- "$staged_arg") || deny "staged upload not found"
    [[ "$staged" == /tmp/spanel-upload-* && -f "$staged" ]] || deny "invalid staged upload"
    [[ "$(stat -c '%U' -- "$staged")" == "spanel" ]] || deny "staged upload must be owned by spanel"
    parent=$(dirname -- "$target")
    runuser -u "$user" -- mkdir -p -- "$parent"
    harden_site_dir_path "$root_target" "$parent" "$user"
    base=$(basename -- "$target")
    tmp="$parent/.${base}.spanel-install-$$"
    rm -f -- "$tmp"
    # Move, do not copy. The staging area and the site tree are on the same
    # filesystem, so this is a rename - the bytes never move. `install` copied
    # them, which meant a 100 MB upload wrote 100 MB here on top of the 100 MB
    # already written to staging, for no gain. If the two ever do land on
    # different filesystems, mv falls back to a copy and this is merely as slow
    # as it used to be.
    mv -f -- "$staged" "$tmp"
    chown "$user":"$SPANEL_SITES_GROUP" -- "$tmp"
    chmod 0644 -- "$tmp"
    mv -f -- "$tmp" "$target"
    # What site-path-fix would have done, without a second sudo and a second
    # helper start-up. Those cost about a fifth of a second each on a busy VPS,
    # which is most of the time a small upload takes.
    fix_site_tree "$target" "$user"
    # And what fastcgi-cache-clear would have done, for the same reason.
    #
    # Worth knowing: this empties the cache for every site on the machine, not
    # just the one that received the file. That is how it has always behaved;
    # folding it in here does not make it worse, but it is not free either.
    install -d -o www-data -g www-data -m 0755 /var/cache/nginx/spanel-fastcgi
    find /var/cache/nginx/spanel-fastcgi -mindepth 1 -delete
    ;;

  site-populate)
    # Replace a managed site's tree from a panel-staged copy, as root, because
    # the site directory belongs to its Linux user and the panel process
    # (spanel) cannot write into it. Used by the DirectAdmin importer and the
    # full-user restore. The staged tree lives under the panel-owned import
    # staging area; symlinks and device nodes are dropped so a crafted backup
    # cannot smuggle one into the served tree.
    [[ $# -eq 3 ]] || deny "usage: site-populate <site-user> <site-root> <staged-source-dir>"
    user="$1"; root_arg="$2"; src_arg="$3"
    require_linux_user "$user"
    root_target=$(require_managed_path "$root_arg" "$user")
    case "$src_arg" in
      /var/lib/spanel/import-stage/*|/var/lib/spanel/da-import/*) : ;;
      *) deny "staged source must be under /var/lib/spanel/import-stage" ;;
    esac
    [[ ! -L "$src_arg" ]] || deny "staged source cannot be a symlink"
    src=$(readlink -e -- "$src_arg") || deny "staged source not found"
    case "$src/" in
      /var/lib/spanel/import-stage/*/|/var/lib/spanel/da-import/*/) : ;;
      *) deny "staged source escaped the import staging area" ;;
    esac
    [[ -d "$src" ]] || deny "staged source is not a directory"
    [[ "$(stat -c '%U' -- "$src")" == "spanel" ]] || deny "staged source must be owned by spanel"
    runuser -u "$user" -- mkdir -p -- "$root_target"
    find "$root_target" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
    cp -a --no-preserve=ownership -- "$src/." "$root_target/"
    find "$root_target" \( -type l -o -type b -o -type c -o -type p -o -type s \) -delete 2>/dev/null || true
    mkdir -p -- "$root_target/public_html"
    fix_site_tree "$root_target" "$user"
    ;;

  site-archive-extract)
    [[ $# -eq 7 ]] || deny "usage: site-archive-extract <site-user> <site-root> <archive-path> <destination-path> <zip|tar.gz> <max-items> <max-bytes>"
    user="$1"; root_arg="$2"; archive_rel="$3"; destination_rel="$4"; archive_kind="$5"
    max_items="$6"; max_bytes="$7"
    require_linux_user "$user"
    [[ "$archive_kind" == "zip" || "$archive_kind" == "tar.gz" ]] || deny "unsupported archive type"
    [[ "$max_items" =~ ^[0-9]+$ && "$max_bytes" =~ ^[0-9]+$ ]] || deny "invalid archive limits"
    root_target=$(require_managed_path "$root_arg" "$user")
    archive_target=$(require_safe_path "$root_target" "$root_target/$archive_rel")
    destination_target=$(require_safe_path "$root_target" "$root_target/$destination_rel")
    [[ -f "$archive_target" && ! -L "$archive_target" ]] || deny "archive not found"
    [[ -d "$destination_target" && ! -L "$destination_target" ]] || deny "archive destination not found"
    tmp_archive=$(mktemp "/tmp/spanel-extract-XXXXXX")
    trap 'rm -f -- "$tmp_archive"' EXIT
    install -o "$user" -g "$user" -m 0600 -- "$archive_target" "$tmp_archive"
    runuser -u "$user" -- python3 - "$tmp_archive" "$archive_kind" "$destination_target" "$max_items" "$max_bytes" "$archive_target" <<'PY'
import os
import shutil
import stat
import sys
import tarfile
import zipfile

archive_path, archive_kind, destination = sys.argv[1:4]
max_items, max_bytes = int(sys.argv[4]), int(sys.argv[5])
source_archive = os.path.realpath(sys.argv[6])
destination = os.path.realpath(destination)


def safe_target(name):
    """Normalize backslash paths and resolve to a safe absolute path."""
    if "\x00" in name:
        raise ValueError("archive contains an unsafe path")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        raise ValueError("archive contains an absolute path")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("archive contains an unsafe path")
    target = os.path.abspath(os.path.join(destination, *parts))
    resolved = os.path.realpath(target)
    if os.path.commonpath((destination, resolved)) != destination:
        raise ValueError("archive path escapes destination")
    return target, resolved


def zip_implied_dirs(infos):
    implied = set()
    for info in infos:
        parts = [part for part in info.filename.replace("\\", "/").split("/") if part not in ("", ".")]
        for index in range(1, len(parts)):
            implied.add("/".join(parts[:index]))
    return implied


def _is_dir_entry(info, implied_dirs):
    """Return True if a ZipInfo represents a directory."""
    normalized = info.filename.replace("\\", "/")
    if info.is_dir() or normalized.endswith("/"):
        return True
    mode = (info.external_attr >> 16) & 0o170000
    if stat.S_ISDIR(mode) and info.file_size == 0:
        return True
    if info.file_size == 0 and normalized.rstrip("/") in implied_dirs:
        return True
    return False


def is_source_archive(resolved):
    return resolved == source_archive


def ensure_regular_target(target):
    if os.path.islink(target):
        raise ValueError("refusing to overwrite a symlink")
    if os.path.isdir(target):
        raise ValueError("archive file conflicts with an existing directory")


def ensure_directory_target(target):
    if os.path.islink(target):
        raise ValueError("refusing to overwrite a symlink")
    if os.path.exists(target) and not os.path.isdir(target):
        try:
            if os.path.getsize(target) == 0:
                return
        except OSError:
            pass
        raise ValueError("archive directory conflicts with an existing file")


def validate_zip():
    count = 0
    total = 0
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        implied_dirs = zip_implied_dirs(infos)
        for info in infos:
            count += 1
            if max_items and count > max_items:
                raise ValueError("archive has too many files")
            target, resolved = safe_target(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                raise ValueError("archive symlinks are not allowed")
            if is_source_archive(resolved):
                continue
            if _is_dir_entry(info, implied_dirs):
                ensure_directory_target(target)
                continue
            ensure_regular_target(target)
            total += info.file_size
            if max_bytes and total > max_bytes:
                raise ValueError("archive is too large")


def extract_zip():
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        implied_dirs = zip_implied_dirs(infos)
        for info in infos:
            target, resolved = safe_target(info.filename)
            if is_source_archive(resolved):
                continue
            if _is_dir_entry(info, implied_dirs):
                if os.path.exists(target) and not os.path.isdir(target):
                    os.unlink(target)
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                with archive.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
            except RuntimeError as exc:
                raise ValueError("archive entry cannot be extracted") from exc


def validate_tar():
    count = 0
    total = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            count += 1
            if max_items and count > max_items:
                raise ValueError("archive has too many files")
            target, resolved = safe_target(member.name)
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError("archive links and devices are not allowed")
            if is_source_archive(resolved):
                continue
            if not member.isdir() and not member.isfile():
                raise ValueError("archive contains unsupported entries")
            if member.isdir():
                ensure_directory_target(target)
                continue
            ensure_regular_target(target)
            total += member.size
            if max_bytes and total > max_bytes:
                raise ValueError("archive is too large")


def extract_tar():
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            target, resolved = safe_target(member.name)
            if is_source_archive(resolved):
                continue
            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("archive entry cannot be extracted")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with source, open(target, "wb") as dst:
                shutil.copyfileobj(source, dst, length=1024 * 1024)


if archive_kind == "zip":
    validate_zip()
    extract_zip()
else:
    validate_tar()
    extract_tar()
PY
    # The archive may contain an entry with its own filename. Restore the
    # original source archive after extraction so it cannot overwrite itself.
    install -o "$user" -g "$SPANEL_SITES_GROUP" -m 0644 -- "$tmp_archive" "$archive_target"
    # The destination is usually a folder that already holds the site; the
    # extracted entries are new (644/755), the rest keeps its modes.
    own_site_tree "$destination_target" "$user"
    rm -f -- "$tmp_archive"
    trap - EXIT
    ;;

  panel-user-ensure)
    [[ $# -eq 1 ]] || deny "usage: panel-user-ensure <panel-user>"
    ensure_panel_user_home "$1"
    ;;

  user-limits-apply)
    # Apply Linux Cgroups v2 (systemd user-<UID>.slice & spanel-user-<user>.slice),
    # PHP-FPM pool limits, and optional Linux filesystem disk quota for a tenant account.
    [[ $# -ge 9 && $# -le 11 ]] || deny "usage: user-limits-apply <user> <cpu_pct> <mem_high_mb> <mem_max_mb> <tasks_max> <io_read_mbps> <io_write_mbps> <iops_read> <iops_write> [storage_limit_mb] [inode_limit]"
    u_name="$1"
    cpu_pct="$2"; mem_high="$3"; mem_max="$4"; tasks_max="$5"
    io_r="$6"; io_w="$7"; iops_r="$8"; iops_w="$9"
    storage_mb="${10:-0}"
    inode_limit="${11:-0}"
    require_linux_user "$u_name"
    for num_arg in "$cpu_pct" "$mem_high" "$mem_max" "$tasks_max" "$io_r" "$io_w" "$iops_r" "$iops_w" "$storage_mb" "$inode_limit"; do
      [[ "$num_arg" =~ ^[0-9]{1,9}$ ]] || deny "invalid numeric resource limit: $num_arg"
    done
    ensure_panel_user_home "$u_name"
    u_uid="$(id -u "$u_name" 2>/dev/null || true)"
    [[ "$u_uid" =~ ^[0-9]+$ && "$u_uid" -ge 1000 ]] || deny "could not resolve non-system UID for $u_name"

    slice_unit="user-${u_uid}.slice"
    slice_dir="/etc/systemd/system/${slice_unit}.d"
    install -d -o root -g root -m 0755 "$slice_dir"
    install -d -o root -g spanel -m 0750 /etc/spanel /etc/spanel/limits
    cat >"/etc/spanel/limits/${u_name}.env" <<EOF
CPU_PCT=${cpu_pct}
MEM_HIGH_MB=${mem_high}
MEM_MAX_MB=${mem_max}
TASKS_MAX=${tasks_max}
IO_READ_MBPS=${io_r}
IO_WRITE_MBPS=${io_w}
IOPS_READ=${iops_r}
IOPS_WRITE=${iops_w}
STORAGE_MB=${storage_mb}
INODE_LIMIT=${inode_limit}
EOF
    chown root:spanel "/etc/spanel/limits/${u_name}.env"
    chmod 0640 "/etc/spanel/limits/${u_name}.env"

    base_dev="$(tenant_io_base_device)"

    {
      echo "# Generated by SPanel Resource Limits for user ${u_name} (UID ${u_uid})"
      echo "[Slice]"
      echo "CPUAccounting=yes"
      echo "MemoryAccounting=yes"
      echo "TasksAccounting=yes"
      echo "IOAccounting=yes"
      if (( cpu_pct > 0 )); then
        echo "CPUQuota=${cpu_pct}%"
      else
        echo "CPUQuota="
      fi
      if (( mem_high > 0 )); then
        echo "MemoryHigh=${mem_high}M"
      else
        echo "MemoryHigh=infinity"
      fi
      if (( mem_max > 0 )); then
        echo "MemoryMax=${mem_max}M"
      else
        echo "MemoryMax=infinity"
      fi
      if (( tasks_max > 0 )); then
        echo "TasksMax=${tasks_max}"
      else
        echo "TasksMax=infinity"
      fi
      if [[ -n "$base_dev" ]]; then
        (( io_r > 0 )) && echo "IOReadBandwidthMax=${base_dev} ${io_r}M"
        (( io_w > 0 )) && echo "IOWriteBandwidthMax=${base_dev} ${io_w}M"
        (( iops_r > 0 )) && echo "IOReadIOPSMax=${base_dev} ${iops_r}"
        (( iops_w > 0 )) && echo "IOWriteIOPSMax=${base_dev} ${iops_w}"
      fi
    } >"${slice_dir}/50-spanel-limits.conf"
    chmod 0644 "${slice_dir}/50-spanel-limits.conf"

    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl start "$slice_unit" >/dev/null 2>&1 || true
    # Apply live to active slice if running
    systemctl set-property "$slice_unit" \
      CPUAccounting=yes MemoryAccounting=yes TasksAccounting=yes IOAccounting=yes \
      CPUQuota="$( (( cpu_pct > 0 )) && printf '%s%%' "$cpu_pct" || printf '' )" \
      MemoryHigh="$( (( mem_high > 0 )) && printf '%sM' "$mem_high" || printf 'infinity' )" \
      MemoryMax="$( (( mem_max > 0 )) && printf '%sM' "$mem_max" || printf 'infinity' )" \
      TasksMax="$( (( tasks_max > 0 )) && printf '%s' "$tasks_max" || printf 'infinity' )" >/dev/null 2>&1 || true

    # Tune PHP-FPM pools owned by this user to respect persisted limits. The
    # account's own masters already run inside the slice limited above; pools
    # still on a distro master get the classify hook below.
    user_pool_files=""
    for pool_file in "$SPANEL_PHP_FPM_ROOT"/*/"${u_name}"/pool.d/spanel-*.conf \
      /etc/php/*/fpm/pool.d/spanel-"${u_name}"-*.conf /etc/php/*/fpm/pool.d/spanel-"${u_name}".conf; do
      [[ -f "$pool_file" ]] || continue
      calculate_php_fpm_pool_tuning "$pool_file"
      apply_saved_user_limits_to_pool_file "$pool_file" "$u_name"
      if php_pool_file_is_legacy "$pool_file"; then
        ensure_php_fpm_cgroup_dropin "$(php_pool_file_version "$pool_file")"
      fi
      user_pool_files+="${pool_file}"$'\n'
    done
    reload_php_pool_owners <<<"$user_pool_files"

    # Disk space and inode limits through kernel quota, when quota-enable has
    # turned it on for the filesystem holding /home (see quota-status).
    apply_kernel_quota "$u_name" "$storage_mb" "$inode_limit"

    # The account's own PHP-FPM masters already run in the slice. Whatever of
    # this user still runs outside it (cron jobs, a pool left on a distro
    # master) is moved into user-<UID>.slice/spanel.scope.
    classify_all_tenant_workers
    echo "Applied Cgroups v2 & PHP-FPM resource limits for ${u_name} (UID ${u_uid})"
    ;;

  quota-status)
    [[ $# -eq 0 ]] || deny "usage: quota-status"
    quota_status
    ;;

  quota-enable)
    # Installs the quota tools and adds user quota to /etc/fstab for the
    # filesystem holding /home; once that is mounted, builds the quota file
    # and switches it on. Never remounts or reboots.
    [[ $# -eq 0 ]] || deny "usage: quota-enable"
    quota_enable
    ;;

  cgroup-classify-workers)
    # Move tenant processes running outside their account's slice (cron jobs,
    # pools on a distro PHP-FPM master) into user-<UID>.slice/spanel.scope, so
    # an attacked site cannot escape its limits. Processes already inside the
    # slice - the account's own PHP-FPM master and its workers - stay put.
    [[ $# -eq 0 ]] || deny "usage: cgroup-classify-workers"
    classify_all_tenant_workers
    ;;

  mysql-governor-ensure)
    # Configure MariaDB InnoDB Buffer Pool Anti-Pollution & Per-User CPU/Row Statistics (userstat=1)
    [[ $# -eq 0 ]] || deny "usage: mysql-governor-ensure"
    if [[ -d /etc/mysql/mariadb.conf.d ]]; then
      cat >/etc/mysql/mariadb.conf.d/99-spanel-governor.cnf <<'EOF'
[mysqld]
# Managed by SPanel MySQL Governor & Anti-Scraping Shield
userstat = 1
max_statement_time = 15
innodb_old_blocks_pct = 37
innodb_old_blocks_time = 1000
tmp_table_size = 32M
max_heap_table_size = 32M
EOF
      chmod 0644 /etc/mysql/mariadb.conf.d/99-spanel-governor.cnf
    fi
    mysql -e "SET GLOBAL userstat = 1; SET GLOBAL innodb_old_blocks_pct = 37; SET GLOBAL innodb_old_blocks_time = 1000;" >/dev/null 2>&1 || true
    ;;

  mysql-sizes-collect)
    # Fast physical size collection of all MariaDB/MySQL databases in /var/lib/mysql
    [[ $# -eq 0 ]] || deny "usage: mysql-sizes-collect"
    if [[ -d /var/lib/mysql ]]; then
      for db_dir in /var/lib/mysql/*/; do
        [[ -d "$db_dir" ]] || continue
        db_base="$(basename "$db_dir")"
        case "$db_base" in
          mysql|performance_schema|information_schema|sys|"#innodb_redo"|"#innodb_temp") continue ;;
        esac
        sz="$(du -sb -- "$db_dir" 2>/dev/null | awk '{print $1}')"
        [[ "$sz" =~ ^[0-9]+$ ]] && printf '%s\t%s\n' "$sz" "$db_base"
      done
    fi
    ;;

  phpmyadmin-remote-ca)
    # CA certificate (PEM on stdin) for phpMyAdmin server <index>, a remote MySQL host.
    [[ $# -eq 1 && "$1" =~ ^[0-9]{1,2}$ ]] || deny "usage: phpmyadmin-remote-ca <server-index>"
    (( 10#$1 >= 2 && 10#$1 <= 99 )) || deny "phpmyadmin-remote-ca: server index out of range"
    write_phpmyadmin_remote_ca "$((10#$1))"
    ;;

  phpmyadmin-remote-servers)
    # The full list of remote MySQL hosts phpMyAdmin offers, INDEX,HOST,PORT,TLS,CA each.
    [[ $# -le 98 ]] || deny "usage: phpmyadmin-remote-servers [INDEX,HOST,PORT,TLS,CA ...]"
    write_phpmyadmin_remote_servers "$@"
    ;;

  panel-user-password)
    [[ $# -eq 1 ]] || deny "usage: panel-user-password <panel-user>"
    set_panel_user_password "$1"
    ;;

  panel-user-delete)
    [[ $# -eq 1 ]] || deny "usage: panel-user-delete <panel-user>"
    delete_panel_user_runtime "$1"
    ;;

  sftp-account-ensure)
    [[ $# -eq 3 ]] || deny "usage: sftp-account-ensure <panel-user> <sub-account> <site-path>"
    ensure_sftp_account "$1" "$2" "$3"
    ;;

  sftp-account-password)
    [[ $# -eq 1 ]] || deny "usage: sftp-account-password <sub-account>"
    set_sftp_account_password "$1"
    ;;

  sftp-account-lock)
    [[ $# -eq 2 ]] || deny "usage: sftp-account-lock <sub-account> <lock|unlock>"
    set_sftp_account_lock "$1" "$2"
    ;;

  sftp-account-delete)
    [[ $# -eq 1 ]] || deny "usage: sftp-account-delete <sub-account>"
    delete_sftp_account "$1"
    ;;

  site-runtime-ensure)
    [[ $# -eq 3 ]] || deny "usage: site-runtime-ensure <site-user> <path> <php-version|none>"
    user="$1"; path="$2"; php_version="$3"
    require_linux_user "$user"
    target=$(require_managed_path "$path" "$user")
    ensure_panel_user_home "$user"
    if [[ -d "$target/public" && ! -e "$target/public_html" ]]; then
      mv "$target/public" "$target/public_html"
    elif [[ -d "$target/public" && -d "$target/public_html" && -z "$(find "$target/public_html" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
      rmdir "$target/public_html"
      mv "$target/public" "$target/public_html"
    fi
    mkdir -p "$target/public_html"
    harden_site_dir_path "$target" "$target/public_html" "$user"
    # Runs on every new cron job, PHP version change and update: ownership
    # only, never the modes of what is already in the site.
    own_site_tree "$target" "$user"
    ensure_php_pool "$user" "$target" "$php_version"
    ;;

  site-runtime-move)
    [[ $# -eq 4 ]] || deny "usage: site-runtime-move <site-user> <old-path> <new-path> <php-version|none>"
    user="$1"; old_path="$2"; new_path="$3"; php_version="$4"
    require_linux_user "$user"
    old_target=$(require_managed_path "$old_path")
    new_target=$(require_managed_path "$new_path" "$user")
    old_user="${old_target#${HOME_ROOT}/}"
    old_user="${old_user%%/*}"
    ensure_panel_user_home "$user"
    if [[ "$old_target" != "$new_target" ]]; then
      delete_site_php_pools "$old_user" "$old_target"
      mkdir -p "$(dirname "$new_target")"
      if [[ -d "$old_target" && -d "$new_target" ]]; then
        cp -a "$old_target"/. "$new_target"/ 2>/dev/null || true
        rm -rf "$old_target"
      elif [[ -e "$old_target" && ! -e "$new_target" ]]; then
        mv "$old_target" "$new_target"
      elif [[ -e "$old_target" ]]; then
        rm -rf "$new_target"
        mv "$old_target" "$new_target"
      else
        mkdir -p "$new_target"
      fi
    fi
    if [[ -d "$new_target/public" && ! -e "$new_target/public_html" ]]; then
      mv "$new_target/public" "$new_target/public_html"
    fi
    mkdir -p "$new_target/public_html"
    harden_site_dir_path "$new_target" "$new_target/public_html" "$user"
    own_site_tree "$new_target" "$user"
    ensure_php_pool "$user" "$new_target" "$php_version"
    ;;

  site-runtime-delete)
    [[ $# -eq 2 ]] || deny "usage: site-runtime-delete <site-user> <path>"
    user="$1"; path="$2"
    require_linux_user "$user"
    target=$(require_managed_path "$path" "$user")
    delete_site_php_pools "$user" "$target"
    exec rm -rf "$target"
    ;;

  rm-site)
    [[ $# -eq 3 ]] || deny "usage: rm-site <site-user> <site-root> <path>"
    user="$1"; root="$2"; path="$3"
    target=$(require_bound_managed_path "$user" "$root" "$path")
    delete_no_follow "$user" "$root" "$target"
    ;;

  mkdir-site)
    [[ $# -eq 1 ]] || deny "usage: mkdir-site <path>"
    target=$(require_managed_path "$1")
    install -d -o www-data -g www-data -m 0750 "$target"
    install -d -o www-data -g www-data -m 0750 "$target/public_html"
    ;;

  site-log-read)
    [[ $# -eq 3 ]] || deny "usage: site-log-read <domain> <access|error> <lines>"
    read_site_log "$1" "$2" "$3"
    ;;

  site-logs-read-many)
    [[ $# -ge 3 ]] || deny "usage: site-logs-read-many <access|error> <lines> <domain>..."
    read_site_logs_many "$@"
    ;;

  site-log-clear)
    [[ $# -eq 2 ]] || deny "usage: site-log-clear <domain> <access|error>"
    domain="$1"; kind="$2"
    require_domain "$domain"
    [[ "$kind" == "access" || "$kind" == "error" ]] || deny "invalid log kind: $kind"
    path="/var/log/nginx/${domain}.${kind}.log"
    resolved=$(readlink -m "$path") || deny "cannot resolve log path"
    case "$resolved" in
      /var/log/nginx/*) ;;
      *) deny "log path outside /var/log/nginx: $resolved" ;;
    esac
    if [[ -f "$resolved" ]]; then
      : > "$resolved"
    fi
    ;;

  # ---- WP-CLI as www-data ----------------------------------------------
  wp)
    # Narrowed to `--info`, which is all this verb is still for: the installer
    # and the updater call it to prove the sudo trampoline works end to end
    # (install.sh:678, update.sh:1252).
    #
    # It used to take arbitrary WP-CLI argv and run it as www-data. That is the
    # widest identity on the box - usermod -aG puts www-data in EVERY site's
    # group (:3596) plus spanel-sites (:3513), and site secrets are 0640
    # group-readable, so one `wp eval` there read every tenant's wp-config.php.
    # It was reached whenever a Website row had no linux_user; services/
    # wordpress.py now derives one instead of falling back here.
    #
    # If a real need for WP-CLI as www-data ever returns, it needs its own verb
    # with a validated subcommand allowlist - not this one.
    [[ $# -eq 1 && "${1:-}" == "--info" ]] || deny "usage: wp --info (use wp-site <user> ... to act on a website)"
    exec runuser -u www-data -- env HOME=/var/www WP_CLI_PHP_ARGS='-d pcre.jit=0' php -d pcre.jit=0 /usr/local/bin/wp --info
    ;;

  wp-site)
    # WP-CLI bootstraps the target install's own wp-config.php and active
    # plugins, so this is tenant-authored PHP. It needs the same confinement
    # the terminal's wp branch applies (:5545) - without it this verb was the
    # third place the panel started an unconfined interpreter as a site user,
    # after the terminal (fixed) and cron (fixed in services/cron.py). The
    # value is built by site_open_basedir so the two branches cannot drift.
    #
    # WP-CLI has to run under the same PHP the site runs, not whatever the
    # `php` alternative happens to point at. On a server with several PHP
    # versions installed those differ, and the difference is not cosmetic: a
    # site on 8.4 was updated by the 8.3 CLI, which had no mysqli, so every
    # `wp core update` failed with "Your PHP installation appears to be
    # missing the MySQL extension" - reported as a bare 500 in the panel.
    [[ $# -ge 2 ]] || deny "usage: wp-site <site-user> [--php-version=<version>] <args...>"
    user="$1"; shift
    require_linux_user "$user"
    wp_php="php"
    if [[ "${1:-}" == --php-version=* ]]; then
      wp_php_version="${1#--php-version=}"
      require_php_version "$wp_php_version"
      wp_php="php${wp_php_version}"
      command -v "$wp_php" >/dev/null 2>&1 || deny "PHP CLI is not installed: $wp_php"
      shift
    fi
    [[ $# -ge 1 ]] || deny "usage: wp-site <site-user> [--php-version=<version>] <args...>"
    wp_site_basedir="$(site_open_basedir "$user"):/usr/local/bin"
    # Move into the tenant's home first. WP-CLI probes its working directory
    # during bootstrap even when --path is given, and the API unit's cwd is
    # /opt/spanel/backend - outside the basedir - so every call printed a row
    # of open_basedir warnings to stderr, which the panel shows the customer.
    # terminal-exec has always done this (cd "$target"); wp-site never did,
    # and it did not matter until the confinement above made the cwd visible.
    cd "$HOME_ROOT/$user" 2>/dev/null || deny "no home for $user"
    exec runuser -u "$user" -- env HOME="$HOME_ROOT/$user" \
      WP_CLI_PHP_ARGS="-d pcre.jit=0 -d open_basedir=$wp_site_basedir" \
      "$wp_php" -d pcre.jit=0 -d open_basedir="$wp_site_basedir" /usr/local/bin/wp "$@"
    ;;

  # ---- crontab managed via root-owned /etc/cron.d/spanel-user-<user> ---
  cron-list)
    user="${1:-www-data}"
    if [[ "$user" != "www-data" ]]; then
      require_linux_user "$user"
      cron_file="/etc/cron.d/spanel-user-${user}"
      if [[ -f "$cron_file" ]]; then
        grep -vE '^(#|$|SHELL=|PATH=)' "$cron_file" 2>/dev/null || true
        exit 0
      fi
    fi
    exec runuser -u "$user" -- crontab -l 2>/dev/null
    ;;
  cron-write)
    # crontab content is fed via stdin
    user="${1:-www-data}"
    if [[ "$user" != "www-data" ]]; then
      require_linux_user "$user"
      ensure_cron_jail_binary
      user_home="/home/${user}"
      resolved_home="$(realpath -m -- "$user_home" 2>/dev/null || echo "$user_home")"
      cron_file="/etc/cron.d/spanel-user-${user}"
      tmp_cron="$(mktemp /etc/cron.d/.spanel-cron.XXXXXX)"
      chmod 0644 "$tmp_cron"
      chown root:root "$tmp_cron"
      # Same grammar as JAILED_COMMAND_ARG_RE in backend/app/services/cron.py:
      #   ^'[^']*('(\\'|"'")'[^']*)*'$
      # and a % after an even run of backslashes, which cron would cut at.
      q="'"
      jailed_arg_re="^${q}[^${q}]*(${q}(\\\\${q}|\"${q}\")${q}[^${q}]*)*${q}\$"
      unescaped_pct_re='(^|[^\])(\\\\)*%'
      {
        echo "# Managed by SPanel — root-enforced Mount Namespace Jail for ${user}"
        echo "SHELL=/bin/sh"
        echo "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        while IFS= read -r line; do
          [[ -z "${line//[[:space:]]/}" ]] && continue
          [[ "$line" =~ ^[[:space:]]*# ]] && continue
          [[ "$line" =~ ^[[:space:]]*(SHELL|PATH)= ]] && continue
          if [[ "$line" =~ ^([0-9*,/-]+[[:space:]]+[0-9*,/-]+[[:space:]]+[0-9*,/-]+[[:space:]]+[0-9*,/-]+[[:space:]]+[0-9*,/-]+)[[:space:]]+root[[:space:]]+/usr/local/bin/spanel-cron-jail[[:space:]]+(\'?[a-z_][a-z0-9_-]{0,31}\'?)[[:space:]]+(\'[^\']+\'|/[A-Za-z0-9._/-]+)[[:space:]]+--[[:space:]]+/bin/sh[[:space:]]+-c[[:space:]]+(\'.*\')[[:space:]]+(#[[:space:]]*spanel:[a-z0-9.-]{3,253})[[:space:]]*$ ]]; then
            # Copy every capture now: the next regex test resets them.
            sched="${BASH_REMATCH[1]}"
            line_user="${BASH_REMATCH[2]//\'/}"
            cwd_word="${BASH_REMATCH[3]}"
            line_cwd="${cwd_word//\'/}"
            quoted_cmd="${BASH_REMATCH[4]}"
            marker="${BASH_REMATCH[5]}"
            if [[ "$line_user" != "$user" ]]; then
              rm -f "$tmp_cron"
              deny "cron-write: user mismatch in cron line (${line_user} != ${user})"
            fi
            resolved_cwd="$(realpath -m -- "$line_cwd" 2>/dev/null || true)"
            if [[ -z "$resolved_cwd" || ( "$resolved_cwd" != "$resolved_home" && "$resolved_cwd" != "${resolved_home}/"* ) ]]; then
              rm -f "$tmp_cron"
              deny "cron-write: working directory outside ${resolved_home}: ${line_cwd}"
            fi
            # quoted_cmd must be one shell word: single-quoted segments joined
            # only by '\'' or '"'"', so root's shell sees nothing unquoted.
            if [[ ! "$quoted_cmd" =~ $jailed_arg_re ]]; then
              rm -f "$tmp_cron"
              deny "cron-write: unquoted shell tokens outside /bin/sh -c string refused"
            fi
            if [[ "$quoted_cmd" =~ $unescaped_pct_re ]]; then
              rm -f "$tmp_cron"
              deny "cron-write: unescaped % in /bin/sh -c string refused"
            fi
            printf '%s root /usr/local/bin/spanel-cron-jail %s %s -- /bin/sh -c %s %s\n' \
              "$sched" "$line_user" "$cwd_word" "$quoted_cmd" "$marker"
          else
            rm -f "$tmp_cron"
            deny "cron-write: rejected unconfined or malformed cron line for ${user}"
          fi
        done
      } >"$tmp_cron"
      mv -f "$tmp_cron" "$cron_file"
      # Remove any user-writable crontab and deny direct crontab access so tenant cannot strip jail prefix
      crontab -r -u "$user" 2>/dev/null || true
      if ! grep -qxF "$user" /etc/cron.deny 2>/dev/null; then
        printf '%s\n' "$user" >>/etc/cron.deny 2>/dev/null || true
      fi
      exit 0
    fi
    ensure_cron_jail_binary
    exec runuser -u "$user" -- crontab -
    ;;

  # ---- service status (read-only, no privilege change needed but useful)
  service-status)
    [[ $# -eq 1 ]] || deny "usage: service-status <service>"
    is_allowed_service "$1" || deny "service not allowed: $1"
    exec systemctl status "$1" --no-pager
    ;;

  audit-anchor-write)
    [[ $# -eq 3 ]] || deny "usage: audit-anchor-write <last_id> <head_hash> <total_entries>"
    last_id="$1"; head_hash="$2"; total_entries="$3"
    [[ "$last_id" =~ ^[0-9]{1,18}$ ]] || deny "invalid audit last_id"
    [[ "$head_hash" =~ ^[0-9a-f]{64}$ ]] || deny "invalid audit head_hash"
    [[ "$total_entries" =~ ^[0-9]{1,18}$ ]] || deny "invalid audit total_entries"
    install -d -m 0750 -o root -g spanel /etc/spanel
    anchor_file="/etc/spanel/audit-chain-anchor.json"
    anchor_log="/etc/spanel/audit-chain-anchor.log"
    if [[ -f "$anchor_file" ]]; then
      prev_id="$(awk -F'[^0-9]+' '/"last_id"/ {print $2; exit}' "$anchor_file" 2>/dev/null || echo 0)"
      prev_total="$(awk -F'[^0-9]+' '/"total_entries"/ {print $2; exit}' "$anchor_file" 2>/dev/null || echo 0)"
      if [[ "$prev_id" =~ ^[0-9]+$ ]] && (( last_id < prev_id )); then
        deny "audit-anchor-write: refusing non-monotonic last_id (${last_id} < ${prev_id})"
      fi
      if [[ "$prev_total" =~ ^[0-9]+$ ]] && (( total_entries < prev_total )); then
        deny "audit-anchor-write: refusing non-monotonic total_entries (${total_entries} < ${prev_total})"
      fi
    fi
    ts_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    tmp_anchor="$(mktemp /etc/spanel/.audit-anchor.XXXXXX)"
    cat >"$tmp_anchor" <<EOF
{
  "last_id": ${last_id},
  "head_hash": "${head_hash}",
  "total_entries": ${total_entries},
  "updated_at": "${ts_utc}"
}
EOF
    chown root:spanel "$tmp_anchor"
    chmod 0640 "$tmp_anchor"
    mv -f "$tmp_anchor" "$anchor_file"
    printf '%s\tlast_id=%s\ttotal_entries=%s\thead_hash=%s\n' "$ts_utc" "$last_id" "$total_entries" "$head_hash" >>"$anchor_log"
    chown root:spanel "$anchor_log"
    chmod 0640 "$anchor_log"
    ;;

  # ---- terminal command execution as panel Linux user ------------------
  terminal-exec)
    # Execute a whitelisted command as the panel Linux user
    # Args: <site-user> <cwd> [--timeout=<sec>] [--php-version=<version>] <command> [args...]
    [[ $# -ge 3 ]] || deny "usage: terminal-exec <site-user> <cwd> [--timeout=<sec>] [--php-version=<version>] <command> [args...]"
    user="$1"; cwd_arg="$2"; shift 2
    php_version=""
    terminal_timeout=""
    while [[ $# -gt 0 ]]; do
      case "${1:-}" in
        --php-version=*)
          php_version="${1#--php-version=}"
          require_php_version "$php_version"
          shift
          ;;
        --timeout=*)
          terminal_timeout="${1#--timeout=}"
          [[ "$terminal_timeout" =~ ^[0-9]{1,4}$ ]] || deny "invalid terminal timeout: $terminal_timeout"
          (( 10#$terminal_timeout >= 1 && 10#$terminal_timeout <= 1800 )) || deny "terminal timeout out of range"
          shift
          ;;
        *) break ;;
      esac
    done
    [[ $# -ge 1 ]] || deny "usage: terminal-exec <site-user> <cwd> [--timeout=<sec>] [--php-version=<version>] <command> [args...]"
    cmd="$1"; shift
    require_linux_user "$user"
    id -u "$user" >/dev/null 2>&1 || deny "panel Linux user does not exist: $user"
    target=$(require_terminal_cwd "$cwd_arg" "$user")

    install -d -o "$user" -g "$user" -m 0700 "$HOME_ROOT/$user/.composer" "$HOME_ROOT/$user/.npm"
    # Validate cwd exists immediately before cd to avoid TOCTOU
    [[ -d "$target" ]] || deny "working directory does not exist: $target"
    cd "$target" || deny "failed to change to working directory: $target"
    umask 022
    terminal_env=(
      "HOME=$HOME_ROOT/$user"
      "COMPOSER_HOME=$HOME_ROOT/$user/.composer"
      "npm_config_cache=$HOME_ROOT/$user/.npm"
      "PATH=/usr/local/bin:/usr/bin:/bin"
    )
    php_bin="php"
    if [[ -n "$php_version" ]]; then
      php_bin="php${php_version}"
      command -v "$php_bin" >/dev/null 2>&1 || deny "PHP CLI is not installed: $php_bin"
    fi

    # PHP started from the terminal was completely unconfined, while the same
    # site's PHP-FPM pool runs under open_basedir. That gap let one tenant read
    # another's files: the terminal runs as the site user, site files are
    # world-readable by design, and /home/<user> is 0751 - not listable, but
    # traversable if you know the name. `php -r "readfile('/home/other/...')"`
    # was enough. Verified on a live test server before the fix: it printed
    # /etc/passwd.
    #
    # The boundary is the tenant's own home, not one site root: a customer with
    # several sites still has to be able to work across them, and the leak
    # being closed is between customers.
    #
    # /var/lib/php/{sessions,uploads}/<user> match the pool. /tmp and
    # /usr/share/php are what composer and PEAR-era libraries expect. The
    # interpreter must also be able to read the phar it is being asked to run,
    # so the directory of each tool is appended at the call site.
    terminal_open_basedir="$(site_open_basedir "$user")"

    # open_basedir above confines PHP. It does nothing for node, npm, npx,
    # yarn or git, and nothing for `find . -maxdepth 0 -exec sh -c '<cmd>' \;`
    # either - require_terminal_path_args skips every argument matching -*, so
    # -exec walks straight through it and hands the tenant an arbitrary shell.
    # Filtering arguments cannot fix that: the tenant already runs their own
    # code as their own uid through npm lifecycle scripts and git hooks, and no
    # scanner models program text.
    #
    # So confine the filesystem instead of the arguments. In a private mount
    # namespace, /home is replaced by a tmpfs holding exactly one directory -
    # this tenant's own - so every other customer's files are simply not there
    # to read, whatever the tool. Site trees are 0644 and homes 0751 by design
    # (see the header at the top of this file), which is what made the read
    # possible; this removes the path rather than the permission.
    #
    # Outside the namespace /home is untouched, and the namespace dies with the
    # command. /etc/passwd stays readable: it is world-readable system data and
    # tools need it, and it no longer leads anywhere now the homes are gone.
    terminal_jail='
      set -e
      jail_user="$1"
      jail_root="$2"
      jail_cwd="$3"
      shift 3
      jail_home="$jail_root/$jail_user"
      hold="$(mktemp -d)"
      mount --bind "$jail_home" "$hold"
      mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$jail_root"
      mkdir -p "$jail_home"
      mount --move "$hold" "$jail_home"
      rmdir "$hold" 2>/dev/null || true
      # The parent cd-ed here before unshare, and the tmpfs briefly made that
      # directory unreachable by path. Re-enter it so the command starts where
      # the caller asked and pwd agrees with it.
      cd "$jail_cwd"
      exec "$@"
    '

    # Kill the whole process group when the budget runs out. Composer, npm and
    # WP-CLI can wedge on a slow network, and without this the API worker would
    # block on the pipe until the client gives up.
    terminal_runner=(
      unshare --mount --propagation private --
      bash -c "$terminal_jail" spanel-terminal-jail "$user" "$HOME_ROOT" "$target"
      runuser -u "$user" --
    )
    if [[ -n "$terminal_timeout" ]] && command -v timeout >/dev/null 2>&1; then
      terminal_runner=(
        timeout --signal=TERM --kill-after=10 "${terminal_timeout}"
        "${terminal_runner[@]}"
      )
    fi

    # Whitelist of allowed commands for terminal access. Keep this in sync with
    # ALLOWED_COMMANDS in backend/app/services/terminal.py.
    case "$cmd" in
      php)
        exec "${terminal_runner[@]}" env "${terminal_env[@]}" "$php_bin" -d open_basedir="$terminal_open_basedir" "$@"
        ;;
      composer)
        composer_bin="$(command -v composer || true)"
        [[ -n "$composer_bin" ]] || deny "composer not found"
        exec "${terminal_runner[@]}" env "${terminal_env[@]}" "$php_bin" -d open_basedir="$terminal_open_basedir:$(dirname "$composer_bin")" "$composer_bin" "$@"
        ;;
      wp)
        [[ -f /usr/local/bin/wp ]] || deny "wp-cli not found"
        exec "${terminal_runner[@]}" env "${terminal_env[@]}" WP_CLI_PHP_ARGS="-d pcre.jit=0 -d open_basedir=$terminal_open_basedir:/usr/local/bin" "$php_bin" -d pcre.jit=0 -d open_basedir="$terminal_open_basedir:/usr/local/bin" /usr/local/bin/wp "$@"
        ;;
      phpunit)
        phpunit_bin="$(command -v phpunit || true)"
        if [[ -z "$phpunit_bin" && -x "$target/vendor/bin/phpunit" ]]; then
          # Projects normally ship PHPUnit in vendor/bin instead of globally.
          phpunit_bin="$target/vendor/bin/phpunit"
        fi
        [[ -n "$phpunit_bin" ]] || deny "phpunit not found (install it globally or with composer)"
        exec "${terminal_runner[@]}" env "${terminal_env[@]}" "$php_bin" -d open_basedir="$terminal_open_basedir:$(dirname "$phpunit_bin")" "$phpunit_bin" "$@"
        ;;
      node|npm|npx|yarn|git)
        exec "${terminal_runner[@]}" env "${terminal_env[@]}" "$cmd" "$@"
        ;;
      ls|cat|mkdir|rmdir|rm|cp|mv|chmod|chown|grep|find|tar|zip|unzip|diff|head|tail|less|du|df|sed|awk|wc|sort|uniq|stat|file|touch)
        require_terminal_path_args "$user" "$target" "$@"
        exec "${terminal_runner[@]}" env "${terminal_env[@]}" "$cmd" "$@"
        ;;
      pwd|echo|date|whoami|which|clear|id|uname|printenv|basename|dirname|realpath)
        exec "${terminal_runner[@]}" env "${terminal_env[@]}" "$cmd" "$@"
        ;;
      curl|wget)
        require_terminal_download_args "$user" "$target" "$@"
        exec "${terminal_runner[@]}" env "${terminal_env[@]}" "$cmd" "$@"
        ;;
      artisan)
        # Bare `artisan` is a convenience alias for `php artisan`. Laravel keeps
        # it at the project root, one level above public_html.
        if [[ ! -f artisan ]]; then
          deny "artisan not found in $target (Laravel keeps it in the site root; try 'cd ..' first)"
        fi
        exec "${terminal_runner[@]}" env "${terminal_env[@]}" "$php_bin" -d open_basedir="$terminal_open_basedir" artisan "$@"
        ;;
      *)
        echo "Command not allowed: $cmd" >&2
        echo "Allowed commands: php, composer, artisan, wp, phpunit, node, npm, npx, yarn, git," >&2
        echo "  ls, cat, mkdir, rmdir, rm, cp, mv, chmod, chown, touch, grep, find, tar, zip, unzip," >&2
        echo "  diff, head, tail, less, du, df, sed, awk, wc, sort, uniq, stat, file, curl, wget," >&2
        echo "  pwd, echo, date, whoami, which, clear, id, uname, printenv, basename, dirname, realpath" >&2
        exit 126
        ;;
    esac
    ;;

  nginx-upgrade-map-ensure)
    [[ $# -eq 0 ]] || deny "usage: nginx-upgrade-map-ensure"
    ensure_proxy_upgrade_map
    echo "websocket upgrade map present"
    ;;

  # ---- managed application runtimes (node / python / docker) -----------
  site-app-write)
    [[ $# -ge 3 ]] || deny "usage: site-app-write <owner-user> <name> <node|python|docker|compose> [--flags]"
    user="$1"; app_name="$2"; app_runtime="$3"; shift 3
    require_linux_user "$user"
    require_app_name "$app_name"
    is_in "$app_runtime" node python docker compose || deny "invalid runtime: $app_runtime"
    app_port=""; app_memory="512"; app_node_major=""
    app_exec=""; app_arg=""; app_image=""; app_container_port="3000"; app_cpus="1"
    while [[ $# -gt 0 ]]; do
      case "${1:-}" in
        --port=*)           app_port="${1#*=}" ;;
        --memory=*)         app_memory="${1#*=}" ;;
        --node-major=*)     app_node_major="${1#*=}" ;;
        --exec=*)           app_exec="${1#*=}" ;;
        --arg=*)            app_arg="${1#*=}" ;;
        --image=*)          app_image="${1#*=}" ;;
        --container-port=*) app_container_port="${1#*=}" ;;
        --cpus=*)           app_cpus="${1#*=}" ;;
        *) deny "unknown site-app-write option: $1" ;;
      esac
      shift
    done
    require_app_port "$app_port"
    require_app_memory "$app_memory"
    require_app_cpus "$app_cpus"
    app_dir="$(ensure_app_directory "$user" "$app_name")"
    remove_legacy_app_units "$user" "$app_name"
    unit_name="$(app_unit_name "$user" "$app_name")"
    unit_path="/etc/systemd/system/${unit_name}.service"
    if [[ "$app_runtime" == "compose" ]]; then
      command -v docker >/dev/null 2>&1 || deny "Docker is not installed; run docker-install first"
      docker compose version >/dev/null 2>&1 || deny "the docker compose plugin is not installed"
      compose_file="$(app_compose_file "$user" "$app_name")"
      write_app_compose_file "$compose_file"
      ensure_compose_bind_dirs "$compose_file" "$user" "$app_dir"
      write_compose_app_unit "$unit_path" "$app_name" "$user" "$app_dir" "$compose_file" \
        "$(app_container_name "$user" "$app_name")"
      chown root:root "$unit_path"
      chmod 0644 "$unit_path"
      systemctl daemon-reload
      echo "$unit_name"
      exit 0
    fi
    env_file="$(app_env_file "$user" "$app_name")"
    write_app_env_file "$env_file"
    if [[ "$app_runtime" == "node" ]]; then
      require_node_major "$app_node_major"
      [[ "$app_arg" =~ ^[A-Za-z0-9._@/-]{1,120}$ ]] || deny "invalid start argument: $app_arg"
      write_node_app_unit "$unit_path" "$app_name" "$user" "$app_dir" "$env_file" \
        "$app_port" "$app_memory" "$app_node_major" "$app_exec" "$app_arg"
    elif [[ "$app_runtime" == "python" ]]; then
      [[ "$app_arg" =~ ^[A-Za-z0-9._:@/-]{1,120}$ ]] || deny "invalid Python start argument: $app_arg"
      write_python_app_unit "$unit_path" "$app_name" "$user" "$app_dir" "$env_file" \
        "$app_port" "$app_memory" "$app_exec" "$app_arg"
    else
      command -v docker >/dev/null 2>&1 || deny "Docker is not installed; run docker-install first"
      require_docker_image "$app_image"
      require_container_port "$app_container_port"
      container_name="$(app_container_name "$user" "$app_name")"
      write_docker_app_unit "$unit_path" "$app_name" "$user" "$container_name" "$app_dir" "$env_file" \
        "$app_port" "$app_memory" "$app_image" "$app_container_port" "$app_cpus"
    fi
    chown root:root "$unit_path"
    chmod 0644 "$unit_path"
    systemctl daemon-reload
    echo "$unit_name"
    ;;

  site-app-rename)
    # An app's directory is derived from its name, so a rename has to take the
    # customer's files with it or they are orphaned in the old path.
    [[ $# -eq 3 ]] || deny "usage: site-app-rename <owner-user> <old-name> <new-name>"
    user="$1"; app_name="$2"; app_new_name="$3"
    require_linux_user "$user"
    require_app_name "$app_name"
    require_app_name "$app_new_name"
    [[ "$app_name" != "$app_new_name" ]] || exit 0
    old_dir="$(app_directory "$user" "$app_name")"
    new_dir="$(app_directory "$user" "$app_new_name")"
    if [[ -d "$old_dir" ]]; then
      [[ -e "$new_dir" ]] && deny "a directory already exists at ${new_dir}"
      install -d -m 0750 "$(dirname "$new_dir")"
      mv -T -- "$old_dir" "$new_dir"
    fi
    ensure_app_directory "$user" "$app_new_name" >/dev/null
    old_env="$(app_env_file "$user" "$app_name")"
    [[ -f "$old_env" ]] && mv -f -- "$old_env" "$(app_env_file "$user" "$app_new_name")"
    old_compose="$(app_compose_file "$user" "$app_name")"
    [[ -f "$old_compose" ]] && mv -f -- "$old_compose" "$(app_compose_file "$user" "$app_new_name")"
    echo "$new_dir"
    ;;

  site-app-dir-ensure)
    [[ $# -eq 2 ]] || deny "usage: site-app-dir-ensure <owner-user> <name>"
    ensure_app_directory "$1" "$2"
    echo
    ;;

  site-app-export)
    # Everything an application owns, in one tar the panel can put in a backup:
    # its directory (parts of which containers own and the panel cannot read) and
    # each named volume (which live under /var/lib/docker, root's territory).
    [[ $# -eq 3 ]] || deny "usage: site-app-export <owner-user> <name> <dest-tar>"
    user="$1"; app_name="$2"; dest="$3"
    require_linux_user "$user"
    require_app_name "$app_name"
    require_backup_path "$dest"
    app_dir="$(app_directory "$user" "$app_name")"
    [[ -d "$app_dir" ]] || deny "application directory not found: $app_dir"
    stage="$(mktemp -d "${BACKUP_ROOT}/.app-export-XXXXXX")"
    trap 'rm -rf "$stage"' EXIT
    install -d -m 0700 "${stage}/volumes"
    tar -C "$app_dir" -cf "${stage}/files.tar" . 2>/dev/null || deny "could not read the application directory"
    if command -v docker >/dev/null 2>&1; then
      while IFS= read -r volume_name; do
        [[ -n "$volume_name" ]] || continue
        volume_path="/var/lib/docker/volumes/${volume_name}/_data"
        [[ -d "$volume_path" ]] || continue
        # Numeric owners: a database image expects its own uid inside the volume,
        # and that uid is the image's, not this machine's.
        tar -C "$volume_path" --numeric-owner -cf "${stage}/volumes/${volume_name}.tar" . 2>/dev/null || true
      done < <(docker volume ls --format '{{.Name}}' 2>/dev/null \
        | grep -E "^$(app_container_name "$user" "$app_name")_" || true)
    fi
    tar -C "$stage" --numeric-owner -cf "$dest" files.tar volumes
    chown spanel:spanel "$dest"
    chmod 0600 "$dest"
    rm -rf "$stage"
    trap - EXIT
    du -sb "$dest" | cut -f1
    ;;

  site-app-import)
    [[ $# -eq 3 ]] || deny "usage: site-app-import <owner-user> <name> <src-tar>"
    user="$1"; app_name="$2"; src="$3"
    require_linux_user "$user"
    require_app_name "$app_name"
    require_backup_path "$src"
    [[ -f "$src" && ! -L "$src" ]] || deny "no such export file: $src"
    app_dir="$(ensure_app_directory "$user" "$app_name")"
    stage="$(mktemp -d "${BACKUP_ROOT}/.app-import-XXXXXX")"
    trap 'rm -rf "$stage"' EXIT
    tar -C "$stage" -xf "$src" --no-same-owner
    [[ -f "${stage}/files.tar" ]] || deny "export file has no application directory"
    # No-same-owner then chown: the uid recorded in the archive may belong to a
    # different account on this machine, and a site tree is always owned by its
    # own user.
    tar -C "$app_dir" -xf "${stage}/files.tar" --no-same-owner
    chown -R "$user:$SPANEL_SITES_GROUP" "$app_dir"
    harden_site_dir "$app_dir" "$user"
    restored=0
    if command -v docker >/dev/null 2>&1; then
      for volume_tar in "${stage}/volumes"/*.tar; do
        [[ -f "$volume_tar" ]] || continue
        volume_name="$(basename "$volume_tar" .tar)"
        [[ "$volume_name" == "$(app_container_name "$user" "$app_name")_"* ]] \
          || deny "export contains a volume for another application: $volume_name"
        docker volume create "$volume_name" >/dev/null
        volume_path="/var/lib/docker/volumes/${volume_name}/_data"
        [[ -d "$volume_path" ]] || continue
        tar -C "$volume_path" -xf "$volume_tar" --numeric-owner -p
        restored=$((restored + 1))
      done
    fi
    rm -rf "$stage"
    trap - EXIT
    echo "restored ${app_name}: directory + ${restored} volume(s)"
    ;;

  site-app-volume-usage)
    # Named volumes live under /var/lib/docker, which the panel user cannot read,
    # so a customer's container data was invisible to the disk quota.
    [[ $# -eq 1 ]] || deny "usage: site-app-volume-usage <owner-user>"
    user="$1"
    require_linux_user "$user"
    command -v docker >/dev/null 2>&1 || { echo 0; exit 0; }
    total=0
    while IFS= read -r volume_name; do
      [[ -n "$volume_name" ]] || continue
      volume_path="/var/lib/docker/volumes/${volume_name}/_data"
      [[ -d "$volume_path" ]] || continue
      size="$(du -sb --one-file-system "$volume_path" 2>/dev/null | cut -f1)"
      [[ "$size" =~ ^[0-9]+$ ]] && total=$((total + size))
    done < <(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -E "^spanel-${user}-" || true)
    echo "$total"
    ;;

  site-app-volume-list)
    [[ $# -eq 2 ]] || deny "usage: site-app-volume-list <owner-user> <name>"
    user="$1"; app_name="$2"
    require_linux_user "$user"
    require_app_name "$app_name"
    command -v docker >/dev/null 2>&1 || exit 0
    docker volume ls --format '{{.Name}}' 2>/dev/null \
      | grep -E "^$(app_container_name "$user" "$app_name")_" || true
    ;;

  site-app-control)
    [[ $# -eq 3 ]] || deny "usage: site-app-control <owner-user> <name> <action>"
    user="$1"; app_name="$2"; app_action="$3"
    require_linux_user "$user"
    require_app_name "$app_name"
    is_in "$app_action" start stop restart status is-active is-enabled enable disable \
      || deny "action not allowed: $app_action"
    unit_name="$(app_unit_name "$user" "$app_name")"
    [[ -f "/etc/systemd/system/${unit_name}.service" ]] || deny "application unit not found: ${unit_name}"
    exec systemctl "$app_action" "${unit_name}.service" --no-pager
    ;;

  site-app-logs)
    [[ $# -eq 2 || $# -eq 3 ]] || deny "usage: site-app-logs <owner-user> <name> [lines]"
    user="$1"; app_name="$2"; log_lines="${3:-200}"
    require_linux_user "$user"
    require_app_name "$app_name"
    [[ "$log_lines" =~ ^[0-9]{1,4}$ ]] || deny "invalid line count: $log_lines"
    unit_name="$(app_unit_name "$user" "$app_name")"
    exec journalctl -u "${unit_name}.service" -n "$log_lines" --no-pager --output short-iso
    ;;

  site-app-delete)
    [[ $# -eq 2 ]] || deny "usage: site-app-delete <owner-user> <name>"
    user="$1"; app_name="$2"
    require_linux_user "$user"
    require_app_name "$app_name"
    unit_name="$(app_unit_name "$user" "$app_name")"
    systemctl disable --now "${unit_name}.service" 2>/dev/null || true
    rm -f "/etc/systemd/system/${unit_name}.service"
    remove_legacy_app_units "$user" "$app_name"
    systemctl daemon-reload
    if command -v docker >/dev/null 2>&1; then
      compose_file="$(app_compose_file "$user" "$app_name")"
      if [[ -f "$compose_file" ]]; then
        docker compose -f "$compose_file" --project-directory "$(app_directory "$user" "$app_name")" \
          -p "$(app_container_name "$user" "$app_name")" down --volumes 2>/dev/null || true
      fi
      docker rm -f "$(app_container_name "$user" "$app_name")" 2>/dev/null || true
    fi
    rm -f "$(app_env_file "$user" "$app_name")" "$(app_compose_file "$user" "$app_name")"
    # The app's files stay put; deleting a customer's code is never implied by
    # removing its runtime.
    echo "removed ${unit_name}"
    ;;

  site-app-install-deps)
    # npm install for a node app, as the site user, with a hard timeout so a
    # runaway postinstall cannot hold a worker forever.
    [[ $# -eq 3 ]] || deny "usage: site-app-install-deps <owner-user> <name> <node-major>"
    user="$1"; app_name="$2"; app_node_major="$3"
    require_linux_user "$user"
    require_app_name "$app_name"
    require_node_major "$app_node_major"
    app_dir="$(ensure_app_directory "$user" "$app_name")"
    [[ -f "${app_dir}/package.json" ]] || deny "no package.json in ${app_name}"
    bin_dir="$(resolve_node_bin_dir "$app_node_major")" \
      || deny "Node ${app_node_major} is not installed; run node-install ${app_node_major} first"
    exec timeout 900 runuser -u "$user" -- env -i \
      HOME="${HOME_ROOT}/${user}" \
      PATH="${bin_dir}:/usr/local/bin:/usr/bin:/bin" \
      NODE_ENV=production \
      "${bin_dir}/npm" install --omit=dev --no-audit --no-fund --prefix "$app_dir"
    ;;

  site-app-python-deps)
    [[ $# -eq 2 ]] || deny "usage: site-app-python-deps <owner-user> <name>"
    user="$1"; app_name="$2"
    require_linux_user "$user"
    require_app_name "$app_name"
    app_dir="$(ensure_app_directory "$user" "$app_name")"
    venv_dir="${app_dir}/.venv"
    if [[ ! -x "${venv_dir}/bin/python" ]]; then
      runuser -u "$user" -- python3 -m venv "$venv_dir" \
        || deny "failed to create Python virtualenv in ${venv_dir}"
    fi
    if [[ -f "${app_dir}/requirements.txt" ]]; then
      exec timeout 900 runuser -u "$user" -- env -i \
        HOME="${HOME_ROOT}/${user}" \
        PATH="${venv_dir}/bin:/usr/local/bin:/usr/bin:/bin" \
        "${venv_dir}/bin/pip" install --no-cache-dir -r "${app_dir}/requirements.txt"
    elif [[ -f "${app_dir}/pyproject.toml" ]]; then
      exec timeout 900 runuser -u "$user" -- env -i \
        HOME="${HOME_ROOT}/${user}" \
        PATH="${venv_dir}/bin:/usr/local/bin:/usr/bin:/bin" \
        "${venv_dir}/bin/pip" install --no-cache-dir "$app_dir"
    else
      echo "Python virtualenv initialized at ${venv_dir} (no requirements.txt found)"
    fi
    ;;

  site-app-git-pull)
    [[ $# -eq 2 ]] || deny "usage: site-app-git-pull <owner-user> <name>"
    user="$1"; app_name="$2"
    require_linux_user "$user"
    require_app_name "$app_name"
    app_dir="$(ensure_app_directory "$user" "$app_name")"
    if [[ -d "${app_dir}/.git" ]]; then
      timeout 120 runuser -u "$user" -- env -i \
        HOME="${HOME_ROOT}/${user}" \
        PATH="/usr/local/bin:/usr/bin:/bin" \
        git -C "$app_dir" pull --ff-only 2>&1 || true
    else
      echo "No .git repository detected in ${app_dir}; skipping git pull and proceeding with redeploy."
    fi
    ;;

  site-app-compose-ps)
    [[ $# -eq 2 ]] || deny "usage: site-app-compose-ps <owner-user> <name>"
    user="$1"; app_name="$2"
    require_linux_user "$user"
    require_app_name "$app_name"
    command -v docker >/dev/null 2>&1 || deny "Docker is not installed; run docker-install first"
    compose_file="$(app_compose_file "$user" "$app_name")"
    [[ -f "$compose_file" ]] || deny "no compose file for ${app_name}; deploy it once first"
    app_dir="$(app_directory "$user" "$app_name")"
    project="$(app_container_name "$user" "$app_name")"
    container_ids="$(timeout 60 docker compose -f "$compose_file" --project-directory "$app_dir" \
      -p "$project" ps --all --quiet 2>/dev/null || true)"
    [[ -n "$container_ids" ]] || exit 0
    # A container that keeps dying is restarted by Docker, so at any moment it
    # reads as running; only the restart count tells the panel it is looping.
    exec timeout 60 docker inspect --format \
      '{"service":"{{index .Config.Labels "com.docker.compose.service"}}","state":"{{.State.Status}}","restarts":{{.RestartCount}},"exit":{{.State.ExitCode}},"oom":{{.State.OOMKilled}},"started":"{{.State.StartedAt}}"}' \
      $container_ids
    ;;

  site-app-compose-pull)
    [[ $# -eq 2 ]] || deny "usage: site-app-compose-pull <owner-user> <name>"
    user="$1"; app_name="$2"
    require_linux_user "$user"
    require_app_name "$app_name"
    command -v docker >/dev/null 2>&1 || deny "Docker is not installed; run docker-install first"
    compose_file="$(app_compose_file "$user" "$app_name")"
    [[ -f "$compose_file" ]] || deny "no compose file for ${app_name}; deploy it once first"
    app_dir="$(app_directory "$user" "$app_name")"
    exec timeout 1800 docker compose -f "$compose_file" --project-directory "$app_dir" \
      -p "$(app_container_name "$user" "$app_name")" pull
    ;;

  site-app-pull)
    [[ $# -eq 1 ]] || deny "usage: site-app-pull <image>"
    command -v docker >/dev/null 2>&1 || deny "Docker is not installed; run docker-install first"
    require_docker_image "$1"
    exec timeout 900 docker pull -- "$1"
    ;;

  docker-install)
    [[ $# -eq 0 ]] || deny "usage: docker-install"
    install_docker_engine
    ;;

  docker-status)
    [[ $# -eq 0 ]] || deny "usage: docker-status"
    if ! command -v docker >/dev/null 2>&1; then
      echo "installed=no"
      exit 0
    fi
    echo "installed=yes"
    echo "version=$(docker --version 2>/dev/null | head -n1)"
    echo "active=$(systemctl is-active docker 2>/dev/null)"
    # Images are shared by every tenant on the box, so they cannot be billed to
    # one customer's quota; an administrator still has to see what they cost.
    while IFS= read -r line; do
      [[ -n "$line" ]] && echo "df=$line"
    done < <(docker system df --format '{{.Type}}|{{.Size}}|{{.Reclaimable}}' 2>/dev/null || true)
    ;;

  docker-prune)
    # Dangling layers and build cache only: nothing that a tagged image, a
    # volume or a container still refers to is touched.
    [[ $# -eq 0 ]] || deny "usage: docker-prune"
    command -v docker >/dev/null 2>&1 || deny "Docker is not installed"
    docker image prune -f 2>&1 || true
    docker builder prune -f 2>&1 || true
    echo "--- remaining ---"
    docker system df --format '{{.Type}}|{{.Size}}|{{.Reclaimable}}' 2>/dev/null || true
    ;;

  docker-firewall-guard)
    [[ $# -eq 0 ]] || deny "usage: docker-firewall-guard"
    install_docker_firewall_guard
    echo "Docker inbound guard applied"
    ;;

  node-install)
    [[ $# -eq 1 ]] || deny "usage: node-install <major>"
    install_node_major "$1"
    ;;

  node-list)
    [[ $# -eq 0 ]] || deny "usage: node-list"
    list_installed_node_majors
    ;;

  process-top)
    [[ $# -le 1 ]] || deny "usage: process-top [threads]"
    top_flags=(-b -n 1 -c)
    if [[ "${1:-}" == "threads" ]]; then
      top_flags=(-H -b -n 1 -c)
    elif [[ -n "${1:-}" ]]; then
      deny "invalid mode: $1"
    fi
    unshare --mount -- bash -c 'mount -t proc proc /proc 2>/dev/null || true; exec env LC_ALL=C COLUMNS=512 top "$@"' _ "${top_flags[@]}" 2>/dev/null \
      || exec env LC_ALL=C COLUMNS=512 top "${top_flags[@]}"
    ;;

  process-ps)
    [[ $# -eq 0 ]] || deny "usage: process-ps"
    unshare --mount -- bash -c 'mount -t proc proc /proc 2>/dev/null || true; exec env LC_ALL=C ps -eo pid,user:32,pcpu,rss,nlwp,comm,args --no-headers' 2>/dev/null \
      || exec env LC_ALL=C ps -eo pid,user:32,pcpu,rss,nlwp,comm,args --no-headers
    ;;

  process-kill)
    [[ $# -eq 1 ]] || deny "usage: process-kill <pid>"
    target_pid="$1"
    [[ "$target_pid" =~ ^[0-9]{1,10}$ ]] || deny "invalid pid: $target_pid"
    (( target_pid > 2 )) || deny "refusing to kill protected system PID: $target_pid"
    api_main_pid="$(systemctl show -p MainPID --value spanel-api 2>/dev/null || echo 0)"
    if [[ "$target_pid" == "$api_main_pid" || "$target_pid" == "$$" || "$target_pid" == "$PPID" ]]; then
      deny "refusing to kill SPanel API process: $target_pid"
    fi
    if [[ -d "/proc/${target_pid}" ]] && [[ ! -s "/proc/${target_pid}/cmdline" ]] && [[ ! -e "/proc/${target_pid}/exe" ]]; then
      deny "refusing to kill kernel thread: $target_pid"
    fi
    # Only hosting accounts' processes: anything owned by root or a system
    # account is a service, and those are restarted from System Services.
    target_uid="$(awk '$1=="Uid:"{print $2; exit}' "/proc/${target_pid}/status" 2>/dev/null || true)"
    [[ "$target_uid" =~ ^[0-9]+$ ]] || deny "No such process: $target_pid"
    if (( target_uid < 1000 || target_uid >= 65000 )); then
      deny "refusing to kill process of system account (uid ${target_uid}): $target_pid"
    fi
    if [[ "$(getent passwd "$target_uid" | cut -d: -f1)" == "spanel" ]]; then
      deny "refusing to kill SPanel process: $target_pid"
    fi
    target_comm="$(tr -d '\0\n' <"/proc/${target_pid}/comm" 2>/dev/null || true)"
    case "$target_comm" in
      systemd|systemd-*|init|kthreadd|rcu_*|kworker*|ksoftirqd*|migration*|watchdog*|dbus-daemon|sshd|mariadbd|mysqld|nginx|redis-server|cron|crond|rsyslogd|fail2ban-server|pdns_server|dovecot|exim4|rspamd*|clamd|freshclam|dockerd|containerd*)
        deny "refusing to kill protected core daemon (${target_comm}): use System Services to manage daemons"
        ;;
    esac
    kill -9 "$target_pid"
    ;;

  *)
    deny "unknown command: $cmd"
    ;;
esac
