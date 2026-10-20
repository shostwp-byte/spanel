import pytest

from app.services import nginx


def test_wordpress_csp_allows_gutenberg_blob_iframe():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="wordpress",
        php_version="8.3",
    )

    assert "frame-src 'self' https: blob:;" in rendered
    assert "worker-src 'self' blob:;" in rendered


def test_php_vhost_defaults_to_static_try_files():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="php",
        php_version="8.3",
    )

    assert "try_files $uri $uri/ =404;" in rendered
    assert "try_files $uri $uri/ /index.php?$query_string;" not in rendered


def test_vhost_disables_tenant_symlink_following():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="php",
        php_version="8.3",
    )

    line = next(line.strip() for line in rendered.splitlines() if line.strip().startswith("disable_symlinks "))
    assert line.replace("\\", "/").endswith("/home/bp_example_test/example.test;")


def test_laravel_vhost_allows_same_owner_storage_symlink_policy():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="php",
        php_version="8.3",
        rewrite_mode="laravel",
    )

    assert "disable_symlinks if_not_owner" in rendered
    assert "disable_symlinks on" not in rendered
    assert "try_files $uri $uri/ /index.php?$query_string;" in rendered


def test_vhost_includes_alias_domains_in_server_name():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="php",
        php_version="8.3",
        aliases=["alias.test", "www.example.test", "alias.test"],
    )

    assert "server_name example.test www.example.test alias.test;" in rendered


def test_vhost_renders_redirect_domains_as_separate_301_servers():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="php",
        php_version="8.3",
        aliases=["app-alias.test"],
        redirects=["old.example.test"],
    )

    assert "server_name example.test www.example.test app-alias.test;" in rendered
    assert "server_name old.example.test;" in rendered
    assert "location ^~ /.well-known/acme-challenge/ {" in rendered
    assert "root /var/www/spanel-acme;" in rendered
    assert "return 301 https://example.test$request_uri;" in rendered


def test_manual_ssl_vhost_adds_https_redirect_server_for_redirect_domains():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="php",
        php_version="8.3",
        redirects=["old.example.test"],
        ssl_cert_path="/etc/nginx/spanel/ssl/sites/example.test/cert.crt",
        ssl_key_path="/etc/nginx/spanel/ssl/sites/example.test/privkey.key",
    )

    assert "listen 443 ssl http2;" in rendered
    assert "server_name old.example.test;" in rendered
    assert "location ^~ /.well-known/acme-challenge/ {" in rendered
    assert "root /var/www/spanel-acme;" in rendered
    assert "ssl_certificate /etc/nginx/spanel/ssl/sites/example.test/cert.crt;" in rendered
    assert "return 301 https://example.test$request_uri;" in rendered


def test_ssl_paths_accept_the_two_spanel_roots_and_nothing_else():
    # uploaded certs
    nginx._manual_ssl_paths(
        "/etc/nginx/spanel/ssl/sites/example.test/cert.crt",
        "/etc/nginx/spanel/ssl/sites/example.test/privkey.key",
    )
    # borrowed / wildcard certs point at the certbot lineage
    cert, key, ca = nginx._manual_ssl_paths(
        "/etc/letsencrypt/live/example.test/fullchain.pem",
        "/etc/letsencrypt/live/example.test/privkey.pem",
    )
    assert cert.endswith("/fullchain.pem") and ca is None
    for bad in ("/etc/passwd", "/etc/letsencrypt/../shadow", "/home/x/cert.pem"):
        with pytest.raises(ValueError):
            nginx._manual_ssl_paths(bad, "/etc/letsencrypt/live/example.test/privkey.pem")


def test_borrowed_wildcard_cert_is_wired_verbatim():
    rendered = nginx.render_vhost(
        "blog.example.test",
        "/home/bp_example/blog.example.test",
        app_type="php",
        php_version="8.3",
        ssl_cert_path="/etc/letsencrypt/live/example.test/fullchain.pem",
        ssl_key_path="/etc/letsencrypt/live/example.test/privkey.pem",
    )
    assert "ssl_certificate /etc/letsencrypt/live/example.test/fullchain.pem;" in rendered
    assert "ssl_certificate_key /etc/letsencrypt/live/example.test/privkey.pem;" in rendered


def test_vhost_exists_reports_a_config_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(nginx.settings, "nginx_sites_available", str(tmp_path))
    assert nginx.vhost_exists("example.test") is False
    (tmp_path / "example.test.conf").write_text("server {}\n")
    assert nginx.vhost_exists("example.test") is True
    assert nginx.vhost_exists("EXAMPLE.TEST") is True   # case-insensitive
    assert nginx.vhost_exists("not a domain") is False


def test_certbot_ssl_vhost_enables_http2():
    plain_vhost = "\n".join([
        "server {",
        "    listen 80;",
        "    server_name example.test www.example.test;",
        "}",
    ])
    certbot_vhost = "\n".join([
        "server {",
        "    listen 443 ssl;",
        "    server_name example.test www.example.test;",
        "    ssl_certificate /etc/letsencrypt/live/example.test/fullchain.pem;",
        "    ssl_certificate_key /etc/letsencrypt/live/example.test/privkey.pem;",
        "}",
    ])

    rendered = nginx._merge_certbot_ssl_config(plain_vhost, certbot_vhost)

    assert "listen 443 ssl http2;" in rendered


def test_certbot_ssl_redirect_vhost_enables_http2():
    certbot_vhost = "\n".join([
        "server {",
        "    listen 443 ssl;",
        "    server_name example.test www.example.test;",
        "    ssl_certificate /etc/letsencrypt/live/example.test/fullchain.pem;",
        "    ssl_certificate_key /etc/letsencrypt/live/example.test/privkey.pem;",
        "}",
    ])

    rendered = nginx._append_certbot_redirect_vhosts(certbot_vhost, "example.test", ["old.example.test"])

    assert "listen 443 ssl http2;" in rendered
    assert "server_name old.example.test;" in rendered


def test_php_vhost_supports_front_controller_rewrite_mode():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="php",
        php_version="8.3",
        rewrite_mode="front_controller",
    )

    assert "try_files $uri $uri/ /index.php?$query_string;" in rendered


def test_php_vhost_supports_laravel_rewrite_mode_without_changing_root():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="php",
        php_version="8.3",
        rewrite_mode="laravel",
    )

    root_line = next(line.strip() for line in rendered.splitlines() if line.strip().startswith("root "))
    assert root_line.replace("\\", "/").endswith("public_html/public;")
    assert "try_files $uri $uri/ /index.php?$query_string;" in rendered


def test_php_vhost_laravel_rewrite_mode_does_not_double_public_root():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="php",
        php_version="8.3",
        document_root="public_html/public",
        rewrite_mode="laravel",
    )

    root_line = next(line.strip() for line in rendered.splitlines() if line.strip().startswith("root "))
    normalized = root_line.replace("\\", "/")
    assert normalized.endswith("public_html/public;")
    assert "public_html/public/public" not in normalized


def test_php_vhost_supports_codeigniter_rewrite_mode():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="php",
        php_version="8.3",
        rewrite_mode="codeigniter",
    )

    assert "try_files $uri $uri/ /index.php?$query_string;" in rendered


def test_php_vhost_supports_seohburl_rewrite_mode():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="php",
        php_version="8.3",
        rewrite_mode="seohburl",
    )

    assert "try_files $uri $uri/ @seohburl;" in rendered
    assert "location @seohburl" in rendered
    assert "rewrite ^/(.+)$ /index.php?/$1 last;" in rendered


def test_ensure_hsts_header_adds_gutenberg_safe_wordpress_csp():
    content = '\n'.join([
        "server {",
        "    server_tokens off;",
        '    add_header X-XSS-Protection "1; mode=block" always;',
        "}",
        "",
    ])

    hardened = nginx._ensure_hsts_header(content)

    assert "frame-src 'self' https: blob:;" in hardened
    assert "worker-src 'self' blob:;" in hardened


# --- proxied app types ------------------------------------------------------

def _proxy_vhost(app_type="application", app_port=21000, **kwargs):
    return nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type=app_type,
        php_version=None,
        app_port=app_port,
        **kwargs,
    )


def test_proxy_vhost_forwards_to_the_loopback_app_port():
    rendered = _proxy_vhost(app_port=21042)

    assert "proxy_pass http://127.0.0.1:21042;" in rendered
    # A proxied site must never hand a request to PHP-FPM.
    assert "fastcgi_pass" not in rendered


def test_the_application_mode_is_the_only_proxied_one():
    assert nginx.PROXIED_APP_TYPES == {"application"}
    assert "proxy" not in nginx.ALLOWED_APP_TYPES
    assert "nodejs" not in nginx.ALLOWED_APP_TYPES


def test_proxy_vhost_supports_websocket_upgrade():
    rendered = _proxy_vhost()

    assert "proxy_http_version 1.1;" in rendered
    assert "proxy_set_header Upgrade $http_upgrade;" in rendered
    assert "proxy_set_header Connection $connection_upgrade;" in rendered


def test_proxy_vhost_passes_the_original_scheme_and_client_ip():
    rendered = _proxy_vhost()

    for header in (
        "proxy_set_header Host $host;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
    ):
        assert header in rendered


def test_proxy_vhost_keeps_serving_acme_challenges_from_disk():
    """Certificate renewal must not depend on the application being up."""
    rendered = _proxy_vhost()

    assert "location ^~ /.well-known/acme-challenge/ {" in rendered
    assert "root /var/www/spanel-acme;" in rendered


def test_proxy_vhost_streams_instead_of_buffering():
    assert "proxy_buffering off;" in _proxy_vhost()


def test_proxy_vhost_requires_an_app_port():
    import pytest

    for missing in (None, "", "not-a-port"):
        with pytest.raises(ValueError, match="installed application"):
            _proxy_vhost(app_port=missing)


def test_app_port_is_ignored_for_php_sites():
    rendered = nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="php",
        php_version="8.3",
        app_port=21000,
    )

    assert "proxy_pass" not in rendered
