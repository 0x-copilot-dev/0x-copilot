# PRD-C3 — Workspace product integration 🎨

**Goal.** Connect the durable overlay, Effect Coordinator, and Electron
LocalWorkspaceAuthority into one end-to-end product flow. Reads auto-run within active
grants; creates/modifies stage according to policy; destructive actions always wait;
approval commits exactly reviewed bytes; web falls back to artifact download. Retire
the generic filesystem interrupt/direct-write path.

## Implementer brief

Read:

1. `../01-sdr.md` §§9–11 and sequences S4, S5, S7, S8.
2. `PRD-C1-workspace-overlay.md` and
   `PRD-C2-workspace-broker-commit.md`.
3. `services/ai-backend/src/agent_runtime/execution/factory.py`.
4. `services/ai-backend/src/runtime_worker/stream_events.py`,
   `native_tool_approval_payloads`.
5. `services/ai-backend/src/runtime_worker/handlers/approval.py`.
6. `packages/chat-surface/src/destinations/run/RunDestination.tsx`.
7. Existing staged draft/table, gate, pending, receipt, and Sources components.
8. `apps/desktop/renderer/destinationBinders.tsx` and preload/main capability bridge.
9. `apps/frontend/src/ports/FilePickerWeb.ts` and `download.ts`.

This PR is complete only with a supervised desktop live smoke. Unit tests alone cannot
prove host authority wiring.

## Context

C1 makes local changes safe but unreal. C2 can mutate host files safely but is not
reachable from the product. C3 joins them without reintroducing direct write-through.
The user’s concrete case must work:

> “Create a CSV and save it as `/workspace/Finance/report.csv`.”

The CSV first exists as an artifact/overlay revision. The user reviews a dataset/file
stage. Only then can Electron main atomically create the host file.

## Interfaces consumed

- A3 Operation Gateway and generalized gates.
- A4 EffectStage and A5 Coordinator/executor registry.
- B2 artifact/dataset renderers and editor.
- B3 canvas lifecycle.
- C1 overlay/change-set/backend.
- C2 authority/permit/prepare/apply/reconcile.

## Interfaces exposed

- `WorkspaceEffectExecutor` registered as executor kind `workspace`.
- `WorkspaceGrantGate` using `gate.opened.v2/gate.resolved.v2`.
- shared workspace stage/projector/UI.
- desktop `WorkspaceApprovalHostPort`.
- web `WorkspaceSaveFallbackPort`.

## Design

### D1. Operation descriptors and policy

Register:

| Operation                      | Effect class         | Default                                 |
| ------------------------------ | -------------------- | --------------------------------------- |
| stat/list/glob/grep/read       | none                 | auto within active read grant           |
| create file/mkdir              | external_reversible  | stage; may auto only by explicit policy |
| replace/edit                   | external_reversible  | stage; may auto only by explicit policy |
| delete/trash                   | external_destructive | explicit approval                       |
| move without overwrite         | external_reversible  | stage                                   |
| move with overwrite            | external_destructive | explicit approval                       |
| recursive tree/permanent purge | external_destructive | explicit approval                       |

Most restrictive wins across deployment/org/user policy, grant mode, sensitive path,
agent hold, and descriptor. CAS/grant checks are never bypassed.

### D2. Replace generic filesystem interrupts

Remove `FilesystemPermission(mode="interrupt")` as the product approval mechanism for
`/workspace/**`. `MergedWorkspaceBackend` mutation calls return a structured staged
result after EffectStager persists the proposal.

Do not resume/replay the original `write_file` tool to perform the effect. The worker
uses `WorkspaceEffectExecutor`.

During rollout, `WORKSPACE_EFFECT_MODE=off|shadow|enforce`; enforce is allowed only when
C2 launch attestation is green. In shadow, host mutation stays on the old path only in
non-production test/dev and is used solely for comparison; production must not have two
writable paths.

### D3. Grant gates

Before base read or stage preparation:

- no grant → park with `gate_kind=grant`;
- expired/revoked grant → park/fail safely;
- read-only grant on mutation → capability gate;
- grant disappears after stage → stage remains held; commit does not fall through.

Gate card:

- shows mount label and requested mode/path scope, never physical path;
- Connect opens Electron-native folder picker;
- Cancel/skip resumes with no host effect;
- a newly granted folder gets a new grant id and root identity.

Auth/connect consent is not approval of staged bytes.

### D4. Workspace stage surface

One shared surface supports:

- create/replace/delete/move/mkdir badges;
- virtual path and mount label;
- artifact/data preview where safe;
- text/CSV diff or binary metadata/hash diff;
- baseline/precondition summary;
- revision history and author;
- exact approval pledge:
  “Only this revision and target will be applied.”
- Approve, Reject, Restore, Edit;
- conflict/reconcile/recovery state.

Destructive actions use stronger treatment and native confirmation on desktop.
Untrusted path/title text is never interpreted as markup.

### D5. Artifact-to-workspace flow

“Save” on an artifact:

1. user/model chooses virtual workspace target, never host absolute path;
2. create an OperationRequest with artifact revision as proposal ref;
3. C1 writes overlay and creates/revises stage;
4. stage surface previews artifact/dataset;
5. approval goes through desktop host port;
6. A5 worker resolves `WorkspaceEffectExecutor`;
7. executor asks C2 prepare/upload/commit;
8. effect/applied events update stage, receipt, Sources.

Editing the artifact or overlay after approval creates a new stage revision and
invalidates the old decision.

### D6. Desktop approval host port

For workspace decisions only:

- shared UI calls `WorkspaceApprovalHostPort.decide(stage snapshot, decision)`;
- desktop preload/main sends the decision to facade, verifies returned stage revision
  and decision ledger id/digests, and mints/stores the C2 one-use permit;
- web uses ordinary facade decision only because it cannot commit local workspace;
- destructive approval invokes main-owned confirmation before recording/minting.

Main returns no physical path or permit token to renderer. The worker receives only an
opaque prepared/permit ref through its private channel.

### D7. Workspace executor

`WorkspaceEffectExecutor`:

- `prepare`: resolve stage change-set and call C2 prepare;
- upload: stream each A2 content ref;
- `apply`: provide opaque permit and call C2 commit;
- `reconcile`: call C2 journal reconciliation;
- `abort`: remove uncommitted prepared state.

It has no policy logic. It verifies returned digests against stage before reporting
success. Only the A5 coordinator invokes it.

### D8. Web behavior

Web cannot write arbitrary local paths through server APIs.

- artifact Save offers exact browser download;
- optional File System Access API is a future/client-local adapter and requires browser
  user activation; no handles go to server;
- `/workspace/` is absent or a clear unavailable/tombstone mount;
- instructions such as “save to my Desktop” produce an artifact plus a truthful
  “Download” action, not a false success.

Self-host server filesystem is a separate administrator capability, not “local
workspace.”

### D9. Receipt, Sources, pending work

Project generalized events:

- reads count as reads;
- staged workspace entries count as writes proposed;
- approvals/applies/holds reflect exact rows/entries;
- Sources shows artifact revision, grant label, capability, and keyed/redacted target;
- pending queue aggregates unresolved workspace stages/gates;
- receipt exports no physical path.

### D10. Failure and recovery UX

| Failure                      | Product behavior                                   |
| ---------------------------- | -------------------------------------------------- |
| grant revoked before approve | held; reconnect grant                              |
| grant revoked after approve  | commit blocked; decision remains auditable         |
| baseline changed             | conflict surface; zero mutation; regenerate/rebase |
| crash before host commit     | replay/retry safely                                |
| crash after possible commit  | indeterminate; automatic reconcile; no blind retry |
| platform unsupported         | artifact/download fallback                         |
| content upload mismatch      | failed before effect                               |
| recovery conflict            | new recovery proposal                              |

### D11. Retirement

After enforce smoke:

- remove broker mutation methods from `BrokeredWorkspaceBackend`;
- remove filesystem-native graph approval projection assumptions;
- delete direct write/edit/delete/move/mkdir calls from AI-backend;
- retain read-only broker client and new authority port;
- ensure `/workspace/` can never fall through on resume.

C2 may retain disabled compatibility route code until E2 if rollback requires it, but
no production flag combination may make both paths writable.

## Implementation plan

1. Register descriptors/gates/workspace executor.
2. Wire MergedWorkspaceBackend into run and resume.
3. Add workspace stage projection and shared components.
4. Add desktop approval host port/preload/main integration.
5. Add artifact Save-to-workspace and web download fallback.
6. Extend pending/receipt/Sources/usage/audit projections.
7. Add reconcile/conflict/recovery UI.
8. Enable enforce only after startup attestation.
9. Remove direct runtime write-through path.
10. Run supervised desktop and web smoke matrix.

## Test plan

### End-to-end hermetic

- create CSV → artifact → overlay → stage → approve → exactly one host create;
- replace file with external edit before commit → conflict/zero overwrite;
- edit stage after approval → old permit unusable;
- delete/move destructive requires native confirmation;
- duplicate worker delivery → one host mutation;
- crash after commit before event → reconcile to applied.

### Grant/resume

- missing grant parks;
- connect resumes same operation;
- revoke narrows immediately;
- resume without authority mounts tombstone, never StateBackend;
- read-only grant cannot mint write permit.

### Host behavior

- desktop full flow;
- web exact download and honest no-local-write response;
- both use shared stage/canvas UI;
- Focus compact cards; Studio full stage.

### No-bypass

- architecture gate finds only A5→WorkspaceExecutor→C2 mutation route;
- direct broker mutation route calls fail;
- generic graph approval cannot execute host write;
- physical path absent from all public events/network snapshots.

## Definition of done

- [ ] Local CSV create/save flow works exactly once on supervised desktop.
- [ ] Host remains unchanged until exact approval.
- [ ] Drift/revocation/crash behavior is safe and visible.
- [ ] Web produces artifact/download, never false local-save success.
- [ ] Direct workspace write-through is retired.
- [ ] Receipts/Sources/pending work include workspace facts without physical paths.
- [ ] Design parity and live smoke pass.
- [ ] UI, effect-path, and standard DoD pass.

## Out of scope

- Browser File System Access API implementation.
- Cross-volume moves.
- Silent recursive operations.
- Treating cloud/self-host filesystem as desktop-local.

## Guardrails

- Approval never replays the model filesystem tool.
- Renderer never receives permit, root handle, or physical path.
- “Allow always” never bypasses CAS, revocation, destructive policy, or agent hold.
- Web never sends a client-local path to the server.
- No production mode permits both legacy and new workspace writes.
