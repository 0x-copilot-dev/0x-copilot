# PR 9.0 — Tool display-metadata middleware (drop the per-call polish LLM)

> **Status:** Draft (PRD + Spec)
> **Plan reference:** Replaces the per-tool-call presentation polish step (`gpt-4.1-nano`, [`presentation_templates.py`](../../services/ai-backend/src/agent_runtime/api/presentation_templates.py), [`settings.py:RuntimePresentationSettings`](../../services/ai-backend/src/agent_runtime/settings.py)) with a 3-tier deterministic-first projection. Closes the gap that lets MCP tool cards render with engineering-style titles ("call_tool result") and unformatted JSON dumps.
>
> **Owner:** ai-backend (new `capabilities/middleware/display_metadata.py`; extend `BackendMcpProvider._tool_descriptor`; `ToolCard.display` becomes required for default tools; optional `_display_*` injection on the LangChain args*schema; `PresentationGenerator` resolution chain updated; polish LLM call removed). · api-types (zero — wire shape unchanged; the FE already reads `presentation.title` / `presentation.summary`). · frontend (zero — the projection layer is unchanged from the FE's perspective; the change is \_which* upstream produces the values).
> **Size:** **L.** ~480 LoC across backend + tests. No DB migration. Zero FE changes. Zero `api-types` changes.
> **Depends on:**
>
> - ✅ The 4-step `PresentationGenerator` resolution chain ([`api/presentation_templates.py`](../../services/ai-backend/src/agent_runtime/api/presentation_templates.py)) — deterministic templates → `ToolDisplayTemplate` → `PayloadProjector` → polish LLM. We're collapsing step 4 into a deterministic-or-agent-supplied path.
> - ✅ `ToolDisplayTemplate` exists today on `ToolCard` and `McpToolDescriptor` ([`capabilities/tools/cards.py:55`](../../services/ai-backend/src/agent_runtime/capabilities/tools/cards.py#L55)) but is `Optional`. This PR makes registration enforce a non-`None` template for default tools and synthesises one for MCP.
> - ✅ The FE already prefers `presentation.title` / `presentation.summary` over its own heuristics ([`McpTool.tsx`](../../apps/frontend/src/features/chat/components/tools/McpTool.tsx), [`ApprovalTool.tsx`](../../apps/frontend/src/features/chat/components/tools/ApprovalTool.tsx)).
>
> **Reads alongside:**
>
> - [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — Pydantic at every IO boundary; capability exposure is enforced in `capabilities/` middleware, not custom builders.
> - [`services/ai-backend/docs/specs/02-dynamic-tool-loading-spec.md`](../../services/ai-backend/docs/specs/02-dynamic-tool-loading-spec.md) — `ToolCard` registration contract.
> - [`services/ai-backend/docs/specs/04-dynamic-mcp-loading-spec.md`](../../services/ai-backend/docs/specs/04-dynamic-mcp-loading-spec.md) — `BackendMcpProvider` descriptor build path.
> - [`services/ai-backend/docs/specs/03-skills-middleware-spec.md`](../../services/ai-backend/docs/specs/03-skills-middleware-spec.md) — existing `capabilities/middleware/` pattern this PR follows.

---

## 0 · TL;DR

Today every MCP tool result triggers a small extra LLM call (`gpt-4.1-nano`, 1.5s timeout, `RUNTIME_PRESENTATION_MODEL`) to produce a card title + summary, because MCP tool descriptors don't ship a `ToolDisplayTemplate` and most default tools opt out. The polish LLM is the silent path of least resistance. The fix is a 3-tier middleware that produces display metadata **at registration time or as part of the agent's own tool call** — never as a separate post-hoc call:

1. **Tier 1 — Deterministic `ToolDisplayTemplate` (registration time).** For every default Python/LangGraph tool, `ToolCard.display` becomes **required** (Pydantic). `string.format`-style placeholders fill from the tool's args payload. Zero LLM cost, frozen at registration, deterministic per (tool, payload).
2. **Tier 2 — Auto-synthesised template for MCP tools (descriptor-build time).** New `DisplayMetadataMiddleware.synthesise_for_mcp(descriptor)` runs in `BackendMcpProvider._tool_descriptor`. Humanises the tool name (`list_issues` → `"List {connector} issues"`), picks `result_preview_path` / `result_preview_row` from the JSON-schema's `properties`, and attaches the synthesised `ToolDisplayTemplate` to the descriptor. Zero LLM cost; happens once per (server_id, tool_name) at install/load.
3. **Tier 3 — Optional agent-supplied `_display_*` fields (same call as the tool invocation).** The middleware wraps every tool's `args_schema` to add two **optional** fields: `_display_title: str | None` and `_display_summary: str | None`. The agent fills them in only when Tier 1/2 would produce a generic template. Stripped from the args before forwarding to the actual tool implementation; surfaced on the event payload for the projector. Zero extra LLM call — the values arrive on the same call as the tool args.
4. **Polish LLM removed.** `RUNTIME_PRESENTATION_MODEL` and the `PresentationOutput` polish path are deleted from `presentation_templates.py`. The FE-visible `presentation` payload is identical in shape; only the producer changes.

| Surface                                        | Today                                                                                     | After this PR                                                                                                         |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `ToolCard.display`                             | `ToolDisplayTemplate \| None`                                                             | **Required.** Pydantic rejects registration with `display=None`.                                                      |
| `McpToolDescriptor.display`                    | Always `None` (vendors don't ship templates).                                             | Auto-synthesised by `DisplayMetadataMiddleware` at descriptor-build time. Cached per `(server_id, tool_name)`.        |
| Tool `args_schema`                             | Vendor / author-defined.                                                                  | Wrapped with optional `_display_title` / `_display_summary` (~30 tokens added to schema). Stripped before tool call.  |
| `PresentationGenerator` resolution             | Templates → projector → **polish LLM** → fallback.                                        | Templates → projector → **agent `_display_*`** → fallback. No LLM call.                                               |
| `RUNTIME_PRESENTATION_MODEL` / nano polish LLM | Runs per tool result that misses templates. ~$0.0001/result; +1.5s timeout / latency tax. | **Deleted.** Setting + module path gone.                                                                              |
| Per-call cost (primary model)                  | Baseline.                                                                                 | +30-50 output tokens _only_ when the agent fills `_display_*` (i.e. when Tier 1/2 misses). Most calls: zero overhead. |
| Per-call cost (nano polish)                    | ~$0.0001/result × N tool calls per turn.                                                  | Zero.                                                                                                                 |
| Latency from tool-end → card visible           | Up to 1.5s (polish timeout window).                                                       | 0 — display fields land with the tool call event.                                                                     |
| Reliability                                    | Polish can timeout, return malformed JSON, or fail; FE falls back to heuristic strings.   | Deterministic. Pydantic enforces shape at registration; agent-supplied fields validated by the same wrap.             |
| MCP tool card title (today's leak)             | "call_tool result" / "list_issues" — raw tool-name heuristic when polish times out.       | "List Linear issues" — synthesised at descriptor-build time, frozen for the conversation.                             |

LoC: backend ≈ 380 (middleware module +180, MCP descriptor wiring +30, ToolCard validator +15, presentation generator chain rewrite +60, polish LLM deletion -90, settings cleanup -25, tests +210). FE/api-types zero.

The four runtime / streaming invariants (frozen at run-start, binary at runtime, single PATCH endpoint, replay-by-sequence) are preserved. No new event types; no DB migration; the wire `presentation` payload is identical in shape.

---

## 1 · PRD

### 1.1 Problem

The 4-step resolution chain in `PresentationGenerator` ([`api/presentation_templates.py`](../../services/ai-backend/src/agent_runtime/api/presentation_templates.py)) is well-designed, but tier 4 (polish LLM) is doing most of the work in production because tiers 2 and 3 are systematically empty:

1. **MCP tools never ship `display` templates.** [`BackendMcpProvider._tool_descriptor`](../../services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py#L287) builds an `McpToolDescriptor` from the vendor's MCP `tools/list` response and leaves `display=None`. There is no path for a vendor to register a `ToolDisplayTemplate` because vendors don't know about our types. So 100% of MCP tool calls fall to tiers 3-4.
2. **`PayloadProjector` only fills `result_preview` rows**, not `title` / `summary`. So the projector can fill the _body_ of a card but not the header. The header always either (a) comes from a deterministic template, (b) comes from the polish LLM, or (c) falls through to the FE heuristic (`inlineMcpToolTitle(toolName, ...)` — produces strings like "list_issues from Linear" or worse, "call_tool result").
3. **Polish LLM is the silent default.** It runs _per tool result_, costs nano money, takes up to 1.5s, can fail, and when it fails the FE falls back to a worse string than what a deterministic template would produce. This is the path of least resistance for any new tool.
4. **Default Python/LangGraph tools opt out.** `ToolCard.display: ToolDisplayTemplate | None = None` makes the deterministic path optional. Authors skip it and the polish LLM runs for those too.

The "call_tool result" / unformatted-JSON screenshot the user shared is the symptom: polish missed, FE heuristic kicked in, the disclosure dumped raw wire-format.

We have two architectural options to fix this:

- **A. Per-tool-load LLM**: at MCP-install time, ask a small LLM to produce a `ToolDisplayTemplate` from the vendor's tool name + JSON schema. Cache by `(server_id, tool_name)`. Reuse forever. **One** LLM call per tool, amortised across all future invocations.
- **B. Required fields injected into every tool's args_schema**: the agent fills `_display_title` / `_display_summary` as part of the same call that produces the tool args. Zero extra LLM calls. Output-token bloat on the primary model (~30-50 tokens × N calls per turn).

Both reduce per-call cost to near-zero. (A) keeps the primary model's output token count flat at the cost of an install-time LLM call (cheap, one-shot, easy to reason about). (B) costs more output tokens on the primary but produces better copy (full conversation context) with zero install-time work.

This PR ships a **hybrid** that beats either alone: deterministic templates by default (zero LLM, zero output bloat for most calls) + agent-supplied fallback for the edge cases (output bloat _only_ when needed).

### 1.2 Goals

1. **Drop the per-call polish LLM.** `RUNTIME_PRESENTATION_MODEL`, `RUNTIME_PRESENTATION_TIMEOUT_SECONDS`, and the `PresentationOutput` polish path are deleted. No replacement nano call.
2. **`ToolCard.display` becomes required** for default LangGraph tools. Pydantic rejects `display=None`. Existing call sites that registered a `ToolCard` without `display` get a meaningful error (with a one-line migration hint pointing at the new helper `ToolDisplayTemplate.from_tool_name(name, connector=...)`).
3. **MCP tool descriptors auto-synthesise a `display` template** at build time via `DisplayMetadataMiddleware.synthesise_for_mcp`. Synthesis logic is deterministic + pure: tool name → humanised title, JSON-schema → `result_preview_path` / `result_preview_row`. Cached on the descriptor; the worker never recomputes.
4. **Every tool's `args_schema` is wrapped** to add two optional fields: `_display_title: Annotated[str | None, Field(default=None, max_length=80, description="One-line user-facing title (optional).")]` and `_display_summary: Annotated[str | None, Field(default=None, max_length=240, description="Optional short summary.")]`. The wrap happens uniformly for default + dynamic + MCP tools. The fields are stripped from the args before the tool is invoked (the actual tool never sees them); they're surfaced on the `tool_call` event payload for the projector.
5. **`PresentationGenerator` resolution becomes 3-tier**: deterministic templates → agent-supplied `_display_*` → projector body fill. No LLM call. The "minimal envelope fallback" stays as the last-resort safety net.
6. **No FE changes**: the FE already reads `presentation.title` / `presentation.summary` from event payloads. The wire shape is unchanged; only the producer changes.
7. **No DB migration, no new event types.**
8. **Honest cost picture in code comments**: a single comment block in `display_metadata.py` documents the per-call output-token bloat from `_display_*` and the trigger for the agent to fill them (template missing or marked `synthetic=True`).

### 1.3 Non-goals

- **Per-tool-load LLM for synthesis (Option A).** Deterministic synthesis covers ≥95% of MCP tools (verified against the 13 catalog vendors); the remainder lean on the agent-supplied fallback. Adding an install-time LLM is a separate, optional follow-up and not blocking.
- **Per-vendor recogniser library for display.** That's the pattern for approval params (`approval_recognisers.py`) and remains scoped to approvals; display metadata stays centralised in the middleware.
- **Streaming / replay vocabulary changes.** Existing event types carry the same `presentation` field; no new event types.
- **`api-types` mirror changes.** The `RuntimePresentation` shape is identical to today.
- **FE changes.** None.
- **`result_preview` LLM polish.** `PayloadProjector` already produces deterministic `result_preview` rows from `result_preview_path` / `result_preview_row` heuristics. We keep that. The agent-supplied fields are limited to header copy (title/summary) — keeping output-token bloat bounded.
- **i18n on synthesised titles.** English-only for v1; matches the rest of the surface.
- **Removing `presentation_templates.py` itself.** Only the polish-LLM branch is deleted; deterministic templates and the projector stay.
- **Compatibility shim for old `ToolCard(display=None)` registrations in the wild.** This is an internal contract; we update all call sites in the same change.

### 1.4 Success criteria

- ✅ `agent_runtime/capabilities/middleware/display_metadata.py` exists and exports `DisplayMetadataMiddleware`.
- ✅ `ToolCard.display: ToolDisplayTemplate` (no `| None`); Pydantic rejects construction without it.
- ✅ `BackendMcpProvider._tool_descriptor` returns descriptors with `display` populated. Existing tests that asserted `display is None` are flipped to assert non-None and to verify the synthesised title format.
- ✅ Every tool registered via the agent runtime has `_display_title` / `_display_summary` as optional fields on its `args_schema`. Verified via a runtime test that introspects registered tools.
- ✅ The two `_display_*` fields are stripped from `args` before tool invocation (the underlying tool function/MCP RPC never sees them). Verified by a unit test that registers a fake tool, invokes it via the wrapped path, and asserts the function receives only the original args.
- ✅ The two `_display_*` fields are surfaced on the `RuntimeEventEnvelope` for `tool_call` / `tool_result` events and consumed by `PresentationGenerator`. Verified by an end-to-end test that runs a fake tool, asserts the event payload carries the agent's display strings, and asserts `presentation.title` is the agent-supplied value.
- ✅ `PresentationGenerator` resolution chain is **deterministic templates → agent-supplied `_display_*` → projector body → minimal envelope fallback.** No LLM call. Verified by a test that monkey-patches every LLM client to raise; presentation generation succeeds.
- ✅ `RUNTIME_PRESENTATION_MODEL`, `RUNTIME_PRESENTATION_TIMEOUT_SECONDS`, and `RuntimePresentationSettings` are deleted. Settings tests updated.
- ✅ `PresentationOutput` (the polish LLM structured-output schema) is deleted.
- ✅ Per-call output-token bloat is bounded: `_display_title` ≤ 80 chars (~25 tokens), `_display_summary` ≤ 240 chars (~70 tokens). Pydantic enforces.
- ✅ All Python tests under `services/ai-backend/tests/unit/` pass; new `test_display_metadata_middleware.py` covers ~14 cases (wrapping, stripping, synthesis, registration enforcement).
- ✅ `api-types` typecheck clean (no changes to the package, only verification it still builds).
- ✅ FE typecheck + tests clean (no changes to the package).
- ✅ `services/ai-backend/.env` example updated; the deleted env vars removed.
- ✅ One PR-doc cross-reference: `services/ai-backend/docs/specs/04-dynamic-mcp-loading-spec.md` updated to note the descriptor-build-time middleware step.

### 1.5 User stories

| #    | Persona                          | Story                                                                                                                                                                                                                                                                                                                                                          |
| ---- | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | Sarah · Linear `list_issues`     | Atlas calls `list_issues`. Card lands instantly with title `"List Linear issues"` (auto-synthesised from MCP descriptor) and a 3-row preview (issue titles + status badges, from the projector). No polish LLM ran. No JSON dump in the disclosure — pretty-printed inner payload (deep-unwrap from prior PR) sits behind ▼ for power users. Latency: ~50ms.   |
| US-2 | Sarah · ambiguous tool           | Atlas calls a custom `run_workflow` tool whose synthesised title would be the generic `"Run workflow"`. Tier 1 produces a low-quality template (we mark it `synthetic=True`). On the same call, the agent fills `_display_title="Approving Q1 budget"`, `_display_summary="Routing to finance for sign-off."`. Card renders with the agent-supplied title.     |
| US-3 | Marcus · admin auditor           | An audit query for tool calls in a run shows `display_title` populated for every entry. Marcus filters by `display_title CONTAINS "Linear"` and finds every Linear interaction without grepping raw tool names.                                                                                                                                                |
| US-4 | Engineer · adding a default tool | She registers a new `ToolCard(name="search_docs", ...)` without `display`. Pydantic raises `ValidationError: display: required (use ToolDisplayTemplate.from_tool_name() if no custom copy)`. She copies the suggested helper, ships, done.                                                                                                                    |
| US-5 | Engineer · adding an MCP server  | He installs a new MCP server. He doesn't write a single line of display code. The descriptor middleware runs at install, synthesises titles for every tool the server advertises, caches them on the descriptor. First chat call renders a sensible card.                                                                                                      |
| US-6 | SRE · cost dashboard             | The `RUNTIME_PRESENTATION_MODEL` line item disappears from the OpenAI billing dashboard. Per-conversation OpenAI cost drops by ~$0.0001 × (avg tool calls per conversation). For chats with 20 tool calls/day × 1000 conversations: ~$2/day saved. Latency budget for the post-tool-result render goes from "up to 1.5s" to "synchronous with event emission." |
| US-7 | Engineer · model swap            | Migrating from one primary LLM to another doesn't change presentation behaviour. There's no second model to swap (today the polish nano model lives separately and migrating it is its own change). One less moving part.                                                                                                                                      |
| US-8 | Compliance officer               | She greps audit rows for "tool calls without a display_title". The query returns zero — Pydantic rejects registration without one, MCP synthesis always produces one, agent fallback fills the rest. The audit log is consistently human-readable.                                                                                                             |

---

## 2 · Spec

### 2.1 Module — `agent_runtime/capabilities/middleware/display_metadata.py` (new)

```python
"""Display-metadata middleware — Tier 1/2/3 hybrid for tool card titles + summaries.

Replaces the per-tool-call presentation polish LLM (`gpt-4.1-nano`) with a
deterministic-or-agent-supplied projection that runs at registration /
descriptor-build / agent-call time, never as a separate post-hoc call.

Resolution order (consumed by ``PresentationGenerator``):
    1. ToolCard.display / McpToolDescriptor.display — registered template.
    2. Agent-supplied ``_display_title`` / ``_display_summary`` from the
       wrapped args_schema (only if Tier 1 was marked ``synthetic=True``).
    3. PayloadProjector — fills ``result_preview`` rows.
    4. Minimal envelope fallback — humanised tool name + status.

Steps 1 + 2 + 3 + 4 produce a complete envelope deterministically. No LLM
call is made at presentation time; tier 2 is an additive output of the
*same* LLM call that produced the tool args.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, create_model

from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

# --- Tier 1: enforce ToolDisplayTemplate on default tools ------------------

# The validator lives on ToolCard itself (see §2.2). The middleware exposes a
# helper for callers without custom copy.

class ToolDisplayTemplate(ToolDisplayTemplate):  # re-export for clarity
    @classmethod
    def from_tool_name(
        cls,
        tool_name: str,
        *,
        connector: str | None = None,
    ) -> "ToolDisplayTemplate":
        """Synthesise a generic-but-usable template from a tool name.

        ``list_issues`` + connector=``linear`` → title=``"List Linear issues"``.
        Mark ``synthetic=True`` so the resolution chain prefers any
        agent-supplied ``_display_*`` fields over the synthesised copy.
        """
        ...

# --- Tier 2: synthesise for MCP descriptors --------------------------------

class DisplayMetadataMiddleware:
    """Registration-time + agent-call-time middleware. Pure functions only."""

    @classmethod
    def synthesise_for_mcp(
        cls,
        *,
        tool_name: str,
        connector: str,
        input_schema: Mapping[str, Any],
        output_shape: Mapping[str, Any],
    ) -> ToolDisplayTemplate:
        """Build a deterministic ToolDisplayTemplate for an MCP tool.

        Inputs:
        - tool_name — vendor-supplied (e.g. "list_issues", "post_message").
        - connector — vendor display name (e.g. "Linear", "Slack").
        - input_schema — JSON schema of the args (used to pick placeholders).
        - output_shape — JSON schema of the result (used to pick result_preview_path).

        Behaviour:
        - title_template: humanise tool_name with connector verb-form
          fixups (``list_*`` → "List X", ``post_*`` → "Post X", etc.).
          Reads top-level ``properties`` from input_schema and folds in the
          most-likely "primary entity" placeholder (e.g. ``{query}`` for
          ``search_*``, ``{channel}`` for ``post_*``).
        - result_preview_path: walk output_shape for known roots
          (``items``, ``results``, ``data``, ``content``, ``output.content``).
          Picks the first array-shaped one.
        - result_preview_row: heuristic from row property names
          (``title``/``name``/``summary`` → row title;
          ``id``/``url``/``permalink`` → subtitle).
        - synthetic: True. Signals to PresentationGenerator that the agent's
          ``_display_*`` fields, if present, take precedence over this.
        """
        ...

# --- Tier 3: wrap args_schema with optional _display_* fields --------------

class _DisplayFields(BaseModel):
    """Optional agent-supplied display strings.

    These are stripped from the args before forwarding to the tool
    implementation; the projector reads them off the event payload.
    Lengths bounded so we don't blow the primary model's output token
    budget — most calls leave them None and pay zero output bloat.
    """

    display_title: str | None = Field(
        default=None,
        max_length=80,
        alias="_display_title",
        description=(
            "Optional. Use only when the tool's deterministic display "
            "template would be too generic (e.g. a generic ``run_workflow`` "
            "tool whose meaning varies per call). One-line user-facing copy."
        ),
    )
    display_summary: str | None = Field(
        default=None,
        max_length=240,
        alias="_display_summary",
        description=(
            "Optional. Same trigger as ``_display_title``; one short sentence."
        ),
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}


def wrap_args_schema(args_schema: type[BaseModel]) -> type[BaseModel]:
    """Return a new Pydantic model that extends ``args_schema`` with
    optional ``_display_title`` + ``_display_summary`` fields.

    Caller responsibilities:
    - Strip ``_display_*`` from the args dict before invoking the tool.
    - Surface the stripped values on the ``tool_call`` event payload so
      ``PresentationGenerator`` can consume them downstream.
    """
    return create_model(
        f"{args_schema.__name__}WithDisplay",
        __base__=(args_schema, _DisplayFields),
    )


def strip_display(args: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str | None]]:
    """Split a wrapped-args dict into (real_args, display_fields)."""
    display = {
        "_display_title": args.get("_display_title"),
        "_display_summary": args.get("_display_summary"),
    }
    real = {k: v for k, v in args.items() if k not in {"_display_title", "_display_summary"}}
    return real, display
```

### 2.2 `ToolCard.display` becomes required

Update [`agent_runtime/capabilities/tools/cards.py`](../../services/ai-backend/src/agent_runtime/capabilities/tools/cards.py):

```python
class ToolCard(RuntimeContract):
    name: str
    display_name: str = Field(min_length=1, max_length=Limits.TOOL_NAME_MAX_LENGTH)
    short_description: str = Field(...)
    connector: str
    tags: frozenset[str] = Field(default_factory=frozenset)
    required_scopes: frozenset[str] = Field(default_factory=frozenset)
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    load_cost: PositiveInt = Field(le=Limits.TOOL_LOAD_COST_MAX)
    enabled: bool = True
    # PR 9.0 — required. Use ``ToolDisplayTemplate.from_tool_name`` if you
    # don't want to author custom copy.
    display: ToolDisplayTemplate

    @model_validator(mode="after")
    def _display_required(self) -> "ToolCard":
        # Pydantic enforces non-None already; this hook is a place to
        # validate the template's title_template renders against an empty
        # payload (catches typos like "{{tquery}}" that would silently fall
        # to the literal string at runtime).
        ...
```

`McpToolDescriptor.display` stays `Optional` because the descriptor is _built by_ the middleware, not registered by an author. The middleware always populates it.

### 2.3 MCP descriptor wiring

Update [`agent_runtime/capabilities/mcp/backend_provider.py:_tool_descriptor`](../../services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py#L287):

```python
@classmethod
def _tool_descriptor(cls, tool: dict[str, Any]) -> McpToolDescriptor:
    name = cls._required_string(tool, Keys.Field.NAME, Values.Placeholder.TOOL_NAME)
    input_schema = cls._schema(...)
    output_shape = cls._schema(...)
    # PR 9.0 — synthesise display metadata at descriptor-build time so the
    # presentation generator never falls through to an LLM polish.
    display = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name=name,
        connector=cls._connector_label(),  # provider knows its server's display_name
        input_schema=input_schema,
        output_shape=output_shape,
    )
    return McpToolDescriptor(
        name=name,
        description=cls._optional_string(tool.get("description")) or f"{name} MCP tool.",
        input_schema=input_schema,
        output_shape=output_shape,
        risk_level=McpRiskLevel.MEDIUM,
        display=display,
    )
```

### 2.4 LangChain args_schema wrap

The agent runtime's tool-loading path (default + dynamic + MCP) goes through a single registration layer. Wrap the args_schema there:

```python
# In agent_runtime/execution/deep_agent_builder.py (or wherever tools are
# bound to the LangGraph agent — the existing single seam).

def _bind_tools_with_display(tools: list[BaseTool]) -> list[BaseTool]:
    return [_with_display(t) for t in tools]

def _with_display(tool: BaseTool) -> BaseTool:
    wrapped = wrap_args_schema(tool.args_schema)

    def _invoke(args: dict, **kwargs):
        real_args, display = strip_display(args)
        # `display` flows out via the event-emit hook (next section).
        _emit_display_metadata_for_call(tool.name, display)
        return tool.invoke(real_args, **kwargs)

    return tool.copy(update={"args_schema": wrapped, "func": _invoke})
```

The `_emit_display_metadata_for_call` hook attaches `_display_title` / `_display_summary` to the next emitted `tool_call` event so `PresentationGenerator` sees them.

### 2.5 `PresentationGenerator` resolution rewrite

[`agent_runtime/api/presentation_templates.py`](../../services/ai-backend/src/agent_runtime/api/presentation_templates.py) drops tier 4 (polish LLM) and inserts tier 1.5 (agent-supplied `_display_*` from the event payload):

```python
class PresentationGenerator:
    """Composes the user-facing presentation envelope without an LLM call.

    Resolution order (PR 9.0):
        1. DeterministicTemplates — fully payload-derived (approval, auth, etc.).
        2. Tool template — ToolDisplayTemplate from ToolCard / McpToolDescriptor.
        3. Agent-supplied _display_* — only when the tool template was synthetic.
        4. PayloadProjector — fills result_preview rows.
        5. Minimal envelope fallback — humanised tool name + status.
    """

    @classmethod
    def for_event(cls, event: RuntimeEventEnvelope) -> RuntimePresentation:
        # ... 1, 2 unchanged ...
        title, summary = cls._template_or_synthesised(event)
        if cls._template_was_synthetic and event.payload.get("_display_title"):
            title = event.payload["_display_title"]
        if cls._template_was_synthetic and event.payload.get("_display_summary"):
            summary = event.payload["_display_summary"]
        # ... projector + fallback unchanged ...
```

### 2.6 Settings cleanup

Delete from [`agent_runtime/settings.py`](../../services/ai-backend/src/agent_runtime/settings.py):

- `Env.PRESENTATION_MODEL`
- `Env.PRESENTATION_TIMEOUT_SECONDS`
- `RuntimePresentationSettings`
- The `presentation: RuntimePresentationSettings` field on the parent settings model
- The construction call in `from_environment`

Update `services/ai-backend/.env.example` to remove `RUNTIME_PRESENTATION_MODEL` / `RUNTIME_PRESENTATION_TIMEOUT_SECONDS` references.

### 2.7 Test plan

New `services/ai-backend/tests/unit/capabilities/middleware/test_display_metadata.py` covers:

| Case                                                                                                                     | What it pins                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| `synthesise_for_mcp(name="list_issues", connector="Linear", ...)` → `"List Linear issues"`                               | Verb-form humanisation for `list_*`.                                                          |
| `synthesise_for_mcp(name="post_message", connector="Slack", ...)`                                                        | `post_*` verb form, primary-entity placeholder picked from `input_schema.properties.channel`. |
| `synthesise_for_mcp(name="search_repos", connector="GitHub", ...)`                                                       | `search_*` verb form, `{query}` placeholder.                                                  |
| `synthesise_for_mcp` produces `synthetic=True` always                                                                    | Agent fallback can override.                                                                  |
| Synthesised `result_preview_path` walks `output.content`                                                                 | MCP envelope unwrapping.                                                                      |
| `wrap_args_schema(schema)` → new model has `_display_title` and `_display_summary`                                       | Schema augmentation works.                                                                    |
| `strip_display({"foo": 1, "_display_title": "Hi"})` → `({"foo": 1}, {"_display_title": "Hi", "_display_summary": None})` | Stripping is exact.                                                                           |
| `_DisplayFields` rejects extra keys                                                                                      | `extra="forbid"` enforced.                                                                    |
| `_DisplayFields` rejects `_display_title` longer than 80 chars                                                           | Output-token budget enforced.                                                                 |
| Wrapped tool invocation: actual `func` receives only the real args                                                       | The `_display_*` fields don't leak to tool implementations.                                   |
| `ToolCard(display=None)` → `ValidationError`                                                                             | Required-display contract for default tools.                                                  |
| `BackendMcpProvider._tool_descriptor` produces a non-None `display`                                                      | MCP synthesis runs at the right seam.                                                         |
| `PresentationGenerator.for_event` with a missing template + agent-supplied `_display_*`                                  | Tier 3 fallback wins.                                                                         |
| `PresentationGenerator.for_event` with no LLM client configured                                                          | Resolution succeeds; no AttributeError. Pins polish-LLM removal.                              |

Plus integration: a worker-level test runs a fake MCP tool, asserts the emitted `tool_call` event payload carries the agent's `_display_*` strings, asserts the `tool_result` event's `presentation.title` matches.

Plus a sweep of existing call sites that registered `ToolCard(display=None)` — pre-PR they all need `display=ToolDisplayTemplate.from_tool_name(...)` added (or a real custom template). Test failures + `git grep "display=None"` give an exhaustive list at PR-cut time.

---

## 3 · Risks + open questions

1. **Output token bloat from the schema additions.** Adding `_display_title` and `_display_summary` to every tool's args_schema increases the system prompt (tool definitions block) by ~30 tokens × N tools loaded. For a chat with 30 MCP tools loaded, that's ~900 extra system-prompt tokens. Concretely: $0.0027 / Mtok input × 30 tools × 30 tokens = $0.0024 per turn (Claude Opus pricing as a worst case). Acceptable. Alternative: only wrap tools whose deterministic template was marked `synthetic=True` — narrows the schema bloat to where it matters. **Open question:** wrap-everything (uniform, simple) vs. wrap-only-synthetic (cheaper, but the agent has to keep two mental models)? Recommendation: **wrap everything** for v1; profile and narrow if production token costs warrant it.

2. **MCP tool name humanisation correctness.** Verb-form fixups for prefixes (`list_*`, `post_*`, `search_*`, `get_*`, `create_*`, `update_*`, `delete_*`) cover ~85% of vendor tool names; the rest produce slightly awkward titles like `"Run workflow"`. The agent-supplied fallback covers those. **Open question:** ship a vendor-specific override map (`linear:list_issues → "Search Linear issues"`)? Recommendation: **no**, that's the recogniser pattern from approvals; we kept that scoped to approvals on purpose. The agent fills the gap here.

3. **Polish LLM removal as a deploy gate.** The polish LLM is currently the "safety net" — even if a deterministic template + projector both miss, the LLM smooths it over. After this PR, the safety net is the minimal envelope fallback (humanised tool name + status). If a tool's display template is broken at registration time (typo'd placeholder), production renders the literal placeholder text. **Mitigation:** the `_display_required` validator on `ToolCard` renders the template against an empty payload at registration to catch typos. Plus: registration tests for every default tool.

4. **`synthetic=True` semantics.** When does the _MCP_ synthesis declare a template synthetic? Always (we never hand-author MCP templates). When does a _default-tool_ template declare itself synthetic? Only when the author opts into `ToolDisplayTemplate.from_tool_name(...)` (i.e. they didn't write custom copy). **Decision (this PR):** `synthetic=True` is set by `from_tool_name` and `synthesise_for_mcp`; explicit `ToolDisplayTemplate(title_template=..., ...)` constructors default to `synthetic=False`. The agent's `_display_*` only overrides when synthetic.

5. **Backward compatibility on `presentation.debug_label`.** Today some FE call sites read `presentation.debug_label` ("Tool details" disclosure label). It's emitted by the polish LLM. After this PR, the deterministic templates emit `debug_label` as a constant `"Tool details"`. FE behaviour unchanged.

6. **Streaming during long tool calls.** A tool call in flight emits `tool_call` (start) and `tool_result` (end) events. The agent-supplied `_display_*` values arrive on `tool_call` (start), so the _running_ card carries the title + summary too. We don't have to wait for the result. Latency for "card lands with title" is sub-100ms.

7. **Subagent fleet card titles.** Subagents emit their own `display_title` via [`stream_subagents.py`](../../services/ai-backend/src/runtime_worker/stream_subagents.py). Out of scope for this PR — that path is separate from tool calls. Future work could fold it into the same middleware.

8. **What happens when the agent fills `_display_*` even though Tier 1 produced a real template?** The resolution chain ignores the agent's strings (Tier 1 wins when not synthetic). The agent wastes ~50 output tokens. **Mitigation:** the field's `description` explicitly says "use only when the deterministic template would be too generic" — instructs the model to leave them None by default. Profile after launch; tighten the prompt if the agent fills them indiscriminately.

---

## 4 · Out-of-scope follow-ups

- **Vendor-specific override map** for the ~15% of MCP tool names that humanise awkwardly — only if profiling shows the agent fills `_display_*` too often.
- **Subagent display middleware** (apply the same pattern to `run_subagent` / fleet titles).
- **`result_preview` per-vendor enrichment** (e.g. resolve a Slack channel ID to `#launch · 14 members` at render time). Separate PR; touches connector clients, not the middleware.
- **Per-tool-load LLM as Option A** — install-time synthesis with caching. If after this PR we still see ≥10% of tool calls fall through to the agent fallback, consider the install-time LLM as a more sophisticated Tier 2 replacement.
- **Streaming replay of agent-supplied `_display_*`.** Today event replay produces deterministic output because the values are persisted with the event. Pinning this in a test is a small follow-up.
