"""Regressions found while reviewing the 1.0.7 release candidate.

Each test names the fault it guards against, so a failure says what broke.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

HELPER = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"
UPDATE_SH = REPO_ROOT / "installer" / "update.sh"
INSTALL_SH = REPO_ROOT / "installer" / "install.sh"


def _helper_section(start: str, end: str) -> str:
    text = HELPER.read_text(encoding="utf-8")
    return text.split(start, 1)[1].split(end, 1)[0]


# --- scheduled backups ------------------------------------------------------

def test_scheduled_backup_calls_create_user_backup_with_its_real_signature():
    """run_one() passed encryption_passphrase=, which create_user_backup does
    not take: every scheduled backup died with TypeError before starting."""
    from app.services import backup, backup_scheduler

    user = SimpleNamespace(id=7, username="alice")
    schedule = SimpleNamespace(
        id=1, user_id=7, target_id=None, retention=3, backup_mode="full",
        full_every_n=7, name_suffix="full_date",
        last_run_at=None, last_status=None, last_message=None,
    )
    db = SimpleNamespace(commit=lambda: None)

    with patch.object(backup, "create_user_backup", autospec=True, return_value="/tmp/a.tar.gz") as create, \
            patch.object(backup, "prune_user_backups", autospec=True), \
            patch.object(backup_scheduler, "_schedule_users", return_value=[user]), \
            patch.object(backup_scheduler, "_upload_if_configured", return_value="local"):
        assert backup_scheduler.run_one(db, schedule) is True, schedule.last_message

    create.assert_called_once()
    assert schedule.last_status == "ok"


# --- cron: one grammar for the backend and for root's cron-write --------------

CRON_ARG_SAMPLES = [
    ("'php8.3 cron.php'", True),
    ("'php8.3 script.php '\"'\"'hello world'\"'\"''", True),  # shlex.quote style
    ("'php8.3 script.php '\\''hello world'\\'''", True),  # '\'' style
    ("'date +\\%F'", True),
    ("'id'>>/etc/cron.d/evil''", False),
    ("'x' ; id #'", False),
    ("'a' 'b'", False),
    ("'a'\"'\"''$(id)''", False),
    ("'a'\\''", False),
    ("'date +%F'", False),  # cron would cut the line at %
    ("'a\\\\%b'", False),  # "\\" is a pair, so this % is unescaped too
]


def _python_accepts(arg: str) -> bool:
    from app.services.cron import JAILED_COMMAND_ARG_RE, _UNESCAPED_PERCENT_RE

    return bool(JAILED_COMMAND_ARG_RE.fullmatch(arg)) and not _UNESCAPED_PERCENT_RE.search(arg)


@pytest.mark.parametrize("arg,expected", CRON_ARG_SAMPLES)
def test_backend_cron_argument_grammar(arg, expected):
    assert _python_accepts(arg) is expected


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_helper_cron_write_uses_the_same_grammar_as_the_backend(tmp_path):
    """validate_cron_write_line accepted lines cron-write refused (and the
    reverse for '"'"' quoting): the two must agree on every sample."""
    section = _helper_section("  cron-write)", "  service-status)")
    defs = [
        line.strip() for line in section.splitlines()
        if line.strip().startswith(("q=", "jailed_arg_re=", "unescaped_pct_re="))
    ]
    assert len(defs) == 3, defs
    script = "\n".join(defs) + r'''
while IFS= read -r s; do
  if [[ "$s" =~ $jailed_arg_re ]] && ! [[ "$s" =~ $unescaped_pct_re ]]; then echo 1; else echo 0; fi
done
'''
    samples = [arg for arg, _ in CRON_ARG_SAMPLES]
    # Script in a file and bytes on stdin: a -c argument and text mode both
    # rewrite backslashes / line endings on their way to bash on Windows.
    script_file = tmp_path / "grammar.sh"
    script_file.write_bytes(script.encode("utf-8"))
    out = subprocess.run(
        [shutil.which("bash"), script_file.as_posix()], input=("\n".join(samples) + "\n").encode("utf-8"),
        capture_output=True, check=True,
    ).stdout.decode("utf-8").split()
    assert out == ["1" if _python_accepts(arg) else "0" for arg in samples]
    assert out == ["1" if expected else "0" for _, expected in CRON_ARG_SAMPLES]


def test_helper_cron_write_keeps_the_working_directory():
    """A second =~ inside the loop reset BASH_REMATCH, so every jailed line was
    written with an empty <cwd> and no tenant cron job could start."""
    section = _helper_section("  cron-write)", "  service-status)")
    loop = section.split("while IFS= read -r line; do", 1)[1]
    after_first_match = loop.split("; then", 1)[1]
    second_match = after_first_match.index("=~")
    assert "BASH_REMATCH" not in after_first_match[second_match:]
    assert '"$cwd_word" "$quoted_cmd" "$marker"' in section


@pytest.mark.parametrize("command", [
    "php8.3 cron.php",
    "php8.3 script.php 'hello world' > /dev/null 2>&1",
    "php8.3 report.php --date=$(date +%F) # 100%",
    'php8.3 a.php "double quoted" \\\\%',
])
def test_rendered_jailed_lines_pass_both_validators(command):
    from app.services import cron

    line = cron._render_jailed_line(
        "alice", "*/5 * * * *", "/home/alice/domains/example.com/public_html", command, "example.com",
    )
    assert cron.validate_cron_write_line("alice", line) is True
    parsed = cron._parse_cron_line(0, line)
    assert parsed["command"] == command


def test_percent_escaping_round_trips_like_cron():
    from app.services.cron import _escape_percent, _unescape_percent

    # A % after an odd run of backslashes has no crontab spelling (cron always
    # eats one backslash there), so only even runs are expected to round-trip.
    for raw in ["100%", "a\\\\%b", "%%", "no percent"]:
        assert _unescape_percent(_escape_percent(raw)) == raw
    assert _escape_percent("a\\\\%b") == "a\\\\\\%b"


# --- update.sh cron migration ------------------------------------------------

def test_update_cron_migration_never_runs_app_code_as_root():
    body = UPDATE_SH.read_text(encoding="utf-8").split("migrate_site_cron_php_binary() {", 1)[1].split("\n}\n", 1)[0]
    # Stage 1 (root) uses the system interpreter in isolated mode.
    assert '/usr/bin/python3 -I - "$backup_dir" "$spool_json"' in body
    # Stage 2 is the only place the app is imported, and it runs as spanel.
    as_root, as_spanel = body.split("sudo -u spanel env", 1)
    assert "from app." not in as_root and "PYTHONPATH" not in as_root
    assert "from app." in as_spanel
    # Stage 3 only acts on users stage 1 dumped, whatever spanel prints.
    assert 'grep -qxF -- "$migrated_user" <<<"$dumped_users" || continue' in body


# --- process monitor ----------------------------------------------------------

def test_process_kill_is_limited_to_hosting_accounts():
    section = _helper_section("  process-kill)", "\n  *)\n")
    assert '(( target_uid < 1000 || target_uid >= 65000 ))' in section
    assert section.index("target_uid") < section.index('kill -9 "$target_pid"')


def test_api_keeps_proc_invisible():
    for script in (INSTALL_SH, UPDATE_SH):
        text = script.read_text(encoding="utf-8")
        assert "ProtectProc=invisible" in text
        assert "ProtectProc=default" not in text
        assert "ProcSubset=all" not in text


# --- webhooks --------------------------------------------------------------------

def test_legacy_webhook_url_is_recognised_after_webhook_key_rotation():
    from app.core.config import settings
    from app.services import site_apps

    app = SimpleNamespace(id=3, owner_id=1, name="api")
    with patch.object(settings, "webhook_secret_key", ""):
        legacy = site_apps.legacy_webhook_token_for(app)
        current = site_apps.webhook_token_for(app)
    assert legacy != current
    with patch.object(settings, "webhook_secret_key", "rotated-webhook-key-0123456789abcdef"):
        assert site_apps.verify_webhook_token(app, current) is False
        assert site_apps.is_legacy_webhook_token(app, legacy) is True
        assert site_apps.is_legacy_webhook_token(app, "") is False


# --- nginx QUIC ------------------------------------------------------------------

def test_deleting_the_reuseport_vhost_hands_reuseport_on_per_address(tmp_path):
    """Only the plain 443 / [::]:443 listeners were handed on before; a vhost
    on a dedicated IP lost its reuseport for good."""
    from app.core.config import settings
    from app.services import nginx

    (tmp_path / "a.com.conf").write_text("    listen 443 quic;\n    listen 10.0.0.5:443 quic;\n", encoding="utf-8")
    (tmp_path / "b.com.conf").write_text(
        "    listen 443 quic;\n    listen 10.0.0.5:443 quic;\n    listen [::]:443 quic reuseport;\n", encoding="utf-8",
    )
    with patch.object(settings, "nginx_sites_available", str(tmp_path)):
        nginx._ensure_quic_reuseport_owner()
        nginx._ensure_quic_reuseport_owner()  # idempotent

    a = (tmp_path / "a.com.conf").read_text(encoding="utf-8")
    b = (tmp_path / "b.com.conf").read_text(encoding="utf-8")
    assert "listen 443 quic reuseport;" in a and "listen 10.0.0.5:443 quic reuseport;" in a
    assert "listen 443 quic;" in b and "listen 10.0.0.5:443 quic;" in b
    assert (a + b).count("reuseport") == 3


# --- archive extraction scan ---------------------------------------------------

def test_extract_archive_hands_unscanned_files_to_the_background_queue(tmp_path):
    """Files over the inline scan budget used to be skipped silently."""
    from app.services import file_manager

    site_dir = tmp_path / "site"
    (site_dir / "out").mkdir(parents=True)
    with zipfile.ZipFile(site_dir / "code.zip", "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("small.php", "<?php echo 1;")
        zf.writestr("big.php", "<?php /*" + "a" * (10 * 1024 * 1024) + "*/")
    site = SimpleNamespace(id=5, root_path=str(site_dir), domain="example.com", linux_user="")

    scanned, queued = [], []
    with patch.object(file_manager.site_users, "fix_site_path"), \
            patch.object(file_manager, "_clear_fastcgi_cache"), \
            patch.object(file_manager.malware_scan, "scan_upload_realtime",
                         side_effect=lambda path, **_: scanned.append(Path(path).name) or {"status": "clean"}), \
            patch.object(file_manager.malware_queue, "enqueue",
                         side_effect=lambda wid, path, name: queued.append((wid, name))):
        file_manager.extract_archive(site, "code.zip", "out")

    assert scanned == ["small.php"]
    assert queued == [(5, "big.php")]
