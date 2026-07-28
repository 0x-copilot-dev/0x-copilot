# Generative Surfaces v2.1

This folder is the architecture and delivery record for the 17-PRD Generative
Surfaces v2.1 program.

- [Product overview](00-overview.md)
- [System design record](01-sdr.md)
- [A–E PRD index](02-prds.md)
- [Agent Runtime Quality, Efficiency, and Learning](../agent-runtime-quality/README.md)

## Product posture

The launch profile is `single_user_desktop`: Electron supervises the local
services, ai-backend uses its file-native store beneath the application-data
directory, and Electron main/native code retains physical workspace and browser
authority. A future hosted profile may implement the same ports, but hosted
Postgres support is not allowed to become an accidental requirement for the
desktop product.

All capabilities share one operation, artifact, effect, workspace, audit,
retention, and replay model. No adapter, renderer, built-in, subagent, sandbox,
or browser integration may create a second approval or execution path.

## Reconciliation contract

The implementation inventory for A1–E2 is assumed complete for this
reconciliation. “Implemented” means the code is present on `main`; it does not
mean every PRD requirement or release gate is currently proven.

This ledger separates:

- **architectural gap** — required product behavior is missing or wired through
  the wrong authority boundary;
- **evidence gap** — the intended implementation exists, but the required
  current-revision test, build, journey, or parity receipt is absent;
- **closed/stale** — an older finding has been superseded and must not stay in
  the active backlog.

**Audited baseline:** `origin/main` at
`e96d55d5bd54aac1674c1f0c7b11b5e535f406f4` (2026-07-27). The reconciliation
was source- and committed-test-based. It did not claim fresh execution evidence.

## What D3 “hosted sandbox” means

D3 is an optional remote code/file execution adapter, not a Postgres-backed
desktop runtime:

1. the desktop selects a retained C1 workspace/artifact revision;
2. ai-backend exports a bounded, immutable base-plus-overlay snapshot;
3. a remote provider proves the requested isolation policy before it receives
   the snapshot;
4. the provider runs with no inherited credentials, deny-by-default egress,
   quotas, cancellation, and owned-resource teardown;
5. outputs return as A2 artifacts and a declarative patch;
6. only an explicit user action may import that patch into C1, after which the
   normal review/stage/C3 commit path applies.

The file-native lifecycle, sealing, cleanup, and recovery foundation exists.
The production prerequisite resolver intentionally returns unavailable because
the full snapshot exporter, policy-bound provider attestation, deliverable
publisher, patch importer, and shared UI are incomplete. Therefore
`run_in_sandbox` must remain absent from the model. The OpenAI hosted-container
adapter is only a candidate provider adapter; it is not evidence that the
hosted sandbox product is safe or available.

## PRD DoD reconciliation board

| PRD                                | Reconciled state                            | What is proven                                                                                                                                                                    | Remaining closure                                                                                                                                                                                                                    |
| ---------------------------------- | ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| A1 — contracts                     | Implemented; evidence gap only              | Canonical vocabulary, IDs, refs, golden journeys, Python/TypeScript mirrors                                                                                                       | Record current-SHA owning suites, typecheck, migration-manifest, and consumer-build receipts                                                                                                                                         |
| A2 — artifact repository           | Implemented; evidence gaps                  | Immutable revisions, file repository, streaming APIs, outbox/lifecycle machinery, facade boundary                                                                                 | Prove receipt/audit-export reference retention; complete endpoint-identity matrix and file-adapter release evidence. Postgres conformance belongs to a future hosted profile                                                         |
| A3 — operation gateway             | Implemented; evidence gap only              | Universal gateway, descriptor resolution, off/shadow/enforce modes, task-bound stage authority, fail-closed unknowns                                                              | Record current-SHA mode-conformance/replay receipts and reconcile the final SDR close-out text                                                                                                                                       |
| A4 — effect stager                 | Implemented; architectural gaps             | Transport-neutral, digest-pinned proposal/stage/decision boundary with no executor reachability                                                                                   | Make decision→commit-command durable or repairable as one protocol; serialize/CAS concurrent stage mutation; expand all-operation exploding-effect coverage                                                                          |
| A5 — commit coordinator            | Implemented; architectural gaps             | Closed executor registry, claim-before-effect, exact approved material, sole dispatch path, reconciliation model                                                                  | Wire production cancellation; classify ambiguous transport outcomes as indeterminate; make enqueue idempotent/repairable; persist phase audit and provider receipt evidence                                                          |
| B1 — authored artifacts            | Implemented; product gaps                   | Explicit publication, canonical artifact-backed drafts, exact revision send, provider-neutral tools                                                                               | Preserve subagent work-item/operation lineage; add server-verified code-block selection and draft-version promotion                                                                                                                  |
| B2 — renderers/editors             | Implemented; evidence-heavy                 | Shared safe code/markdown/document/dataset rendering, artifact URIs, streaming transport, revision UI                                                                             | Close oversize/raw download, complete 409 local-buffer UX, CSV/accessibility/flag-off matrices, and retain current-SHA desktop evidence. Existing v3 review and dataset parity are 0 HIGH / 0 MEDIUM                                 |
| B3 — canvas lifecycle              | Implemented; product gaps                   | Deterministic selection, common event projection, hydration, replay parity, receipt selection rules                                                                               | Preserve the prior run’s open surface across chat-only follow-ups; add Focus Download/Save; wire selected record/table production path; stabilize tab identity/order; integrate hydration retry/raw recovery                         |
| C1 — workspace overlay             | Implemented; architectural gaps             | Exact projection-bound staging and zero direct host mutation                                                                                                                      | Fix file-store publish ordering and immutable Postgres history contract; make move destructive; complete coalescing, authority-loss reads, collision/continuation semantics, request-digest conflicts, and keyed redacted path audit |
| C2 — workspace authority           | Implemented foundation; architectural gaps  | Main/native-only fail-closed create/mkdir path, one-use permit, no-replace create, durable conservative journal                                                                   | Implement replace/delete/move, preimage/trash recovery, post-crash target reconciliation, hostile-child confinement proof, live revocation for legacy reads, stable-id wire parity, and idempotent prepare                           |
| C3 — workspace product integration | Implemented; release evidence gap           | Exact staged workspace flow, common review UI, web download fallback, redacted Sources/receipts, current review parity 0 HIGH / 0 MEDIUM                                          | Retain successful current-main supervised G1/G2 and web live smoke, including grant/revoke/drift/crash recovery                                                                                                                      |
| D1 — MCP convergence               | Implemented; product gaps                   | Pre-dispatch classification, read-once/write-stage, exact approved dispatch, post-approval auth, no direct write fallback                                                         | Enforce selective presentation; move MCP auth to operation-linked gates; offload full large results; retain provider receipts; add safe generic MCP proposal preview/diff                                                            |
| D2 — built-ins/subagents           | Implemented foundation; architectural gaps  | Descriptor/conformance inventory, gateway treatment of built-ins, safe row-set staging                                                                                            | Wire authority intersection into production subagents; use deterministic operation lineage; propagate cancellation; produce usage/artifact attribution edges; retire bespoke connector/legacy row-set presentation                   |
| D3 — hosted sandbox                | Safe dark foundation only                   | File-native state, immutable sealing, lifecycle/recovery primitives, gateway-only construction, zero host-write authority                                                         | Complete attested provider, full snapshot export, A2 result publication, explicit C1 patch import, shared UI, usage attribution, real-provider journeys, and D3 parity; remain default-off/model-dark                                |
| D4 — browser adapter               | Implemented click/submit core; product gaps | Staged exact click/submit, one-use binding, observational reconciliation, no direct effect dispatch                                                                               | Build production download→artifact and artifact-backed upload; browser review UI; user/device/run/expiry-bound refs; screenshot redaction; cross-origin-frame policy; restart/reconcile journey and parity                           |
| E1 — accountability/lifecycle      | Implemented foundation; architectural gaps  | Immutable usage/attribution, usage APIs, Receipt/Sources/Pending foundations, signed audit export, legal-hold primitives, lifecycle reference enumeration, safe metrics           | Finalize reported in-flight usage on terminal paths; compose file-native lifecycle jobs; make retention/deletion/holds graph-aware; complete repair families, privileged-access audit, and Pending projection parity                 |
| E2 — migration/cutover             | Implemented control plane; not cut over     | Ten independent rollout modes, startup validation, trusted cohort/kill-switch wiring, draft/stage migration engines, legacy replay, shadow helpers, conformance/performance gates | Gate admission on migration readiness; migrate native filesystem interrupts; retire mutable legacy drafts/`WriteStager`; add a durable promotion controller and cutover/backout runbooks; all ten modes still default off            |

## Active architectural backlog

This is the only active product backlog for the program. Items are grouped by
root authority boundary so that one architectural fix closes all dependent
symptoms.

| ID         | Priority / launch tier  | Root closure                                                                                                         | PRDs           | Done when                                                                                                                                                                                                                                |
| ---------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GS-ARCH-01 | P0 · desktop core       | Transactional or repairable approval→command handoff plus serialized stage mutation                                  | A4, A5         | A crash or concurrent decision cannot strand, duplicate, or apply the wrong approved revision                                                                                                                                            |
| GS-ARCH-02 | P0 · desktop core       | Complete the coordinator’s cancellation, uncertainty, idempotent enqueue, phase-audit, and provider-receipt protocol | A5, D1         | Cancellation reaches production dispatch; ambiguous calls remain indeterminate; replay never resends; audit/receipt evidence is durable                                                                                                  |
| GS-ARCH-03 | P0 · desktop core       | Make C1 projection versions durable and semantically identical, with a complete transition/coalescing model          | C1, D3         | File publication is crash-safe; every adapter preserves immutable versions; move/collision/authority-loss/continuation semantics are explicit and tested                                                                                 |
| GS-ARCH-04 | P0 · desktop core       | Complete native workspace mutation and recovery under one-use main-owned authority                                   | C2, C3         | Create/replace/delete/move are CAS-bound; recovery and post-crash reconciliation are possible; hostile children cannot escape grants; revocation is immediate                                                                            |
| GS-ARCH-05 | P0 · Studio UX          | Make canvas identity conversation-scoped while operation state stays run-scoped                                      | B3             | A chat-only follow-up preserves the open surface; tabs have stable IDs/order; hydration failure recovers in place; Focus exposes safe Open/Download/Save                                                                                 |
| GS-ARCH-06 | P1 · authored artifacts | Make selection, promotion, provenance, and retention reference-complete                                              | A2, B1         | Every selected code/draft revision is server-verified, lineage-complete, retained while referenced, and promotable without client-trusted offsets                                                                                        |
| GS-ARCH-07 | P1 · MCP                | Enforce the generic presentation/gate/result/receipt contracts                                                       | D1             | Scalar reads stay activity-only; selected data uses canonical surfaces; large results remain retrievable; gates and provider receipts join the operation                                                                                 |
| GS-ARCH-08 | P1 · subagents          | Make the deterministic operation tree authoritative in production                                                    | D2             | Parent/request/definition/runtime authority is intersected; cancellation cascades; artifacts, stages, and usage share retry-stable lineage                                                                                               |
| GS-ARCH-09 | P2 · optional sandbox   | Finish D3 without weakening the filesystem-first desktop contract                                                    | D3             | A policy-attested provider receives only a sealed snapshot and returns only artifacts/patches; C1 import is explicit; the tool remains absent otherwise                                                                                  |
| GS-ARCH-10 | P2 · optional browser   | Complete browser artifacts, uploads, private-ref authority, redaction, and shared review UI                          | D4             | Download/upload and exact review are product-usable; secrets and foreign/expired refs fail closed; restart remains no-blind-retry                                                                                                        |
| GS-ARCH-11 | P0 · desktop core       | Compose a file-native lifecycle supervisor and close terminal usage accounting                                       | E1             | Reported in-flight usage is finalized on timeout/cancel/failure; bounded rollup, retention, cleanup, repair, and audit-verification loops run with durable cursors under the desktop supervisor                                          |
| GS-ARCH-12 | P0 · desktop core       | Make the lifecycle reference graph authoritative for deletion, retention, legal hold, repair, and privileged access  | A2, E1         | Held deletion hides product content while retaining required bytes; every reference family is enumerated/repaired safely; privileged reads are durably audited                                                                           |
| GS-ARCH-13 | P0 · cutover            | Make migration readiness a hard cohort prerequisite and retire dual write truth                                      | A4, B1, C1, E2 | Signed/fenced draft and pending-stage reports gate admission; native filesystem interrupts are converted or cancelled; legacy drafts become read-only; `WriteStager` and old approval fallback leave production composition              |
| GS-ARCH-14 | P0 · release control    | Build one authorized, audited promotion/cutover controller and operational rollback contract                         | E1, E2         | The controller binds migration facts, soak metrics, current-SHA journeys/parity/performance, producer order, stop criteria, default flips, asymmetric rollback, and durable evidence; operator/self-host/mixed-version runbooks match it |

## Release-evidence backlog

Evidence work does not authorize product patches. If a gate fails, the failure
is routed to the owning architectural item above.

| ID         | Priority | Evidence package                                                                                                                             | Done when                                                                                                                                                        |
| ---------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GS-EVID-01 | P0       | Current-revision full Python suites, affected npm tests/typechecks, builds, migration manifests, service-boundary and contract-parity checks | One revision-bound manifest records every command and result                                                                                                     |
| GS-EVID-02 | P0       | Supervised desktop filesystem journeys                                                                                                       | G0–G10 scripts exist; G1/G2 and the required recovery/reopen paths pass against the installed supervised app with facade truth and retained logs/screenshots     |
| GS-EVID-03 | P0       | Web and Focus/Studio live smoke                                                                                                              | Ordinary chat stays chat-only; artifact and workspace flows remain honest on web; Focus remains compact; Studio owns full surfaces                               |
| GS-EVID-04 | P0       | Computed-style design parity                                                                                                                 | Existing v3 review/dataset reports stay at 0 HIGH / 0 MEDIUM and every newly introduced MCP/subagent/sandbox/browser state gets mapped before release            |
| GS-EVID-05 | P1       | Cross-language and compatibility corpus                                                                                                      | Public Python/JSON/TypeScript contracts, old v2 replay, signed receipt export, large-content, auth-negative, and redaction fixtures pass at the release revision |

## Desktop journey construction board

“Crafted” means an executable-quality script exists and passes static
validation. It does not mean the real app journey has been executed.

| Journey                     | Script state                            | Execution state                                                 |
| --------------------------- | --------------------------------------- | --------------------------------------------------------------- |
| G0 plain chat               | Existing                                | Historical execution only; include in the final current-SHA run |
| G1 Markdown lifecycle       | Existing                                | Current-SHA supervised execution pending                        |
| G2 CSV row-set              | Existing                                | Current-SHA supervised execution pending                        |
| G2A web artifact-only       | Existing                                | Current-SHA web execution pending                               |
| G3 code artifact            | Being crafted                           | Do not execute in this reconciliation                           |
| G4 DOCX                     | Being crafted                           | Do not execute in this reconciliation                           |
| G5 local email triage       | Being crafted against `fixture://` only | Do not execute in this reconciliation                           |
| G6 local timeline/X         | Being crafted against `fixture://` only | Do not execute in this reconciliation                           |
| G7 local Discord moderation | Being crafted against `fixture://` only | Do not execute in this reconciliation                           |
| G8 mixed multi-surface      | Being crafted                           | Do not execute in this reconciliation                           |
| G9 recovery/honesty         | Being crafted                           | Do not execute in this reconciliation                           |
| G10 retention/reopen        | Being crafted                           | Do not execute in this reconciliation                           |

## Closed and removed stale items

The following are historical evidence, not active backlog:

- the old 57-HIGH review-surface report; current
  `tools/design-parity/surfaces/generative-surfaces-v3/out/report.md` and
  `artifact-dataset/out/report-v3-shared.md` are 0 HIGH / 0 MEDIUM for their
  mapped states;
- F-006 immutable draft ownership, F-009 workspace mutation reachability,
  F-010 selected presentation, F-012 forged generic operation scope, and F-014
  direct bespoke surface construction;
- old GSQA-002/005/006/007 and GSB-009/010/011/012 findings superseded by the
  shared review architecture and current parity;
- “G0–G2 are plan-only”; executable scripts now exist;
- the historical macOS Accessibility block as a product defect. Host
  preflight remains useful release-harness work, but the old machine state is
  not a standing code backlog item;
- “D3 needs a Postgres fallback.” D3 desktop state is intentionally file-only;
  hosted persistence is a separate future profile.

## Updating this ledger

When code or evidence changes:

1. update the relevant PRD row;
2. close or change one root backlog item rather than adding a duplicate symptom;
3. attach revision-bound commands/results to the evidence package;
4. move superseded findings to the stale section;
5. never promote a row to complete from a merged PR or historical test receipt
   alone.
