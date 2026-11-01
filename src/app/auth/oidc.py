"""Provider-generic OIDC with a Google adapter.

A Provider speaks three operations: build the authorize URL, exchange
a code for tokens, and turn verified claims into an Identity. The
Google adapter adds PKCE, one-time state (checked by the service
layer), and pinned issuer plus audience; an `hd` allow-list from
config optionally restricts hosted domains. HTTP goes through plain
httpx (the test transport guard only watches the LLM client stack).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx
import jwt

from app.errors import AuthError


@dataclass(frozen=True)
class Identity:
    """Verified identity: provider, subject, email, name, avatar, verification."""

    provider: str
    subject: str
    email: str
    name: str
    avatar: str
    email_verified: bool = False


def _issuer_candidates(issuer: str) -> list[str]:
    """Both Google issuer forms: with and without the URL scheme."""
    bare = issuer
    for scheme in ("https://", "http://"):
        if bare.startswith(scheme):
            bare = bare[len(scheme) :]
    forms = {issuer, bare}
    return sorted(forms)


class Provider(Protocol):
    """One external identity provider behind three operations."""

    name: str

    async def authorize_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        """Build the provider's authorize URL for this login attempt."""
        ...

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, object]:
        """Trade a code (with PKCE verifier) for the provider's token set."""
        ...

    async def verify_id_token(self, id_token: str, *, nonce: str) -> Identity:
        """Verify signature, issuer, audience, expiry, and nonce; return identity."""
        ...


def pkce_pair() -> tuple[str, str]:
    """A (verifier, S256 challenge) pair for one login attempt."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class GoogleProvider:
    """Google OIDC adapter; any issuer works when its discovery doc does."""

    name = "google"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_url: str,
        issuer: str = "https://accounts.google.com",
        allowed_domains: tuple[str, ...] = (),
        http_timeout_s: float = 10.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_url = redirect_url
        self._issuer = issuer.rstrip("/")
        self._allowed_domains = tuple(part.lower() for part in allowed_domains)
        self._timeout = http_timeout_s
        self._discovery: dict[str, str] | None = None

    async def _discovery_doc(self) -> dict[str, str]:
        if self._discovery is None:
            url = self._issuer + "/.well-known/openid-configuration"
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url)
            if response.status_code != 200:
                raise AuthError("identity provider is unreachable")
            doc = response.json()
            self._discovery = {
                "authorization_endpoint": str(doc["authorization_endpoint"]),
                "token_endpoint": str(doc["token_endpoint"]),
                "jwks_uri": str(doc["jwks_uri"]),
            }
        return self._discovery

    async def authorize_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        """Authorize URL with PKCE, nonce, and offline-safe parameters."""
        doc = await self._discovery_doc()
        query = httpx.QueryParams(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_url,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{doc['authorization_endpoint']}?{query}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, object]:
        """POST the code plus verifier; never log or print the response."""
        doc = await self._discovery_doc()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                doc["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._redirect_url,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            raise AuthError("sign-in failed at the identity provider")
        payload = response.json()
        if not isinstance(payload, dict) or "id_token" not in payload:
            raise AuthError("sign-in failed at the identity provider")
        return {str(key): value for key, value in payload.items()}

    async def verify_id_token(self, id_token: str, *, nonce: str) -> Identity:
        """Verify the JWT against the provider JWKS with iss/aud pinned."""
        doc = await self._discovery_doc()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            jwks = (await client.get(doc["jwks_uri"])).json()
        try:
            header = jwt.get_unverified_header(id_token)
            key = next(item for item in jwks["keys"] if item.get("kid") == header.get("kid"))
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
            claims = jwt.decode(
                id_token,
                key=cast(Any, public_key),
                algorithms=["RS256"],
                issuer=_issuer_candidates(self._issuer),
                audience=self._client_id,
                leeway=60,
                options={"require": ["exp", "iss", "aud", "sub", "nonce"]},
            )
        except Exception as exc:
            raise AuthError("invalid identity token") from exc
        if claims.get("nonce") != nonce:
            raise AuthError("invalid identity token")
        azp = claims.get("azp")
        if azp is not None and str(azp) != self._client_id:
            raise AuthError("invalid identity token")
        domain = str(claims.get("hd", "") or "").lower()
        if self._allowed_domains and domain not in self._allowed_domains:
            raise AuthError("this domain cannot sign in here")
        email = str(claims.get("email", "") or "").strip().lower()
        if not email:
            raise AuthError("the identity provider gave no email address")
        return Identity(
            provider=self.name,
            subject=str(claims["sub"]),
            email=email,
            name=str(claims.get("name", "") or ""),
            avatar=str(claims.get("picture", "") or ""),
            email_verified=claims.get("email_verified") is True,
        )
