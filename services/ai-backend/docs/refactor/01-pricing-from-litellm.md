# Consolidate model names, pricing/budgets, and token counting onto litellm

**Status:** In progress · **Directive:** replace the home-grown model-catalog / pricing / token implementations with the LangChain-compatible **litellm** library. (This doc also resolves the dangling reference in `config/pricing_overrides.yaml`.)

## Why this is now safe (investigation, 2026-07-21)

The team originally **vendored** litellm's `model_prices.json` and refused to import the package "because litellm pins older openai/pydantic." That reason is **stale**:

- **Dep conflict is gone.** `litellm==1.93.0` requires `openai>=2.20,<3` + `pydantic>=2.10,<3`; the service runs openai 2.46 / pydantic 2.13. `pip install litellm --dry-run` resolves **clean, zero downgrades** (adds only `importlib_metadata`).
- **Coverage is now good.** litellm 1.93.0 `model_cost` has **11 of 12** product models with **real provider prices** (claude-opus-4-8 $5/$25, claude-sonnet-5 $2/$10, gpt-5.6 $5/$30, gpt-5.4-mini $0.75/$4.50, gpt-5 $1.25/$10, gemini-2.5-pro/flash). **Only `gemini-3-flash` is missing.**
- **The seeds were bugs, not markup.** `config/pricing_overrides.yaml` pins values 3–10× higher than litellm, but each `reason` says _"seed value preserved while LiteLLM discrepancy is investigated; verify against provider catalog before removing."_ litellm's prices match the real provider catalogs (gpt-5 = $1.25/$10 is OpenAI's list price). So trusting litellm **fixes an over-billing bug**; there is no markup to preserve.

## Decisions (owner-confirmed)

- **Pricing:** litellm is the source of truth; keep a **thin reviewed override backstop** ONLY for models litellm lacks/misprices (today: `gemini-3-flash`). Delete the seed/refresh/compare/ingest machinery.
- **Model catalog:** move the picker onto litellm too (retire models.dev). Derive **display names from model ids** (litellm has no `name`); replace the **release_date** ordering/enablement (litellm has no `release_date`, only `deprecation_date`).
- **Token counting:** use `litellm.token_counter` for the **pre-run estimate only**; post-run charging stays on **provider-reported** usage (authoritative).

## Architecture: delete / keep / add

**DELETE (home-grown reimplementation of litellm):**

- `pricing/litellm_source.py` + `pricing/litellm_data/model_prices.json` (vendored) — replaced by `litellm.model_cost` / `get_model_info`.
- `pricing/{seed_loader,composer,upsert_planner,refresh_loop,compare_litellm}.py` + `pricing/seeds/*.yaml` — the ingest/merge/temporal-DB/refresh/drift machinery; litellm is the live catalog.
- `api/models_dev_source.py` + `config/models_dev_snapshot.json` — the picker's metadata source → litellm.
- The stale override entries in `config/pricing_overrides.yaml` (keep the file + mechanism; shrink to `gemini-3-flash`).

**KEEP (product/compliance concerns litellm does not cover — but re-source their inputs to litellm):**

- `pricing/calculator.py` — integer micro-USD + banker's rounding (BIGINT billing integrity). Fed by litellm's per-token rates instead of the DB catalog.
- `budgets/*` (enforcer/charger/estimator/reservations/period) + `usage_budgets*` tables — spend-cap storage + Allow/Warn/Deny + reservations + idempotent CAS charge.
- Temporal price snapshot on usage rows (`pricing_id`/`pricing_version`/`cost_micro_usd`) — historical cost immutability (audit). Snapshot the litellm-derived rate+version onto each row.
- `observability/token_usage.py` + `run_metrics.py` — post-run provider-reported usage (authoritative billed figure).
- `execution/{models.py,deep_agent_builder.py,openai_compat.py}` — the run-path provider funnel + allowlist + local Ollama. **Do NOT adopt ChatLiteLLM** (would break the single-funnel guard + native stream adapters).
- The **local Ollama** catalog (`/v1/local-models`) + per-workspace enablement + `supports_provider` filter.

**ADD:**

- `litellm==1.93.0` in `requirements.txt` (done).
- A thin `LitellmModelSource` (metadata: display-name-from-id, context window, capabilities) + `LitellmRateSource` (cost_per_token) behind the existing `ModelPricingCatalog` / `ModelCatalog` seams so callers don't change.
- A display-name deriver + an ordering/enablement rule that does not need `release_date`.

## Slices

1. **Pricing → litellm library.** Import litellm; `LiteLLMPricingSource`/catalog reads `litellm.model_cost`; `CostCalculator` keeps integer-micro-USD wrapping litellm rates; drop stale overrides (keep gemini-3-flash); delete seeds/refresh/compare/upsert. Verify cost/budget tests + charge parity.
2. **Model catalog → litellm.** `ModelCatalog.build` sources from litellm (name-from-id, capabilities, ctx); retire models.dev source + snapshot; new ordering/enablement without release_date; preserve the `supports_provider` filter + settings-default-first + Ollama merge. Verify `/v1/agent/models` + admin default validation.
3. **Token counting → litellm.** `BudgetEstimator` pre-run estimate uses `litellm.token_counter`; post-run stays provider-usage. Verify preflight tests + hermetic (pre-cache tiktoken; no network in CI).

## Risks / guardrails

- **Hermetic CI:** `litellm.token_counter` fetches tiktoken BPE on first use — pre-cache or vendor; `litellm.model_cost` is bundled (offline-safe).
- **Float→int:** litellm returns float USD; the integer-micro-USD calculator must remain the rounding boundary.
- **Coverage regressions:** anything litellm lacks (gemini-3-flash today) lives in the override/supplement — never silently 0/None a cost or drop a model.
- Verify each slice against the full ai-backend unit suite (currently ~3061) + the cost/budget/catalog tests specifically.
