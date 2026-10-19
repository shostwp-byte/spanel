import json
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, ed448, padding, rsa
from cryptography.x509.oid import ExtensionOID, NameOID

from app.core.config import settings
from app.services.shell import CommandResult, shell


MAX_SSL_PART_BYTES = 256 * 1024
ALLOWED_SSL_EXTENSIONS = {".crt", ".pem", ".key", ".ca"}
MANUAL_SSL_ROOT = Path("/etc/nginx/spanel/ssl/sites")


class ManualSslSnapshot:
    def __init__(self, cert_path: str, key_path: str, ca_path: str | None, domain: str | None = None):
        cert = Path(cert_path)
        self.domain = _safe_domain(domain or cert.parent.name)
        self.cert_path = cert
        self.key_path = Path(key_path)
        self.ca_path = Path(ca_path) if ca_path else None
        self.paths = [cert, Path(key_path), cert.with_name("fullchain.crt")]
        if ca_path:
            self.paths.append(Path(ca_path))
        self.contents: dict[Path, bytes | None] = {}

    def capture(self) -> None:
        for path in self.paths:
            try:
                self.contents[path] = path.read_bytes()
            except OSError:
                self.contents[path] = None

    def restore(self) -> None:
        cert = self.contents.get(self.cert_path)
        key = self.contents.get(self.key_path)
        ca = self.contents.get(self.ca_path) if self.ca_path else None
        if cert and key:
            _write_manual_ssl_files(self.domain, cert, key, ca or b"")
        else:
            remove_manual_ssl(self.domain)


def _safe_domain_list(domain: str, aliases: list[str] | tuple[str, ...] | None = None) -> list[str]:
    names = [_safe_domain(domain)]
    seen = set(names)
    for alias in aliases or []:
        safe_alias = _safe_domain(str(alias))
        if safe_alias in seen:
            continue
        names.append(safe_alias)
        seen.add(safe_alias)
    return names


def issue_ssl(domain: str, aliases: list[str] | tuple[str, ...] | None = None) -> CommandResult:
    # nginx's own vhost always listens on www.<domain> too (see
    # nginx._server_names) whether or not anyone added it as an alias - a
    # certificate that only covers the bare domain leaves every visitor who
    # types "www." with a hard TLS mismatch instead of the site. Request it
    # by default here, the one place all four SSL-issuing call sites share,
    # rather than in _safe_domain_list: that helper also builds the list a
    # manually-uploaded certificate is checked against, and plenty of real
    # certificates only cover the bare domain on purpose.
    safe_domain = _safe_domain(domain)
    extra_aliases = list(aliases or [])
    if not safe_domain.startswith("www."):
        existing = {_safe_domain(str(a)) for a in extra_aliases}
        www_domain = f"www.{safe_domain}"
        if www_domain not in existing:
            extra_aliases.append(www_domain)
    domains = _safe_domain_list(domain, extra_aliases)
    helper_args = domains[:]
    fallback = ["certbot", "--nginx"]
    for name in domains:
        fallback.extend(["-d", name])
    fallback.extend(["--non-interactive", "--agree-tos", "--redirect", "--expand", "--allow-subset-of-names"])
    if settings.ssl_email:
        helper_args.append(settings.ssl_email)
        fallback.extend(["--email", settings.ssl_email])
    else:
        fallback.append("--register-unsafely-without-email")
    return shell.privileged("certbot-issue", helper_args=helper_args, check=False, fallback=fallback)


def renew_all() -> CommandResult:
    return shell.privileged("certbot-renew", check=False, fallback=["certbot", "renew", "--quiet"])


# --- wildcard via Cloudflare DNS-01 -----------------------------------------

LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")


def ensure_cloudflare_plugin() -> CommandResult:
    return shell.privileged(
        "certbot-dns-cloudflare-install",
        check=False,
        fallback=["bash", "-lc", "apt-get install -y python3-certbot-dns-cloudflare"],
    )


def issue_wildcard_ssl(zone: str, token: str, email: str = "") -> CommandResult:
    """Issue (or renew, idempotently) a cert covering ``zone`` and ``*.zone``.

    The token is handed to the helper on stdin so it never reaches a process
    list or the sudo log.
    """
    safe_zone = _safe_domain(zone)
    helper_args = [safe_zone]
    if email:
        helper_args.append(email)
    return shell.privileged(
        "cloudflare-ssl-issue",
        helper_args=helper_args,
        check=False,
        input=token,
        sensitive=True,
        fallback=["bash", "-lc", "echo 'cloudflare-ssl-issue needs the spanel helper'; exit 1"],
    )


def letsencrypt_live_paths(name: str) -> dict[str, str]:
    safe = _safe_domain(name)
    base = f"/etc/letsencrypt/live/{safe}"
    return {"cert": f"{base}/fullchain.pem", "key": f"{base}/privkey.pem", "ca": None}


def cert_info(name: str) -> dict:
    """Expiry and covered names for a certificate on this machine.

    Reads via the helper because /etc/letsencrypt/live is root-only.
    """
    safe = _safe_domain(name)
    result = shell.privileged(
        "ssl-cert-info",
        helper_args=[safe],
        check=False,
        fallback=["bash", "-lc", f"echo 'no cert info for {safe}'; exit 1"],
    )
    info: dict = {"not_after": "", "sans": []}
    if result.returncode != 0:
        return info
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition("=")
        if key == "not_after":
            info["not_after"] = value.strip()
        elif key == "sans":
            info["sans"] = [name for name in value.strip().split(",") if name]
    return info


def cert_covers(sans: list[str] | tuple[str, ...], domain: str) -> bool:
    target = _safe_domain(domain)
    return any(_hostname_matches(target, name.strip().lower()) for name in sans if name.strip())


def delete_ssl(domain: str) -> CommandResult:
    """Remove every certificate this server holds for ``domain``.

    Covers the Let's Encrypt lineage (and its renewal config, so certbot stops
    trying) and any uploaded certificate. Callers should go through
    :func:`release_site_certificates`, which first checks nothing else is still
    being served from the same lineage.
    """
    safe_domain = _safe_domain(domain)
    return shell.privileged(
        "certbot-delete",
        helper_args=[safe_domain],
        check=False,
        fallback=["certbot", "delete", "--cert-name", safe_domain, "--non-interactive"],
    )


def release_site_certificates(db, domain: str, *, exclude_website_id: int | None = None) -> str:
    """Drop the certificates a deleted site leaves behind.

    One certificate often covers more than the site it was issued for - an
    alias, a subdomain added later, the hostname the panel itself answers on.
    Deleting the lineage in that case would take a *live* site's HTTPS down, so
    anything still covering a name this server hosts is left alone and named in
    the returned message.

    Never raises: a website must still be deletable when certbot is unhappy.
    """
    # Imported here so ssl.py stays usable (and testable) without the ORM, the
    # same way panel_settings reaches for nginx only when it needs it.
    from app.models.entities import Website, WebsiteAlias
    from app.services import panel_settings

    try:
        safe_domain = _safe_domain(domain)
    except ValueError:
        return ""

    keep: set[str] = set()
    try:
        query = db.query(Website.domain)
        if exclude_website_id is not None:
            query = query.filter(Website.id != exclude_website_id)
        for (other,) in query.all():
            if other and other.strip().lower() != safe_domain:
                keep.add(other.strip().lower())
        alias_query = db.query(WebsiteAlias.domain)
        if exclude_website_id is not None:
            alias_query = alias_query.filter(WebsiteAlias.website_id != exclude_website_id)
        for (other,) in alias_query.all():
            if other and other.strip().lower() != safe_domain:
                keep.add(other.strip().lower())
    except Exception:  # pragma: no cover - a broken query must not block deletion
        return ""

    # The panel's own hostname is not a website row when an admin pointed it at
    # a domain by hand, so read it separately.
    try:
        panel_host = panel_settings.parse_panel_url(panel_settings.configured_panel_url())[1]
        if panel_host:
            keep.add(panel_host.strip().lower())
    except Exception:  # pragma: no cover - an unset panel URL is normal
        pass

    if keep:
        try:
            sans = cert_info(safe_domain).get("sans") or []
        except Exception:  # pragma: no cover - helper failure
            sans = []
        still_used = sorted(name for name in keep if cert_covers(sans, name))
        if still_used:
            return f"kept the certificate for {safe_domain}: still covers {', '.join(still_used)}"

    try:
        result = delete_ssl(safe_domain)
    except Exception as exc:  # pragma: no cover - helper failure
        return f"could not remove the certificate for {safe_domain}: {exc}"
    if result.returncode != 0:
        return f"could not remove the certificate for {safe_domain}: {(result.stderr or result.stdout or '').strip()}"
    return (result.stdout or "").strip()


def manual_ssl_paths(domain: str) -> dict[str, str | None]:
    safe_domain = _safe_domain(domain)
    base = f"/etc/nginx/spanel/ssl/sites/{safe_domain}"
    return {
        "cert": f"{base}/cert.crt",
        "key": f"{base}/privkey.key",
        "ca": f"{base}/ca.crt",
    }


def read_ssl_part(value, *, label: str, required: bool = True) -> bytes:
    if value is None:
        if required:
            raise ValueError(f"{label} is required")
        return b""
    filename = getattr(value, "filename", None)
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_SSL_EXTENSIONS:
            raise ValueError(f"{label} must be .crt, .pem, .key, or .ca")
    if hasattr(value, "file"):
        raw = _read_limited(value.file, label)
    elif isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode("utf-8")
    return _normalize_pem(raw, label, required=required)


def validate_manual_ssl(
    domain: str,
    certificate: bytes,
    private_key: bytes,
    ca_bundle: bytes = b"",
    aliases: list[str] | tuple[str, ...] | None = None,
) -> None:
    domains = _safe_domain_list(domain, aliases)
    cert = _load_certificate(certificate, "certificate")
    key = _load_private_key(private_key)
    if ca_bundle:
        _load_ca_bundle(ca_bundle)
    _validate_certificate_time(cert)
    for name in domains:
        _validate_certificate_domain(cert, name)
    _validate_key_matches_certificate(key, cert)


def install_manual_ssl(
    domain: str,
    certificate: bytes,
    private_key: bytes,
    ca_bundle: bytes = b"",
    aliases: list[str] | tuple[str, ...] | None = None,
) -> dict[str, str | None]:
    validate_manual_ssl(domain, certificate, private_key, ca_bundle, aliases=aliases)
    return _write_manual_ssl_files(domain, certificate, private_key, ca_bundle)


def _write_manual_ssl_files(domain: str, certificate: bytes, private_key: bytes, ca_bundle: bytes = b"") -> dict[str, str | None]:
    paths = manual_ssl_paths(domain)
    payload = json.dumps(
        {
            "certificate": certificate.decode("utf-8"),
            "private_key": private_key.decode("utf-8"),
            "ca_bundle": ca_bundle.decode("utf-8") if ca_bundle else "",
        }
    )
    fallback = [
        "python",
        "-c",
        (
            "import json,pathlib,sys;"
            "domain=sys.argv[1];"
            "base=pathlib.Path('/etc/nginx/spanel/ssl/sites')/domain;"
            "base.mkdir(parents=True, exist_ok=True);"
            "data=json.load(sys.stdin);"
            "(base/'cert.crt').write_text(data['certificate'], encoding='utf-8');"
            "(base/'privkey.key').write_text(data['private_key'], encoding='utf-8');"
            "ca=data.get('ca_bundle') or '';"
            "(base/'ca.crt').write_text(ca, encoding='utf-8') if ca else (base/'ca.crt').unlink(missing_ok=True)"
        ),
        _safe_domain(domain),
    ]
    result = shell.privileged(
        "manual-ssl-install",
        helper_args=[_safe_domain(domain)],
        check=False,
        input=payload,
        sensitive=True,
        fallback=fallback,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not install manual SSL").strip())
    if not ca_bundle:
        paths["ca"] = None
    return paths


def snapshot_manual_ssl(cert_path: str | None, key_path: str | None, ca_path: str | None) -> ManualSslSnapshot | None:
    if not cert_path or not key_path:
        return None
    snapshot = ManualSslSnapshot(cert_path, key_path, ca_path)
    snapshot.capture()
    return snapshot


def snapshot_manual_ssl_domain(domain: str) -> ManualSslSnapshot:
    paths = manual_ssl_paths(domain)
    snapshot = ManualSslSnapshot(paths["cert"], paths["key"], paths["ca"], domain=domain)
    snapshot.capture()
    return snapshot


def restore_manual_ssl(snapshot: ManualSslSnapshot | None) -> None:
    if snapshot is not None:
        try:
            snapshot.restore()
        except (RuntimeError, OSError):
            pass


def remove_manual_ssl(domain: str) -> None:
    safe_domain = _safe_domain(domain)
    fallback = [
        "python",
        "-c",
        (
            "import pathlib,sys;"
            "base=pathlib.Path('/etc/nginx/spanel/ssl/sites')/sys.argv[1];"
            "[(base/name).unlink(missing_ok=True) for name in ('cert.crt','privkey.key','ca.crt','fullchain.crt')];"
            "base.rmdir() if base.exists() and not any(base.iterdir()) else None"
        ),
        safe_domain,
    ]
    result = shell.privileged("manual-ssl-remove", helper_args=[safe_domain], check=False, fallback=fallback)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not remove manual SSL").strip())


def remove_manual_ssl_files(cert_path: str | None, key_path: str | None, ca_path: str | None) -> None:
    if cert_path:
        try:
            remove_manual_ssl(Path(cert_path).parent.name)
            return
        except (RuntimeError, ValueError, OSError):
            pass
    for raw_path in (cert_path, key_path, ca_path):
        if raw_path:
            try:
                Path(raw_path).unlink(missing_ok=True)
            except OSError:
                pass
    if cert_path:
        try:
            Path(cert_path).with_name("fullchain.crt").unlink(missing_ok=True)
        except OSError:
            pass


def _read_limited(handle: BinaryIO, label: str) -> bytes:
    raw = handle.read(MAX_SSL_PART_BYTES + 1)
    if len(raw) > MAX_SSL_PART_BYTES:
        raise ValueError(f"{label} is too large")
    return raw


def _normalize_pem(raw: bytes, label: str, *, required: bool) -> bytes:
    if not raw:
        if required:
            raise ValueError(f"{label} is required")
        return b""
    if b"\x00" in raw:
        raise ValueError(f"{label} contains a NUL byte")
    try:
        text = raw.decode("utf-8", errors="strict").replace("\r\n", "\n").strip()
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8 PEM text") from exc
    if not text:
        if required:
            raise ValueError(f"{label} is required")
        return b""
    return (text + "\n").encode("utf-8")


def _safe_domain(domain: str) -> str:
    safe = (domain or "").strip().lower()
    if not safe or "/" in safe or "\\" in safe or ".." in safe:
        raise ValueError("Invalid domain")
    return safe


def _load_certificate(raw: bytes, label: str):
    try:
        return x509.load_pem_x509_certificate(raw)
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid PEM certificate") from exc


def _load_ca_bundle(raw: bytes) -> None:
    try:
        certs = x509.load_pem_x509_certificates(raw)
    except ValueError as exc:
        raise ValueError("ca_bundle is not a valid PEM certificate bundle") from exc
    if not certs:
        raise ValueError("ca_bundle is not a valid PEM certificate bundle")


def _load_private_key(raw: bytes):
    try:
        return serialization.load_pem_private_key(raw, password=None)
    except (TypeError, ValueError) as exc:
        raise ValueError("private_key is not a valid unencrypted PEM private key") from exc


def _validate_certificate_time(cert) -> None:
    now = datetime.now(timezone.utc)
    not_before = cert.not_valid_before_utc
    not_after = cert.not_valid_after_utc
    if now < not_before:
        raise ValueError("certificate is not valid yet")
    if now >= not_after:
        raise ValueError("certificate is expired")


def _validate_certificate_domain(cert, domain: str) -> None:
    names: set[str] = set()
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        names.update(name.lower() for name in san.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        pass
    if not names:
        names.update(attr.value.lower() for attr in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME))
    if not any(_hostname_matches(domain, name) for name in names):
        raise ValueError("certificate CN/SAN does not match the website domain")


def _hostname_matches(domain: str, pattern: str) -> bool:
    if pattern == domain:
        return True
    if not pattern.startswith("*."):
        return False
    suffix = pattern[1:]
    return domain.endswith(suffix) and domain.count(".") == suffix.count(".")


def _validate_key_matches_certificate(private_key, cert) -> None:
    cert_public = cert.public_key()
    try:
        if isinstance(private_key, rsa.RSAPrivateKey) and isinstance(cert_public, rsa.RSAPublicKey):
            message = b"spanel-manual-ssl-check"
            signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
            cert_public.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
            return
        if isinstance(private_key, ec.EllipticCurvePrivateKey) and isinstance(cert_public, ec.EllipticCurvePublicKey):
            message = b"spanel-manual-ssl-check"
            signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
            cert_public.verify(signature, message, ec.ECDSA(hashes.SHA256()))
            return
        if isinstance(private_key, ed25519.Ed25519PrivateKey) and isinstance(cert_public, ed25519.Ed25519PublicKey):
            message = b"spanel-manual-ssl-check"
            cert_public.verify(private_key.sign(message), message)
            return
        if isinstance(private_key, ed448.Ed448PrivateKey) and isinstance(cert_public, ed448.Ed448PublicKey):
            message = b"spanel-manual-ssl-check"
            cert_public.verify(private_key.sign(message), message)
            return
    except Exception as exc:
        raise ValueError("private_key does not match certificate") from exc
    if _public_bytes(private_key.public_key()) != _public_bytes(cert_public):
        raise ValueError("private_key does not match certificate")


def _public_bytes(public_key) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
