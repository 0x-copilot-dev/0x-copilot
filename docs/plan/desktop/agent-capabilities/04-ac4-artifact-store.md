# AC4 — Artifact store and tool-result offloading

| Field             | Decision                                                                                                                                                          |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Spec ID           | AC4                                                                                                                                                               |
| Status            | Draft; decision-complete and awaiting architecture review                                                                                                         |
| Wave              | 1 — Durable primitives                                                                                                                                            |
| Estimated effort  | M — 7–10 engineer-days for offload wiring, the `/large_tool_results/` route, reachability/GC policy, repair, and crash evidence (the byte primitive is AC2-owned) |
| Dependencies      | AC1 desktop capability foundation; **AC2 content-addressed object store (`objects/sha256/`)**                                                                     |
| Required for      | AC3 checkpoints/recovery, AC5 file history, AC6 Monty snapshots, AC7 transfers, AC8 browser artifacts, AC10 hardening                                             |
| Primary owner     | `services/ai-backend` persistence and context management                                                                                                          |
| Supporting owners | File runtime adapter, runtime worker, desktop storage/diagnostics                                                                                                 |
| Web impact        | None                                                                                                                                                              |

> **Scope: offload WIRING over AC2's object store (not a second store).** The
> content-addressed byte primitive — `objects/sha256/` put/get/verify/dedupe,
> the atomic temp→`fsync`→rename→readback protocol, and per-workspace
> deduplication — now lives in the [AC2-light foundation](02-ac2-file-session-store.md#content-addressed-object-store).
> AC4 does **not** build a second byte store. AC4 owns the layer above it: it
> reuses the existing `ContextPayloadManager` / `OffloadWriter` /
> `ManagedContextPayload` seam to write large tool results into the AC2 object
> store as a typed `ArtifactRefV1` with a bounded, redacted preview; it routes
> the Deep Agents `CompositeBackend` `/large_tool_results/` path to reads from
> that object store; and it owns reference reachability, retention/legal-hold
> policy, and garbage collection over those references. **AC2/AC4 overlap is
> explicit:** AC2 stores and verifies the bytes; AC4 decides _what_ to offload,
> _how_ it is referenced, and _when_ an object becomes collectible.
> Summarization is **store-agnostic** — it runs above the adapter on the
> preview/inline text and does not depend on where the bytes live.

## Delivered (light) vs Deferred — implementation status

This PRD was written ahead of the code. What shipped is the **offload wiring**
over AC2's object store — reusing the existing `ContextPayloadManager` /
`OffloadWriter` seam — plus the Deep Agents `CompositeBackend` routing and a
durable checkpointer. The typed artifact-store layer (`ArtifactRefV1`,
`ArtifactStorePort`, commit coordinator, retention/GC, sidecars, gzip/MIME
policy) was intentionally **not** built. This section is the authoritative
reconciliation; where a later section describes that layer, read it as design
intent, not shipped behavior.

### Delivered (light) — what shipped

- **Write half** (`runtime_adapters/file/offload.py`): `FileOffloadWriter`
  implements the existing `OffloadWriter = Callable[[str], str]` alias — it
  UTF-8-encodes the content, `put`s it once in AC2's `FileObjectStore`, and
  returns a `/large_tool_results/<sha256>` reference (with a ≤200-char inline
  preview stored on the `ObjectRef`).
- **Offload decision** (`runtime_worker/tool_result_offload.py`):
  `ToolResultOffloader` runs oversized `TOOL_RESULT` output through the shared
  **synchronous** `ContextPayloadManager.prepare_tool_output(...)` with a
  `TokenBudgetPolicy`. On `OFFLOAD` it rewrites the event payload — `output`/
  `preview` become the bounded preview and `output_ref` becomes the reference.
  The threshold actually used is **~8,000 estimated tokens**
  (`INLINE_TOKEN_BUDGET`), not the spec's 32 KiB-byte + 8,192-token pair.
- **Read half** (`runtime_adapters/file/large_tool_result_backend.py`):
  `FileLargeToolResultBackend` implements the pinned Deep Agents
  `BackendProtocol` read-only — it resolves `/large_tool_results/<sha256>` back
  out of the object store (verify-on-read); `write`/`edit` are refused;
  `ls`/`glob`/`grep` return empty (the content store is not enumerable).
- **`/subagents/` file-native reader**
  (`runtime_adapters/file/subagent_trace_backend.py`):
  `FileSubagentTraceBackend` serves the canonical per-subagent JSONL.
- **CompositeBackend routing** (`agent_runtime/execution/factory.py`
  `_composed_deep_backend`): registers `/subagents/`, `/drafts/`, and
  `/large_tool_results/` over a `StateBackend` default; unrouted on non-desktop
  backends so those paths stay on `StateBackend` exactly as before.
- **Durable checkpointer** (`agent_runtime/execution/deep_agent_builder.py`): an
  `AsyncSqliteSaver` at `<root>/index/checkpoints.sqlite3` on the desktop file
  store; `InMemorySaver` elsewhere.
- **Construction/gating** (`runtime_worker/handlers/run.py`): builds
  `ToolResultOffloader(FileOffloadWriter(store.object_store))`,
  `FileSubagentTraceBackend`, and `FileLargeToolResultBackend` only when the
  store exposes an object store (the `file` backend); postgres/in-memory/web keep
  emitting full inline output.
- **Reuse of AC2's object store** for the durable bytes — no second byte store
  was built (a stated goal).

### Deferred / not in the light build

Every subsection below describing this layer is design intent, not shipped code:

- **`ArtifactRefV1`** typed frozen ref (`version`/`artifact_id`/`stored_size`/
  `compression`/`kind`/…). The delivered reference is AC2's minimal `ObjectRef`
  (`sha256`/`size`/`media_type`/`preview`); the model-facing pointer is a plain
  `/large_tool_results/<sha256>` string. `artifact://sha256/<digest>` URIs are
  not used.
- **`ArtifactStorePort` / `ArtifactReferenceSinkPort` / `ArtifactCommitCoordinator`**,
  the `ArtifactWriteRequestV1`/`ArtifactUseRecordV1`/`ContextPayloadRecordV1`
  contracts, and the async `ContextPayloadOffloadPort` /
  `aprepare_tool_output()` seam. The delivered path uses the pre-existing
  **synchronous** `prepare_tool_output` + `OffloadWriter`.
- **`runtime_context_payloads`-equivalent canonical owner records** and the
  "object first, canonical owner record second, then acknowledge" commit
  ordering. The offloaded bytes are referenced inline in the tool-result event's
  `output_ref`; there is no separate durable owner/use record, reachability
  index, or refcount.
- **Retention classes, legal hold, garbage collection, quarantine, orphan grace,
  startup repair/scrub, and quota/free-space admission.** None built.
- **The `.meta.json` decoding sidecar** and the deterministic **`none`/`gzip`
  compression + MIME-sniffing + preview-redaction policy.** Not built — bytes are
  stored verbatim (UTF-8), the object layout is AC2's single-shard
  `objects/sha256/<hh>/<hash>`, and the preview is a raw 200-char prefix.
- **`packages/api-types/src/artifacts.ts`** and the broker artifact-ref fixtures.
- **The proposed `agent_runtime/persistence/artifacts/*` and
  `runtime_adapters/file/artifacts.py` / `artifact_index.py` / `artifact_repair.py`
  files, and the artifact GC worker job.** Not created.
- **The full crash / corruption / adversarial / retention / platform test matrix
  and the `artifact.*` structured events/metrics/audit.** Not built.
- Per-kind handling of **attachments, screenshots, downloads, and LangGraph/Monty
  checkpoints through one artifact port** — only tool-result/context offload and
  the subagent-trace/large-tool-result read routes ship.

## Problem and why now

The repository already names most of the concepts needed for large payloads, but
does not connect them into a durable production path:

- `ManagedContextPayload` and `ContextPayloadManager` can choose `offload`, but
  the writer is only an optional synchronous `Callable[[str], str]`. Repository
  call sites currently exercise it in tests; the production tool/MCP path does
  not supply a durable writer.
- `runtime_context_payloads` already models `storage_backend='local_file'`,
  `storage_uri`, SHA-256, byte size, MIME, redaction state, and retention. There
  is no local-file artifact adapter or persistence port that writes such a
  payload. The `runtime_context_payload_blobs` sidecar is a Postgres encrypted
  blob path and must not become a second copy of desktop-local object bytes.
- Deep Agents currently receives `StateBackend` plus `/drafts/` and
  `/subagents/` routes. `/large_tool_results/` is recognized by event and
  presentation code, but it still has no durable artifact-backed route.
- `runtime_checkpointer()` falls back to `InMemorySaver`; attachment requests
  can carry serialized content; browser screenshots/downloads, remote sandbox
  transfers, Monty snapshots, and host-file preimages have no common byte
  owner.

Without AC4, a large result can remain in process memory, be summarized without
a recoverable source, be copied into multiple records, or disappear on restart.
AC5–AC8 would each invent their own blob directory, checksum, retention, and
corruption behavior. That would create duplicate truth and make deletion,
legal hold, export, and quota accounting unreliable.

AC2 already provides the one workspace-scoped content-addressed object store
(`objects/sha256/`) for durable large bytes. AC4 connects the runtime's offload
producers to it: canonical records retain typed `ArtifactRefV1` references and
bounded previews; SQLite is only a rebuildable reachability/index projection.
AC4 is the wiring and policy layer — offload decision, reference model,
`/large_tool_results/` route, retention, and GC — not a second byte store.

## Goals

### Delivered (light)

- Reuse AC2's content-addressed object store for durable large bytes; do not
  build a second byte store. Each logical byte sequence is stored once under its
  SHA-256 digest by AC2's `FileObjectStore`.
- Delegate byte commit (same-filesystem temp file, `fsync`, atomic rename,
  verify-on-read) to AC2's `FileObjectStore`; the offload writer supplies bytes
  and consumes the verified reference.
- Wire production context/tool-result offload through the existing
  `ContextPayloadManager` / `OffloadWriter` seam so oversized tool output is
  parked in the object store with a bounded preview and a
  `/large_tool_results/<sha256>` reference.
- Add a durable object-backed `/large_tool_results/` Deep Agents route
  (read-only) and a file-native `/subagents/` route behind the existing
  `CompositeBackend`, preserving event visibility and frontend behavior.
- Provide a durable LangGraph checkpointer (`AsyncSqliteSaver`) on the desktop
  file store.
- Preserve current web/Postgres selection and public API/SSE semantics (all
  wiring is gated to the `file` backend).

### Deferred / not in the light build

- Freeze and use `ArtifactRefV1` for every durable large-payload reference (the
  delivered reference is AC2's minimal `ObjectRef`).
- One deterministic `none`/`gzip` storage policy with MIME validation and
  bounded, **redacted** previews, plus the `.meta.json` decoding sidecar.
- Wire offload to `runtime_context_payloads`-equivalent canonical owner records
  with `storage_backend=local_file` and `artifact://sha256/<digest>` URIs.
- Store attachments, screenshots, downloads, LangGraph/Monty checkpoints,
  remote-transfer payloads, and AC5 file-history preimages through one port.
- Derive reachability and reference counts from canonical owner records; make
  every SQLite artifact table disposable and rebuildable.
- Define deterministic quota, retention, deletion, legal-hold, garbage
  collection, quarantine, repair, and corruption behavior.

## Non-goals

- A cross-workspace or global deduplication service.
- General-purpose object storage for non-runtime product data.
- A public artifact HTTP endpoint or a renderer-accessible local filesystem
  path.
- Executing, auto-opening, importing, or trusting content based on a filename
  or MIME claim.
- Application-level encryption of desktop artifacts. AC4 follows AC1's
  explicit plaintext plus owner-only OS-permission posture.
- Replacing backend `TokenVault`, browser profiles, connector storage, user
  workspace files, or remote-sandbox provider storage.
- Moving web/Postgres payload bytes to the desktop file store.
- Persisting the same offloaded bytes in JSONL, SQLite, Postgres blob rows,
  Deep Agents state, and the object store.
- Defining the final AC10 retention UI or backup product.

## User-visible behavior and failure behavior

### Normal behavior

1. Small textual tool/context results remain inline.
2. When a result crosses the AC4 threshold, the activity feed retains its
   existing bounded summary. The model receives a bounded preview and a virtual
   `/large_tool_results/<opaque-ref>` path it can page through with existing
   file tools.
3. Attachments, screenshots, downloads, checkpoints, transfer files, and
   file-history snapshots are stored as artifacts from the first durable write;
   canonical events/messages contain metadata plus `ArtifactRefV1`.
4. Repeated identical bytes in the same workspace reuse one object. Different
   conversations may have separate references and retention rules without
   copying the object.
5. The desktop artifact details surface shows logical name, validated MIME,
   logical size, source kind, created time, expiry/pin state, and checksum. It
   never shows the physical object path.
6. Export or restore reads verify the object before use. An artifact is never
   executed or opened automatically.

The current web UI already treats `/large_tool_results/` paths as internal and
shows “Large result saved for internal inspection.” AC4 preserves that exact
behavior. It does not add a web artifact browser or change web rendering.

### Failure behavior

- A quota or free-space failure occurs before a canonical reference is
  appended. The tool returns `artifact_quota_exceeded` with a safe, actionable
  message; no unbounded inline fallback is allowed.
- A disk-full, permission, or read-only error removes uncommitted temporary
  files where possible and returns `artifact_store_unavailable`. Existing
  verified objects remain readable.
- A missing object returns `artifact_not_found`; its owner record remains and
  renders an unavailable marker rather than silently deleting history.
- A checksum, encoded-size, metadata, or decompression mismatch quarantines the
  object and returns `artifact_corrupt`. Unverified bytes are never sent to the
  model, renderer, checkpoint decoder, restore flow, or sandbox provider.
- An unsupported artifact/metadata version or compression value fails closed.
  No reader guesses an encoding.
- A crash before object publication leaves only a temporary file. A crash after
  object publication but before the owner record leaves an unreferenced object,
  which the orphan grace period collects.
- A crash after the owner record is durable is recoverable: startup rebuilds
  reachability and verifies the referenced object before use.
- If the feature is disabled, new oversized results are safely summarized or
  rejected according to context policy; they are not redirected to transient
  `StateBackend`. The read path remains available for existing references.

## Alternatives considered

### Keep full tool outputs inline

Rejected. It amplifies JSONL/events/checkpoints, pushes unbounded data into model
context, complicates SSE replay, and forces every consumer to implement its own
truncation and deletion logic.

### Make `runtime_context_payload_blobs` the desktop byte store

Rejected. It would keep artifact bytes in embedded Postgres, conflict with the
file-native desktop target, and duplicate bytes when an object is also needed
for Deep Agents, checkpoints, or file history. That sidecar remains a
Postgres/encryption concern for non-desktop profiles.

### Store one mutable file per tool call

Rejected. Tool-call IDs do not deduplicate repeated content, mutable paths make
checksum and recovery ambiguous, and rename/delete semantics become another
catalog. Virtual large-result paths are aliases to immutable objects, not
physical filenames.

### Use SQLite as the artifact catalog and reference-count authority

Rejected. SQLite is intentionally disposable. A corrupt/deleted index must not
lose bytes, ownership, retention, or legal-hold evidence.

### Use a shared machine-wide content store

Rejected. Cross-workspace digest equality leaks content correlation and makes
tenant/workspace deletion and quota accounting harder. Deduplication is limited
to one AC1 workspace root.

### Store compressed-byte hashes

Rejected. Compression versions/settings would change identity and prevent
stable deduplication. SHA-256 and `logical_size` cover uncompressed logical
bytes; compression is a replaceable storage detail.

### Select Zstandard from the target-overview example

Rejected for v1. AC1 froze `ArtifactRefV1.compression` to `none | gzip`.
Changing that enum in AC4 would violate contract parity. A future compression
format requires `ArtifactRefV2` and an explicit compatibility migration.

### Let each AC5–AC8 feature own a blob directory

Rejected. It duplicates atomic-write, hash, quota, retention, legal-hold,
repair, and export behavior and makes a byte reachable from multiple
inconsistent catalogs.

### Use an object-storage service in desktop mode

Rejected. It adds network, credentials, availability, and data-residency
dependencies to a local-first capability. A future non-desktop adapter may
implement the same `ArtifactStorePort`; AC4 does not change that deployment.

## Architecture and ownership

### Component topology

```text
tool / MCP / attachment / checkpoint / browser / sandbox / AC5 history producer
  -> ArtifactCommitCoordinator
       1. ArtifactStorePort.put(logical byte stream)
            -> AC2 ObjectStore.put(encoded bytes)   # objects/sha256 (AC2-owned)
       2. append canonical owner record containing ArtifactRefV1
  -> rebuildable SQLite reachability/refcounts/verification cache (AC4-owned)

Deep Agents CompositeBackend
  default                 -> StateBackend
  /drafts/                -> existing DraftBackend
  /subagents/             -> existing subagent trace backend
  /large_tool_results/    -> ArtifactBackend   # reads AC2 objects/sha256
  /workspace/             -> AC5 BrokeredWorkspaceBackend
```

`ArtifactStorePort` is AC4's facade over AC2's `ObjectStore`: it computes the
logical-byte digest, applies compression/MIME/preview policy, and delegates the
actual atomic byte commit and verification to AC2. AC2 owns immutable byte
storage under `objects/sha256/`; AC4 owns the encoded-bytes decision, the
reference model, and the reachability projection.
`ArtifactCommitCoordinator` owns the “object first, canonical reference second”
ordering. The producer owns the canonical record and retention purpose.
`ArtifactBackend` adapts the existing Deep Agents filesystem protocol; it does
not become a second store.

### Ownership rules

| Concern                                                                                                     | Canonical owner                                                                                           | Boundary                                                                       |
| ----------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Content-addressed byte storage: atomic object I/O, dedupe, digest verification, quarantine of corrupt bytes | **AC2 `runtime_adapters/file/objects` (`ObjectStore`)**                                                   | App-owned AC1 workspace root only; consumed by AC4 through `ArtifactStorePort` |
| Artifact identity typing, compression selection, preview, reference/reachability model                      | `agent_runtime/persistence/artifacts` + `runtime_adapters/file/artifacts` (facade over AC2 `ObjectStore`) | No Electron or concrete-filesystem dependency beyond AC2's store               |
| Artifact contracts, retention classes, ports, error vocabulary                                              | `agent_runtime/persistence/artifacts`                                                                     | No Electron or concrete-filesystem dependency                                  |
| Tool/context threshold and preview policy                                                                   | `agent_runtime/context`                                                                                   | Shared by built-in and MCP result shaping                                      |
| `/large_tool_results/` protocol adaptation                                                                  | `agent_runtime/capabilities/backends`                                                                     | Read/write only through `ArtifactStorePort` and a canonical-reference sink     |
| Canonical ownership/reachability record                                                                     | Owning event/message/checkpoint/mutation workflow                                                         | Object metadata never invents an owner                                         |
| SQLite object/refcount/search projection                                                                    | Desktop file runtime adapter                                                                              | Rebuildable; never sole evidence                                               |
| Root provisioning and owner-only ACL verification                                                           | Electron desktop supervisor under AC1                                                                     | Electron does not interpret artifact content                                   |
| Renderer presentation/export request                                                                        | Desktop renderer through existing facade/IPC paths                                                        | No physical path or direct object read                                         |

The AI service may use Python filesystem APIs for its app-owned
`RUNTIME_FILE_STORE_ROOT`. That is not authority to access an AC5 user root.
User-root I/O remains exclusively behind Electron main and the AC5 broker.

### SOLID, DRY, KISS, and single source of truth

- **Single responsibility:** the store owns bytes; context policy decides
  inline/offload; owner records own reachability; AC10 owns final retention UX.
- **Open/closed:** another storage adapter implements `ArtifactStorePort`
  without changing tool, checkpoint, browser, or sandbox producers.
- **Liskov substitution:** in-memory and desktop-file stores pass the same
  put/open/verify/delete conformance suite and return the same typed failures.
- **Interface segregation:** producers receive put/read/reference operations,
  not a broad runtime store or host filesystem.
- **Dependency inversion:** runtime/context code depends on product ports and
  `ArtifactRefV1`, not `pathlib`, SQLite, Electron, or cloud SDKs.
- **DRY:** one threshold policy, compression algorithm, object layout, preview
  redactor, reachability rebuild, and GC implementation serves every artifact
  kind.
- **KISS:** one digest algorithm, one v1 compression algorithm, one object per
  digest per workspace, and no dual-write byte store.
- **Single source of truth:** the object owns logical bytes; the owner record
  owns reachability; the physical metadata sidecar owns decoding facts; SQLite
  owns nothing durable.

## Canonical bytes, thresholds, MIME, preview, and limits

> **Deferred / not in the light build.** The delivered offload uses a **single
> ~8,000-token threshold** (`ToolResultOffloader.INLINE_TOKEN_BUDGET`) on
> tool-result output only, via the shared `ContextPayloadManager`. The byte/token
> threshold pairs, per-kind hard ceilings (512 MiB, etc.), the deterministic
> `none`/`gzip` compression policy, MIME sniffing, logical-name normalization, and
> redacted-preview policy in this section are **design intent** — the shipped code
> stores UTF-8 bytes verbatim with a raw 200-char preview.

### Logical-byte rules

The caller supplies a byte stream plus a content representation:

- `binary`: hash the bytes exactly as received.
- `utf8_text`: validate UTF-8 and hash `text.encode("utf-8")` exactly. Do not
  normalize Unicode, line endings, or trailing whitespace.
- `canonical_json`: validate a JSON value, serialize RFC 8785 canonical JSON as
  UTF-8, and hash those bytes. Non-finite numbers, duplicate object keys at a
  textual JSON boundary, cycles, and non-JSON host objects are rejected.

The store never hashes a Python `repr`, base64 wrapper, gzip stream, filename,
MIME claim, preview, or metadata. `sha256` and `logical_size` always describe
the logical-byte stream. An empty payload is valid and has the standard SHA-256
digest of zero bytes.

### Inline and artifact thresholds

| Payload class                                | Inline rule                                                                                  | Artifact rule                                 |                          Hard ceiling |
| -------------------------------------------- | -------------------------------------------------------------------------------------------- | --------------------------------------------- | ------------------------------------: |
| Tool/MCP/context textual or canonical JSON   | Inline only when logical bytes are at most 32 KiB **and** estimated tokens are at most 8,192 | Offload when either threshold is exceeded     |                    512 MiB per result |
| Deep Agents `/large_tool_results/` write     | Never durable-inline                                                                         | Always artifact                               |                               512 MiB |
| User attachment submitted with a desktop run | Metadata inline; bytes never inline in the durable message                                   | Always artifact, including files under 32 KiB |         100 MiB each; 250 MiB per run |
| Screenshot                                   | Metadata inline                                                                              | Always artifact                               |              10 MiB and 16 megapixels |
| Browser/sandbox download or generated file   | Metadata inline                                                                              | Always artifact                               |                          512 MiB each |
| LangGraph or Monty checkpoint                | Typed checkpoint envelope inline; serialized state as artifact                               | Always artifact                               | 8 MiB for Monty; 64 MiB for LangGraph |
| AC5 file-history preimage                    | Mutation metadata inline                                                                     | Always artifact, including empty files        |                          512 MiB each |
| Draft body                                   | Inline through the owning draft policy up to 256 KiB                                         | Artifact above 256 KiB                        |                                16 MiB |

Deployment policy may lower these values. Raising a hard ceiling requires an
AC4 revision with memory, disk, crash, and denial-of-service evidence. The
store streams data and never buffers an entire hard-ceiling object in RAM.

Workspace defaults are a 5 GiB soft quota, 10 GiB hard quota, one million
objects, four concurrent writers, and 32 read leases. A write also requires
free disk after reservation of at least `max(1 GiB, 10% of volume capacity)`.
Temporary raw plus gzip candidates count against reservation. Deduplicated
reuses consume reference quota but not object-byte quota.

### Canonical compression policy

AC1 permits only `none` and `gzip`. Compression is deterministic from logical
bytes alone so the same digest cannot choose two encodings:

1. Payloads smaller than 4 KiB or larger than 64 MiB use `none`.
2. For all other payloads, stream once to a raw temporary file and once through
   gzip level 6 with `mtime=0`, no original filename/comment, and a fixed OS
   header byte.
3. Select `gzip` only when `gzip_size <= floor(logical_size * 7 / 8)`.
4. Otherwise select `none`.

The 12.5% savings rule and 64 MiB compression ceiling are v1 constants.
`stored_size` is the selected encoded payload size and excludes the metadata
sidecar. Readers reject concatenated gzip members, trailing encoded bytes,
decompression beyond `logical_size`, and unknown compression values.

### MIME and logical names

- Producer MIME and filename are untrusted hints.
- The store derives MIME from bounded magic-byte inspection, then UTF-8/text
  validation, and falls back to `application/octet-stream`.
- A conflicting producer claim is retained only as redacted diagnostic
  metadata; `ArtifactRefV1.mime_type` contains the validated value.
- MIME parameters are stripped except a validated `charset=utf-8` for textual
  types. The normalized value is lowercase and at most 255 characters.
- Logical names are NFC-normalized display metadata, stripped of path
  components/control characters, and capped at 255 Unicode scalar values. They
  never select a storage path and are not part of `ArtifactRefV1`.
- Executable, script, archive, document, and image MIME values are descriptive,
  not permission to execute, parse with an unsafe library, or auto-open.

### Preview policy

- `preview_utf8` is at most 4,096 Unicode scalar values and is produced only
  after secret/PII redaction.
- UTF-8/JSON `tool_result`, `large_tool_result`, `context`, `attachment`, and
  `draft` writes use the matching redacted text/JSON preview policy. Binary
  representations, checkpoints, Monty snapshots, downloads, screenshots, and
  file-history preimages use `none`.
- A preview is generated from logical bytes, never compressed bytes. Invalid
  UTF-8 yields no preview.
- JSON previews are bounded structural summaries, not arbitrary pretty-print
  copies. Key/value redaction runs before truncation.
- `preview_truncated=true` whenever any logical content was omitted or
  redacted. A preview is presentation/context help and is never valid for
  restore, export, checksum verification, or legal-hold evidence.

## Strict typed contracts

> **Deferred / not in the light build.** None of these contracts ship.
> `ArtifactRefV1`, `ArtifactOwnerV1`, `ArtifactWriteRequestV1`,
> `ArtifactUseRecordV1`, `ContextPayloadRecordV1`, the `ArtifactStorePort` /
> `ArtifactReferenceSinkPort` ports, and the stable `artifact_*` error vocabulary
> are design intent. The delivered reference is AC2's minimal `ObjectRef`
> (`sha256`/`size`/`media_type`/`preview`), and `packages/api-types/src/artifacts.ts`
> does not exist.

AC1's `ArtifactRefV1` is normative. AC4 does not add, remove, rename, loosen, or
reinterpret a field:

```python
class ArtifactRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    artifact_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    logical_size: int = Field(ge=0)
    stored_size: int = Field(ge=0)
    mime_type: str = Field(min_length=1, max_length=255)
    compression: Literal["none", "gzip"]
    kind: Literal[
        "tool_result",
        "large_tool_result",
        "screenshot",
        "download",
        "attachment",
        "draft",
        "file_history",
        "langgraph_checkpoint",
        "monty_checkpoint",
        "context",
    ]
    preview_utf8: str | None = Field(default=None, max_length=4096)
    preview_truncated: bool
```

`artifact_id` must equal `f"sha256:{sha256}"`. Readers validate that invariant.
The storage URI is derived as `artifact://sha256/<sha256>` and is not an extra
reference field. Physical paths are never serialized into runtime records.

App-facing TypeScript uses an equivalent strict shape with the same
snake-case JSON keys already used by runtime events:

```typescript
export interface ArtifactRefV1 {
  readonly version: 1;
  readonly artifact_id: `sha256:${string}`;
  readonly sha256: string;
  readonly logical_size: number;
  readonly stored_size: number;
  readonly mime_type: string;
  readonly compression: "none" | "gzip";
  readonly kind:
    | "tool_result"
    | "large_tool_result"
    | "screenshot"
    | "download"
    | "attachment"
    | "draft"
    | "file_history"
    | "langgraph_checkpoint"
    | "monty_checkpoint"
    | "context";
  readonly preview_utf8?: string | null;
  readonly preview_truncated: boolean;
}
```

The broker-specific AC1 JSON fixture layer uses camelCase aliases where a
broker message embeds this shape. Runtime event/storage JSON remains
snake_case, preserving current app-facing conventions. Both validators share
valid/invalid fixtures and enforce digest equality, integer safety, enum
closure, and preview bounds.

### Write and ownership contracts

Bytes travel on a bounded stream; they are never a Pydantic `bytes` field:

```python
class ArtifactOwnerV1(RuntimeContract):
    org_id: str
    workspace_id: str
    conversation_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    tool_invocation_id: str | None = None


class ArtifactWriteRequestV1(RuntimeContract):
    version: Literal[1] = 1
    request_id: UUID
    idempotency_key: str
    owner: ArtifactOwnerV1
    kind: ArtifactKindV1
    representation: Literal["binary", "utf8_text", "canonical_json"]
    declared_mime_type: str | None = None
    logical_name: str | None = None
    expected_logical_size: int | None = None
    retention_class: Literal[
        "transient_7d", "raw_30d", "conversation", "security_evidence"
    ]
    preview_policy: Literal["none", "redacted_text", "redacted_json"]


class ArtifactUseRecordV1(RuntimeContract):
    version: Literal[1] = 1
    reference_id: UUID
    owner: ArtifactOwnerV1
    artifact: ArtifactRefV1
    storage_backend: Literal["local_file"] = "local_file"
    storage_uri: str
    logical_name: str | None = None
    virtual_path: str | None = None
    retention_class: str
    retention_until: AwareDatetime | None
    created_at: AwareDatetime
```

Identity fields come from verified run context. A model, renderer, attachment
metadata map, or broker request cannot supply `org_id`, `workspace_id`,
retention class, storage URI, or a physical path.

The desktop equivalent of an existing `runtime_context_payloads` row is:

```python
class ContextPayloadRecordV1(RuntimeContract):
    version: Literal[1] = 1
    payload_id: str
    run_id: str
    task_id: str | None = None
    tool_invocation_id: str | None = None
    org_id: str
    kind: Literal["tool_result", "context", "artifact", "checkpoint"]
    storage_backend: Literal["local_file"] = "local_file"
    storage_uri: str
    sha256: str
    byte_size: int
    mime_type: str
    redaction_state: Literal["offloaded"] = "offloaded"
    retention_until: AwareDatetime | None
    artifact: ArtifactRefV1
    created_at: AwareDatetime
```

Mapping is fixed:

- `tool_result` and `large_tool_result` → `tool_result`
- `context` → `context`
- `langgraph_checkpoint` and `monty_checkpoint` → `checkpoint`
- every other `ArtifactRefV1.kind` → `artifact`

For desktop-local payloads, no `runtime_context_payload_blobs` row is written.
For non-desktop Postgres profiles, AC4 does not select `local_file` or change
existing table/blob behavior.

### Ports

```python
class ArtifactStorePort(Protocol):
    async def put(
        self,
        request: ArtifactWriteRequestV1,
        chunks: AsyncIterator[bytes],
    ) -> ArtifactRefV1: ...

    async def open_verified(
        self,
        *,
        owner: ArtifactOwnerV1,
        reference: ArtifactRefV1,
        purpose: Literal["model_read", "preview", "export", "restore", "checkpoint"],
    ) -> ArtifactReadLease: ...

    async def verify(
        self,
        *,
        owner: ArtifactOwnerV1,
        reference: ArtifactRefV1,
        full: bool,
    ) -> ArtifactVerificationResultV1: ...

    async def quarantine(
        self,
        *,
        owner: ArtifactOwnerV1,
        reference: ArtifactRefV1,
        reason: ArtifactQuarantineReasonV1,
    ) -> None: ...


class ArtifactReferenceSinkPort(Protocol):
    async def append_use(self, record: ArtifactUseRecordV1) -> None: ...
```

`ArtifactReadLease` exposes a bounded async byte iterator and `aclose()`. It is
runtime-only and not serializable. A put is not externally durable until its
owner record is appended. `ArtifactCommitCoordinator` calls `put`, appends the
owner record, and only then acknowledges a tool result, attachment, checkpoint,
download, or mutation snapshot.

### Stable errors

- `artifact_disabled`
- `artifact_invalid_request`
- `artifact_invalid_content`
- `artifact_mime_invalid`
- `artifact_too_large`
- `artifact_quota_exceeded`
- `artifact_store_unavailable`
- `artifact_not_found`
- `artifact_reference_denied`
- `artifact_corrupt`
- `artifact_version_unsupported`
- `artifact_compression_unsupported`
- `artifact_idempotency_conflict`
- `artifact_busy`
- `artifact_cancelled`

Safe errors contain no content, logical name, physical path, token, raw
exception, or cross-workspace existence signal.

## Deep Agents and context-payload integration

### Production offload path

> **Delivered via the existing synchronous seam.** The shipped path uses
> `ContextPayloadManager.prepare_tool_output(...)` (synchronous) with an
> `OffloadWriter`, wired by `ToolResultOffloader`. The async
> `ContextPayloadOffloadPort` / `aprepare_tool_output()` seam and the
> owner-record integration below are **deferred**.

AC4 adds an async production seam:

```python
class ContextPayloadOffloadPort(Protocol):
    async def offload(
        self,
        *,
        content: str | JsonValue,
        owner: ArtifactOwnerV1,
        kind: Literal["tool_result", "large_tool_result", "context"],
        logical_name: str | None,
    ) -> ArtifactUseRecordV1: ...
```

`ContextPayloadManager.aprepare_tool_output()` applies the AC4 byte/token
threshold, calls this port, and returns a strict managed payload containing the
bounded preview, virtual path, and `ArtifactRefV1`. Every production built-in
and MCP result passes through this shared result-shaping seam before it reaches
Deep Agents or the event producer.

The current synchronous `prepare_tool_output()` remains only as a pure
compatibility helper for inline/summarization unit tests. It cannot accept a
durable writer after AC4. Production registration tests fail if a tool or MCP
callable can return an oversized payload without the async shaper.

### `/large_tool_results/` route

`ArtifactBackend` implements the pinned Deep Agents `BackendProtocol` and is
constructed per run with verified owner context:

- `write/awrite` accepts only one normalized alias segment, stores content as
  `large_tool_result`, appends its canonical context-payload/use record, and
  returns the alias.
- `read/aread` resolves only aliases reachable from the bound
  conversation/run and returns bounded decoded text slices.
- `ls/als`, `glob/aglob`, and `grep/agrep` operate on reachable aliases with
  result/file/byte/time limits.
- `edit/aedit` is denied; immutable artifacts are replaced by a new object and
  a new owner record.
- Physical paths, cross-workspace digests, object enumeration, delete, and
  arbitrary URI reads are impossible through the backend.

The canonical alias is:

```text
/large_tool_results/<22-character base64url token>
```

The token is 128 random bits, no padding, and is scoped by the owner record.
It is not the object path and does not grant access without verified runtime
context. Existing incoming `call_<id>` aliases from the pinned Deep Agents
version are accepted after strict `[A-Za-z0-9_-]{1,128}` validation and
immediately mapped to a generated 22-character canonical alias. The canonical
alias is the only path returned to new callers or persisted in owner records.

The route is inserted alongside, not instead of, `/drafts/` and
`/subagents/`. AC5 later adds `/workspace/`. Prefix-overlap and route-order
contract tests are mandatory.

## Persistence, atomicity, and recovery

> **Mostly deferred.** What ships is AC2's atomic object `put`/verify-on-read
> (temp → `fsync` → `os.replace` → readback). The `.meta.json` decoding sidecar,
> `quarantine/` tree, the artifact SQLite projections
> (`artifact_objects`/`artifact_references`/`artifact_aliases`/`artifact_leases`/
> `artifact_gc_queue`), startup repair/scrub, and idempotency-key handling in this
> section are **design intent**.

### Object layout (AC2-owned)

The object byte layout is AC2's, under AC1's workspace root. AC4 writes an
adjacent immutable metadata sidecar and manages quarantine through the same AC2
primitive; there is no separate `artifacts/objects` tree:

```text
objects/                                # AC2-owned content-addressed bytes
└── sha256/
    └── <ab>/<cd>/
        ├── <64-hex>              # object bytes (AC2 ObjectStore)
        └── <64-hex>.meta.json    # AC4 immutable decoding sidecar
quarantine/
└── <reason>/
    └── <utc>-<digest>-<random>.{obj,json}   # AC4-managed, via AC2 atomic rename
tmp/                                     # AC2-owned same-volume staging
```

The immutable metadata sidecar contains only metadata schema version, digest,
logical/stored sizes, compression, creation time, and object-store format. It
contains no owner, logical name, preview, physical source path, user content,
token, or retention state. Artifact kind and MIME are per-reference facts and
remain in owner records.

Directories are `0700` and files `0600` on macOS. Windows ACLs grant the
current user and `SYSTEM` only. AC1 storage-root readiness must verify these
before AC4 enables.

### Atomic put (delegated to AC2's `ObjectStore`)

AC4 prepares the encoded bytes and metadata; the atomic byte commit is AC2's
`ObjectStore.put`. Since the desktop is single-writer (AC2), there is **no
cross-process digest lock**:

1. AC4 validates owner, request, expected size, quota reservation, and idempotency
   key before reading content.
2. AC4 streams logical bytes while calculating SHA-256 and logical size, and
   builds the deterministic gzip candidate only in the eligible size range.
3. AC4 rejects size mismatch/overflow and chooses the canonical encoding.
4. AC4 hands the encoded bytes and the metadata sidecar to AC2's `ObjectStore.put`,
   which streams to a same-volume temp file, `fsync`s, atomically renames into the
   digest path, `fsync`s the shard directory, and reads the object back. The
   object bytes are the publication point; readers ignore a metadata sidecar
   without its object.
5. If the digest already exists, AC2 verifies it and reuses it on exact match; AC4
   discards its candidate. Only a successful readback returns `ArtifactRefV1`.
6. AC4 releases its quota reservation and the coordinator appends the canonical
   owner record, and only then exposes the reference.

No rename crosses a filesystem, and a process never overwrites an existing
digest. If an existing digest fails verification, both the existing
representation and the new candidate are quarantined, the write fails with
`artifact_corrupt`, and no collision is silently resolved.

### Canonical records and rebuildable SQLite

Canonical reachability comes only from records that own the bytes:

- message attachment records;
- tool/context payload events;
- checkpoint records;
- browser/sandbox artifact events and manifests;
- draft-version records;
- AC5 mutation intents/file-history records;
- explicit user pin/export references.

The immutable object metadata sidecar describes decoding but does not pin an
object. A `put` followed by a crash before owner append therefore produces an
orphan, not a hidden reference.

SQLite projects:

- `artifact_objects(digest, sizes, compression, state, verified_identity, verified_at)`
- `artifact_references(reference_id, digest, owner_kind, owner_id, retention_until, held)`
- `artifact_aliases(owner_scope, virtual_alias, reference_id)`
- `artifact_leases(digest, process_id, expires_at)`
- `artifact_gc_queue(digest, eligible_at, attempt, state)`

`refcount` is a query over live `artifact_references` or a transactionally
maintained cached column verified against that table. It is never authoritative.
Deleting `index/catalog.sqlite3` and replaying canonical records plus immutable
object metadata must recreate identical reachability, aliases, retention
deadlines, and object state.

### Startup repair and verification

- Delete abandoned temporary directories older than 24 hours after proving no
  live process owns them.
- Blob without metadata: verify its path/digest and move it to
  `quarantine/orphan_metadata` after the 24-hour orphan grace.
- Metadata without blob: mark every owner reference unavailable and quarantine
  the metadata.
- Invalid metadata/version/path: quarantine; never infer compression.
- Rebuild or reconcile SQLite before accepting GC. Reads can fall back to
  canonical owner lookup while rebuild runs; writes require quota accounting to
  be ready.
- On the first read after each app boot, verify encoded size, decode bounds,
  logical size, and full SHA-256. Cache the verified file identity/mtime/size in
  SQLite for later non-critical reads.
- Checkpoint, restore, export, and AC5 file-history reads always perform full
  verification, regardless of cache.
- An idle scrub verifies at least 1% of objects per day (minimum 100, maximum
  10,000) and completes a full pass every 30 days. It pauses on battery saver,
  user activity, or quota pressure.

### Idempotency

`(workspace_id, idempotency_key)` maps to a request digest and resulting
`ArtifactRefV1` in the rebuildable projection backed by the canonical owner
record. Retrying the same key/request returns the same reference. Reusing a key
with a different owner, kind, representation, expected size, or logical digest
returns `artifact_idempotency_conflict`.

An uncertain retry before an owner record exists may recompute the object and
deduplicate by digest. It must then append exactly one owner record using that
record's own idempotency key.

## Retention, deletion, GC, legal hold, and export

> **Deferred / not in the light build.** No retention classes, deletion cascade
> over artifact references, legal hold, garbage collection, quarantine, or
> export/backup for artifacts are implemented. Offloaded objects are written and
> read; nothing reclaims or expires them yet. This entire section is design intent.

### Reference retention

Default classes are fixed for this track:

- `transient_7d`: browser accessibility snapshots/screenshots, remote-sandbox
  input snapshots, intermediate interpreter/checkpoint state.
- `raw_30d`: raw tool/connector results, downloads, generated files, file
  history, terminal recovery checkpoints, diagnostic traces.
- `conversation`: explicit user attachments and user-kept drafts; retained
  while the owning conversation/message exists.
- `security_evidence`: only a separately classified security workflow may use
  it; duration comes from deployment policy and content minimization still
  applies.

AC6–AC9 may select only these classes and may lower duration. They cannot
invent a hidden store or silently extend retention. A user pin creates a new
explicit `conversation` reference; it does not mutate another reference.

### Deletion and legal hold

- Conversation deletion tombstones all attachment, result, checkpoint, draft,
  and child-task references owned solely by that conversation.
- Expiry removes the reference, not immediately the object.
- Legal hold pins every reachable reference and object. It does not copy bytes
  into another store.
- A hold released after normal expiry makes the reference eligible on the next
  sweep.
- Workspace deletion first blocks writes, revokes leases, computes a dry-run
  manifest, and then removes canonical owner records and eligible objects.
- Artifact retention never deletes AC5 user workspace files, browser profiles,
  backend tokens, or remote-provider data.
- Deletion is best effort at the filesystem/SSD level; secure erase and OS
  backup deletion are not claimed.

### Garbage collection

An object is eligible only when:

1. rebuilt live reference count is zero;
2. no legal hold or export pin reaches it;
3. no read lease is live;
4. its object/last-reference age exceeds the 24-hour orphan grace;
5. the canonical record scan and SQLite projection agree; and
6. the store is not in repair/read-only mode.

GC atomically renames blob and metadata into `quarantine/gc_pending`, `fsync`s
both source and quarantine directories, appends deletion evidence, and then
unlinks. A crash resumes from the quarantine entry. A checksum-corrupt object
uses `quarantine/corrupt` and remains seven days for diagnostics unless user
deletion, workspace deletion, or policy requires earlier removal. Legal hold
can pin corruption evidence but never makes corrupt bytes readable.

### Export and backup

Export contains canonical owner records, one copy of each reachable logical
object, an artifact manifest with SHA-256/size/MIME/compression/kind, and layout
version. It excludes SQLite WAL/SHM, temporary/quarantine data by default,
tokens, browser profiles, and physical AC5 paths.

Backup inputs are canonical JSONL plus AC2's `objects/sha256` (object bytes and
their metadata sidecars). Restoring requires manifest verification before the
workspace becomes writable.

## Trust, security, and privacy

### Actors and authorization

- Verified runtime identity supplies org, user, workspace, conversation, run,
  and task scope.
- The model may receive a virtual alias only after policy permits the producing
  tool. The alias is not authority outside the bound runtime context.
- The renderer may request preview/export through typed app APIs; it never
  receives `RUNTIME_FILE_STORE_ROOT`, object paths, SQLite access, or an
  arbitrary artifact URI fetcher.
- Electron provisions the app-owned root but does not grant the AI worker an
  AC5 user path.
- Remote sandboxes/browser workers receive only explicitly approved streamed
  objects, not the artifact root.

### Threats and required controls

| Threat                                  | Required control                                                               | Verification                           |
| --------------------------------------- | ------------------------------------------------------------------------------ | -------------------------------------- |
| Cross-workspace digest probe            | Workspace-local namespaces and verified owner lookup before existence response | Indistinguishable missing/denied tests |
| Path traversal or digest-path injection | Path derived only from validated lowercase 64-hex digest                       | Malformed digest corpus                |
| Partial/torn object                     | Temp + fsync + atomic rename + metadata publication + readback                 | Kill at every write step               |
| Corrupt/compression-bomb object         | Stored/logical bounds, single gzip member, full logical hash                   | Mutation/fuzz/bomb corpus              |
| Duplicate durable bytes                 | One object path; owner records contain refs only; no local-file blob sidecar   | Disk/DB/JSONL content-canary scan      |
| Secret leakage in preview/logs          | Redaction before preview; allowlisted structured telemetry                     | Secret-shaped corpus                   |
| Malicious attachment/download           | MIME is untrusted metadata; no auto-open/execute; AC5 export quarantine        | Polyglot/executable/archive tests      |
| Checkpoint deserialization attack       | Verify digest/version; owning decoder only; no pickle introduced by AC4        | Wrong-kind/ABI/corruption tests        |
| GC deletes held/live content            | Canonical rebuild, hold/pin/lease checks, quarantine-before-unlink             | Retention/hold/crash matrix            |
| Same-user local process reads plaintext | Owner-only OS permissions and explicit documented boundary                     | ACL/mode packaging tests               |

Plaintext artifacts can contain credentials that a tool or user supplied.
AC4 minimizes previews/logging but cannot guarantee source content has no
secret. Same-OS-user malware and local administrators remain outside this
consumer desktop boundary. Full-disk encryption, managed backup, KMS, WAF, and
SIEM are deployment controls and are not claimed by this local store.

### Sensitive-workflow accountability

For every artifact use, durable records answer:

- who/which verified run produced or selected it;
- which tool, attachment, checkpoint, browser, sandbox, or mutation workflow
  owns the reference;
- what logical digest/size/MIME/kind was stored;
- what approval/grant was associated with any external transfer or host
  mutation;
- where the canonical object and owner record live;
- which retention class/legal hold applies; and
- when verification, quarantine, export, reference deletion, and physical GC
  occurred.

Local operational logs alone are not immutable audit or SIEM evidence.

## Observability and audit

### Structured events

- `artifact.write_started`
- `artifact.write_deduplicated`
- `artifact.write_committed`
- `artifact.reference_committed`
- `artifact.read_started`
- `artifact.read_verified`
- `artifact.read_failed`
- `artifact.quarantined`
- `artifact.reference_expired`
- `artifact.gc_eligible`
- `artifact.gc_completed`
- `artifact.gc_failed`
- `artifact.index_rebuilt`
- `artifact.quota_pressure`

Fields are limited to workspace/run/conversation/task/tool opaque IDs, digest,
kind, compression, logical/stored sizes, dedupe flag, retention class, result,
safe error, duration, verification mode, and correlation IDs. Logs exclude
content, preview, logical filename, physical path, user workspace path,
attachment data, tokens, and unredacted producer metadata.

### Metrics

- `runtime_artifact_writes_total{kind,outcome,deduplicated}`
- `runtime_artifact_write_seconds{kind}`
- `runtime_artifact_logical_bytes_total{kind}`
- `runtime_artifact_stored_bytes_total{compression}`
- `runtime_artifact_dedupe_bytes_saved_total`
- `runtime_artifact_reads_total{purpose,outcome}`
- `runtime_artifact_verify_seconds{mode}`
- `runtime_artifact_checksum_failures_total{reason}`
- `runtime_artifact_objects{state}`
- `runtime_artifact_workspace_bytes{state}`
- `runtime_artifact_quota_ratio`
- `runtime_artifact_orphans`
- `runtime_artifact_gc_total{outcome}`
- `runtime_artifact_gc_lag_seconds`
- `runtime_artifact_index_rebuild_seconds`

Digest values are trace/event fields only when needed for diagnosis; they are
not metric labels.

### Audit

Audit events cover reference creation/deletion, user pin/export, corruption,
quarantine, legal-hold decisions, and GC evidence. Routine model reads may be
aggregated in runtime events rather than one security-audit row per page.
Audit includes verified actor/service, owner scope, kind, digest, sizes,
approval/grant IDs where applicable, retention/hold, action, outcome, and
correlation ID. It excludes bytes, previews, physical paths, and secrets.

## Comprehensive test plan

### Unit and strict contract tests

- Pydantic and TypeScript fixtures accept every valid `ArtifactRefV1` and
  reject unknown fields, unsafe integers, mismatched `artifact_id`/`sha256`,
  uppercase/short digests, invalid enums, negative sizes, and long previews.
- Logical-byte vectors cover binary, exact UTF-8 without normalization, RFC
  8785 JSON, empty content, non-finite JSON, malformed UTF-8, and chunk-boundary
  independence.
- Compression vectors prove deterministic gzip headers/settings, the 4 KiB and
  64 MiB boundaries, exact 7/8 decision, single-member decoding, and stable
  digest across `none`/`gzip`.
- MIME tests cover conflicting claims, polyglots, text/binary ambiguity,
  charset stripping, malformed values, and octet-stream fallback.
- Preview tests prove redaction-before-truncation, Unicode-safe limits, JSON
  summaries, binary suppression, and `preview_truncated`.
- Port conformance runs against in-memory and desktop-file stores.

### Integration and context tests

- Every built-in and MCP result at threshold minus one, threshold, and plus one
  takes the expected inline/offload path.
- Token threshold triggers offload even below 32 KiB; byte threshold triggers
  even below 8,192 tokens.
- A production tool cannot return an oversized unshaped payload.
- `runtime_context_payloads` semantics map exactly to local-file owner records;
  no desktop `runtime_context_payload_blobs` write occurs.
- `CompositeBackend` preserves `/drafts/`, `/subagents/`, default state routes,
  and adds `/large_tool_results/` without prefix confusion.
- Large-result aliases are run/workspace scoped, pageable, internal in event
  visibility, and rendered with unchanged web-safe text.
- Attachments, screenshots, downloads, checkpoints, sandbox files, drafts, and
  AC5 preimages all use one object format and reference type.

### Atomicity and crash injection

Kill the writer:

- before/while reading chunks;
- before and after raw/gzip temp `fsync`;
- before and after payload rename (via AC2 `ObjectStore.put`);
- before and after directory `fsync`;
- before and after metadata-sidecar rename;
- during post-write verification;
- after put but before owner append;
- after owner append but before acknowledgement;
- during SQLite projection; and
- during GC quarantine rename/unlink.

After each restart, prove there is either no owner reference or one valid,
readable reference; never a visible partial object, duplicate owner record, or
unbounded inline fallback.

### Corruption and adversarial tests

- Bit flips/truncation/appends in raw, gzip, and metadata files.
- Wrong compression, stored/logical size, digest, metadata version, shard, and
  filename.
- Gzip bombs, concatenated members, trailing bytes, deep JSON, output floods,
  tiny chunks, cancellation, and disk-full simulation.
- Existing-digest reuse under concurrent in-process offloads and idempotency-key
  reuse with changed payload (single writer; no cross-process digest race).
- Traversal, separators, Unicode, Windows reserved names, and symlinked object
  shard attempts.
- Cross-workspace digest/alias/ref probing and forged org/run/task fields.
- Secret canaries across JSONL, SQLite, Postgres, objects, previews, events,
  logs, traces, diagnostics, exports, and quarantine manifests.

### Reachability, retention, and recovery

- Rebuild SQLite from canonical owner records/object metadata and compare every
  object, reference, alias, count, deadline, hold, and verification state.
- One object with multiple references/classes remains until the last live,
  unheld reference expires.
- Conversation/task/workspace deletion cascades only its references.
- Legal hold blocks expiry and GC; release resumes eligibility.
- Open leases block GC and expire safely after process death.
- Orphan grace, corrupt quarantine, user deletion, and GC crash recovery follow
  exact deadlines.
- Missing object remains visible as unavailable and is never synthesized from
  preview/event copies.

### Performance, platform, and regression

- Stream the 512 MiB object limit with bounded RSS; hash/compress throughput and
  event-loop latency meet the implementation-spec budget.
- One million-reference SQLite rebuild and 10 GiB quota scan stay within the
  AC10 operational window.
- macOS arm64/x64 and Windows x64 packaged tests cover Unicode/long user-data
  paths, owner-only modes/ACLs, fsync/rename semantics, process races, disk
  removal/read-only transitions, and app update/restart.
- Existing in-memory/Postgres adapters, `RUNTIME_STORE_BACKEND=postgres`,
  Postgres schema/migrations, SSE replay, approval, frontend, and web build
  suites remain unchanged.
- A non-desktop profile cannot instantiate the local artifact store or route,
  even if `RUNTIME_ENABLE_DESKTOP_ARTIFACTS=true` is set.

Normal PR CI uses temporary files and fake producers; it requires no live LLM,
network, cloud object store, or production credential.

## Rollout and backout

### Rollout

1. Land strict contracts, fixtures, in-memory port fake, metrics, and store
   activation checks with `RUNTIME_ENABLE_DESKTOP_ARTIFACTS=false`.
2. Land desktop-file object put/open/verify/quarantine and crash tests without
   production producers.
3. Build/rebuild the SQLite projection and run an offline object-store
   soak/scrub on macOS and Windows.
4. Wire context/tool result shaping and `/large_tool_results/` for internal
   desktop workspaces with a 1 GiB quota and read-only diagnostics.
5. Wire attachments and AC3 checkpoints after canonical-owner integration is
   proven.
6. Enable AC5–AC8 producers one at a time under their own feature flags.
7. Raise the default to 5 GiB soft/10 GiB hard only after quota, retention,
   deletion, export, and corruption drills pass. AC10 owns default-on rollout.

Stop conditions are any checksum mismatch, visible partial object, duplicate
payload bytes in another durable desktop store, cross-workspace read,
unbounded result, secret preview/log leak, held/live object deletion, inability
to rebuild reference counts, web/Postgres behavior change, or unsupported
platform atomicity.

### Backout

- Disable new writers with `RUNTIME_ENABLE_DESKTOP_ARTIFACTS=false`; keep the
  compatible read/verify/export path enabled for existing refs.
- New oversized results summarize or fail safely. They do not fall back to
  transient `/large_tool_results/` state or duplicate inline storage.
- Quiesce writers before downgrading. Finish or abort temporary writes, rebuild
  reachability, and preserve objects/owner records read-only.
- A prior compatible release may read v1 objects. An incompatible release
  opens the workspace read-only for export and does not rewrite metadata.
- Removing an AC5–AC8 producer does not delete its existing artifacts; normal
  retention continues.
- No migration copies local artifact bytes into Postgres during ordinary
  backout. A full AC2/AC3 store rollback exports each logical object once and
  imports supported references under the separately approved runtime-store
  rollback procedure.

## Acceptance criteria

### Delivered (light) — met

- Offload reuses AC2's object store; identical bytes deduplicate by digest and no
  second byte store is built.
- Every committed object follows AC2's temp + `fsync` + atomic rename +
  verify-on-read path; a missing/corrupt object raises `ObjectStoreError` rather
  than serving unverified bytes.
- Production tool-result offload parks oversized output in the object store and
  rewrites the event to a bounded preview + `/large_tool_results/<sha256>`
  reference through the shared `ContextPayloadManager` seam.
- `/large_tool_results/` resolves through `FileLargeToolResultBackend` behind the
  current `CompositeBackend`; existing `/drafts/`, `/subagents/`, and default
  routes retain behavior.
- Offloaded bytes are not duplicated inline in the durable event payload (the
  `output` is replaced by the preview).
- A durable `AsyncSqliteSaver` checkpointer replaces `InMemorySaver` on the
  desktop file store.
- Desktop-local offload wiring is unavailable outside the `file` backend;
  web/Postgres APIs, migrations, SSE, and UI remain unchanged.

### Deferred / not in the light build

- `ArtifactRefV1` field/semantic compatibility, `none | gzip`, and logical-byte
  hashing distinct from stored bytes.
- Compression, MIME, redacted previews, inline byte/token threshold pairs, and
  per-kind limit/quota behavior matching this PRD.
- A canonical `runtime_context_payloads`-equivalent owner record written before
  acknowledgement (delivered offload references the object inline in the event).
- Attachments, screenshots, downloads, checkpoints, transfers, drafts, and
  file-history preimages using one port/reference/object format.
- SQLite artifact projections and their rebuild; reachability/refcounts/aliases/
  leases/GC queue.
- Retention, conversation/workspace deletion cascade, legal hold, open leases,
  orphan grace, GC, quarantine, and export.

## Definition of done

### Delivered (light)

- Offload write/read wiring, the `/large_tool_results/` + `/subagents/`
  CompositeBackend routes, and the `AsyncSqliteSaver` checkpointer are
  implemented and gated to the desktop `file` backend.
- Offload behavior is covered by
  `services/ai-backend/tests/unit/runtime_adapters/file/test_offload_and_composite_reads.py`.

### Deferred / not in the light build

- The component-local implementation spec pinning metadata schema, gzip vectors,
  filesystem calls, SQLite schema, limits, and benchmark budgets.
- Artifact contracts, ports, desktop-file artifact adapter, commit coordinator,
  reference projection, repair, scrub, GC, events, metrics, and audit.
- The unit/port-conformance/integration/crash/corruption/adversarial/retention/
  load/macOS/Windows suites and the recovery/rebuild/quarantine/legal-hold/backout
  drills.
- `services/ai-backend/docs/features/artifacts.md` and the desktop support docs.
- Repository scans proving one durable owner and no physical-path/secret leaks.

## Critical current and proposed files

### Delivered — actual files

Offload wiring (`services/ai-backend/src/`):

- `runtime_adapters/file/offload.py` — `FileOffloadWriter` (write half).
- `runtime_adapters/file/large_tool_result_backend.py` — `FileLargeToolResultBackend` (read half).
- `runtime_adapters/file/subagent_trace_backend.py` — `FileSubagentTraceBackend` (`/subagents/` reader).
- `runtime_adapters/file/object_store.py` — AC2 `FileObjectStore` + `ObjectRef` (the durable bytes).
- `runtime_worker/tool_result_offload.py` — `ToolResultOffloader` (~8k-token offload decision).
- `runtime_worker/handlers/run.py` — constructs the offloader/backends, gated to the `file` backend.
- `agent_runtime/execution/factory.py` — `_composed_deep_backend` CompositeBackend routing.
- `agent_runtime/execution/deep_agent_builder.py` — `AsyncSqliteSaver` checkpointer.
- `agent_runtime/context/memory/summarization.py` / `contracts.py` — the reused `ContextPayloadManager` / `OffloadWriter` / `ManagedContextPayload` seam.

Tests:

- `services/ai-backend/tests/unit/runtime_adapters/file/test_offload_and_composite_reads.py`.

### Deferred / proposed (not built)

The typed artifact layer — none of these exist:

- `agent_runtime/persistence/artifacts/{__init__,contracts,ports,service,retention}.py`
- `agent_runtime/capabilities/backends/artifact_backend.py`
- `agent_runtime/context/memory/artifact_payloads.py`
- `runtime_adapters/file/{artifacts,artifact_index,artifact_repair}.py`
- `runtime_worker/jobs/artifact_gc.py`
- `tests/contract/artifacts/*`, `tests/integration/runtime_adapters/file/test_artifact_crash_recovery.py`, `tests/integration/runtime_worker/test_artifact_offload.py`
- `packages/api-types/src/artifacts.ts`, `docs/contracts/desktop-broker/v1/artifact-ref-{valid,invalid}.json`
- `services/ai-backend/docs/features/artifacts.md`, `services/ai-backend/docs/specs/desktop-agent-capabilities/ac4-artifact-store.md`

No implementation may import a sibling deployable component's source, expose a
physical artifact path, or add artifact bytes to `packages/api-types`.

## Unresolved risks

There are no open implementation choices in AC4. Accepted residual risks are:

- SHA-256 collision is cryptographically remote but not impossible. A
  same-digest verification mismatch quarantines both representations and stops;
  the implementation never chooses one silently.
- Plaintext objects are readable by another process running as the same OS
  user. Owner-only permissions and content minimization do not provide a
  kernel boundary.
- Large stores make full scrub/export/rebuild expensive. Bounded streaming,
  daily sampling, monthly full verification, and visible progress limit impact
  but do not remove disk cost.
- Filesystem or hardware failure can destroy both object and metadata. AC4
  detects and reports loss; backup/restore under AC10 is required for recovery
  from device failure.
- Preview redaction cannot prove arbitrary source content contains no secret.
  Previews are bounded and optional; high-risk kinds have no preview by
  default.

These risks do not authorize another hash, compression enum, duplicate byte
store, cross-workspace deduplication, unverified read, or transient-state
fallback.
