# Generative Surfaces v2.1 planning package

This folder contains the Generative Surfaces v2.1 implementation program.

## Generative surfaces and governed effects

The existing 17-PR A–E program defines immutable artifacts, selective
presentation, the universal Operation Gateway, no-executor staging, exact
approval, durable commit/reconcile, workspace overlays, MCP convergence,
subagents, sandbox/browser adapters, accountability, and cutover.

- [Product overview](00-overview.md)
- [System design record](01-sdr.md)
- [A–E PRD index](02-prds.md)

## Related program: agent runtime quality, efficiency, and learning

The separate program builds on the A–E contracts. It covers prompt/cache
architecture, tool discovery and execution efficiency, research/grounding,
multi-file edit planning, final-answer verification, skills, durable memory,
learning from completed and historical work, routines, goals, cross-run
orchestration, and governed extensibility.

- [Agent Runtime Quality, Efficiency, and Learning — normative README](../agent-runtime-quality/README.md)

That program's README is the source of truth for scope, PRD ownership, dependency
order, launch gates, and the complete implementation checklist.

## Deployment posture

The current product target is the `single_user_desktop` profile: Electron
supervises the local services, ai-backend defaults to its file-native store
under the user's application-data directory, and Electron main retains native
workspace/browser authority. The related runtime program must optimize for
that local-first B2C path. A future hosted consumer sync offering may implement
the same ports, but is not a prerequisite or a source of authority for the
desktop product.

## Shared rule

The second program must not create a parallel execution or approval path.
Every capability continues to use the A–E operation, artifact, effect,
workspace, audit, retention, and replay contracts. Where current ai-backend
behavior is already stronger—brokered desktop authority, staged effects,
citation provenance, deterministic event replay, and subagent authority
intersection—it is preserved rather than replaced.

## Implementation ledger

This section is the **single status ledger** for Generative Surfaces v2.1. It
exists because a merged PR proves that code reached `main`; it does not prove
that every requirement, architectural invariant, real desktop journey, or
design-parity requirement still holds after later merges.

**Baseline:** `origin/main` at `881bca443fca3978ea276e34255b894bb46b465a`
(2026-07-26). Update this section in the same PR whenever implementation,
verification, a finding, or a release-gate result changes.

### Status vocabulary

| Status                                 | Meaning                                                                                                                                       |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `Merged — evidence audit pending`      | An implementation is on `main`, but its current-main DoD evidence has not been recorded item by item here. It is not a claim of completion.   |
| `Architecture reconciliation required` | A later design decision changes a foundational constraint. Existing code must be audited and aligned before it can be considered implemented. |
| `DoD audit complete`                   | Each PRD and inherited DoD item has a current-main source/test/smoke/parity evidence link, with no unresolved finding.                        |
| `Release gate complete`                | The full regression plus the required real Studio, desktop, Playwright, and computed-style parity gates passed against the recorded commit.   |

### Current implementation inventory

| PRD                                | Merged implementation evidence                                                                                                                                                                                                                                                                                                                                                                                                                         | Current evidence status                  | Next proof required                                                                                                                                                                 |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A1 — contracts                     | [#262](https://github.com/0x-copilot-dev/0x-copilot/pull/262)                                                                                                                                                                                                                                                                                                                                                                                          | Merged — evidence audit pending          | Contract, golden journey, and cross-language parity inventory.                                                                                                                      |
| A2 — artifact repository           | [#263](https://github.com/0x-copilot-dev/0x-copilot/pull/263)                                                                                                                                                                                                                                                                                                                                                                                          | Merged — evidence audit pending          | Adapter parity, streaming limits, retention/deletion, route isolation.                                                                                                              |
| A3 — operation gateway             | [#264](https://github.com/0x-copilot-dev/0x-copilot/pull/264)                                                                                                                                                                                                                                                                                                                                                                                          | Merged — evidence audit pending          | Descriptor coverage and off/shadow/enforce conformance.                                                                                                                             |
| A4 — effect stager                 | [#265](https://github.com/0x-copilot-dev/0x-copilot/pull/265)                                                                                                                                                                                                                                                                                                                                                                                          | Merged — evidence audit pending          | No-executor object graph and zero-effect adversarial proof.                                                                                                                         |
| A5 — commit coordinator            | [#268](https://github.com/0x-copilot-dev/0x-copilot/pull/268), [#269](https://github.com/0x-copilot-dev/0x-copilot/pull/269), [#272](https://github.com/0x-copilot-dev/0x-copilot/pull/272)                                                                                                                                                                                                                                                            | Merged — evidence audit pending          | Claim-before-effect, reconcile, sole producer, and exact MCP flow.                                                                                                                  |
| B1 — authored artifacts            | [#266](https://github.com/0x-copilot-dev/0x-copilot/pull/266), [#270](https://github.com/0x-copilot-dev/0x-copilot/pull/270)                                                                                                                                                                                                                                                                                                                           | Merged — evidence audit pending          | Exact revision source/promotion and draft convergence.                                                                                                                              |
| B2 — renderers/editors             | [#277](https://github.com/0x-copilot-dev/0x-copilot/pull/277), [#343](https://github.com/0x-copilot-dev/0x-copilot/pull/343)                                                                                                                                                                                                                                                                                                                           | Merged — evidence audit pending          | Four safe renderers, large-content fallback, editor conflicts, UI parity.                                                                                                           |
| B3 — canvas lifecycle              | [#282](https://github.com/0x-copilot-dev/0x-copilot/pull/282), [#326](https://github.com/0x-copilot-dev/0x-copilot/pull/326), [#327](https://github.com/0x-copilot-dev/0x-copilot/pull/327)                                                                                                                                                                                                                                                            | Merged — evidence audit pending          | Event replay, Studio/Focus common projector, lifecycle presentation.                                                                                                                |
| C1 — workspace overlay             | [#267](https://github.com/0x-copilot-dev/0x-copilot/pull/267), [#319](https://github.com/0x-copilot-dev/0x-copilot/pull/319)                                                                                                                                                                                                                                                                                                                           | Merged — evidence audit pending          | File/in-memory/Postgres adapter parity, no-host-mutation and restart tests.                                                                                                         |
| C2 — workspace authority           | [#273](https://github.com/0x-copilot-dev/0x-copilot/pull/273), [#322](https://github.com/0x-copilot-dev/0x-copilot/pull/322), [#328](https://github.com/0x-copilot-dev/0x-copilot/pull/328), [#341](https://github.com/0x-copilot-dev/0x-copilot/pull/341)                                                                                                                                                                                             | Merged — evidence audit pending          | Native confinement/permit/journal proofs on supported desktop platforms.                                                                                                            |
| C3 — workspace product integration | [#288](https://github.com/0x-copilot-dev/0x-copilot/pull/288), [#292](https://github.com/0x-copilot-dev/0x-copilot/pull/292), [#306](https://github.com/0x-copilot-dev/0x-copilot/pull/306), [#344](https://github.com/0x-copilot-dev/0x-copilot/pull/344)                                                                                                                                                                                             | Merged — evidence audit pending          | Real supervised CSV save, web fallback, visible drift/revocation/crash behavior.                                                                                                    |
| D1 — MCP convergence               | [#274](https://github.com/0x-copilot-dev/0x-copilot/pull/274), [#344](https://github.com/0x-copilot-dev/0x-copilot/pull/344)                                                                                                                                                                                                                                                                                                                           | Merged — evidence audit pending          | Pre-dispatch classification and exact approved dispatch proof.                                                                                                                      |
| D2 — built-ins/subagents           | [#278](https://github.com/0x-copilot-dev/0x-copilot/pull/278), [#279](https://github.com/0x-copilot-dev/0x-copilot/pull/279), [#280](https://github.com/0x-copilot-dev/0x-copilot/pull/280), [#285](https://github.com/0x-copilot-dev/0x-copilot/pull/285), [#345](https://github.com/0x-copilot-dev/0x-copilot/pull/345)                                                                                                                              | Merged — evidence audit pending          | Full callable descriptor inventory, authority narrowing, retry-safe attribution.                                                                                                    |
| D3 — sandbox adapter               | Earlier sandbox work: [#281](https://github.com/0x-copilot-dev/0x-copilot/pull/281), [#286](https://github.com/0x-copilot-dev/0x-copilot/pull/286). Filesystem-first contract: [#346](https://github.com/0x-copilot-dev/0x-copilot/pull/346).                                                                                                                                                                                                          | **Architecture reconciliation required** | Implement and verify the new file-native lifecycle, immutable snapshot, provider-attestation, C1/C3 handoff, and real desktop/parity requirements. No ai-backend Postgres fallback. |
| D4 — browser adapter               | [#276](https://github.com/0x-copilot-dev/0x-copilot/pull/276), [#290](https://github.com/0x-copilot-dev/0x-copilot/pull/290), [#330](https://github.com/0x-copilot-dev/0x-copilot/pull/330)                                                                                                                                                                                                                                                            | Merged — evidence audit pending          | Browser adversarial/live suite, exact action binding, reconciliation.                                                                                                               |
| E1 — accountability/lifecycle      | [#289](https://github.com/0x-copilot-dev/0x-copilot/pull/289) through [#320](https://github.com/0x-copilot-dev/0x-copilot/pull/320), plus [#337](https://github.com/0x-copilot-dev/0x-copilot/pull/337)                                                                                                                                                                                                                                                | Merged — evidence audit pending          | Cross-language receipt/source/pending folds, retention, repair, and operation evidence.                                                                                             |
| E2 — cutover/conformance           | [#309](https://github.com/0x-copilot-dev/0x-copilot/pull/309), [#313](https://github.com/0x-copilot-dev/0x-copilot/pull/313), [#315](https://github.com/0x-copilot-dev/0x-copilot/pull/315), [#324](https://github.com/0x-copilot-dev/0x-copilot/pull/324), [#338](https://github.com/0x-copilot-dev/0x-copilot/pull/338)–[#342](https://github.com/0x-copilot-dev/0x-copilot/pull/342), [#344](https://github.com/0x-copilot-dev/0x-copilot/pull/344) | Merged — evidence audit pending          | Cohort/backout evidence and the final all-PRD release gate.                                                                                                                         |

### How to read the identifiers

**A1–E2 are the only Generative Surfaces v2.1 PRDs and implementation
waves.** They define the product scope. They have not been renamed or replaced.

`F-###` is an **audit-finding identifier**, not a sixth PRD wave and not new
feature scope. Findings let a later review name a cross-PRD architectural or
release defect precisely, keep its evidence and owner stable, and prevent a
merged implementation slice from being mistaken for complete product evidence.
For example, F-012 is a finding against C1's universal mutation boundary; it is
not an additional feature after Wave E.

`G#` is a **release-journey identifier** used only for the real supervised
desktop scenarios. It is likewise not a PRD wave. The release state is the
conjunction: every A–E DoD is evidenced, no audit finding remains open without
an approved non-launch disposition, and the required G journeys and
computed-style parity run on one recorded current-main SHA.

### Current-main revalidation (2026-07-26)

This is a **source/evidence revalidation** at
`881bca443fca3978ea276e34255b894bb46b465a`, not a substitute for the command
receipts and real-product gates in the close-out record below. All historical
Wave audit baselines remain useful implementation evidence, but none proves the
current release candidate on its own.

- Every inventory row remains `Merged — evidence audit pending` or
  `Architecture reconciliation required`; none may advance to `DoD audit
complete` from historical focused tests alone.
- The file-native D3 foundation merged in [#372](https://github.com/0x-copilot-dev/0x-copilot/pull/372).
  This does **not** make a sandbox available: a concrete attested provider,
  A2 deliverable publication, explicit C1 patch import, and real desktop/parity
  evidence remain required. There is no ai-backend Postgres fallback.
- F-006 remains a correction in progress in draft
  [#356](https://github.com/0x-copilot-dev/0x-copilot/pull/356). Its generic
  effect decision path must fail closed for event-store errors and preserve
  superseding-stage correlation across host-run changes before it can merge. A
  later independent review also found that a same-org peer can PATCH an
  unowned draft, replace its mutable owner field, and then send it; PATCH,
  discard, and artifact-resolution fallback must be corrected at their shared
  ownership boundary before the PR can merge.
- Draft [#376](https://github.com/0x-copilot-dev/0x-copilot/pull/376) adds G0,
  the plain-chat supervised-desktop journey, but independent review found that
  its absence assertions omit v2 tool-ledger events and one receipt surface
  selector. G0 therefore remains blocked; G1–G10 and the computed-style parity
  source/anchor/report remain absent as release evidence.
- Draft [#371](https://github.com/0x-copilot-dev/0x-copilot/pull/371) is also
  blocked: its proposed gateway stage capability remains reflectively mintable
  and usable from a child task. Draft [#374](https://github.com/0x-copilot-dev/0x-copilot/pull/374)
  is blocked because its presentation canary is bypassable and staged writes
  retain a second effect-dispatch route. These are architectural corrections,
  not new PRD waves.
- F-008 now has one current-main execution receipt: `services/ai-backend` at
  `c0315a431b412efb5e8769bd6d03855096161ee4` passed `4,977`, skipped `126`,
  and deselected `1` (`python -m pytest -q`, 2026-07-26). Backend, facade,
  TypeScript workspaces, desktop, real Studio, and parity receipts remain
  **current-main unverified**. The same code baseline also has full service
  receipts for `services/backend` (`1,939 passed`, `47 skipped`) and
  `services/backend-facade` (`353 passed`, `1 skipped`). F-009, F-010, F-011,
  and F-014 remain code/static gates until the remaining release commands also
  exercise them.

### D3 foundation independent review update (2026-07-26)

This review covers the unmerged file-native D3 foundation candidate, not a
merged product capability. It **blocked** that candidate from becoming ready
until the following shared-boundary failures are resolved:

- A genuinely empty, version-zero workspace can currently materialize as an
  empty snapshot and continue to provider execution. The snapshot authority
  must reject any plan with no sealed selected input before upload or execution;
  retained-history pointer loss remains a separate fail-closed corruption case.
- If provider creation succeeds, cleanup-duty persistence fails, and immediate
  teardown also fails, the provider reference is lost. The recovery record must
  be durably claimable before the provider can become unreachable, so a reaper
  can complete cleanup after restart.
- The composition path can expose the model-facing tool before all authority
  prerequisites exist: full C1 base-plus-overlay snapshot export, durable A2
  artifact/deliverable publication, and an explicit user-triggered C1 patch
  importer. Its availability gate must require all of them; a runner that
  returns no deliverables or a coordinator with no importer remains dark.

The reviewer confirmed the intended positives: queued context is compared with
persisted run identity before provider work, retained-history nonzero pointer
gaps fail closed, model input is command-only, and no host-write or automatic
patch-import route is exposed. These controls do not make the capability
mergeable while the P1s above remain.

### Legacy v2 E3 clarification

The older Generative Surfaces v2 program has a separate
[`PRD-E3`](../generative-surfaces-v2/prds/PRD-E3-audit-usage-retirement.md): audit
hardening, usage endpoints, and v1 retirement. Its implementation and cutover were
merged in [#253](https://github.com/0x-copilot-dev/0x-copilot/pull/253) and
[#254](https://github.com/0x-copilot-dev/0x-copilot/pull/254), respectively. It is
not an unimplemented v2.1 PRD and must not be recreated in parallel with v2.1 D3.
The v2.1 Wave E work is E1 and E2 only; any new E3 scope requires a new reviewed PRD.

### Audit findings ledger — `F-###` is not a PRD wave

The `F-###` identifiers below are stable **finding IDs**. They do not add,
renumber, or replace A1–E2. A finding may span several PRDs because it records a
broken shared boundary discovered only when their merged implementations meet.

| ID    | Finding                                                                                                                                                 | Evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Decision / required architectural resolution                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Status                                                                 |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| F-001 | A merged implementation was being treated as completion without a current-main evidence ledger.                                                         | PRs #262–#346 show many merged slices; the PRDs have no live DoD evidence map.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | This README is the required program ledger. Every close-out must map each DoD item to current evidence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Open until every PRD is audited.                                       |
| F-002 | The sandbox must be desktop and filesystem first; selected bytes must be sealed and verified before any provider receives them.                         | [#372](https://github.com/0x-copilot-dev/0x-copilot/pull/372) merged the file-native lifecycle/snapshot foundation and its three initial P1 corrections; independent focused review passed `203`, skipped `2`. It did not ship a concrete attested provider, A2 deliverables, C1 patch import, or real desktop/parity proof.                                                                                                                                                                                                                                         | Retain the file-backed lifecycle/session/usage/cleanup namespace and C1/C3-only local mutation. No snapshot with no sealed selected input may upload or execute; provider recovery must be durable before the provider can be lost; identity comes only from the verified persisted run. Non-file/Postgres history remains unavailable; do not add a fallback.                                                                                                                                                                                                                                                                                                          | **Release blocker — provider/handoff/release proof pending.**          |
| F-003 | Desktop runtime documentation contained stale language that describes ai-backend Postgres as the default.                                               | Resolved by [#370](https://github.com/0x-copilot-dev/0x-copilot/pull/370), merged 2026-07-26: file-first docs/comments now defer to the tested `resolveAiStoreBackend` source of truth.                                                                                                                                                                                                                                                                                                                                                                              | Retain the real supervised desktop file-first smoke; do not treat comments or hermetic tests as runtime proof.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | **Code merged — real smoke remains a release blocker.**                |
| F-004 | Adversarial tests exist in individual PR slices, but their coverage is not recorded against the final integrated architecture.                          | A5/D3/D4/E2 PRDs explicitly require adversarial/conformance checks; #330 and #339 added related readiness/conformance work.                                                                                                                                                                                                                                                                                                                                                                                                                                          | Audit test names, scope, and current-main results per PRD; add root-cause fixes and shared architectural gates for any gap.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Open.                                                                  |
| F-005 | D3 has a typed gateway/tool seam but no verified production worker composition, so the model-visible sandbox capability must remain unavailable.        | No repository provider attests the D3 lifecycle/ownership/reaper contract. Official OpenAI materials show that hosted Code Interpreter offers network-policy configuration and that hosted shell uses restricted policy-controlled egress; this makes OpenAI a viable candidate, not an implemented or attested provider. The correction is in draft [#377](https://github.com/0x-copilot-dev/0x-copilot/pull/377).                                                                                                                                                  | Compose exactly one file-native `SandboxWorkerBundle` from verified C1/A2 authority, trusted file-store identities, durable restart-safe cleanup, a real patch collector, and an externally verifiable provider gateway. Availability depends on every prerequisite. The model-facing tool calls only the coordinator; no direct executor, Postgres fallback, or automatic patch import.                                                                                                                                                                                                                                                                                | **Release blocker — provider implementation and attestation pending.** |
| F-006 | Draft-send has two competing sources of truth: it stages legacy mutable draft bytes rather than the selected immutable Artifact revision.               | Current `main` still stages `DraftRecord` through legacy `WriteStager`. Draft [#356](https://github.com/0x-copilot-dev/0x-copilot/pull/356) adds generic MCP decisions, immutable binding, and direct send ownership checks; independent review found a same-org peer can still PATCH a draft without a current-owner check, overwrite the mutable owner field, then send it. The same missing boundary affects discard. The Artifact resolver also collapses inaccessible/mismatched canonical artifacts into the `None` value used for unmigrated legacy fallback. | Bind the proposal and coordinator exclusively to an Artifact revision/content digest. At the shared DraftService/repository mutation boundary, verify the persisted owner before every mutation or transition and never transfer ownership through an update; return opaque denial with zero versions/events/effects for peers. Reserve the legacy fallback exclusively for a verified unmigrated row; inaccessible or mismatched canonical artifacts must fail closed. Retain generic owner-authorized decisions and fail closed on lookup error or any F-006 supersession across host-run changes. Do not add a draft-specific bypass or copy bytes at approval time. | **Release blocker — correction in progress.**                          |
| F-007 | The release gate lacks complete executable supervised-desktop journey proof and a computed-style parity comparison for artifact surfaces.               | Draft [#376](https://github.com/0x-copilot-dev/0x-copilot/pull/376) adds G0 but independent review found it does not forbid all v2 tool-ledger events and checks only one of the receipt-surface selectors. G1–G10, mapped source/anchors, and current-SHA reports remain absent.                                                                                                                                                                                                                                                                                    | Repair G0's absence guards; add runnable real-stack journeys and a mapped design source/anchor set; retain generated artifacts as release evidence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | **Release blocker — correction in progress.**                          |
| F-008 | The integrated release gate remains incomplete; service regression receipts alone do not prove the product.                                             | Current-main code baseline `c0315a43` (2026-07-26): ai-backend `4,977 passed / 126 skipped / 1 deselected`; backend `1,939 passed / 47 skipped`; facade `353 passed / 1 skipped`. Chat-surface, surface-renderers, frontend, desktop, real Studio, and design-parity receipts have not yet been captured at a current SHA.                                                                                                                                                                                                                                           | Run every remaining workspace/desktop suite at the recorded SHA, then diagnose any failure against the current architecture; do not mask it through a broad skip. The full release gate also still requires real Studio journeys and computed-style parity.                                                                                                                                                                                                                                                                                                                                                                                                             | Open.                                                                  |
| F-009 | The static no-executor test only scans immediate `agent_runtime/effects/*.py` imports, so indirect model/stager-to-executor reachability can escape it. | Resolved by [#351](https://github.com/0x-copilot-dev/0x-copilot/pull/351), merged 2026-07-26: `effect_execution_reachability.py` follows symbol-level model/stager/worker paths and has an indirect function-local-import dispatch canary (`11 passed`).                                                                                                                                                                                                                                                                                                             | Keep the explicit allowlist narrow and retain the architecture suite in the final regression. AST analysis does not prove reflective or runtime-injected dispatch, which remains forbidden by the surrounding boundaries and review.                                                                                                                                                                                                                                                                                                                                                                                                                                    | Merged — static boundary closed.                                       |
| F-010 | Python and TypeScript each reduced presentation lifecycle events independently without a cross-language differential replay fixture.                    | Resolved by [#354](https://github.com/0x-copilot-dev/0x-copilot/pull/354), merged 2026-07-26: the shared `canvas_lifecycle_corpus.json` is replayed at every event prefix by the real Python and TypeScript reducers; both must produce byte-equivalent state (`5` tests per reducer).                                                                                                                                                                                                                                                                               | Retain the shared-corpus differential in the release regression. Archival is deliberately not claimed: no canonical archival event/state contract currently exists to replay.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Merged — current lifecycle parity closed.                              |
| F-011 | Electron production composition installs `UnavailableNativeWorkspaceAuthority`, so C2's native commit helper is reached only by tests.                  | Resolved by [#352](https://github.com/0x-copilot-dev/0x-copilot/pull/352), merged 2026-07-26: Electron main constructs the authority only after encrypted storage, confinement self-test, signed helper, and root-identity checks; targeted tests and desktop typecheck are green.                                                                                                                                                                                                                                                                                   | Unsupported or unverified environments remain unavailable. Retain the packaged-macOS supervised smoke as a release gate.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | **Code merged — real smoke remains a release blocker.**                |
| F-012 | The C1 raw overlay service remains directly usable outside the enforced gateway, so "every mutation stages" is not structurally universal.              | Draft [#371](https://github.com/0x-copilot-dev/0x-copilot/pull/371) replaced the public string scope with a one-use stage capability, but independent review still reflectively minted the module-global private function and used it outside `OperationGateway`; the capability also survived into an `asyncio.create_task` child.                                                                                                                                                                                                                                  | Establish an invocation-scoped authority boundary that cannot be retrieved through arbitrary same-process reflection, bind it to the issuing gateway task/frame, and prove public, reflective, replay, cross-run, cross-digest, and child-task attempts have zero revisions, effects, host mutations, and commands.                                                                                                                                                                                                                                                                                                                                                     | **Release blocker — correction in progress.**                          |
| F-013 | D1 retains MCP-specific ledger/surface coupling and lacks a transport-neutral sole-dispatch proof.                                                      | Draft [#374](https://github.com/0x-copilot-dev/0x-copilot/pull/374) correctly moves generic presentation and reauthorizes before generic dispatch, but independent review found its AST canary bypassable through ordinary/dynamic imports and a second staged-write connector dispatch outside `EffectCoordinator`.                                                                                                                                                                                                                                                 | Harden the boundary against direct, module, function-local, dynamic, and reflective imports; reconcile staged-write dispatch into the same approved effect architecture or formally refactor the shared coordinator so there is one provable dispatch boundary. Add adverse proof for both paths.                                                                                                                                                                                                                                                                                                                                                                       | **Release blocker — correction in progress.**                          |
| F-014 | D2 lacked a repository-wide inventory/canary proving all model-visible tools use approved descriptors and cannot emit bespoke surfaces.                 | Resolved by [#358](https://github.com/0x-copilot-dev/0x-copilot/pull/358), merged 2026-07-26: the enabled runtime/framework tool inventory maps every model-visible tool to exactly one catalog entry and descriptor; planted AST canaries fail for an unregistered tool or direct `SurfaceEnvelope`/`surface` emission (`34` focused tests).                                                                                                                                                                                                                        | Retain the inventory and planted negatives in final regression. AST analysis does not prove reflective/runtime-injected emission or arbitrary interprocedural dataflow; those remain forbidden by reviewed runtime boundaries.                                                                                                                                                                                                                                                                                                                                                                                                                                          | Merged — static boundary closed.                                       |

### No-bandaid operating rule

When a check fails, the owner records the failure in the findings ledger before
changing behavior. The remedy must identify the violated boundary or contract,
change the lowest shared layer that owns that contract, and add a reusable
conformance/adversarial test. A local conditional, exception, feature-specific
fallback, waiver, duplicate executor, or new direct path is not an acceptable
resolution unless the README records a reviewed temporary exemption with an owner,
expiry, and E2 removal plan.

In particular:

- do not add local workspace writes outside C3's Electron-main authority;
- do not add direct provider/MCP/browser dispatch around the gateway/stager/commit
  coordinator path;
- do not add a persistence fallback that changes the desktop file-first authority;
- do not claim an in-process helper or mocked provider is a production security
  boundary;
- fix repeated UI drift in the shared component/token/projector layer, not separately
  in web and desktop hosts.

### Audit run — Wave A + B (2026-07-26)

Audit baseline: clean `main` at `757e009d` (the audit began before the current
origin/main documentation-only merge). This is evidence, not a release pass.

| Area                  | Current result                                                                                                                                                    | Required next action                                                                                       |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| A1 contracts          | Proven for SSOT, Python/TS parity, golden journeys, and old-fixture replay.                                                                                       | Keep the contract corpus in the final release regression.                                                  |
| A2 artifacts          | Memory/file adapter conformance and scoped streaming routes are proven; Postgres coverage is narrower and retention is not aggregated across every deletion path. | Add shared Postgres conformance plus aggregate retention/deletion proof.                                   |
| A3 gateway            | Descriptor/off/shadow and operation matrix tests are proven.                                                                                                      | Retain as integration regression; historical PR-isolation negatives are not inferred from this later main. |
| A4/A5 effects         | Core staging, coordinator ordering, claims, reconciliation, sole `effect.applied` producer, and the F-009 symbol-level reachability gate are proven.              | Retain the architecture gate and establish whole-suite zero-effect proof while staging.                    |
| B1 publication/drafts | Explicit publication and Artifact-backed authoring are proven. Draft-send is **not** Artifact-revision-backed.                                                    | Resolve F-006 before release.                                                                              |
| B2/B3 UI              | Safe renderer/editor coverage and the shared Python/TS lifecycle differential are proven by [#354](https://github.com/0x-copilot-dev/0x-copilot/pull/354).        | Resolve F-007 and F-008 before release; archival remains explicitly out of the lifecycle contract.         |

Focused audit suites passed, but this does **not** override F-008: the historical
audit had no green full-suite receipt. A later ai-backend full-suite receipt is
recorded above; the remaining workspace and product gates are still unverified.
The remaining PRD waves are being audited independently and will be appended here
with the same evidence / finding / required-action format.

### Audit run — Wave C + D except D3 (2026-07-26)

Audit baseline: `origin/main` at `202364f1`. It was a source and adversarial-test
evidence review; it did not freshly execute the listed suites. `Proven` therefore
means that a committed focused test exists, not that a release gate passed.

| Area                   | Current result                                                                                                                                                         | Required next action                                                             |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| C1 overlay             | Overlay-first reads, durable mutations, grant rejection, and no host write through the enforced route have focused proof.                                              | Resolve F-012 and add common adapter/restart conformance.                        |
| C2 authority           | Main-only protocol, permits, CAS, journal, and verified production composition are implemented.                                                                        | Run a real supervised-desktop confinement and commit smoke.                      |
| C3 product             | Hermetic authority tests prove approve-only writes; live desktop CSV, web fallback, receipts, and parity proof are absent.                                             | Add executable desktop journeys and parity evidence after C2 is composed.        |
| D1 MCP                 | Canonical classification, staging, and exact dispatch are proven.                                                                                                      | Resolve F-013 and retain auth/reconnect/UI live evidence.                        |
| D2 built-ins/subagents | Catalog/adapters plus the F-014 model-visible-tool inventory and bespoke-surface canaries are proven by [#358](https://github.com/0x-copilot-dev/0x-copilot/pull/358). | Add exhaustive authority narrowing and retry-attribution coverage.               |
| D4 browser             | Electron-main ownership is structurally present; real browser artifact/upload/effect and security smokes are absent.                                                   | Add a supervised browser-session staged-effect journey and no-bypass graph gate. |

These findings supersede any earlier claim that C2/C3 is merely awaiting a smoke:
the desktop authority must first be composed correctly. The audit did not find a
safe shortcut around Electron-main authority, and none will be added.

### Audit run — Wave E1 + E2 (2026-07-26)

Audit baseline: `origin/main` at `c6734529`. This was static evidence review, so
`Proven` means implementation plus focused in-tree coverage exists; it is not a new
release execution receipt.

| Area                   | Current result                                                                                                             | Required next action                                                                                                                            |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| E1 usage/export        | Retry-safe attribution, tamper-evident receipt export, and sensitive-route identity tests are present.                     | Add one cross-language all-prefix conformance gate for receipt, Sources, and Pending projections; finish lifecycle retention/deletion evidence. |
| E1 repair/operations   | Repair executor and tests exist, while the lifecycle runbook still marks D10–D13 families incomplete.                      | Align the runbook with implemented controls and add scheduled/operational evidence before claiming production completeness.                     |
| E2 migration           | Migration services and hermetic crash/resume/quarantine tests are present.                                                 | Retain a real-dataset migration report and a retained-export verification receipt.                                                              |
| E2 rollout/backout     | Cohort and soak policy code exists, but `RolloutCohortPolicy.admit()` has no production caller; rollback is a pure helper. | Wire cohort admission and operational backout control through the production request path; prove rollback cannot restore unsafe writes.         |
| E2 final conformance   | The D9 runner has strong static checks, but its `ready=true` unit result is not a release invocation.                      | Produce a versioned `--require-all` artifact after the complete D10 journey matrix exists.                                                      |
| E1/E2 release evidence | Current parity, real supervised Electron Studio, credentialed Playwright, and six continuous Studio scenarios are absent.  | Implement and run the executable journey suite, then run fresh computed-style parity against E1/E2 surfaces.                                    |

Wave E therefore remains **DoD audit incomplete**. A static final-conformance report
or a fake-model service topology smoke must never be relabeled as the product release
gate.

### Required close-out record for every PR

Before merging an implementation PR, add or update an entry under this README with:

1. PR URL, merge commit, and `origin/main` base SHA.
2. Every PRD and inherited DoD item, its implementation location, and its exact test,
   real smoke, or design-parity artifact.
3. Any item that was not run, marked `unknown` rather than passed, with a concrete
   follow-up owner and gate.
4. Adversarial/no-bypass result for effectful work, including the code path tested.
5. Any architecture finding discovered and the shared-layer remediation.
6. The post-merge rebase/full affected-suite result.

### Final release gate

Only mark the program `Release gate complete` after evidence on one recorded current
`main` SHA shows all of the following:

- every row above is `DoD audit complete`;
- full affected backend and TypeScript/workspace regression suites pass;
- six continuous Studio scenarios pass on the real supervised desktop stack;
- Playwright desktop journeys from `tools/desktop-journeys/` pass with real configured
  providers where the scenario requires them;
- the computed-style `tools/design-parity/` audit against the current design mock has
  zero HIGH findings, with the report linked here;
- all architectural findings are resolved or have an explicit approved non-launch
  disposition.
