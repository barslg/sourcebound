"""Standalone fake OIDC issuer for the e2e run: Google is never contacted.

Usage: python frontend/e2e/oidc_stub.py PORT. The /auth endpoint answers a
browser navigation with a 302 back to redirect_uri carrying a fresh one-time
code bound to the request's nonce and PKCE challenge, and the signed-in
account named by login_hint (default stub@example.com).
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
from t02_issuer import _JWKS, _mint_id_token  # noqa: E402

CLIENT_ID = "stub-client"
CODES: dict[str, dict[str, str]] = {}


def _challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        base = f"http://127.0.0.1:{self.server.server_port}"
        if parsed.path == "/.well-known/openid-configuration":
            self._json(
                {
                    "issuer": base,
                    "authorization_endpoint": base + "/auth",
                    "token_endpoint": base + "/token",
                    "jwks_uri": base + "/jwks.json",
                }
            )
        elif parsed.path == "/jwks.json":
            self._json(_JWKS)
        elif parsed.path == "/auth":
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            code = f"code-{uuid.uuid4().hex}"
            CODES[code] = {
                "nonce": q["nonce"],
                "challenge": q["code_challenge"],
                "email": q.get("login_hint", "stub@example.com"),
            }
            self.send_response(302)
            self.send_header(
                "Location", q["redirect_uri"] + "?" + urlencode({"code": code, "state": q["state"]})
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._json({"error": "not_found"}, 404)

    def do_POST(self) -> None:
        form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
        stored = CODES.pop((form.get("code") or [""])[0], None)
        if (
            stored is None
            or _challenge((form.get("code_verifier") or [""])[0]) != stored["challenge"]
        ):
            self._json({"error": "bad_code"}, 400)
            return
        base = f"http://127.0.0.1:{self.server.server_port}"
        email = stored["email"]
        token = _mint_id_token(
            base, CLIENT_ID, stored["nonce"], f"sub-{email}", "", email, True, "", 300
        )
        self._json({"access_token": "fake", "token_type": "Bearer", "id_token": token})


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
