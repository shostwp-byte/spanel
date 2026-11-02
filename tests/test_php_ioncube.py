"""ionCube Loader integration tests across backend service and spanel-helper.sh."""
from __future__ import annotations

import io
import re
import subprocess
import sys
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from _isolation_bash import BASH, REPO_ROOT, helper_assignment, helper_functions, run_bash

sys.path.insert(0, str(REPO_ROOT / "backend"))

needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")

IONCUBE_HELPER_FUNCTIONS = (
    "require_php_version",
    "user_php_fpm_unit",
    "reload_user_php_fpm_masters",
    "ioncube_resolve_arch",
    "resolve_php_cli_bin",
    "resolve_php_fpm_bin",
    "neutralize_php_tune_jit_for_ioncube",
    "ensure_ioncube_archive",
    "install_ioncube_loader",
    "ioncube_is_healthy",
    "sync_ioncube_loaders",
    "install_php_extension",
    "uninstall_php_version",
)


def _build_fake_ioncube_tarball(dest: Path, versions: tuple[str, ...] = ("5.6", "7.4", "8.1", "8.2", "8.3", "8.4", "8.5")) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for ver in versions:
            for suffix in ("", "_ts"):
                payload = f"ELF-FAKE-IONCUBE-{ver}{suffix}\n".encode("utf-8")
                info = tarfile.TarInfo(name=f"ioncube/ioncube_loader_lin_{ver}{suffix}.so")
                info.size = len(payload)
                info.mode = 0o644
                tf.addfile(info, io.BytesIO(payload))
    dest.write_bytes(buf.getvalue())


def _ioncube_bash_script(root: Path, body: str) -> str:
    preamble = "\n".join([
        "set -euo pipefail",
        f"ROOT='{root.as_posix()}'",
        'deny() { echo "deny: $*" >&2; exit 1; }',
        'SPANEL_PHP_FPM_ROOT="$ROOT/etc/spanel/php-fpm"',
        'SPANEL_PHP_RUN_ROOT="$ROOT/run/spanel-php"',
        'SYSTEMD_UNIT_DIR="$ROOT/systemd"',
        'PHP_ETC_ROOT="$ROOT/etc/php"',
        'PHP_CLI_BIN_DIR="$ROOT/usr/bin"',
        'PHP_FPM_SBIN_DIR="$ROOT/usr/sbin"',
        'IONCUBE_TARGET_DIR="$ROOT/usr/local/ioncube"',
        'IONCUBE_CACHE_DIR="$ROOT/var/cache/spanel/ioncube"',
        'SPANEL_HOST_ARCH="${SPANEL_HOST_ARCH:-x86_64}"',
        helper_assignment("PHP_EXTENSION_WHITELIST"),
        helper_assignment("IONCUBE_SUPPORTED_VERSIONS"),
        'mkdir -p "$SYSTEMD_UNIT_DIR" "$PHP_CLI_BIN_DIR" "$PHP_FPM_SBIN_DIR" "$ROOT/logs"',
        'touch "$ROOT/logs/systemctl.log" "$ROOT/logs/curl.log" "$ROOT/active"',
    ])
    stubs = r'''
if ! command -v id >/dev/null 2>&1; then id() { echo 1000; }; fi
if ! command -v chmod >/dev/null 2>&1; then chmod() { :; }; fi
if ! command -v install >/dev/null 2>&1; then
  install() {
    if [[ "${1:-}" == "-d" ]]; then mkdir -p "${@: -1}"; else cp "${@: -2:1}" "${@: -1}"; fi
  }
fi
systemctl() {
  echo "$*" >>"$ROOT/logs/systemctl.log"
  local unit="${*: -1}"
  case "$1" in
    is-active) grep -qxF "$unit" "$ROOT/active" ;;
    *) return 0 ;;
  esac
}
curl() {
  local out="" prev="" arg url=""
  for arg in "$@"; do
    if [[ "$prev" == "-o" ]]; then out="$arg"; fi
    if [[ "$arg" == https://* ]]; then url="$arg"; fi
    prev="$arg"
  done
  echo "$url" >>"$ROOT/logs/curl.log"
  [[ "${FAKE_CURL_FAIL:-0}" == "1" ]] && return 22
  cp "$ROOT/fake_ioncube.tar.gz" "$out"
}
dpkg-query() { :; }
dpkg() {
  if [[ "${1:-}" == "--print-architecture" ]]; then echo "$SPANEL_HOST_ARCH"; return 0; fi
  return 1
}
apt-get() { echo "apt-get $*" >>"$ROOT/logs/apt.log"; }
setup_fake_php() {
  local ver="$1"
  mkdir -p "$PHP_ETC_ROOT/$ver/cli/conf.d" "$PHP_ETC_ROOT/$ver/fpm/conf.d" "$PHP_ETC_ROOT/$ver/fpm/pool.d"
  printf '[global]\n' >"$PHP_ETC_ROOT/$ver/fpm/php-fpm.conf"
  echo "php${ver}-fpm" >>"$ROOT/active"
  cat >"$PHP_CLI_BIN_DIR/php${ver}" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "-n" ] && [ "${2:-}" = "-r" ]; then
  printf '%s' "${FAKE_PHP_ZTS:-0}"
  exit 0
fi
if [ "${1:-}" = "-v" ]; then
  if [ "${FAKE_CLI_FAIL:-0}" = "1" ]; then
    echo "PHP Warning: Failed loading Zend extension (cannot open shared object file)" >&2
    echo "PHP 8.x (cli)"
    exit 0
  fi
  scan="${PHP_INI_SCAN_DIR:-}"
  if [ -n "$scan" ] && [ -f "$scan/00-ioncube.ini" ]; then
    echo "PHP ${0##*php} (cli)"
    echo "    with the ionCube PHP Loader v15.0.0, Copyright (c) 2002-2026, by ionCube Ltd."
  else
    echo "PHP ${0##*php} (cli)"
  fi
  exit 0
fi
exit 0
EOF
  chmod +x "$PHP_CLI_BIN_DIR/php${ver}"
  cat >"$PHP_FPM_SBIN_DIR/php-fpm${ver}" <<'EOF'
#!/bin/sh
if [ "${FAKE_FPM_TEST_FAIL:-0}" = "1" ]; then
  echo "ERROR: FPM configuration test failed" >&2
  exit 78
fi
exit 0
EOF
  chmod +x "$PHP_FPM_SBIN_DIR/php-fpm${ver}"
}
'''
    return "\n".join([preamble, helper_functions(*IONCUBE_HELPER_FUNCTIONS), stubs, body, ""])


def test_whitelist_in_sync_between_backend_and_helper():
    from app.services import php

    line = helper_assignment("PHP_EXTENSION_WHITELIST")
    match = re.match(r"^PHP_EXTENSION_WHITELIST=\(([^)]+)\)$", line.strip())
    assert match, f"unexpected whitelist format: {line}"
    helper_exts = tuple(match.group(1).split())
    assert php.PHP_EXTENSIONS == helper_exts
    assert "ioncube" in php.PHP_EXTENSIONS


def test_list_extensions_ioncube_matrix_states(tmp_path, monkeypatch):
    from app.services import php

    php_etc = tmp_path / "etc" / "php"
    ioncube_dir = tmp_path / "usr" / "local" / "ioncube"
    ioncube_dir.mkdir(parents=True)

    # 8.4: fully installed (CLI module loaded + both cli/fpm 00-ioncube.ini + non-empty .so)
    so_84 = ioncube_dir / "ioncube_loader_lin_8.4.so"
    so_84.write_bytes(b"ELF-84")
    for sapi in ("cli", "fpm"):
        conf_d = php_etc / "8.4" / sapi / "conf.d"
        conf_d.mkdir(parents=True)
        (conf_d / "00-ioncube.ini").write_text(f"zend_extension={so_84}\n", encoding="utf-8")

    # 8.3: partial (CLI loaded, cli ini present, FPM ini missing -> must report 'available' so + repairs FPM)
    so_83 = ioncube_dir / "ioncube_loader_lin_8.3.so"
    so_83.write_bytes(b"ELF-83")
    cli_83 = php_etc / "8.3" / "cli" / "conf.d"
    cli_83.mkdir(parents=True)
    (cli_83 / "00-ioncube.ini").write_text(f"zend_extension={so_83}\n", encoding="utf-8")
    (php_etc / "8.3" / "fpm" / "conf.d").mkdir(parents=True)

    # 8.2: ini files exist but .so file is missing -> must report 'available', not 'installed'
    for sapi in ("cli", "fpm"):
        conf_d = php_etc / "8.2" / sapi / "conf.d"
        conf_d.mkdir(parents=True)
        (conf_d / "00-ioncube.ini").write_text(f"zend_extension={ioncube_dir / 'ioncube_loader_lin_8.2.so'}\n", encoding="utf-8")

    monkeypatch.setattr(php, "PHP_ETC_DIR", php_etc)
    monkeypatch.setattr(php, "IONCUBE_DIR", ioncube_dir)
    monkeypatch.setattr(php, "list_installed_php", lambda: ["5.6", "7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5"])

    def fake_cmd(args: list[str]) -> str:
        if args[0] == "php8.4":
            return "[PHP Modules]\nthe ionCube PHP Loader + ionCube24\n"
        if args[0] == "php8.3":
            return "[PHP Modules]\nthe ionCube PHP Loader\n"
        return ""

    monkeypatch.setattr(php, "_command_output", fake_cmd)

    for arch in ("x86_64", "aarch64"):
        monkeypatch.setattr(php, "_host_arch", lambda a=arch: a)
        data = php.list_extensions()
        ioncube_row = next(r for r in data["extensions"] if r["name"] == "ioncube")
        assert ioncube_row["versions"] == {
            "5.6": "available",
            "7.4": "available",
            "8.0": "unavailable",
            "8.1": "available",
            "8.2": "available",
            "8.3": "available",
            "8.4": "installed",
            "8.5": "available",
        }

    # Unsupported CPU architecture -> non-loaded versions report unavailable
    monkeypatch.setattr(php, "_host_arch", lambda: "riscv64")
    data_riscv = php.list_extensions()
    ioncube_riscv = next(r for r in data_riscv["extensions"] if r["name"] == "ioncube")
    assert ioncube_riscv["versions"]["8.4"] == "installed"
    assert ioncube_riscv["versions"]["8.3"] == "unavailable"
    assert ioncube_riscv["versions"]["8.5"] == "unavailable"


def test_install_extension_ioncube_validation_and_dispatch(monkeypatch):
    from app.services import php

    monkeypatch.setattr(php, "list_installed_php", lambda: ["8.0", "8.4"])
    monkeypatch.setattr(php, "_host_arch", lambda: "x86_64")

    with pytest.raises(ValueError, match="8.0"):
        php.install_extension("8.0", "ioncube")

    captured = {}

    def fake_privileged(subcommand, *, helper_args=(), fallback=None, **kwargs):
        captured["subcommand"] = subcommand
        captured["helper_args"] = list(helper_args)
        captured["fallback"] = list(fallback or [])
        return subprocess.CompletedProcess(["spanel-helper"], 0, "ionCube Loader is ready", "")

    with patch.object(php.shell, "privileged", side_effect=fake_privileged):
        out = php.install_extension("8.4", "ioncube")

    assert out == "ionCube Loader is ready"
    assert captured["subcommand"] == "php-ext-install"
    assert captured["helper_args"] == ["8.4", "ioncube"]
    assert "apt-get" not in " ".join(captured["fallback"])

    monkeypatch.setattr(php, "_host_arch", lambda: "s390x")
    with pytest.raises(ValueError, match="architecture"):
        php.install_extension("8.4", "ioncube")


@needs_bash
def test_helper_php_ext_install_ioncube_writes_ini_neutralizes_jit_and_reloads_user_fpm(tmp_path):
    _build_fake_ioncube_tarball(tmp_path / "fake_ioncube.tar.gz")
    script = _ioncube_bash_script(tmp_path, r'''
setup_fake_php 8.4
# Legacy duplicate ini that would cause "already loaded" warning if kept
printf 'zend_extension=/old/ioncube.so\n' >"$PHP_ETC_ROOT/8.4/fpm/conf.d/20-ioncube.ini"
printf 'zend_extension=/old/ioncube.so\n' >"$PHP_ETC_ROOT/8.4/cli/conf.d/20-ioncube.ini"
# Pre-existing Auto Tune config with active JIT (incompatible with ionCube)
for d in "$PHP_ETC_ROOT/8.4/fpm/conf.d" "$PHP_ETC_ROOT/8.4/cli/conf.d"; do
  cat >"$d/95-spanel-tune.ini" <<'INI'
memory_limit = 1024M
opcache.jit = tracing
opcache.jit_buffer_size = 64M
INI
done
# Isolated per-account FPM master for tenant alice
mkdir -p "$SPANEL_PHP_FPM_ROOT/8.4/alice/pool.d"
printf '[global]\n' >"$SPANEL_PHP_FPM_ROOT/8.4/alice/php-fpm.conf"
echo "spanel-php8.4-fpm@alice.service" >>"$ROOT/active"

install_php_extension 8.4 ioncube
''')
    res = run_bash(tmp_path, script)
    assert "ionCube Loader is ready" in res.stdout

    so_file = tmp_path / "usr/local/ioncube/ioncube_loader_lin_8.4.so"
    assert so_file.is_file()

    for sapi in ("cli", "fpm"):
        conf_d = tmp_path / "etc/php/8.4" / sapi / "conf.d"
        assert (conf_d / "00-ioncube.ini").is_file()
        assert not (conf_d / "20-ioncube.ini").exists()
        tune_text = (conf_d / "95-spanel-tune.ini").read_text(encoding="utf-8")
        assert "memory_limit = 1024M" in tune_text
        assert "opcache.jit = off" in tune_text
        assert "opcache.jit_buffer_size = 0" in tune_text

    systemctl_calls = (tmp_path / "logs/systemctl.log").read_text(encoding="utf-8")
    assert "reload php8.4-fpm" in systemctl_calls
    assert "reload spanel-php8.4-fpm@alice.service" in systemctl_calls


@needs_bash
def test_helper_ioncube_caches_archive_across_all_versions_and_supports_arm64(tmp_path):
    _build_fake_ioncube_tarball(tmp_path / "fake_ioncube.tar.gz")
    script = _ioncube_bash_script(tmp_path, r'''
export SPANEL_HOST_ARCH=aarch64
for v in 5.6 7.4 8.1 8.2 8.3 8.4 8.5; do
  setup_fake_php "$v"
  install_php_extension "$v" ioncube
done
''')
    run_bash(tmp_path, script)
    curl_calls = [line for line in (tmp_path / "logs/curl.log").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(curl_calls) == 1
    assert "ioncube_loaders_lin_aarch64.tar.gz" in curl_calls[0]
    for v in ("5.6", "7.4", "8.1", "8.2", "8.3", "8.4", "8.5"):
        assert (tmp_path / f"etc/php/{v}/fpm/conf.d/00-ioncube.ini").is_file()


@needs_bash
def test_helper_ioncube_php80_strict_vs_best_effort_and_rollback_on_failure(tmp_path):
    _build_fake_ioncube_tarball(tmp_path / "fake_ioncube.tar.gz")
    # 1) PHP 8.0 strict fails cleanly without downloading; best_effort skips cleanly
    res_strict = run_bash(
        tmp_path,
        _ioncube_bash_script(tmp_path, "setup_fake_php 8.0\ninstall_php_extension 8.0 ioncube\n"),
        check=False,
    )
    assert res_strict.returncode != 0
    assert "8.0" in res_strict.stderr

    res_best = run_bash(
        tmp_path,
        _ioncube_bash_script(tmp_path, "setup_fake_php 8.0\ninstall_ioncube_loader 8.0 best_effort\n"),
    )
    assert "Skipping ionCube Loader for PHP 8.0" in res_best.stdout
    assert (tmp_path / "logs/curl.log").read_text(encoding="utf-8").strip() == ""

    # 2) Rollback if php -v fails validation
    res_cli_fail = run_bash(
        tmp_path,
        _ioncube_bash_script(tmp_path, "setup_fake_php 8.4\nFAKE_CLI_FAIL=1 install_php_extension 8.4 ioncube\n"),
        check=False,
    )
    assert res_cli_fail.returncode != 0
    assert not (tmp_path / "etc/php/8.4/fpm/conf.d/00-ioncube.ini").exists()
    assert not (tmp_path / "etc/php/8.4/cli/conf.d/00-ioncube.ini").exists()

    # 3) Rollback if php-fpm -t fails validation
    res_fpm_fail = run_bash(
        tmp_path,
        _ioncube_bash_script(tmp_path, "setup_fake_php 8.4\nFAKE_FPM_TEST_FAIL=1 install_php_extension 8.4 ioncube\n"),
        check=False,
    )
    assert res_fpm_fail.returncode != 0
    assert not (tmp_path / "etc/php/8.4/fpm/conf.d/00-ioncube.ini").exists()
    assert not (tmp_path / "etc/php/8.4/cli/conf.d/00-ioncube.ini").exists()


@needs_bash
def test_helper_sync_ioncube_loaders_and_uninstall_cleanup(tmp_path):
    _build_fake_ioncube_tarball(tmp_path / "fake_ioncube.tar.gz")
    script = _ioncube_bash_script(tmp_path, r'''
setup_fake_php 8.0
setup_fake_php 8.3
setup_fake_php 8.4
install_php_extension 8.4 ioncube
: >"$ROOT/logs/curl.log"
: >"$ROOT/logs/systemctl.log"
sync_ioncube_loaders
''')
    res1 = run_bash(tmp_path, script)
    assert "1 enabled/repaired, 1 already healthy" in res1.stdout
    assert (tmp_path / "etc/php/8.3/fpm/conf.d/00-ioncube.ini").is_file()
    assert not (tmp_path / "etc/php/8.0/fpm/conf.d/00-ioncube.ini").exists()

    # Second sync when everything is healthy makes zero network or reload calls
    res2 = run_bash(
        tmp_path,
        _ioncube_bash_script(tmp_path, ": >\"$ROOT/logs/curl.log\"\n: >\"$ROOT/logs/systemctl.log\"\nsync_ioncube_loaders\n"),
    )
    assert "0 enabled/repaired, 2 already healthy" in res2.stdout
    assert (tmp_path / "logs/curl.log").read_text(encoding="utf-8").strip() == ""
    assert (tmp_path / "logs/systemctl.log").read_text(encoding="utf-8").strip() == ""

    # Uninstall PHP 8.3 removes 8.3 .so binary while keeping 8.4 .so intact
    run_bash(tmp_path, _ioncube_bash_script(tmp_path, "uninstall_php_version 8.3\n"))
    assert not (tmp_path / "usr/local/ioncube/ioncube_loader_lin_8.3.so").exists()
    assert (tmp_path / "usr/local/ioncube/ioncube_loader_lin_8.4.so").is_file()
