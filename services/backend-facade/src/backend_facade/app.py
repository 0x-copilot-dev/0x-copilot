"""FastAPI facade that exposes app-facing MCP and chat APIs."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BaseModel, Field

from backend_facade.auth import AuthenticatedIdentity, FacadeAuthenticator
from backend_facade.settings import FacadeSettings


class FacadeConversationRequest(BaseModel):
    org_id: str | None = None
    user_id: str | None = None
    assistant_id: str | None = None
    title: str | None = None
    idempotency_key: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class FacadeRunRequest(BaseModel):
    conversation_id: str
    org_id: str | None = None
    user_id: str | None = None
    user_input: str
    assistant_id: str | None = None
    model: dict[str, object] | None = None
    content: list[dict[str, object]] | None = None
    attachments: list[dict[str, object]] | None = None
    quote: dict[str, object] | None = None
    parent_message_id: str | None = None
    source_message_id: str | None = None
    regenerate_from_message_id: str | None = None
    branch_id: str | None = None
    branch: dict[str, object] | None = None
    idempotency_key: str | None = None
    request_context: dict[str, object] = Field(default_factory=dict)


def create_app(settings: FacadeSettings | None = None) -> FastAPI:
    app = FastAPI(title="Enterprise Search Backend Facade")
    app.state.settings = settings or FacadeSettings.load()

    @app.get("/v1/session")
    async def get_session(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return {
            "identity": {
                "org_id": identity.org_id,
                "user_id": identity.user_id,
                "roles": list(identity.roles),
                "permission_scopes": list(identity.permission_scopes),
            }
        }

    @app.post("/v1/mcp/servers")
    async def create_mcp_server(
        request: Request, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "POST",
            "/v1/mcp/servers",
            json=identity.scoped_payload(payload),
            identity=identity,
        )

    @app.get("/v1/mcp/servers")
    async def list_mcp_servers(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            "/v1/mcp/servers",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.delete("/v1/mcp/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_mcp_server(
        request: Request,
        server_id: str,
    ) -> Response:
        identity = FacadeAuthenticator.authenticate_request(request)
        await forward_json(
            app,
            "DELETE",
            f"/v1/mcp/servers/{server_id}",
            params=identity.scoped_params(),
            expect_json=False,
            identity=identity,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.patch("/v1/mcp/servers/{server_id}")
    async def update_mcp_server(
        request: Request,
        server_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "PATCH",
            f"/v1/mcp/servers/{server_id}",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    @app.post("/v1/mcp/servers/{server_id}/auth/start")
    async def start_mcp_auth(
        request: Request, server_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "POST",
            f"/v1/mcp/servers/{server_id}/auth/start",
            json=identity.scoped_payload(payload),
            identity=identity,
        )

    @app.post("/v1/mcp/servers/{server_id}/auth/skip")
    async def skip_mcp_auth(
        request: Request,
        server_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "POST",
            f"/v1/mcp/servers/{server_id}/auth/skip",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.get("/v1/mcp/oauth/callback")
    async def mcp_oauth_callback(
        request: Request,
        state: str = Query(..., min_length=1),
        code: str | None = Query(None, min_length=1),
        error: str | None = Query(None, min_length=1),
        error_description: str | None = Query(None, min_length=1),
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        params: dict[str, str] = {"state": state}
        if code is not None:
            params["code"] = code
        if error is not None:
            params["error"] = error
        if error_description is not None:
            params["error_description"] = error_description
        return await forward_json(
            app,
            "GET",
            "/v1/mcp/oauth/callback",
            params=params,
            identity=identity,
        )

    @app.post("/v1/agent/conversations")
    async def create_conversation(
        request: Request, payload: FacadeConversationRequest
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "POST",
            "/v1/agent/conversations",
            json=identity.scoped_payload(payload.model_dump(exclude_none=True)),
            identity=identity,
        )

    @app.get("/v1/agent/conversations")
    async def list_conversations(
        request: Request,
        limit: int = Query(30, ge=1, le=200),
        include_archived: bool = False,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            "/v1/agent/conversations",
            params=identity.scoped_params(
                {"limit": limit, "include_archived": include_archived}
            ),
            identity=identity,
        )

    @app.get("/v1/agent/conversations/{conversation_id}")
    async def get_conversation(
        request: Request,
        conversation_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.get("/v1/agent/conversations/{conversation_id}/messages")
    async def get_messages(
        request: Request,
        conversation_id: str,
        limit: int = Query(50, ge=1, le=200),
        include_deleted: bool = False,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}/messages",
            params=identity.scoped_params(
                {"limit": limit, "include_deleted": include_deleted}
            ),
            identity=identity,
        )

    @app.get("/v1/agent/models")
    async def list_models(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            "/v1/agent/models",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.post("/v1/agent/runs")
    async def create_run(
        request: Request, payload: FacadeRunRequest
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "POST",
            "/v1/agent/runs",
            json=identity.scoped_payload(
                payload.model_dump(exclude_none=True), include_request_context=True
            ),
            identity=identity,
        )

    @app.post("/v1/skills")
    async def create_skill(
        request: Request, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "POST",
            "/v1/skills",
            json=identity.scoped_payload(payload),
            identity=identity,
        )

    @app.get("/v1/skills")
    async def list_skills(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            "/v1/skills",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.get("/v1/skills/{skill_id}")
    async def get_skill(
        request: Request,
        skill_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            f"/v1/skills/{skill_id}",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.put("/v1/skills/{skill_id}")
    async def update_skill(
        request: Request,
        skill_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "PUT",
            f"/v1/skills/{skill_id}",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    @app.delete("/v1/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_skill(
        request: Request,
        skill_id: str,
    ) -> Response:
        identity = FacadeAuthenticator.authenticate_request(request)
        await forward_json(
            app,
            "DELETE",
            f"/v1/skills/{skill_id}",
            params=identity.scoped_params(),
            expect_json=False,
            identity=identity,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/agent/runs/{run_id}/events")
    async def run_events(
        request: Request,
        run_id: str,
        after_sequence: int = Query(0, ge=0),
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/runs/{run_id}/events",
            params=identity.scoped_params({"after_sequence": after_sequence}),
            identity=identity,
        )

    @app.get("/v1/agent/runs/{run_id}")
    async def get_run(
        request: Request,
        run_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/runs/{run_id}",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.get("/v1/agent/runs/{run_id}/stream")
    async def stream_run(
        request: Request,
        run_id: str,
        after_sequence: int = Query(0, ge=0),
    ) -> StreamingResponse:
        identity = FacadeAuthenticator.authenticate_request(request)

        async def event_stream() -> AsyncIterator[bytes]:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "GET",
                    f"{settings_for(app).ai_backend_url}/v1/agent/runs/{run_id}/stream",
                    params=identity.scoped_params({"after_sequence": after_sequence}),
                    headers=FacadeAuthenticator.service_headers(identity),
                ) as response:
                    async for chunk in response.aiter_bytes():
                        if await request.is_disconnected():
                            break
                        yield chunk

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/v1/agent/runs/{run_id}/cancel")
    async def cancel_run(
        request: Request,
        run_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "POST",
            f"/v1/agent/runs/{run_id}/cancel",
            params=identity.scoped_params(),
            json={**payload, "requested_by_user_id": identity.user_id},
            identity=identity,
        )

    @app.post("/v1/agent/approvals/{approval_id}/decision")
    async def approval_decision(
        request: Request,
        approval_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "POST",
            f"/v1/agent/approvals/{approval_id}/decision",
            params={"org_id": identity.org_id},
            json={**payload, "decided_by_user_id": identity.user_id},
            identity=identity,
        )

    @app.delete("/v1/agent/history")
    async def delete_agent_history(
        request: Request,
        reason: str | None = Query(None),
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "DELETE",
            "/v1/agent/history",
            params=identity.scoped_params({"reason": reason} if reason else None),
            identity=identity,
        )

    return app


async def forward_json(
    app: FastAPI,
    method: str,
    path: str,
    *,
    params: dict[str, object] | None = None,
    json: dict[str, object] | None = None,
    expect_json: bool = True,
    identity: AuthenticatedIdentity,
) -> dict[str, object]:
    return await _forward_json(
        base_url=settings_for(app).backend_url,
        method=method,
        path=path,
        params=params,
        json=json,
        expect_json=expect_json,
        headers=FacadeAuthenticator.service_headers(identity),
    )


async def forward_json_to_ai(
    app: FastAPI,
    method: str,
    path: str,
    *,
    params: dict[str, object] | None = None,
    json: dict[str, object] | None = None,
    identity: AuthenticatedIdentity,
) -> dict[str, object]:
    return await _forward_json(
        base_url=settings_for(app).ai_backend_url,
        method=method,
        path=path,
        params=params,
        json=json,
        headers=FacadeAuthenticator.service_headers(identity),
    )


async def _forward_json(
    *,
    base_url: str,
    method: str,
    path: str,
    params: dict[str, object] | None = None,
    json: dict[str, object] | None = None,
    expect_json: bool = True,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(
            method,
            f"{base_url}{path}",
            params=params,
            json=json,
            headers=headers,
        )
    if response.status_code >= 400:
        raise HTTPException(response.status_code, _upstream_error_detail(response))
    if not expect_json:
        return {}
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Upstream response was not an object"
        )
    return payload


def _upstream_error_detail(response: httpx.Response) -> object:
    """Preserve upstream error detail without exposing transport internals."""

    try:
        payload = response.json()
    except ValueError:
        return response.text or "Upstream request failed"
    if isinstance(payload, dict) and "detail" in payload:
        return payload["detail"]
    return payload if payload else "Upstream request failed"


def settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


app = create_app()
