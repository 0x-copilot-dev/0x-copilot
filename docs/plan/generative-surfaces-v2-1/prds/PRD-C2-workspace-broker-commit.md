# PRD-C2 — Electron workspace authority and commit protocol

**Goal.** Turn Electron main/native into the sole LocalWorkspaceAuthority for host
files. Add scoped reads, one-use commit authorization, prepare/upload/commit/reconcile,
handle-relative atomic mutations, preimages, durable journaling, immediate revocation,
and crash-safe recovery. Writable capability stays disabled on platforms where the
required isolation/native primitives are unavailable.

## Implementer brief

Read:

1. `../01-sdr.md` §§4.5, 10, 14–15.
2. `PRD-A1-artifact-effect-contracts.md` and
   `PRD-A5-commit-coordinator.md`.
3. `apps/desktop/main/capabilities/broker.ts`.
4. `apps/desktop/main/capabilities/host-fs.ts`.
5. `apps/desktop/main/capabilities/path-validation.ts`.
6. `apps/desktop/main/capabilities/grant-store.ts`.
7. `apps/desktop/main/capabilities/run-context.ts`.
8. `apps/desktop/native/workspace-fs/`.
9. `apps/desktop/main/services/python-service.ts` and `service-env.ts`.
10. `services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py`.
11. `docs/plan/desktop/agent-capabilities/05-ac5-filesystem-capability.md`.

This is a security boundary PR. No writable fallback to path-string Node APIs is
allowed.

## Context

The current broker has good virtual-path validation and atomic replace mechanics, but
it uses one process-wide bearer, permits mutation without approval proof, closes
validated parent handles before path-string mutation, lacks CAS hashes/stable identity,
and has no complete recovery journal. The supervised Python child also retains ambient
same-user filesystem authority, so the broker cannot yet be claimed as a security
boundary.

## Interfaces consumed

- A1 effect execution request/digest/idempotency contracts.
- A5 executor prepare/apply/reconcile semantics.
- Existing folder picker, grant UI, virtual paths, broker transport, native
  `openBeneath`, and atomic-replace implementation.

## Interfaces exposed

Electron-main internal APIs:

```text
LocalWorkspaceAuthority
  createReadCapability
  stat/read/list/glob/grep
  prepareChangeSet
  uploadPreparedContent
  authorizeCommitFromUserDecision
  commitPreparedChangeSet
  reconcileCommit
  abortPreparedChangeSet
  proposeRecovery
```

AI-backend typed client:

```python
class WorkspaceAuthorityPort(Protocol):
    async def prepare(request) -> WorkspacePreparedEffect
    async def upload(prepared_ref, content_ref) -> None
    async def commit(prepared_ref, commit_permit) -> WorkspaceCommitResult
    async def reconcile(claim_id) -> WorkspaceCommitResult
    async def abort(prepared_ref) -> None
```

No route is facade/public. Communication is a private local authenticated channel.

## Design

### D1. Trust model and launch gate

Trusted for local-file authority:

- Electron main/native;
- native folder picker;
- local commit journal;
- the explicit desktop host adapter that conveys user approval.

Untrusted for host paths/mutation:

- model/tool content;
- renderer content;
- sandbox;
- remote services;
- generic AI-backend capability code.

The supervised Python services must run under an OS confinement profile that denies
ambient access outside product runtime/data directories. The exact implementation may
be platform-specific, but startup must attest:

```text
workspace_write_isolation = enforced | unavailable
native_workspace_primitives = available | unavailable
```

Writable grants are enabled only when both are `enforced/available`. Otherwise reads may
remain available under their reviewed threat model, but writes fail closed with
`workspace_write_unsupported`.

Development may have an explicit `UNSAFE_DEV_WORKSPACE_TCB=true`, visibly labeled and
impossible in production builds. It does not count as launch evidence.

### D2. Capability split

Replace the process-wide mutation bearer with:

1. boot transport authentication, which grants no filesystem authority;
2. short-lived run-scoped read capability:
   - exact run/user/device;
   - allowed grant ids/modes/path subsets;
   - expiry and quotas;
3. one-use commit permit:
   - stage id/revision;
   - decision ledger id;
   - change-set/target/proposal digests;
   - run/user/device;
   - prepared ref;
   - expiry/nonce;
   - allowed operation count/bytes.

The authority consumes a permit atomically. Replay with the same commit id returns the
recorded result; changed digests conflict.

The AI-backend cannot mint commit permits. On desktop, the shared approval UI calls a
host port; Electron main records/verifies the exact server decision response and then
mints the local permit. Destructive operations require a main-owned/native confirmation
sheet unless the security review explicitly accepts renderer user intent.

### D3. Grant hardening

Persist:

- grant id, local profile/account id, installation/device id;
- canonical root held only in main;
- root volume id and directory file id/inode;
- mode, allowed path subset, quotas;
- created/updated/expiry;
- live revocation state.

On every operation, intersect:

```text
run snapshot ∩ live non-revoked grant ∩ product policy ∩ commit permit
```

Revocation narrows immediately, including existing runs. Root identity mismatch
invalidates the grant rather than following a replaced directory.

Grant persistence uses temp+fsync+atomic replace+directory fsync and OS-protected
permissions. Physical paths never enter renderer/facade/ledger/audit export.

### D4. Native handle-relative operations

Expand the native module to make writable mode mandatory on:

- handle-relative secure open/stat/read/list;
- create-no-replace;
- same-directory atomic replace/exchange;
- handle-relative unlink/rmdir/mkdir;
- handle-relative rename-no-replace;
- stable identity/volume query;
- fsync file/directory;
- reparse/symlink/junction refusal.

Unix uses `openat2/openat`, `renameat2`, `unlinkat`, `mkdirat` equivalents. Windows uses
root-relative handles and refuses reparse traversal. Retain root/parent handles through
the complete mutation. Never reconstruct an absolute path after authorization.

Native load failure, unsupported kernel/filesystem, or network filesystem with
unproven semantics means writes are unavailable. Do not silently fall back.

### D5. Prepare

Input is a C1 `WorkspaceChangeSet` by refs/digests, not host paths. Prepare:

1. verifies read capability/grant/device/run;
2. resolves virtual path under retained root handle;
3. rejects symlinks, special files, mount crossings, ambiguous case/Unicode;
4. reads stable identity and SHA-256 baseline;
5. compares every precondition;
6. captures encrypted preimage/trash metadata where needed;
7. allocates private same-volume temp objects;
8. writes durable journal state `prepared`;
9. returns opaque `prepared_ref`, observed target digest, upload slots, expiry.

Prepare performs no user-visible mutation.

### D6. Content upload

- Private local stream or chunked IPC; no base64 JSON.
- Incremental SHA-256 and byte quotas.
- Content is written only into private staging files.
- Expected digest/size must match.
- Staged files are not executable and have restrictive permissions.
- Interrupted/mismatched upload is aborted and collectible.

### D7. Commit semantics by operation

| Operation     | Preconditions                                                            | Mutation                                                |
| ------------- | ------------------------------------------------------------------------ | ------------------------------------------------------- |
| create        | target still absent                                                      | fsynced temp, atomic no-replace rename                  |
| replace/edit  | stable id and baseline hash match                                        | fsynced temp, atomic replace/exchange; record post-hash |
| delete        | type/id/hash match                                                       | move to private same-volume trash by default            |
| move          | source identity/hash match; destination absent unless explicit overwrite | handle-relative rename; overwrite snapshots destination |
| mkdir         | parent identity; target absent or explicit idempotent-dir                | handle-relative mkdir                                   |
| explicit tree | every manifest entry matches                                             | bounded journaled per-entry transaction/result          |

Metadata policy explicitly covers mode, executable bit, ownership, ACL, xattrs, flags,
and timestamps. Unsupported preservation requirements block before mutation.

Cross-volume move is out of initial launch unless implemented as journaled
copy+verify+commit+trash with separate limits.

### D8. Journal and result

Encrypted, integrity-protected journal states:

```text
prepared → authorized → committing → applied
                              ↘ failed_before_effect
                              ↘ indeterminate
applied → recovery_proposed → rolled_back | recovery_conflict
```

Record safe fields:

- device/run/stage/revision/decision/claim ids;
- keyed path token, never plaintext path in export;
- operation, baseline/result hashes, bytes;
- preimage/trash refs;
- timestamps and outcome.

Detailed local display metadata may be stored separately under OS-protected local
storage.

### D9. Reconcile and recovery

At startup and on worker request, reconcile every nonterminal journal:

- verify current identity/hash and temp/trash state;
- determine applied/not-applied/indeterminate;
- never replay blindly;
- return stable result for the same claim.

Recovery is CAS-protected and staged:

- undo create only if current hash equals committed post-hash;
- restore replace only if current hash equals post-hash;
- restore delete only if destination is free;
- reverse move only if identities match;
- conflict creates a recovery proposal; never force.

### D10. Read operations

Read capability methods also retain secure handles and enforce:

- live grant intersection;
- size/range/result limits;
- regular file/dir only;
- digest/stable identity in stat;
- bounded, streamed content;
- handle-relative list/walk;
- no absolute path response.

### D11. Broker routes

Replace direct mutation routes with versioned private routes or IPC:

```text
POST /internal/workspace/v2/prepare
PUT  /internal/workspace/v2/prepared/{id}/content/{slot}
POST /internal/workspace/v2/prepared/{id}/commit
POST /internal/workspace/v2/claims/{id}/reconcile
POST /internal/workspace/v2/prepared/{id}/abort
```

Old `write/edit/delete/move/mkdir` routes remain disabled in production immediately
when v2 is available; E2 deletes them. Every mutation request without a valid one-use
permit is rejected regardless of boot bearer.

## Implementation plan

1. Add threat-model/startup attestation and writable launch gate.
2. Harden grant schema/store/revocation/root identity.
3. Expand native handle-relative primitives and platform contract tests.
4. Split transport/read/commit capabilities.
5. Implement prepare and encrypted journal.
6. Implement streaming upload.
7. Implement operation commits and metadata policy.
8. Implement one-use permit path through desktop host approval adapter.
9. Implement reconcile/recovery.
10. Replace Python broker client mutation API with the typed authority port.
11. Add fault-injection and platform smoke suites.

## Test plan

### Security/adversarial

- boot bearer alone cannot read or mutate;
- read capability cannot mutate;
- AI-backend request without main-issued permit cannot commit;
- revoked/expired/wrong-user/wrong-device/wrong-run capability fails;
- root directory substitution invalidates grant;
- symlink/junction/reparse/hard-link policy and mount crossings are enforced;
- ancestor swap race cannot escape retained handles;
- native module absent means zero writable capability;
- OS-confinement probe proves Python child cannot directly access an ungranted file.

### Preconditions/atomicity

- external edit between prepare and commit gives drift/zero mutation;
- create race never overwrites;
- destination race blocks move;
- full bytes written/fsynced before rename;
- injected crash at every journal boundary reconciles correctly;
- duplicate commit produces one mutation and same result.

### Recovery

- CAS-safe undo for create/replace/delete/move;
- post-commit external edit causes recovery conflict, not overwrite;
- corrupted journal/preimage fails closed and is auditable;
- retention deletes preimages only when permitted.

### Scale/platform

- streamed large file stays within memory budget;
- quotas/open-handle limits;
- macOS, Windows, Linux contract suite on supported runners;
- unsupported/network filesystem visibly fails closed.

## Definition of done

- [ ] Electron main/native is the only process able to mutate granted files.
- [ ] Production writable mode requires OS confinement and native primitives.
- [ ] Every mutation requires a one-use exact commit permit.
- [ ] Mutations are handle-relative and CAS-bound.
- [ ] Journal/reconcile survives every injected crash boundary.
- [ ] Grants are identity-bound, expiring, and immediately revocable.
- [ ] Physical paths/tokens never reach facade/ledger/exportable audit.
- [ ] Direct mutation routes are disabled.
- [ ] Effect-path and standard DoD pass.

## Out of scope

- Shared workspace UI.
- Web local filesystem.
- Cross-volume move at initial launch.
- Recursive implicit deletes.
- Claiming an unconfined dev process as a security boundary.

## Guardrails

- No writable fallback when native primitives are unavailable.
- No path-string mutation after authorization.
- No process-wide mutation bearer.
- No trust in `approved=true` from AI-backend.
- No unconditional recovery overwrite.
