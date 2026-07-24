"""Raw streaming facade for the public Artifact Repository surface."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import StreamingResponse

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


class ArtifactProxy:
    """Authenticate once, then stream request and response bytes unchanged."""

    REQUEST_HEADERS = frozenset(
        {
            "content-type",
            "idempotency-key",
            "if-match",
            "range",
            "if-range",
            "digest",
        }
    )
    RESPONSE_HEADERS = frozenset(
        {
            "content-type",
            "content-length",
            "content-range",
            "accept-ranges",
            "content-disposition",
            "etag",
            "cache-control",
            "x-content-type-options",
        }
    )
    QUERY_FIELDS = frozenset({"kind", "limit", "cursor"})

    @classmethod
    async def forward(
        cls,
        *,
        app: FastAPI,
        request: Request,
        upstream_path: str,
    ) -> Response:
        settings = cls._settings(app)
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request,
            backend_url=settings.backend_url,
            http_client=client,
        )
        headers = dict(FacadeAuthenticator.service_headers(identity))
        headers["Accept-Encoding"] = "identity"
        for name in cls.REQUEST_HEADERS:
            value = request.headers.get(name)
            if value is not None:
                headers[name] = value
        params = [
            (name, value)
            for name, value in request.query_params.multi_items()
            if name in cls.QUERY_FIELDS
        ]
        body = request.stream() if request.method in {"POST", "PUT", "PATCH"} else None
        upstream = await client.send(
            client.build_request(
                request.method,
                f"{settings.ai_backend_url}{upstream_path}",
                params=params,
                headers=headers,
                content=body,
                timeout=None,
            ),
            stream=True,
        )
        response_headers = {
            name: value
            for name, value in upstream.headers.items()
            if name.lower() in cls.RESPONSE_HEADERS
        }
        if upstream.status_code == status.HTTP_204_NO_CONTENT:
            await upstream.aclose()
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return StreamingResponse(
            cls._raw_response(request=request, upstream=upstream),
            status_code=upstream.status_code,
            headers=response_headers,
        )

    @staticmethod
    async def _raw_response(
        *,
        request: Request,
        upstream: httpx.Response,
    ) -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            await upstream.aclose()

    @staticmethod
    def _settings(app: FastAPI) -> FacadeSettings:
        return app.state.settings


def register_artifact_proxy_routes(app: FastAPI) -> None:
    """Attach the eight facade-only product routes.

    The literal promotion path is registered before artifact-id routes.
    """

    @app.post("/v1/agent/artifacts:promote")
    async def promote_artifact(request: Request) -> Response:
        return await ArtifactProxy.forward(
            app=app,
            request=request,
            upstream_path="/v1/agent/artifacts:promote",
        )

    @app.get("/v1/agent/runs/{run_id}/artifacts")
    async def list_run_artifacts(request: Request, run_id: str) -> Response:
        return await ArtifactProxy.forward(
            app=app,
            request=request,
            upstream_path=f"/v1/agent/runs/{run_id}/artifacts",
        )

    @app.post("/v1/agent/runs/{run_id}/artifacts")
    async def create_run_artifact(request: Request, run_id: str) -> Response:
        return await ArtifactProxy.forward(
            app=app,
            request=request,
            upstream_path=f"/v1/agent/runs/{run_id}/artifacts",
        )

    @app.get("/v1/agent/artifacts/{artifact_id}/revisions/{revision}/content")
    async def get_artifact_content(
        request: Request,
        artifact_id: str,
        revision: int,
    ) -> Response:
        return await ArtifactProxy.forward(
            app=app,
            request=request,
            upstream_path=(
                f"/v1/agent/artifacts/{artifact_id}/revisions/{revision}/content"
            ),
        )

    @app.get("/v1/agent/artifacts/{artifact_id}/revisions/{revision}")
    async def get_artifact_revision(
        request: Request,
        artifact_id: str,
        revision: int,
    ) -> Response:
        return await ArtifactProxy.forward(
            app=app,
            request=request,
            upstream_path=f"/v1/agent/artifacts/{artifact_id}/revisions/{revision}",
        )

    @app.post("/v1/agent/artifacts/{artifact_id}/revisions")
    async def append_artifact_revision(
        request: Request,
        artifact_id: str,
    ) -> Response:
        return await ArtifactProxy.forward(
            app=app,
            request=request,
            upstream_path=f"/v1/agent/artifacts/{artifact_id}/revisions",
        )

    @app.get("/v1/agent/artifacts/{artifact_id}")
    async def get_artifact(request: Request, artifact_id: str) -> Response:
        return await ArtifactProxy.forward(
            app=app,
            request=request,
            upstream_path=f"/v1/agent/artifacts/{artifact_id}",
        )

    @app.delete("/v1/agent/artifacts/{artifact_id}")
    async def delete_artifact(request: Request, artifact_id: str) -> Response:
        return await ArtifactProxy.forward(
            app=app,
            request=request,
            upstream_path=f"/v1/agent/artifacts/{artifact_id}",
        )


__all__ = ("ArtifactProxy", "register_artifact_proxy_routes")
