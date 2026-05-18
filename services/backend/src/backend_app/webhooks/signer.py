"""HMAC signer for outbound Atlas webhooks — single source of truth.

connectors-prd §9 (Routines §9.7 Q6 lands here). Every outbound webhook
fire (Routine-triggered or test-fire) carries:

* ``X-Atlas-Routine-Signature: hmac-sha256=<hex>`` over the request body
  concatenated with the timestamp header value (matches the receiver
  snippet rendered on the wizard's "Verify" step).
* ``X-Atlas-Signature-Timestamp: <unix-seconds>`` — receivers MUST
  reject if ``|now - ts| > 300``.

DRY invariant (CLAUDE.md preamble): every signer / verifier in the
codebase reads the algorithm and header names FROM THIS MODULE. No
other module hard-codes the strings.

The pattern matches Stripe / GitHub webhook signing; no novelty. The
P5 Routines webhook module (``routines/webhook.py``) implements the
*inbound* HMAC verification on a different envelope (the secret IS the
auth, no timestamp guard). Inbound and outbound stay separate because
the security models differ: inbound trusts the caller's IP +
constant-time secret compare; outbound includes a timestamp so a
replayed body alone can't be re-signed.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Final


# ---------------------------------------------------------------------------
# Public constants — single source of truth.
# ---------------------------------------------------------------------------


#: Wire envelope tag. Used in the signature header value as
#: ``hmac-sha256=<hex>``. Receivers split on the first ``=`` and reject
#: if the prefix is anything else.
HMAC_ALGO: Final[str] = "hmac-sha256"

#: Outbound signature header name. Constant so signer + verifier + audit
#: stay in lock-step.
SIGNATURE_HEADER: Final[str] = "X-Atlas-Routine-Signature"

#: Outbound timestamp header name. Receivers consult this for the skew
#: rejection check in :func:`verify`.
TIMESTAMP_HEADER: Final[str] = "X-Atlas-Signature-Timestamp"

#: Maximum permitted clock skew between the signing host and the
#: receiver. 5 minutes per connectors-prd §9.1.
TIMESTAMP_MAX_SKEW_S: Final[int] = 300

#: Header value prefix the signer emits. Receivers verify by stripping
#: this prefix and constant-time-comparing the remainder.
_SIGNATURE_PREFIX: Final[str] = f"{HMAC_ALGO}="


def sign(*, body: bytes, secret: bytes, ts: int) -> str:
    """Compute the outbound ``X-Atlas-Routine-Signature`` header value.

    The MAC is taken over ``body || str(ts).encode()`` so a replayed
    body alone — without the matching timestamp header — fails the
    verify step. Returns the full header value with the
    ``hmac-sha256=`` prefix.

    ``secret`` is bytes by convention (token vault returns plaintext
    strings; callers encode to UTF-8 at the boundary). Tests + the
    receiver verification snippet in connectors-prd §9.4 match this
    shape exactly.
    """

    if not isinstance(body, (bytes, bytearray)):
        raise TypeError("sign(body=...) must be bytes")
    if not isinstance(secret, (bytes, bytearray)):
        raise TypeError("sign(secret=...) must be bytes")
    payload = bytes(body) + str(int(ts)).encode("ascii")
    digest = hmac.new(bytes(secret), payload, hashlib.sha256).hexdigest()
    return f"{_SIGNATURE_PREFIX}{digest}"


def verify(
    *,
    body: bytes,
    sig_header: str | None,
    ts_header: str | None,
    secret: bytes,
    now: int,
) -> bool:
    """Constant-time verification of an outbound-style Atlas webhook signature.

    Returns ``True`` only when ALL of the following hold:

    * ``sig_header`` and ``ts_header`` are non-empty.
    * ``ts_header`` parses as an integer and ``|now - ts| <= TIMESTAMP_MAX_SKEW_S``.
    * ``sig_header`` starts with ``hmac-sha256=``.
    * The remainder constant-time-matches HMAC-SHA256 over
      ``body + str(ts).encode("ascii")`` using ``secret``.

    Any single failure returns ``False`` — we never raise on shape so an
    attacker can't probe failure modes via exception messages.
    """

    if not sig_header or not ts_header:
        return False
    try:
        ts = int(ts_header)
    except (TypeError, ValueError):
        return False
    if abs(int(now) - ts) > TIMESTAMP_MAX_SKEW_S:
        return False
    if not sig_header.startswith(_SIGNATURE_PREFIX):
        return False
    candidate = sig_header[len(_SIGNATURE_PREFIX) :].strip().lower()
    expected = sign(body=body, secret=secret, ts=ts)[len(_SIGNATURE_PREFIX) :]
    return hmac.compare_digest(candidate, expected)


__all__ = [
    "HMAC_ALGO",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "TIMESTAMP_MAX_SKEW_S",
    "sign",
    "verify",
]
