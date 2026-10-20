"""Reusing the certificate DirectAdmin was serving.

A migration cannot obtain a Let's Encrypt certificate until DNS points at the
new server, and DNS moves last - by which time visitors are already arriving.
The archive carries the certificate the old host was serving, so the site can
answer HTTPS from the moment it is imported.

The risk is the opposite one: DirectAdmin writes a self-signed placeholder when
a domain has no real certificate, and an archive may be months old. Installing
one of those would be worse than leaving SSL off, so these tests pin down the
cases that must be refused.
"""

from pathlib import Path

import pytest

from app.services import da_import


class FakeWebsite:
    def __init__(self, domain="example.test"):
        self.domain = domain
        self.root_path = f"/home/u/{domain}"
        self.app_type = "wordpress"
        self.php_version = "8.3"
        self.document_root = "public_html"
        self.nginx_custom = ""
        self.nginx_rewrite_mode = "front_controller"
        self.waf_enabled = True
        self.aliases = []
        self.ssl_enabled = False
        self.ssl_mode = None
        self.ssl_cert_path = None
        self.ssl_key_path = None
        self.ssl_ca_path = None
        self.ssl_updated_at = None


class FakeDb:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def _archive_with_cert(tmp_path, domain="example.test", cert=b"CERT", key=b"KEY", ca=b"CA"):
    base = tmp_path / "backup" / domain
    base.mkdir(parents=True)
    if cert is not None:
        (base / "domain.cert").write_bytes(cert)
    if key is not None:
        (base / "domain.key").write_bytes(key)
    if ca is not None:
        (base / "domain.cacert").write_bytes(ca)
    return tmp_path


def test_a_usable_certificate_is_installed_and_the_vhost_rewritten(monkeypatch, tmp_path):
    root = _archive_with_cert(tmp_path)
    site, db, summary = FakeWebsite(), FakeDb(), {}
    written = {"cert": "/etc/nginx/spanel/ssl/sites/example.test/cert.crt",
               "key": "/etc/nginx/spanel/ssl/sites/example.test/privkey.key",
               "ca": "/etc/nginx/spanel/ssl/sites/example.test/ca.crt"}
    rewrites = []

    from app.services import ssl as ssl_service
    monkeypatch.setattr(ssl_service, "install_manual_ssl", lambda *a, **k: written)
    monkeypatch.setattr(da_import.nginx, "rewrite_vhost", lambda *a, **k: rewrites.append(k))

    assert da_import._import_da_certificate(db, site, root, summary) is True
    assert site.ssl_enabled is True
    assert site.ssl_mode == "manual"
    assert site.ssl_cert_path == written["cert"]
    assert summary["ssl_imported_domains"] == ["example.test"]
    # Writing the files changes nothing until the vhost names them.
    assert rewrites and rewrites[0]["ssl_cert_path"] == written["cert"]
    assert rewrites[0]["preserve_existing_ssl"] is False


def test_a_rejected_certificate_leaves_ssl_off(monkeypatch, tmp_path):
    """Expired, self-signed or mismatched: validate_manual_ssl raises, and that
    is a normal outcome rather than a failed import."""
    root = _archive_with_cert(tmp_path)
    site, db, summary = FakeWebsite(), FakeDb(), {}

    from app.services import ssl as ssl_service
    monkeypatch.setattr(ssl_service, "install_manual_ssl",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("certificate has expired")))
    monkeypatch.setattr(da_import.nginx, "rewrite_vhost",
                        lambda *a, **k: pytest.fail("must not rewrite when nothing was installed"))

    assert da_import._import_da_certificate(db, site, root, summary) is False
    assert site.ssl_enabled is False
    assert site.ssl_mode is None
    assert "ssl_imported_domains" not in summary


def test_an_archive_with_no_certificate_is_not_an_error(tmp_path):
    root = tmp_path
    (root / "backup" / "example.test").mkdir(parents=True)
    assert da_import._import_da_certificate(FakeDb(), FakeWebsite(), root, {}) is False


def test_an_empty_certificate_file_is_ignored(tmp_path):
    root = _archive_with_cert(tmp_path, cert=b"   ", key=b"KEY")
    assert da_import._import_da_certificate(FakeDb(), FakeWebsite(), root, {}) is False


def test_a_certificate_without_its_key_is_ignored(tmp_path):
    root = _archive_with_cert(tmp_path, key=None)
    assert da_import._import_da_certificate(FakeDb(), FakeWebsite(), root, {}) is False


def test_a_failed_vhost_rewrite_reports_failure(monkeypatch, tmp_path):
    """The certificate is on disk but nginx is not serving it: do not claim it."""
    root = _archive_with_cert(tmp_path)
    site, db, summary = FakeWebsite(), FakeDb(), {}
    from app.services import ssl as ssl_service
    monkeypatch.setattr(ssl_service, "install_manual_ssl",
                        lambda *a, **k: {"cert": "c", "key": "k", "ca": "a"})
    monkeypatch.setattr(da_import.nginx, "rewrite_vhost",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nginx -t failed")))

    assert da_import._import_da_certificate(db, site, root, summary) is False
    assert "ssl_imported_domains" not in summary


def test_lets_encrypt_is_preferred_when_dns_already_points_here():
    """The imported certificate does not renew itself, so it is the fallback.

    The import calls _enable_ssl_when_dns_matches first and only reaches the
    archive's certificate when that left ssl_enabled false.
    """
    source = Path(da_import.__file__).read_text(encoding="utf-8")
    block = source.split("# SSL: a fresh Let's Encrypt certificate")[1][:600]

    le = block.index("_enable_ssl_when_dns_matches")
    fallback = block.index("_import_da_certificate")
    assert le < fallback
    assert "if not website.ssl_enabled:" in block
