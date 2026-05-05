"""FastAPI facade that exposes app-facing MCP and chat APIs."""

from __future__ import annotations

from collections.abc import AsyncIterator

from enterprise_service_contracts.headers import REQUEST_ID_HEADER
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BaseModel, Field

from backend_facade.auth import AuthenticatedIdentity, FacadeAuthenticator
from backend_facade.auth_routes import register_auth_routes
from backend_facade.me_routes import register_me_routes
from backend_facade.scim_routes import register_scim_routes
from backend_facade.deployment_profile import (
    DeploymentProfile,
    log_profile,
    resolve_or_exit,
)
from backend_facade.observability import (
    RequestContextMiddleware,
    TelemetryBootstrap,
    configure_logging,
    current_context,
    emit_access_log,
)
from backend_facade.routes.health import register_health_routes
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


def create_app(
    settings: FacadeSettings | None = None,
    *,
    configure_logging_on_create: bool = True,
    configure_telemetry_on_create: bool = True,
    deployment: DeploymentProfile | None = None,
) -> FastAPI:
    if configure_logging_on_create:
        configure_logging()
    if configure_telemetry_on_create:
        TelemetryBootstrap.configure()
        TelemetryBootstrap.instrument_httpx_clients()
    resolved_deployment = deployment or resolve_or_exit()
    log_profile(resolved_deployment)
    app = FastAPI(title="Enterprise Search Backend Facade")
    app.add_middleware(RequestContextMiddleware, access_log_emitter=emit_access_log)
    if configure_telemetry_on_create:
        TelemetryBootstrap.instrument_fastapi(app)
    app.state.settings = settings or FacadeSettings.load()
    app.state.deployment = resolved_deployment

    @app.get("/v1/health")
    async def health() -> dict[str, object]:
        return {
            "service": "backend-facade",
            "deployment_profile": resolved_deployment.name,
            "feature_toggles_hash": resolved_deployment.toggles_hash(),
        }

    register_auth_routes(app)
    register_me_routes(app)
    register_scim_routes(app)

    @app.post("/v1/telemetry/otlp/v1/traces")
    async def telemetry_otlp_traces(request: Request) -> Response:
        """Pass browser-originated OTLP/HTTP traces to the in-perimeter collector.

        The browser never reaches the OTEL collector directly so the collector
        stays inside the customer perimeter; the facade is the only egress
        path. The body is forwarded as-is (OTLP/HTTP protobuf or JSON);
        identity is enforced via the standard bearer-token auth so the endpoint
        cannot be abused as an open relay.
        """

        identity = FacadeAuthenticator.authenticate_request(request)
        endpoint = settings_for(app).otel_collector_url
        if not endpoint:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        body = await request.body()
        outbound_headers = _outbound_headers(identity)
        ct = request.headers.get("content-type")
        if ct:
            outbound_headers["content-type"] = ct
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                upstream = await client.post(
                    f"{endpoint.rstrip('/')}/v1/traces",
                    content=body,
                    headers=outbound_headers,
                )
        except httpx.HTTPError:
            # Telemetry must never break the user; swallow upstream errors and
            # let the browser keep trying. The facade access log records the
            # 502 so we have a signal.
            return Response(status_code=status.HTTP_502_BAD_GATEWAY)
        return Response(
            status_code=upstream.status_code,
            content=upstream.content,
            media_type=upstream.headers.get("content-type"),
        )

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
        include_deleted: bool = False,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            "/v1/agent/conversations",
            params=identity.scoped_params(
                {
                    "limit": limit,
                    "include_archived": include_archived,
                    "include_deleted": include_deleted,
                }
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

    @app.get("/v1/agent/conversations/{conversation_id}/context")
    async def get_conversation_context(
        request: Request,
        conversation_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}/context",
            params=identity.scoped_params(),
            identity=identity,
        )

    # PR 1.2 — per-chat connector scope override. RFC 7396 merge-patch body.
    @app.patch("/v1/agent/conversations/{conversation_id}/connectors")
    async def update_conversation_connectors(
        request: Request,
        conversation_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "PATCH",
            f"/v1/agent/conversations/{conversation_id}/connectors",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    # PR 1.6 — conversation lifecycle (title/folder/archived) + soft-delete + restore.
    @app.patch("/v1/agent/conversations/{conversation_id}")
    async def update_conversation(
        request: Request,
        conversation_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "PATCH",
            f"/v1/agent/conversations/{conversation_id}",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    @app.delete("/v1/agent/conversations/{conversation_id}")
    async def delete_conversation(
        request: Request,
        conversation_id: str,
    ) -> Response:
        identity = FacadeAuthenticator.authenticate_request(request)
        # The runtime API returns 204 — preserve that shape (no JSON body).
        await forward_json_to_ai(
            app,
            "DELETE",
            f"/v1/agent/conversations/{conversation_id}",
            params=identity.scoped_params(),
            identity=identity,
        )
        return Response(status_code=204)

    @app.post("/v1/agent/conversations/{conversation_id}/restore")
    async def restore_conversation(
        request: Request,
        conversation_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "POST",
            f"/v1/agent/conversations/{conversation_id}/restore",
            params=identity.scoped_params(),
            json={},
            identity=identity,
        )

    # PR 1.6 — workspace defaults (model + connectors + retention slider).
    @app.get("/v1/agent/workspace/defaults")
    async def get_workspace_defaults(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            "/v1/agent/workspace/defaults",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.put("/v1/agent/workspace/defaults")
    async def put_workspace_defaults(
        request: Request,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "PUT",
            "/v1/agent/workspace/defaults",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    # PR 1.3 — Workspace-pane drafts. Proxied 1:1 to ai-backend.
    @app.get("/v1/agent/conversations/{conversation_id}/drafts")
    async def list_drafts(
        request: Request,
        conversation_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}/drafts",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.get("/v1/agent/drafts/{draft_id}")
    async def get_draft(
        request: Request,
        draft_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        params = dict(identity.scoped_params())
        params.update({k: v for k, v in request.query_params.items()})
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/drafts/{draft_id}",
            params=params,
            identity=identity,
        )

    @app.patch("/v1/agent/drafts/{draft_id}")
    async def patch_draft(
        request: Request,
        draft_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "PATCH",
            f"/v1/agent/drafts/{draft_id}",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    @app.post("/v1/agent/drafts/{draft_id}/send")
    async def send_draft(
        request: Request,
        draft_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "POST",
            f"/v1/agent/drafts/{draft_id}/send",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    @app.post("/v1/agent/drafts/{draft_id}/discard")
    async def discard_draft(
        request: Request,
        draft_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "POST",
            f"/v1/agent/drafts/{draft_id}/discard",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    # PR 1.5 — Workspace pane data feeds (subagents + sources). Read-only.
    @app.get("/v1/agent/conversations/{conversation_id}/subagents")
    async def list_subagents(
        request: Request,
        conversation_id: str,
        status: str | None = Query(None, min_length=1, max_length=32),
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        params: dict[str, object] = {"limit": limit}
        if status is not None:
            params["status"] = status
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}/subagents",
            params=identity.scoped_params(params),
            identity=identity,
        )

    @app.get("/v1/agent/conversations/{conversation_id}/sources")
    async def list_sources(
        request: Request,
        conversation_id: str,
        run_id: str | None = Query(None, min_length=1, max_length=128),
        limit: int = Query(200, ge=1, le=500),
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        params: dict[str, object] = {"limit": limit}
        if run_id is not None:
            params["run_id"] = run_id
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}/sources",
            params=identity.scoped_params(params),
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
        """Aggregate user/preloaded skills (backend) with system skills (ai-backend).

        System skills live on the runtime's filesystem and are exposed via an
        internal endpoint; backend never sees them. Returning a single merged
        list keeps the settings page on one fetch and lets the UI section by
        `source_type`. System skills lead so they render at the top.
        """

        identity = FacadeAuthenticator.authenticate_request(request)
        backend_payload = await forward_json(
            app,
            "GET",
            "/v1/skills",
            params=identity.scoped_params(),
            identity=identity,
        )
        system_payload = await forward_json_to_ai(
            app,
            "GET",
            "/internal/v1/skills/system",
            identity=identity,
        )
        backend_skills = _coerce_skill_list(backend_payload.get("skills"))
        system_skills = _coerce_skill_list(system_payload.get("skills"))
        return {"skills": [*system_skills, *backend_skills]}

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

        client = httpx.AsyncClient(timeout=None)
        try:
            upstream = await client.send(
                client.build_request(
                    "GET",
                    f"{settings_for(app).ai_backend_url}/v1/agent/runs/{run_id}/stream",
                    params=identity.scoped_params({"after_sequence": after_sequence}),
                    headers=_outbound_headers(identity),
                ),
                stream=True,
            )
        except Exception:
            await client.aclose()
            raise

        if upstream.status_code >= 400:
            await upstream.aread()
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(
                upstream.status_code,
                _upstream_error_detail(upstream),
            )

        async def event_stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_bytes():
                    if await request.is_disconnected():
                        break
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

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

    # ------------------------------------------------------------------
    # Usage endpoints (B4) — token + cost analytics, scoped to the caller.
    # ``/v1/usage/org`` is admin-only; until A10 RBAC ships, gating is by
    # role check at the AI-backend layer (the facade just forwards the
    # verified identity).
    # ------------------------------------------------------------------

    @app.get("/v1/usage/me")
    async def usage_me(
        request: Request,
        period: str = Query("7d"),
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            "/v1/usage/me",
            params=identity.scoped_params({"period": period}),
            identity=identity,
        )

    @app.get("/v1/usage/me/conversations")
    async def usage_me_conversations(
        request: Request,
        period: str = Query("7d"),
        limit: int = Query(10, ge=1, le=100),
    ) -> list[dict[str, object]]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(  # type: ignore[return-value]
            app,
            "GET",
            "/v1/usage/me/conversations",
            params=identity.scoped_params({"period": period, "limit": limit}),
            identity=identity,
        )

    @app.get("/v1/usage/runs/{run_id}")
    async def usage_run(
        request: Request,
        run_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/usage/runs/{run_id}",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.get("/v1/usage/conversations/{conversation_id}")
    async def usage_conversation(
        request: Request,
        conversation_id: str,
        period: str = Query("30d"),
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            f"/v1/usage/conversations/{conversation_id}",
            params=identity.scoped_params({"period": period}),
            identity=identity,
        )

    @app.get("/v1/usage/org")
    async def usage_org(
        request: Request,
        period: str = Query("month"),
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            "/v1/usage/org",
            params=identity.scoped_params({"period": period}),
            identity=identity,
        )

    # ------------------------------------------------------------------
    # Budgets (B7). Admin endpoints are gated by the same
    # FacadeAuthenticator path used elsewhere; the ``admin:budgets``
    # scope check lands in A10. ``/v1/budgets/me`` is open to any
    # authenticated user.
    # ------------------------------------------------------------------

    @app.get("/v1/budgets")
    async def list_budgets(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            "/v1/budgets",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.post("/v1/budgets")
    async def create_budget(
        request: Request,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "POST",
            "/v1/budgets",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    @app.get("/v1/budgets/me")
    async def my_budgets(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "GET",
            "/v1/budgets/me",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.patch("/v1/budgets/{budget_id}")
    async def update_budget(
        request: Request,
        budget_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "PATCH",
            f"/v1/budgets/{budget_id}",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    @app.delete("/v1/budgets/{budget_id}")
    async def delete_budget(
        request: Request,
        budget_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json_to_ai(
            app,
            "DELETE",
            f"/v1/budgets/{budget_id}",
            params=identity.scoped_params(),
            identity=identity,
        )

    register_health_routes(app)

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
        headers=_outbound_headers(identity),
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
        headers=_outbound_headers(identity),
    )


def _outbound_headers(identity: AuthenticatedIdentity) -> dict[str, str]:
    """Augment service headers with current correlation IDs.

    Identity headers come from the verified bearer token. Correlation headers
    (request_id + W3C trace context) come from the inbound request so a single
    user action stays one trace across facade -> backend / ai-backend.
    """

    headers = dict(FacadeAuthenticator.service_headers(identity))
    ctx = current_context()
    if ctx is not None and ctx.request_id:
        headers[REQUEST_ID_HEADER] = ctx.request_id
    return headers


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


def _coerce_skill_list(value: object) -> list[dict[str, object]]:
    """Tolerate upstream payload variations when concatenating skill lists.

    Both backend and ai-backend return `{"skills": [...]}`, but a future
    upstream change should not produce a 500 here — drop non-list shapes and
    non-object items so the merge is robust.
    """

    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


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
