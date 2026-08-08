"""A loopback MCP server for the end-to-end surface journey.

This is a TEST DOUBLE AT THE NETWORK BOUNDARY and nothing more. It exists
because every connector in the desktop profile needs an OAuth authorization that
an automated journey must not complete in the user's name, and without SOME
connected MCP server the PRESENT stage never fires, so no surface is ever
created and the floor cannot be observed end to end.

Everything inside the app stays real: the real MCP client dials this over
loopback HTTP, the real ``McpPresentMiddleware`` sees the result, the real
``SurfaceProjector`` climbs the real ladder, the real ``WorkLedgerEmitter``
writes the real ledger, the real hydration endpoint serves it, and the real
renderers draw it. Only the vendor on the far end is substituted.

**Why this file was rewritten.** The previous version had two tools and both
returned a Python ``dict`` with the default ``structured_output``, which makes
FastMCP derive an output schema and send ``structuredContent`` — the one wire
form that arrives at the runtime already structured. So the journey passed
10/10 against a payload shape that effectively no real MCP server produces,
while every real connector rendered an empty table
(``docs/plan/generative-ui-floor/NORMALISATION-DESIGN.md``). A fixture that
proves a path nobody takes is worse than no fixture: it converts a blind spot
into a green check.

**The instrument.** One dataset — the same three incidents — served in eight
envelopes, one per tool. Nothing about the *data* varies between them; only the
shape of the wire response does. So a difference in what renders is
attributable to the envelope and to nothing else.

===  ==================================  =======================================
#    tool                                wire form
===  ==================================  =======================================
1    ``list_incidents``                  one text block holding a JSON **object**
                                         (Linear: ``{"issues": [...]}``)
2    ``get_incident``                    one text block holding a **deeply
                                         nested** JSON object (Figma-ish)
3    ``list_incidents_structured``       ``structuredContent`` + a text block
                                         (the well-behaved minority)
4    ``list_incidents_array``            one text block holding a JSON **array**
                                         at the root (Atlassian)
5    ``summarize_incidents_prose``       one text block of **prose**, no JSON
                                         (Slack)
6    ``list_incidents_markdown``         one text block holding a **markdown
                                         table**
7    ``export_incidents_csv``            one text block holding **CSV**
8    ``incident_briefing``               **three** text blocks in one response
===  ==================================  =======================================

**How the wire form is controlled.** ``structured_output`` is not cosmetic here,
it is the whole point. FastMCP auto-derives an output schema from the return
annotation (``func_metadata``), and any tool that has one sends
``structuredContent``; ``langchain-mcp-adapters`` then builds an
``MCPToolArtifact`` from that field and the runtime reads the structured half.
That is shape 3 — and it is what the old fixture was, accidentally, for both of
its tools. Every other tool here passes ``structured_output=False`` and returns
``TextContent`` verbatim, so what leaves this process is byte-for-byte the
content-block form a real server sends:

.. code-block:: json

    {"content": [{"type": "text", "text": "{\\"incidents\\": [...]}"}]}

Returning a Python object instead would let FastMCP re-serialise it, and
``_convert_to_content`` flattens a ``list`` into one content block **per
element** — which silently turns shape 4 into shape 8. Hence the explicit
``TextContent``.

Every tool is annotated ``readOnlyHint`` so the policy layer classifies it READ
and the PRESENT stage runs (a write parks on an approval gate instead).
"""

from __future__ import annotations

import json
import os
from typing import Any, Final

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

#: Default port, shared with the journey (``artifacts_and_surfaces.py``
#: phase AS-9) through the same env var.
DEFAULT_PORT: Final[int] = 8931

#: Overridable because a *stale* fixture is the exact failure this whole file
#: exists to prevent. A previous session's server left listening on 8931 answers
#: the journey's registration happily and serves its OWN, older tool list — the
#: journey then measures a fixture nobody edited and reports it as today's
#: result. This was hit while building the eight shapes: the probe connected,
#: initialised, and returned "Unknown tool" for every tool added that hour.
#: AS-9 additionally asserts the connected server advertises all eight tools,
#: which is the check that makes a stale server loud instead of confusing.
PORT: Final[int] = int(os.environ.get("SURFACE_FIXTURE_PORT", str(DEFAULT_PORT)))

#: The server name reaches the surface header, the ledger's ``source.server``
#: and the canvas tab label, so it is pinned even when the port is not.
mcp = FastMCP("incidents-fixture", host="127.0.0.1", port=PORT)

#: Read-only, and stated on every tool. The PRESENT stage only runs for a tool
#: the policy layer classified READ; a write parks on an approval gate and is
#: presented by the approval path instead, so a fixture tool that forgot this
#: would silently measure nothing.
_READ_ONLY: Final[dict[str, Any]] = {"readOnlyHint": True}

#: THE dataset. Every shape below serves exactly these three rows, so any
#: difference in what renders is caused by the envelope alone.
_INCIDENTS: Final[list[dict[str, Any]]] = [
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

#: Column order for the derived text formats (markdown, CSV). Fixed rather than
#: read off the first row so shapes 6 and 7 present the same columns in the same
#: order and can be compared to shape 1 directly.
_COLUMNS: Final[tuple[str, ...]] = ("number", "title", "status", "urgency")


class _Envelope:
    """Builders for the wire forms. One place decides how a shape is spelled."""

    @staticmethod
    def text(payload: str) -> TextContent:
        """One MCP text content block carrying ``payload`` verbatim."""

        return TextContent(type="text", text=payload)

    @classmethod
    def json_text(cls, document: object) -> TextContent:
        """One text block holding ``document`` serialised as JSON.

        ``separators`` is compact on purpose: a real connector does not
        pretty-print, and whitespace would be the only thing distinguishing this
        from a hand-made fixture.
        """

        return cls.text(json.dumps(document, separators=(",", ":")))

    @staticmethod
    def cell(row: dict[str, Any], key: str) -> str:
        """A row's value for ``key`` flattened to a display string.

        The nested mappings (``assignee``, ``service``) are what make the JSON
        shapes interesting and what the text shapes cannot carry — flattening is
        the fidelity a markdown table or a CSV genuinely loses.
        """

        value = row.get(key)
        if isinstance(value, dict):
            return str(value.get("displayName") or value.get("name") or "")
        return "" if value is None else str(value)


@mcp.tool(
    description=(
        "List open incidents as a JSON object in a text block. Read-only. "
        "This is the shape Linear returns."
    ),
    annotations={**_READ_ONLY, "title": "List incidents"},
    structured_output=False,
)
def list_incidents() -> TextContent:
    """SHAPE 1 — a JSON object inside a single text block.

    Byte-identical in form to a verified real Linear response::

        {"result": [{"type": "text",
                     "text": "{\\"issues\\": [...], \\"hasNextPage\\": false}"}]}

    The rows are right there, as a JSON *string*, and until
    ``EnvelopeUnwrapper._decode_content_once`` landed nothing parsed it: a
    curated ``items_path: "issues"`` was correct and still bound zero rows,
    because ``issues`` is a key of the document *inside* ``text``, not of the
    envelope around it. This is the overwhelming majority of real servers, and
    the reason this fixture was rewritten.
    """

    return _Envelope.json_text({"incidents": _INCIDENTS, "has_next_page": False})


@mcp.tool(
    description="Get one incident, as a deeply nested JSON document. Read-only.",
    annotations={**_READ_ONLY, "title": "Get incident"},
    structured_output=False,
)
def get_incident(number: str) -> TextContent:
    """SHAPE 2 — a deeply nested document in a single text block.

    The record case, and the one shape that also proves arguments cross the
    wire. Deliberately nested three levels (``service.owner.team.name``) the way
    a Figma or Notion read is, so the record renderer has to bind through
    mappings rather than off a flat row. No array of mappings anywhere near the
    root: array-at-root is shape 4's job, and mixing the two would make a
    finding un-attributable.
    """

    wanted = number.lstrip("#")
    incident = next(
        (row for row in _INCIDENTS if row["number"].lstrip("#") == wanted),
        _INCIDENTS[0],
    )
    return _Envelope.json_text(
        {
            "incident": {
                **incident,
                "assignee": {
                    "displayName": incident["assignee"]["displayName"],
                    "email": "on-call@example.invalid",
                },
                "service": {
                    **incident["service"],
                    "owner": {"team": {"name": "Platform", "slug": "platform"}},
                },
                "timeline": {
                    "created_at": incident["created_at"],
                    "acknowledged_at": None,
                    "resolved_at": None,
                },
                "labels": ["sev2", "customer-visible"],
            }
        }
    )


@mcp.tool(
    description=(
        "List open incidents with MCP structuredContent. Read-only. "
        "This is the well-behaved minority of servers."
    ),
    annotations={**_READ_ONLY, "title": "List incidents (structured)"},
)
def list_incidents_structured() -> dict[str, Any]:
    """SHAPE 3 — a server that sends ``structuredContent``.

    ``structured_output`` is left at its default so FastMCP derives an output
    schema from this annotation and populates ``structuredContent``;
    ``langchain-mcp-adapters`` then hands the runtime an ``MCPToolArtifact`` and
    the payload arrives already structured, under a ``structured_content``
    wrapper key that ``EnvelopeUnwrapper`` peels.

    **This — and only this — is what the old fixture measured.** It is kept
    because it is a real shape and must not regress, not because it is
    representative.
    """

    return {"incidents": _INCIDENTS}


@mcp.tool(
    description="List open incidents as a bare JSON array in a text block. Read-only.",
    annotations={**_READ_ONLY, "title": "List incidents (array)"},
    structured_output=False,
)
def list_incidents_array() -> TextContent:
    """SHAPE 4 — a JSON **array** at the root of the text block.

    Atlassian's shape. There is no object to hang a ``items_path`` off: the
    document *is* the collection. ``SurfaceSpecInferrer.infer`` returns ``None``
    for anything that is not a ``Mapping``, and ``EnvelopeUnwrapper`` decodes
    this to a ``list``, so the inferrer falls back to reading the *envelope*
    instead of the payload it wraps.
    """

    return _Envelope.json_text(_INCIDENTS)


@mcp.tool(
    description="Summarise the open incidents in prose. Read-only.",
    annotations={**_READ_ONLY, "title": "Summarise incidents"},
    structured_output=False,
)
def summarize_incidents_prose() -> TextContent:
    """SHAPE 5 — prose, carrying no machine-readable structure at all.

    Slack's shape, and the honest floor of the problem: there is no path into
    this payload to bind, so no amount of deterministic parsing yields a spec.
    It is the case the model tier exists for, and the case whose *degradation*
    must stay legible — a surface that cannot be shaped must not become an empty
    table promising rows it does not have.
    """

    lines = [
        f"{_Envelope.cell(row, 'title')} ({row['number']}) is {row['status']} "
        f"at {row['urgency']} urgency, owned by "
        f"{_Envelope.cell(row, 'assignee')} on {_Envelope.cell(row, 'service')}."
        for row in _INCIDENTS
    ]
    return _Envelope.text(
        "There are three open incidents this morning. " + " ".join(lines)
    )


@mcp.tool(
    description="List open incidents as a markdown table. Read-only.",
    annotations={**_READ_ONLY, "title": "List incidents (markdown)"},
    structured_output=False,
)
def list_incidents_markdown() -> TextContent:
    """SHAPE 6 — a markdown table inside the text block.

    Structure a human can read and no dot-path can address. Common from servers
    that were written to be pasted into a chat window rather than parsed, and
    the shape most likely to be mistaken for "already fine" in a screenshot.
    """

    header = "| " + " | ".join(column.title() for column in _COLUMNS) + " |"
    divider = "| " + " | ".join("---" for _ in _COLUMNS) + " |"
    body = [
        "| " + " | ".join(_Envelope.cell(row, column) for column in _COLUMNS) + " |"
        for row in _INCIDENTS
    ]
    return _Envelope.text("\n".join([header, divider, *body]))


@mcp.tool(
    description="Export open incidents as CSV. Read-only.",
    annotations={**_READ_ONLY, "title": "Export incidents (CSV)"},
    structured_output=False,
)
def export_incidents_csv() -> TextContent:
    """SHAPE 7 — CSV inside the text block.

    Fully structured, addressable by nobody: ``json.loads`` refuses it, so
    ``EnvelopeUnwrapper._decode_block`` falls back to the raw string and the
    payload reaches the ladder as text. Quoting is deliberately naive because
    the values are chosen to need none — a quoting bug here would be a finding
    about this fixture, not about the pipeline.
    """

    header = ",".join(_COLUMNS)
    body = [
        ",".join(_Envelope.cell(row, column) for column in _COLUMNS)
        for row in _INCIDENTS
    ]
    return _Envelope.text("\n".join([header, *body]))


@mcp.tool(
    description="Return an incident briefing as several content blocks. Read-only.",
    annotations={**_READ_ONLY, "title": "Incident briefing"},
    structured_output=False,
)
def incident_briefing() -> list[TextContent]:
    """SHAPE 8 — three content blocks in one response.

    A single response whose ``content`` list has more than one element.
    ``EnvelopeUnwrapper._decode_content_once`` decodes each block and returns a
    **list** of the decoded values when there is more than one — so even when
    one block is perfectly good JSON, the payload as a whole stops being a
    ``Mapping`` and the same non-mapping wall as shape 4 applies.

    The mix is deliberate and realistic: a prose preamble, the data, and a
    rendered table of the same data. A server that returns three text blocks
    almost never returns three of the same kind.
    """

    return [
        _Envelope.text(f"{len(_INCIDENTS)} incidents are open."),
        _Envelope.json_text({"incidents": _INCIDENTS}),
        _Envelope.text(list_incidents_markdown().text),
    ]


#: Bumped whenever a shape is added, removed or re-spelled. AS-9 reads it off
#: the RUNNING server and refuses to measure a revision it does not recognise,
#: which is the difference between "the pipeline regressed" and "you are talking
#: to last week's fixture".
REVISION: Final[str] = "eight-shapes.1"

#: The shape table, served over HTTP so the journey can verify it is talking to
#: THIS fixture. Sequence order is the matrix order.
SHAPES: Final[tuple[dict[str, Any], ...]] = (
    {"shape": 1, "tool": "list_incidents", "wire": "json object in a text block"},
    {
        "shape": 2,
        "tool": "get_incident",
        "wire": "nested json document in a text block",
    },
    {"shape": 3, "tool": "list_incidents_structured", "wire": "structuredContent"},
    {"shape": 4, "tool": "list_incidents_array", "wire": "json array at root"},
    {"shape": 5, "tool": "summarize_incidents_prose", "wire": "prose"},
    {"shape": 6, "tool": "list_incidents_markdown", "wire": "markdown table"},
    {"shape": 7, "tool": "export_incidents_csv", "wire": "csv"},
    {"shape": 8, "tool": "incident_briefing", "wire": "three content blocks"},
)


@mcp.custom_route("/shapes", methods=["GET"])
async def shapes_manifest(_request: object) -> Any:
    """Plain HTTP manifest of what this process serves. Not an MCP method.

    A journey cannot ask "am I talking to the fixture I just edited?" over MCP
    without opening a session, and the answer matters more than the elegance:
    a previous run's fixture left listening on the port answers registration,
    initialises cleanly, and serves its own older tool list. Measured during
    this file's construction — a stale server on 8931 returned "Unknown tool"
    for every shape added that day while looking perfectly healthy.

    ``rows`` is the dataset size, so a journey can assert a rendered row count
    against the fixture's own truth rather than a number copied into two files.
    """

    from starlette.responses import JSONResponse

    return JSONResponse(
        {
            "fixture": "surface-floor",
            "revision": REVISION,
            "rows": len(_INCIDENTS),
            "shapes": list(SHAPES),
        }
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
