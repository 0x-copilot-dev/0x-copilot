# Refactor PRD — LiteLLM provider streaming (P20 / Phase 5)

**Status:** Draft — pre-investigation
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §3](../architecture/refactor-audit.md#3-library-replacements) (provider stream adapters row)
**Roadmap slot:** [P20](00-roadmap.md#phase-5--major-library-swaps--structural-shifts)
**Pre-requisite:** verification spike (see §2) — without it the recommendation is unsafe.
**Risk:** High — touches the hot path of every model turn.

---

## 1. Problem

The runtime carries three custom provider stream adapters:

- [`agent_runtime/execution/providers/anthropic_stream_adapter.py`](../../src/agent_runtime/execution/providers/anthropic_stream_adapter.py) — handles thinking modes, streaming.
- [`agent_runtime/execution/providers/openai_responses_stream_adapter.py`](../../src/agent_runtime/execution/providers/openai_responses_stream_adapter.py) — Responses API + reasoning summary.
- [`agent_runtime/execution/providers/gemini_grounding_stream_adapter.py`](../../src/agent_runtime/execution/providers/gemini_grounding_stream_adapter.py) — grounding metadata + search.

Plus a `provider_kwargs.py` module that resolves workspace + user policy model kwargs (training opt-out, region routing, …). Plus a `CitationStreamPipeline` that taps each provider's stream and extracts citation references.

[LiteLLM](https://github.com/BerriAI/litellm) is the canonical multi-provider abstraction: one streaming API, one kwargs format, one usage-reporting format, automatic provider routing. The codebase already uses `init_chat_model` (per [refactor-audit §C5](../architecture/refactor-audit.md#3-library-replacements)) which routes through provider-specific wrappers — but the streaming layer is custom anyway. The redundancy is real.

### What's hard about replacing this

Reasoning streaming differs substantially per provider, and is a recent feature:

- **Anthropic** has `thinking_mode {ENABLED, ADAPTIVE}` + `display {OMITTED, SUMMARIZED}` (per [f6](../architecture/f6-thinking.puml)). Streams `thinking` blocks separately from content.
- **OpenAI Responses API** has `summary {AUTO, CONCISE, DETAILED}`. Streams `reasoning_summary` parts separately.
- **Gemini** has no native reasoning summary stream — `effort` is a kwarg and grounding metadata is inline.

If LiteLLM's streaming layer doesn't faithfully forward each provider's reasoning surface, replacing the adapters will silently drop user-visible behavior (REASONING_SUMMARY_DELTA / REASONING_SUMMARY events). This is the highest-risk subsystem in the audit for that reason.

### What this is NOT

- Not switching providers. Anthropic / OpenAI / Gemini remain.
- Not changing `RuntimeEventEnvelope` schema. Whatever LiteLLM streams in, we still emit our event types.
- Not removing reasoning support. If LiteLLM partially covers reasoning, the unsupported provider keeps its custom adapter; everything else moves to LiteLLM.
- Not P12 (pricing) — that's the pricing-catalog side. P20 is the streaming/call side. They land independently.

---

## 2. Verification required before approval — **blocker**

This PRD cannot be opened until the following spike completes. Without it, replacement is reckless.

### 2.1 Spike scope

Write a standalone test harness (outside `services/ai-backend/src/`, in a scratch branch) that uses LiteLLM's streaming `acompletion` against each provider with the exact kwargs ai-backend uses today. For each provider:

1. Compare the streamed events: do they include reasoning content? In which chunk type? With what termination semantics?
2. Compare usage metadata: input / output / cached_input / reasoning_tokens.
3. Compare error types raised vs the existing custom adapter's typed errors.
4. Compare grounding metadata (Gemini) and citation references (Anthropic + OpenAI's web tools, if used) — does LiteLLM forward provider-native citation data?

### 2.2 Spike verification matrix

| Behavior                                                                                                      | Anthropic              | OpenAI Responses | Gemini grounding | Outcome if any provider fails                                                      |
| ------------------------------------------------------------------------------------------------------------- | ---------------------- | ---------------- | ---------------- | ---------------------------------------------------------------------------------- |
| Streaming text chunks                                                                                         | Expected ✓ via LiteLLM | Expected ✓       | Expected ✓       | If any fails: this PR is dead for that provider                                    |
| `thinking_mode = ENABLED` produces a separately-typed reasoning chunk                                         | ?                      | n/a              | n/a              | If LiteLLM merges thinking into content: keep Anthropic adapter                    |
| `thinking_mode = ADAPTIVE` correctly toggles                                                                  | ?                      | n/a              | n/a              | Same                                                                               |
| `display = OMITTED` skips reasoning in client output but tokens still bill                                    | ?                      | n/a              | n/a              | Same                                                                               |
| OpenAI Responses API `summary = AUTO / CONCISE / DETAILED` streams summary parts                              | n/a                    | ?                | n/a              | Same                                                                               |
| Gemini grounding metadata (URLs, snippets) inline                                                             | n/a                    | n/a              | ?                | If absent: keep Gemini adapter; `CitationStreamPipeline` continues for Gemini only |
| Usage metadata fields: `input_tokens`, `output_tokens`, `cached_input_tokens`, `reasoning_tokens` all present | ?                      | ?                | ?                | Reasoning-token billing column depends on this; failure = keep adapter             |
| Error types map cleanly to `RuntimeErrorCode`                                                                 | ?                      | ?                | ?                | If chaotic: write a thin error mapper, do not give up                              |
| `workspace_model_kwargs` + `user_policy_model_kwargs` forwarded verbatim                                      | ?                      | ?                | ?                | If LiteLLM filters them: hybrid path needed                                        |
| Citation references in Anthropic streaming (citations API beta) preserved                                     | ?                      | n/a              | n/a              | If absent: keep Anthropic adapter for the citation path                            |

### 2.3 Spike output

A document — `services/ai-backend/docs/refactor/spikes/litellm-streaming-coverage.md` — that fills the matrix above with **`yes` / `no` / `partial` + notes** for every cell. This PRD's §3 and §6 are revised to match the spike output before any code change ships.

If three cells come back `no`: this PR becomes "use LiteLLM for non-reasoning calls only; keep all three adapters." If most cells come back `yes`: this PR proceeds at full scope.

---

## 3. Goal and non-goals

### Goal

Use LiteLLM for every provider call where it preserves all behaviors in §5. Retain custom adapters **only** for provider-specific surfaces LiteLLM cannot represent (typically: a specific reasoning-streaming variant). Drop the custom adapters that LiteLLM fully covers. Drop `provider_kwargs.py` if LiteLLM accepts the kwargs without translation.

### Non-goals

- Migrating to a different abstraction (LangChain's `init_chat_model` route, or any other).
- Changing model selection logic (`ModelConfigResolver` stays).
- Changing the citation pipeline architecture beyond what's necessary to feed it from LiteLLM streams.
- Adding new reasoning capabilities. Whatever exists today exists after; nothing new.

### Success criteria

- Spike output (§2) documented and reviewed.
- For each provider with **`yes`** across all relevant cells: custom adapter deleted.
- For each provider with any **`no`** or **`partial`**: custom adapter retained, with a docstring listing which behaviors it preserves that LiteLLM cannot.
- Same `StreamEvent` + `StreamEventSource` taxonomy emitted regardless of source (LiteLLM or custom adapter).
- `REASONING_SUMMARY_DELTA` / `REASONING_SUMMARY` events match pre-refactor byte-identical for a representative reasoning corpus.
- Usage metadata fields (`input_tokens`, `output_tokens`, `cached_input_tokens`, `reasoning_tokens`) populated correctly on every `MODEL_CALL_COMPLETED` event.
- All provider-specific tests pass; new tests added for the LiteLLM path (see §8).
- Citation extraction from provider grounding ([f5](../architecture/f5-citations.puml)) continues to feed `CitationLedger`.

---

## 4. Systems touched

**Pending spike.** Provisional inventory assumes full coverage.

### 4.1 Files possibly deleted

| File                                                                                                                                       | Condition                                                 |
| ------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| [`execution/providers/anthropic_stream_adapter.py`](../../src/agent_runtime/execution/providers/anthropic_stream_adapter.py)               | LiteLLM covers thinking modes + display + token reporting |
| [`execution/providers/openai_responses_stream_adapter.py`](../../src/agent_runtime/execution/providers/openai_responses_stream_adapter.py) | LiteLLM covers Responses API summary streaming            |
| [`execution/providers/gemini_grounding_stream_adapter.py`](../../src/agent_runtime/execution/providers/gemini_grounding_stream_adapter.py) | LiteLLM forwards grounding metadata                       |
| [`execution/provider_kwargs.py`](../../src/agent_runtime/execution/provider_kwargs.py)                                                     | LiteLLM accepts our kwargs format                         |

### 4.2 Files changed

| File                                                                                                                         | Change                                                                               |
| ---------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| [`agent_runtime/execution/factory.py`](../../src/agent_runtime/execution/factory.py)                                         | Resolve LiteLLM model string + kwargs; pass to Deep Agents builder                   |
| [`agent_runtime/execution/deep_agent_builder.py`](../../src/agent_runtime/execution/deep_agent_builder.py)                   | Configure LangGraph to use LiteLLM's `acompletion` for streaming                     |
| [`agent_runtime/execution/providers/citation_pipeline.py`](../../src/agent_runtime/execution/providers/citation_pipeline.py) | Adapt to consume from LiteLLM's chunk format (and/or retained custom adapter chunks) |
| Worker streaming pipeline (`stream_messages.py`, `stream_parts.py`)                                                          | Map LiteLLM chunks → `RuntimeEventEnvelope` events                                   |

### 4.3 Files added

| File                                                                     | Purpose                                                                                              |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `agent_runtime/execution/providers/litellm_router.py`                    | Thin facade: model string + kwargs → LiteLLM `acompletion` call                                      |
| `agent_runtime/execution/providers/chunk_adapter.py`                     | Maps LiteLLM stream chunks to internal `StreamEvent` (and similarly for any retained custom adapter) |
| `tests/unit/agent_runtime/execution/providers/test_litellm_streaming.py` | Per-provider streaming tests                                                                         |

---

## 5. Behaviors preserved

From [refactor-audit § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved). Each gets a pinned test.

### Anthropic

- `thinking_mode = ENABLED` produces a stream of thinking chunks distinguishable from content chunks; these become `REASONING_SUMMARY_DELTA` events.
- `thinking_mode = ADAPTIVE` toggles based on prompt; the same event types fire when thinking happens.
- `display = OMITTED` suppresses client-side `REASONING_SUMMARY_*` events but reasoning tokens still bill.
- `display = SUMMARIZED` emits the events normally.
- Citation references (if used) preserved.

### OpenAI Responses API

- `summary = AUTO / CONCISE / DETAILED` correctly forwards the summary mode kwarg.
- Reasoning summary parts arrive as separate stream chunks → `REASONING_SUMMARY_DELTA` events.
- Tool calls + function calls still parsed correctly.
- `store = False` continues to suppress server-side conversation storage where applicable.

### Gemini

- Grounding metadata (search URLs, snippets, source titles) feeds `CitationStreamPipeline` → `CitationLedger`.
- `effort` kwarg forwarded.
- Inline grounding citations still extracted.

### Usage metadata

- Every `MODEL_CALL_COMPLETED` event carries: `input_tokens`, `output_tokens`, `cached_input_tokens`, `reasoning_tokens` (provider-specific column when applicable).
- These feed `BudgetCharger.charge_run` per [f9](../architecture/f9-usage-metrics.puml). Cost stamped using active `ModelPricingRecord` (per [P12](00-roadmap.md#phase-3--library-replacements-independent) and the [pricing PRD](09-pricing-from-litellm.md) once written).

### Errors

- Provider-side errors map to typed `RuntimeErrorCode` exactly as the custom adapters do today.
- Stream interruption (provider-side) closes the stream cleanly; `RUN_FAILED` fires with the right error code.

### Citation pipeline

- `CitationStreamPipeline` continues to ingest both provider-grounding citations (Gemini) and Anthropic/OpenAI citation references (when present).
- Conversation-scoped ordinal namespace still works (per [P14 citations consolidation](00-roadmap.md#phase-4--targeted-decoupling)).

---

## 6. Phasing

Hybrid migration. Each provider migrates independently; failed-spike providers stay on custom adapters.

### Phase A — Spike

§2. **No production code changes.** Output: coverage matrix. Decide which providers migrate fully, partially, or not at all.

### Phase B — LiteLLM facade + chunk adapter

Add `litellm_router.py` and `chunk_adapter.py` behind a feature flag (`RUNTIME_USE_LITELLM_FOR_<PROVIDER>`). Both old and new paths in tree; flag selects per-provider. Unit tests cover both paths.

### Phase C — Per-provider cutover

For each provider with full coverage from §2:

1. Enable flag in staging.
2. Compare event streams from custom vs LiteLLM for a recorded fixture corpus (deterministic provider replay if possible; otherwise diff aggregate properties).
3. Enable in production behind staged rollout (10% / 50% / 100%).
4. Delete custom adapter once 100% rollout is stable for a week.

### Phase D — Retire `provider_kwargs.py`

Only if LiteLLM accepts our kwargs format cleanly. If LiteLLM filters or renames kwargs, keep a thin translation layer.

### Phase E — Verification

Latency benchmark on a tool-heavy turn (where each call goes through the new path). p99 must not regress > 5%. Cost-tracking diff (per turn): must not change (LiteLLM rounding, if any, can introduce drift — pin to integer micro-USD per [P12](00-roadmap.md#phase-3--library-replacements-independent)).

---

## 7. Risks

| Risk                                                                                                  | Severity | Mitigation                                                                                           |
| ----------------------------------------------------------------------------------------------------- | -------- | ---------------------------------------------------------------------------------------------------- |
| LiteLLM merges Anthropic thinking blocks into content stream → lose `REASONING_SUMMARY_*` events      | Critical | Spike catches it; if so, keep Anthropic adapter.                                                     |
| LiteLLM's OpenAI Responses API support lags the API itself                                            | Critical | Spike + pin LiteLLM version. Keep OpenAI adapter if lag is observed.                                 |
| Gemini grounding metadata not forwarded → citation pipeline silently drops sources                    | High     | Spike + `CitationStreamPipeline` integration test in §8.                                             |
| Usage metadata field names differ between LiteLLM and provider native → `BudgetCharger` undercounts   | High     | Spike captures full usage shape; chunk adapter normalizes; tests pin token counts byte-identical.    |
| LiteLLM error types don't map cleanly to `RuntimeErrorCode`                                           | Medium   | Add a typed error mapper in `chunk_adapter.py`.                                                      |
| `workspace_model_kwargs` / `user_policy_model_kwargs` semantics change                                | Medium   | Keep `provider_kwargs.py` until §6 Phase D confirms LiteLLM accepts the kwargs unchanged.            |
| Performance regression — LiteLLM adds per-chunk overhead                                              | Medium   | Phase E benchmark; if regress > 5%, profile, consider keeping the adapter for the affected provider. |
| Deep Agents integration with LiteLLM (vs the existing `init_chat_model` path) is non-trivial          | Medium   | Spike includes "can Deep Agents accept a `litellm.acompletion`-backed chat model?"                   |
| Provider-side API version drift (Anthropic citations API, OpenAI Responses) lands AFTER our migration | Medium   | Pin both LiteLLM version and provider SDK versions; subscribe to LiteLLM release notes.              |
| LiteLLM costs us a new dependency footprint (sub-deps, license)                                       | Low      | Verify dependency audit clean; license is MIT for LiteLLM core.                                      |

---

## 8. Unit testing requirements

Per [`docs/CLAUDE.md`](../CLAUDE.md). Pre-spike, write the test scaffolding; post-spike, write the assertions.

### Spike-output tests (per provider, per behavior in §5)

- **`test_anthropic_thinking_streaming.py`** — `thinking_mode=ENABLED` produces `REASONING_SUMMARY_DELTA` events with the same body shape as today; `display=OMITTED` skips emission but tokens still report.
- **`test_openai_responses_summary.py`** — `summary={AUTO,CONCISE,DETAILED}` each produces the expected stream of summary parts → events.
- **`test_gemini_grounding.py`** — grounding metadata flows to `CitationLedger`; `SOURCE_INGESTED` events fire.
- **`test_usage_metadata_fields.py`** — every provider returns the four token columns; `MODEL_CALL_COMPLETED.payload.usage` matches the schema.
- **`test_error_mapping.py`** — auth error / rate limit / context length / network drop each map to the right `RuntimeErrorCode`.
- **`test_citation_pipeline_litellm.py`** — `CitationStreamPipeline` consumes LiteLLM chunks and produces the same ledger state as consuming custom-adapter chunks.

### Regression — record/replay

- **`tests/fixtures/streams/<provider>/<scenario>.jsonl`** — recorded streaming fixtures from each provider, for: greeting (Flow 1), tool-heavy turn (Flow 2), reasoning turn (Flow 6), grounding turn (Flow 5).
- **`test_stream_replay_event_equivalence.py`** — replay each fixture through both adapters (where both exist); assert the emitted `RuntimeEventEnvelope` sequence is identical modulo timestamps.

---

## 9. Rollback plan

Per-provider feature flags `RUNTIME_USE_LITELLM_FOR_ANTHROPIC` / `_FOR_OPENAI` / `_FOR_GEMINI`. Flip the flag = rollback for that provider. Custom adapters stay in tree through Phase C; only deleted after staged rollout stable.

If post-deletion (Phase C end) a provider regression appears: `git revert` the deletion. Custom adapters are mechanical to restore.

---

## 10. Dependencies on other roadmap items

- **Independent of:** [P12 pricing-from-LiteLLM](09-pricing-from-litellm.md) — pricing and streaming are separate LiteLLM surfaces. May land in either order.
- **Independent of:** [P17 LangGraph Checkpointer](14-langgraph-checkpointer.md) — orthogonal.
- **Should respect:** [P14 citations consolidation](11-citations-consolidation.md) — if P14 ships first, this PR plugs into the new `CitationService` interface.
- **Should land before:** [P21 LangGraph interrupts](18-langgraph-interrupts.md) — interrupts depend on a stable streaming pipeline; touching both at once is risky.
- **Independent of:** [P19 repository collapse](16-repository-collapse.md) — different layer.

---

## 11. Open questions tracked from §2

(Filled in during spike. PRD revised before §3 / §6 lock.)

- [ ] Anthropic thinking blocks: separate stream chunk type via LiteLLM? Yes / No / Partial.
- [ ] OpenAI Responses API summary modes: forwarded? Yes / No / Partial.
- [ ] Gemini grounding metadata: inline in chunks? Yes / No / Partial.
- [ ] Usage metadata shape across providers: cleanly normalized by LiteLLM?
- [ ] Error type mapping: clean, chaotic, or per-provider?
- [ ] `workspace_model_kwargs` + `user_policy_model_kwargs`: accepted verbatim?
- [ ] Anthropic citations API support: present?
- [ ] Deep Agents compatibility with LiteLLM-backed chat model?
- [ ] LiteLLM version to pin?
