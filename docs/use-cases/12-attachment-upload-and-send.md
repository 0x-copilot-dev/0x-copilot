# 12. Attachment upload and send

> Status: documented · Layers: fe / facade / ai-backend · Related: 01, 11

## Trigger

User drops or picks one or more files (e.g. a PDF, a `.txt`, an image) into the composer, types a message, and presses Send. All attachments must travel with the very first run request — there is no separate upload step.

## Preconditions

- A chat is open (or about to be created on first send) and `activeRunId === null`.
- File MIME / extension matches one of the registered attachment adapters. Files outside the union are rejected by the composer before ever reaching `add()`.
- The user is authenticated to the facade; `identity` carries `org_id` / `user_id`.

## Sequence diagram

```mermaid
sequenceDiagram
    actor User
    participant FE as Browser (ChatScreen)
    participant Facade as backend-facade
    participant AI as ai-backend

    User->>FE: Drop files (PDF, txt, image) + type "Summarize these"
    FE->>FE: CompositeAttachmentAdapter.add(file) per file<br/>(image → SimpleImage; txt → SimpleText; pdf/docx/xlsx → GenericFile)
    Note over FE: Each pending attachment status = requires-action / composer-send
    User->>FE: Click Send
    FE->>FE: For each pending → adapter.send() reads as Data URL<br/>returns CompleteAttachment with content[type=file,data,mimeType]
    FE->>FE: optimisticUserMessage(...) — render attachments + text immediately
    FE->>FE: attachmentsFromAppendMessage → RunAttachmentRequest[]
    FE->>Facade: POST /v1/agent/runs  CreateRunRequest {user_input, content, attachments[]}
    Facade->>AI: POST /v1/agent/runs (forwarded, identity injected)
    AI-->>Facade: 201 {run_id, user_message_id}
    Facade-->>FE: 201 {run_id, user_message_id}
    FE->>FE: swap optimistic message id → user_message_id, open SSE stream
```

## Function trace

1. Composer wires a `CompositeAttachmentAdapter` chain — [apps/frontend/src/features/chat/ChatScreen.tsx:757-765](../../apps/frontend/src/features/chat/ChatScreen.tsx#L757-L765) — in this order: `SimpleImageAttachmentAdapter` → `SimpleTextAttachmentAdapter` → `GenericFileAttachmentAdapter`. First adapter whose `accept` regex matches wins.
2. `GenericFileAttachmentAdapter` — [apps/frontend/src/features/chat/ChatScreen.tsx:902-940](../../apps/frontend/src/features/chat/ChatScreen.tsx#L902-L940) — declares a `accept` MIME/extension list covering PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX. Its `add({file})` returns a `PendingAttachment` with `status: { type: "requires-action", reason: "composer-send" }` so assistant-ui defers reading bytes until Send.
3. `mimeTypeForFile` — [apps/frontend/src/features/chat/ChatScreen.tsx:951-971](../../apps/frontend/src/features/chat/ChatScreen.tsx#L951-L971) — fills `contentType` from the file extension when `file.type` is empty (e.g. some browsers return `""` for `.docx`).
4. On Send, assistant-ui calls `adapter.send(attachment)` for each pending. `GenericFileAttachmentAdapter.send` — [apps/frontend/src/features/chat/ChatScreen.tsx:917-935](../../apps/frontend/src/features/chat/ChatScreen.tsx#L917-L935) — calls `readFileDataURL(file)` ([line 942-949](../../apps/frontend/src/features/chat/ChatScreen.tsx#L942-L949)), which uses a `FileReader.readAsDataURL` Promise, and returns a `CompleteAttachment` whose `content[0]` is `{ type: "file", filename, data: <data URL>, mimeType }`. `SimpleImage` / `SimpleText` produce comparable parts (image / text) instead.
5. `submitUserMessage` — [apps/frontend/src/features/chat/ChatScreen.tsx:407-510](../../apps/frontend/src/features/chat/ChatScreen.tsx#L407-L510) — collects the now-complete attachments via `attachmentsFromAppendMessage` ([line 1028-1040](../../apps/frontend/src/features/chat/ChatScreen.tsx#L1028-L1040)) and the text via `textFromAppendMessage`. The optimistic local message is appended immediately ([line 442-456](../../apps/frontend/src/features/chat/ChatScreen.tsx#L442-L456)) using `optimisticUserMessage` so the user sees their files render before the network round-trip.
6. `optimisticUserMessage` — [apps/frontend/src/features/chat/chatModel/conversion.ts:67-97](../../apps/frontend/src/features/chat/chatModel/conversion.ts#L67-L97) — produces a local `ChatItem` with `role: "user"`, `content`, and `attachments` (the full `CompleteAttachment[]`). The `@assistant-ui/react` thread renders attachment chips/thumbnails directly from this.
7. `attachmentsFromAppendMessage` maps each `CompleteAttachment` to a `RunAttachmentRequest`: `{ id, type, name, content_type, size: file?.size ?? null, content: content.map(normalizeRunContentPart) }`. `normalizeRunContentPart` ([line 1042-1056](../../apps/frontend/src/features/chat/ChatScreen.tsx#L1042-L1056)) converts FE-side `mimeType` to the wire-format `mime_type`.
8. `createRun` — [apps/frontend/src/api/agentApi.ts:146-176](../../apps/frontend/src/api/agentApi.ts#L146-L176) — POSTs `CreateRunRequest` with `attachments` inline to `/v1/agent/runs`. Data URLs ride in the JSON body; there is no `multipart/form-data` and no separate upload endpoint.
9. `backend-facade` forwards the body verbatim to `ai-backend` after authenticating and scoping identity. Worker logic then sees `attachments` on the queued run and feeds them into the LangGraph context.

## Wire shapes

`CreateRunRequest.attachments[]` and `RunAttachmentRequest` / `RunContentPart` live in [packages/api-types/src/index.ts:517-585](../../packages/api-types/src/index.ts#L517-L585). For a file attachment the inner part is:

```jsonc
{
  "type": "file",
  "filename": "spec.pdf",
  "data": "data:application/pdf;base64,JVBERi0xLjQK...",
  "mime_type": "application/pdf",
}
```

## State changes

- Client: an optimistic `ChatItem` with `attachments` is appended; on server response its `id` is replaced with the canonical `user_message_id` and its `runId` is filled. The active SSE stream opens at `sequence_no=0`.
- Server: `ai-backend` persists the user message + attachments alongside the run record. Attachments are part of the conversation message audit trail (not stored in a separate blob store today).

## Edge cases handled

- Browser returns empty `file.type` for some Office files: `GenericFileAttachmentAdapter.send` falls back through `attachment.contentType || mimeTypeForFile(name) || "application/octet-stream"` ([line 928-932](../../apps/frontend/src/features/chat/ChatScreen.tsx#L928-L932)).
- `SimpleImage` / `SimpleText` adapters precede `GenericFile`, so a `.txt` file is never read as a generic file Data URL — it goes through the text path which produces a `text` content part instead.
- Send while a run is active: `submitUserMessage` early-returns at the `activeRunId !== null` check ([line 413](../../apps/frontend/src/features/chat/ChatScreen.tsx#L413)). Attachments stay pending in the composer until the run finishes.
- Network failure during `createRun`: an inline error `ChatItem` is appended ([line 491-499](../../apps/frontend/src/features/chat/ChatScreen.tsx#L491-L499)); the optimistic user message remains visible so the user sees what they tried to send.

## Known gaps / TODOs

- **No client-side size check.** Neither `GenericFileAttachmentAdapter.add` nor `submitUserMessage` enforces a maximum file size or a per-message total. A user dropping a 50 MB PDF will let `FileReader.readAsDataURL` allocate its base64 representation in memory and then ship the whole thing as a JSON string.
- **Data URL overhead.** Base64 encoding is ~33% larger than the raw bytes. A 10 MB PDF becomes ~13.3 MB in the JSON body. The browser's JSON serializer, the facade's request-body parser, and `ai-backend`'s FastAPI body limit all stack their own caps. The exact server-side ceiling is not documented in `services/backend-facade` or `services/ai-backend` — large attachments will fail with an opaque `413`/`payload too large` from whichever layer is tightest. A proper fix would be a presigned upload + reference-by-id pattern.
- **No async upload progress.** Because `send()` reads the file inline before `createRun` is called, the composer shows no progress bar; large files appear to "hang" between Send-click and the SSE stream opening.
- **Attachments are not persisted as resumable artifacts.** They live inside the user-message record and the run's persisted history; deleting a conversation deletes them with no separate retention knob.

## References

- Composer adapter chain: [apps/frontend/src/features/chat/ChatScreen.tsx:757-765](../../apps/frontend/src/features/chat/ChatScreen.tsx#L757-L765)
- Generic file adapter: [apps/frontend/src/features/chat/ChatScreen.tsx:902-940](../../apps/frontend/src/features/chat/ChatScreen.tsx#L902-L940)
- Submit flow: [apps/frontend/src/features/chat/ChatScreen.tsx:407-510](../../apps/frontend/src/features/chat/ChatScreen.tsx#L407-L510)
- `attachmentsFromAppendMessage`: [apps/frontend/src/features/chat/ChatScreen.tsx:1028-1056](../../apps/frontend/src/features/chat/ChatScreen.tsx#L1028-L1056)
- Optimistic render: [apps/frontend/src/features/chat/chatModel/conversion.ts:67-97](../../apps/frontend/src/features/chat/chatModel/conversion.ts#L67-L97)
- `createRun`: [apps/frontend/src/api/agentApi.ts:146-176](../../apps/frontend/src/api/agentApi.ts#L146-L176)
- Wire types: [packages/api-types/src/index.ts:517-585](../../packages/api-types/src/index.ts#L517-L585)
