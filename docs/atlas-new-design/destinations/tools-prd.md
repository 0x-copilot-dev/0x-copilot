# Tools destination — sub-PRD (Phase 10)

**Status:** binding (drafted 2026-05-18, orchestrator)
**Master PRD:** [destinations-master-prd.md §5.7](../destinations-master-prd.md)
**Cross-audit:** [cross-audit.md](../cross-audit.md) (binding decisions §1–§5)
**Impl-plan slot:** [implementation-plan.md §2 Phase 10](../implementation-plan.md)
**Owner:** parth · **Phase:** 10

**Companion contracts:**

- `packages/api-types/src/tools.ts` (NEW — this PRD)
- `services/backend/src/backend_app/tools/` (NEW — this PRD)
- `services/ai-backend/src/agent_runtime/capabilities/tools/` (EXISTS — extended, not rewritten)
- `packages/chat-surface/src/destinations/tools/` (EXISTS as stub — replaced)
- `apps/frontend/src/features/tools/` (NEW)

**Binding cross-PRD inputs (recap):**

- `ItemRef` kind `tool` already in the canonical union ([cross-audit.md §1.1](../cross-audit.md))
- `ToolId` brand already in `packages/api-types/src/brands.ts`
- Project-scoped ACL master rule consumed via `services/backend/src/backend_app/projects/acl.py::is_project_member` ([cross-audit.md §1.3](../cross-audit.md))
- Audit `context` shape master-level optional ([cross-audit.md §1.4](../cross-audit.md))
- Filter axis OR ([cross-audit.md §1.5](../cross-audit.md))
- `<PageHeader>`, `<FilterTabs>`, `<EmptyState>`, `<CardGrid>`, `<DocList>`, `<ActivityList>`, `<StatusPill>`, `<ItemLink>`, `formatRelativeTime` are SP-1 primitives ([cross-audit.md §1.6](../cross-audit.md))
- SSE convention ([cross-audit.md §5.2](../cross-audit.md))
- TU-1 single-tracker invariant — every LLM call (including code-routine tool-shaped runs) attributes via `runtime_run_usage` / `runtime_model_call_usage` ([cross-audit.md §5.5](../cross-audit.md))

---

## §1 Premise

### 1.1 What a Tool is

A **Tool** is anything Atlas can _call on the user's behalf_ that isn't a chat or a model invocation: an MCP server method, a REST endpoint described by an OpenAPI document, a built-in capability shipped with Atlas (file read/write, web search, todo create, library save), a user-installed skill, or a **code-routine** (user-authored deterministic code that runs in a sandbox — Routines §9.7 Q1, deferred from Phase 5 to here). Every agent run, every chat run, every routine fire selects a subset of tools to expose to the LLM; the catalog lives here.

Tools have a **wire-callable contract**: a name, an args JSON Schema, a returns JSON Schema, a `kind` discriminator, an owner, a scope (`read` / `write` / `both`), permissions, and an invocation record. They are not chat-surface widgets; they are not connectors (Phase 11 owns connector lifecycles); they are not skills-as-prompts (those are first-class Phase 7 Library `page` artefacts). The Tools destination is the **catalog + onboarding + audit lens** for everything callable.

### 1.2 Why a separate destination instead of "tools are just a property of an agent"

Three reasons:

1. **Lifecycle independence.** A Slack-summary MCP server is installed once and reused by 20 agents + 3 routines + interactive chats. The install/uninstall/scope-change/disable affordances belong on the tool, not on each consumer.
2. **Audit lens.** When compliance asks _"who called Salesforce.updateAccount this quarter and from which run?"_, the natural starting point is the tool, then drill into invocations. No agent-centric or chat-centric pivot answers that cleanly.
3. **Code-routines need a home.** Routines §9.7 Q1 deferred code-routine execution (user-authored deterministic code in a sandbox) to "later". This is later. Code-routines are tools — they have args, returns, an owner, an audit trail. Slotting them anywhere else creates parallel infra (separate registry, separate audit, separate scope model). One catalog, four kinds.

### 1.3 What Tools is NOT

- **A connector manager.** Connectors are auth-gated _data sources_ (Salesforce, Gmail). A connector is consumed _by_ tools (the Salesforce MCP tool's transport authenticates via the Salesforce connector). Phase 11 owns connectors.
- **A skill editor with rich prompt versioning.** Skills (prompt + response template) are stored as Library pages with a `kind=skill` tag. Tools register the _name + execution shim_; the prompt text lives in Library.
- **A test-bench for agents.** Detail page shows last N invocations, but a full "rerun in a sandbox with mock data" is a Phase 11+ concern (or a separate dev-tools surface).
- **An API explorer (Postman replacement).** Onboarding wizard has a "test call" step, but it's gated on schema validation and audit-on. Free-form API hitting is out of scope.

### 1.4 User success states

- _"I want to onboard the internal billing API."_ → `/tools/onboard` wizard: paste OpenAPI URL → auth picker (Bearer / OAuth via existing connector / API-key vault entry) → scope review → test call → save. Tool appears in catalog with status `enabled`.
- _"Which agents call Salesforce.updateAccount?"_ → `/tools/salesforce-update-account` → "Used by" tab → list of agents + recent invocations.
- _"This MCP server has 12 methods. Disable the destructive ones."_ → `/tools/<mcp-id>` → method list → per-method `enabled` toggle → save. Scope shrinkage cascades to per-chat tool grants (existing path) plus emits an audit event per disabled method.
- _"Run my data-cleanup code-routine on the latest CRM pull."_ → `/tools/code/<id>` → "Run with…" picker (args form generated from JSON Schema) → submit → sandbox executes → result lands in the run page + invocation log.
- _"Which tools cost the most this month?"_ → catalog list, sort by `usage.calls_30d` desc.

### 1.5 Relationship to other destinations

| Surface    | Tools relationship                                                                                                                                                  |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Chats      | Per-chat tool allowlist (already exists). Composer's tool-popover reads the catalog; per-chat overrides write `chat_tool_grants`.                                   |
| Agents     | An agent's `tool_grants[]` references tool ids. Agent edit page picks from the catalog. Default-agent-of-project → its tool_grants flow to that project's chats.    |
| Connectors | Tool `transport.connector_ref?: ConnectorId` field — when a tool's transport authenticates via a connector, this is the back-reference. Phase 11 wires the reverse. |
| Library    | Skills (Library pages tagged `skill`) appear in the Tools catalog as `kind = "skill"` with `Tool.skill_page_ref: ItemRef { kind: "library_page" }`.                 |
| Inbox      | Tool errors that the runtime classifies as user-actionable (auth_required / scope_missing) deliver to Inbox via existing `tool_error` reason.                       |
| Home       | "Errored tools" tile on the triage strip (count of tools with `status = "error"` in last 24h). InFlightProject doesn't cite tools directly.                         |
| Routines   | Code-routines that were deferred from Routines §9.7 Q1 ship here as `kind = "code"` tools. A routine that fires a code-tool uses the existing tool-call envelope.   |
| Memory     | Memory is read by tools (e.g. an "answer-from-memory" built-in); not the reverse. Phase 12 ships memory.                                                            |
| Team       | Team page shows "agents this person owns" + "tools this person registered" — tools authored by a user surface there with a back-link.                               |

### 1.6 Status semantics (the four `status` values)

- `enabled` — installed, scope reviewed, callable by every grant that includes it. The default after a successful onboarding wizard.
- `disabled` — admin or owner has paused calls. The tool still exists; grants and audit history are preserved. Invocations are rejected at the runtime with `tool_disabled` error.
- `error` — last N invocations failed with a transport-level error (auth expired, schema mismatch, sandbox crash). Auto-set by the runtime when consecutive failures exceed the threshold; auto-cleared on first success.
- `pending_review` — only for code-routines and OpenAPI-onboarded tools that triggered a scope-change review (e.g. an OpenAPI doc was re-fetched and now requests broader permissions). Sits in an admin queue until approved; calls rejected meanwhile.

---

## §2 User journeys (the 7 concrete flows)

### U1. "Onboard an internal API."

User opens `/tools/onboard`. Pastes the OpenAPI URL. Wizard:

1. Fetches and validates the OpenAPI document (server-side; rate-limited; URL-scheme allowlisted to https + tenant-internal hosts).
2. Lists operations. User selects which to expose (default: all `GET` ops, no write ops).
3. Auth picker:
   - **Connector** — pick an existing Phase 11 connector. Tool's transport uses that connector's token.
   - **API key** — paste into the token vault. Stored encrypted; never echoed back.
   - **None** — public APIs only; tagged in the catalog.
4. Scope review — wizard shows the requested scopes (per operation) and the user confirms. Each operation becomes a `Tool` row.
5. Test call — wizard calls the first selected `GET` op with default args; result rendered. Test calls themselves audit-logged.
6. Save — tool rows are inserted; `status = "enabled"`.

### U2. "Install an MCP server."

User opens `/tools` → "MCPs" tab → "Browse marketplace" or "Add a custom MCP URL". Existing MCP OAuth path (services/backend MCP registration) is reused; this destination wraps it with a card UI and a per-method enable toggle.

### U3. "Build a code-routine."

User opens `/tools/onboard` → "Code" tab. Editor (single-file Python or JS; ace-mode; lint-on-save). User defines:

- Function signature: `def run(args: ArgsModel) -> ResultModel: ...`
- ArgsModel + ResultModel are Pydantic / Zod (lang-dependent). The editor introspects to generate JSON Schemas on save.
- Resource hints: timeout (max 30s), memory cap (max 512 MB), network egress (deny by default; opt-in to specific hosts from the connector allowlist).

On save:

1. Static-analysis pass (imports allow-list, no subprocess, no eval).
2. Submitted to the sandbox build step (warm container; cold-start hidden behind a "compiling…" spinner).
3. Test call runs in the sandbox with sample args (user-provided).
4. On pass, tool row inserted with `kind = "code"`, `status = "enabled"`, `code_ref: { repo_ref, env_ref }` per Routines §9.7 Q1 wire shape.

### U4. "Disable a destructive tool."

User opens `/tools/<id>`. Sees current `status = "enabled"`. Clicks "Disable". Confirmation dialog: "Affects N agents and M routines that grant this tool." Server-side query lists consumers. On confirm: `status = "disabled"`, audit row, ItemLink references stay valid (UI shows the disabled badge).

### U5. "See who called this tool."

User opens `/tools/<id>` → "Invocations" tab. List of recent calls. Each row: timestamp + caller (run_id linkified) + args summary + result-or-error chip. Filterable by date, caller-kind (agent / routine / interactive-chat), status. Cap 90-day window in v1.

### U6. "Set per-project tool allowlist."

(Phase 6 already shipped `default_connector_allowlist` at the project level. Phase 10 extends with `default_tool_allowlist`.) User opens `/projects/<id>` → "Tools" tab → picks from catalog. Subsequent chats/agents/routines filed under the project inherit at create-time (Phase 6 §9.8 Q3 rule).

### U7. "Add a tool to the chat composer."

Existing composer tool-popover. No new UI; this PRD just makes the catalog endpoint that backs the popover canonical. Composer reads `GET /v1/tools?installed=true&scope=<read|write|both>` and renders.

---

## §3 Data shape

### 3.1 Canonical wire types (`packages/api-types/src/tools.ts`)

```typescript
export type ToolKind = "mcp" | "openapi" | "builtin" | "code" | "skill";
export type ToolScope = "read" | "write" | "both";
export type ToolStatus = "enabled" | "disabled" | "error" | "pending_review";

export interface ToolTransport {
  readonly kind: "mcp" | "http" | "in_process" | "sandbox";
  /** When http: the URL template (vars substituted at call time). */
  readonly url_template?: string;
  /** Reverse-link to the connector that authenticates this tool, if any.
   *  Lets Phase 11 Connectors render "tools that use me" in O(1). */
  readonly connector_ref?: {
    readonly kind: "connector";
    readonly id: ConnectorId;
  };
  /** For sandbox/in_process: name of the resolved executor in the runtime. */
  readonly executor?: string;
}

export interface ToolUsageProjection {
  readonly calls_24h: number;
  readonly calls_30d: number;
  readonly p50_latency_ms_30d: number | null;
  readonly success_rate_30d: number | null; // 0-1; null when no calls
  readonly last_used_at: string | null;
}

export interface Tool {
  readonly id: ToolId;
  readonly tenant_id: TenantId;
  readonly name: string;
  readonly description: string;
  readonly kind: ToolKind;
  readonly scope: ToolScope;
  readonly status: ToolStatus;
  readonly status_reason?: string;
  /** JSON Schemas (Draft 2020-12) — server-validated at call time. */
  readonly args_schema: Record<string, unknown>;
  readonly returns_schema: Record<string, unknown>;
  readonly transport: ToolTransport;
  /** Owner is the user who registered/authored it. Tenant admins can
   *  edit any tool; project members can only call them via grants. */
  readonly owner_user_id: UserId;
  /** Project this tool was created under (optional). When set, the
   *  master ACL rule from cross-audit §1.3 applies: non-readers get
   *  404 on detail; catalog list filters by visibility. */
  readonly project_id?: ProjectId | null;
  /** When kind="skill": back-link to the Library page that owns the
   *  prompt + response template. */
  readonly skill_page_ref?: {
    readonly kind: "library_page";
    readonly id: LibraryPageId;
  };
  /** When kind="code": forwards-compat shape from Routines §9.7 Q1. */
  readonly code_ref?: {
    readonly repo_ref: ItemRef;
    readonly env_ref: ItemRef;
    readonly entry: string;
  };
  readonly tags: ReadonlyArray<string>;
  readonly usage: ToolUsageProjection; // read-only projection — §3.3
  readonly created_at: string;
  readonly updated_at: string;
}

export interface ToolInvocation {
  readonly id: string; // toolinv_<ulid>
  readonly tool_id: ToolId;
  readonly tenant_id: TenantId;
  readonly run_id: RunId;
  readonly caller_kind: "agent" | "routine" | "chat";
  readonly caller_ref: ItemRef; // narrowed to agent/routine/chat
  readonly args_summary: string; // truncated to 240 chars; full payload in audit
  readonly result_summary?: string; // truncated
  readonly status: "ok" | "error";
  readonly error_kind?:
    | "auth_required"
    | "scope_missing"
    | "schema_invalid"
    | "timeout"
    | "sandbox_crash"
    | "transport_error"
    | "unknown";
  readonly started_at: string;
  readonly ended_at: string;
  readonly latency_ms: number;
}

export interface ToolListResponse {
  readonly tools: ReadonlyArray<Tool>;
  readonly next_cursor: string | null;
}

export interface ToolDetailResponse {
  readonly tool: Tool;
  readonly consumers: {
    readonly agents: ReadonlyArray<ItemRef>; // narrowed to "agent"
    readonly routines: ReadonlyArray<ItemRef>; // narrowed to "routine"
    readonly chats_with_grant: number; // count only; per-chat list is admin-only
  };
}
```

### 3.2 Why `ToolUsageProjection` is a projection (no parallel tracker)

The runtime already emits `runtime_run_usage` + `runtime_model_call_usage` rows when a tool call wraps an LLM step. For tools that don't call an LLM (most), the **runtime_tool_invocations** table (§5.2 — already exists for chat-surface tool-call envelopes) carries one row per call. `ToolUsageProjection` is built at read time as a `GROUP BY tool_id` over those tables — there is NO parallel `tool_usage_daily` table. TU-1 invariant preserved ([cross-audit.md §5.5](../cross-audit.md)).

### 3.3 Code-routine forward compatibility (Routines §9.7 Q1)

The `code_ref?: { repo_ref, env_ref, entry }` field mirrors the `RoutineCode` wire shape from `packages/api-types/src/routines.ts`. A routine's `actions[]` can include a `tool_call { tool_id }` where the resolved tool has `kind = "code"` — no separate "code-routine" wire path. One catalog. One runtime path (the existing tool-call envelope).

---

## §4 Endpoints (all `/v1/tools/*` via `services/backend-facade`)

### 4.1 `GET /v1/tools` — list/search

Query params:

- `q?: string` — name + description fuzzy match
- `kind?: ToolKind` — repeated for OR
- `scope?: ToolScope`
- `status?: ToolStatus[]`
- `project_id?: ProjectId` — show only tools filed under this project
- `installed?: boolean` — when `true`, only tools the caller can invoke (after ACL)
- `cursor?: string`, `limit?: number` (default 50, max 200)
- `sort?: "name" | "calls_30d_desc" | "last_used_desc" | "created_at_desc"` (default `name`)

Returns `ToolListResponse`. ACL: tenant-scoped; project-scoped via `is_project_member`. 404-not-403 on out-of-tenant.

### 4.2 `GET /v1/tools/{id}` — detail

Returns `ToolDetailResponse`. 404 on unauthorized read.

### 4.3 `POST /v1/tools` — register

Body: `{ kind, name, description, scope, args_schema, returns_schema, transport, project_id?, tags?, code_ref? }`. For `kind=code`: requires the sandbox build step to pass first (call returns 202 + a build job id when the build is async). For `kind=mcp` / `openapi`: server fetches the upstream schema and validates.

### 4.4 `PATCH /v1/tools/{id}` — edit

Owner OR tenant admin. Patchable: name, description, tags, scope (down-shrink only without review), status (enable/disable), args_schema (only for `kind=code`).

### 4.5 `POST /v1/tools/{id}/test` — test-call

Owner OR tenant admin. Body: `{ args }`. Runs the tool with the supplied args in test mode (no agent context; result not used by anything downstream; still audited). Returns `{ status, result, latency_ms, error? }`.

### 4.6 `POST /v1/tools/{id}/disable` / `enable`

Owner OR tenant admin. Disable rejects all subsequent invocations; existing grants preserved. Audit row written. Inbox notification to consumers' owners if any consumer's owner != caller.

### 4.7 `DELETE /v1/tools/{id}`

Owner OR tenant admin. Soft-delete (90d tombstone). Consumers' grants preserved as dead refs and rendered with a "removed" badge.

### 4.8 `GET /v1/tools/{id}/invocations` — history

Query: `?after_id`, `?since_iso`, `?caller_kind`, `?status`, `?limit` (default 50, max 200). Returns paginated `ToolInvocation[]`.

### 4.9 `GET /v1/tools/{id}/usage` — usage projection

Returns `ToolUsageProjection` for windows `24h`, `7d`, `30d` (plus the rolled-up version on the `Tool` shape). Built by projection over `runtime_tool_invocations` + `runtime_model_call_usage`.

### 4.10 `GET /v1/tools/stream` — SSE

Envelopes:

- `tool.created` — new tool registered
- `tool.updated` — patch (status, scope, name)
- `tool.deleted` — soft-delete
- `tool.invoked` — a new invocation landed (server batches at ~1Hz to avoid floods)
- `tool.error_threshold` — `status` flipped to `error`
- `tool.heartbeat` — every 30s ([cross-audit.md §5.2](../cross-audit.md))

`Last-Event-ID` resume.

### 4.11 Internal endpoints

- `GET /internal/v1/tools/by_ids` — bulk fetch, used by ai-backend when materializing `tool_grants[]` for a run.
- `POST /internal/v1/tools/{id}/invocations` — write a `runtime_tool_invocations` row. Called by ai-backend at every tool-call return.
- `POST /internal/v1/tools/{id}/error` — bump consecutive-error counter; flip status when threshold reached. Called by ai-backend transport adapters.

### 4.12 Filter / sort allowlist ([cross-audit.md §1.5](../cross-audit.md))

- **Filter axes:** kind, scope, status, project_id, installed (boolean), q.
- **Sort:** name (asc), calls_30d_desc, last_used_desc, created_at_desc.

---

## §5 Storage (Postgres, owned by `services/backend`)

### 5.1 `tools` table

| Column                      | Type                             |
| --------------------------- | -------------------------------- |
| `id`                        | text PK (prefix `tool_`)         |
| `tenant_id`                 | text NN                          |
| `name`                      | text NN                          |
| `description`               | text NN                          |
| `kind`                      | text NN — enum check             |
| `scope`                     | text NN — enum check             |
| `status`                    | text NN — enum check             |
| `status_reason`             | text                             |
| `args_schema`               | jsonb NN                         |
| `returns_schema`            | jsonb NN                         |
| `transport`                 | jsonb NN — `ToolTransport` shape |
| `owner_user_id`             | text NN                          |
| `project_id`                | text                             |
| `skill_page_ref`            | jsonb                            |
| `code_ref`                  | jsonb                            |
| `tags`                      | text[] NN default `{}`           |
| `consecutive_error_count`   | int NN default 0                 |
| `created_at` / `updated_at` | timestamptz NN                   |
| `deleted_at`                | timestamptz (soft-delete)        |

Indexes: `(tenant_id, deleted_at, kind)`, `(tenant_id, project_id, deleted_at)`, `(tenant_id, owner_user_id, deleted_at)`, GIN on `tags`, BTREE on `lower(name)` for q-prefix.

### 5.2 `runtime_tool_invocations` (already exists)

Phase 10 reuses the existing table. New indexes (if not already present):

- `(tenant_id, tool_id, started_at desc)` — invocations tab.
- `(tenant_id, tool_id, status, started_at desc)` — error-rate aggregation.

### 5.3 `tool_marketplace_entries` (for U2)

Out-of-scope for Phase 10. Marketplace is a Wave 6 admin feature; for Phase 10 the "Browse marketplace" CTA goes to a curated static list (config-file backed) of vetted MCP servers.

### 5.4 Retention

- `tools.deleted_at` → hard-delete past 90 days; cascade on `runtime_tool_invocations` is by `tool_id` foreign-key with `ON DELETE SET NULL` (so invocation history isn't lost; tool_id becomes null in archives).
- `runtime_tool_invocations` already has a retention policy (master §3.3). No change.

---

## §6 ACL + audit

### 6.1 ACL — read

- Caller must be in tenant.
- If `tool.project_id IS NOT NULL`: caller must be project member via `is_project_member(user, project, "read")` ([cross-audit.md §1.3](../cross-audit.md)).
- If neither: tenant member is enough.
- Out-of-scope reads → 404 (existence not leaked).

### 6.2 ACL — write

- Register (POST): any tenant member.
- Patch / Disable / Enable / Delete: owner OR tenant admin.
- Test call: owner OR tenant admin. (Project members CAN invoke via a normal run; test mode is owner-only because it bypasses run audit attribution.)

### 6.3 Audit

Every state-changing action writes an audit row via the existing audit helper (mirrors `agents/` audit shape):

- `tool.created`
- `tool.updated` (fields-changed in `context.fields`)
- `tool.disabled` / `tool.enabled`
- `tool.deleted` (soft) / `tool.purged` (hard, by retention job)
- `tool.test_called` (records args summary + result summary)
- `tool.scope_changed` (subset of `tool.updated`; emitted in addition for compliance search)

Every invocation is audited via the existing `runtime_tool_invocations` write path (no parallel audit table).

### 6.4 Inbox routing on errors

When `tool.status` flips to `"error"` and the tool has any active consumer, an Inbox item is created for the tool's owner (NOT for each consumer) — the owner is the one who can fix the auth/schema/sandbox issue. Item kind `tool_errored`; ItemRef points at the tool. Per [cross-audit.md §9.1](../cross-audit.md), Inbox respects routing rules.

---

## §7 Frontend surface

### 7.1 Route map

- `/tools` — catalog (default tab: My)
- `/tools/<id>` — detail
- `/tools/<id>/invocations` — invocation history tab
- `/tools/<id>/edit` — owner-only editor
- `/tools/onboard` — wizard (entry)
- `/tools/onboard/openapi` / `/tools/onboard/mcp` / `/tools/onboard/code` / `/tools/onboard/skill` — wizard branches

### 7.2 Destination components (`packages/chat-surface/src/destinations/tools/`)

- `ToolsDestination.tsx` — shell with `<PageHeader>` + `<FilterTabs>` (My / Installed / Available / Custom / By kind) + `<CardGrid>` of `ToolCard`s.
- `ToolsPanel.tsx` — left rail: filter chips, search, "Onboard" CTA.
- `ToolCard.tsx` — name + kind chip + scope chip + status pill + 30-day usage spark.
- `ToolDetailView.tsx` — header + tabs (Overview / Args & Returns / Invocations / Used by / Audit / Edit).
- `ToolEditor.tsx` — owner-only form. Tabbed (Basics / Schema / Transport / Permissions).
- `OnboardingWizard.tsx` — step machine (`step` state local; `<FilterTabs>` for the auth picker).
- `ToolInvocationsTable.tsx` — paginated `<ActivityList>` (rows are ItemLink-wrapped to the run page).
- `ToolUsageChart.tsx` — read-only projection viz; mirrors `AgentUsageChart` from Phase 8.

All pure presentation. Data-binder is `apps/frontend/src/features/tools/`.

### 7.3 Code-routine editor

Inside `OnboardingWizard.tsx` (kind=code branch). Reuses an existing code-editor component if Library's PageEditor has one we can lift; else a minimal Monaco-or-CodeMirror integration kept inside the wizard (NOT in design-system; it's wizard-scoped).

### 7.4 Empty / error states

- Catalog empty: `<EmptyState>` with the four kind tiles (Built-in / MCP / API / Code) — each a deep-link to the wizard.
- Detail 404 → "Tool not found or removed" + back-link to catalog.
- Onboarding fails (test call errored): wizard stays on the test step; renders the error inline; provides "Save anyway with status=error" option.

---

## §8 Cross-destination linking

- Agent edit page: tool-grants picker → `GET /v1/tools?installed=true&kind=...` → `ItemLink ref={{ kind: "tool", id }}` for each grant.
- Composer tool-popover: same endpoint, scoped to current chat's tool allowlist.
- Library skill page → tool: when a Library page is created with `tag=skill`, a tool row is auto-created (`kind=skill`, `skill_page_ref` set). Soft-delete of the page soft-deletes the tool row (cascade by trigger).
- Inbox tool-errored item → tool detail.
- Home triage strip "errored tools" tile → `/tools?status=error`.

---

## §9 Sandbox + permissions for code-routines

### 9.1 Sandbox spec

- Language: Python 3.13 (Phase 10 lands Python only; JS deferred to Wave 11).
- Container: existing ai-backend worker pattern (warm pool of containers; `pyodide` was considered but rejected — too slow for non-trivial routines).
- Resource limits: timeout 30s; memory 512MB; CPU 1 core; disk write to `/tmp` only (ephemeral).
- Network: deny by default; opt-in to specific connector hosts (the connector allowlist from §3.1 `transport.connector_ref`).
- File system: read-only mount of `/atlas/code/<tool_id>/` containing the routine's source; write-only `/tmp`.

### 9.2 Permission model

A code-routine's `transport.connector_ref?` declares which connector it authenticates against. At call time the runtime:

1. Verifies the caller's grant includes this tool.
2. Verifies the caller has the connector (per [Phase 11 Connectors](../destinations/connectors-prd.md) — TBD).
3. Injects the connector's resolved token as an env var or callable in the sandbox.

### 9.3 Why this lands at Phase 10 not Phase 5

Routines §9.7 Q1: "Wire shape lands now (forwards-compatible); executor + sandbox deferred to Wave 6". Phase 10 is that Wave 6. The wire shape from Phase 5 (`code?: { repo_ref, env_ref, entry }`) is already in `packages/api-types/src/routines.ts`; Phase 10 plugs it into the Tools catalog (kind=code) and adds the sandbox executor in ai-backend. No api-types break for Routines.

---

## §10 Open questions (defer to orchestrator)

1. **Test-call cost.** Test calls hit real upstream APIs and consume real credentials. Should each test call require an admin confirmation when the auth picker is "API key"? **Recommend:** yes for write-scope tools; auto-allow for read-scope.
2. **MCP marketplace curation.** Phase 10 ships a config-file curated list. Should there be an `is_workspace_approved` flag on MCP registry rows so tenant admins can pre-approve specific servers? **Recommend:** yes — extends Phase 11's connector approval model; minimal new code.
3. **Code-routine dependency management.** A Python routine that imports `requests` needs that package available in the sandbox. Should we pin a fixed allowlist of allowed imports, or let routines declare a `requirements.txt` and rebuild the container? **Recommend:** fixed allowlist for Phase 10 (no rebuilds); declare-and-rebuild deferred to Wave 11.
4. **Cross-tenant tool sharing.** Atlas-published built-in tools (e.g. web-search) live in every tenant. Should there be a "shared library" concept that mirrors a tool across tenants without duplication? **Recommend:** no — built-ins are baked into the runtime, not into the catalog. The `tools` table only rows what's tenant-installed.
5. **Tool versioning.** Should tools have `current_version_id` like agents (Phase 8 §3.2)? **Recommend:** no for Phase 10. Patch is in-place; old args_schema migrations are caller-side (the grant snapshots the args_schema hash at install time, and the runtime warns when the hash drifts). Versioning revisit at Wave 7.
6. **Invocation log retention.** Master §3.3 says runtime events 365 days. Should tool invocations be longer (compliance evidence)? **Recommend:** 365 days default + per-tenant override (admin-set) up to 7 years. UI in Phase 12 Settings.
7. **Per-chat connector overrides.** Existing path already covers this; no Phase 10 work.
8. **Per-tool rate limits.** Master §5.7 open question. **Recommend:** yes — `tools.rate_limit_per_minute` and `rate_limit_per_day` on the table. UI in Phase 10. Defend at the runtime via a Redis token-bucket (or in-memory for single-instance dev). Burst tolerated 1.5×.
9. **Skill onboarding.** Phase 10 §7.3 routes skill onboarding to the Library page editor (kind=skill); the Tools wizard's "Skill" branch is just a deep-link to `/library/new?kind=skill`. **Recommend:** confirm — no editor in the Tools wizard for skills.

---

## §11 Phasing within Phase 10 (P10-A/B/C sub-phases)

| Sub-phase | Scope                                                                                                       | Estimated LOC | Worktree-able in parallel? |
| --------- | ----------------------------------------------------------------------------------------------------------- | ------------- | -------------------------- |
| P10-A1    | api-types/tools.ts canonical wire                                                                           | ~250          | Yes (independent)          |
| P10-A2    | services/backend/src/backend_app/tools/ — schema + service + store + routes + audit                         | ~900          | Yes (after A1)             |
| P10-A3    | services/ai-backend code-routine sandbox executor + Purpose.TOOL_CODE_RUN attribution                       | ~600          | Yes (independent)          |
| P10-A4    | facade tool_routes.py — proxy /v1/tools/\* + /v1/tools/stream                                               | ~200          | Yes (after A2)             |
| P10-B1    | chat-surface destinations/tools/ — ToolsDestination + ToolCard + ToolsPanel + filter                        | ~700          | Yes (after A1)             |
| P10-B2    | chat-surface ToolDetailView + ToolEditor + invocation table + usage chart                                   | ~700          | Yes (after B1)             |
| P10-B3    | chat-surface OnboardingWizard (OpenAPI + MCP + Code + Skill branches)                                       | ~900          | Yes (after B1)             |
| P10-C     | apps/frontend/src/features/tools/ — route + adapters + tests; replace existing Tools DestinationPlaceholder | ~500          | Yes (after A2 + B1)        |

Total: ~4750 LOC. Orchestrator dispatches A1 first, then A2/A3/B1 in parallel, then A4/B2/B3/C in parallel after.

---

## §12 Done definition

- Every endpoint in §4 implemented + tested (happy + ACL + tenant-isolation).
- Every component in §7 rendered + tested + a11y-pass (ARIA roles, keyboard nav).
- Code-routine sandbox executes the canonical "echo args" test routine in <500ms p95.
- A user can onboard the internal billing API end-to-end via the wizard with zero CLI usage.
- Catalog sort + filter perform under 200ms p95 on a tenant with 1000 tools.
- All audit rows write through the canonical helper; no parallel audit table.
- TU-1 invariant preserved: no direct LLM SDK imports in `services/backend/`; code-routine sandbox runs without LLM calls (LLM is opt-in via internal `/v1/llm/*` calls).
- `is_project_member` is the only project ACL helper used.
- ToolCard / ToolDetailView use SP-1 primitives — no inline color / spacing.

---

## §13 References

- [destinations-master-prd.md §5.7](../destinations-master-prd.md)
- [cross-audit.md](../cross-audit.md) §1.1 / §1.3 / §1.5 / §1.6 / §5.2 / §5.5
- [implementation-plan.md §2 Phase 10](../implementation-plan.md)
- [Routines §9.7 Q1](../cross-audit.md) — code-routines wire shape (P5-A1, already landed)
- [Agents PRD §3.2](agents-prd.md) — version-snapshot pattern (recap; Tools does NOT use snapshots in v1)
