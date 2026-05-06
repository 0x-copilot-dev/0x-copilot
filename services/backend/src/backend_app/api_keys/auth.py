"""Bearer parsing + HMAC verification for ``atlas_pk_*`` API keys.

The wire shape is::

    atlas_pk_<prefix>_<secret>

* ``atlas_pk_`` — fixed sentinel that lets callers (FE auth, API
  middlewares, log redactors) recognise the value cheaply.
* ``<prefix>`` — public, indexable; surfaced by the listing endpoint
  so the user can identify a key without seeing the secret.
* ``<secret>`` — high-entropy plaintext. Stored as
  ``HMAC(secret, server_pepper)`` so a database leak alone can't
  authenticate.

Every entry point on the verification side uses ``hmac.compare_digest``
to keep timing-attack surface small.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

# Bytes of entropy. Each base32-style hex byte yields two characters,
# so the wire-shape strings below are ``2 * BYTES`` characters long.
API_KEY_PREFIX_BYTES = 6  # 12 hex chars
API_KEY_SECRET_BYTES = 24  # 48 hex chars
API_KEY_SECRET_HASH_BYTES = 32  # SHA-256 truncated/raw

_BEARER_SENTINEL = "atlas_pk_"


class InvalidApiKey(ValueError):
    """Raised when a bearer string can't be parsed into a valid shape.

    The auth middleware catches this and translates to a 401 with a
    safe public message — never echoes the offending bytes.
    """


@dataclass(frozen=True)
class ApiKeyBearer:
    """Parsed bearer token — the prefix tells us which row to look up."""

    prefix: str
    secret: str


def parse_bearer(bearer: str) -> ApiKeyBearer:
    """Split a wire-format bearer into ``(prefix, secret)``.

    Rejects anything that doesn't match exactly the
    ``atlas_pk_<prefix>_<secret>`` shape with hex-only ASCII bytes
    of the expected lengths. The prefix is hex-validated here so
    a downstream lookup can't be tricked into scanning by partial
    matching on a non-hex prefix.
    """

    if not isinstance(bearer, str) or not bearer.startswith(_BEARER_SENTINEL):
        raise InvalidApiKey("malformed_prefix")
    rest = bearer[len(_BEARER_SENTINEL) :]
    parts = rest.split("_")
    if len(parts) != 2:
        raise InvalidApiKey("malformed_prefix")
    prefix, secret = parts[0], parts[1]
    expected_prefix_chars = API_KEY_PREFIX_BYTES * 2
    expected_secret_chars = API_KEY_SECRET_BYTES * 2
    if len(prefix) != expected_prefix_chars or len(secret) != expected_secret_chars:
        raise InvalidApiKey("malformed_prefix")
    if not _is_hex(prefix) or not _is_hex(secret):
        raise InvalidApiKey("malformed_prefix")
    return ApiKeyBearer(prefix=prefix, secret=secret)


def _is_hex(value: str) -> bool:
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


class ApiKeyHasher:
    """HMAC-SHA256 of the secret under a server pepper.

    The pepper is a deployment secret — it lives only in process memory
    + the deployment's secrets manager. A row leak alone can't
    authenticate because the pepper is required to compute the same
    hash. Rotating the pepper invalidates every existing key (a
    deliberate emergency lever).
    """

    def __init__(self, *, server_pepper: bytes) -> None:
        if not isinstance(server_pepper, (bytes, bytearray)) or len(server_pepper) < 16:
            raise ValueError("server_pepper must be at least 16 bytes")
        self._pepper = bytes(server_pepper)

    def hash(self, secret: str) -> str:
        digest = hmac.new(
            self._pepper, secret.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return digest

    def verify(self, secret: str, expected_hash: str) -> bool:
        actual = self.hash(secret)
        # Constant-time compare so a malicious caller can't byte-bisect
        # a stored hash via timing.
        return hmac.compare_digest(actual, expected_hash)

    def mint(self) -> tuple[str, str]:
        """Return ``(prefix, secret)`` of a freshly minted key.

        Both halves are random hex; ``token_hex`` uses ``secrets``
        under the hood so the entropy is CSPRNG-quality. The full
        wire-format bearer the user copies once is
        ``atlas_pk_<prefix>_<secret>``.
        """

        prefix = secrets.token_hex(API_KEY_PREFIX_BYTES)
        secret = secrets.token_hex(API_KEY_SECRET_BYTES)
        return prefix, secret


def render_bearer(prefix: str, secret: str) -> str:
    """Reverse of :func:`parse_bearer` — only used at mint time."""

    return f"{_BEARER_SENTINEL}{prefix}_{secret}"


__all__ = [
    "API_KEY_PREFIX_BYTES",
    "API_KEY_SECRET_BYTES",
    "API_KEY_SECRET_HASH_BYTES",
    "ApiKeyBearer",
    "ApiKeyHasher",
    "InvalidApiKey",
    "parse_bearer",
    "render_bearer",
]
