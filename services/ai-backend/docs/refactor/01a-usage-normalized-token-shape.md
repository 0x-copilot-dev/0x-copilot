# Sub-PRD 01a — Normalized Token Shape

**Status:** Draft (2026-05-11)
**Parent:** [01-usage-capture-and-attribution.md](01-usage-capture-and-attribution.md)
**Position in plan:** P11.7.a (first of four sub-PRDs)
**Risk:** Low. Direct cutover; additive schema; provider extractors covered by snapshot fixtures.

---

## 1. Problem

Today token-usage extraction in [`run_metrics.py`](../../src/runtime_worker/run_metrics.py):

- Walks arbitrary objects for "looks like usage" mappings (the `TokenUsageExtractor` class returns `tuple[Mapping[str, object], ...]`).
- Pulls four fields via aliases (`input_tokens / prompt_tokens / prompt_token_count` ⇒ `input`).
- Caches `cached_input` from `prompt_tokens_details.cached_tokens`.
- Drops everything else on the floor.

What's missing:

- **`reasoning_tokens`** — OpenAI o-series + Anthropic extended thinking carry these in `completion_tokens_details.reasoning_tokens` / `usage.reasoning_tokens`. Currently unread.
- **`cache_creation_input_tokens`** — Anthropic emits this when prompt caches are written. Currently unread. Often >50% of cached-request cost.
- **`audio_input_tokens` / `audio_output_tokens`** — OpenAI Responses voice. Currently unread.
- **Anthropic input normalization** — Anthropic's `input_tokens` field is the _non-cache_ portion; the gross input is `input + cache_creation + cache_read`. Today we read it raw and undercount.

Downstream: P12 LiteLLM pricing rows price these kinds distinctly. Multiplying by missing columns silently undercounts cached / reasoning workloads.

---

## 2. Goal

A provider-agnostic `NormalizedTokenUsage` value object with explicit fields for every token kind we price. Provider-specific extraction is the only place that knows about chunk shapes; downstream code never branches on provider.

Two new contracts:

1. **`NormalizedTokenUsage`** — frozen Pydantic value object. Default 0 for every kind. Pricing math is total.
2. **`ProviderTokenUsageExtractor`** — Protocol with one method `extract(chunk) -> NormalizedTokenUsage | None`. One impl per provider, registered with `TokenUsageExtractorRegistry`.

Persistence: extend `RuntimeModelCallUsageRecord` and `RuntimeRunUsageRecord` with `reasoning_tokens / cache_creation_input_tokens / audio_input_tokens / audio_output_tokens` columns. Migration is additive.

Streaming executor + run-metrics accumulator switch to the new extractor. The existing `TokenUsageExtractor` class in `run_metrics.py` is deleted.

---

## 3. Non-goals

- Attribution context (purpose / subagent_slug / originating_tool) — that's 01b.
- `UsageRecorder` Protocol — that's 01c.
- Rollup-table column expansion — that's 01d. The 01a columns land on per-call + run-level rows; rollup tables retain their current sum semantics.
- New provider support. Only the three we have today (OpenAI, Anthropic, Gemini).

---

## 4. Architecture

### 4.1 The value object

```python
# agent_runtime/observability/token_usage.py

from pydantic import BaseModel, ConfigDict, NonNegativeInt, computed_field


class NormalizedTokenUsage(BaseModel):
    """Provider-agnostic token-usage value object.

    Field semantics:

    - ``input_tokens``: GROSS input (regular + cached + cache_creation).
      Provider extractors normalize provider-specific subsets into this
      gross figure before constructing.
    - ``cached_input_tokens``: subset of ``input_tokens`` billed at the
      cached-read rate.
    - ``cache_creation_input_tokens``: subset of ``input_tokens`` billed
      at the cache-write rate.
    - ``output_tokens``: completion / response tokens.
    - ``reasoning_tokens``: reasoning / hidden-chain tokens (OpenAI o-
      series, Anthropic extended thinking).
    - ``audio_input_tokens`` / ``audio_output_tokens``: voice tokens
      where the provider charges separately.

    Pricing math (P12 plugs in here):

        cost = (input - cached - cache_creation) * price_input
             + cached                            * price_cached_input
             + cache_creation                    * price_cache_creation
             + output                            * price_output
             + reasoning                         * price_reasoning
             + audio_input                       * price_audio_input
             + audio_output                      * price_audio_output
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0
    reasoning_tokens: NonNegativeInt = 0
    audio_input_tokens: NonNegativeInt = 0
    audio_output_tokens: NonNegativeInt = 0

    @computed_field  # type: ignore[misc]
    @property
    def total_tokens(self) -> int:
        # input already includes cached + cache_creation; output is its
        # own thing; reasoning and audio are independent kinds.
        return (
            self.input_tokens
            + self.output_tokens
            + self.reasoning_tokens
            + self.audio_input_tokens
            + self.audio_output_tokens
        )

    def merge(self, other: "NormalizedTokenUsage") -> "NormalizedTokenUsage":
        """Last-write-wins, field-by-field, for cumulative-chunk providers.

        OpenAI streams usage cumulatively across chunks of the same
        AIMessage; the final chunk carries the authoritative total. We
        take the larger value for each kind so we never undercount.
        """
        return NormalizedTokenUsage(
            input_tokens=max(self.input_tokens, other.input_tokens),
            output_tokens=max(self.output_tokens, other.output_tokens),
            cached_input_tokens=max(self.cached_input_tokens, other.cached_input_tokens),
            cache_creation_input_tokens=max(
                self.cache_creation_input_tokens, other.cache_creation_input_tokens
            ),
            reasoning_tokens=max(self.reasoning_tokens, other.reasoning_tokens),
            audio_input_tokens=max(self.audio_input_tokens, other.audio_input_tokens),
            audio_output_tokens=max(self.audio_output_tokens, other.audio_output_tokens),
        )
```

### 4.2 The extractor Protocol + registry

```python
@runtime_checkable
class ProviderTokenUsageExtractor(Protocol):
    """Normalize provider-specific chunks into ``NormalizedTokenUsage``."""

    def extract(self, chunk: object) -> NormalizedTokenUsage | None: ...


class OpenAIProviderTokenUsageExtractor:
    """OpenAI Chat Completions + Responses API.

    Reads ``usage_metadata`` (LangChain-native AIMessage) and
    ``response_metadata.token_usage`` (raw OpenAI dict). Pulls reasoning
    out of ``completion_tokens_details.reasoning_tokens``, cached out
    of ``prompt_tokens_details.cached_tokens``, audio out of
    ``prompt_tokens_details.audio_tokens`` and
    ``completion_tokens_details.audio_tokens``.
    """

    def extract(self, chunk: object) -> NormalizedTokenUsage | None: ...


class AnthropicProviderTokenUsageExtractor:
    """Anthropic Messages API.

    Anthropic reports ``input_tokens`` = *non-cache* portion. The gross
    input is ``input + cache_creation + cache_read``. This extractor
    normalizes so ``NormalizedTokenUsage.input_tokens`` is the gross
    figure (matching the OpenAI semantic). Reads
    ``cache_creation_input_tokens`` and ``cache_read_input_tokens``;
    maps the latter to ``cached_input_tokens``. Reasoning lands on
    Anthropic's extended-thinking messages under
    ``thinking.usage.input_tokens`` etc. — see ``_extract_reasoning``.
    """

    def extract(self, chunk: object) -> NormalizedTokenUsage | None: ...


class GeminiProviderTokenUsageExtractor:
    """Google Gemini.

    Reads ``usage_metadata`` (LangChain) → ``prompt_token_count`` /
    ``candidates_token_count``. No cache / reasoning / audio fields
    today; returns zeros for those. When Gemini adds those (e.g. 2.0
    Flash thinking mode), extend this extractor and add a fixture.
    """

    def extract(self, chunk: object) -> NormalizedTokenUsage | None: ...


class TokenUsageExtractorRegistry:
    """Map provider slug → extractor instance.

    Instances are stateless and shareable. Lookup is exact-match; the
    provider slug comes from ``RunRecord.model_provider`` which is
    normalized at run-create time to one of {openai, anthropic,
    gemini}. Unknown providers fall back to a permissive extractor that
    matches the old ``run_metrics.TokenUsageExtractor`` behavior — so
    a new provider's tokens are captured (at lcd quality) before its
    dedicated extractor lands.
    """

    @classmethod
    def for_provider(cls, provider: str) -> ProviderTokenUsageExtractor: ...
```

### 4.3 What gets deleted in this PR

- The `TokenUsageExtractor` class in [`run_metrics.py:21-136`](../../src/runtime_worker/run_metrics.py) — replaced by the registry. The `_USAGE_KEYS` frozenset, `_extract_from_object` walker, all of it. The new path is one method call: `extractor.extract(chunk)` returns a `NormalizedTokenUsage` or `None`.
- The `_token_value` / `_cached_input_tokens` alias-soup helpers on `AssistantRunMetrics` (the merging path also collapses).
- The provider-coupling that lives implicitly in `_merge_usage` / `_merge_into_slot` — those now take a `NormalizedTokenUsage` value object and call `.merge()`.

Two things kept:

- `_MessageIdExtractor` in `streaming_executor.py` stays. It's about identity not tokens, used by the per-call dedup keying. Untouched in 01a.
- `_PerCallSlot` keeps its existing fields plus the four new token kinds. The shape changes additively.

### 4.4 Schema changes

Migration `0027_runtime_usage_token_kinds.sql`:

```sql
ALTER TABLE runtime_model_call_usage
    ADD COLUMN IF NOT EXISTS reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS audio_input_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS audio_output_tokens INTEGER NOT NULL DEFAULT 0;

ALTER TABLE runtime_usage_runs
    ADD COLUMN IF NOT EXISTS reasoning_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cache_creation_input_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS audio_input_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS audio_output_tokens BIGINT NOT NULL DEFAULT 0;
```

`NOT NULL DEFAULT 0` is safe on Postgres 11+ — no table rewrite, no lock. Pre-migration rows take 0 for the new kinds.

Rollback drops the columns. Old code keeps reading the columns it knows.

Rollup table columns NOT added in 01a (deferred to 01d).

### 4.5 Pydantic records

[`agent_runtime/persistence/records/telemetry.py`](../../src/agent_runtime/persistence/records/telemetry.py):

```python
class RuntimeModelCallUsageRecord(RuntimeContract):
    # … existing fields …
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0    # NEW
    reasoning_tokens: NonNegativeInt = 0               # NEW
    audio_input_tokens: NonNegativeInt = 0             # NEW
    audio_output_tokens: NonNegativeInt = 0            # NEW
    total_tokens: NonNegativeInt = 0
    # … rest unchanged …


class RuntimeRunUsageRecord(RuntimeContract):
    # … same four NEW columns …
```

### 4.6 Streaming executor + run-metrics rewire

`run_metrics.py`:

- `AssistantRunMetrics.record_usage_from(chunk, ...)` switches to:
  ```python
  extractor = TokenUsageExtractorRegistry.for_provider(self.provider)
  usage = extractor.extract(chunk)
  if usage is None:
      return
  self._merge_usage_object(usage)
  if message_id is not None:
      self.per_call.observe(usage, message_id=message_id, task_id=task_id, ...)
  ```
- `_PerCallSlot` adds the four new fields; `observe(usage)` takes a `NormalizedTokenUsage` directly (was a `Mapping`).
- `AssistantRunMetrics.__init__` takes `provider: str` so the registry call is cheap (no per-chunk lookup).

`streaming_executor.py`:

- `if not TokenUsageExtractor.extract(source): return` becomes `if extractor.extract(source) is None: return` using the same registry call.

---

## 5. Behaviors preserved

| Behavior                                                          | How                                                                                                                            |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Per-call row written exactly once per AIMessage with usage        | `PerCallTokenAccumulator.mark_completed` semantics unchanged.                                                                  |
| Cumulative-chunk dedup (last write wins via `.merge`)             | New `NormalizedTokenUsage.merge` matches old "last write wins" with the same field-wise max.                                   |
| Cold-turn (no usage chunk) → no per-call row                      | Extractor returns `None`; emit short-circuits.                                                                                 |
| Run-level totals stamped at RUN_COMPLETED                         | Same path; just operates on a typed value object now.                                                                          |
| Wire payload `performance_metrics.usage` shape                    | Unchanged. The four new kinds default to 0 on rows + payloads (additive — FE ignores unknown fields today; 01d wires them in). |
| Cost stamping at write time, micro-USD, banker's rounding         | Unchanged. `CostCalculator` still operates on whatever columns exist. P12 will extend that math to include new kinds.          |
| Subagent rollup math in `PerCallTokenAccumulator.subagent_rollup` | Unchanged. New kinds aren't yet rolled up there; that's 01d.                                                                   |

---

## 6. Tests

### 6.1 Per-provider snapshot fixtures

`tests/unit/agent_runtime/observability/test_token_usage_extractors.py`:

- **OpenAI Chat Completions chunk** (current `gpt-4o` shape): asserts `input_tokens`, `output_tokens`, `cached_input_tokens`, `total_tokens` populated; `reasoning_tokens=0`.
- **OpenAI o-series chunk** (`o1-preview` / `o3-mini`): asserts `reasoning_tokens > 0` extracted from `completion_tokens_details.reasoning_tokens`.
- **OpenAI Responses-API chunk with audio**: asserts `audio_input_tokens` + `audio_output_tokens` populated.
- **Anthropic Claude chunk with prompt caching**: asserts `cache_creation_input_tokens > 0`, `cached_input_tokens > 0`, and `input_tokens` is the GROSS figure (not Anthropic's raw `input_tokens` field).
- **Anthropic extended-thinking chunk**: asserts `reasoning_tokens > 0`.
- **Gemini chunk**: asserts `input_tokens` + `output_tokens` populated; reasoning/cached/audio = 0.
- **Empty chunk** (no usage payload): asserts `extract` returns `None`.
- **Malformed chunk** (wrong types, missing keys): asserts `extract` returns `None`, never raises.

### 6.2 Value-object tests

`tests/unit/agent_runtime/observability/test_normalized_token_usage.py`:

- `merge` is commutative and field-wise max.
- `total_tokens` computed-field math.
- Pydantic `frozen=True` — assignment raises.
- `extra="forbid"` — unknown field raises.

### 6.3 Registry tests

- `for_provider("openai")` returns the OpenAI extractor.
- `for_provider("anthropic")` returns the Anthropic extractor.
- `for_provider("gemini")` returns the Gemini extractor.
- `for_provider("unknown")` returns a fallback extractor matching old LCD behavior.

### 6.4 Integration

- `AssistantRunMetrics.record_usage_from` invoked with each fixture chunk produces a per-call slot whose token counts match the fixture's expected `NormalizedTokenUsage`.
- `RuntimeModelCallUsageRecord` materialized from a slot has all four new columns populated (or zero) according to the fixture.

### 6.5 Regression

The existing `run_metrics` tests must pass unchanged. Existing four kinds are unaffected for non-reasoning, non-cache-write, non-audio workloads — fixtures landed pre-01a continue to assert the same numbers.

---

## 7. Risks

| Risk                                                                                       | Likelihood | Impact | Mitigation                                                                                                                     |
| ------------------------------------------------------------------------------------------ | ---------- | ------ | ------------------------------------------------------------------------------------------------------------------------------ |
| Anthropic's "input is non-cache portion" semantic varies by API version                    | Low        | Medium | Snapshot fixture captures the current shape; a future Anthropic field rename fails the fixture loudly.                         |
| OpenAI Responses-API audio fields land in a different chunk structure than today's preview | Medium     | Low    | The OpenAI extractor reads through multiple alias paths; if a new path emerges, add it + a fixture.                            |
| Gemini introduces reasoning/cache fields between PRs                                       | Low        | Low    | Falls through to 0; harmless. Re-extend the Gemini extractor when Google ships the fields.                                     |
| Migration `ALTER TABLE … NOT NULL DEFAULT 0` locks the table on a too-old Postgres         | Low        | Medium | Project standard is Postgres 14+. `NOT NULL DEFAULT` of a literal is a metadata-only change on PG 11+. Confirmed before merge. |

---

## 8. Implementation order

1. Create `agent_runtime/observability/token_usage.py` with the value object, Protocol, three extractors, registry.
2. Land per-provider snapshot fixtures + tests (drives extractor correctness before wiring).
3. Add new columns to Pydantic records + migration `0027_*.sql` + `.rollback.sql`.
4. Rewire `run_metrics.py`: delete `TokenUsageExtractor`, switch `record_usage_from` to use the registry, extend `_PerCallSlot` with the new fields, update `model_call_usage_records` + `to_usage_record` builders.
5. Rewire `streaming_executor.py`: the one site that calls `TokenUsageExtractor.extract` to gate emit.
6. Run full test suite; fix any regression.

---

## 9. Done

- Migration 0027 landed.
- Old `TokenUsageExtractor` deleted from `run_metrics.py`.
- New `NormalizedTokenUsage` + extractor registry in place.
- All snapshot fixture tests green.
- Full ai-backend suite green.
- This sub-PRD `Status: Shipped` and parent PRD §4 row ticked.
