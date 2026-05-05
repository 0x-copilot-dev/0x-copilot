# PR 2.1 — Topbar chrome + status pill + thinking-depth

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 2, PR 2.1 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** frontend (chrome) · api-types (one optional field) · ai-backend (read-through; no new endpoints)
> **Size:** **M.** Pure FE composition. Zero migrations, zero new events, one optional `api-types` field, one new run-create input slot already in the contract.
> **Reads alongside:** [`pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md), [`pr-1.5-subagent-discovery-workspace-feeds.md`](pr-1.5-subagent-discovery-workspace-feeds.md), [`pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md), [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md)
> **Sibling docs (Wave 2):** PR 2.2 — sidebar + user card + keymap · PR 2.3 — welcome state + thread polish

---

## 0 · TL;DR

The Atlas topbar replaces today's ad-hoc `aui-chat-header` (a `<select>` + a string status + a Share `<button>`) with the design-doc's chrome: **crumb · title · status pill · connectors pill · usage meter · share · settings · panel toggle · model pill · thinking-depth pill**.

Almost every primitive is **already in `@enterprise-search/design-system`** (`StatusPill`, `IconButton`, `AppIcon`, `ConnectorChip`, `Menu`, `Button`, `Badge`). Almost every behavior is **already in `apps/frontend/src/features/chat/`** (`runUiState` phases → header status; `ConversationConnectorScopes` from PR 1.2 → connectors pill; `useDetailsPanel` `'usage'` → usage overlay; `share` handler → share popover). What's new is the **layout composition**, two new tiny components (`Crumb`, `ThinkingDepthControl`), and one richer `ModelPill` that replaces the old `<select>`.

This PR also fixes a P0 in the design-doc TODO list — _"Thinking-depth control in composer (Fast / Balanced / Deep) — currently model picker conflates speed and reasoning depth"_ — by separating depth from model. Depth maps onto the existing `ModelSelection.reasoning.effort` slot, so **no schema change**.

LoC estimate: FE ≈ 320 (most of which is JSX layout) · api-types ≈ 8 · tests ≈ 220.

---

## 1 · PRD

### 1.1 Problem

Today's chat header (`apps/frontend/src/features/chat/components/thread/AssistantThread.tsx:27-64`) renders:

- a sidebar toggle,
- a `<LogoMark compact>` when sidebar is collapsed,
- a native `<select>` model picker (`ModelSelector.tsx`),
- a free-form status string (`<span class="aui-status-pill">{status}</span>` — _not_ the design-system `StatusPill`),
- a "Share" `<button>` that copies `window.location.href` to the clipboard.

Compared with the design-doc topbar this is missing: crumb (`Workspace › Folder`), title, the _connectors pill with per-chat scope_, the usage meter, the panel toggle, a thinking-depth control, and a richer model pill. The status string also reads unstructured ("Working...", "Stream paused. Reconnecting..."): the design renders it as a 3-tone pill with a pulsing dot.

The **Thinking-depth control** is a separate problem flagged P0 in the design doc:

> _"currently model picker conflates speed and reasoning depth, which makes it hard to reason about when 'Atlas Reasoning' vs 'Atlas Research' matters."_

The runtime already accepts `ModelSelection.reasoning.effort` per request (today plumbed into the ai-backend's `ModelConfig` in [`agent_runtime/execution/models.py`](../../services/ai-backend/src/agent_runtime/execution/models.py)). What's missing is a UI handle for it that doesn't require pulling open the model menu.

### 1.2 Goals

1. The chat header matches the design-doc layout pixel-shape and tokens — built **entirely from primitives already in `@enterprise-search/design-system`** (no churn promoted into the design system in this PR).
2. The status pill is the existing `StatusPill` from design-system, fed by the existing `runUiState.phase` enum (idle | starting | working | acting | writing | reasoning | waiting_for_permission | terminal).
3. The connectors pill renders the **chat's** active connectors (PR 1.2 `enabled_connectors`) — not the user's globally-authenticated set — and clicking it opens the same `ConnectorPopover` that the composer connectors button uses (PR 3.4).
4. The usage meter is a 1-line bar + percentage that reuses the existing `useDetailsPanel('usage')` overlay; no new fetch.
5. The model pill replaces the native `<select>` with a `Menu` popover that shows description + reasoning support + context window per model — same data shape that `ModelCatalogModel` already exposes.
6. The thinking-depth control is a 3-state pill (Fast / Balanced / Deep) that maps to `reasoning.effort = "low" | "medium" | "high"`. It hides itself when the selected model doesn't support reasoning (`supports_reasoning === false`).
7. Streaming behavior is **byte-identical**. No new event. No new SSE handshake. The pill colors and labels are projections of state we already receive.
8. The share button is moved into a popover (per PR 4.5 spec) — but in this PR it remains a one-shot copy-link; PR 4.5 fills the menu with workspace/email/people radios.

### 1.3 Non-goals

- **No new event types.** This PR is presentation only.
- **Per-tool MCP scope toggles** in the connectors pill — those land in PR 4.4 / a later MCP catalog overhaul. The pill in this PR shows server-level state from PR 1.2.
- **Topbar branching / fork buttons.** Branching is a P1 in the design TODO and lives in a future PR.
- **Changing what the share button does.** That's PR 4.5; this PR only relocates the trigger and ensures the popover slot exists.
- **Promoting a `Topbar` primitive into design-system.** Per [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md): "Only promote UI from `apps/frontend` into design-system once it is stable and reusable — used (or clearly needed) in more than one place." The topbar is single-instance.
- **Workspace switcher in the topbar.** The user card in PR 2.2 owns workspace switch.

### 1.4 Success criteria

- ✅ The header renders crumb, title, status pill, connectors pill, usage meter, share button, settings link, panel toggle on the right; sidebar toggle, model pill, thinking-depth pill on the left.
- ✅ Status pill displays the right tone-and-label for every `RunUiPhase`. No string drift between header and the existing `chatRunState.headerStatusForPhase`.
- ✅ Selecting a non-reasoning model hides the depth control (no jitter; CSS `display:none` plus aria-hidden).
- ✅ Selecting "Deep" on the depth control causes the next `POST /v1/agent/runs` to send `model_selection.reasoning.effort = "high"`. Mid-run depth changes do not affect the active run.
- ✅ Connectors pill renders up to 4 chip-glyphs + "+N" when there are more; click opens the per-chat `ConnectorPopover`; toggle round-trips through `PATCH …/conversations/{id}/connectors` (PR 1.2).
- ✅ Usage meter shows a 0–100% bar driven by the existing context-window utilization derived in `UsagePanel`/`ContextPanel`. Click opens the existing `'usage'` `DetailsPanelHost`.
- ✅ The chrome remains accessible: `aria-live="polite"` on the status pill, `aria-controls` from each trigger to its popover/panel, every `IconButton` carries a `data-tooltip`, focus order matches reading order.
- ✅ Below 1100px viewport the workspace pane auto-closes (per design); below 820px the sidebar auto-collapses (already wired). Topbar wraps two rows on <760px (small-screen polish).

### 1.5 User stories

| As…              | I want…                                                                       | So that…                                                                |
| ---------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Sarah (end user) | to see at a glance which model + depth is running mid-thread                  | I don't have to re-open the composer menu to remember                   |
| Sarah            | to flip from Balanced to Deep without giving up my current model              | I'm choosing a depth, not a different model                             |
| Sarah            | a status pill that reads "Waiting for permission..." when an approval is open | I know whether the silence is "thinking" or "blocked on me"             |
| Sarah            | to pause Slack from the topbar mid-thread and have only this chat affected    | yesterday's launch noise stays out of today's investigation             |
| Marcus (admin)   | the usage meter to surface near-cap state visually                            | I notice context filling up before the agent compacts memory mid-stream |
| Future-2.2 / 2.3 | the topbar to expose the same shared state the sidebar / welcome consume      | sidebar and welcome stay decoupled but read the same `RunUiState`       |

---

## 2 · Spec

### 2.1 Layout

```
┌───────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│  [⌘\ panel]  Logo                                                                                          │
│  ◧ Acme · Marketing › Launches              ╭───────────────────────────────────────────╮                  │
│  ─────────────                              │  [● Running]  [N G S +2 ▾]  [▮▮▮▮▯▯ 64%]   │  Share  ⚙  ◫    │
│  FY26 Q1 launch announcement draft          ╰───────────────────────────────────────────╯                  │
│                                                                                                            │
│  [⏵ Atlas Reasoning ▾]   [◐ Balanced ▾]                                                                    │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- Two-row header on default desktop (≥ 1100px). The first row carries identity (crumb + title) and global state (status pill, connectors pill, usage meter, share, settings, panel toggle); the second row carries per-run controls (model + thinking-depth) — separate so the model/depth pills sit closer to the composer they affect.
- One-row collapse on ≥ 760 px < 1100 px: model + depth pills inline at the right of the first row, panel toggle dropped (workspace pane auto-closes anyway).
- One-row collapse on < 760 px: crumb hidden, title truncates, status pill becomes a dot-only icon, model + depth become a single combined `Atlas Reasoning · Balanced ▾` pill. (This last collapse is mobile only and lives in CSS.)

### 2.2 Components — what we add, what we reuse

| Component                                          | Source                                                                              | Notes                                                                                                                                       |
| -------------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ----- | --------------------------------------- |
| `Topbar` (new layout shell)                        | `apps/frontend/src/features/chat/components/shell/Topbar.tsx` _(new)_               | Pure layout. Owns no state.                                                                                                                 |
| `Crumb`                                            | `apps/frontend/src/features/chat/components/shell/Crumb.tsx` _(new)_                | `Workspace › Folder` from `Conversation.folder` (PR 1.6). Single line, clipped 220 px. Falls back to workspace-only when folder is null.    |
| `ConversationTitle`                                | `apps/frontend/src/features/chat/components/shell/ConversationTitle.tsx` _(new)_    | One line, 13 px. Editable on dbl-click → `PATCH …/conversations/{id}` (PR 1.6 endpoint already wired).                                      |
| `StatusPill`                                       | `@enterprise-search/design-system` (existing)                                       | Tone = `running                                                                                                                             | ready | idle`. Already CSS-pulses on `running`. |
| `ConnectorsPill`                                   | `apps/frontend/src/features/chat/components/shell/ConnectorsPill.tsx` _(new)_       | Wraps `AppIcon × N`, ▾ caret, click → `ConnectorPopover` (PR 3.4). Subscribes to `useConversationConnectorScopes(conversationId)` (PR 1.2). |
| `UsageMeter`                                       | `apps/frontend/src/features/chat/components/shell/UsageMeter.tsx` _(new)_           | 1-line bar + %; click → `useDetailsPanel().open('usage')` (existing).                                                                       |
| `ShareButton` (slot)                               | reuses existing `onShare` in `ChatScreen.tsx:564` for v1                            | Same one-shot copy-link in this PR; PR 4.5 replaces the body of the popover.                                                                |
| `IconButton` for ⚙ (settings) and ◫ (panel toggle) | `@enterprise-search/design-system`                                                  | Settings click → `applyAppRoute({screen:'settings', section:'general'})` (existing).                                                        |
| `ModelPill`                                        | `apps/frontend/src/features/chat/components/shell/ModelPill.tsx` _(new)_            | Replaces `ModelSelector.tsx`. Uses `Menu` from design-system + the existing `ModelCatalogModel[]` from `demoModels`.                        |
| `ThinkingDepthControl`                             | `apps/frontend/src/features/chat/components/shell/ThinkingDepthControl.tsx` _(new)_ | 3 chips. Mapped to `reasoning.effort`. Hidden when `model.supports_reasoning === false`.                                                    |

Existing files we **delete or shrink**:

- `AssistantThread.tsx` — keep the file (still hosts the section), but the `<header>` block is replaced by `<Topbar … />`. Footer / body layout untouched.
- `ModelSelector.tsx` — superseded by `ModelPill.tsx`. Delete file; remove import.

### 2.3 Wire — what (if anything) changes on the network

#### 2.3.1 Run create — depth-aware

Today `ChatScreen.submitUserMessage` calls:

```ts
const run = await createRun(conversationId, text, identity, {
  model: modelSelectionForId(demoModels, selectedModelId),
  …
});
```

`modelSelectionForId` returns `{ provider, model_name, reasoning }` where `reasoning` carries whatever the model row hard-codes. After this PR, the topbar carries selected `depth` state and feeds it through:

```ts
const run = await createRun(conversationId, text, identity, {
  model: applyDepth(modelSelectionForId(demoModels, selectedModelId), depth),
  …
});

// applyDepth:
function applyDepth(selection: ModelSelection, depth: ThinkingDepth): ModelSelection {
  if (depth === undefined || !selection.reasoning?.enabled) return selection;
  return {
    ...selection,
    reasoning: { ...selection.reasoning, effort: EFFORT_BY_DEPTH[depth] },
  };
}

const EFFORT_BY_DEPTH = { fast: 'low', balanced: 'medium', deep: 'high' } as const;
```

The runtime already accepts `reasoning.effort` per call (`agent_runtime/execution/models.py` resolves it into the worker's `RuntimeContext`). **No backend change required.**

#### 2.3.2 Status pill — read-only projection

`StatusPill` consumes the existing `RunUiState.phase` (from `chatRunState.ts`). The pill tone mapping is one switch table:

```ts
const TONE_BY_PHASE: Record<RunUiPhase, StatusTone> = {
  idle: "idle",
  starting: "running",
  working: "running",
  acting: "running",
  writing: "running",
  reasoning: "running",
  waiting_for_permission: "ready", // amber-ish in design tokens
  terminal: "ready",
};
```

`RunUiState.headerStatus` is the label. No re-projection — same string the user sees today.

#### 2.3.3 Connectors pill — uses PR 1.2 hook

```ts
const { scopes, setScopes } = useConversationConnectorScopes(conversationId);
const activeIds = Object.entries(scopes)
  .filter(([, v]) => v !== null)
  .map(([k]) => k);
```

Up to 4 `AppIcon`s render in z-stack with negative margin; the rest collapse into `+N`. Click opens `ConnectorPopover` (PR 3.4) anchored below.

#### 2.3.4 api-types — one optional field added, nothing renamed

```ts
// packages/api-types/src/index.ts (extend existing ModelCatalogModel)
export interface ModelCatalogModel {
  // … existing fields
  reasoning?: {
    enabled: boolean;
    effort?: "low" | "medium" | "high";
    summary?: "auto" | "off";
    /** Optional human-friendly label. When absent, FE uses the depth name. */
    depth_label?: string;
  } | null;
}
```

`depth_label` is optional and additive; existing consumers ignore it.

### 2.4 State

The Topbar is **derived state only**. It owns no fetches. Its inputs:

| Input                     | Source                                                                  |
| ------------------------- | ----------------------------------------------------------------------- |
| `conversation`            | `ChatScreen` already has the active `Conversation`                      |
| `runUiState`              | already computed via `deriveRunUiState` in `ChatScreen.tsx:689`         |
| `models`, `selectedModel` | `ChatScreen` has both today                                             |
| `depth`                   | new local state in `ChatScreen` — `useState<ThinkingDepth>('balanced')` |
| `connectorScopes`         | new hook `useConversationConnectorScopes(conversationId)` (PR 1.2)      |
| `usagePct`                | new selector over the existing `RunUsage` from `usagePanel` data        |
| `panelOpen`               | new local state — controls workspace pane (PR 3.2)                      |

**State that does NOT live here:** the active connectors set (PR 1.2 server), the conversation title (PR 1.6 server), the model catalog (server), the usage breakdown (`UsagePanel`).

### 2.5 Streaming impact — explicitly **none**

| Subsystem                            | Touched?                                                             |
| ------------------------------------ | -------------------------------------------------------------------- |
| `runtime_events` schema              | **No.** Zero new event types.                                        |
| `RuntimeEventEnvelope` Pydantic / TS | **No.**                                                              |
| SSE handshake (`?after_sequence=N`)  | **No.** Reconnect identical.                                         |
| `runtime_worker` job loop            | **No.**                                                              |
| `chatModel/eventReducer.ts`          | **No.** Topbar reads `runUiState`, which already exists.             |
| Capabilities middleware              | **No.** `reasoning.effort` is already plumbed; this PR just sets it. |
| Audit chain                          | **No.**                                                              |

The only protocol-level change is **depth → effort mapping inside `createRun`**, which is a client-side translation of an already-supported request field.

### 2.6 Permissions

| Caller                                                        | Topbar action                                                                                      |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Conversation owner                                            | full — can change model, depth, connector scopes, title; open share, settings, usage               |
| Other workspace member viewing a shared conversation (PR 6.1) | read-only chrome — model/depth/connector/share controls disabled with tooltip explaining read-only |
| Workspace admin                                               | same as conversation owner (admin-override is per-row, not per-pill)                               |

The disabled state for shared-read uses the existing `ChatScreen.modelDisabled` pattern; we widen it to `chromeDisabled` and apply to all interactive pills.

### 2.7 Error semantics

| Condition                                                       | UI behavior                                                                                                                                                   |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Model that doesn't support reasoning is selected                | Depth control hidden; if `depth` was set, it persists in local state but is not sent.                                                                         |
| `useConversationConnectorScopes` fetch fails                    | Connectors pill shows the user's globally-authenticated set with a `data-stale` flag; clicking still opens the popover, which surfaces the error in its body. |
| `usagePct` is unknown (no run in flight, no past runs)          | Meter renders empty bar, label "—", click still opens the panel.                                                                                              |
| `PATCH …/conversations/{id}` (rename via dbl-click) returns 4xx | Title rolls back; toast in the existing notification surface.                                                                                                 |
| Run is in `waiting_for_permission`                              | Status pill amber; ⌘↩ from PR 2.2 keymap focuses the inline approval card.                                                                                    |
| User changes depth mid-run                                      | Local state updates; status pill flashes "(applies to next message)" for 2s; the active run is unaffected.                                                    |

### 2.8 Accessibility

- Status pill carries `role="status"` + `aria-live="polite"`. Designed to be polite (no interrupting screen reader during streams).
- Each interactive pill is a `<button>` with `aria-haspopup="menu"`, `aria-expanded`, `aria-controls=<menu_id>`.
- Tooltips reuse the existing `data-tooltip` attribute pattern from `apps/frontend/src/styles.css`.
- Keyboard order: sidebar-toggle → crumb (skip; non-interactive) → title (Enter to edit) → status pill (Enter opens an explainer popover with full status text) → connectors pill → usage meter → share → settings → panel toggle → model pill → thinking-depth pill → composer.
- Reasoning effort change emits an `aui-status-pill` polite text "Depth: Deep — applies to next message." for one cycle.
- Depth control is a **radio group** (`role="radiogroup"`, three `role="radio"` items) — not three independent buttons. Standard arrow-key navigation comes for free.

### 2.9 What we do NOT add

- **No new design-system primitive.** Everything that's reusable already lives there. Topbar parts are app-feature components by [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md) policy.
- **No third-party dropdown library.** `Menu` already has mousedown-outside dismissal and Escape close. (Surveyed: Radix UI's `DropdownMenu` would add ~30 KB gzipped for one feature; `Menu` is already used by the composer plus-menu.)
- **No popover-position library.** Auto-flip is a real concern only for the composer's connectors button (anchors at the _bottom_ of the viewport — see PR 3.4). Topbar pills always anchor below; no flip needed. (We considered `@floating-ui/react` ≈ 18 KB gz — deferred until a single consumer needs it.)
- **No global keyboard handler in this PR.** Keymap is PR 2.2.

---

## 3 · Architecture

### 3.1 Where Topbar lives in the system

```
                          ┌──────────────────────────────────────────────────────────┐
                          │  apps/frontend/src/features/chat/ChatScreen.tsx          │
                          │  (existing controller — already owns conversation,       │
                          │   activeRunId, latestRunEvent, identity, models,         │
                          │   selectedModel, runUiState, …)                          │
                          └──────────────────────────────┬───────────────────────────┘
                                                         │ props
                                                         ▼
                                          ┌──────────────────────────┐
                                          │ AssistantThread (existing)│
                                          │  hosts <Topbar /> + body  │
                                          └────────┬─────────────────┘
                                                   │
                                                   ▼
                                          ┌──────────────────────────┐
                                          │ Topbar (NEW)              │
                                          │  pure layout, no fetches  │
                                          └─┬───────┬───────┬───────┬─┘
                                            │       │       │       │
                                            ▼       ▼       ▼       ▼
                          design-system   StatusPill  IconBtn  AppIcon  Menu (popover host)
                            (existing)        ▲         ▲        ▲       ▲
                                            tone     hover     letter   anchored, mousedown-outside
                                            from   tooltip    from     dismissal already in place
                                          runUiState         connector
                                                              scope
                                                               │
                                                               │ PR 3.4 popover body
                                                               ▼
                                                       ConnectorPopover
                                                         (mounted by PR 3.4;
                                                          this PR just opens/closes it)
```

### 3.2 How the four state inputs flow into the topbar

```
   user types prompt
        │
        ▼
   ChatScreen.submitUserMessage()
        │  POST /v1/agent/runs   { model: applyDepth(model, depth) }
        ▼
   ai-backend RunService.create_run()
        │  freezes ModelConfig (provider, model_name, reasoning) into runtime_context_json
        ▼
   worker claims run, builds DeepAgent, streams events
        │
        ▼  SSE                                     (existing)
   ChatScreen.handleEvent  →  setLatestRunEvent
        │
        ▼
   deriveRunUiState({activeRunId, items, latestEvent})  (existing)
        │
        ▼
   Topbar.props.runUiState     ──►  StatusPill tone + label

   independently:                                    (existing PR 1.2 server)
   useConversationConnectorScopes(conversationId)
        │
        ▼
   Topbar.props.connectorScopes ──►  ConnectorsPill render

   independently:                                    (existing UsagePanel sources)
   useUsageSnapshot(conversationId, runId?)
        │
        ▼
   Topbar.props.usagePct        ──►  UsageMeter bar
```

**Single source of truth per concern.** ChatScreen still owns the integration; Topbar is a leaf.

### 3.3 DRY — what we reuse vs. what we add

| Concern                         | Reuse                                                                                                       | Add                                                                                                                  |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Status semantics                | `chatRunState.deriveRunUiState`, `headerStatusForPhase` (`apps/frontend/src/features/chat/chatRunState.ts`) | one `TONE_BY_PHASE` table (~10 LOC)                                                                                  |
| Status visual                   | `StatusPill` (`packages/design-system/src/index.tsx:266`)                                                   | —                                                                                                                    |
| Connector scope                 | `useConversationConnectorScopes` + `ConnectorPopover` (PR 1.2 + PR 3.4)                                     | `ConnectorsPill` thin wrapper (~40 LOC)                                                                              |
| Usage data                      | existing `UsagePanel.tsx` selectors                                                                         | `UsageMeter` component (~30 LOC)                                                                                     |
| Usage overlay                   | existing `useDetailsPanel('usage')` open path                                                               | one click handler                                                                                                    |
| Share                           | existing `onShare` clipboard handler in `ChatScreen.tsx`                                                    | trigger button (~10 LOC); popover body lands in PR 4.5                                                               |
| Settings nav                    | existing `applyAppRoute` in `App.tsx:175`                                                                   | one click handler                                                                                                    |
| Panel toggle                    | new local state in `ChatScreen` (`workspacePaneOpen`); workspace pane host lands in PR 3.2                  | one boolean + one ⌘\ shortcut (in PR 2.2) + one toggle button                                                        |
| Model select shape              | `ModelCatalogModel` (`@enterprise-search/api-types`)                                                        | one optional `reasoning.depth_label` field                                                                           |
| Reasoning effort wire           | `ModelSelection.reasoning.effort` already accepted by `RunService`                                          | `applyDepth` translation + one `useState`                                                                            |
| Menu / popover                  | `Menu` (`packages/design-system/src/index.tsx:365`)                                                         | —                                                                                                                    |
| Icon glyph                      | `IconButton` (`packages/design-system/src/index.tsx:240`)                                                   | —                                                                                                                    |
| App glyph                       | `AppIcon` (`packages/design-system/src/index.tsx:290`)                                                      | —                                                                                                                    |
| Theming                         | `ThemeProvider` already exposes `accent` and persists it                                                    | —                                                                                                                    |
| Tooltips                        | existing `data-tooltip` CSS pattern (`apps/frontend/src/styles.css`)                                        | —                                                                                                                    |
| Persisting depth across reloads | localStorage helper used by `ThemeProvider` (~12 LOC of inspiration)                                        | one `useLocalStorageState<ThinkingDepth>('atlas:depth', 'balanced')` (~20 LOC; placed in `apps/frontend/src/utils/`) |

**Net new code is layout + a translation table.** Mostly JSX. ≈ 320 LOC excluding tests.

### 3.4 No third-party dependency added

Surveyed:

- **`@radix-ui/react-dropdown-menu` / `@radix-ui/react-popover`** — beautiful semantics, but `Menu` already covers the common case and `@floating-ui/react` (their dependency) would be overkill for top-anchored popovers that don't need flip logic. ~30 KB gz.
- **`react-aria` / `react-stately`** (Adobe) — comprehensive but pulls a 60+ KB tree for one accessible radio-group. We get the same a11y from a tiny inline `role="radiogroup"` + arrow-key handler.
- **`use-text-resize-observer`** — for the title's clipping behavior. CSS `text-overflow: ellipsis` plus `max-width` already handle this; observers are needed only when we need to know the truncation state, which we don't here.
- **Reasoning-effort presets library (none exists)** — the depth-to-effort mapping is three rows, hard-coded.

We add nothing from npm in this PR.

### 3.5 Sequence — user flips depth from Balanced to Deep mid-thread

```
Sarah                        Topbar                          ChatScreen                         worker
  │                            │                                │                                  │
  │ click "Deep"               │                                │                                  │
  │ ─────────────────────────► │                                │                                  │
  │                            │ onDepthChange("deep")          │                                  │
  │                            │ ──────────────────────────────►│ setDepth("deep") +              │
  │                            │                                │ persist to localStorage         │
  │                            │                                │                                  │
  │                            │  StatusPill flashes a polite "(applies to next message)" hint     │
  │                            │  via aria-live, no visual modal                                   │
  │                            │                                │                                  │
  │ types and sends prompt                                       │                                  │
  │ ─────────────────────────────────────────────────────────►  │                                  │
  │                                                              │ POST /v1/agent/runs              │
  │                                                              │ { model: { provider, model_name, │
  │                                                              │             reasoning: {        │
  │                                                              │               enabled: true,    │
  │                                                              │               effort: "high"    │
  │                                                              │             } } }               │
  │                                                              │ ───────────────────────────────►│ creates run
  │                                                              │                                  │ freezes ModelConfig
  │                                                              │                                  │ runtime_context_json
  │                                                              │                                  │ ◄── claim ──
  │                                                              │  SSE events (existing — no change)
  │                                                              │ ◄────────────────────────────── │
  │                                                              │                                  │
  │ at run end, depth stays "deep" for the next prompt unless the user changes model.               │
```

### 3.6 Edge cases

| Case                                                                               | Behavior                                                                                                                                                   |
| ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| User selects a non-reasoning model while `depth === 'deep'`                        | Depth control hides; depth state persists in storage. Switching back to a reasoning model restores the chip selection.                                     |
| Model catalog returns a row with `supports_reasoning: false` and `reasoning: null` | Depth control hidden; `applyDepth` no-op.                                                                                                                  |
| `runUiState.phase === 'reasoning'` but model doesn't advertise reasoning           | Status pill still shows "Thinking..." — that's a server projection (`headerStatusForPhase('reasoning')`). FE renders truthfully.                           |
| Conversation switches mid-stream (PR 2.2 disables it; this case shouldn't fire)    | Depth state persists; topbar re-renders against the new conversation's connectors / title / scopes.                                                        |
| `conversationId === null` (pre-create)                                             | Connectors pill consults workspace defaults from PR 1.6 (`workspace_defaults.default_connectors`); usage meter renders "—".                                |
| Reduce-motion preference set                                                       | StatusPill `running` dot stops pulsing; usage meter bar fills via `transition: none`. (Falls out of existing token CSS; one media query.)                  |
| Title rename collides with an in-flight save                                       | Optimistic update in the FE; the ai-backend route is idempotent (PR 1.6 PATCH); last-write-wins.                                                           |
| Depth changed during a run                                                         | Local state updates immediately. The next `POST /v1/agent/runs` carries the new effort. Active run is unaffected. We do **not** apply depth retroactively. |

### 3.7 Test plan

Lives in the same PR.

**Frontend (`apps/frontend/src/features/chat/components/shell/`)**

- `Topbar.test.tsx` — composition snapshot at three viewport widths.
- `StatusPill.integration.test.tsx` — every `RunUiPhase` produces the expected tone + label.
- `ConnectorsPill.test.tsx` — renders 0 / 1 / 4 / 7 chips correctly; "+N" appears at 5+; click opens popover.
- `UsageMeter.test.tsx` — 0 %, 64 %, 100 %; click opens `details` panel via the existing host.
- `ModelPill.test.tsx` — keyboard navigation, disabled rows, persisted selection.
- `ThinkingDepthControl.test.tsx` — radiogroup ARIA contract, hidden on non-reasoning models, applied on next run-create.
- `applyDepth.test.ts` — pure function table-test.
- `useLocalStorageState.test.ts` — load / write / clear; SSR safety.

**Existing tests we update**

- `AssistantThread.test.tsx` — the old `<header>` snapshot is replaced with a Topbar mount assertion.
- `ChatScreen.test.tsx` — model + depth combo lands in `createRun` calls (one test per case).

**Cross-service smoke (`make test`)** — unchanged. Topbar is FE-only.

### 3.8 Rollout

- **Flag-free.** Topbar replaces the existing header in one PR. There's no parallel-running mode. The replaced header is small and obviously inferior.
- **Zero migration.** Pure FE change.
- **Backout.** Revert the PR. `ModelSelector.tsx` returns; the design-system `StatusPill` is unused but harmless.

### 3.9 Open questions

1. **Where does the title rename live — topbar or sidebar?** Both surfaces show the title. v1: topbar dbl-click is the primary edit affordance; sidebar shows the read-only label. Revisit if testing shows people expect to right-click in the sidebar instead.
2. **Should "Settings" open in a side-overlay rather than full route?** The Atlas design opens it as a full page (`Settings.html`), so this PR keeps the existing full-page route. Revisit when settings density warrants a slide-over.
3. **Sticky behavior on scroll.** The topbar today is sticky; we keep it. For >2 row collapse modes we may need a "shrink on scroll" pattern — out of v1.

---

## 4 · Acceptance checklist

- [ ] `apps/frontend/src/features/chat/components/shell/Topbar.tsx` ships and replaces the `<header>` block in `AssistantThread.tsx`.
- [ ] `Crumb`, `ConversationTitle`, `ConnectorsPill`, `UsageMeter`, `ModelPill`, `ThinkingDepthControl` ship as small, single-responsibility components in the same folder.
- [ ] `StatusPill` from `@enterprise-search/design-system` is consumed; the ad-hoc `aui-status-pill` `<span>` is gone.
- [ ] `ModelSelector.tsx` is deleted; no remaining import.
- [ ] `applyDepth(model, depth)` is exercised by `submitUserMessage` and `onReload`; depth changes never alter an active run.
- [ ] `ThinkingDepth` enum (`'fast' | 'balanced' | 'deep'`) and the `EFFORT_BY_DEPTH` table live in **one** module (`apps/frontend/src/features/chat/depth.ts`).
- [ ] Depth state persists via `useLocalStorageState` and survives reload.
- [ ] `ModelCatalogModel.reasoning.depth_label` exported by `@enterprise-search/api-types`; existing call sites compile unchanged.
- [ ] No new `runtime_event` type. Pydantic schemas in `services/ai-backend/src/runtime_api/schemas/events.py` are byte-identical pre/post merge.
- [ ] No new endpoint. `services/backend-facade` route table is unchanged.
- [ ] Topbar renders correctly at ≥ 1100 px (two-row), 760–1099 px (one-row), < 760 px (compact).
- [ ] Status pill's `aria-live` polite region updates on phase transitions; depth changes announce "Depth: <name> — applies to next message." once.
- [ ] All interactive pills are keyboard-reachable; Escape closes any open popover; arrow keys navigate the depth radiogroup.
- [ ] `npm run typecheck --workspace @enterprise-search/frontend` and `npm run build --workspace @enterprise-search/frontend` pass.
- [ ] `npm run typecheck --workspace @enterprise-search/api-types` passes.
- [ ] `make test` green.

---

## 5 · References

- [`apps/frontend/src/features/chat/components/thread/AssistantThread.tsx`](../../apps/frontend/src/features/chat/components/thread/AssistantThread.tsx) — header replaced.
- [`apps/frontend/src/features/chat/components/thread/ModelSelector.tsx`](../../apps/frontend/src/features/chat/components/thread/ModelSelector.tsx) — superseded by `ModelPill`.
- [`apps/frontend/src/features/chat/chatRunState.ts`](../../apps/frontend/src/features/chat/chatRunState.ts) — phase enum + header status; consumed verbatim.
- [`apps/frontend/src/features/chat/ChatScreen.tsx`](../../apps/frontend/src/features/chat/ChatScreen.tsx) — `submitUserMessage`, `onReload`, `runUiState` integration site.
- [`apps/frontend/src/features/chat/components/details/UsagePanel.tsx`](../../apps/frontend/src/features/chat/components/details/UsagePanel.tsx) — meter data source.
- [`packages/design-system/src/index.tsx`](../../packages/design-system/src/index.tsx) — `StatusPill`, `IconButton`, `AppIcon`, `ConnectorChip`, `Menu`, `Button`, `Badge`.
- [`packages/api-types/src/index.ts`](../../packages/api-types/src/index.ts) — `ModelCatalogModel`, `ModelSelection`.
- [`services/ai-backend/src/agent_runtime/execution/models.py`](../../services/ai-backend/src/agent_runtime/execution/models.py) — `reasoning.effort` resolution chain (already wired).
- [`services/ai-backend/src/runtime_api/services/runs.py`](../../services/ai-backend/src/runtime_api/services/runs.py) — accepts `reasoning.effort` per call (no change).
- [`docs/new-design/pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) — `useConversationConnectorScopes` is consumed.
- [`docs/new-design/pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md) — provides `Conversation.folder` for the crumb and the title-rename PATCH endpoint.
- [`docs/new-design/pr-1.5-subagent-discovery-workspace-feeds.md`](pr-1.5-subagent-discovery-workspace-feeds.md) — workspace pane auto-open contract; this PR's `panelOpen` toggle is the FE control surface.
- [Anthropic API · `reasoning.effort`](https://docs.anthropic.com/en/api/extended-thinking) — semantics of low/medium/high.
- [WAI-ARIA · radiogroup pattern](https://www.w3.org/WAI/ARIA/apg/patterns/radio/) — depth control accessibility model.
