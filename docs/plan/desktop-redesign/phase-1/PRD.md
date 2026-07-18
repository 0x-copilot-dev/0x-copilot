# Phase 1 — Consolidate the interaction layer into `chat-surface` (the (a) SSOT refactor)

> Implementation PRD. Branch `feat/desktop-redesign`, worktree
> `/Users/parthpahwa/Documents/work/enterprise-search-redesign`.
> Conventions per [`../_TEMPLATE.md`](../_TEMPLATE.md); design source of truth
> [`../design-reference/DESIGN-SPEC.md`](../design-reference/DESIGN-SPEC.md);
> phase map [`../PLAN.md`](../PLAN.md) §3 (ADR), §7 (consolidation map), §8 (Phase 1).

---

## 1. Context & problem

The production chat interaction richness — streaming-markdown renderer, the advanced
composer (real model catalog + custom OpenRouter, attachments, connectors, skills, stop),
citations, subagent/fleet cards, the 4-zone approval card, and the tabbed workspace pane —
lives in `apps/frontend/src/features/chat/components/*`. Desktop cannot import it
(`CLAUDE.md`: _no deployable component imports another's `src/`_), so today desktop would
have to re-implement or fork it. [`PLAN.md`](../PLAN.md) §3 locks decision **(a): hoist those
components down into `packages/chat-surface`** — already designated the framework-agnostic
chat UI surface — behind the existing ports (`Transport` / `Router` / `KeyValueStore` /
`PresenceSignal` and the Phase 0.5 leaf ports `FilePickerPort` / `ClipboardPort` /
`NotificationPort`). Both `apps/frontend` and `apps/desktop` then consume **one** copy.

Phase 0E already created the module homes and hoisted the _pure_ helpers: `chat-surface/src/messages`
holds `citationRemarkPlugin`, `streamingCursor`, `citationHrefs`, `markdownLinks`, `PlainText`,
`Reasoning`, `types`; `chat-surface/src/citations` holds the headless `CitationChip`,
`OrdinalCitationChip`, `SourceRow`, `SourceFavicon`, `registry`, `linkReducer`; `chat-surface/src/composer`
holds a base `Composer`, `ModelPicker`, `ToolPicker`, `MentionPopover`; and 67 files in
`apps/frontend/src` already import `@0x-copilot/chat-surface`. Phase 1 finishes the job for the
six remaining component families in [`PLAN.md`](../PLAN.md) §7, one family per PR, with `apps/frontend`
re-exporting from `chat-surface` (shims) so **web behavior stays byte-for-byte identical**
(the regression guard). This unblocks Phase 3 (Run cockpit) mounting the real surface on desktop.

This phase is UI-plumbing, not redesign: v2 "quiet" token application is Phase 2/0B. Phase 1 moves
code and preserves behavior; the only visual change permitted is _none_.

## 2. Goals / Non-goals

**Goals**

- Hoist the six families into `packages/chat-surface` behind props/ports, one family per PR, keeping
  `chat-surface` framework-agnostic (no bare `window`/`document`/`fetch`/`localStorage`/`EventSource`;
  ESLint `no-restricted-globals` + `no-restricted-imports` stay green — see
  `packages/chat-surface/eslint.config.js`).
- `apps/frontend` re-exports the hoisted components from `@0x-copilot/chat-surface` (thin shims / web
  adapters), so every one of the ~67 existing import sites keeps compiling and rendering unchanged.
- Keep the tree green after **every** PR: `chat-surface` typecheck + vitest, `apps/frontend` typecheck +
  vitest, all pass; no snapshot / DOM-structure / class-name diffs in the web app.
- Establish the **headless-core-in-`chat-surface` + DOM-bound-adapter-in-host** split as the repeatable
  pattern for Phase 2–6 (it already exists for `CitationChip`; Phase 1 generalizes it).

**Non-goals** (explicitly deferred)

- Applying v2 "quiet" tokens/fonts or any visual restyle — Phase 0B (tokens) / Phase 2A (shell). Phase 1
  keeps the existing `aui-*` / `atlas-*` class names and CSS untouched.
- Mounting anything on desktop (`apps/desktop`) — Phase 2 (shell) and Phase 3 (Run cockpit). Phase 1
  produces the consumable package only; it does not add a desktop consumer.
- Moving the data-binding domain (`chatModel/*` reducers, `runtime/*`, transport wiring, `activityDataBuilders`)
  into `chat-surface`. Those stay host-owned; only presentational cores move. Where a core needs domain
  data it receives it as props (the established pattern).
- Reconciling the two composer stacks into a single component, deleting the base `chat-surface` `Composer`,
  or dropping `ThinkingDepth` vs `chat-surface` `Depth` duplication beyond a thin re-export — flagged, deferred to Phase 3E.
- The Run right-rail recomposition of `WorkspacePane` into `[Chat · Sources · Agents · Approvals]` — that is
  Phase 3B; Phase 1F only hoists the pane as-is (Sources/Agents/Draft/Approvals/Skills).

## 3. User stories

| ID         | Role                   | Story                                                                                                                                                                                                        | Acceptance criteria (Given/When/Then)                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ---------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **US-1.1** | Developer/maintainer   | As a maintainer, I want the streaming-markdown renderer to live once in `chat-surface`, so that web and desktop render assistant text identically.                                                           | **Given** `MarkdownText`/`MarkdownLink`/`ReasoningGroup` are hoisted, **when** `apps/frontend` imports them via its shim, **then** `apps/frontend` vitest (`markdown/*.test.tsx`, `citationRemarkPlugin.test.ts`, `streamingCursor.test.tsx`) pass unchanged and the rendered DOM (classes `assistant-markdown`, `assistant-markdown--streaming`, `.aui-reasoning-group`) is byte-identical.                                                                            |
| **US-1.2** | Solo user              | As a solo user, I want the advanced composer (model pill incl. custom OpenRouter, thinking depth, attachments, connectors, skills, stop) to work exactly as today, so that consolidation is invisible to me. | **Given** the composer is hoisted, **when** I open the `+` menu, pick a file, select a model, add a custom `vendor/model` slug, attach a skill, and press stop mid-run, **then** every action behaves as before; the file picker routes through `FilePickerPort`; the plus-menu popover still anchors above the composer.                                                                                                                                               |
| **US-1.3** | Solo user              | As a solo user, I want inline citation chips and the post-prose Sources strip to resolve and preview exactly as today, so that I can trust sources after the move.                                           | **Given** citations are hoisted, **when** an assistant message streams `[[3]]`/`[c…]` tokens and I hover a chip, **then** the chip resolves against the run registry, the hover preview card appears, and the Sources strip lists cited sources ordered by ordinal.                                                                                                                                                                                                     |
| **US-1.4** | Solo user              | As a solo user, I want subagent and parallel-fleet cards to render the same, so that multi-agent runs stay legible.                                                                                          | **Given** the subagent family is hoisted, **when** a run dispatches a fleet and children stream status/findings, **then** `SubagentFleetCard` shows running/total counts, `SubagentCard`/`FleetSubagentRow` render task/finding/meta + `<details>` disclosure, and the paused/amber chrome behaves as before.                                                                                                                                                           |
| **US-1.5** | Solo user              | As a solo user, I want the 4-zone approval card and its collapsed receipt to look and act the same, so that consent is unchanged.                                                                            | **Given** approvals are hoisted, **when** the agent requests an MCP action, **then** the 4-zone card (header · params · actions · footer reassurance) renders; approving collapses it to a one-line receipt with the 60s undo window; rejecting/forwarding/cancelling route as before.                                                                                                                                                                                  |
| **US-1.6** | Solo user              | As a solo user, I want the workspace pane tabs (Sources/Agents/Draft/Approvals/Skills) to open, badge, and switch exactly as today, so that the right-rail is unaffected.                                    | **Given** `WorkspacePane` is hoisted, **when** sources/subagents/drafts/approvals/skills flow in, **then** empty tabs are hidden, badges count correctly (`"N live"` for running agents), and the close button + overlay mode behave unchanged.                                                                                                                                                                                                                         |
| **US-1.7** | Developer/maintainer   | As a maintainer, I want `chat-surface` to stay substrate-agnostic, so that desktop can mount it without a browser leak.                                                                                      | **Given** any hoisted file (incl. the composer with its picker/portal/outside-click/timer touchpoints and the timer leaf hooks), **when** I run `npm run lint --workspace @0x-copilot/chat-surface` **and** the FR-1.31 enumeration grep, **then** `no-restricted-globals` (window/document/fetch/localStorage/EventSource/…), `no-restricted-imports` (`apps/*`, `@0x-copilot/frontend`), and the grep all report zero (bar the sanctioned `globalThis.localStorage`). |
| **US-1.8** | Developer/maintainer   | As a maintainer, I want each family hoisted independently and reversibly, so that a regression in one family never blocks the others.                                                                        | **Given** the PR sequence, **when** any single PR lands, **then** the tree is green with no other family touched, and reverting that one PR restores the prior state without conflict.                                                                                                                                                                                                                                                                                  |
| **US-1.9** | Solo user (edge/error) | As a solo user, I want empty/loading/error states preserved through the move, so that nothing regresses in the unhappy path.                                                                                 | **Given** a run with no sources, an unresolved `?` citation, a failed draft load, and an approval that gets run-cancelled, **when** those states render, **then** the placeholder chip (`citation-chip--unresolved`), empty-tab hiding, error rows, and "Cancelled" receipt render exactly as pre-hoist.                                                                                                                                                                |

## 4. Functional requirements

Grouped by family. Each FR maps to ≥1 story (§3) and ≥1 test (§8).

**Message / markdown renderer (US-1.1, US-1.7)**

- **FR-1.1** `chat-surface/src/messages` MUST own `MarkdownText` (the `streamdown` `<Streamdown>` wrapper that
  spreads `streamingCursorProps(status)` and applies `remarkPlugins=[createRemarkCitations(...)]`), moved from
  `apps/frontend/.../markdown/MarkdownText.tsx`.
- **FR-1.2** `MarkdownText` MUST accept the citation-diagnostics sink (`onMatch`) and the anchor/chip renderer
  (`components.a`) as injected props/config, NOT import `apps/frontend`'s `citationDebug` or the web `MarkdownLink`
  directly — so the module stays app-import-free (ESLint `no-restricted-imports`).
- **FR-1.3** `chat-surface/src/messages` MUST own `MarkdownLink` (the chip dispatcher routing `#cite-ord:` →
  `OrdinalCitationChip`, `#cite:` → `CitationChip`, else `<a>` with `markdownLinkLabel`), rendering the **headless**
  chips and delegating chip _resolution_ to a host-provided context/props.
- **FR-1.4** `chat-surface/src/messages` MUST own `ReasoningGroup` (the `<details className="aui-reasoning-group">`
  thought-process accordion using the exported `ThinkingIcon`), preserving `data-status` and the "Thinking…" /
  "Thought process" label flip.
- **FR-1.5** `apps/frontend/.../markdown/{MarkdownText,MarkdownLink,ReasoningGroup}.tsx` MUST become re-export shims
  (or the host adapter that binds `citationDebug` + the web chips) so all existing import sites in `apps/frontend`
  compile and render unchanged.

**Composer wiring (US-1.2, US-1.7)**

- **FR-1.6** `chat-surface/src/composer` MUST own `ModelPill` (anchored model menu, custom-OpenRouter-slug input via
  `onAddCustom`), `ThinkingDepthControl`, `ComposerPlusMenu`, and `ComposerConnectorsButton`, moved from
  `apps/frontend/.../shell/*` and `.../composer/*`.
- **FR-1.7** `ThinkingDepthControl`'s `ThinkingDepth` domain (`apps/frontend/src/features/chat/depth.ts`) MUST be either
  moved into `chat-surface/composer` or re-exported from it; the existing `chat-surface` `Depth`/`listDepthDescriptors`
  duplication MUST be documented (reconciliation deferred to Phase 3E), not silently forked.
- **FR-1.8** `chat-surface/src/composer` MUST own the `AssistantComposer` composition (topbar skill pills, bottom-bar
  render, send/stop button, slash-cue), moved from `apps/frontend/.../composer/AssistantComposer.tsx`.
- **FR-1.9** `AssistantComposer` MUST replace **every** direct DOM/global touchpoint with a port or host slot so the
  moved core has **zero** `no-restricted-globals` / `createPortal` hits. The file (`apps/frontend/.../composer/AssistantComposer.tsx`)
  currently carries **four distinct** touchpoints, each of which MUST be resolved (not just the file picker):
  1. **File picker** — `document.createElement("input")` + `document.body.appendChild` (≈L193/198) MUST route through
     `FilePickerPort.pick({multiple, accept})` (FR-1.9 original scope).
  2. **Plus-menu portal** — the `AnchoredPlusMenu` helper defined _inside the same file_ (≈L601-647; uses
     `window.innerHeight`, `window.addEventListener("resize"|"scroll")`, `createPortal(document.body)`) MUST be
     **extracted to the host** (`apps/frontend`) and passed into the moved core as a `ReactNode`/render-prop slot — it is
     NOT hoisted. The moved core exposes an anchor + open-state; the host owns the portal.
  3. **Outside-click dismissal** — `document.addEventListener("pointerdown", …)` / `removeEventListener` (≈L179-180)
     MUST become a host-provided dismissal slot or be driven by the design-system popover primitive; the moved core MUST
     NOT reference `document`.
  4. **Slash-cue timer** — `window.setTimeout` / `window.clearTimeout` (≈L186/224/226) MUST drop the `window.` prefix
     (bare `setTimeout`/`clearTimeout` are not in the `no-restricted-globals` list) per FR-1.30.
     Acceptance is machine-checked: `npm run lint --workspace @0x-copilot/chat-surface` reports zero on the moved file.
- **FR-1.10** The runtime→chat-surface `AttachmentAdapter` bridge, the two-stage `add`/`send`/`remove` semantics, and
  the `selectedSkills` prompt-prefixing MUST be preserved exactly (behavior unchanged for `onSubmit`).
- **FR-1.11** `apps/frontend`'s composer import sites MUST resolve via shims so `ChatScreen` wiring (composerRef,
  attachment adapters, connectors trigger) is unchanged.

**Citations subsystem (US-1.3, US-1.9)**

- **FR-1.12** `chat-surface/src/citations` MUST own the citation **read context** (`CitationsProvider` +
  `useCitation` / `useRunCitations` / `useOrdinalCitation` / `useResolvedOrdinalCitation`), parameterized on the
  registries so the domain link-reducer stays host-side or moves with a documented boundary.
- **FR-1.13** `chat-surface/src/citations` MUST own `MessageSourcesStrip` and the `SourcesPanel` body (rendering shared
  `SourceRow`), taking `citations` / `SourceEntryMap`-projected data as props.
- **FR-1.14** The hover-preview trigger (`SourcePreview` / `useSourcePreviewTrigger`, which uses `document`/`window`/
  `createPortal`) MUST remain a host-provided web adapter passed into the headless `CitationChip` via `previewProps`
  (the pattern already in place) — the chip core does NOT gain a browser dependency.
- **FR-1.15** Unresolved citations MUST still render the `citation-chip--unresolved` "?" placeholder; ordinal ordering
  in the strip MUST stay `a.ordinal - b.ordinal`.

**Subagent / fleet cards (US-1.4)**

- **FR-1.16** `chat-surface/src/subagents` MUST own `SubagentCard`, `FleetSubagentRow`, `SubagentFleetCard`,
  `subagentCardViewModel`, and `labels`, moved from `apps/frontend/.../subagents/*` and `.../messages/SubagentFleetCard.tsx`.
- **FR-1.17** The view-model adapter MUST take normalized lifecycle status + activity records as inputs; the host keeps
  ownership of `chatModel/subagentStatus` and `utils/activityDataBuilders` (either those pure helpers move too, or the
  VM accepts already-normalized inputs — the PR MUST pick one and keep it app-import-free).
- **FR-1.18** The shared leaf primitives the cards render (`ActivityStatusIcon`, `SubagentActivityList`,
  `useElapsedSeconds`) MUST be reachable from `chat-surface` without importing `apps/frontend` — moved or injected.
  `useElapsedSeconds` (`apps/frontend/.../tools/useElapsedSeconds.ts`) currently uses `window.setInterval` /
  `window.clearInterval`; on move these MUST be neutralized per FR-1.30 or the moved file fails `no-restricted-globals`.
- **FR-1.19** Paused chrome (amber indicator, paused chip, frozen progress, "Review approval →" anchor) and the
  `<details>` disclosure behavior MUST be preserved.

**Approvals (US-1.5, US-1.9)**

- **FR-1.20** `chat-surface/src/approvals` MUST own the presentational `ApprovalCard` (4 zones: header ·
  params · actions · footer reassurance) and `ApprovalReceipt` (settled one-line record + 60s undo countdown),
  moved from `apps/frontend/.../activity/{ApprovalCard,ApprovalReceipt}.tsx`.
- **FR-1.21** The leaf dependencies of those two (`ActivityDetails`, `ActivityParams`, `useUndoCountdown`, and the
  `ActivityParam` shape) MUST be reachable from `chat-surface` without an `apps/*` import (moved or re-typed as
  chat-surface props). `useUndoCountdown` (`apps/frontend/.../tools/useUndoCountdown.ts`) currently uses
  `window.setInterval` / `window.clearInterval`; on move these MUST be neutralized per FR-1.30.
- **FR-1.22** The approval **routing/wiring** (`tools/ApprovalTool.tsx`, `useApprovalsQueue`, `chatModel/approval`,
  `ApprovalFocusContext`, `WorkspaceMemberPicker`, forward/chain/undo POST plumbing) MUST stay host-owned in
  `apps/frontend`; only the two presentational components move. `ApprovalTool` MUST import them from the shim.
- **FR-1.23** All approval visual states MUST be preserved: `data-status="waiting"`, category vendor·access pill,
  resolved→receipt collapse, "Cancelled" / "Forwarded" / chain-final paths, undo button vs "Undo requested" chip.

**Workspace pane (US-1.6)**

- **FR-1.24** `chat-surface/src/workspace` MUST own `WorkspacePane`, `WorkspaceTabs`, the five tab bodies
  (`SourcesTab`, `AgentsTab`, `DraftTab`, `ApprovalsTab`, `SkillsTab`), and `pluralize`, moved from
  `apps/frontend/.../workspace/*`.
- **FR-1.25** The pane MUST remain a composition shell that owns no fetches/subscriptions: all data
  (sources/subagents/drafts/approvalsQueue/skills + loading/error flags + callbacks) flows in as props; the host keeps
  the hooks (`useWorkspacePaneState`, `useWorkspacePaneAutoOpen`, `useApprovalsQueue`, `useArchivedSources`, `useDrafts`,
  `useSubagents`, `useSubagentActivities`).
- **FR-1.26** Tab visibility (hide empty), badge computation (`sourcesCount`, `"N live"` running agents, pending
  approvals), `role="tabpanel"` wiring, overlay mode (`data-overlay`), and the close handler MUST be preserved.
- **FR-1.27** Any `chatModel`-typed props (`SourceEntryMap`, `SubagentSnapshotMap`, `SubagentActivityRecord`) at the
  pane boundary MUST be expressed as `@0x-copilot/api-types` or `chat-surface`-local types, not by importing
  `apps/frontend/src/features/chat/chatModel/*`.

**Cross-cutting (US-1.7, US-1.8)**

- **FR-1.28** After every PR, `packages/chat-surface/src/index.ts` MUST export the newly hoisted public surface, and
  `chat-surface` + `apps/frontend` MUST both typecheck, lint, and test green.
- **FR-1.29** No PR may change any `aui-*` / `atlas-*` / `citation-chip` class name, CSS file, or rendered DOM structure
  in `apps/frontend`; the web app is behaviorally identical (regression guard).
- **FR-1.30** **Timer globals.** Every moved file that schedules time — `useElapsedSeconds` (`window.setInterval`/
  `window.clearInterval`), `useUndoCountdown` (`window.setInterval`/`window.clearInterval`), and `AssistantComposer`'s
  slash-cue (`window.setTimeout`/`window.clearTimeout`) — MUST reference the timer functions **without the `window.`
  prefix** (bare `setInterval`/`clearInterval`/`setTimeout`/`clearTimeout`, which are not in `no-restricted-globals`),
  OR route through an injected clock. Behavior (5000 ms elapsed tick, 1000 ms undo tick, slash-cue delay) MUST be
  byte-identical. This is a pure lint-safety rewrite, not a behavior change.
- **FR-1.31** **Boundary enumeration is exhaustive, not exemplary.** Before each PR merges, a grep of the moved files
  for `\b(window|document|localStorage|sessionStorage|fetch|EventSource|XMLHttpRequest|WebSocket|navigator|location|
history)\b` and `createPortal` MUST return **zero** hits in `packages/chat-surface`; any residual is either neutralized
  (FR-1.30), routed through a port (FilePickerPort), or extracted to a host slot (AnchoredPlusMenu, SourcePreview,
  connectors popover). The known inventory to clear this phase is: `AssistantComposer` (4 touchpoints, FR-1.9),
  `useElapsedSeconds` + `useUndoCountdown` (timers, FR-1.30). No moved file may keep an un-enumerated global.

## 5. Architecture & system design

### 5.1 Single source of truth

The canonical owner of each _presentational_ concept becomes `packages/chat-surface`; the canonical owner of each
_data-binding_ concept stays `apps/frontend` (host). Phase 1 draws that line per family and removes the duplication by
making `apps/frontend` re-export (never re-declare) the moved code.

| Concept                                                                                               | Canonical owner after Phase 1      | Removed / superseded                                                |
| ----------------------------------------------------------------------------------------------------- | ---------------------------------- | ------------------------------------------------------------------- |
| Streaming-markdown render, chip dispatch, reasoning accordion                                         | `chat-surface/src/messages`        | web copies collapse to shims                                        |
| Model pill, depth control, plus-menu, connectors button, composer shell                               | `chat-surface/src/composer`        | web copies collapse to shims; DOM use replaced by ports/slots       |
| Citation read-context, sources strip/panel body, chips                                                | `chat-surface/src/citations`       | web wrapper keeps only preview-portal adapter                       |
| Subagent/fleet cards + view-model                                                                     | `chat-surface/src/subagents` (new) | web copies collapse to shims                                        |
| Approval card + receipt (presentational)                                                              | `chat-surface/src/approvals` (new) | web copies collapse to shims; `ApprovalTool` wiring stays host-side |
| Workspace pane + tabs (composition shell)                                                             | `chat-surface/src/workspace` (new) | web copies collapse to shims; hooks stay host-side                  |
| `chatModel/*` reducers, `runtime/*`, transport wiring, `activityDataBuilders`, `ApprovalFocusContext` | `apps/frontend` (unchanged)        | —                                                                   |

Established precedent this generalizes: `chat-surface/src/citations/CitationChip.tsx` is already headless, and
`apps/frontend/.../citations/CitationChip.tsx` is already a thin web wrapper that resolves data via `useCitation`
and hands the chip `previewProps`. Phase 0E already hoisted `citationRemarkPlugin`, `streamingCursor`, `markdownLinks`,
`citationHrefs`. Phase 1 applies the same headless-core + host-adapter split to the remaining families.

### 5.2 Boundaries & ports

- **Hard boundary:** `chat-surface` MUST NOT import `apps/frontend` (`@0x-copilot/frontend`, `apps/*`). Enforced by
  `packages/chat-surface/eslint.config.js` → `no-restricted-imports` (`BOUNDARY_MESSAGE_APP_IMPORT`).
- **Substrate boundary:** `chat-surface` MUST NOT reference `window` / `document` / `history` / `navigator` /
  `location` / `localStorage` / `sessionStorage` / `fetch` / `EventSource` / `XMLHttpRequest` / `WebSocket`.
  Enforced by `no-restricted-globals` (`BOUNDARY_MESSAGE_GLOBALS`). The lone sanctioned touchpoint is the web
  `KeyValueStore` reference impl using `globalThis.localStorage`.
- **Ports used this phase:**
  - `FilePickerPort` (`ports/FilePickerPort.ts`) — replaces `AssistantComposer`'s `document.createElement("input")`
    picker (FR-1.9). `pick({multiple, accept}) → FilePickerSelection[]` with `name/size/type/stream()`.
  - `Transport` / `Router` / `KeyValueStore` / `PresenceSignal` — NOT newly consumed by the moved presentational cores
    (they take data via props); the pane/cards stay pure. Named here because they define the boundary the host binds across.
  - **Slot pattern** (not a new port) for DOM-bound adapters that stay host-side: the plus-menu portal
    (`AnchoredPlusMenu` — today defined _inside_ `AssistantComposer.tsx` at ≈L601-647; it MUST be **extracted to the
    host** and injected as a `ReactNode`/render-prop slot, uses `createPortal` + `window.innerHeight` +
    `window.addEventListener`), the plus-menu **outside-click dismissal** (`document.addEventListener("pointerdown")`,
    ≈L179-180 — host slot or design-system popover), the `SourcePreview` hover portal, and the connectors popover are
    passed into `chat-surface` components as `ReactNode` slots / `previewProps` — the pattern the base `Composer`
    (`bottomBarRender` / `topBarSlot`) and headless `CitationChip` (`previewProps`) already use.
  - **Timer neutralization** (FR-1.30): three moved files call `window.set*/clear*` (`useElapsedSeconds`,
    `useUndoCountdown`, `AssistantComposer` slash-cue). These are rewritten to the bare timer globals (not restricted) —
    no port needed; they are not substrate-specific.
  - **Enumeration guard** (FR-1.31): the boundary is verified by an _exhaustive_ grep of moved files, not by the two
    examples in FR-1.9; every one of the touchpoints above is accounted for before merge.

### 5.3 Data flow & key types/interfaces

- **Message render:** host `MessageParts` → `MarkdownText({ text, status })` (`messages/types.ts`
  `TextMessagePartProps`, `MessagePartStatus`) → `streamingCursorProps(status)` → `<Streamdown>` with
  `createRemarkCitations({ onMatch })` (`messages/citationRemarkPlugin.ts`) → chip tokens → `MarkdownLink`
  (`messages/citationHrefs.ts` predicates) → headless `CitationChip` / `OrdinalCitationChip`.
- **Composer submit:** host `ChatScreen` → `AssistantComposer` props (`connectors`, `skills`, `models`,
  `selectedModel`, `depth`, `attachmentAdapter`, `onSubmit`, `onCancel`) → base `Composer` (`composer/Composer.tsx`,
  `ComposerHandle`, `AttachmentAdapter`, `ComposerSubmitPayload`).
- **Citations:** host owns `CitationRegistryByRun` (`citations/registry.ts`) + `CitationLinkRegistryByRun`
  (`chatModel/citationLinkReducer`, host) → `CitationsProvider` value → `useCitation`/`useOrdinalCitation` →
  `CitationSourceRef` / `CitationLink` (`@0x-copilot/api-types`).
- **Subagents:** host `SubagentSnapshotMap` / `SubagentEntry` (`@0x-copilot/api-types`) + `SubagentActivityRecord`
  → `subagentCardViewModel` → `SubagentCardViewModel` → `SubagentCard` / `FleetSubagentRow`.
- **Approvals:** host `ApprovalTool` (`ToolCallMessagePartProps`) → `ApprovalCard` props (`title`, `reason`,
  `category{vendor,access}`, `params: ActivityParam[]`, `actions`, `reassurance`) / `ApprovalReceipt` props
  (`kind: ApprovalReceiptKind`, `undoUntil`, `onUndo`).
- **Workspace:** host lifts hook outputs → `WorkspacePaneProps` (`state: WorkspacePaneState`, `sources: SourceEntryMap`,
  `subagents: SubagentSnapshotMap`, `approvalsQueue: ApprovalsQueueProjection`, `skills`, callbacks).

### 5.4 Reuse vs new (real paths)

| Component / module                                                                                                                                   | Action                         | From → To                                                                                                                                                                                |
| ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MarkdownText`, `MarkdownLink`, `ReasoningGroup`                                                                                                     | **Move**                       | `apps/frontend/src/features/chat/components/markdown/*.tsx` → `packages/chat-surface/src/messages/*.tsx`                                                                                 |
| `citationRemarkPlugin`, `streamingCursor`, `citationHrefs`, `markdownLinks`, `PlainText`, `Reasoning`, `types`                                       | **Reuse** (already hoisted 0E) | `packages/chat-surface/src/messages/*`                                                                                                                                                   |
| `ModelPill`, `ThinkingDepthControl`                                                                                                                  | **Move**                       | `apps/frontend/.../shell/{ModelPill,ThinkingDepthControl}.tsx` → `packages/chat-surface/src/composer/*.tsx`                                                                              |
| `ComposerPlusMenu`, `ComposerConnectorsButton`, `AssistantComposer`, `fileAttachmentAccept`, `AttachmentPill`                                        | **Move**                       | `apps/frontend/.../composer/*` → `packages/chat-surface/src/composer/*`                                                                                                                  |
| `depth.ts` (`ThinkingDepth`)                                                                                                                         | **Move or re-export**          | `apps/frontend/src/features/chat/depth.ts` → `packages/chat-surface/src/composer/depth.ts`                                                                                               |
| `AnchoredPlusMenu` (portal helper, currently inline in `AssistantComposer.tsx` ≈L601-647)                                                            | **Extract → keep host**        | split out of `apps/frontend/.../composer/AssistantComposer.tsx` into its own host file; injected into the moved core as a slot (uses `createPortal`/`window`)                            |
| `Composer`, `ModelPicker`, `ToolPicker`, `MentionPopover`                                                                                            | **Reuse** (base)               | `packages/chat-surface/src/composer/*`                                                                                                                                                   |
| `citationsContext`, `MessageSourcesStrip`, `SourcesPanel`                                                                                            | **Move**                       | `apps/frontend/.../{citations/citationsContext,messages/MessageSourcesStrip,details/SourcesPanel}.tsx` → `packages/chat-surface/src/citations/*`                                         |
| `SourcePreview` / `useSourcePreviewTrigger`                                                                                                          | **Keep host adapter**          | stays `apps/frontend/.../citations/SourcePreview.tsx` (uses `createPortal`/`window`)                                                                                                     |
| `CitationChip` / `OrdinalCitationChip` (headless)                                                                                                    | **Reuse**                      | `packages/chat-surface/src/citations/*`                                                                                                                                                  |
| `SubagentCard`, `FleetSubagentRow`, `SubagentFleetCard`, `subagentCardViewModel`, `labels`                                                           | **Move**                       | `apps/frontend/.../{subagents/*,messages/SubagentFleetCard.tsx}` → `packages/chat-surface/src/subagents/*`                                                                               |
| `ActivityStatusIcon`, `SubagentActivityList`, `useElapsedSeconds`                                                                                    | **Move (leaf)**                | `apps/frontend/.../{activity/ActivityStatusIcon,tools/SubagentActivityList,tools/useElapsedSeconds}` → `packages/chat-surface/src/subagents/*` (or a shared `chat-surface/src/activity`) |
| `ApprovalCard`, `ApprovalReceipt`                                                                                                                    | **Move**                       | `apps/frontend/.../activity/{ApprovalCard,ApprovalReceipt}.tsx` → `packages/chat-surface/src/approvals/*`                                                                                |
| `ActivityDetails`, `ActivityParams`, `useUndoCountdown`, `ActivityParam` type                                                                        | **Move (leaf)**                | `apps/frontend/.../{activity/*,tools/useUndoCountdown,utils/activityDataBuilders}` → `packages/chat-surface/src/approvals/*` (typed props)                                               |
| `ApprovalTool`, `useApprovalsQueue`, `ApprovalFocusContext`, `WorkspaceMemberPicker`, `chatModel/approval`                                           | **Keep host**                  | stays `apps/frontend/...` (wiring)                                                                                                                                                       |
| `WorkspacePane`, `WorkspaceTabs`, `SourcesTab`, `AgentsTab`, `DraftTab`, `ApprovalsTab`, `SkillsTab`, `pluralize`                                    | **Move**                       | `apps/frontend/.../workspace/*` → `packages/chat-surface/src/workspace/*`                                                                                                                |
| `useWorkspacePaneState`, `useWorkspacePaneAutoOpen`, `useApprovalsQueue`, `useArchivedSources`, `useDrafts`, `useSubagents`, `useSubagentActivities` | **Keep host** (hooks)          | stays `apps/frontend/.../workspace/*`                                                                                                                                                    |
| `RightRail` (chats-canvas 2-tab)                                                                                                                     | **Do not touch**               | `packages/chat-surface/src/shell/RightRail.tsx` is a _different_ rail; Phase 1F does not merge into it                                                                                   |
| `packages/chat-surface/src/index.ts`                                                                                                                 | **Modify**                     | add `messages`/`composer`/`citations`/`subagents`/`approvals`/`workspace` public exports                                                                                                 |

## 6. Affected files / component inventory

**Create** (new module homes + barrels in `packages/chat-surface/src`):

- `messages/MarkdownText.tsx`, `messages/MarkdownLink.tsx`, `messages/ReasoningGroup.tsx`
- `composer/ModelPill.tsx`, `composer/ThinkingDepthControl.tsx`, `composer/ComposerPlusMenu.tsx`,
  `composer/ComposerConnectorsButton.tsx`, `composer/AssistantComposer.tsx`, `composer/AttachmentPill.tsx`,
  `composer/fileAttachmentAccept.ts`, `composer/depth.ts`
- `citations/CitationsContext.tsx`, `citations/MessageSourcesStrip.tsx`, `citations/SourcesPanel.tsx`
- `subagents/index.ts`, `subagents/SubagentCard.tsx`, `subagents/FleetSubagentRow.tsx`, `subagents/SubagentFleetCard.tsx`,
  `subagents/subagentCardViewModel.ts`, `subagents/labels.ts`, `subagents/ActivityStatusIcon.tsx`,
  `subagents/SubagentActivityList.tsx`, `subagents/useElapsedSeconds.ts`
- `approvals/index.ts`, `approvals/ApprovalCard.tsx`, `approvals/ApprovalReceipt.tsx`, `approvals/ActivityDetails.tsx`,
  `approvals/ActivityParams.tsx`, `approvals/useUndoCountdown.ts`, `approvals/types.ts`
- `workspace/index.ts`, `workspace/WorkspacePane.tsx`, `workspace/WorkspaceTabs.tsx`, `workspace/SourcesTab.tsx`,
  `workspace/AgentsTab.tsx`, `workspace/DraftTab.tsx`, `workspace/ApprovalsTab.tsx`, `workspace/SkillsTab.tsx`,
  `workspace/pluralize.ts`
- Colocated vitest files for each moved component (moved with them; see §8), plus one genuinely-new test:
  `composer/AssistantComposer.filepicker.test.tsx` (asserts the picker routes through `FilePickerPort`, not
  `document.createElement`).
- New host file `apps/frontend/src/features/chat/components/composer/AnchoredPlusMenu.tsx` — extracted from
  `AssistantComposer.tsx` (the `createPortal`/`window` portal helper stays host-side; PR-1.3).

**Modify:**

- `packages/chat-surface/src/index.ts` — add public exports for each family (per PR).
- `packages/chat-surface/package.json` — no change expected (`streamdown` already a peer dep; `unified`/`unist-util-visit`
  already deps). Confirm no new runtime dep sneaks in.
- `apps/frontend/src/features/chat/components/**` — the moved files become re-export shims OR thin host adapters
  (binding `citationDebug`, `SourcePreview`, the plus-menu portal, `FilePickerPort` impl). ~67 import sites resolve
  transitively; import specifiers stay `@0x-copilot/chat-surface` where already used, or the local shim path.
- `apps/frontend/src/features/chat/components/tools/ApprovalTool.tsx` — import `ApprovalCard`/`ApprovalReceipt` from shim.
- `apps/frontend/src/features/chat/depth.ts` — becomes a re-export of `chat-surface/composer/depth` (if moved).

**Delete** (only after the shim proves green; may be deferred to PR-1.8 cleanup):

- Original bodies of moved components in `apps/frontend` (replaced by shims). No behavior deleted — only relocation.
- No CSS deletion in Phase 1 (styles stay in `apps/frontend/src/styles.css`; consumed by class name; Phase 2 owns CSS).

**Flagged (superseded / coupling to resolve in-PR):**

- `apps/frontend/src/features/chat/depth.ts` vs `chat-surface` `Depth`/`listDepthDescriptors` — duplicate depth models;
  Phase 1 re-exports, does not unify (Phase 3E).
- `SourcePreview` and `AnchoredPlusMenu` use `createPortal`/`window` — they stay host-side as slots; they are NOT hoisted.
  `AnchoredPlusMenu` today lives _inside_ `composer/AssistantComposer.tsx` (≈L601-647) and MUST be split out into its own
  host file before/while `AssistantComposer` is moved (PR-1.3).
- Banned globals in the moved set beyond the composer portal (full inventory, all cleared this phase): `AssistantComposer`
  outside-click `document.addEventListener("pointerdown")` (≈L179-180) and slash-cue `window.setTimeout/clearTimeout`
  (≈L186/224/226); `tools/useElapsedSeconds.ts` `window.setInterval/clearInterval` (≈L13-14, moved in PR-1.5);
  `tools/useUndoCountdown.ts` `window.setInterval/clearInterval` (≈L23-24, moved in PR-1.6). Each resolved per FR-1.9 /
  FR-1.30; verified by FR-1.31 grep.
- `WorkspacePane`/tabs import `chatModel/{sourcesReducer,subagentReducer,subagentStatus}` and `utils/activityDataBuilders`
  — those type/util imports must be re-pathed to `@0x-copilot/api-types` or moved as chat-surface-local types (FR-1.27).

## 7. PR / commit breakdown

Ordered; each independently mergeable, ≤ ~1000 LOC (incl. moved tests), leaves `chat-surface` + `apps/frontend` green.
Numbering is phase-scoped `PR-1.n`. Every PR follows the same recipe: (1) move file(s) into `chat-surface`, replacing
any banned global with a port/slot; (2) add the public export to `chat-surface/src/index.ts`; (3) replace the
`apps/frontend` original with a shim / host adapter; (4) move colocated tests; (5) typecheck+lint+test both packages.

**Mapping to [`PLAN.md`](../PLAN.md) §8** (which sketches six families 1A–1F): `PR-1.1` = 1A (message/markdown);
`PR-1.2` + `PR-1.3` = 1B (composer — split into sub-controls then the shell, because the shell alone is ~700 LOC with
port/slot rework); `PR-1.4` = 1C (citations); `PR-1.5` = 1D (subagents); `PR-1.6` = 1E (approvals — presentational only;
PLAN's "+routing" stays host-side, FR-1.22); `PR-1.7` = 1F (workspace pane); `PR-1.8` = barrel/cleanup/proof (implicit in
PLAN's "gate each" step). The finer 8-PR split honors the ≤~1000 LOC rule; no family is dropped or added.

| PR         | Title                                                      | Scope                                                                                                                                                                                                                                                                             | Files (real)                                                                                                                                                                                                                                                        | Deps                                                  | Acceptance                                                                                                                                                                                                   | Size     |
| ---------- | ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- |
| **PR-1.1** | Hoist message/markdown renderer                            | Move `MarkdownText`/`MarkdownLink`/`ReasoningGroup` behind an injected chip-renderer + `onMatch` sink; shim in `apps/frontend`.                                                                                                                                                   | `chat-surface/src/messages/{MarkdownText,MarkdownLink,ReasoningGroup}.tsx` (+ tests `MarkdownLink.test.tsx`, `ReasoningGroup.test.tsx`, `streamingCursor.test.tsx`, `citationRemarkPlugin.test.ts`); shims at `apps/frontend/.../markdown/*`                        | none                                                  | `apps/frontend` markdown tests pass unchanged; chip dispatch + reasoning accordion DOM identical; `chat-surface` lint green                                                                                  | M (~300) |
| **PR-1.2** | Hoist composer sub-controls                                | Move `ModelPill`, `ThinkingDepthControl`, `ComposerPlusMenu`, `ComposerConnectorsButton`, `depth.ts` (or re-export).                                                                                                                                                              | `chat-surface/src/composer/{ModelPill,ThinkingDepthControl,ComposerPlusMenu,ComposerConnectorsButton,depth}.*` (+ `ModelPill.test.tsx`, `ThinkingDepthControl.test.tsx`, `ConnectorsPill.test`?→N/A, `ComposerConnectorsButton.test.tsx`); shims                    | PR-1.1 (barrel convention)                            | model menu + custom slug + depth show/hide + plus-menu views render identical; `depth` duplication documented                                                                                                | M (~500) |
| **PR-1.3** | Hoist `AssistantComposer` shell behind ports/slots         | Move `AssistantComposer` + `AttachmentPill` + `fileAttachmentAccept`; resolve **all four** touchpoints (FR-1.9): `FilePickerPort` for the picker, **extract `AnchoredPlusMenu` to host** + inject as slot, host outside-click dismissal slot, and bare-timer slash-cue (FR-1.30). | `chat-surface/src/composer/{AssistantComposer,AttachmentPill,fileAttachmentAccept}.*` (+ `AttachmentPill.test.tsx`, new `AssistantComposer.filepicker.test.tsx`); `apps/frontend` gains a standalone `AnchoredPlusMenu.tsx` host file + `FilePickerPort` impl; shim | PR-1.2                                                | attachments/skills/model/depth/stop all work; file picker via port; **FR-1.31 grep zero** on the moved file (no `window`/`document`/`createPortal`); web composer DOM identical                              | L (~700) |
| **PR-1.4** | Hoist citations subsystem                                  | Move `citationsContext` (→ `CitationsContext`), `MessageSourcesStrip`, `SourcesPanel`; keep `SourcePreview` host adapter as `previewProps`.                                                                                                                                       | `chat-surface/src/citations/{CitationsContext,MessageSourcesStrip,SourcesPanel}.tsx` (+ `MessageSourcesStrip.test.tsx`, `SourcePreview.test.tsx`→host, `OrdinalCitationChip.test.tsx`); shims                                                                       | PR-1.1                                                | chips resolve; hover preview works; strip ordered by ordinal; `?` placeholder intact; link-reducer boundary documented                                                                                       | L (~600) |
| **PR-1.5** | Hoist subagent/fleet cards                                 | Move `SubagentCard`, `FleetSubagentRow`, `SubagentFleetCard`, `subagentCardViewModel`, `labels`, and leaf `ActivityStatusIcon`/`SubagentActivityList`/`useElapsedSeconds`; VM takes normalized inputs; neutralize `useElapsedSeconds` timers (FR-1.30).                           | `chat-surface/src/subagents/*` (+ `SubagentCard.test.tsx`, `FleetSubagentRow.test.tsx`, `subagentCardViewModel.test.ts`, `useElapsedSeconds.test.ts`); shims                                                                                                        | PR-1.1                                                | fleet counts, task/finding/meta, paused chrome, `<details>` disclosure identical; `subagentStatus`/`activityDataBuilders` boundary app-import-free; **FR-1.31 grep zero** (incl. `useElapsedSeconds` timers) | L (~800) |
| **PR-1.6** | Hoist approval card + receipt                              | Move presentational `ApprovalCard`, `ApprovalReceipt` + leaves `ActivityDetails`, `ActivityParams`, `useUndoCountdown`, `ActivityParam` type; neutralize `useUndoCountdown` timers (FR-1.30); keep `ApprovalTool` wiring host-side.                                               | `chat-surface/src/approvals/*`; `apps/frontend/.../tools/ApprovalTool.tsx` imports shim                                                                                                                                                                             | PR-1.1                                                | 4-zone card, category pill, receipt collapse, undo window/chip, cancelled/forwarded/chain paths identical; `ApprovalTool` tests pass; **FR-1.31 grep zero** (incl. `useUndoCountdown` timers)                | M (~500) |
| **PR-1.7** | Hoist `WorkspacePane` + tabs                               | Move pane shell + 5 tabs + `pluralize`; re-type `chatModel` props at the boundary; keep hooks host-side.                                                                                                                                                                          | `chat-surface/src/workspace/*` (+ `WorkspacePane.test.tsx`, `WorkspaceTabs.test.tsx`, `SourcesTab.test.tsx`, `AgentsTab.test.tsx`, `pluralize.test.ts`); shims                                                                                                      | PR-1.4 (sources), PR-1.5 (agents), PR-1.6 (approvals) | tab hide-empty, badges incl. "N live", tabpanel roles, overlay, close identical; no `chatModel/*` import in `chat-surface`                                                                                   | L (~900) |
| **PR-1.8** | Barrel finalize, shim cleanup, boundary + regression proof | Finalize `index.ts` exports; delete dead original bodies; verify ESLint boundary + full web regression; update READMEs.                                                                                                                                                           | `packages/chat-surface/src/index.ts`; `packages/chat-surface/README.md`; `apps/frontend/src/features/chat/README*` if present; remove superseded originals                                                                                                          | PR-1.1..1.7                                           | both packages typecheck+lint+test green; zero web DOM diff; live desktop-package smoke import compiles                                                                                                       | S (~150) |

## 8. Testing plan

Runner: TS packages/apps use **vitest** via `npm run test --workspace @0x-copilot/chat-surface` and
`npm run test --workspace @0x-copilot/frontend`; typecheck via `npm run typecheck --workspace <pkg>`; lint via
`npm run lint --workspace @0x-copilot/chat-surface`. (No Python in this phase.) Tests move **with** their components so
coverage never drops; the same assertions run from the new location.

**Unit (named cases)**

- `packages/chat-surface/src/messages/MarkdownLink.test.tsx` — `#cite-ord:3` renders `OrdinalCitationChip`; `#cite:cX`
  renders `CitationChip`; `https://…` renders external `<a target=_blank rel=noreferrer>` with compact
  `markdownLinkLabel`. _(FR-1.3)_
- `packages/chat-surface/src/messages/ReasoningGroup.test.tsx` — label is "Thinking…" when `status==="running"`, else
  "Thought process"; `data-status` reflects status; elapsed stamp hidden when 0. _(FR-1.4)_
- `packages/chat-surface/src/messages/citationRemarkPlugin.test.ts` + `streamingCursor.test.tsx` — unchanged assertions
  pass from either location; partial `[[` renders as text; `streamingCursorProps("running")` adds
  `assistant-markdown--streaming`. _(FR-1.1, FR-1.2)_
- `packages/chat-surface/src/composer/ModelPill.test.tsx` — model list renders; `onAddCustom("openai/gpt-x")` fires;
  disabled state. `ThinkingDepthControl.test.tsx` — returns null when `visible=false`. _(FR-1.6)_
- `packages/chat-surface/src/composer/AttachmentPill.test.tsx` — pill renders name/size. A new
  `AssistantComposer.filepicker.test.tsx` — clicking "Attach file" calls the injected `FilePickerPort.pick`, NOT
  `document.createElement`. _(FR-1.9, FR-1.10)_
- `packages/chat-surface/src/citations/MessageSourcesStrip.test.tsx` — empty citations → `null`; multiple → sorted by
  `ordinal`. `CitationsContext` — `useRunCitations` returns `[]` for `sealedOnly` on non-terminal run;
  `useOrdinalCitation` falls back to scanning runs when `activeRunId` null. _(FR-1.12, FR-1.13, FR-1.15)_
- `packages/chat-surface/src/subagents/{SubagentCard,FleetSubagentRow,subagentCardViewModel}.test.*` — VM truncates
  task/finding + strips code fences; paused status renders amber chip + "Review approval →"; fleet card running/total
  counts. _(FR-1.16, FR-1.19)_
- `packages/chat-surface/src/approvals/ApprovalCard` (new `ApprovalCard.test.tsx`) — renders 4 zones; category pill
  `vendor·access`; empty `params` → no frame. `ApprovalReceipt.test.tsx` — `kind` glyph/label map; undo button inside
  window, "Undo requested" chip when `undoRequestedAt` set, nothing past expiry. _(FR-1.20, FR-1.23)_
- `packages/chat-surface/src/workspace/{WorkspacePane,WorkspaceTabs,SourcesTab,AgentsTab,pluralize}.test.*` — hide
  empty tabs; `agentsBadge` shows `"N live"` when running>0 else count; `role="tabpanel"` + `data-active-tab`; close
  fires `state.close("manual")`. _(FR-1.24, FR-1.26)_

**Integration (cross-module, mocked transport/props)**

- `apps/frontend` `ApprovalTool.test.tsx` — with the shimmed `ApprovalCard`/`ApprovalReceipt`, approve→resume,
  reject→resume, forward→picker, run-cancelled→"Cancelled", chain-final→receipt; all still pass. _(FR-1.22)_
- `apps/frontend` `WorkspacePane.test.tsx` — hooks (`useApprovalsQueue`, `useWorkspacePaneState`) feed the shimmed pane;
  tab switching + badges unchanged. _(FR-1.25)_
- `apps/frontend` composer-flow test — `AssistantComposer` shim with the web `FilePickerPort` impl + portal slot:
  attach file, add custom model, attach skill, submit prefixes skill instructions, stop cancels. _(FR-1.11)_

- `packages/chat-surface/src/subagents/useElapsedSeconds.test.ts` + `approvals/useUndoCountdown.test.ts` (move with the
  hooks) — the 5000 ms elapsed tick and 1000 ms undo tick still fire on `vi.advanceTimersByTime`, proving the
  `window.`-prefix→bare-timer rewrite (FR-1.30) preserved behavior. _(FR-1.18, FR-1.21, FR-1.30)_

**Boundary / lint (the substrate guard)**

- `npm run lint --workspace @0x-copilot/chat-surface` after every PR — `no-restricted-globals` and
  `no-restricted-imports` (`apps/*`, `@0x-copilot/frontend`) report **zero**. This is the machine check for FR-1.7 /
  FR-1.28; a moved file that still references `window`/`document`/`chatModel` fails CI. _(US-1.7)_
- **Exhaustive enumeration grep (FR-1.31)** — CI (or the PR checklist) runs
  `grep -rnE '\b(window|document|localStorage|sessionStorage|fetch|EventSource|XMLHttpRequest|WebSocket|navigator|location|history)\b|createPortal' packages/chat-surface/src`
  and asserts **zero** hits (excluding the sanctioned `globalThis.localStorage` KeyValueStore ref impl). This catches a
  banned global that lint config might not name and is the guard that the FR-1.9 list was _complete_, not exemplary. Known
  cleared inventory: `AssistantComposer` ×4, `useElapsedSeconds`, `useUndoCountdown`. _(FR-1.31)_

**Regression guard (Phase 1's defining test — web must be identical)**

- Full `npm run test --workspace @0x-copilot/frontend` green after every PR (no snapshot / DOM diff).
- `npm run typecheck --workspace @0x-copilot/frontend` and `--workspace @0x-copilot/api-types` green.
- Spot DOM-identity checks: `assistant-markdown` / `--streaming` classes; `.aui-reasoning-group[data-status]`;
  `citation-chip` / `citation-chip--unresolved`; `atlas-approval-card[data-status=waiting]` 4 zones;
  `atlas-workspace-pane` badges. Any class-name or structure change is a fail (FR-1.29).

**E2E / live desktop smoke (per `apps/desktop/SMOKE.md`)**

- Phase 1 adds no desktop consumer, so the live smoke is a **build/import** smoke: after PR-1.8,
  `npm run typecheck --workspace @0x-copilot/chat-surface` + a throwaway import of the new barrels compiles, proving the
  package is desktop-consumable (no browser global at module-eval time). The full boot→run→approve live smoke is
  Phase 3/6 (unit fakes have hidden real-run breakage before — see MEMORY "Virtuals launch effort"; the live path is
  exercised when desktop actually mounts the surface in Phase 3).

**FR → test map:** FR-1.1/1.2 → `citationRemarkPlugin`/`streamingCursor` tests; FR-1.3 → `MarkdownLink.test`;
FR-1.4 → `ReasoningGroup.test`; FR-1.5/1.11/1.22/1.25 → `apps/frontend` shim integration tests + regression suite;
FR-1.6 → `ModelPill`/`ThinkingDepthControl` tests; FR-1.7/1.28 → lint boundary + both typechecks;
FR-1.8 → per-PR green tree + revert check; FR-1.9/1.10 → `AssistantComposer.filepicker.test`;
FR-1.12/1.13/1.15 → `CitationsContext`/`MessageSourcesStrip` tests; FR-1.14 → `SourcePreview` host test;
FR-1.16/1.17/1.18/1.19 → subagents tests; FR-1.20/1.21/1.23 → `ApprovalCard`/`ApprovalReceipt` tests;
FR-1.24/1.26/1.27 → workspace tests + lint boundary; FR-1.29 → regression guard suite;
FR-1.30 → `useElapsedSeconds`/`useUndoCountdown` fake-timer tests + slash-cue behavior in composer-flow test;
FR-1.31 → exhaustive enumeration grep (boundary/lint section).

## 9. UI/UX acceptance checklist

Phase 1 is a **move with zero visual change**; the checklist is therefore an _invariance_ checklist against
[`DESIGN-SPEC.md`](../design-reference/DESIGN-SPEC.md) — the existing rendering must still match today's DOM (which
Phase 2/0B later retokenizes). Tokens below are the current design-system values the moved components already consume.

- [ ] **Tokens/dims unchanged** — moved components keep consuming `--color-*`, `--font-size-*`, radii, spacing via the
      same class names (`aui-*`, `atlas-*`, `citation-chip`); no inline hex introduced. Streaming markdown keeps the
      `assistant-markdown` / `assistant-markdown--streaming` hooks that `styles.css` reduced-motion rule keys off.
- [ ] **Approval card** — 4 zones intact: header (shield glyph · title · reason · `vendor·access` pill) · inset params
      frame · actions row · footer reassurance line; `data-status="waiting"` on the card.
- [ ] **Approval receipt** — one line `glyph · LABEL · title · meta`; undo button `Undo (Ns)` inside window, "Undo
      requested" chip after, nothing past expiry.
- [ ] **Workspace pane** — tab strip hides empty surfaces; badges: sources count, `"N live"` running agents, pending
      approvals; close `✕`; overlay `data-overlay` below 1100px.
- [ ] **Citation chip** — resolved chip is a `<a class="citation-chip">` with `ordinal`; unresolved is
      `<span class="citation-chip citation-chip--unresolved">?</span>`.
- [ ] **States** — default / hover (citation preview card; model pill anchored menu) / active (selected tab, pressed
      connectors button) / **focus-visible** (existing focus ring, unchanged) / **loading** (`sourcesLoading`,
      `draftLoading`, `skillsLoading` shimmers/rows) / **empty** (empty Sources strip → null; empty tabs hidden;
      workspace-pane closed → null) / **error** (`sourcesError`/`draftError`/`skillsError` rows) / **streaming** (Streamdown
      fade-in cursor via `streamingCursorProps`, blinking cursor + `streaming · N%` behavior preserved).
- [ ] **a11y** — roles preserved: `WorkspaceTabs` tablist/tab + `role="tabpanel"` (`aria-label` = active tab label);
      `ReasoningGroup` is a native `<details>` disclosure (Enter/Space toggles); `ApprovalReceipt` `role="note"`; slash-cue
      `role="status"`; composer `+` button `aria-haspopup="menu"` + `aria-expanded`; citation chip `aria-label` on the
      unresolved placeholder. Keyboard: ⌘↵ approve path via host `ApprovalFocusContext` (unchanged); composer `/` opens
      skills, `↵` send / `⇧↵` newline hint intact.
- [ ] **prefers-reduced-motion** — the `assistant-markdown--streaming` class (the reduced-motion hook) is preserved by
      `streamingCursorProps`; no new animation added.
- [ ] **Contrast** — no color change; existing token contrast preserved (verified by "no CSS/class diff").
- [ ] **Theming** — light + dark unchanged (components consume tokens, not literals); `[data-density]` unaffected.
- [ ] **Single-accent discipline** — no new decorative color introduced; moved components use existing tokens only
      (v2 single-accent enforcement is Phase 0C/2, not Phase 1).
- [ ] **Component reuse noted** — `MarkdownLink` reuses headless `CitationChip`/`OrdinalCitationChip`; `AssistantComposer`
      reuses base `Composer`; `WorkspacePane` reuses `WorkspaceTabs`; cards reuse design-system `Card`/`Button`/`IconButton`/`Badge`.

## 10. Dependencies & sequencing

- **Upstream (blocked by):** Phase 0E (`chat-surface` module homes + shim pattern + ESLint boundary guard) — done;
  Phase 0-B ports facade (`ports/index.ts`, `FilePickerPort`) — done. Phase 1 does not depend on Phase 0B tokens or 0A
  desktop CSS wiring (it changes no styling).
- **Internal order (DAG):** PR-1.1 (barrel convention baseline) → PR-1.2 → PR-1.3 (composer needs its sub-controls first);
  PR-1.4, PR-1.5, PR-1.6 each depend only on PR-1.1 and may parallelize; PR-1.7 depends on PR-1.4/1.5/1.6 (the pane tabs
  render sources/agents/approvals); PR-1.8 depends on all. No cycles.
- **Downstream (blocks):** Phase 3 (Run cockpit) — `ThreadCanvas` right-rail reuses `WorkspacePane` (needs 1F);
  Run chat reuses the composer + markdown + citations + approvals (needs 1A–1E). Phase 2 (shell) is independent of
  Phase 1 and may run in parallel per [`PLAN.md`](../PLAN.md) §9. Desktop consumption of these components is Phase 2/3.

## 11. Risks & mitigations

| Risk                                                                                                            | Likelihood       | Impact | Mitigation / rollback                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| --------------------------------------------------------------------------------------------------------------- | ---------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hoisting regresses web behavior                                                                                 | Med              | High   | One family per PR; tests move with code; full `apps/frontend` vitest + typecheck gate each PR; any single PR is revertible in isolation (US-1.8).                                                                                                                                                                                                                                                                                                                                                                               |
| A moved file smuggles a browser global into `chat-surface` (the FR-1.9 example list understated the real count) | High             | High   | Enumeration is exhaustive, not exemplary (FR-1.31): a full grep of moved files gates each PR. Known inventory cleared this phase — `AssistantComposer` (file picker→`FilePickerPort`, `AnchoredPlusMenu`+outside-click→host slots, slash-cue→bare timers), `useElapsedSeconds` + `useUndoCountdown` (`window.set/clearInterval`→bare timers, FR-1.30). DOM-bound adapters (`AnchoredPlusMenu`, `SourcePreview`, connectors popover) stay host-side as slots/`previewProps`. CI grep + `no-restricted-globals` fail on any leak. |
| Deep `chatModel/*` coupling in `WorkspacePane`/subagents/approvals blocks a clean move                          | High             | Med    | Re-type props at the boundary to `@0x-copilot/api-types` / chat-surface-local types (FR-1.17, FR-1.21, FR-1.27); keep reducers/hooks host-side; move only pure leaves. If a leaf can't be cleanly split, keep it host-side and inject via prop rather than force the move.                                                                                                                                                                                                                                                      |
| `ThinkingDepth` (host) vs `Depth` (chat-surface) duplication causes drift                                       | Med              | Low    | Phase 1 re-exports `depth.ts` from `chat-surface`; document the duplicate; defer unification to Phase 3E (do not force a risky merge now).                                                                                                                                                                                                                                                                                                                                                                                      |
| PR-1.3 / PR-1.7 exceed ~1000 LOC with moved tests                                                               | Med              | Low    | Split allowed: 1.3 may shed `AttachmentPill` into 1.2; 1.7 may land tabs in two commits (Sources/Agents first, Draft/Approvals/Skills second) still under one mergeable PR umbrella.                                                                                                                                                                                                                                                                                                                                            |
| Import-site churn across ~67 files                                                                              | Low              | Med    | Prefer shim files at the original paths so specifiers don't change en masse; only `ApprovalTool` and `depth` re-point explicitly.                                                                                                                                                                                                                                                                                                                                                                                               |
| CSS lives in `apps/frontend/src/styles.css` (not moved) while components move to `chat-surface`                 | High (by design) | Low    | Intentional for Phase 1 — components are consumed by class name; CSS ownership migration is Phase 2. Document that `chat-surface` components render unstyled without the host stylesheet (acceptable until Phase 2/0A wires design-system CSS on desktop).                                                                                                                                                                                                                                                                      |

## 12. Definition of done

- [ ] All FR-1.1 … FR-1.31 met.
- [ ] Six families (`messages` markdown, `composer`, `citations`, `subagents`, `approvals`, `workspace`) live in
      `packages/chat-surface/src`; `apps/frontend` re-exports/adapts them; no re-declared duplicates remain.
- [ ] `npm run typecheck` + `npm run lint` + `npm run test` green for **both** `@0x-copilot/chat-surface` and
      `@0x-copilot/frontend` after every PR and at PR-1.8.
- [ ] `chat-surface` ESLint `no-restricted-globals` + `no-restricted-imports` report zero, **and** the exhaustive
      enumeration grep (FR-1.31) returns zero banned-global/`createPortal` hits in `packages/chat-surface/src` (excluding the
      sanctioned `globalThis.localStorage` KeyValueStore) — package stays framework-agnostic and app-import-free.
- [ ] Web app behaviorally identical: full `apps/frontend` vitest green, zero DOM/class/snapshot diff on the spot-checked
      surfaces (§9 regression guard); manual smoke of composer/citations/approvals/workspace in `make dev` shows no change.
- [ ] Build/import smoke: the new barrels compile as a desktop-consumable package (no module-eval browser global).
- [ ] Docs updated: `packages/chat-surface/README.md` lists the new modules; `apps/frontend` chat README (if present)
      notes the shim/adapter split; this PRD's flagged couplings (`depth` dup, CSS ownership, `SourcePreview` host adapter)
      recorded for Phase 2/3.
- [ ] No dead code: superseded original bodies removed; every moved test runs from its new home; no orphaned imports.
