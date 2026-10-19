"""Passkeys (WebAuthn), bound to the hostname the panel is being reached on.

Two things shape everything here.

**A credential belongs to a hostname.** WebAuthn's Relying Party ID is a
domain, and a browser will not so much as admit that a credential exists to any
other name. app/serve.py answers on every hostname on the machine that has a
certificate, so the panel derives the RP ID from the request rather than from a
setting, and stores it on the credential. An account can then hold one passkey
per name it actually signs in through.

**A passkey is never the only way in.** Because of the above, signing in at a
name the passkey was not registered for offers nothing - so TOTP stays live
beside it and the login falls through. A second factor that can strand a
customer on the wrong hostname is not a security feature.

The library does the cryptography. What is worth reading here is the boundary:
what is trusted from the browser (nothing, beyond what the library verifies
against a challenge we issued), and what is derived on the server (the RP ID
and the origin, both from the request, never from the payload).
"""

from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime
from typing import Optional

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.models.entities import User, WebauthnCredential

# How long a challenge stays usable. Long enough to pick a key up off the desk,
# short enough that a captured one is worthless.
CHALLENGE_TTL_SECONDS = 180

RP_NAME = "spanel"


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def rp_id_for_host(host: str) -> str:
    """The Relying Party ID for the hostname this request arrived on.

    Derived from the request, never from the body: the RP ID is half of what a
    signature is checked against, and letting a caller choose it would let them
    choose which credential their assertion appears to satisfy.

    A bare IP address cannot be an RP ID - WebAuthn requires a domain - so this
    returns an empty string and the caller offers password plus TOTP instead.
    """
    hostname = (host or "").split(":")[0].strip().lower().rstrip(".")
    if not hostname:
        return ""
    if hostname == "localhost":
        return hostname
    # Reject anything that looks like an IPv4 or IPv6 literal.
    if all(part.isdigit() for part in hostname.split(".") if part) and hostname.count(".") == 3:
        return ""
    if ":" in hostname or hostname.startswith("["):
        return ""
    if "." not in hostname:
        return ""
    return hostname


def origin_for_request(scheme: str, host: str) -> str:
    """The exact origin the browser will report, port included."""
    return f"{scheme}://{host}"


def supported(host: str) -> bool:
    return bool(rp_id_for_host(host))


# --- challenges -------------------------------------------------------------
#
# Kept server-side and single-use. The value the browser signs has to be one we
# issued and have not accepted before, or a captured assertion could be
# replayed.

def new_challenge() -> bytes:
    return secrets.token_bytes(32)


def challenge_key(kind: str, subject: str, rp_id: str) -> str:
    return f"spanel:webauthn:{kind}:{rp_id}:{subject}"


# --- registration -----------------------------------------------------------

def registration_options(user: User, host: str, existing: list[WebauthnCredential]) -> tuple[str, bytes]:
    """Options for navigator.credentials.create(), and the challenge to store."""
    rp_id = rp_id_for_host(host)
    if not rp_id:
        raise ValueError(
            "Passkeys need a domain name. This panel is being reached by IP "
            "address, where the browser will not create one."
        )
    challenge = new_challenge()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        # Stable per account so a re-registration replaces rather than
        # duplicates, and opaque so it carries nothing about the customer.
        user_id=str(user.id).encode("utf-8"),
        user_name=user.username,
        user_display_name=user.username,
        challenge=challenge,
        # Exclude what this account already has for this hostname, so the
        # browser refuses a second registration of the same authenticator
        # instead of silently making a duplicate.
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=unb64(c.credential_id))
            for c in existing
            if c.rp_id == rp_id
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    return options_to_json(options), challenge


def verify_registration(
    *,
    credential_json: str,
    challenge: bytes,
    scheme: str,
    host: str,
) -> dict:
    """Check what the browser sent and return the row to store."""
    rp_id = rp_id_for_host(host)
    if not rp_id:
        raise ValueError("Passkeys are not available on this hostname")
    verification = verify_registration_response(
        credential=credential_json,
        expected_challenge=challenge,
        expected_rp_id=rp_id,
        expected_origin=origin_for_request(scheme, host),
        require_user_verification=False,
    )
    transports = []
    try:
        parsed = json.loads(credential_json)
        transports = parsed.get("response", {}).get("transports") or []
    except (ValueError, AttributeError):
        transports = []
    return {
        "credential_id": b64(verification.credential_id),
        "public_key": b64(verification.credential_public_key),
        "sign_count": int(verification.sign_count or 0),
        "rp_id": rp_id,
        "transports": ",".join(t for t in transports if isinstance(t, str))[:128],
    }


# --- authentication ---------------------------------------------------------

def authentication_options(credentials: list[WebauthnCredential], host: str) -> tuple[str, bytes]:
    rp_id = rp_id_for_host(host)
    if not rp_id:
        raise ValueError("Passkeys are not available on this hostname")
    challenge = new_challenge()
    options = generate_authentication_options(
        rp_id=rp_id,
        challenge=challenge,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=unb64(c.credential_id))
            for c in credentials
            if c.rp_id == rp_id
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return options_to_json(options), challenge


def verify_authentication(
    *,
    credential_json: str,
    challenge: bytes,
    stored: WebauthnCredential,
    scheme: str,
    host: str,
) -> int:
    """Verify an assertion and return the new signature counter.

    Raises if anything does not line up: the library checks the signature, the
    challenge, the RP ID hash and the origin, and this adds the counter rule
    the spec leaves to the relying party.
    """
    rp_id = rp_id_for_host(host)
    if not rp_id or stored.rp_id != rp_id:
        raise ValueError("This passkey belongs to a different hostname")
    verification = verify_authentication_response(
        credential=credential_json,
        expected_challenge=challenge,
        expected_rp_id=rp_id,
        expected_origin=origin_for_request(scheme, host),
        credential_public_key=unb64(stored.public_key),
        credential_current_sign_count=int(stored.sign_count or 0),
        require_user_verification=False,
    )
    new_count = int(verification.new_sign_count or 0)
    # A counter that goes backwards means the credential has been cloned. An
    # authenticator that does not count at all reports 0 every time, which the
    # spec allows and which this must not mistake for a clone.
    if new_count and new_count <= int(stored.sign_count or 0) and int(stored.sign_count or 0) > 0:
        raise ValueError("Passkey signature counter went backwards")
    return new_count


def credential_id_from(credential_json: str) -> Optional[str]:
    """The id the browser says it used, so the row can be looked up.

    Only used to find the stored public key. Nothing is trusted about it: the
    assertion is then verified against that key, the challenge and the origin.
    """
    try:
        parsed = json.loads(credential_json)
    except (ValueError, TypeError):
        return None
    raw = parsed.get("rawId") or parsed.get("id")
    return raw if isinstance(raw, str) and raw else None


def touch(credential: WebauthnCredential, sign_count: int) -> None:
    credential.sign_count = sign_count
    credential.last_used_at = datetime.utcnow()
