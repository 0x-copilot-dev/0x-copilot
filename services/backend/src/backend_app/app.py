"""FastAPI application for core product backend APIs."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Response, status

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
    def create_server(payload: CreateMcpServerRequest) -> McpServerResponse:
        return mcp_service(app).create_server(payload)

    @app.get("/v1/mcp/servers", response_model=McpServerListResponse)
    def list_servers(
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> McpServerListResponse:
        return mcp_service(app).list_servers(org_id=org_id, user_id=user_id)

    @app.delete("/v1/mcp/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_server(
        server_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        deleted = mcp_service(app).delete_server(org_id=org_id, user_id=user_id, server_id=server_id)
        if not deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.patch("/v1/mcp/servers/{server_id}", response_model=McpServerResponse)
    def update_server(
        server_id: str,
        payload: UpdateMcpServerRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> McpServerResponse:
        try:
            return mcp_service(app).update_server(
                org_id=org_id,
                user_id=user_id,
                server_id=server_id,
                request=payload,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.post("/v1/mcp/servers/{server_id}/auth/start", response_model=McpAuthStartResponse)
    def start_auth(server_id: str, payload: McpAuthStartRequest) -> McpAuthStartResponse:
        try:
            return mcp_service(app).start_auth(server_id=server_id, request=payload)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.post("/v1/mcp/servers/{server_id}/auth/skip", response_model=McpServerResponse)
    def skip_auth(
        server_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> McpServerResponse:
        try:
            return mcp_service(app).skip_auth(org_id=org_id, user_id=user_id, server_id=server_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.get("/v1/mcp/oauth/callback", response_model=McpServerResponse)
    def oauth_callback(state: str, code: str) -> McpServerResponse:
        try:
            return mcp_service(app).complete_auth(McpAuthCallbackRequest(state=state, code=code))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.get("/internal/v1/mcp/cards", response_model=InternalMcpServerListResponse)
    def internal_cards(
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalMcpServerListResponse:
        return mcp_service(app).list_internal_cards(org_id=org_id, user_id=user_id)

    @app.post(
        "/internal/v1/mcp/servers/{server_id}/auth/start",
        response_model=McpAuthStartResponse,
    )
    def internal_start_auth(server_id: str, payload: InternalMcpAuthRequest) -> McpAuthStartResponse:
        try:
            return mcp_service(app).start_auth(server_id=server_id, request=payload)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.post(
        "/internal/v1/mcp/servers/{server_id}/client-session",
        response_model=InternalMcpClientSession,
    )
    def internal_client_session(
        server_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalMcpClientSession:
        try:
            return mcp_service(app).create_internal_client_session(
                org_id=org_id,
                user_id=user_id,
                server_id=server_id,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.post(
        "/internal/v1/mcp/servers/{server_id}/test-token",
        response_model=McpServerResponse,
    )
    def internal_test_token(
        server_id: str,
        payload: OAuthTokenRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> McpServerResponse:
        try:
            return mcp_service(app).upsert_token_for_test(
                org_id=org_id,
                user_id=user_id,
                server_id=server_id,
                request=payload,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.post("/v1/skills", response_model=SkillResponse)
    def create_skill(payload: CreateSkillRequest) -> SkillResponse:
        try:
            return skills_service(app).create_skill(payload)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.get("/v1/skills", response_model=SkillListResponse)
    def list_skills(
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SkillListResponse:
        return skills_service(app).list_skills(org_id=org_id, user_id=user_id)

    @app.get("/v1/skills/{skill_id}", response_model=SkillResponse)
    def get_skill(
        skill_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SkillResponse:
        try:
            return skills_service(app).get_skill(org_id=org_id, user_id=user_id, skill_id=skill_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.put("/v1/skills/{skill_id}", response_model=SkillResponse)
    def update_skill(
        skill_id: str,
        payload: UpdateSkillRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SkillResponse:
        try:
            return skills_service(app).update_skill(
                org_id=org_id,
                user_id=user_id,
                skill_id=skill_id,
                request=payload,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.delete("/v1/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_skill(
        skill_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        try:
            deleted = skills_service(app).delete_skill(
                org_id=org_id,
                user_id=user_id,
                skill_id=skill_id,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        if not deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/internal/v1/skills/cards", response_model=InternalSkillListResponse)
    def internal_skill_cards(
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalSkillListResponse:
        return skills_service(app).list_internal_cards(org_id=org_id, user_id=user_id)

    @app.get("/internal/v1/skills/{skill_id}", response_model=InternalSkillBundle)
    def internal_skill_bundle(
        skill_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalSkillBundle:
        try:
            return skills_service(app).get_internal_bundle(
                org_id=org_id,
                user_id=user_id,
                skill_id=skill_id,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.get("/internal/v1/skills/by-name/{name}", response_model=InternalSkillBundle)
    def internal_skill_bundle_by_name(
        name: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalSkillBundle:
        try:
            return skills_service(app).get_internal_bundle_by_name(
                org_id=org_id,
                user_id=user_id,
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
