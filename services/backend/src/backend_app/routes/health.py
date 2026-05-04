"""Health and readiness endpoints for the backend service.

``/healthz`` is a liveness probe: returns 200 as soon as the process can
serve a request. It does NOT check downstreams -- a flaky database must not
flap the pod's liveness, only its readiness.

``/readyz`` is a readiness probe: returns 200 only when the service can
actually serve traffic. The default implementation reports OK; callers can
register additional ``Checker`` callables that return ``(name, ok, detail)``
tuples (database connection, OTEL collector reachability, etc.) as those
dependencies are wired in.

Both endpoints are excluded from OTEL's auto-instrumentation (configured in
``observability/otel.py``) so polling probes don't flood the trace store.

This module is a register-on-demand route registry, not a side-effect import.
Wire it into the FastAPI app with ``register_health_routes(app)``.
"""

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


# A readiness checker returns the result of one dependency probe. Sync only:
# liveness/readiness must complete in <100ms typically.
Checker = Callable[[], CheckResult]


def register_health_routes(
    app: FastAPI,
    *,
    readiness_checkers: list[Checker] | None = None,
) -> None:
    """Attach ``/healthz`` and ``/readyz`` to the FastAPI app.

    ``readiness_checkers`` is a list of callables; if any returns ``ok=False``
    the endpoint returns 503 with a JSON body listing failures. Empty list
    means the service self-reports ready as long as the process is up.
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
