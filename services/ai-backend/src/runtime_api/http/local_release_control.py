"""Authenticated loopback ingress for development harness release control."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.harness_quality.evaluation_contracts import (
    HarnessManifest,
    HarnessManifestPointer,
)
from agent_runtime.release.control import (
    LocalReleaseControlService,
    ReleaseActivationError,
)
from agent_runtime.release.local_control import ReleaseControlError
from agent_runtime.release.manifest import ReleaseManifestVerificationError
from runtime_api.auth import RuntimeServiceAuthenticator


class LocalReleaseVerifyResponse(RuntimeContract):
    manifest_ref: str
    verification_digest: str


class LocalReleaseInstallRequest(RuntimeContract):
    manifest: HarnessManifest
    activation_decision_id: str


class LocalReleaseRollbackRequest(RuntimeContract):
    target_manifest_id: str
    target_manifest_revision: str
    activation_decision_id: str
    rationale: str


class LocalReleaseControlRouter:
    """Routes mounted only when explicit development/dogfood composition succeeds."""

    _PREFIX = "/internal/dev/evaluation/releases"

    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(prefix=cls._PREFIX, tags=["local-release-control"])
        router.add_api_route(
            "/verify",
            cls.verify,
            methods=["POST"],
            response_model=LocalReleaseVerifyResponse,
        )
        router.add_api_route(
            "/install",
            cls.install,
            methods=["POST"],
            response_model=HarnessManifestPointer,
        )
        router.add_api_route(
            "/rollback",
            cls.rollback,
            methods=["POST"],
            response_model=HarnessManifestPointer,
        )
        router.add_api_route(
            "/export",
            cls.export,
            methods=["POST"],
        )
        return router

    @classmethod
    async def verify(
        cls,
        request: Request,
        manifest: HarnessManifest,
    ) -> LocalReleaseVerifyResponse:
        service, peer_host = cls._authorized_service(request)
        try:
            verified = service.verify(manifest=manifest, peer_host=peer_host)
        except (ReleaseControlError, ReleaseManifestVerificationError) as exc:
            raise cls._rejected(exc) from exc
        return LocalReleaseVerifyResponse(
            manifest_ref=verified.manifest.manifest_ref,
            verification_digest=verified.verification_digest,
        )

    @classmethod
    async def install(
        cls,
        request: Request,
        response: Response,
        payload: LocalReleaseInstallRequest,
    ) -> HarnessManifestPointer:
        service, peer_host = cls._authorized_service(request)
        try:
            pointer = await service.install(
                manifest=payload.manifest,
                activation_decision_id=payload.activation_decision_id,
                peer_host=peer_host,
            )
            response.headers["x-runtime-restart-required"] = "true"
            return pointer
        except (
            ReleaseActivationError,
            ReleaseControlError,
            ReleaseManifestVerificationError,
        ) as exc:
            raise cls._rejected(exc) from exc

    @classmethod
    async def rollback(
        cls,
        request: Request,
        response: Response,
        payload: LocalReleaseRollbackRequest,
    ) -> HarnessManifestPointer:
        service, peer_host = cls._authorized_service(request)
        try:
            pointer = await service.rollback(
                target_manifest_id=payload.target_manifest_id,
                target_manifest_revision=payload.target_manifest_revision,
                activation_decision_id=payload.activation_decision_id,
                rationale=payload.rationale,
                peer_host=peer_host,
            )
            response.headers["x-runtime-restart-required"] = "true"
            return pointer
        except (
            ReleaseActivationError,
            ReleaseControlError,
            ReleaseManifestVerificationError,
        ) as exc:
            raise cls._rejected(exc) from exc

    @classmethod
    async def export(
        cls,
        request: Request,
    ) -> Response:
        service, peer_host = cls._authorized_service(request)
        try:
            exported = await service.export(
                output_path="http-response",
                peer_host=peer_host,
            )
        except (ReleaseActivationError, ReleaseControlError) as exc:
            raise cls._rejected(exc) from exc
        return Response(
            content=exported.payload,
            media_type="application/vnd.0xcopilot.evaluation-export+json",
            headers={"x-content-sha256": exported.payload_digest},
        )

    @staticmethod
    def _authorized_service(
        request: Request,
    ) -> tuple[LocalReleaseControlService, str]:
        RuntimeServiceAuthenticator.require_configured_service_token(request)
        service = getattr(request.app.state, "local_release_control_service", None)
        if not isinstance(service, LocalReleaseControlService):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "Local release control is unavailable.",
            )
        peer = request.client
        return service, "" if peer is None else peer.host

    @staticmethod
    def _rejected(exc: Exception) -> HTTPException:
        if isinstance(exc, ReleaseControlError):
            return HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Local release control request is forbidden.",
            )
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Local release control request is invalid.",
        )


__all__ = (
    "LocalReleaseControlRouter",
    "LocalReleaseInstallRequest",
    "LocalReleaseRollbackRequest",
    "LocalReleaseVerifyResponse",
)
