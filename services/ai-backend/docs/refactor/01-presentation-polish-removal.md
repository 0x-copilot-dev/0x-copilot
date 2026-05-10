# PRD — Remove the per-event presentation polish LLM

> **Status:** Draft (PRD)
> **Refactor target:** Audit finding [§1.1](../architecture/refactor-audit.md) — _PresentationGenerator polish on every event_.
> **Companion design:** [pr-9.0-tool-display-metadata-middleware.md](../../../docs/new-design/pr-9.0-tool-display-metadata-middleware.md) (existing draft of the 3-tier hybrid). This PRD reuses the design and adds two findings discovered while reading the actual code.
> **Owner:** ai-backend.
> **Scope:** Backend only. No `api-types` change. No frontend wire-shape change (one cleanup possible — see §6.4).

---

## 0 · TL;DR

Today every "tool" runtime event ([`TOOL_CALL`, `TOOL_CALL_STARTED`, `TOOL_RESULT`, `PROGRESS`]) in production triggers a background `gpt-4.1-nano` LLM call to "polish" the card title and summary, then emits a follow-up `PRESENTATION_UPDATED` event that patches body fields onto the preliminary. Two missing wires in production make this 100%-of-tool-events:

- The `tool_display_lookup` injection point on `PresentationGenerator` is **never wired** in production. All four `RuntimeEventProducer` construction sites use the default constructor.
- **No** `ToolCard(...)` is registered with `display=...`. The `ToolDisplayTemplate` Pydantic class exists, the renderer exists, but no caller produces one.
- **No** `McpToolDescriptor` is built with `display=...` either ([backend_provider.py:288](../../src/agent_runtime/capabilities/mcp/backend_provider.py#L288) leaves it `None`).

So the entire deterministic Tier-2 chain in the design ([api/presentation_templates.py](../../src/agent_runtime/api/presentation_templates.py)) is shipped but unreachable. The polish LLM is the _de facto_ renderer. The fix is to (a) wire what already exists, (b) add MCP descriptor synthesis, (c) add an optional agent-supplied `_display_*` slot for the long tail, and (d) delete the polish path.

The companion document [pr-9.0-tool-display-metadata-middleware.md](../../../docs/new-design/pr-9.0-tool-display-metadata-middleware.md) already specifies this 3-tier hybrid. This PRD treats that as the intended architecture and focuses on:

1. **What we discovered the code actually does** (different from the document's assumptions in two places).
2. **Every system the polish touches** (so we don't silently regress one).
3. **Every functionality it provides today** (so each is preserved or consciously dropped).
4. **Every user flow that lands a card** (so each is tested before/after).
5. **The refactor sequence**, with rollback safety and the exact test seam at each step.

---

## 1 · Problem

### 1.1 What polish does today

[`PresentationGenerator`](../../src/agent_runtime/api/presentation.py) generates the card metadata that the frontend renders for runtime events. Resolution order in code:

1. `metadata["presentation"]` if a caller already set one explicitly.
2. [`DeterministicTemplates`](../../src/agent_runtime/api/presentation_templates.py) for fully payload-derivable events: `APPROVAL_RESOLVED`, `APPROVAL_REQUESTED`, `MCP_AUTH_REQUIRED`, `ERROR`, `RUN_FAILED`, `TOOL_CALL_DELTA`. **This works in production.**
3. `tool_display_lookup(tool_name) → ToolDisplayTemplate?` then `ToolTemplateRenderer.render(...)`. **In production this returns `None` because the lookup is `None`.**
4. Minimal envelope (humanized tool name + status), with `PayloadProjector` filling `result_preview` rows for result events. **This produces the preliminary in 100% of production tool events.**
5. **Background LLM polish** for `{TOOL_CALL, TOOL_CALL_STARTED, TOOL_RESULT, PROGRESS}` — only when `metadata["presentation"]` was empty AND the event isn't in `DeterministicTemplates.HANDLED` AND `tool_display_lookup` returned no template. Today the conjunction is "always true for tool events."

### 1.2 What the code actually does in production (verified)

Reading [`runtime_worker/handlers/run.py:168`](../../src/runtime_worker/handlers/run.py#L168), [`handlers/cancel.py:33`](../../src/runtime_worker/handlers/cancel.py#L33), [`handlers/approval.py:110`](../../src/runtime_worker/handlers/approval.py#L110), [`runtime_api/app.py:329`](../../src/runtime_api/app.py#L329):

```python
self.event_producer = RuntimeEventProducer(
    persistence=self.persistence,
    event_store=self.event_store,
    on_event_appended=on_event_appended,
)  # no presentation_generator= → default PresentationGenerator() → tool_display_lookup=None
```

Reading [`backend_provider.py:288`](../../src/agent_runtime/capabilities/mcp/backend_provider.py#L288):

```python
return McpToolDescriptor(
    name=name,
    description=...,
    input_schema=...,
    output_shape=...,
    risk_level=McpRiskLevel.MEDIUM,
)  # no display=
```

Reading [`capabilities/tools/cards.py:94`](../../src/agent_runtime/capabilities/tools/cards.py#L94):

```python
class ToolCard(RuntimeContract):
    ...
    display: ToolDisplayTemplate | None = None
```

A repo-wide grep for `ToolCard(` returns only the class definition itself — there are no production registration sites.

**Net result:** every tool event in production runs the preliminary minimal envelope, then spawns an async polish LLM, then (on success) emits a separate `PRESENTATION_UPDATED` event.

### 1.3 Why this is a problem

| Concern                  | Evidence                                                                                                                                                                                                                                                                                           |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Cost**                 | Per tool event: 1 `gpt-4.1-nano` call (~150-400 tokens depending on context). For a 5-tool turn → 5 polish calls. At 1k turns/day on the upper-mid plan, ≈ 50k–100k extra calls/day.                                                                                                               |
| **Latency**              | Polish has a 1.5s timeout ([`settings.py:141`](../../src/agent_runtime/settings.py#L141)). On miss/timeout the user sees the minimal-envelope card; on success the FE re-renders when `PRESENTATION_UPDATED` lands. So the perceived card jitters for up to 1.5s after every tool event.           |
| **Doubled event volume** | Every successful polish emits a separate `PRESENTATION_UPDATED` envelope ([events.py:425](../../src/agent_runtime/api/events.py#L425)). With per-event DB amplification (`append_event` + `set_run_latest_sequence` + `SELECT FOR UPDATE`), this doubles the DB write load on visible tool events. |
| **Reliability**          | LLM polish can timeout (1.5s) or return malformed JSON. On miss the FE falls back to the minimal-envelope strings ("Checked source", "list_issues") which are worse than what a deterministic template would produce.                                                                              |
| **Provider lock-in**     | The polish path is hardcoded to OpenAI ([presentation.py:308](../../src/agent_runtime/api/presentation.py#L308)). Migrating off OpenAI requires a parallel migration of this subsystem.                                                                                                            |
| **Dead code**            | The deterministic Tier-2 path (`ToolTemplateRenderer` + `tool_display_lookup`) is shipped but unreachable — pure maintenance tax with no behavioral payoff.                                                                                                                                        |

### 1.4 Why this exists

Inferred from code comments and structure (no incident data available):

- The polish LLM was the path of least resistance: it works without anyone needing to author templates per-tool, and the agent's recent text gets injected as `agent_intent_hint` ([events.py:75-79](../../src/agent_runtime/api/events.py#L75)) so the polish can write summaries that reflect _why_ the agent invoked the tool.
- `ToolDisplayTemplate` was the right abstraction; the registration discipline + the production wiring just never landed.
- MCP descriptor synthesis was never built because vendors don't ship our types.

The `agent_intent_hint` mechanism is the one feature polish has that a naive deterministic system would miss — and it's exactly what the 3-tier design's Tier-3 (agent-supplied `_display_*`) replaces, by spending the agent's _existing_ output-token budget instead of a separate LLM call.

---

## 2 · Map of every system the polish touches

This is the surface area to keep in mind when refactoring. Each file below is either deleted, modified, or wired in this PRD.

### 2.1 Core polish machinery (will shrink or delete)

| File                                                                                                   | Role today                                                                                    | Refactor verdict                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/api/presentation.py`](../../src/agent_runtime/api/presentation.py)                     | `PresentationGenerator` — preliminary chain + LLM polish path + cache                         | **Modify.** Delete `_generate`, `_structured_model`, `_prompt`, `_context`, `_safe_json`, `_display_facts`, `_connector_display_name`, `_action_display_name`, `_server_card`, `_with_deterministic_fields`, `_deterministic_card_fields`, `enrich_presentation_for_event`, `event_eligible_for_enrichment`, `cache`, `presenter`, `presentation_settings`, `llm_factory`, `_cached_model`, `llm_eligible_event_types`. Add a new `tier_3_from_payload(payload)` helper that reads agent-supplied `_display_*` from event payload. Net: ~280 LOC removed, ~30 added. |
| [`agent_runtime/api/presentation_templates.py`](../../src/agent_runtime/api/presentation_templates.py) | Templates + projector + `PresentationOutput` LLM schema                                       | **Modify.** Delete `PresentationOutput` and `PresentationPreviewRowOutput`. Keep `DeterministicTemplates`, `ToolTemplateRenderer`, `PayloadProjector`, `_StatusLabel`, `_Kind`, `_ErrorMessage`, `_Identifier`. Net: ~30 LOC removed.                                                                                                                                                                                                                                                                                                                                |
| [`agent_runtime/api/events.py`](../../src/agent_runtime/api/events.py)                                 | `RuntimeEventProducer` — appends events + spawns enrichment                                   | **Modify.** Delete `_spawn_enrichment`, `_enrich_and_patch`, `_merge_polish`, `_pending_enrichment` map, `_intent_buffer`, `_inject_intent_hint`, `_track_intent`, `flush_pending_enrichment`, `_POLISH_BODY_FIELDS`, `INTENT_BUFFER_MAX`, `INTENT_HINT_MAX_CHARS`, `_INTENT_EVENT_TYPES`, `_first_text` (unused after deletes). Append path becomes synchronous: build preliminary, persist, notify, return. Net: ~180 LOC removed.                                                                                                                                 |
| [`agent_runtime/settings.py`](../../src/agent_runtime/settings.py)                                     | `RuntimePresentationSettings` + `Env.PRESENTATION_MODEL` + `Env.PRESENTATION_TIMEOUT_SECONDS` | **Modify.** Delete the class, the two env keys, the `presentation` field on `RuntimeSettings`, the `from_environment` construction. Update `.env.example` to drop `RUNTIME_PRESENTATION_MODEL` / `RUNTIME_PRESENTATION_TIMEOUT_SECONDS`.                                                                                                                                                                                                                                                                                                                             |

### 2.2 Schemas (kept as-is, with one open question)

| File                                                                       | Role today                                                                              | Refactor verdict                                                                                                                                                                                                                           |
| -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`runtime_api/schemas/events.py`](../../src/runtime_api/schemas/events.py) | `RuntimeEventEnvelope`, `RuntimeEventPresentation`, `RuntimeEventPresentationProjector` | **Keep wire shape.** `RuntimeEventPresentation` fields stay identical so the FE doesn't change. The `PRESENTATION_UPDATED` projector (line 215) becomes unreachable from the producer — but the enum value stays for replay compatibility. |
| [`runtime_api/schemas/common.py`](../../src/runtime_api/schemas/common.py) | `RuntimeApiEventType.PRESENTATION_UPDATED` enum value (line 139)                        | **Keep.** Old persisted runs may have these envelopes. New runs won't emit them.                                                                                                                                                           |

**Open question:** can we delete `PRESENTATION_UPDATED` from the enum? Decision below in §6.5.

### 2.3 Tool registration (will gain registrations)

| File                                                                                                                      | Role today                                                      | Refactor verdict                                                                                                                                                                                                               |
| ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`agent_runtime/capabilities/tools/cards.py`](../../src/agent_runtime/capabilities/tools/cards.py)                        | `ToolCard.display: ToolDisplayTemplate \| None`                 | **Modify.** Keep `Optional` initially (Phase 1) so we don't break the empty world. Make required (Phase 3) once every default tool has a template + a `from_tool_name` helper exists.                                          |
| [`agent_runtime/capabilities/mcp/cards.py`](../../src/agent_runtime/capabilities/mcp/cards.py)                            | `McpToolDescriptor.display: ToolDisplayTemplate \| None`        | **Keep `Optional`.** The descriptor is built by `BackendMcpProvider`, not registered by an author; the synthesis middleware always populates it but the field stays `Optional` for backward compat with persisted descriptors. |
| [`agent_runtime/capabilities/mcp/backend_provider.py`](../../src/agent_runtime/capabilities/mcp/backend_provider.py#L288) | `_tool_descriptor` builds `McpToolDescriptor` without `display` | **Modify.** Inject `display=DisplayMetadataMiddleware.synthesise_for_mcp(...)` so every MCP tool gets a deterministic template at descriptor-build time.                                                                       |
| `agent_runtime/capabilities/middleware/display_metadata.py` (new)                                                         | n/a                                                             | **Create.** Houses `DisplayMetadataMiddleware.synthesise_for_mcp`, `wrap_args_schema`, `strip_display`, `ToolDisplayTemplate.from_tool_name` helper.                                                                           |

### 2.4 Production wiring sites (must inject the lookup)

All four sites below construct `RuntimeEventProducer` without a `presentation_generator=`. We pass one with the registry-backed lookup wired:

| File                                                                                        | Line | Refactor verdict                                                                                                |
| ------------------------------------------------------------------------------------------- | ---- | --------------------------------------------------------------------------------------------------------------- |
| [`runtime_worker/handlers/run.py`](../../src/runtime_worker/handlers/run.py#L168)           | 168  | **Modify.** Pass `presentation_generator=PresentationGenerator(tool_display_lookup=tool_registry.display_for)`. |
| [`runtime_worker/handlers/cancel.py`](../../src/runtime_worker/handlers/cancel.py#L33)      | 33   | **Modify.** Same.                                                                                               |
| [`runtime_worker/handlers/approval.py`](../../src/runtime_worker/handlers/approval.py#L110) | 110  | **Modify.** Same.                                                                                               |
| [`runtime_api/app.py`](../../src/runtime_api/app.py#L329)                                   | 329  | **Modify.** Same — for the `DraftService` event producer.                                                       |

The `tool_registry.display_for(tool_name) → ToolDisplayTemplate | None` resolver needs to be added on the tool registry. See §4 step 2.

### 2.5 Args-schema wrap site (Tier 3)

| File                                                                                                       | Role today                                                     | Refactor verdict                                                                                                                                                                                                           |
| ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/execution/deep_agent_builder.py`](../../src/agent_runtime/execution/deep_agent_builder.py) | Builds the LangGraph agent with all tools bound                | **Modify.** Wrap each tool's `args_schema` via `wrap_args_schema(...)`, install a `_strip_and_emit_display(...)` shim that strips `_display_*` from args and threads them onto the next emitted `tool_call` event payload. |
| [`runtime_worker/stream_tools.py`](../../src/runtime_worker/stream_tools.py)                               | Adapts LangGraph stream events into `tool_call` runtime events | **Modify.** Carry `_display_title` / `_display_summary` from the tool call args dict onto `payload['_display_title']` / `payload['_display_summary']` so `PresentationGenerator` Tier 3 can read them.                     |

### 2.6 Tests

| File                                                                                                                               | LOC | Refactor verdict                                                                                                                                                                                                  |
| ---------------------------------------------------------------------------------------------------------------------------------- | --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`tests/unit/agent_runtime/api/test_presentation.py`](../../tests/unit/agent_runtime/api/test_presentation.py)                     | 794 | **Modify substantially.** Delete every test that asserts polish behavior (cache, timeout, presenter monkey-patch, `flush_pending_enrichment`, `PRESENTATION_UPDATED` patch event). Add tests for Tier-3 fallback. |
| [`tests/unit/agent_runtime/api/test_presentation_templates.py`](../../tests/unit/agent_runtime/api/test_presentation_templates.py) | 431 | **Light edit.** Delete `PresentationOutput` tests; rest is unchanged.                                                                                                                                             |
| `tests/unit/agent_runtime/capabilities/middleware/test_display_metadata.py` (new)                                                  | n/a | **Create.** Cases listed in §5.                                                                                                                                                                                   |
| `tests/unit/agent_runtime/capabilities/mcp/test_backend_provider_display.py` (new)                                                 | n/a | **Create.** Asserts every MCP descriptor produced has `display` populated.                                                                                                                                        |

### 2.7 Frontend (no required change)

The wire shape doesn't change. `RuntimeEventPresentation` carries the same fields. The FE already reads `presentation.title` / `summary` / `status_label` / `result_preview` / `debug_label` from the event envelope:

- [`presentationHelpers.ts`](../../../../apps/frontend/src/features/chat/components/activity/presentationHelpers.ts) — `presentationFromArgs` reads `args.presentation` (a separate path used by `McpTool.tsx` / `ApprovalTool.tsx` / `AskAQuestionTool.tsx` / `ConnectorAuthTool.tsx` / `ProgressTool.tsx`).
- [`GeneratedPresentationCard.tsx`](../../../../apps/frontend/src/features/chat/components/activity/GeneratedPresentationCard.tsx) — reads from event presentation directly.
- [`presentation.ts`](../../../../apps/frontend/src/features/chat/chatModel/presentation.ts) — handles the `PRESENTATION_UPDATED` patch logic. Becomes a no-op if we keep the enum value but stop emitting the event. **Optional cleanup** in §6.4.

---

## 3 · Functionalities the polish currently provides

Every item below must be either preserved or consciously dropped after the refactor. Marked **PRESERVED** or **DROPPED** with rationale.

### 3.1 Tier-by-tier behaviour today

| #    | Behavior                                                                                                                                        | Owner today                                                                                            | After refactor                                                                                                                                                                                                                                                                                                                                                          |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| F-1  | Card title for tool events                                                                                                                      | Polish LLM (~100% of cases)                                                                            | **PRESERVED.** Tier 2 (deterministic templates) for tools with `display`; Tier 2-MCP (auto-synthesis) for MCP descriptors; Tier 3 (`_display_title` from agent) for the long tail; minimal envelope as the safety net.                                                                                                                                                  |
| F-2  | Card summary for tool events                                                                                                                    | Polish LLM                                                                                             | **PRESERVED.** Same chain. Default body falls back to `payload.message` or projector preview.                                                                                                                                                                                                                                                                           |
| F-3  | Result preview rows for `TOOL_RESULT`                                                                                                           | `PayloadProjector` (already deterministic) + polish overlay                                            | **PRESERVED.** `PayloadProjector` stays. Polish-overlay path goes (it was rarely producing different rows).                                                                                                                                                                                                                                                             |
| F-4  | `status_label` / `kind` lifecycle freezing                                                                                                      | Synchronously assigned by `_deterministic_card_fields` and frozen across polish patches                | **PRESERVED, simplified.** Already deterministic; no patch path means there's nothing to freeze against.                                                                                                                                                                                                                                                                |
| F-5  | `agent_intent_hint` (rolling 4-event buffer of MODEL_DELTA / FINAL_RESPONSE text injected into polish prompt)                                   | `_intent_buffer` + `_inject_intent_hint` in `RuntimeEventProducer`                                     | **DROPPED in this form, REPLACED by Tier 3.** The intent-hint mechanism is a hack to give the polish LLM context about _why_ the agent invoked the tool. Tier 3 (`_display_*` filled by the agent in the same call as the tool args) gives the agent the same context for free — and the agent has _better_ context than a 1200-char buffer of recent assistant tokens. |
| F-6  | Per-(run_id, group_key) polish task cancellation (newer event for same group cancels older pending polish)                                      | `_pending_enrichment` map + `task.add_done_callback`                                                   | **DROPPED.** No async tasks → no race → no cancellation needed.                                                                                                                                                                                                                                                                                                         |
| F-7  | Cache by `(run_id, event_type, call_id, approval_id, status)`                                                                                   | `cache: dict[str, JsonObject]`                                                                         | **DROPPED.** No LLM call → no cost to cache.                                                                                                                                                                                                                                                                                                                            |
| F-8  | `PRESENTATION_UPDATED` patch event with `patches` field listing changed body keys                                                               | `_enrich_and_patch` emits a separate envelope                                                          | **DROPPED.** No async patch → no event needed. The preliminary already carries the final body.                                                                                                                                                                                                                                                                          |
| F-9  | Polish ignores deterministic events (approvals, auth, errors, deltas) — `event_eligible_for_enrichment` returns False                           | Already in code                                                                                        | **PRESERVED implicitly.** Those events still go through `DeterministicTemplates`; nothing else to do.                                                                                                                                                                                                                                                                   |
| F-10 | Polish runs only for `{TOOL_CALL, TOOL_CALL_STARTED, TOOL_RESULT, PROGRESS}`                                                                    | `llm_eligible_event_types` whitelist                                                                   | **N/A after refactor.** No polish path.                                                                                                                                                                                                                                                                                                                                 |
| F-11 | Title/status_label/kind frozen across polish (lifecycle owned, body patchable)                                                                  | `_POLISH_BODY_FIELDS` whitelist                                                                        | **N/A after refactor.** No polish merge.                                                                                                                                                                                                                                                                                                                                |
| F-12 | `debug_label` always set to `"Tool details"`                                                                                                    | `_envelope` in templates + `setdefault` in polish merge                                                | **PRESERVED.** Stays in `_envelope` and minimal-envelope construction.                                                                                                                                                                                                                                                                                                  |
| F-13 | Sanitisation: strip `mcp_*`, `_com`, `_io`, `_app` from connector names; remove HTML-ish chars from text; placeholder for known-large file refs | `_Identifier.humanize`, `_safe_text` (sanitisation parts only), `_PayloadProjector` field-name mapping | **PRESERVED.** Templates and projector keep these. The numeric `_clamp` / `[:N]` slicing is **dropped** (see §9 — no truncation).                                                                                                                                                                                                                                       |
| F-14 | "Large result saved for internal inspection" placeholder when payload references a large result file                                            | `_safe_text`, `_safe_json`                                                                             | **PRESERVED.** Templates and projector keep this.                                                                                                                                                                                                                                                                                                                       |
| F-15 | Strict redaction: drop `server_id`, `approval_id`, `call_id`, `tool_name`, `password`/`token`/`secret`/`key` from LLM prompt context            | `_safe_json`, `_secretish`                                                                             | **DROPPED (no LLM = no prompt = no leak).** This was a defense for the LLM path. The deterministic chain reads the validated event payload directly; redaction stays at the event-validation boundary (`ObservabilityRedactor`) where it always was.                                                                                                                    |

### 3.2 Producer behaviour that stays

These are not "polish features" but live in the same `RuntimeEventProducer` file and must be preserved:

- Sync ports adapted to async via `adapt_*_to_async` (the `to_thread` bridge is orthogonal to polish; out of scope here).
- `append_compression_note` for `COMPRESSION_NOTE` events (memory subsystem).
- `append_stream_event` and `append_stream_events` batch wrapper.
- `set_run_latest_sequence` + `on_event_appended` notification per event.

---

## 4 · How I plan to refactor (phased)

The phasing below is designed so each phase is independently shippable, behavior-preserving, and reversible.

### Phase 1 — Wire the existing dead code (1 day)

**Goal:** Get `tool_display_lookup` actually working in production for any tool that registers `display=...`. No semantic change yet because no tools register it. This phase exists to validate the wiring path doesn't break anything.

**Changes:**

1. Add `display_for(tool_name: str) → ToolDisplayTemplate | None` to the tool registry (the protocol the worker already holds).
2. Modify the four production sites to pass `presentation_generator=PresentationGenerator(tool_display_lookup=tool_registry.display_for)`.
3. Test: register a fake tool with `display=ToolDisplayTemplate(title_template="Fixed title")`, drive a `TOOL_CALL` event, assert the preliminary uses the fixed title and polish does not spawn.

**Risk:** Low. If the lookup raises, `_resolve_tool_template` already swallows the exception and returns None.

**Rollback:** Drop the `presentation_generator=` argument from the four sites.

### Phase 2 — Add MCP descriptor synthesis (1-2 days)

**Note on scope:** the original PRD draft folded the synthesis and the lookup-wiring into one phase. Reading the code revealed they are different problems: synthesis is a pure transform on the descriptor; the lookup wiring needs payload-extraction in the presentation generator (the actual MCP tool name lives inside `payload.args.tool_name`, not `payload.tool_name`, because MCP calls flow through the `call_mcp_tool` dispatcher). They are now two phases — **2.A synthesis** (this entry) and **2.B lookup wiring** (next).

**Goal of 2.A:** Every MCP descriptor gets a deterministic `display` template at build time. The synthesised template lives on the descriptor and travels with it; nothing in the runtime consumes it yet — that's Phase 2.B's job.

**Changes:**

1. Add `synthetic: bool = False` field to [`ToolDisplayTemplate`](../../src/agent_runtime/capabilities/tools/cards.py#L55). Synthesis paths set it `True`; author-written templates default to `False`. Tier 3 (Phase 3) reads this to decide whether agent-supplied `_display_*` overrides.
2. Create [`agent_runtime/capabilities/middleware/`](../../src/agent_runtime/capabilities/middleware/) package mirroring the existing `skills/` middleware pattern (per [04-dynamic-mcp-loading-spec.md](../specs/04-dynamic-mcp-loading-spec.md)).
3. Add `display_metadata.py` with `DisplayMetadataMiddleware.synthesise_for_mcp(tool_name, connector, input_schema, output_shape) → ToolDisplayTemplate`. Implementation rules (from [PR-9.0 §2.1](../../../docs/new-design/pr-9.0-tool-display-metadata-middleware.md)):
   - Verb-form humanisation: `list_*` → `"List {connector} ..."`, `post_*` → `"Post to {connector} ..."`, `search_*` → `"Search {connector} for {query}"`, `get_*`, `create_*`, `update_*`, `delete_*` analogously.
   - Pick the most-likely "primary entity" placeholder from `input_schema.properties` (e.g. `{query}` for `search_*`, `{channel}` for `post_*`).
   - Walk `output_shape` for known result-array roots (`items`, `results`, `data`, `content`, `output.content`); first array becomes `result_preview_path`.
   - Heuristic on row property names for `result_preview_row`: `title`/`name`/`summary` → row title; `id`/`url`/`permalink` → subtitle.
   - Always sets `synthetic=True`.
4. Modify [`backend_provider.py:_tool_descriptor`](../../src/agent_runtime/capabilities/mcp/backend_provider.py#L288) to inject `display=DisplayMetadataMiddleware.synthesise_for_mcp(...)`.
5. Tests: synthesis cases (verb forms, schema walking, idempotency, synthetic flag); end-to-end backend_provider asserts every descriptor has `display` populated.

**Observable change in production:** none yet. Synthesis runs but no consumer reads `descriptor.display` for MCP tool events. Phase 2.B closes that loop.

**Risk:** Low. Pure transform; synthesised templates travel on the descriptor without being used.

**Rollback:** Stop populating `display=` in `_tool_descriptor`.

### Phase 2.B — Wire MCP descriptors into the per-run lookup (1-2 days)

**Goal:** The presentation generator's lookup actually consults the synthesised MCP templates, dropping polish for 100% of MCP tool events.

**Why this is its own phase:** MCP tool calls flow through a single dispatcher tool (`call_mcp_tool`). The runtime emits events with `payload.tool_name = "call_mcp_tool"` — the _actual_ MCP tool name (`list_issues`, `post_message`) lives inside `payload.args.tool_name`. So the lookup needs a payload-extraction step before it can hit the synthesised template, plus a per-run mutable registry that gets populated as the loader builds `LoadedMcpServer` instances (descriptors load lazily during the run, not at startup).

**Changes:**

1. Per-run `McpDescriptorRegistry` (mutable `dict[str, ToolDisplayTemplate]` keyed by MCP tool name). Constructed in the run handler; bound via a new ContextVar (similar to `ToolDisplayLookupContext`, separate object).
2. The MCP loader, when constructing `LoadedMcpServer.tools`, calls `McpDescriptorRegistry.register(tool_name, descriptor.display)` for each tool that has a non-None display.
3. The composite tool-display lookup callable bound by the run handler walks: `tool_registry.display_for(name)` → `McpDescriptorRegistry.get(name)`.
4. `PresentationGenerator._resolve_tool_template` adds an MCP-aware payload-extraction step: when `payload.tool_name == "call_mcp_tool"`, look up `payload.args.tool_name` instead. Behaviour for all other tool names is unchanged.
5. Tests: end-to-end test that drives a fake MCP tool result, asserts the rendered `presentation.title` came from the synthesised template (not the polish path).

**Observable change:** MCP cards render with deterministic synthesised titles instead of polish output. Polish runs for 0% of MCP tool events.

**Risk:** Medium. Touches the dispatcher payload extraction — needs careful tests for the fallback paths (unknown server, unknown tool, dispatcher with no `args.tool_name`).

**Rollback:** Remove the payload-extraction step in `_resolve_tool_template`. Synthesised templates remain on descriptors but go unused.

### Phase 3 — Add `_display_*` Tier 3 (2-3 days)

**Note on scope:** Phase 3 splits the same way Phase 2 did. **3.A receive-side** ships the helpers + the Tier-3 read in `PresentationGenerator` (pure functions, easy to test, zero risk to existing code). **3.B tool-wrap** is the more invasive change that wraps every bound tool's `args_schema` so the agent's emissions actually reach the wire. 3.A is shippable on its own — it makes the receive-side ready, and an end-to-end test that simulates the agent emitting `_display_*` proves the path works.

**Goal of 3.A:** Pure helpers + Tier-3 read. Long-tail coverage starts working the moment 3.B wires the wrap into bind time.

**Changes:**

1. In `display_metadata.py`, add `wrap_args_schema(args_schema) → type[BaseModel]` — returns a Pydantic model that extends `args_schema` with two optional fields: `_display_title: str | None` and `_display_summary: str | None`. **No `max_length` caps** (per §8 — brevity comes from the field `description` shown to the model, not from rejection / truncation). Both have an `alias` and `populate_by_name=True` so the LLM can emit either form. `extra="forbid"` is preserved if the original had it.
2. In `display_metadata.py`, add `strip_display(args) → (real_args, display_dict)` — splits a wrapped-args dict into the original args and the `_display_*` payload.
3. In `PresentationGenerator`: add a Tier-3 read between Tier-2 (tool template) and the minimal-envelope fallback. Reads `payload.args._display_title` / `payload.args._display_summary` (consistent for both regular tools and the `call_mcp_tool` dispatcher — for dispatcher events the agent puts `_display_*` at the top of `args`, **not** inside `args.arguments`). Tier-3 wins only when the matched template has `synthetic=True` (or no template was found). Author-written templates always beat the agent.
4. Tests: helper round-trip + Tier-3 wins for synthetic + Tier-3 ignored for author-written + end-to-end TOOL*CALL event with simulated `\_display*\*` in args renders the agent-supplied title.

**Observable change in production:** none yet. No tool's `args_schema` is wrapped, so the agent doesn't see `_display_*` in its tool block and never emits them. The receive-side is correct and tested; 3.B closes the loop.

**Risk:** Low. Pure helpers + a small read in the existing chain. No tool binding touched.

**Rollback:** Revert the Tier-3 read in `PresentationGenerator`. Helpers remain importable but unused.

### Phase 3.B — Wire `_display_*` into tool binding (2-3 days)

**Goal:** Every tool the agent sees has `_display_title` / `_display_summary` in its JSON schema, and the wrapped invoke strips them before delegating to the underlying tool function.

**Why this is its own phase:** the LangChain tool-binding layer has multiple shapes — `BaseTool` subclasses, `StructuredTool` instances, our custom dataclasses (`CallMcpTool`, `LoadMcpServerTool`), and connector-specific wrappers (`ToolBudgetGuardedTool`, `CitationCapturingTool`). A naive wrap breaks one of them. The safe approach is a per-tool-shape strategy with a fallback for unknown types.

**Changes:**

1. In `display_metadata.py`, add `wrap_tool_with_display(tool) → tool_like` — strategy-pattern wrap that handles each tool shape (most cases via `StructuredTool.copy(update={...})`; custom dataclasses via dataclass replace).
2. In `build_deep_agent` ([deep_agent_builder.py](../../src/agent_runtime/execution/deep_agent_builder.py)): apply `wrap_tool_with_display` to every tool in `request.tools` before passing to `create_deep_agent`. Idempotent (no-op if already wrapped).
3. Update `CallMcpTool` and `LoadMcpServerTool` to accept and strip `_display_*` from the parsed input before dispatching the actual MCP RPC.
4. Tests: each tool-shape wraps correctly; underlying tool function never receives `_display_*`; agent's `_display_*` appears in the emitted `TOOL_CALL` event payload's `args`; end-to-end run of a wrapped fake tool with simulated agent input renders the agent-supplied title.

**Observable change:** the agent's tool block in the system prompt grows by ~30 input tokens per tool (the two new optional fields); for ambiguous tools where the synthesised title would be too generic the agent fills `_display_*` and the card title comes out personalised. Polish still runs as a fallback for tools where neither Tier-2 nor Tier-3 fires.

**Risk:** Medium-High. Touches every tool the agent sees. Mitigation: per-tool-shape strategy with explicit fallback, every existing tool test must pass unmodified, plus a new "underlying tool never sees `_display_*`" test per shape.

**Rollback:** Don't apply the wrap in `build_deep_agent`. Helpers remain importable but unused.

### Phase 4 — Delete the polish path (1 day)

**Goal:** Remove all LLM machinery from `PresentationGenerator` + `RuntimeEventProducer` + `settings.py`.

**Changes:**

1. Delete the methods listed in §2.1 from `presentation.py` and `events.py`.
2. Delete `PresentationOutput` + `PresentationPreviewRowOutput` from `presentation_templates.py`.
3. Delete `RuntimePresentationSettings` + the two `Env` keys from `settings.py`.
4. Update `.env.example` to remove `RUNTIME_PRESENTATION_MODEL` / `RUNTIME_PRESENTATION_TIMEOUT_SECONDS`.
5. Delete polish-related tests in `test_presentation.py` (cache, timeout, presenter, flush, patch event).
6. Add a new test that monkey-patches every LLM client to raise; assert preliminary still succeeds for every event type. Pins the polish removal.

**Risk:** Low (after Phases 1-3 prove the deterministic chain works). The change is mostly deletion.

**Rollback:** Revert. The code being deleted has no other consumers.

### Phase 5 — Make `ToolCard.display` required (optional, after default-tool registrations land)

**Goal:** Force the deterministic path for every default tool registration.

**Changes:**

1. Audit every `ToolCard(...)` registration site (currently zero, but expected to grow). Add a `display=ToolDisplayTemplate.from_tool_name(name)` to each.
2. Change `ToolCard.display` from `ToolDisplayTemplate | None` to `ToolDisplayTemplate`.
3. Add a `model_validator` that renders the `title_template` against an empty payload to catch typos at registration time.

**Risk:** Low if Phase 5 happens after default tools exist. Trivial Pydantic migration.

**Rollback:** Make `display` optional again. No data migration.

---

## 5 · User flows the polish covers

Each flow below is one of the `f1`-`f9` flows in the architecture index. For each, I list **what the user sees today**, **what they'll see after**, and **the test that pins the behavior**.

### 5.1 Flow f1 — Single-turn (no tools)

**Today.** No tool events → no polish → no PRESENTATION_UPDATED. `MODEL_DELTA` and `FINAL_RESPONSE` events have no `presentation` field. Card: just the assistant message bubble.

**After.** Identical. Polish removal has zero observable effect on this flow.

**Test:** existing assertion that `MODEL_DELTA` envelopes have `presentation=None`.

### 5.2 Flow f2 — Multi-turn with built-in tool

**Today.** Each `TOOL_CALL` and `TOOL_RESULT` produces:

- A preliminary envelope with title `"Working on <tool>"` (or `"Checked source"` for results) and a humanized minimal body.
- A background polish call with `agent_intent_hint = recent_assistant_text` → after up to 1.5s, a `PRESENTATION_UPDATED` patch with a more contextual title/summary.

UI flicker: card lands in <50ms with generic title, replaced after 1.5s with the polished version.

**After.** Each `TOOL_CALL` and `TOOL_RESULT` produces a single envelope with the title from (in order): registered `ToolDisplayTemplate` → agent-supplied `_display_title` (when the agent chose to fill it) → minimal envelope. No flicker.

For built-in tools that have a registered `ToolDisplayTemplate`, titles are higher-quality than today's polish (they're authored, deterministic). For built-ins that don't, the agent fills `_display_*` on calls where it matters; fallback for the rest is the humanized tool name.

**Test:** new integration test that registers a tool with `display=ToolDisplayTemplate(title_template="Searching {query}")`, drives a `TOOL_CALL` event with `payload={"tool_name": "search", "query": "linear tickets"}`, asserts envelope's `presentation.title == "Searching linear tickets"`. Plus a polish-removed test that asserts no `PRESENTATION_UPDATED` envelope is appended.

### 5.3 Flow f3 — SSE resume

**Today.** Replay-after-disconnect returns every persisted envelope, including any `PRESENTATION_UPDATED` patches. The FE merges patches into the original presentation per [`chatModel/presentation.ts`](../../../../apps/frontend/src/features/chat/chatModel/presentation.ts).

**After.** New runs don't emit `PRESENTATION_UPDATED`. Old persisted runs still have these envelopes; the FE merge logic still works on them. Replay equivalence is preserved.

**Test:** existing SSE replay tests pass unmodified. Plus a new test that drives a tool call, replays the event stream, asserts no `PRESENTATION_UPDATED` envelope is in the replay.

### 5.4 Flow f4 — Cancellation

**Today.** Cancel can race with in-flight polish. The producer handles this via `_pending_enrichment` map cancellation. On worker shutdown, `flush_pending_enrichment` waits for outstanding polish tasks.

**After.** No async polish → no race → no flush. Cancel handler simplifies: it just emits `RUN_CANCELLED` and exits.

**Test:** modify the existing cancel test that asserts `flush_pending_enrichment` was called → assert it's not (the method no longer exists).

### 5.5 Flow f5 — Citations across MCP / subagent / web

**Today.** Each MCP `TOOL_CALL` and `TOOL_RESULT` triggers polish. With the agent's intent hint, polish generates titles like `"Searching Linear for ticket ABC-123"`. Citations themselves emit their own `SOURCE_INGESTED` events (no polish — not in `llm_eligible_event_types`).

**After.** Each MCP tool descriptor has a synthesized `display` template (e.g. `"Search Linear for {query}"`). The Tier-2 path renders this from the payload. For MCP tools where synthesis produces a generic title, the agent fills `_display_*`. Quality matches or exceeds today's polish for the common case (Linear, Notion, Slack tools have predictable name patterns that synthesise well).

**Test:** for each of the 13 catalog vendors, assert `synthesise_for_mcp(...)` returns a non-empty title. Snapshot test on representative tool names per vendor.

### 5.6 Flow f6 — Reasoning / "thinking"

**Today.** `REASONING_SUMMARY_DELTA` and `REASONING_SUMMARY` aren't in `llm_eligible_event_types` → no polish. They have their own deterministic presentation.

**After.** Identical. Out of scope.

### 5.7 Flow f7 — Adding an MCP server (catalog / JSON / custom)

**Today.** Install path doesn't touch presentation. After install, the next call's tool events get polished.

**After.** When the descriptor is built (during loader's `list_tools()`, before any chat call), `display` is synthesized. First chat call to the new server renders a sensible card immediately — no first-time polish miss.

**Test:** drive `BackendMcpProvider.list_tools()` against a fake MCP server that advertises `list_issues`, assert the returned descriptor has `display.title_template` populated and human-readable.

### 5.8 Flow f8 — MCP auth in-chat

**Today.** `MCP_AUTH_REQUIRED` is in `DeterministicTemplates.HANDLED` → no polish. Cards rendered from the deterministic template ("Connect Linear").

**After.** Identical. Out of scope.

### 5.9 Flow f9 — Usage / token metrics

**Today.** Polish LLM calls show up in OpenAI billing under `gpt-4.1-nano`. They aren't attributed to the run via `RuntimeRunUsageRecord` because they don't go through the run's model path.

**After.** Line item disappears from OpenAI billing. No retroactive change to historical usage rows.

**Test:** none required — observable in production OpenAI dashboard.

---

## 6 · Open questions / decisions

### 6.1 Should `synthetic=True` be a real field on `ToolDisplayTemplate`?

**Decision:** Yes. Add `synthetic: bool = False` to `ToolDisplayTemplate`. Synthesis paths (`from_tool_name`, `synthesise_for_mcp`) set it `True`. Tier 3 (`_display_*` from payload) wins **for either or both of `title` and `summary`** when the matched template was synthetic — author-written templates always beat the agent.

### 6.2 Wrap-everything vs wrap-only-synthetic

**Decided: wrap-everything.** Uniform schema shape, simpler agent mental model, ~30 input tokens × N tools per turn added to the system prompt (~$0.0024/turn worst case at Opus pricing). The latency win (no separate polish round-trip per event) far outweighs the token cost. Output-token bloat is bounded — most calls leave `_display_*` null based on the field description guidance.

### 6.3 What replaces the `agent_intent_hint` benefit?

The polish LLM had access to a 4-event rolling buffer of recent assistant text (`MODEL_DELTA` / `FINAL_RESPONSE`). This is what made polish summaries occasionally great ("Searching for ticket ABC-123 the user asked about").

**Replacement:** Tier 3 (`_display_*`). The agent has the entire conversation in its context when it produces tool args; if the deterministic title would be too generic, it fills `_display_*`. This is _strictly_ more context than the rolling buffer had.

**Decision:** drop `agent_intent_hint`. Don't add a different intent-injection mechanism.

### 6.4 Keep `PRESENTATION_UPDATED` envelope handling on the FE?

**Decision:** Keep for replay compatibility. The FE merge logic in [`chatModel/presentation.ts`](../../../../apps/frontend/src/features/chat/chatModel/presentation.ts) becomes a no-op for new runs (they never emit it), but old persisted runs still have these envelopes. Removing the FE handler would silently lose data on replay of old runs. **Optional cleanup:** delete the FE handler in 90 days (after retention rolls old runs out).

### 6.5 Keep the `RuntimeApiEventType.PRESENTATION_UPDATED` enum value?

**Decision:** Keep. Same reason as 6.4 — old persisted envelopes have `event_type="presentation_updated"`. Pydantic deserialization fails if the enum value is removed.

### 6.6 Should the `_intent_buffer` be removed even if a follow-up needs it?

**Decision:** Yes, remove. The buffer was specifically a polish-prompt input. If a future feature needs assistant-text rolling buffers, it should pull from the event store directly (already persisted). Don't keep dead state in the producer.

### 6.7 Order: wire-then-synthesize vs synthesize-then-wire?

**Decision:** Wire first (Phase 1), then synthesize (Phase 2). Wiring is risk-free and validates the lookup path. Synthesis without wiring would still trigger polish (because the lookup is None and `_resolve_tool_template` returns None even though the descriptor has a template).

### 6.8 Skill cards (`McpServerCard`)?

The PR-9.0 doc mentions `McpServerCard.display`. The current code has display-related fields on `ToolCard` and `McpToolDescriptor` but I didn't see one on `McpServerCard` specifically (different surface). **Out of scope** for this PRD; verify the field doesn't exist before adding it.

### 6.9 Subagent fleet display (`stream_subagents.py`)?

PR-9.0 §3.7 calls this out as out-of-scope. **Confirmed.** Subagent display flows separately and isn't polished. Don't fold in.

### 6.10 Truncation as a brevity strategy

**Decided: NO truncation, anywhere.** Trailing ellipsis on a tool card looks broken. We do not truncate `_display_title` or `_display_summary` in the producer (`[:80]` slicing removed), in Pydantic (no `max_length` on `_display_*` fields), or in the FE (no CSS line-clamp with ellipsis). Brevity is enforced **upstream** by the JSON-schema field description shown to the model. See §9 for the design.

The downstream consequence: if the agent occasionally emits a 200-char summary, the card grows by one line. That is strictly preferable to a card that ends in `…`.

### 6.11 Cross-event summaries

Already handled by the existing grouping mechanism — no extra work needed.

- Tool calls inside a subagent share `parent_task_id` ([envelope schema, runtime_api/schemas/events.py](../../src/runtime_api/schemas/events.py)) → FE groups them under the supervisor's call card.
- Repeated tool calls under one logical step share `group_key` (= `source_tool_call_id` / `call_id` / `approval_id`) → FE groups them as a stack under one parent card.
- Subagent fleet events (`SUBAGENT_FLEET_STARTED` / `SUBAGENT_FLEET_FINISHED`) bracket a multi-subagent operation.

So a query like _"Search Linear, Notion, web; have the research subagent cross-check Slack; compile risks"_ renders as one card per call, grouped under their respective parents. No "summarize across events" feature is needed because the grouping already gives the user a logical unit. **The Phase 6 / batched-polish-after-FINAL_RESPONSE idea from earlier discussion is dropped.**

---

## 7 · Cost / value

**Cost of doing this** (engineering): ~6-8 days end-to-end. Phase 1 (1 day) is risk-free wiring. Phases 2-3 (3-5 days) are the real work. Phase 4 (1 day) is deletion. Phase 5 (optional) is small.

**Cost saving** (production): rough estimate, assuming 20 tool events per active conversation × 1k conversations/day = 20k polish calls/day eliminated. At ~$0.0001/call, that's ~$2/day, or ~$700/year. Latency saving is the bigger win: card jitter window from "up to 1.5s" to zero.

**Cost saving** (operational): removes one external dependency (OpenAI for the polish path). Provider migration becomes one move instead of two. Removes a 1.5s timeout to investigate when something goes wrong.

**Cost saving** (cognitive): deletes ~280 LOC of polish machinery + cache + cancellation + intent buffer + structured output schema. New surface (`display_metadata.py`) is ~180 LOC of pure deterministic functions. Net deletion ~100 LOC.

**Risk surface:** the agent must produce reasonable `_display_*` strings when needed. Mitigated by deterministic templates handling the common case and by the field's `description` instructing the model to leave them None unless the deterministic template would be too generic.

---

## 8 · Brevity by design (no truncation)

The whole point of agent-supplied display copy is to give a per-call human-readable bit. Truncating it ruins the point — the user sees `"Looking up Q1 launch tickets in Lin…"` and the card reads as broken. We do not truncate. Brevity is enforced **before** the model writes the string, not after.

### 8.1 Three layers of upstream pressure

1. **Field `description` shown to the model in its tool block.** This is the load-bearing layer. The model follows examples better than rules, so the description ships concrete short examples:

   ```python
   _display_title: str | None = Field(
       default=None,
       alias="_display_title",
       description=(
           "Optional. A short noun phrase (~3-7 words) for the activity card title. "
           "NOT a full sentence. Use ONLY when the deterministic title would be too generic. "
           "Examples: 'Q1 launch risk tickets', 'Recent Slack mentions', 'External Q1 coverage'. "
           "Counter-examples (do NOT do this): 'Searching Linear for the user-requested...', "
           "'Looking through all the documents that...'"
       ),
   )
   _display_summary: str | None = Field(
       default=None,
       alias="_display_summary",
       description=(
           "Optional. ONE short clause (~10-15 words) for the activity card body. "
           "Why this specific call helps the current request, in plain English. "
           "NOT a description of what the tool does in general. "
           "Examples: 'Risk-tagged tickets opened in the launch quarter', "
           "'Posts that mention the launch in the past two weeks'. "
           "Leave null if the tool's deterministic title is already clear."
       ),
   )
   ```

2. **`extra="forbid"` on `_DisplayFields`.** The agent cannot invent extra `_display_*` keys (e.g. `_display_long_summary`) and have them silently work. Pydantic rejects extras at args validation; the tool call fails loudly during testing, never in production.

3. **Examples in agent system prompt (low priority follow-up).** If post-launch profiling shows the agent is verbose despite the field description, add 1-2 short examples of well-formed `_display_*` to the system prompt. Out of scope for v1.

### 8.2 What is **not** done

- **No `max_length` on `_display_*`.** Pydantic does not reject long strings. If the agent emits 500 chars, the field validates. The card grows by one row.
- **No `[:N]` slicing in the producer.** The values flow from `payload._display_*` into `RuntimeEventPresentation.{title, summary}` unchanged.
- **No CSS line-clamp / ellipsis on the card title or summary.** The card has a min-height; long content extends it. (Result preview rows are still capped to 5 by `PayloadProjector.MAX_ROWS` — that's structural, not text truncation.)

### 8.3 Existing `[:N]` slicing — what gets removed

These spots in [`presentation.py`](../../src/agent_runtime/api/presentation.py) and [`presentation_templates.py`](../../src/agent_runtime/api/presentation_templates.py) become removed-or-reframed:

| Location                                                                                                         | Today                                            | After                                                                                                  |
| ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| `PresentationGenerator._minimal_envelope` — `title[:80]`, `error_summary[:240]`, `humanized_tool[:80]`           | Defensive truncation on author-controlled text   | **Removed.** Author-controlled text doesn't need defending. Templates produce reasonable lengths.      |
| `PresentationGenerator._safe_text` — `text[:max_length]`                                                         | Hard truncation                                  | **Replaced** with sanitisation only (HTML-ish strip, large-result placeholder). No length cap.         |
| `PresentationGenerator._safe_json` — string truncation in LLM-prompt context (`value[:500]`, `str(value)[:300]`) | Cap on text fed to polish LLM prompt             | **Removed entirely** — no LLM prompt to feed (Phase 4 deletes the polish path).                        |
| `ToolTemplateRenderer.render` — `title[:80]`, `summary[:240]`, `primary_entity[:80]`                             | Defensive truncation on rendered template output | **Removed.** Templates are author-controlled.                                                          |
| `PayloadProjector._clamp(text, limit)` — `text[:limit]`                                                          | Per-cell clamp on preview rows                   | **Removed.** Preview rows are structural; widths are managed by FE column layout, not byte truncation. |
| `_safe_text` — `text[:180]` (in `PresentationGenerator._row_text`)                                               | Cap on row text fed to LLM context               | **Removed** — no LLM context.                                                                          |

### 8.4 The downstream consequence

If a model regression makes the agent verbose, the worst observable result is a card with a 3-line summary instead of 1. Compared to today's failure mode (polish times out, FE shows `"call_tool result"`), this is strictly better.

If we _do_ observe agents being verbose post-launch, the fix is to update the field `description` (1-line code change, ships to the next chat call automatically). No code path needs to be reworked.

---

## 9 · Out-of-scope / follow-ups

- **Default tool template authoring.** This PRD doesn't add `display=...` to specific default tools; it just enables the path. Authoring lives in whichever PRs add new default tools.
- **`McpServerCard.display`** if/when that surface gains presentation needs.
- **Per-vendor MCP override map.** If profiling shows the agent fills `_display_*` for >10% of MCP tool calls, consider a vendor-specific override map (e.g. `linear:list_issues → "Search Linear issues"`). Not blocking.
- **Subagent fleet card display** ([`stream_subagents.py`](../../src/runtime_worker/stream_subagents.py)). Same pattern, separate path, separate PRD.
- **Per-tool-load LLM (Option A in PR-9.0)** — install-time synthesis with caching. Only consider if the deterministic synthesis quality proves insufficient post-launch.
- **Frontend cleanup** of `PRESENTATION_UPDATED` merge logic after 90-day retention window.

---

_This PRD is the implementation plan for [refactor-audit.md §1.1](../architecture/refactor-audit.md). Each phase ships as its own PR with the listed tests. Update this document as findings emerge during implementation._
