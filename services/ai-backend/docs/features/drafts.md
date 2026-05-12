# Drafts

How the agent creates and manages draft messages (e.g. draft emails or Slack messages)
and the approval-gated send flow.

See also:

- [features/approvals.md](approvals.md) — the approval row that gates draft send

---

## What it does

When a user asks the agent to compose and send a message via an integration (e.g.
"draft a reply to this email and send it"), the agent:

1. Generates draft content and writes it to `DraftStorePort`.
2. Emits an approval-style event to the frontend so the user can review the draft.
3. On user confirmation, the draft is sent via the relevant MCP tool.
4. On rejection, the draft is discarded or revised.

---

## Key modules

| File                                                   | Role                                                        |
| ------------------------------------------------------ | ----------------------------------------------------------- |
| `agent_runtime/capabilities/backends/draft_backend.py` | `DraftBackend` — draft CRUD: upsert, get, delete, mark sent |
| `agent_runtime/api/draft_service.py`                   | `DraftService` — domain service: list, get, update drafts   |
| `runtime_api/http/drafts.py`                           | HTTP routes for draft management                            |
| `runtime_api/schemas/drafts.py`                        | `DraftRecord`, `DraftStatus`, `CreateDraftRequest`          |
| `agent_runtime/persistence/ports.py`                   | `DraftStorePort` — persistence protocol                     |
| `runtime_adapters/in_memory/draft_store.py`            | In-memory adapter                                           |
| `runtime_adapters/postgres/draft_store.py`             | Postgres adapter                                            |

---

## Draft lifecycle

```
DRAFT_CREATED
    ↓
DRAFT_PENDING_APPROVAL  (user reviews in frontend)
    ↓
DRAFT_APPROVED → send MCP tool → DRAFT_SENT
    or
DRAFT_REJECTED → discard / revise
```

---

## `DraftBackend`

`agent_runtime/capabilities/backends/draft_backend.py`

The `DraftBackend` is the only intended caller of `DraftStorePort`. It:

- `upsert_draft(run_id, conversation_id, content, metadata)` — creates or updates.
  Idempotent on `(run_id, draft_id)`.
- `get_draft(draft_id)` — returns `DraftRecord` or `None`.
- `delete_draft(draft_id)` — soft-deletes; draft is no longer accessible.
- `mark_sent(draft_id)` — transitions to `DRAFT_SENT` status.

---

## Draft send approval

When the agent has composed a draft, it calls an internal tool that:

1. Calls `DraftBackend.upsert_draft()`.
2. Emits a `DRAFT_READY` event carrying the draft preview for the frontend.
3. Calls `langgraph_interrupt()` with `approval_kind=draft_send`.

The approval row stores the `draft_id`. On user approval:

- `RuntimeApprovalHandler` loads the draft.
- Calls the send MCP tool (e.g. send email via an email MCP server).
- Calls `DraftBackend.mark_sent(draft_id)`.

On user rejection:

- The draft may be marked `DRAFT_REJECTED`.
- The run continues; the model can offer to revise.

---

## Draft record shape (`runtime_api/schemas/drafts.py`)

| Field             | Type          | Notes                                                         |
| ----------------- | ------------- | ------------------------------------------------------------- |
| `draft_id`        | `str`         | UUID                                                          |
| `run_id`          | `str`         | Which run created this draft                                  |
| `conversation_id` | `str`         | Parent conversation                                           |
| `org_id`          | `str`         | Tenant scoping                                                |
| `user_id`         | `str`         | Owner                                                         |
| `status`          | `DraftStatus` | `CREATED`, `PENDING_APPROVAL`, `APPROVED`, `REJECTED`, `SENT` |
| `content`         | `str`         | Draft body text                                               |
| `metadata`        | `dict`        | Integration-specific fields (subject, recipient, channel, …)  |
| `created_at`      | `datetime`    |                                                               |
| `updated_at`      | `datetime`    |                                                               |

---

## HTTP endpoints

| Method   | Path                                  | Action                            |
| -------- | ------------------------------------- | --------------------------------- |
| `GET`    | `/v1/agent/drafts/{draft_id}`         | Get a specific draft              |
| `GET`    | `/v1/agent/conversations/{id}/drafts` | List drafts for a conversation    |
| `PATCH`  | `/v1/agent/drafts/{draft_id}`         | Update draft content (user edits) |
| `DELETE` | `/v1/agent/drafts/{draft_id}`         | Discard a draft                   |
