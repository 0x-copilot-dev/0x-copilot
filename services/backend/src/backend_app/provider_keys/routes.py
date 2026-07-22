"""Public ``/v1/settings/provider-keys`` routes (Phase 2 BYOK).

Frozen wire contract (facade re-exposes these verbatim):

  GET    /v1/settings/provider-keys
      -> 200 {"keys": [{"provider", "key_hint", "updated_at"}]}
  PUT    /v1/settings/provider-keys/{provider}   body {"api_key": "..."}
      -> 200 {"provider", "key_hint", "updated_at",
              "live_check"?: "passed" | "skipped_unreachable"}
      -> 422 on unknown provider (path enum), 400 on format mismatch or
         a provider-rejected key ("api_key_rejected_by_provider")
  DELETE /v1/settings/provider-keys/{provider}
      -> 204
  POST   /v1/settings/provider-keys/{provider}/validate
         body {"api_key": "..."}
      -> 200 {"valid": true,  "models": [...], "reason": null}
      -> 200 {"valid": false, "models": null,  "reason": "invalid_key"}
      -> 200 {"valid": null,  "models": null,
              "reason": "provider_unreachable"}   (couldn't check —
              NOT a failure verdict)

The validate lane calls the provider's live listing endpoint with the
submitted key (see ``live_validator.py``); the key is used for that one
outbound call ONLY — never stored, never audited, never echoed. PUT
keeps the format check as the gate, then best-effort live-checks:
a provider-rejected key 400s, an unreachable provider still stores
(offline-friendly) and reports ``live_check: "skipped_unreachable"``.

``key_hint`` is the ONLY key material on this surface — plaintext never
appears in any response, log line, or audit row. Identity follows the
sibling ``/v1/settings/*`` routes: RBAC via ``RequireScopes(RUNTIME_USE)``
plus the trusted facade-headers envelope (query identity in dev).
"""

from __future__ import annotations

from typing import Literal

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator, ScopedIdentity
from backend_app.identity.rbac import RequireScopes
from backend_app.provider_keys.live_validator import (
    LiveCheckStatus,
    ProviderKeyLiveValidator,
)
from backend_app.provider_keys.service import (
    ProviderKeyFormatError,
    ProviderKeysService,
    validate_api_key_format,
)
from backend_app.provider_keys.ssrf_guard import SsrfGuard, SsrfValidationError
from backend_app.provider_keys.store import ProviderApiKeyRecord, ProviderName


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class ProviderKeyResponse(BaseModel):
    """One stored key, hint-only. NEVER carries plaintext.

    ``default_model`` is the server's single-source projection of the model
    chosen for this key (PRD-F PR-F.5). ``base_url`` + ``label`` (decision D-2)
    are the user-supplied endpoint + display name for the ``openai_compatible``
    custom provider — display-safe, never key material. All three are omitted
    from the wire when ``None`` (``response_model_exclude_none``), so the legacy
    three-field shape stays byte-identical for the native providers.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    key_hint: str
    updated_at: str
    default_model: str | None = None
    base_url: str | None = None
    label: str | None = None


class ProviderKeyListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[ProviderKeyResponse]


class SetProviderKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(..., min_length=1)
    # Optional default-model pick to persist alongside the key (PRD-F PR-F.5).
    # ADDITIVE: older clients omit it and any stored pick is preserved on
    # rotation. Display-safe slug only — never key material.
    default_model: str | None = None
    # Decision D-2 — the custom OpenAI-compatible endpoint. ``base_url`` is
    # REQUIRED in practice when the path provider is ``openai_compatible``
    # (enforced in the route, not the schema, so native providers stay
    # additive); ``label`` is the display name. Both are display-safe.
    base_url: str | None = None
    label: str | None = None


class PutProviderKeyResponse(ProviderKeyResponse):
    """PUT response: the stored summary plus a live-check note.

    ``live_check`` is ``"passed"`` when the provider accepted the key,
    ``"skipped_unreachable"`` when the provider couldn't be reached and
    the key was stored anyway (offline-friendly). Omitted entirely
    (``response_model_exclude_none``) when live validation is disabled,
    which keeps the legacy three-field shape byte-identical.
    """

    live_check: Literal["passed", "skipped_unreachable"] | None = None


class ValidateProviderKeyRequest(BaseModel):
    """Body for the validate lane. The key is used for ONE outbound
    probe and discarded — never stored, never audited."""

    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(..., min_length=1)
    # Decision D-2 — probe target for the ``openai_compatible`` custom slug
    # (``{base_url}/models``). Required for that provider; ignored otherwise.
    base_url: str | None = None


class ValidateProviderKeyResponse(BaseModel):
    """Tri-state live verdict — discriminate on ``valid``:

    * ``true``  -> ``models`` lists the ids this key can reach (may be
      empty where the authenticated probe isn't a model listing).
    * ``false`` -> ``reason`` is ``"invalid_key"`` (provider said 401/403).
    * ``null``  -> ``reason`` is ``"provider_unreachable"`` — the check
      couldn't run; NOT a failure verdict.

    All three keys are always present so clients never branch on key
    existence. Never carries key material.
    """

    model_config = ConfigDict(extra="forbid")

    valid: bool | None
    models: list[str] | None = None
    reason: Literal["invalid_key", "provider_unreachable"] | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_provider_keys_routes(
    app: FastAPI,
    *,
    service: ProviderKeysService,
    live_validator: ProviderKeyLiveValidator | None = None,
    ssrf_guard: SsrfGuard | None = None,
) -> None:
    """Attach the ``/v1/settings/provider-keys`` routes to ``app``.

    ``live_validator=None`` disables the live lane: PUT falls back to
    the format-only gate (legacy behavior) and the validate route
    answers ``provider_unreachable`` (couldn't check).

    ``ssrf_guard`` validates the user-supplied ``base_url`` of the
    ``openai_compatible`` custom provider (decision D-2) at BOTH validate and
    store time. ``None`` means the custom flow is unavailable on this
    deployment: any ``openai_compatible`` request 400s (``custom_endpoint_
    unavailable``) — fail closed, never store or probe an unguarded URL.
    """

    @app.get(
        "/v1/settings/provider-keys",
        response_model=ProviderKeyListResponse,
        response_model_exclude_none=True,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_provider_keys(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProviderKeyListResponse:
        identity = _identity(request, org_id=org_id, user_id=user_id)
        records = service.list_keys(org_id=identity.org_id, user_id=identity.user_id)
        return ProviderKeyListResponse(
            keys=[_to_response(record) for record in records]
        )

    def _require_custom_base_url(provider: ProviderName, base_url: str | None) -> None:
        """Validate the custom slug's base_url, or 400. No-op for native slugs.

        The single gate for decision D-2's SSRF surface on the request path: it
        runs BEFORE any probe or store, so a rejected URL never leaves the
        process. The 400 detail is a machine-readable reason code — never the
        URL, host, or a resolved IP.
        """

        if provider is not ProviderName.OPENAI_COMPATIBLE:
            return
        if ssrf_guard is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "custom_endpoint_unavailable"
            )
        if not base_url or not base_url.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "base_url_required")
        try:
            ssrf_guard.check(base_url)
        except SsrfValidationError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"base_url_rejected:{exc.reason.value}"
            ) from exc

    @app.put(
        "/v1/settings/provider-keys/{provider}",
        response_model=PutProviderKeyResponse,
        response_model_exclude_none=True,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def put_provider_key(
        request: Request,
        provider: ProviderName,
        body: SetProviderKeyRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> PutProviderKeyResponse:
        identity = _identity(request, org_id=org_id, user_id=user_id)
        # Custom endpoint: require + SSRF-guard the base_url before anything
        # touches it (probe or store).
        _require_custom_base_url(provider, body.base_url)
        # Format check stays the gate — cheap, offline, and it keeps a
        # clearly-wrong key from ever leaving this process.
        try:
            cleaned = validate_api_key_format(provider=provider, api_key=body.api_key)
        except ProviderKeyFormatError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        # Best-effort live check: a provider-rejected key is a hard 400;
        # an unreachable provider stores anyway (offline-friendly) and
        # says so via ``live_check``.
        live_check: Literal["passed", "skipped_unreachable"] | None = None
        if live_validator is not None:
            outcome = await live_validator.validate(
                provider=provider, api_key=cleaned, base_url=body.base_url
            )
            if outcome.status is LiveCheckStatus.INVALID_KEY:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "api_key_rejected_by_provider"
                )
            live_check = (
                "passed"
                if outcome.status is LiveCheckStatus.VALID
                else "skipped_unreachable"
            )
        try:
            saved = service.set_key(
                org_id=identity.org_id,
                user_id=identity.user_id,
                provider=provider,
                api_key=body.api_key,
                default_model=body.default_model,
                base_url=body.base_url,
                label=body.label,
                request_ip=_request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        except ProviderKeyFormatError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return _to_put_response(saved, live_check=live_check)

    @app.post(
        "/v1/settings/provider-keys/{provider}/validate",
        response_model=ValidateProviderKeyResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def validate_provider_key(
        request: Request,
        provider: ProviderName,
        body: ValidateProviderKeyRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ValidateProviderKeyResponse:
        """Live-check a key WITHOUT storing it. The submitted key feeds
        exactly one outbound probe; no row is written, no audit event
        carries material, and the response never echoes the key."""

        _identity(request, org_id=org_id, user_id=user_id)
        # Custom endpoint: reject an unsafe/missing base_url with a clear 400
        # before probing (rather than a murky "unreachable").
        _require_custom_base_url(provider, body.base_url)
        if live_validator is None:
            return ValidateProviderKeyResponse(
                valid=None, models=None, reason="provider_unreachable"
            )
        outcome = await live_validator.validate(
            provider=provider, api_key=body.api_key, base_url=body.base_url
        )
        if outcome.status is LiveCheckStatus.VALID:
            return ValidateProviderKeyResponse(
                valid=True, models=list(outcome.model_ids), reason=None
            )
        if outcome.status is LiveCheckStatus.INVALID_KEY:
            return ValidateProviderKeyResponse(
                valid=False, models=None, reason="invalid_key"
            )
        return ValidateProviderKeyResponse(
            valid=None, models=None, reason="provider_unreachable"
        )

    @app.delete(
        "/v1/settings/provider-keys/{provider}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def delete_provider_key(
        request: Request,
        provider: ProviderName,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = _identity(request, org_id=org_id, user_id=user_id)
        service.delete_key(
            org_id=identity.org_id,
            user_id=identity.user_id,
            provider=provider,
            request_ip=_request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _identity(request: Request, *, org_id: str, user_id: str) -> ScopedIdentity:
    return BackendServiceAuthenticator.scoped_identity(
        request, org_id=org_id, user_id=user_id
    )


def _to_response(record: ProviderApiKeyRecord) -> ProviderKeyResponse:
    return ProviderKeyResponse(
        provider=record.provider.value,
        key_hint=record.key_hint,
        updated_at=record.updated_at.isoformat(),
        default_model=record.default_model,
        base_url=record.base_url,
        label=record.label,
    )


def _to_put_response(
    record: ProviderApiKeyRecord,
    *,
    live_check: Literal["passed", "skipped_unreachable"] | None,
) -> PutProviderKeyResponse:
    return PutProviderKeyResponse(
        provider=record.provider.value,
        key_hint=record.key_hint,
        updated_at=record.updated_at.isoformat(),
        default_model=record.default_model,
        base_url=record.base_url,
        label=record.label,
        live_check=live_check,
    )


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


__all__ = [
    "ProviderKeyListResponse",
    "ProviderKeyResponse",
    "PutProviderKeyResponse",
    "SetProviderKeyRequest",
    "ValidateProviderKeyRequest",
    "ValidateProviderKeyResponse",
    "register_provider_keys_routes",
]
