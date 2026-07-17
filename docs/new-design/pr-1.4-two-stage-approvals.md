# PR 1.4 — Two-stage approvals (forward chain)

> **Status:** Spec · v1 · Owner: TBD · Target wave: W1 (blocker for the launch‑announcement flow in W3)
> **Scope:** `services/ai-backend` (persistence + worker + projector) · `apps/frontend` (decide UI + pending‑on‑someone‑else card) · `services/backend` (notification fan‑out — optional this PR) · `packages/api-types` (wire contract)
> **Reads alongside:** [`docs/new-design/00-plan.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md), Atlas Design Doc (handoff bundle, §"Flow — Launch (full agent)" step 5, §"Flow — Approval", §"Approvals as content, not modals" decision), `services/ai-backend/CLAUDE.md`, `apps/frontend/CLAUDE.md`, sibling PRDs `01-citations-live-registry.md`, `pr-1-2-per-chat-connector-scope.md`, `pr-1.3-draft-artifact.md`.

---

## 1 · PRD

### 1.1 Problem

The Atlas launch‑announcement flow has a marquee step:

> _"Inline approval card: 'Post to #announcements? @marcus must approve.' Two‑stage: Atlas waits for the human in the chat, who must approve **before it sends to Marcus**."_ — Design Doc, Flow — Launch step 5.

Today the runtime only models a **single‑actor** approval: the user who is in the chat decides, and the LangGraph graph resumes immediately. There is no notion of _forwarding_ a decision to another workspace member, no chain of custody when a sensitive action requires multiple sign‑offs, and no way for the chat to render "waiting on Marcus" inline as the design demands.

A faithful implementation requires:

- A way for an in‑chat user to **approve and route to** a second workspace member without losing the LangGraph interrupt.
- The agent harness to remain **paused** until the chain's leaf decision arrives.
- The chat to render the chain **inline as content** (per the design's "Approvals as content, not modals" decision) — not as a modal, not as a separate inbox the user must hunt for.
- Audit retains who approved what, in what order, against the same `tool_invocation_id`.

### 1.2 Goals

1. **Forwarding is bookkeeping, not a harness change.** The LangChain `HumanInTheLoopMiddleware` and the LangGraph interrupt/resume contract stay byte‑identical. The graph sees exactly one resume, with the leaf decision, exactly as it does today.
2. **One new decision type at the API edge** — `forward` — that creates a child `runtime_approval_requests` row, emits an `approval_forwarded` event, and _does not_ resume the run. Approve and reject behave exactly as they do today.
3. **Inline UI per design.** The original approval card transforms into a "Waiting on @marcus · forwarded by Sarah at 10:41" pill; on leaf decision it transforms again into the existing "Approved by Marcus at 10:45 · Posted to #announcements" record.
4. **Compliance‑grade audit.** Each link in the chain produces an immutable `runtime_audit_log` row keyed by `(approval_id, decided_by_user_id, decision, parent_approval_id)`. The chain is reconstructable from the table without traversing events.
5. **Notifications use a port we already have.** Forward emits a notification through the existing `notification` adapter (Slack DM / email / desktop, per Settings → Notifications matrix). If the matrix isn't built yet (W4.1), the adapter is a no‑op and the inbox UI is the recipient's only signal — an acceptable first‑PR fallback.
6. **Less code than the question implies.** Net new in ai‑backend: 1 migration (~50 lines), 1 enum value + 1 event type (~30 lines), ~120 lines in the worker handler, ~60 lines in the API service. No new module. No new graph node.

### 1.3 Non‑goals (this PR)

- **N‑level chains in v1.** The schema supports an arbitrary chain (each approval has at most one `chain_parent_approval_id`); the UI in v1 only surfaces one forward step. Multi‑hop policy ("send to legal _and_ security") is W6+.
- **External recipients.** Forward target is a workspace user (`forward_to.kind = "workspace_user"`) only. Email‑link approvals (forward to `external_email`) lands in W6 alongside the share schema, which already gives us a token vault, recipient table, and ACL story.
- **Re‑routing after forward.** Once forwarded, the only resolutions are the target's approve / reject. The original requester cannot "take it back" — they can `cancel_run` and start over. (Same constraint as today's single‑actor approvals.)
- **Notification matrix UI.** This PR fires events through the notification port; the per‑user Slack/email/desktop matrix is W4.1.
- **SLA timers / auto‑escalation.** Reuse `expires_at` (already in the table) for v1. Auto‑forward on expiry is a follow‑up.
- **Risk‑policy server.** The Atlas spec hints at "policies that determine which actions need two‑stage approval"; a generic policy DSL is out of scope. v1 trigger is **client‑driven** (the deciding user picks "Approve & forward to…"). A server‑side policy ("any Slack write to `#announcements` requires forwarding") is captured as a follow‑up that bolts onto the same chain.
- **Forwarding `ask_a_question` interrupts.** Only `tool_action` and `mcp_auth` approval kinds are forwardable in v1. `ask_a_question` is a clarification to the _requester_, not a sensitive action — forwarding makes no semantic sense.

### 1.4 Success criteria

- Sarah's "Approve & forward to Marcus" produces a pending approval addressed to Marcus, while the run remains `WAITING_FOR_APPROVAL` (validated against `runtime_approval_requests.status` and `agent_runs.status`).
- The chat renders the original card as "Waiting on @marcus" within ≤1 frame of the API ack; Marcus sees the new card in his Approvals tab + a notification (when adapter wired).
- Marcus's `approve` resumes the LangGraph harness with the **same** `Command(resume=...)` payload shape used today (`{"decisions": [{"type": "approve"}]}` for tool actions, `{"decision": "approved", "approval_id": …}` for MCP auth). Zero changes to `RuntimeApprovalHandler._resume_payload`'s output shape.
- `make test` passes; ai‑backend full suite passes; `runtime_approval_requests` and `runtime_audit_log` tables hold complete chain rows with append‑only triggers intact.
- Replaying the run via `replayRunEvents` produces the same chain UI deterministically (forwarded card → resolved record).
- A reject anywhere in the chain terminates with a single `Command(resume={"decisions": [{"type": "reject"}]})` and the in‑thread record reads "Rejected by Marcus at 10:45 · Forwarded by Sarah."
- New code surface ≤ ~350 LoC across ai‑backend (excluding tests + use‑case doc).

### 1.5 User stories

| #    | Persona                              | Story                                                                                                                                                                                                                                       |
| ---- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US‑1 | Sarah (Marketing Ops, in chat)       | Atlas pauses on "Post draft to #launch‑aurora? @marcus must approve." I click **Approve & forward to Marcus**. The card transforms in place into "Waiting on @marcus — forwarded by you at 10:41." I keep working in another chat.          |
| US‑2 | Marcus (recipient)                   | I get a desktop ping (or see an unread badge on Approvals). The Approvals tab shows the forwarded card with the same draft preview Sarah saw plus an explicit "Forwarded by Sarah · 10:41" line. I click **Approve**.                       |
| US‑3 | Sarah (after Marcus approves)        | The card in my chat transforms into "Approved by Marcus at 10:45 · Posted to #announcements." I never had to switch chats.                                                                                                                  |
| US‑4 | Anyone replaying scrollback later    | A week later I open the thread; the inline record reads exactly as it did then — full chain visible: who initiated, who forwarded, who approved, when each happened.                                                                        |
| US‑5 | Auditor                              | I export the audit log; one decision produced two rows (`forward` by Sarah, `approve` by Marcus), both linkable via `chain_parent_approval_id`, both signed in the existing append‑only chain.                                              |
| US‑6 | Marcus rejects                       | Marcus rejects with a reason. The card in Sarah's chat reads "Rejected by Marcus at 10:45 · Reason: 'Hold for press embargo'." The run terminates the action without posting.                                                               |
| US‑7 | Marcus is offline                    | Marcus has the desktop adapter off. The notification fan‑out fails open: the approval still appears in his Approvals tab; an email is queued (when wired). The run stays `WAITING_FOR_APPROVAL` until he resolves it or `expires_at` fires. |
| US‑8 | Sarah re‑opens chat after forwarding | She loads history; replay rebuilds: original card → forwarded pill → (resolved or still pending). No client state required to render correctly.                                                                                             |

---

## 2 · Wire contract

We extend exactly **one** request shape, add **one** decision enum value, and define **one** new event type. Nothing else on the wire moves.

### 2.1 Decision request (extension)

```ts
// packages/api-types/src/index.ts
export type ApprovalDecision = "approved" | "rejected" | "forwarded"; // ← new variant

export interface ApprovalForwardTarget {
  kind: "workspace_user"; // forward to an internal member; v2 adds "external_email"
  user_id: string;
}

export interface ApprovalDecisionRequest {
  decision: ApprovalDecision;
  decided_by_user_id: string;
  reason?: string | null;
  answer?: string | null; // existing (ask_a_question)
  forward_to?: ApprovalForwardTarget | null; // ← new; required iff decision === "forwarded"
}
```

Server‑side enforcement (validators on `ApprovalDecisionRequest` and a `model_validator(mode="after")`):

- `decision === "forwarded"` ⇒ `forward_to` is set and `forward_to.user_id !== decided_by_user_id` (no self‑forward).
- `decision !== "forwarded"` ⇒ `forward_to` is `None`.
- `forward_to.user_id` resolves to a `users` row in the same `org_id` whose membership is active. (Backend lookup; no new endpoint — re‑uses `/internal/v1/users/{id}`.)
- `approval.metadata.approval_kind` ∈ `{"tool_action", "mcp_auth"}` (forwarding `ask_a_question` returns 422; see §1.3).
- `approval.status === "pending"` (chain step's row, not the original).

### 2.2 New event: `approval_forwarded`

```ts
export interface RuntimeApprovalForwardedEvent extends RuntimeEventEnvelopeBase {
  event_type: "approval_forwarded";
  payload: {
    approval_id: string; // the *child* approval (assigned to recipient)
    chain_parent_approval_id: string; // the original (now resolved with status="forwarded")
    forwarded_by_user_id: string;
    forwarded_to_user_id: string;
    forwarded_at: string; // ISO 8601
    action_summary: string; // copied from parent for display continuity
  };
}
```

The presentation projector emits:

| Field           | Value                                   |
| --------------- | --------------------------------------- |
| `activity_kind` | `approval`                              |
| `display_title` | `Forwarded to {recipient_display_name}` |
| `summary`       | first 160 chars of `action_summary`     |
| `status`        | `pending`                               |

This means the existing FE pipeline (which already keys on the projection fields, never on `event_type` strings — see `apps/frontend/CLAUDE.md`) renders the new card with no special‑casing in shared chrome; only the inline thread adds a transform.

### 2.3 The other two events do not change

- `approval_requested` — same payload as today; emitted twice in a chain (one for the original, one for the child). They share `metadata.tool_invocation_id` so the chat can collapse them into one inline card per action.
- `approval_resolved` — same payload as today, fired once for the leaf approval. The runtime treats this as the **only** resume signal. (Forwarded approvals also receive `approval_resolved` events with `status="forwarded"` so the FE can transform the original card; the worker discriminates on `status` to decide whether to resume.)

### 2.4 Inverse symmetry with `approval_resolved`

Today `approval_resolved` carries `status: "approved" | "rejected"`. We extend the union to `"approved" | "rejected" | "forwarded"`. Existing FE branches that key on `approved`/`rejected` are untouched; new code reads only the `forwarded` case.

### 2.5 What the LangGraph interrupt/resume sees

Nothing changes. The harness still receives one `interrupt(...)` call (from `HumanInTheLoopMiddleware`) per side‑effecting tool call, and exactly one `Command(resume=...)` after the leaf approver decides. The forwarding chain is invisible to the graph — it lives entirely in our table + worker.

---

## 3 · Architecture

### 3.1 The single insight

> **Forwarding is a finite‑state addition to `runtime_approval_requests`. The graph stays paused; the worker only resumes when a leaf decision arrives.**

We already use [LangChain's `HumanInTheLoopMiddleware`][hitl-docs] (configured via Deep Agents' `interrupt_on=`, see [`agent_runtime/execution/deep_agent_builder.py:146-173`](../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py#L146-L173)). The middleware is the prebuilt approval primitive — its decision protocol is `Command(resume={"decisions": [{"type": "approve" | "reject" | "edit"}]})`, and our [`RuntimeApprovalHandler._resume_payload`](../../services/ai-backend/src/runtime_worker/handlers/approval.py#L255-L286) already speaks it. We do **not** subclass it, monkey‑patch it, or add a parallel mechanism. Forwarding never touches the middleware.

```
                ┌────────────────────────────────────────────────────────┐
                │  Existing single‑actor flow (unchanged)                │
                │                                                        │
                │  graph.astream(...) → tool node → HumanInTheLoopMW     │
                │     │                                                  │
                │     ▼                                                  │
                │  interrupt(...)  ──▶  api.create approval row          │
                │                       run.status = WAITING_FOR_APPROVAL│
                │                                                        │
                │  user decides (approve/reject)                         │
                │     │                                                  │
                │     ▼                                                  │
                │  worker resumes graph with Command(resume={…})         │
                └────────────────────────────────────────────────────────┘
                                    │
                                    │ NEW: a third decision type
                                    │ "forwarded" forks here ↓
                                    ▼
                ┌────────────────────────────────────────────────────────┐
                │  Forwarded flow (new bookkeeping; graph still paused)  │
                │                                                        │
                │  api.decide(approval, decision="forwarded",            │
                │             forward_to={user_id})                      │
                │     │                                                  │
                │     ├─ resolve original row: status="forwarded",       │
                │     │  decided_by_user_id=Sarah                        │
                │     ├─ insert child row: chain_parent_approval_id,     │
                │     │  requested_by_user_id=Marcus,                    │
                │     │  metadata.tool_invocation_id (copied),           │
                │     │  metadata.native_interrupt_id (copied)           │
                │     ├─ append APPROVAL_RESOLVED (status=forwarded)     │
                │     ├─ append APPROVAL_FORWARDED                       │
                │     ├─ append APPROVAL_REQUESTED (for child)           │
                │     ├─ audit: 'approval.forward' (Sarah)               │
                │     └─ notify(Marcus)  ← existing notification port    │
                │                                                        │
                │  worker does NOT enqueue resume; agent_runs.status     │
                │  stays WAITING_FOR_APPROVAL.                           │
                │                                                        │
                │  Marcus decides (approve/reject) → flows through       │
                │  existing single‑actor flow ABOVE, on the child row.   │
                │  Worker resumes graph ONCE on the leaf decision.       │
                └────────────────────────────────────────────────────────┘
```

The forwarding logic is implemented entirely in the **API service** (it's a transactional write of three rows + three events + one audit + one notification — pure CRUD + projection). The **worker** only has to learn one thing: _don't resume on `decision === "forwarded"`_. The **graph** learns nothing.

### 3.2 Where the change lands, file by file

#### 3.2.1 Persistence — one migration

```sql
-- services/ai-backend/migrations/0014_approval_forwarding.sql
ALTER TABLE runtime_approval_requests
  ADD COLUMN chain_parent_approval_id TEXT
    REFERENCES runtime_approval_requests(approval_id) ON DELETE CASCADE,
  ADD COLUMN forwarded_to_user_id TEXT,        -- null until decision="forwarded" is recorded
  ADD COLUMN forwarded_at TIMESTAMPTZ,
  ADD COLUMN forwarded_decided_at TIMESTAMPTZ; -- set when child row resolves; convenience for queries

-- A single‑direction chain — a row can have at most one parent.
-- (PG enforces this via the column being scalar; we add a CHECK to forbid self‑parent.)
ALTER TABLE runtime_approval_requests
  ADD CONSTRAINT runtime_approval_requests_no_self_parent
    CHECK (chain_parent_approval_id IS NULL OR chain_parent_approval_id <> approval_id);

-- 'forwarded' is a terminal status for the parent row (never resumed by worker).
-- We extend the existing CHECK on status; the enum stays text‑level for back‑compat.
ALTER TABLE runtime_approval_requests
  DROP CONSTRAINT IF EXISTS runtime_approval_requests_status_check,
  ADD CONSTRAINT runtime_approval_requests_status_check
    CHECK (status IN ('pending','approved','rejected','expired','forwarded'));

-- Read path for "show me the chain for this run/tool_invocation":
CREATE INDEX runtime_approval_requests_chain_idx
  ON runtime_approval_requests (run_id, chain_parent_approval_id);
```

No new table. No PII columns added (recipient is an opaque user_id; display name resolves at read time). Append‑only audit triggers on `runtime_audit_log` already cover the new audit actions; nothing to extend there.

#### 3.2.2 Persistence record — extend `PersistenceApprovalRequestRecord`

Append four optional fields to [`agent_runtime/persistence/records/approvals.py`](../../services/ai-backend/src/agent_runtime/persistence/records/approvals.py):

```python
chain_parent_approval_id: str | None = None
forwarded_to_user_id: str | None = None
forwarded_at: datetime | None = None
forwarded_decided_at: datetime | None = None
```

Plus extend `PersistenceApprovalStatus` with `FORWARDED = "forwarded"`. Codec hooks unchanged (these are scalar non‑sensitive columns; no `FieldCodec` needed).

#### 3.2.3 API service — `decide_approval` gets one new branch

In `agent_runtime/api/service.py` (the existing approval‑decision service method), add an explicit `_decide_forwarded(...)` private path. The public `decide_approval(...)` shape is unchanged; routing happens on `request.decision`.

```python
# Pseudocode: shape only — production lives in a class with Keys/Values constants per CLAUDE.md.
async def decide_approval(self, *, approval_id, request: ApprovalDecisionRequest) -> ApprovalDecisionResponse:
    parent = await self._load_pending(approval_id, expected_org=request.org_id)
    if request.decision is ApprovalDecision.FORWARDED:
        return await self._decide_forwarded(parent=parent, request=request)
    return await self._decide_terminal(parent=parent, request=request)  # existing path

async def _decide_forwarded(self, *, parent, request) -> ApprovalDecisionResponse:
    self._guard_forwardable(parent)            # checks approval_kind ∈ {tool_action, mcp_auth}
    self._guard_target(request.forward_to, org=parent.org_id)  # workspace_user lookup
    async with self._txn() as txn:
        await txn.update_approval(parent.approval_id,
            status=PersistenceApprovalStatus.FORWARDED,
            decided_by_user_id=request.decided_by_user_id,
            forwarded_to_user_id=request.forward_to.user_id,
            forwarded_at=now_utc(),
            decided_at=now_utc(),
            decision_reason=request.reason,
        )
        child = await txn.insert_approval(self._derive_child_record(parent, request))
        # Three events appended in the SAME txn as the row mutations:
        await self._events.append_approval_resolved(parent, status=ApprovalStatus.FORWARDED)
        await self._events.append_approval_forwarded(parent=parent, child=child, request=request)
        await self._events.append_approval_requested(child)
    await self._audit.emit_approval_forwarded(parent=parent, child=child, by=request.decided_by_user_id)
    await self._notifications.notify_approval_assigned(child)
    return ApprovalDecisionResponse(approval_id=parent.approval_id,
                                    run_id=parent.run_id,
                                    status=ApprovalStatus.FORWARDED,
                                    decided_at=parent.decided_at)
```

`_derive_child_record` is the only piece worth eyeballing: it copies the parent's `request_payload`, `tool_invocation_id`, `risk_class`, `expires_at`, and the entire `metadata` blob (notably `approval_kind`, `native_interrupt_id`). Then it overrides `requested_by_user_id` to the recipient and sets `chain_parent_approval_id`. The harness sees the child as a brand‑new approval addressed to a different user — exactly the model that makes "leaf decision resumes the graph" trivially correct.

#### 3.2.4 Worker — `RuntimeApprovalHandler` does not resume on forwarded

[`runtime_worker/handlers/approval.py`](../../services/ai-backend/src/runtime_worker/handlers/approval.py) already routes resolution commands. We add a single guard at the top of `handle()`:

```python
if command.decision is ApprovalDecision.FORWARDED:
    # API has already updated the parent row, inserted the child, emitted events,
    # audited, and dispatched notifications. Nothing for the worker to do — the run
    # stays WAITING_FOR_APPROVAL.
    return
```

Approve and reject paths are untouched. The handler's existing `_is_action_interrupt(result)` re‑pause logic still covers the case where the leaf approval is itself part of a deeper chain that the agent issues a fresh `interrupt()` after resuming — this is just LangGraph's normal behavior.

The `RuntimeApprovalResolvedCommand` schema already carries `decision: ApprovalDecision`; we extend the enum to include `FORWARDED`. The command is queued by the API in the same `outbox` transaction as the row writes (no behavior change to the durable command pipeline).

#### 3.2.5 HTTP route — one optional field on the existing endpoint

`POST /v1/agent/approvals/{approval_id}/decide` (existing). The body gains `forward_to`. No new endpoint, no breaking change. Facade proxy follows the same pattern (see `services/backend-facade/src/backend_facade/routes/approvals.py`).

#### 3.2.6 Notifications — use the port we have

`agent_runtime/api/notifications.py` (existing port; today no‑op in dev) gains `notify_approval_assigned(approval)`. The implementation reads the recipient's notification preferences from `services/backend` (`/internal/v1/users/{id}/notifications`) and fans out:

- Slack DM via the user's connected Slack identity (when present).
- Email via the workspace email adapter.
- A websocket / SSE push for the active web session (we re‑use the existing `runtime_events` SSE — Marcus's frontend, when subscribed to his Approvals inbox, gets a synthetic `approval_assigned_to_me` event projected from his org's recent rows; see §3.3).

If the matrix UI doesn't exist yet (W4.1), the port falls back to `MEMBER_DEFAULTS` (email + in‑app). The architecture is correct; the matrix is just the override surface.

#### 3.2.7 Frontend — three small additions

Per `apps/frontend/CLAUDE.md` we never derive activity types from event‑name prefixes — we use the projection. So the FE work is:

1. **`ApprovalTool.tsx`** ([apps/frontend/src/features/chat/components/tools/ApprovalTool.tsx](../../apps/frontend/src/features/chat/components/tools/ApprovalTool.tsx)) — add a workspace‑user picker behind a new "Approve & forward to…" secondary button. Picker reads from `useWorkspaceMembers()` (already used by `@‑mention` autocomplete in W3.1). Submitting calls `decideApproval(id, "forwarded", { forward_to: { kind: "workspace_user", user_id } })`.
2. **`chatModel/eventReducer.ts`** — one new branch on `approval_forwarded`: it transforms the existing in‑thread approval card item into a `ApprovalForwardedItem` keyed by `chain_parent_approval_id`, carrying the recipient's display name + timestamp. On `approval_resolved` (status=approved/rejected) for any child approval, look up its parent and patch the same in‑thread item to the resolved record. The two events keep the parent's id stable as the inline anchor.
3. **`ApprovalsTab.tsx`** (workspace pane right rail, W3.2) — gains a "Pending on you" group at the top, populated from a small REST query: `GET /v1/agent/approvals?assigned_to_me=true&status=pending`. Marcus's tab shows Sarah's forwarded card here. Click → opens the source conversation and scrolls to the in‑thread card.

That's the entire FE delta. No second screen, no separate inbox app — the recipient sees the work _in the same chat surface, in the right rail_, exactly per design.

### 3.3 The recipient's chat session

Two relevant questions: _where does Marcus's frontend learn about the assignment_ and _what does it render?_

- **Learning.** Marcus's frontend already polls `listConversations()` on mount and after every run completion (see `ChatScreen.tsx:131-134`). We don't add polling — we add a thin `useAssignedApprovals()` hook that hits `GET /v1/agent/approvals?assigned_to_me=true&status=pending` on Settings → Notifications cadence (default 60s when tab visible) and on push. The push channel is the user's `runtime_events` SSE stream that already exists for any active run; we add a lightweight per‑user channel `GET /v1/agent/me/inbox/stream` _only if_ Marcus has no active run. Implementation detail: this can defer to W4.1 where the notification matrix lives.
- **Rendering.** The Approvals tab grouping is the v1 surface. Clicking the row navigates Marcus into Sarah's conversation in **read‑only** mode (he wasn't a participant) — UI cue is identical to a share recipient view (W6) but without the share resolver. The inline `ApprovalTool` card is the _same_ component he's used to; the only difference is a "Forwarded by Sarah · 10:41" line above the action summary.

### 3.4 Streaming + replay invariants

Because forwarding is bookkeeping, the streaming guarantees are inherited:

- Every event we add (`APPROVAL_RESOLVED status=forwarded`, `APPROVAL_FORWARDED`, `APPROVAL_REQUESTED for child`) is appended through the existing `RuntimeEventProducer.append_api_event` path → gets a monotonic `sequence_no` → persists in `runtime_events` → survives RLS, codec, retention.
- SSE reconnect (`?after_sequence=N`) replays the trio in the original order; the FE reducer is idempotent (key on `approval_id` + `chain_parent_approval_id`), so applying any prefix produces the right inline card state.
- `replayRunEvents` for archive reads produces the exact same card state — no client‑side server fetch needed for chain reconstruction.
- For Marcus's separate session, the events for the parent run are visible only via his Approvals inbox query (he isn't a member of Sarah's conversation). His conversation history is unaffected.

### 3.5 Why this is the smallest possible design

Because the forwarding semantic is **strictly weaker than what already runs through the system**:

- Today, when an approval resolves, the worker picks it up and resumes the graph. Forwarding is "an approval that resolves into another approval, without resuming." The graph protocol is unchanged. The middleware is unchanged. The interrupt is unchanged. The resume payload is unchanged.
- The forwarding _act_ is one DB UPDATE + one DB INSERT + three event appends + one audit emit + one notification dispatch — all in one transaction. No background job. No saga. No state machine with five states. The only state we add is `runtime_approval_requests.status='forwarded'`.

If a reviewer wants to delete the feature, the path is: drop the migration, remove the four added fields from the record, drop the `_decide_forwarded` branch, drop the `ApprovalDecision.FORWARDED` enum, drop the `approval_forwarded` event projector entry, drop the FE branch. ~15 minutes.

---

## 4 · DRY / re‑use audit

| Need                                            | Re‑used                                                                                                                                                                                                                      | Why this beats a fork                                                                                                                                           |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Pause/resume the agent harness                  | LangChain [`HumanInTheLoopMiddleware`][hitl-docs] via Deep Agents `interrupt_on=`, already wired in [`deep_agent_builder.py:146-173`](../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py#L146-L173) | The middleware _is_ the prebuilt approval primitive. Forwarding doesn't change how the graph pauses or resumes — only when.                                     |
| Decision protocol for resume                    | Existing `Command(resume={"decisions":[{"type":...}]})` shape ([LangChain docs][hitl-docs])                                                                                                                                  | Worker's `_resume_payload` already produces this. Adding a third API decision type doesn't change the protocol — `forwarded` never reaches the graph.           |
| Approval row                                    | Extend `runtime_approval_requests` with 4 columns                                                                                                                                                                            | Schema migration < new table. Append‑only audit triggers, RLS, codec patterns all apply for free.                                                               |
| Audit chain                                     | Existing `runtime_audit_log` append‑only chain (migration 0003)                                                                                                                                                              | Forwarding emits two well‑known actions (`approval.forward`, `approval.resolve`); SIEM exporter (migration 0016) picks them up automatically.                   |
| Notification fan‑out                            | Existing `notification` port + Settings → Notifications matrix (W4.1)                                                                                                                                                        | Don't build a second notification channel for approvals; reuse the matrix. The port is there even when the UI isn't yet.                                        |
| Event ordering, sequence_no, replay, SSE resume | `RuntimeEventProducer.append_api_event` (`agent_runtime/api/events.py:81-143`)                                                                                                                                               | All wire guarantees come for free.                                                                                                                              |
| Recipient inbox UI surface                      | Workspace pane Approvals tab (already in W3.2)                                                                                                                                                                               | The design already reserves the surface for "queue of pending decisions across this chat" — we extend "across this chat" to "+ assigned to me." Same component. |
| Recipient notification                          | The existing per‑user `runtime_events` SSE channel (active runs) + a tiny `me/inbox/stream` (idle users)                                                                                                                     | One protocol. No bespoke "approval push" service.                                                                                                               |
| Workspace‑user lookup                           | `services/backend` users table + existing `/internal/v1/users/{id}`                                                                                                                                                          | No new directory; SCIM provisioning already populates this.                                                                                                     |
| Idempotency of decide                           | Existing `with_optimistic_retry` (`agent_runtime/persistence/__init__.py`) wraps row updates                                                                                                                                 | Re‑post of a `forwarded` decision is harmless: the row is already `status=forwarded`, the second call returns the prior child row.                              |
| FE `ApprovalTool` card                          | Existing component + `useWorkspaceMembers()` (used by @‑mention picker)                                                                                                                                                      | One picker, two callers. Don't duplicate the @‑mention dropdown.                                                                                                |
| FE reducer event handling                       | Existing event reducer + projection fields (`activity_kind`/`display_title`)                                                                                                                                                 | One new `case`, mirroring every other approval event handler.                                                                                                   |
| Approve / reject UI on recipient                | Same `ApprovalTool` component                                                                                                                                                                                                | The card is identical for the recipient — only the "Forwarded by Sarah" caption is added.                                                                       |

**Things we explicitly do not introduce:**

- A "two‑stage approval" middleware (would duplicate `HumanInTheLoopMiddleware`).
- A separate "approvals service."
- A new graph node or graph edge.
- A separate "inbox" app surface.
- A new background worker / saga.
- Server‑side approval policies (deferred — and would slot into the existing decide endpoint when added).
- A second resume protocol or a parallel command queue.

---

## 5 · Code surface inventory

Approximate sizes are upper bounds — pessimistic guesses for code review.

### 5.1 `packages/api-types`

| File           | Change                                                                                                                                                         | Est. LoC |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| `src/index.ts` | Extend `ApprovalDecision`, add `ApprovalForwardTarget`, extend `ApprovalDecisionRequest`, add `RuntimeApprovalForwardedEvent`, extend `ApprovalStatus` literal | +35      |

### 5.2 `services/ai-backend`

| File                                                                    | Change                                                                                                | Est. LoC |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | -------- |
| `migrations/0014_approval_forwarding.sql` (+ rollback)                  | 4 columns, CHECK relax, index                                                                         | +35      |
| `src/agent_runtime/persistence/records/approvals.py`                    | 4 fields, 1 enum value                                                                                | +12      |
| `src/agent_runtime/persistence/records/common.py`                       | `PersistenceApprovalStatus.FORWARDED` enum                                                            | +2       |
| `src/agent_runtime/persistence/postgres/*.py`                           | INSERT/UPDATE columns                                                                                 | +25      |
| `src/agent_runtime/api/service.py`                                      | `_decide_forwarded`, `_guard_forwardable`, `_guard_target`, `_derive_child_record`                    | +120     |
| `src/agent_runtime/api/events.py`                                       | `append_approval_forwarded(...)`                                                                      | +30      |
| `src/agent_runtime/api/notifications.py`                                | `notify_approval_assigned(approval)` port + default no‑op                                             | +25      |
| `src/agent_runtime/api/presentation_templates.py`                       | template for `approval_forwarded` event                                                               | +12      |
| `src/agent_runtime/api/audit.py`                                        | `emit_approval_forwarded(...)`                                                                        | +20      |
| `src/runtime_api/schemas/approvals.py`                                  | `forward_to`, validators, `ApprovalForwardTarget`                                                     | +35      |
| `src/runtime_api/schemas/common.py`                                     | `ApprovalDecision.FORWARDED`, `ApprovalStatus.FORWARDED`                                              | +4       |
| `src/runtime_api/schemas/events.py`                                     | `RuntimeApiEventType.APPROVAL_FORWARDED`, projector branch                                            | +18      |
| `src/runtime_api/http/routes.py`                                        | Optional: `GET /v1/agent/approvals?assigned_to_me=true` (recipient inbox; can also defer)             | +35      |
| `src/runtime_worker/handlers/approval.py`                               | 3‑line guard at top of `handle()` for `decision == FORWARDED`; enum in `_resume_payload` is unchanged | +6       |
| `tests/unit/agent_runtime/api/test_decide_forwarded.py` (new)           | 20+ unit cases; see §8.1                                                                              | +400     |
| `tests/unit/runtime_worker/test_approval_handler_forward_skip.py` (new) | worker resume‑skip semantics                                                                          | +90      |
| `tests/unit/runtime_api/test_approval_routes_forward.py`                | route validation, error shapes                                                                        | +120     |
| `docs/use-cases/15-two-stage-approval-forwarding.md` (new)              | use‑case doc following the existing template                                                          | +160     |

### 5.3 `services/backend`

| File                                                     | Change                                                                                                  | Est. LoC |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | -------- |
| `src/backend_app/identity/users.py`                      | (re‑use existing) tiny helper to validate (org_id, user_id) is an active member, if not already exposed | 0–20     |
| `src/backend_app/notifications/dispatcher.py` (existing) | Wire `approval_assigned` event type + Slack DM template + email template                                | +60      |

### 5.4 `apps/frontend`

| File                                                                            | Change                                                                          | Est. LoC |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | -------- |
| `src/api/agentApi.ts`                                                           | `decideApproval` adds `forward_to` parameter; `listAssignedApprovals` (new)     | +35      |
| `src/features/chat/components/tools/ApprovalTool.tsx`                           | "Approve & forward to…" secondary action + picker integration                   | +90      |
| `src/features/workspace/useWorkspaceMembers.ts` (existing)                      | (no change; reuse)                                                              | 0        |
| `src/features/chat/chatModel/eventReducer.ts`                                   | one branch on `approval_forwarded`; child→parent linking on `approval_resolved` | +45      |
| `src/features/chat/chatModel/citationsRegistry.ts`/sibling                      | no change                                                                       | 0        |
| `src/features/workspace/ApprovalsTab.tsx` (W3.2)                                | "Pending on you" group; query bind to `useAssignedApprovals`                    | +60      |
| `src/features/workspace/useAssignedApprovals.ts` (new)                          | poll + push hook                                                                | +50      |
| `__tests__/eventReducer.forwarding.test.ts`, `ApprovalTool.forwarding.test.tsx` | edge cases incl. replay ordering                                                | +200     |

**Totals:** ai‑backend ~1.1k LoC (incl. tests + use‑case); frontend ~480 LoC (incl. tests); contracts ~35 LoC. The system core (migration + record + service branch + event + worker guard + reducer branch + UI button) is **~330 LoC** — the rest is tests and a thoroughgoing use‑case doc.

---

## 6 · End‑to‑end sequence

```
Sarah's browser     facade        ai-backend (api)       worker/queue       runtime_events SSE     Marcus's browser
     │                │                  │                    │                      │                     │
     │ POST /…/decide  decision=approved (CURRENT BEHAVIOR — for contrast)          │                     │
     │ ──────────────▶│ ────────────────▶│ resolve row, append APPROVAL_RESOLVED │ → resume graph         │
     │                │                  │                    │                      │                     │
     ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ NEW PATH ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
     │                │                  │                    │                      │                     │
     │ POST /…/decide  decision=forwarded, forward_to={user_id: marcus}             │                     │
     │ ──────────────▶│ ────────────────▶│  _guard_forwardable + _guard_target      │                     │
     │                │                  │                                          │                     │
     │                │                  │  txn:                                    │                     │
     │                │                  │    UPDATE parent SET status=forwarded    │                     │
     │                │                  │    INSERT child (chain_parent=parent)    │                     │
     │                │                  │    APPEND approval_resolved (forwarded)  │ ──── seq+1 ─────────│
     │                │                  │    APPEND approval_forwarded             │ ──── seq+2 ─────────│
     │                │                  │    APPEND approval_requested (child)     │ ──── seq+3 ─────────│
     │                │                  │  audit: approval.forward                 │                     │
     │                │                  │  notify(marcus) via notifications port   │ ─ Slack DM / email -▶│
     │                │                  │                                          │                     │
     │                │  200 OK ◀────────│                                          │                     │
     │ ◀──────────────│                  │                                          │                     │
     │                                                                                                    │
     │  reducer: transform card → "Waiting on @marcus · forwarded by you · 10:41"                          │
     │                                                                                                    │
     │                                                          ┌── Marcus's UI: assigned approvals tab ──│
     │                                                          │   row appears → click → loads thread    │
     │                                                          │   in read‑only; ApprovalTool card       │
     │                                                          │   renders with "Forwarded by Sarah"     │
     │                                                          └─────────────────────────────────────────│
     │                                                                                                    │
     │                                  ◀── POST /…/decide approval_id=child decision=approved ───────────│
     │                │ ────────────────▶│ resolve child row; APPROVAL_RESOLVED (approved)                │
     │                │                  │ enqueue RuntimeApprovalResolvedCommand                         │
     │                │                  │                    │                                           │
     │                │                  │                    │ worker: _resume_payload(child) →          │
     │                │                  │                    │ Command(resume={"decisions":[{type:appr"}]})
     │                │                  │                    │ ─ astream_runtime_resume ──────────▶ graph│
     │                │                  │                    │ tool runs: post_to_slack(...)             │
     │                │                  │                    │ final_response                            │
     │                │                  │                    │                      │ ── seq+N → both Sarah and Marcus
     │                │                                                                                    │
     │  reducer: card transforms to "Approved by Marcus · 10:45 · Posted to #announcements"               │
```

The diagram makes the symmetry visible: the "current path" is unchanged; the "new path" forks at the API edge, lands all‑bookkeeping work in one transaction, and reconverges at Marcus's decision through the existing approve/reject path. The graph experiences exactly one resume.

---

## 7 · Edge cases & their resolutions

| Case                                                                            | Resolution                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Self‑forward (Sarah → Sarah)                                                    | API rejects with 422; `_guard_target` checks `forward_to.user_id != decided_by_user_id` AND active membership in same org.                                                                                                                                                                                                                                                                     |
| Forward target is not in the same org                                           | API rejects with 404 (we don't leak existence); `_guard_target` runs `users` lookup scoped by `org_id`.                                                                                                                                                                                                                                                                                        |
| Forward target inactive / disabled                                              | API rejects with 422; same guard checks `users.status='active'` AND `organization_members.removed_at IS NULL`.                                                                                                                                                                                                                                                                                 |
| Concurrent decide on the parent row                                             | `with_optimistic_retry` retries; the loser sees `status != pending` and returns 409.                                                                                                                                                                                                                                                                                                           |
| Marcus loses access mid‑chain (org member removed)                              | Child row remains pending; auto‑expiry runs at `expires_at` and the run terminates the action with a system rejection. (Reuses the existing expiry sweep — added in W1‑adjacent migration; not new code.)                                                                                                                                                                                      |
| Marcus re‑forwards to Devi (n‑level chain)                                      | Allowed by schema; v1 UI exposes only one forward step. Chain depth is capped at `RUNTIME_APPROVAL_MAX_CHAIN_DEPTH=3` (config) — over‑cap returns 422 to the deciding user.                                                                                                                                                                                                                    |
| Cycle (chain points back to a parent)                                           | `chain_parent_approval_id` is scalar so a cycle requires misuse; the depth cap prevents infinite chains. A unit test asserts no row in the produced chain points back to an ancestor.                                                                                                                                                                                                          |
| Parent expires while child pending                                              | The child row continues to govern resolution (not the parent's expiry). UI shows "Forwarded — recipient still has X minutes."                                                                                                                                                                                                                                                                  |
| User cancels run while chain pending                                            | Existing cancel flow marks the run cancelled; both parent and pending child rows transition to `expired` via the same sweep that handles other terminal cancellations.                                                                                                                                                                                                                         |
| Replay arrives in non‑monotonic order due to retry                              | Reducer keys on `approval_id` and `chain_parent_approval_id` so any prefix of events lands at the right card; the SSE side guarantees monotone delivery anyway.                                                                                                                                                                                                                                |
| Forward of an `mcp_auth` approval                                               | Allowed (`approval_kind ∈ {tool_action, mcp_auth}`). The recipient is asked to authenticate the connector for _their_ account — the chain only authorizes who completes the OAuth, never moves credentials between users. The MCP OAuth flow runs on the recipient's session and stores tokens against their `user_id` (existing behavior of `services/backend/src/backend_app/mcp_oauth.py`). |
| Forward of an `ask_a_question` interrupt                                        | Rejected with 422; the question is for the requester, not a sensitive action.                                                                                                                                                                                                                                                                                                                  |
| FE sees `approval_resolved status=forwarded` without prior `approval_forwarded` | Possible only if the FE is at a partial replay; reducer is idempotent — the next event applies cleanly when it arrives. Never seen in production because the API appends both in the same transaction (same sequence_no batch).                                                                                                                                                                |
| Notification adapter fails                                                      | Logged + retried via existing notification dispatcher; the chain does NOT roll back. The recipient still has the in‑app Approvals tab as the primary surface; notifications are best‑effort.                                                                                                                                                                                                   |

---

## 8 · Test plan

### 8.1 Unit (ai‑backend, ~25 cases)

Service:

- `decide_forwarded_inserts_child_with_chain_parent`
- `decide_forwarded_resolves_parent_with_status_forwarded`
- `decide_forwarded_appends_three_events_in_same_txn`
- `decide_forwarded_emits_audit_with_chain_parent_link`
- `decide_forwarded_emits_notification_to_recipient`
- `decide_forwarded_rejects_self_forward`
- `decide_forwarded_rejects_cross_org_target`
- `decide_forwarded_rejects_inactive_target`
- `decide_forwarded_rejects_ask_a_question_kind`
- `decide_forwarded_rejects_when_parent_already_resolved`
- `decide_forwarded_rejects_when_chain_depth_exceeds_cap`
- `decide_forwarded_idempotent_on_repost`
- `child_inherits_native_interrupt_id_and_tool_invocation_id`
- `child_inherits_metadata_and_request_payload_byte_for_byte`
- `child_expires_at_capped_to_parent_expires_at`

Worker:

- `worker_skips_resume_on_decision_forwarded`
- `worker_resumes_on_leaf_approve_with_unchanged_payload_shape`
- `worker_resumes_on_leaf_reject_with_unchanged_payload_shape`

Replay / projector:

- `projector_emits_approval_forwarded_with_correct_activity_kind`
- `replay_reproduces_card_state_for_forwarded_then_pending`
- `replay_reproduces_card_state_for_forwarded_then_approved`
- `replay_reproduces_card_state_for_forwarded_then_rejected`

Persistence / migration:

- `migration_round_trips`, `migration_rollback_clears_columns`
- `runtime_audit_log_chain_signature_holds_after_forward_actions`

### 8.2 Frontend (~14 cases)

- `reducer_transforms_card_on_approval_forwarded`
- `reducer_links_child_resolved_back_to_parent_card`
- `reducer_idempotent_on_replayed_forward_event`
- `approval_tool_renders_forward_picker_only_for_tool_action_kind`
- `approval_tool_disables_self_forward_in_picker`
- `approval_tool_picker_filters_to_active_workspace_members`
- `approval_tool_renders_forwarded_pill_with_recipient_display_name`
- `approvals_tab_renders_pending_on_you_group`
- `approvals_tab_click_navigates_to_source_thread_in_read_only`
- `assigned_approvals_hook_polls_on_visibility_change`
- `assigned_approvals_hook_drains_push_events_idempotently`
- `decideApproval_api_includes_forward_to_only_when_decision_is_forwarded`
- `replay_reconstructs_chain_in_read_only_view`
- `mcp_auth_forward_takes_recipient_through_their_oauth`

### 8.3 Integration

- New use case `docs/use-cases/15-two-stage-approval-forwarding.md` covering the full Sarah→Marcus flow (mirrors the existing template; ties into the use‑case test harness).
- Extend `docs/use-cases/06-mcp-installed-not-authenticated.md` with a forward variant.
- E2E (Playwright, gated): launch‑announcement scenario → trigger Slack post approval → "Approve & forward to Marcus" → log in as Marcus in second context → approve → first context shows "Approved by Marcus · Posted to #announcements" record without page reload.

### 8.4 Compliance check

- Confirm `runtime_approval_requests` retention sweep (migration 0012) covers child rows and forwarded parent rows symmetrically.
- Confirm `runtime_audit_log` rows for `approval.forward` and `approval.resolve (status=forwarded)` flow through the SIEM exporter (migration 0016) — verify `siem_export_cursors` advances and dead‑letter is empty after forwarding flow runs.
- Confirm RLS denies cross‑org reads of forwarded rows (set `app.current_org_id` to another org; expect zero).
- Confirm the chain‑parent UPDATE is allowed by the append‑only audit guard (it's the existing `runtime_approval_requests` table, not the audit table — the guard is on `runtime_audit_log`, which still only sees INSERTs).

### 8.5 Manual / dogfood

- One day of dogfood with `RUNTIME_APPROVAL_FORWARDING_ENABLED=true` for the team org. Verify the inbox UI is visible to recipients and that notifications fire (Slack DM at minimum).

---

## 9 · Rollout

1. **Behind a runtime config flag** `RUNTIME_APPROVAL_FORWARDING_ENABLED` (default off). Flag gates: API `_decide_forwarded` branch (returns 501 when off), schema accepts `decision="forwarded"` only when on, FE picker hidden when off.
2. **Phase 1 — schema + service.** Land the migration, the record extension, and the API branch with FE flag still off. Replay paths verified; no UI change for users.
3. **Phase 2 — FE picker on for staff org.** Internal staff exercise the flow on the launch‑announcement scenario.
4. **Phase 3 — flag default‑on for all orgs**, FE picker visible everywhere.
5. **Phase 4 — remove the flag** after one release of clean telemetry (no audit dead‑letters; no run‑status drift).

Backfill: not required. Prior approvals have `chain_parent_approval_id IS NULL` and behave exactly as today.

---

## 10 · Open questions (non‑blocking)

- **Maximum chain depth.** v1 caps at 3. Open to lifting to 5 if Members + Audit (W4.2 / W7.1) reveal real demand for deeper escalation. Caps live in config, not in code.
- **Auto‑escalate on expiry.** Today expiry rejects the action. A smart default could be "auto‑forward to the requester's manager" — but that requires a manager graph in the directory, which we don't have yet. Defer.
- **Forward with edits.** Sarah might want to forward Marcus a _modified_ draft. We deliberately do not support this in v1: forwarding preserves `request_payload` byte‑for‑byte (audit relies on this). If we add an "edit on forward" later, it becomes `decision=forwarded_with_edit` — separate field, separate audit action, no schema change beyond a new `edited_request_payload` column.
- **Pendinginbox SSE channel** (`/v1/agent/me/inbox/stream`) — we may decide to merge this into a generic per‑user notifications channel in W4.1 rather than have a one‑off endpoint. This PR can ship with the polling fallback; the FE hook is `useAssignedApprovals` either way.
- **Org‑scoped vs cross‑org forwards.** Out of scope. Today every approval lives in exactly one org; a cross‑org workflow needs Sharing (W6) and the share‑recipient resolver to pre‑exist. Will revisit when both ship.

---

## 11 · References

- Atlas Design Doc (handoff bundle, [`/tmp/design-doc/0x-copilot/project/Design Doc.html`](file:///tmp/design-doc/0x-copilot/project/Design%20Doc.html)) — §"Flow — Launch (full agent)" step 5, §"Flow — Approval", §"Approvals as content, not modals" decision.
- [`docs/new-design/00-plan.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md) — wave plan, PR sizing, sequencing.
- Sibling PRDs: [`01-citations-live-registry.md`](01-citations-live-registry.md) (event/projection conventions), [`pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) (per‑conversation persistence patterns), [`pr-1.3-draft-artifact.md`](pr-1.3-draft-artifact.md) (draft‑send goes through approval, which can now forward).
- LangChain Human‑in‑the‑Loop docs — the prebuilt middleware we already use, unchanged by this PR: <https://docs.langchain.com/oss/python/langchain/human-in-the-loop>
- LangChain Middleware reference (Python): <https://reference.langchain.com/python/langchain/middleware/>
- LangGraph interrupt + `Command(resume=...)` docs: <https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/>
- Existing implementation surfaces:
  - [`services/ai-backend/src/runtime_worker/handlers/approval.py`](../../services/ai-backend/src/runtime_worker/handlers/approval.py) — current resume path; gains a 3‑line guard.
  - [`services/ai-backend/src/agent_runtime/persistence/records/approvals.py`](../../services/ai-backend/src/agent_runtime/persistence/records/approvals.py) — record gains 4 fields.
  - [`services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py`](../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py) — `interrupt_on=` wiring; **not modified** by this PR.
  - [`services/ai-backend/src/runtime_api/schemas/approvals.py`](../../services/ai-backend/src/runtime_api/schemas/approvals.py) — request schema gains `forward_to`.
- `services/ai-backend/CLAUDE.md` — module boundaries, untrusted‑input rules, append‑only audit invariants.
- `apps/frontend/CLAUDE.md` — Streamdown markdown rendering rule; activity_kind/display_title/summary/status projection rule.
- Inventory report from W0 plan (Explore agent §A–§I).

[hitl-docs]: https://docs.langchain.com/oss/python/langchain/human-in-the-loop
