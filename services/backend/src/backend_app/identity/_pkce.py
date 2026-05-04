"""PKCE primitives + state/nonce generators.

Single source of truth for the verifier/challenge pair used by both the
OIDC SSO module (A3) and the MCP OAuth 2.1 flow ([backend_app.service]).
Mirrors RFC 7636 §4. Keeping both flows on these helpers means a future
change (e.g. wider verifier, hardware RNG) lands in one place.

Stateless, side-effect-free; no class wrapper.
"""

from __future__ import annotations

import base64
import hashlib
import secrets


# RFC 7636 mandates a verifier between 43 and 128 unreserved-characters long.
# 64 bytes of urlsafe base64 yields ~86 chars, well within the window.
_DEFAULT_VERIFIER_BYTES = 64

# Nonce + state ought to be at least 16 bytes per OWASP. We pick 32 for
# safety margin so a brute-force attacker can't precompute a useful set.
_DEFAULT_STATE_BYTES = 32
_DEFAULT_NONCE_BYTES = 32


def generate_verifier(num_bytes: int = _DEFAULT_VERIFIER_BYTES) -> str:
    """Return a fresh PKCE code_verifier.

    The result is urlsafe-base64 with no padding — also a valid OIDC
    ``state`` if a caller needs a generic random string.
    """

    return secrets.token_urlsafe(num_bytes)


def compute_challenge(verifier: str) -> str:
    """S256 transformation: ``urlsafe_b64(sha256(verifier))`` with no padding."""

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def generate_state(num_bytes: int = _DEFAULT_STATE_BYTES) -> str:
    """Return a CSRF-style ``state`` parameter for the OAuth/OIDC flow."""

    return secrets.token_urlsafe(num_bytes)


def generate_nonce(num_bytes: int = _DEFAULT_NONCE_BYTES) -> str:
    """Return an OIDC ``nonce`` claim binding the token to this request."""

    return secrets.token_urlsafe(num_bytes)


__all__ = [
    "compute_challenge",
    "generate_nonce",
    "generate_state",
    "generate_verifier",
]
