# Phase 2.D (v2): tc-chat-composer

## Vision

`TcChat` and the Composer family are the **chat-as-right-rail** primitives
inside the ThreadCanvas grid. Chat is not the center of the product
(`project_atlas_product_model`); it is the place where the user converses
with the agent _about_ a surface — the email being drafted, the sheet
being edited, the slide being assembled. The composer is the only place
in the app where the user originates a turn.

Three modes shape this surface (PRD §5 Phase 2 row 2D):

- **Studio**: full message list with composer at the bottom — this is the
  default working stance.
- **Auto**: same as Studio with reduced chrome (the chat is just there,
  watching the agent run).
- **Focus**: chat collapses to Activity / Approvals tabs only; the
  composer is hidden. This is the "agent owns the room" stance — the user
  is reviewing, not steering.

The other load-bearing behaviour: `TcChat` is the **viewer** for swimlane
scrub state (D26). When `TcSwimlanes` is scrubbed off-now, `TcChat` shows
**ghost-message previews** of the conversation at-time-T — muted opacity,
"viewing HH:MM:SS" banner. The reverse direction — scrub state pushed
from chat to swimlanes — does not exist; that would couple two destination
primitives and create two truths about playhead time. The scrub state
flows one way, via React context (`SwimlaneScrubContext`).

Staff-engineer take applied to this phase's primitives:

- **DRY.** Reuse `PlainText` / `Reasoning` from `messages/` for message
  rendering. Reuse `useTransport` from the existing port. No new message
  primitive; no new transport client.
- **Substitution.** `Composer` exposes `onSend(text)` as a pure callback;
  it does not own the send semantics. `TcChat` exposes `onApprove` /
  `onReject` for surface diffs as pure callbacks — those land at
  `TcSurfaceMount` (D28). Composer and TcChat are event emitters, not
  controllers.
- **Simple & elegant.** Picker open/close state is local `useState` in
  the composer; no popover framework. The mention popover detects `@` by
  inspecting the textarea's selection cursor — no rich-text editor, no
  contenteditable. Auto-grow is a controlled `<textarea>` whose height is
  set from its scrollHeight.
- **Single source of truth.** Swimlane scrub state lives in one context
  (`SwimlaneScrubContext`). `TcChat` reads it via `useSwimlaneScrub()` —
  it does not maintain a parallel "viewing-time" state of its own.
- **Substrate purity.** No `window`, `document`, `fetch`, `localStorage`,
  `EventSource`. Tool / model / mention popovers render inline in the
  document flow by default; an optional `portalTarget?: HTMLElement` prop
  lets the host (web frontend, desktop renderer) mount them in an
  overlay layer. Default is **not** to portal to `document.body` (the
  package's ESLint config forbids `document`).

## Status

- Status: done
- Agent slug: `tc-chat-composer-v2`
- Branch: `desktop/phase-2-tc-chat-composer-v2`
- Worktree: `.claude/worktrees/agent-acbcaa3316ef57d4a`
- Created: 2026-05-17
- Audited: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-2/2D-tc-chat-composer-v2.md` — this file.
- `packages/chat-surface/src/thread-canvas/TcChat.tsx` — NEW.
- `packages/chat-surface/src/thread-canvas/TcChat.test.tsx` — NEW.
- `packages/chat-surface/src/thread-canvas/SwimlaneScrubContext.tsx` — NEW.
- `packages/chat-surface/src/thread-canvas/SwimlaneScrubContext.test.tsx` — NEW.
- `packages/chat-surface/src/composer/Composer.tsx` — NEW.
- `packages/chat-surface/src/composer/Composer.test.tsx` — NEW.
- `packages/chat-surface/src/composer/ToolPicker.tsx` — NEW.
- `packages/chat-surface/src/composer/ToolPicker.test.tsx` — NEW.
- `packages/chat-surface/src/composer/ModelPicker.tsx` — NEW.
- `packages/chat-surface/src/composer/ModelPicker.test.tsx` — NEW.
- `packages/chat-surface/src/composer/MentionPopover.tsx` — NEW.
- `packages/chat-surface/src/composer/MentionPopover.test.tsx` — NEW.
- `packages/chat-surface/src/composer/index.ts` — NEW barrel for the
  composer subtree. Internal-use only; the top-level
  `packages/chat-surface/src/index.ts` re-exports from the thread-canvas
  barrel only (orchestrator owns the package boundary export lines).
- `packages/chat-surface/src/thread-canvas/index.ts` — EDIT. Append the
  Phase 2-D delimited block per orchestrator coordination contract; no
  other changes.

**Out of scope** (other agents own these — do **not** touch):

- `packages/chat-surface/src/index.ts` — orchestrator owns the package
  boundary export lines. The required exports are listed in the audit
  section below.
- `packages/chat-surface/src/thread-canvas/{ThreadCanvas,TcTabs,TcSwimlanes,TcSurfaceMount,TcInlineDiff}.tsx` — owned by 2B / 2C / 2E.
- Any `apps/*` integration — desktop app + frontend wiring is downstream
  of this phase.
- Real send / approve / reject semantics for surface diffs — owned by
  `TcSurfaceMount` (D28). Composer and TcChat expose pure callbacks.

## Decisions

- **D25 (Time Machine is the swimlane, not a destination).** `TcChat`
  shows ghost previews when scrub is off-now; it does not own a separate
  "viewing time" or its own scrub UI. Reads context, renders muted.
- **D26 (no client-side renderer-to-renderer event bus).** Scrub state
  flows one direction: `TcSwimlanes` writes it (via the provider in
  `ThreadCanvas`); `TcChat` reads. No bus, no inverse coupling.
- **D28 (adapter purity → host owns actions).** Composer / TcChat do not
  apply diffs, do not send approve / reject to the backend. They emit
  callbacks. The host (`TcSurfaceMount`, downstream) maps to transport.
- **PRD §6.4 (functional only, no `useEffect` for derived state).**
  Picker open state is local `useState`; ghost-mode opacity is derived
  inline from scrub state. No `useEffect` for derived render state.
- **PRD §6.5 (substrate ports).** All transport calls go through
  `useTransport()`. No `fetch`, `window`, `document`, `localStorage`,
  `EventSource` references in this code.
- **Popover portaling.** Popovers render inline by default. An optional
  `portalTarget?: HTMLElement` prop allows the host to mount them in an
  overlay; this package never reaches for `document.body`. ESLint forbids
  `document` references here.
- **No comments by default (CLAUDE.md, PRD §6.1).** This sub-PRD is the
  prose; the components themselves carry no narration.

## Components

### `SwimlaneScrubContext`

- `SwimlaneScrubState = { scrubbedTo: number | "now" }`.
- `SwimlaneScrubProvider({ value, children })` — pass-through; host owns
  the state.
- `useSwimlaneScrub(): SwimlaneScrubState` — defaults to `{ scrubbedTo:
"now" }` when no provider is mounted, so consumer code can be tested
  in isolation without a provider.

### `TcChat`

- Props: `{ conversationId: string; mode: "studio" | "auto" | "focus" }`.
- On mount and on `conversationId` change, fetches
  `GET /v1/conversations/{id}/messages` via `useTransport`.
- Studio + Auto: renders a vertical message list with `<PlainText>` /
  `<Reasoning>` per part, then a `<Composer>` at the bottom. Auto uses
  reduced chrome (no header strip).
- Focus: hides the composer and renders an Activity / Approvals tabs
  stub (full tab content is downstream; this phase ships the tab strip
  - empty panels so the mode is visibly different).
- Ghost mode: when `useSwimlaneScrub().scrubbedTo !== "now"`, the
  message list renders at muted opacity (~0.55) with a banner that
  reads `Viewing HH:MM:SS`; composer is disabled in ghost mode (cannot
  send from the past). The banner formats with `Intl.DateTimeFormat` to
  avoid touching the substrate clock.

### `Composer`

- Props: `{ onSend(text), disabled?, placeholder?, portalTarget? }`.
- Auto-growing controlled textarea. Enter sends; Shift+Enter inserts a
  newline; sending clears the field.
- Inline action buttons (icons-only) open `ToolPicker` and `ModelPicker`
  beneath the composer. State for selected tools and selected model is
  local; the wiring of selection into the next request is downstream
  (host attaches them when calling `transport.request` for the run).
- Detects `@` followed by a non-space prefix at the caret and opens
  `MentionPopover`. Selecting a mention inserts `@{slug} ` at the caret.

### `ToolPicker`

- Props: `{ open, selectedTools, onToggle, onClose, portalTarget? }`.
- Fetches `GET /v1/mcp/tools` lazily on first `open=true`, caches result.
- Multi-select listbox; click a row to toggle.

### `ModelPicker`

- Props: `{ open, selectedModel, onSelect, onClose, portalTarget? }`.
- Hardcoded list per CLAUDE.md: Opus 4.7, Sonnet 4.6, Haiku 4.5.
- Single-select listbox.

### `MentionPopover`

- Props: `{ open, query, onSelect, onClose, portalTarget?, anchorRect? }`.
- Fetches mention candidates via `GET /v1/mentions?q={query}`; debounced
  by `query` change inside the popover's effect (no inputting from
  outside while the popover is closed).
- Renders absolutely-positioned at `anchorRect` when provided; otherwise
  inline.

## Audit

Run from worktree root:

```
npm install
npm test --workspace @0x-copilot/chat-surface
npm run typecheck --workspace @0x-copilot/chat-surface
npm run lint --workspace @0x-copilot/chat-surface
```

Test counts (chat-surface workspace):

- Baseline at branch start: 135 passing (14 files).
- After this phase: 185 passing (20 files). Delta: +50 tests, +6 test files.
- Other workspaces unchanged: chat-transport 26, surface-renderers 12,
  design-system 36, frontend 763.

ESLint: clean (no `no-restricted-globals` triggers; no `any`; no class
components outside the existing TcSurfaceMount exception).

Typecheck: clean.

### Required `packages/chat-surface/src/index.ts` exports (for orchestrator)

Add at the end of the file, preserving the existing delimited-block
style used by Phase 0-A / 0-B / 1-B / 1-D:

```ts
// === Phase 2-D tc-chat ===
export { TcChat, type TcChatProps } from "./thread-canvas";
export {
  SwimlaneScrubProvider,
  useSwimlaneScrub,
  type SwimlaneScrubState,
} from "./thread-canvas";
export {
  Composer,
  type ComposerProps,
  ToolPicker,
  type ToolPickerProps,
  type ToolDescriptor,
  ModelPicker,
  type ModelPickerProps,
  type ModelDescriptor,
  MentionPopover,
  type MentionPopoverProps,
  type MentionCandidate,
} from "./composer";
// === end Phase 2-D ===
```

### Deviations from scope

- None. The component file list and behavior match the PRD §5 Phase 2
  row 2D scope verbatim.
