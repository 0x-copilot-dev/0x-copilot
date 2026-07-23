"""Model-visible tool descriptions used by runtime capabilities."""

from __future__ import annotations


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
    "changes}`. `row_key` is a stable unique id (the target record id); "
    "`target_args` is the EXACT arguments object `target_op` will be called with "
    "for that row; `changes` is a list of `{field, old, new}` diffs shown to the "
    "user (display only). Keep `target_args` byte-accurate — it is what sends.\n"
    "- `agent_holds` (optional): rows you are deliberately withholding, each "
    "`{row_key, reason}` (≤200 chars). Pre-hold anything risky (a recent reply, a "
    "record you are unsure about); the reason stays visible and a held row is "
    "NEVER applied unless the user explicitly overrides it.\n\n"
    "Returns `{stage_id, surface_id, rows_staged, rows_pre_held, status}`. The "
    "run continues — the user decides on the surface; do not wait or re-ask."
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
    "Load an authorized MCP server by stable name and return validated "
    "tool and resource descriptors."
)

LOAD_TOOL_SPEC_DESCRIPTION = (
    "Load the full schema and instructions for an authorized tool by stable name."
)

LOAD_SKILL_TOOL_DESCRIPTION = (
    "Load the full Markdown for an available Skill by stable skill_name. "
    "Use this only when a compact Skill card is relevant to the user request."
)
