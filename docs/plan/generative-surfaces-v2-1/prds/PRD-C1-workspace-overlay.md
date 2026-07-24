# PRD-C1 — Durable workspace overlay

**Goal.** Replace `/workspace/` write-through with a durable, versioned virtual overlay.
The agent gets read-your-writes semantics, while the host filesystem remains unchanged
until an exact EffectStage is approved and committed by the workspace executor.

## Implementer brief

Read:

1. `../00-overview.md` principles P5–P10 and FR-D1–D8.
2. `../01-sdr.md` §§9, 13 S4–S5, 14–15.
3. `PRD-A2-artifact-repository.md` and `PRD-A4-effect-stager.md`.
4. `services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_backend.py`.
5. `services/ai-backend/src/runtime_worker/workspace_backend_wiring.py`.
6. `services/ai-backend/src/agent_runtime/execution/factory.py`,
   `_composed_deep_backend` and `_workspace_write_permissions`.
7. `services/ai-backend/src/agent_runtime/capabilities/sandbox/workspace_transfer.py`.
8. `services/ai-backend/src/runtime_adapters/file/object_store.py`.

This PR must prove zero calls to desktop broker mutation routes. Reads may use a scoped
base-read port; overlay mutations may not.

## Context

The current `BrokeredWorkspaceBackend.awrite/aedit` captures a snapshot and then calls
the host broker directly after a generic graph interrupt. That gives neither durable
product approval cards nor exact compare-and-swap binding. It also means later agent
reads depend on an already-mutated host file.

The new model is:

```text
base snapshot/read capability + immutable overlay entries = merged virtual workspace
```

Every agent mutation changes only the overlay and creates/revises a stage. Approval
does not occur inside the filesystem backend.

## Interfaces consumed

- A2 artifact/blob repository for overlay bytes and preimage refs.
- A4 EffectStager and workspace executor kind.
- Existing broker read operations and run-scoped grant context.
- Existing Deep Agents backend protocol.
- Existing virtual-path validation rules.

## Interfaces exposed

Create:

```text
agent_runtime/capabilities/workspace/
  contracts.py
  ports.py
  overlay.py
  merged_backend.py
  changeset.py
  errors.py

runtime_adapters/{in_memory,file,postgres}/workspace_overlay_store.py
```

Stable interfaces:

```python
class WorkspaceBaseReadPort(Protocol):
    async def stat(path) -> BaseEntry
    async def read(path, *, start=None, end=None) -> AsyncIterator[bytes]
    async def list(path) -> Sequence[BaseEntry]
    async def glob(pattern) -> Sequence[BaseEntry]
    async def grep(query, paths) -> Sequence[BaseMatch]

class WorkspaceOverlayStorePort(Protocol):
    async def get_manifest(run_id) -> OverlayManifest
    async def append_revision(expected_version, mutations) -> OverlayManifest
    async def compact(run_id) -> OverlayManifest

class WorkspaceOverlayService:
    async def propose_create(...)
    async def propose_replace(...)
    async def propose_delete(...)
    async def propose_move(...)
    async def propose_mkdir(...)
```

## Design

### D1. Overlay entry model

Each normalized virtual path has at most one current overlay entry:

```text
OverlayEntry
  virtual_path
  entry_kind: file | directory | tombstone | move
  operation: create | replace | delete | move | mkdir
  content_ref?, content_digest?, byte_size?
  source_virtual_path?
  baseline: BasePrecondition
  stage_id, stage_revision
  overlay_revision
  author, created_at
```

`BasePrecondition`:

```text
existence: must_exist | must_not_exist | any
entry_type?
opaque_generation?
content_digest?
stable_file_id?
size?
mtime_ns?
```

SHA-256 plus stable identity, when available, is authoritative. Size/mtime are
supplemental and never replace a digest for content overwrite.

### D2. Virtual path rules

Reuse one canonical normalizer equivalent to desktop
`normalizeVirtualPath`. Paths:

- begin under `/workspace/<mount>/`;
- contain no traversal, NUL, absolute-host syntax, drive/UNC syntax, confusable dot
  segment, reserved device name, or excessive length/depth;
- are normalized once and stored canonically;
- never expose a host path.

Case and Unicode collisions are resolved using filesystem capability metadata returned
by the base-read port. If the host cannot provide a reliable rule, reject ambiguous
mutations.

### D3. Merged reads

Resolution order:

1. exact overlay tombstone → not found;
2. exact overlay file/directory → overlay;
3. move destination → referenced overlay/base content;
4. path hidden by moved/deleted ancestor → not found;
5. otherwise base-read port.

Directory listing merges children by canonical comparison key. Overlay wins. Results
are deterministic and sorted. Glob/grep include overlay contents and exclude tombstoned
base entries.

After an overlay write, every subsequent model read sees the proposed content even
though the host is unchanged.

### D4. Mutation semantics

`write_file`:

- missing target → create with `must_not_exist`;
- existing target → replace with captured baseline;
- content is stored in A2 blob storage;
- append overlay revision;
- stage/revise a workspace effect;
- return a structured “staged in workspace overlay” result.

`edit_file`:

- resolve current merged bytes;
- apply deterministic edit against overlay/base;
- store full result bytes and optional diff metadata;
- revise same path stage.

`delete`:

- creates tombstone;
- captures exact type/identity/digest;
- destructive classification;
- never recursively infers child deletion.

`move`:

- records source and destination preconditions;
- overwrite destination is a distinct destructive operation;
- cross-mount move is rejected in C1.

`mkdir`:

- one explicit directory only;
- recursive parent creation must be an explicit bounded changeset.

### D5. Stage coalescing

One unresolved logical target has one active workspace stage:

- repeated edits to the same path create stage revisions;
- approval of an older revision becomes stale;
- create then edit remains create with newest bytes;
- create then delete before apply cancels/supersedes the stage and removes the overlay
  entry if no other dependency;
- replace then delete becomes delete with original baseline;
- move then edit destination produces one ordered changeset;
- dependent multi-path changes use one `WorkspaceChangeSet` stage where atomic grouping
  is required.

Coalescing is a pure function with exhaustive table tests.

### D6. Change-set contract

```text
WorkspaceChangeSet
  change_set_id
  mount_id
  entries[]:
    operation
    virtual_path_token
    source_path_token?
    baseline
    result_ref?
    result_digest?
    metadata_policy
  manifest_digest
```

The ledger/stage may contain redacted display paths for the authorized user, but
exportable audit uses a keyed path token. Absolute paths never leave Electron main.

Proposal digest covers ordered entries and every baseline/result digest. Entry order is
canonical except when dependencies require explicit ordering.

### D7. Persistence and adapter parity

Overlay metadata is durable for run lifetime and restart:

- in-memory adapter;
- atomic file adapter;
- postgres adapter.

Bytes are A2 content refs, not embedded in overlay rows. Use optimistic manifest
versioning. Concurrent model tools either serialize or receive a conflict and retry
through the runtime; they never drop an entry.

Migration uses next free number and updates all schema/manifest mirrors.

### D8. Deep Agents backend integration

Implement `MergedWorkspaceBackend` for the existing backend protocol. It exposes
read/list/glob/grep and mutation methods, but mutation methods call OverlayService only.

Remove direct mutation clients from its constructor. An architecture test asserts the
backend object graph contains no method/attribute named `write`, `edit`, `delete`,
`move`, `mkdir`, `commit`, or generic broker client capable of host mutation.

During compatibility:

- new backend is enabled by `WORKSPACE_OVERLAY_MODE=off|shadow|enforce`;
- shadow may compare read results but must not stage duplicate writes;
- enforce replaces `BrokeredWorkspaceBackend` mutation methods.

### D9. Missing authority and resume

If the original grant/base-read authority is unavailable:

- `/workspace/` remains mounted as a fail-closed tombstone backend;
- overlay content remains readable where self-contained;
- base-dependent reads return `workspace_authority_unavailable`;
- mutations requiring unknown baseline are held/blocked;
- the composite backend must never fall through to `StateBackend` for
  `/workspace/**`.

Resume tests must explicitly revoke/remove a grant and prove no ephemeral write occurs.

### D10. Limits

Initial configurable limits:

- 1,000 overlay entries/run;
- 250 MiB total referenced result bytes/run;
- 100 MiB/file;
- 10,000 explicit entries in one directory-manifest proposal only with elevated
  product policy;
- no symlinks, sockets, devices, FIFOs, or sparse-file amplification;
- bounded glob/grep/list results with continuation.

## Implementation plan

1. Add contracts, path normalization parity vectors, and pure merge/coalesce tests.
2. Add OverlayStore port and three adapters.
3. Add OverlayService with A2/A4 fakes.
4. Add `MergedWorkspaceBackend`.
5. Wire base-read-only broker adapter.
6. Add settings/mode and run-context construction.
7. Add fail-closed tombstone mount.
8. Add stage/change-set projection APIs needed by C3.
9. Add restart/concurrency/limit tests.
10. Add no-host-mutation architecture and adversarial tests.

## Test plan

### Read-your-writes

- base read, create, replace, edit, delete, move, mkdir;
- merged list/glob/grep;
- create→edit→delete coalescing;
- restart and replay produce identical manifest;
- two concurrent mutations never lose data.

### No host mutation

- exploding fake for every broker mutation method records zero calls;
- full Deep Agents `write_file/edit_file` flows only change overlay/stage;
- approving a stage in C1 alone still performs zero host calls;
- static/object-graph gate proves no mutation client reachable.

### Preconditions and failure

- baseline hash/identity captured;
- missing base authority fails closed;
- oversized/special/ambiguous paths rejected;
- cancelled upload leaves no overlay revision;
- stale manifest version conflicts.

### Adapter parity

- shared suite over memory/file/postgres;
- file crash points preserve previous complete manifest;
- postgres concurrent append is serialized.

## Definition of done

- [ ] `/workspace/` mutations change only durable overlay state.
- [ ] Agent reads observe overlay changes.
- [ ] Each mutation creates/revises an exact A4 stage.
- [ ] Coalescing and stale approval behavior are test-pinned.
- [ ] Missing grant never falls through to ephemeral state.
- [ ] No host mutation method is reachable.
- [ ] All three adapters pass parity/restart tests.
- [ ] Effect-path and standard DoD pass.

## Out of scope

- Electron commit protocol.
- Approval UI.
- Host filesystem mutation.
- Recursive tree operations beyond explicit manifests.

## Guardrails

- Never mutate the host from the merged backend.
- Never use mtime/size alone as overwrite precondition.
- Never embed file bytes in the ledger/overlay row.
- Never infer recursive deletion.
- Never allow `/workspace/` to fall through to another backend on resume.
