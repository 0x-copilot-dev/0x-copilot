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

### Architectural findings ledger

| ID    | Finding                                                                                                                        | Evidence                                                                                                                         | Decision / required architectural resolution                                                                                                                                                            | Status                           |
| ----- | ------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| F-001 | A merged implementation was being treated as completion without a current-main evidence ledger.                                | PRs #262–#346 show many merged slices; the PRDs have no live DoD evidence map.                                                   | This README is the required program ledger. Every close-out must map each DoD item to current evidence.                                                                                                 | Open until every PRD is audited. |
| F-002 | The sandbox must be desktop and filesystem first; it cannot gain an ai-backend Postgres lifecycle fallback.                    | [D3 contract](prds/PRD-D3-sandbox-adapter.md), [desktop file-store migration](../../operations/desktop-file-store-migration.md). | D3 owns a file-backed lifecycle/session/usage/cleanup namespace, immutable artifact snapshots, and C1/C3-only local mutation. Earlier D3 code must be reconciled to this design, not patched around it. | Open.                            |
| F-003 | Desktop runtime documentation contains stale language that describes ai-backend Postgres as the default.                       | `tools/desktop-runtime/README.md`; `apps/desktop/main/services/service-env.ts` contains legacy branch comments.                  | The D3 implementation PR corrects documentation/comments while retaining `resolveAiStoreBackend` as the one tested source of truth.                                                                     | Open.                            |
| F-004 | Adversarial tests exist in individual PR slices, but their coverage is not recorded against the final integrated architecture. | A5/D3/D4/E2 PRDs explicitly require adversarial/conformance checks; #330 and #339 added related readiness/conformance work.      | Audit test names, scope, and current-main results per PRD; add root-cause fixes and shared architectural gates for any gap.                                                                             | Open.                            |

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
