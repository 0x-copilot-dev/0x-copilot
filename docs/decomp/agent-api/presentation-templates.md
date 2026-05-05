# Decomp — `agent_runtime/api/presentation_templates.py`

Source: [services/ai-backend/src/agent_runtime/api/presentation_templates.py](../../../services/ai-backend/src/agent_runtime/api/presentation_templates.py) — **646 LOC, L.** Steps 1, 2, and 3 of the resolution chain (the deterministic ones — no LLM). Three rendering classes (`DeterministicTemplates`, `ToolTemplateRenderer`, `PayloadProjector`), four constant pools (`_StatusLabel`, `_Kind`, `_ErrorMessage`, `_Identifier`), and two Pydantic schemas (`PresentationOutput`, `PresentationPreviewRowOutput`).

## A. Top-level structure

| Symbol                                                  |   Lines | Purpose                                                                                                                                                   |
| ------------------------------------------------------- | ------: | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Module docstring                                        |    1–18 | Documents the 4-step resolution chain.                                                                                                                    |
| Type alias `JsonObject`                                 |      31 | `dict[str, object]`.                                                                                                                                      |
| `PresentationPreviewRowOutput(BaseModel)`               |   34–40 | Schema: `title` (1–120), `subtitle` (≤240), `url` (≤500), `badge` (≤40).                                                                                  |
| `PresentationOutput(BaseModel)`                         |   43–55 | LLM-fillable fields: `title` (1–80), `summary` (≤240), `primary_entity` (≤80), `action_label` (≤60), `result_preview: list[…]`.                           |
| `_StatusLabel`                                          |   58–62 | Constants: `RUNNING`, `WAITING ("Waiting for permission")`, `DONE`, `FAILED`.                                                                             |
| `_Kind`                                                 |   65–70 | Constants: `PROGRESS`, `RESULT`, `APPROVAL`, `AUTH`, `ERROR`.                                                                                             |
| `_ErrorMessage`                                         |  73–120 | **Static title/summary tuples per typed error code.** 9 codes + DEFAULT.                                                                                  |
| `_ErrorMessage.for_code(code)`                          | 115–120 | Case-insensitive lookup; `-` → `_` normalisation; falls through to DEFAULT.                                                                               |
| `_Identifier`                                           | 123–149 | Humanizer for snake/slug strings (drops `mcp_` prefix; drops `_com`/`_io`/`_app` suffix; title-cases).                                                    |
| `_Identifier.humanize(value)`                           | 129–149 | Returns `None` for non-strings or empty input.                                                                                                            |
| `DeterministicTemplates.HANDLED`                        | 159–168 | Frozen set of event types this class handles: `APPROVAL_RESOLVED`, `APPROVAL_REQUESTED`, `MCP_AUTH_REQUIRED`, `ERROR`, `RUN_FAILED`, `TOOL_CALL_DELTA`.   |
| `DeterministicTemplates.render(...)`                    | 170–191 | Dispatch on `event_type`.                                                                                                                                 |
| `_approval_resolved(payload, group_key)`                | 193–213 | "Permission granted"/"Permission denied" envelope based on `status in {approved, granted, allowed}`.                                                      |
| `_approval_requested(payload, group_key)`               | 215–239 | "Allow {tool}?" envelope; summary mentions `display_name`/`server_name` if present.                                                                       |
| `_mcp_auth_required(payload, group_key)`                | 241–257 | "Connect {entity}" envelope; entity from `display_name`/`server_name`/`"this app"`.                                                                       |
| `_run_failed(payload, group_key)`                       | 259–272 | Look up error code → static title/summary; envelope `kind=error`, `status_label=Failed`.                                                                  |
| `_error(payload, group_key)`                            | 274–285 | Same shape as `_run_failed`.                                                                                                                              |
| `_tool_call_delta(payload, timeline_fields, group_key)` | 287–309 | "Working on {tool}" running envelope; **only uses `payload.message`**, never `payload.delta` (which is raw streaming-arg JSON tokens).                    |
| static `_envelope(...)`                                 | 311–339 | Build the dict with mandatory `{title, status_label, kind, debug_label}` + optional `{summary, group_key, primary_entity, action_label, result_preview}`. |
| static `_text(value)`                                   | 341–346 | Stripped non-empty string or None.                                                                                                                        |
| static `_lower_text(value)`                             | 348–353 | Stripped lowercase non-empty string or None.                                                                                                              |
| classmethod `_first_text(payload, keys)`                | 355–363 | First non-empty string match.                                                                                                                             |
| `ToolTemplateRenderer._RESULT_EVENT_TYPES`              | 375–380 | `TOOL_RESULT`, `TOOL_CALL_COMPLETED`.                                                                                                                     |
| `ToolTemplateRenderer._START_EVENT_TYPES`               | 381–388 | `TOOL_CALL`, `TOOL_CALL_STARTED`, `TOOL_CALL_DELTA`, `PROGRESS`.                                                                                          |
| `ToolTemplateRenderer.render(...)`                      | 390–439 | Format title + summary against payload via `string.Formatter`; build envelope; project preview rows on result events.                                     |
| static `_safe_format(template, payload)`                | 441–467 | **Format-spec walker:** parse `template`, lookup each `{field}` in `payload`, return `None` if any field is missing/empty.                                |
| static `_safe_text(value)`                              | 469–474 | Stripped non-empty string or None.                                                                                                                        |
| `PayloadProjector.MAX_ROWS = 5`                         |     493 | Cap on preview row count.                                                                                                                                 |
| `PayloadProjector._CONTAINER_KEYS`                      | 494–502 | `results`, `items`, `rows`, `matches`, `documents`, `sources`, `output`.                                                                                  |
| `PayloadProjector._TITLE_KEYS`                          |     503 | `title`, `name`, `subject`, `filename`, `headline`.                                                                                                       |
| `PayloadProjector._SUBTITLE_KEYS`                       | 504–510 | `snippet`, `description`, `preview`, `summary`, `excerpt`.                                                                                                |
| `PayloadProjector._URL_KEYS`                            |     511 | `url`, `link`, `href`, `permalink`.                                                                                                                       |
| `PayloadProjector._BADGE_KEYS`                          |     512 | `source`, `connector`, `kind`, `type`.                                                                                                                    |
| `PayloadProjector.project(...)`                         | 514–527 | Try declared rows → fallback heuristic; cap at 5.                                                                                                         |
| `_declared_rows(payload, template)`                     | 529–540 | Walk `template.result_preview_path`.                                                                                                                      |
| `_heuristic_rows(payload)`                              | 542–567 | Try `payload["output"]` then `payload`; for each, look at top-level list or container-key list.                                                           |
| `_project_row(row, template)`                           | 569–589 | Map row → `{title, subtitle?, url?, badge?}`. Subtitle dropped if equal to title. URL must be `http(s)://`.                                               |
| `_field_value(row, declared, slot, fallback_keys)`      | 591–609 | Use declared key if mapped, else first non-empty fallback.                                                                                                |
| `_walk_path(value, path)`                               | 611–624 | Walk `"a.b.c"` path, parsing JSON strings on the way.                                                                                                     |
| static `_parse_value(value)`                            | 626–633 | `json.loads` with passthrough on failure.                                                                                                                 |
| static `_safe_text(value)`                              | 635–642 | Strip `<>`, collapse whitespace, **return None for `/large_tool_results/` paths**.                                                                        |
| static `_clamp(text, limit)`                            | 644–646 | Truncate to limit.                                                                                                                                        |

## B. Feature inventory

| Domain                                         | Symbols                                                 |  LOC |
| ---------------------------------------------- | ------------------------------------------------------- | ---: |
| **Pydantic schemas**                           | `PresentationPreviewRowOutput`, `PresentationOutput`    |  ~25 |
| **Constant pools**                             | `_StatusLabel`, `_Kind`, `_ErrorMessage`, `_Identifier` |  ~95 |
| **Deterministic templates (step 1)**           | `DeterministicTemplates` + 6 builders + 4 helpers       | ~210 |
| **Tool author templates (step 2)**             | `ToolTemplateRenderer` + `_safe_format` + helpers       | ~110 |
| **Payload projector (step 3 / row extractor)** | `PayloadProjector` + heuristics + clamps                | ~155 |

## C. Functional spec per domain

### `_ErrorMessage` — typed error → static UI copy

Codes (uppercase, `-` → `_`):

- `TIMEOUT` / `TOOL_TIMEOUT` / `TOOL_RUN_TIMEOUT` — three timeout variants with distinct copy.
- `PERMISSION_DENIED`
- `EXTERNAL_SERVICE_ERROR`
- `TOOL_EXCEPTION`
- `TOOL_RUN_ABANDONED`
- `TOOL_CANCELLED`
- `RUN_WORKER_LOST`
- `DEFAULT` ("Step failed", "Enterprise Search couldn't complete this step.")

`for_code(code)` (115–120) normalises `code.strip().upper().replace("-","_")` then `getattr` with default. **Adding a new error code = add a class attribute here.**

### `DeterministicTemplates` (step 1)

Six event types render fully without LLM:

| Event type                     | Title pattern                               | Status  | Kind     |
| ------------------------------ | ------------------------------------------- | ------- | -------- |
| `APPROVAL_RESOLVED (approved)` | "Permission granted"                        | DONE    | APPROVAL |
| `APPROVAL_RESOLVED (denied)`   | "Permission denied"                         | FAILED  | APPROVAL |
| `APPROVAL_REQUESTED`           | "Allow {tool}?"                             | WAITING | APPROVAL |
| `MCP_AUTH_REQUIRED`            | "Connect {entity}"                          | WAITING | AUTH     |
| `RUN_FAILED`                   | from error code                             | FAILED  | ERROR    |
| `ERROR`                        | from error code                             | FAILED  | ERROR    |
| `TOOL_CALL_DELTA`              | "Working on {tool}" or `display_title` hint | RUNNING | PROGRESS |

`_approval_resolved` keys off `status in {approved, granted, allowed}` — three synonyms for "yes" (line 199).

`_approval_requested` summary changes shape based on whether `display_name`/`server_name` is present (223–231).

`_tool_call_delta` deliberately uses **only** `payload.message`, NOT `payload.delta` (comment 298–299): "payload.delta is the raw streaming JSON-arg token (`{"`, `":`, `"}`, etc.) and is not user-readable."

### `ToolTemplateRenderer` (step 2)

**Two event-class buckets**:

- Result events (`TOOL_RESULT`, `TOOL_CALL_COMPLETED`) → use `template.result_title_template` / `result_summary_template` (falling back to plain `title_template`/`summary_template`); `kind=RESULT`, `status=DONE`.
- Start events (`TOOL_CALL`, `TOOL_CALL_STARTED`, `TOOL_CALL_DELTA`, `PROGRESS`) → use `template.title_template` / `summary_template`; `kind=PROGRESS`, `status=RUNNING`.

Other event types → `None` (caller falls through to next step).

**`_safe_format`** (441–467): Custom `string.Formatter` walker. For each `{field_name}`:

- `formatter.get_field(field_name, (), payload)` → catches `KeyError, IndexError, AttributeError, TypeError` → `None`.
- Empty string after strip → `None`.
- Allows nested attribute lookup (e.g. `{server.display_name}`) via Python's standard format-spec.

If ANY field is missing/empty, `_safe_format` returns `None` → caller falls back to step 3 or 4.

Also runs `PayloadProjector.project` for result events (435–438) so a tool's declared template still gets its result-preview body filled.

### `PayloadProjector` (step 3)

**MAX_ROWS = 5** (493).

Container keys (top-level fallback): `results, items, rows, matches, documents, sources, output` (494–502).

Field-name heuristics:

- title: `title, name, subject, filename, headline`
- subtitle: `snippet, description, preview, summary, excerpt`
- url: `url, link, href, permalink`
- badge: `source, connector, kind, type`

**Resolution order** (project, 514–527):

1. `_declared_rows(payload, template)` — walk `template.result_preview_path` (e.g. `"output.results"`).
2. Fallback `_heuristic_rows(payload)` — try `payload["output"]` then `payload` itself; flat list or container-key list.

**Per-row mapping** (`_project_row`, 569–589):

- title required (else row dropped).
- subtitle dropped if equal to title.
- url accepted only if `http://` / `https://` prefix.
- title clamped to 120 chars; subtitle 240; url 500; badge 40.

`_safe_text` (635–642) strips `<>`, collapses whitespace, **returns None for any string containing `/large_tool_results/`** — drops that row instead of leaking the blob path.

`_walk_path` (611–624) supports `"a.b.c"` dot-path; parses JSON strings as it walks (so a stringified JSON in a nested field is traversable).

## D. Bugs / edge cases / invariants

- **Empty card never returned** by deterministic builders — they always return a non-None envelope when `event_type` is in HANDLED.
- **Tool-name humanization fallback** in `_approval_resolved`/`_approval_requested`: `"this action"` / `"an action"` (198, 219).
- **`_run_failed` and `_error` are duplicate functions** (259–285) — same body. The dispatch at 185–188 routes both to identical builders. Note the `RUN_FAILED` case in `HANDLED` even though `ERROR` could cover it semantically.
- **`_tool_call_delta` strips `payload.delta`** (298–299): explicit comment that streaming-arg JSON tokens aren't user-readable.
- **Title/summary truncation** (302–303, 421, 427): 80 / 240 / 60 char caps applied per builder. Same caps as `presentation.py` `_minimal_envelope`.
- **`_safe_format` failure semantics** — ANY missing field → None. Tool template authors must ensure all their `{field}` placeholders exist in payloads, otherwise the tool template is silently skipped.
- **PayloadProjector returns `[]` for unrecognised payloads** — caller treats `[]` as "no body".
- **PayloadProjector skips `/large_tool_results/` rows** (640–641): defends against leaking the blob-store path through preview rows.
- **`_walk_path` parses JSON strings mid-walk** (618–619): a tool's `output` field can be a JSON-string and still be walked into. Defends against MCP-style tools that serialize structured payloads as strings.
- **Subtitle equality drop** (581): `subtitle != title` — prevents redundant body text.
- **URL scheme guard** (584): only `http(s)://`. Defends against `data://`, `file://`, etc.
- **`_first_text` skips empty-stripped values** (361): `_text` returns None for whitespace-only.
- **`_safe_text` strips `<` and `>` characters** (639): defends against HTML injection in card body when rendered by the FE.

## E. Hardcoded vs configurable

### Hardcoded

- All 9 typed error code → title/summary mappings (80–113).
- All status labels / kinds.
- Identifier prefix list `("mcp_",)` and suffix list `("_com", "_io", "_app")` (126–127).
- Title fallback strings: `"this action"`, `"an action"`, `"Working on step"`, `"this app"`.
- "Permission granted/denied" / "Allow {tool}?" / "Connect {entity}" / "Sign in to {entity}" copy.
- `"Tool details"` debug label (327).
- `MAX_ROWS = 5` (493).
- All container/title/subtitle/url/badge key lists.
- Truncation limits: 80/240/60/40/120/500.
- URL scheme allowlist: `("http://", "https://")`.
- HTML-strip characters: `<`, `>`.
- Large-results path marker: `"/large_tool_results/"`.

### Configurable

- `ToolDisplayTemplate` is the per-tool author input (passed to `ToolTemplateRenderer.render` and `PayloadProjector.project`).
- Tool authors can override row-field keys via `template.result_preview_row` dict.
- `template.result_preview_path` overrides heuristic row extraction.

## F. External dependencies and coupling

### Internal

- `agent_runtime.capabilities.tools.cards.ToolDisplayTemplate` — only external internal dependency.
- `runtime_api.schemas.RuntimeApiEventType` — event type enum.

### Stdlib / third-party

- `pydantic.BaseModel`, `Field`.
- `json`, `collections.abc.Mapping`, `string.Formatter`.

This file has **almost no coupling** — it's pure functions of `(event_type, payload, optional ToolDisplayTemplate)`. That's why it can sit cleanly under `presentation.py` as the deterministic engine.

## G. Suggested decomposition seams

The file already has 5 well-separated classes. Cuts:

1. **`presentation_schemas.py`** — `PresentationOutput`, `PresentationPreviewRowOutput`. ~25 LOC. The wire-format Pydantic types — could move to `runtime_api/schemas/` next to `RuntimeEventPresentation`.
2. **`presentation_constants.py`** — `_StatusLabel`, `_Kind`, `_Identifier`. ~50 LOC. Pure constants + identifier humanizer (which is duplicated in `presentation.py`).
3. **`error_messages.py`** — `_ErrorMessage` + the per-code static-tuple table. ~50 LOC. Easy to grow as new error codes appear.
4. **`deterministic_templates.py`** — `DeterministicTemplates` + its 6 builders + 4 helpers + `_envelope`. ~200 LOC. Self-contained step 1.
5. **`tool_template_renderer.py`** — `ToolTemplateRenderer` + `_safe_format`. ~75 LOC. Step 2.
6. **`payload_projector.py`** — `PayloadProjector` + all its helpers. ~155 LOC. Step 3.

The **`_Identifier.humanize`** method is duplicated in [presentation.py](presentation.md) (`_humanize_identifier`, lines 588–600). That dup is the single most obvious refactor — extract into a shared helper module.

The **`_safe_text` / `_safe_format` rules** (HTML-strip, large-results detection, length clamping) are also duplicated between this file and `presentation.py`. The shared seam is the safe-payload-redaction helpers extracted in [presentation.md](presentation.md) `safe_payload.py`.

The `result_preview` row generation is split between `PayloadProjector.project` here and `_result_preview` in `presentation.py`. **Two parallel implementations** of "extract preview rows from a payload." The two should likely merge into the projector (which has more sophisticated declared-template support).
