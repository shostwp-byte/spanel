# -*- coding: utf-8 -*-
"""A software authenticator, to drive both WebAuthn ceremonies for real.

This builds the bytes a real security key would produce - clientDataJSON, the
authenticator data, a COSE public key and an ECDSA signature - so the panel's
own verification path runs end to end against something it did not generate.

If the server's checks were wrong in the direction that matters (accepting what
it should not), the negative cases at the bottom are what catch it.
"""
import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class SoftAuthenticator:
    """One key pair, one credential id, a counter that increments."""

    def __init__(self, counter_starts_at: int = 0, counts: bool = True):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = counter_starts_at
        self.counts = counts

    # --- helpers ---------------------------------------------------------
    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({
            1: 2,    # kty: EC2
            3: -7,   # alg: ES256
            -1: 1,   # crv: P-256
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        })

    def _auth_data(self, rp_id: str, *, attested: bool) -> bytes:
        flags = 0x01 | 0x04  # user present, user verified
        data = hashlib.sha256(rp_id.encode()).digest()
        if attested:
            flags |= 0x40
        blob = data + bytes([flags]) + struct.pack(">I", self.sign_count)
        if attested:
            blob += (
                b"\x00" * 16                                   # aaguid
                + struct.pack(">H", len(self.credential_id))
                + self.credential_id
                + self._cose_key()
            )
        return blob

    def _client_data(self, kind: str, challenge: bytes, origin: str) -> bytes:
        return json.dumps({
            "type": kind,
            "challenge": b64(challenge),
            "origin": origin,
            "crossOrigin": False,
        }, separators=(",", ":")).encode()

    # --- ceremonies ------------------------------------------------------
    def register(self, *, rp_id: str, challenge: bytes, origin: str) -> str:
        client_data = self._client_data("webauthn.create", challenge, origin)
        auth_data = self._auth_data(rp_id, attested=True)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return json.dumps({
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64(client_data),
                "attestationObject": b64(attestation),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        })

    def assert_(self, *, rp_id: str, challenge: bytes, origin: str) -> str:
        if self.counts:
            self.sign_count += 1
        client_data = self._client_data("webauthn.get", challenge, origin)
        auth_data = self._auth_data(rp_id, attested=False)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        return json.dumps({
            "id": b64(self.credential_id),
            "rawId": b64(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64(client_data),
                "authenticatorData": b64(auth_data),
                "signature": b64(signature),
                "userHandle": None,
            },
            "clientExtensionResults": {},
        })
