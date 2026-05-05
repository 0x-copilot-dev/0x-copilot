# PR 3.3 — Inline MCP discovery card + two-stage approval UI polish

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 3, PR 3.3 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** ai-backend (one new tool + one event variant) · frontend (one new card variant + approval card transform polish) · api-types (one optional payload field)
> **Size:** **M.** PR 1.4 / 1.4.1 already shipped two-stage approvals end-to-end (server, worker, schema, audit chain, `<ApprovalTool>` UI with WorkspaceMemberPicker). This PR is the **MCP discovery** addition (a different card kind) plus the **inline transform polish** the design doc calls for.
> **Depends on:** PR 1.4 (two-stage approvals — implemented), PR 1.4.1 (approval forwarding hardening — implemented), PR 3.2 (workspace pane — Approvals tab consumes the same projection), existing `ConnectorAuthTool` for blocking MCP-auth.
> **Reads alongside:** [`pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md), [`pr-1.4.1-approval-forwarding-hardening.md`](pr-1.4.1-approval-forwarding-hardening.md), [`pr-3.2-workspace-pane-right-rail.md`](pr-3.2-workspace-pane-right-rail.md), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md), [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md)
> **Sibling docs (Wave 3):** PR 3.1 — citation chips + sources tab · PR 3.2 — workspace pane right rail · PR 3.4 — connector popover

---

## 0 · TL;DR

Two surfaces, both small.

**1. MCP discovery card (NEW).** The Atlas launch flow describes a non-blocking moment:

> _"Atlas notices a relevant Linear MCP server is available but not authorized. Inline card: 'Connect Linear to fetch ticket statuses?' with Connect / Skip buttons."_ — Design Doc, Flow — Launch step 3

Today we have only **blocking** MCP auth (`ConnectorAuthTool`, fired by `mcp_auth_required`). The agent already pauses execution and demands auth. The design needs a **proactive, non-blocking** invitation: the agent keeps working, the user can click Connect any time, and if Skip is clicked the agent moves on with whatever it can find without that connector.

We add **one new tool** (`suggest_mcp_connector`) the agent can call when it identifies a server that _would help_ but isn't authorized, and **one new optional payload flag** (`discovery_reason`) to distinguish the two. The existing `<ConnectorAuthTool>` component renders both kinds with a small variant switch.

**2. Two-stage approval UI polish (REFINEMENT).** PR 1.4 already wired the wire and the transform. Today the resolved card reads "Forwarded for sign-off — Waiting on @<user_id>." The design doc requires:

- **Recipient name resolution** — "@marcus", not "@usr_01HM…". The data is one round-trip away (`GET /v1/workspace/members/{id}`).
- **Final-state record** — once the leaf approves, the card transforms again into "Approved by Marcus at 10:45 · Posted to #announcements". Today it shows the existing approved-card with no chain visibility.
- **Approvals-tab projection** — the queue in PR 3.2's Approvals tab shows the same chain semantics (pending-on-someone-else as a distinct row state).

This is **5 polish tasks**, not a re-write. New code is small.

LoC estimate: ai-backend ≈ 110 (new tool + event flag + projection) · FE ≈ 180 (member-name resolver + chain-final transform + Approvals tab projection) · api-types ≈ 8.

---

## 1 · PRD

### 1.1 Problem

#### MCP discovery — a missing intent

The current MCP-auth flow is a hard interrupt: the agent halts, an inline "Connect Linear" card appears, the run is blocked until the user resolves it. That fits when the action _requires_ the connector ("I cannot answer your Salesforce question without Salesforce auth"). It does not fit the **launch-flow** scenario:

- The agent is gathering information from Notion, Drive, Slack.
- It notices that Linear has the ticket statuses that would round out the answer.
- It should **suggest** connecting, not **demand** connecting. The user should be able to skip without losing their place.

Today we don't model "suggest." The agent has only one MCP-related lever: the blocking interrupt. As a result, the launch flow either has the agent silently miss data (because no card is generated) or pause inappropriately (because the only card kind we have is blocking).

#### Approval forwarding — UI parity gaps

PR 1.4 / 1.4.1 implemented forwarding correctly on the wire and in the worker. The UI shows the right shape:

- Original card → "Forwarded for sign-off — Waiting on @<user_id>."
- Leaf decision → existing "Approved" / "Rejected" record.

But three details from the design are not yet there:

1. **Display name lookup.** The chip shows the raw `user_id` (`@usr_01HMP…`) because we don't resolve to a display name. The Atlas spec wants `@marcus`. There's no infrastructure problem — `services/backend` exposes `/internal/v1/users/{id}`, used by the existing forward picker (PR 1.4.1 Phase C). We just don't render names yet.
2. **Final-state chain visibility.** Once Marcus approves, the card today reads "Approved" with the original requester's identity. The design wants **"Approved by Marcus at 10:45 · Forwarded by Sarah at 10:41 · Posted to #announcements"** — the full chain in the inline record so scrollback tells the story.
3. **Approvals-tab projection.** PR 3.2's Approvals tab needs to distinguish "pending on me" from "pending on someone else (forwarded by me)". Today the projection treats all pending approvals identically.

### 1.2 Goals

1. **Add `suggest_mcp_connector` tool** the agent can call when it identifies a server that would improve an answer but isn't authorized. Non-blocking. Emits the existing `MCP_AUTH_REQUIRED` event with a new `discovery_reason` payload field that flips the card variant.
2. **Reuse the existing `<ConnectorAuthTool>` component** — one variant switch on `args.discovery_reason`. No new component file.
3. **Resolve forwarded recipient display names.** Add a tiny `useWorkspaceMember(userId)` hook that round-trips `GET /v1/workspace/members/{id}` and caches per-session. The existing `WorkspaceMemberPicker` already loads the catalog — we re-use that fetch.
4. **Chain-final transform.** When `approval_resolved` lands for a leaf approval whose `chain_parent_approval_id` is set, the FE transforms the original card into the "Approved by … · Forwarded by … · {action_summary}" record. This is a reducer branch addition, not new state.
5. **Approvals tab projection.** `useApprovalsQueue` (PR 3.2) gets one extra return slot: `{pending_on_me, pending_on_others, recent}`. The tab UI shows two empty headers when both lists are empty.
6. **Zero changes to LangGraph harness or worker.** Discovery is a tool call, not an interrupt. Approval polish is presentation-only.

### 1.3 Non-goals

- **No re-rendering blocking MCP-auth.** That flow remains as PR 1.1 / existing `ConnectorAuthTool` ships it. Variant flag is additive.
- **No server-side risk policy** ("auto-flag this action as sensitive"). PR 1.4 §1.3 already deferred. Forwarding remains client-driven.
- **No N-level chain UI.** PR 1.4 §1.3 — schema supports it, UI shows one forward step in v1.
- **No external-recipient forwarding.** W6 sharing schema territory.
- **No new event type.** Discovery reuses `mcp_auth_required` with `discovery_reason`. Chain-final transform reuses `approval_resolved` with the existing `chain_parent_approval_id` field.
- **No SLA timers / auto-escalation.** PR 1.4 deferred.
- **No promotion of the card variant into design-system.** Feature-only.
- **No connector-popover integration here.** PR 3.4 owns the per-chat connectors model; discovery is orthogonal (it's about authorizing the connector at the workspace+user layer).

### 1.4 Success criteria

- ✅ Agent can call `suggest_mcp_connector(server_id, reason, expected_value)` and have it render as a non-blocking inline card with **Connect / Skip** buttons.
- ✅ The agent **does not pause** while the card is on screen. Streaming continues. The user can ignore the card and the run completes with whatever sources it had.
- ✅ Click **Connect** triggers the existing OAuth flow (the same `connectors.authenticate(serverId)` path `ConnectorAuthTool` already uses); on success the card transforms into "Connected · Atlas can now use Linear when relevant."
- ✅ Click **Skip** records the decision (audit row) and the card transforms into "Skipped — answer without Linear."
- ✅ Forwarded approval cards show display names: "Waiting on @marcus" not "@usr\_…".
- ✅ Leaf approval resolution renders the **chain final record** inline: e.g. "Approved by Marcus at 10:45 · Forwarded by Sarah at 10:41".
- ✅ Approvals tab in PR 3.2 splits **pending on me** vs. **pending on others (forwarded by me)** when relevant.
- ✅ Discovery card replays deterministically from `replayRunEvents`. Skipped/connected state preserved.
- ✅ `make test` green.

### 1.5 User stories

| #    | Persona       | Story                                                                                                                                                                                                                                            |
| ---- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| US-1 | Sarah         | While drafting the launch announcement, Atlas posts an inline card: "Connect Linear to fetch ticket statuses?" — but keeps writing. I see the card and click Connect; the OAuth tab opens; I confirm; Atlas now references Linear in the answer. |
| US-2 | Sarah (skips) | I'm in a hurry; I click Skip. Atlas writes "Note: ticket statuses unavailable — connect Linear next time." The run completes.                                                                                                                    |
| US-3 | Sarah         | Atlas hits a hard wall ("I need Salesforce to read this opportunity"). Same `<ConnectorAuthTool>` renders but with the original blocking copy and no Skip option. Behavior unchanged.                                                            |
| US-4 | Sarah         | I forward an approval to Marcus; the in-thread chip reads "Waiting on **@marcus**", not the raw user id.                                                                                                                                         |
| US-5 | Sarah         | Marcus approves. The original card transforms into "Approved by **Marcus** at 10:45 · Forwarded by you at 10:41 · Posted to #announcements." I see the chain.                                                                                    |
| US-6 | Marcus        | I open the Approvals tab. There's one card under "Pending on me — forwarded by Sarah".                                                                                                                                                           |
| US-7 | Sarah         | I open the Approvals tab. The same chain shows under "Pending on others" (with my own pending items, if any, on top).                                                                                                                            |

---

## 2 · Spec

### 2.1 Wire — MCP discovery

#### 2.1.1 New tool: `suggest_mcp_connector`

Lives in `services/ai-backend/src/agent_runtime/capabilities/tools/builtin/suggest_mcp_connector.py`. It is a **tool**, not an interrupt.

```python
@tool
async def suggest_mcp_connector(
    server_id: str,
    reason: str,                  # "fetch ticket statuses", "look up Salesforce opportunity"
    expected_value: str,          # one-line: "could ground claims about ticket progress"
) -> dict:
    """Surface a non-blocking suggestion to authorize an MCP server.

    The agent calls this when it has identified a server that would
    improve the answer but isn't authenticated. Returns immediately
    (does not interrupt) so the agent can keep working with the
    sources it has.
    """
    # Idempotent per (run_id, server_id): subsequent calls for the
    # same pair noop and return {"already_suggested": True}.
    return await McpDiscoveryService.suggest(
        run_id=ctx.run_id,
        conversation_id=ctx.conversation_id,
        org_id=ctx.org_id,
        server_id=server_id,
        reason=reason,
        expected_value=expected_value,
    )
```

`McpDiscoveryService.suggest(...)` does three things:

1. Confirms `mcp_servers.{server_id}` is enabled for the org and that no `mcp_auth_connections` row exists for this user (otherwise return `{"already_authenticated": True}` and skip emitting the event).
2. Inserts an audit row (`runtime_audit_log` `action="mcp.discovery.suggested"`) — keeps the audit chain consistent with PR 1.4 forwarded events.
3. Emits an `MCP_AUTH_REQUIRED` event with the existing payload **plus** a new optional field:

```ts
// packages/api-types/src/index.ts — extend the existing payload
export interface RuntimeMcpAuthRequiredPayload {
  // existing fields …
  approval_id: string;
  server_id: string;
  display_name?: string;
  message?: string;
  expires_at?: string | null;
  /**
   * NEW (this PR). When present, the card is rendered as a non-blocking
   * suggestion ("Connect / Skip") rather than a blocking gate
   * ("Connect / Not now"). The run is **not paused** when this field
   * is set.
   */
  discovery_reason?: string | null;
  /** NEW (this PR). The agent's one-line value statement for the user. */
  expected_value?: string | null;
}
```

Two reasons we extend an existing event rather than adding a new one:

- The frontend already routes `mcp_auth_required` through `ConnectorAuthTool`. Re-using the routing is DRY.
- The _card kind_ is presentation; the projector already classifies as `activity_kind: connector` → no projection branch needed.

#### 2.1.2 Worker — does NOT pause

The blocking flow goes through `HumanInTheLoopMiddleware` and pauses the LangGraph until `Command(resume=…)`. The discovery flow does not. `suggest_mcp_connector` is a regular tool call: the harness keeps running.

To express "don't pause", `suggest_mcp_connector` is **not registered** with `interrupt_on=` (see `agent_runtime/execution/deep_agent_builder.py:146-173`). Only `mcp_auth_required` (the blocking variant, which is itself a tool call wrapped in an interrupt) gates the run.

In `RuntimeApprovalHandler` (worker), discovery `approval_id`s are **never** awaited for resume — the row's `metadata.discovery_reason` is set, the run stays in `RUNNING`, and the user's Connect / Skip is recorded by the resolution endpoint without affecting the run's flow.

#### 2.1.3 Resolution

**Connect** — fires the existing `connectors.authenticate(serverId)` path. On OAuth success, the run-level `mcp_auth_connections` row exists; future MCP loader middleware passes (the agent can now use the server in a subsequent reasoning loop).

**Skip** — `decideApproval(approval_id, "rejected", identity, "mcp_discovery_skipped")`. The server records the audit decision and resolves the approval row to `status='rejected'`. The run is not affected.

Both decisions emit the existing `approval_resolved` event so the FE reducer transforms the card via the same code path it uses for blocking auth.

### 2.2 Wire — approval polish (display names + chain final)

**Display names.** No new endpoint. We add a thin FE hook:

```ts
// apps/frontend/src/features/workspace/useWorkspaceMember.ts
export interface WorkspaceMember {
  user_id: string;
  display_name: string;
  email?: string;
  handle?: string; // "@marcus" if present
}

// Cached per-session (Map<user_id, WorkspaceMember | "loading" | "error">).
export function useWorkspaceMember(
  userId: string | null,
): WorkspaceMember | null;
```

Round-trips `GET /v1/workspace/members/{id}` (already proxied through `backend-facade`; identity headers preserved). Cache lifetime = browser session. Bulk loader (`useWorkspaceMembers([id…])`) deduplicates within a render and lives next to it; PR 1.4.1 Phase C's `WorkspaceMemberPicker` shares the cache.

**Chain final transform.** Today `ApprovalTool` renders three states based on `result`:

- `result === undefined` → pending card.
- `result.status === "forwarded"` → "Waiting on @user_id" pill.
- otherwise → "Approved" / "Rejected" record.

We extend the third branch: when `result.chain_parent_approval_id` is set (PR 1.4 already populates this on forwarded chains), the card renders the chain:

```
Approved by @marcus at 10:45
  ↳ forwarded by @sarah at 10:41
  Posted to #announcements
```

This is a 30-LOC change to the `resolved && !isForwarded` branch in `ApprovalTool.tsx`. The data is already on the wire; we just don't render it.

**Approvals-tab projection.** PR 3.2 ships `useApprovalsQueue(items, activeRunId)`. We extend its return:

```ts
// from PR 3.2:
interface ApprovalsQueueProjection {
  pending: ApprovalQueueItem[];
  recent: ApprovalQueueItem[];
}

// after PR 3.3:
interface ApprovalsQueueProjection {
  pending_on_me: ApprovalQueueItem[]; // unresolved + addressed to current user
  pending_on_others: ApprovalQueueItem[]; // unresolved + forwarded by current user
  recent: ApprovalQueueItem[];
}
```

The two pending lists drive separate sub-headers in `ApprovalsTab`. Same projection as before; one new filter.

### 2.3 FE — components added, components reused

| Component                        | Source                                                       | Notes                                                                                                                                                           |
| -------------------------------- | ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ConnectorAuthTool`              | _existing_                                                   | One added prop `variant: "blocking" \| "discovery"` derived from `args.discovery_reason`. Discovery copy: "Connect / Skip"; blocking copy: "Connect / Not now". |
| `ApprovalTool`                   | _existing_ (PR 1.4)                                          | One reducer branch addition for chain-final transform; one display-name swap.                                                                                   |
| `useWorkspaceMember` (NEW, tiny) | `apps/frontend/src/features/workspace/useWorkspaceMember.ts` | Single GET + per-session cache. Used by `<ApprovalTool>` and `<WorkspaceMemberPicker>` (PR 1.4.1 Phase C — already exists; share the cache).                    |
| `MentionLabel` (NEW, tiny)       | `apps/frontend/src/features/workspace/MentionLabel.tsx`      | `<MentionLabel userId="usr_…" />` → "@marcus" (loading skeleton during fetch, raw id on error). 30 LOC.                                                         |
| `useApprovalsQueue`              | _PR 3.2_                                                     | Extended to return three lists.                                                                                                                                 |
| `ApprovalsTab`                   | _PR 3.2_                                                     | Adds the second header.                                                                                                                                         |

### 2.4 ai-backend — files

| File                                                                                              | Purpose                                                                                            |
| ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `services/ai-backend/src/agent_runtime/capabilities/tools/builtin/suggest_mcp_connector.py` (NEW) | The non-blocking tool. ≤80 LOC.                                                                    |
| `services/ai-backend/src/agent_runtime/api/mcp_discovery_service.py` (NEW)                        | `McpDiscoveryService.suggest(...)`. Idempotency, audit row, event emission. ≤60 LOC.               |
| `services/ai-backend/src/runtime_api/schemas/events.py`                                           | Extend the `mcp_auth_required` payload schema with optional `discovery_reason` + `expected_value`. |
| `services/ai-backend/src/agent_runtime/api/constants.py`                                          | New audit action string `mcp.discovery.suggested`.                                                 |
| `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py`                           | Register the new tool in the default toolkit. **Not** added to `interrupt_on=`.                    |
| `packages/api-types/src/index.ts`                                                                 | Extend `RuntimeMcpAuthRequiredPayload` with the two optional fields.                               |

### 2.5 Streaming impact — explicit

| Subsystem                            | Touched?                                                                                                                                                                                                                               |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runtime_events` schema              | **No.** No new event type.                                                                                                                                                                                                             |
| `RuntimeEventEnvelope` payload union | **Additive.** Existing `mcp_auth_required` payload gains two optional fields.                                                                                                                                                          |
| SSE handshake (`?after_sequence=N`)  | **No.** Reconnect identical.                                                                                                                                                                                                           |
| `runtime_worker` job loop            | **No.** Discovery is a regular tool call; chain final is presentation-only.                                                                                                                                                            |
| `chatModel/eventReducer.ts`          | **One new branch:** when `approval_resolved` lands and the row's `chain_parent_approval_id !== null`, the reducer also tags the **parent** card with the leaf decision so the inline transform fires on the original `<ApprovalTool>`. |
| Capabilities middleware              | **No.**                                                                                                                                                                                                                                |
| Audit chain                          | **One new action** (`mcp.discovery.suggested`) — written through the existing append-only chain.                                                                                                                                       |

### 2.6 Permissions

| Caller                                              | Action                                                                                                                                       |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Conversation owner                                  | May Connect (their own OAuth) or Skip (records skip + audit).                                                                                |
| Workspace member viewing a shared conversation (W6) | The discovery card renders read-only (Connect / Skip disabled with tooltip). Future PR may allow recipient to authorize their own connector. |
| Workspace admin                                     | Same as conversation owner.                                                                                                                  |
| Service-to-service                                  | The tool is invoked only by the agent harness; no external caller.                                                                           |

Approval polish honors the existing PR 1.4 / 1.4.1 permission story (only the recipient resolves the leaf approval; only workspace members can be picked; etc.).

### 2.7 Error semantics

| Condition                                                          | UI behavior                                                                                                               |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| `suggest_mcp_connector` called but server is already authenticated | Tool returns `{"already_authenticated": True}`; no event emitted; no card rendered.                                       |
| Same suggestion fired twice in one run                             | Idempotent in service: `(run_id, server_id)` key. Second call returns `{"already_suggested": True}`; one card on screen.  |
| Connect button → OAuth fails / user cancels                        | Card renders "Couldn't connect — try again" with retry button. Run continues.                                             |
| Skip button → audit write fails                                    | UI optimistic; on failure shows "Couldn't record skip" banner; the card transforms anyway (skip is a non-binding signal). |
| Discovery card never resolved by user                              | After `expires_at` (default 30 minutes) the row is marked `expired`; card transforms to grey "Suggestion expired" record. |
| `useWorkspaceMember` returns 404 (member removed)                  | `MentionLabel` falls back to raw user_id with a tooltip "Member no longer in workspace".                                  |
| `useWorkspaceMember` returns 5xx                                   | Falls back to raw id with retry-on-mount; doesn't poison cache.                                                           |
| Chain-final rendered before parent display name resolves           | Inline record renders skeleton "Approved by … at 10:45" until the name lands; replays show the same.                      |
| Replay loads a forwarded chain                                     | Reducer rebuilds: original card → "Waiting on …" → chain-final transform — exactly as during live run.                    |

### 2.8 Accessibility

- Discovery card carries `role="status"` (not `role="alert"`) — non-blocking, polite. Same focus order as today's blocking auth card.
- Connect / Skip buttons are tabbable; Enter/Space activates.
- `MentionLabel` exposes `aria-label="Marcus (member)"`; screen readers announce the resolved name once available.
- Chain-final inline record uses semantic structure: `<dl>` of `dt`/`dd` for "Approved by", "Forwarded by", "Posted to" (extends today's `approvalDetailsContent`).

### 2.9 What we explicitly do NOT add

- **No new event type.** Existing `mcp_auth_required` carries discovery via additive flag.
- **No new middleware.** Discovery is a tool, not an interrupt.
- **No new API edge for forwarding.** PR 1.4 / 1.4.1 own that.
- **No new design-system primitive.** Card variant lives on the existing `<ConnectorAuthTool>`.
- **No member-search endpoint.** PR 1.4.1 Phase C ships `GET /v1/workspace/members?q=`; we re-use it (no changes).

---

## 3 · Architecture

### 3.1 Where the pieces live

```
     ┌──────────────────────────────────────────────────────────────────┐
     │  Agent harness (existing)                                        │
     │   ─ DeepAgent.invoke()                                          │
     │       │                                                          │
     │       ├─ tool: search_*  (existing)                              │
     │       ├─ tool: suggest_mcp_connector  ◀── NEW (this PR)           │
     │       │   │  via McpDiscoveryService                             │
     │       │   ▼                                                      │
     │       │   - check existing auth                                  │
     │       │   - audit row                                            │
     │       │   - emit MCP_AUTH_REQUIRED { discovery_reason: "…" }     │
     │       │   - return immediately (no interrupt)                    │
     │       │                                                          │
     │       └─ continue reasoning (run NOT paused)                     │
     │                                                                   │
     │   ─ HumanInTheLoopMiddleware (existing) handles only true gates  │
     │       (mcp_auth_required without discovery_reason, tool_action,   │
     │        ask_a_question)                                           │
     └──────────────────────────────────────────────────────────────────┘
                               │ SSE
                               ▼
     ┌──────────────────────────────────────────────────────────────────┐
     │  Frontend (existing controller)                                  │
     │   chatModel/eventReducer.ts                                      │
     │     mcp_auth_required → ChatItem with toolName "mcp_auth_required"│
     │       │                                                          │
     │       ▼                                                          │
     │   <ConnectorAuthTool args=… result=…>                            │
     │       variant = args.discovery_reason ? "discovery" : "blocking" │
     │       (NEW — single switch, ~25 LOC)                             │
     │                                                                   │
     │   chatModel/eventReducer.ts                                      │
     │     approval_resolved (chain leaf) → tag parent card             │
     │       │                                                          │
     │       ▼                                                          │
     │   <ApprovalTool args=… result=… leaf=…>                          │
     │       resolved + chain_parent → chain-final inline record (NEW)  │
     │                                                                   │
     │   <MentionLabel userId> ─►  useWorkspaceMember(userId) ─►  cache │
     │                                                                   │
     │   PR 3.2 <ApprovalsTab>                                          │
     │     useApprovalsQueue(items, activeRunId) → 3 lists (extended)    │
     └──────────────────────────────────────────────────────────────────┘
```

### 3.2 Sequence — discovery on the launch flow

```
Sarah                             Worker / Agent                                       FE
 │   "Draft launch announcement"                                                       │
 │ ───────────────────────────►                                                        │
 │                                run starts; agent reasons over Notion + Drive       │
 │                                agent decides Linear ticket data would help          │
 │                                ToolNode → suggest_mcp_connector(linear, …)          │
 │                                  ─ check auth: no row in mcp_auth_connections       │
 │                                  ─ audit: mcp.discovery.suggested                   │
 │                                  ─ emit MCP_AUTH_REQUIRED{discovery_reason: "…"}    │
 │                                  ─ tool returns {"emitted": true}                   │
 │                                agent CONTINUES — drafts the announcement           │
 │                                                                                     │
 │                                ◄ SSE: mcp_auth_required {discovery_reason}          │
 │                                                                                     │
 │                                            <ConnectorAuthTool variant="discovery">  │
 │                                            "Connect Linear to fetch ticket statuses?│
 │                                             [Connect] [Skip]"                       │
 │                                                                                     │
 │                                ◄ SSE: model_delta "Posting press window …"          │
 │                                                                                     │
 │  user clicks Skip                                                                   │
 │ ─────────────────────────────► decideApproval(id, rejected, "mcp_discovery_skipped")│
 │                                                                                     │
 │                                ◄ SSE: approval_resolved {status: "rejected"}        │
 │                                                                                     │
 │                                            <ConnectorAuthTool> transforms           │
 │                                            "Skipped — answer without Linear"       │
 │                                                                                     │
 │                                ◄ SSE: final_response                                │
```

### 3.3 Sequence — approval polish (chain-final)

```
Sarah                                                                 Worker / API                                FE
 │  Approve & forward to Marcus (existing PR 1.4 path)                                                            │
 │ ─────────────────────────────────────────────────────────────────► forward; emit approval_forwarded            │
 │                                                                     emit approval_resolved {status: forwarded} │
 │                                                                                                                 │
 │                                                                                                                 │ ─►  reducer flips parent card to
 │                                                                                                                 │     "Waiting on @MentionLabel"
 │                                                                                                                 │     <MentionLabel> resolves → "@marcus"
 │                                                                                                                 │
 │  Marcus approves (existing PR 1.4 path)                                                                         │
 │ ─────────────────────────────────────────────────────────────────► leaf; emit approval_resolved {status: approved│
 │                                                                                                  chain_parent_id}│
 │                                                                                                                 │ ─►  reducer ALSO tags parent
 │                                                                                                                 │     <ApprovalTool> resolved + chain_parent
 │                                                                                                                 │     renders chain-final record:
 │                                                                                                                 │     "Approved by @marcus at 10:45
 │                                                                                                                 │      ↳ forwarded by you at 10:41
 │                                                                                                                 │      Posted to #announcements"
 │                                                                                                                 │
 │  Marcus rejects (variant)                                                                                       │
 │ ────────────────────────────────────────────────────────────────► leaf; emit approval_resolved {status: rejected,│
 │                                                                                                  chain_parent_id}│
 │                                                                                                                 │ ─►  parent transforms to
 │                                                                                                                 │     "Rejected by @marcus at 10:45
 │                                                                                                                 │      ↳ forwarded by you at 10:41"
```

### 3.4 DRY — what's reused vs. what's added

| Concern             | Reuse                                             | Add                                               |
| ------------------- | ------------------------------------------------- | ------------------------------------------------- |
| Tool registration   | DeepAgent toolkit (existing)                      | one tool decl in the toolkit (~5 LOC)             |
| Event payload       | `MCP_AUTH_REQUIRED` projector (existing)          | two optional fields                               |
| Card rendering      | `ConnectorAuthTool` (existing)                    | one variant switch + copy table                   |
| Approval card       | `ApprovalTool` (PR 1.4 / 1.4.1)                   | chain-final inline branch (~30 LOC)               |
| Member lookup       | `GET /v1/workspace/members/{id}` (PR 1.4.1)       | one tiny `useWorkspaceMember` hook                |
| Member name display | —                                                 | `<MentionLabel>` component (~30 LOC)              |
| Approvals queue     | `useApprovalsQueue` (PR 3.2)                      | one extra filter on `forward_to_user_id === self` |
| Audit chain         | `runtime_audit_log` (existing append-only chain)  | one new action string                             |
| Skipped state       | `decideApproval(rejected, …)` (existing endpoint) | one reason code (`mcp_discovery_skipped`)         |

Net new: ai-backend ≈ 110 LOC · FE ≈ 180 LOC · api-types ≈ 8.

### 3.5 Dependency survey

- **`@radix-ui/react-toast`** for non-blocking confirmations — not needed; the discovery card itself is the surface.
- **`react-query` / `swr`** for member-name caching — overkill for one route. Inline `Map<id, …>` cache is fine; if multiple PRs need it, promote to a query client later.
- **Reasoning frameworks for tool naming** — none relevant; the tool is small.

We add nothing from npm.

### 3.6 Edge cases

| Case                                                                          | Behavior                                                                                                                                                                                                      |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Agent calls `suggest_mcp_connector` for a server the org admin has disabled   | Tool returns `{"server_disabled": True}`; no card; no event. Audit row records the attempt.                                                                                                                   |
| Agent calls `suggest_mcp_connector` twice for the same server in the same run | Second call no-ops (idempotency); same `approval_id`.                                                                                                                                                         |
| User clicks Connect mid-stream; OAuth flow takes 30 seconds                   | Card stays "Connecting…" while OAuth is in progress (existing pattern); run continues independently. On completion the card transforms; the agent on its next reasoning loop can use Linear if it chooses to. |
| User clicks Skip on a discovery card mid-stream                               | Card transforms to "Skipped"; run unaffected; subsequent prompts still see Linear in the per-chat connectors pill (PR 3.4).                                                                                   |
| User opens a discovery card, never resolves, run completes                    | Card transforms to grey "Suggestion (unresolved)" record after `expires_at`. On next prompt with the same need the agent may suggest again.                                                                   |
| Discovery card displayed in shared (read-only) view                           | Connect / Skip disabled; tooltip explains read-only. Future PR may allow recipient connect.                                                                                                                   |
| `MentionLabel` cache miss + simultaneous render of multiple cards             | Bulk loader (`useWorkspaceMembers([id…])`) batches a single `?ids=…` query when supported, else falls back to per-id fetch.                                                                                   |
| Chain-final lands while user is on a different conversation                   | Reducer applies regardless; when user navigates back the parent card already shows the final record.                                                                                                          |
| Replay of a discovery card the user skipped                                   | Reducer reapplies `approval_resolved` event; card renders Skipped from cold start.                                                                                                                            |
| Replay of an MCP-auth (blocking) card the user has never resolved             | Card renders pending; status pill is "Waiting for permission" (existing path); nothing changes.                                                                                                               |

### 3.7 Test plan

**ai-backend**

- `tests/unit/agent_runtime/capabilities/test_suggest_mcp_connector.py` — happy path; idempotency; already-authenticated short-circuit; disabled-server short-circuit; audit row; event payload shape.
- `tests/unit/runtime_api/schemas/test_mcp_auth_required_payload.py` — additive fields parse; existing payloads still parse.
- `tests/integration/runtime_worker/test_discovery_does_not_pause.py` — emits a suggest then a final_response in the same run without a `Command(resume=…)`; run terminates `COMPLETED`.

**Frontend**

- `ConnectorAuthTool.variant.test.tsx` — discovery vs. blocking copy + buttons; Skip routes to `decideApproval(rejected, "mcp_discovery_skipped")`; Connect routes to `connectors.authenticate(serverId)`.
- `ApprovalTool.chain-final.test.tsx` — leaf approval with `chain_parent_approval_id` renders chain record; reject variant; replays match live.
- `useWorkspaceMember.test.ts` — cache hit; 404 fallback; 5xx retry semantics.
- `MentionLabel.test.tsx` — loading skeleton; resolved name; error fallback.
- `useApprovalsQueue.test.ts` — extension to `pending_on_me / pending_on_others / recent`.

**Cross-service smoke**

- `make test` — extend the launch-announcement scenario to assert (1) a discovery card appears mid-stream and the run continues, (2) chain-final transforms once the leaf approves.

### 3.8 Rollout

- **Flag-free for the chain-final transform** — purely visual on existing wire.
- **Flag-gated for `suggest_mcp_connector`** — `RUNTIME_FEATURE_MCP_DISCOVERY=true` env var on ai-backend; falls back to no-op tool registration when off. One env, one PR-level switch.
- **Backout.** Revert PR. The variant flag drops back; existing blocking flow unaffected. Chain-final transform reverts to today's "Approved" record.

### 3.9 Open questions

1. **Should Skip be permanent per (user, server)?** v1: per-discovery-card only. A future PR could record "Sarah has muted Linear suggestions for Q1 launch" but that's a Settings → Notifications feature.
2. **Should the agent see the user's skip when reasoning about subsequent prompts?** v1: no — the agent reads only its working memory and capability snapshot. If we want skip persistence to influence reasoning, we'd add a `runtime_memory_items` row keyed by `(user, server, conversation)`. Out of scope.
3. **Bulk member lookup endpoint.** Today `GET /v1/workspace/members/{id}` is single-id. If a thread has > 5 forwarded chains the FE batches client-side. If contention shows in profiling, add `?ids=` server support.

---

## 4 · Acceptance checklist

- [ ] `services/ai-backend/src/agent_runtime/capabilities/tools/builtin/suggest_mcp_connector.py` ships and is registered in the default toolkit.
- [ ] `services/ai-backend/src/agent_runtime/api/mcp_discovery_service.py` ships with idempotency + audit + event emission.
- [ ] `services/ai-backend/src/runtime_api/schemas/events.py` extends `mcp_auth_required` payload with `discovery_reason`, `expected_value` (both optional).
- [ ] `services/ai-backend/src/agent_runtime/api/constants.py` adds `mcp.discovery.suggested` audit action string.
- [ ] `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py` registers the tool but does **not** add it to `interrupt_on=`.
- [ ] `packages/api-types/src/index.ts` extends `RuntimeMcpAuthRequiredPayload` with the two optional fields.
- [ ] `apps/frontend/src/features/chat/components/tools/ConnectorAuthTool.tsx` reads `args.discovery_reason` and renders the appropriate copy + Skip semantics.
- [ ] `apps/frontend/src/features/chat/components/tools/ApprovalTool.tsx` extends the `resolved && !isForwarded` branch to render the chain final record when `result.chain_parent_approval_id` is present.
- [ ] `apps/frontend/src/features/workspace/useWorkspaceMember.ts` and `MentionLabel.tsx` ship; cache lifetime = session.
- [ ] `WorkspaceMemberPicker` (PR 1.4.1 Phase C) shares the cache.
- [ ] `apps/frontend/src/features/chat/chatModel/eventReducer.ts` adds the chain-final tag branch.
- [ ] `useApprovalsQueue` (PR 3.2) returns three lists; `ApprovalsTab` renders the second header.
- [ ] No new `RuntimeApiEventType`. Pydantic schemas otherwise unchanged.
- [ ] Discovery card replays deterministically (`replayRunEvents`).
- [ ] `npm run typecheck` clean; `npm run build` clean.
- [ ] `make test` green.

---

## 5 · References

- [`apps/frontend/src/features/chat/components/tools/ConnectorAuthTool.tsx`](../../apps/frontend/src/features/chat/components/tools/ConnectorAuthTool.tsx) — extended with variant.
- [`apps/frontend/src/features/chat/components/tools/ApprovalTool.tsx`](../../apps/frontend/src/features/chat/components/tools/ApprovalTool.tsx) — extended with chain-final transform.
- [`services/ai-backend/src/runtime_worker/handlers/approval.py`](../../services/ai-backend/src/runtime_worker/handlers/approval.py) — leaf-resume contract (unchanged).
- [`services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py`](../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py) — toolkit registration; `interrupt_on=` configuration.
- [`docs/new-design/pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md) — wire + worker for forwarding.
- [`docs/new-design/pr-1.4.1-approval-forwarding-hardening.md`](pr-1.4.1-approval-forwarding-hardening.md) — Phase C (member picker + facade route).
- [`docs/new-design/pr-3.2-workspace-pane-right-rail.md`](pr-3.2-workspace-pane-right-rail.md) — Approvals tab projection consumer.
- [LangChain `HumanInTheLoopMiddleware`](https://python.langchain.com/docs/concepts/middleware) — interrupt contract (unchanged).
- Atlas Design Doc — §"Flow — Launch (full agent)" step 3 + step 5, §"Flow — Approval" step 3, §"Approvals as content, not modals".
