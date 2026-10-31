#!/usr/bin/env bash
set -euo pipefail

SPANEL_INSTALLER_VERSION="${SPANEL_INSTALLER_VERSION:-v1.0.8}"

if [[ $EUID -ne 0 ]]; then
  echo "Please run this installer as root"
  exit 1
fi

if [[ ! -f /etc/os-release ]]; then
  echo "Cannot find /etc/os-release"
  exit 1
fi

source /etc/os-release
if [[ "${ID}" != "ubuntu" || "${VERSION_ID}" != "24.04" ]]; then
  echo "This installer only supports Ubuntu 24.04"
  echo "Current OS: ${PRETTY_NAME:-unknown}"
  exit 1
fi

if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
else
  SCRIPT_DIR=""
  PROJECT_ROOT=""
fi
BACKEND_SRC="${PROJECT_ROOT:+${PROJECT_ROOT}/backend}"
FRONTEND_SRC="${PROJECT_ROOT:+${PROJECT_ROOT}/frontend}"

if [[ ! -t 0 ]]; then
  # `[[ -r /dev/tty ]]` is the wrong test. In a session-less context (a CI
  # runner, a systemd unit, some container shells) /dev/tty exists and its
  # permission bits say readable, so the test passes -- then the open fails
  # with ENXIO and the install dies after claiming a terminal was available.
  # Make the open itself the test. It is probed in a subshell first because a
  # failed `exec` redirection terminates a non-interactive shell outright.
  if (exec </dev/tty) 2>/dev/null; then
    exec </dev/tty
  elif [[ -n "${PANEL_URL:-}" ]]; then
    : # Nothing left to prompt for: PANEL_URL supplies hostname and port.
  else
    echo "ERROR: This installer needs an interactive terminal." >&2
    echo "       Run it from SSH or export SPANEL_URL/PANEL_PORT first." >&2
    exit 1
  fi
fi

if [[ -z "${BACKEND_SRC}" || ! -d "${BACKEND_SRC}" ]]; then
  echo "ERROR: Local source files not found." >&2
  echo "Please extract the SPanel source code and run the installer from within the extracted directory." >&2
  echo "Example:" >&2
  echo "  cd /root/spanel-main" >&2
  echo "  bash installer/install.sh" >&2
  exit 1
fi

PANEL_URL="${PANEL_URL:-}"
# Filled in by enable_ipv6_when_available: off | on:<address> | failed
IPV6_RESULT="off"
PANEL_HOSTNAME="${PANEL_HOSTNAME:-}"
PANEL_DOMAIN=""
PANEL_PORT="${PANEL_PORT:-2222}"
# Opt-in only: set PANEL_TIMEZONE=Asia/Ho_Chi_Minh (or any zoneinfo name) to have
# the installer set the server timezone. Left blank, the installer never touches
# it - TOTP runs off UTC, so the clock sync below is what actually matters.
PANEL_TIMEZONE="${PANEL_TIMEZONE:-}"
SERVER_IP=""
ENABLE_SSL="${ENABLE_SSL:-auto}"
SSL_EMAIL="${SSL_EMAIL:-}"
NODE_MAJOR="${NODE_MAJOR:-22}"
PHP_DEFAULT="${PHP_DEFAULT:-8.4}"
PHP_VERSIONS="${PHP_VERSIONS:-8.3 8.4}"
APP_DIR="${APP_DIR:-/opt/spanel}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/spanel}"
# Opt-in: SPANEL_DISK_QUOTA=kernel turns on kernel user quota (disk space and
# inodes per account) for the filesystem holding /home. The installer only
# edits /etc/fstab; when the options are not mounted yet it says a remount or
# reboot is needed and does neither itself. Without it the panel's own
# accounting applies, which PHP and SFTP can write past.
SPANEL_DISK_QUOTA="${SPANEL_DISK_QUOTA:-}"
ADMIN_PASSWORD=""

# `dpkg -s` exits 0 for a package that was removed but kept its config files
# ("deinstall ok config-files"), whose binaries are gone. Ask for the state.
pkg_installed() {
  [[ "$(dpkg-query -W -f='${Status}' "$1" 2>/dev/null)" == "install ok installed" ]]
}

log() {
  echo ""
  echo "==> $1"
}

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

detect_server_ip() {
  hostname -I 2>/dev/null | awk '{print $1}' || true
}

find_sshd() {
  if command -v sshd >/dev/null 2>&1; then
    command -v sshd
    return 0
  fi
  for candidate in /usr/sbin/sshd /usr/local/sbin/sshd; do
    [[ -x "$candidate" ]] && { echo "$candidate"; return 0; }
  done
  return 1
}

validate_port() {
  [[ "$1" =~ ^[0-9]{1,5}$ ]] || fail "Invalid PANEL_PORT: $1"
  (( $1 >= 1 && $1 <= 65535 )) || fail "PANEL_PORT out of range: $1"
}

detect_ssh_ports() {
  local sshd_bin
  sshd_bin="$(find_sshd || true)"
  {
    if [[ -n "$sshd_bin" ]]; then
      "$sshd_bin" -T 2>/dev/null | awk '$1 == "port" {print $2}'
    fi
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
      awk '{print $4}' <<<"$SSH_CONNECTION"
    fi
    awk '
      tolower($1) == "port" && $2 ~ /^[0-9]+$/ { print $2 }
      tolower($1) == "listenaddress" {
        for (i = 2; i <= NF; i++) {
          value = $i
          gsub(/^\[/, "", value)
          gsub(/\]$/, "", value)
          if (value ~ /:[0-9]+$/) {
            sub(/^.*:/, "", value)
            print value
          }
        }
      }
    ' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null
  } | awk '$1 ~ /^[0-9]+$/ && $1 >= 1 && $1 <= 65535 {print $1}' | sort -nu
}

is_domain_name() {
  [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]]
}

need_dir() {
  [[ -d "$1" ]] || fail "Missing directory $1. Upload backend, frontend, and installer."
}

validate_sources() {
  need_dir "$BACKEND_SRC"
  need_dir "$FRONTEND_SRC"
  [[ -f "${PROJECT_ROOT}/VERSION" ]] || fail "Missing VERSION"
  [[ -f "${BACKEND_SRC}/requirements.txt" ]] || fail "Missing backend/requirements.txt"
  [[ -f "${FRONTEND_SRC}/package.json" ]] || fail "Missing frontend/package.json"
}

ask_panel_url() {
  validate_port "$PANEL_PORT"
  if [[ -n "$PANEL_URL" ]]; then
    PANEL_URL="${PANEL_URL%/}"
    if [[ "$PANEL_URL" =~ ^https?:// ]]; then
      PANEL_HOSTNAME="$(echo "$PANEL_URL" | sed -E 's#^https?://([^/:]+).*#\1#')"
      parsed_port="$(echo "$PANEL_URL" | sed -nE 's#^https?://[^/:]+:([0-9]+).*#\1#p')"
    else
      PANEL_HOSTNAME="$(echo "$PANEL_URL" | sed -E 's#^([^/:]+).*#\1#')"
      parsed_port="$(echo "$PANEL_URL" | sed -nE 's#^[^/:]+:([0-9]+).*#\1#p')"
    fi
    if [[ -n "${parsed_port:-}" ]]; then
      PANEL_PORT="$parsed_port"
      validate_port "$PANEL_PORT"
    fi
  fi

  if [[ -z "$PANEL_HOSTNAME" ]]; then
    read -rp "Enter panel hostname (optional, blank = server IP): " PANEL_HOSTNAME
  fi
  PANEL_HOSTNAME="${PANEL_HOSTNAME#http://}"
  PANEL_HOSTNAME="${PANEL_HOSTNAME#https://}"
  PANEL_HOSTNAME="${PANEL_HOSTNAME%%/*}"
  if [[ "$PANEL_HOSTNAME" == *:* ]]; then
    parsed_port="${PANEL_HOSTNAME##*:}"
    PANEL_HOSTNAME="${PANEL_HOSTNAME%%:*}"
    [[ -n "$parsed_port" ]] && PANEL_PORT="$parsed_port"
    validate_port "$PANEL_PORT"
  fi
  if [[ -z "${PANEL_URL:-}" ]]; then
    read -rp "Enter panel port [${PANEL_PORT}]: " panel_port_answer
    if [[ -n "$panel_port_answer" ]]; then
      PANEL_PORT="$panel_port_answer"
      validate_port "$PANEL_PORT"
    fi
  fi

  if [[ -z "$PANEL_HOSTNAME" ]]; then
    SERVER_IP="$(detect_server_ip)"
    [[ -n "$SERVER_IP" ]] || fail "Cannot detect server IP. Set PANEL_HOSTNAME manually."
    PANEL_DOMAIN=""
    PANEL_URL="http://${SERVER_IP}:${PANEL_PORT}"
    ENABLE_SSL="no"
    return 0
  fi

  PANEL_DOMAIN="$PANEL_HOSTNAME"

  if [[ "$PANEL_DOMAIN" == "localhost" || "$PANEL_DOMAIN" == "127.0.0.1" || "$PANEL_DOMAIN" =~ ^[0-9.]+$ ]]; then
    ENABLE_SSL="no"
    PANEL_URL="http://${PANEL_DOMAIN}:${PANEL_PORT}"
  elif [[ "$ENABLE_SSL" == "auto" ]]; then
    if ! is_domain_name "$PANEL_DOMAIN"; then
      fail "Invalid panel domain: $PANEL_DOMAIN"
    fi
    read -rp "Enable Let's Encrypt SSL for ${PANEL_DOMAIN}:${PANEL_PORT}? [Y/n]: " ssl_answer
    ssl_answer="${ssl_answer:-Y}"
    if [[ "$ssl_answer" =~ ^[Nn]$ ]]; then
      ENABLE_SSL="no"
      PANEL_URL="http://${PANEL_DOMAIN}:${PANEL_PORT}"
    else
      ENABLE_SSL="yes"
      PANEL_URL="https://${PANEL_DOMAIN}:${PANEL_PORT}"
    fi
  elif [[ "$ENABLE_SSL" == "yes" ]]; then
    is_domain_name "$PANEL_DOMAIN" || fail "Invalid panel domain: $PANEL_DOMAIN"
    PANEL_URL="https://${PANEL_DOMAIN}:${PANEL_PORT}"
  else
    ENABLE_SSL="no"
    PANEL_URL="http://${PANEL_DOMAIN}:${PANEL_PORT}"
  fi

  if [[ "$ENABLE_SSL" == "yes" && -z "$SSL_EMAIL" ]]; then
    read -rp "Enter email for Let's Encrypt registration: " SSL_EMAIL
    [[ -n "$SSL_EMAIL" ]] || fail "Email is required to issue SSL"
  fi
}

# A freshly-booted Ubuntu runs unattended-upgrades for the first few minutes,
# holding the dpkg locks. Every apt call here went straight to
# "Could not get lock /var/lib/dpkg/lock-frontend", which killed the install
# partway through -- leaving a half-configured box. Wait the lock out instead.
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

install_base_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt_get update --allow-releaseinfo-change
  local pkgs=(software-properties-common ca-certificates curl gnupg git composer nginx mariadb-server redis-server openssh-server python3 python3-pip python3-venv certbot python3-certbot-nginx tar zip unzip openssl iptables ipset phpmyadmin acl)
  if ! apt_get install -y "${pkgs[@]}"; then
    # Seen on some VPS images: a stuck package pin (e.g. libsystemd-shared)
    # leaves an unrelated dependency "not going to be installed" and apt
    # itself suggests this fix. Repair once and retry before giving up -
    # otherwise the whole install dies here instead of a targeted failure.
    echo "Package install hit a broken dependency; repairing and retrying..."
    apt_get --fix-broken install -y || true
    apt_get install -y "${pkgs[@]}"
  fi
  systemctl enable --now nginx mariadb redis-server
  systemctl enable --now ssh 2>/dev/null || systemctl enable --now sshd 2>/dev/null || true
}

install_nodejs() {
  curl -fsSL --connect-timeout 10 --max-time 180 "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
  apt_get install -y nodejs
  node - <<'NODE'
const major = Number(process.versions.node.split('.')[0]);
if (major < 20) {
  throw new Error(`Node.js 20+ is required, current: ${process.version}`);
}
console.log(`Using Node.js ${process.version}`);
NODE
  npm --version
}

install_ioncube_loader() {
  local version="$1" arch url tmp archive loader target_dir target loader_ini_dir
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

  apt_get install -y ca-certificates curl tar >/dev/null
  tmp="$(mktemp -d)" || fail "Cannot create ionCube temporary directory"
  archive="${tmp}/ioncube_loaders.tar.gz"
  if ! curl -fsSL --connect-timeout 10 --max-time 300 "$url" -o "$archive"; then
    rm -rf -- "$tmp"
    fail "Failed to download ionCube Loader"
  fi
  if ! tar -xzf "$archive" -C "$tmp"; then
    rm -rf -- "$tmp"
    fail "Failed to unpack ionCube Loader"
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
    if ! "php${version}" -v 2>&1 | grep -qi 'ionCube'; then
      rm -f /etc/php/"$version"/cli/conf.d/00-ioncube.ini /etc/php/"$version"/fpm/conf.d/00-ioncube.ini
      fail "ionCube Loader failed to load for PHP ${version}"
    fi
  fi
  echo "ionCube Loader enabled for PHP ${version}"
}

install_php() {
  add-apt-repository -y ppa:ondrej/php
  apt_get update --allow-releaseinfo-change

  if [[ ! " ${PHP_VERSIONS} " =~ " ${PHP_DEFAULT} " ]]; then
    fail "PHP_DEFAULT=${PHP_DEFAULT} must be included in PHP_VERSIONS='${PHP_VERSIONS}'"
  fi

  for version in $PHP_VERSIONS; do
    packages=(
      "php${version}-fpm"
      "php${version}-cli"
      "php${version}-mysql"
      "php${version}-sqlite3"
      "php${version}-gd"
      "php${version}-xml"
      "php${version}-mbstring"
      "php${version}-curl"
      "php${version}-zip"
      "php${version}-opcache"
      "php${version}-intl"
      "php${version}-bcmath"
      "php${version}-redis"
      "php${version}-imagick"
      "php${version}-imap"
    )

    available_packages=()
    missing_packages=()
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

    if [[ ${#available_packages[@]} -eq 0 ]]; then
      fail "No package found for PHP ${version}. Remove ${version} from PHP_VERSIONS."
    fi

    apt_get install -y "${available_packages[@]}"

    # Hold them out of autoremove's reach. Extensions are leaf packages -
    # nothing depends on php<v>-mysql - so apt is free to decide they are
    # unused and take them, which is exactly what happened to a customer.
    # Marking them manual states the truth: an operator asked for these.
    apt-mark manual "${available_packages[@]}" >/dev/null 2>&1 || true

    # Installing php<v>-mysql is not the same as enabling it, and enabling a
    # module whose .so is absent is worse than leaving it alone: phpenmod
    # writes the symlink anyway and exits 0, after which every `php` run
    # prints "Unable to load dynamic library". Enable only what is present.
    php_ext_dir="$("php${version}" -i 2>/dev/null | sed -n 's/^extension_dir => \([^ ]*\).*/\1/p' | head -1)"
    if [[ -n "$php_ext_dir" && -f "${php_ext_dir}/mysqli.so" ]]; then
      for mod in mysqlnd mysqli pdo_mysql; do
        phpenmod -v "$version" "$mod" 2>/dev/null || true
      done
    fi

    install_ioncube_loader "$version"

    ini_file="/etc/php/${version}/fpm/php.ini"
    if [[ -f "$ini_file" ]]; then
      sed -i \
        -e 's/^\s*;\?\s*upload_max_filesize\s*=.*/upload_max_filesize = 1024M/' \
        -e 's/^\s*;\?\s*post_max_size\s*=.*/post_max_size = 1024M/' \
        -e 's/^\s*;\?\s*memory_limit\s*=.*/memory_limit = 1024M/' \
        -e 's/^\s*;\?\s*max_execution_time\s*=.*/max_execution_time = 300/' \
        -e 's/^\s*;\?\s*max_input_time\s*=.*/max_input_time = 600/' \
        -e 's/^\s*;\?\s*max_input_vars\s*=.*/max_input_vars = 10000/' \
        -e 's/^\s*;\?\s*max_file_uploads\s*=.*/max_file_uploads = 100/' \
        "$ini_file"
    fi

    systemctl enable --now "php${version}-fpm"
  done

  update-alternatives --set php "/usr/bin/php${PHP_DEFAULT}" || true
}

configure_fastcgi_cache() {
  install -d -o www-data -g www-data -m 0755 /var/cache/nginx/spanel-fastcgi
  find /var/cache/nginx/spanel-fastcgi -mindepth 1 -delete
  cat >/etc/nginx/conf.d/00-spanel-fastcgi-cache.conf <<'NGINX'
map $request_uri $spanel_cache_uri {
    default $request_uri;
    "~^([^?]+)\?(?:utm_[a-z]+=[^&]*|fbclid=[^&]*|gclid=[^&]*|ttclid=[^&]*|msclkid=[^&]*|_ga=[^&]*|mc_[ce]id=[^&]*)(?:&(?:utm_[a-z]+=[^&]*|fbclid=[^&]*|gclid=[^&]*|ttclid=[^&]*|msclkid=[^&]*|_ga=[^&]*|mc_[ce]id=[^&]*))*$" $1;
}
fastcgi_cache_path /var/cache/nginx/spanel-fastcgi levels=1:2 keys_zone=SPANEL_FASTCGI:32m inactive=2h max_size=1g use_temp_path=off;
fastcgi_cache_key "$scheme$request_method$host$spanel_cache_uri";
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

write_modsec_base_conf() {
  install -d -o root -g root -m 0755 /etc/nginx/modsec /etc/nginx/modsec/sites
  {
    [[ -f /etc/modsecurity/modsecurity.conf ]] && echo "Include /etc/modsecurity/modsecurity.conf"
    echo "SecRuleEngine On"
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

write_http_flood_nginx_conf() {
  install -d -o root -g root -m 0755 /etc/nginx/spanel /etc/nginx/conf.d
  if [[ ! -f /etc/nginx/spanel/http-flood-zones.conf ]]; then
    cat >/etc/nginx/spanel/http-flood-zones.conf <<'CONF'
# Managed by SPanel. Shared zones for per-website HTTP flood protection.
map $cookie_spanel_http_flood_ok $spanel_http_flood_key {
    default $binary_remote_addr;
    1 "";
}
limit_conn_zone $spanel_http_flood_key zone=spanel_conn_flood:10m;
CONF
  fi
  cat >/etc/nginx/conf.d/00-spanel-http-flood.conf <<'CONF'
# Managed by SPanel. Shared zones for per-website HTTP flood protection.
include /etc/nginx/spanel/http-flood-zones.conf;
CONF
  rm -f /etc/nginx/conf.d/spanel-http-flood.conf /etc/nginx/spanel/http-flood-server.conf 2>/dev/null || true
  chown root:root /etc/nginx/conf.d/00-spanel-http-flood.conf /etc/nginx/spanel/http-flood-zones.conf
  chmod 0644 /etc/nginx/conf.d/00-spanel-http-flood.conf /etc/nginx/spanel/http-flood-zones.conf
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

install_waf_engine() {
  export DEBIAN_FRONTEND=noninteractive
  if ! pkg_installed libnginx-mod-http-modsecurity; then
    apt_get update --allow-releaseinfo-change
    apt_get install -y libnginx-mod-http-modsecurity modsecurity-crs libmodsecurity3 || \
      apt_get install -y libnginx-mod-http-modsecurity libmodsecurity3
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
  systemctl reload nginx || true
}

install_wp_cli() {
  if ! command -v wp >/dev/null 2>&1; then
    curl -fsSL --connect-timeout 10 --max-time 180 -o /usr/local/bin/wp https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar
    chmod +x /usr/local/bin/wp
  fi
}

install_composer_cli() {
  local php_cli="" composer_ver="" cand
  for cand in php php8.5 php8.4 php8.3 php8.2 php8.1; do
    if command -v "$cand" >/dev/null 2>&1; then
      php_cli="$(command -v "$cand")"
      break
    fi
  done
  [[ -n "$php_cli" ]] || return 0
  if [[ -x /usr/local/bin/composer ]]; then
    if command -v timeout >/dev/null 2>&1; then
      composer_ver="$(COMPOSER_ALLOW_SUPERUSER=1 COMPOSER_NO_INTERACTION=1 timeout 15 "$php_cli" -d error_reporting=8191 /usr/local/bin/composer --version --no-interaction </dev/null 2>/dev/null || true)"
    else
      composer_ver="$(COMPOSER_ALLOW_SUPERUSER=1 COMPOSER_NO_INTERACTION=1 "$php_cli" -d error_reporting=8191 /usr/local/bin/composer --version --no-interaction </dev/null 2>/dev/null || true)"
    fi
    if printf '%s\n' "$composer_ver" | grep -qE 'Composer version (2\.(8|9|[1-9][0-9])|[3-9]\.)'; then
      return 0
    fi
  fi
  local tmp_dir expected_sha actual_sha
  tmp_dir="$(mktemp -d)" || return 0
  if ( curl -4fsSL --connect-timeout 8 --max-time 60 -o "${tmp_dir}/composer.phar" https://getcomposer.org/download/latest-stable/composer.phar </dev/null \
       || curl -fsSL --connect-timeout 8 --max-time 60 -o "${tmp_dir}/composer.phar" https://getcomposer.org/download/latest-stable/composer.phar </dev/null ) \
    && ( curl -4fsSL --connect-timeout 8 --max-time 20 -o "${tmp_dir}/composer.phar.sha256sum" https://getcomposer.org/download/latest-stable/composer.phar.sha256sum </dev/null \
       || curl -fsSL --connect-timeout 8 --max-time 20 -o "${tmp_dir}/composer.phar.sha256sum" https://getcomposer.org/download/latest-stable/composer.phar.sha256sum </dev/null ); then
    expected_sha="$(awk '{print $1}' "${tmp_dir}/composer.phar.sha256sum" 2>/dev/null || true)"
    actual_sha="$(sha256sum "${tmp_dir}/composer.phar" 2>/dev/null | awk '{print $1}' || true)"
    if [[ -n "$expected_sha" && "$expected_sha" == "$actual_sha" ]]; then
      install -o root -g root -m 0755 "${tmp_dir}/composer.phar" /usr/local/bin/composer
    else
      echo "WARNING: Composer PHAR SHA-256 mismatch; falling back to system composer." >&2
    fi
  else
    echo "WARNING: Could not download latest Composer PHAR; falling back to system composer." >&2
  fi
  rm -rf "$tmp_dir"
}

copy_sources() {
  mkdir -p "$APP_DIR" "$BACKUP_ROOT"
  if [[ "${PROJECT_ROOT}" != "${APP_DIR}" ]]; then
    rm -rf "${APP_DIR}/backend" "${APP_DIR}/frontend"
    cp -r "$BACKEND_SRC" "${APP_DIR}/backend"
    cp -r "$FRONTEND_SRC" "${APP_DIR}/frontend"
    install -m 0644 "$PROJECT_ROOT/VERSION" "${APP_DIR}/VERSION"
  fi
}

build_frontend() {
  cd "${APP_DIR}/frontend"
  rm -rf node_modules package-lock.json dist .vite
  export npm_config_legacy_peer_deps=true
  export NPM_CONFIG_LEGACY_PEER_DEPS=true
  npm install --no-audit --no-fund --legacy-peer-deps || npm install --no-audit --no-fund --force
  VITE_API_URL=/api npm run build
  if [[ ! -f dist/index.html ]]; then
    fail "Frontend build failed: ${APP_DIR}/frontend/dist/index.html is missing"
  fi
  # Nginx (www-data) needs to read the bundle. The frontend is public anyway.
  chmod o+rX "${APP_DIR}" "${APP_DIR}/frontend" 2>/dev/null || true
  chmod -R o+rX "${APP_DIR}/frontend/dist"
  echo "Frontend built: $(grep -oE 'index-[a-zA-Z0-9_-]+\.js' dist/index.html | head -n1 || echo 'unknown')"
}

setup_panel_user() {
  if ! getent group spanel-sites >/dev/null; then
    groupadd --system spanel-sites
  fi
  if ! getent group spanel-sftp >/dev/null; then
    groupadd --system spanel-sftp
  fi
  if ! id -u spanel >/dev/null 2>&1; then
    useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin --user-group spanel
  fi
  usermod -aG www-data spanel || true
  usermod -aG spanel-sites spanel || true
  usermod -aG spanel-sites www-data || true

  # Allow spanel to write into /etc/nginx/conf.d (vhost files).
  install -d -o root -g spanel -m 2775 /etc/nginx/conf.d
  install -d -o root -g spanel -m 2775 /etc/nginx/spanel/custom
  # setgid so new files inherit the spanel group; allows future writes.
  chmod g+s /etc/nginx/conf.d || true
  chmod g+s /etc/nginx/spanel/custom 2>/dev/null || true

  # Make the panel data dirs writable by spanel.
  install -d -o spanel -g spanel -m 0750 "$APP_DIR"
  install -d -o spanel -g spanel -m 0750 "$BACKUP_ROOT"
  # Account backups live one level down. Create it here rather than leaving it
  # to whatever runs first: on a server where a root process got there ahead of
  # the panel it came out root-owned, and every scheduled backup after that
  # died on Permission denied.
  install -d -o spanel -g spanel -m 0750 "$BACKUP_ROOT/users"
  install -d -o spanel -g spanel -m 0750 "$BACKUP_ROOT/users/restore"
  # DirectAdmin import staging dirs
  install -d -o spanel -g spanel -m 0750 /home/admin/spanel_backups/da
  install -d -o spanel -g spanel -m 0750 /var/lib/spanel/da-import
  install -d -o spanel -g spanel -m 0750 /var/lib/spanel/import-stage

  # MariaDB: create an admin user that spanel can use without password
  # (auth via a defaults-file in ~spanel/.my.cnf, mode 0600).
  local mariadb_password
  mariadb_password="$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)"
  if ! mariadb -e "SELECT 1" >/dev/null 2>&1 && ! mysql -e "SELECT 1" >/dev/null 2>&1; then
    echo "MySQL/MariaDB root password required."
    read -s -p "Enter MySQL/MariaDB root password: " MYSQL_ROOT_PW
    echo ""
    export MYSQL_PWD="$MYSQL_ROOT_PW"
  fi

  mariadb -e "
    CREATE USER IF NOT EXISTS 'spanel'@'localhost' IDENTIFIED BY '${mariadb_password}';
    GRANT ALL PRIVILEGES ON *.* TO 'spanel'@'localhost' WITH GRANT OPTION;
    FLUSH PRIVILEGES;
  " 2>/dev/null || mysql -e "
    CREATE USER IF NOT EXISTS 'spanel'@'localhost' IDENTIFIED BY '${mariadb_password}';
    GRANT ALL PRIVILEGES ON *.* TO 'spanel'@'localhost' WITH GRANT OPTION;
    FLUSH PRIVILEGES;
  "

  cat >"${APP_DIR}/.my.cnf" <<MYCNF
[client]
user=spanel
password="${mariadb_password}"
host=localhost

[mysqldump]
user=spanel
password="${mariadb_password}"
host=localhost
MYCNF
  chown spanel:spanel "${APP_DIR}/.my.cnf"
  chmod 0600 "${APP_DIR}/.my.cnf"
}

setup_sftp_access() {
  local sshd_config="/etc/ssh/sshd_config" backup
  getent group spanel-sftp >/dev/null || groupadd --system spanel-sftp
  getent group spanel-sftp-site >/dev/null || groupadd --system spanel-sftp-site
  # Root owns the chroot roots and everything above them. Deliberately not
  # under /var/lib/spanel, which belongs to the panel account.
  install -d -o root -g root -m 0755 /var/lib/spanel-sftp
  install -d -o root -g root -m 0755 /run/sshd
  rm -f /etc/ssh/sshd_config.d/99-spanel-sftp.conf 2>/dev/null || true
  touch "$sshd_config"
  backup="${sshd_config}.spanel.bak"
  cp "$sshd_config" "$backup"
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
  if ! sshd -t; then
    cp "$backup" "$sshd_config"
    fail "Invalid SSHD configuration for SPanel SFTP users"
  fi
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
}

install_privileged_helper() {
  install -m 0750 -o root -g spanel "${SCRIPT_DIR}/files/spanel-helper.sh" /usr/local/sbin/spanel-helper
  sed -i "s#^APP_DIR=\"/opt/spanel\"#APP_DIR=\"${APP_DIR}\"#" /usr/local/sbin/spanel-helper
  if [[ -f "${SCRIPT_DIR}/files/spanel-cron-jail.sh" ]]; then
    install -m 0755 -o root -g root "${SCRIPT_DIR}/files/spanel-cron-jail.sh" /usr/local/bin/spanel-cron-jail
  fi
  # The Email addon's renderer: root's, read by the helper only.
  if [[ -f "${SCRIPT_DIR}/files/spanel_mailtool.py" ]]; then
    install -d -m 0755 -o root -g root /usr/local/lib/spanel
    install -m 0644 -o root -g root "${SCRIPT_DIR}/files/spanel_mailtool.py" /usr/local/lib/spanel/spanel_mailtool.py
  fi
  if [[ -f "${SCRIPT_DIR}/files/webmail/backend/main.py" && -f "${SCRIPT_DIR}/files/webmail/backend/requirements.txt" ]]; then
    install -d -m 0755 -o root -g root /usr/local/lib/spanel/webmail /usr/local/lib/spanel/webmail/backend
    install -m 0755 -o root -g root "${SCRIPT_DIR}/files/webmail/backend/main.py" /usr/local/lib/spanel/webmail/backend/main.py
    install -m 0644 -o root -g root "${SCRIPT_DIR}/files/webmail/backend/requirements.txt" /usr/local/lib/spanel/webmail/backend/requirements.txt
  fi
  if [[ -f "${SCRIPT_DIR}/files/spanel_webshells.yar" ]]; then
    install -d -m 0755 -o root -g spanel /etc/spanel/yara
    install -m 0644 -o root -g spanel "${SCRIPT_DIR}/files/spanel_webshells.yar" /etc/spanel/yara/spanel_webshells.yar
  fi
  install -d -m 0750 -o root -g spanel /etc/spanel
  if [[ ! -s /etc/spanel/audit-hmac.key ]]; then
    openssl rand -hex 32 >/etc/spanel/audit-hmac.key
  fi
  chown root:spanel /etc/spanel/audit-hmac.key
  chmod 0640 /etc/spanel/audit-hmac.key
  # The launcher: every update fetches its steps from the release it installs.
  install -m 0755 -o root -g root "${SCRIPT_DIR}/files/spanel-update" /usr/local/sbin/spanel-update
  install -m 0440 -o root -g root "${SCRIPT_DIR}/files/spanel-sudoers" /etc/sudoers.d/spanel
  visudo -c -f /etc/sudoers.d/spanel >/dev/null
  install -m 0755 -o root -g root "${SCRIPT_DIR}/rescue-firewall.sh" /usr/local/sbin/spanel-rescue-firewall
  install -m 0755 -o root -g root "${SCRIPT_DIR}/files/spanel-memory-guard" /usr/local/sbin/spanel-memory-guard
  ln -sfn /usr/local/sbin/spanel-rescue-firewall /usr/local/sbin/spanel-rescue-ufw-blocklist
  if [[ -f "${PROJECT_ROOT}/change_IP.sh" ]]; then
    install -m 0755 -o root -g root "${PROJECT_ROOT}/change_IP.sh" /usr/local/sbin/spanel-change-ip
  fi
}

install_panel_cli() {
  install -m 0755 -o root -g root "${SCRIPT_DIR}/files/spanelctl" /usr/local/sbin/spanel
  ln -sfn /usr/local/sbin/spanel /usr/local/sbin/spanelctl
  sed -i "s#APP_DIR=\"\${APP_DIR:-/opt/spanel}\"#APP_DIR=\"\${APP_DIR:-${APP_DIR}}\"#" /usr/local/sbin/spanel /usr/local/sbin/spanelctl 2>/dev/null || true
}

validate_privileged_helper() {
  sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper wp --info >/dev/null
}

# Hand the backend tree to spanel but keep the virtualenv root-owned. See the
# call site for why: root sources and executes files from .venv, so spanel must
# not be able to write them.
chown_backend_except_venv() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0
  find "$dir" -path "$dir/.venv" -prune -o -exec chown -h spanel:spanel {} + 2>/dev/null || true
  # Reclaim the venv on an upgrade from a build that gave it away.
  [[ -d "$dir/.venv" ]] && chown -R root:root "$dir/.venv" 2>/dev/null || true
  return 0
}

# The same rule one directory over. build_frontend runs `npm run build` as root
# with the cwd set here, and frontend/package.json's "build" script is
# `vite build`, which npm resolves to node_modules/.bin/vite - so Node loads the
# whole vite/rolldown/lightningcss graph, two compiled .node addons included,
# into a uid-0 process. A spanel-owned node_modules is therefore the same thing
# a spanel-owned .venv was: a file the confined account writes and root
# executes, which walks past every argv check in spanel-helper. Nothing running
# as spanel ever writes node_modules - only this script does - so root keeps it
# and the service reads it, which root-owned 0755 already allows.
chown_frontend_except_node_modules() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0
  find "$dir" -path "$dir/node_modules" -prune -o -exec chown -h spanel:spanel {} + 2>/dev/null || true
  # Reclaim it on an upgrade from a build that handed it to spanel.
  [[ -d "$dir/node_modules" ]] && chown -R root:root "$dir/node_modules" 2>/dev/null || true
  return 0
}

setup_backend() {
  cd "${APP_DIR}/backend"
  python3 -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt

  ADMIN_PASSWORD="${SPANEL_ADMIN_PASSWORD:-$(openssl rand -base64 24 | tr -d '\n')}"

  cat > .env <<ENV
APP_ENV=production
SECRET_KEY=$(openssl rand -hex 32)
COMMAND_DRY_RUN=false
DATABASE_URL=sqlite:///${APP_DIR}/backend/spanel.db
REDIS_URL=redis://localhost:6379/0
RATE_LIMIT_BACKEND=redis
ALLOWED_ORIGINS=${PANEL_URL}
BACKUP_ROOT=${BACKUP_ROOT}
SSL_EMAIL=${SSL_EMAIL}
PANEL_URL=${PANEL_URL}
PANEL_DOMAIN=${PANEL_DOMAIN}
PANEL_PORT=${PANEL_PORT}
PANEL_SSL_CERT=
PANEL_SSL_KEY=
FRONTEND_DIST=${APP_DIR}/frontend/dist
ENV

  # Lock down the env file: contains SECRET_KEY and ALLOWED_ORIGINS.
  chmod 0640 "${APP_DIR}/backend/.env"

  # Make panel files writable before seed creates the SQLite DB and admin Linux user.
  #
  # The virtualenv is deliberately NOT handed to spanel. update.sh runs as root
  # and does `source .venv/bin/activate`, `.venv/bin/pip` and `.venv/bin/python
  # -m py_compile` - so a spanel-owned venv is a file the confined account can
  # write and root then executes, which walks straight past every argv check in
  # spanel-helper. Root keeps it; the service only needs read+execute, and
  # root-owned 0755 gives that.
  chown_backend_except_venv "${APP_DIR}/backend"
  chown_frontend_except_node_modules "${APP_DIR}/frontend"

  sudo -u spanel env HOME="$APP_DIR" SPANEL_USE_HELPER=true SPANEL_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    "${APP_DIR}/backend/.venv/bin/python" -m app.seed
  deactivate || true
}

wait_for_backend() {
  for _ in {1..30}; do
    if curl -fsS --connect-timeout 2 --max-time 5 "http://127.0.0.1:${PANEL_PORT}/api/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  journalctl -u spanel-api -n 80 --no-pager || true
  fail "spanel-api did not respond at http://127.0.0.1:${PANEL_PORT}/api/health"
}

setup_systemd() {
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

  cat >/etc/systemd/system/spanel-api.service <<SERVICE
[Unit]
Description=SPanel API
After=network.target mariadb.service

[Service]
Type=exec
User=spanel
Group=spanel
SupplementaryGroups=www-data spanel-sites
WorkingDirectory=${APP_DIR}/backend
EnvironmentFile=${APP_DIR}/backend/.env
Environment=HOME=${APP_DIR}
Environment=SPANEL_USE_HELPER=true
ExecStart=/usr/local/sbin/spanel-api-start
Restart=always
RestartSec=3

# Hardening. These settings must not block the sudo helper; privileged work is
# restricted by /usr/local/sbin/spanel-helper and /etc/sudoers.d/spanel.
NoNewPrivileges=false
ProtectSystem=false
ProtectHome=false
ReadWritePaths=${APP_DIR} /home ${BACKUP_ROOT} /etc/nginx/conf.d /etc/nginx/spanel/custom /tmp /var/lib/spanel /home/admin/spanel_backups/da /var/lib/spanel/da-import /var/lib/spanel/import-stage
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
ProtectProc=invisible
# Mount namespaces only, and only because the panel cannot work without them.
# (No backticks anywhere in this block: the heredoc below is unquoted so it
# can carry ${APP_DIR}, which means backticks in prose would run as root.)
#
# This used to be true, which denies every namespace type to this process and
# to everything it starts. spanel-helper's terminal jail runs
# unshare --mount to give a tenant a /home containing only their own
# directory - the confinement the terminal escape was fixed with - and a
# sudo'd child inherits this restriction, so the unshare failed with EPERM.
#
# Everything the panel routes through terminal-exec broke with it: the file
# manager could list a directory (that is a plain filesystem read) but every
# read and every save returned 500. Found from a customer report, not from the
# test suite, because the jail had only ever been exercised from a root shell -
# the one context where this restriction does not apply.
#
# mnt permits exactly the namespace the jail needs and keeps the other seven
# denied.
RestrictNamespaces=mnt
RestrictRealtime=true
RestrictSUIDSGID=false
LockPersonality=true
MemoryDenyWriteExecute=false
SystemCallArchitectures=native
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
CapabilityBoundingSet=~

[Install]
WantedBy=multi-user.target
SERVICE

  install -d -o spanel -g spanel -m 0750 /var/lib/spanel /var/lib/spanel/geoip
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
ReadWritePaths=${APP_DIR} /home ${BACKUP_ROOT} /etc/nginx/conf.d /etc/nginx/spanel/custom /tmp /var/lib/spanel /home/admin/spanel_backups/da /var/lib/spanel/da-import /var/lib/spanel/import-stage
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
# The runner blocks until the scan it starts finishes (a whole-server scan can
# take hours). Without this, systemd's 90s default start timeout kills it and
# the scan lands in 'interrupted'.
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

  # Keep the clock honest. TOTP logins reject every code once it drifts past
  # ~30s, and many budget VPS hosts block outbound UDP 123 so systemd-timesyncd
  # never converges - spanel-helper time-sync then falls back to an HTTPS Date
  # header.
  timedatectl set-ntp true >/dev/null 2>&1 || true
  # Timezone is the operator's call - only touched when PANEL_TIMEZONE is set.
  if [[ -n "$PANEL_TIMEZONE" ]]; then
    if timedatectl set-timezone "$PANEL_TIMEZONE" >/dev/null 2>&1; then
      log "Server timezone set to ${PANEL_TIMEZONE}"
    else
      log "WARNING: PANEL_TIMEZONE='${PANEL_TIMEZONE}' is not a valid zone; timezone left unchanged"
    fi
  fi
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

  if [[ -f "${SCRIPT_DIR}/files/spanel-watchdog.service" && -f "${SCRIPT_DIR}/files/spanel-watchdog.timer" ]]; then
    install -m 0644 -o root -g root "${SCRIPT_DIR}/files/spanel-watchdog.service" /etc/systemd/system/spanel-watchdog.service
    sed -i "s#/opt/spanel#${APP_DIR}#g" /etc/systemd/system/spanel-watchdog.service
    install -m 0644 -o root -g root "${SCRIPT_DIR}/files/spanel-watchdog.timer" /etc/systemd/system/spanel-watchdog.timer
  fi
  if [[ -f "${SCRIPT_DIR}/files/spanel-maintenance.service" && -f "${SCRIPT_DIR}/files/spanel-maintenance.timer" ]]; then
    install -m 0644 -o root -g root "${SCRIPT_DIR}/files/spanel-maintenance.service" /etc/systemd/system/spanel-maintenance.service
    sed -i "s#/opt/spanel#${APP_DIR}#g" /etc/systemd/system/spanel-maintenance.service
    install -m 0644 -o root -g root "${SCRIPT_DIR}/files/spanel-maintenance.timer" /etc/systemd/system/spanel-maintenance.timer
  fi

  systemctl daemon-reload
  systemctl disable --now spanel-auto-update.timer 2>/dev/null || true
  rm -f /etc/systemd/system/spanel-auto-update.service /etc/systemd/system/spanel-auto-update.timer
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl enable --now spanel-api
  systemctl restart spanel-api || true
  systemctl enable --now spanel-backup-scheduler.timer
  systemctl enable --now spanel-malware-scheduler.timer
  systemctl enable --now spanel-notify.timer
  systemctl enable --now spanel-watchdog.timer >/dev/null 2>&1 || true
  systemctl enable --now spanel-maintenance.timer >/dev/null 2>&1 || true
  systemctl enable spanel-autotune.service >/dev/null 2>&1 || true
  systemctl start spanel-autotune.service >/dev/null 2>&1 || true
  systemctl enable spanel-timesync.timer >/dev/null 2>&1 || true
  if id -u spanel >/dev/null 2>&1; then
    # Start the clock unit only once the helper that answers `time-sync` is in
    # place, so it never flashes up as a failed unit mid-install.
    systemctl start spanel-timesync.timer >/dev/null 2>&1 || true
    systemctl start spanel-timesync.service >/dev/null 2>&1 || true
    sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper certbot-auto-renew-install >/dev/null 2>&1 || true
    sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper firewall-blocklist-timer-install >/dev/null 2>&1 || true
    # Certificates the panel can answer a handshake with, plus the renewal hook
    # that keeps them fresh.
    sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper panel-sni-sync >/dev/null 2>&1 || true
  fi
  wait_for_backend
}

write_tools_nginx_config() {
  local api_scheme="http" tools_scheme="http" pma_secure="false" ssl_block=""
  if [[ -n "${PANEL_SSL_CERT:-}" && -n "${PANEL_SSL_KEY:-}" && -f "${PANEL_SSL_CERT}" && -f "${PANEL_SSL_KEY}" ]]; then
    api_scheme="https"
    tools_scheme="https"
    pma_secure="true"
    printf -v ssl_block '\n    listen 443 ssl http2 default_server;\n    ssl_certificate %s;\n    ssl_certificate_key %s;' "$PANEL_SSL_CERT" "$PANEL_SSL_KEY"
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

    location = /phpmyadmin {
        return 301 /phpmyadmin/;
    }

    location /phpmyadmin/ {
        alias /usr/share/phpmyadmin/;
        index index.php;
        try_files \$uri \$uri/ =404;
    }

    location ~ ^/phpmyadmin/(.+\.php)$ {
        alias /usr/share/phpmyadmin/\$1;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME /usr/share/phpmyadmin/\$1;
        fastcgi_param SCRIPT_NAME /phpmyadmin/\$1;
        fastcgi_pass unix:/run/php/php${PHP_DEFAULT}-fpm.sock;
        fastcgi_read_timeout 300;
    }
}
NGINX

  local host
  host="${PANEL_DOMAIN:-$SERVER_IP}"
  [[ -n "$host" ]] || host="$(detect_server_ip)"
  sed -i -E "/api\/databases\/phpmyadmin-sso/s#'[^']+/api/databases/phpmyadmin-sso/'#'${api_scheme}://127.0.0.1:${PANEL_PORT}/api/databases/phpmyadmin-sso/'#" /usr/share/phpmyadmin/spanel-signon.php 2>/dev/null || true
  sed -i -E "s#('secure' => )(true|false)#\1${pma_secure}#" /etc/phpmyadmin/conf.d/spanel-signon.php /usr/share/phpmyadmin/spanel-signon.php /etc/phpmyadmin/conf.d/spanel-remote-servers.php 2>/dev/null || true
  sed -i -E "/PmaAbsoluteUri/s#'https?://[^']+/phpmyadmin/'#'${tools_scheme}://${host}/phpmyadmin/'#" /etc/phpmyadmin/conf.d/spanel-signon.php 2>/dev/null || true
}


setup_phpmyadmin_sso() {
  local blowfish_secret
  blowfish_secret="$(openssl rand -hex 32)"
  local pma_host pma_scheme pma_secure
  pma_host="${PANEL_DOMAIN:-$SERVER_IP}"
  [[ -n "$pma_host" ]] || pma_host="$(detect_server_ip)"
  pma_scheme="http"
  pma_secure="false"
  if [[ "$ENABLE_SSL" == "yes" ]]; then
    pma_scheme="https"
    pma_secure="true"
  fi

  cat >/etc/phpmyadmin/conf.d/spanel-signon.php <<PHP
<?php
\$cfg['blowfish_secret'] = '${blowfish_secret}';
\$i = 1;
\$cfg['Servers'][\$i]['auth_type'] = 'signon';
\$cfg['Servers'][\$i]['SignonSession'] = 'SPanelPmaSignon';
\$cfg['Servers'][\$i]['SignonCookieParams'] = [
    'lifetime' => 0,
    'path' => '/',
    'domain' => '',
    'secure' => ${pma_secure},
    'httponly' => true,
    'samesite' => 'Lax',
];
\$cfg['Servers'][\$i]['SignonURL'] = '/phpmyadmin/spanel-signon.php';
\$cfg['Servers'][\$i]['host'] = 'localhost';
\$cfg['Servers'][\$i]['AllowNoPassword'] = false;
\$cfg['Servers'][\$i]['only_db'] = '';
\$cfg['SessionSavePath'] = '/var/lib/php/sessions';
\$cfg['PmaAbsoluteUri'] = '${pma_scheme}://${pma_host}/phpmyadmin/';
PHP

  # Picks the phpMyAdmin server (local MariaDB or a remote MySQL host) from
  # the SSO token; update.sh installs the same file.
  install -m 0644 -o root -g root "${SCRIPT_DIR}/files/phpmyadmin-spanel-signon.php" /usr/share/phpmyadmin/spanel-signon.php

  local api_scheme="http"
  if [[ "$ENABLE_SSL" == "yes" ]]; then
    api_scheme="https"
  fi
  sed -i "s#__SPANEL_API_BASE__#${api_scheme}://127.0.0.1:${PANEL_PORT}/api/databases/phpmyadmin-sso/#" /usr/share/phpmyadmin/spanel-signon.php
  sed -i "s#__SPANEL_PMA_COOKIE_SECURE__#${pma_secure}#" /usr/share/phpmyadmin/spanel-signon.php

  chown root:www-data /etc/phpmyadmin/conf.d/spanel-signon.php
  chmod 640 /etc/phpmyadmin/conf.d/spanel-signon.php
  chmod 644 /usr/share/phpmyadmin/spanel-signon.php
}

setup_nginx() {
  rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf 2>/dev/null || true
  rm -f /etc/nginx/sites-enabled/spanel.conf /etc/nginx/sites-available/spanel.conf 2>/dev/null || true
  write_tools_nginx_config
  nginx -t
  systemctl reload nginx
}

setup_firewall() {
  # IP filtering is iptables + ipset, driven by spanel-helper. Any UFW install
  # left over from an older SPanel release is removed here.
  local ssh_port helper="/usr/local/sbin/spanel-helper"
  set +e
  # sshd -T (used by the helper) misses ports only visible from the live SSH
  # session or a non-default sshd_config, so seed those explicitly first.
  while read -r ssh_port; do
    [[ "$ssh_port" =~ ^[0-9]{1,5}$ ]] || continue
    sudo -u spanel env HOME="$APP_DIR" sudo -n "$helper" firewall-allow-port "$ssh_port" tcp >/dev/null 2>&1
  done < <(detect_ssh_ports || true)
  sudo -u spanel env HOME="$APP_DIR" sudo -n "$helper" firewall-migrate
  # A fresh install always enforces; firewall-migrate only inherits the
  # previous state when upgrading an existing box.
  sudo -u spanel env HOME="$APP_DIR" sudo -n "$helper" firewall-enable
  set -e
  return 0
}

setup_selfsigned_ssl() {
  # No domain, or Let's Encrypt declined. The panel still takes an admin
  # password, so it gets a certificate of its own rather than answering in the
  # clear: the browser warns once, which is one warning more than plain HTTP
  # gives anybody.
  local host san
  host="${PANEL_DOMAIN:-${SERVER_IP:-127.0.0.1}}"
  san="DNS:${host}"
  [[ "$host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && san="IP:${host}"
  [[ -n "${SERVER_IP:-}" && "$SERVER_IP" != "$host" ]] && san="${san},IP:${SERVER_IP}"
  install -d -o root -g spanel -m 0750 /etc/spanel
  if ! openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
      -keyout /etc/spanel/panel-selfsigned-privkey.pem \
      -out /etc/spanel/panel-selfsigned-fullchain.pem \
      -subj "/CN=${host}" -addext "subjectAltName=${san}" >/dev/null 2>&1; then
    echo "WARNING: could not generate a self-signed certificate; the panel stays on HTTP" >&2
    return 0
  fi
  chown root:spanel /etc/spanel/panel-selfsigned-fullchain.pem /etc/spanel/panel-selfsigned-privkey.pem
  chmod 0640 /etc/spanel/panel-selfsigned-fullchain.pem /etc/spanel/panel-selfsigned-privkey.pem
  PANEL_SSL_CERT=/etc/spanel/panel-selfsigned-fullchain.pem
  PANEL_SSL_KEY=/etc/spanel/panel-selfsigned-privkey.pem
  PANEL_URL="https://${host}:${PANEL_PORT}"
  sed -i \
    -e "s#^PANEL_SSL_CERT=.*#PANEL_SSL_CERT=${PANEL_SSL_CERT}#" \
    -e "s#^PANEL_SSL_KEY=.*#PANEL_SSL_KEY=${PANEL_SSL_KEY}#" \
    -e "s#^PANEL_URL=.*#PANEL_URL=${PANEL_URL}#" \
    -e "s#^ALLOWED_ORIGINS=.*#ALLOWED_ORIGINS=${PANEL_URL}#" \
    "${APP_DIR}/backend/.env"
  grep -q "^PANEL_SSL_MODE=" "${APP_DIR}/backend/.env" \
    && sed -i "s#^PANEL_SSL_MODE=.*#PANEL_SSL_MODE=selfsigned#" "${APP_DIR}/backend/.env" \
    || echo "PANEL_SSL_MODE=selfsigned" >>"${APP_DIR}/backend/.env"
  write_tools_nginx_config
  nginx -t && systemctl reload nginx
  systemctl restart spanel-api
  for _ in {1..20}; do
    if curl -kfsS --connect-timeout 2 --max-time 5 "https://127.0.0.1:${PANEL_PORT}/api/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  # Better a panel that answers than a locked-out operator: put it back and say so.
  echo "WARNING: the panel did not come up over HTTPS; reverting to HTTP" >&2
  PANEL_URL="http://${host}:${PANEL_PORT}"
  PANEL_SSL_CERT=""
  PANEL_SSL_KEY=""
  sed -i \
    -e "s#^PANEL_SSL_CERT=.*#PANEL_SSL_CERT=#" \
    -e "s#^PANEL_SSL_KEY=.*#PANEL_SSL_KEY=#" \
    -e "s#^PANEL_SSL_MODE=.*#PANEL_SSL_MODE=#" \
    -e "s#^PANEL_URL=.*#PANEL_URL=${PANEL_URL}#" \
    -e "s#^ALLOWED_ORIGINS=.*#ALLOWED_ORIGINS=${PANEL_URL}#" \
    "${APP_DIR}/backend/.env"
  systemctl restart spanel-api
}

setup_ssl() {
  if [[ "$ENABLE_SSL" != "yes" ]]; then
    setup_selfsigned_ssl
    return 0
  fi

  certbot certonly --standalone \
    -d "$PANEL_DOMAIN" \
    --email "$SSL_EMAIL" \
    --agree-tos \
    --non-interactive \
    --pre-hook "systemctl stop nginx || true" \
    --post-hook "systemctl start nginx || true" \
    --deploy-hook "install -d -o root -g spanel -m 0750 /etc/spanel && install -m 0640 -o root -g spanel /etc/letsencrypt/live/${PANEL_DOMAIN}/fullchain.pem /etc/spanel/panel-fullchain.pem && install -m 0640 -o root -g spanel /etc/letsencrypt/live/${PANEL_DOMAIN}/privkey.pem /etc/spanel/panel-privkey.pem && systemctl restart spanel-api || true"
  install -d -o root -g spanel -m 0750 /etc/spanel
  install -m 0640 -o root -g spanel "/etc/letsencrypt/live/${PANEL_DOMAIN}/fullchain.pem" /etc/spanel/panel-fullchain.pem
  install -m 0640 -o root -g spanel "/etc/letsencrypt/live/${PANEL_DOMAIN}/privkey.pem" /etc/spanel/panel-privkey.pem
  PANEL_SSL_CERT=/etc/spanel/panel-fullchain.pem
  PANEL_SSL_KEY=/etc/spanel/panel-privkey.pem
  sed -i \
    -e "s#^PANEL_SSL_CERT=.*#PANEL_SSL_CERT=/etc/spanel/panel-fullchain.pem#" \
    -e "s#^PANEL_SSL_KEY=.*#PANEL_SSL_KEY=/etc/spanel/panel-privkey.pem#" \
    -e "s#^PANEL_URL=.*#PANEL_URL=${PANEL_URL}#" \
    -e "s#^ALLOWED_ORIGINS=.*#ALLOWED_ORIGINS=${PANEL_URL}#" \
    "${APP_DIR}/backend/.env"
  # Say which certificate this is. Left unrecorded, the first update took the
  # panel's own Let's Encrypt certificate for one it had not adopted yet,
  # copied it over itself and announced a switch to a self-signed one.
  grep -q "^PANEL_SSL_MODE=" "${APP_DIR}/backend/.env" \
    && sed -i "s#^PANEL_SSL_MODE=.*#PANEL_SSL_MODE=letsencrypt#" "${APP_DIR}/backend/.env" \
    || echo "PANEL_SSL_MODE=letsencrypt" >>"${APP_DIR}/backend/.env"
  write_tools_nginx_config
  nginx -t
  systemctl reload nginx
  systemctl restart spanel-api
  for _ in {1..20}; do
    if curl -kfsS --connect-timeout 2 --max-time 5 "https://127.0.0.1:${PANEL_PORT}/api/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  journalctl -u spanel-api -n 80 --no-pager || true
  fail "spanel-api did not respond after enabling panel SSL"
}

print_summary() {
  echo ""
  echo "=================================================="
  echo "Panel URL: ${PANEL_URL}"
  echo "User: admin"
  echo "Password: ${ADMIN_PASSWORD}"
  case "$IPV6_RESULT" in
    on:*) echo "IPv6: enabled on ${IPV6_RESULT#on:}" ;;
    failed) echo "IPv6: detected but not enabled - turn it on in Panel settings" ;;
    *) echo "IPv6: not available on this server" ;;
  esac
  echo "=================================================="
}

write_login_info() {
  local tmp
  (
    if command -v flock >/dev/null 2>&1; then
      flock -x 9
    fi
    tmp="$(mktemp /root/login.txt.XXXXXX)"
    chmod 600 "$tmp"
    cat >"$tmp" <<INFO
Panel URL: ${PANEL_URL}
User: admin
Password: ${ADMIN_PASSWORD}
INFO
    mv -f "$tmp" /root/login.txt
  ) 9>/root/.spanel-login.lock
  chmod 600 /root/login.txt
}

source_version() {
  if [[ -f "${PROJECT_ROOT}/VERSION" ]]; then
    tr -d '[:space:]' <"${PROJECT_ROOT}/VERSION"
    return 0
  fi
  sed -nE 's/^APP_VERSION = "([^"]+)"/\1/p' "${PROJECT_ROOT}/backend/app/core/version.py" 2>/dev/null | head -n 1
}

write_update_state() {
  local version now
  version="$(source_version)"
  version="${version:-1.0.8}"
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  install -d -o spanel -g spanel -m 0750 /var/lib/spanel /var/lib/spanel/geoip
  cat >/var/lib/spanel/update-status.json <<STATE
{
  "current_version": "${version}",
  "latest_tag": "v${version}",
  "latest_version": "${version}",
  "last_checked_at": "${now}",
  "last_update_finished_at": "${now}",
  "last_update_ref": "v${version}",
  "last_update_status": "installed"
}
STATE
  chown spanel:spanel /var/lib/spanel/update-status.json 2>/dev/null || true
  chmod 0640 /var/lib/spanel/update-status.json
}

cleanup_release_source() {
  [[ "${CLEAN_RELEASE_SOURCE:-true}" == "true" ]] || return 0
  [[ "$PROJECT_ROOT" == "/opt/spanel-source" ]] || return 0
  [[ ! -d "${PROJECT_ROOT}/.git" ]] || return 0
  log "Removing release source from ${PROJECT_ROOT}"
  cd /
  rm -rf "$PROJECT_ROOT" /tmp/spanel-release /tmp/spanel-release.zip
}

enable_ipv6_when_available() {
  # A fresh install takes the network as it finds it: a machine that already
  # has a global IPv6 address should serve on it without somebody having to go
  # and find the switch. Servers that update into this are left alone - their
  # admin decides.
  #
  # This cannot conjure an address the provider assigned but never configured;
  # only the ones the machine actually holds are detected.
  id -u spanel >/dev/null 2>&1 || return 0
  local status
  status="$(sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper ipv6-status 2>/dev/null || true)"
  if [[ "$status" != *"available=yes"* ]]; then
    log "No global IPv6 address on this server; IPv6 stays off"
    IPV6_RESULT="off"
    return 0
  fi
  local address
  address="$(printf '%s\n' "$status" | sed -n 's/^addresses=//p' | head -n1)"
  if sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper ipv6-enable >/dev/null 2>&1; then
    log "IPv6 detected (${address}); websites and panel will answer on it too"
    IPV6_RESULT="on:${address}"
  else
    log "WARNING: IPv6 was detected but could not be enabled; turn it on from Panel settings"
    IPV6_RESULT="failed"
  fi
}

configure_log_limits() {
  # systemd-journald ships with no size limit: it falls back to 10% of the
  # filesystem, which on a 72G disk is 7.2G. Measured on a live server, the
  # journal had reached 2.7G - 53 times the size of every nginx log put
  # together - fed mostly by SSH password-guessing hitting sshd thousands of
  # times an hour. nginx's own logs were never the problem; they rotate daily,
  # keep 14 days and compress, and totalled 51M.
  #
  # A drop-in rather than an edit of journald.conf, so a distribution upgrade
  # cannot quietly revert it.
  mkdir -p /etc/systemd/journald.conf.d
  cat >/etc/systemd/journald.conf.d/99-spanel-size.conf <<'JOURNALD'
# Managed by SPanel.
[Journal]
SystemMaxUse=500M
SystemKeepFree=1G
MaxRetentionSec=2week
JOURNALD
  systemctl restart systemd-journald 2>/dev/null || true
  journalctl --vacuum-size=500M >/dev/null 2>&1 || true

  # btmp records every failed login and Ubuntu ships no rule for it. On the
  # same server it had grown to 130M across two files, holding 62,000 failed
  # SSH attempts. `su root root` is required because /var/log is root:syslog
  # and group-writable, and logrotate refuses to act on a file in a directory
  # it considers unsafe unless told whose identity to use.
  cat >/etc/logrotate.d/btmp <<'BTMP'
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
  chmod 644 /etc/logrotate.d/btmp
}

main() {
  validate_sources
  ask_panel_url

  log "Installing base packages"
  install_base_packages

  log "Installing Node.js ${NODE_MAJOR} from NodeSource"
  install_nodejs

  log "Installing PHP ${PHP_VERSIONS} from Ondrej PPA"
  install_php

  log "Configuring Nginx FastCGI cache"
  configure_fastcgi_cache
  configure_proxy_upgrade_map

  log "Installing Nginx ModSecurity WAF engine"
  if ! install_waf_engine; then
    echo "WARNING: WAF engine installation failed; continuing without ModSecurity."
  fi

  log "Installing WP-CLI and Composer"
  install_wp_cli
  install_composer_cli

  log "Copying source to ${APP_DIR}"
  copy_sources

  log "Building frontend"
  build_frontend

  log "Creating spanel system user, MariaDB credentials and filesystem ACLs"
  setup_panel_user

  log "Configuring SFTP access for panel users"
  setup_sftp_access

  log "Installing privileged helper and sudoers rule"
  install_privileged_helper

  log "Installing SSH maintenance menu"
  install_panel_cli

  log "Validating privileged helper"
  validate_privileged_helper

  log "Configuring backend"
  setup_backend

  log "Creating systemd service (hardened, runs as spanel user)"
  setup_systemd

  log "Configuring phpMyAdmin SSO"
  setup_phpmyadmin_sso

  log "Preparing Nginx for customer websites"
  setup_nginx

  log "Configuring firewall"
  setup_firewall

  log "Capping log growth (journald + btmp)"
  configure_log_limits

  # Swap, a ceiling on clamd and an OOM preference for nginx. Must come after
  # install_privileged_helper, which is what puts spanel-memory-guard on disk -
  # calling it earlier silently did nothing, hidden by the `|| true`.
  log "Configuring memory limits (swap, clamd ceiling, OOM preference)"
  /usr/local/sbin/spanel-memory-guard || true
  # clamd's stock limits let a padded upload past the scanner: anything over
  # MaxFileSize comes back OK without being read. Same lesson as the line
  # above, one mechanism further on: root cannot call the helper directly -
  # it refuses anything that is not sudo from 'spanel' - and `|| true` would
  # have swallowed the refusal without a word. On a fresh machine ClamAV is
  # usually not installed yet and install_clamav_engine tunes it when it is.
  if [[ -f /etc/clamav/clamd.conf ]]; then
    sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper clamav-tune >/dev/null ||       log "  (warning: could not raise clamd's scan limits; large uploads stay unscanned)"
  fi

  if [[ "$SPANEL_DISK_QUOTA" == "kernel" ]]; then
    log "Enabling kernel disk quota (SPANEL_DISK_QUOTA=kernel)"
    sudo -u spanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/spanel-helper quota-enable | sed -n -E 's/^(state|hint|backup)=/  \1: /p' || \
      log "  (warning: could not enable kernel disk quota; the panel's own accounting still applies)"
  fi

  log "Configuring SSL"
  setup_ssl

  log "Checking for IPv6"
  enable_ipv6_when_available

  write_login_info
  write_update_state

  print_summary
  cleanup_release_source
}

main "$@"
