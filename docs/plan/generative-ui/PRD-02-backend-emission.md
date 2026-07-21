# PRD-02 — Backend surface emission + builtin curated specs (Wave 1)

**Goal:** the #1 blocker. Make ai-backend attach `surface` envelopes (with `surface_uri`) to tool results and draft updates, fed by a builtin library of curated SurfaceSpecs for catalog connectors. After this PR, the event stream carries everything the FE needs; nothing on the FE changes yet.

**Depends on:** PRD-01 (contract types frozen). **Scope: `services/ai-backend` only.**

## Scope — files

| File                                                                     | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/agent_runtime/capabilities/surfaces/projector.py`                   | NEW — `SurfaceProjector` (pure domain, no I/O): `resolve(server_name, tool_name, output: Mapping) -> SurfaceEnvelope \| None`. Ladder: builtin spec → injected `SurfaceSpecStorePort` (port defined here as a Protocol; only the in-memory impl in this PRD) → `None` spec ⇒ envelope with `state.data` only (tier-3 renderable). Builds `surface_uri` per the D3 grammar; derives the id segment from common id fields (`id`, `key`, `identifier`, `number`) else a stable hash of the call_id |
| `src/agent_runtime/capabilities/surfaces/builtin_specs/*.json`           | NEW — curated specs, one file per `(server, tool)`, validated at import by `validate_surface_spec`. Minimum set (12): linear `get_issue`(record) + `list_issues`(table); github `get_issue`(record) + `list_issues`(table) + `list_pull_requests`(table); notion `get_page`(doc); asana `list_tasks`(table); sentry `list_issues`(table); atlassian/jira `get_issue`(record) + `search_issues`(table); intercom `list_conversations`(timeline); zapier passthrough omitted (no stable shape)    |
| `src/agent_runtime/capabilities/surfaces/builtin.py`                     | NEW — loads + validates the JSON dir once at import; exposes `lookup(server, tool)`. A bad builtin file must fail tests, not runtime                                                                                                                                                                                                                                                                                                                                                            |
| `src/agent_runtime/capabilities/mcp/middleware/call_tool.py`             | EXTEND — after result construction (same place the citation hint is annotated), call `SurfaceProjector.resolve(...)` on non-error results with dict output; attach the envelope into the tool_result event payload under key `surface` AND mirror `surface_uri` at payload top level (the FE projector keys off top-level `payload.surface_uri` — verified in `eventProjector.ts:510`). Best-effort: any exception logs and skips (a surface must never fail a tool call)                       |
| `src/agent_runtime/capabilities/backends/draft_backend.py`               | EXTEND — `make_event_emitter` payload gains `surface_uri = f"message://draft/{draft_id}"` + a `surface` envelope: archetype `message`, `state.data` = {to, subject, sections/body}, `diff.changes` = section-level before/after when a prior version exists                                                                                                                                                                                                                                     |
| `src/agent_runtime/...` config module (follow existing env-flag pattern) | EXTEND — `RUNTIME_SURFACE_EMISSION` env flag, default `true`; when false, projector short-circuits to `None`                                                                                                                                                                                                                                                                                                                                                                                    |

## Behavior (normative)

- Only `tool_result` (non-error) and `draft_updated` events gain surfaces. Never `model_delta`/`final_response`.
- Output that is not a Mapping (str/None) ⇒ no envelope.
- List-shaped outputs with a known spec map to `table`/`board` archetypes with `items_path`; without a spec, the envelope still ships (`state.data` only) so tier-3 renders.
- The envelope's `state.data` is the **redacted** tool output (reuse whatever redaction the tool_result payload already went through — do not introduce a second redaction path).
- `surface_uri` is stable across events for the same logical resource (same server+tool+id ⇒ same URI), so the FE projector merges rather than forking tabs.

## Acceptance criteria

1. Unit: `CallMcpTool` fake-provider test asserts a linear `get_issue` result event payload contains top-level `surface_uri == "record://linear/get_issue/<id>"` and a `surface.state.spec` matching the builtin fixture; a tool with no builtin spec still gets `surface_uri` + `state.data` and NO spec; an `isError` result gets neither.
2. Unit: draft flow test asserts `DRAFT_UPDATED` carries `message://draft/<id>` + diff changes on v2.
3. Import-time validation test: corrupt a builtin fixture in-test ⇒ loader raises with the file name in the message.
4. `RUNTIME_SURFACE_EMISSION=false` test: payloads unchanged from today (byte-compatible snapshot).
5. Full ai-backend unit suite green (`.venv/bin/python -m pytest tests/unit`).

## Non-goals / guardrails

- NO LLM calls, NO generation, NO store adapters beyond in-memory (PRD-07/08).
- Do not touch `runtime_api` beyond what PRD-01 already merged; do not add event types.
- Do not modify FE packages. Do not emit `presentation_updated` (dead event).
- Keep `SurfaceProjector` free of transport/store imports except the injected Protocol (mirrors the ports discipline in `agent_runtime/persistence`).
