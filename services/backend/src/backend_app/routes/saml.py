"""Backend internal SAML endpoints (A5).

All routes mount under ``/internal/v1/auth/saml/*`` and require the service
token. Public ``/v1/auth/saml/*`` lives on the facade.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, Response, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import (
    SamlAuthorizeRequest,
    SamlAuthorizeResult,
    SamlConsumeRequest,
    SamlConsumeResult,
)
from backend_app.identity import (
    AccountLocked,
    IdentityStore,
    SamlAssertionExpired,
    SamlAudienceMismatch,
    SamlConfigError,
    SamlIdpInitiatedDisabled,
    SamlInResponseToMismatch,
    SamlMissingAssertion,
    SamlProviderDisabled,
    SamlReplayDetected,
    SamlService,
    SamlSignatureError,
    SamlUserNotProvisioned,
    SamlVerifierError,
)


def register_saml_routes(
    app: FastAPI,
    *,
    service: SamlService,
    identity_store: IdentityStore,
) -> None:
    @app.post(
        "/internal/v1/auth/saml/{provider_id}/authorize",
        response_model=SamlAuthorizeResult,
    )
    def authorize(
        request: Request, provider_id: str, payload: SamlAuthorizeRequest
    ) -> SamlAuthorizeResult:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id="-"
        )
        try:
            return service.authorize(
                org_id=payload.org_id,
                provider_id=provider_id,
                relay_state=payload.relay_state,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
        except SamlConfigError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except SamlProviderDisabled as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except SamlVerifierError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    @app.post(
        "/internal/v1/auth/saml/consume",
        response_model=SamlConsumeResult,
    )
    def consume(request: Request, payload: SamlConsumeRequest) -> SamlConsumeResult:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        try:
            return service.consume(
                provider_id=payload.provider_id,
                saml_response_b64=payload.saml_response,
                relay_state=payload.relay_state,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
        except AccountLocked as exc:
            headers = (
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds > 0
                else {}
            )
            raise HTTPException(
                status.HTTP_423_LOCKED, str(exc), headers=headers
            ) from exc
        except SamlReplayDetected as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except (
            SamlSignatureError,
            SamlAssertionExpired,
            SamlAudienceMismatch,
            SamlInResponseToMismatch,
            SamlMissingAssertion,
        ) as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        except SamlIdpInitiatedDisabled as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except SamlUserNotProvisioned as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        except SamlConfigError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except SamlVerifierError as exc:
            # Catch-all for any verifier failure that didn't match a more
            # specific subclass — still treated as auth failure (401).
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    @app.get(
        "/internal/v1/auth/saml/{provider_id}/metadata",
        response_class=Response,
    )
    def metadata(request: Request, provider_id: str) -> Response:
        # SP metadata is technically public information (the IdP admin needs
        # it without auth) but we still gate this endpoint behind the
        # service token — the facade is the only public surface, and only
        # the facade is the canonical caller.
        provider = identity_store.get_auth_provider_by_id(provider_id)
        if provider is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown provider")
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=provider.org_id, user_id="-"
        )
        try:
            xml = service.metadata(org_id=provider.org_id, provider_id=provider_id)
        except SamlConfigError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except SamlVerifierError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        return Response(content=xml, media_type="application/xml")


__all__ = ["register_saml_routes"]
