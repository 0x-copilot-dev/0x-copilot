"""FastAPI facade that exposes app-facing MCP and chat APIs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

from enterprise_service_contracts.headers import REQUEST_ID_HEADER
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BaseModel, Field
from backend_facade.settings import FacadeSettings
from backend_facade.auth import AuthenticatedIdentity, FacadeAuthenticator
from backend_facade.adapter_registry_routes import (
    register_adapter_registry_routes,
)
from backend_facade.adapter_review_routes import register_adapter_review_routes
from backend_facade.audit_routes import register_audit_routes
from backend_facade.auth_routes import register_auth_routes
from backend_facade.home_routes import register_home_routes
from backend_facade.http_client import HttpClientPool, http_client
from backend_facade.me_routes import register_me_routes
from backend_facade.scim_routes import register_scim_routes
from backend_facade.workspace_routes import register_workspace_routes
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

ForwardTarget = Literal["backend", "ai_backend"]


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
    # Composer's Fast / Balanced / Deep selection. Forwarded verbatim to
    # ai-backend, which validates the value against its ``ReasoningDepth``
    # literal union (anything else → 422). Declared here so Pydantic's
    # default ``extra="ignore"`` doesn't drop the field on ``model_dump``.
    reasoning_depth: str | None = None
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
    app = FastAPI(
        title="Enterprise Search Backend Facade",
        lifespan=HttpClientPool.lifespan,
    )
    app.add_middleware(RequestContextMiddleware, access_log_emitter=emit_access_log)
    if configure_telemetry_on_create:
        TelemetryBootstrap.instrument_fastapi(app)
    app.state.settings = settings or FacadeSettings.load()
    app.state.deployment = resolved_deployment
    # One shared httpx.AsyncClient per worker process — every facade route
    # that hits backend / ai-backend reads it off app.state. Construction
    # is sync (httpx defers sockets to first use); the lifespan closes
    # the pool on graceful shutdown. See backend_facade/http_client.py
    # for the full why.
    HttpClientPool.attach(app)

    @app.get("/v1/health")
    async def health() -> dict[str, object]:
        return {
            "service": "backend-facade",
            "deployment_profile": resolved_deployment.name,
            "feature_toggles_hash": resolved_deployment.toggles_hash(),
        }

    register_adapter_registry_routes(app)
    register_adapter_review_routes(app)
    register_audit_routes(app)
    register_auth_routes(app)
    register_home_routes(app)
    register_me_routes(app)
    register_scim_routes(app)
    register_workspace_routes(app)

    @app.post("/v1/telemetry/otlp/v1/traces")
    async def telemetry_otlp_traces(request: Request) -> Response:
        """Pass browser-originated OTLP/HTTP traces to the in-perimeter collector.

        The browser never reaches the OTEL collector directly so the collector
        stays inside the customer perimeter; the facade is the only egress
        path. The body is forwarded as-is (OTLP/HTTP protobuf or JSON);
        identity is enforced via the standard bearer-token auth so the endpoint
        cannot be abused as an open relay.
        """

        endpoint = settings_for(app).otel_collector_url
        if not endpoint:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        identity = FacadeAuthenticator.authenticate_request(request)
        body = await request.body()
        outbound_headers = _outbound_headers(identity)
        ct = request.headers.get("content-type")
        if ct:
            outbound_headers["content-type"] = ct
        try:
            upstream = await http_client(app).post(
                f"{endpoint.rstrip('/')}/v1/traces",
                content=body,
                headers=outbound_headers,
                timeout=15,
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

    # ----- Dev IdP proxy (W0.1) -----
    # Two unauthenticated proxies to ``services/backend`` /v1/dev/*; they are
    # the bootstrap path the FE / pytest fixture / curl harness uses to mint
    # a bearer. Registered only when FACADE_ENVIRONMENT=development AND the
    # deployment profile permits it. Backend is the actual gate — it only
    # registers /v1/dev/* when BACKEND_ENVIRONMENT=development, so two-key
    # safety in production.
    if _dev_idp_enabled():

        @app.get("/v1/dev/personas")
        async def list_dev_personas() -> dict[str, object]:
            return await _proxy_dev(app, "GET", "/v1/dev/personas")

        @app.post("/v1/dev/identity/mint")
        async def mint_dev_identity(
            payload: dict[str, object],
        ) -> dict[str, object]:
            return await _proxy_dev(app, "POST", "/v1/dev/identity/mint", json=payload)

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
            target="backend",
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
            target="backend",
            params=identity.scoped_params(),
            identity=identity,
        )

    # Composer Tools popover — sectioned skill + MCP listing. Pass-through;
    # backend owns the aggregation and tags each entry with ``kind``.
    @app.get("/v1/mcp/tools")
    async def list_mcp_tools(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            "/v1/mcp/tools",
            target="backend",
            params=identity.scoped_params(),
            identity=identity,
        )

    # PR 4.4.6 — curated catalog (read-only) and explicit install path.
    @app.get("/v1/mcp/catalog")
    async def list_mcp_catalog(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            "/v1/mcp/catalog",
            target="backend",
            identity=identity,
        )

    @app.post("/v1/mcp/servers/install")
    async def install_mcp_server(
        request: Request, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "POST",
            "/v1/mcp/servers/install",
            target="backend",
            json=identity.scoped_payload(payload),
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
            target="backend",
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
            target="backend",
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
            target="backend",
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
            target="backend",
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
            target="backend",
            params=params,
            identity=identity,
        )

    @app.post("/v1/agent/conversations")
    async def create_conversation(
        request: Request, payload: FacadeConversationRequest
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "POST",
            "/v1/agent/conversations",
            target="ai_backend",
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
        return await forward_json(
            app,
            "GET",
            "/v1/agent/conversations",
            target="ai_backend",
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
        return await forward_json(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}",
            target="ai_backend",
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
        return await forward_json(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}/messages",
            target="ai_backend",
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
        return await forward_json(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}/context",
            target="ai_backend",
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
        return await forward_json(
            app,
            "PATCH",
            f"/v1/agent/conversations/{conversation_id}/connectors",
            target="ai_backend",
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
        return await forward_json(
            app,
            "PATCH",
            f"/v1/agent/conversations/{conversation_id}",
            target="ai_backend",
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
        await forward_json(
            app,
            "DELETE",
            f"/v1/agent/conversations/{conversation_id}",
            target="ai_backend",
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
        return await forward_json(
            app,
            "POST",
            f"/v1/agent/conversations/{conversation_id}/restore",
            target="ai_backend",
            params=identity.scoped_params(),
            json={},
            identity=identity,
        )

    # PR 6.2 — recipient forks a shared chat into their own workspace.
    # Identity must be authenticated (no anonymous public link in v1);
    # the share token is the access grant, not the identity.
    @app.post("/v1/agent/shares/{share_token}/fork")
    async def fork_share(
        request: Request,
        share_token: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "POST",
            f"/v1/agent/shares/{share_token}/fork",
            target="ai_backend",
            params=identity.scoped_params(),
            json=payload or {},
            identity=identity,
        )

    # PR 1.6 — workspace defaults (model + connectors + retention slider).
    @app.get("/v1/agent/workspace/defaults")
    async def get_workspace_defaults(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            "/v1/agent/workspace/defaults",
            target="ai_backend",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.put("/v1/agent/workspace/defaults")
    async def put_workspace_defaults(
        request: Request,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "PUT",
            "/v1/agent/workspace/defaults",
            target="ai_backend",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    # PR 4.3 — read-only effective retention TTL (Privacy & data panel).
    # Open to any tenant member; the ai-backend route shares the same
    # ``runtime:use`` gate.
    @app.get("/v1/retention/effective")
    async def get_retention_effective(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            "/v1/retention/effective",
            target="ai_backend",
            params=identity.scoped_params(),
            identity=identity,
        )

    # PR 4.3 — workspace data lifecycle stubs. Both routes proxy 1:1.
    # The export endpoint queues + audits (returns 202); the delete-all
    # endpoint always returns 501 and audits the typed-confirmation
    # correctness (admin-gate enforced by ai-backend).
    @app.post("/v1/agent/workspace/export")
    async def request_workspace_export(
        request: Request,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "POST",
            "/v1/agent/workspace/export",
            target="ai_backend",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    @app.delete("/v1/agent/workspace/data")
    async def delete_workspace_data(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        # ``confirm_slug`` rides as a query parameter (DELETE-with-body
        # is not idiomatic). Forward the caller's query string verbatim
        # onto the ai-backend call so the typed-confirmation reaches
        # the audit row.
        params = dict(identity.scoped_params())
        confirm_slug = request.query_params.get("confirm_slug")
        if confirm_slug is not None:
            params["confirm_slug"] = confirm_slug
        return await forward_json(
            app,
            "DELETE",
            "/v1/agent/workspace/data",
            target="ai_backend",
            params=params,
            identity=identity,
        )

    # PR 1.3 — Workspace-pane drafts. Proxied 1:1 to ai-backend.
    @app.get("/v1/agent/conversations/{conversation_id}/drafts")
    async def list_drafts(
        request: Request,
        conversation_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}/drafts",
            target="ai_backend",
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
        return await forward_json(
            app,
            "GET",
            f"/v1/agent/drafts/{draft_id}",
            target="ai_backend",
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
        return await forward_json(
            app,
            "PATCH",
            f"/v1/agent/drafts/{draft_id}",
            target="ai_backend",
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
        return await forward_json(
            app,
            "POST",
            f"/v1/agent/drafts/{draft_id}/send",
            target="ai_backend",
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
        return await forward_json(
            app,
            "POST",
            f"/v1/agent/drafts/{draft_id}/discard",
            target="ai_backend",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    # PR 6.1 — conversation sharing (creator surface + recipient view).
    # The bearer token rides in the URL path on the recipient endpoints,
    # but the caller must still be a valid session — the token grants
    # access to the *share row*, not the *user identity*.
    @app.post("/v1/agent/conversations/{conversation_id}/share")
    async def create_share(
        request: Request,
        conversation_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "POST",
            f"/v1/agent/conversations/{conversation_id}/share",
            target="ai_backend",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    @app.get("/v1/agent/conversations/{conversation_id}/shares")
    async def list_shares(
        request: Request,
        conversation_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}/shares",
            target="ai_backend",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.patch("/v1/agent/shares/{share_id}")
    async def update_share(
        request: Request,
        share_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "PATCH",
            f"/v1/agent/shares/{share_id}",
            target="ai_backend",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    @app.delete("/v1/agent/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke_share(
        request: Request,
        share_id: str,
    ) -> Response:
        identity = FacadeAuthenticator.authenticate_request(request)
        await forward_json(
            app,
            "DELETE",
            f"/v1/agent/shares/{share_id}",
            target="ai_backend",
            params=identity.scoped_params(),
            expect_json=False,
            identity=identity,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/agent/shares/{share_token}")
    async def get_shared_conversation(
        request: Request,
        share_token: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            f"/v1/agent/shares/{share_token}",
            target="ai_backend",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.get("/v1/agent/shares/{share_token}/preview")
    async def preview_shared_conversation(
        request: Request,
        share_token: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            f"/v1/agent/shares/{share_token}/preview",
            target="ai_backend",
            params=identity.scoped_params(),
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
        return await forward_json(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}/subagents",
            target="ai_backend",
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
        return await forward_json(
            app,
            "GET",
            f"/v1/agent/conversations/{conversation_id}/sources",
            target="ai_backend",
            params=identity.scoped_params(params),
            identity=identity,
        )

    @app.get("/v1/agent/models")
    async def list_models(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            "/v1/agent/models",
            target="ai_backend",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.post("/v1/agent/runs")
    async def create_run(
        request: Request, payload: FacadeRunRequest
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "POST",
            "/v1/agent/runs",
            target="ai_backend",
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
            target="backend",
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
            target="backend",
            params=identity.scoped_params(),
            identity=identity,
        )
        system_payload = await forward_json(
            app,
            "GET",
            "/internal/v1/skills/system",
            target="ai_backend",
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
            target="backend",
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
            target="backend",
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
            target="backend",
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
        return await forward_json(
            app,
            "GET",
            f"/v1/agent/runs/{run_id}/events",
            target="ai_backend",
            params=identity.scoped_params({"after_sequence": after_sequence}),
            identity=identity,
        )

    @app.get("/v1/agent/runs/{run_id}")
    async def get_run(
        request: Request,
        run_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            f"/v1/agent/runs/{run_id}",
            target="ai_backend",
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

        # SSE stream uses the shared pooled client. Only the upstream
        # response holds the connection; closing it returns the socket
        # to the pool for the next request — no per-stream client to
        # open/close. ``timeout=None`` per request keeps the stream
        # open indefinitely while the pool's default timeout protects
        # other callers.
        client = http_client(app)
        upstream = await client.send(
            client.build_request(
                "GET",
                f"{settings_for(app).ai_backend_url}/v1/agent/runs/{run_id}/stream",
                params=identity.scoped_params({"after_sequence": after_sequence}),
                headers=_outbound_headers(identity),
                timeout=None,
            ),
            stream=True,
        )

        if upstream.status_code >= 400:
            await upstream.aread()
            await upstream.aclose()
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

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/v1/agent/runs/{run_id}/cancel")
    async def cancel_run(
        request: Request,
        run_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "POST",
            f"/v1/agent/runs/{run_id}/cancel",
            target="ai_backend",
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
        return await forward_json(
            app,
            "POST",
            f"/v1/agent/approvals/{approval_id}/decision",
            target="ai_backend",
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
        return await forward_json(
            app,
            "DELETE",
            "/v1/agent/history",
            target="ai_backend",
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
        return await forward_json(
            app,
            "GET",
            "/v1/usage/me",
            target="ai_backend",
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
        return await forward_json(  # type: ignore[return-value]
            app,
            "GET",
            "/v1/usage/me/conversations",
            target="ai_backend",
            params=identity.scoped_params({"period": period, "limit": limit}),
            identity=identity,
            expect_object=False,
        )

    @app.get("/v1/usage/runs/{run_id}")
    async def usage_run(
        request: Request,
        run_id: str,
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            f"/v1/usage/runs/{run_id}",
            target="ai_backend",
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
        return await forward_json(
            app,
            "GET",
            f"/v1/usage/conversations/{conversation_id}",
            target="ai_backend",
            params=identity.scoped_params({"period": period}),
            identity=identity,
        )

    @app.get("/v1/usage/org")
    async def usage_org(
        request: Request,
        period: str = Query("month"),
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            "/v1/usage/org",
            target="ai_backend",
            params=identity.scoped_params({"period": period}),
            identity=identity,
        )

    # Sub-PRD 01d — org-scoped subagent + purpose breakdowns. Same
    # admin-or-auditor scope as /v1/usage/org (enforced on the
    # ai-backend side).
    @app.get("/v1/usage/org/subagents")
    async def usage_org_subagents(
        request: Request,
        period: str = Query("month"),
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            "/v1/usage/org/subagents",
            target="ai_backend",
            params=identity.scoped_params({"period": period}),
            identity=identity,
        )

    @app.get("/v1/usage/org/purpose")
    async def usage_org_purpose(
        request: Request,
        period: str = Query("month"),
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            "/v1/usage/org/purpose",
            target="ai_backend",
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
        return await forward_json(
            app,
            "GET",
            "/v1/budgets",
            target="ai_backend",
            params=identity.scoped_params(),
            identity=identity,
        )

    @app.post("/v1/budgets")
    async def create_budget(
        request: Request,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "POST",
            "/v1/budgets",
            target="ai_backend",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )

    @app.get("/v1/budgets/me")
    async def my_budgets(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await forward_json(
            app,
            "GET",
            "/v1/budgets/me",
            target="ai_backend",
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
        return await forward_json(
            app,
            "PATCH",
            f"/v1/budgets/{budget_id}",
            target="ai_backend",
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
        return await forward_json(
            app,
            "DELETE",
            f"/v1/budgets/{budget_id}",
            target="ai_backend",
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
    target: ForwardTarget,
    params: dict[str, object] | None = None,
    json: dict[str, object] | None = None,
    expect_json: bool = True,
    expect_object: bool = True,
    identity: AuthenticatedIdentity,
) -> object:
    """Forward an authenticated request to the named upstream service.

    ``target="backend"`` routes to ``services/backend`` (MCP / skills / OAuth /
    SCIM / dev IdP). ``target="ai_backend"`` routes to ``services/ai-backend``
    (conversations / runs / events / approvals / drafts / sources / subagents).

    Returns ``{}`` for 2xx no-content responses (e.g. DELETE → 204). Pass
    ``expect_json=False`` to receive ``None`` instead. The 204 short-circuit
    fixes Bug 2 from the W0 QA report (json.JSONDecodeError on empty body).
    """

    base_url = (
        settings_for(app).backend_url
        if target == "backend"
        else settings_for(app).ai_backend_url
    )
    return await _forward_json(
        client=http_client(app),
        base_url=base_url,
        method=method,
        path=path,
        params=params,
        json=json,
        expect_json=expect_json,
        expect_object=expect_object,
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


def _dev_idp_enabled() -> bool:
    """Whether to expose unauthenticated ``/v1/dev/*`` proxies to backend.

    Two gates:
      1. ``FACADE_ENVIRONMENT=development`` — same gate as everything else dev.
      2. The deployment profile must permit dev affordances; production
         profiles set ``dev_auth_bypass_allowed=False`` and we reuse that
         flag rather than adding a parallel knob.

    The actual safety lives on the backend side: ``/v1/dev/*`` is only
    registered there when ``BACKEND_ENVIRONMENT=development``, so even if
    the facade leaked the proxy in production the upstream would 404.
    """

    import os as _os

    if (
        _os.environ.get("FACADE_ENVIRONMENT", "development").strip().lower()
        != "development"
    ):
        return False
    try:
        from backend_facade.deployment_profile import DeploymentProfileLoader

        return DeploymentProfileLoader.load().toggles.dev_auth_bypass_allowed
    except Exception:
        return False


async def _proxy_dev(
    app: FastAPI,
    method: str,
    path: str,
    *,
    json: dict[str, object] | None = None,
) -> dict[str, object]:
    """Unauthenticated proxy to ``services/backend`` for dev-only endpoints."""

    base_url = settings_for(app).backend_url
    upstream = await http_client(app).request(
        method,
        f"{base_url}{path}",
        json=json,
        timeout=10,
    )
    if upstream.status_code >= 400:
        raise HTTPException(upstream.status_code, _upstream_error_detail(upstream))
    if upstream.status_code == 204 or not upstream.content:
        return {}
    payload = upstream.json()
    if not isinstance(payload, dict):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Upstream response was not an object"
        )
    return payload


async def _forward_json(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    method: str,
    path: str,
    params: dict[str, object] | None = None,
    json: dict[str, object] | None = None,
    expect_json: bool = True,
    expect_object: bool = True,
    headers: dict[str, str] | None = None,
) -> object:
    response = await client.request(
        method,
        f"{base_url}{path}",
        params=params,
        json=json,
        headers=headers,
        timeout=30,
    )
    if response.status_code >= 400:
        raise HTTPException(response.status_code, _upstream_error_detail(response))
    # HTTP-aware no-content handling. ai-backend's DELETE / idempotent POST
    # routes correctly return 204 No Content with empty body; calling
    # response.json() on an empty body raises JSONDecodeError. (Bug 2.)
    if (
        response.status_code == 204
        or response.headers.get("content-length") == "0"
        or not response.content
    ):
        return {} if expect_json else None  # type: ignore[return-value]
    if not expect_json:
        return {}
    payload = response.json()
    if expect_object and not isinstance(payload, dict):
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
