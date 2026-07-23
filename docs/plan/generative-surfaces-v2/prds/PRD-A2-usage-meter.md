# PRD-A2 — UsageMeter seam + store

Every LLM call the product makes — main run loop, subagents, surface-spec shaping, later
shape requests — must be metered and attributed to {org, user, conversation, run,
purpose} **before any new v2 feature lands** (FR-G1–G3, ../01-problem-and-requirements.md
§2G; ../02-sdr.md §8). This PR establishes the seam: one recording port (`UsageMeter`), a
`usage.recorded` ledger event behind `SURFACES_V2`, per-call `user_id`/`surface_id`
attribution columns, per-attempt metering of the currently-unmetered spec-generation
path, rollup-query coverage, and a gate test that fails if anyone constructs a model
client outside the seam. No UI, no facade changes, no pricing tables.

## Implementer brief

You are working in a fresh git worktree branched off `main` of the `enterprise-search`
monorepo. All code changes are inside `services/ai-backend/` plus one migration pair and
this doc. Run `make setup` once from the repo root if `services/ai-backend/.venv` does not
exist. This service owns its own venv — never use another service's.

Test commands (the only components touched):

```bash
cd services/ai-backend && .venv/bin/python -m pytest                     # full suite
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/observability/
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_worker/test_per_call_usage.py
python tools/check_migration_manifest.py           # from repo root; checks migrations MANIFEST.lock (CI gate)
python tools/check_migration_manifest.py --write   # regenerate MANIFEST.lock after adding a migration
```

Read these files first (paths relative to repo root; ai-backend paths shortened to `src/` = `services/ai-backend/src/`):

1. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 event vocabulary (authoritative), §8 usage design.
2. `src/agent_runtime/observability/token_usage.py` — `NormalizedTokenUsage`, extractor registry: reuse, never reimplement.
3. `src/agent_runtime/observability/attribution.py` — `Purpose` StrEnum (12 values), `UsageAttributionContext` invariants.
4. `src/agent_runtime/observability/usage_recorder.py` — `UsageRecorder` protocol, `PostgresUsageRecorder`, `InMemoryUsageRecorder`, fail-soft discipline.
5. `src/agent_runtime/persistence/records/telemetry.py` — `RuntimeModelCallUsageRecord` (~62): the per-call row you extend.
6. `src/runtime_worker/run_metrics.py` — `AssistantRunMetrics`, `PerCallTokenAccumulator` (per-`message.id` slots, max-merge, `mark_completed`).
7. `src/runtime_worker/streaming_executor.py` — `_maybe_emit_model_call_completed` (~429): the emission site you piggyback.
8. `src/runtime_worker/handlers/run.py` — ctor (~162), `_record_run_usage` (~828), `_build_surface_generation_scheduler` (~1569), `_stream_runtime` (~1409).
9. `src/agent_runtime/capabilities/surfaces/generator.py` — `SpecCompletionPort`, `SpecCompletionResult`, `SurfaceSpecGenerator.generate`/`_meter`, `LangChainSpecCompletion`, `build_surface_generation_scheduler` (~1137).
10. `src/agent_runtime/execution/deep_agent_builder.py` — `build_chat_model` (~355), `build_chat_model_from_id` (~457), `build_embeddings_model` (~545); the sole `init_chat_model`/`init_embeddings` call sites (~450/~571) — the funnel the gate test pins.
11. `src/agent_runtime/settings.py` — `_EnvFields` + `RuntimeExecutionSettings`: the flag pattern to copy.
12. `src/runtime_api/schemas/common.py` + `events.py` — `RuntimeApiEventType`; projector allow-list precedent `_surface_spec_generated_payload`.
13. `services/ai-backend/CLAUDE.md` + `services/ai-backend/tests/CLAUDE.md` — binding engineering + test rules.

## Context

Generative Surfaces v2 makes an agent's work on real SaaS tools visible as live artifact
surfaces on a per-run canvas, with fail-closed staged writes and one append-only Work
Ledger from which receipts, sources, and usage totals are folded (../02-sdr.md §2–§6).
Requirement §2G (../01-problem-and-requirements.md) makes usage attribution a launch FR:
tokens (not dollars) stored per call, attributed to user/conversation/run (+surface where
applicable), queryable at those levels; the Settings UI is explicitly out (FR-G4).

This PR is Wave A (foundation, no user-visible change), after PRD-A1 (ledger vocabulary +
contracts), before PRD-A3 (ledger emission + SurfaceStore). Per the program's standard
DoD, every later PRD that adds an LLM call site must route it through this seam — so it
lands before B3/B4 shaping work.

Reality check (the SDR §2 table understates this): ai-backend already has a tested usage
pipeline — streaming per-call capture (`AssistantRunMetrics`/`PerCallTokenAccumulator`),
the `runtime_model_call_usage` row (baseline DDL line 468), daily rollup tables, and a
facade-proxied `/v1/usage` family (`backend_facade/app.py:1170,1185`). Genuinely missing,
delivered here: (a) spec generation records nothing durable (OTel counters only); (b) no
`usage.recorded` ledger event; (c) the per-call row lacks `user_id`/`surface_id`;
(d) nothing enforces the seam. Extend what exists; do not rebuild.

## Interfaces consumed / exposed

**Consumed (from PRD-A1 and existing code):**

- PRD-A1: the `usage.recorded` event-type string constant in
  `packages/service-contracts` (py) and the `UsageRecord` contract in
  `packages/api-types`; the `v` payload-field convention (`"v": 1`). Wire value is the
  SDR §5 name `"usage.recorded"`. VERIFY AT IMPL: import the constant from
  `copilot_service_contracts` if A1 has merged; if running in parallel, define the
  literal in `RuntimeApiEventType` and reconcile before merge.
- Existing: `UsageRecorder` protocol + impls (`observability/usage_recorder.py`);
  `RuntimeRunHandler.usage_recorder` (default `PostgresUsageRecorder`, run.py:244);
  `RuntimeEventProducer.append_api_event` (`agent_runtime/api/events.py`);
  `TokenUsageExtractorRegistry` (`observability/token_usage.py`); `RunRecord.user_id`
  (`runtime_api/schemas/runs.py:347`, in `RunRecord` at :341); `MigrationRunner`
  (`agent_runtime/persistence/schema/migrate.py`).

**Exposed (later PRDs depend on these — do not rename):**

- `UsageMeter` + `MeteredModelInvocation` in NEW
  `src/agent_runtime/observability/usage_meter.py` — B3/B4 call it; the standard-DoD
  "new LLM call sites go through the UsageMeter seam" refers to this class.
- NEW `Purpose.VIEW_SHAPING = "view_shaping"` / `Purpose.SHAPE_REQUEST = "shape_request"`
  enum members — B3/B4 pass them.
- `RuntimeExecutionSettings.surfaces_v2` (env `SURFACES_V2`) — A3+ reuse this flag.
- `usage.recorded` events on the run stream — A3's UsageTotals fold, E3's endpoints.
- `user_id`/`surface_id` columns on `runtime_model_call_usage` — E3 rollups read them.
- Seam-gate test `tests/unit/test_llm_seam_gate.py` — every later PRD keeps it green.

## Design

### D1 — The seam, stated precisely

The SDR §8 phrase "one `MeteredModelInvocation` wrapper" is implemented as two enforced
halves, because wrapping the shared `BaseChatModel` would double-count the streaming path
(usage is chunk-accumulated per `message.id` with field-wise-max merge, `token_usage.py`):

1. **Construction seam (exists, now test-enforced):** every model is constructed via
   `build_chat_model` / `build_chat_model_from_id` / `build_embeddings_model` in
   `deep_agent_builder.py`; the pre-commit AST guard `tools/check_llm_provider_imports.py`
   bans direct provider imports; this PR adds an in-suite gate test (D7).
2. **Recording seam (new):** one port, `UsageMeter`, through which every usage
   observation flows to (a) the row store via the existing `UsageRecorder` and (b) the
   ledger as `usage.recorded` when `SURFACES_V2` is on. The streaming accumulator and
   the non-streamed callers are feeders into this one port.

Record this refinement in ../02-sdr.md §8 (standard DoD).

### D2 — Flag

`src/agent_runtime/settings.py`: `_EnvFields.SURFACES_V2 = "SURFACES_V2"` (bare name,
like `DATABASE_URL`); `RuntimeExecutionSettings.surfaces_v2: bool = False`; parsed in
`RuntimeSettings.load` next to `enable_local_models`
(`_s(v, E.SURFACES_V2, "false").lower() in _truthy`). Flag off ⇒ no new events,
byte-identical event stream. Rows/columns/purposes are NOT flag-gated (additive,
invisible — the future UI must need no backfill, FR-G4).

### D3 — `usage.recorded` event (SDR §5 verbatim)

`runtime_api/schemas/common.py`: add `USAGE_RECORDED = "usage.recorded"` to
`RuntimeApiEventType` (value pinned to the A1 constant, see Interfaces).

`runtime_api/schemas/events.py` (`RuntimeEventPresentationProjector`): a
`payload_for_event` branch → NEW `_usage_recorded_payload` allow-list keeping exactly
`v`, `purpose`, `model`, `tokens_in`, `tokens_out`, `surface_id` (keys in the module's
`_Fields` class); an `activity_kind_for` branch → `RuntimeActivityKind.EVENT`; no
display-title branch. Never add tenant ids to the envelope (`org_id` stays draft-only).
VERIFY AT IMPL: which visibility the projector defaults assign to `usage.recorded` and
that replay/SSE carry it.

Payload shape (SDR §5 verbatim; `purpose` is the closed 4-value ledger vocabulary, not
the 12-value store enum):

```json
{
  "v": 1,
  "purpose": "run|subagent|view_shaping|shape_request",
  "model": "<provider>:<model_name>",
  "tokens_in": 123,
  "tokens_out": 45,
  "surface_id": "optional"
}
```

### D4 — `UsageMeter` + `MeteredModelInvocation` (NEW `agent_runtime/observability/usage_meter.py`)

```python
class LedgerPurpose(StrEnum):          # SDR §5 closed vocabulary — NEW
    RUN = "run"; SUBAGENT = "subagent"
    VIEW_SHAPING = "view_shaping"; SHAPE_REQUEST = "shape_request"

class UsageMeter:                      # NEW — the recording port
    # Writes the usage row (query index); when surfaces_v2, also emits
    # usage.recorded (ledger truth). Fail-soft: never raises into the caller.
    def __init__(self, *, recorder: UsageRecorder,
                 emit_event: Callable[[JsonObject], Awaitable[None]] | None,
                 surfaces_v2: bool) -> None: ...
    async def record_call(self, record: RuntimeModelCallUsageRecord,
                          *, pricing_at: datetime) -> None: ...
    @classmethod
    def ledger_purpose_for(cls, purpose: str) -> LedgerPurpose | None: ...

class MeteredModelInvocation:          # NEW — adapter for non-streamed callers
    # Bound to one run's attribution; builds the per-call record from reported
    # token counts (SpecCompletionResult-style) and feeds UsageMeter per attempt.
    def __init__(self, *, meter: UsageMeter, run: RunRecord, purpose: Purpose) -> None: ...
    async def record_attempt(self, *, model_id: str, input_tokens: int | None,
                             output_tokens: int | None, duration_ms: int,
                             surface_id: str | None = None) -> None: ...
```

Purpose→ledger mapping (class-level table, exhaustive over `Purpose`): `main` /
`tool_planning` / `tool_interpretation` / `context_compression` → `run`;
`subagent_work` → `subagent`; the two new members map to themselves; all others
(`todo_extraction`, `library_*`, `memory_*`, `palette_ranking`) → `None` = row only, no
ledger event (background jobs are not part of the run's canvas story). Add the two NEW
`Purpose` members in `attribution.py` (no attribution-field implications —
`_purpose_invariants` unchanged). Do NOT build a `UsageAttributionContext` for
spec-generation calls (its invariants are stream-shaped); `MeteredModelInvocation`
builds the record directly. `emit_event` failures are logged (structured,
`safe_message`, no content) and swallowed — usage must never break a run (same
discipline as `PostgresUsageRecorder`). Signature confirmed against
`observability/usage_recorder.py:56`: `async record_call(record: RuntimeModelCallUsageRecord,
*, pricing_at: datetime) -> UsageRecordingResult` (matches the sketch).

**Field derivation (`record_attempt`).** `MeteredModelInvocation` is bound to one
`run: RunRecord` (fields confirmed at `runtime_api/schemas/runs.py:341`), from which it
copies the record's required non-null attribution fields: `org_id=run.org_id`,
`run_id=run.run_id`, `conversation_id=run.conversation_id`, `user_id=run.user_id`,
`trace_id=run.trace_id`. The per-call `model_id` argument is
`SpecCompletionResult.model` — the shaping model (`SURFACE_SPEC_MODEL`), NOT the run's
main model — and is split into the record's separate `model_provider` / `model_name`
columns via `SurfaceModelConfigFactory.from_id(model_id)`
(`agent_runtime/execution/deep_agent_builder.py:503`, function-level import mirroring the
existing pattern at `generator.py:1158`; this is not an `init_chat_model` reference, so the
D7 seam gate stays green). Remaining record fields: `purpose=self._purpose`
(`Purpose.VIEW_SHAPING` for specgen), `surface_id` from the arg, `input_tokens` /
`output_tokens` = the reported counts or `0` when `None`, `duration_ms` from the arg,
`created_at=now`. It then calls `await self._meter.record_call(record,
pricing_at=record.created_at)`.

**Event payload (`record_call`).** `UsageMeter.record_call` always writes the row via the
injected `UsageRecorder`. Then, only when `surfaces_v2` is on **and**
`UsageMeter.ledger_purpose_for(record.purpose)` is not `None`, it builds the
`usage.recorded` payload from the row it just wrote — `{v: 1, purpose: <ledger_purpose>,
model: f"{record.model_provider}:{record.model_name}", tokens_in: record.input_tokens,
tokens_out: record.output_tokens, surface_id: record.surface_id}` (omit `surface_id` when
`None`) — and hands it to `emit_event(payload)`. The D5b closure binds `run`,
`source=StreamEventSource.MODEL`, and `event_type=RuntimeApiEventType.USAGE_RECORDED`, so
only the payload varies; the projector's `_usage_recorded_payload` allow-list re-filters it
on append (D3). Keys live in the module's `_Fields`/`Keys` classes, never inline strings.

### D5 — Wiring the three call families

**(a) Main loop + subagents (streamed).** Rows already flow via
`_record_run_usage` → leave that path. Two changes:

- `run_metrics.py` `model_call_usage_records(run, *, trace_id)` (~321): stamp
  `user_id=run.user_id` on every built record (`surface_id` stays `None` here).
- Ledger emission piggybacks `StreamingExecutor._maybe_emit_model_call_completed`, which
  already fires exactly once per usage-bearing `message.id` (`mark_completed` dedupe).
  In the same guarded block, after the `MODEL_CALL_COMPLETED` append: if
  `surfaces_v2_enabled` and `UsageMeter.ledger_purpose_for(slot.purpose)` is not `None`,
  append `usage.recorded` via `event_producer.append_api_event(run=run,
source=StreamEventSource.MODEL, event_type=RuntimeApiEventType.USAGE_RECORDED,
payload={...})` with `model=f"{run.model_provider}:{run.model_name}"`,
  `tokens_in=slot.usage.input_tokens`, `tokens_out=slot.usage.output_tokens`. Thread a
  new `surfaces_v2_enabled: bool = False` kwarg on `StreamingExecutor.run`, passed from
  `_stream_runtime` as `self.settings.execution.surfaces_v2` (pattern: the
  `delta_coalesce_*` kwargs, run.py:1438); both helper call sites share the hook.
  Emission is mid-run, so always before the terminal event — never emit from
  `_record_run_usage` (it runs AFTER `run_termination.terminate`; SSE would miss it).
  Confirmed: the non-streaming branch (run.py:411–425, `metrics.record_usage_from`) writes
  rows later via `_record_run_usage` but emits no `MODEL_CALL_COMPLETED` (only the streaming
  path via `StreamingExecutor` does); leave it row-only and note that in the SDR §8 update.

**(b) Spec generation (non-streamed, currently unmetered).** In
`RuntimeRunHandler._build_surface_generation_scheduler` (run.py:1570), build a bound
meter and thread it through:

```python
meter = UsageMeter(recorder=self.usage_recorder,
                   emit_event=<closure over self.event_producer.append_api_event, like _emit>,
                   surfaces_v2=self.settings.execution.surfaces_v2)
invocation = MeteredModelInvocation(meter=meter, run=run, purpose=Purpose.VIEW_SHAPING)
```

`build_surface_generation_scheduler(...)` gains optional kwarg
`usage_meter: MeteredModelInvocation | None = None`, passed to `SurfaceSpecGenerator`.
In `SurfaceSpecGenerator.generate` (attempt loop, generator.py:653–685), immediately after
the existing sync `_meter(...)` call (generator.py:670) and before the
`if outcome.spec is not None: return` at generator.py:680, guard on both the injected meter
and a present result, then record:

```python
if usage_meter is not None and outcome.result is not None:
    await usage_meter.record_attempt(
        model_id=outcome.result.model,           # SpecCompletionResult.model (generator.py:159)
        input_tokens=outcome.result.input_tokens,
        output_tokens=outcome.result.output_tokens,
        duration_ms=duration_ms,                 # already computed at generator.py:668
    )
```

This is a NEW async call next to (not inside) `_meter` (`_meter` at generator.py:749 is
sync; recording is async). It runs on every attempt, including the successful one, because
it precedes the early `return outcome.spec` — that is what makes retries count (DoD). A
model-error attempt has `outcome.result is None` (generator.py:704) and is skipped (no
model to attribute); an attempt that produced a result but reported `None` tokens records
zeros (see D4 field derivation) — the row is still written. Confirmed: `generate`'s attempt loop (generator.py:652–685)
computes `duration_ms` per attempt and passes `outcome.result` (the `SpecCompletionResult`
on `_AttemptOutcome`) into `_meter(...)` at generator.py:670 — both are reachable there.

**(c) Shape requests (B4, future).** Nothing wired now; B4 constructs
`MeteredModelInvocation(purpose=Purpose.SHAPE_REQUEST)` with a `surface_id` per call —
the enum member + mapping land here so B4 is one line.

### D6 — Store changes

`RuntimeModelCallUsageRecord` (telemetry.py): add `user_id: str | None = None` and
`surface_id: str | None = None` (nullable — pre-migration rows exist; `schema_version`
stays 1, additive).

Migration (postgres only; conventions in `migrate.py`): NEW
`services/ai-backend/migrations/0002_usage_call_attribution.sql` — `ALTER TABLE
runtime_model_call_usage ADD COLUMN user_id text; ADD COLUMN surface_id text; CREATE
INDEX idx_runtime_model_call_usage_org_user_created ON runtime_model_call_usage (org_id,
user_id, created_at);` (name is new — the baseline already has
`idx_runtime_model_call_usage_org_run` / `_org_connector_created`) — plus sibling
`0002_usage_call_attribution.rollback.sql` (drop index + both columns), then regenerate
`migrations/MANIFEST.lock` via `python tools/check_migration_manifest.py --write` (CI
refuses drift).

Adapters: extend the postgres INSERT column list
(`runtime_adapters/postgres/runtime_api_store.py` ~2537); VERIFY AT IMPL the
`SELECT * FROM runtime_model_call_usage` read paths (~3107/3115/3342) tolerate the new
keys (record fields have defaults, so they should). File adapter (~2431,
`_Tables.MODEL_CALL_USAGE` JSONL via `model_dump(mode="json")`) and in-memory adapter
need no change — new fields flow automatically.

### D7 — Rollups + seam gate

Rollups: `UsageQueryService` (`agent_runtime/api/usage_service.py`) already provides
per-user (`rollup_user_rows`), per-purpose (`rollup_purpose_rows` — purpose is a string
dimension, so the new purposes flow through with zero code), per-run
(`PersistencePort.query_run_usage`, `agent_runtime/api/ports.py:718`) and per-conversation
(`PersistencePort.query_top_conversations` → `UsageConversationAggregateRecord`,
`agent_runtime/api/ports.py:742`; facade `/v1/usage/me/conversations`). No new endpoints (facade exposure is E3): this PR adds
tests proving totals for a fixture including the new purposes, fixing any exposed gap
in-place.

Seam gate: NEW `services/ai-backend/tests/unit/test_llm_seam_gate.py` — AST-walks every
`.py` under `src/`, failing if `init_chat_model`/`init_embeddings` is referenced outside
`agent_runtime/execution/deep_agent_builder.py`, or if any
`langchain_openai`/`langchain_anthropic`/`langchain_google_genai`/`anthropic`/`openai`
import appears anywhere (mirrors `tools/check_llm_provider_imports.py`; no escape marker
— ai-backend has no legitimate exceptions). Helpers live inside the test class
(tests-CLAUDE mixin rule).

## Implementation plan

1. Settings flag: `src/agent_runtime/settings.py` (`_EnvFields`,
   `RuntimeExecutionSettings`, `load`). Purposes:
   `src/agent_runtime/observability/attribution.py` (+`VIEW_SHAPING`, `SHAPE_REQUEST`).
2. NEW `src/agent_runtime/observability/usage_meter.py` (`LedgerPurpose`, `UsageMeter`,
   `MeteredModelInvocation`, payload `_Fields`).
3. Event type + projector: `src/runtime_api/schemas/common.py`,
   `src/runtime_api/schemas/events.py`.
4. Record + migration: `src/agent_runtime/persistence/records/telemetry.py`; NEW
   `migrations/0002_usage_call_attribution.sql` + `.rollback.sql`; regen
   `migrations/MANIFEST.lock`; INSERT list in
   `src/runtime_adapters/postgres/runtime_api_store.py`.
5. Streaming wiring: `src/runtime_worker/run_metrics.py` (user_id stamp),
   `src/runtime_worker/streaming_executor.py` (flag kwarg + emission),
   `src/runtime_worker/handlers/run.py` (`_stream_runtime` passes flag).
6. Specgen wiring: `src/agent_runtime/capabilities/surfaces/generator.py`
   (`SurfaceSpecGenerator` + `build_surface_generation_scheduler` kwargs, attempt-loop
   recording), `src/runtime_worker/handlers/run.py`
   (`_build_surface_generation_scheduler` builds the bound meter).
7. Seam gate test + remaining tests (below); SDR §8 note in
   `docs/plan/generative-surfaces-v2/02-sdr.md`.

## Test plan

All under `services/ai-backend/`, run with `.venv/bin/python -m pytest <path>`. Fakes,
no network, no live LLM (`tests/CLAUDE.md`); inject `InMemoryUsageRecorder` and assert on
`.calls` (existing convention); async tests are plain `async def`.

- NEW `tests/unit/agent_runtime/observability/test_usage_meter.py` —
  `test_ledger_purpose_mapping_is_exhaustive_over_purpose_enum` ·
  `test_record_call_writes_row_via_recorder` · `test_flag_off_emits_no_event`
  (adversarial: even with emitter wired) ·
  `test_background_purpose_writes_row_but_no_event` (todo_extraction) ·
  `test_payload_contains_exactly_sdr_fields` ·
  `test_emitter_failure_is_swallowed_and_logged`.
- NEW `tests/unit/agent_runtime/surfaces/test_specgen_usage_metering.py` — injected fake
  `SpecCompletionPort` failing schema validation once then succeeding:
  `test_retried_attempt_records_per_attempt` (two records, correct tokens each — the DoD
  fake-completion test) · `test_purpose_is_view_shaping` ·
  `test_none_token_result_records_zeros` · `test_no_meter_injected_keeps_generation_working`.
- NEW `tests/unit/runtime_worker/test_usage_recorded_events.py` — drive
  `StreamingExecutor.run` with fake usage-bearing chunks (patterns:
  `test_per_call_usage.py` + `test_stream_events.py`'s `RecordingEventProducer`):
  `test_flag_on_appends_usage_recorded_after_model_call_completed` ·
  `test_flag_off_event_stream_byte_identical` (snapshot of appended kwargs) ·
  `test_subagent_chunk_maps_to_purpose_subagent` ·
  `test_duplicate_usage_chunk_emits_once` (dedupe via `mark_completed`).
- EXTEND `tests/unit/runtime_worker/test_per_call_usage.py` — rows carry
  `user_id == run.user_id`.
- NEW `tests/unit/agent_runtime/api/test_usage_rollups_v2.py` — seed
  `InMemoryRuntimeApiStore` and the file adapter (tmp root) with call/run rows across
  purposes incl. `view_shaping`: `test_rollup_totals_equal_sum_of_rows_in_memory` ·
  `test_rollup_totals_equal_sum_of_rows_file_store` ·
  `test_purpose_rollup_has_view_shaping_bucket` · per-run + per-conversation totals.
- NEW `tests/integration/persistence/test_usage_rollup_v2_pg.py` — live-PG twin of the
  seeded-fixture rollup assertion (conventions from
  `tests/integration/persistence/test_rls_isolation.py`); with the file test this closes
  the "both adapters" DoD.
- NEW `tests/unit/test_llm_seam_gate.py` — `test_init_chat_model_only_in_funnel` ·
  `test_no_direct_provider_imports` · a canary asserting the gate fails on a planted
  fixture string (so it can't rot silently).
- Migration: apply/rollback via existing
  `tests/unit/agent_runtime/persistence/test_migration_runner.py` patterns; CI's
  manifest check covers `MANIFEST.lock`.

Live-smoke script (DoD item 1):

1. `make dev` from repo root with `SURFACES_V2=1`, `SURFACE_SPEC_MODEL=<cheap model id>`,
   real provider key in `services/ai-backend/.env`.
2. `export TOKEN=$(make dev-bearer)`; create a conversation + run via facade `:8200`
   (recipes: `docs/dev-testing.md`) with a prompt triggering at least one tool call
   (surface specgen) and one subagent delegation.
3. `curl -H "Authorization: Bearer $TOKEN"
http://127.0.0.1:8200/v1/agent/runs/<run_id>/events` — `usage.recorded` events
   present before the terminal event, purposes `run` + `subagent` + `view_shaping`,
   payload fields exactly per D3.
4. `curl .../v1/usage/me` and `.../v1/usage/me/conversations` via facade — totals
   nonzero and consistent with step 3; per-call rows carry the caller's `user_id`
   (inspect the postgres table or `<RUNTIME_FILE_STORE_ROOT>/state/model_call_usage.jsonl`).
5. Re-run with `SURFACES_V2` unset — new run's `/events` has no `usage.recorded`
   (flag-off byte-identical); rows still written.

## Definition of done

From 03-prds.md PRD-A2 (binding minimums, never weakened):

- [ ] A live run records usage rows for main-loop + subagent + shaping calls with correct
      {user, conversation, run, purpose} attribution — proven by live-smoke steps 2–4 and
      the extended `test_per_call_usage.py` + `test_specgen_usage_metering.py`.
- [ ] Retried shaping attempts record per-attempt (asserted in a fake-completion test) —
      `test_retried_attempt_records_per_attempt`.
- [ ] Rollup query totals equal the sum of rows in a seeded fixture (both adapters) —
      `test_usage_rollups_v2.py` (file) + `test_usage_rollup_v2_pg.py` (postgres).
- [ ] Grep-gate test fails if `init_chat_model`/completion construction appears outside
      the seam — `tests/unit/test_llm_seam_gate.py` incl. the planted-fixture canary.

Standard DoD:

- [ ] `cd services/ai-backend && .venv/bin/python -m pytest` green; no TS component
      touched (nothing to typecheck/build beyond CI defaults).
- [ ] Flags off ⇒ byte-identical behavior — `test_flag_off_event_stream_byte_identical` + live-smoke step 5.
- [ ] No service-boundary violations (all changes in ai-backend + its migrations; no
      cross-`src/` imports; no facade/app changes).
- [ ] New LLM call sites go through the UsageMeter seam — none added here; the seam +
      gate test now enforce it for every later PRD.
- [ ] Docs: ../02-sdr.md §8 updated with the D1 two-half seam refinement and the
      non-streaming row-only note.

(No UI in this PRD ⇒ design-parity DoD not applicable.)

## Out of scope

- Facade `/v1/usage/*` additions and any Settings/usage UI (FR-G4; E3 owns endpoints).
- Pricing tables / dollar computation (query-time concern; `ModelPricingCatalog` untouched).
- Unifying the three usage-read implementations (`token_usage.py` extractors,
  `LangChainSpecCompletion._usage`, `todo_extractor._UsageExtractor`) — tracked
  follow-up, do not refactor here.
- The embed route / todo / proposal extractors (already write rows with explicit
  purposes; untouched); ledger events for the non-streaming run path (row-only, D5a).
- `shape_request` call wiring (B4) and any `SURFACES_V2` behavior beyond usage events.

## Guardrails

- **Service boundaries:** apps → facade only; ai-backend never imports another
  component's `src/`; backend owns policy/OAuth — untouched here; cross-language
  constants live in `packages/service-contracts`, never copied strings.
- **Flag-off byte-identical:** with `SURFACES_V2` unset, the persisted event stream is
  exactly today's (snapshot-tested); schema/enum additions invisible to flag-off flows.
- **ai-backend rules** (`services/ai-backend/CLAUDE.md`): no module-level helper
  functions; no inline string keys (`_Fields`/`Keys` classes); Pydantic at every
  boundary; typed errors with safe public messages; content never logged at INFO+
  (token counts and ids only). **Test rules** (`tests/CLAUDE.md`): fakes/mixins, no
  network, no live LLM in unit tests; assert typed errors and safe messages.
- **Metering discipline:** per-call usage merges by field-wise max per `message.id` —
  never sum chunks (cumulative providers double-count); Anthropic gross-input
  normalization stays in the extractors; `pricing_at` anchor remains `completed_at`;
  usage/pricing failures are fail-soft; usage keyed per attempt (real spend).
- **Migrations:** `NNNN_topic.sql` + `.rollback.sql` + regenerated `MANIFEST.lock`
  (forward+rollback hashed with `\x00` separator); next ai-backend number is `0002`;
  prod applies migrations as a separate deploy step
  (`RUNTIME_MIGRATIONS_AUTO_APPLY=false`) — a pure additive ALTER is safe for that.
- **Do not:** add a second model-construction site; wrap the shared `BaseChatModel`
  per-call (subagents inherit the supervisor's instance — a wrapper double-counts the
  stream); emit events for runs that don't exist or after the terminal event.

## Open questions

- **`view_shaping` surface attribution.** D5b records spec-generation usage with
  `surface_id=None`, because `SurfaceSpecGenerator.generate` runs on a tool-output _shape_
  and does not carry the concrete `surface_id` the derived view will get. The SDR §5
  `usage.recorded` schema makes `surface_id` optional, and the DoD attribution set is
  `{user, conversation, run, purpose}` (surface "where applicable"), so `None` is
  in-contract. But `shape_request` (B4) _does_ carry a per-call `surface_id`. Decision
  for review: leave `view_shaping` surface-less (accepting that per-surface shaping cost
  is only queryable for B4 shape-requests), or plumb the scheduler's surface id into
  `generate(...)`/`record_attempt(surface_id=...)` so both shaping purposes attribute to a
  surface uniformly. Not resolvable from the SDR alone; deferring the plumb is the current
  default and does not block this PR.
