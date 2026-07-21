# PRD-11 — Hardening: eval harness, metering, injection lint, registry scoping (Wave 4)

**Goal:** make the generation subsystem measurable and the substrate enterprise-clean: an offline eval harness for the spec-authoring skill (so skill/model changes are scored, not vibed), generation metering surfaced as counters, a spec injection-lint pass, and per-instance registry scoping groundwork.

**Depends on:** PRD-07. **Scope:** `services/ai-backend` + `packages/chat-surface` (registry scoping only).

## Scope

**1. Eval harness (`services/ai-backend/tests/evals/surfaces/`)** — pytest-marked `evals` (excluded from default CI):
- Corpus: ≥20 real-shaped fixtures `{tool_descriptor, sample_output}` across the catalog connectors + 5 adversarial (injection strings in values, 40-key flat objects, deep nesting, empty arrays, unicode/emoji keys).
- Scorers (pure, deterministic): schema-valid rate; path-resolution rate; archetype-choice accuracy vs golden; label quality lint (length/case rules from SKILL.md); field-count sanity (4–8). Output: one JSON report per run `{model, skill_version, per-fixture scores, aggregate}` written to the evals dir.
- Runner supports `SURFACE_SPEC_MODEL` matrix (run the same corpus against 2–3 configured cheap models locally) — this is the data for the model-routing choice, refreshed whenever the skill or model lineup changes.
- Golden regression mode: with a fake "replay" model (recorded outputs), the whole harness runs hermetically in CI as unit tests of the scorers + lint.

**2. Metering** — promote the PRD-07 log lines to counters via the existing observability module (`agent_runtime/observability/`): `surfaces_specgen_total{verdict}`, `surfaces_specgen_tokens{direction}`, `surfaces_render_fallback_total{tier}` (the last one emitted FE-side? no — count backend envelope emissions without spec as the proxy; FE metrics are out of scope). Budget alarm: warn-log when a single run exceeds `SURFACE_SPEC_MAX_GEN_PER_RUN`.

**3. Injection lint (spec-level, backend)** — a `lint_spec(spec, sample)` pass (extend PRD-07's path-lint): reject specs whose labels contain URLs or imperative-instruction patterns (`ignore`, `system:`, markdown links), whose `url_path` resolves to a non-http(s) value in the sample, or whose field count exceeds schema bounds. Runs on generation AND on backend-registry write (PRD-08 hook if merged).

**4. Registry scoping groundwork (`packages/chat-surface`)** — keep the module-global default (desktop single-instance is fine) but: add `createSurfaceRegistry()` returning an isolated instance + a `SurfaceRegistryProvider`/context that `TcSurfaceMount` consults BEFORE the global (default = global, so zero behavior change). This unlocks per-tenant scoping for multi-tenant web later without a rewrite; document the invariant in the file header.

## Acceptance criteria

1. Hermetic CI: scorer/lint unit tests green; evals marker excluded by default (`pytest -m "not evals"` is the default config — verify).
2. One committed baseline report from a local live run (redact nothing sensitive — it's shapes only) under `tests/evals/surfaces/baselines/`, with the model + skill_version stamped.
3. Injection-lint unit tests: each adversarial fixture rejected with a named reason code.
4. Metering unit test: counters increment per verdict path (fake model).
5. chat-surface: provider-scoped registry test (two isolated instances resolve independently; absent provider falls back to global; ALL existing registry tests untouched and green).

## Non-goals / guardrails

- No CI live-model calls, ever. No new metrics backend — reuse observability as-is.
- No multi-tenant enforcement yet (groundwork only); no FE telemetry.
- Do not change scorers into LLM-judges — deterministic only, so evals are reproducible.
