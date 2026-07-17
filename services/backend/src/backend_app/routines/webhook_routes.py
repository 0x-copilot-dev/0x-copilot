"""``/v1/webhook/routines/*`` + secret reveal — P5-A3.

Three routes:

* ``POST /v1/webhook/routines/{trigger_id}`` — **public** (no bearer).
  Auth is the secret + HMAC. Every hit is audited:
  ``routine.fire_webhook`` on success, ``routine.fire_webhook_unauthorized``
  on failure, both with
  ``context = { trigger_id, source_ip, auth_method, reason? }`` per
  cross-audit §6.1 + §2.4.

* ``POST /internal/v1/routines/{routine_id}/triggers/{trigger_id}/rotate-secret``
  — owner-only. Rotates the secret, returns the masked tail plus the
  one-shot reveal token; the actual plaintext is fetched via the reveal
  endpoint exactly once.

* ``GET /v1/routines/{routine_id}/webhook/{trigger_id}/secret`` — owner-only,
  returns plaintext **exactly once** if the row was just rotated; otherwise
  returns the masked view + grace metadata.

Auth shape: this route module deliberately mounts no bearer dependency on
the public webhook POST. The secret + HMAC IS the auth (cross-audit
§2.4). The rotate + reveal endpoints sit on the regular bearer-auth path
because they're owner-scoped Settings operations.

Fire enqueue: on a valid hit the route calls into the ``RoutineFireEnqueuer``
port. P5-A2 owns the queue + scheduler; this module deliberately stops at
the port so the auth slice can ship independently of the queue adapter.
"""

from __future__ import annotations

from typing import Any, Protocol

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import IdentityAuditEventRecord
from backend_app.identity.rbac import RequireScopes, public_route
from backend_app.identity.store import IdentityStore
from backend_app.routines.webhook import (
    RoutineWebhookValidator,
    WebhookAuthFailure,
    WebhookAuthResult,
    WebhookValidationError,
)


# ---------------------------------------------------------------------------
# Fire enqueue port
# ---------------------------------------------------------------------------


class RoutineFireEnqueuer(Protocol):
    """Port the webhook route uses to hand off to P5-A2's scheduler.

    The route's responsibility ends with auth + audit; everything beyond
    (idempotency keys, run creation, retries) belongs to the scheduler.
    The protocol returns ``(fire_id, run_id)`` so the response shape
    matches the §3.12 PRD contract.
    """

    def enqueue_webhook_fire(
        self,
        *,
        org_id: str,
        routine_id: str,
        trigger_id: str,
        payload: dict[str, Any] | None,
        source_ip: str | None,
    ) -> tuple[str, str | None]:  # pragma: no cover - protocol shape
        ...


class _NullEnqueuer:
    """Default adapter when P5-A2 isn't wired yet. Returns a synthetic
    ``fire_id`` so the route stays observable in isolation tests."""

    _counter = 0

    def enqueue_webhook_fire(
        self,
        *,
        org_id: str,
        routine_id: str,
        trigger_id: str,
        payload: dict[str, Any] | None,
        source_ip: str | None,
    ) -> tuple[str, str | None]:
        del org_id, routine_id, trigger_id, payload, source_ip
        type(self)._counter += 1
        return (f"fire_{type(self)._counter:08d}", None)


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class WebhookFireResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fire_id: str
    run_id: str | None


class RotateSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routine_id: str = Field(min_length=1, max_length=128)
    ip_allowlist: tuple[str, ...] = ()


class RotateSecretResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_id: str
    secret_masked: str
    secret_rotated_at: str
    secret_grace_until: str | None
    ip_allowlist: tuple[str, ...]


class RevealSecretResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_id: str
    plaintext: str | None  # set ONLY on first call after a rotation
    secret_masked: str
    secret_rotated_at: str
    secret_grace_until: str | None


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


# Bounded body size so a hostile caller cannot exhaust memory before auth.
# §3.12 PRD pins the limit at 256 KB.
_MAX_WEBHOOK_BODY_BYTES = 256 * 1024


def register_routines_webhook_routes(
    app: FastAPI,
    *,
    validator: RoutineWebhookValidator,
    identity_store: IdentityStore,
    fire_enqueuer: RoutineFireEnqueuer | None = None,
) -> None:
    """Mount the webhook ingest + rotation routes onto ``app``."""

    enqueuer = fire_enqueuer or _NullEnqueuer()

    # -- Public webhook POST ----------------------------------------------

    @app.post(
        "/v1/webhook/routines/{trigger_id}",
        response_model=WebhookFireResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(public_route())],
    )
    async def fire_webhook(
        request: Request,
        trigger_id: str,
    ) -> WebhookFireResponse:
        # Read the raw body BEFORE parsing JSON — HMAC is over raw bytes,
        # and the size guard runs before any decoding cost.
        raw_body = await request.body()
        if len(raw_body) > _MAX_WEBHOOK_BODY_BYTES:
            # Body too large is audited as ``routine.fire_webhook_unauthorized``
            # with ``reason='payload_too_large'`` so SIEM queries can
            # group every fire-rejection under one filter. The helper
            # always raises.
            _reject_payload_too_large(request, trigger_id, validator, identity_store)

        source_ip = _request_ip(request)
        header_secret = request.headers.get("x-atlas-routine-secret")
        header_signature = request.headers.get("x-atlas-routine-signature")

        outcome = validator.authenticate(
            trigger_id=trigger_id,
            source_ip=source_ip,
            header_secret=header_secret,
            header_signature=header_signature,
            raw_body=raw_body,
        )

        if isinstance(outcome, WebhookAuthFailure):
            # ``_reject_webhook`` always raises HTTPException —
            # trigger-not-found → 404 (existence-leak prevention),
            # everything else → 401.
            _reject_webhook(
                request=request,
                trigger_id=trigger_id,
                source_ip=source_ip,
                outcome=outcome,
                header_secret_present=bool(header_secret),
                header_signature_present=bool(header_signature),
                identity_store=identity_store,
                validator=validator,
            )

        # Auth ok — record the success audit + enqueue the fire.
        return _accept_webhook(
            request=request,
            trigger_id=trigger_id,
            source_ip=source_ip,
            outcome=outcome,
            raw_body=raw_body,
            enqueuer=enqueuer,
            identity_store=identity_store,
        )

    # -- Owner: rotate secret ---------------------------------------------

    @app.post(
        "/v1/routines/triggers/{trigger_id}/rotate-secret",
        response_model=RotateSecretResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def rotate_secret(
        request: Request,
        trigger_id: str,
        payload: RotateSecretRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> RotateSecretResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            row = validator.rotate_secret(
                trigger_id=trigger_id,
                org_id=identity.org_id,
                owner_user_id=identity.user_id,
                routine_id=payload.routine_id,
                ip_allowlist=tuple(payload.ip_allowlist),
            )
        except WebhookValidationError as exc:
            if exc.reason == "trigger_not_found":
                # Tenant-isolation 404 (existence-not-leaked).
                raise HTTPException(status.HTTP_404_NOT_FOUND, "trigger_not_found")
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, exc.reason
            ) from exc
        identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=identity.org_id,
                actor_user_id=identity.user_id,
                subject_user_id=identity.user_id,
                action="routine.secret_rotated",
                metadata={
                    "trigger_id": trigger_id,
                    "routine_id": payload.routine_id,
                    "grace_until": (
                        row.previous_secret_expires_at.isoformat()
                        if row.previous_secret_expires_at is not None
                        else None
                    ),
                },
                request_ip=_request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        )
        return RotateSecretResponse(
            trigger_id=row.trigger_id,
            secret_masked=row.current_secret_mask,
            secret_rotated_at=row.current_rotated_at.isoformat(),
            secret_grace_until=(
                row.previous_secret_expires_at.isoformat()
                if row.previous_secret_expires_at is not None
                else None
            ),
            ip_allowlist=row.ip_allowlist,
        )

    # -- Owner: reveal secret (one-shot) ----------------------------------

    @app.get(
        "/v1/routines/triggers/{trigger_id}/webhook/secret",
        response_model=RevealSecretResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def reveal_secret(
        request: Request,
        trigger_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> RevealSecretResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        row = validator._store.get_for_owner(  # noqa: SLF001  — owner-scoped lookup
            org_id=identity.org_id,
            owner_user_id=identity.user_id,
            trigger_id=trigger_id,
        )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "trigger_not_found")
        plaintext = validator.consume_reveal(trigger_id=trigger_id)
        identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=identity.org_id,
                actor_user_id=identity.user_id,
                subject_user_id=identity.user_id,
                action="routine.secret_revealed",
                metadata={
                    "trigger_id": trigger_id,
                    "revealed": plaintext is not None,
                },
                request_ip=_request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        )
        return RevealSecretResponse(
            trigger_id=row.trigger_id,
            plaintext=plaintext,
            secret_masked=row.current_secret_mask,
            secret_rotated_at=row.current_rotated_at.isoformat(),
            secret_grace_until=(
                row.previous_secret_expires_at.isoformat()
                if row.previous_secret_expires_at is not None
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _accept_webhook(
    *,
    request: Request,
    trigger_id: str,
    source_ip: str | None,
    outcome: WebhookAuthResult,
    raw_body: bytes,
    enqueuer: RoutineFireEnqueuer,
    identity_store: IdentityStore,
) -> WebhookFireResponse:
    payload = _decode_payload(raw_body)
    fire_id, run_id = enqueuer.enqueue_webhook_fire(
        org_id=outcome.secret.org_id,
        routine_id=outcome.secret.routine_id,
        trigger_id=trigger_id,
        payload=payload,
        source_ip=source_ip,
    )
    metadata: dict[str, Any] = {
        "trigger_id": trigger_id,
        "routine_id": outcome.secret.routine_id,
        "source_ip": source_ip,
        "auth_method": outcome.auth_method,
        "matched_grace": outcome.matched_grace,
        "fire_id": fire_id,
    }
    if run_id is not None:
        metadata["run_id"] = run_id
    identity_store.append_identity_audit(
        IdentityAuditEventRecord(
            org_id=outcome.secret.org_id,
            actor_user_id=None,
            subject_user_id=outcome.secret.owner_user_id,
            action="routine.fire_webhook",
            metadata=metadata,
            request_ip=source_ip,
            user_agent=request.headers.get("user-agent"),
        )
    )
    return WebhookFireResponse(fire_id=fire_id, run_id=run_id)


def _reject_webhook(
    *,
    request: Request,
    trigger_id: str,
    source_ip: str | None,
    outcome: WebhookAuthFailure,
    header_secret_present: bool,
    header_signature_present: bool,
    identity_store: IdentityStore,
    validator: RoutineWebhookValidator,
) -> None:
    # Pick the audit ``auth_method`` from what the caller actually sent
    # so the audit row tells us which surface was attempted, even on a
    # miss. ``reason`` is the *why*.
    if header_secret_present and header_signature_present:
        attempted_method = "secret+signature"
    elif header_signature_present:
        attempted_method = "signature"
    elif header_secret_present:
        attempted_method = "secret"
    else:
        attempted_method = "none"

    # For trigger-not-found we don't know the org, so we cannot record a
    # tenant-scoped audit row. We still surface 404 and emit a
    # tenant-less audit row under a synthetic ``org_id`` slot so the
    # forensics trail is preserved without leaking tenant existence.
    if outcome.reason == "trigger_not_found":
        identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id="org_unknown",
                actor_user_id=None,
                subject_user_id=None,
                action="routine.fire_webhook_unauthorized",
                metadata={
                    "trigger_id": trigger_id,
                    "source_ip": source_ip,
                    "auth_method": attempted_method,
                    "reason": outcome.reason,
                },
                request_ip=source_ip,
                user_agent=request.headers.get("user-agent"),
            )
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trigger_not_found")

    # For all other failure modes we have a row — pull org_id from it so
    # the audit lands under the right tenant. We re-do the store lookup
    # here so the validator stays stateless and the failure type stays
    # narrow.
    row = validator._store.get_for_trigger(  # noqa: SLF001 - read-only audit lookup
        trigger_id=trigger_id
    )
    # If the row disappeared mid-flight (delete race), fall back to
    # ``org_unknown`` — better than dropping the audit.
    audit_org = row.org_id if row is not None else "org_unknown"
    identity_store.append_identity_audit(
        IdentityAuditEventRecord(
            org_id=audit_org,
            actor_user_id=None,
            subject_user_id=None,
            action="routine.fire_webhook_unauthorized",
            metadata={
                "trigger_id": trigger_id,
                "source_ip": source_ip,
                "auth_method": attempted_method,
                "reason": outcome.reason,
            },
            request_ip=source_ip,
            user_agent=request.headers.get("user-agent"),
        )
    )
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, outcome.reason)


def _reject_payload_too_large(
    request: Request,
    trigger_id: str,
    validator: RoutineWebhookValidator,
    identity_store: IdentityStore,
) -> None:
    """Audit + reject oversize bodies. Uses the same audit ``action`` as
    other unauthorized hits with ``reason='payload_too_large'`` so SIEM
    queries can group all fire-rejections under one filter."""

    source_ip = _request_ip(request)
    row = validator._store.get_for_trigger(  # noqa: SLF001 - read-only lookup
        trigger_id=trigger_id
    )
    audit_org = row.org_id if row is not None else "org_unknown"
    identity_store.append_identity_audit(
        IdentityAuditEventRecord(
            org_id=audit_org,
            actor_user_id=None,
            subject_user_id=None,
            action="routine.fire_webhook_unauthorized",
            metadata={
                "trigger_id": trigger_id,
                "source_ip": source_ip,
                "auth_method": "n/a",
                "reason": "payload_too_large",
            },
            request_ip=source_ip,
            user_agent=request.headers.get("user-agent"),
        )
    )
    raise HTTPException(413, "payload_too_large")


def _decode_payload(raw_body: bytes) -> dict[str, Any] | None:
    """Best-effort JSON decode. Non-JSON bodies are stored as ``None`` and
    the caller can inspect the raw bytes elsewhere; we never let a bad
    JSON shape fail the fire — webhooks are inherently heterogeneous."""

    if not raw_body:
        return None
    try:
        import json

        decoded = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(decoded, dict):
        return {"value": decoded}
    return decoded


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


__all__ = [
    "RotateSecretRequest",
    "RotateSecretResponse",
    "RoutineFireEnqueuer",
    "RevealSecretResponse",
    "WebhookFireResponse",
    "register_routines_webhook_routes",
]
