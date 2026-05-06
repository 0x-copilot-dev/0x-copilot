# Cluster: budgets, pricing, deployment (`agent_runtime`)

Three related concerns grouped per plan; use separate **H2 sections** below.

---

## Budgets (`agent_runtime/budgets`)

### Boundary

[`services/ai-backend/src/agent_runtime/budgets/`](../../../services/ai-backend/src/agent_runtime/budgets/)

### Static signals (2026-05-06)

- Ruff `F401`, `F841`: clean.
- Vulture 60%: [`reservations.py`](../../../services/ai-backend/src/agent_runtime/budgets/reservations.py) `_Port`, `ReservationOutcome` — likely **typing helpers** or legacy; grep usage.

### Smells

- Budget enforcement overlaps **capabilities middleware** spec ([`docs/specs/usage/B8-tool-budget.md`](../../../services/ai-backend/docs/specs/usage/B8-tool-budget.md)) — align docs when wiring changes.

---

## Pricing (`agent_runtime/pricing`)

### Boundary

[`services/ai-backend/src/agent_runtime/pricing/`](../../../services/ai-backend/src/agent_runtime/pricing/)

### Static signals (2026-05-06)

- Ruff: clean.
- Vulture 60%: **`PricingSeedLoader`** “unused” — **false negative for tooling**: [`tests/unit/agent_runtime/pricing/test_calculator_and_seeds.py`](../../../services/ai-backend/tests/unit/agent_runtime/pricing/test_calculator_and_seeds.py) imports and calls `PricingSeedLoader.load_all()`.

### Test-only usage

- Seed loader exercised in tests; verify calculator/catalog usage from worker/API for production pricing paths.

### Smells

- YAML seeds under [`pricing/seeds/`](../../../services/ai-backend/src/agent_runtime/pricing/seeds/) — ensure loader is invoked from startup or job paths if pricing must stay current.

---

## Deployment profile (`agent_runtime/deployment`)

### Boundary

[`services/ai-backend/src/agent_runtime/deployment/profile.py`](../../../services/ai-backend/src/agent_runtime/deployment/profile.py)

### Static signals (2026-05-06)

- Vulture 60%: many fields on deployment profile dataclass (`allow_embedded_provider_keys`, `enforce_rls`, …) appear unused — likely **configuration schema** read selectively by callers or planned controls.

### Smells

- **Unused profile fields** may indicate incomplete enforcement or documentation-only placeholders — compliance reviews should treat as **not evidenced** until wired (see workspace compliance rules).

---

## Cross-cluster links

- Usage rollup / budgets records — persistence [cluster-agent-runtime-persistence.md](./cluster-agent-runtime-persistence.md), worker jobs [cluster-runtime-worker.md](./cluster-runtime-worker.md).

## Extended vulture inventory

Verbatim [Vulture](https://github.com/jendrikseipp/vulture) lines for this cluster’s paths (`vulture src --min-confidence 60` from `services/ai-backend`; **13** lines):

- [`artifacts/cluster-agent-runtime-ops-economics-vulture.txt`](./artifacts/cluster-agent-runtime-ops-economics-vulture.txt)

Merged output for all of `src/` (**634** lines): [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt).

These lists are **candidate** unused symbols — many entries are Pydantic validators, Protocol signatures, OTEL hooks, or FastAPI/RBAC decorators. Use as a triage queue, not an automatic delete list. Regenerate: [`README.md`](./README.md), [`artifacts/README.md`](./artifacts/README.md).
