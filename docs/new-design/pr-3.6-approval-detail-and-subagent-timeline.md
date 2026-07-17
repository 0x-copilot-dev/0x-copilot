# PR 3.6 — Approval card detail metadata + Subagent step timeline expansion

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 3 (chat semantics) follow‑up to PR 1.4 + PR 1.5 + PR 3.2 + PR 3.3 in [`0-OVERALL_PLAN.md`](0-OVERALL_PLAN.md)
> **Owner:** ai‑backend (1 schema field on the existing approval payload + 1 small projector update + 1 stream forwarder extension — zero new tables, zero new endpoints) · backend‑facade (zero — re‑uses existing routes) · api‑types (2 small interface extensions) · frontend (extend `ApprovalTool.tsx` rendering, extend `AgentsTab.tsx` with `<Collapsible>`, add `subagent_step` reducer branch, ≈ 1 dep add)
> **Size:** **M.** All wire is additive; no migrations; no new endpoints; no new persistence. The only net‑new code is (a) a presentation block on the approval payload that tools fill in, (b) a `SUBAGENT_STEP` event that the stream forwarder mints from existing child‑run events, (c) a frontend collapsible card. Everything else is already in the tree.
> **Depends on:**
>
> - ✅ PR 1.4 two‑stage approvals (shipped — `runtime_approval_requests`, `ApprovalDecisionRequest`, `approval_resolved` / `approval_forwarded` events)
> - ✅ PR 1.4.1 approval forwarding hardening (shipped)
> - ✅ PR 1.5 subagent discovery (shipped — `runtime_async_tasks`, `runtime_subagent_results`, `GET /v1/agent/conversations/{id}/subagents`)
> - ✅ PR 3.2 workspace pane right rail (Agents tab exists, currently click‑to‑jump only)
> - ✅ Streaming infra (SSE outbox + `sequence_no` replay + `RuntimeEventEnvelope`)
> - ✅ `runtime_events` persistence (every event already stored; archive read works for free)
>
> **Reads alongside:**
>
> - [`pr-3.3-mcp-discovery-approval-polish.md`](pr-3.3-mcp-discovery-approval-polish.md) — same approval card, different polish dimension (forwarding name lookup); this PR is orthogonal and ships independently.
> - [`pr-1.5-subagent-discovery-workspace-feeds.md`](pr-1.5-subagent-discovery-workspace-feeds.md) — established the subagent read endpoint + reducer pattern this PR extends.
> - [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) — projection fields, no event‑name‑prefix derivation, facade‑only network rule.
> - [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — strict typing, additive payloads, never break the SSE wire.

---

## 0 · TL;DR

Two small gaps between the chat surface we ship today and the Atlas design handoff. Both can ride one wire change because they share the same constraint: **no new tables, no new endpoints, no new auth — purely additive on event payloads + a frontend Collapsible**.

| Surface                | Today                                                                                                                                               | After this PR                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Approval card**      | `ApprovalTool.tsx` renders title + status + free‑text description + two buttons (Approve / Reject) + (PR 1.4) Approve‑and‑forward picker.           | Renders the full Atlas spec: a top‑right accent pill (e.g. `SLACK · WRITE`), 3–5 labeled rows in a nested surface (`CHANNEL`, `VISIBILITY`, `ACTION`, `REVERSIBLE`), primary button copy authored by the tool (`Approve & continue`), secondary copy (`Skip this step`), and a footer note with the shield glyph (`You're always asked before Atlas writes outside this chat.`). Wire stays the same shape; only the payload's optional `presentation` block grows. |
| **Subagent card**      | `AgentsTab.tsx` shows status + name + objective + result + duration + "↗ jump to thread." No expansion in‑place.                                    | Each card is a Radix `<Collapsible>`. Expanded body shows the full step timeline of that subagent: thinking, tool calls, sub‑search hits, finals — streaming **live** while the subagent runs, replayable post‑run on conversation reopen.                                                                                                                                                                                                                          |
| **Subagent step wire** | Parent run sees `subagent_started / progress / completed`. Per‑step events stay inside the child run; parent never sees them.                       | Parent worker forwards a single new typed event `subagent_step` per step (started+completed coalesced) onto its own SSE stream. Reuses the same `sequence_no` outbox; survives reconnect.                                                                                                                                                                                                                                                                           |
| **Persistence**        | None for "what is the labeled metadata for this approval"; none for "what steps did this subagent take" beyond the existing `runtime_events` table. | None added. `runtime_events` already stores every event for every run — archive read for the timeline is a filter on `run_id = child_run_id`. The approval `presentation` block lives inside the existing `runtime_approval_requests.payload` JSON column.                                                                                                                                                                                                          |

**The three principles**

1. **Presentation is data.** The labeled rows on the approval card are not hard‑coded per tool in the frontend; the tool author emits them in a `presentation` block and the frontend renders them generically. New write‑tools (Drive, Jira, GitHub) ship a `presentation` block; nothing in the FE has to learn about them. DRY.
2. **No new persistence.** Every byte we need is already on the wire and in `runtime_events`. The wire grows by additive optional fields; the table grows by zero.
3. **Streaming stays the streaming we already have.** No new SSE channel, no new replay mechanism, no new auth, no new endpoint. One new event type rides the existing outbox; one new reducer branch consumes it. Reconnect with `after_sequence=N` works without changes.

LoC estimate: ai‑backend ≈ 180 (1 Pydantic field + 1 projector update + 1 forwarder extension + tests) · backend‑facade ≈ 0 (existing routes proxy the new payload shape transparently) · api‑types ≈ 30 (2 interfaces extended) · frontend ≈ 220 (extend `ApprovalTool` render, extend `AgentsTab` with `<Collapsible>`, 1 reducer branch, ≈ 80 LOC of CSS) · design‑system ≈ 0 (Radix Collapsible installed once at `apps/frontend`).

---

## 1 · PRD

### 1.1 Problem

The screenshot the user shared (FY26 Q1 Aurora launch demo) shows two surfaces that we render thinner than the design specifies.

**(a) Approval card.** The Atlas design ships a structured detail block — a small nested surface with 3–5 labeled rows that answer the questions a non‑engineer asks before approving _any_ side effect:

- _What touches what?_ (CHANNEL = `#launch-aurora · 14 members`)
- _Who can see it?_ (VISIBILITY = `Channel members + linked threads`)
- _What exactly happens?_ (ACTION = `Post message + pin until embargo`)
- _Can I undo this?_ (REVERSIBLE = `Yes — Atlas will keep an undo for 60s`)

It also ships a top‑right accent pill (e.g. `SLACK · WRITE`) for orientation, an explicit primary‑button copy that's tool‑authored (`Approve & continue`, not the generic `Approve`), a friendlier secondary (`Skip this step` rather than `Reject`), and a footer note in muted text with a shield glyph.

The current `ApprovalTool.tsx` (lines 28–263) renders exactly four things: title, status badge, a free‑text `message` from the tool, and two generic buttons. Everything the spec calls out — the labeled rows, the accent pill, the primary copy, the footer — is missing. Today an MCP `slack.write` approval looks indistinguishable from a `drive.write` or `jira.create` approval. That's the gap a non‑engineer feels first: "what am I actually approving?"

**(b) Subagent card.** When the parent run dispatches subagents in parallel (PR 1.5 wire shipped), the workspace pane Agents tab renders one card per subagent with status + objective + result + duration. Today the only interaction is a "↗ jump to thread" button (`AgentsTab.tsx:115–124`). The design specifies that each card is **expandable in‑place** — clicking it reveals the timeline of steps that subagent took (thinking, search calls, doc reads, the final return). This matters for two reasons:

1. The parent thread doesn't render subagent internals — the whole point of subagents is to keep the main thread clean. So the only place a user can audit the subagent's reasoning is in the workspace pane.
2. Streaming subagent steps live (while parallel work is in flight) is the moment of trust the design is selling — you watch Press Scout think and decide concurrently with Voice Reviewer, both running while the main thread is still drafting.

Today the subagent runs its steps inside its own child run; the events stay there; the parent SSE stream sees only `subagent_started` / `subagent_progress` / `subagent_completed`. There is no wire that surfaces step‑by‑step events to the parent.

### 1.2 Goals

1. **Approval cards render the full design.** The labeled‑row block, the accent pill, the tool‑authored primary button copy, the friendly secondary, and the footer note all render — and they do so generically: any tool that emits an approval can ship its own labels by populating one optional `presentation` block on the existing `approval_requested` event.
2. **Approval wire stays additive.** No breaking changes. Old tools that don't emit a `presentation` block keep rendering with current behavior (just the title + message + buttons). New tools opt in by emitting a `presentation`. This lets us roll the presentation block out tool‑by‑tool without a flag day.
3. **Subagent cards expand in‑place to a step timeline.** Clicking the card opens an animated disclosure showing every step the subagent has taken: model thinking, harness tool calls (no approval — `ls`, `grep`, `search_corpus`), MCP tool invocations, and the final response. Streams live while running; persists on conversation re‑open.
4. **Streaming is unchanged.** The new `subagent_step` event rides the parent run's SSE stream the same way every other event does — sequence‑numbered, replayable from `?after_sequence=N`, survives reconnect. No new channel, no new endpoint, no new auth.
5. **No new persistence.** The labeled metadata lives inside the existing `runtime_approval_requests.payload` JSON column. The subagent step timeline is reconstituted at archive‑read time from `runtime_events` filtered by the subagent's child `run_id` (already known via `runtime_async_tasks`).
6. **Re‑use a prebuilt primitive for the disclosure.** Add `@radix-ui/react-collapsible` (same family as the already‑shipped `@radix-ui/react-popover`) for the subagent expansion. Don't roll our own animation/keyboard/ARIA disclosure.
7. **Agent harness is unaffected.** Subagent invocation, tool gating, the LangGraph executor, and the deep‑agent builder don't learn about this. The forwarder watches the existing event log; presentation is decided at tool‑emit time, not in the engine.

### 1.3 Non‑goals

- **Per‑row approval edits.** The user can _approve_ or _skip_; we don't ship "edit the channel before approving." Editing the underlying tool arguments is a deeper UX (and a bigger security surface — argument tampering). Out of scope; tracked as a future polish.
- **Streaming the subagent's model deltas to the parent stream.** The step timeline shows step‑level events (started, completed, summary) — not token‑by‑token model output of the subagent. Subagent token deltas stay in the child run; the parent gets one summary per step.
- **A new "Skip" decision in `runtime_approval_requests.status`.** "Skip this step" maps to the existing `rejected` decision on the wire; the difference is purely the tool‑authored secondary‑button copy. The model receives `rejected` and decides what to do (the Slack write tool already understands "user rejected, continue without posting"). Adding a third status is more code for zero additional behavior.
- **Custom risk colours per tool.** The accent pill colour is keyed off the existing `risk_level` field (`low | medium | high` → status palette tokens). Tools don't get to override colour.
- **Search inside the subagent timeline.** The expanded card lists steps in order; no search field. If a subagent has > 30 steps the card scrolls.
- **Cross‑subagent join views.** "Show me all the steps across all three subagents on a single timeline" is a power‑user view; out of scope. Per‑card timelines only.
- **Streaming events from a sibling subagent into another sibling.** Subagents are leaves of the parent run; they don't see each other's steps.

### 1.4 Success criteria

- ✅ A new tool emitting an approval with a `presentation` block (e.g. the existing `slack.post_message` MCP wrapper, updated to populate it) renders the labeled rows, the accent pill, the tool‑authored button copy, and the footer in `ApprovalTool.tsx`. Visual diff against the design's Approval card screenshot is < 4 px on the labeled‑row block at 320 px panel width.
- ✅ A tool that does **not** emit a `presentation` block renders identically to today (the old card). No regression on any of the existing approval users (`mcp_oauth_required`, MCP forwarded, generic `tool_invocation`).
- ✅ A run dispatching 3 parallel subagents (the Aurora demo) emits one `subagent_step` event per discrete step (thinking, search hit, doc read, final), interleaved with the parent run's own events on the SSE stream.
- ✅ Reconnecting at `?after_sequence=N` mid‑run rebuilds the step timeline correctly for every subagent that's currently running. No duplicates, no gaps.
- ✅ Re‑opening a completed conversation in a fresh tab rebuilds the timelines from `runtime_events` (filtered by `run_id IN (SELECT child_run_id FROM runtime_async_tasks WHERE conversation_id=?)`) without any extra round trip — `replay_events` already returns them.
- ✅ `AgentsTab.tsx` renders each card as a `<Collapsible>`. Expanded state animates open in ≤ 200 ms; arrow rotates; ARIA `aria-expanded` updates; keyboard `Enter` and `Space` toggle. Multiple cards can be open simultaneously.
- ✅ `make test` green; ai‑backend pytest green; frontend typecheck + build green.
- ✅ No new migrations. No new endpoints. No new RLS policies. No new audit actions (the existing approval and subagent audit lines already cover everything that mutates).
- ✅ Streaming‑handshake bytes pre/post merge: the new `subagent_step` envelope adds ~120 bytes per step; an Aurora‑demo run (~24 subagent steps) adds ~3 KB to the event log; well under the 32 KB envelope cap.

### 1.5 User stories

| #    | Persona                       | Story                                                                                                                                                                                                                                                                                    |
| ---- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US‑1 | Sarah · Marketing Ops         | The agent's about to post the Aurora draft to `#launch-aurora`. The approval card shows `SLACK · WRITE` in the corner, the four labeled rows, "Approve & continue" / "Skip this step", and the shield‑glyph footer. She approves with one click — she didn't have to read the JSON.      |
| US‑2 | Sarah, three subagents flying | Press Scout / Doc Reader / Voice Reviewer cards stream in. She clicks Press Scout. The card expands; she watches "Searched #launch-aurora messages → 18 hits" then "Read embargo doc" then "Composed summary" appear in order. She closes it; the chat keeps drafting underneath.        |
| US‑3 | Marcus · Eng                  | He opens an old Aurora chat from yesterday. Each subagent card is collapsed by default. He clicks Voice Reviewer; the timeline rebuilds from history. No spinner, no loading state — the events were already returned in the conversation replay.                                        |
| US‑4 | Devi · Compliance auditor     | She filters the audit log for `runtime_approval.decision`. Each row links to the approval card view; clicking one renders the labeled metadata that was on the card at decision time (preserved in `runtime_approval_requests.payload.presentation`). Sufficient context, no replay.     |
| US‑5 | Tool author · adding Jira     | Adds a `jira.create_issue` MCP tool. Wires its approval payload's `presentation` block: `{accent_label: "JIRA · CREATE", details: [{label: "PROJECT", value: "ATL"}, ...], ...}`. Frontend renders correctly with zero FE change. Lands in one PR.                                       |
| US‑6 | Sarah · network blip          | Mid‑Aurora‑run her wifi flaps. The page reconnects with `?after_sequence=N`. The subagent timelines snap back to the same rows they had pre‑disconnect. No "loading" state, no missing rows, no duplicate rows.                                                                          |
| US‑7 | Sarah · forwarded approval    | (PR 1.4 chain.) Marcus is the forward target. The card transforms to "Waiting on @marcus" (PR 3.3). The labeled rows stay visible — Marcus needs them too. When Marcus approves, Sarah's card transforms to "Approved by Marcus · Posted to #launch-aurora at 10:45." Rows stay visible. |
| US‑8 | Old tool · no presentation    | The legacy `mcp_oauth_required` approval (which doesn't ship a `presentation` block) renders exactly as it does today: title + message + Connect button. No regression.                                                                                                                  |

---

## 2 · Spec

### 2.1 Wire — `approval_requested` event payload (extended, additive)

```ts
// packages/api-types/src/index.ts — ApprovalRequestedPayload (existing) gets one optional field.

export interface ApprovalRequestedPayload {
  approval_id: string;
  approval_kind: ApprovalKind;
  tool_name: string;
  arguments: Record<string, unknown>;
  message: string | null;
  risk_level: "low" | "medium" | "high";
  read_only: boolean;
  source_tool_call_id: string | null;
  // ↓ NEW — optional. Tools that emit it get the rich card; tools that don't keep today's render.
  presentation?: ApprovalPresentation;
}

export interface ApprovalPresentation {
  /** Top-right accent pill, e.g. "SLACK · WRITE". 1–24 chars; uppercased server-side. */
  accent_label: string;
  /** 0–8 labeled rows shown inside the nested surface. Order preserved. Renderer truncates a value past 80 chars with a tooltip. */
  details: ApprovalDetailRow[];
  /** Free-text muted footer line, rendered with the shield glyph. Max 200 chars. Optional. */
  footer_note?: string;
  /** Tool-authored copy for the primary action. Default if absent: "Approve". Max 32 chars. */
  primary_action_label?: string;
  /** Tool-authored copy for the secondary action. Default if absent: "Reject". Max 32 chars. */
  secondary_action_label?: string;
}

export interface ApprovalDetailRow {
  /** UPPERCASED short label, max 24 chars. */
  label: string;
  /** Display value, max 200 chars. Plain text only — no markdown. */
  value: string;
  /** Optional one-line tooltip / hover hint. */
  hint?: string;
}
```

**Why no new event type.** The card mutation point is the moment the approval is _requested_. Approval events already deliver everything the card needs; we're adding a presentation slot, not a new lifecycle event. Resolving / forwarding / cancelling all keep their existing event types.

**Why server‑side uppercase on `accent_label`.** Tools authored by humans will send "Slack · Write" or "SLACK·write" — we normalise once at projection time so the FE renders a uniform pill.

**Why `details` is a flat list and not a typed schema (channel/visibility/action/...).** Every write tool has different concerns (Drive's `permissions`, Jira's `assignee`, GitHub's `branch`, Slack's `visibility`). A typed schema would constrain new tools to ship a schema migration whenever the design grows. A flat `(label, value)` list lets a tool author ship a new approval card in one PR with zero FE change.

### 2.2 Wire — `subagent_step` event (new)

```ts
// packages/api-types/src/index.ts — new event variant alongside `subagent_started/progress/completed`.

export interface RuntimeSubagentStepEvent {
  type: "subagent_step";
  task_id: string; // primary key — joins to `runtime_async_tasks.task_id`
  step_id: string; // unique per step — used for de-dup across replay/reconnect
  step_kind: SubagentStepKind; // `thinking | tool_call | mcp_invocation | citation | final`
  label: string; // human, ≤ 80 chars — "Searched #launch-aurora · 18 hits"
  summary: string | null; // ≤ 200 chars — first-line preview of the step's output
  status: "running" | "completed" | "failed";
  started_at: string; // ISO 8601
  completed_at: string | null; // null while running
  duration_ms: number | null; // null while running
  error_summary: string | null; // populated on `failed`
}

export type SubagentStepKind =
  | "thinking"
  | "tool_call"
  | "mcp_invocation"
  | "citation"
  | "final";
```

**Coalescing.** A subagent step typically generates _two_ events in its child run (one `started`, one `completed`). The parent's forwarder coalesces these into one `subagent_step` event with `status="running"` first, then mutates to `status="completed"` (or `failed`) — both rendered with the same `step_id`. The reducer applies the second by `step_id` lookup; the FE renders the latest.

This is intentional: live cards need to show "Reading embargo doc…" with a spinner while running, and "Read embargo doc · 1.2s" once done — the FE doesn't render two separate rows.

**Why not stream every model‑delta from the subagent.** Two reasons. (1) The parent thread is the surface where the user reads; subagent token deltas would fire dozens of events per step and bloat the parent stream. (2) The subagent's model is an internal detail the user doesn't audit at the token level. They audit "what did it do, what did it find."

### 2.3 Persistence — zero changes

| What                                                   | Where it lives today                                                                                                                                                                                                                                                                                                                           |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `presentation` block on a pending approval             | `runtime_approval_requests.payload` is already a JSONB column with the entire `approval_requested` payload. `presentation` is stored alongside `arguments`, `risk_level`, etc. Field‑level encryption at rest applies to `value`s containing user content via `FieldCodec` on the `payload` column (already in 0008 RLS+encryption migration). |
| `subagent_step` events while running                   | `runtime_events(run_id, sequence_no, type, payload)` — every event already lands here for replay. Parent's forwarded `subagent_step` events lands on the **parent run's** event log. Child run's underlying events also land on the **child run's** event log (no change).                                                                     |
| Subagent step timeline post‑run                        | `SELECT payload FROM runtime_events WHERE run_id = $1 AND type = 'subagent_step' ORDER BY sequence_no` — already a one‑line read.                                                                                                                                                                                                              |
| Mapping `task_id → child_run_id` for archive timelines | `runtime_async_tasks(task_id, child_run_id)` — already exists from PR 1.5.                                                                                                                                                                                                                                                                     |

**Why no new column on `runtime_approval_requests`.** Adding `presentation` as a top‑level column would be type‑checked but force a migration every time we add a field to the block (e.g. `accent_icon`). Storing it inside the existing `payload` JSONB keeps the contract type‑checked at the Pydantic layer and allows additive expansion without a migration. Pydantic enforces shape on read; the column stays generic.

**Why `presentation.value` is not separately encrypted.** It's a thin projection of `arguments` (which is already encrypted via `FieldCodec`). The presentation rows are by definition non‑sensitive labels for the user (channel name, visibility scope, etc.); the actual sensitive payload lives in `arguments`.

### 2.4 Service path — no new routes

The four existing flows are unchanged:

```
SSE stream → frontend                  GET /v1/agent/runs/{run_id}/stream?after_sequence=N
Replay (archive read on conversation)  GET /v1/agent/runs/{run_id}/events
Approval decision                       POST /v1/agent/approvals/{approval_id}/decide
Subagents archive read                  GET /v1/agent/conversations/{id}/subagents
```

The forwarded `subagent_step` events ride the SSE stream and the replay endpoint without code change — `runtime_api/http/runs.py` returns whatever events the run has, generically.

### 2.5 Where the new code lives

#### ai‑backend

- **`services/ai-backend/src/runtime_api/schemas/events.py`** — register `subagent_step` event variant on the `RuntimeEventEnvelope` discriminator. Add `ApprovalPresentation` and `ApprovalDetailRow` Pydantic models. Extend `_approval_requested_payload()` (already at line 521) to project `presentation` if present. Uppercase `accent_label` here.
- **`services/ai-backend/src/runtime_worker/stream_subagents.py`** — extend the existing forwarder. Today it watches the child run and emits parent‑scoped `subagent_started/progress/completed`. Add: for each child‑run event of type `tool_invocation_started / completed`, `model_thinking_completed`, `model_final`, mint a coalesced `subagent_step` parent event. De‑dup by `step_id = sha256(child_run_id || child_event_id)` so re‑emission on reconnect is a no‑op.
- **Tools that emit approvals (Slack, Drive, etc.)** — populate the `presentation` block in their approval emit calls. Lives in `services/ai-backend/src/agent_runtime/capabilities/tools/builtin/approvals.py` (new helper `build_presentation(...)`) and per‑tool wrappers under `capabilities/tools/builtin/`. Existing approvals without presentation blocks are untouched.

No persistence layer changes. No `migrations/*.sql`. No new tables.

#### backend‑facade

- **No code change.** Existing routes `POST /v1/agent/approvals/{id}/decide`, `GET /v1/agent/runs/{run_id}/stream`, `GET /v1/agent/runs/{run_id}/events`, `GET /v1/agent/conversations/{id}/subagents` proxy the new payload shape transparently. Pydantic schemas in the facade are loose enough (or are imported from api‑types) that the new fields pass through.

#### api‑types

- **`packages/api-types/src/index.ts`** — extend `ApprovalRequestedPayload` with `presentation?: ApprovalPresentation`; add `ApprovalPresentation`, `ApprovalDetailRow`, `RuntimeSubagentStepEvent`, `SubagentStepKind`. Re‑export.

#### frontend

- **Add dependency.** `apps/frontend/package.json`: `@radix-ui/react-collapsible@^1.1.x`. Already use `@radix-ui/react-popover@^1.1.15`; same maintainer, same release cadence, same SSR story.
- **`apps/frontend/src/features/chat/components/tools/ApprovalTool.tsx`** — when `payload.presentation` is present, render the new card layout: accent pill (top‑right), labeled rows (`<dl>` semantics for accessibility), tool‑authored primary/secondary copy, footer note. When absent, render the existing layout. Keep the (PR 1.4) forward picker in both modes.
- **`apps/frontend/src/features/chat/components/workspace/AgentsTab.tsx`** — wrap each card in `<Collapsible.Root>`. Inside the collapsible body, render `SubagentStepTimeline` (new component, ≈ 60 LOC) — a vertical list of step rows: status dot, label, summary, duration. Streams live; sorted by `started_at`.
- **`apps/frontend/src/features/chat/chatModel/eventReducer.ts`** — one new case for `subagent_step`. Upserts into a per‑task step buffer keyed by `task_id`. De‑dups by `step_id`.
- **`apps/frontend/src/features/chat/chatModel/subagentReducer.ts`** — extend `SubagentEntry` (already exists) with a `steps: SubagentStep[]` field. Reducer fills it.
- **CSS** — extend `apps/frontend/src/features/chat/components/tools/ApprovalTool.module.css` (or equivalent) with the labeled‑row block, accent pill, footer styles. Extend `AgentsTab.module.css` with the disclosure / step‑row styles. ≈ 80 LOC of CSS total. Tokens come from the design system (already aligned in PR 0.1: `--color-accent`, `--color-bg`, `--color-surface`, status palette).

### 2.6 Audit

Every privileged event already audits today. Specifically:

| Action                     | Already audited?                                                                             |
| -------------------------- | -------------------------------------------------------------------------------------------- |
| `approval.decided`         | ✅ `runtime_audit_log` writes the decision + `decided_by_user_id` + `approval_id` (PR 1.4).  |
| `approval.forwarded`       | ✅ `runtime_audit_log` writes the chain row (PR 1.4).                                        |
| Subagent dispatch / result | ✅ `runtime_async_tasks` is the audit anchor; `runtime_subagent_results` writes the outcome. |
| Step timeline              | ✅ `runtime_events` is append‑only, sequence‑numbered. Forensic queries already work.        |

**No new audit actions** — the new events are presentation/observability, not privileged writes. The decision the user makes on the approval still writes the existing `runtime_audit_log` row. The labeled metadata at decision time is preserved in `runtime_approval_requests.payload.presentation` for SIEM export.

### 2.7 Permissions, rate limits, errors

- **No new endpoints**, so no new auth, no new rate limits, no new error codes.
- The new payload field passes the existing org‑scoped RLS on `runtime_approval_requests` (read/write only by the requesting org).
- The new event passes the existing run‑scope check on `GET /v1/agent/runs/{id}/stream` and `…/events` (caller must be a member of the run's `org_id`).

### 2.8 Validation rules (server‑side, on emit)

```py
# services/ai-backend/src/runtime_api/schemas/events.py — new validator.

class ApprovalPresentation(BaseModel):
    accent_label: str = Field(min_length=1, max_length=24)
    details: list[ApprovalDetailRow] = Field(max_length=8)
    footer_note: str | None = Field(default=None, max_length=200)
    primary_action_label: str | None = Field(default=None, max_length=32)
    secondary_action_label: str | None = Field(default=None, max_length=32)

    @field_validator("accent_label")
    @classmethod
    def _uppercase(cls, v: str) -> str:
        return v.upper()


class ApprovalDetailRow(BaseModel):
    label: str = Field(min_length=1, max_length=24)
    value: str = Field(min_length=1, max_length=200)
    hint: str | None = Field(default=None, max_length=200)

    @field_validator("label")
    @classmethod
    def _uppercase(cls, v: str) -> str:
        return v.upper()
```

Approvals exceeding the limits get rejected at the runtime layer (the tool gets a `ValueError` it can catch) — we never let the frontend render an unbounded card.

### 2.9 Streaming guarantees (no change)

The new `subagent_step` event uses the same envelope, the same `sequence_no` allocator, the same outbox writer, and the same SSE encoding as every other event. Therefore:

- **Resume** with `?after_sequence=N` is correct without any code change.
- **Cancel** mid‑run drops in‑flight subagent steps the same way it drops other in‑flight events.
- **Replay** (`GET /v1/agent/runs/{id}/events`) returns the same wire payload, so archive read and live stream parse identically (the FE has one reducer).
- **Backpressure** is handled by the existing outbox writer (PR 1.4 hardening already ensures we don't drop on slow consumers).

---

## 3 · Architecture

### 3.1 Data flow — approval card

```
Tool (slack.post_message)                   ai-backend (runtime_api)
─────────────────────────                   ──────────────────────────
  build_presentation(...)                     project to ApprovalRequestedPayload
        │                                          │
        ▼                                          ▼
  approval_requested  ──persist──►  runtime_approval_requests.payload  ──emit──►  SSE
                                                                                  │
                                                                                  ▼
                                                                         frontend reducer
                                                                                  │
                                                                                  ▼
                                                                    ApprovalTool.tsx renders
                                                                       (presentation? rich : legacy)
```

A tool author emits the approval. The runtime persists the full payload (including `presentation`) and emits the event. The frontend reducer hands the payload to `ApprovalTool.tsx`, which checks whether `presentation` is populated and picks the right render path. Two render paths, one component — branching on the data, not on the tool name.

### 3.2 Data flow — subagent step timeline

```
parent run                child run (subagent)
──────────                ─────────────────────
  dispatch       ────►    runtime_worker spawns
                            │
                            ▼
                          steps fire events on
                          child's runtime_events log
                            │
                            ▼ (forwarder watches)
  stream_subagents.py  ◄────┘
        │
        ▼
  mint coalesced subagent_step events on parent's runtime_events log
        │
        ▼  (live)                              │  (replay on conversation re-open)
  parent SSE stream                            │
        │                                      ▼
        ▼                                GET /runs/{parent}/events
  frontend reducer                       (returns same envelopes)
        │
        ▼
  subagentReducer fills entry.steps[]
        │
        ▼
  AgentsTab card <Collapsible> expanded → renders steps
```

**Live and archive use the same wire and the same reducer.** This is the key DRY win: a fresh page load consumes `runtime_events` via the replay endpoint, runs them through the same reducer the live SSE feeds, and rebuilds the timeline byte‑for‑byte. No second code path.

### 3.3 Why coalesce at the forwarder (and not at the reducer)?

If we forwarded raw `started` and `completed` from the child, the parent's event log would carry **two** events per step — fine on the wire, ugly when a SIEM admin queries `runtime_events` and sees both. Coalescing at the forwarder means one row per step in the parent's log; the FE reducer mutates the row's status from `running` to `completed`. SIEM sees one step; FE sees the right state at every snapshot. This is also why `step_id` is deterministic (`sha256(child_run_id || child_event_id)`) — re‑emission on reconnect is idempotent.

### 3.4 Library choice — `@radix-ui/react-collapsible`

We already ship `@radix-ui/react-popover` (design system + frontend). Same maintainer, same release lane, same SSR posture. The collapsible primitive gives us animated `data-state="open|closed"`, ARIA `aria-expanded`, keyboard handling (Enter, Space), and a controlled mode if we want to programmatically open a card from outside (e.g. on `subagent_step` failure, auto‑expand the failed card — we don't ship that in v1 but the door is open).

The alternative — native `<details>/<summary>` — has poor animation support across browsers and no controlled API. The alternative — Headless UI / cmdk / our own — is more code we maintain. The Radix sibling is the smallest delta.

### 3.5 Backend code locality

- **One file** for the wire change: `services/ai-backend/src/runtime_api/schemas/events.py`.
- **One file** for the projector: same file (`_approval_requested_payload`).
- **One file** for the forwarder extension: `services/ai-backend/src/runtime_worker/stream_subagents.py`.
- **One helper** for tool authors: `services/ai-backend/src/agent_runtime/capabilities/tools/builtin/approvals.py::build_presentation()`.

Total surface in the backend: ≈ 4 functions, 1 model, 1 helper. No new modules.

### 3.6 Frontend code locality

- `ApprovalTool.tsx` — branch on `presentation`. ≈ 60 LOC of new render.
- `AgentsTab.tsx` — wrap card in `<Collapsible>`. ≈ 30 LOC.
- New `SubagentStepTimeline.tsx` — ≈ 60 LOC.
- `eventReducer.ts` — one case. ≈ 12 LOC.
- `subagentReducer.ts` — one field, one helper. ≈ 18 LOC.
- CSS — ≈ 80 LOC.

No primitive added to `packages/design-system` (the Radix collapsible lives at the app boundary; it's a chat‑surface concern, not a cross‑cutting primitive yet). If we use it in 3+ surfaces later, we lift it.

### 3.7 What this PR explicitly does **not** change

- LangGraph executor, deep‑agent builder, runtime factory, capability loader, MCP middleware, OAuth flow, token vault, audit chain signing, RLS policies, encryption codec, retention policy.
- Any existing approval consumer (`mcp_oauth_required`, generic tool invocation, `ConnectorAuthTool`).
- Subagent dispatch, scheduler, fan‑out, completion semantics.
- The parent run's other event types (`model_delta`, `final_response`, `tool_invocation_*`).
- `runtime_api/http/runs.py`, `runtime_api/http/conversations.py`, `runtime_api/http/approvals.py`.

These are tested invariants. Anything we touch outside the listed files is a regression.

---

## 4 · Verification

### 4.1 Unit / service tests (`services/ai-backend`)

- `tests/unit/runtime_api/schemas/test_approval_presentation.py` — Pydantic accepts/rejects bounds (label > 24 chars rejected; `accent_label` lowercased input becomes uppercased output; missing block stays missing; `details=[]` valid).
- `tests/unit/runtime_worker/test_stream_subagents.py` — given a child run with N step events, parent gets N coalesced `subagent_step` envelopes with deterministic `step_id`. Re‑driving the forwarder on the same child events emits zero new envelopes (idempotent).
- `tests/integration/test_approval_with_presentation.py` — end‑to‑end: tool emits approval with presentation; SSE consumer receives the projected payload; replay endpoint returns it identically.
- `tests/integration/test_subagent_timeline_live_and_archive.py` — three subagents in flight; live SSE produces N steps per agent; reconnect at `?after_sequence=N` yields no duplicates; archive read after run completes yields the same N rows.

### 4.2 Frontend tests (`apps/frontend`)

- `__tests__/eventReducer.subagent_step.test.ts` — given a sequence of `subagent_step` events, the per‑task buffer holds the right rows in the right order; coalescing flips `status` correctly.
- `__tests__/ApprovalTool.presentation.test.tsx` — renders rich card when `presentation` is set; renders legacy when not. Snapshot for both.
- `__tests__/AgentsTab.collapsible.test.tsx` — clicking a card toggles `aria-expanded`; pressing Space/Enter on the card toggles; rendered timeline matches reducer state.

### 4.3 Cross‑service smoke (`make test`)

The Aurora demo scenario: send the prompt, observe (a) the slack‑post approval card with `SLACK · WRITE` pill + four rows + tool‑authored buttons + footer; (b) three subagent cards in the Agents tab; (c) clicking each expands a live step timeline; (d) reconnecting mid‑run rebuilds the timelines without dups.

### 4.4 Compliance gate

- Audit log unchanged in shape; SIEM export contract preserved.
- No new sensitive workflow added; therefore no new "who can do it / who approved it / where logged" entries needed.
- Field‑level encryption: confirm the JSONB `payload` (including `presentation`) round‑trips through `FieldCodec` v1 the same as today (no special handling needed because rows are sub‑fields of an already‑encrypted column).
- RLS: confirm `runtime_approval_requests` queries with the new payload shape pass the existing tenant‑isolation policies (no test change needed; payload doesn't influence the predicate).

### 4.5 Telemetry gate

- `pg_stat_statements` shows no new query shapes (we didn't add columns or endpoints).
- New event type appears in `runtime_events.type` cardinality dashboard; alert thresholds adjusted (one‑time PR in the observability follow‑up — not blocking).

---

## 5 · Out of scope (this PR)

These were considered and explicitly left for follow‑ups:

- **Per‑tool overrides on accent‑pill colour.** Today colour is keyed off `risk_level`; tools can't override. If an admin wants a "LOW‑risk but BRAND‑critical" tool to be amber, that's a future hook.
- **Editing the approval before approving.** "Change the channel from `#launch-aurora` to `#launch-aurora-test` and then approve" is a deeper UX. We ship view + decision only.
- **Subagent step search / filter / pin.** Long timelines scroll; no in‑card search in v1.
- **Cross‑subagent unified timeline view.** "Show me the merged timeline of all subagents on this run." Power‑user feature, future.
- **Streaming subagent token deltas.** We surface step‑level events only; sub‑model tokens stay in the child run.
- **Subagent timeline export.** "Download the steps as JSON." Audit log already covers this for compliance; we don't need a UI export in v1.
- **Per‑step model attribution.** Showing which sub‑model the subagent used per step is a Usage‑pane concern, not an Agents‑tab concern.

---

## 6 · References

- Atlas design handoff bundle: `/tmp/design-fetch/extracted/0x-copilot/`
  - `project/messages.jsx` — original approval card render with labeled rows + accent pill + footer note
  - `project/app.jsx` — buildScenario → Aurora subagents
  - `Design Doc.html` § Pages → Main app → Approval card / Agents tab
- Existing PRDs:
  - [`pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md) — chain wire we're additive on
  - [`pr-1.4.1-approval-forwarding-hardening.md`](pr-1.4.1-approval-forwarding-hardening.md) — the picker we keep
  - [`pr-1.5-subagent-discovery-workspace-feeds.md`](pr-1.5-subagent-discovery-workspace-feeds.md) — `runtime_async_tasks` / `runtime_subagent_results`, the read endpoint
  - [`pr-3.2-workspace-pane-right-rail.md`](pr-3.2-workspace-pane-right-rail.md) — Agents tab host
  - [`pr-3.3-mcp-discovery-approval-polish.md`](pr-3.3-mcp-discovery-approval-polish.md) — display‑name + chain‑final card; orthogonal to this PR
- Library:
  - `@radix-ui/react-collapsible` (https://www.radix-ui.com/primitives/docs/components/collapsible) — already‑in‑family of the Popover primitive we ship today.
