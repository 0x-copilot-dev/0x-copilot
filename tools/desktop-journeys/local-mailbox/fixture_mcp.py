"""A loopback MCP server holding a LOCAL mailbox. No vendor, no OAuth.

Sibling of ``surface-floor/fixture_mcp.py`` and deliberately built to its shape:
a test double at the network boundary and nothing else. Everything inside the
app stays real — the real MCP client dials this over loopback HTTP, the real
policy layer classifies each tool, the real ``SurfaceProjector`` climbs the real
ladder, the real ``WorkLedgerEmitter`` writes the real ledger, and the real
renderers draw it.

**Why a local mailbox, and not Gmail.** Gmail and Drive are gated OFF pending
Google's CASA restricted-scope review, so there is no mail connector an
automated journey may authorise. A local mailbox has no vendor to authorise
against, which makes the ``email://`` surface observable end to end today rather
than after somebody else's compliance review.

The wire form
-------------
Every tool returns ``TextContent`` with ``structured_output=False``, so what
leaves this process is byte-for-byte what a real server sends::

    {"content": [{"type": "text", "text": "{\\"messages\\": [...]}"}]}

That is not a detail. ``surface-floor``'s predecessor returned Python ``dict``\\s
and left ``structured_output`` at its default, which makes FastMCP derive an
output schema and send ``structuredContent`` — the one wire form effectively no
real MCP server produces. It passed 10/10 while every real connector rendered an
empty table. A fixture that proves a path nobody takes is worse than no fixture.

The three tools, and why each is annotated the way it is
--------------------------------------------------------
===================  ==========  =====================================
tool                 class       what it proves
===================  ==========  =====================================
``list_messages``    READ        the normal ladder draws a table
``draft_reply``      READ        the ``email://`` composer is minted
``send_reply``       **WRITE**   the existing write gate takes it
===================  ==========  =====================================

``readOnlyHint`` is not decoration here — it is the whole classification. The
PDP resolves ``(action × trust × posture)``, and ``action`` comes from
``CapabilityDescriptorFactory.action_for``: catalog first, then
``destructiveHint``, then ``readOnlyHint``, then **fail-closed WRITE**. A WRITE
under the default policy (``write=ask``) resolves to ``GATE``, which
``PolicyStageMcpTool._authorize`` parks on ``ToolAccessGate.park_for_approval``
and dispatches only when the resume says approved.

So the split below is exactly the product's own line:

* ``draft_reply`` is annotated read-only because it **changes nothing**. It
  composes a reply from a message already in this mailbox and hands it back; the
  mailbox on disk is untouched, nothing leaves the machine. That is a read, and
  it is what puts a draft on screen for a human to look at before anything
  happens to it.
* ``send_reply`` carries **no** ``readOnlyHint`` and no catalog entry, so it
  fails closed to WRITE and cannot dispatch without an approval. It is
  deliberately left un-annotated rather than annotated ``readOnlyHint: False``:
  the un-annotated case is the one a real server most often ships, and it is the
  one whose safety depends on the runtime's default rather than on the server's
  honesty.

**Nothing here sends anything anywhere.** ``send_reply`` appends to an in-memory
list and returns a receipt. It exists to be gated, not to deliver mail.
"""

from __future__ import annotations

import json
import os
from typing import Any, Final

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

#: Default port. 8931 is ``surface-floor``'s; this fixture takes the next one so
#: both can be listening at once (``artifacts_and_surfaces.py`` drives AS-9 and
#: AS-10 in the same booted app).
DEFAULT_PORT: Final[int] = 8932

#: Overridable for the same reason the sibling fixture's is: a PREVIOUS
#: session's server left listening on this port answers registration happily and
#: serves its OWN, older tool list, so the journey measures a fixture nobody
#: edited. ``/mailbox`` below is the manifest that makes a stale server loud.
PORT: Final[int] = int(os.environ.get("MAILBOX_FIXTURE_PORT", str(DEFAULT_PORT)))

#: Reaches the surface header, the ledger's ``source.server``, the canvas tab
#: label and the ``email://<server-slug>/…`` URI, so it is pinned even when the
#: port is not.
mcp = FastMCP("local-mailbox", host="127.0.0.1", port=PORT)

#: Stated on the two tools that genuinely change nothing. Its ABSENCE on
#: ``send_reply`` is what makes that tool a gated write.
_READ_ONLY: Final[dict[str, Any]] = {"readOnlyHint": True}

#: The mailbox. Three received messages, fixed, so a rendered row count can be
#: asserted against this file's own truth rather than a number copied twice.
_MESSAGES: Final[list[dict[str, Any]]] = [
    {
        "id": "m-1041",
        "from": "jordan.reyes@acme.example",
        "to": "sam@local.example",
        "subject": "Renewal terms — Q4 wrap and FY27 path",
        "received_at": "2026-08-06T09:14:00Z",
        "unread": True,
        "preview": "Following up on the per-seat rate we discussed on Tuesday.",
        "body": (
            "Hi Sam,\n\n"
            "Following up on the per-seat rate we discussed on Tuesday. "
            "Can you confirm the locked-price block still applies for FY27?\n\n"
            "Jordan"
        ),
    },
    {
        "id": "m-1039",
        "from": "billing@vendor.example",
        "to": "sam@local.example",
        "subject": "Invoice 88213 is ready",
        "received_at": "2026-08-06T07:02:00Z",
        "unread": False,
        "preview": "Your August invoice is available.",
        "body": "Your August invoice is available in the billing portal.",
    },
    {
        "id": "m-1036",
        "from": "riya.s@local.example",
        "to": "sam@local.example",
        "subject": "Notes from the platform sync",
        "received_at": "2026-08-05T16:40:00Z",
        "unread": False,
        "preview": "Three follow-ups, one of them yours.",
        "body": "Three follow-ups from the sync, one of them yours: the worker queue.",
    },
]

#: Everything ``send_reply`` has been approved for, this process's lifetime.
#: A journey reads it back through ``/mailbox`` to prove that a DECLINED gate
#: left it empty — an assertion about the gate that the client DOM cannot make.
_SENT: Final[list[dict[str, Any]]] = []


class _Envelope:
    """Builders for the wire forms. One place decides how a shape is spelled."""

    @staticmethod
    def text(payload: str) -> TextContent:
        """One MCP text content block carrying ``payload`` verbatim."""

        return TextContent(type="text", text=payload)

    @classmethod
    def json_text(cls, document: object) -> TextContent:
        """One text block holding ``document`` as JSON.

        Compact separators on purpose: a real connector does not pretty-print,
        and whitespace would be the only thing distinguishing this from a
        hand-made fixture.
        """

        return cls.text(json.dumps(document, separators=(",", ":")))


class _Mailbox:
    """Reads over the fixed message list. Pure; no tool mutates it."""

    @staticmethod
    def find(message_id: str) -> dict[str, Any]:
        """The message with ``message_id``, else the newest one.

        Falling back rather than raising keeps the journey's failure modes
        separable: a model that passes a slightly wrong id must not produce a
        connector error that reads like a pipeline break.
        """

        wanted = message_id.strip()
        for row in _MESSAGES:
            if row["id"] == wanted:
                return row
        return _MESSAGES[0]

    @staticmethod
    def quote(body: str) -> str:
        """The original message, quoted the way a mail client quotes it."""

        return "\n".join(f"> {line}" for line in body.splitlines())


@mcp.tool(
    description=(
        "List the messages in the local mailbox as a JSON object in a text "
        "block. Read-only: changes nothing and sends nothing."
    ),
    annotations={**_READ_ONLY, "title": "List messages"},
    structured_output=False,
)
def list_messages() -> TextContent:
    """READ — a JSON object inside a single text block.

    The overwhelmingly common real shape (``surface-floor`` shape 1, modelled on
    Linear). The rows are right there, as a JSON *string* inside ``text``, which
    is what ``EnvelopeUnwrapper._decode_content_once`` exists to decode. Through
    the normal ladder this binds ``items_path: "messages"`` and draws a table.

    ``body`` is deliberately omitted from the list rows. A mail list view shows
    a preview, and shipping three full bodies into a table would put a paragraph
    in a cell — which is a fixture making the pipeline look worse than it is.
    """

    rows = [
        {key: value for key, value in row.items() if key != "body"} for row in _MESSAGES
    ]
    return _Envelope.json_text({"messages": rows, "has_next_page": False})


@mcp.tool(
    description=(
        "Compose a reply to one message and return the draft. Read-only: it "
        "does not save, queue, or send anything."
    ),
    annotations={**_READ_ONLY, "title": "Draft a reply"},
    structured_output=False,
)
def draft_reply(message_id: str, note: str = "") -> TextContent:
    """READ — the payload the ``email://`` producer recognises as a draft.

    Returns a single-object envelope, ``{"draft": {...}}``, because that is what
    a mail connector actually returns and because it exercises the producer's
    one-level unwrap. The keys are deliberately NOT the renderer's own: ``to``
    is a **list**, the subject key is ``subject_line`` and the body key is
    ``body_text``. The runtime maps them onto ``EmailState``'s four slots — the
    same direction the write lane takes, where the model maps fields and never
    values.

    ``cc`` is present and empty, which is the case that matters most: a ``Cc``
    the user never saw is the failure this surface must not have, so the
    producer reads that slot from a cc-named key or leaves it blank, never
    back-filling it from recipients.
    """

    original = _Mailbox.find(message_id)
    opening = note.strip() or "Confirming the locked-price block still applies."
    return _Envelope.json_text(
        {
            "draft": {
                "id": f"draft-{original['id']}",
                "in_reply_to": original["id"],
                "to": [original["from"]],
                "cc": [],
                "subject_line": f"Re: {original['subject']}",
                "body_text": (
                    f"Hi {original['from'].split('@')[0].split('.')[0].title()},\n\n"
                    f"{opening}\n\n"
                    "Best,\nSam\n\n"
                    f"{_Mailbox.quote(original['body'])}"
                ),
            }
        }
    )


@mcp.tool(
    description=(
        "Send a reply from the local mailbox. This is a write: it is the only "
        "tool here that changes anything."
    ),
    # No ``readOnlyHint``, and that omission IS the test. With no catalog entry
    # and no annotation, ``CapabilityDescriptorFactory.action_for`` fails closed
    # to WRITE, the PDP returns GATE under the default ``write=ask`` policy, and
    # ``PolicyStageMcpTool._authorize`` parks on the approval before dispatch.
    annotations={"title": "Send reply"},
    structured_output=False,
)
def send_reply(to: str, subject: str, body: str, cc: str = "") -> TextContent:
    """WRITE — reached only after the gate approves.

    Appends to an in-memory list and returns a receipt. Nothing leaves this
    process; there is no SMTP, no network egress, no file written. It exists to
    be gated.

    That the receipt is itself draft-shaped is intentional: after an approved
    send the same ``email://`` composer is minted over the sent copy, so the
    surface a person approved and the surface they end up looking at are the
    same shape.
    """

    sent = {
        "id": f"sent-{len(_SENT) + 1}",
        "to": to,
        "cc": cc,
        "subject": subject,
        "body": body,
        "status": "sent",
    }
    _SENT.append(sent)
    return _Envelope.json_text({"sent": sent, "sent_count": len(_SENT)})


#: Bumped whenever a tool is added, removed or re-spelled. A journey reads this
#: off the RUNNING server and refuses to measure a revision it does not know,
#: which is the difference between "the pipeline regressed" and "you are talking
#: to last week's fixture".
REVISION: Final[str] = "local-mailbox.1"

#: The tool table, served over plain HTTP so a journey can verify it is talking
#: to THIS fixture without opening an MCP session.
TOOLS: Final[tuple[dict[str, Any], ...]] = (
    {"tool": "list_messages", "class": "read", "draws": "table"},
    {"tool": "draft_reply", "class": "read", "draws": "email://"},
    {"tool": "send_reply", "class": "write", "draws": "the write gate"},
)


@mcp.custom_route("/mailbox", methods=["GET"])
async def mailbox_manifest(_request: object) -> Any:
    """Plain HTTP manifest of what this process serves. Not an MCP method.

    ``sent`` is the load-bearing field. "Nothing sends without an approval" is
    a claim about a code path, and the only way to observe it from outside is to
    ask the far end whether it was ever called. A journey that declines the gate
    and then reads ``sent == 0`` here has measured the gate; one that only checks
    the DOM has measured a button.
    """

    from starlette.responses import JSONResponse

    return JSONResponse(
        {
            "fixture": "local-mailbox",
            "revision": REVISION,
            "messages": len(_MESSAGES),
            "sent": len(_SENT),
            "tools": list(TOOLS),
        }
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
