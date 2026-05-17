"""Routines webhook ingest — secret rotation + grace + HMAC + IP allowlist.

P5-A3. Cross-audit §2.4 + §9.7 Q6:

* Per-trigger rotating secret in ``X-Atlas-Routine-Secret``. Secret bytes
  are encrypted at rest via the same ``TokenVault`` adapter that protects
  MCP OAuth tokens — local Fernet in dev, KMS in prod.
* On rotation the previous secret enters a **7-day grace window**: both
  the current and previous secret accept until the grace window expires.
* Optional CIDR allowlist per trigger. Empty allowlist = no restriction
  (matches the cross-audit binding decision).
* HMAC-of-payload signature header ``X-Atlas-Routine-Signature:
  hmac-sha256=<hex>``. Computed over the **raw** request body using the
  same secret the ``X-Atlas-Routine-Secret`` header carries. Validation
  is constant-time (``hmac.compare_digest``).

The webhook auth IS the secret + HMAC; no bearer token is required (or
honored). This module produces a ``WebhookAuthResult`` that the route
layer translates into an HTTP response + an audit row.

Module deliberately stops at *validation*. Enqueueing the routine fire
itself is left to the route, which calls into the routines service /
fire-store (P5-A1 / P5-A2 territory) once auth passes.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from backend_app.token_vault import TokenVault


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: How long the previous secret remains valid after a rotation. Cross-audit §2.4.
GRACE_WINDOW = timedelta(days=7)

#: Plaintext secret length in bytes. 32 raw bytes → 64 hex chars; comfortably
#: above the 128-bit "no birthday paradox" floor for HMAC keys.
SECRET_BYTES = 32

#: Header names. Constants so route + tests + audit stay in lock-step.
SECRET_HEADER = "x-atlas-routine-secret"
SIGNATURE_HEADER = "x-atlas-routine-signature"
SIGNATURE_PREFIX = "hmac-sha256="


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WebhookValidationError(ValueError):
    """Raised when a webhook hit fails authentication."""

    reason: str

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class RoutineWebhookSecret(BaseModel):
    """One per-trigger webhook secret record.

    Stores *ciphertext* — the plaintext is revealed exactly once on
    rotation through ``RoutineWebhookValidator.rotate_secret``. The
    masked tail (last 4 chars of the plaintext) is preserved so the
    Settings UI can show the secret without consulting the vault.

    Tenant isolation: every row carries ``org_id`` so the route's
    ``get_for_trigger`` lookup can refuse to return cross-tenant rows.
    The route surfaces those as 404 (existence-not-leaked) rather than
    403.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_id: str
    org_id: str
    routine_id: str
    owner_user_id: str
    # Ciphertext envelopes; both stamped by TokenVault.encrypt(...).
    current_secret_ciphertext: str
    current_secret_mask: str  # last 4 plaintext chars, public-safe
    current_rotated_at: datetime
    previous_secret_ciphertext: str | None = None
    previous_secret_mask: str | None = None
    previous_secret_expires_at: datetime | None = None
    ip_allowlist: tuple[str, ...] = ()
    # Set by ``rotate_secret`` on the row produced by that call. NEVER persisted
    # beyond the in-flight response — the store strips it before insert/update.
    # When set, the route's reveal endpoint returns plaintext exactly once.
    reveal_plaintext: str | None = None


# ---------------------------------------------------------------------------
# Store protocol + in-memory adapter
# ---------------------------------------------------------------------------


class RoutineWebhookStore(Protocol):
    """Storage port. Postgres adapter lands alongside the rest of the
    Routines persistence work; the in-memory adapter is sufficient for
    this slice's tests and for ``make dev``.
    """

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    def get_for_trigger(self, *, trigger_id: str) -> RoutineWebhookSecret | None: ...

    def get_for_owner(
        self, *, org_id: str, owner_user_id: str, trigger_id: str
    ) -> RoutineWebhookSecret | None: ...

    def upsert(self, secret: RoutineWebhookSecret) -> RoutineWebhookSecret: ...

    def consume_reveal(self, *, trigger_id: str) -> str | None:
        """Pop the one-shot plaintext stored alongside a fresh rotation.

        Returns ``None`` if the reveal has already been consumed or if
        the row was never rotated.
        """


class InMemoryRoutineWebhookStore:
    """Dev / tests adapter. NOT for production — no row-level locks."""

    def __init__(self) -> None:
        self._rows: dict[str, RoutineWebhookSecret] = {}
        self._reveals: dict[str, str] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    def get_for_trigger(self, *, trigger_id: str) -> RoutineWebhookSecret | None:
        return self._rows.get(trigger_id)

    def get_for_owner(
        self, *, org_id: str, owner_user_id: str, trigger_id: str
    ) -> RoutineWebhookSecret | None:
        row = self._rows.get(trigger_id)
        if row is None or row.org_id != org_id or row.owner_user_id != owner_user_id:
            return None
        return row

    def upsert(self, secret: RoutineWebhookSecret) -> RoutineWebhookSecret:
        # Strip ``reveal_plaintext`` before persisting; surface it on the
        # one-shot reveal channel instead.
        if secret.reveal_plaintext is not None:
            self._reveals[secret.trigger_id] = secret.reveal_plaintext
            stored = secret.model_copy(update={"reveal_plaintext": None})
        else:
            stored = secret
        self._rows[stored.trigger_id] = stored
        return stored

    def consume_reveal(self, *, trigger_id: str) -> str | None:
        return self._reveals.pop(trigger_id, None)


# ---------------------------------------------------------------------------
# Validator (auth path)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebhookAuthFailure:
    """The validator's failure shape — drives both the 401 response and
    the ``routine.fire_webhook_unauthorized`` audit ``reason``."""

    reason: str  # one of: "missing_secret" | "bad_secret" | "ip_not_allowed" |
    #            "bad_signature" | "trigger_not_found"


@dataclass(frozen=True)
class WebhookAuthResult:
    """Success shape returned to the route on a valid hit."""

    secret: RoutineWebhookSecret
    auth_method: str  # "secret" | "signature" | "secret+signature"
    matched_grace: bool  # True if the *previous* secret matched (rotation grace)


class RoutineWebhookValidator:
    """Pure auth logic — store + clock + vault injected.

    Side-effect-free w.r.t. the routines domain. The route layer composes
    this validator with the audit writer + the fire-enqueue path.
    """

    def __init__(
        self,
        *,
        store: RoutineWebhookStore,
        token_vault: TokenVault,
        clock: callable | None = None,  # type: ignore[type-arg]
    ) -> None:
        self._store = store
        self._vault = token_vault
        self._clock = clock or _now

    # -- Rotation / reveal -------------------------------------------------

    def rotate_secret(
        self,
        *,
        trigger_id: str,
        org_id: str,
        owner_user_id: str,
        routine_id: str,
        ip_allowlist: tuple[str, ...] = (),
    ) -> RoutineWebhookSecret:
        """Mint a new secret. The previous secret enters a 7-day grace.

        The returned row carries ``reveal_plaintext`` set; the store
        strips it before persisting and exposes it through
        ``consume_reveal``. Callers (the route) MUST handle the
        plaintext exactly once.

        Tenant isolation: any existing row at ``trigger_id`` must already
        belong to ``org_id`` + ``owner_user_id`` — a mismatched lookup
        raises ``WebhookValidationError("trigger_not_found")`` so a
        cross-tenant rotate attempt cannot poison another tenant's row.
        """

        _validate_cidrs(ip_allowlist)
        plaintext = _mint_plaintext()
        ciphertext = self._vault.encrypt(plaintext)
        mask = _mask(plaintext)
        now = self._clock()
        previous = self._store.get_for_trigger(trigger_id=trigger_id)
        if previous is not None and (
            previous.org_id != org_id or previous.owner_user_id != owner_user_id
        ):
            raise WebhookValidationError("trigger_not_found")
        if previous is None:
            row = RoutineWebhookSecret(
                trigger_id=trigger_id,
                org_id=org_id,
                routine_id=routine_id,
                owner_user_id=owner_user_id,
                current_secret_ciphertext=ciphertext,
                current_secret_mask=mask,
                current_rotated_at=now,
                ip_allowlist=tuple(ip_allowlist),
                reveal_plaintext=plaintext,
            )
        else:
            row = previous.model_copy(
                update={
                    "current_secret_ciphertext": ciphertext,
                    "current_secret_mask": mask,
                    "current_rotated_at": now,
                    "previous_secret_ciphertext": previous.current_secret_ciphertext,
                    "previous_secret_mask": previous.current_secret_mask,
                    "previous_secret_expires_at": now + GRACE_WINDOW,
                    "ip_allowlist": tuple(ip_allowlist),
                    "reveal_plaintext": plaintext,
                }
            )
        return self._store.upsert(row)

    def consume_reveal(self, *, trigger_id: str) -> str | None:
        """One-shot plaintext reveal post-rotation. ``None`` if already consumed."""

        return self._store.consume_reveal(trigger_id=trigger_id)

    # -- Auth --------------------------------------------------------------

    def authenticate(
        self,
        *,
        trigger_id: str,
        source_ip: str | None,
        header_secret: str | None,
        header_signature: str | None,
        raw_body: bytes,
    ) -> WebhookAuthResult | WebhookAuthFailure:
        """Run all auth checks against a candidate webhook hit.

        Order matters for the audit ``reason`` field:

        1. Trigger lookup — 404 / ``trigger_not_found`` if absent.
        2. IP allowlist — ``ip_not_allowed`` if a non-empty allowlist
           rejects ``source_ip``. We check IP **before** secret so an
           attacker with a stolen secret but wrong-source-IP is still
           filtered, and the audit row records the truly-blocked reason.
        3. Auth method:
              * neither secret nor signature supplied →
                ``missing_secret``.
              * secret supplied → constant-time compare against current
                + previous-in-grace secrets; ``bad_secret`` on miss.
              * signature supplied (with no secret OR alongside one) →
                constant-time HMAC compare; ``bad_signature`` on miss.

        Either valid secret OR valid signature is sufficient; the
        ``auth_method`` field on the success path records which one
        (or both) matched, per cross-audit §2.4.
        """

        row = self._store.get_for_trigger(trigger_id=trigger_id)
        if row is None:
            return WebhookAuthFailure(reason="trigger_not_found")

        if row.ip_allowlist and not _ip_in_allowlist(source_ip, row.ip_allowlist):
            return WebhookAuthFailure(reason="ip_not_allowed")

        if not header_secret and not header_signature:
            return WebhookAuthFailure(reason="missing_secret")

        # Decrypt candidate secrets once; both auth paths need them.
        candidates: list[tuple[str, bool]] = [
            (self._vault.decrypt(row.current_secret_ciphertext), False)
        ]
        if (
            row.previous_secret_ciphertext is not None
            and row.previous_secret_expires_at is not None
            and row.previous_secret_expires_at >= self._clock()
        ):
            candidates.append(
                (self._vault.decrypt(row.previous_secret_ciphertext), True)
            )

        secret_match: tuple[str, bool] | None = None
        if header_secret:
            for plaintext, is_grace in candidates:
                if hmac.compare_digest(header_secret, plaintext):
                    secret_match = (plaintext, is_grace)
                    break
            if secret_match is None:
                return WebhookAuthFailure(reason="bad_secret")

        signature_match: tuple[str, bool] | None = None
        if header_signature:
            expected_pool = [secret_match] if secret_match is not None else candidates
            for plaintext, is_grace in expected_pool:
                if _verify_signature(
                    body=raw_body, secret=plaintext, header=header_signature
                ):
                    signature_match = (plaintext, is_grace)
                    break
            if signature_match is None:
                return WebhookAuthFailure(reason="bad_signature")

        # Compose the human-readable ``auth_method`` for the audit.
        if secret_match is not None and signature_match is not None:
            method = "secret+signature"
            matched_grace = secret_match[1] or signature_match[1]
        elif signature_match is not None:
            method = "signature"
            matched_grace = signature_match[1]
        else:
            assert secret_match is not None  # narrowing — guarded above
            method = "secret"
            matched_grace = secret_match[1]
        return WebhookAuthResult(
            secret=row, auth_method=method, matched_grace=matched_grace
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mint_plaintext() -> str:
    return secrets.token_urlsafe(SECRET_BYTES)


def _mask(plaintext: str) -> str:
    if len(plaintext) <= 4:
        return "****"
    return "****" + plaintext[-4:]


def _verify_signature(*, body: bytes, secret: str, header: str) -> bool:
    """Constant-time HMAC-SHA256 verification over the raw body.

    Accepts the documented ``hmac-sha256=<hex>`` envelope (cross-audit
    §9.7 Q6). Anything else is treated as a mismatch — we don't error
    on shape, we just refuse to authenticate so the audit row records
    ``bad_signature`` consistently.
    """

    if not header.startswith(SIGNATURE_PREFIX):
        return False
    candidate = header[len(SIGNATURE_PREFIX) :].strip().lower()
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(candidate, expected)


def _validate_cidrs(allowlist: tuple[str, ...]) -> None:
    for entry in allowlist:
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError as exc:
            raise WebhookValidationError(
                "invalid_cidr", f"invalid CIDR entry: {entry!r}"
            ) from exc


def _ip_in_allowlist(source_ip: str | None, allowlist: tuple[str, ...]) -> bool:
    if source_ip is None:
        return False
    try:
        addr = ipaddress.ip_address(source_ip)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if addr in network:
            return True
    return False


def compute_signature_header(*, body: bytes, secret: str) -> str:
    """Helper exposed for tests + integration clients.

    Returns the full ``X-Atlas-Routine-Signature`` header value computed
    over ``body`` with ``secret``. Mirrors :func:`_verify_signature`.
    """

    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


__all__ = [
    "GRACE_WINDOW",
    "SECRET_HEADER",
    "SIGNATURE_HEADER",
    "SIGNATURE_PREFIX",
    "InMemoryRoutineWebhookStore",
    "RoutineWebhookSecret",
    "RoutineWebhookStore",
    "RoutineWebhookValidator",
    "WebhookAuthFailure",
    "WebhookAuthResult",
    "WebhookValidationError",
    "compute_signature_header",
]
