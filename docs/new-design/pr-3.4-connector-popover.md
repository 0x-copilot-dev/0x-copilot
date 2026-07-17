# PR 3.4 — Per-chat connector toggle UI + ConnectorPopover

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 3, PR 3.4 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** frontend (popover + composer button) · ai-backend (zero — endpoint shipped in PR 1.2) · api-types (zero new fields)
> **Size:** **M.** Pure FE composition over PR 1.2's persistence + hook. One small popover component used by topbar (already mounted by PR 2.1) and composer (new button slot).
> **Depends on:** PR 1.2 (per-chat connector scope persistence + `useConversationConnectors` hook — implemented), PR 2.1 (`<ConnectorsPill>` topbar trigger — implemented).
> **Reads alongside:** [`pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md), [`pr-1-2.1-per-chat-connector-scope-followups.md`](pr-1-2.1-per-chat-connector-scope-followups.md), [`pr-2.1-topbar-chrome-thinking-depth.md`](pr-2.1-topbar-chrome-thinking-depth.md), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md), [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md)
> **Sibling docs (Wave 3):** PR 3.1 — citation chips + sources tab · PR 3.2 — workspace pane right rail · PR 3.3 — MCP discovery + approval forwarding polish

---

## 0 · TL;DR

PR 1.2 shipped:

- `agent_conversations.enabled_connectors` JSONB column + `connectors_updated_at`,
- `PATCH /v1/agent/conversations/{id}/connectors` (RFC 7396 merge-patch),
- `useConversationConnectors(conversation, identity)` hook (optimistic UI, multi-tab reconciliation via visibilitychange),
- the audit chain entry on every toggle.

PR 2.1 shipped:

- `<ConnectorsPill>` in the topbar — clicking it currently calls `onOpen()` but **the popover body is not yet wired**.

PR 3.4 ships:

1. **`<ConnectorPopover>`** — a single popover component that displays the four-state vocabulary (active / paused / disconnected / workspace-off) and round-trips toggles through the PR 1.2 hook.
2. **Composer connectors button** — a sibling trigger anchored at the bottom of the chat that opens the _same_ popover, with an auto-flip placement helper so the popover renders above when there's no room below.
3. **Single hook owner.** Both triggers share one instance of `useConversationConnectors` lifted into `ChatScreen.tsx` — so the topbar pill and the composer button always agree, optimistic updates roll back atomically, and there's exactly one fetch on conversation switch.
4. **Connector catalog projection** — a tiny pure helper `projectConnectors(servers, scopes)` that produces the `ConnectorRow[]` the popover renders, classifying each server into one of four states. ~40 LOC; lives next to `useConversationConnectors`.

LoC estimate: FE ≈ 320 (popover + composer button + auto-flip helper + projection + tests) · ai-backend ≈ 0 · api-types ≈ 0.

---

## 1 · PRD

### 1.1 Problem

The Atlas design specifies **per-conversation connector scoping** with a four-state vocabulary used everywhere connectors appear:

| State         | Visual             | Action                                                      |
| ------------- | ------------------ | ----------------------------------------------------------- |
| Active        | solid app icon     | toggle off → "Pause"                                        |
| Paused        | greyscale + dot    | toggle on → "Resume"                                        |
| Disconnected  | dashed border      | "Connect" → user OAuth (existing `connectors.authenticate`) |
| Workspace-off | grey "Enable" pill | "Enable" → admin-only; routes to Settings → Connectors      |

PR 1.2 wires the data; PR 2.1 wires the topbar trigger. No popover body yet exists. Without it:

- The topbar pill opens **nothing**: `onOpen` is a no-op stub waiting for this PR.
- The composer has no per-chat affordance — users have to reach to the topbar (longer pointer travel; off-thread).
- The workspace member's mental model ("workspace-installed → connected for me → active for this chat") is not communicated anywhere visually.

The popover is the **single surface** that explains all three layers in one glance and lets the user toggle the third (per-chat) without leaving the chat.

### 1.2 Goals

1. **One popover, two triggers.** Topbar `<ConnectorsPill>` (anchored top) and composer connectors button (anchored bottom) both open the same component. Auto-flip placement keeps the popover on screen.
2. **Single state owner.** `useConversationConnectors` is invoked **once** in `ChatScreen.tsx` and threaded down. The popover is presentational; the composer button is presentational.
3. **Render the four-state vocabulary.** Each row uses the design-system `<ConnectorChip>` (already exists with all four variants per `packages/design-system/src/index.tsx:327`).
4. **Optimistic UI.** Toggling a connector flips the row immediately (PR 1.2 hook owns rollback on 4xx). The popover never blocks on the network.
5. **Disconnected → Connect routes to existing `connectors.authenticate`** (the same path `<ConnectorAuthTool>` uses for blocking MCP-auth). On success the connector becomes Active and the per-chat scope flips on.
6. **Workspace-off → Enable** is admin-only. Non-admins see a tooltip ("Ask your workspace admin to enable {connector}"). Admins are routed to `/settings#connectors` (PR 4.3 hash routing).
7. **Manage link** at the bottom of the popover routes to Settings → Connectors.
8. **Keyboard + screen-reader parity.** Popover is a `role="menu"` per WAI-ARIA; rows are toggleable; Escape closes; arrow keys navigate.
9. **Zero new endpoint.** Zero new event type. Zero new wire field.

### 1.3 Non-goals

- **Per-tool scope toggles.** PR 1.2 §1.3 / PR 4.4 own the MCP catalog overhaul. v1 popover is server-level.
- **Read-only preset.** Same — PR 4.4.
- **Server install / uninstall.** Settings → Connectors owns admin install. The popover only adjusts per-chat state (and triggers per-user OAuth via the existing path).
- **Search inside the popover.** Lists ≤12 servers in a typical workspace; if it grows, a search input becomes a future polish.
- **Drag-reorder in the popover.** No precedent in the design.
- **Promotion of `<ConnectorPopover>` into design-system.** The popover composes a feature workflow (PR 1.2 hook + product-specific routing). It's a feature component per `packages/design-system/CLAUDE.md`.

### 1.4 Success criteria

- ✅ Topbar pill click opens popover anchored below the pill.
- ✅ Composer button click opens popover anchored above the button (or below if there's room — the auto-flip helper picks).
- ✅ Same popover, same data, same optimistic semantics regardless of trigger.
- ✅ Toggling Slack from Active → Paused fires `PATCH …/connectors` with `{slack: null}`; row flips immediately; on 4xx rolls back and surfaces the error in the popover footer.
- ✅ Disconnected → Connect routes through `connectors.authenticate(serverId)` (existing) — popover stays open while OAuth tab opens.
- ✅ Workspace-off → Enable routes admins to `/settings#connectors`; non-admins see a disabled tooltip.
- ✅ Manage link routes to `/settings#connectors`.
- ✅ Below 1100px the popover transforms to a bottom-sheet on small viewports (kept on screen, not clipped).
- ✅ Keyboard: ArrowUp/Down navigates rows; Space/Enter toggles; Escape closes; focus returns to the trigger.
- ✅ `useConversationConnectors` is called **exactly once** per `ChatScreen` instance (verified by render-counting test).
- ✅ `npm run typecheck` clean; `npm run build` clean.

### 1.5 User stories

| #    | Persona                    | Story                                                                                                                                                                                |
| ---- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| US-1 | Sarah                      | I'm investigating Q1 launch. I click the connectors pill in the topbar, see Notion / Drive / Slack / Salesforce / Confluence. I tap Slack to pause it. The pill updates immediately. |
| US-2 | Sarah                      | I'm in the composer about to send a prompt. I tap the layers icon next to the model pill. The same popover opens above the composer because there's no room below. I pause Calendar. |
| US-3 | Sarah                      | Calendar isn't connected yet. I tap "Connect"; OAuth tab opens; I authorize; the popover updates: Calendar is Active and my next prompt sees it.                                     |
| US-4 | Sarah (ops)                | GitHub is workspace-disabled. I see "Enable in Settings" badge — clicking takes me directly to `/settings#connectors`.                                                               |
| US-5 | Marcus (member, not admin) | GitHub is workspace-disabled. I see "Ask admin to enable" tooltip; the row is non-actionable.                                                                                        |
| US-6 | Sarah                      | I switch from chat A (Slack paused) to chat B (Slack active). The popover in B correctly shows Slack active. No leaking state.                                                       |
| US-7 | Sarah (offline blip)       | I toggle Slack; network drops; the optimistic flip rolls back; the popover footer says "Couldn't pause Slack — retry"; I retry; works.                                               |

---

## 2 · Spec

### 2.1 Wire — explicit zero

| Surface                                         | Touched?                                                                            |
| ----------------------------------------------- | ----------------------------------------------------------------------------------- |
| `PATCH /v1/agent/conversations/{id}/connectors` | **No.** PR 1.2 ships it; this PR consumes the existing `useConversationConnectors`. |
| `Conversation.enabled_connectors`               | **No.**                                                                             |
| Event types                                     | **No.**                                                                             |
| api-types                                       | **No additions.** All shapes exist (`ConversationConnectorScopes`, `McpServer`).    |
| Audit chain                                     | **No.** Toggles go through the existing audited PATCH endpoint.                     |

### 2.2 Components — what we add, what we reuse

| Component                                  | Source                                                                             | Notes                                                                                                                                   |
| ------------------------------------------ | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `ConnectorPopover` (NEW)                   | `apps/frontend/src/features/connectors/ConnectorPopover.tsx`                       | Pure presentational. Receives `{rows, onToggle, onConnect, onEnableInSettings, onManage, anchorEl, onClose}`.                           |
| `ConnectorRow` (NEW, internal)             | inside `ConnectorPopover.tsx`                                                      | One row; uses design-system `<ConnectorChip>` for the visual; switch on row.state.                                                      |
| `projectConnectors` (NEW pure helper)      | `apps/frontend/src/features/connectors/projectConnectors.ts`                       | `(servers: McpServer[], scopes: ConversationConnectorScopes, viewer: {is_admin}) → ConnectorRow[]`. ~40 LOC; pure; tested in isolation. |
| `useAutoFlipPlacement` (NEW small hook)    | `apps/frontend/src/utils/useAutoFlipPlacement.ts`                                  | ~50 LOC. Returns `{placement: "top"                                                                                                     | "bottom", style}` based on anchor rect + viewport. No dependency. |
| `ComposerConnectorsButton` (NEW)           | `apps/frontend/src/features/chat/components/composer/ComposerConnectorsButton.tsx` | The trigger that lives in the composer footer. Uses the same anchor + popover.                                                          |
| `ConnectorsPill` (existing PR 2.1)         | _existing_                                                                         | Already mounted in topbar. PR 3.4 wires its `onOpen` to the popover.                                                                    |
| `<ConnectorChip>` (existing design-system) | `@0x-copilot/design-system`                                                        | Renders the four-state visual. **Already exists.**                                                                                      |
| `<AppIcon>` (existing design-system)       | `@0x-copilot/design-system`                                                        | Renders the brand letter glyph.                                                                                                         |
| `<Menu>` (existing design-system)          | `@0x-copilot/design-system`                                                        | Used as the popover host shell (mousedown-outside dismissal already implemented).                                                       |

### 2.3 Hook lifting — single owner

```tsx
// ChatScreen.tsx (existing controller; relevant additions only)

const conversation = useMemo(
  () => conversations.find((c) => c.conversation_id === conversationId) ?? null,
  [conversations, conversationId],
);

const connectorState = useConversationConnectors(conversation, identity);

const [popoverOpen, setPopoverOpen] = useState(false);
const popoverAnchorRef = useRef<HTMLElement | null>(null);

// Topbar trigger (PR 2.1)
<Topbar
  …
  onConnectorsOpen={(anchor) => {
    popoverAnchorRef.current = anchor;
    setPopoverOpen(true);
  }}
/>

// Composer trigger (NEW)
<AssistantThread … >
  <ThreadBody …>
    {/* … messages */}
    <Composer …>
      <ComposerConnectorsButton
        active={activeCount(connectorState.scopes)}
        onOpen={(anchor) => {
          popoverAnchorRef.current = anchor;
          setPopoverOpen(true);
        }}
      />
    </Composer>
  </ThreadBody>
</AssistantThread>

// Single popover instance
{popoverOpen && popoverAnchorRef.current && (
  <ConnectorPopover
    rows={projectConnectors(connectors.servers, connectorState.scopes, viewer)}
    busy={connectorState.loading}
    error={connectorState.error}
    onToggle={(server_id, nextScope) =>
      void connectorState.patch({ [server_id]: nextScope })
    }
    onConnect={(server_id) =>
      void connectors.authenticate(server_id) // existing path
    }
    onEnableInSettings={() => onOpenSettings("connectors")}
    onManage={() => onOpenSettings("connectors")}
    anchorEl={popoverAnchorRef.current}
    onClose={() => setPopoverOpen(false)}
  />
)}
```

The hook is invoked **once** per `ChatScreen`. Both triggers (topbar + composer) read the same React state. Toggling from one updates the other in the same render.

### 2.4 The four-state projection

```ts
// apps/frontend/src/features/connectors/projectConnectors.ts

export type ConnectorRowState =
  | "active" // workspace-installed + user-authenticated + per-chat scope ≠ null
  | "paused" // workspace-installed + user-authenticated + per-chat scope === null
  | "disconnected" // workspace-installed + user NOT authenticated
  | "workspace_off"; // workspace-disabled / not installed

export interface ConnectorRow {
  server_id: string;
  display_name: string;
  brand_letter: string;
  brand_color?: string;
  state: ConnectorRowState;
  current_scopes: string[] | null; // active scopes when state==='active'; null when paused
  default_scopes: string[]; // workspace defaults; the resume target
  workspace_admin_managed: boolean; // true → only admins can Enable
}

export function projectConnectors(
  servers: McpServer[],
  scopes: ConversationConnectorScopes,
  viewer: { is_admin: boolean },
): ConnectorRow[] {
  return servers.map((server) => {
    const installed = server.enabled === true;
    const authenticated = server.auth_state === "authenticated";
    const perChatScope = scopes[server.id]; // undefined | string[] | null

    let state: ConnectorRowState = "workspace_off";
    if (installed && authenticated) {
      state = perChatScope === null ? "paused" : "active";
    } else if (installed && !authenticated) {
      state = "disconnected";
    }

    return {
      server_id: server.id,
      display_name: server.display_name,
      brand_letter: server.display_name.charAt(0).toUpperCase(),
      brand_color: server.brand_color ?? undefined,
      state,
      current_scopes: state === "active" ? (perChatScope as string[]) : null,
      default_scopes: server.default_scopes ?? [],
      workspace_admin_managed: server.admin_managed ?? false,
    };
  });
}
```

Tested in isolation — pure function, table-test friendly.

### 2.5 Toggle semantics

| Click on …                      | What happens                                                                                                                                                                          |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Active row                      | `patch({ [server_id]: null })` → row flips to Paused (optimistic).                                                                                                                    |
| Paused row                      | `patch({ [server_id]: row.default_scopes })` → row flips to Active.                                                                                                                   |
| Disconnected → Connect          | Calls existing `connectors.authenticate(server_id)`. Popover stays open. On OAuth success, server's `auth_state` becomes `authenticated`; row re-projects to Active (default scopes). |
| Workspace-off → Enable (admin)  | Closes popover; routes to `/settings#connectors` (existing routing helper).                                                                                                           |
| Workspace-off → Enable (member) | No-op (button disabled). Tooltip "Ask your workspace admin to enable {connector}".                                                                                                    |
| Manage link (bottom)            | Closes popover; routes to `/settings#connectors`.                                                                                                                                     |

### 2.6 Auto-flip placement

The composer button anchors at the bottom of the viewport. A naive popover renders below and clips. The hook decides:

```ts
// useAutoFlipPlacement.ts — returns the better of {top, bottom} based on anchor rect + estimated popover height.
//  - default placement: bottom
//  - if anchorRect.bottom + POPOVER_HEIGHT_ESTIMATE > viewport.height - SAFE_PADDING: flip to top
//  - on resize / scroll: re-evaluate (lightweight ResizeObserver + scroll listener; debounced 30ms)
```

Topbar pill always anchors below — no flip needed; pass `placement="bottom"` explicitly when called from the topbar.

The hook returns a **`style` object** (top/left/transform) that the popover applies; collisions on the horizontal axis use `align="end"` for composer (right edge of popover aligns with right edge of viewport-12px) and `align="start"` for topbar.

### 2.7 Streaming impact — explicit

| Subsystem                            | Touched?                                                                              |
| ------------------------------------ | ------------------------------------------------------------------------------------- |
| `runtime_events` schema              | **No.**                                                                               |
| `RuntimeEventEnvelope` Pydantic / TS | **No.**                                                                               |
| SSE handshake                        | **No.**                                                                               |
| `runtime_worker` job loop            | **No.**                                                                               |
| `chatModel/eventReducer.ts`          | **No.**                                                                               |
| Capabilities middleware              | **No.** Toggles affect the **next** run only (PR 1.2 contract — frozen at run-start). |
| Audit chain                          | **No.** Existing PATCH endpoint already audits each toggle.                           |

The popover is presentation over the PR 1.2 endpoint. There is no new wire concern.

### 2.8 Permissions

| Caller                                              | Popover behavior                                                                                                        |
| --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Conversation owner (non-admin)                      | May toggle Active ↔ Paused; may Connect own connector via OAuth; cannot Enable workspace-off (Ask admin).               |
| Workspace admin                                     | Same as owner + may Enable workspace-off (routes to Settings).                                                          |
| Workspace member viewing a shared conversation (W6) | Read-only popover — toggles disabled; tooltip explains read-only. (W6 sharing has its own connector substitution flow.) |

Disabled state widens `chromeDisabled` (PR 2.1) to the popover via prop drilling.

### 2.9 Error semantics

| Condition                                           | Behavior                                                                                                        |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `patch` returns 4xx (validation, scope-not-allowed) | Optimistic flip rolls back (PR 1.2 hook); popover footer shows the error inline; row remains in original state. |
| `patch` returns 5xx                                 | Same rollback; footer shows "Server error — retry"; PR 1.2.1 reconciliation kicks in on tab focus.              |
| `connectors.authenticate` fails / user cancels      | Popover stays open; row stays Disconnected; small banner "Couldn't connect — try again".                        |
| Network drop while popover open                     | UI is local; rows reflect last-known state; reconnection picks up via PR 1.2.1 visibilitychange listener.       |
| Conversation switches while popover open            | Popover closes; opening it again on the new conversation uses the new conversation's scopes.                    |
| Server list empty (no MCP servers installed)        | Popover renders "No connectors installed yet" + "Open Connectors in Settings" link.                             |
| Active connector becomes paused on another tab      | PR 1.2.1 visibilitychange listener refreshes scopes on tab focus; popover updates if open.                      |

### 2.10 Accessibility

- Popover host: `role="menu"`; rows: `role="menuitemcheckbox"` for toggleable rows, `role="menuitem"` for Connect / Enable / Manage actions.
- ArrowUp/Down navigates rows (roving tabindex); Home/End jumps to first/last; Space/Enter activates; Escape closes and returns focus to the trigger.
- Toggle row labels are explicit: "Notion — currently active. Press Space to pause."
- Popover open state announces row count via `aria-live="polite"`.
- Reduce-motion: open/close transitions use `prefers-reduced-motion` to disable scaling.
- The 4-state visual encoding is **never** the only signal — every row carries text ("Active" / "Paused" / "Connect" / "Enable in Settings").

### 2.11 What we explicitly do NOT add

- **No `@floating-ui/react`.** The auto-flip use case is binary (top vs. bottom). 50 LOC inline matches the design-system rule of "promote when ≥2 consumers need real positioning logic." If a future PR needs collision detection on more axes, swap in `@floating-ui/react` in one place.
- **No `@radix-ui/react-popover`.** `<Menu>` from design-system already handles dismissal; the popover ARIA contract is small.
- **No new design-system primitive.** `<ConnectorChip>` covers the visual; the popover is feature composition.
- **No client-side caching of `mcp_servers`.** That list is small, refreshed by `useConnectors()` on mount; no need.
- **No optimistic Connect.** OAuth is real; we don't fake the row to Active until the auth_state flips on the server.

---

## 3 · Architecture

### 3.1 Where the popover lives

```
ChatScreen.tsx (existing)
  │
  │  one hook instance:
  │    const connectorState = useConversationConnectors(conversation, identity)
  │
  ├─ <Topbar>
  │     └─ <ConnectorsPill onOpen=(anchor)=>setOpen> (PR 2.1)
  │
  ├─ <AssistantThread>
  │     └─ <ThreadBody>
  │            └─ <Composer>
  │                  └─ <ComposerConnectorsButton onOpen=(anchor)=>setOpen> (NEW)
  │
  └─ {open && <ConnectorPopover
                rows={projectConnectors(servers, scopes, viewer)}
                onToggle={…}
                onConnect={…}
                onEnableInSettings={…}
                onManage={…}
                anchorEl={anchorRef.current}
                onClose={…} />}
```

### 3.2 Data flow

```
PR 1.2 endpoint  ◄──────┐
   PATCH /…/connectors  │ optimistic patch
                        │
                useConversationConnectors        (hook owns state + multi-tab refetch)
                        │
                        │  scopes: ConversationConnectorScopes
                        ▼
                projectConnectors(scopes, servers, viewer)
                        │
                        ▼  rows: ConnectorRow[]
                <ConnectorPopover>
                        │
                        ▼  user click
                onToggle / onConnect / onEnableInSettings / onManage
                        │
                        ├─►  patch({ [server_id]: null | scopes }) ─►  hook ─► API
                        ├─►  connectors.authenticate(server_id)   ─►  existing OAuth path
                        ├─►  applyAppRoute('settings', 'connectors') ─► routing
                        └─►  applyAppRoute('settings', 'connectors')
```

### 3.3 Sequence — pause Slack from the composer button

```
Sarah                       Composer button                        Popover                      ChatScreen
 │                              │                                    │                              │
 │ click ⊞ button               │                                    │                              │
 │ ───────────────────────────► │                                    │                              │
 │                              │ onOpen(anchorEl)                   │                              │
 │                              │ ─────────────────────────────────────────────────────────────────►│ setPopoverOpen(true);
 │                              │                                    │                              │ anchorRef.current = button
 │                              │                                    │                              │
 │                              │                                    │ render anchored ABOVE        │
 │                              │                                    │ (auto-flip helper picks)     │
 │                              │                                    │                              │
 │ keyboard ↓ to "Slack"        │                                    │                              │
 │ Space                                                                                            │
 │ ───────────────────────────────────────────────────────────────►  │                              │
 │                                                                   │ onToggle(slack, null)        │
 │                                                                   │ ─────────────────────────►   │ connectorState.patch({slack:null})
 │                                                                   │                              │
 │                                                                   │  (PR 1.2 hook):              │
 │                                                                   │   optimistic flip            │
 │                                                                   │   PATCH /…/connectors        │
 │                                                                   │                              │
 │                                                                   │  popover row re-renders      │
 │                                                                   │  topbar pill re-renders      │
 │                                                                   │  (same source of truth)      │
 │                                                                   │                              │
 │ Esc                                                               │ onClose                       │
 │                                                                   │ focus → composer button      │
```

### 3.4 DRY — what's reused vs. what's added

| Concern                | Reuse                                                                      | Add                                              |
| ---------------------- | -------------------------------------------------------------------------- | ------------------------------------------------ |
| Persistence            | PR 1.2 endpoint                                                            | —                                                |
| Hook                   | `useConversationConnectors` (PR 1.2 + PR 1.2.1)                            | —                                                |
| Connector chip visuals | `<ConnectorChip>` from design-system (4 variants exist)                    | —                                                |
| Brand glyph            | `<AppIcon>` from design-system                                             | —                                                |
| Menu host shell        | `<Menu>` from design-system (mousedown-outside dismissal already in place) | —                                                |
| Topbar trigger         | `<ConnectorsPill>` (PR 2.1)                                                | wires `onOpen` to the popover                    |
| OAuth path             | `connectors.authenticate(serverId)` existing                               | —                                                |
| Settings routing       | `applyAppRoute('settings', 'connectors')` (existing)                       | —                                                |
| Auto-flip              | —                                                                          | `useAutoFlipPlacement` (~50 LOC, no dep)         |
| Projection             | `McpServer` + `ConversationConnectorScopes` (existing types)               | `projectConnectors` pure helper (~40 LOC)        |
| Popover                | —                                                                          | `<ConnectorPopover>` (~140 LOC w/ rows + footer) |
| Composer button        | —                                                                          | `<ComposerConnectorsButton>` (~40 LOC)           |

Net new code: **FE ≈ 320 LOC**.

### 3.5 Dependency survey

- **`@floating-ui/react`** (~14 KB gz) — the de facto standard for popover positioning (collision detection, auto-flip, virtual elements). Considered. **Rejected for this PR**: our auto-flip surface is binary (top/bottom); a 50-LOC inline helper does the job. If a future PR needs collision detection on additional axes (e.g. Settings → Connectors install wizard with cascading menus), swap to `@floating-ui/react` in **one place** (`useAutoFlipPlacement.ts`).
- **`@radix-ui/react-popover`** (~6 KB gz, depends on Floating UI) — same trade-off; `<Menu>` covers dismissal.
- **`@radix-ui/react-toggle-group`** — overkill for one popover with checkbox rows.
- **`react-aria` `useMenu`** — comprehensive ARIA but pulls a tree we don't need.
- **`mantine`, `chakra`, `material-ui`** — wrong abstraction; we already have the design system.

We add **nothing** from npm.

### 3.6 Edge cases

| Case                                                                                                  | Behavior                                                                                                                                                                                 |
| ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| User pauses connector mid-run                                                                         | The active run is unaffected (PR 1.2 contract — capabilities frozen at run-start). The next run picks up the new scope. Status pill shows "(applies to next message)" tooltip on toggle. |
| Disconnected connector with `default_scopes=[]`                                                       | Connect button visible; on success row becomes Active with scopes `[]` — i.e. the connector is reachable but no tools enabled. (Server enforces.)                                        |
| Workspace-off with admin-managed flag and current viewer is admin                                     | Enable button visible; click routes to Settings. Popover closes.                                                                                                                         |
| Server `enabled=true` but `auth_state` toggles between `auth_pending` and `authenticated` mid-popover | Popover re-renders on next refresh; OAuth-pending rows show a "Connecting…" indicator (existing pattern in `ConnectorAuthTool`).                                                         |
| Two popovers requested simultaneously (topbar + composer)                                             | One state owner → only one is open at a time; if open and the other trigger fires, the popover re-anchors to the new trigger.                                                            |
| Popover opened with no conversation selected                                                          | Disable toggles; show "Open or start a chat to scope connectors" empty state with the catalog list visible (workspace defaults).                                                         |
| Tab is hidden while user toggles, then made visible                                                   | PR 1.2.1 visibilitychange listener refetches and reconciles; if local optimistic state diverges, server wins on `connectors_updated_at` strict-newer comparison.                         |
| `mcp_servers` list refetched mid-popover                                                              | New rows appended; existing rows preserve toggle state.                                                                                                                                  |
| Reduce-motion preference                                                                              | Open/close uses `transition: none`.                                                                                                                                                      |
| Mobile (< 760 px)                                                                                     | Popover transforms to a bottom-sheet (`position: fixed; left: 0; right: 0; bottom: 0; max-height: 70vh`); same internal markup, different CSS.                                           |

### 3.7 Test plan

**Frontend**

- `projectConnectors.test.ts` — table-test all 16 input combinations (3 server states × 4 scope cell values × 2 admin booleans, with collapses where the cell is `undefined`).
- `useAutoFlipPlacement.test.ts` — anchored at top of viewport flips down; anchored at bottom flips up; resize re-evaluates; scroll re-evaluates with debounce.
- `ConnectorPopover.test.tsx` — renders rows in projection order; ARIA contract; arrow-key navigation; Escape closes + returns focus; Toggle calls `onToggle` with correct delta; Connect calls `onConnect`; Enable routes to Settings; Manage routes to Settings.
- `ComposerConnectorsButton.test.tsx` — count badge; click opens popover anchored above; respects `chromeDisabled`.
- `ChatScreen.connectors-integration.test.tsx` — single hook invocation; topbar pill + composer button share state; toggling from one updates the other.

**Cross-service smoke**

- `make test` — extend the per-chat-scope use-case test to assert UI flow: open via topbar, pause, send prompt, agent runs without paused connector tools, toggle back on, send prompt, agent uses the tool.

### 3.8 Rollout

- **Flag-free.** The PR 1.2 endpoint is already live; PR 2.1 ships an inert pill. PR 3.4 wires the pill + adds the composer button — no behavioral risk if new code is bypassed.
- **Backout.** Revert PR. Topbar pill returns to its no-op `onOpen`; composer keeps no connectors button. Connector scopes still persist via PR 1.2; only the UI affordance disappears.
- **Migration.** None. Pure FE.

### 3.9 Open questions

1. **Should toggling all rows off auto-pause the run?** v1: no — the toggle affects only the next run, per PR 1.2 contract. The status pill explains "(applies to next message)" so users aren't surprised.
2. **Should "Enable in Settings" deep-link to the specific server?** Settings → Connectors is the v1 target. A `?server=…` query param to scroll to the server card is a cheap follow-up in PR 4.3.
3. **Should we add a search input to the popover?** Defer until typical workspace exceeds ~12 connectors.
4. **Should the composer button respect a per-conversation hide preference?** No — the layers icon is always visible. Power users can ignore it.

---

## 4 · Acceptance checklist

- [ ] `apps/frontend/src/features/connectors/ConnectorPopover.tsx` ships with the four-state row vocabulary, role=menu/menuitemcheckbox ARIA, ArrowUp/Down + Home/End + Space/Enter + Escape keymap.
- [ ] `apps/frontend/src/features/connectors/projectConnectors.ts` ships and is unit-tested in isolation.
- [ ] `apps/frontend/src/utils/useAutoFlipPlacement.ts` ships; binary (top/bottom) auto-flip; debounced resize/scroll re-evaluation.
- [ ] `apps/frontend/src/features/chat/components/composer/ComposerConnectorsButton.tsx` ships; clicking opens the same popover; count badge reflects active connectors.
- [ ] `<ConnectorsPill>` (PR 2.1) `onOpen` is wired in `ChatScreen` to set the same popover-open state.
- [ ] `useConversationConnectors` is invoked **exactly once** per `ChatScreen` (verified by render-counting test).
- [ ] No new `RuntimeApiEventType`. Pydantic schemas unchanged.
- [ ] No new endpoint. Facade route table unchanged.
- [ ] No new api-types fields.
- [ ] No new design-system primitive.
- [ ] No npm dependency added.
- [ ] Below 1100 px the popover transforms to a bottom-sheet; toggle behavior identical.
- [ ] Manage and Enable links route to `/settings#connectors` via `applyAppRoute`.
- [ ] Connect button uses the existing `connectors.authenticate(serverId)` path; popover stays open during OAuth.
- [ ] `npm run typecheck --workspace @0x-copilot/frontend` and `npm run build --workspace @0x-copilot/frontend` pass.
- [ ] `make test` green.

---

## 5 · References

- [`apps/frontend/src/features/connectors/useConversationConnectors.ts`](../../apps/frontend/src/features/connectors/useConversationConnectors.ts) — single source of truth for per-chat scopes (PR 1.2 + PR 1.2.1).
- [`apps/frontend/src/features/chat/components/shell/ConnectorsPill.tsx`](../../apps/frontend/src/features/chat/components/shell/ConnectorsPill.tsx) — topbar trigger (PR 2.1).
- [`apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx`](../../apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx) — composer host where the connectors button lands.
- [`packages/design-system/src/index.tsx`](../../packages/design-system/src/index.tsx) — `<ConnectorChip>` (line 327), `<AppIcon>` (line 290), `<Menu>` (line 365), `<IconButton>` (line 240), `<Badge>` (line 167).
- [`apps/frontend/src/features/connectors/`](../../apps/frontend/src/features/connectors) — connector hook + state surfaces.
- [`docs/new-design/pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) — endpoint + persistence + hook (PR 1.2).
- [`docs/new-design/pr-1-2.1-per-chat-connector-scope-followups.md`](pr-1-2.1-per-chat-connector-scope-followups.md) — multi-tab reconciliation.
- [`docs/new-design/pr-2.1-topbar-chrome-thinking-depth.md`](pr-2.1-topbar-chrome-thinking-depth.md) — `<ConnectorsPill>` consumer (already mounted, awaiting popover wire).
- [WAI-ARIA Menu Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/menubar/) — `role="menu"` + `menuitemcheckbox` semantics.
- Atlas Design Doc — §"ConnectorPopover", §"Flow — Connector scoping", §"Why per-chat?".
