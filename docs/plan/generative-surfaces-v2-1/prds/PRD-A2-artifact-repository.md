# PRD-A2 — Artifact Repository and content APIs

**Goal.** Add the canonical, tenant-scoped Artifact Repository: immutable revisions,
streaming content storage, metadata projections, exact download, retention-safe
references, and facade-only product APIs. The repository is dark infrastructure in this
PR; no model or tool producer is cut over yet.

## Implementer brief

Read:

1. `../00-overview.md` §§3–8.
2. `../01-sdr.md` §§8, 15, 17, 18.
3. `PRD-A1-artifact-effect-contracts.md`.
4. `services/ai-backend/src/agent_runtime/persistence/ports.py`.
5. `services/ai-backend/src/runtime_adapters/in_memory/`.
6. `services/ai-backend/src/runtime_adapters/file/`.
7. `services/ai-backend/src/runtime_adapters/file/object_store.py`.
8. `services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py`.
9. `services/ai-backend/src/runtime_api/http/routes.py` and
   `agent_runtime/api/conversation_query_service.py`.
10. `services/backend-facade/src/backend_facade/app.py`.

Use each service's own virtual environment. Do not import a storage adapter into the
domain package; inject ports at app construction.

## Context

Today, model-authored drafts, tool-result payloads, surface state, and local workspace
content have separate lifecycles. A canonical artifact must survive replay, support
multiple revisions, stream large bodies, and remain independent from whether it is
currently shown on the canvas or eventually committed externally.

Artifact creation is app-internal and reversible. It therefore does not use the
external-effect approval path. Authorization still applies: artifacts are always
scoped by verified `org_id` and `user_id`.

## Interfaces consumed

- A1 `Artifact`, `ArtifactRevision`, ids/refs, digest rules, and artifact ledger events.
- Existing `RuntimePersistencePort` selection and adapter patterns.
- Existing `FileObjectStore` content-addressed object primitive.
- Existing runtime identity, `_run_for_scope`, event append, replay, and facade
  forwarding conventions.

## Interfaces exposed

### Domain ports

Create `services/ai-backend/src/agent_runtime/artifacts/`:

```python
class ArtifactMetadataStorePort(Protocol):
    async def create_artifact(...)
    async def append_revision(...)
    async def get_artifact(...)
    async def get_revision(...)
    async def list_artifacts(...)
    async def soft_delete(...)
    async def list_unreferenced_content(...)

class ArtifactBlobStorePort(Protocol):
    async def put_stream(expected_digest, chunks, byte_limit) -> BlobWriteResult
    async def open_stream(content_ref, *, start=None, end=None) -> AsyncIterator[bytes]
    async def stat(content_ref) -> BlobStat
    async def delete_if_unreferenced(content_ref) -> bool
```

### Application service

`ArtifactService` methods:

- `create_from_stream`
- `create_from_bytes` for bounded internal callers only
- `append_revision_from_stream`
- `get_metadata`
- `get_revision_metadata`
- `stream_revision`
- `list_for_run`
- `promote_source`
- `soft_delete`

### App-facing routes

Through runtime API and facade:

- `POST /v1/agent/runs/{run_id}/artifacts`
- `GET /v1/agent/runs/{run_id}/artifacts`
- `GET /v1/agent/artifacts/{artifact_id}`
- `GET /v1/agent/artifacts/{artifact_id}/revisions/{revision}`
- `GET /v1/agent/artifacts/{artifact_id}/revisions/{revision}/content`
- `POST /v1/agent/artifacts/{artifact_id}/revisions`
- `POST /v1/agent/artifacts:promote`
- `DELETE /v1/agent/artifacts/{artifact_id}`

Mutation routes accept `Idempotency-Key`. Content routes support `Range` where the
selected blob adapter can satisfy it.

## Design

### D1. Domain package and layering

Use:

```text
agent_runtime/artifacts/
  contracts.py
  ports.py
  service.py
  errors.py
  projection.py
```

`agent_runtime/artifacts` may import A1 contracts and general runtime contracts. It may
not import `runtime_api`, FastAPI, postgres, file-system adapters, or facade code.

### D2. Metadata model

Persist artifact and revision metadata separately:

```text
artifact
  org_id, user_id, artifact_id
  conversation_id, run_id, kind, title, media_type
  current_revision, created_by, created_at, updated_at, deleted_at

artifact_revision
  org_id, user_id, artifact_id, revision
  parent_revision, content_ref, content_digest, byte_size
  author, source_ref, created_at
```

Uniqueness:

- `(org_id, artifact_id)`;
- `(org_id, artifact_id, revision)`;
- revision increments are serialized per artifact;
- an idempotency record binds `(org_id, user_id, route, key)` to request digest and
  response.

The database never stores artifact bytes inline.

### D3. Blob semantics

- Blob address is based on SHA-256 content digest, not user filename.
- `content_ref` is an artifact revision ref, resolved through metadata to an internal
  blob key.
- Upload streams to a temporary object while hashing and enforcing a byte cap.
- Only after digest verification does the adapter atomically publish the blob.
- Duplicate bytes deduplicate physically but remain separate logical revisions.
- Failed/cancelled uploads remove temporary state.
- Reads stream; no route materializes an unbounded body.
- An artifact filename is metadata used only for `Content-Disposition`; sanitize CR/LF,
  path separators, control characters, and reserved names.

Initial hard limits, configurable server-side:

| Kind     | Maximum artifact bytes | Inline preview bytes |
| -------- | ---------------------: | -------------------: |
| code     |                 10 MiB |                1 MiB |
| document |                 20 MiB |                2 MiB |
| dataset  |                100 MiB |                2 MiB |
| file     |                250 MiB |              512 KiB |

Routes reject declared or streamed overflow with 413 and leave no revision.

### D4. Adapter parity

Implement:

- in-memory metadata/blob adapters for hermetic tests;
- file metadata adapter plus content-addressed blob files, with atomic replace and
  restart tests;
- postgres metadata adapter and durable blob adapter selected by the existing runtime
  storage configuration.

If production currently uses local object storage, keep that explicit. Do not place a
large `bytea` column into postgres as a shortcut. The postgres metadata transaction
stores only a blob key/ref and digest.

Migration filenames use the next free number discovered at implementation time.
Update schema mirrors, rollback SQL, and `MANIFEST.lock`.

### D5. Atomic service behavior

Create:

1. authorize run scope;
2. stream/hash/write temporary blob;
3. publish or reuse blob;
4. transactionally create metadata and revision 1;
5. append `artifact.created`;
6. return metadata.

Revision:

1. authorize artifact scope and ensure not deleted;
2. require `If-Match` or explicit `parent_revision`;
3. stream/hash/publish bytes;
4. transactionally compare current revision and append next revision;
5. append `artifact.revised`;
6. return metadata.

If event append and metadata cannot share one transaction in an adapter, use the
existing runtime outbox pattern or add a local outbox record in the same metadata
transaction. A successful API response must not expose an artifact whose creation
event can be permanently lost.

### D6. Promotion semantics

`POST /artifacts:promote` accepts:

```json
{
  "run_id": "run_…",
  "source_ref": "message://… | operation://…/result | payload://…",
  "kind": "code | document | dataset | file",
  "title": "…",
  "media_type": "…",
  "suggested_filename": "…"
}
```

The server resolves source bytes itself after authorization. The client may not submit
an arbitrary server-local path as a source. Promotion creates revision 1 and emits
`artifact.promoted` in addition to `artifact.created`, joined by `artifact_id`.

### D7. HTTP behavior

- Identity comes from verified runtime API authentication, never request body/query.
- A missing or foreign artifact returns 404, not 403.
- Content response headers:
  - exact `Content-Type` from validated metadata, falling back to
    `application/octet-stream`;
  - `X-Content-Type-Options: nosniff`;
  - safe `Content-Disposition`;
  - strong ETag derived from `content_digest`;
  - `Accept-Ranges: bytes` only when supported.
- `GET` metadata never returns bytes or physical blob keys.
- Soft-deleted artifacts return 404 to product routes.
- Facade forwards streaming responses without buffering.

### D8. Projection and listing

`ArtifactProjection.fold(events)` is a pure compatibility/reference projection, not the
metadata store. The list API reads the authoritative metadata repository and optionally
cross-checks the projection in tests.

List ordering: `updated_at DESC, artifact_id ASC`, cursor-based, maximum 100. Filters:
`run_id`, `kind`, and `include_deleted=false` only.

### D9. Retention and deletion

- A soft delete records `deleted_at`; it does not immediately delete shared bytes.
- GC calculates references from live artifact revisions, effect proposals, receipts,
  audit exports, and legal holds.
- Delete a physical blob only when no reference remains and the grace period has
  elapsed.
- Conversation/user/org deletion jobs must enumerate artifact metadata and refs.
- A failed GC pass is retryable and never makes metadata point to a missing blob.

## Implementation plan

1. Add domain contracts, ports, errors, and service tests using fakes.
2. Add in-memory adapter and contract suite.
3. Add file adapter and restart/atomicity suite.
4. Add postgres migration/adapter and live-postgres parity test.
5. Wire adapter construction into runtime API app state.
6. Add schemas/routes/query-service methods.
7. Add facade streaming and JSON passthrough routes.
8. Add TypeScript API entities/guards.
9. Add retention enumerator and safe GC command, dark by default.
10. Add event/outbox consistency tests.

## Test plan

### Domain and adapter contract

Run one shared test mixin against all adapters:

- create, fetch, list, revise, conflict, delete;
- same bytes deduplicate, metadata does not;
- expected digest mismatch removes temp data;
- interrupted stream leaves no revision;
- restart preserves metadata and bytes;
- concurrent revision writers yield one success and one 409;
- same idempotency key/digest replays, different digest conflicts;
- range reads return exact bytes.

### Security

- cross-org, cross-user, cross-run, and guessed-id requests return 404;
- filename/header injection is neutralized;
- SVG/HTML/script bytes download with `nosniff` and never execute server-side;
- no content, source physical path, or blob key appears in logs/events;
- content limits hold even when `Content-Length` is absent or false.

### Consistency and recovery

- metadata commit followed by event-process crash is recovered through outbox;
- event retry does not duplicate artifact/revision;
- GC does not remove content referenced by a stage, receipt, or legal hold;
- a dangling blob from a failed metadata transaction becomes safely collectible.

## Definition of done

- [ ] All interfaces listed under “Interfaces exposed” exist.
- [ ] In-memory, file, and postgres implementations pass one contract suite.
- [ ] Content upload/download is streamed and bounded.
- [ ] Route identity/isolation tests cover every endpoint.
- [ ] Create/revise event consistency survives injected crash points.
- [ ] Retention and deletion enumerate all documented references.
- [ ] Facade is the only app-facing network path.
- [ ] No producer is cut over in this PR.
- [ ] Standard DoD passes.

## Out of scope

- Model publication semantics.
- Artifact renderers/editors.
- Workspace host commits.
- External-effect staging.
- Legacy draft backfill.

## Guardrails

- Do not store large bodies in postgres, events, SSE, or API metadata.
- Do not use a client-supplied user/org scope.
- Do not delete content synchronously with soft deletion.
- Do not couple artifact creation to canvas creation.
- Do not expose internal blob paths or signed-storage credentials.
