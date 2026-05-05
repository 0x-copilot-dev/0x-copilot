"""HMAC sign helper for dev-minted bearers.

The signing scheme matches ``services/backend-facade/src/backend_facade/auth.py``
exactly — a base64url(payload) + ``.`` + base64url(HMAC-SHA256(secret, payload))
envelope. We duplicate the helper here (≈15 LoC) rather than reach across
the service boundary; the rules call this out as cheaper than a shared
package for a primitive this small.

Verification stays where it belongs — the facade. We only sign here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def sign_identity_token(payload: dict[str, Any], secret: str) -> str:
    """Return ``base64(payload).base64(HMAC-SHA256(secret, payload))``."""

    if not secret:
        raise ValueError("ENTERPRISE_AUTH_SECRET is empty; refuse to mint")
    canonical = json.dumps(
        payload, separators=(",", ":"), sort_keys=True, default=_default
    ).encode("utf-8")
    payload_part = _b64encode(canonical)
    mac = hmac.new(
        secret.encode("utf-8"), payload_part.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{payload_part}.{_b64encode(mac)}"


def _default(value: object) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    raise TypeError(f"unsupported type: {type(value)!r}")
