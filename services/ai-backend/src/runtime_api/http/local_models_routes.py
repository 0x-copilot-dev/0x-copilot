"""``/v1/local-models/*`` — manage a user-installed local Ollama (Round 2).

A thin, deployment-gated proxy over Ollama's own HTTP API plus a Hugging
Face size lookup. The feature is off unless ``RUNTIME_ENABLE_LOCAL_MODELS``
is set (desktop-runtime + self-host set it): ``/status`` always answers so
the client can hide the section, but every other route 404s when disabled —
server-authoritative, never client-trust.

Endpoints:
  GET    /v1/local-models/status              → LocalModelsStatus (always 200)
  GET    /v1/local-models                     → LocalModelsList
  GET    /v1/local-models/size?repo=&quant=   → LocalModelSize
  POST   /v1/local-models/pull  {repo,quant}  → SSE pull-progress stream
  POST   /v1/local-models/runtime/start       → LocalModelsStatus
  DELETE /v1/local-models/{name:path}         → 204

``/runtime/start`` (PRD-P8 §4.3) is the first route here with a side effect on
the host, so it carries a second gate — ``RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME``
— and emits an audit event.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.openai_compat import OpenAICompatibleProviders
from copilot_service_contracts.headers import ORG_HEADER, USER_HEADER
from copilot_service_contracts.scopes import RUNTIME_USE
from runtime_api.http.errors import RuntimeApiError
from runtime_api.local_models import HfGgufResolver, LocalModelError, LocalModelService
from runtime_api.local_models.ollama_client import OllamaClient
from runtime_api.local_models.ollama_runtime import OllamaRuntimeController
from runtime_api.rbac import RequireScopes
from runtime_api.schemas.local_models import (
    LocalModelErrorKind,
    LocalModelPullEvent,
    LocalModelSize,
    LocalModelsList,
    LocalModelsStatus,
)

_SSE_EVENT_NAME = "local_model_pull"
_SSE_MEDIA_TYPE = "text/event-stream"
_AUDIT_EVENT_TYPE = "local_models.runtime_start"
_UNKNOWN_PRINCIPAL = "unknown"
_LOGGER = logging.getLogger(__name__)


class LocalModelsApiRouter:
    """Build and serve the ``/v1/local-models`` router."""

    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(
            prefix="/v1/local-models",
            tags=["local-models"],
            dependencies=[Depends(RequireScopes(RUNTIME_USE))],
        )
        router.add_api_route(
            "/status", cls.status, methods=["GET"], response_model=LocalModelsStatus
        )
        router.add_api_route(
            "", cls.list_models, methods=["GET"], response_model=LocalModelsList
        )
        router.add_api_route(
            "/size", cls.download_size, methods=["GET"], response_model=LocalModelSize
        )
        # SSE stream — GET so it rides the browser EventSource / transport
        # SSE lane (which is query-only, no request body).
        router.add_api_route("/pull", cls.pull, methods=["GET"])
        # Registered before the ``{name:path}`` catch-all so the literal path
        # always wins, independent of method.
        router.add_api_route(
            "/runtime/start",
            cls.start_runtime,
            methods=["POST"],
            response_model=LocalModelsStatus,
        )
        router.add_api_route(
            "/{name:path}",
            cls.delete_model,
            methods=["DELETE"],
            status_code=status.HTTP_204_NO_CONTENT,
        )
        return router

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    @classmethod
    async def status(cls, request: Request) -> LocalModelsStatus:
        enabled = cls._enabled(request)
        return await cls._service(request).status(enabled=enabled)

    @classmethod
    async def list_models(cls, request: Request) -> LocalModelsList:
        cls._require_enabled(request)
        return await cls._guard(cls._service(request).list_models())

    @classmethod
    async def download_size(
        cls,
        request: Request,
        repo: str = Query(..., min_length=1, max_length=200),
        quant: str = Query(..., min_length=1, max_length=64),
    ) -> LocalModelSize:
        cls._require_enabled(request)
        return await cls._guard(
            cls._service(request).download_size(repo=repo, quant=quant)
        )

    @classmethod
    async def pull(
        cls,
        request: Request,
        repo: str = Query(..., min_length=1, max_length=200),
        quant: str = Query(..., min_length=1, max_length=64),
    ) -> StreamingResponse:
        cls._require_enabled(request)
        service = cls._service(request)
        return StreamingResponse(
            cls._pull_stream(service, request, repo, quant),
            media_type=_SSE_MEDIA_TYPE,
        )

    @classmethod
    async def start_runtime(cls, request: Request) -> LocalModelsStatus:
        """Start (or confirm) the host's local model runtime — PRD-P8 §4.3."""

        cls._require_enabled(request)
        cls._require_runtime_control(request)
        result = await cls._guard(cls._service(request).start_runtime())
        cls._audit_runtime_start(request, result)
        return result

    @classmethod
    async def delete_model(cls, request: Request, name: str) -> Response:
        cls._require_enabled(request)
        await cls._guard(cls._service(request).delete(name=name))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    @classmethod
    async def _pull_stream(
        cls,
        service: LocalModelService,
        request: Request,
        repo: str,
        quant: str,
    ) -> AsyncIterator[str]:
        last_sequence = 0
        try:
            async for event in service.pull_events(repo=repo, quant=quant):
                if await request.is_disconnected():
                    return
                last_sequence = event.sequence_no
                yield cls._sse_frame(event)
        except LocalModelError as exc:
            yield cls._sse_frame(
                LocalModelPullEvent(
                    sequence_no=last_sequence + 1,
                    status="error",
                    done=True,
                    error=exc.public_message,
                    error_kind=exc.kind,
                )
            )

    @staticmethod
    def _sse_frame(event: LocalModelPullEvent) -> str:
        return (
            f"event: {_SSE_EVENT_NAME}\n"
            f"id: {event.sequence_no}\n"
            f"data: {event.model_dump_json()}\n\n"
        )

    # ------------------------------------------------------------------
    # Wiring + gating
    # ------------------------------------------------------------------

    @staticmethod
    def _enabled(request: Request) -> bool:
        settings = getattr(request.app.state, "runtime_settings", None)
        return bool(settings and settings.execution.enable_local_models)

    @staticmethod
    def _runtime_managed(request: Request) -> bool:
        """Whether this deployment may detect/spawn the host runtime (PRD D2)."""

        settings = getattr(request.app.state, "runtime_settings", None)
        return bool(settings and settings.execution.local_models_manage_runtime)

    @classmethod
    def _require_enabled(cls, request: Request) -> None:
        if not cls._enabled(request):
            raise RuntimeApiError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "Local models are not enabled on this deployment.",
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )

    @classmethod
    def _require_runtime_control(cls, request: Request) -> None:
        """404 unless this deployment manages the runtime — server-authoritative."""

        if not cls._runtime_managed(request):
            raise RuntimeApiError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "Local runtime control is not enabled on this deployment.",
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )

    @classmethod
    def _service(cls, request: Request) -> LocalModelService:
        # Allow tests to inject a fake service via app.state.
        override = getattr(request.app.state, "local_model_service", None)
        if override is not None:
            return override
        endpoint = OpenAICompatibleProviders.get("ollama")
        assert endpoint is not None  # registered at import time
        api_root = OllamaClient.api_root_from_openai_base(endpoint.resolve_base_url())
        return LocalModelService(
            ollama=OllamaClient(base_url=api_root),
            hf=HfGgufResolver(),
            runtime=OllamaRuntimeController(manage=cls._runtime_managed(request)),
        )

    @staticmethod
    async def _guard(awaitable):  # type: ignore[no-untyped-def]
        try:
            return await awaitable
        except LocalModelError as exc:
            raise RuntimeApiError(
                RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
                exc.public_message,
                http_status=status.HTTP_502_BAD_GATEWAY,
                # A terminal failure (bad repo, disk full, refused spawn) will
                # fail identically on retry; only the recoverable kinds are
                # advertised as retryable.
                retryable=exc.kind is not LocalModelErrorKind.TERMINAL,
            ) from exc

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    @classmethod
    def _audit_runtime_start(cls, request: Request, result: LocalModelsStatus) -> None:
        """Record the host-side side effect. Best-effort; never fails the call.

        The structured log is the load-bearing record; the chained audit row
        is defence-in-depth when an appender is wired (same contract as
        ``runtime_api.rbac``).
        """

        org_id = request.headers.get(ORG_HEADER, "").strip() or _UNKNOWN_PRINCIPAL
        user_id = request.headers.get(USER_HEADER, "").strip() or _UNKNOWN_PRINCIPAL
        metadata = {
            "runtime_state": result.runtime_state.value,
            "ollama_running": result.ollama_running,
            "route": request.url.path,
        }
        _LOGGER.info(
            _AUDIT_EVENT_TYPE,
            extra={"safe_message": _AUDIT_EVENT_TYPE, "metadata": metadata},
        )
        appender = getattr(request.app.state, "runtime_audit_appender", None)
        if appender is None:
            return
        try:
            appender(
                org_id=org_id,
                event_type=_AUDIT_EVENT_TYPE,
                data={
                    "user_id": user_id,
                    "actor_type": "session",
                    "resource_type": "local_model_runtime",
                    "resource_id": "ollama",
                    "outcome": "started"
                    if result.ollama_running
                    else "start_requested",
                    "metadata": metadata,
                },
            )
        except Exception:
            _LOGGER.exception(
                "local-models runtime-start audit append failed",
                extra={"safe_message": _AUDIT_EVENT_TYPE},
            )


__all__ = ["LocalModelsApiRouter"]
