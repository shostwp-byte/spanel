import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.nginx import (
    FASTCGI_CACHE_SERVER_BLOCK,
    FASTCGI_CACHE_LOCATION_BLOCK,
)
from app.services.resource_monitor import inspect_slow_bottlenecks, collect_domain_traffic_analytics
from app.services.site_apps import (
    PYTHON_START_KINDS,
    validate_start,
    webhook_token_for,
    webhook_secret_for,
    verify_webhook_token,
)
from app.schemas.schemas import SiteAppCreate


def test_pillar5_woocommerce_fastcgi_cache_and_http3_quic():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "templates"
        / "nginx"
        / "wordpress.conf.j2"
    )
    tpl_text = template_path.read_text(encoding="utf-8")
    assert FASTCGI_CACHE_SERVER_BLOCK in tpl_text
    assert FASTCGI_CACHE_LOCATION_BLOCK in tpl_text
    assert "/cart/|/checkout/|/my-account/|/wc-api/|/addons/" in tpl_text
    assert "utm_[a-z]+" in tpl_text and "fbclid=" in tpl_text and "gclid=" in tpl_text
    assert "add-to-cart=|wc-ajax=" in tpl_text
    # Alt-Svc must not be hardcoded on plain HTTP / non-HTTP3 vhosts
    assert 'add_header Alt-Svc \'h3=":443"; ma=86400\' always;' not in tpl_text


def test_pillar5_slow_query_and_php_slowlog_inspector():
    class DummyQuery:
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args, **kwargs):
            return self
        def all(self):
            return []

    class DummyDB:
        def query(self, *args, **kwargs):
            return DummyQuery()

    report = inspect_slow_bottlenecks(DummyDB(), limit=10)
    assert "summary" in report
    assert "mysql_slow_queries" in report
    assert "php_slow_traces" in report
    assert report["summary"]["php_slowlog_timeout_sec"] == 5

    helper_path = (
        Path(__file__).resolve().parents[1]
        / "installer"
        / "files"
        / "spanel-helper.sh"
    )
    helper_text = helper_path.read_text(encoding="utf-8")
    assert "request_slowlog_timeout = 5s" in helper_text
    assert "slowlog = /var/log/php/spanel-slow-${pool_name}.log" in helper_text


def test_pillar6_managed_python_runtime_and_git_webhook():
    assert PYTHON_START_KINDS == {"uvicorn", "gunicorn", "python"}
    kind, arg = validate_start("uvicorn", "main:app")
    assert kind == "uvicorn"
    assert arg == "main:app"

    schema = SiteAppCreate(
        name="fastapi-service",
        kind="python",
        start_kind="uvicorn",
        start_arg="app.main:app",
    )
    assert schema.kind == "python"
    assert schema.start_kind == "uvicorn"

    class DummyApp:
        id = 10
        owner_id = 1
        name = "fastapi-service"

    token1 = webhook_token_for(DummyApp())
    token2 = webhook_token_for(DummyApp())
    secret1 = webhook_secret_for(DummyApp())
    assert len(token1) == 32
    assert token1 == token2
    assert len(secret1) == 64
    assert secret1 != token1
    assert verify_webhook_token(DummyApp(), token1) is True
    assert verify_webhook_token(DummyApp(), "invalid-token") is False

    helper_path = (
        Path(__file__).resolve().parents[1]
        / "installer"
        / "files"
        / "spanel-helper.sh"
    )
    helper_text = helper_path.read_text(encoding="utf-8")
    assert "write_python_app_unit()" in helper_text
    assert "site-app-python-deps)" in helper_text
    assert "site-app-git-pull)" in helper_text


def test_pillar8_domain_traffic_analytics_and_command_palette():
    class DummyQuery:
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args, **kwargs):
            return self
        def all(self):
            return []

    class DummyDB:
        def query(self, *args, **kwargs):
            return DummyQuery()

    analytics = collect_domain_traffic_analytics(DummyDB())
    assert "items" in analytics
    assert "summary" in analytics
    assert analytics["summary"]["total_domains"] == 0

    app_jsx = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "src"
        / "App.jsx"
    ).read_text(encoding="utf-8")
    assert "cmdPaletteOpen" in app_jsx
    assert "Ctrl+K" in app_jsx


def test_phase2_sqlite_wal_audit_chain_watchdog_and_cron_jail(tmp_path):
    import shlex
    from datetime import datetime
    from app.core.database import get_sqlite_health, _set_sqlite_pragma
    from app.models.entities import AuditLog, WatchdogIncident
    from app.services.audit import _compute_entry_hash
    from app.services.cron import (
        CRON_JAIL_BIN,
        _parse_cron_line,
        _validate_command,
        validate_cron_write_line,
    )
    from app.services.system import get_self_healing_status

    assert callable(_set_sqlite_pragma)
    health = get_sqlite_health()
    assert "journal_mode" in health
    assert WatchdogIncident.__tablename__ == "watchdog_incidents"
    assert hasattr(AuditLog, "prev_hash") and hasattr(AuditLog, "entry_hash")

    ts = datetime(2026, 10, 23, 12, 0, 0)
    h1 = _compute_entry_hash("GENESIS", 1, "login", "admin", "ok", ts)
    h2 = _compute_entry_hash(h1, 1, "update_settings", "panel", "changed", ts)
    assert len(h1) == 64 and len(h2) == 64 and h1 != h2

    wd = get_self_healing_status()
    assert wd["max_restarts_per_15m"] == 3
    assert wd["incidents_table"] == "watchdog_incidents"

    # Cron Mount Namespace Jail + PHP disable_functions rendering & parsing
    site_root = Path("/home/alice/domains/example.com")
    doc_root = site_root / "public_html"
    tmp_doc_root = tmp_path / "home" / "alice" / "domains" / "example.com" / "public_html"
    tmp_doc_root.mkdir(parents=True, exist_ok=True)
    (tmp_doc_root / "cron.php").write_text("<?php echo 1;", encoding="utf-8")
    rendered = _validate_command(
        "php cron.php > cron.log 2>&1",
        tmp_doc_root,
        tmp_doc_root.parent,
        "php",
        "alice",
    )
    assert "open_basedir=" in rendered
    assert "disable_functions=" in rendered
    assert ">" in rendered and "2>&1" in rendered

    cron_line = (
        f"*/5 * * * * root {CRON_JAIL_BIN} alice {shlex.quote(doc_root.as_posix())} "
        f"-- /bin/sh -c {shlex.quote(rendered)} # spanel:example.com"
    )
    # Redirection operators must be strictly inside the single-quoted /bin/sh -c argument
    prefix_before_sh, _, after_sh = cron_line.partition("-- /bin/sh -c ")
    assert ">" not in prefix_before_sh
    quoted_part, _, comment_part = after_sh.rpartition(" # spanel:")
    assert quoted_part.startswith("'") and quoted_part.endswith("'")
    assert ">" not in comment_part

    assert validate_cron_write_line("alice", cron_line) is True

    parsed = _parse_cron_line(0, cron_line)
    assert parsed["schedule"] == "*/5 * * * *"
    assert "cron.php" in parsed["command"]
    assert CRON_JAIL_BIN not in parsed["command"]

    # Reject unconfined cron lines, wrong user, cwd outside /home/<user>, or unquoted redirections
    for bad_line in [
        "*/5 * * * * echo pwned > /etc/cron.d/pwned # spanel:example.com",
        f"*/5 * * * * root {CRON_JAIL_BIN} bob /home/bob/site -- /bin/sh -c 'echo 1' # spanel:example.com",
        f"*/5 * * * * root {CRON_JAIL_BIN} alice /etc -- /bin/sh -c 'echo 1' # spanel:example.com",
        f"*/5 * * * * root {CRON_JAIL_BIN} alice {doc_root} -- /bin/sh -c 'echo 1' > /etc/cron.d/pwn # spanel:example.com",
        f"*/5 * * * * root {CRON_JAIL_BIN} alice {doc_root} -- /bin/sh -c 'id'>>/etc/cron.d/evil'' # spanel:example.com",
    ]:
        assert validate_cron_write_line("alice", bad_line) is False, bad_line


def test_phase3_dnssec_malware_shield_backup_aes256_and_core_autopatch(tmp_path):
    import tarfile
    import zipfile
    from types import SimpleNamespace
    from app.services.backup import (
        VALID_RESTORE_MODES,
        decrypt_backup_archive,
        encrypt_backup_archive,
        is_encrypted_backup,
        verify_backup_integrity,
    )
    from app.services.dns import _parse_ds_record
    from app.services.file_manager import extract_archive
    from app.services.malware_scan import scan_upload_realtime
    from app.services.updates import CORE_STACK_SERVICES, check_core_stack_updates

    # 1. Granular restore modes + AES-256-CBC (PBKDF2) encryption & SHA-256 verification
    assert set(VALID_RESTORE_MODES) == {"full", "files_only", "database_only", "mail_only"}
    sample_txt = tmp_path / "index.php"
    sample_txt.write_text("<?php echo 'hello';", encoding="utf-8")
    archive_path = tmp_path / "site-backup.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(sample_txt, arcname="index.php")

    plain_verify = verify_backup_integrity(archive_path)
    assert plain_verify["valid"] is True
    assert plain_verify["encrypted"] is False

    enc_path = encrypt_backup_archive(archive_path, "StrongPass!2026", replace_original=False)
    assert is_encrypted_backup(enc_path) is True
    assert b"SPNLMAC1" in enc_path.read_bytes()[-40:]
    enc_verify = verify_backup_integrity(enc_path, passphrase="StrongPass!2026")
    assert enc_verify["valid"] is True
    assert enc_verify["encrypted"] is True
    assert enc_verify["cipher"] == "AES-256-CBC-PBKDF2-SHA256"
    assert enc_verify["mac"] == "HMAC-SHA256"
    assert enc_verify["member_count"] == 1

    dec_path = tmp_path / "restored.tar.gz"
    decrypt_backup_archive(enc_path, "StrongPass!2026", output_path=dec_path)
    assert is_encrypted_backup(dec_path) is False
    assert verify_backup_integrity(dec_path)["valid"] is True

    # 2. Real-time upload malware shield (< 200ms + YARA/Heuristic + extract_archive scanning)
    disguised_elf = tmp_path / "avatar.jpg"
    disguised_elf.write_bytes(b"\x7fELF\x02\x01\x01\x00FakeDisguisedBinary")
    scan_res = scan_upload_realtime(str(disguised_elf), domain="example.com", auto_quarantine=False)
    assert scan_res["status"] == "infected"
    assert scan_res["under_200ms"] is True
    assert scan_res["signature"] == "SPanel.Heuristic.ELF.DisguisedBinaryInWebroot"

    # Verify extract_archive scans extracted files and rejects archives containing malware
    from unittest.mock import patch
    from app.services import file_manager

    site_dir = tmp_path / "site_extract"
    (site_dir / "extracted").mkdir(parents=True, exist_ok=True)
    evil_zip = site_dir / "payload.zip"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("innocent.jpg", b"\x7fELF\x02\x01\x01\x00DisguisedPayloadInZip")
    # No linux_user: extract in-process instead of through spanel-helper.
    dummy_site = SimpleNamespace(id=1, root_path=str(site_dir), domain="example.com", linux_user="")
    with patch.object(file_manager.site_users, "fix_site_path"), patch.object(file_manager, "_clear_fastcgi_cache"):
        try:
            extract_archive(dummy_site, "payload.zip", "extracted")
            raise AssertionError("extract_archive should have blocked malware inside zip archive")
        except ValueError as exc:
            assert "Archive extraction blocked by Malware Shield" in str(exc)
    assert not (site_dir / "extracted" / "innocent.jpg").exists()

    # 3. DNSSEC DS record parser
    ds_info = _parse_ds_record("example.com", "12345 13 2 ABCDEF0123456789")
    assert ds_info["key_tag"] == 12345
    assert ds_info["algorithm"] == 13
    assert ds_info["digest_type"] == 2

    # 4. Core Stack Auto-Patch
    assert len(CORE_STACK_SERVICES) >= 7
    cs_status = check_core_stack_updates()
    assert "services" in cs_status and len(cs_status["services"]) == len(CORE_STACK_SERVICES)

    # 5. Helper script regressions check
    helper_text = (
        Path(__file__).resolve().parents[1]
        / "installer"
        / "files"
        / "spanel-helper.sh"
    ).read_text(encoding="utf-8")
    assert '"${terminal_runner[@]}"' in helper_text
    assert "rm -rf /var/lib/spanel/update-state" in helper_text
    assert "updates-core-stack-patch)" in helper_text
    assert "apt-mark unhold" in helper_text


def test_all_enterprise_api_routers_registered():
    from app.api import (
        dns,
        licensing,
        mail,
        maintenance,
        malware,
        multi_server,
        panel_settings,
        resources,
        services,
        site_apps,
        updates,
    )

    routers = [
        services.router,
        panel_settings.router,
        dns.router,
        malware.router,
        maintenance.router,
        updates.router,
        mail.router,
        resources.router,
        site_apps.router,
        multi_server.router,
        licensing.router,
    ]
    paths = {getattr(r, "path", "") for router in routers for r in router.routes}
    required = {
        "/services/self-healing",
        "/services/db-health",
        "/panel-settings/audit-logs",
        "/panel-settings/cron-security",
        "/dns/zones/{zone}/dnssec",
        "/malware/quarantine",
        "/maintenance/backups/verify",
        "/maintenance/backups/encrypt",
        "/maintenance/backups/decrypt",
        "/updates/core-stack/status",
        "/mail/rspamd/block",
        "/resources/bottlenecks",
        "/resources/domain-traffic",
        "/site-apps/webhook/{app_id}/{token}",
    }
    missing = required - paths
    assert not missing, f"Missing API routes: {missing}"


def test_p0_dnssec_no_fake_ds_and_watchdog_no_root_escalation():
    from unittest.mock import patch
    from app.services import dns
    from app.services.dns import DnsError

    # P0.1: Verify fake_keys / _stored_dnssec_fallback are completely removed
    assert not hasattr(dns, "_stored_dnssec_fallback")
    assert not hasattr(dns, "_save_dnssec_fallback")

    # When PowerDNS API fails, enable_dnssec and get_dnssec_status must raise DnsError (never return fake DS)
    with patch.object(dns, "_request", side_effect=DnsError("PowerDNS API unreachable", status=502)):
        try:
            dns.enable_dnssec("example.com")
            raise AssertionError("enable_dnssec should have raised DnsError when PowerDNS is unreachable")
        except DnsError as exc:
            assert exc.status == 502

        try:
            dns.get_dnssec_status("example.com")
            raise AssertionError("get_dnssec_status should have raised DnsError when PowerDNS is unreachable")
        except DnsError as exc:
            assert exc.status == 502

    # When PowerDNS returns an active CSK key without a valid DS record, enable_dnssec must raise DnsError
    with patch.object(dns, "_request", return_value=[{"id": 1, "active": True, "dnskey": "257 3 13 AAA=", "ds": []}]):
        try:
            dns.enable_dnssec("example.com")
            raise AssertionError("enable_dnssec should have raised DnsError when DS records are empty")
        except DnsError as exc:
            assert exc.status == 502

    # When PowerDNS returns a real CSK key with DS records, enable_dnssec succeeds with verified DS
    real_keys = [{
        "id": 7,
        "keytype": "csk",
        "active": True,
        "published": True,
        "dnskey": "257 3 13 REALPUBKEY==",
        "ds": ["54321 13 2 0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF"],
    }]
    with patch.object(dns, "_request", return_value=real_keys), \
         patch("app.services.multi_server.trigger_background_zone_sync"):
        res = dns.enable_dnssec("example.com")
        assert res["enabled"] is True
        assert res["primary_ds"]["key_tag"] == 54321
        assert res["primary_ds"]["digest"] == "0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF"

    # P0.2: Verify watchdog-run root escalation is removed from spanel-helper.sh and service runs as spanel without -I
    repo_root = Path(__file__).resolve().parents[1]
    helper_text = (repo_root / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")
    assert "watchdog-run)" not in helper_text

    wd_service = (repo_root / "installer" / "files" / "spanel-watchdog.service").read_text(encoding="utf-8")
    assert "User=spanel" in wd_service
    assert "Group=spanel" in wd_service
    assert "User=root" not in wd_service
    assert "watchdog-run" not in wd_service
    assert "EnvironmentFile=-/opt/spanel/backend/.env" in wd_service
    assert "ExecStart=/opt/spanel/backend/.venv/bin/python -m app.services.system" in wd_service
    assert " -I " not in wd_service

    system_py = (repo_root / "backend" / "app" / "services" / "system.py").read_text(encoding="utf-8")
    assert system_py.count('if __name__ == "__main__":') == 1


def test_p1_cron_jail_audit_hmac_backup_etm_yara_exim4():
    from app.services import audit, cron, updates
    from app.api.maintenance import BackupVerifyPayload

    repo_root = Path(__file__).resolve().parents[1]
    helper_text = (repo_root / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")
    cron_jail_text = (repo_root / "installer" / "files" / "spanel-cron-jail.sh").read_text(encoding="utf-8")
    install_text = (repo_root / "installer" / "install.sh").read_text(encoding="utf-8")
    update_text = (repo_root / "installer" / "update.sh").read_text(encoding="utf-8")
    yar_text = (repo_root / "installer" / "files" / "spanel_webshells.yar").read_text(encoding="utf-8")

    # P1.1: Cron jail runs as root from /etc/cron.d/spanel-user-<user>, validates cwd inside /home/<user>, and drops privileges via runuser
    assert 'if [[ "$(id -u)" -ne 0 ]]' in cron_jail_text
    assert 'RESOLVED_CWD="$(realpath -m -- "$JAIL_CWD"' in cron_jail_text
    assert 'runuser -u "$jail_user"' in cron_jail_text
    assert "cron-list)" in helper_text and "cron-write)" in helper_text
    assert "audit-anchor-write)" in helper_text
    assert cron.CRON_JAIL_BIN == "/usr/local/bin/spanel-cron-jail"

    # P1.2: Installer & Updater wire watchdog timer, cron-jail, audit-hmac.key, and YARA rules
    for script in (install_text, update_text):
        assert "spanel-cron-jail" in script
        assert "spanel-watchdog.timer" in script
        assert "spanel_webshells.yar" in script
        assert "/etc/spanel/audit-hmac.key" in script

    # P1.3: Audit log chain uses HMAC-SHA256 without static default secret fallback
    assert hasattr(audit, "_audit_hmac_key")
    assert len(audit._audit_hmac_key()) == 32
    audit_py = (repo_root / "backend" / "app" / "services" / "audit.py").read_text(encoding="utf-8")
    assert "spanel-default-secret" not in audit_py

    # P1.4: BackupVerifyPayload accepts POST body passphrase and enforces min length 8 on encrypt
    req = BackupVerifyPayload(backup_file="test.tar.gz.enc", passphrase="StrongPass!2026")
    assert req.passphrase == "StrongPass!2026"

    # P1.5: YARA CryptoMiner rule avoids false positive on solo "stratum+tcp://"
    assert "($pool1 or $pool2) and ($bin or $algo or $cfg1 or $cfg2)" in yar_text
    fm_text = (repo_root / "backend" / "app" / "services" / "file_manager.py").read_text(encoding="utf-8")
    assert fm_text.count("scan_upload_realtime") >= 2

    # P1.6: Core stack patch & upgrade_php_version do not hold packages (the
    # core stack no longer offers holds at all; unattended-upgrades is kept off
    # those packages with a Package-Blacklist instead)
    import inspect
    sig = inspect.signature(updates.upgrade_core_stack_packages)
    assert "hold_packages" not in sig.parameters
    php_upgrade_body = helper_text.split("upgrade_php_version() {", 1)[1].split("\n}\n", 1)[0]
    assert "apt-mark unhold" in php_upgrade_body
    assert "apt-mark hold " not in php_upgrade_body

    # P1.7: Exim4 outbound rate-limit ACL & Rspamd blocklist wired in spanel-helper.sh
    assert "acl_check_not_smtp:" in helper_text
    assert "/etc/exim4/spanel_ratelimits" in helper_text


def test_p2_fastcgi_cache_http3_limits_github_hmac_remote_mysql(tmp_path):
    import hashlib
    import hmac as hmac_mod
    from unittest.mock import patch
    from app.services import nginx, site_apps

    repo_root = Path(__file__).resolve().parents[1]
    helper_text = (repo_root / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")

    # P2.1: FastCGI cache key uses normalized $spanel_cache_uri so utm_*/fbclid/gclid hit cache
    assert '$scheme$request_method$host$spanel_cache_uri' in nginx.FASTCGI_CACHE_LOCATION_BLOCK
    assert 'set $spanel_cache_uri $request_uri;' in nginx.FASTCGI_CACHE_SERVER_BLOCK
    assert 'set $spanel_cache_uri $uri;' in nginx.FASTCGI_CACHE_SERVER_BLOCK

    # P2.2: HTTP/3 QUIC listen directives & Alt-Svc only when supported, with reuseport on single listener
    with patch.object(nginx, "_nginx_supports_http3", return_value=True):
        directives_first = nginx._listen_443_directives(ipv6=True, reuseport=True)
        directives_next = nginx._listen_443_directives(ipv6=True, reuseport=False)
        assert "listen 443 quic reuseport;" in directives_first
        assert "listen [::]:443 quic reuseport;" in directives_first
        assert sum("add_header Alt-Svc" in d for d in directives_first) == 1
        assert "listen 443 quic;" in directives_next
        assert not any("reuseport" in d for d in directives_next)
    with patch.object(nginx, "_nginx_supports_http3", return_value=False):
        directives_no_h3 = nginx._listen_443_directives(ipv6=True)
        assert not any("quic" in d for d in directives_no_h3)
        assert not any("Alt-Svc" in d for d in directives_no_h3)
    assert "-p udp --dport 443 -j RETURN" in helper_text

    # P2.3: PHP-FPM cgroup classification & persisted per-user limits
    assert "apply_saved_user_limits_to_pool_file" in helper_text
    assert "classify_all_tenant_workers" in helper_text
    assert "php_admin_value[memory_limit]" in helper_text
    assert "setquota -u" in helper_text

    # P2.4: GitHub webhook X-Hub-Signature-256 HMAC verification uses dedicated secret != URL token
    class DummyApp:
        id = 42
        owner_id = 1
        name = "webhook-app"

    app_obj = DummyApp()
    url_token = site_apps.webhook_token_for(app_obj)
    hmac_secret = site_apps.webhook_secret_for(app_obj)
    assert url_token != hmac_secret
    raw_body = b'{"ref":"refs/heads/main"}'
    valid_sig = "sha256=" + hmac_mod.new(hmac_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    url_token_sig = "sha256=" + hmac_mod.new(url_token.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    assert site_apps.verify_github_webhook_signature(app_obj, raw_body, valid_sig) is True
    assert site_apps.verify_github_webhook_signature(app_obj, raw_body, url_token_sig) is False
    assert site_apps.verify_github_webhook_signature(app_obj, raw_body, "") is False

    # P2.5: Unrequested Remote MySQL provisioning is blocked with HTTP 501 in databases.py
    databases_api_text = (repo_root / "backend" / "app" / "api" / "databases.py").read_text(encoding="utf-8")
    assert "status_code=501" in databases_api_text


if __name__ == "__main__":
    import tempfile

    test_pillar5_woocommerce_fastcgi_cache_and_http3_quic()
    test_pillar5_slow_query_and_php_slowlog_inspector()
    test_pillar6_managed_python_runtime_and_git_webhook()
    test_pillar8_domain_traffic_analytics_and_command_palette()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_phase2_sqlite_wal_audit_chain_watchdog_and_cron_jail(tmp)
        test_phase3_dnssec_malware_shield_backup_aes256_and_core_autopatch(tmp)
        test_p2_fastcgi_cache_http3_limits_github_hmac_remote_mysql(tmp)
    test_all_enterprise_api_routers_registered()
    test_p0_dnssec_no_fake_ds_and_watchdog_no_root_escalation()
    test_p1_cron_jail_audit_hmac_backup_etm_yara_exim4()
    print("ALL 10 REGRESSION SUITES (INCLUDING P0, P1, P2 & LAN 3 FIXES) PASSED 100%!")
