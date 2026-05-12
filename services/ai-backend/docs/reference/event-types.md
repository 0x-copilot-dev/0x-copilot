# Event Types Reference

Complete `RuntimeApiEventType` enum with description, typical source, and payload shape.
All events flow through `RuntimeEventEnvelope` — see [architecture/02-contracts.md](../architecture/02-contracts.md).

To add a new event type: [guides/add-event-type.md](../guides/add-event-type.md).

---

## Run lifecycle events

| Event type       | Source   | Visibility | Description                                                |
| ---------------- | -------- | ---------- | ---------------------------------------------------------- |
| `run_started`    | `WORKER` | `USER`     | Worker has claimed the run and begun execution             |
| `run_cancelling` | `WORKER` | `USER`     | Cancel command received; advisory (cancel not yet applied) |
| `run_cancelled`  | `WORKER` | `USER`     | Run has been cancelled; terminal                           |
| `run_completed`  | `WORKER` | `USER`     | Run finished successfully; terminal                        |
| `run_failed`     | `WORKER` | `USER`     | Run failed with an unrecoverable error; terminal           |
| `budget_denied`  | `WORKER` | `USER`     | Run rejected at pre-flight budget check                    |

**Payload for `run_started`:**

```json
{ "model": "...", "reasoning_config": {...} }
```

**Payload for `run_failed` / `run_cancelled`:**

```json
{ "reason": "...", "cancel_reason": "..." }
```

---

## Model stream events

| Event type             | Source   | Visibility | Description                                         |
| ---------------------- | -------- | ---------- | --------------------------------------------------- |
| `model_call_started`   | `WORKER` | `INTERNAL` | LLM API call beginning; carries model name + config |
| `model_delta`          | `WORKER` | `USER`     | Streaming text chunk from the model                 |
| `model_call_completed` | `WORKER` | `INTERNAL` | LLM API call finished; carries usage breakdown      |
| `final_response`       | `WORKER` | `USER`     | Full assistant message assembled; carries citations |

**Payload for `model_delta`:**

```json
{ "delta": "..." }
```

**Payload for `final_response`:**

```json
{
  "message_id": "...",
  "content": "...",
  "citations": [
    {
      "ordinal": 1,
      "connector": "linear",
      "doc_id": "...",
      "title": "...",
      "url": "..."
    }
  ]
}
```

**Payload for `model_call_completed`:**

```json
{
  "usage": {
    "input_tokens": 1234,
    "output_tokens": 456,
    "cached_input_tokens": 200,
    "reasoning_tokens": 89
  }
}
```

---

## Reasoning / thinking events

| Event type                | Source   | Visibility | Description                                                     |
| ------------------------- | -------- | ---------- | --------------------------------------------------------------- |
| `reasoning_summary_delta` | `WORKER` | `USER`     | Streaming reasoning chunk (Anthropic thinking / OpenAI summary) |
| `reasoning_summary`       | `WORKER` | `USER`     | Full reasoning block closed                                     |

**Payload for both:**

```json
{ "delta": "..." }   // for _delta
{ "content": "..." } // for reasoning_summary (full accumulated text)
```

---

## Tool events

| Event type          | Source | Visibility | Description                                                  |
| ------------------- | ------ | ---------- | ------------------------------------------------------------ |
| `tool_call_started` | `TOOL` | `USER`     | Tool invocation begins                                       |
| `tool_result`       | `TOOL` | `USER`     | Tool returned a result                                       |
| `tool_call_error`   | `TOOL` | `USER`     | Tool invocation failed                                       |
| `budget_warning`    | `TOOL` | `USER`     | Per-run tool budget exhausted; run continues with safe error |

**Payload for `tool_call_started`:**

```json
{ "name": "tool_name", "args": {...}, "tool_call_id": "..." }
```

**Payload for `tool_result`:**

```json
{ "tool_call_id": "...", "result": {...} }
```

---

## Citation events

| Event type         | Source   | Visibility | Description                                                    |
| ------------------ | -------- | ---------- | -------------------------------------------------------------- |
| `source_ingested`  | `TOOL`   | `USER`     | Single source registered in `CitationLedger`                   |
| `sources_ingested` | `TOOL`   | `USER`     | Batch of sources registered                                    |
| `citation_made`    | `WORKER` | `USER`     | `[[N]]` marker detected in model text; tying ordinal to source |

**Payload for `source_ingested`:**

```json
{
  "citation": {
    "ordinal": 1,
    "connector": "linear",
    "doc_id": "...",
    "title": "...",
    "url": "...",
    "snippet": "..."
  }
}
```

**Payload for `citation_made`:**

```json
{
  "link": {
    "conversation_ordinal": 1,
    "message_id": "...",
    "prose_offset": 42,
    "prose_length": 5,
    "source_tool_call_id": "..."
  }
}
```

---

## Approval events

| Event type           | Source   | Visibility | Description                                                  |
| -------------------- | -------- | ---------- | ------------------------------------------------------------ |
| `mcp_auth_required`  | `TOOL`   | `USER`     | MCP server requires user authentication                      |
| `approval_requested` | `WORKER` | `USER`     | Generic approval (ask_a_question, draft_send, tool_approval) |
| `approval_resolved`  | `WORKER` | `USER`     | Approval decision received                                   |
| `approval_forwarded` | `WORKER` | `INTERNAL` | Approval relayed to a subagent                               |
| `approval_expired`   | `WORKER` | `USER`     | Approval row expired without response                        |

**Payload for `mcp_auth_required`:**

```json
{
  "approval_id": "...",
  "server_id": "...",
  "server_name": "...",
  "display_name": "...",
  "auth_url": "...",
  "expires_at": "2026-05-12T..."
}
```

---

## Subagent events

| Event type               | Source     | Visibility | Description                                              |
| ------------------------ | ---------- | ---------- | -------------------------------------------------------- |
| `subagent_fleet_started` | `WORKER`   | `USER`     | Delegation begins; fleet of subagents launched           |
| `subagent_started`       | `SUBAGENT` | `USER`     | Individual subagent started                              |
| `subagent_progress`      | `SUBAGENT` | `USER`     | Subagent intermediate update (tool call, partial result) |
| `subagent_completed`     | `SUBAGENT` | `USER`     | Subagent finished successfully                           |
| `subagent_failed`        | `SUBAGENT` | `USER`     | Subagent failed                                          |

---

## System and infrastructure events

| Event type             | Source   | Visibility | Description                                                   |
| ---------------------- | -------- | ---------- | ------------------------------------------------------------- |
| `heartbeat`            | `SYSTEM` | `USER`     | Synthetic event on non-follow poll; `metadata.transient=true` |
| `compression_event`    | `SYSTEM` | `USER`     | Context summarisation fired                                   |
| `presentation_updated` | `SYSTEM` | `USER`     | Async polish updated a prior event's presentation             |

---

## Retention and audit events (internal)

| Event type                  | Source   | Visibility | Description                                    |
| --------------------------- | -------- | ---------- | ---------------------------------------------- |
| `retention_sweep_completed` | `SYSTEM` | `INTERNAL` | Sweeper job finished a pass                    |
| `audit_*`                   | `SYSTEM` | `AUDIT`    | Audit trail events (separate retention policy) |

---

## Visibility rules

- `USER` — included in `GET /v1/agent/runs/{id}/stream` and `GET /v1/agent/runs/{id}/events`.
- `INTERNAL` — persisted but excluded from SSE output to the browser.
- `AUDIT` — persisted with a longer retention period; not sent to browser; accessible via audit log endpoints.
