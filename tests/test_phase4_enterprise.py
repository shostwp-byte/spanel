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
    assert 'add_header Alt-Svc \'h3=":443"; ma=86400\' always;' in tpl_text


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
