# PR 13 — B3: Pricing Catalog and Cost Calculation

**Spec ID:** B3 | **Track:** Token Usage | **Wave:** 3 (Parallel) | **Estimated effort:** M
**Depends on:** B1 (run usage), B2 (per-call usage), C2
**Required for:** B4 (rollups expose cost), B6 (/usage UI), B7 (budgets in micro-USD)

---

## 1. Functional Specification

### 1.1 Goal

Add a versioned pricing catalog and compute cost per usage row in micro-USD integers. Pricing is per provider × model × region × effective_from. Cost is snapshotted onto the usage row via `pricing_id` so retroactive price changes never mutate historical cost.

### 1.2 User-visible behavior

- **Operator:** can seed pricing from a YAML file or override per-row via a small admin endpoint.
- **End user/Admin:** the `cost_micro_usd` column starts populating on usage rows. UI exposes this in B6.
- **Single-tenant deploys without billing:** can leave pricing unseeded; `cost_micro_usd` stays NULL and downstream code is null-safe.

### 1.3 Out of scope

- Pricing per fine-grained features (function calls, tool surcharges).
- Currency conversion — micro-USD only.
- Real-time pricing feeds.
- Customer-facing invoices.

---

## 2. Technical Specification

### 2.1 Architecture

- Cost in `BIGINT micro_usd` everywhere. `1 USD = 1_000_000 micro_usd`. No floats anywhere on the persistence path.
- Round-half-to-even at the micro-USD boundary.
- Versioned pricing: every row has `pricing_id` and `pricing_version`. Re-pricing today's model never mutates yesterday's cost.
- YAML seed files version-controlled per quarter; operator runs `seed_pricing.py` to upsert.

### 2.2 Schema changes

Migration `services/ai-backend/migrations/0005_model_pricing.sql`:

```sql
CREATE TABLE model_pricing (
    id                                 TEXT PRIMARY KEY,
    provider                           TEXT NOT NULL,
    model_name                         TEXT NOT NULL,
    region                             TEXT NOT NULL DEFAULT 'global',
    effective_from                     TIMESTAMPTZ NOT NULL,
    effective_until                    TIMESTAMPTZ,
    input_per_1m_micro_usd             BIGINT NOT NULL CHECK (input_per_1m_micro_usd >= 0),
    output_per_1m_micro_usd            BIGINT NOT NULL CHECK (output_per_1m_micro_usd >= 0),
    cached_input_per_1m_micro_usd      BIGINT,
    context_window_tokens              INTEGER,
    pricing_source                     TEXT NOT NULL CHECK (pricing_source IN ('yaml-seed','admin-override','partner-feed')),
    pricing_version                    TEXT NOT NULL,
    created_at                         TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_model_pricing_lookup
    ON model_pricing (provider, model_name, region, effective_from DESC);
CREATE UNIQUE INDEX idx_model_pricing_active
    ON model_pricing (provider, model_name, region) WHERE effective_until IS NULL;

ALTER TABLE runtime_run_usage
    ADD COLUMN cost_micro_usd BIGINT,
    ADD COLUMN pricing_id TEXT REFERENCES model_pricing(id),
    ADD COLUMN pricing_version TEXT;

ALTER TABLE runtime_model_call_usage
    ADD COLUMN cost_micro_usd BIGINT,
    ADD COLUMN pricing_id TEXT REFERENCES model_pricing(id),
    ADD COLUMN pricing_version TEXT;
```

### 2.3 Endpoints

**Backend internal:**

- `POST /v1/agent/admin/pricing/seed` (admin scope) — re-runs `seed_pricing.py` from the in-image YAML files.
- `GET /v1/agent/admin/pricing?provider=&model=&at=` — lookup.

(Public endpoints not added; pricing surfaced through B4's `/v1/usage/*` and B5's `/v1/context`.)

### 2.4 Code changes

**New module** `services/ai-backend/src/agent_runtime/pricing/`:

- `catalog.py` — `ModelPricingCatalog.lookup(provider, model, region, at: datetime) -> ModelPricing | None`. Uses LRU cache keyed by `(provider, model, region, at_truncated_to_minute)`.
- `calculator.py` — `CostCalculator.compute(input, output, cached_input, pricing) -> int` (micro-USD; integer math only).
- `seed_loader.py` — reads `services/ai-backend/src/agent_runtime/pricing/seeds/*.yaml`; UPSERT by composite key.
- `seeds/anthropic-2026-q1.yaml`, `seeds/openai-2026-q1.yaml`, `seeds/google-2026-q1.yaml` — declarative; each declares a `pricing_version` semver-ish string.

**New port method:** `lookup_pricing(provider, model, region, at) -> ModelPricing | None`, `upsert_pricing(record) -> None`, `update_run_usage_cost(run_id, pricing_id, pricing_version, cost_micro_usd)`.

**Worker hook in [services/ai-backend/src/runtime_worker/handlers/run.py](../../services/ai-backend/src/runtime_worker/handlers/run.py):**

- After `record_run_usage` (B1): look up pricing, compute cost, call `update_run_usage_cost`.
- Same hook for B2's per-call write path.
- If lookup returns None: leave NULL, emit a structured log + counter (rate-limited so unknown models don't spam).

**New CLI:**

- `services/ai-backend/scripts/usage/seed_pricing.py` — operator-run; idempotent.
- `services/ai-backend/scripts/usage/backfill_run_usage_cost.py` — separate from B1's backfill: walks rows where `cost_micro_usd IS NULL`, looks up pricing as-of `completed_at`, writes cost. Opt-in.

**YAML seed schema:**

```yaml
pricing_version: "anthropic-2026-q1.v1"
provider: anthropic
prices:
  - model_name: claude-opus-4-7
    region: global
    effective_from: 2026-01-01T00:00:00Z
    input_per_1m_micro_usd: 15_000_000     # $15.00 per 1M
    output_per_1m_micro_usd: 75_000_000    # $75.00 per 1M
    cached_input_per_1m_micro_usd: 1_500_000
    context_window_tokens: 1_000_000
  ...
```

### 2.5 Trust model & failure semantics

- Pricing is treated as configuration, not user-visible state. Wrong pricing → wrong cost → operator fixes via re-seed + opt-in backfill.
- Calculator never raises; on any inputs it returns 0 (with NULL pricing → NULL cost on the row).
- Round-half-to-even ensures cost is deterministic across runs.

### 2.6 Tenant isolation

N/A — pricing is global.

### 2.7 Observability

- Metric: `pricing_lookup_total{outcome=hit|miss}` — high `miss` count signals a model the seeds don't cover.
- Metric: `cost_calculated_total`.
- Audit: `pricing.seeded`, `pricing.admin_override` written to `runtime_audit_log`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Re-pricing today's model (insert new row with later `effective_from`) does NOT mutate yesterday's `runtime_run_usage.cost_micro_usd`.
- [ ] `cached_input_per_1m_micro_usd IS NULL` falls back to input price at calculation time.
- [ ] Unknown model → `cost_micro_usd` stays NULL on the row, `pricing_lookup_total{outcome=miss}` increments.
- [ ] `seed_pricing.py` is idempotent — running twice yields no diffs.
- [ ] Backfill cost script is opt-in and idempotent.

### 3.2 Test plan

**Unit:**

- `test_cost_calculator_integer_math` — no float drift across 1M iterations.
- `test_round_half_to_even`.
- `test_cached_input_falls_back_to_input_price` — when cached price is NULL.
- `test_catalog_lookup_picks_right_effective_from`.
- `test_future_dated_pricing_ignored`.
- Property test: `cost(input=N, output=0, …)` is monotonic in N.

**Integration:**

- Insert pricing v1 → run completes → cost computed.
- Insert pricing v2 (later effective_from) → new run uses v2 → existing run unchanged.
- Backfill script: run, observe rows updated; run again, no change.

### 3.3 Compliance evidence produced

- Pricing change auditability via versioned table + audit rows.

### 3.4 Rollout plan

- Migration adds nullable cost columns (no data change).
- Operator seeds pricing.
- Worker starts populating cost on new rows.
- Backfill script optional.

### 3.5 Backout plan

Drop the cost columns + `model_pricing` table via rollback. Cost calculation becomes a no-op without the catalog.

### 3.6 Definition of done

- [ ] Migration 0005 applied.
- [ ] Three provider YAML seeds checked in.
- [ ] `seed_pricing.py` and `backfill_run_usage_cost.py` written + tested.
- [ ] Worker hook computes cost on new rows.
- [ ] Cost reconciliation test: sum of per-call costs = run-level cost (rounding modulo).

---

## 4. Critical files

- New: `services/ai-backend/migrations/0005_model_pricing.sql` (+ rollback)
- New: `services/ai-backend/src/agent_runtime/pricing/{catalog,calculator,seed_loader}.py`
- New: `services/ai-backend/src/agent_runtime/pricing/seeds/{anthropic,openai,google}-2026-q1.yaml`
- New: `services/ai-backend/scripts/usage/seed_pricing.py`
- New: `services/ai-backend/scripts/usage/backfill_run_usage_cost.py`
- Modify: persistence ports + adapters
- Modify: [services/ai-backend/src/runtime_worker/handlers/run.py](../../services/ai-backend/src/runtime_worker/handlers/run.py)
