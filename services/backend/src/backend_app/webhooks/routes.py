"""``/v1/connectors/webhooks/*`` routes — Phase 11 P11-A3.

connectors-prd §4.10. The route layer is presentation-only:

1. Identity scoping via :class:`BackendServiceAuthenticator.scoped_identity`.
2. Translating service exceptions to HTTP status codes
   (``WebhookNotFound`` → 404, ``WebhookForbidden`` → 403,
   ``WebhookInvalidRequest`` → 400).
3. Marshalling request / response bodies to the wire shapes declared
   in ``packages/api-types/src/connectors.ts`` (the ``Webhook`` type).
4. The test-fire path posts a real HTTP request with the canonical
   signed payload using ``httpx`` (5-second timeout) and surfaces the
   upstream status.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.webhooks.service import (
    WebhookForbidden,
    WebhookInvalidRequest,
    WebhookNotFound,
    WebhooksService,
)
from backend_app.webhooks.signer import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    sign,
)
from backend_app.webhooks.store import WebhookRecord


#: Default timeout for the test-fire HTTP request. connectors-prd §4.10
#: — short enough that a flaky receiver doesn't tie up the route.
_TEST_FIRE_TIMEOUT_S = 5.0


#: Canonical sample payload sent on test-fire. Strict template per
#: connectors-prd §10 Q3 — avoids template drift between the wizard's
#: "Verify" step and real routine fires.
_TEST_FIRE_PAYLOAD: dict[str, Any] = {
    "atlas_event": "webhook.test_fire",
    "delivery_id": "test-fire",
    "data": {"note": "Sample payload from Atlas webhook test-fire."},
}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateWebhookRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    secret_strategy: str = "rotating"
    ip_allowlist: list[str] = Field(default_factory=list)
    routine_id: str | None = None
    static_secret: str | None = None


class UpdateWebhookRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    ip_allowlist: list[str] | None = None
    status: str | None = None


class WebhookResponseModel(BaseModel):
    """Wire mirror of ``Webhook`` (packages/api-types/src/connectors.ts).

    The plaintext secret is NEVER on this model — copy-once reveals
    ride a separate response shape (:class:`WebhookCreateResponseModel`,
    :class:`WebhookRotateResponseModel`).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    owner_user_id: str
    url: str
    secret_strategy: str
    hmac_algo: str
    ip_allowlist: list[str]
    status: str
    last_fire_at: str | None = None
    last_status_code: int | None = None
    routine_id: str | None = None
    rotates_at: str | None = None
    created_at: str
    updated_at: str


class WebhookListResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[WebhookResponseModel]
    next_cursor: str | None = None


class WebhookCreateResponseModel(BaseModel):
    """Create returns the row + the initial secret (copy-once reveal)."""

    model_config = ConfigDict(extra="forbid")
    webhook: WebhookResponseModel
    secret_plaintext: str


class WebhookRotateResponseModel(BaseModel):
    """Rotate returns the row + new secret + (if any) the grace secret."""

    model_config = ConfigDict(extra="forbid")
    webhook: WebhookResponseModel
    secret_plaintext: str
    grace_secret_plaintext: str | None = None


class WebhookTestFireResponseModel(BaseModel):
    """Test-fire surfaces the upstream status + a delivery summary."""

    model_config = ConfigDict(extra="forbid")
    response_status: int | None
    response_ok: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_webhook_routes(
    app: FastAPI,
    *,
    service: WebhooksService,
    http_client_factory: Any | None = None,
) -> None:
    """Attach ``/v1/connectors/webhooks`` routes to ``app``.

    ``http_client_factory`` is an optional dependency-injection seam
    for the test-fire path; production passes ``None`` and the route
    builds a default ``httpx.Client`` per request.
    """

    @app.get(
        "/v1/connectors/webhooks",
        response_model=WebhookListResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_webhooks(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> WebhookListResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        statuses = _parse_repeatable_filter(request, "status") or None
        records, next_cursor = service.list_webhooks(
            tenant_id=identity.org_id,
            caller_user_id=identity.user_id,
            caller_roles=identity.roles,
            statuses=statuses,
            cursor=cursor,
            limit=limit,
        )
        return WebhookListResponseModel(
            items=[_to_wire(record) for record in records],
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/connectors/webhooks/{webhook_id}",
        response_model=WebhookResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_webhook(
        request: Request,
        webhook_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> WebhookResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.get_webhook(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                webhook_id=webhook_id,
            )
        except WebhookNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook_not_found") from exc
        return _to_wire(record)

    @app.post(
        "/v1/connectors/webhooks",
        response_model=WebhookCreateResponseModel,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def create_webhook(
        request: Request,
        payload: CreateWebhookRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> WebhookCreateResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            created = service.create_webhook(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                url=payload.url,
                secret_strategy=payload.secret_strategy,
                ip_allowlist=tuple(payload.ip_allowlist),
                routine_id=payload.routine_id,
                static_secret=payload.static_secret,
            )
        except WebhookInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return WebhookCreateResponseModel(
            webhook=_to_wire(created.record),
            secret_plaintext=created.secret_plaintext,
        )

    @app.patch(
        "/v1/connectors/webhooks/{webhook_id}",
        response_model=WebhookResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def update_webhook(
        request: Request,
        webhook_id: str,
        payload: UpdateWebhookRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> WebhookResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        patch_dict = payload.model_dump(exclude_unset=True)
        try:
            stored = service.update_webhook(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                webhook_id=webhook_id,
                url=patch_dict.get("url"),
                ip_allowlist=(
                    tuple(patch_dict["ip_allowlist"])
                    if "ip_allowlist" in patch_dict
                    else None
                ),
                status=patch_dict.get("status"),
            )
        except WebhookNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook_not_found") from exc
        except WebhookForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except WebhookInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return _to_wire(stored)

    @app.post(
        "/v1/connectors/webhooks/{webhook_id}/rotate",
        response_model=WebhookRotateResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def rotate_webhook(
        request: Request,
        webhook_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> WebhookRotateResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            rotated = service.rotate_webhook(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                webhook_id=webhook_id,
            )
        except WebhookNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook_not_found") from exc
        except WebhookForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except WebhookInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return WebhookRotateResponseModel(
            webhook=_to_wire(rotated.record),
            secret_plaintext=rotated.secret_plaintext,
            grace_secret_plaintext=rotated.grace_secret_plaintext,
        )

    @app.delete(
        "/v1/connectors/webhooks/{webhook_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def delete_webhook(
        request: Request,
        webhook_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            service.delete_webhook(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                webhook_id=webhook_id,
            )
        except WebhookNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook_not_found") from exc
        except WebhookForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/v1/connectors/webhooks/{webhook_id}/test-fire",
        response_model=WebhookTestFireResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def test_fire(
        request: Request,
        webhook_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> WebhookTestFireResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.get_webhook(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                webhook_id=webhook_id,
            )
            # Owner-or-admin gate on test-fire (it can be loud, and we
            # don't want a passive viewer to ping a third-party URL).
            if record.owner_user_id != identity.user_id and not any(
                role in {"admin", "tenant_admin"} for role in identity.roles
            ):
                raise WebhookForbidden(webhook_id)
            secret, _grace = service.reveal_secret_for_signing(record=record)
        except WebhookNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook_not_found") from exc
        except WebhookForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc

        client = _resolve_http_client(http_client_factory)
        try:
            import json as _json

            body = _json.dumps(_TEST_FIRE_PAYLOAD, separators=(",", ":")).encode(
                "utf-8"
            )
            ts = int(time.time())
            sig = sign(body=body, secret=secret.encode("utf-8"), ts=ts)
            headers = {
                "content-type": "application/json",
                SIGNATURE_HEADER: sig,
                TIMESTAMP_HEADER: str(ts),
            }
            response = client.post(
                record.url, content=body, headers=headers, timeout=_TEST_FIRE_TIMEOUT_S
            )
            return WebhookTestFireResponseModel(
                response_status=response.status_code,
                response_ok=200 <= response.status_code < 300,
            )
        except httpx.HTTPError as exc:
            return WebhookTestFireResponseModel(
                response_status=None,
                response_ok=False,
                error=type(exc).__name__,
            )
        finally:
            close = getattr(client, "close", None)
            if close is not None and http_client_factory is None:
                close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_repeatable_filter(request: Request, axis: str) -> tuple[str, ...]:
    key = f"filter[{axis}]"
    return tuple(v for v in request.query_params.getlist(key) if v)


def _resolve_http_client(factory: Any | None) -> Any:
    if factory is None:
        return httpx.Client(timeout=_TEST_FIRE_TIMEOUT_S)
    return factory()


def _to_wire(record: WebhookRecord) -> WebhookResponseModel:
    return WebhookResponseModel(
        id=record.id,
        tenant_id=record.tenant_id,
        owner_user_id=record.owner_user_id,
        url=record.url,
        secret_strategy=record.secret_strategy,
        hmac_algo=record.hmac_algo,
        ip_allowlist=list(record.ip_allowlist),
        status=record.status,
        last_fire_at=record.last_fire_at.isoformat() if record.last_fire_at else None,
        last_status_code=record.last_status_code,
        routine_id=record.routine_id,
        rotates_at=record.rotates_at.isoformat() if record.rotates_at else None,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


__all__ = [
    "register_webhook_routes",
]
