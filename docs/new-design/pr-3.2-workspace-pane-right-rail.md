# PR 3.2 — Workspace pane right rail (Sources / Agents / Draft / Approvals / Skills)

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 3, PR 3.2 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** frontend (host + 5 tabs + open/close + responsive) · ai-backend (zero new endpoints — every tab reads off existing surfaces)
> **Size:** **L.** Pure FE composition. No migration. No new event type. No new endpoint. The pane is a re-arrangement of state that already exists.
> **Depends on:** PR 3.1 (Sources tab body) · PR 1.5 (subagent discovery endpoint) · PR 1.3 (drafts artifact endpoint) · PR 1.4 (approvals chain) · existing skills hook.
> **Reads alongside:** [`pr-3.1-citation-chips-sources-tab.md`](pr-3.1-citation-chips-sources-tab.md), [`pr-1.5-subagent-discovery-workspace-feeds.md`](pr-1.5-subagent-discovery-workspace-feeds.md), [`pr-1.3-draft-artifact.md`](pr-1.3-draft-artifact.md), [`pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md), [`pr-2.1-topbar-chrome-thinking-depth.md`](pr-2.1-topbar-chrome-thinking-depth.md), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md)
> **Sibling docs (Wave 3):** PR 3.1 — citation chips + sources tab · PR 3.3 — MCP discovery + approval forwarding polish · PR 3.4 — connector popover

---

## 0 · TL;DR

Five tabs the user already needs. Five data sources we already have. One pane that hosts them.

| Tab       | Data source (already in tree)                                                                        | New code                    |
| --------- | ---------------------------------------------------------------------------------------------------- | --------------------------- |
| Sources   | PR 1.1 `CitationsProvider` registry + PR 3.1 archive read                                            | tab wrapper (~40 LOC)       |
| Agents    | PR 1.5 `GET /v1/agent/conversations/{id}/subagents` + live `subagent_*` events                       | tab wrapper + render rows   |
| Draft     | PR 1.3 `GET /v1/agent/conversations/{id}/drafts` + `DRAFT_UPDATED` events + `POST /drafts/{id}/send` | edit-in-place + send button |
| Approvals | existing thread items filtered by tool name + PR 1.4 `approval_forwarded` event                      | queue projection            |
| Skills    | existing `useSkills()` from `features/skills/`                                                       | tab wrapper                 |

The pane itself is **a single React component** (`<WorkspacePane />`) with five tab bodies. It replaces today's `DetailsPanelHost` overlay (which still exists for slash-command access — both can coexist; see §3.1). It is **collapsible** and **auto-opens** on first source/agent (the user-decided trigger). The state lives in `ChatScreen.tsx` next to `sidebarCollapsed`. Two of the tabs (Sources, Approvals) work entirely off existing FE state; three of them (Agents, Draft, Skills) consume hooks the upstream PRs provide.

LoC estimate: FE ≈ 520 (host + 5 tabs + tabs primitive + responsive logic + tests) · ai-backend ≈ 0 · api-types ≈ 0.

---

## 1 · PRD

### 1.1 Problem

The Atlas design doc is unambiguous: the chat surface has a **right rail** that hosts Sources, Agents, Draft, Approvals, and Skills as tabs, collapsible, default-open when sources/agents exist. Today there is no rail — there's a `DetailsPanelHost` overlay (`/context`, `/usage`, `/sources`) that slides in over the chat, and the rest (drafts, agents, skills, approvals queue) has nowhere to live.

This forces three problems:

1. **Drafts and subagents have no UI home.** PR 1.3 emits `DRAFT_UPDATED`; PR 1.5 exposes `GET …/subagents`. Without a pane, a user has no place to read or edit a draft, no place to monitor a fleet of subagents, no place to "Send to Slack."
2. **Sources lives behind a slash command.** Per PR 3.1 it should be the **default** view when the agent has read documents. Today users have to type `/sources` to see them.
3. **Approvals are scattered.** Each approval is a card inline in the thread. The queue view ("here are all pending approvals across this chat") has nowhere to render.

The pane is the **single artifact host** for everything that isn't conversational text.

### 1.2 Goals

1. **One host, five tabs, collapsible.** A persistent right column in `aui-workspace` that takes its width from the design tokens (`360–420px`) and is pushed off-screen below 1100px (overlay mode) per the design.
2. **Auto-open contract.** First citation or first running subagent in a conversation visit opens the pane on the matching tab. Manual close persists for that conversation visit.
3. **Tab bodies are small and pure.** Each tab is a thin wrapper around a hook or registry the upstream PRs already supply. The pane owns no fetches.
4. **Coexists with `DetailsPanelHost`.** Slash-command users keep `/context`, `/usage`, `/sources`. The pane is the default chrome; the slash-overlay remains a power-user shortcut. Both share the same React tree (the `SourcesPanel` body is mounted from both).
5. **Keyboard parity.** Tab nav with arrow keys; ⌘⇧S/A/D opens specific tabs; Esc closes the pane (matches `DetailsPanelHost`).
6. **Streaming-safe.** Sources, Agents, Draft, Approvals all update from the same SSE stream the chat already reads. No new subscription path.

### 1.3 Non-goals

- **No new event types.** Every signal the pane needs is already on the wire (PR 1.1 / 1.3 / 1.4 / 1.5).
- **No new endpoints.** PR 3.1 ships the sources read; PR 1.3 ships the drafts read; PR 1.5 ships the subagents read. This PR composes them.
- **No splitting `assistant-ui` modes.** The pane lives in the existing `aui-workspace` grid; we don't fork the layout primitive.
- **No promotion of `Tabs` into design-system.** Per [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md), feature-only UI stays in `apps/frontend`. If a second consumer ever appears, promote then.
- **No dragging / resizing the pane in v1.** Width is design-token-driven. Resize is a future polish PR.
- **No "Sources visible to viewer" share toggle.** That belongs to W6 sharing.
- **No `Tweaks panel`** (design says "not part of the shipped product").

### 1.4 Success criteria

- ✅ Pane mounts to the right of the thread body inside `aui-workspace`; collapses to 0px width with a peek-toggle button when closed.
- ✅ Auto-opens on first `source_ingested` (Sources tab) **or** first running subagent (Agents tab) per conversation visit. Honours manual close.
- ✅ Each tab body is < 120 LOC and reads off a single hook / registry. No duplicate fetch.
- ✅ Sources tab parity with `DetailsPanelHost.sources` (PR 3.1) — same rows, same ordering, same chip-click jump.
- ✅ Agents tab updates live as `subagent_started/progress/completed` events arrive (PR 1.5 reducer). Click a row to open the subagent's nested thread (existing `SubagentTool` overlay).
- ✅ Draft tab renders the latest `runtime_drafts` row, supports inline edit + "Send to {connector}" via PR 1.3's `POST /drafts/{id}/send`. Status flips to `sent` on confirmation.
- ✅ Approvals tab lists pending + recent approvals across this chat; clicking jumps to the inline thread card (Atlas's "decision lives in the same artifact" decision).
- ✅ Skills tab reads `useSkills()`; selecting a skill inserts `/<skill>` into the composer (existing path).
- ✅ Below 1100px viewport the pane auto-closes and switches to overlay mode (does not push the thread off-screen).
- ✅ Keyboard: ⌘\ toggles sidebar (existing); **⌘⇧W** toggles the workspace pane; arrow keys navigate the tab list when focused; Esc closes overlay mode.
- ✅ `npm run typecheck` clean; existing `DetailsPanelHost` tests still green.

### 1.5 User stories

| #    | Persona               | Story                                                                                                                                                 |
| ---- | --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | Sarah (research)      | Sources rail opens automatically as Atlas reads docs; I never have to ask for it.                                                                     |
| US-2 | Sarah (drafting)      | When Atlas produces the launch announcement, the Draft tab populates; I edit the headline in place, click "Send to Slack" — approval flow takes over. |
| US-3 | Sarah (multi-tasking) | I dispatch a competitive-frame subagent; the Agents tab shows it running with elapsed time. I keep typing in the chat while it works.                 |
| US-4 | Sarah (decisions)     | Approvals tab shows two pending decisions across this chat; I click "Send draft to #launch-aurora" and jump to the inline card.                       |
| US-5 | Sarah (small screen)  | On my laptop the pane shows beside the chat; on my phone the pane overlays — the thread stays usable.                                                 |
| US-6 | Sarah (intentional)   | I close the pane mid-thread because the chat is the focus. New citations don't re-pop the pane — that was my decision.                                |
| US-7 | Power user            | I still type `/usage` and the existing slash overlay opens; the pane stays in its current state.                                                      |

---

## 2 · Spec

### 2.1 Layout

```
aui-workspace (existing CSS grid)
  ├─ AssistantThreadList  (sidebar, existing)
  ├─ AssistantThread       (chat column, existing)
  └─ WorkspacePane (NEW)   (right column, replaces DetailsPanelHost as default; coexists)
       ├─ <header> with tab strip + close icon-button
       └─ <section> active tab body
```

Grid template (extends the existing `.aui-workspace` rule in `apps/frontend/src/styles.css`):

```css
.aui-workspace {
  grid-template-columns:
    auto /* sidebar */
    minmax(0, 1fr) /* thread column */
    var(--workspace-pane-width, 380px); /* NEW pane column */
}
.aui-workspace[data-pane-open="false"] {
  --workspace-pane-width: 0px;
}
@media (max-width: 1100px) {
  .aui-workspace {
    --workspace-pane-width: 0px; /* overlay below this breakpoint */
  }
  .atlas-workspace-pane[data-overlay="true"] {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    width: min(420px, 100vw);
    z-index: 30;
  }
}
```

Pane internal layout:

```
┌──────────────────────────────────────────────┐
│ [Sources 6][Agents 2 live][Draft][Approvals 1][Skills]   ✕ │
├──────────────────────────────────────────────┤
│                                              │
│   active tab body (scrollable)               │
│                                              │
└──────────────────────────────────────────────┘
```

### 2.2 Components — what we add, what we reuse

| Component                                        | Source                                                                          | Notes                                                                                                                       |
| ------------------------------------------------ | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `WorkspacePane` (NEW host)                       | `apps/frontend/src/features/chat/components/workspace/WorkspacePane.tsx`        | Pure layout shell — owns active-tab state; consumes `useWorkspacePaneState()` for open/close.                               |
| `WorkspaceTabs` (NEW small primitive)            | `apps/frontend/src/features/chat/components/workspace/WorkspaceTabs.tsx`        | ~50 LOC. Roving tabindex, `role="tablist"/"tab"/"tabpanel"`, arrow-key nav. **Not promoted to design-system** (single use). |
| `useWorkspacePaneState` (NEW hook)               | `apps/frontend/src/features/chat/components/workspace/useWorkspacePaneState.ts` | Centralizes `{open, activeTab, openOn(tab, opts), close, manuallyClosedFor}` with per-conversation memory.                  |
| `SourcesTab`                                     | from PR 3.1                                                                     | Re-mounts the existing `<SourcesPanel>` body presentational variant.                                                        |
| `AgentsTab` (NEW)                                | `apps/frontend/src/features/chat/components/workspace/AgentsTab.tsx`            | Reads from `useSubagents(conversationId)` (PR 1.5 hook). Click → existing `SubagentTool` row in thread.                     |
| `DraftTab` (NEW)                                 | `apps/frontend/src/features/chat/components/workspace/DraftTab.tsx`             | Reads from `useDrafts(conversationId)` (PR 1.3 hook). Edit-in-place + "Send to {connector}" → `sendDraft(id)`.              |
| `ApprovalsTab` (NEW)                             | `apps/frontend/src/features/chat/components/workspace/ApprovalsTab.tsx`         | Pure projection over existing thread items (`ChatItem` filter for unresolved approvals + recent resolved). No fetch.        |
| `SkillsTab` (NEW)                                | `apps/frontend/src/features/chat/components/workspace/SkillsTab.tsx`            | Wraps `useSkills()`; click inserts `/<slug>` into composer via existing `useComposerInsert()` (or `aui` insertion).         |
| `useWorkspacePaneAutoOpen`                       | shipped in PR 3.1                                                               | First-ingest signal that calls `openOn("sources")`.                                                                         |
| `useSubagents(conversationId)` (NEW; small hook) | `apps/frontend/src/features/subagents/useSubagents.ts`                          | Single GET on conv switch + reducer over live `subagent_*` events. (Mirrors PR 3.1's archived-sources pattern.)             |
| `useDrafts(conversationId)` (NEW; small hook)    | `apps/frontend/src/features/drafts/useDrafts.ts`                                | Single GET + reducer over `DRAFT_UPDATED` events. Versioned (latest per `draft_id`).                                        |
| `DetailsPanelHost`                               | _existing_                                                                      | **Unchanged.** Still mounts `/context`, `/usage`, `/sources` overlays. Coexists with the pane.                              |

### 2.3 State model

```ts
// useWorkspacePaneState.ts
type WorkspaceTabId = "sources" | "agents" | "draft" | "approvals" | "skills";

interface WorkspacePaneState {
  open: boolean;
  activeTab: WorkspaceTabId;
  openOn(
    tab: WorkspaceTabId,
    opts?: { focus?: { citationId?: string; subagentId?: string } },
  ): void;
  close(reason: "manual" | "viewport"): void;
  manuallyClosedForConversation: ReadonlySet<string>; // not exported, internal
}
```

**Lives in `ChatScreen.tsx`** (lifted from current `detailsPanel` prop). One `useReducer` keyed by `(conversationId, tab)`. The reducer is ~30 LOC. The reason `close()` takes a `reason` is so viewport-driven closes (window shrinks below 1100px) don't poison the manual-close memory.

### 2.4 Tab-body data contracts

#### 2.4.1 Sources

Already specified in PR 3.1. The pane's tab body is the **presentational** form of `SourcesPanel`:

```tsx
<SourcesTab
  citations={citationsForActiveConversation}
  focusCitationId={paneState.focus?.citationId}
  onSelect={(c) => onCiteOpen(c)}
/>
```

#### 2.4.2 Agents

```tsx
const { subagents, loading } = useSubagents(conversationId);
// subagents: SubagentSummary[] from PR 1.5
//   { id, name, status: 'running' | 'done' | 'failed',
//     dispatched_at, elapsed, progress?: number, output_summary?: string }

<AgentsTab
  subagents={subagents}
  loading={loading}
  onOpen={(s) => threadController.scrollToSubagent(s.id)}
/>;
```

Renders rows: bullet (spinner / check / x) → name → 1-line task → progress bar (when present) → elapsed. Click scrolls to the subagent's `<SubagentTool>` block in the thread (existing component) and highlights for 600ms.

#### 2.4.3 Draft

```tsx
const { latestDraft, sendDraft, sending } = useDrafts(conversationId);
// latestDraft: Draft | null from PR 1.3
//   { draft_id, version, title, sections: {h, p}[],
//     target_connector?, target_metadata?, status: 'draft'|'sent'|'discarded' }

<DraftTab
  draft={latestDraft}
  onSend={() => void sendDraft(latestDraft.draft_id)}
  sending={sending}
  onEdit={(patch) => void editDraft(latestDraft.draft_id, patch)}
/>;
```

Edit-in-place uses contenteditable spans on each section's `p`. On blur or Cmd-Enter, the patch flows through `editDraft` (PR 1.3 endpoint, which versions; the optimistic UI lifts the version). Citations are preserved (the chip plugin already runs on draft markdown).

"Send to {connector}" routes through the existing approval flow — `POST /drafts/{id}/send` creates the tool invocation, the `HumanInTheLoopMiddleware` interrupts, the inline approval card renders, the existing approval resolution drives `runtime_drafts.status = 'sent'`.

#### 2.4.4 Approvals

```tsx
const queue = useApprovalsQueue(items, activeRunId);
// Pure projection — no fetch. Reads `ChatItem[]` already in ChatScreen.

<ApprovalsTab
  queue={queue}
  onJumpToCard={(approvalId) => threadController.scrollToApproval(approvalId)}
/>;
```

`useApprovalsQueue` returns `{pending: ApprovalQueueItem[], recent: ApprovalQueueItem[]}`. An item is a tool-call message-part with `toolName === "approval_request"` (or `"mcp_auth_required"` per existing semantics) — `pending` when `result === undefined`, `recent` when resolved within the last 60 minutes. **No new state.** This is data that's already in memory.

#### 2.4.5 Skills

```tsx
const skills = useSkills();
<SkillsTab
  skills={skills.available}
  onPick={(s) => composer.insert(`/${s.slug} `)}
/>;
```

`useSkills()` is the existing hook (`apps/frontend/src/features/skills/useSkills.ts`). Composer insertion uses the existing AssistantUI runtime — see `aui-thread/composer` integration patterns already used by `ComposerPlusMenu`.

### 2.5 Streaming impact — explicitly **none**

| Subsystem                            | Touched?                                                                                                     |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `runtime_events` schema              | **No.** No new event type.                                                                                   |
| `RuntimeEventEnvelope` Pydantic / TS | **No.**                                                                                                      |
| SSE handshake (`?after_sequence=N`)  | **No.** Reconnect identical.                                                                                 |
| `runtime_worker` job loop            | **No.**                                                                                                      |
| `chatModel/eventReducer.ts`          | **No new branches.** Branches added by upstream PRs (1.1 / 1.3 / 1.4 / 1.5) are consumed by their own hooks. |
| Capabilities middleware              | **No.**                                                                                                      |
| Audit chain                          | **No.**                                                                                                      |

### 2.6 Permissions

| Caller                                              | Pane                                                                                                                          |
| --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Conversation owner                                  | Full read + edit (Draft) + send (Draft).                                                                                      |
| Workspace member viewing a shared conversation (W6) | Read-only on every tab. Draft "Send to {connector}" disabled with tooltip. Sources rows respect server-side restriction (W6). |
| Workspace admin                                     | Same as conversation owner.                                                                                                   |

The disabled state widens `chromeDisabled` (PR 2.1) to the pane via prop drilling.

### 2.7 Error semantics

| Condition                                  | UI behavior                                                                                                       |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| Subagents fetch fails                      | Tab shows live data only; banner "Couldn't load past subagents" with retry; live updates still flow.              |
| Drafts fetch fails                         | Tab shows last-known live state; banner with retry. Send button disabled until refetch succeeds.                  |
| Send draft 4xx (e.g. connector revoked)    | Banner with the server message; draft status remains `draft`; row in audit log captured by PR 1.3 already.        |
| Edit draft conflict (version drift)        | Optimistic edit rolls back; banner "Someone else updated this draft — refreshing"; latest version replaces local. |
| Skill list fetch fails                     | Tab shows skills cached from previous load; banner; user can still type `/<slug>` manually.                       |
| Approvals queue empty                      | Empty state ("No pending approvals") + link to last resolved approval (~60 min window).                           |
| Pane open with no conversation selected    | Empty state ("Select or start a chat"); tabs disabled.                                                            |
| Switch conversation while a tab is loading | All in-flight tab loads cancel; new conversation seeds each tab from its hook.                                    |

### 2.8 Accessibility

- `WorkspaceTabs` implements [WAI-ARIA Tabs pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/) — `role="tablist"`, `role="tab"` with `aria-controls`, `role="tabpanel"`, roving tabindex, arrow keys, Home/End. ~50 LOC inline; no Radix dependency.
- Pane host is a `<aside aria-label="Workspace pane">`; collapsed mode uses `aria-hidden="true"`.
- Each tab badge (`Sources 6`, `Agents 2 live`) updates politely (`aria-live="polite"` on the tab strip container).
- Esc inside the pane closes it (overlay mode); within text inputs Esc blurs first then a second Esc closes.
- Reduce-motion: pane open/close uses `transition: width 200ms` which falls back to no transition under `prefers-reduced-motion`.
- Keyboard shortcut **⌘⇧W** toggles the pane; it lives next to existing ⌘\ in the keymap.

### 2.9 What we explicitly do NOT add

- **No `@radix-ui/react-tabs`.** The 50-LOC `WorkspaceTabs` covers our needs without adding a 6 KB dependency.
- **No `react-resizable` / `react-split-pane`.** Width is design-token-driven; resize is future polish.
- **No new design-system primitive.** Everything reusable is already there (`IconButton`, `Badge`, `Card`, `Button`, `StatusPill`).
- **No "expand to full screen" mode.** Outside the design.
- **No drag-to-reorder tabs.** Tabs are fixed.
- **No persisted (cross-session) "last open tab" preference.** Per-conversation memory is in-memory; cross-session belongs to W4.1 Appearance.

---

## 3 · Architecture

### 3.1 Where the pane lives

```
ChatScreen.tsx (existing controller)
   │
   │  paneState = useWorkspacePaneState(conversationId)
   │
   ├── <AssistantThreadList />   (sidebar, existing)
   │
   ├── <AssistantThread …>       (chat column, existing)
   │      ├── <ThreadBody …>
   │      └── (composer)
   │
   └── <WorkspacePane                        ◀── NEW (this PR)
          state={paneState}
          conversationId={conversationId}
          identity={identity}
          citations={citations}
          subagents={subagents}        // hook injected here, see 3.2
          drafts={drafts}
          skills={skills}
          chromeDisabled={…} />
   │
   │  (unchanged, optional)
   └── <DetailsPanelHost kind=… /> when slashCommand !== null
```

`DetailsPanelHost` and `WorkspacePane` both stack to the right of the thread. In default desktop they don't appear simultaneously — slash overlays are full-height; the pane drops to z-0 underneath. In overlay mode (<1100px) only one is visible at a time anyway.

### 3.2 Why the data composition lives in `ChatScreen`, not the pane

`ChatScreen` already owns:

- `conversationId`, `identity`, `items`, `latestRunEvent`, `activeRunId`, `runUiState` (PR 2.1).
- The existing `applyRuntimeEvent` → `setItems` reducer.

The pane's tab data is just **a different projection of the same SSE stream**. Having `ChatScreen` host the hooks (`useSubagents`, `useDrafts`, plus the existing `CitationsProvider`) avoids duplicate subscriptions and ensures **single source of truth per concern**. The pane is a leaf renderer.

```
event reducer (existing)          conversation hooks (PR 1.x → 3.x)
   │                                  │
   ▼                                  ▼
  items[]  ──────────────────►  useApprovalsQueue(items, activeRunId)
  citations registry  ────────►  useCitations(conversationId)
  subagent_* events  ─────────►  useSubagents(conversationId)
  DRAFT_UPDATED event ────────►  useDrafts(conversationId)
                                       │
                                       ▼
                              <WorkspacePane …>
```

Each hook follows the **PR 3.1 archive merge pattern**: one GET on conversation switch, then the live event reducer overlays. ~30 LOC each.

### 3.3 Sequence — auto-open on first source

```
worker streams source_ingested ──► ChatScreen.handleEvent
                                       │
                                       ▼
                            applyCitationEvent (existing)
                                       │
                                       ▼
                            citations registry mutates; useCitations re-renders
                                       │
                                       ▼
                            useWorkspacePaneAutoOpen sees count 0→1
                                       │
                                       ▼
                            paneState.openOn("sources")
                                       │
                                       ▼
                            WorkspacePane re-renders with open=true, activeTab="sources"
                                       │
                                       ▼
                            CSS grid template column expands; SourcesTab renders rows
```

### 3.4 Sequence — user clicks "Send" on a draft

```
DraftTab onClick → sendDraft(draftId)
        │
        │  POST /v1/agent/drafts/{id}/send  (PR 1.3 facade route)
        ▼
ai-backend creates a tool_invocation (existing path via produce-and-send tool)
        │
        ▼
HumanInTheLoopMiddleware interrupts → approval_requested event (PR 1.4)
        │
        ▼  SSE
ChatScreen.handleEvent → adds <ApprovalTool> inline in the thread
        │
        ▼
ApprovalsTab badge increments; the inline card renders the preview
        │
User clicks Approve in inline card; flow proceeds (existing PR 1.4 path);
on resolution the draft `status` flips to 'sent'; DraftTab re-renders state.
```

### 3.5 DRY — what's reused vs. what's added

| Concern                    | Reuse                                                               | Add                                       |
| -------------------------- | ------------------------------------------------------------------- | ----------------------------------------- |
| Sources data               | PR 1.1 + PR 3.1                                                     | tab wrapper                               |
| Agents data                | PR 1.5 endpoint + reducer                                           | tab wrapper + `useSubagents`              |
| Drafts data                | PR 1.3 endpoint + `DRAFT_UPDATED` reducer                           | tab wrapper + `useDrafts` + edit-in-place |
| Approvals data             | existing `ChatItem` array                                           | `useApprovalsQueue` (pure projection)     |
| Skills data                | existing `useSkills()`                                              | tab wrapper                               |
| Activity card primitives   | `<ActivityCard>` (existing) + `<Card>` / `<Badge>` (design-system)  | —                                         |
| Tab strip                  | inline `WorkspaceTabs` (~50 LOC)                                    | —                                         |
| Edit-in-place              | contenteditable + existing markdown serializer (`markdownLinks.ts`) | minimal patch helper (~30 LOC)            |
| Approve / send actions     | PR 1.3 `POST /drafts/{id}/send` + existing `decideApproval`         | —                                         |
| Chip jump (Sources tab)    | PR 3.1 `onSelect`                                                   | —                                         |
| Subagent jump (Agents tab) | existing `SubagentTool` ref + scroll API                            | thin scroll-to helper                     |
| Pane open/close keymap     | PR 2.2 (planned) `useKeymap()`                                      | one binding (`⌘⇧W`)                       |
| Responsive breakpoint      | existing `aui-workspace` media queries                              | one new rule for pane column collapse     |

### 3.6 Dependency survey

- **`@radix-ui/react-tabs`** (~6 KB gz) — mature WAI-ARIA tabs. We pass: 50 LOC of inline tabs is enough for this single-instance use, matching the [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md) "promote when reused" rule. If a second consumer ever needs tabs, promote to design-system + reconsider Radix.
- **`react-resizable-panels`** (~7 KB gz) — for splitting the chat / pane width. Outside scope (no resize in v1).
- **`@floating-ui/react`** — not relevant here. Pane has no popover anchoring.
- **`react-aria` / `react-stately`** — for headless tabs. Same trade-off as Radix; rejected.
- **`@assistant-ui/react`** — already used by the chat. No `Workspace` primitive in their API (verified via the `useAui` / `Suggestions` exports; assistant-ui is conversational UI, not artifact panes). So we keep the pane local.
- **`tldraw`, `react-pdf`, `Lexical`** — none relevant; the Draft tab is a small structured editor for headed sections. contenteditable + a minimal mdast helper (already present in `markdownLinks.ts`) is sufficient.

We add **nothing from npm** in this PR.

### 3.7 Edge cases

| Case                                                                  | Behavior                                                                                                                      |
| --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Pane open mid-stream, user switches conversation                      | Pane stays open; tabs reseed via the per-conversation hook keys; auto-open re-evaluates against the new conv.                 |
| Pane open in overlay mode, user resizes back to ≥1100px               | Pane transitions to push mode; manual-close memory persists.                                                                  |
| Two drafts produced in quick succession (different `draft_id`)        | Draft tab shows the latest by `updated_at`; a small "1 older" pill links to history (PR 1.3 owns the list endpoint).          |
| Draft has no `target_connector`                                       | Send button disabled with tooltip "No target — pick a connector before sending."                                              |
| Subagent endpoint returns rows that aren't in live state yet          | Same merge as Sources: live wins on conflict, archive seeds rows we haven't observed live.                                    |
| `ApprovalsTab` opens with no items                                    | Empty state with link "Open Settings → Notifications" (W4.1).                                                                 |
| Skill list contains 0 skills                                          | Empty state + link to Settings → Skills (existing route).                                                                     |
| User clicks a chip while pane is closed                               | Pane opens on Sources tab; row scrolled into view.                                                                            |
| User clicks a subagent badge while pane is closed                     | Pane opens on Agents tab; row highlighted.                                                                                    |
| Pane open in overlay mode, user types in composer                     | Pane closes on first keystroke (overlay mode policy) so it doesn't obscure the chat.                                          |
| Tab badge count overflows (>99)                                       | Badge renders "99+" (design-system Badge already supports the cap).                                                           |
| Reduce-motion preference                                              | Pane open/close uses `transition: none`.                                                                                      |
| Two tabs share state (Sources + Approvals during streaming)           | Both increment independently; no inter-tab race because each consumes a different hook.                                       |
| Edit-in-place on Draft while a stream is producing new draft versions | Local edits buffer; on `DRAFT_UPDATED` arrival the user sees a banner "New version available — Reload" rather than a clobber. |

### 3.8 Test plan

**Frontend**

- `WorkspacePane.test.tsx` — open / close / overlay mode at three viewport widths.
- `WorkspaceTabs.test.tsx` — ARIA contract; arrow keys; Home/End; click + Enter; roving tabindex.
- `useWorkspacePaneState.test.ts` — `openOn` toggles open + activeTab; `close('manual')` poisons the per-conversation memory; `close('viewport')` does not; switching conversations resets manual close memory; reopen via `openOn` clears the memory.
- `SourcesTab.integration.test.tsx` — see PR 3.1.
- `AgentsTab.test.tsx` — renders running + done subagents; click scrolls to thread row.
- `DraftTab.test.tsx` — edit-in-place patch; send button disabled when no `target_connector`; send happy path triggers approval flow (mock).
- `ApprovalsTab.test.tsx` — pure projection over `ChatItem[]`; pending vs. recent; click jumps to inline card.
- `SkillsTab.test.tsx` — picks invoke composer insert.
- `useSubagents.test.ts` / `useDrafts.test.ts` — archive-merge pattern parity with `useArchivedSources`.

**Cross-service smoke**

- `make test` — extend the launch-announcement scenario to assert pane auto-opens on first source and on first running subagent independently.

### 3.9 Rollout

- **Behind a feature flag** for one release cycle. The flag (`atlas.workspacePane`) is a localStorage boolean toggleable via the existing settings; default-on for `make dev`, default-off for prod until W3 ships end-to-end. Flag check is one line in `ChatScreen`; removal is a one-line cleanup.
- **Backout.** Revert PR; `DetailsPanelHost` is the only remaining surface for these views (`/sources`, `/usage`, `/context`).
- **Migration.** None. Pure FE.

### 3.10 Open questions

1. **Should the pane and `DetailsPanelHost` ever appear simultaneously?** v1: no — slash overlays sit on top, with the pane in z-0 (visible but not focusable while overlay is up). Re-evaluate if user testing flags confusion.
2. **Per-conversation "last open tab" memory** — only across the session in v1. Cross-session belongs to W4.1 Appearance settings.
3. **Drag-to-resize.** Out for v1; design tokens drive width. Future polish behind feature flag.

---

## 4 · Acceptance checklist

- [ ] `apps/frontend/src/features/chat/components/workspace/WorkspacePane.tsx` ships and is mounted from `ChatScreen.tsx` to the right of `<AssistantThread>` inside `aui-workspace`.
- [ ] `apps/frontend/src/features/chat/components/workspace/WorkspaceTabs.tsx` ships with full ARIA tabs pattern and arrow-key nav.
- [ ] `apps/frontend/src/features/chat/components/workspace/useWorkspacePaneState.ts` lifts pane state out of ad-hoc props in `ChatScreen` and exposes `{open, activeTab, openOn, close}`.
- [ ] Five tab files ship: `SourcesTab.tsx` (from PR 3.1), `AgentsTab.tsx`, `DraftTab.tsx`, `ApprovalsTab.tsx`, `SkillsTab.tsx`.
- [ ] `apps/frontend/src/features/subagents/useSubagents.ts` and `apps/frontend/src/features/drafts/useDrafts.ts` ship as the archive-merge hooks for their tabs.
- [ ] CSS in `apps/frontend/src/styles.css`: new column on `.aui-workspace`, overlay rule under 1100px, transition tokens, reduce-motion fallback.
- [ ] `useWorkspacePaneAutoOpen` (PR 3.1) is wired in `ChatScreen.tsx`; auto-opens on first citation; `useSubagents` separately auto-opens on first running subagent.
- [ ] Keymap (PR 2.2) registers `⌘⇧W` to toggle the pane; Esc closes overlay mode.
- [ ] `DetailsPanelHost` continues to mount for `/context`, `/usage`, `/sources` slash commands; both surfaces share the `SourcesPanel` body.
- [ ] No new `runtime_event` type. Pydantic schemas are unchanged.
- [ ] No new endpoint. Facade route table is unchanged (the pane composes existing PR 1.x endpoints).
- [ ] `npm run typecheck --workspace @enterprise-search/frontend` and `npm run build --workspace @enterprise-search/frontend` pass.
- [ ] All upstream PRs' tests still pass.
- [ ] `make test` green.

---

## 5 · References

- [`apps/frontend/src/features/chat/ChatScreen.tsx`](../../apps/frontend/src/features/chat/ChatScreen.tsx) — controller; pane mounts here.
- [`apps/frontend/src/features/chat/components/details/DetailsPanelHost.tsx`](../../apps/frontend/src/features/chat/components/details/DetailsPanelHost.tsx) — coexisting slash-command overlay.
- [`apps/frontend/src/features/chat/components/details/SourcesPanel.tsx`](../../apps/frontend/src/features/chat/components/details/SourcesPanel.tsx) — body re-used as a tab.
- [`apps/frontend/src/features/skills/`](../../apps/frontend/src/features/skills) — Skills tab data source.
- [`apps/frontend/src/styles.css`](../../apps/frontend/src/styles.css) — `aui-workspace` grid + responsive rules.
- [`docs/new-design/01-citations-live-registry.md`](01-citations-live-registry.md) — Sources data model.
- [`docs/new-design/pr-1.5-subagent-discovery-workspace-feeds.md`](pr-1.5-subagent-discovery-workspace-feeds.md) — Agents data model.
- [`docs/new-design/pr-1.3-draft-artifact.md`](pr-1.3-draft-artifact.md) — Drafts data model.
- [`docs/new-design/pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md) — Approvals data model.
- [WAI-ARIA Tabs Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/) — accessibility contract for `WorkspaceTabs`.
- Atlas Design Doc — §"Workspace pane (right rail)", §"Chrome behavior" (auto-collapse rules), §"Approvals as content, not modals".
