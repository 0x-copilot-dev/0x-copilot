"""Facade routes for the tier-2 adapter registry (Phase 7A).

Thin proxy onto ``backend``'s ``/internal/v1/adapter_registry/*``.
Tenant identity is always derived from the verified bearer; the
backend overrides any caller-supplied ``org_id`` again on its side, so
malicious clients cannot smuggle a different tenant via query string.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status

from backend_facade.auth import FacadeAuthenticator


def register_adapter_registry_routes(app: FastAPI) -> None:
    """Attach app-facing adapter-registry routes."""

    from backend_facade.app import forward_json

    @app.post("/v1/adapter_registry/candidates", status_code=status.HTTP_201_CREATED)
    async def submit_candidate(
        request: Request, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        response = await forward_json(
            app,
            "POST",
            "/internal/v1/adapter_registry/candidates",
            target="backend",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )
        return _as_object(response)

    @app.get("/v1/adapter_registry/promoted")
    async def list_promoted(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        response = await forward_json(
            app,
            "GET",
            "/internal/v1/adapter_registry/promoted",
            target="backend",
            params=identity.scoped_params(),
            identity=identity,
        )
        return _as_object(response)

    @app.get("/v1/adapter_registry/opt-out")
    async def get_opt_out(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        response = await forward_json(
            app,
            "GET",
            "/internal/v1/adapter_registry/opt-out",
            target="backend",
            params=identity.scoped_params(),
            identity=identity,
        )
        return _as_object(response)

    @app.put("/v1/adapter_registry/opt-out")
    async def put_opt_out(
        request: Request, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        response = await forward_json(
            app,
            "PUT",
            "/internal/v1/adapter_registry/opt-out",
            target="backend",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )
        return _as_object(response)

    @app.get("/v1/admin/adapter_registry/candidates")
    async def admin_list_candidates(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        params = dict(identity.scoped_params())
        forwarded_status = request.query_params.get("status")
        if forwarded_status:
            params["status"] = forwarded_status
        forwarded_limit = request.query_params.get("limit")
        if forwarded_limit:
            params["limit"] = forwarded_limit
        response = await forward_json(
            app,
            "GET",
            "/internal/v1/adapter_registry/candidates",
            target="backend",
            params=params,
            identity=identity,
        )
        return _as_object(response)

    @app.get("/v1/admin/adapter_registry/candidates/{candidate_id}")
    async def admin_get_candidate(
        request: Request, candidate_id: str
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        response = await forward_json(
            app,
            "GET",
            f"/internal/v1/adapter_registry/candidates/{candidate_id}",
            target="backend",
            params=identity.scoped_params(),
            identity=identity,
        )
        return _as_object(response)

    @app.post("/v1/admin/adapter_registry/candidates/{candidate_id}/decisions")
    async def admin_decide_candidate(
        request: Request,
        candidate_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        response = await forward_json(
            app,
            "POST",
            f"/internal/v1/adapter_registry/candidates/{candidate_id}/decisions",
            target="backend",
            params=identity.scoped_params(),
            json=payload,
            identity=identity,
        )
        return _as_object(response)


def _as_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    return {"result": value}


__all__ = ["register_adapter_registry_routes"]
