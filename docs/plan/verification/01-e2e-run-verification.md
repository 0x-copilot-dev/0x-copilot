# V1 — End-to-end run verification (the keystone)

Status: **In progress** · Owner: runtime/platform · Phase 1 of the verification program.

> Spec-first per [services/ai-backend/docs/CLAUDE.md](../../../services/ai-backend/docs/CLAUDE.md). Exact `path:line` injection points are filled from the seam map (companion investigation) before each component lands.

---

## 1. Problem statement

The desktop's **real production topology — a single supervised process, production auth posture, a durable store, and an in-process run executor — is exercised by zero automated tests.** 1,900+ unit tests, typecheck, and an adversarial review all passed while a one-line guard silently disabled the _only_ run executor on desktop; the AC2b worker-gate bug shipped and reached a user. `tools/desktop-runtime/run-local.mjs` asserts `/v1/health` and the facade proxy — it never submits a run. CI (`ci-desktop`, `ci-ai-backend`) never boots the supervised stack and drives a run to a streamed completion.

The same gap is why the file store was "built, unit-correct, shipped off, and carried a latent citation data-loss bug" — it was **built but never the live path**. Every wiring defect between individually-correct components is currently invisible until a human flips it on.

**This phase makes the real topology continuously verifiable: a run can be driven to a streamed completion, hermetically (no key, no network), in CI, for every supported desktop configuration.**

## 2. Goals / Non-goals

### Goals

- G1. A **deterministic fake model provider**, selected by env, that streams a canned response (text + reasoning + a terminal completion) with **no network and no provider key**.
- G2. An **e2e run→stream smoke**: boot the supervised stack → authenticate → create conversation → `POST /v1/agent/runs` → consume SSE → assert `model_delta` + reasoning + `run_completed`, and assert the persisted event log matches.
- G3. The smoke runs for **both** store backends (`file` and `postgres`) and is wired into CI, path-filtered.
- G4. The fake-provider path **bypasses the BYOK credential gate** so a keyless workspace can complete a run under test.

### Non-goals

- N1. Replacing real-provider integration checks (those stay separate, gated, and may need live keys).
- N2. The production-posture fix (P1) and store-durability work (P2) — separate phases; this phase may _depend on_ a minimal correct auth path.
- N3. UI/renderer e2e (Playwright drive of the window) — a later, heavier tier; this phase asserts at the service/SSE boundary.

## 3. Design (two components)

### 3.1 Deterministic fake model provider

- Selected by an explicit env signal (e.g. `RUNTIME_MODEL_PROVIDER=fake` / `RUNTIME_FAKE_MODEL=1`), resolved in the **narrowest model-construction seam** so it substitutes the concrete chat model without touching orchestration. _(Exact seam + selection point: from the seam map — §1/§3 of the investigation.)_
- Emits a **streamed** canned response: several text deltas + a reasoning/thinking span + a final message, so the smoke asserts real streaming semantics, not a single blob.
- **Never requires a key** and makes the credential gate treat `fake` as satisfied (or short-circuits the gate for that provider). _(Gate location: seam map §2.)_
- Prefer **promoting an existing test fake** into an env-gated runtime provider over inventing a new one, if the unit-test stub is suitable. _(Existing fake: seam map §3.)_
- Fail-closed: the fake provider is **refused unless a non-production posture / explicit test flag** is set, so it can never serve real users.

### 3.2 E2e run→stream smoke

- Boots the supervised stack the way `run-local.mjs` already does, adding the run drive. _(Boot + auth path: seam map §5.)_
- Asserts the terminal `run_completed` arrives over SSE within a bound, that intermediate `model_delta`/reasoning events arrived (streaming, not just a final blob), and that `GET …/events` replay matches the streamed sequence (persistence parity). _(Event names: seam map §4.)_
- Parameterized over `RUNTIME_STORE_BACKEND ∈ {file, postgres}`.
- Runs in CI as a dedicated job (hermetic via the fake provider), path-filtered to the surfaces it covers.

## 4. Non-functional requirements

- **NFR-1 (Hermetic).** No network egress, no real provider key, deterministic output — safe and fast in CI.
- **NFR-2 (Real topology).** Asserts against the _supervised_ stack (embedded PG or file store, in-process worker, production-shaped API), not an in-memory unit harness — that is the whole point.
- **NFR-3 (Fail-closed safety).** The fake provider cannot be selected in a real user deployment; guarded by posture/flag and covered by a test.
- **NFR-4 (Both backends).** Green for `file` and `postgres`; a regression in either fails CI.
- **NFR-5 (Fast signal).** The smoke completes in seconds and reports _which_ assertion failed (no-worker vs no-stream vs no-persistence), so failures are diagnosable, not a timeout.
- **NFR-6 (No prod-path change).** Selecting the fake provider is purely additive; the real model path is byte-for-byte unchanged when the flag is unset.

## 5. Test plan

- The fake provider itself: unit test that it streams the canned deltas + reasoning + terminal, and that it is **refused** under a production posture.
- The credential-gate bypass: run-create with `provider=fake` and no key succeeds; with a real provider and no key still 400s (unchanged).
- The e2e smoke IS the integration test; additionally assert the negative — with the OLD worker guard (or worker disabled) the smoke **fails** (guards against re-introducing the escape).

## 6. Work breakdown (two verification tiers)

**Tier A — hermetic in-process real-graph run→stream (SHIPPED, slice 1).**
Drives a real queued run through the real worker → real Deep Agents graph → real
streaming executor, with only the concrete chat model faked at the construction
funnel. It runs as a plain pytest, so `ci-ai-backend` executes it on every
commit with no supervised boot — fast, hermetic, CI-native. It catches the whole
"runs don't execute/stream" class (the AC2b escape) and already surfaced a real
latent bug (`EmptyMcpRegistry` violated the async registry contract → every run
crashes in any deployment without an MCP backend URL; fixed here).

- ✅ Deterministic fake model (`agent_runtime/execution/fake_model.py`), env-gated at `build_chat_model`.
- ✅ Credential-gate bypass (fake mode ⇒ keyless in `ModelConfigResolver`).
- ✅ Hermetic run→stream test asserting `run_started → model_delta → reasoning → final_response → run_completed` + persistence.
- ✅ Negative test (no worker ⇒ run never completes) + fail-closed default (flag off by default; shipped desktop never sets/allowlists it) + unit coverage.
- ✅ `EmptyMcpRegistry` async-contract fix + regression test.

**Tier B — supervised-boot smoke (FAST-FOLLOW).** Boot the real supervised stack
(embedded PG / file store, in-process worker, production-shaped API) via
`run-local.mjs`, obtain a bearer (reuse `tools/cli-testing/harness/siwe-session.mjs`),
`POST /v1/agent/runs` with the fake-model env, consume SSE, then assert both
`run_completed` and `GET …/events` replay parity — parameterized over `file` and
`postgres`, as its own CI job. This tier adds supervision/env/store coverage that
Tier A (in-memory store) does not. _(Boot + auth: seam map §5; the fake-model env
must be added to the desktop service-env allowlist for the supervised path —
service-env.ts:11–36.)_

Follow-on phases (tracked separately): P1 production posture _(seam map §6 —
`posture.ts` + `COPILOT_PRODUCTION=1` already exist, so smaller than first
feared)_, P2 file-store durability/migration completion, P3 model catalog +
onboarding, P4 distribution/dev-loop/observability, P5 "no off-by-default without
an e2e path" gate.
