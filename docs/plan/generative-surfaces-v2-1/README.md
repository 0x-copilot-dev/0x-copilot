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

**Baseline:** `origin/main` at `62276d42eb548d7803c592171d9f1f2ef74861bc`
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

### Legacy v2 E3 clarification

The older Generative Surfaces v2 program has a separate
[`PRD-E3`](../generative-surfaces-v2/prds/PRD-E3-audit-usage-retirement.md): audit
hardening, usage endpoints, and v1 retirement. Its implementation and cutover were
merged in [#253](https://github.com/0x-copilot-dev/0x-copilot/pull/253) and
[#254](https://github.com/0x-copilot-dev/0x-copilot/pull/254), respectively. It is
not an unimplemented v2.1 PRD and must not be recreated in parallel with v2.1 D3.
The v2.1 Wave E work is E1 and E2 only; any new E3 scope requires a new reviewed PRD.

### Architectural findings ledger

| ID    | Finding                                                                                                                                                 | Evidence                                                                                                                                                                                                                                                                                                                                                                      | Decision / required architectural resolution                                                                                                                                                                                                                                                                                                                                           | Status                                                  |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| F-001 | A merged implementation was being treated as completion without a current-main evidence ledger.                                                         | PRs #262–#346 show many merged slices; the PRDs have no live DoD evidence map.                                                                                                                                                                                                                                                                                                | This README is the required program ledger. Every close-out must map each DoD item to current evidence.                                                                                                                                                                                                                                                                                | Open until every PRD is audited.                        |
| F-002 | The sandbox must be desktop and filesystem first; it cannot gain an ai-backend Postgres lifecycle fallback.                                             | [D3 contract](prds/PRD-D3-sandbox-adapter.md), [desktop file-store migration](../../operations/desktop-file-store-migration.md).                                                                                                                                                                                                                                              | D3 owns a file-backed lifecycle/session/usage/cleanup namespace, immutable artifact snapshots, and C1/C3-only local mutation. Earlier D3 code must be reconciled to this design, not patched around it.                                                                                                                                                                                | Open.                                                   |
| F-003 | Desktop runtime documentation contains stale language that describes ai-backend Postgres as the default.                                                | `tools/desktop-runtime/README.md`; `apps/desktop/main/services/service-env.ts` contains legacy branch comments.                                                                                                                                                                                                                                                               | The D3 implementation PR corrects documentation/comments while retaining `resolveAiStoreBackend` as the one tested source of truth.                                                                                                                                                                                                                                                    | Open.                                                   |
| F-004 | Adversarial tests exist in individual PR slices, but their coverage is not recorded against the final integrated architecture.                          | A5/D3/D4/E2 PRDs explicitly require adversarial/conformance checks; #330 and #339 added related readiness/conformance work.                                                                                                                                                                                                                                                   | Audit test names, scope, and current-main results per PRD; add root-cause fixes and shared architectural gates for any gap.                                                                                                                                                                                                                                                            | Open.                                                   |
| F-005 | The current model-facing sandbox tool still reaches the legacy direct `session_scope → aexecute` path rather than the typed coordinator.                | `runtime_worker/capability_tool_wiring.py`, `agent_runtime/capabilities/sandbox/execute_tool.py`, and `sandbox/coordinator.py`.                                                                                                                                                                                                                                               | Compose one file-native `SandboxWorkerBundle`; the gateway-facing tool builds a canonical `SandboxRunRequest` and calls `SandboxLifecycleCoordinator`. Add an architecture test prohibiting direct model-facing `session_scope`/`aexecute`.                                                                                                                                            | Open.                                                   |
| F-006 | Draft-send has two competing sources of truth: it stages legacy mutable draft bytes rather than the selected immutable Artifact revision.               | Current-main audit, 2026-07-26: `agent_runtime/api/draft_service.py::_stage_send_v2` passes `DraftRecord` into legacy `surfaces_v2/staging.py::WriteStager`. Draft [#356](https://github.com/0x-copilot-dev/0x-copilot/pull/356) proves the immutable binding but its MCP effect cannot be approved through the product: the current effect-decision route is workspace-only. | Rework B1 draft-send so the proposal binds an Artifact revision/content digest and the effect coordinator receives only that immutable revision. Add the generic owner-authorized effect-decision path/UI and reject pre-existing v1 approval resolution once an F-006 `effect.staged` stage supersedes that draft. Do not add a draft-specific bypass or copy bytes at approval time. | **Release blocker — correction in progress.**           |
| F-007 | The release gate has no executable supervised-desktop journey proof or computed-style parity comparison for artifact surfaces.                          | Current-main audit, 2026-07-26: `tools/desktop-journeys/generative-workflows/JOURNEYS.md` is a plan; `tools/design-parity/surfaces/artifact-dataset/SOURCE-GAP.md` records the missing comparison source.                                                                                                                                                                     | Add runnable real-stack journeys and a mapped design source/anchor set, then retain their generated artifacts as release evidence.                                                                                                                                                                                                                                                     | **Release blocker.**                                    |
| F-008 | The integrated full-suite gates are currently non-green.                                                                                                | Current-main audit, 2026-07-26: ai-backend stopped at a credential-gate failure after 1,619 passing tests; `@0x-copilot/chat-surface` failed `RunDestination.sourceOpen.test.tsx`.                                                                                                                                                                                            | Diagnose each failure against the current architecture; update the owning contract or fixture, never mask it through a broad skip.                                                                                                                                                                                                                                                     | Open.                                                   |
| F-009 | The static no-executor test only scans immediate `agent_runtime/effects/*.py` imports, so indirect model/stager-to-executor reachability can escape it. | Resolved by [#351](https://github.com/0x-copilot-dev/0x-copilot/pull/351), merged 2026-07-26: `effect_execution_reachability.py` follows symbol-level model/stager/worker paths and has an indirect function-local-import dispatch canary (`11 passed`).                                                                                                                      | Keep the explicit allowlist narrow and retain the architecture suite in the final regression. AST analysis does not prove reflective or runtime-injected dispatch, which remains forbidden by the surrounding boundaries and review.                                                                                                                                                   | Merged — static boundary closed.                        |
| F-010 | Python and TypeScript each reduced presentation lifecycle events independently without a cross-language differential replay fixture.                    | Resolved by [#354](https://github.com/0x-copilot-dev/0x-copilot/pull/354), merged 2026-07-26: the shared `canvas_lifecycle_corpus.json` is replayed at every event prefix by the real Python and TypeScript reducers; both must produce byte-equivalent state (`5` tests per reducer).                                                                                        | Retain the shared-corpus differential in the release regression. Archival is deliberately not claimed: no canonical archival event/state contract currently exists to replay.                                                                                                                                                                                                          | Merged — current lifecycle parity closed.               |
| F-011 | Electron production composition installs `UnavailableNativeWorkspaceAuthority`, so C2's native commit helper is reached only by tests.                  | Resolved by [#352](https://github.com/0x-copilot-dev/0x-copilot/pull/352), merged 2026-07-26: Electron main constructs the authority only after encrypted storage, confinement self-test, signed helper, and root-identity checks; targeted tests and desktop typecheck are green.                                                                                            | Unsupported or unverified environments remain unavailable. Retain the packaged-macOS supervised smoke as a release gate.                                                                                                                                                                                                                                                               | **Code merged — real smoke remains a release blocker.** |
| F-012 | The C1 raw overlay service remains directly usable outside the enforced gateway, so "every mutation stages" is not structurally universal.              | Current-main C/D audit, 2026-07-26: `agent_runtime/capabilities/workspace/overlay.py` versus enforced `workspace/effects.py` path.                                                                                                                                                                                                                                            | Make the raw overlay service an internal primitive and add a graph gate that restricts model-facing mutation to the enforced gateway.                                                                                                                                                                                                                                                  | Open.                                                   |
| F-013 | D1 retains MCP-specific ledger/surface coupling and lacks a full post-approval authorization proof.                                                     | Current-main C/D audit, 2026-07-26: `capabilities/mcp/operation_adapter.py` invokes `WorkLedgerEmitter` / `SurfaceProjector`.                                                                                                                                                                                                                                                 | Move presentation onto the transport-neutral operation contract; add a post-approval authorization adversarial test before dispatcher entry.                                                                                                                                                                                                                                           | Open.                                                   |
| F-014 | D2 lacks a repository-wide inventory/canary proving all model-visible tools use approved descriptors and cannot emit bespoke surfaces.                  | Current-main C/D audit, 2026-07-26: builtin catalog/adapter coverage is representative only.                                                                                                                                                                                                                                                                                  | Add a static inventory and injected negative fixture that fails for an unregistered model-visible capability or direct bespoke surface emission.                                                                                                                                                                                                                                       | Open.                                                   |

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

Focused audit suites passed, but this does **not** override F-008: the full
ai-backend and chat-surface gates were not green. The remaining PRD waves are
being audited independently and will be appended here with the same evidence /
finding / required-action format.

### Audit run — Wave C + D except D3 (2026-07-26)

Audit baseline: `origin/main` at `202364f1`. It was a source and adversarial-test
evidence review; it did not freshly execute the listed suites. `Proven` therefore
means that a committed focused test exists, not that a release gate passed.

| Area                   | Current result                                                                                                             | Required next action                                                              |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| C1 overlay             | Overlay-first reads, durable mutations, grant rejection, and no host write through the enforced route have focused proof.  | Resolve F-012 and add common adapter/restart conformance.                         |
| C2 authority           | Main-only protocol, permits, CAS, journal, and verified production composition are implemented.                            | Run a real supervised-desktop confinement and commit smoke.                       |
| C3 product             | Hermetic authority tests prove approve-only writes; live desktop CSV, web fallback, receipts, and parity proof are absent. | Add executable desktop journeys and parity evidence after C2 is composed.         |
| D1 MCP                 | Canonical classification, staging, and exact dispatch are proven.                                                          | Resolve F-013 and retain auth/reconnect/UI live evidence.                         |
| D2 built-ins/subagents | Catalog/adapters and representative staging tests exist.                                                                   | Resolve F-014; add exhaustive authority narrowing and retry-attribution coverage. |
| D4 browser             | Electron-main ownership is structurally present; real browser artifact/upload/effect and security smokes are absent.       | Add a supervised browser-session staged-effect journey and no-bypass graph gate.  |

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
