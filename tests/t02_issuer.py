"""Local fake OIDC issuer for tests: real HTTP, real RS256 JWTs, test key.

Google is never contacted. The server implements the three discovery
documents an OIDC client needs (openid-configuration, JWKS, token
endpoint); tests mint one-time codes bound to a nonce, audience, and
PKCE verifier, and the server stamps id tokens the same way a real
provider would.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
_PUBLIC_NUMBERS = _KEY.public_key().public_numbers()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


_JWKS = {
    "keys": [
        {
            "kty": "RSA",
            "kid": "test-key",
            "use": "sig",
            "alg": "RS256",
            "n": _b64url(_PUBLIC_NUMBERS.n.to_bytes(256, "big")),
            "e": _b64url(_PUBLIC_NUMBERS.e.to_bytes(3, "big")),
        }
    ]
}

_CODES: dict[str, dict[str, str]] = {}


def register_code(subject: str, audience: str, nonce: str, hd: str = "") -> str:
    """Stage a one-time code the fake token endpoint will redeem."""
    code = f"code-{uuid.uuid4().hex}"
    _CODES[code] = {
        "subject": subject,
        "audience": audience,
        "nonce": nonce,
        "code_verifier": "verifier-to-fill",
        "hd": hd,
    }
    return code


def stored_verifier_slot(code: str) -> dict[str, str]:
    """Mutable code record so tests can set the expected PKCE verifier."""
    return _CODES[code]


def _mint_id_token(issuer: str, audience: str, nonce: str, subject: str, hd: str) -> str:
    import jwt

    now = int(time.time())
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "email": f"{subject}@example.com",
        "name": "Test User",
        "picture": "https://example.com/avatar.png",
        "nonce": nonce,
        "iat": now,
        "exp": now + 300,
    }
    if hd:
        claims["hd"] = hd
    return jwt.encode(claims, _PRIVATE_PEM, algorithm="RS256", headers={"kid": "test-key"})


class _IssuerHandler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        base = f"http://127.0.0.1:{self.server.server_port}"  # type: ignore[attr-defined]
        if parsed.path == "/.well-known/openid-configuration":
            self._send_json(
                {
                    "issuer": base,
                    "authorization_endpoint": base + "/auth",
                    "token_endpoint": base + "/token",
                    "jwks_uri": base + "/jwks.json",
                }
            )
        elif parsed.path == "/jwks.json":
            self._send_json(_JWKS)
        else:
            self._send_json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/token":
            self._send_json({"error": "not_found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode())
        code = (form.get("code") or [""])[0]
        stored = _CODES.get(code)
        if stored is None:
            self._send_json({"error": "bad_code"}, status=400)
            return
        if stored["code_verifier"] != (form.get("code_verifier") or [""])[0]:
            self._send_json({"error": "bad_verifier"}, status=400)
            return
        base = f"http://127.0.0.1:{self.server.server_port}"  # type: ignore[attr-defined]
        id_token = _mint_id_token(
            base, stored["audience"], stored["nonce"], stored["subject"], stored.get("hd", "")
        )
        self._send_json(
            {"access_token": "fake-access", "token_type": "Bearer", "id_token": id_token}
        )


@contextmanager
def run_issuer() -> Iterator[str]:
    """Serve the fake issuer on 127.0.0.1; yield its base URL."""
    server = HTTPServer(("127.0.0.1", 0), _IssuerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
