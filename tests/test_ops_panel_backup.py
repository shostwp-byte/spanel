"""The panel self-backup (disaster recovery): what goes in, the archive
format, passphrase escrow, the run, and the restore command's decryption."""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

HELPER = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"
SPANELCTL = REPO_ROOT / "installer" / "files" / "spanelctl"

from app.services import backup, panel_backup  # noqa: E402

BASH = shutil.which("bash")
OPENSSL = shutil.which("openssl")


def _function(name: str, source: Path = HELPER) -> str:
    """A shell function's text, skipping heredoc bodies (their "}" lines)."""
    import re

    lines = source.read_text(encoding="utf-8").split("\n")
    start = lines.index(f"{name}() {{")
    terminator = None
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if terminator is not None:
            if line == terminator:
                terminator = None
            continue
        match = re.search(r"<<-?'?([A-Z]+)'?", line)
        if match:
            terminator = match.group(1)
        elif line == "}":
            return "\n".join(lines[start:index + 1]) + "\n"
    raise AssertionError(f"end of {name} not found")


def _array(name: str) -> str:
    text = HELPER.read_text(encoding="utf-8")
    start = text.index(f"{name}=(")
    return text[start:text.index("\n)\n", start) + 3]


def _embedded_python(body: str, nth: int = 0) -> str:
    part = body.split("<<'PY'")[nth + 1]
    return part.split("\n", 1)[1].split("\nPY\n", 1)[0]


# --- what goes in ---------------------------------------------------------------------

@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_include_list_from_a_fixture_tree(tmp_path):
    root = tmp_path / "root"
    for rel in ("opt/spanel/backend/.env", "etc/spanel/audit-hmac.key", "etc/php/8.3/fpm/pool.d/alice.conf",
                "etc/php/8.3/fpm/conf.d/99-spanel-overrides.ini", "etc/php/8.3/fpm/conf.d/10-opcache.ini",
                "etc/nginx/conf.d/00-spanel-tools.conf", "etc/powerdns/pdns.d/zz-spanel.conf",
                "etc/exim4/spanel/dkim/example.com.pem", "var/lib/spanel/update-status.json",
                "etc/systemd/system/spanel-api.service", "etc/systemd/system/nginx.service"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    script = "\n".join(["APP_DIR=/opt/spanel", _array("PANEL_DR_PATHS"), _function("panel_dr_include_paths"),
                        f"panel_dr_include_paths '{root.as_posix()}'"])
    path = tmp_path / "list.sh"
    path.write_bytes(script.encode("utf-8"))
    out = subprocess.run([BASH, path.as_posix()], capture_output=True, check=True).stdout.decode().split()
    assert out == [
        "opt/spanel/backend/.env",
        "etc/spanel",
        "var/lib/spanel",
        "etc/nginx/conf.d",
        "etc/php/8.3/fpm/pool.d",
        "etc/php/8.3/fpm/conf.d/99-spanel-overrides.ini",
        "etc/exim4",
        "etc/powerdns",
        "etc/systemd/system/spanel-api.service",
    ]


def test_archive_leaves_out_its_own_key_and_bulky_state():
    excludes = _array("PANEL_DR_EXCLUDES")
    for item in ("etc/spanel/dr-passphrase", "var/lib/spanel/db-backups", "var/lib/spanel/geoip"):
        assert f'"{item}"' in excludes
    body = _function("panel_dr_archive")
    assert "sqlite_online_copy" in body and "panel_dr_dump_pdns" in body and "panel_dr_encrypt" in body
    # Only encrypted output reaches the panel-readable folder.
    assert body.index("panel_dr_encrypt") < body.index('chown root:spanel "$out"')


def test_pdns_dump_covers_both_backends():
    body = _function("panel_dr_dump_pdns")
    assert "sqlite_online_copy \"$PDNS_DB\"" in body
    assert "mysqldump --single-transaction" in body and "gmysql-dbname" in body
    assert "pdnsutil list-zone" in body and "is_domain" in body


# --- the archive format -------------------------------------------------------------------

@pytest.mark.skipif(OPENSSL is None, reason="openssl not available")
def test_archive_encryption_is_the_panel_backup_format(tmp_path):
    """openssl enc -pbkdf2 + the helper's HMAC trailer must be exactly what
    backup.decrypt_backup_archive opens, and what spanelctl dr-restore checks."""
    passfile = tmp_path / "dr-passphrase"
    passphrase = "q3Xk9-ZtW_pL0aVs7mN2rB8cY4dE6fGh1jK5"
    passfile.write_bytes((passphrase + "\n").encode("utf-8"))   # LF, as the helper writes it
    plain = tmp_path / "plain.tar.gz"
    plain.write_bytes(b"panel state " * 5000)
    enc = tmp_path / "panel-dr-srv1-20261024T013000Z.spdr"
    subprocess.run([OPENSSL, "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter", "100000", "-md", "sha256",
                    "-pass", f"file:{passfile.as_posix()}", "-in", str(plain), "-out", str(enc)], check=True)
    mac_script = tmp_path / "mac.py"
    mac_script.write_text(_embedded_python(_function("panel_dr_encrypt")), encoding="utf-8")
    subprocess.run([sys.executable, "-I", str(mac_script), str(enc), str(passfile)], check=True)

    out = backup.decrypt_backup_archive(enc, passphrase, output_path=tmp_path / "out.tar.gz")
    assert Path(out).read_bytes() == plain.read_bytes()

    # spanelctl dr-restore: HMAC first, then the ciphertext openssl decrypts.
    check_script = tmp_path / "check.py"
    check_script.write_text(_embedded_python(_function("dr_decrypt", SPANELCTL)), encoding="utf-8")
    cipher = tmp_path / "cipher.bin"
    assert subprocess.run([sys.executable, "-I", str(check_script), str(enc), str(passfile), str(cipher)]).returncode == 0
    dec = tmp_path / "dec.tar.gz"
    subprocess.run([OPENSSL, "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "100000", "-md", "sha256",
                    "-pass", f"file:{passfile.as_posix()}", "-in", str(cipher), "-out", str(dec)], check=True)
    assert dec.read_bytes() == plain.read_bytes()

    wrong = tmp_path / "wrong"
    wrong.write_bytes(b"not-the-passphrase\n")
    assert subprocess.run([sys.executable, "-I", str(check_script), str(enc), str(wrong), str(cipher)]).returncode == 1
    tampered = bytearray(enc.read_bytes())
    tampered[100] ^= 0xFF
    enc.write_bytes(bytes(tampered))
    assert subprocess.run([sys.executable, "-I", str(check_script), str(enc), str(passfile), str(cipher)]).returncode == 1


def test_restore_command_is_documented_and_wired():
    ctl = SPANELCTL.read_text(encoding="utf-8")
    assert "dr-restore|--dr-restore) shift; dr_restore" in ctl
    body = _function("dr_restore", SPANELCTL)
    assert body.index("dr_decrypt") < body.index("systemctl stop")      # nothing stops before the archive is proven
    assert "RESTORE" in body and "--with-site-configs" in body
    assert "pool.d" in body and "00-spanel-*" in body
    guide = (REPO_ROOT / "docs" / "ADMIN_AND_CLI_GUIDE.md").read_text(encoding="utf-8")
    assert "spanel dr-restore" in guide


# --- escrow, settings, the run --------------------------------------------------------------

@pytest.fixture()
def dr(tmp_path, monkeypatch):
    monkeypatch.setattr(panel_backup, "CONFIG_FILE", tmp_path / "panel-dr.json")
    monkeypatch.setattr(panel_backup.settings, "backup_root", str(tmp_path / "backups"))
    state = {"passphrase": ""}

    def privileged(cmd, helper_args=None, **kw):
        if cmd == "panel-dr-key-status":
            if not state["passphrase"]:
                return SimpleNamespace(returncode=0, stdout="initialised=no\n", stderr="")
            return SimpleNamespace(returncode=0, stdout=f"initialised=yes\nfingerprint={panel_backup.fingerprint(state['passphrase'])}\n", stderr="")
        if cmd == "panel-dr-key-init":
            if state["passphrase"] and helper_args != ["--rotate"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="already set")
            state["passphrase"] = "S3cret-passphrase-from-helper-0123456789"
            return SimpleNamespace(returncode=0, stdout=f"passphrase={state['passphrase']}\nfingerprint={panel_backup.fingerprint(state['passphrase'])}\n", stderr="")
        if cmd == "panel-dr-archive":
            archive = tmp_path / "backups" / "panel-dr" / "panel-dr-srv1-20261024T013000Z.spdr"
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(b"Salted__........")
            return SimpleNamespace(returncode=0, stdout=f"archive={archive}\nmanifest={archive.with_suffix('.json')}\n", stderr="")
        raise AssertionError(cmd)
    monkeypatch.setattr(panel_backup.shell, "privileged", privileged)
    return state


def test_passphrase_is_shown_once_and_must_be_typed_back(dr):
    assert panel_backup.key_status() == {"initialised": False, "fingerprint": "", "confirmed": False,
                                         "shown_at": "", "confirmed_at": ""}
    created = panel_backup.init_passphrase()
    assert created["passphrase"] == dr["passphrase"]
    # Only its fingerprint is kept by the panel.
    assert dr["passphrase"] not in json.dumps(panel_backup.load_config())
    with pytest.raises(ValueError):
        panel_backup.init_passphrase()            # never silently replaced
    assert panel_backup.key_status()["confirmed"] is False
    with pytest.raises(ValueError):
        panel_backup.confirm_passphrase("a guess")
    status = panel_backup.confirm_passphrase(dr["passphrase"] + "\n")
    assert status["confirmed"] is True
    # Rotating needs a fresh confirmation.
    panel_backup.init_passphrase(rotate=True)
    assert panel_backup.key_status()["confirmed"] is False


def test_settings_need_an_off_server_destination(dr):
    with pytest.raises(ValueError):
        panel_backup.save_settings({"enabled": True}, {1, 2})
    with pytest.raises(ValueError):
        panel_backup.save_settings({"target_ids": [9]}, {1, 2})
    with pytest.raises(ValueError):
        panel_backup.save_settings({"window": "7pm"}, {1})
    saved = panel_backup.save_settings({"enabled": True, "target_ids": [2, 1, 2], "window": "01:45", "keep_remote": 0}, {1, 2})
    assert saved["target_ids"] == [1, 2] and saved["keep_remote"] == 1 and saved["window"] == "01:45"
    sched = panel_backup.scheduled_window()
    assert sched.window == "01:45"


def test_run_refuses_without_a_confirmed_passphrase(dr):
    with pytest.raises(ValueError):
        panel_backup.run_backup(db=None)


def test_run_uploads_everywhere_and_prunes_old_copies(dr, monkeypatch):
    from app.services import backup_remote

    panel_backup.init_passphrase()
    panel_backup.confirm_passphrase(dr["passphrase"])
    panel_backup.save_settings({"target_ids": [1, 2], "keep_remote": 2}, {1, 2})
    targets = [SimpleNamespace(id=1, name="s3-main"), SimpleNamespace(id=2, name="sftp-dr")]
    monkeypatch.setattr(panel_backup, "_targets", lambda db, ids: targets)
    monkeypatch.setattr(backup, "get_server_hostname", lambda: "srv1")
    uploads, deleted = [], []

    def upload(target, local, name, subpath, db=None):
        if target.name == "sftp-dr":
            raise backup_remote.RemoteError("host key mismatch")
        uploads.append((target.name, name, subpath))
        return f"{target.name}:{subpath}/{name}"
    listing = [{"name": f"panel-dr-srv1-2026102{d}T013000Z.spdr", "key": f"k{d}", "modified": f"2026-10-2{d}T01:31:00"}
               for d in range(1, 5)] + [{"name": "notes.txt.spdr", "key": "x", "modified": "2026-10-30"}]
    monkeypatch.setattr(backup_remote, "upload", upload)
    monkeypatch.setattr(backup_remote, "list_files", lambda target, subpath, suffixes=(): listing)
    monkeypatch.setattr(backup_remote, "delete", lambda target, key: deleted.append(key))
    alerts = []
    monkeypatch.setattr(panel_backup, "_report_failure", lambda result: alerts.append(result))

    result = panel_backup.run_backup(db=None)
    assert uploads == [("s3-main", "panel-dr-srv1-20261024T013000Z.spdr", "srv1/_panel-dr")]
    assert deleted == ["k2", "k1"]                       # newest two kept, the stray file untouched
    assert result["status"] == "partial" and "sftp-dr" in result["errors"][0]
    assert alerts and panel_backup.load_config()["last_run"]["status"] == "partial"


def test_remote_prune_plan_keeps_at_least_one():
    entries = [{"name": "panel-dr-h-20261001T000000Z.spdr", "modified": "2026-10-01"}]
    assert panel_backup.plan_remote_prune(entries, 0) == []


def test_panel_dr_is_a_maintenance_window_job():
    from app.services import maintenance_window

    assert "panel_dr" in [job.name for job in maintenance_window.default_jobs()]
