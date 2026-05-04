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


AUTH_MCP_TOOL_DESCRIPTION = (
    "Request an authorization URL for an MCP server when the user has not "
    "authenticated it yet. Use this only when the server is needed."
)

CALL_MCP_TOOL_DESCRIPTION = (
    "Call a tool from an MCP server after load_mcp_server has returned that "
    "server's validated tool descriptors."
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
