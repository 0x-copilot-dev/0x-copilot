# AC5 — Scoped host filesystem capability

| Field             | Decision                                                                                                                                                                                                                         |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Spec ID           | AC5                                                                                                                                                                                                                              |
| Status            | Draft; decision-complete and awaiting architecture review                                                                                                                                                                        |
| Wave              | 2 — Product wiring                                                                                                                                                                                                               |
| Estimated effort  | XL — 20–25 engineer-days, decomposed into **three ordered slices** (see [PR slices](#pr-slices-real-pr-boundaries)): slice 1 broker+picker+grants, slice 2 read ops+path security, slice 3 mutation+approval+`/workspace/` route |
| Dependencies      | AC1 desktop capability foundation, AC4 artifact store                                                                                                                                                                            |
| Required for      | AC7 remote sandbox transfer, AC8 browser upload/download export, AC10 hardening and rollout                                                                                                                                      |
| Primary owner     | `apps/desktop` Electron capability broker                                                                                                                                                                                        |
| Supporting owners | `services/ai-backend` capabilities/backends, desktop native/platform security, runtime worker                                                                                                                                    |
| Web impact        | None                                                                                                                                                                                                                             |

## Delivered (light) vs Deferred — implementation status

This PRD is the full XL epic. What shipped is **slices 1 + 2** (broker + picker +
grants, and read-only FS ops + path security) in `apps/desktop/main/capabilities/`.
Slice 3 (mutation/approval/`/workspace/` route), the signed native Rust helper,
the per-run capability context, the event-sourced grant lifecycle, and the entire
AI-runtime side are **not** built. This section is the authoritative
reconciliation; where a later section describes that machinery, read it as design
intent, not shipped behavior.

Note the delivered code lives **flat** under `apps/desktop/main/capabilities/`,
not the proposed `.../capabilities/workspace/` + `apps/desktop/native/workspace-fs/`
tree; it addresses a grant by its `grant_id` plus a grant-root-relative virtual
path, **not** an opaque `/workspace/<mount-id>` virtual root.

### Delivered (light) — slices 1 + 2

Code in `apps/desktop/main/capabilities/`:

- **Main-owned native folder picker** (`folder-picker.ts`, `FolderPicker`): wraps
  `dialog.showOpenDialog({ properties: ['openDirectory'] })`; only main opens it.
- **`safeStorage`-encrypted grant store** (`grant-store.ts`, `GrantStore`):
  persists grants to `<userData>/capabilities/grants.bin`, encrypted with Electron
  `safeStorage` (cipher marker), **fail-closed** to no-plaintext in production
  (dev-only plaintext fallback with a warning). Three modes `read_only` /
  `read_write_no_delete` / `read_write`. Operations: `create`, `list`,
  `listActive`, `get`, `revoke`, `snapshotActive`. Status is `active | revoked`.
- **Path-free renderer IPC** (`types.ts` `toRendererGrant`, `service.ts`): the
  renderer only ever receives `RendererGrant` (`grantId`/`mode`/`label`/`status`)
  — the canonical host `root` never crosses the IPC boundary.
- **Authenticated loopback broker** (`broker.ts`, `CapabilityBroker`): a
  `127.0.0.1`-only HTTP listener on an OS-assigned ephemeral port; every request
  needs a per-boot 256-bit bearer (constant-time compared), an
  `X-Capability-Protocol` header, POST + JSON body under a 64 KiB cap, and no
  browser fetch metadata (no CORS). Routes: `handshake`, `grants/list`,
  `grants/snapshot`, and `fs/{stat,list,read,glob,grep}`. The token is handed to
  the intended child out of band; a restart mints a fresh token.
- **Read FS ops with path validation** (`host-fs.ts`, `HostFs`; `path-validation.ts`):
  `stat` / `list` / `read` / `glob` / `grep`. The load-bearing algorithm per op is
  **syntactic-normalize** (reject NUL/controls, absolute/drive/UNC, `..`,
  confusable dots, reserved device names, `:`/ADS, trailing dot/space, lone
  surrogates, over-long/over-deep) → **resolve-before-authorize** (`realpath` then
  `assertWithinRoot`) → **lstat type gate** → **atomic open + revalidate**. On
  darwin the open uses `O_NOFOLLOW_ANY` (atomic TOCTOU closure); on non-darwin it
  uses `O_NOFOLLOW` plus a conservative post-open identity + containment recheck
  (non-atomic — the documented Linux/Windows residual). Bounded by `FS_LIMITS`.
  Grant resolved from the **current** active snapshot on every op (a revoke fails
  closed on the next call); mode gated via `modeSatisfies` (fail-closed).
- **Composition + lifecycle** (`index.ts` `createCapabilityService`, `service.ts`
  `CapabilityService`): electron-free composition root; `requestFolderGrant` /
  `listGrants` / `revokeGrant` return only renderer-safe views; broker
  start/stop/token/baseUrl are main-only.
- **Tests**: `broker.test.ts`, `folder-picker.test.ts`, `grant-store.test.ts`,
  `host-fs.test.ts` (incl. darwin `O_NOFOLLOW_ANY` swap denial + non-darwin
  post-open recheck), `path-validation.test.ts`, `service.test.ts`.

### Deferred / not in the light build

Every subsection below describing this machinery is design intent, not shipped
code:

- **The opaque `virtual_root` / `mount_id` contract.** Delivered addresses a grant
  by its `grant_id` (a UUID) plus a grant-root-relative virtual path; there is no
  `/workspace/<26-char lowercase-base32 mount>` root and no
  `^/workspace/[a-z2-7]{26}$` `virtual_root`.
- **The full `CapabilityGrantV1` shape** (`version`/`capability`/`virtual_root`/
  the `active|offline|revoked|needs_reauthorization` status set) and the main-only
  `PhysicalWorkspaceGrantV1` (encrypted-root/identity fields, `workspaceId`/
  `userId`/`mountId`/`policyVersion`). Delivered `Grant`/`RendererGrant` are the
  simpler shapes above; only `active|revoked` status; only create + revoke (no
  expand/downgrade/offline/reauthorize).
- **Per-run `run_capability_context` and the immutable per-run grant snapshot.**
  Not built — no 256-bit run context bound to run/workspace/user/expiry; the
  broker re-reads the active-grant snapshot per op instead of pinning one at run
  start. (A `GrantSnapshot` type exists but is not a run-bound capability token.)
- **Event-sourced grant lifecycle + capability audit/metrics.** Grants are a
  single mutable encrypted collection rewritten on change, not an append-only
  `created`/`expanded`/`downgraded`/`reauthorized`/`offline`/`revoked` event log
  with previous-event hashes. Only a `GrantStoreAudit.warn` seam exists; the
  `workspace.*` structured events and `desktop_workspace_*` metrics are not
  emitted.
- **The signed native Rust N-API helper** (`apps/desktop/native/workspace-fs/*`).
  Not built — path enforcement uses **pure-Node** `realpath` + `O_NOFOLLOW(_ANY)`.
  On darwin this is atomic; on **Windows** the post-open recheck is non-atomic
  (the honestly-documented weakness) and there is no `NtCreateFile` root-handle
  traversal.
- **Slice 3: mutation + approval + route.** `write`/`edit`/`mkdir` and
  `delete`/`move` are absent; there is no two-phase prepare/commit mutation
  journal or reconciler, no AC4 `file_history` preimage, no approval-digest
  binding, no `workspace_mkdir`/`workspace_delete`/`workspace_move` typed tools,
  no `/workspace/` Deep Agents route, and no `BrokeredWorkspaceBackend`.
- **The entire AI-runtime side.** No `agent_runtime/capabilities/workspace/*`
  (contracts/ports/service/policy/tools), no `workspace_backend.py`, no desktop
  broker client. Nothing on the Python side calls this broker yet.
- **Watches, per-run quotas beyond the read `FS_LIMITS` ceilings, and the
  sensitive-path policy** (root deny rules for filesystem/home/`userData`/
  credential stores; `.ssh`/`.env`/`*.pem` per-file approvals). Not built.
- **Hardening items being fixed separately:** **G1** (a physical-root leak),
  **G2** (sensitive-path policy), and **G4** (the `RUNTIME_ENABLE_DESKTOP_FILESYSTEM`
  feature gate — the delivered capability code is not yet behind it).

## Problem and why now

Desktop users expect Copilot to work with files in a project, documents folder,
or other directory they intentionally attach. The current product has no
model-facing host filesystem capability:

- Electron main uses Node filesystem APIs for app-owned runtime, secrets, logs,
  and generated adapters, but it has no user-root grants, run snapshots, or
  model-facing capability broker.
- The renderer is correctly isolated with `contextIsolation=true`,
  `nodeIntegration=false`, `sandbox=true`, and an allowlisted preload bridge.
  Adding Node or arbitrary path IPC would collapse that boundary.
- The existing `FilePickerPort` is a user-driven file-selection abstraction. It
  returns a safe `name/size/type/stream()` selection for attachments; it is not
  a durable directory grant and must not become a model-access channel.
- Deep Agents currently composes `StateBackend` with `/drafts/` and
  `/subagents/`. There is no `/workspace/` route.
- Tool policy classifies read/write/destructive effects and supports
  `auto`, `ask`, `require`, and `block`; LangGraph approvals are durable.
  Neither policy nor approval currently proves that a physical path was
  user-granted.

A naive implementation—giving `FilesystemBackend(root_dir=...)` or Python
`pathlib` access to a selected folder—would put host authority in the trusted AI
worker, expose it to prompt/tool misuse, and leave revocation, path races,
audit, and platform semantics scattered across call sites.

AC5 creates two deliberately separate lanes:

1. a **user lane** for explicit file/root selection through native UI; and
2. an **AI lane** from the supervised runtime worker to an authenticated,
   narrow Electron-main broker.

Electron main remains the sole owner of physical roots and host effects. The AI
runtime sees renderer-safe virtual paths and implements Deep Agents
`BackendProtocol` over the broker.

## PR slices (real PR boundaries)

AC5 is an epic. Its real review/merge units are **three ordered slices**, each
independently shippable behind the `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` gate. The
security model in this PRD applies in full across all three; the slices only
stage _how much surface is wired_, never _how much is enforced_.

| Slice                                         | Scope                                                                                                                                                                                                                         | Explicitly NOT in this slice                                                                                                                                       |
| --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Slice 1 — broker + picker + grants**        | Electron-main capability broker plus native folder picker and the grant model: the three modes `read_only` / `read_write_no_delete` / `read_write`, `safeStorage`-encrypted persistent grants, and renderer-safe grant views. | **No filesystem operations of any kind.** No stat/list/read, no write/edit/mkdir, no delete/move, no `/workspace/` route. It stands up the authority surface only. |
| **Slice 2 — read operations + path security** | FS **read** ops (`stat` / `list` / `read` / `glob` / `grep`) plus the full native path-validation layer: traversal, symlink/junction/reparse, alternate-data-stream, and TOCTOU (time-of-check/time-of-use) defenses.         | No mutation. No `/workspace/` write route. Reads only, behind slice 1's grants.                                                                                    |
| **Slice 3 — mutation + approval + route**     | `write` / `edit` / `mkdir`, and `delete` / `move` behind explicit approval; the immutable per-run grant snapshot; and the Deep Agents `/workspace/` `CompositeBackend` route.                                                 | Nothing new in the security model — it reuses slices 1–2.                                                                                                          |

Slice ordering is a hard dependency: slice 2 requires slice 1's grants; slice 3
requires slice 2's validated read path. The rollout stages below map onto these
slices (stages 1–2 → slice 1; stages 3–4 → slice 2; stages 5–6 → slice 3).

## Goals

### Delivered (light) — slices 1 + 2

- Let only an interactive user select a workspace root through an
  Electron-main native directory picker.
- Support exactly `read_only`, `read_write_no_delete`, and `read_write` grant
  modes (mode enforcement for reads via `modeSatisfies`, fail-closed).
- Keep canonical physical paths and grant persistence in Electron main; expose
  the renderer only a path-free `RendererGrant`.
- Route every FS **read** through the authenticated loopback broker with a
  per-boot bearer, protocol header, no-CORS, and the current active-grant
  snapshot (a revoke fails closed on the next op).
- Resolve and authorize paths by realpath-before-authorize + atomic no-follow
  open + revalidate, defending against traversal, symlinks, reserved names, ADS,
  and (on darwin, atomically) TOCTOU races.
- Provide bounded `stat`/`list`/`read`/`glob`/`grep` behind `FS_LIMITS`.
- Fail closed when the broker or grant is unavailable.
- Preserve renderer isolation, service boundaries, and web/Postgres behavior;
  add nothing to non-desktop tool catalogs.

### Deferred / not in the light build

- Expose only opaque `/workspace/<opaque-mount>/...` paths (delivered uses
  `grant_id` + a grant-root-relative virtual path).
- Add `BrokeredWorkspaceBackend` behind the `CompositeBackend`, and the
  `/workspace/` route + `workspace_mkdir`/`delete`/`move` typed tools.
- Route mutations through verified run identity, an immutable per-run grant
  snapshot, tool policy, approval, budget, and audit; two-phase, durable,
  idempotent mutation intents and AC4 preimages before changing/deleting bytes.
- The signed native helper and its `NtCreateFile`/`openat` handle-relative
  primitives (delivered uses pure-Node `realpath` + `O_NOFOLLOW(_ANY)`; Windows
  is non-atomic).
- `write`/`edit`/`mkdir`/`delete`/`move`, workspace snapshot, patch-apply, and
  advisory watch behavior; the full macOS/Windows conformance contract.
- Keep the existing `FilePickerPort` attachment lane formally separated from a
  `WorkspaceGrantPickerPort` (the delivered picker is main-only and directory-
  only, but the renderer-facing `WorkspaceGrantPickerPort` and its wiring are not
  built).

## Non-goals

- Direct Node.js, `fs`, shell, process, or unrestricted IPC access in the
  renderer or preload.
- Direct Python `open`, `os`, `pathlib`, `shutil`, `subprocess`, or host path
  I/O from `ai-backend`.
- Using Deep Agents `FilesystemBackend` or `LocalShellBackend` as a production
  host boundary.
- Arbitrary shell commands, package installation, build/test execution, or
  executable launch. AC7 owns isolated full execution.
- Selecting a root from a model-supplied string, pasted path, URL, environment
  variable, recent-files list, or restored transcript.
- Recursive delete, chmod/chown/ACL mutation, links, mount operations, device
  files, alternate data streams, extended-attribute editing, or arbitrary
  file-descriptor access.
- Automatically granting the user's home directory, filesystem root, app
  `userData`, browser profile, keychain, or credential directories.
- Treating an approval as a filesystem grant or allowing an approval edit to
  broaden a root/mode.
- A background sync engine or autonomous file-change-triggered agent.
- Mounting a live host root into a remote sandbox.
- Linux desktop support or changing any web behavior.

## User-visible behavior and failure behavior

### Attach a workspace

1. The user chooses **Attach folder** from the desktop UI.
2. A native sheet/dialog owned by Electron main asks for a directory.
3. A native confirmation sheet shows the selected folder, requested mode,
   sensitive-root warning, and exact capabilities:
   - **Read only**
   - **Read and write, no delete**
   - **Read, write, and delete**
4. Main resolves the physical root, verifies platform safety/identity and
   overlap rules, persists the encrypted grant, and returns only:
   `grant_id`, opaque `virtual_root`, sanitized display name, mode, status, and
   timestamps.
5. The settings/chat UI shows the display name and mode. It does not receive
   the physical path.
6. At run start, the user selects which active grants are available. An empty
   selection means the agent has no `/workspace/` mounts.

A mode increase requires the same native confirmation flow. A mode decrease or
revoke can happen from settings and takes effect immediately for new calls.
Revocation never deletes the user's files.

### Agent behavior

- The agent sees paths such as
  `/workspace/k7q4p6m2v9x3c8n5r1t6w2z8ab/report.md`.
- `ls`, `read_file`, `glob`, and `grep` work within a read-capable mount under
  bounded quotas.
- `write_file` and `edit_file` require a write-capable grant and the effective
  tool policy/approval.
- `workspace_mkdir`, `workspace_delete`, and `workspace_move` are separate
  typed tools because pinned Deep Agents `BackendProtocol` does not expose all
  three as portable methods.
- Before overwrite, edit, delete, or move of existing content, Copilot stores a
  verified AC4 `file_history` preimage. The activity detail offers **Restore**
  while that artifact is retained. Restore is a new checked mutation, not a
  hidden undo bypass.
- `read_write_no_delete` permits create/write/edit/mkdir and broker-internal
  atomic replacement of the same logical file. It does not permit user-visible
  delete, move/rename, rename-away, or replacement of a different existing
  path.
- A run never gains a root or stronger mode merely because the model asks, a
  prompt instructs it, or an approval is accepted.

### Failure behavior

- **No grant:** return `workspace_grant_required`; do not open a picker from an
  AI call.
- **Grant revoked/expired/offline:** return the typed state and pause privileged
  resume for user reauthorization. No direct-host fallback.
- **Broker/native helper unavailable:** remove workspace tools from new runs;
  in-flight calls fail `desktop_capability_unavailable`.
- **App/broker restart:** all RAM run-capability contexts expire. Interrupted
  privileged runs require explicit reauthorization before continuing.
- **Invalid/escaped path or unsupported link/type:** deny before content or
  existence detail is returned.
- **File changed after read/approval/prepare:** return
  `workspace_precondition_failed` with safe conflict metadata. Never overwrite.
- **Approval changed the path, operation, content hash, or batch:** invalidate
  it and request a new approval.
- **AC4 snapshot cannot commit:** fail before the host effect.
- **Crash during mutation:** reconcile the durable intent and host
  postcondition. Return committed, safe-to-retry, or `outcome_unknown`; never
  blindly repeat.
- **Quota/watch overflow:** stop the bounded operation and return
  `workspace_quota_exceeded` or `workspace_rescan_required`. Watch events are
  advisory and never substitute for operation-time validation.
- **Root moved or volume removed:** mark `offline`. Re-selection must match the
  recorded root identity or creates a new grant.

## Alternatives considered

### Give the renderer Node.js or Electron `fs`

Rejected. A renderer compromise or untrusted adapter would inherit user-file
authority. It violates the existing BrowserWindow security contract and makes
physical paths/tokens observable to renderer JavaScript.

### Extend preload with `readPath(path)` / `writePath(path, bytes)`

Rejected. An allowlisted channel with arbitrary paths is still an unrestricted
filesystem API. Renderer IPC is the user-presentation lane, not the AI-worker
privilege protocol.

### Let `ai-backend` call Python filesystem APIs

Rejected. The trusted worker runs model-controlled orchestration and would gain
ambient same-user authority. Every path-validation omission would become a
host escape, and Electron could not centrally revoke/audit grants.

### Deep Agents `FilesystemBackend(root_dir=..., virtual_mode=True)`

Rejected as the security boundary. It provides path restriction, not
interactive grants, process authentication, immutable run snapshots,
approval/grant intersection, stable host identity, native race protection, or
main-owned audit. It is not used over a user root.

### Deep Agents `LocalShellBackend`

Rejected. Shell access makes path restrictions meaningless and exposes the
entire host process environment. Production desktop builds must prove it is
unreachable.

### Reuse `FilePickerPort` as the agent filesystem

Rejected. `FilePickerPort` is intentionally user-driven and returns selected
file streams, not durable directory authority. Model-triggered picker use would
confuse attachment consent with an ongoing grant.

### Browser File System Access API

Rejected. It moves host authority into the renderer, behaves differently
across platforms, and cannot supply the main-owned broker/auth/audit boundary.

### Pure Node `realpath` checks before each operation

Rejected. A string check followed by a separate open is vulnerable to ancestor
and final-component swaps. Windows reparse points and rename/delete races need
handle-relative platform operations. AC5 uses a narrow signed native helper.

### Watch filesystem changes and trust the cache

Rejected. FSEvents and `ReadDirectoryChangesW` coalesce and can overflow.
Watches invalidate caches and inform UI only; every operation revalidates
physical identity and authorization.

### One broad read-write mode

Rejected. Users need useful edits without delete/rename authority.
`read_write_no_delete` is a distinct enforceable mode, not UI wording.

### Copy the entire workspace into app storage

Rejected for normal file work. It creates synchronization/conflict ambiguity
and duplicate canonical bytes. AC7 uses an explicit artifact snapshot only
when transferring to a remote sandbox.

## Architecture and ownership

### Two non-interchangeable lanes

```text
USER LANE
user gesture
  -> chat-surface FilePickerPort (individual attachment files), or
     WorkspaceGrantPickerPort (directory grant request)
  -> allowlisted preload IPC
  -> Electron-main native picker/confirmation
  -> renderer-safe selection or CapabilityGrantV1

AI LANE
runtime_worker
  -> policy / approval / budget / event middleware
  -> BrokeredWorkspaceBackend or typed workspace mutation tool
  -> authenticated AC1 loopback broker
  -> Electron-main grant snapshot + mutation journal
  -> native workspace-fs helper
  -> user-granted physical root
```

The user lane has no broker credential. The AI lane cannot open a picker.
Neither lane accepts a physical path supplied by the renderer, model, event,
checkpoint, or Python service.

`FilePickerPort` remains exactly the attachment selection abstraction in
`packages/chat-surface`: it returns `name`, `size`, `type`, and a fresh byte
stream after a user action. Desktop attachment bytes are committed through AC4
and the stream is closed. That selection does not create a reusable grant.

Directory grants use a new `WorkspaceGrantPickerPort` because their lifecycle,
mode, revocation, and renderer-safe result differ from a file attachment:

```typescript
export interface WorkspaceGrantPickerPort {
  pickRoot(
    requestedMode: "read_only" | "read_write_no_delete" | "read_write",
  ): Promise<CapabilityGrantV1 | null>;
}
```

The renderer can request a mode but cannot provide a path, mount ID, grant ID,
physical identity, owner, or policy. Main may return a weaker mode; it never
returns a stronger one than the confirmed native sheet.

### Deep Agents composition

For an authorized desktop run:

```text
CompositeBackend
├── default                 -> StateBackend (transient internal scratch)
├── /drafts/                -> existing DraftBackend
├── /subagents/             -> existing read-only child trace projection
├── /large_tool_results/    -> AC4 ArtifactBackend
└── /workspace/             -> BrokeredWorkspaceBackend
```

`BrokeredWorkspaceBackend` implements the synchronous and asynchronous methods
of the pinned Deep Agents `BackendProtocol`:

- `ls/als`
- `read/aread`
- `write/awrite`
- `edit/aedit`
- `glob/aglob`
- `grep/agrep`

It binds a verified broker client, opaque run-capability context, immutable
grant-snapshot hash, org/workspace/run/task identity, policy dispatcher, and
AC4 coordinator at construction. Prefix-stripped paths begin with
`/<opaque-mount>/...`; full `/workspace/...` paths are accepted only by the
outer routing validator.

Pinned Deep Agents has no portable mkdir/delete/move methods in
`BackendProtocol`. AC5 therefore registers three narrow typed tools:
`workspace_mkdir`, `workspace_delete`, and `workspace_move`. They use the same
`WorkspaceOperationService`, broker, grant checks, policy, approvals, budgets,
intents, events, and audit as backend writes/edits.

The desktop harness permits Deep Agents file write/edit tools only when the
desktop profile and AC5 gate are active. A path-permission wrapper allows:

- model reads under `/workspace/`, `/subagents/`, `/large_tool_results/`, and
  existing approved internal routes;
- model writes only under `/workspace/` and `/drafts/`;
- no model write to `/subagents/`, `/large_tool_results/`, `/skills/`,
  `/memories/`, or another default-state path.

Non-desktop harness profiles and tool catalogs remain byte-for-byte unchanged.

### Responsibility matrix

| Concern                                                              | Canonical owner                     | Must not own                           |
| -------------------------------------------------------------------- | ----------------------------------- | -------------------------------------- |
| Native picker, physical root, filesystem identity, grant persistence | Electron main                       | Model planning or tool policy          |
| Root-relative safe open/read/mutate/watch primitives                 | Signed desktop native helper        | Approval, AI identity, arbitrary shell |
| Run-capability context and grant snapshot                            | Electron main RAM                   | Renderer or transcript                 |
| Abstract read/write/destructive policy, approval, budgets            | AI runtime                          | Physical path or OS handle             |
| Deep Agents protocol adaptation                                      | `services/ai-backend`               | Host filesystem implementation         |
| Preimage bytes/history                                               | AC4 artifact store                  | Physical workspace authority           |
| User files                                                           | User-selected root                  | Runtime transcript/artifact store      |
| Attachment selection                                                 | Existing `FilePickerPort` user lane | Persistent grant                       |
| Renderer                                                             | Presentation and explicit requests  | Node, broker token, physical path      |

### SOLID, DRY, KISS, and single source of truth

- **Single responsibility:** main owns physical authority; the native helper
  owns race-safe primitives; AI runtime owns orchestration/policy; AC4 owns
  history bytes.
- **Open/closed:** another Deep Agents version or broker transport adapts behind
  ports without moving physical authority.
- **Liskov substitution:** fake and real broker clients satisfy one operation
  conformance suite; `BrokeredWorkspaceBackend` satisfies the pinned
  `BackendProtocol`.
- **Interface segregation:** picker, grant management, read operations,
  mutations, snapshots, patch apply, and watches are narrow interfaces. There
  is no `executeAnything`, raw descriptor, or arbitrary-path call.
- **Dependency inversion:** AI code depends on Pydantic ports; Electron depends
  on Zod contracts/native helper; neither imports the other's implementation.
- **DRY:** one path validator, mode matrix, operation service, approval binding,
  intent journal, quota policy, and audit emitter serves every workspace tool.
- **KISS:** one virtual prefix, three grant modes, one broker protocol, no
  renderer Node, no host shell, no recursive delete, and no implicit grant.
- **Single source of truth:** main's encrypted grant log owns authority; the
  host root owns user files; AC4 owns snapshots; runtime events describe
  actions; watchers/caches/SQLite own nothing canonical.

## Grant lifecycle and authorization

> **Partially delivered.** Delivered: main-only picker-driven grant creation,
> `safeStorage`-encrypted persistence, immediate revocation (a revoked grant is
> excluded from the next active snapshot), and the renderer-safe view. **Deferred:**
> the event-sourced lifecycle (`created`/`expanded`/`downgraded`/`reauthorized`/
> `offline`/`revoked` with previous-event hashes), overlap/sensitive-root
> rejection, mode expansion/downgrade flows, the immutable per-run snapshot, and
> the 256-bit `run_capability_context`. Grants are a mutable encrypted collection
> rewritten on change; the broker re-reads the active snapshot per op.

### Grant creation

Only Electron main may create a physical grant:

1. Verify the requesting BrowserWindow is the focused, visible 0xCopilot
   window and no picker is already open.
2. Open `dialog.showOpenDialog` with `openDirectory` and without file,
   multi-select, or arbitrary save-path behavior.
3. Resolve the selected directory through the native helper.
4. Reject filesystem/home/app-data/credential roots, unsupported volumes,
   links/reparse points, identity-less filesystems, and overlap with another
   active grant.
5. Show a native confirmation sheet with the resolved display location and
   requested mode.
6. Generate a random UUID `grant_id` and independent 128-bit lowercase-base32
   `mount_id`.
7. Persist a main-only encrypted grant event and append a redacted capability
   audit event.
8. Return the AC1 `CapabilityGrantV1` safe view.

The renderer cannot bypass steps 2–5. Picker cancellation returns `null` and
creates no audit grant.

AC5 v1 rejects overlapping roots: an active root cannot equal, contain, or be
contained by another active grant, including one in another 0xCopilot
workspace. The user must revoke the old grant and attach the intended root.
This prevents the same file from being reachable through mounts with different
modes.

### Grant modes

| Operation                                    | `read_only` | `read_write_no_delete` | `read_write` |
| -------------------------------------------- | :---------: | :--------------------: | :----------: |
| stat/list/read/glob/grep                     |     yes     |          yes           |     yes      |
| create regular file                          |     no      |          yes           |     yes      |
| overwrite/edit same logical file             |     no      |          yes           |     yes      |
| create directory                             |     no      |          yes           |     yes      |
| broker-internal temp + replace for same file |     no      |          yes           |     yes      |
| delete file                                  |     no      |           no           |     yes      |
| delete empty directory                       |     no      |           no           |     yes      |
| move/rename existing path                    |     no      |           no           |     yes      |
| overwrite a different destination by move    |     no      |           no           |      no      |
| recursive delete, links, devices, chmod/ACL  |     no      |           no           |      no      |

Mode comparison is an explicit matrix; code never orders strings.

Changing `read_only` to either write mode or
`read_write_no_delete` to `read_write` requires a new native confirmation and
creates an `expanded` grant event. Downgrade/revoke creates a new event and
invalidates future calls immediately. An approval cannot invoke this API.

### Persistent grant and run snapshot

Main persists encrypted physical grant events. Active state is the fold of
`created`, `expanded`, `downgraded`, `reauthorized`, `offline`, and `revoked`
events. Every event includes verified product workspace/user, actor, mode,
root-identity version, encrypted physical root/identity, policy version,
timestamp, and previous-event hash.

At run start:

1. The renderer supplies only selected safe `grant_id` values through the
   normal run UX.
2. Main verifies the signed-in product workspace/user and active grant state.
3. Main reopens and revalidates every root identity.
4. Main creates an immutable RAM snapshot with modes/mounts/root handles.
5. Main mints a random 256-bit `run_capability_context` bound to broker
   instance, audience, verified run/workspace/user, snapshot hash, and expiry.
6. Only the supervised AI-worker audience receives that opaque context.

Caller-supplied org, user, workspace, run, grant, role, scope, approval, or
virtual path is never sufficient authority. Main resolves them against the
opaque context. Contexts expire at run terminal state, grant revocation,
sign-out, workspace switch, broker restart, app quit, or a maximum of 60
minutes. Long runs renew only after main revalidates roots/grants; renewal
cannot add a mount or mode.

## Virtual path contract

> **Deferred / not in the light build.** The opaque `/workspace/<26-char base32
mount>` root is **not** delivered. The broker addresses a grant by its
> `grant_id` (UUID) plus a grant-root-relative virtual path; the shipped syntactic
> validation (`normalizeVirtualPath` in `path-validation.ts`) enforces most of the
> segment rules below (traversal, separators, reserved names, ADS, trailing
> dot/space, controls, over-long/over-deep) but the mount-id addressing scheme and
> the case/normalization-collision `workspace_name_collision` handling are design
> intent.

The renderer/model-visible root is exactly:

```text
/workspace/<26-character lowercase base32 mount id>
```

Descendant virtual paths:

- use `/` separators on both platforms;
- are NFC-normalized UTF-8;
- have at most 4,096 UTF-8 bytes, 64 descendant segments, and 255 Unicode
  scalar values per segment;
- reject empty segments, repeated separators, `.`, `..`, backslash, NUL,
  controls, percent-encoded separators/traversal, bidi path controls, and
  noncharacters;
- reject Windows device names, drive syntax, UNC/device prefixes, colons,
  alternate data streams, and segments ending in dot or space on both
  platforms so a recorded path has one cross-platform meaning.

The broker parses the virtual path, looks up the mount in the immutable
snapshot, and passes normalized segments—not a concatenated path string—to the
native helper. Directory listings return canonical virtual paths. Physical
paths never appear in tool results, events, approvals, safe errors, logs,
metrics, or renderer data.

Case and normalization collisions are fail-closed:

- On a case-insensitive volume, comparison uses the filesystem's effective
  case behavior while display preserves on-disk case.
- On a case-sensitive volume, exact case is required.
- If two directory entries normalize to the same NFC/case key, the directory
  is readable only through raw user tools outside Copilot; AC5 returns
  `workspace_name_collision`.

## Native physical enforcement

> **Deferred / not in the light build.** The signed Rust N-API helper is **not**
> built. Delivered enforcement is **pure Node** (`host-fs.ts`): realpath-resolve
> before authorize, `lstat` type gate, and an atomic no-follow open with
> post-open recheck. On **darwin** `O_NOFOLLOW_ANY` closes the TOCTOU window
> atomically; on **non-darwin (incl. Windows)** the recheck is non-atomic (the
> honestly-documented residual) and there is no `NtCreateFile`/`openat`
> handle-relative traversal, `st_dev`/`st_ino` volume-escape enforcement, or
> platform-specific replacement path. The macOS/Windows sections below are design
> intent for the future signed helper.

String normalization is defense in depth, not the authorization boundary.
AC5 ships a signed Rust N-API helper inside `apps/desktop` with only
root-relative filesystem primitives. Electron main owns helper handles and
never exposes the addon to preload/renderer.

The helper:

- opens a selected root and captures stable volume/root identity;
- accepts normalized path segments and an operation enum, never an arbitrary
  command or URL;
- opens every existing ancestor without following links;
- rejects volume/mount transitions below the selected root;
- returns opaque handles/identities to main;
- performs reads and mutations on validated handles;
- enforces size/depth/type/deadline/cancellation limits; and
- has no shell, process, network, dynamic library, Electron, keychain, or
  environment API.

Only regular files and directories are supported. Symlinks, Finder aliases as
targets, junctions, mount points, reparse points, hard-linked regular files,
devices, FIFOs, sockets, and named pipes are denied. Sparse files may be read
only through bounded slices; AC7 snapshot and AC5 mutation reject sparse files.

### macOS behavior

- Supported release targets are APFS and HFS+ volumes that provide stable
  device/inode identity and required `openat` behavior. Unsupported/network
  filesystems fail grant creation.
- Root identity is volume UUID plus `st_dev`/`st_ino`. Every call verifies the
  root handle still matches.
- Descendants are traversed from the root directory descriptor with
  `openat`/`fstatat`, `O_CLOEXEC`, and `O_NOFOLLOW`; each ancestor is checked
  with `AT_SYMLINK_NOFOLLOW`.
- A descendant with a different `st_dev` is a mount escape and is denied.
- Reads use the opened file descriptor and verify `fstat` identity/size before
  and after bounded access.
- Existing-file mutations hold the validated descriptor and recheck identity
  immediately before effect. Rename/delete use `renameat`/`unlinkat` relative
  to validated parent descriptors.
- File replacement writes a random same-directory temporary file, preserves
  existing mode/ownership where permitted, `fsync`s bytes, atomically renames
  over the same logical target, and `fsync`s the parent directory.
- New files use `0666 & umask`; new directories use `0777 & umask`. AC5 does
  not change ACLs or arbitrary extended attributes. Existing-file replacement
  preserves metadata through the platform replacement path.
- Finder aliases are ordinary files; AC5 never resolves or opens their target.
- The picker resolves a selected symlink to its physical directory for the
  native confirmation. The persisted root itself must be a real directory.
- V1 signed/hardened-runtime builds are not Apple App Sandbox builds. If an App
  Sandbox entitlement requiring security-scoped bookmarks is detected, AC5 is
  disabled with `workspace_platform_unsupported`; bookmark support requires a
  separate accepted revision.
- FSEvents supplies advisory recursive watch notifications through the native
  helper. Dropped/coalesced/root-change flags become `rescan_required`.

### Windows behavior

- Supported release targets are local NTFS/ReFS volumes that provide stable
  volume serial/file IDs and required handle operations. UNC paths, mapped
  network shares, device namespaces, unsupported removable filesystems, and
  volumes without stable identity fail grant creation.
- Internally, physical paths use canonical extended-length form. No `\\?\`,
  drive letter, volume GUID, or native path reaches the AI worker/renderer.
- Root identity is volume GUID/serial plus 128-bit file ID.
- The native helper opens each segment relative to the held directory handle
  using `NtCreateFile` root-handle semantics, with reparse-point opening
  disabled for traversal. Every component is checked for
  `FILE_ATTRIBUTE_REPARSE_POINT`, volume change, type, and file ID.
- Junctions, symbolic links, volume mount points, App Execution Aliases,
  OneDrive/cloud placeholder reparse files, and every other reparse tag are
  denied in v1 rather than followed.
- Colons/alternate streams, DOS devices (`CON`, `NUL`, `COM1`, and variants),
  trailing dot/space aliases, short-name ambiguity, and case-fold collisions
  are denied.
- Reads use the opened handle and compare file ID/size before and after.
- Rename/delete use handle-based `SetFileInformationByHandle`; they do not
  re-resolve a model path after authorization.
- Existing-file replacement stages in the same directory, calls
  `FlushFileBuffers`, uses `ReplaceFileW`/handle-based rename without an
  overwrite-of-different-target option, and preserves the existing ACL.
  New entries inherit the parent ACL.
- The mutation journal is the durability authority where Windows cannot
  guarantee a parent-directory flush; restart reconciliation verifies the
  exact file ID/content postcondition.
- `ReadDirectoryChangesW` supplies advisory recursive notifications. Buffer
  overflow, root move, USN discontinuity, or watcher restart becomes
  `rescan_required`.

Use of `NtCreateFile` is confined to the signed helper, pinned to supported
Windows versions, and covered by packaging/security review. Falling back to
string-only `CreateFileW` authorization is prohibited.

## Tool policy, approvals, and grant intersection

> **Deferred / not in the light build.** Delivered read gating is the profile
> grant + grant-mode (`modeSatisfies`) + operation-time physical path/identity
> checks. The tool-use policy (`auto`/`ask`/`require`/`block`), approval-digest
> binding, per-file sensitive-path approval, capability risk floors, and budgets
> are **not** wired (no AI-runtime side calls the broker yet). Sensitive-path
> policy is tracked as **G2**. This section is design intent.

The effective decision is the intersection of:

1. desktop profile and AC5 feature gate;
2. verified runtime identity and worker audience;
3. immutable active grant snapshot;
4. grant mode;
5. sensitive-path policy;
6. tool-use policy (`auto`/`ask`/`require`/`block`);
7. capability-specific risk floor;
8. valid approval bound to exact intent, when required;
9. run/tool/file/byte/time budgets; and
10. operation-time physical path/identity checks.

No layer can broaden a denial from another layer.

### Policy classification

| Operation                                   | Tool policy axis | Minimum approval behavior                                                                     |
| ------------------------------------------- | ---------------- | --------------------------------------------------------------------------------------------- |
| stat/list/read/glob/grep                    | `read`           | Workspace policy may auto-allow; sensitive path can force per-call approval                   |
| create/write/edit/mkdir                     | `write`          | `ask` may cache only per run + snapshot hash + mount + operation class; `require` is per call |
| delete/move/restore/patch apply             | `destructive`    | Per-call approval regardless of a more permissive general policy                              |
| batch touching more than 20 paths or 10 MiB | `destructive`    | One immutable reviewed batch approval                                                         |

`block` always wins. A cached `ask` decision expires with the run context and
cannot cross mounts, grant changes, or operation classes.

### Approval binding

An approval digest covers:

- broker instance and run-capability-context hash;
- grant-snapshot hash and opaque mount;
- operation;
- canonical virtual source/destination;
- current/precondition hash or absence;
- proposed result hash/size or delete marker;
- complete sorted batch manifest, if any;
- policy/risk version; and
- expiry.

Edited arguments produce a new digest and policy decision. Main receives the
approval ID and digest but independently checks the grant/mode/path. A valid
approval:

- cannot add a mount;
- cannot change `read_only` into a write grant;
- cannot add delete to `read_write_no_delete`;
- cannot select another path/root;
- cannot bypass quota, path, link, race, or stale-content checks; and
- cannot survive revoke, mode downgrade, broker restart, or expiry.

Root creation/expansion is never an AI approval category. It always returns to
the native user lane.

## Strict typed contracts

> **Mostly deferred.** Delivered contracts are the simpler `Grant` /
> `RendererGrant` (`grantId`/`root`/`mode`/`label`/`status: active|revoked`/
> timestamps) in `types.ts`, validated at the IPC/broker boundary by the Zod
> schemas in `schemas.ts`, plus the read-op result shapes (`HostStatResult`,
> `HostDirEntry`/`HostListResult`, `HostReadResult`, `HostGlobResult`,
> `HostGrepHit`/`HostGrepResult`). The full `CapabilityGrantV1`/
> `PhysicalWorkspaceGrantV1`, the `WorkspaceRead*`/`WorkspaceMutation*`/two-phase
> intent contracts, `SecretStr` tokens, and the broker mutation-operation
> allowlist below are **design intent**.

### Renderer-safe and main-only grants

AC1's renderer-safe contract remains normative:

```python
class CapabilityGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    grant_id: UUID
    capability: Literal["workspace.files"]
    mode: Literal["read_only", "read_write_no_delete", "read_write"]
    virtual_root: str
    display_name: str = Field(min_length=1, max_length=120)
    status: Literal["active", "offline", "revoked", "needs_reauthorization"]
    created_at: AwareDatetime
    updated_at: AwareDatetime
```

`virtual_root` must match
`^/workspace/[a-z2-7]{26}$`. The renderer-safe TypeScript type contains exactly
these values and no index signature.

Electron main owns an additional type that is never exported from preload:

```typescript
interface PhysicalWorkspaceGrantV1 {
  readonly version: 1;
  readonly grantId: string;
  readonly workspaceId: string;
  readonly userId: string;
  readonly mountId: string;
  readonly mode: "read_only" | "read_write_no_delete" | "read_write";
  readonly encryptedCanonicalRoot: Uint8Array;
  readonly encryptedRootIdentity: Uint8Array;
  readonly displayName: string;
  readonly status: "active" | "offline" | "revoked" | "needs_reauthorization";
  readonly policyVersion: number;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly revokedAt?: string;
}
```

The encrypted fields exist only in main-process storage/memory. Zod rejects
unknown fields at IPC and broker boundaries.

### Workspace entry and bounded read

```python
class WorkspaceEntryV1(RuntimeContract):
    version: Literal[1] = 1
    virtual_path: str
    entry_type: Literal["file", "directory"]
    logical_size: int | None
    modified_at: AwareDatetime | None
    content_sha256: str | None


class WorkspaceReadRequestV1(RuntimeContract):
    version: Literal[1] = 1
    request_id: UUID
    run_capability_context: SecretStr
    virtual_path: str
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=10_000)
    mode: Literal["utf8_lines", "byte_stream"]


class WorkspaceReadResultV1(RuntimeContract):
    version: Literal[1] = 1
    virtual_path: str
    entry: WorkspaceEntryV1
    content_utf8: str | None
    stream_ticket: SecretStr | None
    truncated: bool
    next_offset: int | None
```

`utf8_lines` is the Deep Agents path and rejects invalid UTF-8/NUL/binary
content. `byte_stream` is an internal AC4/AC7/AC8 transfer lane. A stream ticket
is random, one-use, worker-audience/run-bound, expires after 60 seconds, and is
never persisted or exposed to the renderer/model.

### Two-phase mutation contracts

```python
class WorkspaceMutationSpecV1(RuntimeContract):
    version: Literal[1] = 1
    operation: Literal[
        "create_file",
        "replace_file",
        "edit_file",
        "mkdir",
        "delete_file",
        "delete_empty_directory",
        "move",
        "restore",
        "apply_patch",
    ]
    source_virtual_path: str
    destination_virtual_path: str | None = None
    expected_source_sha256: str | None = None
    expected_destination_absent: bool
    proposed_sha256: str | None = None
    proposed_size: int | None = None


class PrepareWorkspaceMutationV1(RuntimeContract):
    version: Literal[1] = 1
    request_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=128)
    run_capability_context: SecretStr
    approval_id: UUID
    approval_digest: str
    spec: WorkspaceMutationSpecV1


class PreparedWorkspaceMutationV1(RuntimeContract):
    version: Literal[1] = 1
    intent_id: UUID
    request_digest: str
    precondition_token: SecretStr
    source_state: Literal["absent", "file", "empty_directory"]
    source_sha256: str | None
    source_size: int | None
    preimage_stream_ticket: SecretStr | None
    content_upload_required: bool
    expires_at: AwareDatetime


class CommitWorkspaceMutationV1(RuntimeContract):
    version: Literal[1] = 1
    request_id: UUID
    idempotency_key: str
    run_capability_context: SecretStr
    intent_id: UUID
    precondition_token: SecretStr
    approval_id: UUID
    approval_digest: str
    file_history_ref: ArtifactRefV1 | None
    staged_content_sha256: str | None
    staged_content_size: int | None


class WorkspaceMutationResultV1(RuntimeContract):
    version: Literal[1] = 1
    intent_id: UUID
    status: Literal[
        "committed",
        "aborted",
        "conflict",
        "outcome_unknown",
    ]
    source_virtual_path: str
    destination_virtual_path: str | None
    before_sha256: str | None
    after_sha256: str | None
    file_history_ref: ArtifactRefV1 | None
    committed_at: AwareDatetime | None
```

Secrets are redacted in repr/logging and excluded from canonical events.
Physical identity is represented to the worker only by a broker-MACed
`precondition_token`; device/inode/volume/file IDs remain in main.

Proposed bytes are uploaded with a separate authenticated, bounded streaming
request to the prepared intent. Main stages them under app-owned
`capability-broker/v1/staging`, verifies declared size/SHA-256, `fsync`s, and
marks `content_staged`. No JSON/base64 body carries a 512 MiB file.

### Broker operations

The worker audience allowlist contains only:

- `workspace.stat`
- `workspace.list`
- `workspace.read`
- `workspace.glob`
- `workspace.grep`
- `workspace.prepare_mutation`
- `workspace.upload_mutation_content`
- `workspace.commit_mutation`
- `workspace.abort_mutation`
- `workspace.snapshot_manifest`
- `workspace.watch_start`
- `workspace.watch_poll`
- `workspace.watch_stop`

There is no physical-path, shell, process, executable, chmod, link, arbitrary
HTTP, environment, keychain, Electron eval, or generic method. AC1 envelope,
authentication, instance, request ID, body limit, no-CORS, replay, deadline,
and audience rules apply unchanged.

### Stable errors

- `desktop_capability_unavailable`
- `workspace_disabled`
- `workspace_grant_required`
- `workspace_grant_not_found`
- `workspace_grant_revoked`
- `workspace_grant_offline`
- `workspace_reauthorization_required`
- `workspace_mode_denied`
- `workspace_approval_required`
- `workspace_approval_invalid`
- `workspace_invalid_path`
- `workspace_path_escape`
- `workspace_link_denied`
- `workspace_mount_escape`
- `workspace_unsupported_file_type`
- `workspace_name_collision`
- `workspace_not_found`
- `workspace_already_exists`
- `workspace_not_empty`
- `workspace_binary_file`
- `workspace_precondition_failed`
- `workspace_intent_expired`
- `workspace_idempotency_conflict`
- `workspace_quota_exceeded`
- `workspace_rescan_required`
- `workspace_outcome_unknown`
- `workspace_cancelled`
- `workspace_io_failed`
- `workspace_platform_unsupported`

Safe messages may include the virtual path and sanitized display name, but
never a physical path, native identity, broker token, precondition token,
content, or cross-workspace existence detail.

## Two-phase and idempotent mutation protocol

> **Deferred / not in the light build (slice 3).** No mutation path ships — no
> prepare/commit journal, reconciler, staging, idempotency keys, AC4 preimage, or
> postcondition verification. This entire section is design intent.

### Phase 0 — propose and approve

The runtime builds the exact virtual-path operation, reads current content only
under read authorization, computes proposed bytes/hash/diff, applies the tool
policy/risk floor, and obtains the required approval. It persists a
`workspace.mutation_proposed` runtime event before contacting prepare.

### Phase 1 — prepare and snapshot

Electron main:

1. Authenticates AC1 worker audience and opaque run context.
2. Intersects grant mode, approval digest, quotas, and sensitive-path policy.
3. Opens source/destination through the native helper and captures physical
   preconditions.
4. Rejects stale expected hashes/absence.
5. Appends and `fsync`s an encrypted `prepared` intent record.
6. Returns a MACed precondition token and, for existing bytes, a one-use
   preimage stream ticket.

The worker streams the preimage into AC4 as `kind=file_history`. It verifies
that AC4's logical digest/size equal the broker's prepared source digest/size.
If they do not, it aborts. AC4 and the canonical mutation/checkpoint record
must be durable before commit.

For create/replace/edit/restore/patch bytes, the worker uploads the exact
approved proposed stream to the intent staging endpoint. Main hashes, size
checks, `fsync`s, and records `content_staged`.

### Phase 2 — commit

Electron main:

1. Reauthenticates context, approval, grant, mode, and intent expiry.
2. Verifies idempotency key/request digest and staged proposed hash/size.
3. Validates the `file_history_ref` digest/size against the prepared preimage.
   The trusted worker contract guarantees AC4 durability; main does not read
   the artifact root.
4. Reopens/rechecks root, source, destination, parent handles, identities, and
   expected content immediately before effect.
5. Appends and `fsync`s `committing`.
6. Performs one bounded native mutation.
7. Verifies the exact postcondition.
8. Appends and `fsync`s `committed`, emits main capability audit, and returns
   the stored result.

Only after the broker returns committed does the worker append the normal
tool-result/runtime event. A worker crash after commit is recovered by calling
the same idempotency key and receiving the stored result.

### Idempotency and restart reconciliation

`(workspace_id, idempotency_key)` binds one request digest. Reuse with another
operation/path/hash/approval is `workspace_idempotency_conflict`.

The main-owned encrypted journal has states:

```text
prepared -> content_staged? -> committing -> committed
         \-> aborted
committing -> conflict | outcome_unknown (reconciliation only)
```

On restart:

- `prepared`/`content_staged` with no effect are aborted because the run
  capability expired; a user must reauthorize and create a new intent.
- `committing` is reconciled:
  - exact expected postcondition → append `committed`;
  - exact unchanged precondition → append `aborted_safe_to_retry`;
  - any third state → append `outcome_unknown` and require user inspection.
- `committed` returns the same result to duplicate delivery, even though the
  old run context cannot authorize a new operation.

Write/edit stages use same-directory temporary files and atomic same-target
replacement. Delete is one file or empty directory only. Move is within the
same mount/volume, requires destination absence, and never overwrites. A patch
prepares/snapshots every entry before the first effect, applies in sorted path
order, and compensates committed entries from AC4 preimages on failure. If
compensation cannot be proven, the batch is `outcome_unknown` with per-path
results; it is never reported as fully committed.

## Persistence, recovery, retention, and deletion

> **Mostly deferred.** Delivered persistence is a single `safeStorage`-encrypted
> `<userData>/capabilities/grants.bin` (whole-collection, rewritten on change),
> not the append-only `grants/`/`intents/`/`staging/`/`quarantine/` event tree
> below. There is no intent journal, mutation reconciliation, staging, or
> file-history retention (no mutations ship). Revocation is delivered; grant/audit
> retention, offline/needs-reauthorization states, and corruption-quarantine
> recovery are design intent.

### Exact main-owned layout

Under Electron `userData`:

```text
capability-broker/
└── v1/
    ├── grants/
    │   └── <workspace-key>/
    │       └── events/
    │           └── <20-digit-sequence>-<event-id>.bin
    ├── intents/
    │   └── <workspace-key>/
    │       └── <intent-id>/
    │           └── <20-digit-sequence>-<state>.bin
    ├── staging/
    │   └── <intent-id>.blob
    └── quarantine/
        └── <utc>-<record-id>.bin
```

Each `.bin` is a strict JSON record encrypted with Electron `safeStorage`,
written to a same-directory temporary file, `fsync`ed, atomically renamed, and
directory-`fsync`ed where supported. Sequence gaps are allowed after a crash;
duplicate sequence/event IDs are not. Active grant and intent state is rebuilt
by folding records. Physical roots/identities never appear in plaintext
filenames, AC2 JSONL, AC4, SQLite, logs, or renderer state.

If `safeStorage` encryption is unavailable in a production desktop build, AC5
does not enable. Dev-only plaintext fallback cannot carry a real persistent
grant and displays a warning.

### Canonical and rebuildable state

- Canonical grant authority: encrypted main grant events.
- Canonical user bytes: selected host root.
- Canonical mutation state: encrypted main intent journal plus host
  postcondition.
- Canonical preimage: AC4 object referenced by runtime mutation record.
- Canonical AI action history: runtime event/tool/approval records.
- RAM only: open root/file handles, run capability contexts, stream tickets,
  watch handles, caches.
- Rebuildable: renderer grant list, watch state, operation counters, search
  caches, and any SQLite projection.

There is no transaction pretending the host filesystem and runtime event store
commit atomically. The prepared/committing journal, AC4 preimage, idempotency
key, and postcondition reconciliation make the boundary explicit.

### Grant retention/deletion

- Revoke immediately removes future authority and closes handles/watches.
- Encrypted grant/audit records remain under the configured security retention
  and legal hold; revocation is not erased merely to hide evidence.
- User/workspace deletion revokes grants first, then removes encrypted physical
  grant material after required audit/deletion evidence. It does not delete
  user files.
- Sign-out revokes all run contexts and closes handles; persistent grants move
  to `needs_reauthorization` until the same verified product user signs in.
- A moved/replaced root with a changed identity is never silently rebound.

### Mutation/file-history retention

- Prepared/aborted staging bytes are removed immediately or on the next startup
  sweep; they are never artifacts.
- Completed intent journal records remain at least 30 days and while the parent
  run may replay; security policy may retain redacted audit longer.
- AC4 `file_history` preimages default to `raw_30d`. User pin/legal hold keeps
  the reference according to AC4 rules.
- Chat deletion removes its file-history references unless held. It does not
  modify current user files.
- Restore reads and fully verifies the preimage, then performs a new approved
  mutation against the current expected hash. It never bypasses a downgraded or
  revoked grant.
- Legal hold preserves action/approval/intent/preimage evidence, not a live
  grant and not the user file itself.

### Recovery

- Corrupt encrypted grant record: mark the workspace capability read-only/off,
  quarantine the record, and require explicit repair; never skip a middle
  grant event and infer authority.
- Corrupt intent before any effect: abort safely.
- Corrupt/non-terminal intent around an effect: deny new mutations on that
  mount until reconciliation/export.
- Missing AC4 preimage for a not-yet-committed mutation: abort.
- Missing preimage after a committed mutation: history is unavailable but the
  host change is not automatically reversed; emit repair/audit evidence.
- Root offline: keep encrypted grant metadata, close handles, and offer
  re-selection.
- Broker restart: invalidate all authority, reconcile committing intents, then
  require run reauthorization.

## Quotas and watch behavior

> **Partially delivered.** The delivered read path enforces the `FS_LIMITS`
> ceilings in `path-validation.ts` (path depth/bytes, read byte caps, dir-entry
> cap, walk depth/entries/deadline, glob/grep match caps, grep file/line caps).
> The per-run/per-workspace grant/mount/byte quotas in the table below, the
> sensitive-path deny/approval rules (**G2**), and **watches** (an Electron-main
> capability) are **not** built. This section is otherwise design intent.

### Operation limits

| Limit                               |     Default | Hard ceiling |
| ----------------------------------- | ----------: | -----------: |
| Active grants per product workspace |           8 |           16 |
| Mounted grants per run              |           4 |            8 |
| Virtual path depth                  | 64 segments |           64 |
| UTF-8 text returned per read        |       1 MiB |        4 MiB |
| Byte-stream file size               |     100 MiB |      512 MiB |
| Directory entries per list          |       2,000 |       10,000 |
| Glob/grep files scanned per call    |      10,000 |       25,000 |
| Glob/grep logical bytes scanned     |     256 MiB |        1 GiB |
| Glob/grep matches returned          |       1,000 |        5,000 |
| Glob/grep wall time                 |         5 s |         15 s |
| Inline model write/edit             |       1 MiB |        4 MiB |
| Artifact-backed write/patch file    |     100 MiB |      512 MiB |
| Mutated paths per run               |         200 |        1,000 |
| Concurrent reads per run            |           8 |           16 |
| Concurrent mutation per grant       |           1 |            1 |
| Total host bytes read per run       |     256 MiB |        2 GiB |
| Total host bytes written per run    |     512 MiB |        4 GiB |

Deployment policy may lower defaults. Model/renderer input cannot raise them.
Recursive traversal skips nothing silently: hitting a limit returns a partial
flag plus `workspace_quota_exceeded`, and mutation calls make no partial
change.

### Sensitive paths

Grant creation rejects a root that is the filesystem/volume root, the user's
home/profile root, Electron `userData` or its ancestor, browser/keychain
stores, OS directories, or a known credential-store root. Within an accepted
root:

- `.ssh`, `.gnupg`, `.aws`, `.azure`, browser-profile/keychain directories,
  private-key/certificate stores, and 0xCopilot app-data aliases are
  non-overridable denies;
- `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, credential files, and likely
  secret/config files require a per-file read approval and produce no inline
  preview;
- secret policy can be stricter by deployment but cannot be weakened by model
  input or a generic tool approval.

### Watches

Watches are an Electron-main/native-helper capability, not a model tool:

- At most four watched roots per run and eight per app.
- Notifications contain virtual path, `created|modified|removed|renamed`,
  watcher generation, and monotonic broker sequence—never bytes or physical
  paths.
- Events coalesce for 250 ms. The per-watch queue is 2,048 events and the
  aggregate rate is 10,000 events/minute.
- Native overflow/coalescing ambiguity/root identity change emits one
  `rescan_required`, clears path caches, and suppresses detailed events until a
  bounded rescan completes.
- Watch state is RAM-only and closes on run completion, revoke, offline root,
  sign-out, broker restart, app quit, or 30 minutes idle.
- A watch invalidates cached hashes/listings and updates visible status. It does
  not authorize an operation, trigger an agent turn, resume a paused run,
  approve a mutation, or prove a file did not change.
- Every subsequent operation reopens/revalidates by handle and checks its own
  precondition. Correctness never depends on receiving every watch event.

## Trust and security model

### Trusted and untrusted actors

Trusted within the product boundary:

- signed Electron main and native helper;
- supervised AI API/worker binaries;
- verified product identity and main-minted broker/run contexts;
- current OS user as the outer local account boundary.

Untrusted until validated:

- renderer/preload messages;
- model output and tool arguments;
- virtual paths, glob/grep patterns, content, filenames, and MIME;
- caller-supplied org/user/workspace/run/grant/approval fields;
- persisted records after external modification or unclean shutdown;
- host directory contents, including files changed by another application;
- prompt instructions inside workspace files.

### Required threat controls

| Threat                          | Required control                                                       | Evidence                         |
| ------------------------------- | ---------------------------------------------------------------------- | -------------------------------- |
| Renderer compromise             | No Node/path/token; allowlisted strict IPC; native picker/confirmation | BrowserWindow/preload/IPC tests  |
| Model creates/expands grant     | No picker operation in AI broker; main-only grant events               | Broker operation allowlist tests |
| Forged identity/grant           | AC1 audience token + opaque run context + main snapshot                | Cross-run/user/workspace tests   |
| Traversal/encoding alias        | Strict virtual parser and segment-based native API                     | Path corpus                      |
| Symlink/junction/reparse escape | No-follow handle traversal and type denial                             | macOS/Windows adversarial tests  |
| Ancestor/final-component race   | Root-relative handle opens, identity recheck, handle-based effect      | Race-swap stress tests           |
| Mount/volume escape             | Stable root/volume identity; deny descendant volume transitions        | Mount-point tests                |
| Hard-link alias                 | Deny hard-linked regular files                                         | Link-count tests                 |
| Stale overwrite                 | Approved expected hash + prepared physical identity + commit recheck   | Conflict matrix                  |
| Duplicate side effect           | Durable idempotent journal and postcondition reconciliation            | Kill/retry matrix                |
| Delete through no-delete grant  | Explicit mode matrix; internal same-file replace only                  | Mode conformance                 |
| Approval expands authority      | Approval digest intersection; main independently authorizes            | Edited/replayed approval tests   |
| Secret file exfiltration        | Root deny rules, sensitive-file approval, no preview, budgets          | Secret corpus                    |
| Prompt injection in a file      | File text is data; grants/policy/approvals remain environmental        | Malicious workspace fixture      |
| Unbounded scan/watch            | File/byte/time/match queues and cancellation                           | Load/overflow tests              |
| Python/Deep Agents bypass       | Broker-only backend; production bans on host backends/imports          | Static/configuration tests       |

The broker is a capability boundary against renderer/model/tool misuse, not a
kernel sandbox against compromise of Electron main, the native helper, the
trusted worker, same-user malware, or an administrator. Full generated code
still goes to AC6/AC7; AC5 does not claim Cowork-style VM containment.

### Sensitive-workflow accountability

| Workflow                  | Who initiates/approves                            | What changes                                       | Durable evidence                                          | Retention/deletion                                                      |
| ------------------------- | ------------------------------------------------- | -------------------------------------------------- | --------------------------------------------------------- | ----------------------------------------------------------------------- |
| Create/expand grant       | Interactive signed-in user in native picker/sheet | Encrypted grant authority                          | Main grant event + capability audit                       | Revoke removes authority; evidence follows security policy/hold         |
| Downgrade/revoke          | User/admin policy                                 | Future authority and active contexts               | Grant event + affected-run IDs                            | Physical grant material deleted after account/workspace deletion policy |
| Read sensitive file       | Model proposes; user approves exact path          | No host change; content may enter context/artifact | Tool/approval/read audit with digest/bytes                | Raw outputs follow AC4/context retention                                |
| Write/edit/mkdir          | Model proposes; policy/user approves as required  | User file/directory                                | Runtime event + main intent/audit + optional AC4 preimage | Intent 30 days; preimage AC4 policy                                     |
| Delete/move/restore/patch | Model proposes; user approves exact effect        | User filesystem                                    | Per-call approval, intent/postcondition, preimage, result | Same as above; user file remains user-owned                             |

Local audit is tamper-evident evidence only if it uses the repository's audit
chain/export path. AC5 does not claim immutable or SIEM-complete audit from a
mutable local log.

## Observability and audit

### Structured events

- `workspace.grant_picker_opened`
- `workspace.grant_created`
- `workspace.grant_expanded`
- `workspace.grant_downgraded`
- `workspace.grant_revoked`
- `workspace.grant_offline`
- `workspace.run_snapshot_created`
- `workspace.operation_started`
- `workspace.operation_denied`
- `workspace.mutation_prepared`
- `workspace.preimage_committed`
- `workspace.mutation_committing`
- `workspace.mutation_committed`
- `workspace.mutation_conflict`
- `workspace.mutation_outcome_unknown`
- `workspace.restore_committed`
- `workspace.watch_started`
- `workspace.watch_overflow`
- `workspace.watch_stopped`
- `workspace.intent_reconciled`

Fields include verified workspace/user/run/task/tool IDs, opaque grant/mount,
mode, operation class, virtual-path HMAC or redacted virtual path, approval ID,
policy/snapshot/request digest, before/after digest, file/byte/match counts,
duration, quota, watcher generation, result, safe error, and correlation IDs.

Logs/metrics exclude physical paths, directory/file names where not required,
file bytes, previews, glob/grep content, broker/run/precondition/stream tokens,
native handles/IDs, attachment data, and AC4 preimages.

### Metrics

- `desktop_workspace_grants{mode,status}`
- `desktop_workspace_grant_changes_total{action,outcome}`
- `desktop_workspace_operations_total{operation,outcome}`
- `desktop_workspace_operation_seconds{operation}`
- `desktop_workspace_bytes_total{direction,operation}`
- `desktop_workspace_denials_total{reason}`
- `desktop_workspace_precondition_conflicts_total{operation}`
- `desktop_workspace_mutation_intents{state}`
- `desktop_workspace_intent_reconciliation_total{outcome}`
- `desktop_workspace_snapshot_failures_total{reason}`
- `desktop_workspace_native_helper_failures_total{platform,operation}`
- `desktop_workspace_watch_events_total{kind}`
- `desktop_workspace_watch_overflow_total{platform}`
- `desktop_workspace_quota_exceeded_total{quota}`

Physical/virtual paths, grant IDs, digests, users, and runs are not metric
labels.

### Audit

Main capability audit answers:

- who created/expanded/downgraded/revoked a grant and under which verified
  product workspace;
- which mode/snapshot authorized an operation;
- who approved the exact mutation or sensitive read;
- which virtual path HMAC, operation, before/after digest, and byte count
  changed;
- where the AC4 preimage and runtime event are referenced;
- whether commit/reconciliation succeeded, conflicted, or is unknown; and
- when grant material, intent evidence, and preimages expire/delete/hold.

It contains no physical path or content. User-facing activity may show the
safe virtual path/display name; exportable security audit uses a keyed path
digest to reduce filename disclosure.

## Comprehensive test plan

### Contract and unit tests

- AC1 Pydantic/Zod fixtures for grants, broker envelope, operations, entries,
  intents, stream tickets, results, and stable errors.
- Reject unknown fields/enums, malformed UUIDs/timestamps, unsafe integers,
  missing approval/preconditions, mismatched hashes/sizes, and secret fields in
  repr/model dumps.
- Exhaustive mode × operation matrix, including internal same-file replacement
  and every denied rename/delete case.
- Policy `auto/ask/require/block` × mode × sensitive/risk floor matrix.
- Approval digest changes for any path, operation, hash, size, batch, policy,
  snapshot, broker instance, or expiry change.
- Idempotency same-key/same-request and same-key/different-request behavior.
- `FilePickerPort` cannot return a directory grant; `WorkspaceGrantPickerPort`
  cannot return file bytes or a physical path.

### Deep Agents and runtime integration

- `BrokeredWorkspaceBackend` passes pinned `BackendProtocol` conformance for
  sync/async list/read/write/edit/glob/grep.
- Composite routing preserves default, `/drafts/`, `/subagents/`,
  `/large_tool_results/`, and `/workspace/` behavior with no prefix escape.
- Desktop file tools are absent outside the desktop profile/feature gate or
  without a healthy broker/run snapshot.
- Model writes outside `/workspace/` and `/drafts/` are denied.
- mkdir/delete/move typed tools traverse the same policy/approval/budget/event/
  audit service as backend writes/edits.
- Revoke/downgrade/sensitive-policy change while waiting for approval causes
  commit denial.
- Existing direct/MCP tool approvals, subagent approval routing, citations,
  budgets, SSE, and event presentation remain correct.

### Virtual-path and native security corpus

- Empty/double separators, `.`, `..`, percent encodings, backslash, NUL,
  controls, bidi, noncharacters, overlong path/segment/depth, invalid UTF-8,
  NFC/NFD collisions, and case collisions.
- POSIX absolute paths, tilde, environment syntax, file URLs.
- Windows drive-relative/absolute, UNC, `\\?\`, `\\.\`, ADS, reserved devices,
  trailing dot/space, short names, mixed separators, and Unicode lookalikes.
- Symlink at every ancestor/final component, symlink root, Finder alias,
  junction, mount point, volume reparse, cloud placeholder, unknown reparse
  tag, hard link, FIFO/socket/device, sparse file, and root-volume swap.
- Race threads replace every ancestor/target/destination between parse,
  prepare, snapshot, staging, commit, rename, delete, and postcondition.
- Prove no test ever reads/writes outside the selected physical root.

### Mutation and crash injection

Kill Electron main, native helper, and worker:

- before/after prepared journal `fsync`;
- while streaming/finalizing AC4 preimage;
- before/after content staging `fsync`;
- before/after `committing` journal;
- immediately before/during/after native effect;
- before/after postcondition verification;
- before/after committed journal;
- before worker event append/acknowledgement; and
- during multi-file patch compensation.

On restart, every intent must converge to committed, safe-to-retry/aborted,
conflict, or visible outcome-unknown with at most one effect. Missing preimage
must prevent a not-yet-started effect.

Test create, overwrite, edit with zero/one/multiple anchors, mkdir, file delete,
empty/non-empty directory delete, move, restore, patch, stale destination,
same-content write, and duplicate request.

### macOS platform tests

- APFS case-insensitive and case-sensitive volumes; HFS+ where CI hardware
  supports it.
- `openat` no-follow traversal, `st_dev`/`st_ino` identity, mounted disk image
  below root, root rename, volume eject, Unicode normalization, long paths,
  permissions, atomic replace, parent `fsync`, and app restart.
- FSEvents coalescing, dropped flags, root change, event flood, and rescan.
- Hardened/notarized arm64 and x64 helper loading/signature.
- An App Sandbox entitlement causes explicit AC5 disable, not a silent
  string-path fallback.

### Windows platform tests

- NTFS and ReFS where CI supports it; unsupported/UNC/removable/network roots
  fail as specified.
- Relative-handle traversal, file ID/volume identity, junction/reparse/cloud
  placeholder denial, ADS/devices/trailing aliases, case behavior, long paths,
  ACL inheritance/preservation, atomic replacement, handle-based rename/delete,
  root move, and volume removal.
- `ReadDirectoryChangesW` rename pairs, buffer overflow, root loss, and rescan.
- Signed x64 native helper packaging; wrong architecture/version/signature
  fails capability readiness.

### Quota, watch, and load tests

- Every quota at minus one, exact, and plus one; deployment may lower but not
  raise hard ceilings.
- Cancellation interrupts scans/streams promptly, closes handles, removes
  staging, and leaves no mutation.
- 10,000-file scans, 1 GiB hard-ceiling scans, 512 MiB streaming writes, and
  concurrent run pressure stay within memory/handle/event-loop budgets.
- Watch events invalidate caches but never trigger an agent action or authorize
  stale content.
- Repeated grant/revoke/run/app restart leaves no handles, watchers, staging
  files, ports, or usable tokens.

### Renderer, service-boundary, and regression tests

- Renderer/preload cannot obtain physical paths, native handles/IDs, broker
  URL/token, run context, precondition token, stream ticket, or AC4 path.
- Renderer IPC exposes only user-lane grant management; no arbitrary path or
  AI broker operation.
- AI workspace modules have no direct host `open`/`os`/`pathlib`/`shutil`/
  `subprocess` implementation and do not import Electron code.
- Production dependency/configuration scans prove `FilesystemBackend` over a
  host root and `LocalShellBackend` are unreachable.
- No sibling deployable `src` import or shared business-logic package is added.
- Existing web frontend, backend facade, Postgres adapters/migrations, public
  APIs, SSE reconnect, attachment picker, desktop auth, and adapter suites pass
  unchanged.
- Non-desktop profiles reject AC5 settings even if the feature flag is set.

Normal PR CI uses fake broker/native ports plus local temporary roots. It
requires no live LLM, network, production secret, or privileged system path.
Packaged macOS/Windows security suites are release-gate jobs.

## Rollout and backout

### Rollout

1. Land strict contracts, fake broker/native helper, operation service, and
   tests with `RUNTIME_ENABLE_DESKTOP_FILESYSTEM=false`.
2. Package/sign the native helper and enable picker/grant persistence only;
   no model tools.
3. Enable read-only mounts for internal users with one root/run, no sensitive
   files, and conservative scan limits.
4. Enable watches for cache invalidation after overflow/rescan evidence.
5. Enable create/write/edit/mkdir under `read_write_no_delete` after AC4
   preimage, approval, idempotency, and crash tests.
6. Enable delete/move/restore under `read_write` after per-call destructive
   approval and platform reconciliation drills.
7. Enable AC7 snapshot/patch and AC8 upload/export consumers independently.
8. AC10 owns wider canary/default rollout, repair UX, quota controls, and
   support policy.

Stop conditions include any path escape, physical-path/token leak, renderer or
Python bypass, unapproved effect, mode escalation, duplicate mutation, missing
required preimage, unexplained outcome, unsafe native fallback, watch-triggered
agent action, orphan handle/staging data, cross-workspace grant confusion, or
web/Postgres regression.

### Backout

- Set `RUNTIME_ENABLE_DESKTOP_FILESYSTEM=false`; remove workspace tools/routes
  from new runs and reject new mutation prepares.
- Close watches/stream tickets, abort prepared/content-staged intents, and
  reconcile every `committing` intent before stopping the broker/helper.
- Preserve encrypted grants as disabled/needs-reauthorization and preserve
  intent/audit/AC4 history under retention. Never delete or modify user files
  during backout.
- Existing chat/tool events and AC4 preimages remain readable. Restore remains
  unavailable until a compatible AC5 version is re-enabled; it never falls
  back to Python/renderer filesystem access.
- A native helper protocol/version/signature mismatch disables AC5 only. It
  does not roll back conversations or artifacts.
- Re-enable the same compatible version by revalidating roots and asking the
  user to authorize new run snapshots.

There is no migration to `FilesystemBackend`, `LocalShellBackend`, browser
storage, or remote sandbox mounts.

## Acceptance criteria

### Delivered (light) — slices 1 + 2

- Only a main-owned native picker creates a root grant; the renderer cannot open
  it and the AI broker cannot open native pickers.
- Exactly three modes exist; read gating is enforced in main via `modeSatisfies`
  (fail-closed for an unknown mode).
- Physical paths stay main-only; the renderer receives only a path-free
  `RendererGrant`, and broker read results carry only grant-root-relative virtual
  paths.
- The authenticated loopback broker enforces the per-boot bearer, protocol
  header, POST/JSON body cap, and no-CORS browser-metadata rejection; a revoked
  grant fails closed on the next op.
- Traversal, symlink, reserved-name, ADS, and (on darwin, atomic) race corpora
  cannot escape the grant root; resolve-before-authorize + no-follow open +
  post-open recheck is applied on every op.
- Read quotas (`FS_LIMITS`) bound each op before unbounded work.
- The renderer has no Node/host API; the delivered capability code adds nothing
  to non-desktop profiles.

### Deferred / not in the light build

- Grant **expansion** through native confirmation; the opaque
  `/workspace/<26-char mount>/...` addressing (delivered uses `grant_id` +
  relative path).
- `BrokeredWorkspaceBackend` passing the pinned `BackendProtocol` suite behind
  `CompositeBackend`; the `/workspace/` route.
- `mkdir`/`delete`/`move` typed tools; every existing-content mutation carrying a
  checksum-matching AC4 `file_history` preimage; prepared/staged/committing/
  committed journal semantics and crash idempotency.
- Approval-digest binding and its edit/replay/revocation invariants.
- The signed native helper's descriptor-/root-handle-relative operations
  (delivered is pure-Node; **Windows is non-atomic**).
- Watches; per-run/per-workspace byte quotas; sensitive-path policy (**G2**).
- The AI-runtime side (Python broker client, workspace backend/tools) — nothing
  calls the broker yet — and the `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` feature gate
  (**G4**). A physical-root leak (**G1**) is being fixed separately.

## Definition of done

### Delivered (light) — slices 1 + 2

- Native picker, `safeStorage` grant store, path-free renderer IPC, authenticated
  loopback broker, and read FS ops (`stat`/`list`/`read`/`glob`/`grep`) with the
  full syntactic + resolve-before-authorize + atomic-open path-validation layer
  are implemented in `apps/desktop/main/capabilities/`.
- Covered by `broker.test.ts`, `folder-picker.test.ts`, `grant-store.test.ts`,
  `host-fs.test.ts`, `path-validation.test.ts`, and `service.test.ts` (incl. the
  darwin atomic-swap-denial and non-darwin post-open-recheck cases).

### Deferred / not in the light build

- The lead implementation spec (broker/native protocol, Rust/Node toolchain,
  supported OS/filesystems, syscalls, journal crypto, packaging/signing).
- Run snapshots, mutation journal/reconciliation, the signed native helper,
  watcher, AI client/backend/tools, policy/approval integration, AC4 history,
  `workspace.*` events, `desktop_workspace_*` metrics, and capability audit.
- The cross-language contract, Deep Agents conformance, crash/idempotency,
  quota/watch, macOS/Windows platform, and packaging suites; the recorded
  commit-boundary/outcome-unknown/root-move/native-helper-disable/backout drills.
- Desktop user/support docs; the security review sign-off; signed packaged
  artifacts.

## Critical current and proposed files

### Delivered — actual files

All under `apps/desktop/main/capabilities/` (flat, not a `workspace/` subtree):

- `index.ts` — `createCapabilityService` composition root + public exports.
- `service.ts` — `CapabilityService` (picker/store/broker composition; renderer-safe returns).
- `folder-picker.ts` — `FolderPicker` (main-only native directory picker).
- `grant-store.ts` — `GrantStore` (`safeStorage`-encrypted grants at `<userData>/capabilities/grants.bin`).
- `broker.ts` — `CapabilityBroker` (authenticated loopback HTTP; handshake/grants/fs read routes).
- `host-fs.ts` — `HostFs` (read ops + atomic no-follow open + TOCTOU recheck).
- `path-validation.ts` — pure syntactic validation, `FS_LIMITS`, `assertWithinRoot`, `modeSatisfies`, `FsError`.
- `channels.ts`, `schemas.ts` (Zod), `types.ts` — IPC channels, boundary schemas, and grant/read-result types.
- Tests: `broker.test.ts`, `folder-picker.test.ts`, `grant-store.test.ts`, `host-fs.test.ts`, `path-validation.test.ts`, `service.test.ts`.

### Deferred / proposed (not built)

Desktop side — none of these exist (the delivered flat files above replace the
proposed `workspace/` subtree for slices 1–2):

- `apps/desktop/main/capabilities/workspace/{grant-picker,grant-policy,run-snapshots,workspace-broker,mutation-journal,mutation-reconciler,watch-manager,protocol-v1,errors}.ts`
- `apps/desktop/native/workspace-fs/*` (the signed Rust N-API helper: `Cargo.toml`, `src/{lib,macos,windows,contracts}.rs`, `tests/`)
- `packages/chat-surface/src/ports/WorkspaceGrantPickerPort.ts`, `packages/chat-transport/src/ipc/workspace-grants.ts`
- `apps/desktop/docs/specs/agent-capabilities/ac5-filesystem-capability.md`, `apps/desktop/docs/workspace-access.md`

AI-runtime side — none of these exist (nothing on the Python side calls the
broker yet):

- `services/ai-backend/src/agent_runtime/capabilities/workspace/{__init__,contracts,ports,service,policy,tools}.py`
- `services/ai-backend/src/agent_runtime/capabilities/backends/workspace_backend.py`
- `services/ai-backend/src/agent_runtime/capabilities/desktop/client.py`
- `services/ai-backend/tests/contract/desktop_broker/test_workspace_protocol.py`, `tests/contract/backends/test_workspace_backend.py`, `tests/unit/agent_runtime/capabilities/workspace/`, `tests/integration/runtime_worker/test_workspace_mutations.py`
- `docs/contracts/desktop-broker/v1/workspace-{valid,invalid}.json`, `services/ai-backend/docs/features/desktop-workspaces.md`

No implementation may add a sibling component import, place business logic in
`packages/service-contracts`, expose the native helper to preload, or add a
generic filesystem/shell method.

## Unresolved risks

There are no open implementation choices in AC5. Accepted residual risks are:

- Electron main, the signed native helper, and the trusted AI worker run as the
  same OS user. Compromise of those trusted components or same-user malware can
  bypass this product capability boundary outside Copilot.
- `NtCreateFile` root-handle behavior is a low-level Windows dependency. AC5
  pins supported Windows versions and fails closed if the helper cannot prove
  required semantics; there is no string-path fallback.
- Denying reparse/cloud-placeholder files, network roots, hard links, and
  unsupported filesystems excludes some OneDrive, enterprise share, and
  developer workflows. V1 prefers explicit unavailability over unverifiable
  path containment.
- Native watchers are lossy. They are advisory only; operation-time handle and
  hash checks remain authoritative.
- Another local application can legitimately modify a file between approval
  and commit. Optimistic conflicts are expected user-visible outcomes, not
  errors to overwrite around.
- AC4 file history restores bytes, not every platform-specific ACL, extended
  attribute, alternate stream, or external application state. Same-file
  replacement preserves supported metadata, but AC5 does not claim a complete
  filesystem backup.
- A user can intentionally grant a folder containing sensitive information.
  Sensitive-path controls and approval reduce accidental exposure but cannot
  classify every secret.

These risks do not authorize direct renderer/Node access, direct Python host
I/O, unrestricted Deep Agents filesystem/shell backends, implicit grants,
string-only path authorization, unverified race fallback, recursive delete, or
approval-based grant expansion.
