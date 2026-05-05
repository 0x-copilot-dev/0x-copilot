# Decomp — `runtime_worker/run_metrics.py`

Source: [services/ai-backend/src/runtime_worker/run_metrics.py](../../../services/ai-backend/src/runtime_worker/run_metrics.py) — **607 LOC, L.** Three classes (`TokenUsageExtractor`, `_PerCallSlot`, `PerCallTokenAccumulator`, `AssistantRunMetrics`). Owns: extracting provider token usage from heterogeneous LangChain shapes, deduping per-call usage by `message.id`, and producing both the `runtime_run_usage` (B1) and `runtime_model_call_usage` (B2) records the worker writes.

## A. Top-level structure

| Symbol                                                        |   Lines | Purpose                                                                                                                                         |
| ------------------------------------------------------------- | ------: | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `TokenUsageExtractor._Fields`                                 |   28–44 | 14 magic-string keys for usage shape detection.                                                                                                 |
| `TokenUsageExtractor._USAGE_KEYS`                             |   46–57 | Frozen set of keys that mark a Mapping as "looks like usage".                                                                                   |
| `TokenUsageExtractor.extract(value)` (classmethod)            |   59–87 | Walk one level deep into mappings/sequences for usage candidates. Returns tuple of mappings.                                                    |
| `_extract_from_object(value, candidates)`                     |  89–120 | Per-object lookup ladder: `usage_metadata` → `response_metadata.{token_usage,usage}` → `usage`/`token_usage` attrs/items → root mapping itself. |
| `_append_if_usage(value, candidates)`                         | 122–132 | Append `value` if it `_looks_like_usage`.                                                                                                       |
| `_looks_like_usage(value)`                                    | 134–136 | True iff any `_USAGE_KEYS` member present.                                                                                                      |
| `_PerCallSlot`                                                | 139–174 | `__slots__` dataclass-y bucket: `message_id`, `task_id`, `input/output/cached/total_tokens`, `started_at`, `completed_at`.                      |
| `PerCallTokenAccumulator.__init__`                            | 191–193 | Init `_slots: dict[message_id, _PerCallSlot]`, `_completed_message_ids: set[str]`.                                                              |
| `observe(usage, *, message_id, task_id, started_at)`          | 195–214 | Get or create slot; **last-write-wins** merge via `AssistantRunMetrics._merge_into_slot`.                                                       |
| `mark_completed(message_id, *, completed_at)`                 | 216–226 | Idempotent flip from in-flight to closed. Returns True only on first transition.                                                                |
| `has_seen(message_id)`                                        | 228–229 | Membership check.                                                                                                                               |
| `slot(message_id)`                                            | 231–232 | Slot lookup.                                                                                                                                    |
| `finalized_calls()`                                           | 234–239 | Tuple of slots that have been marked completed.                                                                                                 |
| `subagent_rollup(task_id)`                                    | 241–269 | **B2 spec §2.3**: sum per-call usage attributed to one subagent task.                                                                           |
| `AssistantRunMetrics.PERFORMANCE_KEY = "performance_metrics"` |     275 | Wrapper key for metadata.                                                                                                                       |
| `AssistantRunMetrics._Fields`                                 | 277–289 | Same magic-string pool (subset of `TokenUsageExtractor._Fields`).                                                                               |
| `AssistantRunMetrics.__init__(*, started_at)`                 | 291–299 | Init timing fields + `per_call: PerCallTokenAccumulator`.                                                                                       |
| classmethod `from_run(run)`                                   | 301–305 | Use `run.started_at or now()`.                                                                                                                  |
| `record_model_delta(delta)`                                   | 307–315 | Increment `chunk_count`; stamp `first_token_at` on first non-empty chunk.                                                                       |
| `record_usage_from(value, *, message_id, task_id)`            | 317–342 | Extract all usage candidates from `value`; merge into run-level + per-call.                                                                     |
| `model_call_usage_records(run, *, trace_id)`                  | 344–379 | One `RuntimeModelCallUsageRecord` per finalized slot.                                                                                           |
| `to_payload(*, completed_at)`                                 | 381–404 | Build `AssistantPerformanceMetrics` JSON: timing + first_token_ms + output/sec + usage.                                                         |
| classmethod `metadata(metrics)`                               | 406–410 | Wrap in `{performance_metrics: metrics}`.                                                                                                       |
| classmethod `with_payload(payload, metrics)`                  | 412–416 | Attach metrics to existing payload.                                                                                                             |
| `to_usage_record(run, *, completed_at, status)`               | 418–460 | Build B1 row at `RUN_COMPLETED` time.                                                                                                           |
| `_usage_payload(*, output_per_second)`                        | 462–481 | None if all token fields + output_per_second are None.                                                                                          |
| `_merge_usage(usage)`                                         | 483–512 | Run-level merge with synonym-resolution and total-fallback (`input + output`).                                                                  |
| classmethod `_merge_into_slot(slot, usage)`                   | 514–550 | Same algorithm but writes to a `_PerCallSlot`.                                                                                                  |
| classmethod `_token_value(value, *keys)`                      | 552–562 | First non-negative int across alias keys.                                                                                                       |
| classmethod `_cached_input_tokens(value)`                     | 564–581 | Walk `input_token_details` / `prompt_tokens_details` → `cache_read` / `cached_tokens`.                                                          |
| static `_non_negative_int(value)`                             | 583–591 | Coerce int / float-with-integer-value to int; reject bool.                                                                                      |
| static `_duration_ms(started_at, completed_at)`               | 593–597 | `max(0, round((end-start)*1000))`.                                                                                                              |
| static `_tokens_per_second(*, output_tokens, duration_ms)`    | 599–607 | `round(output / (ms/1000), 2)`. None for missing/zero inputs.                                                                                   |

## B. Feature inventory

| Domain                                                      | Symbols                                                                                                               |  LOC |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ---: |
| **Token usage extraction (heterogeneous LangChain shapes)** | `TokenUsageExtractor` + class attrs                                                                                   | ~115 |
| **Per-call usage bucketing (B2)**                           | `_PerCallSlot`, `PerCallTokenAccumulator`                                                                             | ~135 |
| **Run-level metrics aggregator (B1)**                       | `AssistantRunMetrics.{__init__, from_run, record_*, to_payload, to_usage_record, _usage_payload, _merge_*}` + helpers | ~310 |
| **Subagent rollup (B2 §2.3)**                               | `PerCallTokenAccumulator.subagent_rollup`                                                                             |  ~30 |
| **Wrapper helpers (metadata, with_payload)**                | classmethods on `AssistantRunMetrics`                                                                                 |  ~10 |

## C. Functional spec per domain

### Token usage extraction (`TokenUsageExtractor.extract`, 59–87)

Walks the value object via this lookup ladder per object:

1. `value.usage_metadata` (LangChain ≥ 0.2 native) — preferred.
2. `value.response_metadata.token_usage` / `.usage`.
3. `value.usage` / `value.token_usage` (direct attribute).
4. `value` itself if it looks like usage.

Then, **one level deep**:

- If `value` is a Mapping, walk its `.values()` (and 1 level into nested sequences).
- If `value` is a Sequence, walk each item.

Returns a tuple of usage mappings. Deduplication is the caller's job.

`_looks_like_usage` (134–136): true iff any of `INPUT_TOKENS, OUTPUT_TOKENS, TOTAL_TOKENS, PROMPT_TOKENS, COMPLETION_TOKENS, *_TOKEN_COUNT` is a key.

### Token-name alias resolution (`_token_value` / `_cached_input_tokens`, 552–581)

| Field        | Aliases tried in order                                                                                                                           |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| input        | `input_tokens`, `prompt_tokens`, `prompt_token_count`                                                                                            |
| output       | `output_tokens`, `completion_tokens`, `completion_token_count`                                                                                   |
| total        | `total_tokens`, `total_token_count`                                                                                                              |
| cached_input | `input_token_details.cache_read`, `input_token_details.cached_tokens`, `prompt_tokens_details.cache_read`, `prompt_tokens_details.cached_tokens` |

Cached tokens are **always** nested under `*_token_details` — never a top-level key.

### Per-call accumulator (`PerCallTokenAccumulator`)

**Last-write-wins by `message.id`** (docstring 142–146): "Counts are _replaced_ (not summed) on each merge because providers stream cumulative usage across chunks of the same AIMessage and the final chunk carries the authoritative total."

`mark_completed` (216–226) is idempotent — second call returns False. Used as the trigger to emit `MODEL_CALL_COMPLETED` at the right time.

`finalized_calls` (234–239) returns slots that have been marked completed AT LEAST ONCE — slots with usage but no completion mark are excluded.

`subagent_rollup(task_id)` (241–269) sums all slots tagged with a particular `task_id`. Returns a zero-rollup with `call_count=0` when no calls were attributed; **callers should leave the SUBAGENT_COMPLETED `usage` field unset rather than emit an empty rollup** (docstring 246–248).

### Run-level metrics aggregator (`AssistantRunMetrics`)

**`record_model_delta`** (307–315): increments `chunk_count` only for non-empty deltas; stamps `first_token_at` on the first non-empty chunk.

**`record_usage_from`** (317–342): extracts ALL usage candidates from the value (which can be deep-nested) and applies them in order. Both run-level merge AND (when message_id present) per-call observe.

**`to_payload`** (381–404): builds `AssistantPerformanceMetrics`:

- `started_at`, `completed_at`, `duration_ms`
- `chunk_count`, `first_chunk_at`, `first_chunk_ms`
- `usage` ← `AssistantUsageMetrics(input, output, total, cached_input, output_per_second)`

**`to_usage_record`** (418–460): builds `RuntimeRunUsageRecord` for B1. Token fields fall back to 0 when usage missing — "the row is still useful for `runs_count` / latency aggregates" (docstring).

**Total fallback** (509–510): if no `total_tokens` reported but both `input` and `output` are, compute `total = input + output`.

**Merge semantics**: the same algorithm runs in `_merge_usage` (run-level) and `_merge_into_slot` (per-call). Both use last-write-wins for individual fields; only writes when the candidate value is not None.

## D. Bugs / edge cases / invariants

- **Last-write-wins by message_id** (per-call) (209–210, 142–146): "providers stream cumulative usage across chunks of the same AIMessage and the final chunk carries the authoritative total."
- **Token names normalised** (483–512): provider differences (OpenAI uses `prompt_tokens`/`completion_tokens`, Anthropic uses `input_tokens`/`output_tokens`, Google uses `*_token_count`) all flow into the same canonical fields.
- **`_non_negative_int` rejects bool** (585): `True` would otherwise pass the int check (Python booleans are ints).
- **`_non_negative_int` accepts integer-valued floats** (589–590): some providers report `1234.0`.
- **`mark_completed` idempotency** (219–220): same call twice → second is a no-op. Lets callers re-emit MODEL_CALL_COMPLETED safely on retries.
- **`finalized_calls` requires both observation AND completion**: a slot that was observed but never marked completed is dropped from B2 records. Drives the worker to call `mark_completed` exactly once per call.
- **Subagent rollup zero-call returns 0 totals** (262–268): callers should treat zero-call as "no usage to report" and skip emitting the rollup field.
- **`record_model_delta` filters empty deltas** (310): only non-empty chunks count toward `chunk_count` and `first_token_at`. Defends against empty intermediate chunks (e.g. empty tool_call_delta chunks).
- **`to_usage_record` total fallback** (451–452): `total_tokens or (input + output)` — provider-supplied total wins, computed total only used when missing.
- **`to_payload` exclude_none** (404): keeps the payload terse; missing fields aren't serialised as `null`.
- **`_usage_payload` returns None when everything is None** (467–474): defends against emitting an "empty usage" object that would mislead the FE.
- **`_tokens_per_second` requires both `output_tokens` AND `duration_ms > 0`** (605): zero-duration runs (cache hits, instant errors) don't compute infinity.
- **`_duration_ms` clamp at 0** (596–597): defends against clock skew / NTP jumps.
- **Nested usage walking** (73–86): only ONE level deep into mapping values + sequence items. A doubly-nested wrapper would miss usage.
- **`from_run` fallback to now()** (304–305): if the run record's `started_at` is None (shouldn't happen by the time this is called, but defensive), use current time.

## E. Hardcoded vs configurable

### Hardcoded

- All token-field name aliases (the `_Fields` pools).
- Cached-input nested key paths.
- `PERFORMANCE_KEY = "performance_metrics"` — wrapper field name.
- `output_per_second` rounding to 2 decimals.
- 1-level walk depth in `extract`.

### Configurable

- `started_at` injected via `from_run` (defaults to now()).

## F. External dependencies and coupling

### Internal

- `agent_runtime.execution.contracts.JsonObject`.
- `agent_runtime.persistence.records.RuntimeModelCallUsageRecord`, `RuntimeRunUsageRecord`.
- `runtime_api.schemas.AssistantPerformanceMetrics`, `AssistantSubagentUsageRollup`, `AssistantUsageMetrics`, `RunRecord`.

### Stdlib

- `datetime`, `collections.abc.Mapping/Sequence`.

No LangChain imports — `TokenUsageExtractor` works against duck-typed objects (`getattr(value, "usage_metadata", ...)`) so it doesn't pin us to a specific LangChain version.

## G. Suggested decomposition seams

Three already-classified clusters:

1. **`token_extraction.py`** — `TokenUsageExtractor` + the `_USAGE_KEYS` set. ~115 LOC. Pure functions over heterogeneous shapes; the alias map is its complete public contract.
2. **`per_call_accumulator.py`** — `_PerCallSlot`, `PerCallTokenAccumulator`. ~135 LOC. Self-contained B2 bucket logic.
3. **`run_metrics.py`** (renamed) — `AssistantRunMetrics` + the merge / token / duration / tps helpers. ~310 LOC.

The **token-name alias resolver** (`_token_value`, `_cached_input_tokens`, `_non_negative_int`) is duplicated between `_merge_usage` and `_merge_into_slot` — the docstring at 514–521 acknowledges this. The seam is to make `_PerCallSlot` an `AssistantRunMetrics`-like accumulator, or extract a shared `_TokenMerger` helper.

The **`subagent_rollup`** logic is small and could move to where it's consumed (`stream_subagents.py`); it's the only part of `PerCallTokenAccumulator` that's per-task rather than per-message. Splitting would make the bucket → rollup flow more explicit.
