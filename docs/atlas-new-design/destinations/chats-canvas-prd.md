# Chats — Thread Canvas (Studio / Focus / Auto) — Phase 1 Sub-PRD

**Status:** draft (2026-05-17)
**Owner:** parth (product) — orchestrator dispatches impl agents from §17
**Parent:** [PRD.md](../PRD.md) §8 + §9 (modes + composer) and [destinations-master-prd.md](../destinations-master-prd.md) §6 phase 1
**Design source of truth:** Claude Design handoff bundle at `/tmp/atlas-design/enterprise-search-template/` — `chats/chat1.md` lines 240–820 are the load-bearing transcript; `project/thread-canvas.jsx`, `project/tc-chat.jsx`, `project/canvas-shared.jsx`, `project/canvas-apps.jsx`, `project/composer.jsx` are the JSX references.

---

## 1. Premise + user job

A **thread** is a working session in which Atlas operates across the user's SaaS surfaces on their behalf (chat1.md L240-242). It is **not** a chat log. The chat is the interface _to_ the session; the session itself is the cross-surface work — rows in Salesforce, drafts in Gmail, swaps in Slides, queries in a database. The whole record persists: every action across every surface, time-ordered, with the chat woven through as the human's running commentary and Atlas's running narration.

The three modes are postures, not layouts (chat1.md L271-280):

- **Studio** — _"Show me everything."_ User is actively co-working with Atlas across multiple surfaces, watching pending diffs land, approving inline. Default for live sessions. Surface top, chat right column, swimlane timeline below.
- **Focus** — _"I trust the work, get out of my way."_ The session is healthy; the user wants one surface large and a quiet status pulse. Surface fills, composer-only bar at the bottom, mini timeline below, right-rail Activity/Approvals tabs supply the awareness lost from removing the chat history.
- **Auto** — _"Run it, don't ask."_ Approvals are pre-applied and narrated as "Auto-applied" cards. Chat fills the canvas. No timeline. A top banner with "Switch to Studio" bridges back.

User jobs ranked (chat1.md L256-269):

1. Approve / reject pending edits — **one click, every minute**.
2. See "what is Atlas touching right now" — **glance, every few seconds**.
3. Reply / direct in chat — often.
4. Open the surface to see context for an approval — several times a session.
5. Scrub back to "what did Atlas do at 11:43?" — a few times when something looks wrong.
6. Restore / branch from a past state — rare.
7. Swap mode — per-session posture switch.
8. Pin a moment — power-user candy.

This sub-PRD is the contract for the impl agents who deliver §17. Every section answers a question one of them will ask.

## 2. Source-of-truth map (this phase only)

| Artifact                                                | Canonical path                                                                    | Consumers                                                                                 | Notes                                                                                                                             |
| ------------------------------------------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| 3-mode container                                        | `packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx`                        | `apps/frontend/src/features/chat/ChatScreen.tsx` (mounts when active conversation exists) | Mounts **once per conversation**. `mode` prop drives layout; never remount on mode switch.                                        |
| Mode enum                                               | `packages/chat-surface/src/thread-canvas/modes.ts` (new)                          | ThreadCanvas, RightRail, persistence layer                                                | `type ThreadMode = "studio" \| "focus" \| "auto"`. Single source.                                                                 |
| Swimlane timeline (Studio)                              | `packages/chat-surface/src/thread-canvas/TcSwimlanes.tsx` (exists)                | ThreadCanvas (Studio branch)                                                              | Owns the transport controls + per-app swimlanes + minimize chevron.                                                               |
| Mini timeline (Focus / Studio-minimized)                | `packages/chat-surface/src/thread-canvas/TcMiniTimeline.tsx` (new)                | ThreadCanvas (Focus branch + Studio minimized branch)                                     | Color-coded beads strip + Live/↩ Now pill + expand-to-Studio chevron.                                                             |
| App tabs strip                                          | `packages/chat-surface/src/thread-canvas/TcTabs.tsx` (exists)                     | ThreadCanvas Studio/Focus                                                                 | Carries per-tab pending-diff count badge; "Replay" pill appears when scrubbing.                                                   |
| Surface mount + pending-diff overlay                    | `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx` (exists)             | ThreadCanvas                                                                              | Lazy-loads `packages/surface-renderers/` via `SurfaceHost` port (see §14).                                                        |
| Inline diff card (in surface)                           | `packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx` (exists)               | TcSurfaceMount overlays + TcChat (mirror) + RightRail Approvals tab (mirror)              | Single component, three call sites — same state, no fork.                                                                         |
| Chat side (subagents + diffs + streaming)               | `packages/chat-surface/src/thread-canvas/TcChat.tsx` (exists; today is stub)      | ThreadCanvas Studio (right column) + Auto (centered)                                      | Reuses the subagent/MCP/tool/diff event shapes projected from the run-event stream — see §4.                                      |
| Right rail (Activity + Approvals tabs)                  | `packages/chat-surface/src/shell/RightRail.tsx` (exists; empty-state-only)        | `ChatShell`                                                                               | Tabs only when destination=chats AND a thread is active. See §3.5.                                                                |
| Right-rail tabs content                                 | `packages/chat-surface/src/shell/right-rail/{ActivityTab,ApprovalsTab}.tsx` (new) | RightRail                                                                                 | Stateless given the event-projector store; no per-tab data fetching.                                                              |
| Event projector (RuntimeEventEnvelope → UI projections) | `packages/chat-surface/src/thread-canvas/eventProjector.ts` (new)                 | TcSwimlanes, TcMiniTimeline, TcChat, RightRail tabs, TcSurfaceMount                       | **One projector, four consumers.** No event-shape conversions outside this file.                                                  |
| Mode persistence                                        | `packages/chat-surface/src/thread-canvas/modePersistence.ts` (new)                | ThreadCanvas, ChatScreen                                                                  | Per-conversation `mode` stored in `KeyValueStore` (see §3.3). Default `studio`.                                                   |
| Composer (THE one)                                      | `packages/chat-surface/src/composer/Composer.tsx` (exists)                        | ThreadCanvas (Studio embedded, Focus bar, Auto bar) + welcome state + welcome-suggestions | The frontend-side `apps/frontend/src/features/chat/runtime/composer/Composer.tsx` is **deleted** as part of this phase — see §15. |
| Composer extras (attachments, skills, edit, connectors) | `packages/chat-surface/src/composer/extras/` (new sub-folder; see §15)            | Composer call sites                                                                       | Absorbed where they conceptually belong; not folded into Composer when they're orthogonal (edit-composer; connectors).            |
| Run-start payload (`reasoning_depth` wiring)            | `apps/frontend/src/api/agentApi.ts:createRun()` (exists)                          | ChatScreen submit path                                                                    | Top-level `reasoning_depth` field — see §16. The `applyDepth(model, depth)` hack moves to the top-level field.                    |
| Activity projection contract                            | `packages/api-types/src/index.ts` (extends `RuntimeEventEnvelope` consumers)      | Frontend                                                                                  | No new wire fields needed for Phase 1. Backend already emits the events the projector needs.                                      |
| Time-travel surface state                               | (proposed) `GET /v1/agent/runs/{run_id}/surface-snapshot?at_sequence=N`           | TcSurfaceMount when `scrubbedIdx != null`                                                 | Server-side new endpoint — see §4.3 and §17 Impl-A.                                                                               |
| Branch-from-bead                                        | (proposed) `POST /v1/agent/conversations/{conv_id}/branches`                      | TcSwimlanes "Branch from here" action                                                     | Server-side new endpoint — see §4.4 and §17 Impl-A. May also use the existing `branch_id` field on `CreateRunRequest`.            |
| Audit (approval decisions, restore, branch)             | `packages/audit-chain` (exists)                                                   | Backend approval-decision handler + new restore/branch handlers                           | Schema in §6.                                                                                                                     |

A second copy of any of these is a bug. The ChatScreen ports the composer instance and the diff projection — it does **not** re-implement them.

## 3. Architecture

### 3.1 Component tree

```
ThreadCanvas (mounts once per conversationId)
│
├── (mode === "studio")
│   ├── TcTabs            — app strip; per-tab pending count; Replay pill when scrubbing
│   ├── grid:
│   │   ├── TcSurfaceMount       — surface column (left)
│   │   ├── TcChat               — chat column (right, 360px)
│   │   │   ├── (renders embedded Composer at bottom — design composer.jsx L21+)
│   │   │   ├── TcSubagent cards (collapsible: TcThinking + TcMcpRow + TcToolRow + TcInlineDiff + stream)
│   │   │   └── Bubble (user/assistant text messages)
│   │   └── TcSwimlanes          — full swimlanes + transport controls + minimize chevron
│   │   OR (if timelineMinimized)
│   │   ├── Composer (hoisted to full surface-column width — design L745-758)
│   │   └── TcMiniTimeline       — bead strip + expand-to-Studio chevron
│
├── (mode === "focus")
│   ├── TcTabs
│   ├── grid:
│   │   ├── (left column)
│   │   │   ├── TcSurfaceMount
│   │   │   ├── Composer (composer-only bar; no chat history above it — design tc-chat.jsx L520+)
│   │   │   └── TcMiniTimeline (expand-to-Studio chevron)
│   │   └── (right column = the workspace RightRail; see §3.5)
│   │
│   └── (RightRail is owned by ChatShell, not ThreadCanvas — but in Focus mode it MUST be open;
│         ThreadCanvas signals via onModeChange so ChatScreen reconciles RightRail state.)
│
└── (mode === "auto")
    ├── Auto banner ("● Auto · N actions applied across M surfaces · Switch to Studio")
    └── TcChat (centered, max-width 760px, auto-applied diff cards render with "Auto-applied" label)
```

The TcEmpty (welcome-state when conversation has no run history yet) is **not** part of the three-mode body — it renders when `actions.length === 0`. Render shape: title + subtitle + Composer (the same one, prop-driven, with `placeholder="Ask Atlas to find, summarize, or draft something…"`).

### 3.2 Data flow (run events → state → UI)

```
ai-backend run-events (SSE /v1/agent/runs/{id}/stream + replay /v1/agent/runs/{id}/events)
     │
     ▼ ChatScreen subscribes once per active run (existing code)
     │  pushes RuntimeEventEnvelope[] into chat-surface via context
     ▼
chat-surface eventProjector.ts (new)
     │
     ├── projectActions(env[]) → SwimlaneBead[]      (one bead per state-changing event)
     ├── projectActivity(env[]) → ActivityEntry[]    (think/mcp/tool/out/stream entries — RightRail Activity)
     ├── projectSubagents(env[]) → SubagentCard[]    (grouped by subagent_id; nested events)
     ├── projectDiffs(env[]) → PendingDiff[]         (approval_requested envelopes still in "pending")
     └── projectStreaming(env[]) → StreamingDelta[]  (live tool_call_delta / model_delta for in-flight steps)
     ▼
state: useMemo'd projections + per-mode local state (scrubbedSeq, timelineMinimized, activeAppUri)
     │
     ▼ consumers
     ├── TcSwimlanes(beads)           — Studio
     ├── TcMiniTimeline(beads)        — Focus / Studio-min
     ├── TcChat(subagentCards, diffs, streaming, bubbles)
     ├── RightRail.ActivityTab(activity)
     ├── RightRail.ApprovalsTab(diffs)
     ├── TcSurfaceMount(activeAppUri, pendingDiffsByUri)
     └── TcInlineDiff (rendered both in TcChat and in TcSurfaceMount — same `diff` instance, shared `onAccept`/`onReject`)
```

**Where state lives.** The run-event stream subscription stays in ChatScreen (it's an HTTP/SSE concern, owned by the host). The projection layer + all per-mode UI state lives in chat-surface — the package consumes events as data, not network. This preserves substrate-agnosticism (§14): a desktop substrate that gets events via a different transport (e.g. IPC from a remote main process) still feeds the same projector.

**ThreadCanvas owns:**

- `scrubbedSeq: number | null` — the playhead. `null` = live.
- `activeAppUri: string` — the foreground app tab.
- `timelineMinimized: boolean` — Studio mode's "collapse swimlanes" toggle (persisted, see §3.3).
- `chatScrollAnchor: number` — restore scroll position on mode switch.

**ChatScreen owns:**

- The SSE subscription.
- `RuntimeEventEnvelope[]` history.
- The composer's last-known draft (so users don't lose typed text across mode switches — though Composer's internal `text` already covers this since the mount is stable).
- The `reasoning_depth` value + the `selectedModelId` (sent into Composer as `initialDepth` / `initialModel`, see §16).

### 3.3 Mode storage + switching

- **Storage:** per-conversation, in `KeyValueStore` (port at `packages/chat-surface/src/ports/KeyValueStore.ts` already exists). Key: `chats.thread.<conversation_id>.mode`. Default `studio`. URL does **not** carry mode — modes are workspace posture, not addressable state. ⌘K / "Restore link" do not need to encode them.
- **Switching:** `ThreadCanvas` takes `mode` + `onModeChange` props. ChatScreen reconciles persistence: on `onModeChange(next)` it writes to KV and updates its own `mode` state. ChatScreen is also the source of `mode === "focus" ? rightRailForced=true : undefined` to tell ChatShell to open the rail.
- **Animation:** mode switch is **not** a remount (performance invariant — §10). It's a CSS-grid template change; the surface column, chat column, swimlane row reshape. Transition: `300ms cubic-bezier(0.2, 0.7, 0.2, 1)` on the grid template; instant on the inner components (no element remounts).
- **What survives across modes:**
  - Chat scroll position (chat history is the same data; we restore the scroll offset).
  - Active app tab.
  - Scrub position (`scrubbedSeq`) — if scrubbed in Studio and the user switches to Focus, the mini timeline shows the same scrub state and the surface stays frozen.
  - Composer draft (Composer is a stable instance across modes — single mount).
  - `reasoning_depth` selection.
- **What resets:**
  - On `conversationId` change, all of the above reset (existing behavior; thread-canvas.jsx L640-645).
  - On entering Auto, pending diffs auto-apply (auto-state, distinct from `accepted`). On leaving Auto, auto-accepted diffs stay applied; unresolved ones revert to pending.

### 3.4 Composer ↔ ThreadCanvas

Three render slots, ONE Composer instance per conversation:

| Mode                             | Composer location                                                          | Surrounding context                                                        |
| -------------------------------- | -------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Studio                           | Embedded at the bottom of TcChat (the right column)                        | Chat history scrolls above; composer is anchored bottom                    |
| Studio with timeline minimized   | Hoisted out — full-width bar under the surface, above the mini timeline    | TcChat hides its internal composer (`hideComposer` prop on TcChat, exists) |
| Focus                            | Full-width composer-only bar at the bottom of the surface column           | No chat history above; chat is in the RightRail Activity tab               |
| Auto                             | Bottom of the centered TcChat column                                       | Chat fills, max-width 760px                                                |
| Welcome (no thread / new thread) | Centered card, large headline above ("Hi `<user>`. What are we shipping?") | No timeline, no surface, no RightRail tabs                                 |

**Implementation rule:** Composer is rendered by ThreadCanvas (or ChatScreen for the welcome state). Its props are computed from ChatScreen state and passed through. There is **no** `composerSlot` prop that lets a parent inject a different composer — the canonical Composer is invariant.

### 3.5 Right rail tabs (Activity + Approvals)

- **When destination=chats AND a thread is active:** RightRail renders the tabbed view.
  - **Activity (default tab):** ChatGPT-style chronological stream of think/MCP/tool/output/streaming entries — see `TcActivityFeed` reference in thread-canvas.jsx L385-438. Item kinds: `think` (collapsible by default), `tool` (mcp / internal tool), `out` (tool result), `stream` (live model/tool delta), `subagent_start`, `subagent_done`. Same data the chat side shows; different projection. No data duplication — the projector emits both shapes from the same envelopes.
  - **Approvals tab:** count + list of pending inline diff cards (per chat1.md L295 — Approvals tab is a **summary**, the actual Approve/Reject still happens inline in the surface). Each list row has "Open in `<surface>` →" that scrolls the surface into view AND highlights the inline diff. Per chat1.md L313: there is **no global approval queue**.
  - **Mode-dependent open/close behavior:**
    - Studio: rail closes by default (the chat column already shows the work); user can open it manually.
    - Focus: rail **opens by default** — Focus removed the chat history; Activity tab restores it without crowding the surface.
    - Auto: rail closes (chat fills canvas).
- **When no thread is active:** RightRail collapses (zero width).
- **Persistence:** existing behavior — open/closed state per workspace destination keyed in KV. Phase 1 adds the per-mode default-open override (computed at render time; doesn't write to KV unless user explicitly toggles).

### 3.6 Approvals — inline-in-surface protocol

Per chat1.md L316 and L340, approvals live **inline in the surface where they apply**. Master PRD §3.2 says every approval is audited.

**Diff card component contract** (`TcInlineDiff`):

```ts
interface TcInlineDiffProps {
  readonly diff: PendingDiff; // { id, surface_uri, title, sub, source_attribution: SourceLink[] }
  readonly state: "pending" | "accepted" | "rejected" | "auto" | "queued";
  readonly onAccept: (id: string) => Promise<void>; // calls decideApproval(id, "accept", identity)
  readonly onReject: (id: string) => Promise<void>; // calls decideApproval(id, "reject", identity)
  readonly onSuggestEdit?: (id: string, suggestion: string) => Promise<void>; // post-Phase-1 (master PRD §3.2 includes "Edit")
  readonly onJumpToSurface: (uri: string) => void; // opens the surface tab if not already active
  readonly autoMode?: boolean;
}
```

**Render rules:**

- Pending state (chat1.md L348-355): the card has Accept (primary) + Reject (secondary) + Open-in-`<surface>` (tertiary). Per chat1.md L383, **no keyboard shortcuts** — buttons only. Hover affordances + visible buttons; no Cmd+Enter chord.
- Source attribution chip is **clickable** (chat1.md L389): clicking jumps to the cited Salesforce record / sheet row / drive file / etc. Implementation: each `SourceLink` carries `{kind, uri}`; ChatScreen registers a router that opens the right destination (citation kinds already supported by `packages/chat-surface/src/citations/`).
- Card renders identically in three places:
  - Inline in TcSurfaceMount overlay (the canonical position; surface flows around it).
  - Inline in TcChat (mirror — when the subagent emits the approval, the chat shows a card with "Open in `<surface>` →" that just scrolls focus to the canonical one in the surface).
  - Inline in RightRail Approvals tab (summary list — each row has the same Accept/Reject buttons; mutations route through the same `decideApproval` call).
  - All three share the same state — mutating one updates the other two (same `diff.id`, same projector reading from the same envelope store).

**How the surface knows a diff is pending:** the projector emits `PendingDiff[]` indexed by `surface_uri`. TcSurfaceMount filters for `pendingDiffs[activeAppUri]`. The mount renders the canonical overlay; the position within the surface is the renderer's responsibility (sheet renderer positions over row 5; email renderer positions over the streaming paragraph). The diff carries `anchor: { kind: "row" | "range" | "cell" | "slide" | "free", spec: ... }` so the renderer can position deterministically.

**How acceptance commits:** `onAccept` calls `decideApproval(diff.id, "accept", identity)` (existing endpoint). The backend resolves the approval, emits `approval_resolved`, runtime resumes execution. The projector observes the resolved envelope and flips the diff's state to `accepted`; UI re-renders the card as the muted "✓ Committed" state. There is **no client-side optimistic-commit** of the diff content into the surface — the next runtime event (`tool_result` from the surface-side write) is what mutates the surface canonically. UI shows the "✓" pill in the meantime.

### 3.7 Timeline — git semantics + scrub protocol

Per chat1.md L322 and L342-344, the timeline is git: beads = commits, scrub freezes surfaces, chat stays live.

- **Beads.** One bead per state-changing event (every `tool_result` that mutated a surface, plus `final_response`, `run_completed`, `subagent_started/completed`, `approval_resolved`). Read-only events (model_delta, observation, source_ingested) do **not** create beads — they show in Activity tab and in TcChat but don't bump the timeline. Bead color = the surface/subagent it belongs to (per chat1.md L391: "color-coded, smaller dots — the information cost is zero, the readability gain is real").
- **Playhead.** `scrubbedSeq: number | null`. `null` = live, last event. Scrubbing freezes:
  - Surface state at `at_sequence=scrubbedSeq` — requires the time-travel snapshot endpoint (§4.3).
  - Surface tab strip gains "Replay" chip (chat1.md L367, "rightmost item in the app-tabs row").
  - Pending-diff overlay hides during scrub (you're viewing history, not pending work).
- **Chat keeps streaming** (chat1.md L344). The chat column does NOT freeze. New `model_delta`/`tool_result`/`final_response` events from the live run keep arriving; the chat scroll auto-pins to bottom; TcChat renders normally.
- **Controls in TcSwimlanes header** (exists today): ◀ step-back · ▶ step-forward · Now button · Minimize chevron.
- **Controls in TcMiniTimeline** (Focus / Studio-min): click a bead jumps to it · ↩ Now pill snaps live · Expand chevron switches to Studio.
- **Floating "viewing card" on the surface during scrub** — exists today as TcSurfaceMount overlay; carries timestamp, app, action label, and three buttons (chat1.md L342):
  - **Restore this state** — fires `POST /v1/agent/conversations/{conv_id}/restore` (proposed; §4.4).
  - **Branch from here** — fires `POST /v1/agent/conversations/{conv_id}/branches` (proposed; §4.4).
  - **Snap to now** — sets `scrubbedSeq=null`.
- **Keyboard scrub.** Arrow Left/Right cycles through beads when the timeline has keyboard focus (chat1.md L209 stays; the affordance is the timeline itself, not a global shortcut the user must memorize). Escape snaps to now. Tab navigates from the swimlane to bead 0 → bead 1 → … with visible focus rings. This satisfies the §9 accessibility requirement without violating the no-cli-shortcuts rule (chat1.md L383).

### 3.8 Right rail Activity vs swimlane — one projector, two consumers

Same `RuntimeEventEnvelope[]` feeds both:

- **Swimlane** keeps only state-changing events (one bead per change), grouped by `surface_uri`, ordered by `sequence_no`.
- **Activity** keeps the full chronological stream (think/MCP/tool/out/stream), grouped optionally by `subagent_id`, ordered by `sequence_no`.

The projector exposes both views from the same input. No duplication. If a third view needs a third projection (e.g. a Pulse-strip one-liner), add a projection method, not a second store.

## 4. Wire contracts

### 4.1 `CreateRunRequest.reasoning_depth` (already landed)

The api-types `CreateRunRequest.reasoning_depth?: ReasoningDepth | null` already exists at `packages/api-types/src/index.ts:1274`. The ai-backend `RunRequest` schema already mirrors it at `services/ai-backend/src/runtime_api/schemas/runs.py:209`. The runtime applies it via `DepthBudgetTable.apply` (`services/ai-backend/src/agent_runtime/execution/depth.py:96`).

**Status in this phase:** the composer-side `selectedDepth` is local state today (`Composer.tsx:69`); the frontend's submit path (`ChatScreen.tsx:868`) mashes depth into the model selection via `applyDepth(model, depth)` instead of the top-level field. §16 cuts that over.

### 4.2 Event projection contract — `RuntimeEventEnvelope` → UI shapes

The projector (`packages/chat-surface/src/thread-canvas/eventProjector.ts`) maps:

| Envelope `event_type` (api-types L239-282)                                               | SwimlaneBead?                             | ActivityEntry?                                                 | Subagent card?                               | PendingDiff?              |
| ---------------------------------------------------------------------------------------- | ----------------------------------------- | -------------------------------------------------------------- | -------------------------------------------- | ------------------------- |
| `run_started`, `run_completed`, `run_cancelled`, `run_failed`                            | bead (run lifecycle)                      | activity entry (`kind=run-lifecycle`)                          | —                                            | —                         |
| `tool_call_started`, `tool_call_delta`, `tool_call_completed`, `tool_result`             | bead **only when** result mutated surface | activity entry (`kind=tool`)                                   | nested in subagent card if `subagent_id` set | —                         |
| `mcp_*`                                                                                  | bead **only when** mutating               | activity entry (`kind=mcp`)                                    | nested                                       | —                         |
| `approval_requested`                                                                     | bead (with `pending=true`)                | activity entry (`kind=approval`)                               | renders TcInlineDiff inline in subagent body | **YES** — primary source  |
| `approval_resolved`                                                                      | mutates the prior bead to settled         | activity entry (`kind=approval-resolved`)                      | mutates the inline diff                      | removes from pending list |
| `model_delta`, `final_response`                                                          | bead **only** on `final_response`         | activity entry (`kind=stream` for delta, `kind=msg` for final) | streaming bubble in chat                     | —                         |
| `subagent_started`, `subagent_progress`, `subagent_completed`, `subagent_paused/resumed` | bead **only** on started/completed        | activity entry (`kind=subagent`)                               | starts/closes the subagent card              | —                         |
| `reasoning_summary`, `reasoning_summary_delta`                                           | —                                         | activity entry (`kind=think`)                                  | nested as TcThinking block                   | —                         |
| `source_ingested`, `sources_ingested`, `citation_made`                                   | —                                         | activity entry (`kind=source`)                                 | —                                            | —                         |
| `presentation_updated`, `draft_updated`                                                  | bead (surface state changed)              | activity entry                                                 | nested if subagent                           | —                         |
| `observation`, `progress`, `heartbeat`, `compression_note`, `budget_warning`, `error`    | —                                         | activity entry only (status / warnings)                        | —                                            | —                         |
| `adapter_generated`                                                                      | bead (a new surface renderer landed)      | activity entry                                                 | —                                            | —                         |

**Rule:** the projector consumes the envelope's projected `activity_kind` / `display_title` / `summary` / `status` fields (root CLAUDE.md backend rule: "Backend projects events into `activity_kind`/`display_title`/`summary`/`status` for the frontend; do not derive activity types from event-name prefixes"). The mapping above is the projector's branch logic — but the user-visible label always comes from the backend's projection, not from the envelope's event type.

### 4.3 Time-travel surface state (NEW backend deliverable)

Today the runtime emits events with `sequence_no`. Replay is `GET /v1/agent/runs/{run_id}/events?after_sequence=N` (forward-only). To freeze a surface at the "Acme sheet as of 11:43" we need the **derived** state at that point.

**Two options the impl agent must choose between:**

- **A. Client-side replay.** ChatScreen has the full envelope buffer already. The projector reduces all envelopes with `sequence_no <= scrubbedSeq` into the surface state. Pro: zero backend work. Con: each renderer (sheet, email, slide, salesforce) needs a deterministic reducer for "events → state at T". The sheet renderer in particular (rows with formulas) is non-trivial.
- **B. Server-side snapshot endpoint.** Add `GET /v1/agent/runs/{run_id}/surface-snapshot?at_sequence=N&surface_uri=...` that returns the surface payload at that point. Backend reduces using the same reducers used to serve the current state. Pro: surface renderers stay stateless. Con: new endpoint, new tests, requires the runtime to be deterministic in its reductions.

**Recommendation:** **A for Phase 1 if reducers are simple (sheet rows ≤ 50; email body is plain text; slide is a static mock for now); B for Phase 2** when surfaces become non-trivial. The renderers in `packages/surface-renderers/` are mock-grade today (per the existing TcSurfaceMount tests), so A is feasible and unblocks Phase 1 without a backend change.

**If the impl agent picks A**, the contract is: `SurfaceRenderer` declares `reduceTo(envelopes: RuntimeEventEnvelope[], at_sequence: number): SurfacePayload`. The mount calls it with the projector's filtered envelopes when scrubbed; otherwise the renderer reads from the live store.

### 4.4 Branch-from-bead + Restore (NEW backend deliverables)

- **Restore.** "Restore this state" reverts the surface state to `at_sequence=N`, then the run continues live from that point. Surfaces post `tool_result`-style envelopes that the projector turns into beads. This is a **destructive** operation against the surface (per chat1.md L322: "we handle changes like git does it" — restore is `git reset --hard`). New endpoint:
  - `POST /v1/agent/conversations/{conv_id}/runs/{run_id}/restore { at_sequence: N, restored_by_user_id }` — server replays the surface effects forward from N (reversing later-than-N writes via the same MCP write tools); audits via `packages/audit-chain`.
- **Branch.** "Branch from here" creates a **new conversation** rooted at the parent conversation's state at `at_sequence=N`. The original conversation continues; the branched one starts where the user clicked. The current `CreateRunRequest.branch_id` field carries the relationship. New endpoint:
  - `POST /v1/agent/conversations/{conv_id}/branches { from_run_id, at_sequence: N, branched_by_user_id }` returning `{ new_conversation_id, branch_id }`.
- Both are gated behind owner-only authorization (§7).

### 4.5 Approvals — no new wire

Existing endpoints: `POST /v1/agent/approvals/{approval_id}/decisions { decision, decided_by_user_id, reason?, answer?, forward_to? }`. The inline diff card calls `decideApproval()` (existing in `apps/frontend/src/api/agentApi.ts:436+`). No new contract.

## 5. Storage + retention

- **Conversations + messages + runs + events:** persisted in ai-backend (`runtime_adapters/postgres/`). Retention is master PRD §3.3 (default 90d, tenant-configurable). The existing `RuntimeStore` already supports `?after_sequence=N` replay; check the postgres adapter's retention sweeper exists for runs older than `RETENTION_DAYS`. **Action for impl agent:** verify the sweeper exists; if not, file a follow-up.
- **Mode (per-conversation):** client KV only (no server-side mirror needed). Lost on KV clear; default `studio`. **Not** auditable — UI-only.
- **Pin-a-bead:** stretch-goal for Phase 1; if shipped, new table `conversation_pins(conversation_id, run_id, sequence_no, pinned_by_user_id, pinned_at, label)` in ai-backend. Otherwise punt to Phase 2.
- **Branched conversations:** new conversation row in ai-backend with `parent_conversation_id` + `branch_from_sequence_no` columns. Cascade delete: deleting the parent conversation does **not** delete branches (they're independent records; the link becomes orphan-but-readable per audit-chain immutability). Deleting a branch leaves the parent untouched.
- **Restore checkpoints:** every Restore writes a new run event (a synthetic `tool_result` envelope sequence) representing the "rewind" effects. The audit row carries the `at_sequence` reverted to. There is no separate `restore_events` table — append-only event log is the single source of truth.

## 6. Audit (compliance — master PRD §3.2)

Every state change writes to `packages/audit-chain`. The shape:

```
{
  tenant_id,
  actor_user_id,
  action,                  // "approval.accept" | "approval.reject" | "approval.suggest_edit" | "thread.restore" | "thread.branch"
  target_kind,             // "approval" | "conversation"
  target_id,               // approval_id | conversation_id
  before_state,            // for approval: { state: "pending", diff_summary }, for restore: { current_sequence_no }
  after_state,             // for approval: { state: "accepted", commit_sequence_no }, for restore: { at_sequence }
  ts,
  request_id,              // correlation
  context: { run_id, conversation_id, sequence_no? }
}
```

Audit row writers:

- Approval decisions — extend the existing `decideApproval` handler in ai-backend. (Verify it emits an audit row already; if not, add it.)
- Restore — new handler.
- Branch — new handler.

**Not audited (intentionally):**

- Mode switches — UI posture, no compliance footprint.
- Timeline scrub — read-only.
- Open / close right-rail tabs — UI state.
- Composer keystrokes / draft saves — UI state. (Final submit is audited via the existing run-creation pipeline.)

**Compliance check** (from root CLAUDE.md): the audit row must be SIEM-exportable. The audit-chain package already supports the export endpoint; impl agent confirms the new actions appear in exports.

## 7. Authorization

- **Read a thread (run + events + chat).** Owner + workspace members (existing rule; no change).
- **Approve / Reject a pending diff.** Owner-only by default. Per-chat config (`PendingDiff.approver_policy: "owner" | "any_member" | "designated"`) is a Phase 2 add. For Phase 1, ai-backend enforces owner-only — non-owner submission returns 403. Frontend hides Accept/Reject buttons for non-owners; the RightRail Approvals tab shows them but renders the actions as disabled with a tooltip ("Only the thread owner can resolve this approval"). **The backend is the enforcer; UI hints only** (master PRD §3.4).
- **Restore a state.** Owner-only.
- **Branch from a bead.** Any workspace member can branch — the branch is a **new conversation** they own. The original is untouched. This means a teammate viewing the Acme thread can fork it into their own conversation to explore "what if". Owner-only on the original would be too restrictive (chat1.md L301 flags multiplayer; this gives a safe escape hatch). The new branch's owner is the brancher; ACLs reset.
- **Cross-tenant attempt** returns 403 at the facade (existing tenant-isolation rule).

## 8. Pagination + search (replay + Activity feed)

- **Forward replay** (existing): `GET /v1/agent/runs/{run_id}/events?after_sequence=N&limit=200`.
- **Backward / paged-from-tail replay** (NEW for Phase 1): for the Activity tab on long-running threads, the rail loads the last 100 events first then page back. Current API is forward-only.
  - Proposed addition: `GET /v1/agent/runs/{run_id}/events?before_sequence=N&limit=100&order=desc`. Backend adds a paginated reverse-scan path (postgres adapter has the index on `(run_id, sequence_no desc)` already from the SSE replay path; just expose it).
  - Alternative if impl-A wants to keep the wire smaller: front-load the last 200 events on initial mount, fall back to forward replay from `0` only if the user explicitly scrolls "All activity". 200 is enough for Phase 1 threads.
- **Activity tab list virtualization:** when entries > 100, virtualize. Use the same primitive the Inbox destination will need (master PRD §4.1). Impl-B exposes `<VirtualList>` if not already present; otherwise consume an existing solution.
- **Search inside a thread:** out of scope for Phase 1. Note in §20.

## 9. Accessibility (WCAG 2.1 AA — master PRD §3.6)

- **Mode switch.** A `<button>` in the topbar (one button per mode, aria-pressed reflects current; per-button label "Studio mode", "Focus mode", "Auto mode"). No keyboard chord (chat1.md L383). Visible focus ring (design-system token).
- **Streaming content polite live region.** TcChat renders an `aria-live="polite"` region announcing "Atlas is drafting in `<surface>`" when a `stream` activity is active. Re-announce throttled to once per 3 seconds to avoid screen-reader spam.
- **Timeline scrub keyboard-accessible.** Tab to the swimlane; bead 0..N receive focus in order; ArrowLeft / ArrowRight steps; Enter activates; Escape snaps to now. ARIA: each bead is `role="button"` with `aria-label="<HH:MM:SS> · <surface> · <action title>"`.
- **Popovers** (Tools, Model·Depth, Mention) — ARIA dialog pattern: `role="dialog"`, `aria-modal="false"` (they don't trap; click-outside closes), Escape closes, focus returns to the trigger button on close. The existing `Composer.tsx` already implements this; impl-B verifies the tests pin it.
- **Color is not the only state carrier.** Pending diff = green dot + "PENDING" text. Accepted = green dot + "✓ Committed". Rejected = red dot + "× Discarded". Auto-applied = amber dot + "Auto-applied". No state is dot-only.
- **Reduced motion.** The mode-switch grid animation respects `prefers-reduced-motion`: instant snap when set.
- **High-contrast theme.** Existing design-system tokens already support it (`:root[data-theme="high-contrast"]`). Impl-B ensures TcInlineDiff and TcSwimlanes derive every color from tokens, no inline hex.

## 10. Performance

- **ThreadCanvas mounts once per conversationId.** Mode is a prop. **Mode switch is NEVER a remount.** Pinned by a Playwright/RTL test: count renders of `<TcSurfaceMount>` across a Studio→Focus→Auto cycle = 1. Composer renders = 1.
- **Swimlane virtualizes beads** when count > 200. Use a windowed list. Today's 8-bead demo is below the threshold; the test must exercise > 200.
- **Surface renderers lazy-load.** `packages/surface-renderers/` already supports `lazy()` per renderer; the TcSurfaceMount + tier-1 loader code path stays.
- **Event projector is memoized.** `useMemo(() => project(envelopes), [envelopes.length, lastSequence])`. New events append; projector incrementally projects only the suffix.
- **LCP target.** < 2.5s on the canvas's first paint after navigating to an existing thread (broadband, cold cache). Initial fetch is `GET /v1/agent/conversations/{id}` + `GET /v1/agent/runs/{latest_run_id}/events?limit=200` — two parallel requests, no waterfall.
- **INP target.** < 200ms on:
  - Click a bead (scrub frozen in < 200ms).
  - Open the Tools popover.
  - Mode switch (animation starts in < 100ms, completes in 300ms).
  - Accept a diff (button responds; backend round-trip is in-flight; UI shows pending pill).
- **Re-render guards.** The Composer is `memo`'d. ThreadCanvas children are split so a `model_delta` arriving every 50ms re-renders TcChat only, not TcSurfaceMount or TcSwimlanes.

## 11. Telemetry (OpenTelemetry)

Per master PRD §3.8 and root CLAUDE.md.

- `destination=chats`, `action=mode_switch`, attributes `{ from, to, conversation_id, tenant_id, user_id_hashed }`.
- `destination=chats`, `action=approval.decide`, attributes `{ decision, approval_id, surface_uri, conversation_id, run_id }`.
- `destination=chats`, `action=thread.restore`, attributes `{ at_sequence, conversation_id, run_id }`.
- `destination=chats`, `action=thread.branch`, attributes `{ at_sequence, parent_conversation_id, new_conversation_id }`.
- `destination=chats`, `action=timeline.scrub`, attributes `{ scrubbed_sequence, conversation_id }`. **Sampled** at 1/20 — high-frequency event.
- `destination=chats`, `action=right_rail.tab`, attributes `{ from, to }` (Activity ↔ Approvals).

Run lifecycle spans (`run_started`, `run_completed`, etc.) already exist in ai-backend — Phase 1 does not duplicate them.

**PII rule** (root CLAUDE.md): spans **must not** include message bodies, diff content, surface payload bytes, user names, emails, or any free-text the user typed. Approval `reason` strings are PII — they go to audit only, not telemetry.

## 12. States (UX completeness — master PRD §3.10)

| State                                     | Render                                                                                                                                                                       |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Welcome (no thread)                       | Centered card: greeting + Composer (with suggestion grid above — existing ThreadWelcome). No timeline, no rail tabs.                                                         |
| Empty chat (new conversation, no run yet) | Mode = `studio` (default). TcChat shows greeting + ThreadWelcome suggestion grid; surface column shows TcEmpty card; timeline = 0 beads.                                     |
| Loading (run starting / events fetching)  | Skeleton matching the shape — TcChat shows 3 placeholder bubble shapes; TcSwimlanes shows the bead-strip outline; surface shows shimmer. No layout shift on resolve.         |
| Streaming (run active)                    | Pulse on the "● Atlas · drafting in `<surface>`" status row (Focus mode); typing cursor in streaming bubbles (TcChat); live bead glow (timeline).                            |
| Idle (run complete, awaiting user)        | No pulse, no cursor. Composer ready. Diffs in pending state if any.                                                                                                          |
| Error (run failed)                        | Error envelope projected into Activity tab with retry CTA. ChatScreen surfaces the same in the chat as a system bubble with "Retry" button.                                  |
| Cancelled                                 | TcChat shows a "Cancelled at `<HH:MM:SS>`" pill. Timeline keeps the bead with a strike-through. Composer is enabled again.                                                   |
| Restored-from-bead                        | Surfaces frozen at the restore point. Chat shows a system bubble "Restored to `<HH:MM:SS>` by `<user>`". Timeline rewinds the head.                                          |
| Scrubbed                                  | Surfaces frozen at the scrub point (read-only); "Replaying · `<HH:MM:SS>`" overlay on the surface; "Replay" pill on the tabs row; chat keeps streaming live.                 |
| Offline                                   | Banner across the topbar ("You're offline — Atlas continues; some surfaces may not refresh"). Chat falls back to cached envelopes (the SSE buffer that's already in memory). |

## 13. Cross-destination references

Threads ↔ everything:

- **Threads ↔ Projects.** A thread carries optional `project_id`. Cascade: deleting a project soft-deletes (or unfiles) its threads — Phase 2 decision; spec says soft-unfile (threads survive, lose `project_id`). Deleting a thread does NOT delete the project. Threads-canvas-prd ships no schema change; reads existing `Conversation.project_id`.
- **Threads ↔ Inbox.** When a run emits `approval_requested` AND the requester (subagent) is configured to "ask via inbox" (Phase 2), an inbox card lands too. Phase 1 does not auto-create inbox cards — approvals live inline only. Cross-link primitive exists: `InboxItem.thread_id` already in master PRD §5.2.
- **Threads ↔ Todos.** Atlas can extract todos from a thread via a planned subagent skill. Phase 1 does NOT extract todos automatically. Cross-link primitive exists: `Todo.source.thread_id` / `source.run_id` per master PRD §5.3. Phase 1 only commits to NOT breaking this — the `run_id` is stable, the `sequence_no` is stable.
- **Threads ↔ Library.** Citations from a run reference library docs/pages/datasets. Existing `citations` provider in chat-surface already handles this. No Phase 1 change.
- **Threads ↔ Agents.** Runs are attributed to an agent (`run.agent_id` if set). Subagent cards in TcChat already show agent identity. Cross-link primitive exists.
- **Threads ↔ Connectors.** Per-chat connector scope override already exists. ThreadCanvas does not own connector toggling — that's in Composer (Tools popover) and in the connectors destination (master PRD §5.8). No change.

**Cascade rules — deleting a thread:**

- Hard delete removes: run_records, run_events, conversation row, KV `chats.thread.<id>.*` keys.
- Audit rows for prior approvals stay (audit is immutable). Anonymized after `tenant_id` deletion.
- Inbox cards referencing the thread: their `thread_id` becomes a dead link; the UI renders "Thread no longer available" instead of routing. Phase 2 may add a sweeper.
- Todos extracted from the thread keep their `source.thread_id` as a dead link, same rule.
- Library citations referencing the thread's run_id keep the link as orphan-readable.

## 14. Desktop substrate caveats

Per master PRD §3.12 and §2.1: ThreadCanvas + its children **never** import a browser API directly. Audit:

- `window.localStorage` — currently used in thread-canvas.jsx L626-637 (the reference) for `atlas.timelineMin`. Replace with `KeyValueStore` port (already exists). No direct localStorage in chat-surface.
- `window.addEventListener("keydown")` — thread-canvas.jsx L675 uses it for arrow-key scrub. **Allowed** because it's a global event the substrate already wires (web → DOM; desktop → wraps via the same DOM since Electron renders the same React tree). Confirm by checking that desktop's `apps/desktop/` renderer process is just the chat-surface bundle.
- `window.dispatchEvent(new CustomEvent(...))` — thread-canvas.jsx L637 uses it for cross-component signalling. **Refactor to React context** — the Tweaks panel can subscribe via a provider in chat-surface, not via global events. Cleaner anyway.
- `document.addEventListener("mousedown")` — Composer.tsx already does this for click-outside on popovers. Acceptable; same DOM on both substrates.
- `requestAnimationFrame` — composer / textarea autoresize uses it. Both substrates have it.
- `prefers-reduced-motion` media query — Both substrates have it. Use the existing design-system hook if one exists; otherwise add one via the design-system package, not chat-surface.

**No filesystem, no notifications, no clipboard, no OS chrome.** None of the Phase 1 features need them. (Attachments upload via `fetch` to the existing facade endpoint; the file picker is `<input type="file">`, which both substrates have.)

**Composer migration** (§15): the deprecated composer in `apps/frontend/src/features/chat/runtime/composer/Composer.tsx` uses `window.getComputedStyle` and direct `requestAnimationFrame` — both fine. The `attachmentAdapter` shape is browser-agnostic (it takes `File` and produces a pending attachment via a server roundtrip). Migration is substrate-safe.

## 15. Composer migration (the load-bearing decision)

End state: ONE composer in the whole monorepo, at `packages/chat-surface/src/composer/Composer.tsx`. The deprecated one at `apps/frontend/src/features/chat/runtime/composer/Composer.tsx` is **deleted** (along with `EditComposer.tsx` and `index.ts` in that folder).

Each "extra" in the deprecated variant gets a disposition. Justifications follow the staff-engineer test: SIMPLE & ELEGANT, single source of truth, only abstract when there are real duplicates.

| Extra                                                                                                          | Disposition                                                                                                                                                          | Where                                                                                                                                                                 | Why                                                                                                                                                                                                                                                                                                                                                                                                          |
| -------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Selected-skills pills** (the chip row above textarea)                                                        | **Absorbed into Composer** as an optional `topBarSlot` prop.                                                                                                         | `Composer.tsx` gains `topBarSlot?: ReactNode`; ChatScreen passes a `<SelectedSkillsRow>` from chat-surface.                                                           | The skills row is purely visual context for the composer; rendering it elsewhere would split where the user adjusts vs. sees their tools. But the data (`Skill[]`) is product-state owned by ChatScreen → KV; the row is a stateless renderer. Keep the data outside, the render inside.                                                                                                                     |
| **ComposerHandle** (imperative-handle: `setText`, `appendText`, `getText`, `focus`, `addAttachment`, `submit`) | **Absorbed.** The canonical Composer exposes a `ComposerHandle` via `forwardRef`.                                                                                    | `Composer.tsx` switches to `forwardRef`. Add the same handle surface; preserve method names.                                                                          | The skill-picker workspace pane writes into the composer programmatically (`composerHandleRef.current?.setText`). That's a legitimate cross-cutting concern (the picker is _not_ inside the composer). Imperative handle is the simplest interface that works. Don't invent a state-share.                                                                                                                   |
| **Attachments (PendingAttachment + CompleteAttachment, AttachmentAdapter, drag-and-drop)**                     | **Absorbed.** The Composer owns attachment state internally; `AttachmentAdapter` is passed as a prop.                                                                | `Composer.tsx` accepts `attachmentAdapter?: AttachmentAdapter`. Internal state for attachments[]. Optional render slot for attachment pills (uses `topBarSlot` path). | Attachments are part of "what the user is sending" — they belong inside the composer instance. The adapter is host-supplied because it talks to the backend. Keep the adapter shape as it exists in `apps/frontend/src/features/chat/runtime/types.ts`. **Move the type into chat-surface** (`packages/chat-surface/src/composer/types.ts`) since the adapter contract is part of the composer's public API. |
| **ComposerSendButton** (Send vs Stop toggle component)                                                         | **Deleted.** The canonical Composer already has the Send/Stop toggle inline.                                                                                         | n/a                                                                                                                                                                   | Already done in `Composer.tsx` lines 255-278.                                                                                                                                                                                                                                                                                                                                                                |
| **EditComposer** (`apps/frontend/src/features/chat/runtime/composer/EditComposer.tsx`)                         | **Absorbed as a Composer mode**: pass `mode="edit"` + `initialText` + `onSave`/`onCancel`. The same component renders without the Tools/Model row when in edit mode. | `Composer.tsx` gains `mode?: "compose"                                                                                                                                | "edit"`. In edit mode: hide Tools popover + Model picker + attach + mic; keep textarea + Send (relabel "Save"); add Cancel.                                                                                                                                                                                                                                                                                  | The edit composer is structurally the same control — a textarea with a send button — for a different intent. Forking a second component would duplicate the textarea autoresize, the Enter-to-submit, the IME-composing safeguard. Prop-driven variants are the right abstraction here. |
| **ComposerConnectorsButton** (the layers icon next to attach, opens ConnectorPopover)                          | **Hosted at the call site** (not absorbed). The Composer exposes a `inlineActions?: ReactNode` slot that appears between attach and the Tools button.                | ChatScreen passes the connector button into that slot.                                                                                                                | Connectors-per-chat is a product behavior tied to MCP state — ChatScreen owns connectors data. The button is the Composer's UI concern, but the popover content and toggle handlers belong to ChatScreen. Slot-based composition keeps the boundary clean.                                                                                                                                                   |
| **`/` skill shortcut** (typing "/" at word boundary opens skill picker)                                        | **Absorbed.** The hint already advertises "/ skills"; wire the keydown so "/" at word boundary opens an inline `SkillPicker` popover anchored to the textarea.       | `Composer.tsx` adds a "/" detection branch in `handleKeyDown` similar to the existing `@` mention detection.                                                          | The user has been told this works (the hint says so). Honoring the hint is non-negotiable. The skill picker's _data_ (the available skills) is host-supplied as a prop. Same shape as MentionCandidate.                                                                                                                                                                                                      |

**Migration steps (impl-B + impl-C will divide):**

1. impl-B: extend `Composer.tsx` with: `topBarSlot`, `inlineActions`, `forwardRef + ComposerHandle`, `attachmentAdapter` + internal state, `mode="edit" | "compose"`, `/` skill detection + `SkillPicker`. Move types from `apps/frontend/src/features/chat/runtime/types.ts` to `packages/chat-surface/src/composer/types.ts`. Tests pin every existing invariant (hint row always renders, Enter-to-submit, popover ARIA).
2. impl-C: rewrite `apps/frontend/src/features/chat/ChatScreen.tsx` composer mount to use the chat-surface Composer. Remove imports of the deprecated path. Delete `apps/frontend/src/features/chat/runtime/composer/`. Re-run the frontend's full test suite — ALL composer tests must pass (the migration is functionally invariant — same behavior, single source).
3. impl-C: update `ChatScreen`'s connector button to render through the new `inlineActions` slot. Update the edit-message flow to mount `<Composer mode="edit">` instead of `<EditComposer>`.

## 16. `reasoning_depth` wiring

Today (`ChatScreen.tsx:868`):

```ts
model: applyDepth(modelSelectionForId(demoModels, selectedModelId), depth),
```

This bakes depth into the model selection's `reasoning.effort` field — a workaround from before the wire field landed. Now that `CreateRunRequest.reasoning_depth` exists (api-types L1274) and ai-backend accepts it (runs.py L209), depth flows at the top level.

**Spec:**

- The Composer's `selectedDepth: Depth` state stays where it is (component-local). The host passes `initialDepth` in (from ChatScreen state) and receives `onDepthChange` (Composer notifies parent when user picks). The host persists the choice.
- **Default if user never picks:** the runtime treats `null/absent` as default behavior (api-types L1271 confirms: "no regression vs. pre-depth behaviour"). The Composer's `initialDepth` defaults to `"balanced"` (composer.tsx L52 picks balanced when no prior choice). The host can pass `null` to signal "use runtime default"; the Composer renders that as Balanced visually but emits `null` in the run-start payload.
- **Persistence scope:** per-conversation, stored in `KeyValueStore` under `chats.thread.<conversation_id>.reasoning_depth`. Survives reload. Does NOT survive switching conversations (each thread has its own depth pick). When user picks a depth, ChatScreen writes KV and updates state.
- **Cross-conversation default:** a per-user `chats.default_depth` KV key sets the default for new conversations. Setting follows: per-conversation KV → per-user default → `null` (runtime default).
- **Submit path** (`agentApi.createRun()`):
  - Add an option field: `options.reasoningDepth?: ReasoningDepth | null`.
  - Set `payload.reasoning_depth = options.reasoningDepth ?? null` in the request body.
  - Remove the `applyDepth(model, depth)` hack from ChatScreen's call site; pass `reasoningDepth: depth` instead.
  - The model selection is unaffected (the runtime applies depth as a multiplier, not as a model attribute).
- **Tests:** impl-A adds an api-types contract test confirming `reasoning_depth` is allowed null and a `ReasoningDepth` literal. impl-C adds an e2e test: pick "fast", submit, assert `createRun` was called with `reasoningDepth: "fast"` AND no model-level depth mutation.

## 17. Implementation phasing for Wave-2 dispatch

Phase 1 splits into **three impl agents** working in parallel. The orchestrator gates merge ordering: A merges first (types + backend), then B (chat-surface), then C (frontend + cleanup). C cannot land if B is unmerged.

### Impl-A — api-types + ai-backend + audit

**Worktree:** `.claude/worktrees/agent-phase1-impl-a-*` · **Branch:** `worktree-agent-phase1-chats-canvas-impl-a`

Files (boundary-strict):

- `packages/api-types/src/index.ts` — confirm `reasoning_depth` is there (it is); add `BranchFromBeadRequest` + `BranchFromBeadResponse`; add `RestoreToSequenceRequest` + `RestoreToSequenceResponse` if §4.4 endpoints land; add (optional) `EventReplayBackwardRequest` if §8 picks the explicit reverse-paging path.
- `services/ai-backend/src/runtime_api/schemas/` — mirror the api-types additions.
- `services/ai-backend/src/runtime_api/http/routes.py` — new routes for branch + restore (if §4.4 lands in Phase 1; otherwise file as a Phase 2 deliverable and Impl-A scope is just the audit-row work for existing approvals).
- `services/ai-backend/src/agent_runtime/persistence/` — new branch/restore handlers.
- `services/ai-backend/tests/` — full test coverage for: branch (creates new conversation, parent unchanged, audit row written); restore (sequence advanced, audit row written); approval audit row schema.
- `services/backend-facade/src/backend_facade/` — proxy routes for the new endpoints (apps call facade only).
- `packages/audit-chain/` — confirm the action verbs (`approval.accept`, `approval.reject`, `thread.restore`, `thread.branch`) are registered constants.

**NOT in scope for Impl-A:** any frontend code, any chat-surface code.

**Open product question gates Impl-A:** if §4.3 picks server-side time-travel snapshot, Impl-A also adds that endpoint; if client-side, Impl-A is unchanged. Orchestrator picks before dispatch.

### Impl-B — chat-surface ThreadCanvas + RightRail tabs + Composer extras absorbed

**Worktree:** `.claude/worktrees/agent-phase1-impl-b-*` · **Branch:** `worktree-agent-phase1-chats-canvas-impl-b`

Files (boundary-strict; only `packages/chat-surface/src/**` and tests):

- `packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx` — replace today's stub layout with the three-mode grid; mode prop; persistence-aware via host.
- `packages/chat-surface/src/thread-canvas/TcMiniTimeline.tsx` (NEW).
- `packages/chat-surface/src/thread-canvas/TcChat.tsx` — port the design's subagent + thinking + MCP row + diff card rendering, driven by projector output (today's file is a stub).
- `packages/chat-surface/src/thread-canvas/eventProjector.ts` (NEW) + extensive unit tests.
- `packages/chat-surface/src/thread-canvas/modes.ts` (NEW) + `modePersistence.ts` (NEW; uses `KeyValueStore` port).
- `packages/chat-surface/src/shell/RightRail.tsx` — add tabs (Activity, Approvals) when a `tabs` prop is supplied; backward-compatible with today's `children` path (other destinations).
- `packages/chat-surface/src/shell/right-rail/ActivityTab.tsx` (NEW) + `ApprovalsTab.tsx` (NEW).
- `packages/chat-surface/src/composer/Composer.tsx` — extend with `topBarSlot`, `inlineActions`, `mode`, `forwardRef + ComposerHandle`, `attachmentAdapter`, `/` skill detection, `SkillPicker`.
- `packages/chat-surface/src/composer/types.ts` (NEW) — move attachment + adapter + handle types here.
- Tests: ThreadCanvas mode-switch-does-not-remount; projector mapping table; RightRail tabs render correctly; Composer extras (forwardRef, attachments, edit mode); skill `/` shortcut.

**NOT in scope for Impl-B:** any ChatScreen change, any backend change, any api-types change.

### Impl-C — frontend migration + reasoning_depth + ChatScreen cleanup

**Worktree:** `.claude/worktrees/agent-phase1-impl-c-*` · **Branch:** `worktree-agent-phase1-chats-canvas-impl-c`

Files (boundary-strict; only `apps/frontend/**` + the deleted composer path):

- `apps/frontend/src/features/chat/ChatScreen.tsx` — mount `ThreadCanvas` (from chat-surface) when destination=chats + active conversation; mount the canonical Composer with the slots Impl-B exposed; wire ComposerHandle to skill-picker; wire connector button into `inlineActions`; rewrite edit flow to use `<Composer mode="edit">`.
- `apps/frontend/src/api/agentApi.ts` — add `reasoningDepth` option + wire `payload.reasoning_depth`. Remove the `applyDepth(model, depth)` hack from ChatScreen's call site (keep `applyDepth` helper for the model-reasoning.effort field if any model still needs it — otherwise delete).
- `apps/frontend/src/features/chat/runtime/composer/` — **delete the entire folder**.
- `apps/frontend/src/features/chat/runtime/types.ts` — drop the attachment types that moved to chat-surface; keep host-side types.
- Tests: existing composer tests adapt to the canonical Composer; e2e test for the depth-wiring (assert run-start payload carries `reasoning_depth`); e2e test for the welcome → Studio → Focus → Auto cycle preserving chat history.

**NOT in scope for Impl-C:** any new component in chat-surface; any backend change.

**Dependency graph:** A blocks B blocks C. Impl-B may stub the new api-types fields if A has not landed (`as ReasoningDepth | null`); impl-C requires both.

## 18. Open product questions for parth

These are the calls the orchestrator must take before impl agents dispatch. Each is small and concrete.

1. **§4.3 surface time-travel — client-side reducers (option A) or server-side snapshot endpoint (option B)?** Recommended A for Phase 1 (surfaces are mock-grade) and revisit at Phase 2 when richer renderers land. Confirm or override.
2. **§4.4 Restore + Branch endpoints in Phase 1 or Phase 2?** Restore + Branch are headline UX (chat1.md L342) — without them the timeline is read-only and the "git" framing weakens. Recommended **Phase 1** as long as Impl-A has the bandwidth; otherwise punt Branch to Phase 2 and ship a disabled-with-tooltip button in Phase 1.
3. **§7 Approval authorization — owner-only or "any workspace member of the thread"?** Recommended **owner-only** for Phase 1 (safest default). Sub-PRD will revisit per-chat designated-approver in Phase 2.
4. **§8 Backward event paging — explicit `before_sequence` endpoint or "front-load 200, then forward-page" hack?** Recommended **front-load 200** for Phase 1 (smaller blast radius); add the explicit endpoint when Activity feeds for long-running threads start hitting the cap.
5. **§13 Pin-a-bead in Phase 1?** Recommended **no** (out of scope per master PRD §6 phase 1, which is canvas + composer + rail tabs only). Phase 2 add.
6. **§15 EditComposer absorbed into Composer via `mode` prop, or kept as a small standalone wrapper that internally renders Composer with edit-mode props?** Recommended **mode prop** (single component, single mount path). Confirm.
7. **§9 Streaming live-region** — `aria-live="polite"` is the right default, but should we throttle to once per 3s (recommended) or once per surface-change (more chatty)? Confirm 3s.
8. **§3.3 Mode storage scope** — per-conversation (recommended; each thread has its own posture) or per-user-global? Confirm per-conversation.
9. **§5 Branched conversations cascade** — delete branch → parent untouched (recommended); delete parent → branches survive with dead `parent_conversation_id` link (recommended; audit immutability requires keeping the trail). Confirm.
10. **§16 Cross-conversation depth default** — read the per-user `chats.default_depth` KV when a new conversation starts (recommended), or always default to `null` (runtime default) and require explicit pick? Confirm read-from-KV.

## 19. Test plan

Per-file targets and acceptance scenarios.

### Per-file unit tests

- `ThreadCanvas.test.tsx` — mode prop changes layout without remounting children; persistence callback invoked; scrub state survives mode switch.
- `TcMiniTimeline.test.tsx` — click a bead scrubs; ↩ Now resets; Expand chevron calls `onModeChange("studio")`.
- `TcChat.test.tsx` (rebuilt) — projector-driven render of subagent cards + thinking + MCP rows + inline diffs + streaming bubbles; collapse/expand toggles; Accept/Reject calls backend.
- `eventProjector.test.ts` — full mapping table from §4.2 covered; incremental projection (append-only); idempotency on replay.
- `RightRail.test.tsx` (extended) — tabs render only when destination=chats + active thread; Focus mode opens rail by default; tab switch persists; backward-compat for empty-state.
- `ActivityTab.test.tsx` + `ApprovalsTab.test.tsx` — render lists from projector; row click jumps to surface; Approve/Reject buttons disabled for non-owner; pending count in tab label reflects projector output.
- `Composer.test.tsx` (extended) — `forwardRef + ComposerHandle.setText/appendText/submit`; `topBarSlot` renders; `inlineActions` renders between attach and Tools; `mode="edit"` hides Tools/Model + relabels Send→Save; `/` at word boundary opens SkillPicker; hint row always renders; attachment drag-and-drop happy path; AttachmentAdapter `remove` failure does not undo UI removal.

### Cross-file integration

- `ChatScreen.test.tsx` — opens a thread, sends a message, sees streaming, switches Studio→Focus→Auto without state loss; pending diff acceptance updates surface; chat draft survives mode switch.
- `runMutation.test.ts` — `createRun` payload includes `reasoning_depth` when the host sets one; `null` when none.
- Backend (ai-backend) — branch endpoint creates new conversation with parent link; restore endpoint emits the right envelopes; both write audit rows that are SIEM-exportable.

### Acceptance scenarios (manual + e2e)

1. **Welcome → first message → Studio.** New conversation, no thread. Compose "draft the Acme renewal email"; submit. Surface column populates as MCP calls land; chat shows subagent cards; timeline ticks.
2. **Studio → Focus → Auto round trip.** Mid-streaming, switch Studio→Focus; surface stays foreground, composer-only at bottom, mini timeline appears, RightRail opens Activity tab. Switch Focus→Auto; chat fills the canvas, banner up top, pending diffs auto-apply. Switch Auto→Studio; mode prop changes, swimlanes return, unresolved-pre-Auto diffs (none) re-enter pending state. **No remount of surface renderer**; chat scroll restored; composer draft preserved.
3. **Scrub + Restore.** Click a bead at `11:43:02`. Surface freezes; "Replay" pill appears; chat keeps streaming. Click "Restore this state". Surface reverts; chat shows a system bubble "Restored to 11:43:02 by sarah_acme". Audit row exists.
4. **Branch.** Scrub to `11:43:02`. Click "Branch from here". New conversation opens in a new tab; the new conversation's first run is at that sequence; the original is untouched. Audit row exists.
5. **Approval — inline.** Pending diff card in surface. Click Accept. Card flips to "✓ Committed"; surface updates with the committed value from the tool_result envelope; chat-side mirror flips; RightRail Approvals count decrements.
6. **Approval — non-owner.** Open the thread as a non-owner (workspace member, not the run owner). Diff cards render with Accept/Reject disabled; tooltip explains. RightRail Approvals shows the same.
7. **Composer e2e.** Type, attach a file, pick depth=Deep, pick Tools (skill + 2 MCPs), submit. CreateRun payload carries `reasoning_depth: "deep"` AND the model selection AND the tool selection AND the attachment refs. Run completes; depth is reflected in the run's budget metadata.
8. **Composer migration regression.** All ~370 pre-existing composer tests in the frontend either pass against the new canonical Composer or are replaced by the chat-surface tests with equivalent coverage. **No test is silently deleted.**
9. **Welcome / loading / error / cancelled / offline.** Each state renders per §12.

### Non-goals for testing (Phase 1)

- Multi-window concurrency.
- Real-time multiplayer (someone else accepts a diff while you're scrubbing).
- Restore at very deep history (> 10k events). Tests cover 200-event scenarios; 10k is a Phase-2 perf concern.

## 20. Anti-goals for this phase

What we are **not** building in Phase 1:

- Agent marketplace, Memory destination, Library detail, Tools detail — those are master PRD phases 6-11.
- Multiplayer threads. The chat is single-user-view for Phase 1 (chat1.md L301: deferred).
- Mobile native canvas. Desktop ports stay desktop; mobile is post-master-PRD.
- Search inside a thread. ⌘K palette is master PRD phase 12 — not Phase 1.
- Pin-a-bead UI. Phase 2.
- Per-chat designated approver policy. Phase 2.
- A global cross-thread approvals queue. Explicitly killed by chat1.md L313.
- Compose mode. Already killed by chat1.md L319.
- Vim-style keyboard chord-driven mode switching. Killed by chat1.md L383.
- Auto-extracted todos from a thread. Phase 2 (master PRD §5.3).
- Auto-routed inbox cards on approval-requested. Phase 2.
- Server-side surface time-travel snapshots (unless §18 Q1 picks B). Default plan is client-side reducers.

---

## References

- Parent PRDs: [PRD.md](../PRD.md), [destinations-master-prd.md](../destinations-master-prd.md).
- Design: `/tmp/atlas-design/enterprise-search-template/chats/chat1.md` (L240–820 transcript), `project/thread-canvas.jsx`, `project/tc-chat.jsx`, `project/canvas-shared.jsx`, `project/canvas-apps.jsx`, `project/composer.jsx`.
- Existing code: `packages/chat-surface/src/thread-canvas/*`, `packages/chat-surface/src/composer/Composer.tsx`, `packages/chat-surface/src/shell/RightRail.tsx`, `apps/frontend/src/features/chat/ChatScreen.tsx`, `apps/frontend/src/api/agentApi.ts`, `packages/api-types/src/index.ts` (L239-339 events, L1260-1285 depth + run-request, L1325+ envelopes).
- Backend runtime: `services/ai-backend/src/agent_runtime/execution/depth.py` (DepthBudgetTable), `services/ai-backend/src/runtime_api/http/routes.py` (events, stream, decide endpoints), `services/ai-backend/src/runtime_api/schemas/runs.py` (RunRequest reasoning_depth).
- Engineering rules: [CLAUDE.md](../../../CLAUDE.md), [services/ai-backend/CLAUDE.md](../../../services/ai-backend/CLAUDE.md), [apps/frontend/CLAUDE.md](../../../apps/frontend/CLAUDE.md) (composer hint row invariant + planning-pulse invariant), [packages/api-types/CLAUDE.md](../../../packages/api-types/CLAUDE.md).
