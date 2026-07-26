# PRD-CT-BE — Authoritative Composer Capability Catalogue

**Status:** proposed implementation plan
**Owners:** Backend, Backend-facade, AI backend
**Companion:** [frontend picker PRD](frontend-prd.md)

## Problem

The client currently combines `/v1/mcp/servers`, `/v1/mcp/catalog`, a local Web
search boolean, and a legacy `/v1/mcp/tools` response. Those APIs serve different
purposes and do not answer the user-facing question: _what can this authenticated
person safely enable for this next run?_

The service boundary must remain hard: `backend` owns connector registration,
OAuth state/token vault, policy and audit; `backend-facade` proxies product
routes; `ai-backend` executes a validated run snapshot. No service imports a
sibling service's source.

## Goals

- Publish one authenticated, tenant-scoped catalogue of eligible composer
  capabilities.
- Resolve selection server-side into an immutable run capability snapshot.
- Include built-ins such as Web search alongside MCP connectors without letting
  the UI grant permissions or invent tool IDs.
- Represent setup/auth/policy state explicitly and support safe remediation.
- Audit selection changes and execution/approval outcomes without recording
  secrets or full sensitive payloads.

## Proposed facade contract

`GET /v1/composer/tools?workspace_id={id}`

The facade authenticates the bearer and derives organization, user, roles, and
workspace access. It must reject caller-supplied tenant/user identity. It proxies
to `backend` for connector/skill/policy projection and to `ai-backend` only for
registered built-in capability availability; the facade returns one response.

```json
{
  "revision": "opaque-etag",
  "tools": [
    {
      "id": "builtin:web-search",
      "kind": "builtin",
      "label": "Web search",
      "availability": "enabled",
      "default_enabled": true,
      "permission": "read",
      "selection": { "type": "web_search" }
    },
    {
      "id": "mcp:srv_123:search_issues",
      "kind": "mcp",
      "label": "Search issues",
      "provider": { "name": "Linear", "icon_key": "linear" },
      "availability": "needs_auth",
      "permission": "read",
      "remediation": "connect",
      "selection": { "type": "connector", "server_id": "srv_123" }
    }
  ]
}
```

`id`, `revision`, and `selection` are opaque transport values. Descriptors must
never include OAuth tokens, custom headers, server secrets, raw custom MCP
configuration, or unrestricted local paths.

### Run creation

Extend the existing facade run payload with an additive selection field:

```json
{
  "request_context": {
    "tool_selection": {
      "catalog_revision": "opaque-etag",
      "enabled_tool_ids": ["builtin:web-search", "mcp:srv_123:search_issues"]
    }
  }
}
```

Compatibility adapters may translate the existing `web_search_enabled` and
`connector_scopes` fields while clients migrate. The facade/backend validates all
IDs against the current caller/workspace catalogue. Unknown, stale, disabled, or
policy-blocked IDs return a structured `409 tool_catalog_stale` or
`422 tool_selection_invalid`; they are never silently enabled.

The validated result is passed to `ai-backend` as a resolved, immutable capability
snapshot, not as client-provided configuration. `ai-backend` applies the existing
Web search gate and connector scope mechanics to that snapshot.

## Availability and policy rules

| Availability           | Server condition                                            | Selection result                                  |
| ---------------------- | ----------------------------------------------------------- | ------------------------------------------------- |
| `enabled` / `disabled` | Allowed by workspace policy; user may toggle.               | Valid only if set enabled.                        |
| `needs_auth`           | Connector installed but no valid user grant.                | Reject selection; return auth remediation.        |
| `needs_setup`          | Connector requires pre-registered credentials/custom setup. | Reject selection; route configuration.            |
| `policy_blocked`       | Org/workspace/role/data-classification rule denies it.      | Never executable; return safe reason code.        |
| `unavailable`          | Runtime/provider/tool health cannot support it.             | Never executable; retryable reason if applicable. |

Skills are listed only if the runtime has a stable invocable capability contract.
Instruction-only skills must remain separate from the executable-tools catalogue.
Likewise, the design's local filesystem/browser entries must not ship as selectable
tools until a policy, sandbox, approval, and audit implementation exists.

## Security, approvals, and audit

- Derive identity, role, organization and workspace from verified auth. Enforce
  tenant isolation on every catalogue and run-resolution query.
- Enforce workspace allowlists, connector ownership, OAuth grant health,
  data-classification rules, and provider/tool availability server-side.
- Classify each capability as `read`, `write`, or `mixed`. A write/mixed tool
  may be selected but must emit the existing runtime approval event immediately
  before mutation; selection is not an approval.
- Record an append-only audit event for catalogue selection resolution, connector
  authorization transitions, policy denials, approvals, tool invocation metadata,
  and final outcome. Store references/redacted summaries, not token material or
  arbitrary prompt/file contents.
- Define retention/deletion coverage for run snapshots, events, approval records,
  tool invocation metadata, connector grants, and audit records. Document legal
  hold and SIEM export expectations before claiming compliance coverage.

## Ownership and implementation phases

1. **Contracts:** add API types and the facade route; retain old MCP endpoints.
2. **Backend projection:** join installed connector, catalog, OAuth grant,
   workspace policy, and audit state into descriptors. Add revision/ETag support.
3. **Built-ins:** make Web search availability/policy explicit from the AI runtime
   configuration. Do not expose local/browser/write capability placeholders.
4. **Resolution:** validate `tool_selection` at run creation, persist the resolved
   snapshot with the run, and send only the snapshot to `ai-backend`.
5. **Approvals and observability:** enforce capability-level approval policy and
   emit structured events that the existing activity projection can render.
6. **Migration:** run compatibility translation, measure client adoption, then
   retire the frontend's client-side MCP joins and legacy `/v1/mcp/tools` use.

## Tests and acceptance criteria

- Backend unit tests cover tenant isolation, role/workspace policy, expired OAuth,
  setup-required connectors, stale revisions, unknown IDs, and no secret fields
  in responses/logs/audit rows.
- Facade contract tests prove identity is derived from bearer/session and only
  allowed routes are exposed; apps call only `:8200` in development.
- AI backend tests prove a resolved snapshot enables Web search/connector scopes
  correctly and rejects a raw client tool configuration bypass.
- End-to-end fixtures cover Web search on/off, authenticated MCP read,
  auth-required, setup-required, policy-blocked, read vs write approval, failure,
  retry, and revocation between catalogue load and run start.
- Audit tests prove selection, approval, invocation and denial records are
  attributable, tenant-scoped, redacted and exportable through the supported
  adapter; an in-memory/no-op adapter is not counted as production evidence.
