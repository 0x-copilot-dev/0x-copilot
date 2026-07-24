# PRD-E2 — Migration, cutover, retirement, and conformance gate 🎨

**Goal.** Migrate existing artifacts/drafts/pending work safely, enable v2.1 by
independent capability cohorts, preserve old replay and signed exports, remove every
direct/bespoke effect and surface path, and make the new architecture the only
production path. Rollback may pause new work; it may never restore an unsafe direct
write.

## Implementer brief

Read all v2.1 docs and PRDs, then:

1. existing v2 migration/cutover docs and flags;
2. all current runtime feature-flag readers;
3. draft/backfill/schema migrations;
4. legacy surface/stage/approval workers;
5. desktop supervised smoke docs;
6. design-parity harness;
7. audit export old fixtures;
8. deployment/self-host/runtime staging code.

E2 is not complete while temporary exemptions, dual writable paths, or unverified
pending-effect migration remain.

## Context

A large architecture can be correct in isolation and still fail during transition.
Risks include:

- old events no longer replaying;
- an approved legacy write applying different content after migration;
- a rollback re-enabling direct workspace/MCP mutation;
- drafts existing in two writable stores;
- shadow mode accidentally dispatching twice;
- old signed receipts becoming unverifiable;
- platform-specific workspace writes enabling without security prerequisites.

## Interfaces consumed

- Every A–E1 interface and conformance suite.
- Existing v2 events/projectors/routes/flags.
- Current draft/surface/stage/approval records and workers.
- Desktop capability flags and service staging.

## Interfaces exposed

- migration/backfill commands with dry-run, checkpoint, report, and rollback metadata;
- versioned compatibility projectors/readers;
- rollout configuration and cohort telemetry;
- final architecture conformance gate;
- operational cutover/backout runbook.

## Design

### D1. Independent rollout controls

Use explicit controls, not one coarse `SURFACES_V2` switch:

```text
ARTIFACT_REPOSITORY_MODE=off|shadow|enforce
OPERATION_GATEWAY_MODE=off|shadow|enforce
EFFECT_STAGER_MODE=off|shadow|enforce
EFFECT_COMMIT_MODE=off|shadow|enforce
PRESENTATION_V2_1_MODE=off|shadow|enforce
WORKSPACE_OVERLAY_MODE=off|shadow|enforce
WORKSPACE_COMMIT_MODE=off|shadow|enforce
MCP_GATEWAY_MODE=off|shadow|enforce
SANDBOX_ADAPTER_MODE=off|shadow|enforce
BROWSER_ADAPTER_MODE=off|shadow|enforce
```

Runtime settings resolve once. Invalid combinations fail startup:

- commit enforce without stager/gateway dependencies;
- workspace commit without C2 isolation/native attestation;
- producer enforce without descriptor/executor;
- legacy and canonical writable path simultaneously.

Final defaults become enforce/on only after gates pass.

### D2. Shadow comparison

Shadow may compare:

- classification;
- disposition;
- surface/tab projection;
- receipt/pending/usage folds;
- artifact/draft metadata;
- proposal canonicalization.

Shadow must never:

- execute an operation twice;
- create a second authoritative artifact/stage;
- enqueue/apply;
- mutate model-visible return;
- suppress legacy behavior before cohort enablement.

Emit low-cardinality mismatch metrics and sampled protected diagnostics.

### D3. Legacy event and surface replay

- never rewrite append-only run events;
- compatibility projector maps old v2 events to canonical read models;
- old `surface.created` connector subjects remain valid;
- old specs/renderers remain usable for old runs;
- production-shaped hydration fixtures replace retired-envelope-only tests;
- export old runs with both old and new verifier versions.

Replay test corpus includes every checked-in old golden/event fixture and selected
production-sanitized sequences.

### D4. Draft/artifact migration

Backfill:

- discover legacy drafts by tenant/run;
- stream/hash every version;
- create artifact/revisions preserving order, timestamp, author/source where available;
- create deterministic mapping/idempotency record;
- verify byte digest and revision count;
- report corrupt/missing versions without silently skipping.

Cutover:

- new writes already use Artifact Service;
- legacy store becomes read-only;
- compare reads for a soak period;
- remove fallback only after 100% verified or explicitly quarantined;
- GC legacy bytes only through E1 reference graph and retention.

### D5. Pending legacy stage/approval migration

Classify each legacy item:

- rejected/cancelled/applied → read-only compatibility projection;
- unapproved proposed/held → create canonical stage revision, no approval carried;
- approved but unapplied → canonicalize exact target/proposal and require fresh
  approval; never transfer approval automatically;
- queued but unclaimed → cancel old command and require canonical reapproval;
- claimed/indeterminate → frozen legacy reconciler only, no redispatch; resolve to
  terminal state before worker deletion;
- native graph filesystem interrupt → cancel/convert into overlay proposal; never resume
  it to perform a host write.

Migration is idempotent and emits mapping audit. If exact bytes/arguments/digests cannot
be proven, hold for user review or quarantine—never guess.

### D6. Producer cutover order

1. Artifact Repository and publication.
2. Presentation V2.1.
3. Effect Stager.
4. Commit Coordinator compatibility MCP.
5. Workspace overlay/read-only.
6. Electron commit and workspace integration by supported platform.
7. MCP reads, then writes by connector cohort.
8. Built-ins/subagents.
9. Sandbox.
10. Browser reads/downloads, then side-effect cohorts.
11. Accountability V2 projections/export.

Each step has entry/exit metrics and soak interval. Cohort can be org/user/device/
connector/capability but scope derives from trusted config.

### D7. Retirement list

Delete or permanently disable:

- `CallMcpTool` post-dispatch classification and universal surface emission;
- automatic mapping-output→surface;
- direct MCP write dispatch from model-facing middleware;
- connector-shaped-only stage coordinator path;
- direct AI-backend desktop broker mutation methods/routes;
- generic filesystem graph interrupt as product write approval;
- writable legacy `/drafts/` store;
- bespoke built-in/subagent surface/effect emitters;
- sandbox direct/live host patch path;
- browser generic side-effect click path;
- old coarse flag behavior that restores any retired write path;
- temporary descriptor exemptions.

Read-only compatibility models/projectors may remain in a named `legacy_v2` package
with deletion date/owner.

### D8. Asymmetric rollback

Safe rollback actions:

- stop accepting a capability;
- return/stage held;
- disable rendering and show raw/card;
- pause commit workers;
- leave approved work pending;
- revert read-only projector.

Forbidden rollback:

- re-enable direct MCP/workspace/browser/sandbox write;
- bypass exact digests/claim;
- resume old filesystem interrupt into host mutation;
- make legacy draft store writable;
- discard indeterminate claims.

Document data/read compatibility for rolling deploys and minimum mixed-version window.

### D9. Final conformance gate

CI/release gate:

1. every model-facing operation has descriptor;
2. every effect-capable descriptor maps to stager and registered executor;
3. no direct effect client upstream;
4. one canonical effect result producer;
5. every model call metered;
6. every ref scheme has auth/retention/deletion owner;
7. all old fixtures/exports replay/verify;
8. no temporary exemption/unsafe flag combination;
9. workspace write enabled only on attested platforms;
10. UI subject/renderers use fixed/constrained schemas;
11. no service-boundary violation;
12. dark-capability scanner reports none.

Plant canaries for each class.

### D10. End-to-end launch matrix

Mandatory:

- chat-only math;
- chat code example without artifact;
- explicit code artifact/edit/download;
- CSV artifact preserving fidelity;
- CSV save to desktop workspace;
- missing/revoked grant;
- local file drift after approval;
- create/replace/delete/move/mkdir;
- MCP read no surface;
- MCP record/table surface;
- MCP write exact args;
- unknown MCP tool held;
- auth expiry;
- built-in pure compute;
- subagent artifact/effect/usage;
- sandbox compute/artifact/patch;
- browser read/download/upload/submit/drift;
- duplicate delivery;
- crash before/after every claim/effect/journal boundary;
- web artifact/download fallback;
- replay/reconnect and previous-run surface selection;
- receipt/Sources/pending/usage/audit export;
- tenant isolation/deletion/legal hold.

Run hermetic CI where possible and supervised/live smoke for platform/provider behavior.

### D11. Performance and capacity gates

Verify SDR budgets:

- time to first narrative/activity;
- artifact stream memory;
- canvas projection/replay;
- large CSV virtualization;
- stage/receipt/pending query plans;
- workspace streaming/commit;
- event/ref retention scans;
- no per-event/per-row unbounded N+1.

Define pass/fail numbers in the runbook from current product SLOs; no “looks fast”
acceptance.

### D12. Documentation and operations

Update:

- architecture/SDR decisions;
- service-boundary docs if interfaces changed;
- dev testing recipes;
- desktop capability/security/smoke docs;
- self-host limitations;
- feature flags/rollback;
- retention/deletion/legal hold;
- support runbooks for drift/indeterminate/recovery;
- audit/usage API docs.

## Implementation plan

1. Build migration inventory/dry-run reports.
2. Add compatibility replay/export corpus.
3. Implement draft migration.
4. Implement pending-stage/approval migration.
5. Add independent config/startup validation.
6. Run shadow comparisons and fix mismatches.
7. Enable cohorts in D6 order.
8. Drain/reconcile legacy pending work.
9. Delete bypass paths/exemptions.
10. Flip defaults and run final conformance/live matrix.
11. Publish runbooks and migration report.

## Test plan

### Migration

- restart/checkpoint/idempotency/dry-run;
- corrupt/missing/duplicate legacy rows;
- exact draft version hashes;
- every pending state category;
- approved legacy work always reapproved or reconciled, never silently applied;
- rolling version compatibility.

### Flags/rollback

- all valid/invalid combinations;
- rollback pauses safely;
- no rollback enables legacy write;
- cohort scoping trusted;
- mixed workers never double-dispatch.

### Replay/export

- all old fixtures and signed bundles;
- event prefix/projector parity;
- old/new API clients in compatibility window.

### Conformance/live

- canary for every D9 rule;
- full D10 matrix;
- supported desktop OS/filesystem matrix;
- provider connector/sandbox/browser smoke;
- design parity/accessibility;
- performance budgets.

## Definition of done

- [ ] Drafts/artifacts and pending legacy work have verified migration reports.
- [ ] Old events and signed exports remain readable/verifiable.
- [ ] Independent flags default to enforce only after cohort soak.
- [ ] Every direct/bespoke effect path and temporary exemption is removed.
- [ ] Safe rollback cannot restore an unsafe write.
- [ ] Final conformance gate and full launch matrix pass.
- [ ] Supported desktop platforms meet C2 attestation; others keep writes disabled.
- [ ] Documentation/runbooks are complete.
- [ ] UI, effect-path, and standard DoD pass.

## Out of scope

- Migrating by rewriting immutable ledger history.
- Enabling unsupported platform/filesystem/provider capability.
- Hiding quarantined/corrupt records to achieve a green report.

## Guardrails

- Never transfer a legacy approval without exact canonical proof and fresh approval.
- Never run legacy and canonical writable paths together.
- Never make rollback synonymous with bypass.
- Never delete legacy data before reference/verification gates.
- Never flip default-on before live crash/security/platform smoke.
