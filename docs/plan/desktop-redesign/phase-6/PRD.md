# Phase 6 — Command palette, polish & live verify

> Design source of truth: [`DESIGN-SPEC.md`](../design-reference/DESIGN-SPEC.md) §6 (command palette + shortcuts), §5 (modal/flow patterns), §1 (shell/topbar), §0 (tokens/dims). Plan: [`PLAN.md`](../PLAN.md) §8 Phase 6 (6A–6E), §9 sequencing.

---

## 1. Context & problem

Phases 0–5 stand up the desktop redesign: v2 "quiet" tokens/fonts are loaded (P0), the interaction layer is consolidated into `packages/chat-surface` (P1), the shell is the 6-destination profile-gated IA (P2), the Run cockpit mounts the real `ThreadCanvas` (P3), the list destinations exist (P4), and the solo Settings surface with BYOK + local models + approval policy is built (P5). What is still missing is the **connective tissue and the cleanup**: a `⌘K` command palette that lets a solo user jump to any of the 6 destinations, any settings section, or launch a key flow without hunting through the rail; the full keyboard-shortcut set from `DESIGN-SPEC.md` §6; removal of the scaffolding (`DesktopPlaceholder`, the superseded second `CommandPalette`) that Phases 1–5 left standing; and — critically — a **live** end-to-end desktop smoke, because unit fakes have hidden real-run breakage before (see [MEMORY: Virtuals launch effort]).

This phase delivers `DESIGN-SPEC.md` §6 in full and closes `PLAN.md`'s Definition of Done items "`⌘K` palette", "end-to-end **live** smoke passes", and "no `DesktopPlaceholder`". It builds directly on the destination outlet, Run handlers, and Settings sections produced in P2–P5. It does **not** re-open any of those surfaces; it wires them together and verifies the whole.

Ground-truth note (read the code, not the plan): the worktree currently contains **two** palette implementations — the canonical search-port palette at `packages/chat-surface/src/shell/CommandPalette.tsx` (consumed by the web host `apps/frontend/src/features/palette/PaletteHost.tsx`) and a **superseded** route-table palette at `packages/chat-surface/src/palette/CommandPalette.tsx`. The superseded one is re-exported from the barrel **under the alias `RouteJumpPalette`** (`packages/chat-surface/src/index.ts:205`, inside the `=== Phase 1-D routing-palette ===` marker block at `:198‑206`) and has **zero non-test importers** (verified by `grep`; its only consumer is its own colocated test). So there is _not_ a name collision on `CommandPalette` — the barrel already names exactly one `CommandPalette` (the shell export at `src/index.ts:209`); the cleanup is deleting the aliased twin, not deduplicating a name. The canonical palette's `activateHit` only dispatches `entity` hits (via `<ItemLink>`); `navigation` / `action` / `command` hits currently just close the palette with **no effect** (`packages/chat-surface/src/shell/CommandPalette.tsx:209‑224`, verified). Those three facts drive PR-6.1 and PR-6.2 below.

## 2. Goals / Non-goals

**Goals**

- Wire a working `⌘K` command palette on desktop that routes to the 6 destinations, the settings sections, and the three key action-flows + New chat, per `DESIGN-SPEC.md` §6.
- Make the canonical `CommandPalette` (SSOT) actually dispatch `navigation` / `action` / `command` hits — closing the real gap at `shell/CommandPalette.tsx:209‑224` — without changing web behavior.
- Implement the full keyboard-shortcut set (`DESIGN-SPEC.md` §6) as a framework-agnostic shell hook, wired on desktop to the nav/run/settings handlers built in P2/P3/P5.
- Remove dead code: `apps/desktop/renderer/DesktopPlaceholder.tsx`, the superseded `packages/chat-surface/src/palette/*`, and any orphaned exports.
- Rewrite `apps/desktop/SMOKE.md` into the redesign flow and **run the live smoke**: boot → run → approve → scrub → settings → BYOK/local-model.
- Update READMEs/docs (`apps/desktop/README.md`, `packages/chat-surface` palette/shell docs, `PLAN.md` status).

**Non-goals**

- Building or restyling any destination, Run panel, or Settings section — owned by P2–P5.
- Server-side palette search (`GET /v1/palette/search`) — the solo desktop palette is a **local static command registry**; the server-search port stays a web concern and is untouched (deferred, out of scope).
- Automating the smoke into Playwright/Spectron — explicitly deferred to "Phase 8" per `SMOKE.md` "Out of scope".
- Changing the `apps/frontend` web palette behavior — it must stay behaviorally identical (regression guard, §8).
- Retiring the web `SettingsScreen` 1,400-line path (`apps/frontend/src/features/settings/*`) — that is the web Settings-redo, not this desktop phase.

## 3. User stories

| ID          | Role                 | Story                                                                                                                                                                     | Acceptance criteria (Given/When/Then)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ----------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **US-6.1**  | Solo user            | As a solo user, I want to press `⌘K` to open a command palette, so that I can act without reaching for the rail.                                                          | **Given** the desktop shell is mounted and focus is anywhere outside a text input, **When** I press `⌘K` (or `Ctrl+K`), **Then** the palette modal opens, the search input autofocuses, and the empty-query state shows the starter command list. **Given** the palette is open, **When** I press `Esc` or click the scrim, **Then** it closes and focus returns to the prior element. **Given** I click the topbar `Search… ⌘K` trigger, **Then** the same palette opens.                                                                                                                                                                            |
| **US-6.2**  | Solo user            | As a solo user, I want the palette to list "Go to Run / Chats / Projects / Activity / Tools / Skills", so that I can jump to any destination.                             | **Given** the palette is open with an empty query, **When** I see the Navigation group, **Then** all 6 destinations appear in spec order. **When** I select "Go to Tools" and press `Enter`, **Then** the shell's active destination becomes `tools`, the palette closes, and the Tools destination renders. **When** I type "act", **Then** only "Go to Activity" matches.                                                                                                                                                                                                                                                                           |
| **US-6.3**  | Solo user            | As a solo user, I want palette entries for "Model & behavior", "Appearance", and "Open Settings", so that I can deep-link into a settings section.                        | **Given** the palette is open, **When** I select "Appearance", **Then** Settings opens focused on the Appearance section. **When** I select "Open Settings", **Then** Settings opens on its default section (`profile`). **Given** Settings is already open, **When** I pick "Model & behavior", **Then** the section switches without a full remount.                                                                                                                                                                                                                                                                                                |
| **US-6.4**  | Solo user            | As a solo user, I want palette entries for "New chat", "Add a provider key", "Download a local model", and "Connect a tool", so that I can start a flow in one keystroke. | **Given** the palette is open, **When** I activate "Add a provider key", **Then** the palette closes and the Add-provider-key modal (`DESIGN-SPEC.md` §5) opens. **Likewise** "Download a local model" opens the download flow and "Connect a tool" opens the ConnectModal. **When** I activate "New chat", **Then** a fresh Run is started (`⌘N` path).                                                                                                                                                                                                                                                                                              |
| **US-6.5**  | Solo user            | As a solo user, I want the palette to handle empty / no-match / error states gracefully, so that it never feels broken.                                                   | **Given** an empty query, **Then** the starter list renders (no "No results"). **Given** I type a string that matches nothing (e.g. "zzzz"), **Then** the "No results." state renders. **Given** the local command registry throws, **Then** the palette clears the list and shows the "No results." state (never a blank crash). **Given** `[data-reduce-motion=1]`, **Then** the open/close has no transition.                                                                                                                                                                                                                                      |
| **US-6.6**  | Solo user            | As a solo user, I want the documented keyboard shortcuts to work, so that I can drive runs and navigation without the mouse.                                              | **Given** the shell is focused (not in a text field), **When** I press `⌘N`, **Then** a new run starts; `⌘,` opens Settings; `⌘⇧M` opens the local-model picker; `⌘⇧F` navigates to Activity search; `⌘K` opens the palette. **Given** the Run cockpit is active, **When** I press `⌘M`/`⌘←`/`⌘→`/`⌘L`/`⌘.`/`⌘↵`/`⌘⌫`, **Then** the corresponding Run handler (mode/rewind/step/live/pause/approve/reject built in P3) fires.                                                                                                                                                                                                                         |
| **US-6.7**  | Solo user            | As a solo user, I want shortcuts to _not_ fire while I'm typing, so that I never lose text or trigger an action mid-sentence.                                             | **Given** focus is in the composer/textarea/input or a `contenteditable`, **When** I press `⌘N` or any single-letter chord, **Then** the shortcut does **not** fire and the keystroke reaches the field. **Exception:** `⌘K` (palette) and `⌘,` (settings) still work from within an input, matching platform convention. **Given** a run-scoped shortcut (`⌘←`) is pressed while Run is not the active destination, **Then** it is a no-op.                                                                                                                                                                                                          |
| **US-6.8**  | Developer/maintainer | As a maintainer, I want the desktop renderer to mount the real destination outlet instead of `DesktopPlaceholder`, so that no scaffolding ships.                          | **Given** the desktop boots past sign-in, **Then** `ChatShell`'s children render the destination outlet (Run by default), **and** `apps/desktop/renderer/DesktopPlaceholder.tsx` no longer exists and is not imported anywhere. **When** I grep the repo for `DesktopPlaceholder`, **Then** zero references remain.                                                                                                                                                                                                                                                                                                                                   |
| **US-6.9**  | Developer/maintainer | As a maintainer, I want the superseded second `CommandPalette` deleted, so that there is one palette SSOT.                                                                | **Given** the redesign palette wiring lands, **Then** `packages/chat-surface/src/palette/` (the route-table `CommandPalette` + its `index.ts` + test) is deleted, **and** the barrel's `RouteJumpPalette` re-export together with its `=== Phase 1-D routing-palette ===` marker block (`src/index.ts:198‑206`) are removed, leaving the barrel's single shell `CommandPalette` export (`src/index.ts:209`) untouched. **When** I run `npm run typecheck --workspace @0x-copilot/chat-surface` and `--workspace @0x-copilot/frontend`, **Then** both pass green (the web `PaletteHost` imports the shell `CommandPalette`, never `RouteJumpPalette`). |
| **US-6.10** | Developer/maintainer | As a maintainer, I want a live end-to-end desktop smoke, so that unit fakes cannot hide a real-run breakage.                                                              | **Given** the backend stack is up and the desktop is launched per `SMOKE.md`, **When** I walk boot → sign-in → start a run → approve an on-surface diff → scrub the timeline → open Settings → add a BYOK key → download a local model, **Then** every step behaves as written and no console error / CSP violation appears. **Given** any step deviates, **Then** the smoke doc's step number is cited in a bug (per `SMOKE.md` contract).                                                                                                                                                                                                           |
| **US-6.11** | Developer/maintainer | As a maintainer, I want READMEs/docs updated, so that the shipped state matches the docs.                                                                                 | **Given** the phase merges, **Then** `apps/desktop/README.md` describes the 6-dest shell + palette + shortcuts (not "Phase 1-A ships the substrate only"), `SMOKE.md` is the redesign flow, and `PLAN.md` §11 DoD items for `⌘K`/live-smoke/`DesktopPlaceholder` are ticked.                                                                                                                                                                                                                                                                                                                                                                          |

## 4. Functional requirements

### Palette component (SSOT — `packages/chat-surface/src/shell/CommandPalette.tsx`)

- **FR-6.1** The canonical `CommandPalette` MUST dispatch non-entity hits: on activation of a `navigation` hit it MUST invoke a new optional `onNavigate(route: string, hit)` prop; on an `action`/`command` hit it MUST invoke a new optional `onRunAction(token: string, hit)` prop; then close. When these props are omitted, behavior MUST be byte-identical to today (close only) so the web host is unregressed. _(US-6.2, US-6.3, US-6.4; regression guard §8.)_
- **FR-6.2** The palette's empty-query state MUST render `starterActions` (host-supplied), and a non-empty query with zero hits MUST render the "No results." state; a rejected port promise MUST clear the list and render "No results." (never throw). _(US-6.5.)_
- **FR-6.3** The palette MUST retain its existing ARIA contract: `role="dialog"`+`aria-modal`, `role="combobox"` input with `aria-controls`/`aria-activedescendant`, `role="listbox"`, `role="option"` rows, `↑↓` wrap selection, `Enter` activate, `Esc`/scrim close, 150 ms debounce. _(US-6.1, US-6.5.)_

### Desktop palette wiring (`apps/desktop/renderer/`)

- **FR-6.4** The desktop MUST provide a **local static command registry** exposed as a `PaletteSearchPort` (`packages/chat-surface/src/ports/PaletteSearchPort.ts`) that returns `PaletteHit`s filtered by case-insensitive substring on title/subtitle; NO network call is made for the solo palette. _(US-6.2–6.4.)_
- **FR-6.5** The registry MUST contain exactly the `DESIGN-SPEC.md` §6 entries: Navigation → Go to Run / Chats / Projects / Activity / Tools / Skills; Actions → New chat, Add a provider key, Download a local model, Connect a tool; Navigation(settings) → Model & behavior, Appearance, Open Settings. _(US-6.2, US-6.3, US-6.4.)_
- **FR-6.6** A desktop `PaletteHost` MUST mount exactly one `CommandPalette`, own its `open` state, wire `useCommandPaletteHotkey` for `⌘K`, and dispatch: `navigation` destination hits → `onNavigate(slug)` on the shell; `navigation` settings hits → open Settings at the target section; `action` hits → the corresponding flow-launch callback (New chat / Add key / Download model / Connect tool). _(US-6.1–6.4.)_
- **FR-6.7** The topbar MUST render the `CommandPaletteTrigger` ("Search… ⌘K", 250 px, suppressed on Run and Settings per `DESIGN-SPEC.md` §1) that opens the same palette. _(US-6.1.)_
- **FR-6.8** Activating a settings-section hit MUST open Settings focused on that section (`appearance`, `model-and-behavior`, or default `profile`); if Settings is already open it MUST switch section in place without remount. _(US-6.3.)_

### Keyboard shortcuts (`packages/chat-surface/src/shell/useShellShortcuts.ts` — new)

- **FR-6.9** A new framework-agnostic `useShellShortcuts` hook MUST register the `DESIGN-SPEC.md` §6 chord set on `globalThis.document` (same substrate convention as `useCommandPaletteHotkey`) and no-op when `globalThis.document === undefined`. _(US-6.6.)_
- **FR-6.10** The hook MUST map each chord to a caller-supplied callback: `⌘N`→onNewRun, `⌘K`→onOpenPalette, `⌘,`→onOpenSettings, `⌘⇧M`→onOpenLocalModelPicker, `⌘⇧F`→onSearchActivity, `⌘M`→onSwitchMode, `⌘←`→onRewind, `⌘→`→onStepForward, `⌘L`→onJumpLive, `⌘.`→onPauseRun, `⌘↵`→onApprove, `⌘⌫`→onReject; a callback left undefined makes that chord a no-op. _(US-6.6.)_
- **FR-6.11** The hook MUST NOT fire single-letter/nav chords while focus is in an `<input>`, `<textarea>`, `<select>`, or `contenteditable` element; `⌘K` and `⌘,` are the only chords exempt from the input guard. _(US-6.7.)_
- **FR-6.12** Each chord MUST match modifiers exactly (`metaKey||ctrlKey` for the command modifier, `shiftKey` required only for `⌘⇧M`/`⌘⇧F`, `altKey` always false) so that, e.g., `⌘⇧M` does not also trigger `⌘M`. _(US-6.6.)_
- **FR-6.13** The desktop MUST wire `useShellShortcuts` with the run-scoped callbacks (mode/rewind/step/live/pause/approve/reject) delegated to the P3 Run handlers, guarded so they are no-ops unless Run is the active destination. _(US-6.6, US-6.7.)_
- **FR-6.14** `⌘K` handling MUST be single-sourced: with `useShellShortcuts` owning `⌘K`→onOpenPalette, the desktop MUST NOT also mount `useCommandPaletteHotkey` for the same key (no double-toggle). _(US-6.1, US-6.6.)_
- **FR-6.15** The Shortcuts settings page (P5, `DESIGN-SPEC.md` §4 Appearance→Shortcuts) MUST read the same shortcut definition table this hook uses, so the displayed list cannot drift from the wired chords. _(US-6.6; SSOT.)_

### Dead-code removal

- **FR-6.16** `apps/desktop/renderer/DesktopPlaceholder.tsx` MUST be deleted and its import removed from `apps/desktop/renderer/bootstrap.tsx`; the `ChatShell` children slot MUST render the real destination outlet. _(US-6.8.)_
- **FR-6.17** `packages/chat-surface/src/palette/CommandPalette.tsx`, `packages/chat-surface/src/palette/index.ts`, and `packages/chat-surface/src/palette/CommandPalette.test.tsx` MUST be deleted, **and** the barrel `packages/chat-surface/src/index.ts` MUST drop the `RouteJumpPalette` re-export plus its `=== Phase 1-D routing-palette ===` marker block (`:198‑206`). The barrel already names exactly one `CommandPalette` (the shell export at `:209`); the route-table twin ships under the alias `RouteJumpPalette`, so the required change is _removing that alias export_, not deduplicating a clashing name. The shell `CommandPalette` export MUST remain byte-identical. _(US-6.9.)_
- **FR-6.18** After removal, `grep -rn "DesktopPlaceholder\|/palette/CommandPalette\|RouteJumpPalette" apps packages` MUST return zero non-historical references, and `npm run typecheck` MUST pass for `@0x-copilot/chat-surface`, `@0x-copilot/desktop`, and `@0x-copilot/frontend`. _(US-6.8, US-6.9.)_
- **FR-6.19** The stale `packages/chat-surface/src/shell/destinations.ts` header comment ("Order matches the 12 top-level destinations… P5 adds Routines as the 12th") and the `packages/chat-surface/src/shell/index.ts` `=== W0 placeholder for not-yet-built destinations ===` (`:62`) + `=== Phase 12 — global ⌘K command palette ===` (`:69`) section markers MUST be reconciled to the 6-dest redesign reality (documentation-only, no behavior change beyond what P2 already applied). _(US-6.9, US-6.11.)_

### Smoke + docs

- **FR-6.20** `apps/desktop/SMOKE.md` MUST be rewritten to cover the redesign flow: boot → sign-in → **start a run** → **approve an on-surface diff** → **scrub the timeline** → **open Settings via `⌘,`** → **add a BYOK key** → **download a local model**, plus a palette (`⌘K`) and shortcut sanity block. _(US-6.10.)_
- **FR-6.21** The live smoke MUST be executed against a running stack and the result (pass, or bug refs by step number) recorded; a console/CSP-clean session is required (the `fetch("https://example.com")` CSP check from `README.md` MUST still fail). _(US-6.10.)_
- **FR-6.22** `apps/desktop/README.md` and `PLAN.md` §11 MUST be updated to reflect shipped state (6-dest shell, palette, shortcuts, no `DesktopPlaceholder`). _(US-6.11.)_

## 5. Architecture & system design

### Single source of truth

- **One palette component.** The canonical `packages/chat-surface/src/shell/CommandPalette.tsx` is the only palette. The route-table twin at `packages/chat-surface/src/palette/CommandPalette.tsx` is **deleted** (FR-6.17); it is re-exported from the barrel only as the aliased `RouteJumpPalette` (`src/index.ts:205`) with zero non-test importers (verified), so deletion is import-safe and does not touch the shell `CommandPalette` export. Web (`PaletteHost.tsx`) and desktop (new `PaletteHost`) both consume the one component through the `PaletteSearchPort` seam — differing only in which port they inject (web: `createWebPaletteSearchPort` → facade `/v1/palette/search`; desktop: new local static registry port).
- **One command registry.** The `DESIGN-SPEC.md` §6 entry list lives in exactly one desktop module (`palette-commands.ts`), consumed by the desktop port. It is data, not a second component.
- **One shortcut table.** The chord→intent mapping is defined once (`shell/shortcuts.ts`) and consumed by both `useShellShortcuts` (wiring) and the Shortcuts settings page (display) — FR-6.15 forbids a second copy.
- **Destination ownership.** The shell already owns the slug↔label SSOT (`shell/destinations.ts`, `ChatShell` reads it). The palette's navigation hits resolve to those same slugs via the desktop host's `onNavigate` — the palette never invents a second routing table.

### Boundaries & ports (respect `CLAUDE.md`)

- No `apps/*` imports another app's `src/`. Desktop consumes only `@0x-copilot/chat-surface` (+ `chat-transport`, `surface-renderers`, `api-types`) — never `apps/frontend`.
- `chat-surface` stays framework-agnostic: the new `useShellShortcuts` touches only `globalThis.document` (the sanctioned substrate touchpoint already used by `useCommandPaletteHotkey.ts` and `HashRouter`), never bare `window.fetch`/`localStorage`. All IO flows through the injected ports: **`PaletteSearchPort`** (palette data), **`Router`** (route navigation for entity hits), **`KeyValueStore`**/**`PresenceSignal`** (unchanged, already provided by `ChatShell`).
- The desktop static-registry port is an adapter that lives in `apps/desktop/renderer/` (host-side), satisfying `PaletteSearchPort`'s contract (`packages/chat-surface/src/ports/PaletteSearchPort.ts`) — the framework-agnostic invariant is preserved because the concrete IO (there is none — it's an in-memory filter) sits in the host, not the package.

### Data flow & key types

- **`PaletteHit` / `PaletteHitKind` / `PaletteSearchRequest` / `PaletteSearchResponse`** — `packages/api-types/src/palette.ts` (unchanged wire contract). Desktop registry emits `PaletteHit`s with `kind: "navigation"` (`route` = destination slug or `settings:<section>`), `kind: "action"` (`action_token` = `new_chat` | `add_provider_key` | `download_local_model` | `connect_tool`).
- **`CommandPaletteProps`** — extended with optional `onNavigate?(route, hit)` and `onRunAction?(token, hit)` (FR-6.1). Existing props (`open`, `onRequestClose`, `searchPort`, `starterActions`, `context`, `limit`, `onConnectToolHint`, `debounceMs`) unchanged.
- **`ShellShortcutMap`** — new type in `shell/shortcuts.ts`: `{ chord: ShortcutChord; intent: ShortcutIntent; label: string; inputSafe: boolean }[]`, plus `UseShellShortcutsOptions` = a partial record of `intent → () => void`.
- **`ShellDestinationSlug`** — `shell/destinations.ts` (the P2 6-dest set; the desktop host maps palette navigation routes to these).

### Reuse vs new

| Component / module                      | Disposition                                          | Path                                                         |
| --------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------ |
| Canonical `CommandPalette`              | **Reuse + extend** (add `onNavigate`/`onRunAction`)  | `packages/chat-surface/src/shell/CommandPalette.tsx`         |
| `PaletteHitRow`                         | Reuse (unchanged)                                    | `packages/chat-surface/src/shell/PaletteHitRow.tsx`          |
| `CommandPaletteTrigger`                 | Reuse (mount in desktop topbar)                      | `packages/chat-surface/src/shell/CommandPaletteTrigger.tsx`  |
| `useCommandPaletteHotkey`               | Reuse **or** fold into `useShellShortcuts` (FR-6.14) | `packages/chat-surface/src/shell/useCommandPaletteHotkey.ts` |
| `PaletteSearchPort`                     | Reuse (implement local adapter)                      | `packages/chat-surface/src/ports/PaletteSearchPort.ts`       |
| `PaletteHit` wire types                 | Reuse (unchanged)                                    | `packages/api-types/src/palette.ts`                          |
| Web `PaletteHost` (reference pattern)   | Reuse as blueprint (do not import)                   | `apps/frontend/src/features/palette/PaletteHost.tsx`         |
| `useShellShortcuts` hook                | **New**                                              | `packages/chat-surface/src/shell/useShellShortcuts.ts`       |
| Shortcut definition table               | **New**                                              | `packages/chat-surface/src/shell/shortcuts.ts`               |
| Desktop palette command registry        | **New**                                              | `apps/desktop/renderer/palette-commands.ts`                  |
| Desktop static `PaletteSearchPort`      | **New**                                              | `apps/desktop/renderer/DesktopPaletteSearchPort.ts`          |
| Desktop `PaletteHost`                   | **New**                                              | `apps/desktop/renderer/PaletteHost.tsx`                      |
| Superseded route-table `CommandPalette` | **Delete**                                           | `packages/chat-surface/src/palette/*`                        |
| `DesktopPlaceholder`                    | **Delete**                                           | `apps/desktop/renderer/DesktopPlaceholder.tsx`               |

## 6. Affected files / component inventory

**Create**

- `packages/chat-surface/src/shell/shortcuts.ts` — chord→intent table (SSOT for §6 shortcuts).
- `packages/chat-surface/src/shell/shortcuts.test.ts` — table integrity + chord-parse tests.
- `packages/chat-surface/src/shell/useShellShortcuts.ts` — global chord listener.
- `packages/chat-surface/src/shell/useShellShortcuts.test.ts` — dispatch + input-guard tests.
- `apps/desktop/renderer/palette-commands.ts` — the §6 command entries.
- `apps/desktop/renderer/DesktopPaletteSearchPort.ts` — local static `PaletteSearchPort`.
- `apps/desktop/renderer/DesktopPaletteSearchPort.test.ts` — filter/empty/error behavior.
- `apps/desktop/renderer/PaletteHost.tsx` — desktop palette + trigger + hotkey wiring.
- `apps/desktop/renderer/PaletteHost.test.tsx` — open/close/dispatch integration.

**Modify**

- `packages/chat-surface/src/shell/CommandPalette.tsx` — add `onNavigate`/`onRunAction` props; extend `activateHit` (currently `:209‑224` handles only `entity`).
- `packages/chat-surface/src/shell/CommandPalette.test.tsx` — add navigation/action dispatch cases.
- `packages/chat-surface/src/shell/index.ts` — export `useShellShortcuts`, `shortcuts` table; reconcile the `=== W0 placeholder for not-yet-built destinations ===` (`:62`) and `=== Phase 12 — global ⌘K command palette ===` (`:69`) section markers to the redesign's Phase-6 reality (FR-6.19).
- `packages/chat-surface/src/index.ts` — remove the `RouteJumpPalette` re-export + the `=== Phase 1-D routing-palette ===` marker block (`:198‑206`); the shell `CommandPalette` export (`:209`) stays byte-identical (FR-6.17).
- `packages/chat-surface/src/shell/destinations.ts` — reconcile the "12 top-level destinations" header comment (FR-6.19).
- `apps/desktop/renderer/bootstrap.tsx` — remove `DesktopPlaceholder` import + usage; render destination outlet; mount `PaletteHost`; wire `useShellShortcuts`; pass `onOpenSettings`.
- `apps/desktop/renderer/bootstrap.test.tsx` — replace placeholder assertion with outlet + palette-mount assertions.
- `apps/desktop/SMOKE.md` — rewrite to the redesign flow (FR-6.20).
- `apps/desktop/README.md` — update status/description (FR-6.22).
- `docs/plan/desktop-redesign/PLAN.md` — tick §11 DoD items (FR-6.22).

**Delete**

- `apps/desktop/renderer/DesktopPlaceholder.tsx` (FR-6.16).
- `packages/chat-surface/src/palette/CommandPalette.tsx`, `.../palette/index.ts`, `.../palette/CommandPalette.test.tsx` (FR-6.17). _(Note: verify the `palette/` dir has no other members before removing the folder — current `ls` shows only these three files.)_

## 7. PR / commit breakdown

Ordered; each independently mergeable, tree stays green (web + typecheck), ≤ ~1000 LOC.

- **PR-6.1 — Delete the superseded route-table palette.** _(S, ~120 LOC removed.)_ Scope: remove `packages/chat-surface/src/palette/*`; drop the `RouteJumpPalette` re-export + the `=== Phase 1-D routing-palette ===` marker block (`src/index.ts:198‑206`); confirm no importer other than the deleted test (verified: `RouteJumpPalette` has zero non-barrel importers, and `./palette` is imported only by the barrel alias and its own colocated test). Files: `palette/*` (delete), `src/index.ts`. Deps: none. Acceptance: `grep -rn "src/palette/CommandPalette\|RouteJumpPalette" apps packages` → zero; `npm run typecheck --workspace @0x-copilot/chat-surface` + `--workspace @0x-copilot/frontend` green; web palette unchanged (`apps/frontend/src/features/palette/PaletteHost.tsx` already imports the shell `CommandPalette`).
- **PR-6.2 — Dispatch non-entity hits from the canonical palette.** _(S, ~120 LOC.)_ Scope: add optional `onNavigate`/`onRunAction` props; extend `activateHit` to call them by `kind`; keep close-only default. Files: `shell/CommandPalette.tsx`, `shell/CommandPalette.test.tsx`. Deps: PR-6.1 (avoids editing two palettes). Acceptance: new tests prove `navigation`→`onNavigate`, `action`→`onRunAction`, `entity`→ItemLink (unchanged), and that omitting props reproduces today's close-only behavior; web `PaletteHost` (no new props) unregressed.
- **PR-6.3 — Desktop command registry + static search port.** _(M, ~200 LOC.)_ Scope: `palette-commands.ts` (the §6 entries) + `DesktopPaletteSearchPort.ts` (substring filter, starter list, never-throws). Files: the two new modules + their tests. Deps: PR-6.1/6.2 (types). Acceptance: port returns all §6 nav+action entries for empty query; filters case-insensitively; a thrown registry surfaces as empty list (FR-6.4/6.5); unit tests green.
- **PR-6.4 — Desktop PaletteHost + topbar trigger.** _(M, ~220 LOC.)_ Scope: `PaletteHost.tsx` mounts one `CommandPalette`, owns `open`, wires `⌘K` open (via the shortcut hook once PR-6.5 lands, or `useCommandPaletteHotkey` interim), dispatches navigation→`onNavigate(slug)`, settings→open-settings-at-section, actions→flow launchers; mount `CommandPaletteTrigger` in the desktop topbar (suppressed on Run/Settings). Files: `PaletteHost.tsx` (+test), `bootstrap.tsx`. Deps: PR-6.2, PR-6.3. Acceptance: integration test opens palette, activates "Go to Tools" → `onNavigate("tools")`, "Appearance" → settings section callback, "Add a provider key" → flow callback; trigger opens palette.
- **PR-6.5 — Shell keyboard-shortcut hook + table.** _(M, ~260 LOC.)_ Scope: `shortcuts.ts` (chord→intent SSOT) + `useShellShortcuts.ts` (listener, exact-modifier match, input guard) + tests. Files: the two new modules + tests; `shell/index.ts` exports. Deps: none (parallel to 6.3/6.4). Acceptance: unit tests prove each chord → its callback, `⌘⇧M`≠`⌘M`, input-guard blocks nav chords in a textarea while allowing `⌘K`/`⌘,`, `document===undefined` no-ops.
- **PR-6.6 — Wire shortcuts on desktop + single-source `⌘K`.** _(M, ~180 LOC.)_ Scope: `bootstrap.tsx` mounts `useShellShortcuts` with global callbacks (`⌘N`/`⌘,`/`⌘⇧M`/`⌘⇧F`/`⌘K`) and run-scoped callbacks delegated to P3 Run handlers (guarded on active destination); remove any duplicate `⌘K` listener (FR-6.14). Files: `bootstrap.tsx` (+test). Deps: PR-6.4, PR-6.5. Acceptance: pressing `⌘,` opens settings, `⌘⇧M` opens local-model picker, `⌘N` starts a run; `⌘K` toggles palette exactly once; run chords no-op off Run.
- **PR-6.7 — Remove DesktopPlaceholder; mount destination outlet.** _(S, ~120 LOC.)_ Scope: delete `DesktopPlaceholder.tsx`; `bootstrap.tsx` renders the real destination outlet (the P2/P3 outlet) as `ChatShell` children; update `bootstrap.test.tsx`. Files: delete + `bootstrap.tsx`/`bootstrap.test.tsx`. Deps: PR-6.4/6.6 (outlet already receives palette/shortcuts). Acceptance: `grep DesktopPlaceholder` → zero; renderer boots into Run; desktop typecheck + vitest green. _(Depends on P2E outlet existing; if P2E is not yet merged in this worktree, this PR blocks on it — see §10.)_
- **PR-6.8 — Docs: header/marker reconciliation + Shortcuts page binding.** _(S, ~80 LOC.)_ Scope: fix the stale 12-dest comment in `destinations.ts` and the "Phase 12 / W0" markers in `shell/index.ts`; bind the P5 Shortcuts settings page to the `shortcuts.ts` table (FR-6.15) if it currently hard-codes a list. Files: `destinations.ts`, `shell/index.ts`, the Shortcuts page (P5). Deps: PR-6.5. Acceptance: Shortcuts page renders from the SSOT table; no behavior change; typecheck green.
- **PR-6.9 — SMOKE.md rewrite, live smoke run, READMEs.** _(M, ~200 LOC docs.)_ Scope: rewrite `SMOKE.md` to the redesign flow; run the live smoke and record the result; update `README.md` + `PLAN.md` §11. Files: `SMOKE.md`, `README.md`, `PLAN.md`. Deps: PR-6.1…6.8 merged. Acceptance: the live walkthrough passes (or bugs filed by step); CSP `fetch` check still fails; DoD items ticked.

## 8. Testing plan

Runner: **vitest** for all TS (`npm run test --workspace @0x-copilot/chat-surface`, `npm run test --workspace @0x-copilot/desktop`, `npm run test --workspace @0x-copilot/frontend`). No Python in this phase.

**Unit — palette component** (`packages/chat-surface/src/shell/CommandPalette.test.tsx`)

- `navigation` hit activation calls `onNavigate` with the hit's `route`, then closes. _(FR-6.1)_
- `action` hit activation calls `onRunAction` with `action_token`, then closes. _(FR-6.1)_
- With `onNavigate`/`onRunAction` omitted, activating a navigation hit closes and calls neither (regression parity). _(FR-6.1)_
- Empty query renders `starterActions`; unmatched query renders "No results."; a rejected port promise renders "No results." not a throw. _(FR-6.2)_
- `↑↓` wrap, `Enter` activate, `Esc`/scrim close, `aria-activedescendant` mirrors selection. _(FR-6.3)_

**Unit — shortcut table + hook** (`shell/shortcuts.test.ts`, `shell/useShellShortcuts.test.ts`)

- Table has all 12 §6 chords, unique intents, `inputSafe` true only for `⌘K`/`⌘,`. _(FR-6.10, FR-6.11, FR-6.15)_
- Each chord dispatches its callback; undefined callback → no-op. _(FR-6.10)_
- `⌘⇧M` fires local-model-picker but **not** switch-mode; `⌘M` fires switch-mode but not `⌘⇧M`. _(FR-6.12)_
- Chord fired while focus is in a `<textarea>` is suppressed (except `⌘K`/`⌘,`). _(FR-6.11)_
- `globalThis.document === undefined` → hook no-ops; listener detached on unmount. _(FR-6.9)_

**Unit — desktop port + registry** (`apps/desktop/renderer/DesktopPaletteSearchPort.test.ts`)

- Empty query returns all 6 nav + 4 action + 3 settings entries. _(FR-6.5)_
- "appear" filters to Appearance only; "model" returns Model & behavior + Download local model. _(FR-6.4)_
- A registry that throws → resolved empty `hits` (never rejects hard beyond the palette's catch). _(FR-6.4/6.2)_

**Integration — desktop PaletteHost** (`apps/desktop/renderer/PaletteHost.test.tsx`)

- `⌘K` opens; `Esc` closes. _(FR-6.6)_
- Activate "Go to Activity" → shell `onNavigate("activity")`. _(FR-6.6)_
- Activate "Appearance" → settings-open callback with `appearance`; "Open Settings" → default `profile`. _(FR-6.8)_
- Activate "Add a provider key" / "Download a local model" / "Connect a tool" / "New chat" → the matching flow callback fires. _(FR-6.6)_
- Trigger button opens the same palette. _(FR-6.7)_

**Integration — bootstrap** (`apps/desktop/renderer/bootstrap.test.tsx`)

- No `desktop-placeholder` testid in the tree; destination outlet renders; `PaletteHost` mounts once. _(FR-6.16)_
- `⌘,` calls the settings opener; run-scoped chord is a no-op when active destination ≠ `run`. _(FR-6.13)_

**Regression guard — web unregressed** (`apps/frontend/src/features/palette/__tests__/PaletteHost.test.tsx`)

- Web `PaletteHost` still mounts the (shell) `CommandPalette` with only `open`/`onRequestClose`/`searchPort`/`starterActions`; the new optional props are absent → behavior identical. Run `npm run test --workspace @0x-copilot/frontend` + `npm run typecheck --workspace @0x-copilot/frontend`: both green. _(FR-6.1, §2 non-goal.)_
- Deleting `palette/*` + dropping the `RouteJumpPalette` alias breaks no web import: `apps/frontend/src/features/palette/PaletteHost.tsx` imports `CommandPalette` from `@0x-copilot/chat-surface` (the shell one) and never `RouteJumpPalette`/`./palette` — verified by grep; web vitest + typecheck stay green. _(FR-6.17)_

**E2E / live desktop smoke** (`apps/desktop/SMOKE.md`, run live per FR-6.21)

1. `make dev` stack up; launch desktop per `SMOKE.md` (`COPILOT_AUTH_MODE=dev-mint`).
2. Boot → sign-in gate → shell renders the 6-dest rail (no placeholder).
3. Press `⌘K` → palette opens; "Go to Tools" navigates; `Esc` closes.
4. Start a run (`⌘N` / "New chat") → send a prompt → tokens stream in Run.
5. Approve an on-surface pending diff (inline Approve) → bead flips to signed/jade.
6. Scrub the timeline (`⌘←` rewind / drag) → viewing banner appears, approvals hidden; `⌘L` snaps to live.
7. `⌘,` opens Settings → Appearance renders; `⌘⇧M` opens the local-model picker.
8. Settings → Provider keys → add a BYOK key (validating spinner → default model → Add).
9. Settings → Local models → download a model (progress → "Ready to run locally").
10. DevTools `fetch("https://example.com")` still **fails** (CSP intact, per `README.md`).
    Record pass or file bugs by step number.

**FR → test map:** 6.1→palette dispatch tests; 6.2→empty/no-result/reject tests; 6.3→ARIA/keyboard tests; 6.4/6.5→port+registry tests; 6.6/6.7/6.8→PaletteHost integration; 6.9–6.12→shortcut hook/table tests; 6.13/6.14→bootstrap tests; 6.15→shortcuts table + Shortcuts-page test; 6.16→bootstrap placeholder-absence test; 6.17/6.18→grep+typecheck gate; 6.19→doc reconciliation (lint/review); 6.20/6.21→live smoke; 6.22→doc review.

## 9. UI/UX acceptance checklist

Grounded in `DESIGN-SPEC.md` §0 (tokens/dims), §5 (palette pattern), §6 (shortcuts), §1 (topbar).

- [ ] **Palette card** ≈ 540 px wide (`DESIGN-SPEC.md` §0 "Command palette 540px"; current component uses `min(640px,92vw)` — reconcile to the spec token/width), radius `--r-lg 12`, border hairline `--line2`, surface `--panel`/`--panel2`, offset from top ~12vh scrim `rgba(0,0,0,.45)`.
- [ ] **Topbar trigger** 250 px, height 28 px, right-aligned, `Search… ⌘K`, hairline `--line`, suppressed on **Run** and **Settings** (§1).
- [ ] **Rows** use tokens (not the current hard-coded `#1a1a1a`/`#d97757` fallbacks): title `--tx` 13px, subtitle/kind-chip `--mut`/`--mut2`, selected row `--panel2`/`--panel3` bg + `--line3` border. Single-accent discipline: no accent on rows except the focus ring; the "Connect a tool →" hint (`CommandPalette.tsx:473`) reads `var(--color-accent, #d97757)`, which resolves to `--sky` under v2 tokens — replace its stale `#d97757` **fallback literal** (an ember-orange from the old brand) with a v2-consistent fallback so even a token-less render never flashes the wrong accent. Same treatment for the `#1a1a1a` surface fallback at `CommandPalette.tsx:414` and the `PaletteHitRow.tsx` `#2a2a2a`/`#3a3a3a` fallbacks.
- [ ] **Group headers** mono uppercase, letter-spacing per `.sect-h`, muted `--mut2`.
- [ ] **States:** default / hover (row bg raise) / active-selected (`--panel2`+border) / focus-visible (`2px solid var(--accent)` offset 2) / loading (debounced; no spinner needed for local port) / **empty query** (starter list) / **no results** ("No results.") / **error** (port reject → "No results."). Streaming: N/A (palette is not a stream surface).
- [ ] **a11y:** `role="dialog"`+`aria-modal`; input `role="combobox"` + `aria-controls`/`aria-activedescendant`/`aria-autocomplete=list`; `role="listbox"`; rows `role="option"` + `aria-selected`; `↑↓` wrap, `Enter` activate, `Esc`/scrim close; focus trapped to the input while open; focus returns to opener on close.
- [ ] **Shortcuts a11y/behavior:** chords match `DESIGN-SPEC.md` §6 exactly; input guard prevents data loss (FR-6.11); the Shortcuts settings page lists the identical set (FR-6.15).
- [ ] **`prefers-reduced-motion` / `[data-reduce-motion=1]`:** palette open/close and any row transition are zeroed.
- [ ] **Theming:** verified in **light** and **dark**; palette + trigger read `--panel*`/`--line*`/`--tx*` correctly in both.
- [ ] **Density:** `[data-density=compact|spacious]` adjusts row padding via `--pad`/`--gap`; palette remains usable at both.
- [ ] **Contrast:** row title `--tx` on `--panel` ≥ 4.5:1; selected-row text ≥ 4.5:1; chip/subtitle `--mut` ≥ 3:1 (large/secondary).
- [ ] **Component reuse:** `CommandPalette` + `PaletteHitRow` + `CommandPaletteTrigger` restyled to tokens (no new palette component); trigger reused verbatim from `shell/CommandPaletteTrigger.tsx`.
- [ ] **Icons:** row/nav icons use the `DESIGN-SPEC.md` §7 stroke set (`run/chats/folder/activity/plug/skill/gear/key/chip/sliders/sun/cmd`), monochrome `--tx2` (no decorative color). _(Note: `packages/chat-surface/src/icons/` currently ships only Copy/Retry/Thinking — the full §7 set is a P2 dependency; if absent, the palette falls back to `PaletteHitRow`'s glyph stub and this box is deferred to the icon-set PR.)_

## 10. Dependencies & sequencing

**Upstream (blocked by):**

- **P2 (Shell & IA):** the 6-dest `destinations.ts`, the destination outlet (P2E), and `onOpenSettings` wiring. PR-6.7 (mount outlet) hard-depends on P2E; PR-6.4 depends on the 6-dest slugs.
- **P3 (Run cockpit):** the run handlers for `⌘M/←/→/L/./↵/⌫` (PR-6.6 delegates to them).
- **P5 (Settings):** the settings shell + section slugs (`appearance`, `model-and-behavior`, `provider-keys`, `local-models`, `connectors`) and the flow modals (Add key / Download model / Connect tool) that palette actions launch; the Shortcuts page (PR-6.8).
- **P0 (tokens):** v2 tokens for the UI/UX checklist.

**Intra-phase DAG:** PR-6.1 → PR-6.2 → {PR-6.3, PR-6.5} → PR-6.4 (needs 6.2+6.3) → PR-6.6 (needs 6.4+6.5) → PR-6.7 (needs 6.6 + P2E) → PR-6.8 (needs 6.5) → PR-6.9 (needs all).

**Downstream (blocks):** nothing — Phase 6 is the terminal phase (`PLAN.md` §9: "6" is last). Its DoD closes the redesign. Audit-after-2-phases cadence (MEMORY) applies: P5+P6 form the final audit pair.

**Ground-truth caveat:** at authoring time this worktree still carries the _pre-redesign_ state (12-dest `destinations.ts`, `DesktopPlaceholder` mounted, two palettes, no settings shell). If P2–P5 have not merged into the branch when Phase 6 starts, PR-6.4/6.6/6.7/6.8 block on their respective upstream PRs. The PR order above is the merge order **given** P2–P5 are green.

## 11. Risks & mitigations

| Risk                                                                                                     | Severity                       | Mitigation                                                                                                                                                                                      | Rollback / flag                                                                                      |
| -------------------------------------------------------------------------------------------------------- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Extending `CommandPalette` regresses the web palette                                                     | High                           | New props are **optional**, default = today's close-only behavior; web `PaletteHost` passes none; regression tests in `apps/frontend` gate the merge (§8).                                      | Revert PR-6.2 in isolation (self-contained).                                                         |
| Global shortcut listener steals keystrokes / clobbers composer text                                      | High                           | Strict input guard (FR-6.11); exact-modifier matching (FR-6.12); unit tests for textarea focus; `⌘K`/`⌘,` are the only input-safe chords.                                                       | `useShellShortcuts` accepts `enabled` (default true) — set false to disable all chords.              |
| Two `⌘K` listeners → double-toggle                                                                       | Med                            | FR-6.14 single-sources `⌘K` in `useShellShortcuts`; remove the interim `useCommandPaletteHotkey` mount in PR-6.6.                                                                               | If `useShellShortcuts` slips, keep `useCommandPaletteHotkey` and defer `⌘K` from the shortcut table. |
| Palette navigation table drifts from the shell's slug SSOT                                               | Med                            | Registry navigation hits resolve through the shell's `onNavigate(slug)` (no second routing table); typecheck binds slugs to `ShellDestinationSlug`.                                             | N/A (compile-time bound).                                                                            |
| Icons for palette rows not yet in `chat-surface/icons` (only Copy/Retry/Thinking today)                  | Low                            | `PaletteHitRow` already ships a glyph fallback; the §7 icon set is a P2 dependency — defer the icon box in §9 if absent.                                                                        | Ship with glyph stub; swap when icons land.                                                          |
| Live smoke reveals a real-run break unit fakes hid                                                       | High (this is the whole point) | FR-6.21 mandates a live run against `make dev`; file bugs by step number before ticking DoD; do not rely on vitest fakes for the run/approve/scrub path.                                        | Block PR-6.9 (DoD) until the live walk is clean.                                                     |
| Deleting `palette/*` orphans an unseen importer                                                          | Low                            | PR-6.1 greps `/palette/CommandPalette` **and** `RouteJumpPalette` for importers first (verified: zero non-test importers today); typecheck across `chat-surface`+`frontend`+`desktop` gates it. | Revert PR-6.1 (isolated delete).                                                                     |
| Settings deep-link section mismatch (spec label vs slug, e.g. "Model & behavior" → `model-and-behavior`) | Low                            | Map labels→slugs once in `palette-commands.ts`; assert against `SETTINGS_SECTIONS` union (P5).                                                                                                  | Fix the map; no schema change.                                                                       |

## 12. Definition of done

- [ ] **FRs met:** FR-6.1 … FR-6.22 all satisfied and each mapped to a passing test (§8).
- [ ] `⌘K` palette works on desktop: opens via hotkey + topbar trigger; lists the 6 destinations, the settings sections, and the 4 actions; navigation/settings/action dispatch all fire (US-6.1–6.5).
- [ ] Full `DESIGN-SPEC.md` §6 shortcut set wired and input-guarded; Shortcuts settings page reads the same SSOT table (US-6.6, US-6.7).
- [ ] `DesktopPlaceholder` deleted and the real destination outlet mounted; `grep DesktopPlaceholder` → zero (US-6.8).
- [ ] Superseded `packages/chat-surface/src/palette/*` deleted; exactly one `CommandPalette` exported (US-6.9).
- [ ] **Tests green:** `npm run test`/`typecheck` for `@0x-copilot/chat-surface`, `@0x-copilot/desktop`, `@0x-copilot/frontend`; web palette behaviorally identical (regression guard).
- [ ] **Live desktop smoke passed** end-to-end (boot → run → approve → scrub → settings → BYOK → local model); CSP `fetch` check still fails; no console errors (US-6.10).
- [ ] **UI/UX checklist** (§9) passed in light + dark, at all densities, reduce-motion honored, single-accent discipline, token-driven (no hard-coded `#1a1a1a`/`#d97757` fallbacks in the palette).
- [ ] **Docs updated:** `apps/desktop/SMOKE.md` (redesign flow), `apps/desktop/README.md` (shipped state), `PLAN.md` §11 DoD items for `⌘K`/live-smoke/`DesktopPlaceholder` ticked (US-6.11).
- [ ] **No dead code left:** no unused palette, no placeholder, no orphaned exports, no stale "12-destination"/"Phase 12" markers.
