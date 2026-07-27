"""Authenticated loopback endpoint for content-free local F1 diagnostics."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from agent_runtime.api.loopback import (
    LoopbackAuthorizationError,
    require_literal_loopback,
)
from agent_runtime.harness_quality.diagnostics import (
    EvaluationDiagnosticSnapshot,
    EvaluationDiagnosticsService,
)
from runtime_api.auth import RuntimeServiceAuthenticator


class LocalEvaluationDiagnosticsRouter:
    """Mount only when explicit development/dogfood local controls are enabled."""

    _PREFIX = "/internal/dev/evaluation/diagnostics"

    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(prefix=cls._PREFIX, tags=["local-evaluation-diagnostics"])
        router.add_api_route(
            "/snapshot",
            cls.snapshot,
            methods=["GET"],
            response_model=EvaluationDiagnosticSnapshot,
        )
        return router

    @staticmethod
    async def snapshot(request: Request) -> EvaluationDiagnosticSnapshot:
        RuntimeServiceAuthenticator.require_configured_service_token(request)
        service = getattr(
            request.app.state,
            "local_evaluation_diagnostics_service",
            None,
        )
        if not isinstance(service, EvaluationDiagnosticsService):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "Local evaluation diagnostics are unavailable.",
            )
        peer = request.client
        try:
            require_literal_loopback("" if peer is None else peer.host)
        except LoopbackAuthorizationError as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Local evaluation diagnostics request is forbidden.",
            ) from exc
        return await service.snapshot()


__all__ = ("LocalEvaluationDiagnosticsRouter",)
