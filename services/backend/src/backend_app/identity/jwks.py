"""JWKS fetch + cache + ID-token signature verification (A3).

The fetcher delegates HTTP to a small ``Fetcher`` Protocol so tests can
inject an in-process fake without monkey-patching httpx. Production uses
``HttpxJwksFetcher``. Verification rests on PyJWT (`pyjwt[crypto]`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import httpx
import jwt
from jwt import PyJWKSet


_LOGGER = logging.getLogger(__name__)

_DEFAULT_JWKS_TTL_SECONDS = 60 * 60  # 1 hour
_DEFAULT_HTTP_TIMEOUT_SECONDS = 5.0
_CLOCK_SKEW_SECONDS = 60  # leeway on exp / iat / nbf


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JwksFetcherError(RuntimeError):
    """Raised when the JWKS fetch fails or returns malformed data."""


class IdTokenVerificationError(RuntimeError):
    """Raised when an ID token fails any structural / cryptographic check."""


class JwksFetcher(Protocol):
    def fetch(self, jwks_url: str) -> dict[str, Any]: ...  # pragma: no cover


class HttpxJwksFetcher:
    """Production implementation."""

    def __init__(
        self, *, timeout_seconds: float = _DEFAULT_HTTP_TIMEOUT_SECONDS
    ) -> None:
        self._timeout = timeout_seconds

    def fetch(self, jwks_url: str) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(jwks_url)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise JwksFetcherError(f"failed to fetch JWKS: {exc}") from exc
        except ValueError as exc:
            raise JwksFetcherError(f"JWKS response is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict) or "keys" not in payload:
            raise JwksFetcherError("JWKS response missing 'keys' field")
        return payload


class JwksProvider:
    """Cache-backed JWKS lookup tied to a single OIDC provider.

    Wraps an underlying ``JwksFetcher`` + an ``OidcStore`` so a process that
    serves many requests doesn't hammer the IdP. On a ``kid`` cache miss the
    cached JWKS is refreshed once before the verifier raises — handles key
    rotation without operator intervention.
    """

    def __init__(
        self,
        *,
        store: Any,
        fetcher: JwksFetcher | None = None,
        ttl_seconds: int = _DEFAULT_JWKS_TTL_SECONDS,
    ) -> None:
        self._store = store
        self._fetcher = fetcher or HttpxJwksFetcher()
        self._ttl_seconds = ttl_seconds

    def jwks_for(self, *, provider_id: str, jwks_url: str) -> dict[str, Any]:
        cached = self._store.get_jwks_cache(provider_id=provider_id)
        if cached is not None:
            return cached.jwks
        return self._refresh(provider_id=provider_id, jwks_url=jwks_url)

    def force_refresh(self, *, provider_id: str, jwks_url: str) -> dict[str, Any]:
        return self._refresh(provider_id=provider_id, jwks_url=jwks_url)

    def _refresh(self, *, provider_id: str, jwks_url: str) -> dict[str, Any]:
        from backend_app.contracts import OidcJwksCacheRecord

        jwks = self._fetcher.fetch(jwks_url)
        expires_at = _now() + timedelta(seconds=self._ttl_seconds)
        record = OidcJwksCacheRecord(
            provider_id=provider_id, jwks=jwks, expires_at=expires_at
        )
        self._store.upsert_jwks_cache(record)
        return jwks


class IdTokenVerifier:
    """Verifies an OIDC ID token against a JWKS + provider config."""

    def __init__(self, *, jwks_provider: JwksProvider) -> None:
        self._jwks_provider = jwks_provider

    def verify(
        self,
        *,
        provider_id: str,
        jwks_url: str,
        id_token: str,
        issuer: str,
        audience: str,
        nonce: str,
    ) -> dict[str, Any]:
        """Return the verified payload.

        Raises ``IdTokenVerificationError`` on any failure. PyJWT does the
        heavy lifting; this method adds the OIDC-specific nonce check and
        retries once on ``kid`` miss to handle JWKS rotation.
        """

        try:
            unverified_header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise IdTokenVerificationError(f"malformed token header: {exc}") from exc
        kid = unverified_header.get("kid")
        algorithm = unverified_header.get("alg")
        if not isinstance(algorithm, str) or not algorithm:
            raise IdTokenVerificationError("token header missing 'alg'")

        signing_key = self._signing_key(
            provider_id=provider_id,
            jwks_url=jwks_url,
            kid=kid,
        )
        try:
            payload = jwt.decode(
                id_token,
                key=signing_key,
                algorithms=[algorithm],
                audience=audience,
                issuer=issuer,
                leeway=_CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.InvalidTokenError as exc:
            raise IdTokenVerificationError(f"id token rejected: {exc}") from exc

        token_nonce = payload.get("nonce")
        if not isinstance(token_nonce, str) or token_nonce != nonce:
            raise IdTokenVerificationError("nonce mismatch")
        return payload

    def _signing_key(
        self,
        *,
        provider_id: str,
        jwks_url: str,
        kid: str | None,
    ) -> Any:
        jwks = self._jwks_provider.jwks_for(provider_id=provider_id, jwks_url=jwks_url)
        signing_key = self._find_signing_key(jwks, kid)
        if signing_key is not None:
            return signing_key
        # `kid` not present in the cached JWKS — refresh once to handle key
        # rotation.
        refreshed = self._jwks_provider.force_refresh(
            provider_id=provider_id, jwks_url=jwks_url
        )
        signing_key = self._find_signing_key(refreshed, kid)
        if signing_key is None:
            raise IdTokenVerificationError(
                f"no JWKS key matching kid={kid!r} after refresh"
            )
        return signing_key

    @staticmethod
    def _find_signing_key(jwks: dict[str, Any], kid: str | None) -> Any:
        try:
            jwk_set = PyJWKSet.from_dict(jwks)
        except (ValueError, jwt.PyJWTError) as exc:
            raise IdTokenVerificationError(f"malformed JWKS: {exc}") from exc
        for key in jwk_set.keys:
            if kid is None or getattr(key, "key_id", None) == kid:
                return key.key
        return None


__all__ = [
    "HttpxJwksFetcher",
    "IdTokenVerificationError",
    "IdTokenVerifier",
    "JwksFetcher",
    "JwksFetcherError",
    "JwksProvider",
]
