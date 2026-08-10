"""A loopback MCP server for the end-to-end surface journey.

This is a TEST DOUBLE AT THE NETWORK BOUNDARY and nothing more. It exists
because every connector in the desktop profile needs an OAuth authorization that
an automated journey must not complete in the user's name, and without SOME
connected MCP server the PRESENT stage never fires, so no surface is ever
created and the floor cannot be observed end to end.

Everything inside the app stays real: the real MCP client dials this over
loopback HTTP, the real `McpPresentMiddleware` sees the result, the real
`SurfaceProjector` climbs the real ladder, the real `WorkLedgerEmitter` writes
the real ledger, the real hydration endpoint serves it, and the real renderers
draw it. Only the vendor on the far end is substituted.

The two tools are chosen to exercise the two rungs the floor is about:

* ``list_incidents`` — a payload no curated spec covers, so it must fall to rung
  0 (deterministic inference) and still render a legible table. It is
  deliberately NOT Linear-shaped, so it cannot accidentally satisfy the shape
  matcher and report a curated hit.
* ``get_incident`` — the same connector's single-object read, which must infer a
  record rather than a table.

Both are annotated ``readOnlyHint`` so the policy layer classifies them READ and
the PRESENT stage runs (a write parks on an approval gate instead).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("incidents-fixture", host="127.0.0.1", port=8931)

_INCIDENTS: list[dict[str, Any]] = [
    {
        "number": "#4471",
        "title": "Facade 5xx above threshold",
        "status": "triggered",
        "urgency": "high",
        "assignee": {"displayName": "Arun M."},
        "service": {"id": "svc-9", "name": "backend-facade"},
        "created_at": "2026-08-04T11:46:00Z",
    },
    {
        "number": "#4468",
        "title": "Worker queue depth > 500",
        "status": "acknowledged",
        "urgency": "medium",
        "assignee": {"displayName": "Riya S."},
        "service": {"id": "svc-3", "name": "runtime-worker"},
        "created_at": "2026-08-04T10:12:00Z",
    },
    {
        "number": "#4460",
        "title": "Postgres connection saturation",
        "status": "resolved",
        "urgency": "low",
        "assignee": {"displayName": "Parth P."},
        "service": {"id": "svc-1", "name": "postgres"},
        "created_at": "2026-08-04T09:02:00Z",
    },
]


@mcp.tool(
    description="List open incidents. Read-only.",
    annotations={"readOnlyHint": True, "title": "List incidents"},
)
def list_incidents() -> dict[str, Any]:
    """Return the incident list exactly as a real connector would shape it."""

    return {"incidents": _INCIDENTS}


@mcp.tool(
    description="Get one incident by its number. Read-only.",
    annotations={"readOnlyHint": True, "title": "Get incident"},
)
def get_incident(number: str) -> dict[str, Any]:
    """Return a single incident object — the record case for the floor."""

    for incident in _INCIDENTS:
        if incident["number"].lstrip("#") == number.lstrip("#"):
            return incident
    return dict(_INCIDENTS[0])


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
