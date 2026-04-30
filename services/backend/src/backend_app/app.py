"""FastAPI application for core product backend APIs."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request, Response, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import (
    CreateMcpServerRequest,
    CreateSkillRequest,
    InternalMcpAuthRequest,
    InternalMcpClientSession,
    InternalMcpServerListResponse,
    InternalSkillBundle,
    InternalSkillListResponse,
    McpAuthCallbackRequest,
    McpAuthStartRequest,
    McpAuthStartResponse,
    McpServerListResponse,
    McpServerResponse,
    OAuthTokenRequest,
    SkillListResponse,
    SkillResponse,
    UpdateMcpServerRequest,
    UpdateSkillRequest,
)
from backend_app.service import McpRegistryService, SkillRegistryService


def create_app(
    service: McpRegistryService | None = None,
    skill_service: SkillRegistryService | None = None,
) -> FastAPI:
    app = FastAPI(title="Enterprise Search Backend")
    app.state.mcp_service = service or McpRegistryService()
    app.state.skill_service = skill_service or SkillRegistryService()

    @app.post("/v1/mcp/servers", response_model=McpServerResponse)
    def create_server(
        request: Request, payload: CreateMcpServerRequest
    ) -> McpServerResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        payload = payload.model_copy(
            update={"org_id": identity.org_id, "user_id": identity.user_id}
        )
        return mcp_service(app).create_server(payload)

    @app.get("/v1/mcp/servers", response_model=McpServerListResponse)
    def list_servers(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> McpServerListResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return mcp_service(app).list_servers(
            org_id=identity.org_id, user_id=identity.user_id
        )

    @app.delete("/v1/mcp/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_server(
        request: Request,
        server_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        deleted = mcp_service(app).delete_server(
            org_id=identity.org_id, user_id=identity.user_id, server_id=server_id
        )
        if not deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.patch("/v1/mcp/servers/{server_id}", response_model=McpServerResponse)
    def update_server(
        request: Request,
        server_id: str,
        payload: UpdateMcpServerRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> McpServerResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return mcp_service(app).update_server(
                org_id=identity.org_id,
                user_id=identity.user_id,
                server_id=server_id,
                request=payload,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.post(
        "/v1/mcp/servers/{server_id}/auth/start", response_model=McpAuthStartResponse
    )
    def start_auth(
        request: Request, server_id: str, payload: McpAuthStartRequest
    ) -> McpAuthStartResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        payload = payload.model_copy(
            update={"org_id": identity.org_id, "user_id": identity.user_id}
        )
        try:
            return mcp_service(app).start_auth(server_id=server_id, request=payload)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.post("/v1/mcp/servers/{server_id}/auth/skip", response_model=McpServerResponse)
    def skip_auth(
        request: Request,
        server_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> McpServerResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return mcp_service(app).skip_auth(
                org_id=identity.org_id, user_id=identity.user_id, server_id=server_id
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.get("/v1/mcp/oauth/callback", response_model=McpServerResponse)
    def oauth_callback(state: str, code: str) -> McpServerResponse:
        try:
            return mcp_service(app).complete_auth(
                McpAuthCallbackRequest(state=state, code=code)
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.get("/internal/v1/mcp/cards", response_model=InternalMcpServerListResponse)
    def internal_cards(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalMcpServerListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return mcp_service(app).list_internal_cards(
            org_id=identity.org_id, user_id=identity.user_id
        )

    @app.post(
        "/internal/v1/mcp/servers/{server_id}/auth/start",
        response_model=McpAuthStartResponse,
    )
    def internal_start_auth(
        request: Request, server_id: str, payload: InternalMcpAuthRequest
    ) -> McpAuthStartResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request,
            org_id=payload.org_id,
            user_id=payload.user_id,
        )
        payload = payload.model_copy(
            update={"org_id": identity.org_id, "user_id": identity.user_id}
        )
        try:
            return mcp_service(app).start_auth(server_id=server_id, request=payload)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.post(
        "/internal/v1/mcp/servers/{server_id}/client-session",
        response_model=InternalMcpClientSession,
    )
    def internal_client_session(
        request: Request,
        server_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalMcpClientSession:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return mcp_service(app).create_internal_client_session(
                org_id=identity.org_id,
                user_id=identity.user_id,
                server_id=server_id,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.post(
        "/internal/v1/mcp/servers/{server_id}/test-token",
        response_model=McpServerResponse,
    )
    def internal_test_token(
        request: Request,
        server_id: str,
        payload: OAuthTokenRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> McpServerResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return mcp_service(app).upsert_token_for_test(
                org_id=identity.org_id,
                user_id=identity.user_id,
                server_id=server_id,
                request=payload,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.post("/v1/skills", response_model=SkillResponse)
    def create_skill(request: Request, payload: CreateSkillRequest) -> SkillResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        payload = payload.model_copy(
            update={"org_id": identity.org_id, "user_id": identity.user_id}
        )
        try:
            return skills_service(app).create_skill(payload)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.get("/v1/skills", response_model=SkillListResponse)
    def list_skills(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SkillListResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return skills_service(app).list_skills(
            org_id=identity.org_id, user_id=identity.user_id
        )

    @app.get("/v1/skills/{skill_id}", response_model=SkillResponse)
    def get_skill(
        request: Request,
        skill_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SkillResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return skills_service(app).get_skill(
                org_id=identity.org_id, user_id=identity.user_id, skill_id=skill_id
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.put("/v1/skills/{skill_id}", response_model=SkillResponse)
    def update_skill(
        request: Request,
        skill_id: str,
        payload: UpdateSkillRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SkillResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return skills_service(app).update_skill(
                org_id=identity.org_id,
                user_id=identity.user_id,
                skill_id=skill_id,
                request=payload,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.delete("/v1/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_skill(
        request: Request,
        skill_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            deleted = skills_service(app).delete_skill(
                org_id=identity.org_id,
                user_id=identity.user_id,
                skill_id=skill_id,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        if not deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/internal/v1/skills/cards", response_model=InternalSkillListResponse)
    def internal_skill_cards(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalSkillListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return skills_service(app).list_internal_cards(
            org_id=identity.org_id, user_id=identity.user_id
        )

    @app.get("/internal/v1/skills/{skill_id}", response_model=InternalSkillBundle)
    def internal_skill_bundle(
        request: Request,
        skill_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalSkillBundle:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return skills_service(app).get_internal_bundle(
                org_id=identity.org_id,
                user_id=identity.user_id,
                skill_id=skill_id,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.get("/internal/v1/skills/by-name/{name}", response_model=InternalSkillBundle)
    def internal_skill_bundle_by_name(
        request: Request,
        name: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalSkillBundle:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return skills_service(app).get_internal_bundle_by_name(
                org_id=identity.org_id,
                user_id=identity.user_id,
                name=name,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return app


def mcp_service(app: FastAPI) -> McpRegistryService:
    return app.state.mcp_service


def skills_service(app: FastAPI) -> SkillRegistryService:
    return app.state.skill_service


app = create_app()
