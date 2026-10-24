from pathlib import Path
from app.services.nginx import (
    FASTCGI_CACHE_SERVER_BLOCK,
    FASTCGI_CACHE_LOCATION_BLOCK,
)
from app.services.resource_monitor import inspect_slow_bottlenecks, collect_domain_traffic_analytics
from app.services.site_apps import (
    PYTHON_START_KINDS,
    validate_start,
    webhook_token_for,
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
    # Alt-Svc belongs to the HTTPS listen block, not the cache block: a plain
    # HTTP vhost or an Nginx without the v3 module must not advertise h3.
    assert "Alt-Svc" not in tpl_text
    assert "Alt-Svc" not in FASTCGI_CACHE_SERVER_BLOCK


def test_alt_svc_only_with_quic(monkeypatch):
    from app.services import nginx

    monkeypatch.setattr(nginx, "_nginx_supports_http3", lambda: False)
    block = nginx._https_listen_block("203.0.113.5")
    assert "listen 203.0.113.5:443 ssl http2;" in block
    assert "quic" not in block
    assert "Alt-Svc" not in block

    monkeypatch.setattr(nginx, "_nginx_supports_http3", lambda: True)
    block = nginx._https_listen_block("203.0.113.5")
    assert "listen 203.0.113.5:443 quic;" in block
    assert "add_header Alt-Svc 'h3=\":443\"; ma=86400' always;" in block


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
    assert len(token1) == 32
    assert token1 == token2
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
    from datetime import datetime
    from app.core.database import get_sqlite_health, _set_sqlite_pragma
    from app.models.entities import AuditLog, WatchdogIncident
    from app.services.audit import _compute_entry_hash
    from app.services.cron import (
        CRON_JAIL_BIN,
        _parse_cron_line,
        _validate_command,
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
    site_root = tmp_path / "home" / "alice" / "domains" / "example.com"
    doc_root = site_root / "public_html"
    doc_root.mkdir(parents=True, exist_ok=True)
    (doc_root / "cron.php").write_text("<?php echo 1;", encoding="utf-8")
    rendered = _validate_command("php cron.php", doc_root, site_root, "php", "alice")
    assert "open_basedir=" in rendered
    assert "disable_functions=" in rendered
    cron_line = f"*/5 * * * * cd {doc_root} && {CRON_JAIL_BIN} alice {doc_root} -- {rendered} # spanel:example.com"
    parsed = _parse_cron_line(0, cron_line)
    assert parsed["schedule"] == "*/5 * * * *"
    assert parsed["command"].endswith("cron.php")
    assert CRON_JAIL_BIN not in parsed["command"]


def test_phase3_dnssec_malware_shield_backup_aes256_and_core_autopatch(tmp_path):
    import tarfile
    from app.services.backup import (
        VALID_RESTORE_MODES,
        decrypt_backup_archive,
        encrypt_backup_archive,
        is_encrypted_backup,
        verify_backup_integrity,
    )
    from app.services.dns import _parse_ds_record
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
    enc_verify = verify_backup_integrity(enc_path, passphrase="StrongPass!2026")
    assert enc_verify["valid"] is True
    assert enc_verify["encrypted"] is True
    assert enc_verify["cipher"] == "AES-256-CBC-PBKDF2-SHA256"
    assert enc_verify["member_count"] == 1

    dec_path = tmp_path / "restored.tar.gz"
    decrypt_backup_archive(enc_path, "StrongPass!2026", output_path=dec_path)
    assert is_encrypted_backup(dec_path) is False
    assert verify_backup_integrity(dec_path)["valid"] is True

    # 2. Real-time upload malware shield (< 200ms + YARA/Heuristic + Auto-Quarantine)
    disguised_elf = tmp_path / "avatar.jpg"
    disguised_elf.write_bytes(b"\x7fELF\x02\x01\x01\x00FakeDisguisedBinary")
    scan_res = scan_upload_realtime(str(disguised_elf), domain="example.com", auto_quarantine=False)
    assert scan_res["status"] == "infected"
    assert scan_res["under_200ms"] is True
    assert scan_res["signature"] == "SPanel.Heuristic.ELF.DisguisedBinaryInWebroot"

    # 3. DNSSEC DS record parser
    ds_info = _parse_ds_record("example.com", "12345 13 2 ABCDEF0123456789")
    assert ds_info["key_tag"] == 12345
    assert ds_info["algorithm"] == 13
    assert ds_info["digest_type"] == 2

    # 4. Core Stack Auto-Patch
    assert len(CORE_STACK_SERVICES) >= 7
    cs_status = check_core_stack_updates()
    assert "services" in cs_status and len(cs_status["services"]) == len(CORE_STACK_SERVICES)

    # 5. Helper script regressions check (Web Terminal unshare + snapshot update-state cleanup + core-stack-patch)
    helper_text = (
        Path(__file__).resolve().parents[1]
        / "installer"
        / "files"
        / "spanel-helper.sh"
    ).read_text(encoding="utf-8")
    assert '"${terminal_runner[@]}"' in helper_text
    assert "rm -rf /var/lib/spanel/update-state" in helper_text
    assert "updates-core-stack-patch)" in helper_text
    assert "apt-mark unhold" in helper_text and "apt-mark hold" in helper_text


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
    test_all_enterprise_api_routers_registered()
    print("ALL 7 ENTERPRISE REGRESSION SUITES (PHASE 1 + 2 + 3 + 4 + ROUTERS + HELPER FIXES) PASSED 100%!")
