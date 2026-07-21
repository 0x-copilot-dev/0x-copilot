# PRD-D — Command palette (⌘K)

**Status:** Draft · **Surface:** ⌘K (web + desktop) · **Package:**
`@0x-copilot/chat-surface` · **Blocked by:** A, B

## 1. Context & problem

The ⌘K palette is **fully backend-backed** — `PaletteSearchPort` → `/v1/palette/
search` → a real scored index in `backend_app/palette/*`. That's a strength, and
it stays. The problem is the opposite of the other surfaces: the palette
**over-delivers on search and under-delivers as a launcher**.

- **P1 — No static command layer.** The design opens ⌘K with a flat list of 13
  nav/action commands (Go to Run/Chats/Projects/Activity/Tools/Skills, New chat,
  Add provider key, Download local model, Connect a tool, Model & behavior,
  Appearance, Open Settings). The current palette on empty query shows only 4
  host-hardcoded starter actions (`PaletteHost.tsx:61-94`) and otherwise waits for
  typing (`CommandPalette.tsx:406-414`), so it can't be used as a keyboard command
  launcher.
- **P2 — Row/structure divergence.** Design is a flat list with a 14×14 SVG icon +
  label + right-aligned mono keyword. Current groups results into 4 uppercase
  kind-buckets, uses text glyphs, a bordered kind-chip ("Go to"/"Open"), and an
  extra subtitle line (`PaletteHitRow.tsx`).
- **P3 — Chrome deltas.** Selected-row colour bug (`#2a2a2a`, fixed in PRD-B),
  scrim `rgba(0,0,0,.45)` no blur (design `rgba(4,4,6,.6)` blur 2px), panel
  640→540px, no input search icon, radius, empty copy, trigger label missing the
  magnifier.

## 2. Goals / Non-goals

**Goals**

- G1 — A static **command layer** (the 13 design commands) that is the empty-query
  default and is always present (as `command`-kind hits) above live search
  results.
- G2 — Flat design row: PRD-A SVG icon + label + right-aligned mono keyword; drop
  kind-groups, kind-chips, and the subtitle line for command rows.
- G3 — Chrome to spec: scrim, panel width/radius, list max-height, input search
  icon, empty copy, trigger label.

**Non-goals**

- NG1 — Removing or replacing the backend search index (keep it; it's a feature).
- NG2 — Changing `PaletteSearchPort` transport/contract.

## 3. User stories

| ID     | As a…     | I want…                                                 | so that…                                               |
| ------ | --------- | ------------------------------------------------------- | ------------------------------------------------------ |
| US-D.1 | Solo user | ⌘K then Enter to jump to any destination without typing | the palette is a real command launcher                 |
| US-D.2 | Solo user | commands and my searched work in one list               | I don't context-switch between "search" and "commands" |
| US-D.3 | Solo user | a clean flat list with familiar icons                   | it reads like the design, not a debug view             |

**Acceptance (US-D.1):** _Given_ the palette opens with an empty query, _when_ it
renders, _then_ the 13 commands are listed (icon + label + keyword); _when_ I type
"proj", _then_ "Go to Projects" ranks with any matching project entities below;
_when_ I press Enter, the top hit runs.

## 4. Functional requirements

- **FR-D.1** — Define `SHELL_COMMANDS` (the 13 design commands) in
  `chat-surface/src/shell/` as `{ id, label, keyword, icon: IconName, intent }`;
  `intent` maps to navigation (reuse `shortcuts.ts` intents where they exist, e.g.
  new-run, settings, palette) or a `navigate(slug[,section])` callback the host
  wires.
- **FR-D.2** — On empty query, the palette list = `SHELL_COMMANDS` (replaces the 4
  starter actions). On non-empty query, `SHELL_COMMANDS` are fuzzy-filtered by
  `label+keyword` and merged **above** backend search hits (commands first, then
  entities), preserving the live index.
- **FR-D.3** — Command rows render `<Icon name={icon} size=14 />` + label
  (`--font-size-xs`) + right-aligned mono keyword (`--font-mono`, 9.5px,
  `--color-text-subtle`); no kind-chip, no subtitle for command rows. Entity/
  search rows may keep richer metadata but adopt the same flat row frame + SVG icon
  via PRD-A.
- **FR-D.4** — Chrome: scrim `rgba(4,4,6,.6)` + `backdrop-filter: blur(2px)`,
  `padding-top 13vh`; panel `width 540px`, radius 11 (or nearest token +
  documented), `list max-height 320`; input gets a leading `<Icon name="search"
size=15 />` in `--color-text-subtle`; placeholder "Search commands, settings,
  tools…"; empty state "No matches."
- **FR-D.5** — Topbar trigger renders the magnifier + "Search & commands" + `⌘K`
  (`CommandPaletteTrigger` label/icon; width 250 already correct).
- **FR-D.6** — Keyboard unchanged (↑↓ wrap, Enter runs selected/first, Esc close);
  Enter on empty query runs the first command.

## 5. Architecture & system design

- **SSOT.** Commands: `SHELL_COMMANDS` (one list; the host maps `intent`→action,
  reusing `shortcuts.ts` where a chord already exists — no third command registry).
  Search: `PaletteSearchPort` unchanged. Merge logic lives in `CommandPalette`
  (commands ∪ search hits), so both hosts get it free.
- **Data flow.** `CommandPalette` composes `SHELL_COMMANDS` (static) with
  `PaletteSearchPort.search` (async). Command actions dispatch via a host-provided
  `onCommand(intent)` / existing `navigate` seam. No new port.
- **Reuse vs new.** Reuse `PaletteSearchPort`, `shortcuts.ts` intents, PRD-A
  `Icon`. New: `SHELL_COMMANDS` + merge. Modify `CommandPalette.tsx`,
  `PaletteHitRow.tsx`, `CommandPaletteTrigger.tsx`, `PaletteHost.tsx` (drop the 4
  starter actions in favour of `SHELL_COMMANDS`).

## 6. Affected files

- **Create:** `chat-surface/src/shell/shellCommands.ts` (+ test).
- **Modify:** `CommandPalette.tsx`, `PaletteHitRow.tsx`, `CommandPaletteTrigger.tsx`,
  `Topbar.tsx` (trigger label), `apps/frontend/src/features/palette/PaletteHost.tsx`,
  desktop palette host.

## 7. PR / commit breakdown

- **PR-D.1** — `SHELL_COMMANDS` + empty-query/merge logic + command action wiring. M.
- **PR-D.2** — Row redesign (flat + SVG icon + mono keyword) + chrome (scrim/panel/
  input icon/empty/trigger). Depends A, B, PR-D.1. M.

## 8. Testing plan

- **Unit** (`shellCommands.test.ts`): the 13 commands present with the right
  `icon`/`keyword`; `intent`s resolve to a nav target.
- **Unit** (`CommandPalette.test.tsx`): empty query → 13 command rows; "proj" →
  "Go to Projects" above entity hits; Enter runs top hit; icon is an SVG (PRD-A),
  not a text glyph; selected row bg = `--color-surface-muted`.
- **Integration:** `PaletteSearchPort` mock still surfaces entities beneath
  commands; debounce/generation-guard behaviour intact.
- **Regression:** `CommandPalette.test.tsx` existing keyboard tests green.

## 9. UI/UX acceptance checklist

- [ ] Empty query shows 13 commands; scrim `rgba(4,4,6,.6)`+blur; panel 540px;
      input search icon; placeholder + empty copy per design.
- [ ] Rows: 14×14 SVG icon, label `--font-size-xs`, right mono keyword; no kind
      groups/chips on command rows; selected row `--color-surface-muted`.
- [ ] Trigger: magnifier + "Search & commands" + ⌘K, width 250.
- [ ] a11y: listbox/option roles, ↑↓/Enter/Esc, focus returns to trigger on close;
      reduced-motion respected; light + dark.

## 10. Dependencies & sequencing

Upstream A, B. Downstream: none.

## 11. Risks & mitigations

| Risk                                   | Mitigation                                                                                       |
| -------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Commands + search ranking feels noisy  | Commands are a bounded 13; only show matching commands once a query is typed; entities below     |
| Host action wiring differs web/desktop | `intent`→action mapped per host (like `shortcuts.ts`); `SHELL_COMMANDS` stays substrate-agnostic |

## 12. Definition of done

- [ ] Static command layer works as launcher (empty + merged); flat design rows;
      chrome to spec; trigger labelled.
- [ ] Backend search index untouched and still surfaced.
- [ ] Unit + integration green; web + desktop verified; typecheck + vitest green.
