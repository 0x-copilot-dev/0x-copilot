"""Electron-main-only proxy for signed C2 workspace capability attestations."""

from __future__ import annotations

import httpx
from copilot_service_contracts.headers import SERVICE_TOKEN_HEADER
from fastapi import FastAPI, HTTPException, Request, Response, status

from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


_ATTESTATION_PATH = "/v1/agent/desktop-workspace-attestation"
_MAX_BODY_BYTES = 8 * 1024


def register_desktop_attestation_routes(app: FastAPI) -> None:
    """Mount the narrow main-process bridge without creating a facade deputy."""

    @app.post(_ATTESTATION_PATH, status_code=status.HTTP_204_NO_CONTENT)
    async def submit_desktop_workspace_attestation(request: Request) -> Response:
        # A renderer/browser has neither this per-install host secret nor the
        # Ed25519 private key. Reject a missing bearer locally so it never
        # reaches ai-backend; an invalid bearer is faithfully rejected there.
        host_token = request.headers.get(SERVICE_TOKEN_HEADER, "").strip()
        if not host_token:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Missing desktop host token",
            )
        declared_length = request.headers.get("content-length")
        if declared_length is not None:
            try:
                if int(declared_length) > _MAX_BODY_BYTES:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        "Desktop workspace attestation is too large",
                    )
            except ValueError:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Invalid content length",
                ) from None
        body = await request.body()
        if len(body) > _MAX_BODY_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "Desktop workspace attestation is too large",
            )
        try:
            upstream = await http_client(app).post(
                f"{settings_for(app).ai_backend_url}{_ATTESTATION_PATH}",
                content=body,
                headers={
                    # Forward the caller's host token verbatim. The facade
                    # must not attach its own service secret, which would let
                    # any browser request turn it into a signing deputy.
                    SERVICE_TOKEN_HEADER: host_token,
                    "content-type": request.headers.get(
                        "content-type", "application/json"
                    ),
                },
                timeout=10,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Desktop workspace attestation service is unavailable",
            ) from exc
        content_type = upstream.headers.get("content-type")
        headers = {"content-type": content_type} if content_type else {}
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=headers,
        )


def settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


__all__ = ("register_desktop_attestation_routes",)
