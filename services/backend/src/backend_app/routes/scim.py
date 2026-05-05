"""Backend internal SCIM endpoints (A7).

The facade does the public ``/scim/v2/*`` mounting and SCIM-bearer
validation; this module implements the internal surface the facade
calls. Two surfaces:

- ``/internal/v1/auth/scim/{provider_id}/tokens`` — admin token mint /
  list / revoke. Service-token gated.
- ``/internal/v1/auth/scim/resource/*`` — User / Group CRUD. Caller
  presents the SCIM bearer in the ``x-scim-bearer-token`` header (the
  facade validated it before forwarding); the backend re-validates so
  this endpoint can never be reached without a real token.
"""

from __future__ import annotations

from typing import Any

from enterprise_service_contracts.scopes import ADMIN_IDP
from fastapi import Depends, FastAPI, HTTPException, Request, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes, public_route
from backend_app.contracts import ScimTokenListResponse, ScimTokenSummary
from backend_app.identity.scim import (
    ResolvedScimToken,
    ScimAuthError,
    ScimConflict,
    ScimError,
    ScimNotFound,
    ScimService,
    ScimUnsupportedFilter,
)
from backend_app.identity.scim_serializer import (
    error_response,
    group_to_scim,
    list_response,
    resource_types_listing,
    schemas_listing,
    service_provider_config,
    user_to_scim,
)


_SCIM_BEARER_HEADER = "x-scim-bearer-token"


def register_scim_routes(
    app: FastAPI,
    *,
    service: ScimService,
) -> None:
    # ----- Token admin (service-token gated) -----------------------------
    @app.post(
        "/internal/v1/auth/scim/{provider_id}/tokens",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(ADMIN_IDP))],
    )
    def mint_token(
        request: Request,
        provider_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request,
            org_id=str(payload.get("org_id", "-")),
            user_id=str(payload.get("created_by_user_id", "-")),
        )
        try:
            result = service.mint_token(
                org_id=identity.org_id,
                provider_id=provider_id,
                created_by_user_id=identity.user_id,
                expires_at=None,
            )
        except ScimError as exc:
            raise _to_http(exc) from exc
        return {
            "token_id": result.token_id,
            "plaintext": result.plaintext,
            "token_prefix": result.token_prefix,
            "created_at": result.created_at.isoformat(),
            "expires_at": result.expires_at.isoformat() if result.expires_at else None,
        }

    @app.get(
        "/internal/v1/auth/scim/{provider_id}/tokens",
        response_model=ScimTokenListResponse,
        dependencies=[Depends(RequireScopes(ADMIN_IDP))],
    )
    def list_tokens(
        request: Request,
        provider_id: str,
        org_id: str,
    ) -> ScimTokenListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id="-"
        )
        records = service.list_tokens(org_id=identity.org_id, provider_id=provider_id)
        summaries = tuple(
            ScimTokenSummary(
                token_id=r.token_id,
                token_prefix=r.token_prefix,
                created_by_user_id=r.created_by_user_id,
                created_at=r.created_at,
                expires_at=r.expires_at,
                revoked_at=r.revoked_at,
                last_used_at=r.last_used_at,
            )
            for r in records
        )
        return ScimTokenListResponse(tokens=summaries)

    @app.delete(
        "/internal/v1/auth/scim/{provider_id}/tokens/{token_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(ADMIN_IDP))],
    )
    def revoke_token(
        request: Request,
        provider_id: str,
        token_id: str,
        org_id: str,
    ) -> None:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id="-"
        )
        ok = service.revoke_token(
            org_id=identity.org_id,
            provider_id=provider_id,
            token_id=token_id,
        )
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "token not found")

    # ----- SCIM resource surface (SCIM-bearer gated) ---------------------
    def _resolve(request: Request) -> ResolvedScimToken:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        bearer = request.headers.get(_SCIM_BEARER_HEADER, "")
        try:
            return service.resolve_token(bearer)
        except ScimError as exc:
            raise _to_http(exc) from exc

    def _base_url(request: Request) -> str:
        # Serializer needs an absolute base URL so SCIM ``$ref`` is browsable.
        return f"{request.url.scheme}://{request.url.netloc}/scim/v2"

    @app.get(
        "/internal/v1/auth/scim/resource/Users",
        dependencies=[Depends(public_route())],
    )
    def scim_list_users(
        request: Request,
        filter: str | None = None,
        startIndex: int = 1,
        count: int = 50,
    ) -> dict[str, Any]:
        token = _resolve(request)
        try:
            users, total = service.list_users(
                token=token,
                filter_expr=filter,
                start_index=startIndex,
                count=count,
            )
        except ScimError as exc:
            raise _to_http(exc) from exc
        base = _base_url(request)
        resources = []
        for user in users:
            external_id = service.get_user_external_id(
                token=token, user_id=user.user_id
            )
            groups = service.list_user_groups(token=token, user_id=user.user_id)
            resources.append(
                user_to_scim(
                    user, base_url=base, external_id=external_id, groups=groups
                )
            )
        return list_response(
            resources,
            total_results=total,
            start_index=startIndex,
            items_per_page=len(resources),
        )

    @app.post(
        "/internal/v1/auth/scim/resource/Users",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(public_route())],
    )
    def scim_create_user(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        token = _resolve(request)
        emails = payload.get("emails") or []
        primary_email = None
        if emails:
            for entry in emails:
                if isinstance(entry, dict) and entry.get("primary") is True:
                    primary_email = entry.get("value")
                    break
            if primary_email is None and isinstance(emails[0], dict):
                primary_email = emails[0].get("value")
        user_name = payload.get("userName") or primary_email or ""
        try:
            user, _ = service.create_user(
                token=token,
                user_name=str(user_name),
                display_name=payload.get("displayName"),
                external_id=payload.get("externalId"),
                active=bool(payload.get("active", True)),
            )
        except ScimError as exc:
            raise _to_http(exc) from exc
        base = _base_url(request)
        external_id = service.get_user_external_id(token=token, user_id=user.user_id)
        return user_to_scim(user, base_url=base, external_id=external_id)

    @app.get(
        "/internal/v1/auth/scim/resource/Users/{user_id}",
        dependencies=[Depends(public_route())],
    )
    def scim_get_user(request: Request, user_id: str) -> dict[str, Any]:
        token = _resolve(request)
        try:
            user = service.get_user(token=token, user_id=user_id)
        except ScimError as exc:
            raise _to_http(exc) from exc
        base = _base_url(request)
        external_id = service.get_user_external_id(token=token, user_id=user_id)
        groups = service.list_user_groups(token=token, user_id=user_id)
        return user_to_scim(user, base_url=base, external_id=external_id, groups=groups)

    @app.put(
        "/internal/v1/auth/scim/resource/Users/{user_id}",
        dependencies=[Depends(public_route())],
    )
    def scim_replace_user(
        request: Request, user_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        token = _resolve(request)
        try:
            user = service.replace_user(
                token=token,
                user_id=user_id,
                user_name=payload.get("userName"),
                display_name=payload.get("displayName"),
                active=payload.get("active"),
            )
        except ScimError as exc:
            raise _to_http(exc) from exc
        base = _base_url(request)
        external_id = service.get_user_external_id(token=token, user_id=user_id)
        return user_to_scim(user, base_url=base, external_id=external_id)

    @app.patch(
        "/internal/v1/auth/scim/resource/Users/{user_id}",
        dependencies=[Depends(public_route())],
    )
    def scim_patch_user(
        request: Request, user_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        token = _resolve(request)
        ops = payload.get("Operations") or []
        if not isinstance(ops, list):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Operations must be a list"
            )
        try:
            user = service.patch_user(token=token, user_id=user_id, operations=ops)
        except ScimError as exc:
            raise _to_http(exc) from exc
        base = _base_url(request)
        external_id = service.get_user_external_id(token=token, user_id=user_id)
        return user_to_scim(user, base_url=base, external_id=external_id)

    @app.delete(
        "/internal/v1/auth/scim/resource/Users/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(public_route())],
    )
    def scim_delete_user(request: Request, user_id: str) -> None:
        token = _resolve(request)
        try:
            service.delete_user(token=token, user_id=user_id)
        except ScimError as exc:
            raise _to_http(exc) from exc

    @app.get(
        "/internal/v1/auth/scim/resource/Groups",
        dependencies=[Depends(public_route())],
    )
    def scim_list_groups(
        request: Request,
        filter: str | None = None,
        startIndex: int = 1,
        count: int = 50,
    ) -> dict[str, Any]:
        token = _resolve(request)
        try:
            groups, total = service.list_groups(
                token=token,
                filter_expr=filter,
                start_index=startIndex,
                count=count,
            )
        except ScimError as exc:
            raise _to_http(exc) from exc
        base = _base_url(request)
        resources = []
        for group in groups:
            members = service.list_group_members(token=token, group_id=group.group_id)
            resources.append(group_to_scim(group, base_url=base, members=members))
        return list_response(
            resources,
            total_results=total,
            start_index=startIndex,
            items_per_page=len(resources),
        )

    @app.post(
        "/internal/v1/auth/scim/resource/Groups",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(public_route())],
    )
    def scim_create_group(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        token = _resolve(request)
        members_raw = payload.get("members") or []
        member_ids: list[str] = []
        for member in members_raw:
            if isinstance(member, dict) and isinstance(member.get("value"), str):
                member_ids.append(member["value"])
        try:
            group = service.create_group(
                token=token,
                display_name=str(payload.get("displayName") or ""),
                external_id=payload.get("externalId"),
                member_user_ids=tuple(member_ids),
                mapped_role_name=payload.get("mappedRoleName"),
            )
        except ScimError as exc:
            raise _to_http(exc) from exc
        base = _base_url(request)
        members = service.list_group_members(token=token, group_id=group.group_id)
        return group_to_scim(group, base_url=base, members=members)

    @app.get(
        "/internal/v1/auth/scim/resource/Groups/{group_id}",
        dependencies=[Depends(public_route())],
    )
    def scim_get_group(request: Request, group_id: str) -> dict[str, Any]:
        token = _resolve(request)
        try:
            group = service.get_group(token=token, group_id=group_id)
        except ScimError as exc:
            raise _to_http(exc) from exc
        base = _base_url(request)
        members = service.list_group_members(token=token, group_id=group_id)
        return group_to_scim(group, base_url=base, members=members)

    @app.patch(
        "/internal/v1/auth/scim/resource/Groups/{group_id}",
        dependencies=[Depends(public_route())],
    )
    def scim_patch_group(
        request: Request, group_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        token = _resolve(request)
        ops = payload.get("Operations") or []
        if not isinstance(ops, list):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Operations must be a list"
            )
        # Group PATCH supports just members add/remove for now (the most
        # common SCIM use case). displayName change is left for a follow-up.
        try:
            for op in ops:
                verb = str(op.get("op", "")).lower()
                path = str(op.get("path", ""))
                value = op.get("value")
                if path != "members":
                    continue
                if verb == "add":
                    for entry in value or []:
                        if not isinstance(entry, dict):
                            continue
                        user_id = entry.get("value")
                        if isinstance(user_id, str):
                            service.add_group_member(
                                token=token,
                                group_id=group_id,
                                user_id=user_id,
                            )
                elif verb == "remove":
                    for entry in value or []:
                        if not isinstance(entry, dict):
                            continue
                        user_id = entry.get("value")
                        if isinstance(user_id, str):
                            service.remove_group_member(
                                token=token,
                                group_id=group_id,
                                user_id=user_id,
                            )
        except ScimError as exc:
            raise _to_http(exc) from exc
        return scim_get_group(request, group_id)

    @app.delete(
        "/internal/v1/auth/scim/resource/Groups/{group_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(public_route())],
    )
    def scim_delete_group(request: Request, group_id: str) -> None:
        token = _resolve(request)
        try:
            service.soft_delete_group(token=token, group_id=group_id)
        except ScimError as exc:
            raise _to_http(exc) from exc

    # ----- Discovery endpoints ------------------------------------------
    @app.get(
        "/internal/v1/auth/scim/resource/ServiceProviderConfig",
        dependencies=[Depends(public_route())],
    )
    def scim_service_provider_config(request: Request) -> dict[str, Any]:
        # Discovery endpoints don't strictly require a SCIM bearer (they
        # describe the SP's capabilities to the IdP) but we still gate on
        # the service token so the facade is the only public surface.
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        return service_provider_config(base_url=_base_url(request))

    @app.get(
        "/internal/v1/auth/scim/resource/Schemas",
        dependencies=[Depends(public_route())],
    )
    def scim_schemas(request: Request) -> dict[str, Any]:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        return schemas_listing()

    @app.get(
        "/internal/v1/auth/scim/resource/ResourceTypes",
        dependencies=[Depends(public_route())],
    )
    def scim_resource_types(request: Request) -> dict[str, Any]:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        return resource_types_listing(base_url=_base_url(request))


def _to_http(exc: ScimError) -> HTTPException:
    body = error_response(
        detail=str(exc),
        status_code=exc.status_code,
        scim_type=exc.scim_type,
    )
    if isinstance(exc, ScimAuthError):
        return HTTPException(401, body)
    if isinstance(exc, ScimNotFound):
        return HTTPException(404, body)
    if isinstance(exc, ScimConflict):
        return HTTPException(409, body)
    if isinstance(exc, ScimUnsupportedFilter):
        return HTTPException(400, body)
    return HTTPException(exc.status_code, body)


__all__ = ["register_scim_routes"]
