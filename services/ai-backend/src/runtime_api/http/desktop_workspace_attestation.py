"""Service-token-gated ingress for Electron's signed C2 capability statement."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from agent_runtime.capabilities.desktop.workspace_attestation import (
    DesktopWorkspaceAttestationEnvelope,
    DesktopWorkspaceAttestationRegistry,
)
from runtime_api.auth import RuntimeServiceAuthenticator


class DesktopWorkspaceAttestationRouter:
    """Private-to-Electron-main facade ingress; never renderer/bearer auth."""

    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(tags=["desktop-capability"])
        router.add_api_route(
            "/v1/agent/desktop-workspace-attestation",
            cls.submit,
            methods=["POST"],
            status_code=status.HTTP_204_NO_CONTENT,
            name="submit_desktop_workspace_attestation",
        )
        return router

    @staticmethod
    async def submit(
        request: Request, payload: DesktopWorkspaceAttestationEnvelope
    ) -> Response:
        """Verify an Electron-main signature and replace only a valid current fact."""

        # The facade forwards the caller's host token verbatim rather than
        # becoming a confused deputy. The Ed25519 proof is checked separately
        # by the registry, so service-token possession alone never enables C2.
        RuntimeServiceAuthenticator.require_service_token(request)
        registry = getattr(
            request.app.state, "desktop_workspace_attestation_registry", None
        )
        if not isinstance(registry, DesktopWorkspaceAttestationRegistry):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Desktop workspace attestation is unavailable.",
            )
        if not registry.submit(payload):
            # Do not distinguish malformed, expired, unconfigured, or
            # signature-invalid input. None of those states may become a
            # launch proof and the response must not be an oracle.
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Invalid desktop workspace attestation.",
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ("DesktopWorkspaceAttestationRouter",)
