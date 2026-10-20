"""The panel answers each TLS handshake with the certificate for that hostname."""

import datetime
import socket
import ssl
import threading
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.core import panel_sni


def _write_certificate(directory, name, dns_names):
    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(dns) for dns in dns_names]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    (directory / "fullchain.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / "privkey.pem").write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return directory


@pytest.fixture
def sni_dir(tmp_path):
    root = tmp_path / "sni"
    _write_certificate(root / "alpha.test", "alpha.test", ["alpha.test", "www.alpha.test"])
    _write_certificate(root / "beta.test", "beta.test", ["beta.test", "*.beta.test"])
    return root


@pytest.fixture
def default_pair(tmp_path):
    directory = _write_certificate(tmp_path / "default", "panel.test", ["panel.test"])
    return directory / "fullchain.pem", directory / "privkey.pem"


def _served_common_name(server_context, hostname):
    """Complete a real handshake and report whose certificate came back."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    error: list[BaseException] = []

    def serve():
        try:
            raw, _ = listener.accept()
            with server_context.wrap_socket(raw, server_side=True) as tls:
                tls.recv(16)
                tls.send(b"ok")
        except BaseException as exc:  # noqa: BLE001 - reported to the test below
            error.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_context.check_hostname = False
    client_context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
        with client_context.wrap_socket(raw, server_hostname=hostname) as tls:
            der = tls.getpeercert(binary_form=True)
            tls.send(b"hi")
            # Under TLS 1.3 the client is done before the server has read its
            # Finished message. Wait for the reply, or closing here would abort
            # the handshake the test is trying to observe.
            tls.recv(2)
    thread.join(timeout=10)
    listener.close()
    if error:
        raise error[0]
    cert = x509.load_der_x509_certificate(der)
    return cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value


@pytest.fixture
def server_context(sni_dir, default_pair):
    certfile, keyfile = default_pair
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certfile), str(keyfile))
    store = panel_sni.SniStore(directory=sni_dir, reload_interval=0.0)
    store.install(context)
    return context


@pytest.mark.parametrize(
    "hostname,expected",
    [
        ("alpha.test", "alpha.test"),
        ("www.alpha.test", "alpha.test"),  # an alias in the same certificate
        ("beta.test", "beta.test"),
        ("panel.beta.test", "beta.test"),  # covered by the wildcard
        ("nothing.test", "panel.test"),  # no certificate of its own
    ],
)
def test_handshake_serves_the_certificate_for_the_requested_hostname(
    server_context, hostname, expected
):
    assert _served_common_name(server_context, hostname) == expected


def test_store_lists_every_hostname_it_can_serve(sni_dir):
    store = panel_sni.SniStore(directory=sni_dir, reload_interval=0.0)
    assert store.hostnames() == ["*.beta.test", "alpha.test", "beta.test", "www.alpha.test"]


def test_a_new_certificate_is_picked_up_without_a_restart(sni_dir):
    store = panel_sni.SniStore(directory=sni_dir, reload_interval=0.0)
    assert store.context_for("gamma.test") is None
    _write_certificate(sni_dir / "gamma.test", "gamma.test", ["gamma.test"])
    assert store.context_for("gamma.test") is not None


def test_a_removed_certificate_stops_being_served(sni_dir):
    store = panel_sni.SniStore(directory=sni_dir, reload_interval=0.0)
    assert store.context_for("alpha.test") is not None
    for item in (sni_dir / "alpha.test").iterdir():
        item.unlink()
    (sni_dir / "alpha.test").rmdir()
    assert store.context_for("alpha.test") is None


def test_an_unreadable_certificate_does_not_take_the_panel_down(sni_dir):
    (sni_dir / "broken.test").mkdir()
    (sni_dir / "broken.test" / "fullchain.pem").write_text("not a certificate", encoding="utf-8")
    (sni_dir / "broken.test" / "privkey.pem").write_text("not a key", encoding="utf-8")
    store = panel_sni.SniStore(directory=sni_dir, reload_interval=0.0)
    assert store.context_for("broken.test") is None
    assert store.context_for("alpha.test") is not None


def test_hostname_matching_ignores_case_port_and_root_dot(sni_dir):
    store = panel_sni.SniStore(directory=sni_dir, reload_interval=0.0)
    assert store.context_for("ALPHA.test") is not None
    assert store.context_for("alpha.test:2222") is not None
    assert store.context_for("alpha.test.") is not None
    # A wildcard covers one label, not two.
    assert store.context_for("a.b.beta.test") is None


def test_a_missing_directory_is_not_an_error(tmp_path):
    store = panel_sni.SniStore(directory=tmp_path / "absent", reload_interval=0.0)
    assert store.hostnames() == []
    assert store.context_for("alpha.test") is None


PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER_SCRIPT = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


def test_the_helper_keeps_a_certificate_copy_the_panel_can_read():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert 'PANEL_SNI_DIR="/etc/spanel/sni"' in helper
    assert "sync_panel_sni_certificates()" in helper
    assert "panel-sni-sync)" in helper
    # Root-owned, readable by the panel's group and by nobody else.
    assert 'install -d -o root -g spanel -m 0750 "$target"' in helper
    assert 'install -m 0640 -o root -g spanel "$cert" "${target}/fullchain.pem"' in helper
    assert 'install -m 0640 -o root -g spanel "$key" "${target}/privkey.pem"' in helper
    # Both kinds of certificate on the machine end up in the store.
    assert "for live_dir in /etc/letsencrypt/live/*/; do" in helper
    assert "for manual_dir in /etc/nginx/spanel/ssl/sites/*/; do" in helper


def test_a_renewal_refreshes_the_copy_without_a_restart():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "install_sni_renewal_hook()" in helper
    assert "/etc/letsencrypt/renewal-hooks/deploy/spanel-sni-certs" in helper
    body = helper.split("install_sni_renewal_hook()", 1)[1]
    hook = body.split("<<'HOOK'", 1)[1].split("\nHOOK", 1)[0]
    assert 'basename "$RENEWED_LINEAGE"' in hook
    # The panel re-reads the files by itself, so the hook must not bounce it.
    assert "systemctl restart spanel-api" not in hook


def test_issuing_or_uploading_a_certificate_updates_the_store():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert helper.count("sync_panel_sni_certificates >/dev/null") >= 4
    # certbot-issue used to hand the process over to certbot with exec, which
    # would have skipped the sync that follows it.
    assert 'exec certbot "${install_args[@]}"' not in helper


def test_the_panel_is_started_through_the_sni_aware_entry_point():
    for script_path in (INSTALL_SCRIPT, UPDATE_SCRIPT):
        script = script_path.read_text(encoding="utf-8")
        assert "/backend/.venv/bin/python -m app.serve" in script
        # The old starter passed one certificate on the uvicorn command line,
        # which is exactly what pinned the panel to a single hostname.
        assert "args=(app.main:app" not in script
        assert "--ssl-certfile" not in script
        assert "/usr/local/sbin/spanel-helper panel-sni-sync" in script
