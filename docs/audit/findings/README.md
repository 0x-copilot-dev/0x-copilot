---
id: findings
kind: report
title: Findings — ranked index
audit_date: 2026-07-20
---

# Findings — ranked index

Consolidated, de-duplicated, and adversarially verified findings from the 18-cluster + 6-flow audit. Each raw auditor finding was cross-checked by an independent per-cluster verifier reading the actual source; semantic near-duplicates reported by several auditors were merged into one entry (the merged sources are named in each themed file).

**How to read this:** the table below is the ranked cross-cutting top 25. Full detail — evidence paths, remediation, payoff — lives in the themed files, one per refactor goal:

| File                                                   | Theme (maps to your refactor goals)                                 |
| ------------------------------------------------------ | ------------------------------------------------------------------- |
| [dead-code.md](dead-code.md)                           | Shipped-but-unreachable code to delete/park (goals 3, 8)            |
| [duplication.md](duplication.md)                       | Same logic in ≥2 places — DRY (goal 6)                              |
| [ssot-violations.md](ssot-violations.md)               | Facts/contracts with no single source of truth (goal 7)             |
| [replace-with-libraries.md](replace-with-libraries.md) | Bespoke code a maintained library/service replaces (goals 2, 5)     |
| [boundary-violations.md](boundary-violations.md)       | Cross-component leaks, logic in the wrong layer (goal 5)            |
| [refactor-simplify.md](refactor-simplify.md)           | God modules / over-abstraction — simplify + reduce LOC (goals 4, 9) |
| [risks.md](risks.md)                                   | Correctness / security / compliance risks surfaced in passing       |

## Verification summary

282 finding-verdicts were recorded across the 24 `_verify/*.json` files:

- **177 confirmed** (high/medium findings that held up against the code)
- **103 accepted** (low-severity, passed through without deep re-check)
- **1 partial** (framing corrected — see appendix)
- **1 refuted** (removed from live findings — see appendix)

Refutation rate on deeply-checked findings: **~0.6%** — the auditors were accurate. The two exceptions are documented in the appendix so no downstream agent re-flags them.

## Counts

Live findings after merge: **120** (≈220 raw auditor findings consolidated).

| Severity | Count |     | Type                  | Count                          |
| -------- | ----- | --- | --------------------- | ------------------------------ |
| high     | 45    |     | risk / inconsistency  | ~40                            |
| medium   | 56    |     | dead-code             | 11 groups (~95k LOC removable) |
| low      | 19    |     | duplication           | 16                             |
|          |       |     | ssot-violation        | 13                             |
|          |       |     | refactor / complexity | 13                             |
|          |       |     | bespoke-replaceable   | 9                              |
|          |       |     | boundary-violation    | 3                              |

## Ranked top 25 (cross-cutting)

Ranked by blast radius × severity × remediation payoff. IDs link into the themed files.

| #   | ID                                                                      | Sev  | Type                | Title                                                                                                                          | Clusters                                            | Action                                                                                                      |
| --- | ----------------------------------------------------------------------- | ---- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 1   | [DEAD-1](dead-code.md)                                                  | high | dead-code           | ~50k LOC folded/unmounted destination families (web `features/*` + chat-surface `destinations/*`) exported & tested as if live | frontend-web, chat-surface-\*                       | Execute the IA-fold sweep; delete dirs + tests, rebuild barrel around live 6, pin with export-snapshot test |
| 2   | [RISK-rbac](risks.md#access-control--auth)                              | high | risk                | RBAC ships default-permissive; enforcement enabled in **no** deployment (both services)                                        | backend-_, ai-runtime-_                             | Make deny-by-default; add a deployment-posture test                                                         |
| 3   | [RISK-bearer-exp](risks.md#access-control--auth)                        | high | risk                | Bearer `exp` never verified on the HMAC path; core facade routes skip session touch                                            | flow-auth, backend-core, backend-facade             | Verify `exp`/session on every authed route; kill-switch on by default                                       |
| 4   | [RISK-audit-egress](risks.md#audit-retention--compliance)               | high | risk                | Audit egress façade-only: SIEM pump unwired + schema-broken; list/export empty under Postgres; deploy audit in-memory          | backend-platform, flow-data                         | Wire the pump, fix schema, add Postgres audit-export tests                                                  |
| 5   | [RISK-checkpoints](risks.md#data-durability)                            | high | risk                | On Postgres, LangGraph checkpoints live in `InMemorySaver` → approval/graph continuation dies on worker restart                | ai-runtime-persistence, ai-runtime-worker           | Adopt `PostgresSaver` (see LIB-4)                                                                           |
| 6   | [RISK-product-inmemory](risks.md#data-durability)                       | high | risk                | All backend product destination stores are in-memory in every shipped composition                                              | backend-product                                     | Provide persistent adapters or gate the features off honestly                                               |
| 7   | [RISK-postgres-approval](risks.md#data-durability)                      | high | risk                | `PostgresRuntimeApiStore` missing 3 `PersistencePort` approval methods → HTTP 500 on assigned-approvals inbox                  | ai-runtime-persistence                              | Implement the 3 methods; contract-test all adapters against the Port                                        |
| 8   | [SSOT-1](ssot-violations.md#cross-language-wire-contracts)              | high | ssot-violation      | `api-types` is a ~9.6k-LOC hand-maintained dual-write of the Pydantic contracts; drift tests cover only 4 enum tuples          | shared-packages, flow-contracts                     | Generate TS from the Python schemas (or vice-versa); fail CI on drift                                       |
| 9   | [BND-1](boundary-violations.md)                                         | high | boundary-violation  | ai-backend "pure domain" `agent_runtime` imports `runtime_api` + `runtime_worker` — a package-level cycle                      | ai-runtime-\*                                       | Break the cycle; move shared types down, invert the deps                                                    |
| 10  | [SSOT-11](ssot-violations.md#front-end--desktop)                        | high | ssot-violation      | Electron **major-version skew** ships to end users vs dev/CI/pinned runtime                                                    | desktop-\*, build-deploy                            | Single-source the Electron version; add a skew check                                                        |
| 11  | [RISK-vault-swallow](risks.md#access-control--auth)                     | high | risk                | `_default_token_vault` swallows the factory's fail-closed error → silent auth-route outage                                     | backend-core                                        | Let it fail closed; alert                                                                                   |
| 12  | [RISK-pepper](risks.md#access-control--auth)                            | high | risk                | Personal-API-key HMAC pepper fails **open** in production                                                                      | backend-identity                                    | Fail closed when pepper is unset in prod                                                                    |
| 13  | [DUP-1](duplication.md)                                                 | high | duplication         | Three parallel run-stream projection pipelines + triplicated reducers                                                          | flow-run-streaming, chat-surface-\*, ai-runtime-api | Collapse to one projection; single reducer                                                                  |
| 14  | [REF-1](refactor-simplify.md#god-composition-roots)                     | high | refactor            | `backend/create_app` — ~1,620-line, ~60–70-param mega-factory every feature edits                                              | backend-core                                        | Decompose into per-domain wiring modules                                                                    |
| 15  | [REF-2](refactor-simplify.md#god-composition-roots)                     | high | refactor            | `backend-facade/app.py` — 1,505-line monolith, ~68 near-identical handlers                                                     | backend-facade                                      | Table-drive the proxy handlers (see DUP-9)                                                                  |
| 16  | [SSOT-3](ssot-violations.md#cross-language-wire-contracts)              | high | ssot-violation      | SIWE EIP-4361 template maintained in 3 code copies (+harness+doc = up to 5)                                                    | flow-auth, backend-identity, frontend-web           | One shared template + cross-language golden test                                                            |
| 17  | [SSOT-4](ssot-violations.md#cross-language-wire-contracts)              | med  | ssot-violation      | ≥6 divergent hardcoded model catalogs                                                                                          | ai-runtime-\*, frontend-web, desktop-app            | One catalog (models.dev-style) consumed everywhere                                                          |
| 18  | [SSOT-5](ssot-violations.md#cross-language-wire-contracts)              | high | ssot-violation      | MCP enum vocab hand-copied in 3 places; ~10 distinct MCP-server representations                                                | flow-mcp, backend-\*, shared-packages               | Canonical MCP model in one package                                                                          |
| 19  | [LIB-4](replace-with-libraries.md)                                      | high | bespoke-replaceable | LangGraph `PostgresSaver` exists; runtime uses `InMemorySaver` on Postgres                                                     | ai-runtime-persistence                              | Swap in the library adapter (pairs with #5)                                                                 |
| 20  | [RISK-retention-days](risks.md#audit-retention--compliance)             | high | risk                | Privacy `retention_days` is a dead end — resolver hook has zero callers                                                        | flow-data, backend-platform                         | Wire retention enforcement or remove the UI promise                                                         |
| 21  | [RISK-legal-hold](risks.md#audit-retention--compliance)                 | high | risk                | Legal hold is checked everywhere and settable nowhere                                                                          | flow-data                                           | Add the setter + admin path, or drop the checks                                                             |
| 22  | [RISK-worker-serial](risks.md#run-lifecycle--streaming)                 | high | risk                | Worker runs serially; cancel cannot preempt; terminal states overwrite each other                                              | ai-runtime-worker                                   | Concurrent claim + preemptible cancel + state guard                                                         |
| 23  | [DUP-6](duplication.md)                                                 | med  | duplication         | Nine `env_float/int/bool` copies + seven hand-rolled periodic-loop lifecycles                                                  | ai-runtime-_, backend-_                             | One env-parse util + one loop primitive in service-contracts                                                |
| 24  | [RISK-connectors-stub](risks.md#connectors--mcp)                        | high | risk                | Web Connectors/Tools connect flow is a stub end-to-end; read model never populated                                             | flow-mcp, frontend-web                              | Finish or hide the flow (don't ship a dead surface)                                                         |
| 25  | [LIB-1](replace-with-libraries.md) + [LIB-2](replace-with-libraries.md) | med  | bespoke-replaceable | Hand-rolled OAuth 2.1 + MCP JSON-RPC on raw `urllib`; near-JWT bearer + EIP-4361 parser + TTL-LRU with libs already in tree    | backend-core, backend-identity, flow-mcp            | Adopt `authlib`/`httpx`/`PyJWT`/`cachetools`                                                                |

**Biggest single lever:** #1 (DEAD-1) — the IA-fold sweep removes ~50k of the ~95k total dead LOC and stops unmounted UI from reading as "capability exists" in compliance reviews.

**Dominant theme:** the highest-severity risks are overwhelmingly _built-but-not-wired_ — controls, stores, and enforcement that exist in code and settings but have no effect at the enforcement point (RBAC, bearer-exp, audit egress, retention, legal-hold, checkpoints, product persistence). This matches the repo's own compliance rule that "a control counts as implemented only when code, config, tests, and docs all support it."

## Appendix — corrected / refuted claims (do not re-flag)

- **REFUTED — shared-packages F2** ("all four tier-1 surface renderers are registered but unreachable, mounted by neither app"): the thread-canvas subtree **is** mounted by desktop — `renderer/bootstrap.tsx:247` mounts `DestinationOutlet`, whose default `run` slug renders `RunDestination.tsx:572 <ThreadCanvas>` → `TcSurfaceMount` → `resolveAdapter`, and `registerAll()` at `bootstrap.tsx:45` populates that registry. The adapters are reachable when a run is active. Excluded from dead-code.
- **PARTIAL — docs-corpus F7** (salvaged prior dead-code list): the cited docs do carry the claims, but the "surviving/high-confidence" framing is **stale for ≥2 items** at HEAD — `InboxEventEnvelopeSchema` **is** used (`sse/inbox_adapter.py:17,72`) and `ToolBudgetMiddleware.check_admit` **is** wired (`runtime_worker/handlers/run.py:1429`). Only `register_health_routes(app)` passing no checkers still holds (`app.py:1317`, the `/readyz`-always-ready item). Treat the prior salvage list as needing per-item re-verification, not as confirmed dead.
