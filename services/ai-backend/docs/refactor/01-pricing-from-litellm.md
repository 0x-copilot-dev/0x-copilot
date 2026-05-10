# Refactor PRD — Pricing source from LiteLLM (Phase 3 / P12)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §1.6](../architecture/refactor-audit.md#16-custom-budget--pricing-system--seed-catalog), [roadmap P12](00-roadmap.md#phase-3--library-replacements-independent)

---

## 1. Problem

The runtime maintains a hand-curated model pricing catalog under [`agent_runtime/pricing/`](../../src/agent_runtime/pricing/):

- [`calculator.py`](../../src/agent_runtime/pricing/calculator.py) — `CostCalculator.compute(usage, pricing)` returns integer micro-USD with banker's rounding.
- [`catalog.py`](../../src/agent_runtime/pricing/catalog.py) — `ModelPricingCatalog` (in-memory lookup over `ModelPricingRecord` rows).
- [`seed_loader.py`](../../src/agent_runtime/pricing/seed_loader.py) — loads bundled seed data into Postgres at startup.
- [`seeds/`](../../src/agent_runtime/pricing/seeds/) — JSON / TOML files with per-model rows carrying `input_per_1m_micro_usd`, `output_per_1m_micro_usd`, `cached_input_per_1m_micro_usd`, and `reasoning_per_1m_micro_usd`.

Every time a provider releases a new model (Claude 4.7, GPT-5.x, Gemini 3.0, etc.) someone has to:

1. Find the published per-token rate.
2. Convert to micro-USD per million tokens.
3. Add a row to `seeds/`.
4. Run the seed loader on staging and prod.

This is recurring tax. It's also error-prone — provider docs publish rates as $/1M tokens, $/1K tokens, or $/token depending on the page; the integer-rounding step is bespoke; reasoning-token rates are listed in different doc sections from headline rates.

[LiteLLM](https://github.com/BerriAI/litellm) maintains [`model_prices_and_context_window.json`](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) — a community-maintained file with `input_cost_per_token`, `output_cost_per_token`, `cache_creation_input_token_cost`, `cache_read_input_token_cost`, `output_reasoning_token_cost`, plus context window and provider tags for every Claude, OpenAI, Gemini, Mistral, Cohere, etc. model. New models typically land within days of provider announcement.

### Symptoms (today)

- Seeds drift behind provider catalogs by 1–4 weeks per release.
- Per-conversion arithmetic is duplicated in `seed_loader.py` and in any analytics or finance scripts that compute model cost out-of-band.
- Reasoning-token rate maintenance is a separate ritual from the headline rate update.
- Adding a new provider requires touching multiple files (`seeds/`, possibly `catalog.py` if the schema needs new fields).

### What this is NOT

- Not a change to [`agent_runtime/budgets/`](../../src/agent_runtime/budgets/). `BudgetCharger`, `BudgetEnforcer`, `BudgetEstimator`, `BudgetReservations`, `Period` all stay.
- Not a change to `CostCalculator.compute` semantics. Integer micro-USD with banker's rounding stays exactly.
- Not a change to `ModelPricingRecord` schema. Existing rows must continue to validate.
- Not a change to historical cost rows in `RuntimeRunUsageRecord` / `RuntimeModelCallUsageRecord`. Pricing-source changes must never retroactively rewrite history (see [f9-usage-metrics](../architecture/f9-usage-metrics.puml)).
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

- `agent_runtime/pricing/seeds/` is removed for upstream-supported models. **Bundled fallback seeds remain** for the air-gapped / offline-startup path (see [§7](#7-rollout--rollback) for the override hierarchy).
- A new `LiteLLMPricingSource` (or equivalently named) ingests LiteLLM data and writes/updates `ModelPricingRecord` rows.
- Custom-model override path exists: a `pricing_overrides.toml` (or DB-resident override rows) takes precedence over LiteLLM data.
- Refresh strategy: configurable schedule (default daily), gated behind a feature flag, with structured-log diff output when an upstream rate changes for a model that's already in the table.
- Pre-existing rows in `RuntimeRunUsageRecord` / `RuntimeModelCallUsageRecord` are byte-identical before and after the migration.
- All pricing-related tests pass; new test confirms LiteLLM ingestion produces values identical to the current seed catalog for every model in `seeds/` today.

---

## 3. Systems touched

Inventory derived from the cluster diagrams ([C8b cross-cutting](../architecture/11-cross-cutting.puml)) and the audit. **Verify exact paths and LOC at implementation time.**

### 3.1 Files added

| File                                             | Purpose                                                                                                                                                                       |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent_runtime/pricing/litellm_source.py`        | Loads `litellm.model_cost` (or fetches the upstream JSON), converts per-token rates → integer micro-USD per million, returns a list of candidate `ModelPricingRecord` writes. |
| `agent_runtime/pricing/overrides.py`             | Loads custom overrides from a project-shipped TOML and DB-resident rows. Override values win over LiteLLM data.                                                               |
| `agent_runtime/pricing/refresh_loop.py`          | Worker-hosted refresh job (similar shape to `usage_rollup_loop.py`). Optional — only runs when `PRICING_REFRESH_ENABLED=true`.                                                |
| `tests/unit/pricing/test_litellm_source.py`      | Conversion correctness, rounding parity, override precedence.                                                                                                                 |
| `tests/unit/pricing/test_seed_parity.py`         | Snapshot test: existing seed catalog matches LiteLLM-sourced values within tolerance bands defined per provider.                                                              |
| `tests/integration/pricing/test_refresh_loop.py` | Refresh job updates existing rows, creates new rows, never mutates `RuntimeModelCallUsageRecord` history.                                                                     |

### 3.2 Files removed

| File                                                                                                                  | Reason                                                                                                                                                    |
| --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Most of [`agent_runtime/pricing/seeds/`](../../src/agent_runtime/pricing/seeds/)                                      | Replaced by LiteLLM data. Keep a minimal `fallback_seeds.toml` covering the model set that must boot in air-gapped mode (see [§7](#7-rollout--rollback)). |
| Per-conversion arithmetic in [`agent_runtime/pricing/seed_loader.py`](../../src/agent_runtime/pricing/seed_loader.py) | Replaced by `litellm_source.py` conversion logic. The loader entry point may stay; what disappears is the bespoke "$/token → micro-USD per million" math. |

### 3.3 Files changed

| File                                                                                                | Change                                                                                                                                                                                                                        |
| --------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/pricing/catalog.py`](../../src/agent_runtime/pricing/catalog.py)                    | Catalog gains a `provenance` field on each row (`"litellm"` / `"override"` / `"fallback"`) for diff logging and ops visibility. Schema field on `ModelPricingRecord` if persisted; otherwise transient on the catalog object. |
| [`agent_runtime/pricing/seed_loader.py`](../../src/agent_runtime/pricing/seed_loader.py)            | Becomes a thin adapter that calls `LiteLLMPricingSource.load()` then `OverrideSource.load()` and merges. Bundled fallback path retained for offline boot.                                                                     |
| [`runtime_worker/__main__.py`](../../src/runtime_worker/__main__.py) and worker dependencies wiring | Optionally register `refresh_loop` when feature flag is enabled.                                                                                                                                                              |
| [`agent_runtime/deployment/profile.py`](../../src/agent_runtime/deployment/profile.py)              | Add `pricing_source` toggle: `{"litellm", "fallback_only"}`. Default `litellm` in production; `fallback_only` valid for air-gapped deployments.                                                                               |

### 3.4 Files **not** touched (out of scope)

- [`agent_runtime/pricing/calculator.py`](../../src/agent_runtime/pricing/calculator.py) — semantics frozen.
- [`agent_runtime/budgets/`](../../src/agent_runtime/budgets/) — entire directory.
- All existing `RuntimeRunUsageRecord` / `RuntimeModelCallUsageRecord` rows — byte-identical.

---

## 4. Approach

### 4.1 Phasing

Land in three steps. Each step is one PR. Don't bundle.

**Step 1 — Add LiteLLM source as observation-only.**

- Add `litellm` (or `litellm-pricing` if we don't need the rest of LiteLLM) to `requirements.txt`.
- Implement `LiteLLMPricingSource.load() → list[CandidatePricingRow]`.
- New CLI: `python -m agent_runtime.pricing.compare_litellm` prints a diff between the current `ModelPricingCatalog` and what LiteLLM says.
- Add the seed-parity test (see §3.1). Run it in CI; if it fails, the team chooses whether to update the seed (current world) or accept LiteLLM as more current (new world).
- **No change to runtime behavior.** Production still loads from `seeds/`.

**Step 2 — Switch source-of-truth to LiteLLM.**

- Implement `OverrideSource` and merge logic (`overrides win > litellm > fallback`).
- Switch `seed_loader.py` to call the new merge.
- Migrate existing seeds: for any model in `seeds/` whose values diverge from LiteLLM by more than the tolerance band, write a row into `pricing_overrides.toml` so legacy values continue exactly. The PRD reviewer signs off on the override list before this PR merges. New models go through LiteLLM.
- Production now loads from LiteLLM at startup; air-gapped boot uses `fallback_seeds.toml`.

**Step 3 — Add refresh loop (optional).**

- Implement `RefreshLoop` in the worker. Default schedule: daily.
- On change: log a structured event (`pricing.upstream_changed`) with the old and new values; never silently update without an alert path.
- Feature flag default off until staging validates.

### 4.2 LiteLLM data → `ModelPricingRecord` conversion

LiteLLM exposes `litellm.model_cost` (a `dict[str, dict[str, Any]]`). Each row carries fields like `input_cost_per_token`, `output_cost_per_token`, `cache_creation_input_token_cost`, `cache_read_input_token_cost`, `output_reasoning_token_cost`, `litellm_provider`, `mode`.

Conversion (verify against [`pricing/calculator.py`](../../src/agent_runtime/pricing/calculator.py) at implementation time):

```
per_million_micro_usd = round_banker(per_token_usd * 1_000_000 * 1_000_000)
```

The `1_000_000 × 1_000_000` is "USD → micro-USD" times "per token → per million tokens." Rounding mode must match `CostCalculator`'s rounding (banker's). Implement the rounding once, in `LiteLLMPricingSource`, and assert byte-equality against `CostCalculator.compute`'s rounding step in a property test.

### 4.3 Override mechanism

```toml
# packages or services/ai-backend/config/pricing_overrides.toml
[overrides."anthropic/claude-finetuned-internal"]
input_per_1m_micro_usd = 12_000_000
output_per_1m_micro_usd = 60_000_000
cached_input_per_1m_micro_usd = 1_200_000
reasoning_per_1m_micro_usd = 60_000_000
reason = "Internal fine-tune; not in LiteLLM catalog"
provenance = "override"
```

Overrides are loaded after LiteLLM and replace any matching row. The `reason` field is required and goes into the `ops_log` when a row is overridden — keeps deviations auditable.

DB-resident overrides (a future option) would live in a `pricing_override` table and could be edited via an admin endpoint without a redeploy. Out of scope for this PRD; called out in [§9](#9-open-questions).

---

## 5. Behaviors preserved

Each must be a pinned test before merge.

| Behavior                                                                              | How preserved                                                                                                                               |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Cost stamped at write time                                                            | `RuntimeModelCallUsageRecord` insert path is untouched; pricing source change only affects the rows we persist on startup.                  |
| Pricing changes never retroactively rewrite history                                   | Refresh loop updates `ModelPricingRecord` rows; never touches `RuntimeRunUsageRecord` or `RuntimeModelCallUsageRecord`. Test asserts.       |
| Integer micro-USD with banker's rounding                                              | Conversion module reuses `CostCalculator`'s rounding step (or an extracted shared helper). Property test enforces byte-equality.            |
| `BudgetCharger.charge_run` CAS idempotency                                            | Charger is not touched.                                                                                                                     |
| Reasoning tokens billed via separate column when provider differentiates              | LiteLLM exposes `output_reasoning_token_cost`; we map it to `reasoning_per_1m_micro_usd`. When LiteLLM is silent, we leave the column NULL. |
| `ModelPricingRecord` schema (input, output, cached_input, reasoning per-1m micro-USD) | Schema unchanged.                                                                                                                           |
| Existing pricing rows pre-migration                                                   | Migration writes overrides for any divergence from LiteLLM, so the active row reads identically before and after.                           |
| `ConversationContextBuilder.headroom_pct`                                             | Unaffected — that path reads context window from `ModelConfig`, not pricing rows.                                                           |

---

## 6. Risks and mitigations

| Risk                                                                                  | Likelihood | Impact | Mitigation                                                                                                                                                   |
| ------------------------------------------------------------------------------------- | ---------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| LiteLLM publishes a wrong rate (typo, source confusion)                               | Medium     | High   | Refresh-loop diff alert. Sanity bounds per provider (a Claude rate jumping 100× triggers a refusal). Manual review for any change > 25% in either direction. |
| LiteLLM removes a model we still serve                                                | Low        | Medium | Override mechanism keeps the model in our catalog regardless of upstream presence. Refresh loop logs a `pricing.upstream_removed` event.                     |
| LiteLLM dependency adds significant install footprint                                 | Low        | Low    | Pin to a slim variant if available, or vendor only the JSON. The pricing path doesn't need LiteLLM's runtime client code.                                    |
| Conversion arithmetic disagrees with `CostCalculator.compute` rounding                | Medium     | High   | Property test asserts byte-equality across a representative usage matrix. Failure blocks merge.                                                              |
| LiteLLM upstream refresh triggers an unintended pricing change in prod                | Medium     | High   | Refresh is feature-flagged off by default. When enabled, changes log + alert; an `auto_apply=false` mode lets ops review before the new value takes effect.  |
| Air-gapped deployment can't fetch LiteLLM data                                        | Low        | Medium | Bundled `fallback_seeds.toml` with the production-supported model set. `pricing_source=fallback_only` is a documented deployment mode.                       |
| Network flake on startup if remote fetch is required                                  | Medium     | Medium | Default to using `litellm.model_cost` (in-package data, no network). Optional remote refresh comes from the worker loop, not the API startup path.           |
| Custom fine-tune models silently bill at LiteLLM's base-model rate                    | Medium     | High   | Override required for any fine-tune (enforced by a startup check that looks for `*-finetune-*` patterns and refuses startup if no override exists for them). |
| Reasoning-token billing column accidentally NULLed for a model that previously had it | Low        | High   | Migration step writes overrides for any model whose existing row has a non-NULL `reasoning_per_1m_micro_usd`; never silently NULL.                           |

---

## 7. Test requirements

Per [`docs/CLAUDE.md`](../CLAUDE.md), unit testing requirements are explicit:

### 7.1 New unit tests

- `test_litellm_source.py`
  - Conversion: known LiteLLM row → expected `ModelPricingRecord`. One case per provider (anthropic, openai, gemini, fireworks if used).
  - Banker's rounding parity: assert byte-equality with `CostCalculator`'s rounding step on a generated matrix of `(token_count, per_token_rate)` pairs.
  - Missing fields: LiteLLM row without `cache_read_input_token_cost` → `cached_input_per_1m_micro_usd` is None, not 0.
  - Reasoning column: model with `output_reasoning_token_cost` set produces `reasoning_per_1m_micro_usd`; model without it produces None.

- `test_overrides.py`
  - Override wins over LiteLLM.
  - Missing-override + missing-LiteLLM falls through to fallback.
  - Override missing `reason` field is rejected at load.
  - DB-resident override (when added) wins over file-based override.

- `test_seed_parity.py`
  - For every model currently in `seeds/`, assert LiteLLM produces matching values within the per-provider tolerance band, OR an override exists explaining the divergence.

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

1. **Step 1 PR — observation-only.** Merge with `pricing_source=fallback_only` still in effect. Run the `compare_litellm` CLI in CI for two weeks; let the team see the diff trend.
2. **Step 2 PR — switch source.** Default `pricing_source=litellm` in dev and staging first. Monitor cost rows for one week. Promote to prod under the toggle. Override file ships with the PR; reviewer sign-off required.
3. **Step 3 PR — refresh loop.** Land with `PRICING_REFRESH_ENABLED=false` default. Enable in staging for one week with `auto_apply=false`. If diffs are sane, flip `auto_apply=true` in staging. Repeat in prod.

### 8.2 Rollback

- Step 1: no rollback needed.
- Step 2: flip `pricing_source=fallback_only` and the bundled `fallback_seeds.toml` is the source. Restore the pre-migration `seeds/` content if needed.
- Step 3: flip `PRICING_REFRESH_ENABLED=false`.

Rollback in any step is a config flip; no schema migration to reverse.

### 8.3 Observability for the rollout

Emit (and dashboard) these structured-log events:

- `pricing.startup_loaded` — counts of `(provenance=litellm, override, fallback)` rows.
- `pricing.upstream_changed` — model, old, new, magnitude, action_taken.
- `pricing.upstream_removed` — model + last-seen-at.
- `pricing.override_applied` — model + reason (audit trail).

---

## 9. Open questions

These must be resolved before the corresponding step can land.

- **LiteLLM dependency surface.** Does pulling in `litellm` add unacceptable transitive deps? Investigate whether LiteLLM ships a slim "data only" variant, or whether vendoring `model_prices_and_context_window.json` directly is preferable. (Either is fine; pick one in Step 1.)
- **Refresh source.** Use `litellm.model_cost` (in-package, requires LiteLLM upgrade for new data) vs fetch the JSON from GitHub at runtime (always fresh, network dependency). Default proposal: in-package data, with a `pip install -U litellm` on a schedule rather than runtime HTTP.
- **DB-resident overrides.** Worth opening a Phase 4 PRD for an admin-editable override table? Decide after Step 2 lands; if file-based overrides cover all real cases, defer indefinitely.
- **Tolerance bands per provider.** What divergence between LiteLLM and our seeds counts as "drift" vs "actively wrong"? Suggested defaults: 0.1% for Anthropic / OpenAI / Gemini, 1% for the long tail. Review in Step 1.
- **Sanity bound for refresh-loop changes.** Magnitude threshold above which an automatic apply is refused. Suggested: > 25% in either direction triggers `auto_apply=false` regardless of the global setting.
- **Custom-model startup check.** The proposal is to refuse startup if a model name matching `*-finetune-*` (or similar) lacks an override. Confirm the actual naming convention before locking the regex.

---

## 10. Done definition

- All tests in §7 added and green.
- Step 1 has shipped to prod with the `compare_litellm` CLI runnable.
- Step 2 has shipped to prod; `pricing_source=litellm` is the default; the override file is reviewed and committed.
- (Optional) Step 3 has shipped with refresh on `auto_apply=true` in at least one production environment.
- The `seeds/` directory has been pruned to the air-gapped fallback set; every removed file's content is reproducible from LiteLLM + overrides.
- Seed-parity snapshot test has been removed (no longer meaningful once LiteLLM is the source).
- This PRD is moved to `Status: Shipped` and the [roadmap](00-roadmap.md) status checkbox flipped.
