# AC2 — File-native session store (LIGHT)

| Field             | Value                                                                                  |
| ----------------- | -------------------------------------------------------------------------------------- |
| Spec ID           | AC2                                                                                    |
| Status            | Draft; decision-complete and implementation-ready                                      |
| Wave              | 1 — Durable primitives                                                                 |
| Estimated effort  | L — 8–12 engineer-days, including migration and cross-platform load/corruption testing |
| Dependencies      | AC1 desktop capability foundation                                                      |
| Required for      | AC3 runtime recovery, AC4 offload wiring, AC6 code mode, AC10 hardening                |
| Primary owner     | `services/ai-backend` persistence and runtime adapters                                 |
| Supporting owners | Desktop supervisor, retention, audit, QA                                               |
| Web impact        | None                                                                                   |

> **Design variant: LIGHT (single-writer).** This PRD is the resolved, LIGHT
> file-native store selected when [overview §25](00-overview.md#25-alternatives-considered)
> was closed. The desktop runs **one** in-process worker
> (`RUNTIME_START_IN_PROCESS_WORKER=true`), and subagents are in-process async
> tasks (`await subagent.ainvoke(...)`), so there is **exactly one writer
> process** for the store. That single-writer fact removes the entire
> cross-process crash-consistency machinery an earlier draft carried
> (copy-on-write generations + `CURRENT`, cross-process advisory locks, WAL-style
> batch commit markers, tail quarantine/repair, per-stream hash chains,
> two-way PostgreSQL migration authority). The prior-art baseline is Claude
> Code, which stores sessions as plain append-only JSONL with no database, WAL,
> or lock protocol. The heavy machinery is only needed **if** the worker later
> becomes a separately supervised process (two writers); that path is
> **optional/deferred** and specified in [AC3b](03-ac3-runtime-recovery.md) — it
> is not built for the light store.

> **Contract alignment.** AC1 is the single normative source and now defines the
> LIGHT shapes directly: the record envelope `FileSessionRecordV1` (§7.4) and the
> flat `events.jsonl` session layout (§8). AC2 imports those unchanged and adds
> the append/durability, projection, retention, and migration semantics below.
> Every other AC1 frozen contract — the broker handshake/headers (§6.3), grant
> modes (§7.3), `ArtifactRefV1` (§7.5), the activation predicate (§6.2) — is also
> imported unchanged.

## Problem and why now

The desktop AI runtime currently selects PostgreSQL persistence and supervises an API process with an in-process worker. That arrangement does not meet the desktop-agent roadmap's file-native contract:

- a user cannot inspect or export one session as a self-contained set of ordinary files;
- subagent transcripts are not independently addressable;
- the AI runtime depends on the configured legacy desktop AI-runtime PostgreSQL store even though the product target is a local, file-native session substrate;
- the existing in-memory alternative loses history and queue state on process exit; and
- SQLite would be convenient for search and queue claims, but making SQLite canonical would recreate an opaque database rather than the required plaintext source of truth.

The current ports are the right seam. `PersistencePort`, `EventStorePort`, `RuntimeQueuePort`, the lifecycle port, and the satellite persistence ports already let API and worker code avoid depending on PostgreSQL directly. AC2 adds a desktop-only implementation of those ports backed by append-only JSONL, a content-addressed object store, and a disposable SQLite index. It does not fork the runtime domain, change public HTTP/SSE contracts, or replace PostgreSQL for web and hosted deployments.

Because the desktop is a **single-writer** deployment (one in-process worker; subagents are in-process async tasks), the store's durability contract is the one Claude Code already ships in production: append a validated JSON line, `fsync` when the write must survive a crash, and ignore a torn trailing line on load. There is no second writer to coordinate with, so there are no cross-process locks, commit markers, or generation pointers.

## Goals

- Make canonical desktop session state ordinary UTF-8 JSONL under the AC1 storage root.
- Store each parent-session record in one main `events.jsonl` stream and each subagent record in one per-subagent `subagents/<task>.jsonl` stream. Each event is written to exactly one file.
- Persist every `RuntimeEventEnvelope` exactly once, preserving its existing per-run `sequence_no` so SSE replay/reconnect behavior is unchanged.
- Store large tool results, checkpoints, screenshots, downloads, and attachments once in a workspace-scoped, SHA-256-addressed `objects/sha256/` object store, and keep bounded typed references (AC1 `ArtifactRefV1`) in JSONL. This object-store primitive is the AC2 foundation that AC4 wires offload into.
- Serialize all appends for one conversation behind a single in-process, per-conversation `asyncio.Lock` — the correct and sufficient concurrency control for one writer process.
- Treat SQLite, FTS, query tables, and queue rows as disposable materialized views rebuildable by scanning JSONL.
- Model the store surface on Anthropic's Agent SDK `SessionStore` contract — `append` / `load` / `list` / `delete` / `listSubkeys` (subkeys are subagent tasks) — so the shape maps cleanly onto documented prior art (<https://code.claude.com/docs/en/agent-sdk/session-storage>).
- Implement the complete runtime persistence port surface for `RUNTIME_STORE_BACKEND=file`; no method may silently fall back to memory or raise `NotImplementedError`.
- Select the file adapter only for `ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop` with a valid AC1 storage grant.
- Preserve the PostgreSQL adapter, schemas, migrations, event notification, and deployment behavior for every non-desktop profile.
- Define migration, retention, deletion, legal-hold, export, and backout behavior before data is written.

## Non-goals

- Replacing PostgreSQL in SaaS, self-hosted web, test environments that explicitly select PostgreSQL, or any non-desktop deployment profile.
- Making SQLite, FTS, a cache, an in-memory map, or an Electron renderer store authoritative.
- Building cross-process crash-consistency machinery — copy-on-write generations, `CURRENT` pointers, cross-process advisory locks (`flock`/`LockFileEx`), WAL-style batch commit markers, tail quarantine/repair, or per-stream hash chains. These are only required for a **separate-process** worker and are specified as optional/deferred in AC3b; they are not built here.
- Storing MCP OAuth tokens, provider API keys, broker tokens, capability grants, or OS secret-storage material in session JSONL or the object store. Secrets stay in the backend `TokenVault` / Electron `safeStorage`, never in plaintext folders.
- Encrypting canonical session JSONL at the application layer. AC2 is intentionally plaintext and relies on the local account boundary and full-disk encryption; the limitation is disclosed below.
- Owning the offload decision and `ArtifactRefV1` typing. AC2 provides the content-addressed object-store primitive; **AC4 wires** `ContextPayloadManager`/`OffloadWriter`/`ManagedContextPayload` onto it and owns the `/large_tool_results/` route.
- Implementing durable LangGraph resume, Monty snapshots, worker leases, or parent/subagent reconciliation. AC3 owns those behaviors on top of this store; the graph checkpointer reuses LangGraph `SqliteSaver` (AC3).
- Allowing arbitrary file roots, network shares, removable media, symlink traversal, or caller-supplied physical paths.

## User experience and failure behavior

### Normal behavior

1. The user creates or opens a chat through the existing facade routes.
2. 0xCopilot creates a session directory under the broker-approved desktop storage root.
3. User messages, run state, approvals, runtime events, subagent state, references, queue transitions, retention metadata, and terminal outcomes append to plaintext JSONL.
4. Parent records are in `events.jsonl`. A record belonging to subagent task `T` is in the one JSONL file keyed for `T`.
5. Conversation lists, message reads, event replay, search, and queue claims use the SQLite projection after it has caught up to the tail of the JSONL files.
6. Deleting the SQLite catalog does not delete user history. The next startup rebuilds it by scanning the JSONL files.
7. Export can copy the conversation directory plus referenced objects without translating an opaque database.

### Failure behavior

- A torn or partial final line (a crash mid-append) is ignored on load: an append is acknowledged only after the line and its required `fsync` complete, so an unterminated trailing line was never acknowledged and is safely discarded. Earlier lines are never rewritten.
- A retry after an uncertain append result returns the already-persisted logical record when its idempotency identity matches; it does not append a duplicate.
- Interior corruption (an invalid line that is not the final line, or a byte flip inside committed history) makes that conversation read-only and surfaces a safe “This chat needs repair” state with a diagnostics/export action. AC2 does not skip the bad line or manufacture replacement state. Raw content and paths are not placed in logs.
- If SQLite is missing or fails an integrity check, it is moved aside and rebuilt by scanning JSONL. Reads may use a bounded JSONL scan while the projection catches up; the worker is not declared ready until the queue projection is complete.
- Disk-full, read-only volume, flush failure, permission failure, or object-write failure returns a typed retryable storage error before success is reported. The UI keeps the composer draft and does not pretend the message was sent.
- FTS unavailability disables search only. It does not block direct conversation reads.
- If the AC1 gate or storage grant is absent, the file backend fails startup. It never selects an arbitrary current working directory or falls back to PostgreSQL/in-memory.

## Alternatives considered

### Keep the configured legacy desktop AI-runtime PostgreSQL store

Rejected as the target. It is durable, but it is not file-native per session, requires a database lifecycle for AI history, and cannot provide the roadmap's inspectable main/subagent transcript contract. It remains the migration source and a time-bounded backout target.

### Keep PostgreSQL canonical and emit JSONL only as an export/backup format

Considered and rejected when overview §25 was closed. The light store proves that a file-native canonical store is cheap on a single-writer desktop — plain append-only JSONL with no bespoke durability engine — so the inspectable-and-portable goal is met by the canonical store itself, and an "export-only" second format would be redundant machinery on top of Postgres that still leaves history trapped in the database day-to-day. See overview §25.

### SQLite as the canonical store

Rejected. SQLite is excellent for indexing, FTS, and serialized claims, but a single database file is not the required user-inspectable session format. Corruption or schema drift would also couple recovery to one derived database. SQLite is therefore rebuildable and disposable.

### Heavy cross-process durability (generations, `CURRENT`, advisory locks, commit markers, tail quarantine, hash chains)

Rejected for the light store. That machinery exists to make a **multi-writer** append safe when two OS processes can race on the same files. The desktop has one writer process, so a per-conversation `asyncio.Lock` plus append-and-`fsync` is sufficient and matches Claude Code's shipped behavior. The heavy design is retained only as the **optional/deferred** AC3b path for a future separately-supervised worker.

### One JSONL file for the whole desktop

Rejected. It couples unrelated conversations during read/compaction/deletion, makes export expensive, and prevents independent subagent transcript access. One directory per conversation is the unit.

### One JSONL file per run

Rejected. Conversations span runs, approvals and messages are conversation-scoped, and cross-turn context needs a stable session container. Per-run files also complicate retention and conversation export.

### Duplicate subagent events into both parent and child streams

Rejected. Duplication makes “exactly once” ambiguous and requires conflict resolution during replay. Every record has one physical owner: parent lifecycle/linkage events in `events.jsonl`, subagent-internal events in that task's `subagents/<task>.jsonl`.

### Rewrite the complete session file on each append

Rejected. Atomic whole-file rewrite per append is simple but produces quadratic I/O, excessive SSD wear, and poor streaming latency. AC2 appends for normal writes and reserves temp-file-plus-atomic-rename for occasional compaction, retention, and deletion only.

### OS file watching as the canonical event bus

Rejected. Watch APIs can coalesce or drop signals and have platform-specific failure modes. JSONL replay is canonical. Because the API and worker share one process, SSE subscribers are woken in-process directly after the append; no cross-process notifier is required for the light store.

### Dual-write PostgreSQL and JSONL indefinitely

Rejected. Dual canonical stores create split-brain and undefined backout semantics. Migration is quiesced and verified; one backend is authoritative at a time.

## Architecture and SOLID ownership

### Component boundaries

```text
runtime API / in-process runtime worker (one process)
        |
        | PersistencePort / EventStorePort / RuntimeQueuePort /
        | existing satellite store ports
        v
RuntimeAdapterFactory
        |
        | backend == "file" and desktop gate is valid
        v
FileRuntimeStore
  ├── SessionJournal      append-only JSONL append/read, per-conversation asyncio.Lock
  ├── ObjectStore         content-addressed objects/sha256 put/get (AC4 wires offload)
  ├── CatalogProjection   rebuildable SQLite + FTS5, scanned from JSONL
  └── File satellite adapters   drafts, shares, ordinals, subagents, sources, queue
```

Electron does not read or write session files. It obtains and passes the AC1 storage grant and desktop profile to the supervised Python process. The AI runtime resolves logical IDs to opaque path keys through the storage-root adapter.

### SOLID mapping

- **Single responsibility:** `SessionJournal` owns JSONL append/read; `ObjectStore` owns content-addressed bytes; `CatalogProjection` owns derived query state; port adapters map domain calls to records.
- **Open/closed:** adding `file` is a new factory branch and adapter package. Existing domain coordinators and public routes stay unchanged.
- **Liskov substitution:** the common persistence contract suite must pass for in-memory, PostgreSQL, and file adapters. File-specific durability tests are additional, not substitutes.
- **Interface segregation:** domain consumers continue to receive the smallest existing port. Compaction and rebuild are lifecycle/maintenance services, not methods added to every domain port.
- **Dependency inversion:** no module under `agent_runtime` imports `pathlib`, SQLite, or `runtime_adapters.file`. The adapter depends on domain records and ports.

### Logical stream ownership

For a canonical session record:

- `task_id is None` means the line is written only to `events.jsonl`.
- `task_id is not None` means the line is written only to that task's `subagents/<task_id>.jsonl`.
- `parent_task_id` is lineage metadata inside the typed subagent payload and does not choose a file.
- a run may therefore have records in `events.jsonl` and several subagent files;
- merged session reads interleave records using the parent linkage events and, within a run, the per-run `sequence_no`; and
- per-subagent reads preserve the physical append order of that file.

No runtime event, message, tool invocation, approval, checkpoint reference, queue transition, or subagent result is copied into another stream for convenience. SQLite may denormalize it because SQLite is derived.

AC1's `workspace.jsonl` and `audit.jsonl` are separate canonical workspace-level append-only streams, never members of a session replay. Their records have `conversation_id=None`. They use the same encoding, immediate flush, and idempotency rules under the same single-writer discipline.

## Typed contracts

All new contracts use Pydantic v2 with `ConfigDict(extra="forbid", frozen=True)`. All datetimes are timezone-aware UTC and serialize with a `Z` suffix. All digests are lowercase 64-character SHA-256 hex. All integer bounds are enforced before allocation or file access.

### Canonical record envelope (light)

AC2 imports AC1's frozen `FileSessionRecordV1` (§7.4) unchanged and adds only
semantic validators; it does not fork that cross-wave contract. The flat,
single-writer envelope carries no `global_sequence_no`, no `previous_stream_hash`
chain, and no batch-commit-marker fields:

```python
class FileSessionRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    record_id: UUID
    record_kind: str = Field(min_length=1, max_length=96)
    org_id: str = Field(min_length=1, max_length=256)
    user_id: str | None = Field(default=None, max_length=256)
    conversation_id: str | None = Field(default=None, max_length=256)
    run_id: str | None = Field(default=None, max_length=256)
    task_id: str | None = Field(default=None, max_length=256)
    run_sequence_no: int | None = Field(default=None, ge=1)
    created_at: AwareDatetime
    payload: dict[str, JsonValue]
    record_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
```

Rules:

1. AC2 session records require `conversation_id` (a semantic validator over AC1's superset envelope); workspace-level records use AC1's separate `workspace.jsonl`/`audit.jsonl` ownership and set `conversation_id=None`.
2. Root `store_id` is generated once in `store.json` and is never accepted from an HTTP request. A domain record's `record_id` is UUIDv5 over `(store_id, record_kind, logical_id)`. A runtime event uses its `event_id` as the logical ID.
3. `org_id` and `user_id` come from verified runtime identity, never a payload or renderer assertion. AC1's lowercase-base32 SHA-256 scoped workspace/conversation path keys are location metadata and are not added to this envelope.
4. A serialized line, including the newline, may not exceed 1 MiB. Larger content must be redacted/truncated or represented by an AC4 `ArtifactRefV1` pointing at an `objects/sha256/` object.
5. The normative encoding is AC1's RFC 8785 canonical JSON UTF-8 with no insignificant whitespace, followed by exactly one `\n`. Duplicate keys, non-finite values, and host objects outside `JsonValue` are rejected; a language's ordinary `json.dumps` defaults are not the contract.
6. `task_id is None` selects `events.jsonl`; a non-null `task_id` selects `subagents/<task-key>.jsonl`. The line is written to exactly one of them.
7. Unknown `schema_version` or `record_kind` fails closed during active reads. A migration tool may decode a specifically registered older version.

The UUIDv5 algorithm is exact: use `store_id` as the UUID namespace and the UTF-8 RFC 8785 encoding of `["ac2-record-v1", record_kind, logical_id_parts]` as the name, where `logical_id_parts` is a typed JSON array in the record-kind registry. No delimiter concatenation or process-random UUID is allowed.

### Runtime event payload

```python
class RuntimeEventFilePayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    event: RuntimeEventEnvelope
```

`record_kind == "runtime.event"`. The adapter stores the existing wire envelope, not the draft, so `org_id` remains outside the user-visible event payload. The record's `run_sequence_no` equals `event.sequence_no`.

### Queue transitions

```python
class QueueTransitionKind(StrEnum):
    ENQUEUED = "enqueued"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTERED = "dead_lettered"


class QueueTransitionPayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    command_id: Annotated[str, Field(min_length=1, max_length=128)]
    command_type: Annotated[str, Field(min_length=1, max_length=96)]
    transition: QueueTransitionKind
    attempt: Annotated[int, Field(ge=0, le=100)]
    available_at: AwareDatetime
    command_body: dict[str, JsonValue] | None = None
    claim_id: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    worker_id_hash: Sha256Hex | None = None
    lease_expires_at: AwareDatetime | None = None
    safe_error_code: Annotated[str, Field(min_length=1, max_length=96)] | None = None
```

`command_body` is the existing command's canonical JSON form, is required only on `ENQUEUED`, is forbidden on every later transition, and must validate against the existing strict `RuntimeRunCommand`, `RuntimeCancelCommand`, or `RuntimeApprovalResolvedCommand` selected by `command_type`; its command, run, conversation, org, and user identities, wherever present, must match the containing transition/envelope. Its command `created_at` is `enqueued_at`; priority is deterministically projected from the versioned command-type registry, never caller input. Later records reference `command_id`. A `CLAIMED` record makes the attempt count rebuildable. AC3 lease renewals update only the disposable SQLite lease row; after a full rebuild every formerly active claim is intentionally recoverable while its persisted attempt count remains.

### Payload-removal tombstone

```python
class PayloadRemovalV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    removed_record_kind: str = Field(min_length=1, max_length=96)
    reason: Literal[
        "retention_expired",
        "superseded_internal_state",
    ]
    removed_at: AwareDatetime
```

Selective expiry of an eligible non-event internal record rewrites the conversation file by temp-file-plus-atomic-rename, replacing that record with a `record_kind="storage.payload_removed"` tombstone at the same `record_id`. This is the sole exception to deriving `record_id` from the current `record_kind`. A retained conversation's `runtime.event` records are never selectively tombstoned because that would break SSE replay; runtime events disappear only as part of a whole-session deletion.

## Sequence and exactly-once semantics

### Per-run event sequence (unchanged)

- `RuntimeEventEnvelope.sequence_no` remains scoped to `run_id`, starts at 1, and is contiguous across the parent and all subagent physical files for that run.
- `append_events_batch()` still requires one `run_id` and returns envelopes in input order with contiguous run sequence numbers.
- Run sequence allocation happens under the per-conversation `asyncio.Lock` that serializes the append.
- Storage records that are not runtime events have `run_sequence_no=None` and do not consume an SSE sequence.
- `list_events_after(after_sequence=N)` merges committed records for the run by `sequence_no` and returns only `N+1...`.

There is **no** session-global sequence counter. Cross-run and cross-file ordering for a merged conversation view is derived from parent linkage events plus per-run `sequence_no` and `created_at`; the single-writer append order within each file is stable. Dropping `global_sequence_no` removes the one field whose allocation previously required a global cross-file coordinator.

### Exactly once

- A record exists in one physical file selected by `task_id`; it is never copied into another file.
- `event_id`, `(run_id, run_sequence_no)`, and `record_id` are unique in the derived index and verified during scans.
- The file adapter creates the envelope/event ID once before its internal write retry loop. An uncertain post-flush error re-checks the tail (or the rebuilt index) by `record_id` and returns the persisted envelope when it is present; otherwise it re-appends.
- Reuse of a logical ID with a different canonical payload is an idempotency conflict and fails closed.
- Queue enqueue, messages, approvals, tool invocations, checkpoints, and terminal outcomes use their existing logical IDs to derive stable record IDs.

“Exactly once” here means one persisted canonical record and one replayed event for a logical append. It does not claim exactly-once execution of an external side effect; AC3 requires invocation idempotency or fail-closed reconciliation for that separate concern.

## Filesystem layout

All names in angle brackets use AC1's lowercase-base32 SHA-256 of the scoped identifier, never raw org, user, conversation, run, task, or object IDs. `<storage-root>` is exactly AC1's injected `RUNTIME_FILE_STORE_ROOT`, `<userData>/agent-data/v1`.

```text
<storage-root>/
├── store.json
├── workspaces/
│   └── <workspace-key>/
│       ├── workspace.jsonl
│       ├── audit.jsonl
│       ├── sessions/
│       │   └── <conversation-key>/
│       │       ├── events.jsonl                 # main conversation/run stream
│       │       └── subagents/
│       │           └── <task-key>.jsonl         # one file per subagent task
│       ├── objects/
│       │   └── sha256/                          # content-addressed bytes (AC4 wires offload)
│       │       └── <ab>/<cd>/<full-hex>
│       └── index/
│           ├── catalog.sqlite3                  # disposable projection + FTS5
│           ├── catalog.sqlite3-wal
│           └── catalog.sqlite3-shm
├── migrations/
└── tmp/                                          # same-volume staging for object put and compaction
```

There is no `CURRENT`, no `generations/`, no `pending/`, no cross-process `locks/`, and no `quarantine/` tree: the single writer appends directly to `events.jsonl`, so a session has exactly one physical copy of its history rather than a copy-on-write generation set. Object bytes are immutable and content-addressed; compaction and deletion operate on whole files, not in-place edits.

### Permissions and path safety

- Root/session directories are `0700` on macOS/POSIX; Windows DACLs grant only the current user and `SYSTEM`, exactly as AC1 freezes.
- Data files, SQLite, and objects are `0600` or that AC1 Windows DACL equivalent.
- Every open walks from a broker-approved root, uses opaque path keys, rejects symlinks/reparse points, and verifies the final resolved handle remains beneath the root.
- The implementation opens data files with no-follow semantics where the platform supports them and validates file identity after open where it does not.
- Network paths, removable paths, case-colliding keys, alternate data streams, reserved Windows device names, and path components outside the generated alphabet are rejected.

## Append and durability protocol

Because there is one writer process, an append is a plain serialized in-process operation:

1. Validate the domain payload and determine its target file (`events.jsonl` or a subagent file) without touching disk.
2. Acquire the per-conversation `asyncio.Lock`. All appends for one conversation are serialized; there is no cross-process lock and no lock file.
3. Reject or return any already-persisted logical ID. Allocate the next contiguous run `sequence_no` for runtime events.
4. If the payload exceeds the inline threshold, put the bytes in `objects/sha256/` (temp-file → `fsync` → atomic rename → verify) and replace them with an `ArtifactRefV1` plus bounded preview before serializing the line.
5. Canonically serialize the line and append it to the target file with a single write. A short write is an error.
6. Flush per the durability class below.
7. Update the disposable SQLite projection best-effort; a projection failure marks the projection dirty and does not roll back or truncate the JSONL.
8. Wake in-process SSE subscribers directly (same process), then release the lock and return.

Only after the required `fsync` (step 6) is the append acknowledged. A crash before that point leaves at most a torn trailing line, which the next load ignores.

### Domain atomic units

Where a workflow spans several records, the adapter appends them under the same held `asyncio.Lock` so a reader never sees a partial workflow while the writer is mid-sequence, and each record carries its own logical ID for idempotent replay. If the process crashes mid-sequence, load ignores any torn trailing line and AC3 recovery reconciles the run from the last durable records — the light store does not need a multi-file commit marker because a single writer cannot interleave another writer's partial batch. The workflows are:

- run submission: user message, run record, immutable runtime-context snapshot, initial `run_queued` event, and run command;
- approval resolution: decision/audit linkage, approval/run transition, and approval-resolution command;
- cancellation: run `CANCELLING` fence, `run_cancelling` event, and cancel command;
- checkpoint publication: AC4 artifact-use record and checkpoint ref;
- subagent terminalization: task status, result/ref, parent linkage event, and queue completion; and
- run terminalization: final message/ref, terminal status/event, usage outcome, and queue completion.

Each record retains its existing domain ID and port return shape.

## Flush policy

Canonical acknowledgement has two durability classes, both of which flush before success:

### Immediate

The adapter `fsync`s before returning success for:

- conversation creation and user messages;
- run creation, runtime-context snapshot, and queue enqueue;
- approvals and approval decisions;
- cancellation fences;
- tool/subagent state transitions and external-side-effect receipts;
- checkpoint and artifact references;
- terminal run/message/result records; and
- retention, legal-hold, deletion, and migration records.

### Micro-batched stream output

High-frequency model/reasoning deltas may coalesce for at most 100 ms, 64 events, or 256 KiB of canonical bytes, whichever occurs first. One flush is performed per touched file. The adapter returns envelopes only after that flush.

Platform flush behavior is explicit:

- macOS/POSIX: `fsync()` each touched file; `fsync()` the parent directory when creating a file/directory or renaming an object into place.
- Windows: `FlushFileBuffers()` each touched file; object/compaction rename uses `MoveFileExW(REPLACE_EXISTING | WRITE_THROUGH)` on the same volume.
- graceful shutdown flushes a pending micro-batch before closing;
- `fsync`/`FlushFileBuffers` failure is a failed append, not a warning; and
- SQLite and in-process notification use their own derived-state policy and are never evidence that canonical JSONL is durable.

## Capacity and admission

AC2 v1 fixes these desktop defaults, configurable only downward by deployment policy:

- 1 GiB of canonical JSONL per session;
- 4 GiB soft and 8 GiB hard canonical JSONL per workspace;
- 10,000 retained sessions per workspace;
- 2,000,000 canonical lines per session; and
- a physical free-space floor of `max(1 GiB, 10% of volume capacity)`, matching AC4.

SQLite/WAL and staged temp files do not count as logical live bytes but do count in physical free-space admission. At the hard session/workspace/session-count/line limit, or below the free-space floor, new conversations, runs, large writes, checkpoints, and external-effect intents fail before mutation with `file_store_quota_exceeded`. The adapter reserves the final 64 MiB below the hard logical limit for bounded cancellation, terminal, and deletion records from already-active work; that reserve cannot start a model, tool, or subagent. Legal hold never yields to quota. Reads/export remain available, and there is no unbounded inline, in-memory, or PostgreSQL fallback.

## Content-addressed object store

AC2 owns the object-store primitive; AC4 owns the offload decision and typing.

- Objects live under `objects/sha256/<ab>/<cd>/<full-hex>`, immutable and deduplicated only within one workspace. A different workspace has a different object namespace even for identical bytes.
- A put streams to a same-volume temp file, hashes and size-limits the bytes, `fsync`s, atomically renames to the digest path, and verifies. An existing digest is reused only after size/hash verification.
- The hash is always over uncompressed logical bytes. `artifact://sha256/<hex>` is the only storage URI exposed to runtime records. A filesystem path is never an artifact reference.
- JSONL records carry typed `ArtifactRefV1` references and bounded previews; full bytes never also remain inline.
- Reference counts and reachability are derived in SQLite from canonical JSONL. A zero count is not sufficient for deletion until retention, legal hold, and open-handle checks pass. **AC4 owns object garbage collection**; AC2 records references and deletion intent only.
- Corrupt or missing objects produce a typed unavailable result and a checksum-failure audit event, with no fallback to unverified bytes.

This section is the AC2/AC4 overlap made explicit: the store lives here; the wiring (which tool results offload, thresholds, previews, `/large_tool_results/` routing, GC) is AC4.

## Rebuildable SQLite, FTS, and queue materialization

### Canonicality rule

Each workspace's `index/catalog.sqlite3` contains no unique user data. Every durable row is derivable by scanning that workspace's JSONL files. Deleting the database and its WAL/SHM files must preserve behavior after rebuild, except that active queue leases return as unclaimed/recoverable by design.

### Required tables

The initial schema contains:

- `projection_meta(schema_version, store_id, rebuilt_at, dirty)`;
- `stream_cursors(conversation_key, file_id, byte_offset, last_record_id)`;
- `records(record_id, conversation_key, file_id, byte_offset, byte_length, record_kind, run_id, run_sequence_no, task_id, created_at)`;
- `conversations`, `messages`, `runs`, `runtime_events`, `approvals`, `approval_items`, `tool_invocations`, `subagent_tasks`, `subagent_results`, `drafts`, `shares`, `sources`, `citations`, `consumer_cursors`, `retention_policies`, and `legal_holds`, matching the current port projections;
- `queue_commands(command_id, conversation_key, command_type, priority, status, attempt, enqueued_at, available_at, claim_id, locked_by_hash, lease_expires_at, last_record_id)`;
- `object_refs(sha256, conversation_key, record_id)` for reachability;
- `idempotency_keys(scope, logical_key, request_hash, record_id)`; and
- FTS5 tables for user-visible conversation title and redacted user/assistant message text.

Required constraints include unique `record_id`, event `event_id`, `(run_id, sequence_no)`, message ID, command ID, approval ID, and task ID.

The database uses WAL mode, foreign keys on, `busy_timeout=5000`, and `synchronous=NORMAL`. It may lose its latest transaction because canonical JSONL is already flushed; startup catches it up. No API response depends on an uncommitted SQLite-only mutation.

### Projection ordering

- Projection applies records in the physical append order of each file, tracking a per-file byte cursor.
- Before serving a direct query for a session, the adapter catches that session up to the tail of its files.
- Before emitting an event notification, event rows and the run cursor are projected.
- Unknown record kinds/schema versions halt projection for that session; they are not ignored.

### Queue claims

Because `RuntimeQueuePort.claim_next()` has no org argument, `FileRuntimeStore` maintains a process-local registry of AC1-validated workspace directories and their catalogs. Because there is one worker process claiming work, a claim is an in-process SQLite `BEGIN IMMEDIATE` transaction plus a canonical `CLAIMED` transition appended to the owning `events.jsonl` — no cross-process claim CAS is needed. `enqueue_*` appends an `ENQUEUED` record and projects it before returning. On rebuild, the fold uses the latest transition and preserves attempt count; a final `CLAIMED` transition is projected as recoverable/unclaimed, and AC3 reconciliation decides whether to resume, cancel, or fail the associated work.

FTS and ordinary projections may rebuild in the background after direct reads are available. Queue projection must rebuild before worker readiness because a missing command is a correctness failure.

### Corrupt catalog

`PRAGMA quick_check` runs on ordinary startup; `integrity_check` runs after unclean shutdown and migration. A failed check closes the database, moves DB/WAL/SHM aside, creates a new schema, rescans every JSONL file, verifies aggregate record/event/queue counts and terminal-state folds, and atomically marks `projection_meta.dirty=0`. No code attempts row-level salvage from a corrupt catalog.

## RuntimeAdapterFactory and port conformance

`RuntimeSettings` accepts `file` as a store backend only when all of these are true:

```text
ENTERPRISE_DEPLOYMENT_PROFILE == "single_user_desktop"
RUNTIME_STORE_BACKEND == "file"
AC1 desktop capability gate == enabled
validated storage grant/root == present
DATABASE_URL is not used by the AI runtime store
```

Any mismatch raises a non-retryable configuration error. `auto` never selects `file`; desktop supervision must request it explicitly.

`RuntimeAdapterFactory.from_settings(settings, role=...)` constructs one shared `FileRuntimeStore` per process. It owns workspace-scoped journal/projection contexts and returns:

- `persistence`: complete `PersistencePort`;
- `event_store`: complete `EventStorePort`;
- `queue`: complete `RuntimeQueuePort`;
- `lifecycle`: file lifecycle with open, validate, project, migrate-schema, close;
- file-backed `DraftStorePort`, `ShareStorePort`, `ConversationToolOrdinalStorePort`, `SubagentStorePort`, and `SourceStorePort`; and
- `postgres_store=None`.

All additional protocols currently satisfied by the PostgreSQL store and used through runtime checks—such as citation, approval, budget, usage, retention, audit, and workspace-default operations—must be implemented by file adapters or explicitly composed file satellite adapters. Falling back to an in-memory sibling in a desktop production process is prohibited.

The shared port-contract suite runs against a fresh file root and verifies return models, optimistic-version conflicts, tenant/user checks, idempotency, ordering, pagination, soft delete/restore, retention, approvals, queue semantics, and lifecycle behavior. A semantic exception must be documented in the port itself and applied to every adapter; AC2 does not add file-only domain semantics.

The existing `postgres` and in-memory branches are not refactored beyond backend-unaware test extraction. No PostgreSQL DDL, migration, SQL, notification, encryption, retention, or default-selection behavior changes.

## Persistence, retention, deletion, and export

### Profile-scoped posture (right-sizing)

AC2 targets `single_user_desktop`, where the signed-in user is the administrator and owns the files. Operator-boundary controls therefore cannot bind them, and AC2 does **not** claim guarantees the profile cannot cash (see overview §20 and the threat model below):

- **Shipped on desktop:** user-configurable **age-based cleanup** (the Claude Code `cleanupPeriodDays` pattern); a real **deletion cascade** (a deleted conversation removes its session directory and decrements AC4 object reachability); and **local tamper-evidence** via `packages/audit-chain` (hash-chained records), disclosed as _tamper-evident, not tamper-proof_ — a same-OS-user process can still delete or rewrite files.
- **A desktop "hold" is advisory:** it stops the app's own automated cleanup/deletion of held sessions and is explicitly **not** compliance-grade legal hold. It cannot prevent the OS user's `rm`.
- **Deferred to the managed / PostgreSQL path:** the binding legal hold that blocks physical deletion, the full retention-sweeper over all entity types with dry-run deletion **plans**, and deletion-**evidence** guarantees. These are meaningful only where an operator/tenant boundary exists and are **not** reimplemented as desktop-only code. Where sections below describe that machinery, read it as the managed-path contract that the file adapter satisfies structurally (cascade correctness, reference accounting) without claiming the operator-boundary guarantee.

### Record model

Every mutable domain object is represented as immutable facts:

- create/upsert/version records carry the full validated current model or deterministic patch;
- status changes carry expected prior version/status and new version/status;
- deletes are tombstones;
- queue state is a transition log;
- event envelopes are immutable; and
- derived current rows are folds.

Optimistic updates compare the expected version in the derived row while holding the per-conversation lock, then append the transition. A conflict writes nothing.

### Soft delete and restore

Conversation soft delete appends an immediate tombstone with `deleted_at` and actor. Restore appends a reversal only while retention has not physically purged the session and no policy forbids restore. Lists exclude tombstoned conversations by default, matching current behavior.

### Physical retention/deletion

JSONL lines are not edited in place during normal operation. Whole-session purge and selective internal-record expiry use two fixed procedures.

For a whole-session retention expiry or verified user deletion:

1. resolve applicable retention and legal-hold policy;
2. enumerate the complete cascade: conversation, messages, runs, runtime events, outbox/queue transitions, object references, memory records, checkpoints, approvals/items/batches, tool/model invocation intents/receipts/results, subagent streams/results, drafts, citations/sources, usage records, consumer cursors, and artifact references;
3. under the per-conversation lock, hide the session in the projection and register durable AC4 dereference intents;
4. atomically rename the entire conversation directory on the same volume into `tmp/deletions/<operation-id>`, `fsync` both parent directories, then recursively unlink the renamed tree;
5. rebuild or incrementally update the workspace SQLite/FTS catalog so no derived bytes retain the deleted conversation;
6. append the workspace `session_payloads_removed` audit record only after the original/staged trees are absent; and
7. let AC4 garbage collection process the durable dereferences under its reachability, legal-hold, and grace rules. AC2 never deletes shared object bytes directly.

Because there is a single writer, an ordinary open cannot race a delete: the same process serializes both. A rename-then-unlink sequence means a crash mid-delete leaves either the intact conversation directory or the staged `tmp/deletions/` tree; startup finishes the unlink idempotently and re-emits the audit record.

For selective expiry of eligible non-event internal data, maintenance rewrites the conversation file with `PayloadRemovalV1` tombstones via temp-file-plus-atomic-rename, then rebuilds the affected projection. Runtime events are never selectively removed; expiry requiring their removal uses whole-session purge.

A hold suspends automated cleanup/deletion and artifact dereference for its scope; it does not make data invisible. On `single_user_desktop` this is an **advisory local hold** that binds the app's own cleanup only — it is explicitly not a compliance-grade legal hold and cannot stop the OS user deleting files directly. Hold create/release is immediate, actor-attributed, and audited via `packages/audit-chain`. Deleting a conversation must not silently erase the tamper-evident record that deletion occurred.

### Export

The export reader copies the conversation directory (`events.jsonl` plus every `subagents/<task>.jsonl`), the referenced object manifest entries, retention/legal-hold metadata allowed by policy, and validation hashes. Because there is one physical copy per session and one writer, export takes the per-conversation lock briefly, snapshots the file byte lengths, and reads up to those lengths — no generation rotation is required. It excludes SQLite, in-process notification state, secrets, broker tokens, and object bytes outside the referenced set.

## Migration from the legacy desktop AI-runtime store

Migration is offline and one-way per attempt; there is no live dual-write.

1. The supervisor stops facade, AI API, and worker and leaves the configured legacy desktop AI-runtime PostgreSQL store available read-only.
2. `scripts/export_desktop_file_store.py` reads through the existing PostgreSQL adapter, ordered by stable IDs/sequences, and writes staged JSONL and objects under `migrations/<migration-id>/`.
3. It emits all parent/subagent records, artifact references, policies, queue terminal state, and workspace metadata through the same Pydantic encoders used for new writes.
4. Validation compares source and target counts by record kind, every event ID and `(run_id, sequence_no)`, every nonterminal/terminal run, every pending approval, and every queue command.
5. Only a clean report installs the session directories. The file root receives a durable migration receipt containing source schema version and aggregate hashes, never source credentials.
6. The file catalog is rebuilt from installed JSONL and checked independently.
7. The configured legacy desktop AI-runtime PostgreSQL store is sealed read-only for at most one stable release and is not updated. Any post-cutover physical retention/deletion request destroys that retained source in full before completion is reported; backout thereafter uses verified reverse export into a fresh database.

Any mismatch removes the staged target and leaves the source authoritative. Migration may be re-run with the same migration ID; stable record IDs make it idempotent.

Legacy-source cleanup runs only with facade/API/worker stopped, requires the matching migration receipt and expected embedded-cluster identity, and removes only the AI-runtime database selected by `AI_BACKEND_DB_NAME`; it never removes the backend database or PostgreSQL cluster.

## Trust and security

### Trust boundary

The file adapter trusts:

- the AC1-verified deployment profile and storage grant;
- Pydantic-validated domain records received through internal ports; and
- OS file handles proven to remain under the approved root.

It does not trust:

- HTTP-supplied org/user/role/scope/path values;
- renderer IPC payloads;
- existing filesystem contents, symlinks, reparse points, manifests, SQLite rows, or JSONL;
- file extensions or MIME labels; or
- record contents as proof against a malicious local account.

Identity continues to come from the verified bearer/service-token path. Even in single-user desktop mode, every read/update verifies the persisted org/user ownership expected by the port. The desktop profile is not an authorization bypass.

### Plaintext disclosure

Canonical JSONL, objects, and derived SQLite/FTS are plaintext. This is a product decision for inspectability, not an encryption control. The UI/setup documentation must state:

- anyone who can read the local OS account's application-data directory can read chat content;
- full-disk encryption and a locked user session are recommended;
- OS backup/snapshot copies outside the AC1 root are not erased by an application deletion and remain governed by the user's backup policy;
- app logs do not contain message bodies or physical paths; and
- OAuth/provider secrets are never stored here — they stay in the backend `TokenVault` / Electron `safeStorage`.

No spec or UI may claim “encrypted at rest” based only on OS permissions or optional full-disk encryption. Likewise, unlink/rebuild is logical product deletion, not forensic media sanitization on SSD/copy-on-write filesystems; 0xCopilot does not perform overwrite passes or claim otherwise.

### Data minimization

- Persist the already-redacted `RuntimeEventEnvelope` payload/metadata; do not persist unredacted provider frames.
- Large tool payloads, files, screenshots, checkpoint bytes, and model binaries use object references, not JSONL embedding.
- FTS indexes only user-visible title and redacted user/assistant text.
- Worker IDs are hashed in canonical queue records.
- Physical paths, bearer values, environment variables, connector tokens, and provider keys are prohibited payload fields and covered by adversarial tests.

### Integrity limitations

JSON validity checks, per-run sequence contiguity, and SQLite checks detect torn writes and accidental corruption. The light store does **not** carry per-stream hash chains; it does not claim to detect a determined same-user process rewriting content and is not marketed as tamper-proof. Authenticated product audit remains in the existing audit subsystem; AC10 may add signed manifests. AC2 does not overstate this control.

## Observability and audit

### Structured logs

Required events:

- `file_store.opened`
- `file_store.append_committed`
- `file_store.append_failed`
- `file_store.quota_rejected`
- `file_store.torn_tail_ignored`
- `file_store.interior_corruption` (interior corruption → conversation read-only)
- `file_store.index_rebuild_started`
- `file_store.index_rebuild_completed`
- `file_store.index_rebuild_failed`
- `file_store.migration_started`
- `file_store.migration_completed`
- `file_store.migration_failed`
- `file_store.retention_compaction_completed`
- `file_store.deletion_staged`
- `file_store.session_payloads_removed`
- `file_store.deletion_completed`
- `file_store.legacy_source_removed`

Allowed fields are hashed workspace/conversation/run identifiers, record counts, byte counts, duration, reason/error code, retryability, and schema version. Logs must not contain logical raw IDs, message/event payloads, search text, physical paths, file names derived from user input, or raw file bytes.

### Metrics

- append latency by durability class;
- flush latency/failure count by platform;
- per-conversation lock wait;
- committed records/bytes;
- projection lag in committed records and milliseconds;
- index rebuild sessions/records/duration/failure;
- torn-tail-ignored count and interior-corruption count;
- queue depth/age/claims/retries/dead letters;
- object bytes/count, dedupe ratio, checksum failures;
- deletion staged bytes/duration and legacy-source cleanup failures;
- logical quota/free-space usage and soft/hard rejections; and
- migration count/duration/mismatch reason.

Metrics use bounded labels only; no user/session/run IDs.

### Release performance gates

On packaged macOS arm64/x64 and Windows x64 reference machines with local SSD storage:

- immediate one-record commit p95 at most 50 ms and p99 at most 250 ms;
- micro-batched stream commit p95 at most 150 ms and p99 at most 500 ms from first queued delta;
- full catalog/FTS/queue rebuild of 100,000 records at most 30 seconds and 1,000,000 records at most 5 minutes; and
- idle projection lag zero, with p99 lag under 500 ms while streaming.

Any committed-record loss/duplication, incorrect rebuild fold, unexplained sequence gap, or latency over twice a bound in two consecutive release-candidate runs blocks rollout. Network/removable filesystems are unsupported rather than benchmark exceptions.

### Product audit

Audit records are required for migration start/completion/failure, legal-hold create/release, retention compaction, conversation/user deletion, diagnostics export, and backout export/import. Each records actor type/ID from verified identity, operation ID, affected scope hash, counts, reason, result, and correlation ID.

An in-memory/no-op audit sink does not satisfy production acceptance. Customer SIEM export remains a deployment/product control outside AC2 and is not inferred from local logs.

## Testing strategy

### Unit tests

- Pydantic accepts every valid v1 contract and rejects unknown fields, naive datetimes, invalid digests, invalid stream ownership, invalid bounds, non-finite JSON numbers, and oversized lines.
- Canonical encoding and record hashes have golden vectors shared across macOS and Windows.
- UUIDv5 record IDs are stable and payload mismatch is an idempotency conflict.
- Run sequences allocate contiguously; there is no session-global counter.
- Stream routing writes `task_id=None` to `events.jsonl` and a task ID to exactly one child file.
- Per-conversation lock serializes concurrent appends deterministically.
- Object put dedupes by digest, verifies size/hash, and rejects mismatches.
- Soft/hard/session/line/free-space limits and the terminal-only 64 MiB reserve reject or admit each operation class exactly as specified.
- Retention folds, tombstones, restores, legal holds, and cascade enumeration include every sensitive record family listed above.

### Port-contract tests

Parameterize the existing persistence contract suite over `InMemoryRuntimeApiStore`, `PostgresRuntimeApiStore`, and `FileRuntimeStore`. For file, cover every `PersistencePort`, `EventStorePort`, `RuntimeQueuePort`, lifecycle, draft, share, ordinal, subagent, source, citation, approval, budget, usage, retention, audit, and workspace-default method used by production. Assert tenant/user isolation, optimistic conflict behavior, idempotency, pagination order, event replay, queue retry/dead-letter behavior, and no in-memory fallback after process reconstruction.

### Integration tests

- Start API+in-process worker with `single_user_desktop` + file backend, create a conversation/run/events, restart the process, and verify byte-for-byte wire-equivalent reads.
- Dispatch a subagent; verify its events land only in its `subagents/<task>.jsonl` and merged replay is complete.
- Remove SQLite/WAL/SHM and verify complete conversation, event, FTS, approval, subagent, and queue reconstruction from JSONL.
- Import a representative configured legacy desktop AI-runtime PostgreSQL store and compare every logical record and cursor.
- Export a session and validate it in a fresh root.
- Apply soft delete, restore, retention compaction, legal hold, and user deletion with referenced object accounting; verify removed payload bytes exist in neither the conversation directory nor any staged tree, prior SQLite/WAL/SHM after completion.
- After migration, request physical deletion and prove the retained legacy AI-runtime database is removed without touching the backend database or PostgreSQL cluster; inject cleanup failure and require a pending state.

### Crash-injection tests

Kill the writer at every append step:

- before the line write;
- after a partial (torn) line;
- after the line but before `fsync`;
- after `fsync` but before SQLite;
- during SQLite commit;
- during an object put (temp → rename); and
- before/after whole-session deletion rename and recursive unlink.

After each crash, reopen in a separate process and assert: only acknowledged records are visible, a torn trailing line is ignored, no event is duplicated, run sequences are contiguous, queue state is recoverable, and no object is half-written under its digest path.

### Adversarial tests

- Symlink/reparse-point swaps between validation and open.
- `..`, absolute, Unicode-confusable, case-colliding, reserved-device, alternate-stream, and oversized path inputs.
- A 1 MiB boundary line, decompression-like JSON nesting, huge integers, invalid UTF-8, NUL, duplicate keys, non-finite numbers, and record-count floods.
- Duplicate event/record/command IDs with same and different payloads.
- Forged org/user/task/run IDs and a child record placed in `events.jsonl`.
- SQLite rows forged ahead of JSONL and a catalog replaced while open.
- Disk full, read-only permissions, flush error, short write, clock rollback, and abrupt power-loss simulation.
- Secret scanner fixtures in event payloads to prove prohibited secrets are rejected/redacted before persistence.

### macOS tests

- arm64 and x64: file/directory `fsync`, same-volume atomic object rename, app-support permissions, Unicode normalization, sleep/wake, abrupt force-quit, and full catalog rebuild.
- Verify no sandbox/container entitlement accidentally broadens the storage root.
- Validate migration and export on a case-insensitive APFS volume.

### Windows tests

- x64: `FlushFileBuffers`, write-through `MoveFileExW`, current-user-plus-`SYSTEM` DACL, long-path handling, reparse-point rejection, case-insensitive collisions, antivirus sharing violations, abrupt process termination, and full rebuild.
- Verify no file remains undeletable after all processes close and no replacement crosses volumes.

### Web regression tests

- Full PostgreSQL and in-memory suites remain green.
- A non-desktop profile with `RUNTIME_STORE_BACKEND=file` fails startup.
- Existing web builds contain no file-adapter import, Node/filesystem polyfill, desktop route, or changed API type.
- PostgreSQL event append/batch, LISTEN/NOTIFY, encryption, retention, and migrations produce unchanged results.
- Browser HTTP/SSE contract snapshots remain byte-compatible.

## Rollout and backout

### Rollout

1. Land contracts, encoder/hash goldens, the JSONL journal, the object store, recovery-on-load, and projection behind an unavailable-by-default `file` factory branch.
2. Pass all unit, port-contract, crash, adversarial, and platform tests.
3. Add desktop supervision with the file backend off behind `COPILOT_DESKTOP_FILE_STORE_V1`.
4. Run shadow export/rebuild validation against disposable test data; do not dual-write production user mutations.
5. Enable for new internal desktop profiles with empty stores.
6. Enable offline migration for internal existing profiles and retain the source read-only.
7. Expand to staged desktop release cohorts only after rebuild, migration, and disk-pressure telemetry meet thresholds.
8. Make file the desktop default after one stable release; retain explicit PostgreSQL import/backout tooling for one additional stable release.

The flag is server-authoritative and honored only with the AC1 activation predicate. It is never sent by the renderer.

### Backout

- Before the first committed file-native user mutation, disable the flag and continue with the unchanged configured legacy desktop AI-runtime PostgreSQL store.
- After file-native writes, stop facade/API/worker and run `scripts/export_file_store_to_postgres.py` into a **fresh** desktop AI-runtime PostgreSQL database. Never overwrite the retained source in place.
- Verify record-kind counts, IDs, event/run sequences, nonterminal state, approvals, queue state, and aggregate hashes. Atomically switch the supervisor's configured database only after verification.
- If reverse export cannot validate, keep the file store authoritative and roll back only higher layers that remain file-compatible. Do not start against stale PostgreSQL and do not discard file data.
- Backout operations are audited; source file data remains read-only until the retention window expires.

## Acceptance criteria and definition of done

AC2 is done only when all are true:

- [ ] The two canonical transcript shapes exist: one `events.jsonl` per session and one JSONL per subagent task.
- [ ] Every runtime event is committed in exactly one physical file.
- [ ] The per-run `sequence_no` invariant is implemented, documented in code, and crash-tested; there is no session-global counter.
- [ ] Appends are serialized by a single in-process per-conversation `asyncio.Lock`; there is no cross-process lock file.
- [ ] An append is acknowledged only after its required `fsync`; a torn trailing line is ignored on load and earlier history is never rewritten.
- [ ] Interior corruption fails closed (conversation read-only) rather than skipping the bad line.
- [ ] Large payloads are stored once as content-addressed `objects/sha256/` objects with typed references and bounded previews in JSONL.
- [ ] Deleting/corrupting SQLite rebuilds all required projections, FTS, and queue state by scanning JSONL.
- [ ] Active claims become recoverable after rebuild while attempt counts remain durable.
- [ ] Capacity, physical-space, and emergency-reserve admission fail closed without blocking bounded terminal/cancel/delete records.
- [ ] `RuntimeAdapterFactory` returns a complete file-backed `RuntimePorts` set only under the desktop activation predicate.
- [ ] No production file-backed method falls back to an in-memory store.
- [ ] Shared port conformance passes, including tenant isolation, unauthorized access, deletion cascades, retention expiry, audit evidence, redaction, and legal hold.
- [ ] Whole-session purge removes canonical JSONL, staged trees, and derived SQLite/WAL/FTS bytes before completion while leaving shared objects to AC4's reference-aware garbage collection.
- [ ] Offline forward migration and verified reverse export/backout work with representative data.
- [ ] Plaintext disclosure, permissions, secret exclusions, and integrity limitations are documented and tested.
- [ ] PostgreSQL and web behavior are unchanged.
- [ ] Operational metrics, redacted logs, product audit, diagnostics, and runbooks exist.
- [ ] The accepted component-local implementation spec maps every critical file, pinned dependency, migration, and acceptance-evidence artifact.
- [ ] AC3 can consume canonical queue transitions and checkpoint/object references without changing AC2's format.

## Critical files

Implementation is expected to add or modify exactly scoped files in these areas; this list is the review checklist, not permission to cross service boundaries.

### AI runtime contracts and selection

- `services/ai-backend/src/agent_runtime/settings.py`
- `services/ai-backend/src/agent_runtime/api/ports.py`
- `services/ai-backend/src/agent_runtime/persistence/records/file_store.py`
- `services/ai-backend/src/runtime_adapters/factory.py`

### File adapter

- `services/ai-backend/src/runtime_adapters/file/__init__.py`
- `services/ai-backend/src/runtime_adapters/file/contracts.py`
- `services/ai-backend/src/runtime_adapters/file/encoding.py`
- `services/ai-backend/src/runtime_adapters/file/journal.py`
- `services/ai-backend/src/runtime_adapters/file/objects.py`
- `services/ai-backend/src/runtime_adapters/file/deletion.py`
- `services/ai-backend/src/runtime_adapters/file/catalog.py`
- `services/ai-backend/src/runtime_adapters/file/queue.py`
- `services/ai-backend/src/runtime_adapters/file/runtime_store.py`
- `services/ai-backend/src/runtime_adapters/file/satellite_stores.py`

### Desktop selection and migration

- `apps/desktop/main/services/service-env.ts`
- `apps/desktop/main/services/supervisor.ts`
- `apps/desktop/main/services/desktop-supervisor.ts`
- `services/ai-backend/scripts/export_desktop_file_store.py`
- `services/ai-backend/scripts/export_file_store_to_postgres.py`
- `services/ai-backend/scripts/remove_legacy_desktop_store.py`

### Tests and runbooks

- `services/ai-backend/tests/contract/test_runtime_store_ports.py`
- `services/ai-backend/tests/unit/runtime_adapters/file/test_contracts.py`
- `services/ai-backend/tests/unit/runtime_adapters/file/test_journal.py`
- `services/ai-backend/tests/unit/runtime_adapters/file/test_objects.py`
- `services/ai-backend/tests/unit/runtime_adapters/file/test_catalog.py`
- `services/ai-backend/tests/unit/runtime_adapters/file/test_queue.py`
- `services/ai-backend/tests/integration/test_file_store_processes.py`
- `services/ai-backend/tests/integration/test_file_store_migration.py`
- `apps/desktop/main/services/service-env.test.ts`
- `apps/desktop/main/services/supervisor.test.ts`
- `services/ai-backend/docs/specs/desktop-agent-capabilities/ac2-file-session-store.md`
- `docs/operations/desktop-file-store-recovery.md`
- `docs/operations/desktop-file-store-migration.md`

## PR decomposition (light)

The light store is fewer, smaller PRs than the heavy draft (no generations, no cross-process lock protocol, no commit-marker/tail-repair engine):

1. Contracts + canonical encoder/hash goldens + `FileSessionRecordV1`.
2. `SessionJournal` (append-only JSONL, per-conversation `asyncio.Lock`, flush classes, load-with-torn-tail-ignore) + `ObjectStore` (content-addressed put/get).
3. `CatalogProjection` (rebuildable SQLite + FTS5 + queue) and port/satellite adapters + factory branch.
4. Migration/export/backout scripts + desktop supervision wiring + runbooks.

Roughly **4 PRs** versus the heavy draft's 6–8.

## Unresolved risks (implementation choices closed)

There is no open implementation choice in this PRD. The remaining risks have fixed handling and ship gates:

- **Plaintext local disclosure:** accepted for v1 with user-only permissions, full-disk-encryption guidance, strict secret exclusion, and explicit product disclosure. Do not add ad hoc field encryption to AC2.
- **Same-user malicious modification:** the light store has no hash chain and is not an authenticated boundary. Detect accidental inconsistency and fail closed; do not market tamper proofing. AC10 may add signed manifests compatibly.
- **Filesystem durability variance:** use the specified platform flush/rename adapters and block rollout on crash-matrix failures. Do not weaken acknowledgement to improve benchmarks.
- **Large histories and rebuild time:** retain JSONL canonicality, use incremental projection and occasional whole-file compaction, and gate release on performance targets. Do not make SQLite canonical.
- **Future separate-process worker:** if the worker is ever separated from the API, the store becomes multi-writer and needs the deferred AC3b machinery (cross-process lock, commit markers, generations). Do not add it pre-emptively to the single-writer store.
- **Migration mismatch:** abort and leave the source authoritative. Do not partially install or dual-write.
- **Artifact lifecycle races:** AC2 records references and deletion intent; AC4 owns byte commit and garbage collection. Never delete an object solely because one session dropped a reference.
- **OS backup remnants:** disclose that app deletion cannot erase external Time Machine, Volume Shadow Copy, or enterprise backup copies. Do not report those deployment-controlled copies as product-managed deletion.
- **Forensic media remnants:** use unlink plus fresh derived-store rebuild and parent flush, but do not claim secure overwrite on SSD/copy-on-write media; device sanitization remains an OS/deployment control.
- **Unknown future schema:** fail closed and require an explicit registered migration. Never skip unknown canonical records.
