# Decomp — `agent_runtime/api/presentation.py`

Source: [services/ai-backend/src/agent_runtime/api/presentation.py](../../../services/ai-backend/src/agent_runtime/api/presentation.py) — **776 LOC, L.** Single dataclass `PresentationGenerator` plus three module-level type aliases. Owns the **4-step resolution chain** for UI card metadata: deterministic templates → tool author templates → payload projector → minimal envelope. The LLM is a polish-on-top layer; the synchronous envelope is always usable so cards never render empty.

## A. Top-level structure

### Module shell (lines 1–60)

| Symbol                         | Lines | Purpose                                                                                   |
| ------------------------------ | ----: | ----------------------------------------------------------------------------------------- |
| Module docstring               |  1–21 | Documents the 4-step resolution chain + LLM enrichment as best-effort polish.             |
| Type alias `JsonObject`        |    57 | `dict[str, object]`.                                                                      |
| Type alias `LlmPresenter`      |    58 | `Callable[[str], object \| Awaitable[object]]` — test-injection seam for prompt → output. |
| Type alias `ToolDisplayLookup` |    59 | `Callable[[str], ToolDisplayTemplate \| None]` — tool-name → template resolver.           |

### Dataclass `PresentationGenerator` (62–776)

| Symbol                                                            |   Lines | Purpose                                                                                                                                               |
| ----------------------------------------------------------------- | ------: | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Field `presentation_settings`                                     |      77 | Pinned model + timeout for the LLM path.                                                                                                              |
| Field `llm_factory`                                               |      78 | Builds the small chat model; defaults to `build_chat_model`.                                                                                          |
| Field `presenter`                                                 |      79 | Test seam — when set, replaces the structured-output LLM path.                                                                                        |
| Field `tool_display_lookup`                                       |      80 | Optional resolver from tool name → `ToolDisplayTemplate`.                                                                                             |
| Field `cache`                                                     |      81 | LLM-result cache keyed by `(run, event_type, call_id, approval_id, status)`.                                                                          |
| Field `_cached_model`                                             |      82 | Lazy-built chat model (single instance per generator).                                                                                                |
| Class attribute `llm_eligible_event_types`                        |   87–94 | Frozen set of event types eligible for LLM polish: `PROGRESS`, `TOOL_CALL`, `TOOL_CALL_STARTED`, `TOOL_RESULT`.                                       |
| Class attribute `_RESULT_EVENT_TYPES`                             | 686–691 | Frozen set: `TOOL_RESULT`, `TOOL_CALL_COMPLETED`.                                                                                                     |
| `presentation_for_event(...)`                                     |  96–130 | **Backwards-compat single-call entry point.** Builds preliminary, optionally enriches via LLM.                                                        |
| `preliminary_presentation_for_event(...)`                         | 132–186 | **The 4-step resolution chain.** Synchronous, no LLM.                                                                                                 |
| `event_eligible_for_enrichment(...)`                              | 188–208 | Predicate for whether to run LLM polish.                                                                                                              |
| `enrich_presentation_for_event(...)`                              | 210–266 | LLM-only path. Cached by `(run, event_type, call_id, approval_id, status)`.                                                                           |
| static `_deterministic_card_fields(...)`                          | 268–294 | Map `event_type` + payload `status` → `{status_label, kind}` deterministically. **Failure status wins over event_type.**                              |
| `_generate(context)`                                              | 296–336 | Run the LLM with structured output, with timeout + exception swallowing.                                                                              |
| `_structured_model(settings)`                                     | 338–357 | Lazy-build the chat model with `with_structured_output(PresentationOutput, method="json_schema", strict=True)`.                                       |
| classmethod `_prompt(context)`                                    | 359–367 | Build the LLM prompt with safe-event-context injection.                                                                                               |
| classmethod `_context(...)`                                       | 369–395 | Build the safe context dict the LLM sees.                                                                                                             |
| classmethod `_safe_json(value)`                                   | 397–425 | **Recursive PII/ID redaction**: drop secret-ish keys, drop ID-bearing keys, truncate long strings, compress lists to first 6 items.                   |
| classmethod `_display_facts(payload)`                             | 427–447 | Extract `primary_entity`, `action`, `status`, `read_only`, `risk_level`, `message_hint`.                                                              |
| classmethod `_connector_display_name(payload)`                    | 449–465 | Resolve connector name from payload's `display_name` / `primary_entity` / `loaded_server.server_card.{display_name,name}` / `server_name`.            |
| classmethod `_action_display_name(payload)`                       | 467–484 | Strip the connector prefix off `tool_name`, drop noise tokens (`mcp`, `tool`, `call`), join first 4 words.                                            |
| classmethod `_server_card(payload)`                               | 486–498 | Walk `payload.loaded_server.server_card` or `payload.output.loaded_server.server_card`.                                                               |
| classmethod `_result_preview(payload)`                            | 500–521 | Build up to 4 preview rows from extracted result rows: `{title, subtitle, url, badge}`.                                                               |
| classmethod `_rows_from_payload(payload)`                         | 523–551 | Extract result rows from `output` or `payload`. Handles nested `results`/`items`/`sources` lists, MCP-style `content[].text` JSON, plain-string JSON. |
| classmethod `_rows_from_text(text)`                               | 553–562 | Parse a JSON string into rows (list or `{results}`/`{items}`/`{sources}`).                                                                            |
| static `_parse_json_value(value)`                                 | 564–571 | Safe `json.loads` with passthrough on decode failure.                                                                                                 |
| classmethod `_row_text(row, keys)`                                | 573–579 | First non-empty matching string, safety-cleaned.                                                                                                      |
| static `_safe_text(value, max_length)`                            | 581–586 | Strip `<>`, collapse whitespace, replace large-result paths, cap length.                                                                              |
| classmethod `_humanize_identifier(value)`                         | 588–600 | Strip `mcp_` prefix + `_com`/`_io`/`_app` suffix; split on `-_`; title-case words.                                                                    |
| static `_secretish(key)`                                          | 602–605 | True if key contains `token` / `secret` / `password` / `key`.                                                                                         |
| static `_group_key(payload, timeline_fields)`                     | 607–616 | Pick first non-empty of `source_tool_call_id` / `call_id` / `approval_id`; fallback to `span_id`.                                                     |
| static `_validated(value)`                                        | 618–627 | `RuntimeEventPresentation.model_validate` + dump + `_without_raw_protocol_terms`. Returns `None` on validation failure.                               |
| classmethod `_without_raw_protocol_terms(value)`                  | 629–644 | Recursively scrub generated text fields.                                                                                                              |
| static `_clean_generated_text(value)`                             | 646–651 | Replace `mcp_` and `_com` substrings + collapse whitespace.                                                                                           |
| `_resolve_tool_template(payload)`                                 | 653–665 | Call `tool_display_lookup(tool_name)`; swallow exceptions to a warning log.                                                                           |
| classmethod `_with_deterministic_fields(validated, *, group_key)` | 667–684 | Backfill `group_key` and `debug_label="Tool details"` on LLM output.                                                                                  |
| `_minimal_envelope(...)`                                          | 693–763 | **Step 4 of the chain.** Build envelope from humanized tool name + status; project preview rows on success.                                           |
| static `_payload_status(payload)`                                 | 765–768 | Lowercase string status or `""`.                                                                                                                      |
| static `_first_text(source, keys)`                                | 770–776 | First non-empty matching string.                                                                                                                      |

## B. Feature inventory

| Domain                                              | Symbols                                                                                                                          |  LOC |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ---: |
| **Public entry points (resolution chain)**          | `presentation_for_event`, `preliminary_presentation_for_event`, `enrich_presentation_for_event`, `event_eligible_for_enrichment` | ~115 |
| **LLM generation**                                  | `_generate`, `_structured_model`, `_prompt`, `_context`                                                                          |  ~70 |
| **Safe-payload redaction (PII / IDs / secrets)**    | `_safe_json`, `_safe_text`, `_secretish`, `_clean_generated_text`, `_humanize_identifier`, `_without_raw_protocol_terms`         |  ~75 |
| **Display facts + connector/action naming**         | `_display_facts`, `_connector_display_name`, `_action_display_name`, `_server_card`                                              |  ~75 |
| **Result-preview row extraction**                   | `_result_preview`, `_rows_from_payload`, `_rows_from_text`, `_parse_json_value`, `_row_text`                                     |  ~80 |
| **Validation + group_key + deterministic backfill** | `_validated`, `_group_key`, `_with_deterministic_fields`, `_deterministic_card_fields`                                           |  ~50 |
| **Minimal envelope (step 4 fallback)**              | `_minimal_envelope`, `_payload_status`, `_first_text`                                                                            |  ~80 |
| **Tool template resolution**                        | `_resolve_tool_template`                                                                                                         |  ~15 |

## C. Functional spec per domain

### The 4-step resolution chain (`preliminary_presentation_for_event`, 132–186)

Inputs: `event_type`, `payload`, `metadata`, `timeline_fields`. Output: `JsonObject | None`.

1. **Caller-provided presentation** (147–149): if `metadata["presentation"]` is already a valid `RuntimeEventPresentation`, return it.
2. **Deterministic templates** (153–162): `DeterministicTemplates.render(event_type, payload, timeline_fields, group_key)` — used for approval / auth / error / tool_call_delta. Validated then returned if non-None.
3. **Tool author templates** (164–175): if `tool_display_lookup` resolves a `ToolDisplayTemplate`, render via `ToolTemplateRenderer.render(...)`.
4. **Minimal envelope** (180–186): only for `llm_eligible_event_types` — humanized tool name + status. Always includes `PayloadProjector.project` rows when applicable.

Returns `None` only for non-LLM-eligible event types with no template (e.g. `MODEL_DELTA`, heartbeats).

### LLM enrichment (`enrich_presentation_for_event`, 210–266)

**Cache key** (233–243): JSON of `{run_id, event_type.value, call_id, approval_id, status}`, sort_keys, default=str. Hit → return cached envelope.

**Generation flow** (247–266):

1. `_generate(context)` returns the LLM output (or `None` on timeout/failure).
2. Merge with `_deterministic_card_fields(event_type, payload)` to backfill `status_label` / `kind` deterministically (252–258).
3. `_validated(merged)` — Pydantic-validate against `RuntimeEventPresentation`; return None on schema miss.
4. `_with_deterministic_fields` backfills `group_key` + `debug_label`.
5. Cache + return.

**Failure modes — LLM** (321–331):

- `TimeoutError` / `asyncio.TimeoutError` → log warning, return `None`.
- Any other exception → log warning with stack trace, return `None`.
- Caller (`presentation_for_event`) falls back to `preliminary` so cards always render.

**Structured output** (338–357): `with_structured_output(PresentationOutput, method="json_schema", strict=True)`. Lazy-builds the model; `temperature=0`, `supports_streaming=False`.

### Safe-payload redaction (`_safe_json`, 397–425)

Recursive scrub:

- **Drop secret-ish keys**: any key containing `token`/`secret`/`password`/`key` (`_secretish`, 602–605).
- **Drop ID keys**: explicit denylist `{server_id, approval_id, action_id, call_id, source_tool_call_id, server_name, tool_name, native_interrupt_id}`.
- **Truncate strings**: 500 chars max for top-level strings; 300 for fallback `str(value)`.
- **Replace `/large_tool_results/` paths** with `"Large result saved for internal inspection."`.
- **Compress lists** to first 6 items.
- Pass through `int|float|bool|None`.

### Result-preview row extraction (`_result_preview`, 500–562)

Looks for rows in this order: `payload["output"]` → `payload`. Rows can be:

- a list directly,
- `{"results": [...]}` / `{"items": [...]}` / `{"sources": [...]}`,
- `{"content": [{"text": "...JSON..."}]}` (MCP shape),
- `{"text": "...JSON..."}` (plain text),
- a JSON string at any of the above.

Each row → `{title, subtitle?, url?, badge?}`:

- `title` from `title|name|summary|url|link` (first non-empty).
- `subtitle` from `snippet|description|content|status`, only if different from title.
- `url` from `url|link`, only if `http://` / `https://` prefix.
- `badge` from `source|type|status`, capped at 40 chars.

Up to 4 rows.

### Display facts (`_display_facts`, 427–447)

Extract a small JSON of safe display values:

- `primary_entity` ← `_connector_display_name(payload)`
- `action` ← `_action_display_name(payload)`
- `status` ← `payload.status`
- `read_only` ← `payload.read_only` (bool only)
- `risk_level` ← `payload.risk_level` (str only)
- `message_hint` ← truncated `payload.message`

### Group key (`_group_key`, 607–616)

Pick the first non-empty of: `source_tool_call_id` / `call_id` / `approval_id` from payload. Fallback to `span_id` from `timeline_fields`. Used to **group multiple events from the same tool call into one card**.

### Minimal envelope (`_minimal_envelope`, 693–763)

Status branching (716–742):

- `is_failed = status in TOOL_FAILURE_STATUSES or status == "error"` → `status_label="Failed"`, `kind="error"`. Title = `_ErrorMessage.for_code(error_code)[0]` or humanized tool. Summary = payload's `error_message` / `safe_message` or the typed-error template.
- `is_result` → `status_label="Done"`, `kind="result"`. Title = humanized tool or `"Checked source"`.
- Otherwise → `status_label="Running"`, `kind="progress"`. Title = humanized tool or `"Working on step"`.

Caps:

- Title 80 chars (745).
- Summary 240 chars (751).
- `primary_entity` 80 chars (755).

Result-preview projection skipped on failure (756–762): "error payloads typically don't carry preview-able rows, and the heuristics could surface noise."

### Validation (`_validated`, 618–627)

`RuntimeEventPresentation.model_validate(value)` → on success: `model_dump(mode="json", exclude_none=True)` + `_without_raw_protocol_terms`. On `ValidationError`: `None`. Means **any invalid envelope is dropped silently** — caller falls back to the next step in the chain.

## D. Bugs / edge cases / invariants

- **Cards never render empty** (1–17 docstring): the synchronous envelope always returns something usable; LLM is polish-on-top.
- **Failure status wins over event_type** (276–279, 717–733): `TOOL_RESULT with status=failed` is a terminal error, not a successful "Done". Centralised in `_deterministic_card_fields`.
- **Result-preview projector skipped on failure** (756–762): error payloads don't preview cleanly.
- **Pinned LLM model** (303): `RuntimePresentationSettings()` defaults to `gpt-4.1-nano`; `temperature=0`; non-streaming. Deterministic-ish output for caching to be effective.
- **Cache key ignores ID variance** (233–243): keyed on `run_id, event_type, call_id, approval_id, status` only — same logical event reuses cached output.
- **`_safe_json` aggressive ID/secret redaction** (404–414): explicit denylist of ID keys plus pattern-based secret-key check. **Anything new with a secret-shaped name should be evaluated against this list.**
- **`/large_tool_results/` path replacement** (420–421, 584–585, 648–649): paths into the large-result blob store are scrubbed in three places — payload, text, and LLM-generated text.
- **`mcp_` / `_com` substring scrub on LLM output** (650): post-processes LLM output to strip protocol-leak-y substrings the model might have copied from the prompt.
- **Tool-template lookup swallowed exceptions** (661–664): bad tool_display_lookup never breaks the resolution chain; logged and skipped.
- **LLM exceptions / timeouts swallowed** (321–331): same — fall back to preliminary.
- **`_validated` silently drops invalid envelopes** (624): caller can't distinguish "not validated" from "no card needed."
- **Group key fallback to `span_id`** (615–616): events without a payload-level identifier still group correctly via tracing span.
- **`_minimal_envelope` title cap of 80** (745) — frontend renders one-line; long titles would wrap or truncate.
- **Cached chat model** (341–357): single instance per `PresentationGenerator`; `_cached_model` field is `init=False` so it's never settable from constructor.
- **Action display strips connector prefix** (475–478): if connector is "GitHub" and tool is `github_search_repos`, the action becomes `search repos`. Token comparison is `lower()`+alphanumeric only.
- **Tool name token denylist** (482): drops `mcp`, `tool`, `call` from the action name.
- **`_safe_json` recursion depth** (415): unbounded — relies on payload depth being small in practice.
- **List truncation at 6** (418): only top-level item count; doesn't apply to nested lists' inner items.

## E. Hardcoded vs configurable

### Hardcoded

- LLM **system prompt** (309–315): "You write concise, plain-text UI card metadata for an enterprise assistant. Never include raw IDs, protocol names, JSON, markdown, or HTML."
- LLM **task prompt** template (361–367).
- Status maps:
  - Failure → `{status_label: "Failed", kind: "error"}` (285).
  - Success → `{status_label: "Done", kind: "result"}` (286–293).
  - Default → `{status_label: "Running", kind: "progress"}` (294, 740–741).
- Field names: `display_name`, `primary_entity`, `tool_name`, `loaded_server`, `server_card`, `server_name`, `output`, `results`, `items`, `sources`, `content`, `text`, `title`, `subtitle`, `url`, `link`, `snippet`, `description`, `content`, `source`, `type`, `status`, `error_code`, `error_message`, `safe_message`, etc. — all string-literal, not via a constant pool.
- ID-redaction denylist (404–413): explicit set.
- Secret pattern tokens (605): `token`/`secret`/`password`/`key`.
- Suffix denylist (594): `_com`, `_io`, `_app`.
- Action-token denylist (482): `mcp`, `tool`, `call`.
- Magic numbers:
  - 6 list-item compression cap (418)
  - 500 string truncation cap (422)
  - 300 fallback string truncation (425)
  - 180 row-text truncation (578, 446)
  - 240 summary cap (751)
  - 80 title / primary_entity cap (745, 755)
  - 40 badge cap (519)
  - 4 preview row cap (504)
  - 4 action word count (484)
- Default titles: `"Checked source"`, `"Working on step"`, `"Tool details"` (debug_label).
- "Large result saved for internal inspection." replacement string.

### Configurable

- `presentation_settings` (LLM model + timeout) — defaults to `RuntimePresentationSettings()`.
- `llm_factory` — defaults to `build_chat_model`.
- `presenter` — test seam.
- `tool_display_lookup` — None by default (no tool templates).
- `cache` — empty dict, dataclass-field-default.

### From settings (indirect)

- `settings.model_name` (345), `settings.timeout_seconds` (319, 347).

## F. External dependencies and coupling

### Internal `agent_runtime.*`

- `agent_runtime.api.presentation_templates` — `DeterministicTemplates`, `PayloadProjector`, `PresentationOutput`, `ToolTemplateRenderer`, `_ErrorMessage`. **Step 1 + Step 3 of the chain live there.**
- `agent_runtime.execution.tool_outcomes.TOOL_FAILURE_STATUSES` — failure-status set used for `is_failed` branch.
- `agent_runtime.capabilities.tools.cards.ToolDisplayTemplate` — type for tool author templates.
- `agent_runtime.execution.contracts.ModelConfig`, `StreamEventSource` — LLM config + event-source enum.
- `agent_runtime.execution.deep_agent_builder.build_chat_model` — default LLM factory.
- `agent_runtime.settings.RuntimePresentationSettings` — model + timeout.

### Internal `runtime_*`

- `runtime_api.schemas` — `RunRecord`, `RuntimeApiEventType`, `RuntimeEventPresentation`.

### Stdlib / third-party

- `langchain_core.language_models.chat_models.BaseChatModel` + `LanguageModelInput`.
- `langchain_core.messages.HumanMessage`, `SystemMessage`.
- `langchain_core.runnables.Runnable`.
- `pydantic.BaseModel`, `ValidationError`.
- `asyncio`, `inspect`, `json`, `logging`, `dataclasses.dataclass/field`, `typing.cast`.

## G. Suggested decomposition seams

The class has eight clearly-separable concerns. Cuts:

1. **`presentation_chain.py`** — public entry points + the resolution-chain orchestration (`presentation_for_event`, `preliminary_presentation_for_event`, `enrich_presentation_for_event`, `event_eligible_for_enrichment`, `_resolve_tool_template`, `_minimal_envelope`). ~150 LOC. The _only_ publicly-callable surface.
2. **`presentation_llm.py`** — `_generate`, `_structured_model`, `_prompt`, `_context`, `cache`, `_cached_model`, plus the `presenter` test seam. ~85 LOC. Self-contained LLM polish layer.
3. **`safe_payload.py`** — `_safe_json`, `_safe_text`, `_secretish`, `_humanize_identifier`, `_clean_generated_text`, `_without_raw_protocol_terms`, the ID-key denylist, the suffix denylist. ~85 LOC. Pure functions; reusable.
4. **`display_facts.py`** — `_display_facts`, `_connector_display_name`, `_action_display_name`, `_server_card`. ~50 LOC.
5. **`result_preview.py`** — `_result_preview`, `_rows_from_payload`, `_rows_from_text`, `_parse_json_value`, `_row_text`. ~80 LOC. Could be promoted to a public utility; the `PayloadProjector` in `presentation_templates.py` covers a similar concern and the two should likely merge.
6. **`status_classifier.py`** — `_deterministic_card_fields`, `_payload_status`, `_first_text`, the `_RESULT_EVENT_TYPES` set. ~30 LOC. Pure logic, used in two methods.
7. **`group_key.py`** + **`validation.py`** — small but cohesive. Could fold into 1 or 5.

The **resolution chain** (1) becomes the only stateful object; everything else can be a module of classmethods/static helpers. The current `dataclass` is mostly state-free (only `cache` and `_cached_model` are stateful) — splitting it doesn't lose much.

The duplicated rule "skip preview on failure" (756–762) and "preview row extraction" appear both here and in `PayloadProjector`. Merging the two would reduce drift risk.
