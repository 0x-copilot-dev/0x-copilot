"""Health and readiness endpoints for the runtime API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Response, status


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str | None = None


Checker = Callable[[], CheckResult]


def register_health_routes(
    app: FastAPI,
    *,
    readiness_checkers: list[Checker] | None = None,
) -> None:
    """Attach ``/healthz`` and ``/readyz`` to the runtime API app.

    Liveness (``/healthz``) is unconditional 200; readiness (``/readyz``) runs
    every registered checker and returns 503 if any reports ``ok=False``. The
    runtime API can pass checkers that probe the runtime store, the queue,
    and the OTEL collector once those wirings exist.
    """

    checkers: list[Checker] = list(readiness_checkers or [])

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, Any]:
        return {"status": "alive"}

    @app.get("/readyz", include_in_schema=False)
    def readyz(response: Response) -> dict[str, Any]:
        results = [checker() for checker in checkers]
        ok = all(result.ok for result in results)
        if not ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ready" if ok else "not_ready",
            "checks": [
                {"name": result.name, "ok": result.ok, "detail": result.detail}
                for result in results
            ],
        }
