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
  DELETE /v1/local-models/{name:path}         → 204
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.openai_compat import OpenAICompatibleProviders
from copilot_service_contracts.scopes import RUNTIME_USE
from runtime_api.http.errors import RuntimeApiError
from runtime_api.local_models import HfGgufResolver, LocalModelError, LocalModelService
from runtime_api.local_models.ollama_client import OllamaClient
from runtime_api.rbac import RequireScopes
from runtime_api.schemas.local_models import (
    LocalModelPullEvent,
    LocalModelSize,
    LocalModelsList,
    LocalModelsStatus,
)

_SSE_EVENT_NAME = "local_model_pull"
_SSE_MEDIA_TYPE = "text/event-stream"


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

    @classmethod
    def _require_enabled(cls, request: Request) -> None:
        if not cls._enabled(request):
            raise RuntimeApiError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "Local models are not enabled on this deployment.",
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )

    @staticmethod
    def _service(request: Request) -> LocalModelService:
        # Allow tests to inject a fake service via app.state.
        override = getattr(request.app.state, "local_model_service", None)
        if override is not None:
            return override
        endpoint = OpenAICompatibleProviders.get("ollama")
        assert endpoint is not None  # registered at import time
        api_root = OllamaClient.api_root_from_openai_base(endpoint.resolve_base_url())
        return LocalModelService(
            ollama=OllamaClient(base_url=api_root), hf=HfGgufResolver()
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
                retryable=True,
            ) from exc


__all__ = ["LocalModelsApiRouter"]
