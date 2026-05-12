# Audit Log Merge

How `GET /v1/audit` combines audit events from backend and ai-backend into a single
paginated stream.

See also:

- [architecture/01-routing.md](../architecture/01-routing.md) — request flow
- [architecture/02-auth-identity.md](../architecture/02-auth-identity.md) — bearer verification

Source: `backend_facade/audit_routes.py`

---

## What it does

The facade calls both upstream audit list endpoints in parallel, merges the results
by `created_at DESC`, and returns a single page. Upstream scope enforcement
(`admin:audit_export`) is trusted — the facade does not duplicate the check.

---

## Streams

| Stream                  | Owner      | Description                                   |
| ----------------------- | ---------- | --------------------------------------------- |
| `mcp_audit_events`      | backend    | MCP server create/update/delete, OAuth events |
| `skill_audit_events`    | backend    | Skill create/update/delete                    |
| `identity_audit_events` | backend    | Sessions, login, invitations, lockouts, MFA   |
| `deploy_audit_events`   | backend    | Deployment config changes                     |
| `runtime_audit_log`     | ai-backend | Model calls, tool invocations, subagent runs  |

---

## Composite cursor

Pagination across two independent upstreams uses a single public cursor that wraps
both upstream cursors:

```
public_cursor = base64url( JSON({ "backend": "<backend_cursor>", "ai": "<ai_cursor>" }) )
```

On the next request, `_decode_composite_cursor(raw)` unpacks it and sends each
cursor to the appropriate upstream. `_encode_composite_cursor(backend_next, ai_next)`
builds the next public cursor from the two upstream responses.

A `None` component means that upstream has no more pages. The public cursor is
`None` (no more data) only when both upstreams return `None`.

---

## Merge logic

Results are sorted by `(created_at DESC, stream, seq)` — a stable multi-key sort
that keeps chain sequence ordering within the same stream. The merged list is then
truncated to `limit` (default 50; caller-supplied but capped by each upstream).

```python
merged = sorted(
    (*backend_rows, *ai_rows),
    key=lambda row: (str(row.get("created_at") or ""), str(row.get("stream") or ""), int(row.get("seq") or 0)),
    reverse=True,
)
page = merged[:limit]
```

---

## Parameter allowlist

The facade forwards only these query params to upstreams (never the raw query string):

```
action, actor_user_id, resource_type, since, until, limit
```

`org_id` and `user_id` are always overwritten from the verified identity — a caller
cannot inject a different tenant via query params.

---

## Degradation

| Failure                           | Behaviour                                                                                                                                   |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend transport error           | `degraded_streams` += `["mcp_audit_events", "skill_audit_events", "identity_audit_events", "deploy_audit_events"]`; empty rows from backend |
| Backend 401/403                   | Propagate status to caller — canonical "not authorised" signal                                                                              |
| Backend 4xx/5xx                   | Propagate upstream error                                                                                                                    |
| AI-backend transport error or 5xx | `degraded_streams` += `["runtime_audit_log"]`; empty rows from ai-backend                                                                   |
| AI-backend 4xx                    | Silently degrade (not all orgs have runtime audit)                                                                                          |

`degraded_streams` in the response lets the UI show a partial-data warning without
treating degradation as an error.

---

## Response shape

```json
{
  "rows": [...],
  "next_cursor": "<composite_cursor | null>",
  "has_more": true,
  "degraded_streams": []
}
```

`has_more` is `true` when `len(page) == limit`, meaning there may be more data.
`next_cursor` is `null` when `has_more` is `false`.
