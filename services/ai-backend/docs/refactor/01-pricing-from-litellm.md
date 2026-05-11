# Refactor PRD — Pricing source from LiteLLM (Phase 3 / P12)

**Status:** Draft (revised 2026-05-10 after code-level verification)
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §1.6](../architecture/refactor-audit.md#16-custom-budget--pricing-system--seed-catalog), [roadmap P12](00-roadmap.md#phase-3--library-replacements-independent)

> **Revision note.** The original draft was directionally correct (LiteLLM as the source instead of hand-curated seeds) but wrong on several specifics. After reading [`pricing/calculator.py`](../../src/agent_runtime/pricing/calculator.py), [`pricing/catalog.py`](../../src/agent_runtime/pricing/catalog.py), [`pricing/seed_loader.py`](../../src/agent_runtime/pricing/seed_loader.py), and a representative seed file:
>
> - `ModelPricingRecord` has **no `reasoning_per_1m_micro_usd` column.** The active schema is `input_per_1m_micro_usd`, `output_per_1m_micro_usd`, `cached_input_per_1m_micro_usd`, `context_window_tokens`. `CostCalculator.compute(...)` doesn't take a `reasoning_tokens` argument either. Adding reasoning-tier billing is a separate change, out of scope for this PRD.
> - Seeds are **quarterly per-provider YAML files** (`anthropic-2026-q1.yaml`, `google-2026-q1.yaml`, `openai-2026-q1.yaml`), not per-model files. Each ships a `pricing_version` and a list of `prices`.
> - `ModelPricingRecord` already has a `pricing_source` field (`"yaml-seed"` today). LiteLLM ingestion can set `pricing_source="litellm"` without schema change.
> - **Time-keying:** `effective_from` / `effective_until` is the mechanism that keeps history frozen. Re-ingest closes prior rows; existing usage rows reference the row that was active at write time.
> - **Lookup is region-keyed** (`region: str` defaults to `"global"`). LiteLLM has no region in its data; we'll map every LiteLLM row to `region="global"`.
> - `PricingSeedLoader.load_all()` produces `ModelPricingRecord` instances and **yields them to a caller** (currently `scripts/usage/seed_pricing.py`) for upsert via the persistence port. The loader doesn't touch the DB itself.
> - `ModelPricingCatalog` wraps lookups in a per-key LRU cache (`maxsize=256`, minute-floored cache key) so worker cost-computation hooks don't pay a DB hit per RUN_COMPLETED.

---

## 1. Problem

The runtime maintains a hand-curated model pricing catalog under [`agent_runtime/pricing/`](../../src/agent_runtime/pricing/):

- [`calculator.py`](../../src/agent_runtime/pricing/calculator.py) (75 LOC) — `CostCalculator.compute(input_tokens, output_tokens, cached_input_tokens, pricing)` returns integer micro-USD using `Decimal` with `ROUND_HALF_EVEN` (banker's rounding). Fail-soft: any negative input → 0.
- [`catalog.py`](../../src/agent_runtime/pricing/catalog.py) (76 LOC) — `ModelPricingCatalog` wraps an async `lookup_pricing(provider, model_name, region, at)` port behind a 256-entry LRU cache, key = `(provider, model_name, region, minute_of(at))`.
- [`seed_loader.py`](../../src/agent_runtime/pricing/seed_loader.py) (86 LOC) — `PricingSeedLoader.load_all()` reads every `*.yaml` under `seeds/` and produces `ModelPricingRecord` instances. The loader does **not** upsert; a script calls it and feeds the records to the persistence port.
- [`seeds/`](../../src/agent_runtime/pricing/seeds/) — three quarterly per-provider YAML files: `anthropic-2026-q1.yaml`, `google-2026-q1.yaml`, `openai-2026-q1.yaml`. Each ships a top-level `pricing_version` (e.g. `"anthropic-2026-q1.v1"`), a `provider`, and a list of `prices` rows.

Every time a provider releases a new model or quarter rolls over, someone has to:

1. Find the published per-token rate.
2. Convert to micro-USD per million tokens (with banker's rounding).
3. Add a row to the relevant provider's quarterly YAML, OR cut a new quarterly YAML and bump `pricing_version`.
4. Run `scripts/usage/seed_pricing.py` on staging and prod, which upserts via the persistence port (closing prior `effective_until` and inserting the new row).

This is recurring tax. It's also error-prone — provider docs publish rates as $/1M tokens, $/1K tokens, or $/token depending on the page; the integer-rounding step is bespoke; the `effective_from` window has to be set correctly to avoid backfilling history.

[LiteLLM](https://github.com/BerriAI/litellm) maintains [`model_prices_and_context_window.json`](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) — a community-maintained file with `input_cost_per_token`, `output_cost_per_token`, `cache_creation_input_token_cost`, `cache_read_input_token_cost`, plus context window and provider tags for every Claude, OpenAI, Gemini, Mistral, Cohere, etc. model. New models typically land within days of provider announcement. The data is exposed as a Python dict via `litellm.model_cost`.

> **Note on reasoning tokens.** LiteLLM also exposes `output_reasoning_token_cost` for providers that bill them separately. The active `ModelPricingRecord` schema and `CostCalculator.compute` do not have a reasoning surface today. Adding it is a separate, schema-changing PRD; **out of scope here.** This PRD ingests LiteLLM's reasoning column into a "drop / log" path so we don't lose information, but doesn't change billing.

### Symptoms (today)

- Seeds drift behind provider catalogs by 1–4 weeks per release; quarterly cadence means new mid-quarter models land late.
- Adding a new provider means a new YAML, a new pricing_version, and a coordinated rollout.
- The conversion math (`USD per token → micro-USD per million tokens`) is implicit in the seed authoring process; mistakes only surface when a finance reconciliation flags an off-by-1000.
- `region` is keyed in the schema but always `"global"` in practice — extra dimension that adds no information today.

### What this is NOT

- Not a change to [`agent_runtime/budgets/`](../../src/agent_runtime/budgets/). `BudgetCharger`, `BudgetEnforcer`, `BudgetEstimator`, `BudgetReservations`, `Period` all stay.
- Not a change to `CostCalculator.compute` semantics. Integer micro-USD with banker's rounding (Decimal `ROUND_HALF_EVEN`) stays exactly.
- Not a change to `ModelPricingRecord` schema. Existing rows must continue to validate.
- Not a change to the `effective_from` / `effective_until` time-keying mechanism. Re-ingest closes prior rows; existing usage records keep referencing the row that was active at write time.
- Not a change to historical cost rows in `RuntimeRunUsageRecord` / `RuntimeModelCallUsageRecord`. Pricing-source changes must never retroactively rewrite history (see [f9-usage-metrics](../architecture/f9-usage-metrics.puml)).
- Not adding a `reasoning_per_1m_micro_usd` column or reasoning-token billing. That's a separate PRD with a schema migration; this one keeps the active billing surface unchanged.
- Not adopting LiteLLM as a runtime model-call client. Provider streaming adapters in [`agent_runtime/execution/providers/`](../../src/agent_runtime/execution/providers/) stay bespoke; that swap is [roadmap P20](00-roadmap.md#phase-5--major-library-swaps--structural-shifts) and depends on a separate verification spike.

---

## 2. Goal and non-goals

### Goal

LiteLLM becomes the **source** for the pricing table. The persisted `ModelPricingRecord` schema and `CostCalculator.compute` semantics stay identical. Historical cost rows stay frozen. The team stops hand-curating `seeds/` for upstream-supported models.

### Non-goals

- Replace `BudgetCharger` / `pricing/calculator.py` semantics.
- Switch the model-call client to LiteLLM.
- Allow retroactive pricing changes to rewrite history.
- Reduce the surface of `ModelPricingRecord` (e.g. drop the `cached_input` or `reasoning` columns). The schema is load-bearing for historical accuracy.

### Success criteria

- A new `LiteLLMPricingSource` (under `agent_runtime/pricing/`) ingests `litellm.model_cost`, converts per-token rates → integer micro-USD per million with banker's rounding, and produces `ModelPricingRecord` instances suitable for the existing upsert script.
- `pricing_source` field on the upserted rows is `"litellm"`; existing `pricing_source="yaml-seed"` rows are untouched (history preserved).
- Custom-model override path exists: a `pricing_overrides.yaml` takes precedence over LiteLLM data. Each override carries a `reason` field for audit.
- The `seeds/` quarterly YAML files are kept for the air-gapped / offline-startup path. They are no longer the primary source of truth; LiteLLM is.
- Refresh strategy: configurable schedule (default daily), gated behind a feature flag, with structured-log diff output when an upstream rate changes for a model that's already in the table.
- Pre-existing rows in `RuntimeRunUsageRecord` / `RuntimeModelCallUsageRecord` are byte-identical before and after the migration.
- A migration step ensures any divergence between `seeds/` and LiteLLM produces an override entry — so the **active** row at the moment of switchover reads identically before and after.
- All pricing-related tests pass; new test confirms LiteLLM ingestion produces values identical to the current seed catalog for every `(provider, model_name)` pair currently in `seeds/`.

---

## 3. Systems touched

Inventory derived from the cluster diagrams ([C8b cross-cutting](../architecture/11-cross-cutting.puml)) and the audit. **Verify exact paths and LOC at implementation time.**

### 3.1 Files added

| File                                             | Purpose                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent_runtime/pricing/litellm_source.py`        | `LiteLLMPricingSource.load_all()`: reads `litellm.model_cost`, converts per-token rates → integer micro-USD per million using the same `Decimal`/`ROUND_HALF_EVEN` math as `CostCalculator`, returns `tuple[ModelPricingRecord, …]` with `pricing_source="litellm"`. Skips LiteLLM rows whose `mode` isn't a chat/completion variant. Drops (and logs) `output_reasoning_token_cost` until reasoning billing lands. |
| `agent_runtime/pricing/overrides.py`             | Loads `pricing_overrides.yaml` and produces `ModelPricingRecord` instances with `pricing_source="override"`. Each override entry carries a `reason` field; load fails closed if `reason` is missing.                                                                                                                                                                                                                |
| `agent_runtime/pricing/refresh_loop.py`          | Worker-hosted refresh loop (similar shape to `UsageRollupLoop` in [`runtime_worker/usage_rollup_loop.py`](../../src/runtime_worker/usage_rollup_loop.py)). Re-ingests LiteLLM data, diffs against the active rows, optionally writes. Opt-in via `PRICING_REFRESH_ENABLED=true` (default off, mirroring `DbStatementMetricsCollector`'s pattern).                                                                   |
| `tests/unit/pricing/test_litellm_source.py`      | Conversion correctness, rounding parity with `CostCalculator`, missing-field handling (no `cache_*`), `mode` filter, reasoning-column drop+log behavior.                                                                                                                                                                                                                                                            |
| `tests/unit/pricing/test_overrides.py`           | Override precedence, missing `reason` rejected, missing override + missing LiteLLM falls through to YAML seed.                                                                                                                                                                                                                                                                                                      |
| `tests/unit/pricing/test_seed_parity.py`         | Snapshot test: every `(provider, model_name)` in the YAML seeds today produces the same `ModelPricingRecord` values via the LiteLLM source within per-provider tolerance bands, OR an override entry exists explaining the divergence.                                                                                                                                                                              |
| `tests/integration/pricing/test_refresh_loop.py` | Refresh loop creates new rows, closes prior `effective_until` correctly, never mutates `RuntimeModelCallUsageRecord` history.                                                                                                                                                                                                                                                                                       |

### 3.2 Files removed

> **None in Step 1 / Step 2.** The `seeds/` quarterly YAMLs stay as the air-gapped fallback. `seed_loader.py`'s reading path is reused by the override loader (same YAML parser).

| File                        | Reason                                                                                                                              |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| _(none in Step 1 / Step 2)_ | The YAML seeds remain the air-gapped fallback. Removal — if it happens at all — is a Step-3 follow-up after a stabilization window. |

### 3.3 Files changed

| File                                                                                     | Change                                                                                                                                                                                                                                        |
| ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/usage/seed_pricing.py` (existing — verify path)                                 | Today calls `PricingSeedLoader.load_all()` and upserts. After Step 2, calls a new `PricingComposer.load(source: Literal["litellm", "yaml"])` that merges LiteLLM (or YAML in air-gapped mode) with the override list.                         |
| [`agent_runtime/pricing/seed_loader.py`](../../src/agent_runtime/pricing/seed_loader.py) | Untouched in Step 1. In Step 2, only used as the fallback source. The conversion math doesn't move — the seed YAML already carries integer micro-USD values directly, so there's no `seed_loader.py` math to delete.                          |
| [`agent_runtime/pricing/catalog.py`](../../src/agent_runtime/pricing/catalog.py)         | Untouched. The cache key (`provider, model_name, region, minute`) and `_PricingPort.lookup_pricing` shape stay. `pricing_source` already lives on `ModelPricingRecord`; LiteLLM-sourced rows get `"litellm"`, override rows get `"override"`. |
| [`runtime_worker/__main__.py`](../../src/runtime_worker/__main__.py)                     | Optionally register `PricingRefreshLoop` when `PRICING_REFRESH_ENABLED=true`. Mirrors the existing pattern for `UsageRollupLoop`, `RetentionSweeperLoop`, `DbStatementMetricsCollector`.                                                      |
| [`agent_runtime/deployment/profile.py`](../../src/agent_runtime/deployment/profile.py)   | Add `pricing_primary_source` toggle: `{"litellm", "yaml"}`. Default `"litellm"` in production; `"yaml"` valid for air-gapped deployments and the dev-default for offline iteration.                                                           |

### 3.4 Files **not** touched (out of scope)

- [`agent_runtime/pricing/calculator.py`](../../src/agent_runtime/pricing/calculator.py) — semantics frozen.
- [`agent_runtime/budgets/`](../../src/agent_runtime/budgets/) — entire directory.
- All existing `RuntimeRunUsageRecord` / `RuntimeModelCallUsageRecord` rows — byte-identical.

---

## 4. Approach

### 4.1 Phasing

Land in three steps. Each step is one PR. Don't bundle.

**Step 1 — Add LiteLLM source as observation-only.**

- Add `litellm` to `requirements.txt`. (No slim variant exists today; pricing data ships in the main package.)
- Implement `LiteLLMPricingSource.load_all() -> tuple[ModelPricingRecord, ...]`.
- New CLI: `python -m agent_runtime.pricing.compare_litellm` prints a diff between the current `ModelPricingCatalog` and what LiteLLM says for every `(provider, model_name)` we currently track.
- Add the seed-parity test (see §3.1). Run it in CI; if it fails, the team chooses whether to update the YAML seed (current world) or accept LiteLLM as more current (new world).
- **No change to runtime behavior.** Production still loads from `seeds/`.

**Step 2 — Switch source-of-truth to LiteLLM.**

- Implement `OverrideSource` and merge logic (`overrides > litellm > yaml-seed`).
- Add `PricingComposer.load(primary_source: Literal["litellm", "yaml"])` that runs the merge and yields records.
- Update `scripts/usage/seed_pricing.py` to call the composer.
- Migrate existing seeds: for any `(provider, model_name)` in `seeds/` whose values diverge from LiteLLM by more than the tolerance band, write an entry into `pricing_overrides.yaml` so legacy values continue exactly. The PRD reviewer signs off on the override list before this PR merges. New models go through LiteLLM.
- `effective_from` for new LiteLLM-sourced rows is the ingest timestamp (truncated to minute). Re-running the script closes prior `effective_until` of any matching row whose values changed, then inserts the new row — same idempotent dance the YAML loader does today.
- Production now loads from LiteLLM at startup; air-gapped boot sets `pricing_primary_source="yaml"`.

**Step 3 — Add refresh loop (optional).**

- Implement `PricingRefreshLoop` in the worker (parallel to the `UsageRollupLoop` pattern). Default schedule: daily.
- On change: log a structured event (`pricing.upstream_changed`) with the old and new values; never silently update without an alert path.
- Feature flag default off until staging validates.

### 4.2 LiteLLM data → `ModelPricingRecord` conversion

LiteLLM exposes `litellm.model_cost` as a `dict[str, dict[str, Any]]`. Each row carries fields like `input_cost_per_token`, `output_cost_per_token`, `cache_creation_input_token_cost`, `cache_read_input_token_cost`, `output_reasoning_token_cost`, `litellm_provider`, `mode`, `max_tokens`, `max_input_tokens`.

Mapping (matches `seed_loader.py`'s output shape):

| LiteLLM field                        | `ModelPricingRecord` field      | Notes                                                                                        |
| ------------------------------------ | ------------------------------- | -------------------------------------------------------------------------------------------- |
| key (e.g. `claude-opus-4-7`)         | `model_name`                    | Strip provider prefix where LiteLLM uses one (e.g. `anthropic/claude-...` → `claude-...`).   |
| `litellm_provider`                   | `provider`                      | Lowercase canonical (`anthropic`, `openai`, `google`, etc.).                                 |
| _hardcoded_                          | `region`                        | Always `"global"` for LiteLLM-sourced rows; LiteLLM has no region dimension.                 |
| `input_cost_per_token`               | `input_per_1m_micro_usd`        | `int(round_banker(value * 1_000_000 * 1_000_000))`.                                          |
| `output_cost_per_token`              | `output_per_1m_micro_usd`       | Same conversion.                                                                             |
| `cache_read_input_token_cost`        | `cached_input_per_1m_micro_usd` | Same conversion. `None` when LiteLLM doesn't ship the field.                                 |
| `max_input_tokens` (or `max_tokens`) | `context_window_tokens`         | Pass through as int.                                                                         |
| `output_reasoning_token_cost`        | _(dropped, logged)_             | No active billing column; we log the field's presence so the reasoning-billing PRD has data. |
| _set by source_                      | `pricing_source`                | `"litellm"`.                                                                                 |
| _set by source_                      | `pricing_version`               | `f"litellm-{ingest_date.isoformat()}"` (or pin to the LiteLLM package version once stable).  |
| _set by source_                      | `effective_from`                | Ingest timestamp truncated to minute.                                                        |
| _set by source_                      | `effective_until`               | `None` initially; set when the next ingest closes this row.                                  |

The conversion `int(round_banker(value * 1e6 * 1e6))` MUST match `CostCalculator`'s rounding. Implementation should reuse a shared helper or assert byte-equality in a property test.

> **`mode` filter.** LiteLLM rows with `mode in {"embedding", "image_generation", "audio_transcription"}` are skipped — we don't bill them via this catalog. Only `chat`, `completion`, `responses`, and unmarked rows are ingested.

### 4.3 Override mechanism

```yaml
# services/ai-backend/config/pricing_overrides.yaml
overrides:
  - provider: anthropic
    model_name: claude-finetuned-internal
    region: global
    input_per_1m_micro_usd: 12_000_000
    output_per_1m_micro_usd: 60_000_000
    cached_input_per_1m_micro_usd: 1_200_000
    context_window_tokens: 200_000
    reason: "Internal fine-tune; not in LiteLLM catalog"
    # `pricing_source` is set to "override" by the loader.
    # `effective_from` defaults to the ingest run if omitted.
```

Overrides are merged after LiteLLM (and after YAML seeds in air-gapped mode) and replace any matching `(provider, model_name, region)` row. The `reason` field is required at load time. Each ingest run logs the active override set with reasons for audit.

DB-resident overrides (admin-editable without redeploy) are out of scope for this PRD; called out in [§9](#9-open-questions).

---

## 5. Behaviors preserved

Each must be a pinned test before merge.

| Behavior                                                                                                                                                                                                                        | How preserved                                                                                                                                                                           |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Cost stamped at write time                                                                                                                                                                                                      | `RuntimeModelCallUsageRecord` insert path is untouched; pricing source change only affects which `ModelPricingRecord` rows exist at lookup time.                                        |
| Pricing changes never retroactively rewrite history                                                                                                                                                                             | Refresh loop creates new `ModelPricingRecord` rows with new `effective_from` and closes prior `effective_until`. Never touches `RuntimeRunUsageRecord` / `RuntimeModelCallUsageRecord`. |
| `effective_from` / `effective_until` time-keying                                                                                                                                                                                | Composer respects the existing close-prior-then-insert idiom. Test asserts that an existing usage row's `pricing_id` (if persisted) still resolves the same way.                        |
| Integer micro-USD with banker's rounding                                                                                                                                                                                        | `LiteLLMPricingSource` uses the same `Decimal`/`ROUND_HALF_EVEN` math as `CostCalculator._token_cost`. Property test enforces byte-equality across a generated matrix.                  |
| `BudgetCharger.charge_run` CAS idempotency                                                                                                                                                                                      | Charger is not touched.                                                                                                                                                                 |
| `ModelPricingRecord` schema (`input_per_1m_micro_usd`, `output_per_1m_micro_usd`, `cached_input_per_1m_micro_usd`, `context_window_tokens`, `region`, `pricing_source`, `pricing_version`, `effective_from`, `effective_until`) | Schema unchanged. No new columns.                                                                                                                                                       |
| Existing pricing rows pre-migration                                                                                                                                                                                             | Migration writes overrides for any divergence from LiteLLM, so the **active** row at switchover reads identically before and after. Historical closed rows are not touched.             |
| `ModelPricingCatalog` LRU cache (size 256, minute-floored key)                                                                                                                                                                  | Cache invalidation triggered after any composer run that wrote new rows.                                                                                                                |
| `ConversationContextBuilder.headroom_pct`                                                                                                                                                                                       | Unaffected — reads context window from `ModelConfig`, not pricing rows.                                                                                                                 |
| `pricing_source="yaml-seed"` rows already in the DB                                                                                                                                                                             | Untouched. Their `effective_until` only closes when a newer LiteLLM-sourced row supersedes them; closed rows are kept indefinitely.                                                     |

---

## 6. Risks and mitigations

| Risk                                                                         | Likelihood | Impact | Mitigation                                                                                                                                                                                                    |
| ---------------------------------------------------------------------------- | ---------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LiteLLM publishes a wrong rate (typo, source confusion)                      | Medium     | High   | Refresh-loop diff alert. Sanity bounds per provider (a Claude rate jumping 100× triggers a refusal). Manual review for any change > 25% in either direction.                                                  |
| LiteLLM removes a model we still serve                                       | Low        | Medium | Override mechanism keeps the model in our catalog regardless of upstream presence. Refresh loop logs a `pricing.upstream_removed` event but does not delete or close the existing active row.                 |
| LiteLLM dependency adds significant install footprint                        | Medium     | Low    | LiteLLM ships only as a single package today. Footprint is acceptable for the value; revisit if it adds heavy transitive deps. As a fallback, vendor the JSON file directly.                                  |
| Conversion arithmetic disagrees with `CostCalculator.compute` rounding       | Medium     | High   | Property test asserts byte-equality across a representative usage matrix. Failure blocks merge. Reuse `CostCalculator._token_cost`'s `Decimal`/`ROUND_HALF_EVEN` rather than reimplementing.                  |
| LiteLLM upstream refresh triggers an unintended pricing change in prod       | Medium     | High   | Refresh is feature-flagged off by default. When enabled, changes log + alert; an `auto_apply=false` mode lets ops review before the new value takes effect.                                                   |
| Air-gapped deployment can't fetch LiteLLM data                               | Low        | Medium | YAML seeds remain shipped in `seeds/`. `pricing_primary_source="yaml"` documented for offline deploys.                                                                                                        |
| Network flake on startup if remote fetch is required                         | Medium     | Medium | Default to using `litellm.model_cost` (in-package data, no network). Optional remote refresh comes from the worker loop, not the API startup path.                                                            |
| Custom fine-tune models silently bill at LiteLLM's base-model rate           | Medium     | High   | Override required for any fine-tune (enforced by an ingest-time check that compares the model name against a known-base-model regex and refuses ingest if a name looks like a fine-tune without an override). |
| LiteLLM ingestion of a `mode: embedding` row pollutes the chat catalog       | Medium     | Low    | Filter `mode in {"chat", "completion", "responses", None}`. Test asserts.                                                                                                                                     |
| LiteLLM data has a different model-name canonicalization than our YAML seeds | Medium     | High   | Step 1 `compare_litellm` CLI surfaces every key mismatch. Resolved by adding a `model_name_aliases` map in the LiteLLM source, with each alias documented in code.                                            |
| Closing prior `effective_until` accidentally overlaps an active LLM call     | Low        | High   | The composer takes a single ingest timestamp and uses it for both `effective_from` of the new row and `effective_until` of the prior row. Test asserts no usage record's `at` falls in the gap.               |

---

## 7. Test requirements

Per [`docs/CLAUDE.md`](../CLAUDE.md), unit testing requirements are explicit:

### 7.1 New unit tests

- `test_litellm_source.py`
  - Conversion: known LiteLLM row → expected `ModelPricingRecord`. One case per provider currently in `seeds/` (anthropic, google, openai).
  - Banker's rounding parity: byte-equality with `CostCalculator._token_cost` on a generated matrix of `(token_count, per_token_usd)` pairs.
  - Missing `cache_read_input_token_cost` → `cached_input_per_1m_micro_usd` is `None` (not 0).
  - `mode` filter: rows with `mode="embedding"` / `mode="image_generation"` / `mode="audio_transcription"` are skipped.
  - `output_reasoning_token_cost` present → field is dropped, a structured log line names the model so the future reasoning-billing PRD has data.
  - `model_name_aliases` map produces the canonical model name our seeds and runtime use.

- `test_overrides.py`
  - Override wins over LiteLLM (same `(provider, model_name, region)` → override row used).
  - Missing-override + missing-LiteLLM + present-YAML → YAML used (air-gapped + LiteLLM-gap path).
  - Missing-override + missing-LiteLLM + missing-YAML → row excluded; subsequent lookup returns `None`.
  - Override missing `reason` field is rejected at load.
  - Override entry sets `pricing_source="override"` on the produced `ModelPricingRecord`.

- `test_seed_parity.py`
  - For every `(provider, model_name)` currently in `seeds/*.yaml`, assert LiteLLM produces matching `input_per_1m_micro_usd`, `output_per_1m_micro_usd`, `cached_input_per_1m_micro_usd`, `context_window_tokens` within the per-provider tolerance band, OR an override entry exists in `pricing_overrides.yaml` with a `reason` explaining the divergence.

### 7.2 Property tests

- `test_rounding_property.py`
  - `forall (per_token_usd, token_count): cost_via_new_path == cost_via_calculator(record_via_new_path, usage)`. Hypothesis-driven, 1000 cases.

### 7.3 Integration tests

- `test_refresh_loop.py`
  - Refresh loop updates an existing row when LiteLLM changes; emits the expected `pricing.upstream_changed` event.
  - Refresh loop creates a new row when LiteLLM adds a model.
  - Refresh loop never touches `RuntimeRunUsageRecord` or `RuntimeModelCallUsageRecord`.
  - Refresh loop respects `auto_apply=false` (logs change, does not write).

- `test_startup_air_gapped.py`
  - With `pricing_source=fallback_only`, startup loads only from `fallback_seeds.toml` and never imports `litellm.model_cost`.

### 7.4 Snapshot / golden tests

- `test_pricing_catalog_snapshot.py`
  - Run the migration logic on the current `seeds/` content and assert the resulting `ModelPricingCatalog` has _exactly_ the same `(model, input, output, cached_input, reasoning)` tuples as production today. This is the no-regression gate.

### 7.5 Tests that must already pass (regression)

- All tests under `tests/unit/budgets/` (charger, enforcer, estimator, reservations, period).
- All tests that compute cost on a representative usage record.
- `f9-usage-metrics` flow integration tests (the per-conversation `/context` endpoint, the per-user / per-org / per-connector `/v1/usage/*` endpoints).

---

## 8. Rollout / rollback

### 8.1 Rollout

1. **Step 1 PR — observation-only.** Merge with `pricing_primary_source="yaml"` still in effect. Run the `compare_litellm` CLI in CI for two weeks; let the team see the diff trend.
2. **Step 2 PR — switch source.** Default `pricing_primary_source="litellm"` in dev and staging first. Monitor cost rows for one week. Promote to prod under the toggle. Override file ships with the PR; reviewer sign-off on every override entry required.
3. **Step 3 PR — refresh loop.** Land with `PRICING_REFRESH_ENABLED=false` default. Enable in staging for one week with `auto_apply=false`. If diffs are sane, flip `auto_apply=true` in staging. Repeat in prod.

### 8.2 Rollback

- Step 1: no rollback needed.
- Step 2: flip `pricing_primary_source="yaml"` and the existing `seeds/` quarterly YAMLs are the source again. The DB still has the LiteLLM-sourced rows from the bad ingest; close them via a one-off script if necessary, but **never delete** — `effective_until` close is enough.
- Step 3: flip `PRICING_REFRESH_ENABLED=false`.

Rollback in any step is a config flip; no schema migration to reverse.

### 8.3 Observability for the rollout

Emit (and dashboard) these structured-log events. They flow through the existing `RuntimeLogger` (no new logger).

- `pricing.startup_loaded` — counts of `(pricing_source=litellm, override, yaml-seed)` rows.
- `pricing.upstream_changed` — provider, model_name, old, new, magnitude, action_taken.
- `pricing.upstream_removed` — provider, model_name, last-seen-at.
- `pricing.override_applied` — provider, model_name, reason.
- `pricing.mode_filtered` — provider, model_name, mode (for ingestion visibility).
- `pricing.reasoning_field_dropped` — provider, model_name, value (so the future reasoning-billing PRD has data).

---

## 9. Open questions

These must be resolved before the corresponding step can land.

- **LiteLLM dependency surface.** Does pulling in `litellm` add unacceptable transitive deps? Audit the install footprint in Step 1. If it's heavy, vendor `model_prices_and_context_window.json` directly and skip the package import.
- **Refresh source.** Use `litellm.model_cost` (in-package, requires LiteLLM package upgrade for new data) vs fetch the JSON from GitHub at runtime (always fresh, network dependency). Default proposal: in-package data, with a `pip install -U litellm` on a schedule rather than runtime HTTP.
- **DB-resident overrides.** Worth opening a Phase 4 PRD for an admin-editable override table? Decide after Step 2 lands; if file-based overrides cover all real cases, defer indefinitely.
- **Tolerance bands per provider.** What divergence between LiteLLM and our YAML seeds counts as "drift" vs "actively wrong"? Suggested defaults: 0.1% for Anthropic / OpenAI / Google, 1% for the long tail. Review in Step 1.
- **Sanity bound for refresh-loop changes.** Magnitude threshold above which an automatic apply is refused. Suggested: > 25% in either direction triggers `auto_apply=false` regardless of the global setting.
- **Custom-model fine-tune detection.** The proposal is to refuse ingest if a model name matching a known fine-tune pattern lacks an override. Confirm the actual naming convention(s) before locking the regex.
- **Reasoning-token billing PRD.** This PRD intentionally drops `output_reasoning_token_cost`. When does the follow-up PRD that adds `reasoning_per_1m_micro_usd` to `ModelPricingRecord` and a `reasoning_tokens` argument to `CostCalculator.compute` ship? It needs to land before any provider where reasoning tokens are a non-negligible fraction of cost.
- **`region` field future.** All current rows are `region="global"`. Is the column worth keeping if no LiteLLM source ever fills it? Decide after Step 2; the answer affects whether the lookup key can drop a dimension.
- **`scripts/usage/seed_pricing.py` exact path.** Verify this script exists and is the upsert entry point before Step 1.

---

## 10. Done definition

- All tests in §7 added and green.
- Step 1 has shipped to prod; the `compare_litellm` CLI is runnable and reports the parity diff.
- Step 2 has shipped to prod; `pricing_primary_source="litellm"` is the default; `pricing_overrides.yaml` is reviewed and committed.
- (Optional) Step 3 has shipped with refresh on `auto_apply=true` in at least one production environment.
- The `seeds/` directory remains as the documented air-gapped fallback. (Pruning it is a follow-up Step 4 only after a full quarter of stable LiteLLM-sourced operation.)
- This PRD is moved to `Status: Shipped` and the [roadmap](00-roadmap.md) status checkbox flipped.
