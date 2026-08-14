"""Model-visible tool descriptions used by runtime capabilities.

Some of these are **progressively disclosed**: the full text below is published
to ``/tools/<name>.md`` and the model surface carries the much shorter
``*_RESIDENT_SUMMARY`` beside it. Both halves live in this one module on
purpose — a summary that drifts from the text it stands in for is the failure
mode, and the only defence is that the next author edits them in the same file.
See :mod:`agent_runtime.capabilities.tools.catalog` for the split's rules.
"""

from __future__ import annotations

from typing import Final


#: Appended to every deferred tool's resident summary. One sentence, three jobs:
#: name the exact call that expands the tool, state that the ARGUMENT SCHEMA is
#: already complete (so a model that never expands still makes a well-formed
#: call), and name the one situation that should send it to the file. It is
#: formatted per tool rather than stated once in ``/tools/TOOLS.md`` because the
#: pointer has to be in front of the model at the moment it is choosing THIS
#: tool; an index it has to already know about is the silent-failure shape.
_GUIDANCE_POINTER: Final[str] = (
    '\n\nFull rules: read_file("/tools/{tool}.md"). The argument schema below '
    "is complete, so a direct call is well-formed — read the file when the call "
    "is non-obvious or one was refused."
)


def _with_guidance_pointer(summary: str, *, tool: str) -> str:
    """Return ``summary`` with the ``/tools/<tool>.md`` pointer appended."""

    return f"{summary}{_GUIDANCE_POINTER.format(tool=tool)}"


ASK_A_QUESTION_TOOL_DESCRIPTION = (
    "Pause and ask the human user a clarifying question, then resume with their "
    "answer. Use only when the user's intent is genuinely ambiguous and progress "
    "depends on their input. Do not use for rhetorical or self-answerable "
    "questions.\n\n"
    "Fields:\n"
    "- `question` (required): the full question, written in second person. Make "
    "it self-contained — do NOT use a separate `hint` field; fold any helper "
    "context into the question itself.\n"
    "- `header` (optional, ≤24 chars): a short title for the card. Defaults to "
    '"Quick question".\n'
    "- `options` (optional, up to 8): suggested answers. Each entry is either a "
    "plain string or `{label, description?, recommended?}`. The user can still "
    "type a free-text reply unless `allow_free_text` is false.\n"
    "- `multi_select` (optional, default false): when true, the user can select "
    "multiple options before submitting.\n"
    "- `allow_free_text` (optional, default true): when false, the user must "
    "pick from `options`. Set false only when the choice space is closed.\n\n"
    "Mark at most one option as `recommended` to express a default without "
    "forcing it. The tool returns `{ok, decision, answer, selected, free_text}` "
    "on submission, or `{ok: false, decision: 'rejected'}` if the user declines."
)


STAGE_ROWSET_WRITE_TOOL_DESCRIPTION = (
    "Stage a BULK write as a reviewable table: N per-row changes the user "
    "decides on individually, then applies with one action. Use for "
    "multi-record updates (e.g. re-prioritize 8 issues, update 12 contacts). "
    "Nothing is written until the user approves — staging never executes.\n\n"
    "Fields:\n"
    "- `target_connector` (required): the connector server slug (e.g. `linear`).\n"
    "- `target_op` (required): the write operation each row calls (e.g. "
    "`update_issue`).\n"
    "- `title` (required): a short label for the whole change.\n"
    "- `rows` (required, up to 200): each row is `{row_key, title, target_args, "
    "changes}` and NOTHING else. `row_key` is a stable unique id (the target "
    "record id); `target_args` is the EXACT arguments object `target_op` will be "
    "called with for that row; `changes` is a list of `{field, old, new}` diffs "
    "shown to the user. Keep `target_args` byte-accurate — it is what sends.\n"
    "  Every `changes` entry must name a KEY OF `target_args` for that row, and "
    "its `new` must be exactly the value that key carries. A diff describing a "
    "field the row would not send, or a value different from the one it would "
    "send, refuses the whole call — the user approves what is sent, so the two "
    "cannot describe different writes. The server derives and displays the "
    "account of every remaining argument; do not try to supply one.\n"
    "- `agent_holds` (optional): rows you are deliberately withholding, each "
    "`{row_key, reason}` (≤200 chars). Pre-hold anything risky (a recent reply, a "
    "record you are unsure about); the reason stays visible and a held row is "
    "NEVER applied unless the user explicitly overrides it.\n\n"
    "Returns `{stage_id, surface_id, rows_staged, rows_pre_held, status}`. The "
    "run continues — the user decides on the surface; do not wait or re-ask."
)


#: Resident half of ``stage_rowset_write``. Keeps what decides WHETHER to reach
#: for this tool — bulk vs single write, and that staging never executes — and
#: defers the row shape and the diff-accuracy rule, which only matter once the
#: tool has been chosen and are exactly what the argument schema already names.
STAGE_ROWSET_WRITE_RESIDENT_SUMMARY: Final[str] = _with_guidance_pointer(
    "Stage a BULK write as a reviewable table: N per-row changes the user "
    "decides on individually, then applies with one action. Use for "
    "multi-record updates (e.g. re-prioritize 8 issues, update 12 contacts). "
    "Nothing is written until the user approves — staging never executes, and "
    "the run continues without waiting.",
    tool="stage_rowset_write",
)


_ARTIFACT_DESTINATION_RULE = (
    "Reporting where it went: state the destination from the tool result's "
    "`stored_in` field and nothing else. `artifact_library` means the content is "
    "in the app's artifact library, openable and downloadable from the canvas. "
    "It is NOT a file on the user's computer. Never say it was saved to a "
    "folder, to disk, to Documents, or to any path unless a filesystem "
    "operation actually ran and returned that path; `wrote_to_filesystem` is "
    "`false` here, so no such claim is true."
)


_ACCENT_RULE = (
    "Choosing an accent: it says WHICH THING a surface is, so a user can tell "
    "two open tabs apart at a glance. It is identity, not decoration and not "
    "emphasis. Leave it unset unless you have a reason — a sensible colour is "
    "already derived from `kind`, and no accent is better than an arbitrary "
    "one. Set it when the default would actively mislead: publishing several "
    "artifacts of the SAME kind in one turn, where distinct colours make them "
    "separable; or continuing a series, where reusing the earlier artifact's "
    "accent shows they belong together. Keep one meaning per colour within a "
    "conversation — the same colour on two unrelated artifacts is worse than no "
    "colour at all. Use `none` for a surface that should deliberately show no "
    "identity. Never use accent to signal status, urgency, progress, or "
    "success: those already have their own colours, and competing with them "
    "makes both unreadable."
)


PUBLISH_ARTIFACT_TOOL_DESCRIPTION = (
    "Create ONE NEW durable code, document, dataset, or file artifact in the "
    "app's artifact library. Use this only when the user explicitly asks to "
    "create, save, or produce a durable artifact. Ordinary prose and fenced "
    "code remain chat text and must not be published automatically.\n\n"
    "To change an artifact that already exists — adding a row, fixing a value, "
    "editing a section — use `revise_artifact` instead. Publishing again makes "
    "a SECOND unrelated artifact and a second canvas tab, which is not what the "
    "user asked for when they said 'add' or 'change'.\n\n"
    "Fields:\n"
    "- `kind` (required): `code`, `document`, `dataset`, or `file`. Choose it "
    "from the media type, not from how the result will be used: `text/csv` and "
    "`text/tab-separated-values` are `dataset`; `text/markdown` is `document`. "
    "`file` is only for media no structured renderer can parse, and a CSV "
    "published as `file` is refused — that view offers a download and nothing "
    "else, so the reader would have no way to edit a cell.\n"
    "- `title` and `media_type` (required): safe display metadata.\n"
    "- exactly one of `content` (UTF-8, at most 1 MiB) or `content_ref` "
    "(a sanctioned server result reference).\n"
    "- `suggested_filename` (optional): download metadata only, never a path.\n"
    "- `presentation_preference` (optional): `auto`, `canvas`, `chat_card`, or "
    "`none`; this is a request and can be downgraded.\n"
    "- `accent` (optional): the artifact's identity colour on its canvas tab "
    "and surface card. One of `jade`, `sky`, `indigo`, `ember`, `violet`, "
    "`plum`, `amber`, `none`. A name only — never a hex code or CSS.\n\n"
    f"{_ACCENT_RULE}\n\n"
    f"{_ARTIFACT_DESTINATION_RULE}\n\n"
    "Return a short normal response after publishing."
)


#: Resident half of ``publish_artifact``. The publish-vs-revise disambiguation
#: stays resident and is the one deliberate overlap with the deferred text: it
#: decides WHICH tool to call, so a model that never opens the file must still
#: have it. The accent vocabulary, the kind/media-type table and the
#: destination-reporting rule are all post-choice usage and move to the file.
PUBLISH_ARTIFACT_RESIDENT_SUMMARY: Final[str] = _with_guidance_pointer(
    "Create ONE NEW durable code, document, dataset, or file artifact in the "
    "app's artifact library. Use only when the user explicitly asks to create, "
    "save, or produce a durable artifact; ordinary prose and fenced code stay "
    "chat text. To CHANGE an artifact that already exists, use "
    "`revise_artifact` — publishing again makes a SECOND unrelated artifact "
    "and a second canvas tab.",
    tool="publish_artifact",
)


REVISE_ARTIFACT_TOOL_DESCRIPTION = (
    "Replace the content of an artifact that ALREADY EXISTS, creating its next "
    "immutable revision. Use this whenever the user asks to change, add to, "
    "correct, or update an artifact you or they previously produced — it keeps "
    "one artifact with a version history and one canvas tab, instead of "
    "scattering near-duplicates.\n\n"
    "Fields:\n"
    "- `artifact_id` (required): the id returned when the artifact was "
    "published or last revised.\n"
    "- `parent_revision` (required): the revision number you are editing FROM. "
    "This is a compare-and-append, so your work can never overwrite an edit the "
    "user made in the meantime. If the artifact has moved on since that "
    "revision, the runtime re-applies your change on top of the newer one for "
    "you and the result names the revision it landed on in "
    "`rebased_onto_revision` — say the user's edit was kept, and never claim "
    "you wrote over it.\n"
    "- exactly one of `content` (the COMPLETE new content, UTF-8, at most "
    "1 MiB) or `content_ref` (a sanctioned server result reference). Send the "
    "whole document, not a patch or a fragment.\n\n"
    "A `failed` result whose message tells you to read the current revision and "
    "revise again is an INSTRUCTION TO YOU, not news for the user. It means the "
    "automatic re-apply could not decide the change on your behalf, and nothing "
    "was overwritten. Read the artifact's current revision, merge the user's "
    "change with yours, and call this tool again from that revision number. "
    "Reporting the conflict instead of retrying leaves the user's request "
    "silently undone.\n\n"
    "Kind, title, and media type cannot be changed by revising; they belong to "
    "the artifact itself.\n\n"
    f"{_ARTIFACT_DESTINATION_RULE}"
)


#: Resident half of ``revise_artifact``. "Send the COMPLETE new content" stays
#: resident because it is the one rule whose violation silently truncates a
#: user's artifact and the argument schema cannot express it. The
#: compare-and-append conflict protocol defers: it only matters after a
#: ``failed`` result, and that result carries the pointer back to the file.
REVISE_ARTIFACT_RESIDENT_SUMMARY: Final[str] = _with_guidance_pointer(
    "Replace the content of an artifact that ALREADY EXISTS, creating its next "
    "immutable revision — one artifact with a version history and one canvas "
    "tab, instead of scattering near-duplicates. Use this whenever the user "
    "asks to change, add to, correct, or update an artifact already produced. "
    "Send the COMPLETE new content, never a patch or a fragment.",
    tool="revise_artifact",
)


AUTH_MCP_TOOL_DESCRIPTION = (
    "Request an authorization URL for an MCP server when the user has not "
    "authenticated it yet. Use this only when the server is needed."
)

CALL_MCP_TOOL_DESCRIPTION = (
    "Call a tool from an MCP server after load_mcp_server has returned that "
    "server's validated tool descriptors. "
    "Pass tool inputs as a JSON object in the `arguments` field "
    "(not `parameters`)."
)

LOAD_MCP_SERVER_TOOL_DESCRIPTION = (
    "Load an authorized MCP server by stable name. Publishes its catalog under "
    "/mcp/<server>/ and returns a pointer plus a short summary — not the full "
    "descriptors. Read /mcp/<server>/SERVER.md for how to call it, list "
    "/mcp/<server>/tools/ to see every tool, and read one "
    "/mcp/<server>/tools/<tool>.json for that tool's full input schema. Use "
    "grep over /mcp/<server>/ to find a tool by keyword."
)

LOAD_TOOL_SPEC_DESCRIPTION = (
    "Load the full schema and instructions for an authorized tool by stable name."
)

LOAD_SKILL_TOOL_DESCRIPTION = (
    "Load the full Markdown for an available Skill by stable skill_name. "
    "Use this only when a compact Skill card is relevant to the user request."
)
