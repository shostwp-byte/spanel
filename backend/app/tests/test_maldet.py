"""Linux Malware Detect (maldet) integration."""

from pathlib import Path

from app.services import maldet

HELPER_SCRIPT = Path(__file__).resolve().parents[3] / "installer" / "files" / "spanel-helper.sh"

_REPORT = """\
Linux Malware Detect v1.6.5
            (C) 2002-2023, R-fx Networks <proj@rfxn.com>

malware detect scan report for host: bp
SCAN ID: 260902-1811.123456
TIME: Sep 2 2026 18:11:07 +0700
PATH: /home
TOTAL FILES: 16676
TOTAL HITS: 2
TOTAL CLEANED: 0

FILE HIT LIST:
{HEX}php.base64.v23eb9 : /home/nhs/nganhaso.net/public_html/shell.php
perl.mailer.x : /home/other/x.pl
===============================================
"""


def test_report_parsing_pulls_family_names_and_total():
    total, threats = maldet.parse_report_text(_REPORT)
    assert total == 16676
    assert threats == [
        {"path": "/home/nhs/nganhaso.net/public_html/shell.php", "signature": "{HEX}php.base64.v23eb9", "domain": ""},
        {"path": "/home/other/x.pl", "signature": "perl.mailer.x", "domain": ""},
    ]


def test_report_parsing_of_a_clean_scan():
    clean = "SCAN ID: 260902-1.2\nTOTAL FILES: 12\nTOTAL HITS: 0\n"
    total, threats = maldet.parse_report_text(clean)
    assert total == 12
    assert threats == []


def test_scan_builds_recent_vs_all_args(monkeypatch):
    calls = {}

    def fake_privileged(cmd, helper_args=None, **kw):
        calls["cmd"] = cmd
        calls["args"] = helper_args

        class R:
            stdout = "scanid=260902-1.2\nexit=0\n"
            returncode = 0
        return R()

    monkeypatch.setattr(maldet.shell, "privileged", fake_privileged)

    maldet.scan("abc123", ["/home"], recent_days=7)
    assert calls["cmd"] == "maldet-scan"
    assert calls["args"] == ["abc123", "recent", "7", "/home"]

    out = maldet.scan("abc123", ["/home/u/site"])
    assert calls["args"] == ["abc123", "all", "0", "/home/u/site"]
    assert out == {"scanid": "260902-1.2", "exit": 0, "raw": "scanid=260902-1.2\nexit=0\n"}


class TestHelper:
    def test_maldet_verbs_and_confinement(self):
        helper = HELPER_SCRIPT.read_text(encoding="utf-8")
        for verb in ("maldet-install)", "maldet-scan)", "maldet-monitor)", "maldet-update-sigs)", "maldet-report)"):
            assert verb in helper
        # scan paths are confined to / or /home
        assert "scan path must be / or under /home" in helper
        # scanid is anchored
        assert "^[0-9]{6}-[0-9]{4}\\.[0-9]+$" in helper
        # ClamAV engine on, resident daemon deliberately not enabled
        assert 'scan_clamscan=1' in helper
        assert "quarantine_hits=0" in helper
        # scans run at the lowest priority
        body = helper.split("run_maldet_scan() {", 1)[1].split("\n}", 1)[0]
        assert "nice -n 19" in body and "ionice -c3" in body
        # inotify limits raised before the monitor starts
        assert "fs.inotify.max_user_watches" in helper

    def test_the_scanner_does_not_scan_the_scanner(self):
        """A server scan walks "/", which includes the signature database.

        /var/lib/clamav/rfxn.yara is a file of malware patterns, so a pattern
        scan reports it INFECTED on every run - the scanner detecting itself.
        It was doing exactly that on every server scan before this list existed.
        """
        helper = HELPER_SCRIPT.read_text(encoding="utf-8")
        block = helper.split("MALDET_IGNORE_PATHS=(", 1)[1].split(")", 1)[0]
        for path in ("/var/lib/clamav", "/usr/local/maldetect"):
            assert path in block, f"{path} would be scanned and report itself"
        # Storage, not code: never executed, and gigabytes of pointless I/O.
        for path in ("/var/lib/mysql", "/proc", "/sys", "/dev", "/run"):
            assert path in block

        body = helper.split("maldet_write_ignores() {", 1)[1].split("\n}", 1)[0]
        assert "${MALDET_HOME}/ignore_paths" in body
        # Append-only, so re-running never duplicates and never drops an entry
        # the admin added by hand.
        assert "grep -qxF" in body
        assert '>>"$f"' in body
        assert '>"$f"' not in body.replace('>>"$f"', "")

        # Written when the config is written, and refreshed on every update so
        # installs that predate the list pick it up.
        conf = helper.split("maldet_write_conf() {", 1)[1].split("\n}", 1)[0]
        assert "maldet_write_ignores" in conf
        sigs = helper.split("maldet-update-sigs)", 1)[1].split(";;", 1)[0]
        assert "maldet_write_conf" in sigs


# --- progress while a scan runs (operator, 2026-09-26) ------------------------
# With the ClamAV engine maldet prints nothing between "scan ... in progress"
# and the end, and the panel read nothing until the end: a four-hour scan sat
# at 0 files and 0% the whole way.

def test_progress_reads_the_helper(monkeypatch):
    calls = {}

    def fake_privileged(cmd, helper_args=None, **kw):
        calls["cmd"], calls["args"] = cmd, helper_args

        class R:
            stdout = "stage=scanning\ntotal=182332\nscanned=10899\n"
            returncode = 0
        return R()

    monkeypatch.setattr(maldet.shell, "privileged", fake_privileged)
    assert maldet.progress("abc123") == {"stage": "scanning", "total": 182332, "scanned": 10899}
    assert calls == {"cmd": "maldet-progress", "args": ["abc123"]}


def test_the_job_moves_while_maldet_runs(monkeypatch):
    import threading

    from app.services import panel_settings

    updates = []
    readings = iter([
        {"stage": "listing", "total": 0, "scanned": 0},
        {"stage": "scanning", "total": 200, "scanned": 50},
        {"stage": "scanning", "total": 200, "scanned": 199},
    ])
    seen_enough = threading.Event()

    def fake_progress(job_id):
        try:
            return next(readings)
        except StopIteration:
            seen_enough.set()
            return {"stage": "scanning", "total": 200, "scanned": 199}

    def fake_scan(job_id, targets, recent_days=None):
        assert seen_enough.wait(5), "no progress was read while the scan ran"
        return {"scanid": "260926-0059.1", "exit": 0, "raw": ""}

    monkeypatch.setattr(panel_settings, "SERVER_SCAN_POLL_SECONDS", 0.01)
    monkeypatch.setattr(panel_settings, "_update_malware_job", lambda job_id, **kw: updates.append(kw))
    monkeypatch.setattr(panel_settings, "_append_malware_log", lambda job_id, line: None)
    monkeypatch.setattr(maldet, "progress", fake_progress)
    monkeypatch.setattr(maldet, "scan", fake_scan)
    monkeypatch.setattr(maldet, "read_job_report", lambda job_id: (200, []))

    panel_settings._run_maldet_job("abc123", "/home")

    moving = [u for u in updates if "scanned" in u and u.get("progress_percent", 0) < 100]
    assert {"message": "Building the file list...", "scanned": 0, "progress_percent": 0, "total_files": 0} in moving
    assert {"message": "Scanning files...", "scanned": 50, "progress_percent": 25, "total_files": 200} in moving
    assert max(u["progress_percent"] for u in moving) == 99, "never 100% before it is over"
    final = updates[-1]
    assert final["status"] == "done" and final["progress_percent"] == 100 and final["scanned"] == 200


def test_the_helper_reports_numbers_from_the_scanners_position():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "maldet-progress)" in helper
    body = helper.split("maldet_scan_progress() {", 1)[1].split("\n}\n", 1)[0]
    assert '[[ "$job" =~ ^[0-9a-f]{8,64}$ ]] || deny' in body
    assert "/proc/${clam}/fdinfo/" in body and 'pgrep -P "$pid" -x clamscan' in body
    # set -euo pipefail: a grep that matches nothing must not end the helper.
    for line in body.splitlines():
        if "$(grep" in line or "$(awk" in line:
            assert "|| true)" in line, line
    assert "printf 'stage=%s\\ntotal=%s\\nscanned=%s\\n'" in body
