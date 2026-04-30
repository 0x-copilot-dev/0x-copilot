"""FastAPI facade that exposes app-facing MCP and chat APIs."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
import httpx

from backend_facade.settings import FacadeSettings


def create_app(settings: FacadeSettings | None = None) -> FastAPI:
    app = FastAPI(title="Enterprise Search Backend Facade")
    app.state.settings = settings or FacadeSettings.load()

    @app.post("/v1/mcp/servers")
    async def create_mcp_server(payload: dict[str, object]) -> dict[str, object]:
        return await forward_json(app, "POST", "/v1/mcp/servers", json=payload)

    @app.get("/v1/mcp/servers")
    async def list_mcp_servers(
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        return await forward_json(
            app,
            "GET",
            "/v1/mcp/servers",
            params={"org_id": org_id, "user_id": user_id},
        )

    @app.delete("/v1/mcp/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_mcp_server(
        server_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        await forward_json(
            app,
            "DELETE",
            f"/v1/mcp/servers/{server_id}",
            params={"org_id": org_id, "user_id": user_id},
            expect_json=False,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.patch("/v1/mcp/servers/{server_id}")
    async def update_mcp_server(
        server_id: str,
        payload: dict[str, object],
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        return await forward_json(
            app,
            "PATCH",
            f"/v1/mcp/servers/{server_id}",
            params={"org_id": org_id, "user_id": user_id},
            json=payload,
        )

    @app.post("/v1/mcp/servers/{server_id}/auth/start")
    async def start_mcp_auth(server_id: str, payload: dict[str, object]) -> dict[str, object]:
        return await forward_json(app, "POST", f"/v1/mcp/servers/{server_id}/auth/start", json=payload)

    @app.post("/v1/mcp/servers/{server_id}/auth/skip")
    async def skip_mcp_auth(
        server_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        return await forward_json(
            app,
            "POST",
            f"/v1/mcp/servers/{server_id}/auth/skip",
            params={"org_id": org_id, "user_id": user_id},
        )

    @app.get("/v1/mcp/oauth/callback")
    async def mcp_oauth_callback(
        state: str = Query(..., min_length=1),
        code: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        return await forward_json(
            app,
            "GET",
            "/v1/mcp/oauth/callback",
            params={"state": state, "code": code},
        )

    @app.post("/v1/agent/conversations")
    async def create_conversation(payload: dict[str, object]) -> dict[str, object]:
        return await forward_json_to_ai(app, "POST", "/v1/agent/conversations", json=payload)

    @app.get("/v1/agent/conversations/{conversation_id}")
    async def get_conversation(
        conversation_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}",
            params={"org_id": org_id, "user_id": user_id},
        )

    @app.get("/v1/agent/conversations/{conversation_id}/messages")
    async def get_messages(
        conversation_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        limit: int = Query(50, ge=1, le=200),
        include_deleted: bool = False,
    ) -> dict[str, object]:
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}/messages",
            params={
                "org_id": org_id,
                "user_id": user_id,
                "limit": limit,
                "include_deleted": include_deleted,
            },
        )

    @app.post("/v1/agent/runs")
    async def create_run(payload: dict[str, object]) -> dict[str, object]:
        return await forward_json_to_ai(app, "POST", "/v1/agent/runs", json=payload)

    @app.post("/v1/skills")
    async def create_skill(payload: dict[str, object]) -> dict[str, object]:
        return await forward_json(app, "POST", "/v1/skills", json=payload)

    @app.get("/v1/skills")
    async def list_skills(
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        return await forward_json(
            app,
            "GET",
            "/v1/skills",
            params={"org_id": org_id, "user_id": user_id},
        )

    @app.get("/v1/skills/{skill_id}")
    async def get_skill(
        skill_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        return await forward_json(
            app,
            "GET",
            f"/v1/skills/{skill_id}",
            params={"org_id": org_id, "user_id": user_id},
        )

    @app.put("/v1/skills/{skill_id}")
    async def update_skill(
        skill_id: str,
        payload: dict[str, object],
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        return await forward_json(
            app,
            "PUT",
            f"/v1/skills/{skill_id}",
            params={"org_id": org_id, "user_id": user_id},
            json=payload,
        )

    @app.delete("/v1/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_skill(
        skill_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        await forward_json(
            app,
            "DELETE",
            f"/v1/skills/{skill_id}",
            params={"org_id": org_id, "user_id": user_id},
            expect_json=False,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/agent/runs/{run_id}/events")
    async def run_events(
        run_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        after_sequence: int = Query(0, ge=0),
    ) -> dict[str, object]:
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/runs/{run_id}/events",
            params={"org_id": org_id, "user_id": user_id, "after_sequence": after_sequence},
        )

    @app.get("/v1/agent/runs/{run_id}")
    async def get_run(
        run_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/runs/{run_id}",
            params={"org_id": org_id, "user_id": user_id},
        )

    @app.get("/v1/agent/runs/{run_id}/stream")
    async def stream_run(
        request: Request,
        run_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        after_sequence: int = Query(0, ge=0),
    ) -> StreamingResponse:
        async def event_stream() -> AsyncIterator[bytes]:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "GET",
                    f"{settings_for(app).ai_backend_url}/v1/agent/runs/{run_id}/stream",
                    params={"org_id": org_id, "user_id": user_id, "after_sequence": after_sequence},
                ) as response:
                    async for chunk in response.aiter_bytes():
                        if await request.is_disconnected():
                            break
                        yield chunk

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/v1/agent/runs/{run_id}/cancel")
    async def cancel_run(
        run_id: str,
        payload: dict[str, object],
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        return await forward_json_to_ai(
            app,
            "POST",
            f"/v1/agent/runs/{run_id}/cancel",
            params={"org_id": org_id, "user_id": user_id},
            json=payload,
        )

    @app.post("/v1/agent/approvals/{approval_id}/decision")
    async def approval_decision(
        approval_id: str,
        payload: dict[str, object],
        org_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        return await forward_json_to_ai(
            app,
            "POST",
            f"/v1/agent/approvals/{approval_id}/decision",
            params={"org_id": org_id},
            json=payload,
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
) -> dict[str, object]:
    return await _forward_json(
        base_url=settings_for(app).backend_url,
        method=method,
        path=path,
        params=params,
        json=json,
        expect_json=expect_json,
    )


async def forward_json_to_ai(
    app: FastAPI,
    method: str,
    path: str,
    *,
    params: dict[str, object] | None = None,
    json: dict[str, object] | None = None,
) -> dict[str, object]:
    return await _forward_json(
        base_url=settings_for(app).ai_backend_url,
        method=method,
        path=path,
        params=params,
        json=json,
    )


async def _forward_json(
    *,
    base_url: str,
    method: str,
    path: str,
    params: dict[str, object] | None = None,
    json: dict[str, object] | None = None,
    expect_json: bool = True,
) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(method, f"{base_url}{path}", params=params, json=json)
    if response.status_code >= 400:
        raise HTTPException(response.status_code, response.text)
    if not expect_json:
        return {}
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Upstream response was not an object")
    return payload


def settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


app = create_app()
