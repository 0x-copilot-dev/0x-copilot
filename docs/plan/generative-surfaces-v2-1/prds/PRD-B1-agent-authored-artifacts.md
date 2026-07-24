# PRD-B1 — Agent-authored artifacts and draft convergence

**Goal.** Give models, subagents, users, and internal producers a provider-neutral,
explicit way to publish durable code, documents, datasets, and files without pretending
that artifact creation is an external write. Converge `/drafts/` on the canonical
Artifact Repository without creating dual truth.

## Implementer brief

Read:

1. `../00-overview.md` §§3.2–3.5, 5–7.
2. `../01-sdr.md` §§5.2–5.4, 7.3–7.4, 13 S1–S2.
3. `PRD-A2-artifact-repository.md` and `PRD-A3-operation-gateway.md`.
4. `services/ai-backend/src/agent_runtime/capabilities/backends/draft_backend.py`.
5. `services/ai-backend/src/agent_runtime/api/draft_service.py`.
6. `services/ai-backend/src/runtime_worker/handlers/run.py` final-response path.
7. `services/ai-backend/src/agent_runtime/delegation/`.
8. `services/ai-backend/src/agent_runtime/execution/factory.py`.
9. `packages/chat-surface/src/messages/MarkdownText.tsx`.

Do not parse ordinary Markdown or code fences to infer publication. Publication is an
explicit structured action.

## Context

The model already writes text and code. “The LLM never writes code” was only true in
the narrow sense that it does not write renderer implementation. The correct invariant
is:

> The model may author content, including code; it never authors executable UI
> renderer code.

Today, ordinary code remains chat text, while `/drafts/` is durable but separate from
the v2 artifact/surface model. The product needs both:

- a normal answer that stays chat;
- an explicit code/document/dataset/file artifact that is durable and can appear on
  the canvas;
- promotion of an existing answer or tool result after the fact;
- exact attribution when a subagent authored it.

## Interfaces consumed

- A2 `ArtifactService` and routes.
- A3 `OperationGateway`, `ArtifactIntent`, and operation attribution.
- Existing model-visible built-in tool registration.
- Existing Deep Agents filesystem backend mounted at `/drafts/`.
- Existing final-response and subagent event streams.

## Interfaces exposed

### Model-facing built-in

One provider-neutral built-in named `publish_artifact`:

```text
publish_artifact(
  kind,
  title,
  media_type,
  content | content_ref,
  suggested_filename?,
  presentation_preference? = "auto"
) -> {
  status: "created",
  artifact_id,
  revision,
  kind,
  title,
  presentation
}
```

The tool is an app-internal operation with
`effect_class=internal_reversible`. It does not use external-write approval.

### Internal typed content part

Define `ArtifactContentPart` for provider adapters that support structured assistant
output. It has the same semantics as `publish_artifact`; provider-specific content
parts normalize into an internal operation and never become a separate persistence
path.

### User promotion API

Use A2 `POST /v1/agent/artifacts:promote`. Add shared client helpers in
`packages/chat-transport` and UI ports in `packages/chat-surface`.

### Draft compatibility

`/drafts/<name>` becomes an adapter over Artifact Service. Stable methods remain
available to the Deep Agents backend during migration, but the bytes and revision
metadata live canonically in the Artifact Repository.

## Design

### D1. Publication is explicit

Three and only three publication paths:

1. model/subagent calls `publish_artifact`;
2. a trusted provider adapter supplies `ArtifactContentPart`;
3. a user invokes Promote on an existing message/operation result.

The runtime must not create an artifact merely because:

- the response contains a fenced code block;
- a string looks like CSV/JSON/Markdown;
- a tool returned a mapping;
- a message is long;
- Studio mode is active.

### D2. Publication tool behavior

The built-in:

1. obtains run/actor context from `OperationContext`;
2. validates metadata and exactly one of `content`/`content_ref`;
3. streams or resolves bytes through Artifact Service;
4. creates revision 1;
5. emits canonical artifact events;
6. lets presentation policy decide canvas/card/none;
7. returns a bounded safe summary.

Limits use A2 kind limits. Inline `content` is capped at 1 MiB at tool-schema and
runtime levels. Larger artifacts require `content_ref` produced by a sanctioned
offload/sandbox/result path.

The model cannot specify org/user/run ids, an artifact id, revision, content digest, or
physical path.

### D3. Media type and kind validation

Allow-list media types per kind:

- code: `text/plain`, `text/x-*`, `application/json`,
  `application/typescript`, other product-reviewed text code types;
- document: `text/markdown`, `text/plain`, bounded sanitized HTML only if the renderer
  treats it as text/sanitized content;
- dataset: `text/csv`, `text/tab-separated-values`, `application/json`;
- file: any safe declared media type, rendered as file metadata/raw download.

Extension is presentation metadata and does not override the media type. Reject
control characters and path separators in `suggested_filename`.

### D4. Presentation preference

`presentation_preference` is a request, not permission:

- `auto`: Presentation Policy decides;
- `canvas`: request a canvas surface;
- `chat_card`: request a compact card;
- `none`: durable but not automatically shown.

The server may downgrade canvas to card/raw/none for size, renderer support, policy, or
host mode. It records `artifact.presentation_decided` with basis. It never silently
upgrades `none`.

### D5. Final response semantics

Publishing an artifact does not suppress the final response. The model should give a
short natural-language completion that references the artifact. The UI may show a
generated artifact chip/card but does not duplicate full artifact content into chat.

If the model only answers in prose/code Markdown and never publishes:

- final response behaves exactly as today;
- zero artifact events;
- Studio canvas reaches a clear chat-only state, not a receipt or phantom surface.

### D6. Subagent attribution

When a subagent publishes:

- `Artifact.created_by=subagent`;
- revision author is subagent;
- `source_ref` identifies the subagent work item without storing its prompt/body;
- `OperationRequest.parent_operation_id` points to delegation;
- usage remains attributed to `subagent_work` and artifact id where available.

The parent agent may reference or revise the artifact through normal authorized APIs.
It cannot rewrite authorship history.

### D7. Promote existing content

Promotable sources:

- assistant message content part;
- a specific code block selected by the user with server-verifiable offsets/digest;
- operation result/payload ref;
- sandbox output ref;
- existing draft version during migration.

The client sends only a source ref, kind, and metadata. The server resolves exact bytes
and verifies scope. Promotion is idempotent by source ref + request digest.

Do not allow promotion of arbitrary URLs or host paths.

### D8. `/drafts/` convergence

Implement `ArtifactDraftBackend` with current backend methods:

- `write_file` creates or revises a document/code artifact;
- `read_file` resolves current revision;
- `edit_file` appends a revision;
- `ls_info` projects artifact metadata;
- delete soft-deletes the artifact when allowed.

Path-to-artifact binding lives in an adapter mapping scoped by org/user/conversation/run
and virtual draft path. It is not a second content store.

Migration behavior:

1. new draft writes use Artifact Service;
2. reads check mapping, then legacy only during compatibility;
3. first legacy read may import and bind atomically;
4. a backfill job imports remaining legacy versions preserving timestamps/authorship
   where available;
5. after verification, legacy writes are disabled;
6. E2 removes legacy read fallback.

Sending/publishing a draft externally creates an EffectStage over an artifact revision;
it does not copy bytes into a connector-shaped draft record.

### D9. Provider neutrality

The `publish_artifact` built-in is the guaranteed baseline for all model providers.
Provider-native structured parts are optional optimizations and normalize to the same
operation. Prompts teach the model:

- answer normally when no durable artifact is useful;
- publish when the user asks to create/save/produce a durable code/doc/dataset/file;
- publication is not the same as saving to a local path;
- use a later workspace operation for local persistence.

Add evals for OpenAI, Anthropic, and Gemini adapters where the repository supports
them. The contract tests must not require live provider keys.

### D10. Failure behavior

- Artifact persistence failure: tool returns a safe failure; no success event.
- Presentation failure: artifact remains durable; record raw/none fallback.
- Duplicate tool delivery: one artifact/revision.
- Model publishes then run fails: artifact remains tied to failed run and is visible.
- Cancellation during upload: no revision, temporary bytes collected.
- Oversize: explicit safe error, no truncation masquerading as complete content.

## Implementation plan

1. Add built-in tool schema/handler and descriptor.
2. Wire Artifact Service through run dependencies and OperationContext.
3. Add optional `ArtifactContentPart` normalization in provider message processing.
4. Add prompt guidance and hermetic provider-adapter tests.
5. Add user promotion transport/UI port, not full renderer.
6. Implement `ArtifactDraftBackend` and path binding.
7. Add legacy import/backfill command with dry-run/report.
8. Wire draft send/stage to an artifact revision ref.
9. Add attribution/usage fields.
10. Add feature flags for publication and draft compatibility.

## Test plan

### Publication

- explicit tool creates one exact artifact;
- fenced code without tool creates none;
- `presentation_preference=none` creates artifact without surface;
- duplicate call id/digest replays;
- same key/different content conflicts;
- oversize and invalid media type create nothing.

### Final response

- chat-only arithmetic produces normal final response and no artifact;
- model publishes code and then returns a brief final response;
- failure after publication preserves artifact and provenance;
- Focus gets only a compact card.

### Draft migration

- new write/read/edit round-trip through Artifact Service;
- legacy import preserves all versions and digests;
- concurrent first-read import is idempotent;
- no legacy write occurs after cutover flag;
- send stages the selected artifact revision exactly.

### Attribution/security

- subagent attribution/parent operation is correct;
- model-supplied tenant/id fields are impossible or ignored;
- source promotion enforces run/user/org scope;
- logs/events contain no artifact body.

## Definition of done

- [ ] `publish_artifact` works for code, document, dataset, and file.
- [ ] Ordinary chat/code remains ordinary chat without explicit publication.
- [ ] User promotion resolves exact server-authorized source bytes.
- [ ] Subagent attribution is preserved.
- [ ] `/drafts/` new writes use Artifact Service as sole content truth.
- [ ] Draft send stages an artifact revision, not a copied mutable body.
- [ ] Provider-neutral contract and hermetic adapter tests pass.
- [ ] Standard DoD passes.

## Out of scope

- Final artifact renderer/editing UX.
- Saving to local workspace.
- Full legacy draft deletion.
- Inferring artifacts from unstructured messages.

## Guardrails

- Never scrape Markdown/code fences for artifact intent.
- Never treat internal artifact creation as an external effect.
- Never let model arguments select tenant or physical path.
- Never make provider-native content parts a separate storage path.
- Never retain two writable draft/artifact stores.
