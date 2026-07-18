# Phase 3 — Run cockpit (flagship: mount ThreadCanvas)

> Implementation PRD. Follows `docs/plan/desktop-redesign/_TEMPLATE.md` (all 12 sections, in order).
> Design source of truth: `docs/plan/desktop-redesign/design-reference/DESIGN-SPEC.md` §2 (Run cockpit) + §9 (decisions overlay).
> Plan anchor: `docs/plan/desktop-redesign/PLAN.md` §6 (Run cockpit spec), §7 (consolidation map), Phase 3 (3A–3G), §9 (sequencing).

---

## 1. Context & problem

Phase 3 turns the fully-built-but-unmounted `packages/chat-surface/src/thread-canvas` subtree into the **Run** destination — the flagship cockpit. Today `ThreadCanvas.tsx` and its family (`TcSurfaceMount`, `TcSwimlanes`, `TcChat`, `TcMiniTimeline`, `TcTabs`, `TcInlineDiff`, `eventProjector`) exist and are unit-tested, but nothing renders them: the desktop renderer mounts `apps/desktop/renderer/DesktopPlaceholder.tsx` inside `<ChatShell>` (see `apps/desktop/renderer/bootstrap.tsx` line ~104), and `packages/chat-surface/src/destinations/chats/ChatsDestination.tsx` still shows the literal placeholder `"ThreadCanvas mounts here (Phase 2-B)"`. This phase composes those primitives into a Run destination host, wires a live run session (SSE → event projector), and fills the DESIGN-SPEC §2 layout: **work surface center** (`TcSurfaceMount` + `surface-renderers`), **tabbed right rail `[Chat · Sources · Agents · Approvals]`** (recomposed `WorkspacePane`), **bottom timeline** (`TcSwimlanes`), with **Studio + Focus modes only** (Auto dropped — autonomy is a run state) and `⌘M`.

It builds on Phase 1 (interaction components hoisted into `chat-surface`: markdown renderer, composer, citations, subagent/fleet cards, approval card + receipt, **WorkspacePane** hoisted in PR-1.6/1F) and Phase 2 (6-destination profile-gated shell with a top-level `run` slug + destination outlet, v2 "quiet" tokens on the shell). It is the critical path (PLAN §9: `3` needs `1`+`2`). It also closes the two prototype gaps DESIGN-SPEC §2 flags: **Run empty/idle** and **multi-run selection**.

Why now: Phases 1+2 have delivered the parts and the shell chrome; the cockpit is the product's reason to exist ("give it a goal, watch/rewind/stop it before it acts" — DESIGN-SPEC preamble). Nothing else in Phase 4/5 matters until Run is real.

---

## 2. Goals / Non-goals

### Goals

- Mount a **Run destination** (`packages/chat-surface/src/destinations/run/`) that composes `ThreadCanvas` + a right-rail `WorkspacePane` + a run header, consumed by `apps/desktop` (replacing `DesktopPlaceholder` on the `run` slug).
- Deliver the DESIGN-SPEC §2 **Studio** layout (`grid 1fr 340px`, surface center / rail right / timeline bottom) and **Focus** layout (rail centered ≤780px, surface hidden, timeline minimized).
- **Drop Auto mode** from `ThreadMode` and the mode switcher; keep the single-mount invariant (no remounts across mode switches). Add `⌘M` toggle.
- Right rail = **tabbed `[Chat · Sources · Agents · Approvals]`** (Chat default), reusing the hoisted `WorkspacePane` tab contents; Chat tab hosts `TcChat`.
- Timeline: `TcSwimlanes` lanes (neutralized color), scrub/step/**snap-to-now**, pinned beads, LIVE/VIEWING; parallel subagents as **lanes + inline `SubagentFleetCard` + Agents tab**.
- **Approvals**: on-surface inline (`TcInlineDiff` / `SheetDiff` per-row) with `Approve & sign` / `✓ Signed` / `Rejected` / `Queued` states; in-chat `ApprovalCard` (4-zone) + `ApprovalReceipt`; Focus-mode `.conf-card` confirmation card.
- **Streaming**: conversational GFM tables via a citation-safe streaming markdown path (Streamdown) in `TcChat`; editable/large tabular surfaces via `surface-renderers` snapshot diffs in the center pane.
- Design the two **prototype-gap states**: Run **empty/idle** (no active run) and **multi-run** selection.
- Reconcile the residual hardcoded lime `#c2ff5a` in the thread-canvas + surface-renderers subtree to the single **sky** accent (`--color-accent`), and neutralize timeline lane colors (DESIGN-SPEC §0 accent discipline + §2 "lane color neutralized"). _(Phase 0C targeted the design-system + top-level files; these leaf files still carry lime — see §6 gap flags.)_
- Keep `apps/frontend` (web) **behaviorally identical**: it does not adopt the Run destination in this phase; ThreadCanvas is not currently imported by web (`grep` confirms no non-test importer).

### Non-goals (explicitly deferred)

- **Migrating web `apps/frontend/src/features/chat/ChatScreen.tsx` onto the Run destination.** Web keeps its existing `ChatScreen` composition. (Deferred; a later web-adoption pass, out of this redesign's desktop scope.)
- **Chats archive → reopen-into-Run** flow and Chats list. → **Phase 4 (4A)**.
- **Activity / Projects / Tools / Skills** destinations. → **Phase 4**.
- **Settings** (approval policy, provider keys, local models, appearance). → **Phase 5**. The Run cockpit _reads_ the approval policy but does not author it here.
- **Command palette `⌘K`** and the full keyboard-shortcut set (except `⌘M`, which Run owns). → **Phase 6 (6A/6B)**.
- **New surface-renderer adapters** (beyond the registered email/salesforce/sheet/slide + generic tier-3). → out of scope; Run consumes what `surface-renderers` registers.
- **Backend run/branch/restore endpoint changes.** Run consumes existing facade `/v1/*` streaming, branch, restore contracts (see `TcSwimlanes` transport calls).
- **Real Ollama/local-model inference.** → Phase 5 (5C).

---

## 3. User stories

Roles: **Solo user** (primary), **Developer/maintainer** (DX/architecture). No Team-admin stories this phase (Run is not profile-gated).

### US-3.1 — Open Run and see my active run

_As a Solo user, I want the Run destination to mount the live cockpit when I have an active run, so that I can watch the agent work across surface, chat, and timeline in one view._

- **Given** the run session is still resolving/connecting (no events yet), **When** Run mounts, **Then** the cockpit shows a non-blocking loading state (chat "Loading messages…" `role="status"`, timeline "Listening for run events…", surface adapter placeholder) — never a blank pane or a spinner that blocks the whole screen — and swaps to content on the first projected event without remount.
- **Given** an active run exists for the current conversation, **When** I select Run in the rail, **Then** the center work surface, right-rail Chat tab, and bottom timeline all render from the same run's events (single event projector).
- **Given** the run is streaming, **When** new events arrive over SSE, **Then** beads append to the timeline, the surface updates, and the Chat tab appends messages — with no remount/flicker of any pane.
- **Given** the transport SSE errors, **When** the subscription drops, **Then** the cockpit shows a non-blocking error banner with **Retry** and keeps the last-projected state visible (no white screen).

### US-3.2 — Switch between Studio and Focus (no Auto)

_As a Solo user, I want a two-way Studio/Focus mode toggle with `⌘M`, so that I can go chat-forward without losing surface/timeline state._

- **Given** I am in Studio, **When** I press `⌘M` (or click Focus), **Then** the work surface hides, the rail centers (≤780px), the timeline minimizes, and my chat scroll position, active surface tab, scrub position, and composer draft are all preserved (no remount).
- **Given** I am in Focus, **When** I click the mini-timeline expand chevron or press `⌘M`, **Then** I return to Studio with the same state.
- **Given** the app previously stored my mode, **When** I reopen Run, **Then** it restores my last mode from the KeyValueStore (default **Studio**).
- **Given** the old three-value `auto` was persisted, **When** the stored value is read, **Then** it is coerced to `studio` (Auto no longer exists).

### US-3.3 — Use the tabbed right rail

_As a Solo user, I want a `[Chat · Sources · Agents · Approvals]` tabbed right rail, so that chat is the rail and the artifact stays center-stage._

- **Given** Studio mode, **When** the rail renders, **Then** tabs are `Chat · Sources · Agents · Approvals` with **Chat** selected by default, `role="tablist"`/`role="tab"`/`role="tabpanel"`, arrow-key navigation, and a live count badge on Agents/Approvals when >0.
- **Given** there are pending approvals, **When** the count rises, **Then** the Approvals tab shows the pending count badge (accent) and Focus mode surfaces the same approvals as inline confirmation cards in the conversation.
- **Given** a tab surface is empty, **When** I open it, **Then** it shows its per-tab empty copy (not a broken `0`).

### US-3.4 — Scrub the timeline and time-travel the surface

_As a Solo user, I want to rewind/step/snap-to-now on the timeline, so that I can inspect what the agent did at a past moment before it acts._

- **Given** the run has beads, **When** I click/drag a bead or press `⌘←`/`⌘→`, **Then** the playhead moves, `snapSet` snaps to the nearest bead within threshold, the surface tab switches to that bead's surface, and a "Viewing HH:MM · … Return to live →" banner appears.
- **Given** I am scrubbed off-now, **When** the "Viewing" banner shows, **Then** approvals are hidden (you cannot approve a past state) and the composer is disabled ("Snap to now to send a message").
- **Given** I am scrubbed, **When** I press `⌘L` / Escape / click **Live**, **Then** the playhead snaps to now, the banner clears, and approvals/composer re-enable.

### US-3.5 — Follow parallel subagents

_As a Solo user, I want parallel subagents shown as timeline lanes, an inline fleet card, and an Agents tab, so that I can track a fan-out without losing the thread._

- **Given** the agent dispatches N subagents in parallel, **When** the dispatch event arrives, **Then** an inline `SubagentFleetCard` ("Dispatched N subagents in parallel", nested child rows) renders in the conversation, one live lane per subagent appears in the timeline, and the Agents tab shows the live count.
- **Given** a subagent finishes, **When** its completion event arrives, **Then** its lane/fleet-row status updates (running → done) without remounting sibling lanes.
- **Given** zero subagents, **When** the run is linear, **Then** no fleet card renders and the Agents tab shows its empty copy.

### US-3.6 — Approve or reject on the surface

_As a Solo user, I want per-row inline approvals on structured artifacts and a 4-zone approval card in chat, so that I approve exactly what the agent will act on._

- **Given** a structured surface (e.g. a sheet) with a staged/pending row, **When** it renders, **Then** the pending row is highlighted (accent-soft, inset accent bar) and shows `Reject` / `Approve & sign` (or `Approve`); resolved rows show `✓ Signed` (jade) / `Rejected` (ember) / `Queued` (muted).
- **Given** a pending approval also appears in chat, **When** the `ApprovalCard` renders, **Then** it shows the 4-zone layout with Approve (`⌘↵`) / Reject (`⌘⌫`) and, on resolve, an `ApprovalReceipt`.
- **Given** I am in Focus mode, **When** an approval is pending, **Then** it appears as a `.conf-card` confirmation card in the conversation ("The agent paused here — it won't sign until you approve").
- **Given** the `TcInlineDiff` state machine, **When** an approval streams then resolves, **Then** it transitions `idle → streaming → pending → accepted|rejected` and rejects invalid transitions (`InvalidInlineDiffTransitionError`).

### US-3.7 — Read streaming output correctly

_As a Solo user, I want streaming chat tables and streaming surface edits rendered without corruption, so that live output is legible and citation-safe._

- **Given** the agent streams a GFM table in chat, **When** partial markdown arrives, **Then** it renders incrementally via the citation-safe streaming markdown path (Streamdown) with a blinking cursor, and never renders a half-parsed table as raw pipes.
- **Given** the agent streams edits to a large/editable tabular surface, **When** updates arrive, **Then** the center pane shows `surface-renderers` snapshot diffs (not a chat table), with a `streaming · N%` chip.

### US-3.8 — See a clear empty/idle Run

_As a Solo user with no active run, I want a purposeful empty state, so that I know how to start work._

- **Given** no active run for the conversation, **When** I open Run, **Then** the cockpit shows an empty/idle state (goal composer + "Give it a goal…" prompt), not a blank ThreadCanvas or a placeholder string.
- **Given** I submit a goal from empty state, **When** the run starts, **Then** the cockpit transitions to the live layout for the new `runId` without a full unmount/remount of the shell.

### US-3.9 — Choose among multiple runs

_As a Solo user with more than one run, I want a multi-run selection affordance, so that I can pick which run the cockpit shows._

- **Given** the conversation has multiple runs, **When** I open Run, **Then** a run selector (list/segmented) shows each run (goal, status running/done/paused, time) and the cockpit binds to the selected `runId`.
- **Given** I switch selected run, **When** I pick another, **Then** the event projector, tabs, timeline, and surface rebind to the new run (its own state), and mode/scrub reset appropriately.

### US-3.10 — Accessible, themed cockpit

_As a Solo user relying on keyboard / reduced motion / high contrast, I want the cockpit fully accessible and single-accent, so that it is usable and visually coherent in light and dark._

- **Given** keyboard-only use, **When** I Tab through the cockpit, **Then** the mode tablist, right-rail tabs, surface tabs, timeline, and composer are all reachable with visible `2px solid var(--color-accent)` focus rings, correct roles, and arrow-key nav within each tablist.
- **Given** `prefers-reduced-motion` / `[data-reduce-motion=1]`, **When** modes switch or beads pulse, **Then** transitions/animations are zeroed.
- **Given** light and dark themes and `[data-accent]` options, **When** the cockpit renders, **Then** it uses only `--color-*` tokens (sky accent; jade=live/success, ember=destructive) with **no** stray lime `#c2ff5a` or per-lane decorative color.

### US-3.11 — Maintain the SSOT boundary (developer)

_As a Developer/maintainer, I want the Run destination to live in `chat-surface` behind ports and be consumed by desktop (and available to web), so that there is one cockpit, not two._

- **Given** the boundary rules, **When** the Run destination is authored, **Then** it lives in `packages/chat-surface/src/destinations/run/`, uses only the `Transport`/`Router`/`KeyValueStore`/`PresenceSignal` ports (no bare `window`/`document`/`fetch`/`localStorage`), and `apps/desktop` imports it from `@0x-copilot/chat-surface` (no `apps/*`→`apps/*` import).
- **Given** the ESLint substrate guard, **When** CI runs, **Then** no framework-agnostic violation is introduced.

---

## 4. Functional requirements

### A. Mounting & layout

- **FR-3.1** A new `RunDestination` component (`packages/chat-surface/src/destinations/run/RunDestination.tsx`) MUST compose a run header, `ThreadCanvas` (center surface + bottom timeline), and the tabbed right rail into the DESIGN-SPEC §2 layout. (US-3.1)
- **FR-3.2** `apps/desktop/renderer/bootstrap.tsx` MUST render `RunDestination` as the `children` of `<ChatShell>` when `activeDestination === "run"`, replacing `DesktopPlaceholder` for that slug. (US-3.1)
- **FR-3.3** The cockpit MUST source all four consumers (surface / swimlanes / chat / timeline) from **one** `useEventProjector` projection per render (no second projection). (US-3.1, US-3.11)
- **FR-3.4** Studio layout MUST be `grid-template-columns: 1fr 340px` (work surface left, rail right, timeline bottom). Focus layout MUST center the rail (`max-width ≤ 780px`), hide the work surface, and minimize the timeline. (US-3.2) _(DESIGN-SPEC §2; current `ThreadCanvas` uses `1fr 360px` — see FR-3.24.)_

### B. Modes (drop Auto)

- **FR-3.5** `ThreadMode` MUST be exactly `"studio" | "focus"`; the `"auto"` value, `MODE_VALUES` auto entry, `MODE_LABELS.auto`, and the `resolvedMode` auto-resolution branch MUST be removed from `ThreadCanvas.tsx`. (US-3.2)
- **FR-3.6** The mode switcher MUST render two `role="tab"` buttons (Studio/Focus) with `aria-selected`, roving `tabIndex`, and ArrowLeft/ArrowRight cycling over the two values only. (US-3.2, US-3.10)
- **FR-3.7** A `useRunMode` hook MUST persist the mode in the `KeyValueStore` port (key namespaced per conversation) and coerce any legacy `"auto"` value to `"studio"` on read. (US-3.2)
- **FR-3.8** A global `⌘M` (Meta/Ctrl+M) shortcut MUST toggle Studio↔Focus while Run is the active destination, and MUST NOT fire while a text input/composer is focused unless the chord is unambiguous. (US-3.2)
- **FR-3.9** Switching modes MUST NOT remount `TcSurfaceMount`, `TcChat`, `TcSwimlanes`, `TcMiniTimeline`, or the composer (single-mount invariant preserved). (US-3.2)

### C. Right rail

- **FR-3.10** The right rail MUST present tabs in order `Chat · Sources · Agents · Approvals` with Chat selected by default, using `role="tablist"/tab/tabpanel`. (US-3.3)
- **FR-3.11** The Chat tab MUST host `TcChat`; Sources/Agents/Approvals tabs MUST reuse the hoisted `WorkspacePane` tab contents (`SourcesTab`, `AgentsTab`, `ApprovalsTab`). Draft and Skills tabs from `WorkspacePane` MUST NOT appear in the Run rail. (US-3.3)
- **FR-3.12** Agents and Approvals tabs MUST show a live count badge when their count > 0 (Agents: "N live" when running; Approvals: pending count in accent). Empty tabs MUST show per-tab empty copy. (US-3.3, US-3.5)
- **FR-3.13** In Focus mode the rail MUST collapse to the Chat surface only (centered); Sources/Agents/Approvals tab chrome MUST be suppressed, and pending approvals surface as inline confirmation cards instead. (US-3.2, US-3.6)

### D. Timeline & subagents

- **FR-3.14** The timeline MUST render `TcSwimlanes` with one lane per surface/subagent, click/drag scrub, `⌘←`/`⌘→` step, `⌘L`/Escape snap-to-now, and pinned beads persisted via the `KeyValueStore` port. (US-3.4)
- **FR-3.15** Scrubbing off-now MUST switch the active surface tab to the scrubbed bead's surface (`snapSet`), show the "Viewing HH:MM · … Return to live →" banner, hide approvals, and disable the composer. (US-3.4)
- **FR-3.16** Snap-to-now MUST clear the viewing banner and re-enable approvals + composer. (US-3.4)
- **FR-3.17** Parallel subagents MUST render as (a) an inline `SubagentFleetCard` in the conversation, (b) one live timeline lane per subagent, and (c) a live count in the Agents tab — from the single projection. (US-3.5)
- **FR-3.18** Timeline lane colors and bead colors MUST be neutralized to `--color-surface-muted`/`--color-text` tokens (no per-lane decorative color); `.now` = jade pulse, `.cur` = accent + ring, `.future` = hollow. (US-3.5, US-3.10)

### E. Streaming

- **FR-3.19** Conversational GFM tables in `TcChat` MUST render through a citation-safe streaming markdown path (Streamdown) with an incremental blinking cursor, never emitting half-parsed table markup. (US-3.7)
- **FR-3.20** Editable/large tabular surfaces MUST render in the center pane via `surface-renderers` snapshot diffs (`SheetRenderer`/`SheetDiffView` etc.), with a `streaming · N%` chip, not as a chat table. (US-3.7)

### F. Approvals

- **FR-3.21** Structured surfaces MUST support per-row inline approval: pending row highlighted (accent-soft + inset accent bar) with `Reject` / `Approve & sign` (or `Approve`); resolved rows show `✓ Signed` (jade) / `Rejected` (ember) / `Queued` (muted). (US-3.6)
- **FR-3.22** In-chat approvals MUST render the hoisted 4-zone `ApprovalCard` with Approve (`⌘↵`) / Reject (`⌘⌫`) and an `ApprovalReceipt` on resolution; Focus mode MUST render the `.conf-card` confirmation card variant. (US-3.6)
- **FR-3.23** The `TcInlineDiff` state machine MUST drive on-surface approval visuals through `idle → streaming → pending → accepted|rejected`, throwing `InvalidInlineDiffTransitionError` on invalid transitions. (US-3.6)

### G. Tokens & prototype-gap states

- **FR-3.24** All hardcoded lime `#c2ff5a` (and lime-derived palette constants) in `TcChat.tsx`, `TcTabs.tsx`, `TcInlineDiff.tsx`, `TcSurfaceMount.tsx`, `ThreadCanvas.tsx`, `TcSwimlanes.styles.ts`, `surfaces/GenericStructuredDiff.tsx`, and `surface-renderers/src/{_shared/palette.ts,sheet/*}` MUST be replaced by the sky accent tokens (`--color-accent`, `--color-accent-soft`, `--color-accent-contrast`) and semantic tokens (`--color-success` = jade `#57c785`, `--color-danger` = the destructive/"ember" token). Phase 3 consumes the `--color-danger` **token name**; its exact hex is owned by `packages/design-system/src/styles.css` (Phase 0B) — do **not** hardcode `#f0764f`/`#d97777` at the leaf. (US-3.10)
- **FR-3.25** The Run **empty/idle** state MUST render a goal composer with "Give it a goal…" prompt when no active run exists (no blank canvas / placeholder string), and starting a goal MUST transition to the live layout without shell remount. (US-3.8)
- **FR-3.26** The **multi-run** state MUST render a run selector (goal, status, time) when the conversation has >1 run and rebind the projection/tabs/timeline/surface on selection. (US-3.9)

### H. Boundaries & a11y

- **FR-3.27** `RunDestination` and all Run host code MUST use only the `Transport`/`Router`/`KeyValueStore`/`PresenceSignal` ports — no bare `window`/`document`/`fetch`/`localStorage` — and MUST pass the ESLint substrate guard. (US-3.11)
- **FR-3.28** `apps/desktop` MUST import the Run destination from `@0x-copilot/chat-surface`; it MUST NOT import `apps/frontend/src`. (US-3.11)
- **FR-3.29** All interactive cockpit controls MUST expose a visible focus ring (`2px solid var(--color-accent)` offset 2), correct ARIA roles (tablist/tab/tabpanel/status/alert/region/group), and honor `prefers-reduced-motion` / `[data-reduce-motion=1]`. (US-3.10)
- **FR-3.30** The cockpit MUST render correctly in light and dark themes and across `[data-density]` values using only design-system tokens. (US-3.10)
- **FR-3.31** A `useRunSession` host hook MUST resolve the active/selected run for a conversation, subscribe to the run event stream via `Transport.subscribeServerSentEvents`, accumulate ordered `RuntimeEventEnvelope`s into an append-only array (stable reference growth), and expose an error/retry state. (US-3.1, US-3.9)
- **FR-3.32** SSE error MUST surface a non-blocking `role="alert"` banner with **Retry** while preserving last-projected state. (US-3.1)
- **FR-3.33** While `useRunSession` is resolving/connecting and no events have arrived yet, the cockpit MUST render a non-blocking loading state per pane (chat "Loading messages…" with `role="status"`; timeline "Listening for run events…"; surface adapter placeholder) — never a full-screen blocking spinner or blank pane — and MUST swap to live content on the first projected event without remounting any pane. (US-3.1)

---

## 5. Architecture & system design

### Single source of truth

- **Cockpit composition** is owned by one new module: `packages/chat-surface/src/destinations/run/`. There is no second cockpit — `apps/desktop` consumes it; `apps/frontend` keeps its legacy `ChatScreen` for this phase (adoption deferred, see Non-goals). This honors PLAN §3 decision (a): hoist into the existing `chat-surface`, two consumers.
- **Event projection** is owned by `useEventProjector` → `eventProjector.ts`. Every consumer (surface, swimlanes, chat, timeline, activity) reads a slice; **no consumer re-projects** (FR-3.3). `TcSwimlanes` currently maintains its **own** SSE subscription + bead state (`transport.subscribeServerSentEvents` inside the component) — Phase 3 keeps that as the live-append source for lanes, while the frozen/scrub projection for the surface flows from `useEventProjector`/`projectAt`. The run-session hook (`useRunSession`) is the single owner of the canonical event array fed to `ThreadCanvas.events`; `TcSwimlanes` keeps its incremental stream for lane liveness. _(Convergence of these two event sources onto one is noted as a follow-up risk, R4.)_
- **Mode** is owned by `useRunMode` (KV-backed), NOT by `ThreadCanvas` (which stays controlled via `mode`/`onModeChange`, per its header contract).
- **Right rail** reuses the **hoisted** `WorkspacePane` tab contents (Phase 1F target `packages/chat-surface/src/workspace/`). The Run rail is a _recomposition_ (Chat + Sources/Agents/Approvals), not a fork of `WorkspacePane`.
- **Surface resolution** is owned by `surface-renderers` via `TcSurfaceMount` → `SurfaceRegistry.resolveAdapter(uri)` (URI-scheme → adapter, tier-3 wildcard fallback). Run adds no adapters.
- **Design tokens** are owned by `packages/design-system/src/styles.css` (sky accent confirmed present: `--color-accent: #5fb2ec`). Run removes the residual leaf-level lime so design-system is the only accent source (FR-3.24).

### Boundaries & ports

- `chat-surface` stays framework-agnostic. Ports used: **Transport** (`ports/Transport.ts` → `@0x-copilot/chat-transport`: `request`, `subscribeServerSentEvents`), **Router** (`ports/Router.ts`), **KeyValueStore** (`ports/KeyValueStore.ts` / `providers/KeyValueStoreProvider`), **PresenceSignal** (`ports/PresenceSignal.ts` / `providers/PresenceSignalProvider`). Providers are already installed by `ChatShell` (`TransportProvider`/`RouterProvider`/`KeyValueStoreProvider`/`PresenceSignalProvider`), so `RunDestination` reads them via the existing `useTransport`/`useKeyValueStore` hooks — no new provider wiring.
- No `apps/*`→`apps/*` imports. `apps/desktop` imports `RunDestination` from `@0x-copilot/chat-surface` only (FR-3.28). ESLint substrate rules (Phase 0E) enforce no bare globals (FR-3.27).

### Data flow & key types

- `RuntimeEventEnvelope` (`@0x-copilot/api-types`) — append-only SSE payloads; `sequence_no` monotonic per run (CLAUDE.md streaming model). `isRuntimeEventEnvelope` guard used by `TcSwimlanes`.
- `ThreadCanvasProps` (`thread-canvas/ThreadCanvas.tsx`): `mode`, `conversationId`, `runId`, `events`, `onModeChange`, `tabs`/`activeUri`/`onActivateTab`/`onCloseTab`, `transport`, `pendingDiff`, `onApprove`/`onReject`/`onSuggestChanges`, `scrubbedSeq`/`onScrub`/`onSnapToNow`. Phase 3 **adds** an optional `rightRail?: ReactNode` slot rendered in the chat gridArea (Studio: alongside as tabs; Focus: Chat-only centered) — the minimal change that lets the rail be `[Chat·Sources·Agents·Approvals]` without ThreadCanvas importing `WorkspacePane` (keeps ThreadCanvas dependency-light).
- `EventProjection` (`useEventProjector.ts`): `{ state, surface, swimlanes, chat, timeline, activity }`.
- `TcTab` (`TcTabs.tsx`): `{ uri, title, pinned? }` — surface tab strip.
- `Playhead` (`TcSwimlanes.tsx`): `"now" | { at: number }`; `InlineDiffState`/`InlineDiffEvent` (`TcInlineDiff.tsx`).
- `WorkspacePaneProps` (hoisted): tab inputs (sources/subagents/approvalsQueue/…). Run passes only Sources/Agents/Approvals inputs.
- **New types (this phase):** `RunSession` (`{ runId, status, events, error, retry }`), `RunMode = "studio" | "focus"`, `RunRailTabId = "chat" | "sources" | "agents" | "approvals"`, `RunListItem` (`{ runId, goal, status, startedAt }`).

### Reuse vs new

| Component / module                                 | Disposition                                                                  | Path                                                                                                                   |
| -------------------------------------------------- | ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `ThreadCanvas` (mode host)                         | **Modify** (drop Auto, add `rightRail` slot, 340px, tokens)                  | `packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx`                                                             |
| `TcSurfaceMount`                                   | Reuse (token reconcile)                                                      | `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx`                                                           |
| `TcSwimlanes` (+styles/controls)                   | Reuse (token/lane-color neutralize)                                          | `packages/chat-surface/src/thread-canvas/TcSwimlanes.tsx`, `TcSwimlanes.styles.ts`, `TcSwimlanesTransportControls.tsx` |
| `TcMiniTimeline`                                   | Reuse (neutralize `LANE_COLORS`)                                             | `packages/chat-surface/src/thread-canvas/TcMiniTimeline.tsx`                                                           |
| `TcChat`                                           | **Modify** (Streamdown path, token reconcile; Focus stays chat-only)         | `packages/chat-surface/src/thread-canvas/TcChat.tsx`                                                                   |
| `TcTabs`                                           | Reuse (token reconcile)                                                      | `packages/chat-surface/src/thread-canvas/TcTabs.tsx`                                                                   |
| `TcInlineDiff` (+fixtures)                         | Reuse (token reconcile)                                                      | `packages/chat-surface/src/thread-canvas/TcInlineDiff.tsx`                                                             |
| `useEventProjector` / `eventProjector`             | Reuse                                                                        | `packages/chat-surface/src/thread-canvas/useEventProjector.ts`, `eventProjector.ts`                                    |
| `WorkspacePane` + tabs                             | **Reuse via Phase 1F hoist** (consumed by Run rail)                          | `packages/chat-surface/src/workspace/` (hoisted from `apps/frontend/src/features/chat/components/workspace/`)          |
| `ApprovalCard` / `ApprovalReceipt`                 | **Reuse via Phase 1E hoist**                                                 | `packages/chat-surface/src/approvals/` (hoisted from `apps/frontend/src/features/chat/components/activity/`)           |
| `SubagentFleetCard` / `SubagentCard`               | **Reuse via Phase 1D hoist**                                                 | `packages/chat-surface/src/subagents/`                                                                                 |
| Markdown / Streamdown renderer                     | **Reuse via Phase 1A hoist** (+ Streamdown dep, see R6)                      | `packages/chat-surface/src/messages/`                                                                                  |
| `SheetRenderer` / `SheetDiffView`                  | Reuse (token reconcile)                                                      | `packages/surface-renderers/src/sheet/`                                                                                |
| `RunDestination` (host composition)                | **New**                                                                      | `packages/chat-surface/src/destinations/run/RunDestination.tsx`                                                        |
| `RunHeader` (`.ws-head` + mode control)            | **New**                                                                      | `packages/chat-surface/src/destinations/run/RunHeader.tsx`                                                             |
| `RunWorkspaceRail` (Chat+Sources+Agents+Approvals) | **New**                                                                      | `packages/chat-surface/src/destinations/run/RunWorkspaceRail.tsx`                                                      |
| `useRunSession` (SSE + events + run resolution)    | **New**                                                                      | `packages/chat-surface/src/destinations/run/useRunSession.ts`                                                          |
| `useRunMode` (KV mode + `⌘M`)                      | **New**                                                                      | `packages/chat-surface/src/destinations/run/useRunMode.ts`                                                             |
| `RunEmptyState` (goal composer)                    | **New**                                                                      | `packages/chat-surface/src/destinations/run/RunEmptyState.tsx`                                                         |
| `RunMultiSelect` (run picker)                      | **New**                                                                      | `packages/chat-surface/src/destinations/run/RunMultiSelect.tsx`                                                        |
| Run module barrel                                  | **New**                                                                      | `packages/chat-surface/src/destinations/run/index.ts`                                                                  |
| `DesktopPlaceholder` (run path)                    | **Delete from run mount** (kept only until FR-3.2 lands; removed in PR-3.11) | `apps/desktop/renderer/DesktopPlaceholder.tsx`                                                                         |

> **Path status (verified against the worktree at authoring):** existing today — `thread-canvas/*` (all `Tc*`, `ThreadCanvas.tsx`, `eventProjector.ts`, `useEventProjector.ts`), `destinations/chats/ChatsDestination.tsx`, `surfaces/GenericStructuredDiff.tsx`, `surface-renderers/src/{_shared/palette.ts,sheet/SheetRenderer.tsx,sheet/SheetDiff.tsx}` (public export alias `SheetDiffView` = `SheetDiff`), `ports/{Transport,Router,KeyValueStore,PresenceSignal}.ts`, `providers/{Transport,KeyValueStore,Router,PresenceSignal}Provider.tsx` with `useTransport`/`useKeyValueStore`, `shell/{ChatShell.tsx,destinations.ts,RightRailTabs.tsx}`, `apps/desktop/renderer/{bootstrap.tsx,DesktopPlaceholder.tsx}`. **Contingent (created by their upstream Phase 1 PR, not present yet):** `chat-surface/src/messages/` markdown+Streamdown export (1A), `chat-surface/src/subagents/` (1D), `chat-surface/src/approvals/` (1E), `chat-surface/src/workspace/` with `SourcesTab`/`AgentsTab`/`ApprovalsTab` (1F, source lives at `apps/frontend/src/features/chat/components/workspace/`). **New this phase:** everything under `chat-surface/src/destinations/run/`. Rows that depend on a contingent path carry an explicit upstream dep in §7/§10.

---

## 6. Affected files / component inventory

### Create

- `packages/chat-surface/src/destinations/run/RunDestination.tsx`
- `packages/chat-surface/src/destinations/run/RunDestination.test.tsx`
- `packages/chat-surface/src/destinations/run/RunHeader.tsx` + `.test.tsx`
- `packages/chat-surface/src/destinations/run/RunWorkspaceRail.tsx` + `.test.tsx`
- `packages/chat-surface/src/destinations/run/useRunSession.ts` + `.test.ts`
- `packages/chat-surface/src/destinations/run/useRunMode.ts` + `.test.ts`
- `packages/chat-surface/src/destinations/run/RunEmptyState.tsx` + `.test.tsx`
- `packages/chat-surface/src/destinations/run/RunMultiSelect.tsx` + `.test.tsx`
- `packages/chat-surface/src/destinations/run/index.ts`

### Modify

- `packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx` — drop Auto (`ThreadMode`, `MODE_VALUES`, `MODE_LABELS`, `resolvedMode` auto branch, `handleExpandToStudio` unchanged); add `rightRail?: ReactNode` slot; Studio `340px`; token reconcile. Update `ThreadCanvas.test.tsx`.
- `packages/chat-surface/src/thread-canvas/TcChat.tsx` — Streamdown path for GFM tables; token reconcile; `TcChatMode` narrows to `"studio" | "focus"`. Update `TcChat.test.tsx`.
- `packages/chat-surface/src/thread-canvas/{TcTabs,TcInlineDiff,TcSurfaceMount}.tsx` + `TcSwimlanes.styles.ts` — lime → sky token reconcile.
- `packages/chat-surface/src/thread-canvas/TcMiniTimeline.tsx` — neutralize `LANE_COLORS` map to monochrome tokens.
- `packages/chat-surface/src/thread-canvas/index.ts` — export narrowed `ThreadMode`; add `rightRail` type surface if needed.
- `packages/chat-surface/src/surfaces/GenericStructuredDiff.tsx` — lime → sky.
- `packages/surface-renderers/src/_shared/palette.ts`, `packages/surface-renderers/src/sheet/{SheetRenderer,SheetDiff}.tsx` — lime → sky/semantic tokens.
- `packages/chat-surface/src/index.ts` — export `RunDestination`, `RunMode`, run types.
- `apps/desktop/renderer/bootstrap.tsx` — route `activeDestination === "run"` to `<RunDestination …>`; keep `DesktopPlaceholder` for other slugs until Phase 4.
- `packages/chat-surface/src/destinations/chats/ChatsDestination.tsx` — remove the "ThreadCanvas mounts here (Phase 2-B)" placeholder note (canvas now lives in Run, not Chats). _(Light touch; Chats reopen→Run is Phase 4.)_

### Delete (in PR-3.11, after outlet wired)

- Remove `DesktopPlaceholder` usage on the `run` path (component may remain for non-Run slugs until Phase 4 removes it wholesale — do NOT delete the file this phase if other slugs still use it).

### Gap flags (code diverges from plan/spec — must be handled here)

1. **Auto still present** in `ThreadCanvas.tsx` (`ThreadMode` union, `MODE_VALUES`, resolution). PLAN/DESIGN-SPEC dropped it → FR-3.5.
2. **Residual lime `#c2ff5a`** in 10 files under `chat-surface` + `surface-renderers` (grep-confirmed). Phase 0C reconciled design-system + top-level; these leaf files remain → FR-3.24.
3. **No right-rail slot** in `ThreadCanvas`; its chat column is plain `TcChat`. DESIGN-SPEC §2 wants `[Chat·Sources·Agents·Approvals]` → FR-3.10/3.11 + new `rightRail` prop.
4. **`WorkspacePane` not yet hoisted** (still `apps/frontend/src/features/chat/components/workspace/`). Run rail depends on Phase 1F. If 1F lands hoisting to `packages/chat-surface/src/workspace/`, Run consumes it; otherwise Run is blocked (see §10).
5. **`TcChat` ignores `events`** — it fetches messages via `transport.request` GET, not the projector/SSE; no streaming markdown. Streaming (FR-3.19) and live chat need wiring; `TcChat` has no Streamdown path today (uses `PlainText`/`Reasoning`).
6. **Streamdown not in the repo** (grep found no `streamdown` dependency). FR-3.19 requires adding it (Phase 1A markdown hoist may bring it; if not, add as a `chat-surface` dep) → risk R6.
7. **`TcMiniTimeline.LANE_COLORS`** hardcodes per-lane hues (email/sheet/sf-opp/slide). DESIGN-SPEC §2 "lane color neutralized" → FR-3.18.
8. **Shell `destinations.ts` still 12-dest** (no `run` slug). `run` slug + outlet come from **Phase 2 (2B/2E)** — upstream dependency; this PRD assumes it.
9. **`RightRailTabs` (shell) has its own Activity/Approvals** — distinct from `WorkspacePane`. DESIGN-SPEC §2 uses the `WorkspacePane` tab set for the Run rail; the shell `RightRail` is suppressed on full-bleed destinations (Run behaves full-bleed). Do not double-render.
10. **`TcSurfaceMount` HostControls** show generic Reject/Suggest/Approve; the per-row `Approve & sign / ✓ Signed / Rejected / Queued` states (DESIGN-SPEC §2) live in the surface adapter's `renderDiff` (sheet) — reconcile labels/states there (FR-3.21).

---

## 7. PR / commit breakdown

Ordered; each independently mergeable, ≤~1000 LOC, leaves `main` + web green. Web is unaffected (no web importer of ThreadCanvas/RunDestination).

**PR-3.1 — Drop Auto mode from ThreadCanvas** _(S, ~250 LOC)_

- Scope: Narrow `ThreadMode` to `"studio" | "focus"`; remove `MODE_VALUES.auto`, `MODE_LABELS.auto`, `resolvedMode` auto branch; simplify `gridStyleFor`; two-tab switcher. Narrow `TcChatMode`.
- Files: `thread-canvas/ThreadCanvas.tsx`, `TcChat.tsx`, `thread-canvas/index.ts`, `ThreadCanvas.test.tsx`, `TcChat.test.tsx`.
- Deps: none.
- Acceptance: type + tests green; no `"auto"` string remains in thread-canvas; single-mount invariant tests still pass.

**PR-3.2 — Token reconciliation (lime → sky) across canvas + surface-renderers** _(M, ~400 LOC touched, mostly constants)_

- Scope: Replace `#c2ff5a`/lime palettes with `--color-accent*`/semantic tokens in `TcChat`, `TcTabs`, `TcInlineDiff`, `TcSurfaceMount`, `ThreadCanvas`, `TcSwimlanes.styles.ts`, `surfaces/GenericStructuredDiff.tsx`, `surface-renderers/_shared/palette.ts`, `surface-renderers/sheet/*`; neutralize `TcMiniTimeline.LANE_COLORS`.
- Files: the 10 grep-confirmed files + their snapshot tests.
- Deps: none (parallel with 3.1).
- Acceptance: `grep -r "c2ff5a" packages/chat-surface/src packages/surface-renderers/src` returns nothing; visual snapshot/style tests updated; jade=success, ember=danger asserted.

**PR-3.3 — `useRunSession` host hook (SSE + events + run resolution)** _(M, ~450 LOC)_

- Scope: Resolve active/selected run for a conversation via `Transport.request`; subscribe to `/v1/agent/runs/{runId}/stream` via `subscribeServerSentEvents`; accumulate ordered `RuntimeEventEnvelope[]` (stable append); expose `{ runId, runs, status, events, error, retry, selectRun }`.
- Files: `destinations/run/useRunSession.ts` + `.test.ts`.
- Deps: none.
- Acceptance: unit tests with a mock transport assert append-only growth, dedupe by `sequence_no`, error→retry, multi-run list, and a `connecting`/loading phase before the first event (FR-3.33).

**PR-3.4 — `useRunMode` (KV mode) + `⌘M` toggle** _(S, ~200 LOC)_

- Scope: KV-backed `RunMode` per conversation; legacy `"auto"`→`"studio"` coercion; `⌘M` global handler gated to Run + not-in-text-input.
- Files: `destinations/run/useRunMode.ts` + `.test.ts`.
- Deps: PR-3.1.
- Acceptance: tests assert persistence via KV port fake, `auto` coercion, `⌘M` toggles, ignores when composer focused.

**PR-3.5 — `RunDestination` shell + `RunHeader` + desktop outlet** _(M, ~500 LOC)_

- Scope: Compose `RunHeader` (`.ws-head` + mode segmented control) + `ThreadCanvas` (Studio 1fr/340px + timeline); wire `useRunSession`+`useRunMode`; add `rightRail` slot to `ThreadCanvas`; route desktop `run` slug to `RunDestination`.
- Files: `destinations/run/RunDestination.tsx` + test, `RunHeader.tsx` + test, `destinations/run/index.ts`, `thread-canvas/ThreadCanvas.tsx` (add `rightRail`), `chat-surface/src/index.ts`, `apps/desktop/renderer/bootstrap.tsx`.
- Deps: PR-3.1, PR-3.3, PR-3.4.
- Acceptance: desktop mounts `RunDestination` on `run` (RTL test on bootstrap wiring); single projection asserted; ThreadCanvas renders center+timeline; per-pane loading copy renders before the first event and swaps to content without remount (FR-3.33); web unchanged.

**PR-3.6 — `RunWorkspaceRail`: `[Chat · Sources · Agents · Approvals]`** _(M, ~500 LOC)_

- Scope: Recompose hoisted `WorkspacePane` tab contents into the Run rail; Chat tab = `TcChat`; badges; Focus collapses to Chat-only. Feed as `rightRail` to `ThreadCanvas`.
- Files: `destinations/run/RunWorkspaceRail.tsx` + test; `RunDestination.tsx` (wire rail).
- Deps: PR-3.5, **Phase 1F** (WorkspacePane hoisted to `packages/chat-surface/src/workspace/`).
- Acceptance: tablist a11y + default Chat + count badges + empty copy tests; Draft/Skills absent; Focus hides non-chat tabs.

**PR-3.7 — Timeline scrub ↔ surface time-travel + snap-to-now** _(M, ~450 LOC)_

- Scope: Wire `TcSwimlanes`/`TcMiniTimeline` scrub to `scrubbedSeq`; `snapSet` switches surface tab; "Viewing…" banner; hide approvals + disable composer off-now; `⌘L`/Escape snap.
- Files: `RunDestination.tsx`, `thread-canvas/ThreadCanvas.tsx` (scrub plumbing), scrub tests in `RunDestination.test.tsx`; `TcSwimlanes.test.tsx`/`TcMiniTimeline.test.tsx` extended.
- Deps: PR-3.5.
- Acceptance: scrub→banner→approvals-hidden→snap-to-now→re-enabled asserted; step keys; pinned beads persisted.

**PR-3.8 — Parallel subagents: lanes + inline fleet card + Agents tab** _(M, ~400 LOC)_

- Scope: Render `SubagentFleetCard` inline in chat from the projection; one live lane per subagent; Agents tab live count.
- Files: `RunDestination.tsx`, `RunWorkspaceRail.tsx` (Agents count), `TcChat.tsx` (fleet card slot), tests.
- Deps: PR-3.6, **Phase 1D** (subagent cards hoisted).
- Acceptance: dispatch of N → fleet card + N lanes + Agents "N live"; completion updates without sibling remount; zero-subagent empty.

**PR-3.9 — Streaming: Streamdown chat tables + surface snapshot diffs** _(M, ~450 LOC)_

- Scope: Route GFM tables in `TcChat` through the citation-safe streaming markdown (Streamdown) with blinking cursor; center pane large/editable tabular surfaces via `surface-renderers` snapshot diffs + `streaming · N%` chip.
- Files: `TcChat.tsx`, `TcSurfaceMount.tsx` (streaming chip), tests; `chat-surface` package manifest (Streamdown dep if not already from Phase 1A).
- Deps: PR-3.5, **Phase 1A** (markdown renderer hoist).
- Acceptance: partial-table stream never emits raw pipes; cursor present; surface stream shows diff + %; citation plugin intact.

**PR-3.10 — Approvals: on-surface inline + in-chat ApprovalCard + Focus conf-card** _(M, ~500 LOC)_

- Scope: Per-row inline approval states (`Approve & sign`/`✓ Signed`/`Rejected`/`Queued`) in sheet adapter `renderDiff`; in-chat `ApprovalCard` + `ApprovalReceipt`; Focus `.conf-card`; `TcInlineDiff` state machine wiring; approvals hidden while scrubbed.
- Files: `surface-renderers/sheet/SheetDiff.tsx` (row states), `RunDestination.tsx` (approve/reject handlers), `TcChat.tsx` (conf-card in Focus), tests.
- Deps: PR-3.6, PR-3.7, **Phase 1E** (ApprovalCard/Receipt hoisted).
- Acceptance: pending-row highlight + label states; approve→signed; reject→rejected; scrubbed hides approvals; `InvalidInlineDiffTransitionError` on bad transitions.

**PR-3.11 — Run empty/idle + multi-run states + smoke/docs cleanup** _(M, ~450 LOC)_

- Scope: `RunEmptyState` (goal composer) when no run; `RunMultiSelect` when >1 run; transition empty→live without shell remount; remove `DesktopPlaceholder` from the `run` path; README/SMOKE note; ensure no dead placeholder string in `ChatsDestination`.
- Files: `destinations/run/RunEmptyState.tsx` + test, `RunMultiSelect.tsx` + test, `RunDestination.tsx` (state selection), `apps/desktop/renderer/bootstrap.tsx`, `destinations/chats/ChatsDestination.tsx`, `apps/desktop/SMOKE.md`, `apps/desktop/README.md`.
- Deps: PR-3.5, PR-3.3.
- Acceptance: empty state renders + goal-submit transition; multi-run selection rebinds; live desktop smoke passes (§8); no `DesktopPlaceholder` on run; no placeholder strings.

---

## 8. Testing plan

Runner: **vitest** for all TS packages/apps via `npm run test --workspace @0x-copilot/chat-surface` / `--workspace @0x-copilot/surface-renderers` / `--workspace @0x-copilot/desktop`. Live smoke per `apps/desktop/SMOKE.md`.

### Unit

- `thread-canvas/ThreadCanvas.test.tsx` — **FR-3.5/3.6/3.9**: switcher renders exactly `Studio`+`Focus`; ArrowLeft/Right cycles two values; asserts `TcSurfaceMount`/`TcChat`/composer instance identity survives a Studio→Focus→Studio switch (no remount, e.g. via a mounted-count ref); no `"auto"` in DOM/`data-mode`.
- `thread-canvas/ThreadCanvas.test.tsx` — **FR-3.4**: Studio grid `1fr 340px`; Focus centers rail, `data-visible="false"` on surface slot, timeline minimized.
- `destinations/run/useRunSession.test.ts` — **FR-3.31/3.32/3.33**: append-only events grow by reference; dedupe by `sequence_no`; SSE error sets `error` + `retry` re-subscribes; multi-run list resolution; exposes a `connecting`/`status: "loading"` phase before the first event.
- `destinations/run/RunDestination.test.tsx` — **FR-3.33**: with a mock transport that has not yet emitted, asserts the per-pane loading copy renders (chat `role="status"` "Loading messages…", timeline "Listening for run events…", surface placeholder), no full-screen blocking spinner, and that the first scripted event swaps to content with `TcChat`/`TcSurfaceMount` instance identity preserved (no remount).
- `destinations/run/useRunMode.test.ts` — **FR-3.7/3.8**: KV persistence via fake `KeyValueStore`; legacy `"auto"`→`"studio"`; `⌘M` toggles; suppressed when a text input is focused.
- `destinations/run/RunWorkspaceRail.test.tsx` — **FR-3.10/3.11/3.12/3.13**: tab order `Chat·Sources·Agents·Approvals`, Chat default, `role="tablist"/tab/tabpanel`, arrow-key nav; Agents "N live"/Approvals pending badge; Draft/Skills absent; Focus → Chat-only.
- `destinations/run/RunDestination.test.tsx` — **FR-3.1/3.3/3.15/3.16**: one `useEventProjector` call per render (spy); scrub → surface tab switch + "Viewing" banner + approvals hidden + composer disabled; snap-to-now re-enables.
- `thread-canvas/TcMiniTimeline.test.tsx` / `TcSwimlanes.test.tsx` — **FR-3.14/3.18**: lane colors are token/monochrome (no hardcoded hue); pinned bead persisted to KV; `.now`/`.cur`/`.future` bead classes.
- `thread-canvas/TcChat.test.tsx` — **FR-3.19**: streaming partial GFM table renders via markdown path (no raw pipe leak); blinking cursor present while streaming.
- `surface-renderers/sheet/SheetDiff.test.tsx` — **FR-3.20/3.21**: pending row highlight + `Approve & sign`; resolved → `✓ Signed` (jade) / `Rejected` (ember) / `Queued` (muted); `streaming · N%` chip.
- `thread-canvas/TcInlineDiff.test.tsx` — **FR-3.23**: `idle→streaming→pending→accepted|rejected`; invalid transition throws `InvalidInlineDiffTransitionError`.
- `destinations/run/RunEmptyState.test.tsx` — **FR-3.25**: renders goal composer when no run; submit fires start callback.
- `destinations/run/RunMultiSelect.test.tsx` — **FR-3.26**: lists runs (goal/status/time); selection fires `selectRun`.
- `thread-canvas/*` token tests / `surface-renderers` snapshot tests — **FR-3.24**: no `#c2ff5a`; accent = `--color-accent`.
- a11y assertions across the above — **FR-3.29/3.30**: focus-ring style, roles, `prefers-reduced-motion` zeroing; light/dark render.

### Integration (mocked transport)

- `RunDestination.test.tsx` with a mock `Transport` streaming a scripted `RuntimeEventEnvelope[]` (model_delta → surface update → subagent dispatch → approval pending → run_completed): asserts beads append, fleet card + lanes appear (**FR-3.17**), inline approval becomes pending then signed (**FR-3.21/3.22**), timeline reaches now.
- SSE-drop mid-stream: error banner (`role="alert"` + Retry) with last-projected state retained (**FR-3.32**).
- Boundary: an ESLint/substrate test (or `npm run lint --workspace @0x-copilot/chat-surface`) confirms `destinations/run/*` has no bare `window`/`document`/`fetch`/`localStorage` (**FR-3.27**); a grep/CI check confirms `apps/desktop` does not import `apps/frontend/src` (**FR-3.28**).

### E2E / live smoke (`apps/desktop/SMOKE.md`)

Stage runtime (`node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64`), then `COPILOT_RUNTIME_DIR=… npm run dev --workspace @0x-copilot/desktop`. Steps:

1. Boot → sign in (dev IdP) → land on **Run**; empty/idle state shows goal composer (**FR-3.25**).
2. Submit a goal → live run starts; center surface + Chat tab + timeline populate from one stream (**FR-3.1**). _(Live per PLAN §12 / memory: unit fakes have hidden real-run breakage.)_
3. `⌘M` → Focus (surface hidden, rail centered, timeline minimized); `⌘M` → Studio; verify chat scroll + surface tab + composer draft preserved (**FR-3.2/3.9**).
4. Scrub timeline back → "Viewing…" banner, approvals hidden, composer disabled; `⌘L` → live (**FR-3.15/3.16**).
5. Trigger a parallel fan-out → inline fleet card + N lanes + Agents "N live" (**FR-3.17**).
6. Reach an approval → approve on-surface (`Approve & sign` → `✓ Signed`) and via in-chat `ApprovalCard`; verify `ApprovalReceipt` (**FR-3.21/3.22**).
7. Toggle theme (dark/light) + `[data-accent]` → single sky accent, no lime, neutral lanes (**FR-3.24/3.18**).

### Regression guard (web behaviorally identical)

- `npm run typecheck --workspace @0x-copilot/frontend` + `npm run test --workspace @0x-copilot/frontend` green. `apps/frontend` imports neither `RunDestination` nor `ThreadCanvas` (grep-verified); its `ChatScreen`/`WorkspacePane` behavior is unchanged. If Phase 1F re-exports `WorkspacePane` from `chat-surface`, confirm the web re-export shim still passes `WorkspacePane.test.tsx` (Phase 1 gate, re-checked here).

### FR → test map

FR-3.1→RunDestination.test; 3.2→bootstrap/RunDestination.test + smoke; 3.3→RunDestination.test (projector spy); 3.4→ThreadCanvas.test; 3.5/3.6/3.9→ThreadCanvas.test; 3.7/3.8→useRunMode.test; 3.10–3.13→RunWorkspaceRail.test; 3.14/3.18→TcSwimlanes/TcMiniTimeline.test; 3.15/3.16→RunDestination.test + smoke; 3.17→RunDestination integration; 3.19→TcChat.test; 3.20/3.21→SheetDiff.test; 3.22→RunDestination integration + smoke; 3.23→TcInlineDiff.test; 3.24→token/snapshot tests; 3.25→RunEmptyState.test + smoke; 3.26→RunMultiSelect.test; 3.27/3.28→lint/CI boundary check; 3.29/3.30→a11y/theme assertions; 3.31/3.32→useRunSession.test + integration; 3.33→useRunSession.test + RunDestination.test (loading state).

---

## 9. UI/UX acceptance checklist

Grounded in DESIGN-SPEC §0 (tokens/dims) + §2 (Run cockpit). Tokens are the design-system `--color-*` names (sky accent confirmed `--color-accent: #5fb2ec`); DESIGN-SPEC §0 short names (`--sky`/`--panel`/`--line`) are the prototype aliases.

**Layout & dims**

- [ ] Studio: `grid-template-columns: 1fr 340px` — work surface left, rail right, timeline bottom (FR-3.4). _(current code `360px` → change to 340px.)_
- [ ] Focus: rail centered `max-width ≤ 780px`, work surface hidden, timeline minimized.
- [ ] Run header `.ws-head`: agent avatar + "ACTIVE RUN" mono kicker + goal `<h2>` (`--font-display`, 600, −.01em) + right-aligned mode segmented control.
- [ ] Topbar suppressed on Run (Run owns full height) — shell treats Run as full-bleed (no 224px context column, no shell RightRail double-render).

**States (per surface)**

- [ ] Mode switcher: default / hover / **active** (`--color-accent` fill + `--color-accent-contrast` text) / focus-visible (`2px solid var(--color-accent)` offset 2).
- [ ] Right-rail tabs: default / hover / active (accent underline) / focus-visible; badge (accent bg, mono).
- [ ] Surface tabs (`TcTabs`): active = accent bottom-border (not lime); close affordance on hover.
- [ ] Timeline: LIVE vs VIEWING label; beads `.now` = jade pulse, `.cur` = accent + ring, `.future` = hollow; draggable 2px accent head-line; lane labels mono, **lane color neutralized** to `--color-surface-muted`/`--color-text`.
- [ ] Loading (FR-3.33): chat "Loading messages…" (`role="status"`); timeline "Listening for run events…"; surface adapter placeholder; non-blocking (no full-screen spinner), swaps to content without remount.
- [ ] Empty: Run empty/idle goal composer ("Give it a goal…"); per-tab empty copy (Sources/Agents/Approvals); mini-timeline "No activity yet".
- [ ] Error: SSE `role="alert"` banner + Retry (state preserved); surface adapter fallback ("No adapter registered for …").
- [ ] Streaming: chat blinking cursor; surface `streaming · N%` chip; inline-diff indeterminate/determinate progress bar.
- [ ] Approvals: pending row highlight (`--color-accent-soft` + inset accent bar); `Approve & sign` / `✓ Signed` (jade) / `Rejected` (ember) / `Queued` (muted); Focus `.conf-card` ("The agent paused here — it won't sign until you approve").
- [ ] Scrubbed: chat ghost banner "Viewing HH:MM" (accent, uppercase, `--font-size-xs`), messages dimmed + non-interactive, composer disabled ("Snap to now to send a message"), approvals hidden.

**a11y**

- [ ] Roles: mode + rail + surface tabs = `tablist`/`tab`/`tabpanel`; timeline = `region`; approval controls = `group`; status banners = `status`; error = `alert`.
- [ ] Keyboard: ArrowLeft/Right within each tablist; `⌘M` mode; `⌘←`/`⌘→` step, `⌘L`/Escape snap-to-now; `⌘↵` approve / `⌘⌫` reject (in-chat); Tab reaches every control with visible focus ring.
- [ ] Focus management: mode switch does not steal focus / preserves composer focus; scrubbed composer disabled state is announced.
- [ ] `prefers-reduced-motion` / `[data-reduce-motion=1]`: grid-template transition (currently 300ms), bead pulse, inline-diff animation all zeroed.
- [ ] Contrast: sky accent on `--color-accent-contrast`; jade/ember on neutral surfaces meet AA.

**Theming & density**

- [ ] Light + dark render from tokens only; `[data-accent]` sky/jade/ember/violet respected (accent swatch changes cockpit accent, not semantics).
- [ ] `[data-density=compact|spacious]` spacing respected in header/rail/timeline.
- [ ] **Single-accent discipline:** zero `#c2ff5a` anywhere; jade only for live/success, ember only for destructive; no decorative per-connector/lane color.

**Component reuse noted**

- [ ] Right rail reuses hoisted `WorkspacePane` tab contents (`SourcesTab`/`AgentsTab`/`ApprovalsTab`), restyled to tokens.
- [ ] In-chat approvals reuse hoisted `ApprovalCard`/`ApprovalReceipt`.
- [ ] Fleet card reuses hoisted `SubagentFleetCard`.
- [ ] Surface diffs reuse `surface-renderers` `SheetRenderer`/`SheetDiffView`.

---

## 10. Dependencies & sequencing

**Upstream (blocked by):**

- **Phase 1** hoists (consumed by Run): 1A markdown/Streamdown (`messages/`), 1D subagent/fleet cards (`subagents/`), 1E ApprovalCard/Receipt (`approvals/`), 1F WorkspacePane (`workspace/`). PR-3.6 needs 1F; PR-3.8 needs 1D; PR-3.9 needs 1A; PR-3.10 needs 1E.
- **Phase 2**: 2B (`destinations.ts` → 6-dest with `run` slug), 2E (destination outlet mounts `children` per slug). Without the `run` slug + outlet, `RunDestination` has nowhere to mount (PR-3.5 wiring). This PRD assumes Phase 2 landed; if not, PR-3.5 must also add a temporary `run`-slug local-state route in `bootstrap.tsx`.
- **Phase 0**: 0B/0C tokens (sky accent present — confirmed), 0D `DeploymentProfile` (Run is not gated, so soft dep), 0E ESLint substrate guard (FR-3.27).

**Internal order (DAG):** PR-3.1, PR-3.2, PR-3.3 (parallel) → PR-3.4 (needs 3.1) → PR-3.5 (needs 3.1/3.3/3.4) → PR-3.6 (needs 3.5 + 1F), PR-3.7 (needs 3.5) → PR-3.8 (needs 3.6 + 1D), PR-3.9 (needs 3.5 + 1A) → PR-3.10 (needs 3.6/3.7 + 1E) → PR-3.11 (needs 3.5/3.3).

**Downstream (blocks):**

- **Phase 4 (4A Chats)** reopen-into-Run reuses `RunDestination`.
- **Phase 6 (6A ⌘K, 6B shortcuts)** extends the Run shortcut set; **6D live smoke** exercises the full Run path; **6C** removes any residual `DesktopPlaceholder`.

Must form a DAG — it does (no cycles).

---

## 11. Risks & mitigations

| #   | Risk                                                                               | Likelihood | Mitigation / rollback                                                                                                                                                                                                                     |
| --- | ---------------------------------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1  | Phase 1F (WorkspacePane hoist) slips → Run rail blocked                            | Med        | PR-3.6 depends on it; if slipped, temporarily import a thin `RunWorkspaceRail` reading the same tab-content components from a `chat-surface` re-export shim; do NOT import `apps/frontend/src` (boundary). Land 3.1–3.5+3.7 meanwhile.    |
| R2  | Dropping Auto breaks a persisted `"auto"` KV value                                 | Low        | `useRunMode` coerces `"auto"`→`"studio"` on read (FR-3.7); unit-tested.                                                                                                                                                                   |
| R3  | Single-mount invariant regresses (mode switch remounts, losing scroll/draft/scrub) | Med        | Keep JSX shape invariant; visibility via CSS not tree-swap (existing ThreadCanvas contract); explicit no-remount test (FR-3.9).                                                                                                           |
| R4  | Two event sources (`useRunSession` array vs `TcSwimlanes` own SSE) diverge         | Med        | Feed both from the same `runId`; assert bead parity in integration; follow-up ticket to converge `TcSwimlanes` onto the projector post-Phase 3.                                                                                           |
| R5  | Live run breaks despite green unit fakes (history: dep bumps)                      | Med-High   | Mandatory **live** desktop smoke in PR-3.11 (PLAN §12 / memory); do not close phase on unit green alone.                                                                                                                                  |
| R6  | Streamdown not in repo / bundle-size or CSP concerns on desktop                    | Med        | Bring Streamdown via Phase 1A markdown hoist; if unavailable, PR-3.9 falls back to the existing `MarkdownText` streaming path (still citation-safe) and files a follow-up — GFM table streaming degrades to block-render, not corruption. |
| R7  | Residual lime reconcile changes surface-renderer snapshots broadly                 | Low        | PR-3.2 isolated to constants; update snapshots in the same PR; jade/ember semantics asserted.                                                                                                                                             |
| R8  | Web regression from shared `chat-surface` edits                                    | Low        | Web does not import ThreadCanvas/Run; run web typecheck+tests as gate (FR regression guard).                                                                                                                                              |
| R9  | `⌘M` collides with browser/OS or fires in inputs                                   | Low        | Gate handler to Run active + not-in-text-input; reduce-motion respected; documented in SHORTCUTS (Phase 6).                                                                                                                               |

**Flagging strategy:** Run destination is only reachable via the `run` slug; until PR-3.11, empty/multi-run states guard against half-wired runs. No feature flag needed (desktop-only surface, web untouched); rollback = revert the `bootstrap.tsx` outlet line to `DesktopPlaceholder`.

---

## 12. Definition of done

- [ ] All FR-3.1–FR-3.33 met and mapped to ≥1 passing test (§8 map).
- [ ] Run cockpit loading/connecting state (per-pane, non-blocking) renders before the first event and swaps to content without remount (FR-3.33).
- [ ] `ThreadMode` is `studio | focus` only; no `"auto"` anywhere in `chat-surface`.
- [ ] `RunDestination` mounts on the desktop `run` slug (replacing `DesktopPlaceholder`); center surface + `[Chat·Sources·Agents·Approvals]` rail + bottom timeline render from **one** event projection.
- [ ] Studio/Focus + `⌘M` work with the single-mount invariant preserved (scroll/tab/scrub/draft survive).
- [ ] Timeline scrub/step/snap-to-now + "Viewing…" banner + approvals-hidden-while-scrubbed verified; pinned beads persist.
- [ ] Parallel subagents show as fleet card + lanes + Agents count.
- [ ] On-surface inline approvals (`Approve & sign`/`✓ Signed`/`Rejected`/`Queued`) + in-chat `ApprovalCard`/`ApprovalReceipt` + Focus `.conf-card` all function; `TcInlineDiff` state machine correct.
- [ ] Streaming chat GFM tables (Streamdown or documented fallback) + surface snapshot diffs (`streaming · N%`) render without corruption.
- [ ] Run empty/idle + multi-run states designed and functional.
- [ ] Zero `#c2ff5a`; single sky accent; jade=live/success, ember=destructive; lanes neutralized. Light+dark+density+reduce-motion pass the UI checklist.
- [ ] `chat-surface` framework-agnostic (ports only; ESLint substrate green); `apps/desktop` imports Run from `@0x-copilot/chat-surface`; no `apps/*`→`apps/*` import.
- [ ] Unit + integration green (`npm run test --workspace @0x-copilot/chat-surface|surface-renderers|desktop`); web typecheck+tests green (unregressed).
- [ ] **Live** desktop smoke (§8 E2E) passes end-to-end (boot → run → mode → scrub → subagents → approve).
- [ ] `apps/desktop/README.md` + `SMOKE.md` updated for the Run cockpit; no dead placeholder strings (`ChatsDestination`) or `DesktopPlaceholder` on the run path; no dead code left.
