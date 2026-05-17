"""Public ``/v1/audit`` route — admin-facing audit log query (PR 7.1).

Browser-facing read for the Settings → Members → "Audit log" table.
Identity is established by the existing bearer-verify path, then
forwarded as a service-token request to the two internal audit-list
endpoints — backend's (4 streams: mcp/skill/identity/deploy) and
ai-backend's (1 stream: runtime_audit_log) — with results merged by
``created_at DESC`` into a single page.

Each upstream owns its own scope check (``admin:audit_export``); the
facade does not duplicate it — a defence-in-depth gate would race with
role updates and surface as spurious 403s. Trust the backends.

Cursor encoding is composite: the public cursor wraps both upstream
cursors so a single ``?cursor=`` round-trips back to the right slice
on each side. ``base64(json({"backend": ..., "ai": ...}))``.

Wire into the FastAPI app with ``register_audit_routes(app)``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


# Filters the FE may legitimately pass through. We allowlist (rather
# than copy the request's full query string) so a client mistake of
# e.g. ``?org_id=other`` cannot smuggle a tenant argument we'd
# otherwise replace; the facade always overrides ``org_id`` +
# ``user_id`` from the verified identity.
_FORWARDED_PARAMS: tuple[str, ...] = (
    "action",
    "actor_user_id",
    "resource_type",
    "since",
    "until",
    "limit",
)


def _decode_composite_cursor(raw: str | None) -> tuple[str | None, str | None]:
    """Return ``(backend_cursor, ai_cursor)`` from the public cursor."""

    if raw is None or raw.strip() == "":
        return None, None
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "invalid_cursor") from exc
    backend_cursor = payload.get("backend")
    ai_cursor = payload.get("ai")
    return (
        backend_cursor if isinstance(backend_cursor, str) else None,
        ai_cursor if isinstance(ai_cursor, str) else None,
    )


def _encode_composite_cursor(
    backend_cursor: str | None, ai_cursor: str | None
) -> str | None:
    if backend_cursor is None and ai_cursor is None:
        return None
    payload = {"backend": backend_cursor, "ai": ai_cursor}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


async def _call_upstream(
    client: httpx.AsyncClient,
    *,
    url: str,
    params: dict[str, str],
    headers: dict[str, str],
) -> httpx.Response | None:
    """Call one upstream; return ``None`` on transport failure (degrade)."""

    try:
        return await client.get(url, params=params, headers=headers)
    except httpx.HTTPError:
        return None


def register_audit_routes(app: FastAPI) -> None:
    @app.get("/v1/audit")
    async def list_audit(request: Request) -> dict[str, object]:
        settings = settings_for(app)
        backend_url = settings.backend_url
        ai_backend_url = settings.ai_backend_url
        client = http_client(request.app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        backend_cursor, ai_cursor = _decode_composite_cursor(
            request.query_params.get("cursor")
        )
        shared: dict[str, str] = {
            "org_id": identity.org_id,
            "user_id": identity.user_id,
        }
        for key in _FORWARDED_PARAMS:
            value = request.query_params.get(key)
            if value is not None and value != "":
                shared[key] = value
        backend_params = dict(shared)
        if backend_cursor is not None:
            backend_params["cursor"] = backend_cursor
        ai_params = dict(shared)
        if ai_cursor is not None:
            ai_params["cursor"] = ai_cursor
        headers = FacadeAuthenticator.service_headers(identity)

        backend_resp, ai_resp = await asyncio.gather(
            _call_upstream(
                client,
                url=f"{backend_url}/internal/v1/audit/list",
                params=backend_params,
                headers=headers,
            ),
            _call_upstream(
                client,
                url=f"{ai_backend_url}/internal/v1/audit/list",
                params=ai_params,
                headers=headers,
            ),
        )

        # If backend is unauthorized/forbidden, surface its status; that's
        # the canonical "you're not allowed to read audit" answer.
        if backend_resp is not None and backend_resp.status_code in (401, 403):
            _raise_for_upstream(backend_resp)
        if backend_resp is not None and backend_resp.status_code >= 400:
            _raise_for_upstream(backend_resp)

        backend_body: dict[str, Any] = (
            backend_resp.json() if backend_resp is not None else {"rows": []}
        )
        degraded: list[str] = list(backend_body.get("degraded_streams") or [])
        if backend_resp is None:
            degraded.extend(
                [
                    "mcp_audit_events",
                    "skill_audit_events",
                    "identity_audit_events",
                    "deploy_audit_events",
                ]
            )

        ai_rows: list[dict[str, Any]] = []
        ai_next: str | None = None
        if ai_resp is not None and ai_resp.status_code < 400:
            ai_body = ai_resp.json()
            ai_rows = list(ai_body.get("rows") or [])
            ai_next = ai_body.get("next_cursor")
        elif ai_resp is None or ai_resp.status_code >= 500:
            degraded.append("runtime_audit_log")

        merged = sorted(
            (*backend_body.get("rows", []), *ai_rows),
            key=lambda row: (
                str(row.get("created_at") or ""),
                str(row.get("stream") or ""),
                int(row.get("seq") or 0),
            ),
            reverse=True,
        )
        # Limit defaults to 50; respect the caller's value (allowlisted
        # in shared above) — the upstreams already enforce the cap.
        try:
            limit = int(shared.get("limit", "50"))
        except ValueError:
            limit = 50
        page = merged[:limit]
        next_cursor = _encode_composite_cursor(backend_body.get("next_cursor"), ai_next)
        return {
            "rows": page,
            "next_cursor": next_cursor if len(page) == limit else None,
            "has_more": len(page) == limit,
            "degraded_streams": tuple(degraded),
        }


def _raise_for_upstream(response: httpx.Response) -> None:
    """Turn an upstream error into a faithful HTTPException.

    Matches the pattern used by ``me_routes.py`` — same private copy to
    avoid a circular import for one helper.
    """

    if response.status_code < 400:
        return
    detail: Any
    try:
        body = response.json()
    except ValueError:
        detail = response.text or "Upstream error"
    else:
        detail = body.get("detail") if isinstance(body, dict) else body
    raise HTTPException(response.status_code, detail or "Upstream error")


def settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


__all__ = ["register_audit_routes"]
