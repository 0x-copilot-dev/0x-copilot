# PRD-02 — Status chip recipe: one chip spec, matching the design

> **Reconciled against the normative `README.md`.** Wave 1, **blocked by PRD-01**.
> Applied rulings: **C11** (the 10.5px mono token is minted by PRD-01 as
> `--font-size-mono-10-5`; this PRD consumes it and mints no type token), **C12**
> (sequence `01 → 02 → 08`; PRD-08 D2 owns the `needs_input` **tone** flip and the final
> harness re-run), **DoD-Q3** (item 11), **DoD-Q4** (item 16), and the program-wide
> harness note (frozen HIGH/MEDIUM counts are stale — DoD 13/14 are now anchor-scoped
> commands plus a merge-base delta, not frozen totals). Hot-file order that binds this
> PRD: `styles.css` `01 → 02 → 08 → 11 → 10`; `ActivityDestination.tsx` `02 → 04 → 08`;
> `ProjectsDestination.tsx` `02 → 03 → 07 → 10`; `ChatsArchive.tsx` `02 → 09`.

## Problem

Every status chip in the product — the `running` tag beside a chat title, the `done`
tag on an Activity row, the `active` tag on a Project card — renders as a **loud,
filled, UPPERCASE, sans-serif badge**: a solid jade fill with a full-opacity jade
border reading **"RUNNING"**. The design specifies the opposite: a **quiet, hairline,
monospace, lowercase outline tag** reading `running` — transparent inside, a 25 %-alpha
tone border, 10.5 px mono.

The consequence is that the chip out-shouts the row title it is supposed to annotate.
On a Chats list the eye lands on a column of coloured lozenges instead of the
conversation names. Eight visual properties diverge at once (background, border colour,
font family, font size, font weight, text-transform, padding, gap), plus the label text
itself, so no single tweak fixes it.

One component produces all of them, and it produces them from an inline
`CSSProperties` object rather than the design-system recipe that already exists for
exactly this shape. The prior UI-kit consolidation (#219/#220/#221) shipped `.ui-badge`
whose docblock literally reads _"design `.chip` — mono, bordered, NO fill"_ —
`StatusPill` is the copy that consolidation missed.

## Evidence

Every row below opened and read in this worktree.

| Claim                                                                          | File:line                                                                                                              | What the code actually does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `StatusPill` styles itself with an inline object, not a recipe class           | `packages/chat-surface/src/shell/StatusPill.tsx:66-92`                                                                 | `pillStyle()` returns a `CSSProperties` literal; `className` is only a pass-through (`:103`). No design-system class is applied anywhere in the file.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Filled background                                                              | `packages/chat-surface/src/shell/StatusPill.tsx:75`                                                                    | `backgroundColor: palette.bg` → `--color-success-bg #1a2f23` / `--color-warning-bg #322615` / `--color-surface-muted #16161a` (`packages/design-system/src/styles.css:171,192,195`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Full-opacity tone border                                                       | `packages/chat-surface/src/shell/StatusPill.tsx:77`                                                                    | `border: 1px solid ${palette.border}` where `palette.border` is the **solid** `var(--color-success)` / `var(--color-warning)` (`:42,51`). Muted uses `--color-border` `rgba(255,255,255,.06)` — one step weaker than the design's `--line2` `rgba(255,255,255,.1)`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Sans font                                                                      | `packages/chat-surface/src/shell/StatusPill.tsx:66-84`                                                                 | No `fontFamily` declared at all → inherits the body sans. Confirmed HIGH `mono → sans` on every chip anchor.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 11.2 px / weight 600 / uppercase / 0.3 px tracking                             | `packages/chat-surface/src/shell/StatusPill.tsx:78-81`                                                                 | `fontSize: "var(--font-size-2xs, 11px)"` (`--font-size-2xs: 0.7rem` = 11.2 px, `styles.css:63`), `fontWeight: 600`, `letterSpacing: 0.3`, `textTransform: "uppercase"`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Fixed height + zero vertical padding                                           | `packages/chat-surface/src/shell/StatusPill.tsx:72-73`                                                                 | `height: 20`, `padding: "0 8px"`, `gap: 6`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Labels are Title-Case at source, then uppercased by CSS (drift twice over)     | `packages/chat-surface/src/shell/statusTone.ts:41-55`                                                                  | `running → "Running"`, `done → "Done"`, `paused → "Paused"`, `stopped → "Stopped"`, `archived → "Archived"`. Combined with `textTransform: uppercase` the chip renders `RUNNING`. Design renders `running`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| A **second**, divergent label map exists for Activity                          | `packages/chat-surface/src/destinations/activity/ActivityDestination.tsx:87-100`                                       | `activityStatusLabel()` re-declares the same five labels, and disagrees with the SSOT on one: `needs_input → "Needs input"` here vs `"Needs you"` at `statusTone.ts:49`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| A **third** label map exists for Chats                                         | `packages/chat-surface/src/destinations/chats/ChatsArchive.tsx:79-90`                                                  | `statusLabel()` re-declares `Running/Paused/Done/Archived` even though the file already imports `runStatusTone` (`:38`) and uses its `tone` + `showDot`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| A **fourth** label map exists for Projects                                     | `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx:577-580`                                      | `STATUS_LABEL = { active: "Active", archived: "Archived" }`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Tone semantics already match the design and must be preserved                  | `packages/chat-surface/src/shell/statusTone.ts:40-56`                                                                  | `done → ok` (jade, not grey), `stopped/cancelled/archived → muted` (not red), `paused/waiting_for_approval → warning`. This is correct against `copilot-app.jsx:15-19,257-260`. **Do not touch the tone map.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `.ui-badge` already IS the design `.chip`                                      | `packages/design-system/src/styles.css:555-568`                                                                        | `background: transparent`, `border: 1px solid var(--color-border-strong)`, `font-family: var(--font-mono)`, `border-radius: var(--radius-full)`, `padding: 2px 8px`, tone variants recolour text + border only. Docblock: _"design `.chip` — mono, bordered, NO fill"_.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| …but `.ui-badge` is not yet chip-exact                                         | `packages/design-system/src/styles.css:555-568` (rule), `:563,564,566`, `:570-588` (tones)                             | `font-size: var(--font-size-2xs)` (11.2 px vs 10.5, `:563`), `font-weight: var(--font-weight-semibold)` (600 vs 500, `:564`), `padding: 2px 8px` (`:566`; rows use 1px 8px), no `gap`, no `line-height`, no dot element, **solid** tone border-colors, no `muted`/`off` tone, `--accent` uses `--color-accent-strong` where the design uses `--accent`. **PRD-01 §C lands the `font-size` + `font-weight` half** (its Scope row `src/styles.css:555-568`); everything else in this list is PRD-02's.                                                                                                                                                                                                                                                                            |
| The three tone-line tokens genuinely do not exist yet                          | `grep -n "success-line\|warning-line\|danger-line" packages/design-system/src/styles.css` → 0 hits                     | CONFIRMED against the use-what-exists rule. Only `--color-accent-line` (`:218`) and `--color-accent-soft` (`:216`) exist. So the three 25 %-alpha tone lines are a real gap, not a duplicate of a shipped token. `--font-size-mono-10-5` also does not exist today (only `--font-size-mono-10`, `:71`) — **PRD-01 mints it**, not this PRD (C11).                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `.ui-badge` has exactly one consumer today                                     | `packages/design-system/src/index.tsx:180-193`; `packages/chat-surface/src/settings/ProviderKeysPage.test.tsx:161,179` | `<Badge tone>` renders `ui-badge ui-badge--${tone}`; tones `neutral \| success \| warning \| danger \| accent`. Only ProviderKeysPage uses it. Low blast radius to extend.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| There are **two different components named `StatusPill`**                      | `packages/design-system/src/index.tsx:299-323` and `packages/chat-surface/src/shell/StatusPill.tsx:94`                 | design-system's takes `tone: "running" \| "ready" \| "idle"` and renders `.ui-status-pill` (`styles.css:809-842`, a filled/pulsing pill). chat-surface's takes `status: ok\|error\|warning\|info\|muted`. Same name, different props, different CSS.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **DISPUTED — "eight call sites, and `apps/frontend` is untouched"**            | seven importer files, enumerated                                                                                       | **CODE WINS: there are NINE JSX call sites in SEVEN files, one of them in `apps/frontend`.** Importers of the design-system `StatusPill`: `ToolEditor.tsx:43` (use `:298`), `ToolDetailView.tsx:39` (`:202`), `AgentDetailView.tsx:21` (`:136,141`), `AgentEditor.tsx:45` (`:355`), `ForkDialog.tsx:19` (`:58`), `VersionHistoryTab.tsx:26` (`:136,187`) — **and `apps/frontend/src/features/chat/components/shell/Topbar.tsx:1,171`**, whose test pins the class: `Topbar.test.tsx:66` `classList.contains("ui-status-pill")`. Plus the registry binding `Tier2Loader.tsx:22,86`. The earlier "Not touched: `apps/frontend`" claim is therefore false and is corrected in Scope.                                                                                               |
| Deleting `.ui-status-pill` has three comment/keyframe dependents               | `styles.css:838,844`, `styles.css:1141-1143`, `index.tsx:391`, `apps/frontend/src/features/connectors/adapters.ts:30`  | CONFIRMED. `@keyframes ui-pulse` (`:844`) has exactly one consumer, `.ui-status-pill--running .ui-status-pill__dot` (`:838`) — it dies with the recipe. `.ui-pill`'s docblock says "Seeds off `.ui-status-pill`" (`:1141-1143`) and `Pill`'s says "Generalises `StatusPill`" (`index.tsx:391`); `adapters.ts:30` cites "the design-system `<StatusPill>` API". All four become dangling references and are in scope.                                                                                                                                                                                                                                                                                                                                                            |
| `showDot` defaults to `true` — backwards vs the design                         | `packages/chat-surface/src/shell/StatusPill.tsx:98`                                                                    | Only 3 call sites pass it (`ActivityDestination.tsx:528`, `ChatsArchive.tsx:374,480`). All ~100 other `<StatusPill>` sites get a dot the design never draws (design draws `.dotk` only when `status === "running"`, `copilot-app.jsx:65,272`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| The tone→colour tokens are already byte-identical to the design                | `packages/design-system/src/styles.css:174-195` vs `design-kit/app-v3/copilot.css:11-25`                               | `--color-success #57c785` = `--jade`; `--color-warning #e8b45e` = `--amber`; `--color-text-subtle #64646d` = `--mut2`; `--color-text-muted #98989f` = `--mut`; `--color-border-strong rgba(255,255,255,.1)` = `--line2`; `--color-accent #5fb2ec` = `--sky`. **No new colour tokens are needed.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| The 25 %-alpha border idiom already exists in the kit                          | `packages/design-system/src/styles.css:218`                                                                            | `--color-accent-line: color-mix(in srgb, var(--color-accent) 35%, transparent)` — matches the design's `--accent-line` exactly. The same construction at 25 % gives the `ok`/`warn`/`danger` chip borders.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Measured deltas, Chats (re-derived on this worktree)                           | `tools/design-parity/surfaces/chats/out/report-default.json`, group `Status pills`                                     | **10 HIGH / 14 MEDIUM / 18 LOW / 3 INFO**, out of the report's `15/59/64/10` total. HIGH = `fontFamily` ×4 (`chip.running`, `chip.running.dot`, `chip.paused`, `chip.archived`), `backgroundColor` ×3, `borderColor` ×3. MEDIUM = `fontSize` 10.5→11.2 ×4, `fontWeight` 500→600 ×4, `padding` `1px 8px`→`0px 8px` ×3, `gap` 5→6 ×3. LOW = `lineHeight 15.75px→normal`, `letterSpacing normal→0.3px`, `textTransform none→uppercase`, `height 19.75→20`, width. INFO = `"running"→"Running"`, `"paused"→"Paused"`, `"archived"→"Archived"`. The older "11 HIGH / 12 MED / 17 total" figures predate the harness change to `lib/extract-computed.js` + `lib/compare.mjs` (phantom `borderColor` rows on borderless elements removed) — **do not quote frozen counts**; re-derive. |
| Measured deltas, Activity (re-derived)                                         | `tools/design-parity/surfaces/activity/out/report-default.json`, labels `*chip*`                                       | **12 HIGH / 16 MEDIUM / 20 LOW / 4 INFO** on `row.live.chip`, `row.done.chip`, `chip.paused`, `chip.stopped` (groups `Row/live`, `Row/rest`, `Status` — the chip rows are **not** one group here, so grade by label, not by group), out of the report's `19/52/68/11` total. Same delta set as Chats.                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **DISPUTED — brief line numbers**                                              | `tools/design-parity/design-kit/app-v3/copilot.css:575-605`                                                            | The brief cites `.chip` at "~lines 354-368". In this worktree `.chip` is at **575-586**, `.chip svg` 587-590, `.chip--ok` 591-594, `.chip--sky` 595-598, `.chip--warn` 599-602, `.chip--off` 603-605, `.dotk` 606-612. (`surfaces/chats/out/AUDIT.md:18` cites 575-586 correctly; `surfaces/chats/out/FINDINGS.md:26-31` still cites the stale `:112-118`.)                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **DISPUTED — "the INLINE `padding:1px 8px` override the Activity rows apply"** | `tools/design-parity/design-kit/app-v3/copilot-app.jsx:64-66` **and** `:274`                                           | **Both** Activity rows and Chats rows apply it. In the whole v3 app there is no chip rendered at the base `2px 8px` — every `.chip` instance carries `style={{padding:"1px 8px"}}`. That makes `1px 8px` the real spec, not an exception.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **DISPUTED — design chip weight**                                              | `tools/design-parity/design-kit/app-v3/copilot.css:575-586,1635-1642`                                                  | `.chip` declares **no** `font-weight`; the measured design value of 500 is **inherited** from `.lrow__name { font-weight: 500 }` (`:1637`). The recipe must therefore land on `--font-weight-medium` (500), not on `semibold` and not on "unset".                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **DISPUTED — audit R1 recommends a new `.ui-pill--outline`**                   | `tools/design-parity/surfaces/activity/out/AUDIT.md:258,282-288`                                                       | The Activity audit proposes adding an outline variant to `.ui-pill`. The Chats audit (`chats/out/AUDIT.md:34`) correctly identifies `.ui-badge` as the already-shipped recipe. **The code agrees with Chats** — `styles.css:555-568` exists and is unused-but-correct. Adding `.ui-pill--outline` would create a **third** chip recipe. Rejected; see Architectural decision.                                                                                                                                                                                                                                                                                                                                                                                                   |
| Project cards render a chip the design does not have                           | `tools/design-parity/surfaces/projects/out/report-default-chatsurface.md:108`; `ProjectsDestination.tsx:481-484,551`   | `default.x.card.statuspill` = `extra-in-live`. The design's Projects grid card (`copilot-app.jsx:388-400`) carries no chip. Also `viewer_role` renders through `StatusPill` (`:551`) — a role tag, not a status.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Project **detail** chat rows are missing their chip entirely                   | `tools/design-parity/surfaces/projects/out/report-detail.md:31`                                                        | `detail.chatrow.chip` = `missing-in-live`. Design reuses `ChatRow` (`copilot-app.jsx:363-366`); live's project detail renders no status chip. Structural gap, not a styling gap.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Comparator tolerances (why exact values matter)                                | `tools/design-parity/lib/compare.mjs:89-110`                                                                           | `fontSize` diffs ≥ 0.4 px are flagged (≥ 2 px = HIGH); colours are compared as **exact serialized strings** — any `color`/`backgroundColor`/`borderColor` mismatch is HIGH; `letterSpacing`/`lineHeight` need a parseable px on both sides to be tolerated.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

## Design intent

Literal source: `tools/design-parity/design-kit/app-v3/copilot.css:575-612`.

```css
.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: var(--mono);
  font-size: 10.5px;
  color: var(--mut);
  border: 1px solid var(--line2);
  background: transparent;
  border-radius: 999px;
  padding: 2px 8px;
}
.chip svg {
  width: 10px;
  height: 10px;
}
.chip--ok {
  color: var(--jade);
  border-color: rgba(87, 199, 133, 0.25);
}
.chip--sky {
  color: var(--accent);
  border-color: var(--accent-line);
}
.chip--warn {
  color: var(--amber);
  border-color: rgba(232, 180, 94, 0.25);
}
.chip--off {
  color: var(--mut2);
}
.dotk {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex: none;
}
```

Markup, `copilot-app.jsx:60-68` (Activity) and `:271-277` (Chats/Projects detail):

```jsx
<span className={"chip " + cc} style={{ padding: "1px 8px" }}>
  {isLive && <span className="dotk" />}
  {cl}
</span>
```

with the label vocabulary at `copilot-app.jsx:15-19` / `:257-260` — **lowercase literals**
`running` / `done` / `paused` / `stopped` / `archived`. No `text-transform` anywhere.
`.chip` declares no `font-weight` and no `letter-spacing`, so it inherits 500 / `normal`
from `.lrow__name` (`copilot.css:1635-1642`).

Resolved target values (dark theme), i.e. what `getComputedStyle` must report:

| Property                  | Design value                                               | design-system token                                         |
| ------------------------- | ---------------------------------------------------------- | ----------------------------------------------------------- |
| `font-family`             | JetBrains Mono stack                                       | `var(--font-mono)` (`styles.css:46` — identical stack)      |
| `font-size`               | `10.5px`                                                   | `var(--font-size-mono-10-5)` — **PRD-01 mints it** (C11)    |
| `font-weight`             | `500` (inherited)                                          | `var(--font-weight-medium)` (`styles.css:73`)               |
| `line-height`             | `15.75px` (= 10.5 × 1.5)                                   | `1.5`                                                       |
| `letter-spacing`          | `normal`                                                   | **declare nothing**                                         |
| `text-transform`          | `none`                                                     | **declare nothing**                                         |
| `background`              | `rgba(0, 0, 0, 0)`                                         | `transparent`                                               |
| `padding`                 | `1px 8px`                                                  | literal                                                     |
| `gap`                     | `5px`                                                      | literal                                                     |
| `border-radius`           | `999px`                                                    | `var(--radius-full)` (`styles.css:112`)                     |
| `height` (computed)       | `19.75px`                                                  | consequence of the above — no explicit `height`             |
| base colour               | `#98989f` (`--mut`)                                        | `var(--color-text-muted)`                                   |
| base border               | `rgba(255,255,255,0.1)` (`--line2`)                        | `var(--color-border-strong)`                                |
| `ok` colour/border        | `#57c785` / `rgba(87,199,133,0.25)`                        | `var(--color-success)` / **new** `--color-success-line`     |
| `warn`                    | `#e8b45e` / `rgba(232,180,94,0.25)`                        | `var(--color-warning)` / **new** `--color-warning-line`     |
| `sky`/info                | `#5fb2ec` / `rgba(95,178,236,0.35)`                        | `var(--color-accent)` / `var(--color-accent-line)` (exists) |
| `off`/muted               | `#64646d` (`--mut2`), border stays `--line2`               | `var(--color-text-subtle)`                                  |
| `danger` (no design chip) | `#f0764f` / 25 % alpha                                     | `var(--color-danger)` / **new** `--color-danger-line`       |
| dot                       | `6px`, `currentColor`, `50%`, `flex: none`, only when live | literal                                                     |
| inline svg                | `10px × 10px`                                              | literal                                                     |

## Architectural decision

**The seam is `packages/design-system` `.ui-badge` + `<Badge>`. `StatusPill` stops
carrying style and becomes a thin tone-adapter over it.**

Three moves, in this order:

1. **Make `.ui-badge` chip-exact** (`packages/design-system/src/styles.css:555-568`
   - tones `:570-588`). **PRD-01 lands two of the declarations first** (`font-size` →
     `var(--font-size-mono-10-5)`, `font-weight` → `var(--font-weight-medium)`, PRD-01 §C
     / its Scope row `src/styles.css:555-568`). This PRD adds everything else: `gap: 5px`,
     `line-height: 1.5`, `.ui-badge svg { width:10px; height:10px }`, a `.ui-badge__dot`,
     `padding` → `1px 8px`, a `--muted` tone (`--color-text-subtle`, base border), and the
     four tone borders swapped onto 25 %-alpha `--color-*-line` tokens built with the
     **existing** `color-mix(in srgb, …)` idiom at `styles.css:218`. `.ui-badge--accent`
     moves from `--color-accent-strong` to `--color-accent` to match `.chip--sky`.
     The only new tokens this PRD mints are the three colour lines —
     `--color-success-line`, `--color-warning-line`, `--color-danger-line` beside
     `--color-accent-line` (`:218`); verified absent today (Evidence). **No type token is
     minted here** (C11): `--font-size-mono-10-5` is PRD-01's, declared beside
     `--font-size-mono-10` (`:71`).

2. **Delete the duplicate.** Remove design-system's `StatusPill` + `StatusTone`
   (`index.tsx:299-323`), its `.ui-status-pill*` block (`styles.css:809-842`) and the
   now-orphaned `@keyframes ui-pulse` (`:844`, whose only consumer is `:838`), and
   migrate its **nine** call sites in **seven** files to `<Badge>` — six chat-surface
   files plus `apps/frontend/src/features/chat/components/shell/Topbar.tsx:171` (see the
   DISPUTED Evidence row; the audit's "eight sites, `apps/frontend` untouched" is wrong).
   Retarget the three docblocks that name the deleted symbols (`styles.css:1141-1143`,
   `index.tsx:391`, `apps/frontend/src/features/connectors/adapters.ts:30`) so no
   dangling reference survives. Two components sharing one name with different prop
   shapes is itself the defect; a deprecation alias would preserve it.
   `.ui-pill` (`styles.css:1141-1170`) **stays** — it is a genuinely different object
   (filled selection chip with an `--active` accent fill; the design's own selection
   pills, not `.chip`).

3. **Rewrite `chat-surface/src/shell/StatusPill.tsx` as a `<Badge>` wrapper.** Delete
   `PALETTE`, `pillStyle`, `dotStyle`. Keep the public props (`status`, `label`,
   `className`, `showDot`) and the `data-testid="status-pill"` / `data-status` /
   `aria-label` contract so ~100 call sites and their tests are untouched. Map
   `ok→success`, `error→danger`, `warning→warning`, `info→accent`, `muted→muted`.
   **Flip `showDot`'s default to `false`** — the design draws `.dotk` only for the live
   state, and the only three call sites that care already pass it explicitly.

**Casing is fixed at the source, not by CSS.** `statusTone.ts:41-55` labels become
lowercase literals (`"running"`, `"done"`, `"needs approval"`, …) and `titleCase()`
(`:58-61`) becomes a lowercasing normaliser. The three duplicate label maps
(`ActivityDestination.activityStatusLabel`, `ChatsArchive.statusLabel`,
`ProjectsDestination.STATUS_LABEL`) are **deleted**; Activity and Chats already call
`runStatusTone()` and simply start using its `.label`. Projects' two statuses
(`active`/`archived`) are not run statuses, so its map stays but goes lowercase. Net:
one label SSOT for run/conversation status, and the `needs_input` disagreement
("Needs input" vs "Needs you") resolves to the SSOT's `needs you`.

**This PRD changes labels only, never tones** (C12). `statusTone.ts`'s tone column is
correct against the design (`copilot-app.jsx:15-19,257-260`) and is explicitly frozen
here. The one tone edit the program does make — `needs_input` `info → warning` — belongs
to **PRD-08 D2** and lands after this PRD in the `01 → 02 → 08` sequence. If PRD-08 has
already landed when this is implemented, keep its `warning`; do not revert it.

**Tier-2 surface-spec registry.** `packages/chat-surface/src/surfaces/Tier2Loader.tsx:22,86`
and the worker allowlist `surfaces/tier2Worker.ts:55,84` expose the string
`"StatusPill"` to generated surface specs — a public contract for model-authored JSON.
The key stays; it rebinds to chat-surface's `StatusPill`, and the loader accepts a
legacy `tone` prop (`running|ready → ok`, `idle → muted`) alongside `status`. This shim
lives at the spec boundary (where compat with already-emitted specs is the boundary's
job), not inside the component.

**Alternatives rejected**

- _Edit `pillStyle` in place._ Leaves the chip as a style object inside chat-surface,
  outside the kit, invisible to the design-system SKILL guide — the exact condition
  that let it drift. Also leaves the two-`StatusPill` name collision.
- _Add `.ui-pill--outline` (Activity `AUDIT.md:258,282-288`)._ Creates a third chip
  recipe next to `.ui-badge`, which already exists and already documents itself as the
  design `.chip`. Rejected on the "if a recipe already exists, using it IS the fix" rule.
- _Keep `textTransform: uppercase` and lowercase only the source strings._ Still renders
  `RUNNING`. Keep the source strings and drop only the transform, and every non-status
  caller (`viewer_role`, tag chips, `"3 selected"`) silently changes case. Fixing both
  ends together is the only correct move.
- _Retarget the existing `--font-size-mono-10` (10 px) to 10.5 px._ Decided in **PRD-01**
  (which owns the mono ladder) and recorded here because it is the reason `.ui-badge`
  can point at a distinct rung: the design genuinely has both steps — `.mw-chip` is
  10 px (`copilot.css:233-239`) and `.chip`/`.lrow__time` are 10.5 px (`:575-586`,
  `:1655-1660`) — and `--font-size-mono-10`'s consumers (`onboarding.css:575`,
  atlas-model-pill) are the 10 px family. Two design steps → two tokens.
- _Accept 10 px for the chip and add no token._ `compare.mjs:94` flags any ≥ 0.4 px
  delta; 0.5 px would leave a permanent MEDIUM row on three surfaces.

**No backend, no contract, no migration.** This PRD is entirely presentational: the
status strings already arrive from `ai-backend` and are already projected correctly
(`chats/out/AUDIT.md:117` marks `chats.status.fourStates` PARITY end-to-end). No
`packages/api-types` change, no facade route, no schema. Verified against the migration
high-water marks on disk — `services/backend/migrations` tops out at `0045`
(`0045_provider_api_keys_custom_endpoint.sql`) and `services/ai-backend/migrations` has
only `0001_runtime_baseline.sql` — **this PRD claims no migration id**, so nothing in
the README's C18 reassignment table applies to it.

## Scope

### `packages/design-system`

| File             | Reason                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/styles.css` | Add `--color-{success,warning,danger}-line` (near `:218`, both themes as required) — **no type token; PRD-01 mints `--font-size-mono-10-5` and already repoints `.ui-badge`'s `font-size`/`font-weight`** (C11/C12). Make `.ui-badge` chip-exact and add `.ui-badge--muted` + `.ui-badge__dot` (`:555-568`, tones `:570-588`); delete `.ui-status-pill*` (`:809-842`) and the orphaned `@keyframes ui-pulse` (`:844`); drop the `.ui-pill` docblock's "Seeds off `.ui-status-pill`" reference (`:1141-1143`). |
| `src/index.tsx`  | `Badge` gains `dot?: boolean` and the `muted` tone; delete `StatusPill` + `export type StatusTone` (`:299-323`); fix `Pill`'s "Generalises `StatusPill`" docblock (`:391`).                                                                                                                                                                                                                                                                                                                                   |
| `SKILL.md`       | Record `<Badge>` as the canonical status/metadata chip and that `.ui-status-pill` is gone, so the agent guide stops pointing at a deleted recipe.                                                                                                                                                                                                                                                                                                                                                             |
| `CLAUDE.md`      | Same, for the producer rules.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

### `packages/chat-surface`

| File                                                     | Reason                                                                                                                                                  |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/shell/StatusPill.tsx`                               | Delete `PALETTE`/`pillStyle`/`dotStyle`; render `<Badge>`; tone map; `showDot` default → `false`.                                                       |
| `src/shell/StatusPill.test.tsx`                          | Assert recipe classes + no-fill + default-no-dot instead of the old inline-style behaviour (`:23-27`).                                                  |
| `src/shell/statusTone.ts`                                | Lowercase every label (`:41-55`); `titleCase` → `normaliseLabel` lowercaser (`:58-61`).                                                                 |
| `src/shell/statusTone.test.ts`                           | Update label expectations; add the lowercase-invariant assertion.                                                                                       |
| `src/destinations/activity/ActivityDestination.tsx`      | Delete `activityStatusLabel` (`:87-100`); use `runStatusTone(status).label`. **Order `02 → 04 → 08`.**                                                  |
| `src/destinations/activity/ActivityDestination.test.tsx` | `:152-154` assert the deleted function; retarget to the SSOT labels.                                                                                    |
| `src/destinations/chats/ChatsArchive.tsx`                | Delete local `statusLabel` (`:79-90`); use `presentation.label` (already computed at `:476-482`). **Order `02 → 09`; PRD-09 owns the file thereafter.** |
| `src/destinations/projects/ProjectsDestination.tsx`      | `STATUS_LABEL` → lowercase (`:577-580`). **Order `02 → 03 → 07 → 10`; PRD-10 owns the file thereafter.**                                                |
| `src/destinations/tools/ToolEditor.tsx`                  | Migrate design-system `StatusPill` → `<Badge>` (`:43,298`).                                                                                             |
| `src/destinations/tools/ToolDetailView.tsx`              | Same (`:39,202`); local `statusTone` (`:441`) returns the new Badge tone union.                                                                         |
| `src/destinations/agents/AgentDetailView.tsx`            | Same (`:21,136,141,271`).                                                                                                                               |
| `src/destinations/agents/AgentEditor.tsx`                | Same (`:45,355`).                                                                                                                                       |
| `src/destinations/agents/ForkDialog.tsx`                 | Same (`:19,58`).                                                                                                                                        |
| `src/destinations/agents/VersionHistoryTab.tsx`          | Same (`:26,136,187`).                                                                                                                                   |
| `src/surfaces/Tier2Loader.tsx`                           | `"StatusPill"` registry key rebinds to chat-surface's `StatusPill` + legacy-`tone` prop adapter (`:22,86`).                                             |
| `src/surfaces/tier2Worker.ts`                            | Keep `"StatusPill"` in the allowlist (`:55,84`); no rename.                                                                                             |
| `src/index.ts`                                           | Barrel unchanged for `StatusPill`/`runStatusTone`; drop any re-export of the deleted design-system symbols.                                             |

### `apps/frontend` — **corrected: this app IS touched**

The earlier "not touched" claim did not survive the code check (see the DISPUTED
Evidence row). These three files hold the ninth call site and two dangling references to
symbols this PRD deletes. None of them is claimed by another PRD — PRD-09 owns
`packages/chat-surface/src/shell/Topbar.tsx`, a **different file**.

| File                                                    | Reason                                                                                                                                          |
| ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/features/chat/components/shell/Topbar.tsx:1,171`   | Migrate the design-system `StatusPill` → `<Badge>`; map `running/ready → success`, `idle → muted`. Keep `role="status"` + `aria-live="polite"`. |
| `src/features/chat/components/shell/Topbar.test.tsx:66` | The assertion pins `classList.contains("ui-status-pill")`; retarget to `ui-badge` (the text assertion `"Ready"` is unaffected).                 |
| `src/features/connectors/adapters.ts:30`                | Docblock cites "the design-system `<StatusPill>` API"; retarget to `<Badge>`. Comment only — `statusTone()`'s own union is unrelated and stays. |

### `tools/design-parity`

| File                                                          | Reason                                                             |
| ------------------------------------------------------------- | ------------------------------------------------------------------ |
| `surfaces/{chats,activity}/out/report-default.md` (+ `.json`) | Regenerated post-change; they are the graded artefact for the DoD. |

### Not touched

`apps/desktop`, both host binders, any service. `StatusPill`'s prop signature and DOM
contract are preserved, so no binding changes — which is the point of fixing the seam
rather than the call sites. (Desktop consumes the chip only through
`@0x-copilot/chat-surface`; `grep -rn "StatusPill" apps/desktop` → 0 hits.)

## Non-goals

- **The mono ladder itself.** `--font-size-mono-8-5` / `-9-5` / `-10-5` are minted by
  **PRD-01** (C11), which also repoints `.ui-badge`'s `font-size` + `font-weight`. This
  PRD consumes `--font-size-mono-10-5`; it neither declares nor renames a type token.
- **Font-size ladder anywhere else.** `.sect-h` 9.5 px vs `--font-size-3xs` 9 px belongs
  to **PRD-01** (which migrates the label element — not the wrapper — onto `.ui-mono-caps`,
  per C13). `.lrow__time` / `Row.tsx metaStyle` 10.5 px vs 11.2 px belongs to **PRD-08**,
  which owns `_shared/Row.tsx` (C9) and may consume the same token.
- **`Row.tsx` / `SectionHeader.tsx` weights** (`--font-weight-semibold` where the design
  is 400/500). Same root-cause family, different recipe: `Row.tsx` → **PRD-08** (C9),
  `SectionHeader` → **PRD-01** (C13).
- **Chats `modelMonoStyle` (RC-4).** `ChatsArchive.tsx:426-430` forces
  `--color-text-muted` where the design's `.mono` changes family only. Assigned to
  **PRD-09** by README G1 — explicitly **not** "PRD-02 and siblings". This PRD touches
  only `statusLabel` in that file.
- **Projects card composition.** Deleting the design-absent `card.statuspill`
  (`ProjectsDestination.tsx:481-484`) and the `viewer_role` chip (`:551`), and adding the
  missing project-detail `ChatRow` chip (`projects/out/report-detail.md:31`), are
  structural decisions owned by **PRD-10** (which owns `ProjectsDestination.tsx` and
  `ProjectDetailView.tsx`'s markup, C16). Here those chips simply get the correct recipe.
- **`.ui-pill`** (`styles.css:1141-1170`) and `<Pill>`. Different object, stays.
- **Dot animation.** The design's chip dot is static (`copilot.css:606-612`, no
  keyframes); `.ui-status-pill--running`'s `ui-pulse` dies with that recipe (its only
  consumer). Reviving a pulse anywhere is out of scope.
- **Status tones.** `statusTone.ts`'s tone column is correct and frozen here; the
  `needs_input` `info → warning` flip is **PRD-08 D2** (C12).
- **Live-status freshness.** `chats/out/AUDIT.md:120` records that neither host polls or
  subscribes on Chats, so a `running` chip goes stale. Owned by **PRD-09**, which owns
  the conversations SSE stream.
- **Light theme audit.** Tokens are set for both themes; the parity harness measures dark
  only, and this PRD is graded on dark.

## Risks & rollback

| Risk                                                                                                                           | Guard                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| ~100 `<StatusPill>` call sites lose their dot when the default flips.                                                          | Intended. `statusTone.test.ts:35-44` already pins `showDot: false` for non-live states; the three explicit sites are unchanged. A new `StatusPill.test.tsx` case asserts default = no dot.                                                                                                                                                             |
| Snapshot/text assertions break on lowercase labels.                                                                            | `ActivityDestination.test.tsx:152-154` is the known one and is in scope. Sweep: `npx vitest run --root packages/chat-surface` — any residual `"Running"`/`"Done"` expectation is a real drift to fix, not to re-pin.                                                                                                                                   |
| `color-mix` serializes as `color(srgb …)` and the comparator's exact-string colour check (`compare.mjs:108`) still flags HIGH. | Mandate `in srgb` (the form already proven by `--color-accent-line`, `styles.css:218`). If a computed value still fails to serialize as `rgba(…)`, fall back to literal `rgba(87,199,133,0.25)` etc. in the three new tokens — the values are fixed by the design either way.                                                                          |
| Deleting design-system `StatusPill` breaks generated tier-2 surface specs that pass `tone="running"`.                          | The Tier2Loader adapter keeps the `"StatusPill"` key and accepts legacy `tone`. `surfaces/` tests plus `tier2Worker` allowlist tests cover the registry.                                                                                                                                                                                               |
| `.ui-badge` padding `2px 8px → 1px 8px` changes ProviderKeysPage's existing chip.                                              | Correct direction (the design has no `2px` chip). `ProviderKeysPage.test.tsx:161,179` asserts classes, not padding, so it stays green.                                                                                                                                                                                                                 |
| Desktop and web diverge.                                                                                                       | Structurally impossible for the chat-surface chip: both hosts mount the same `StatusPill` from `@0x-copilot/chat-surface` and both load `design-system/src/styles.css`. The one host-side edit is web-only by construction — `apps/frontend`'s legacy ChatScreen topbar, which desktop does not mount (`grep -rn "StatusPill" apps/desktop` → 0 hits). |
| `apps/frontend`'s `Topbar.test.tsx:66` asserts the deleted class and goes red.                                                 | In scope and rewritten in the same commit as the deletion (DoD 17). `npm run typecheck --workspace @0x-copilot/frontend` also fails loudly at the import if the migration is missed, because the export is gone rather than deprecated.                                                                                                                |
| PRD-01 has not landed and `--font-size-mono-10-5` does not resolve.                                                            | Hard-ordered: the README puts this PRD in Wave 1 behind PRD-01, and DoD 1 greps for PRD-01's declaration before `.ui-badge` may reference it. An unresolved var would surface as a `fontSize` MEDIUM row in DoD 13's anchor check, not silently.                                                                                                       |

**Rollback.** Three self-contained commits — (1) design-system tone-line tokens +
`.ui-badge`, (2) `StatusPill` rewrite + label lowercasing, (3) design-system `StatusPill`
deletion + the nine call-site migrations (incl. `apps/frontend` Topbar).
Reverting (3) alone restores the old tools/agents/topbar pills;
reverting (2) restores the filled chip without touching the kit; reverting (1) is safe
only after (2). No data, no persisted state, no feature flag.

## Definition of Done

Every item is one command with a stated expected output, or a named assertion in a named
file. Run from the repo root.

1. **Tokens.** `grep -c "^  --color-success-line:\|^  --color-warning-line:\|^  --color-danger-line:" packages/design-system/src/styles.css`
   prints `3`, and each is
   `color-mix(in srgb, var(--color-{success,warning,danger}) 25%, transparent)` declared
   in the bare-`:root` alias block adjacent to `--color-accent-line` (`:218`):
   `awk '/^:root \{/,/^\}/' packages/design-system/src/styles.css | grep -c -- "-line:"`
   prints `5` (today it prints `2` — `--color-accent-line` and the unrelated
   `--color-line` alias). **No type token is added here:**
   `grep -c "font-size-mono-105" packages/design-system/src/styles.css` prints `0`, and
   `grep -c -- "--font-size-mono-10-5:" packages/design-system/src/styles.css` prints `1`
   (PRD-01's declaration, which `.ui-badge` then references).
2. **`.ui-badge` geometry pin.** After the harness re-run (item 13), the live and design
   extracts agree on every shape property for the chip anchor:

   ```
   node -e "const d=require('./tools/design-parity/surfaces/chats/out/design-default.json'),\
   l=require('./tools/design-parity/surfaces/chats/out/live-default.json'),\
   P=['fontFamily','fontSize','fontWeight','lineHeight','letterSpacing','textTransform',\
   'backgroundColor','padding','gap','borderRadius','height'];\
   console.log(P.filter(p=>d['chip.running'].styles[p]!==l['chip.running'].styles[p]))"
   ```

   prints `[]`. The design side of that comparison is, literally (verified in
   `design-default.json`, sourced from `copilot.css:575-586` + the inline
   `padding:1px 8px` at `copilot-app.jsx:274`): `fontFamily "JetBrains Mono", ui-monospace,
SFMono-Regular, monospace`, `fontSize 10.5px`, `fontWeight 500`, `lineHeight 15.75px`,
   `letterSpacing normal`, `textTransform none`, `backgroundColor rgba(0, 0, 0, 0)`,
   `padding 1px 8px`, `gap 5px`, `borderRadius 999px`, `height 19.75px`.
   **(design-value pin)**

3. **Tone pin.** The same command with `P=['color','borderColor']` over
   `['chip.running','chip.paused','chip.archived']` prints `[]` — i.e. live matches
   design at `rgb(87, 199, 133)` / `rgba(87, 199, 133, 0.25)` (`.chip--ok`,
   `copilot.css:591-594`), `rgb(232, 180, 94)` / `rgba(232, 180, 94, 0.25)`
   (`.chip--warn`, `:599-602`), and `rgb(100, 100, 109)` (`--mut2`) with the base border
   `rgba(255, 255, 255, 0.1)` (`--line2`) for the muted tone — the archived chip renders
   `class="chip chip--off"` in `design-default.json` (`.chip--off`, `:603-605`).
   Additionally `grep -c "^\.ui-badge--muted" packages/design-system/src/styles.css`
   prints `1`. **(design-value pin)**
4. `grep -rn "StatusPill\|StatusTone\|ui-status-pill\|ui-pulse" packages/design-system/src`
   returns nothing — the component, its type, its CSS block (`styles.css:809-842`), the
   orphaned keyframes (`:844`) and both docblock references (`styles.css:1141-1143`,
   `index.tsx:391`) are all gone.
5. `grep -rn "ui-status-pill" packages apps --include='*.ts' --include='*.tsx' --include='*.css' | grep -v node_modules | grep -v "aui-status-pill"`
   returns nothing — the class is gone from the kit, from `Topbar.test.tsx:66`, and from
   the `.ui-pill` docblock. (`apps/frontend/src/styles.css:1883,4639` `.aui-status-pill`
   is a different legacy web class and deliberately stays.)
6. `grep -c "CSSProperties\|backgroundColor" packages/chat-surface/src/shell/StatusPill.tsx`
   prints `0`, and `StatusPill.test.tsx` asserts that
   `render(<StatusPill status="ok" label="running" />)`'s
   `[data-testid="status-pill"]` element has `className === "ui-badge ui-badge--success"`.
7. `packages/chat-surface/src/shell/StatusPill.test.tsx` asserts that
   `render(<StatusPill status="ok" label="running" />)` yields
   `container.querySelectorAll(".ui-badge__dot").length === 0` (dot off by default) and
   that `showDot` yields exactly `1`. **(regression guard — fails on `main`, where
   `StatusPill.tsx:98` defaults `showDot` to `true`)**
8. `packages/chat-surface/src/shell/statusTone.test.ts` asserts
   `statusTone(s).label === statusTone(s).label.toLowerCase()` for every key in
   `STATUS_MAP` plus an unknown status, and pins
   `statusTone("running").label === "running"`, `statusTone("done").label === "done"`,
   `statusTone("needs_input").label === "needs you"`. **(regression guard — fails on
   `main`, where `statusTone.ts:41-55` returns `"Running"`)**
9. `grep -rn '"Running"\|"Done"\|"Paused"\|"Stopped"\|"Archived"' packages/chat-surface/src/shell/statusTone.ts packages/chat-surface/src/destinations/activity/ActivityDestination.tsx packages/chat-surface/src/destinations/chats/ChatsArchive.tsx packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx`
   returns nothing, and `grep -rn "activityStatusLabel" packages/chat-surface/src`
   returns nothing.
10. `npx vitest run --root packages/chat-surface` exits 0.
11. `npm run typecheck --workspace @0x-copilot/design-system` exits 0. (**DoD-Q3**: the
    disjunction is dropped — `packages/design-system/package.json` declares exactly one
    script, `typecheck`, and no test root, verified on disk. Rendering of the recipe is
    covered by item 10 and by the harness in item 13.)
12. `npm run typecheck --workspace @0x-copilot/chat-surface`,
    `npm run typecheck --workspace @0x-copilot/frontend` and
    `npm run build --workspace @0x-copilot/frontend` each exit 0 — proving the deleted
    design-system exports have no remaining consumer, including the ninth call site at
    `apps/frontend/src/features/chat/components/shell/Topbar.tsx:171`.
13. **Chats parity, anchor-scoped.** Re-run the harness (steps in
    `tools/design-parity/SKILL.md`:
    `node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs`, then
    `lib/extract-playwright.mjs` for both sides, then `lib/compare.mjs`), then:

    ```
    node -e "const j=require('./tools/design-parity/surfaces/chats/out/report-default.json');\
    const A=['chip.running','chip.running.dot','chip.paused','chip.archived'];\
    console.log(j.findings.filter(f=>A.includes(f.label)&&(f.severity==='high'||f.severity==='medium')).length)"
    ```

    prints `0`. On the merge base the same command prints `24` (10 HIGH + 14 MEDIUM,
    re-derived on this worktree) — **do not treat that number as frozen**; regenerate it
    on this PR's own merge base, since the harness's property set and severity rules have
    moved once already. The whole-report totals are **not** part of this gate: the final
    regeneration of the committed Chats/Activity artefacts happens after PRD-08 (C12).

14. **Activity parity, anchor-scoped.** The same command against
    `surfaces/activity/out/report-default.json` with
    `A=['row.live.chip','row.done.chip','chip.paused','chip.stopped']` prints `0`
    (merge-base value re-derived here: `28` = 12 HIGH + 16 MEDIUM). Grade by **label**,
    not by group — these anchors span the `Row/live`, `Row/rest` and `Status` groups.
15. **No casing or tracking divergence left.**

    ```
    node -e "const j=require('./tools/design-parity/surfaces/chats/out/report-default.json');\
    const A=['chip.running','chip.running.dot','chip.paused','chip.archived'];\
    console.log(j.findings.filter(f=>A.includes(f.label)&&['text','textTransform','letterSpacing'].includes(f.prop||'text')).length)"
    ```

    prints `0` — no `text` INFO row (`"running" → "Running"` and its two siblings are
    gone), and no `textTransform` / `letterSpacing` row on any chip anchor.

16. **FTUE gate unmoved (DoD-Q4).** Regenerate `tools/design-parity/surfaces/first-run/out/`
    and run
    `git diff --exit-code -- tools/design-parity/surfaces/first-run/out/report.md` —
    it shows no line added under the `## HIGH` heading (exit 0, or a diff containing only
    non-HIGH-section lines). Proves the `.ui-badge` padding/tone change did not regress
    the FTUE gate.
17. `apps/frontend/src/features/chat/components/shell/Topbar.test.tsx` asserts the
    `role="status"` element's `classList.contains("ui-badge")` (replacing the
    `"ui-status-pill"` assertion at `:66`) and still reads `"Ready"`;
    `npx vitest run --root apps/frontend src/features/chat/components/shell/Topbar.test.tsx`
    exits 0.

## Dependencies

**Blocked by: PRD-01** (Wave 0 → Wave 1). Corrected from the earlier "blocked by
nothing": C11 gives PRD-01 the `--font-size-mono-10-5` token and C12 fixes the sequence
`01 → 02 → 08` on `styles.css`. `.ui-badge` cannot be made chip-exact until PRD-01's
`font-size`/`font-weight` repoint and its mono rung exist.

**Runs in parallel with PRD-03** (README Batch 1) — disjoint file sets: this PRD touches
`styles.css` + `StatusPill`/`statusTone` + destination call sites; PRD-03 touches
`contract/`, `shell/ChatShell`, `projections/chats` and both host binders.

**Unblocks (and must land before, per the hot-file order):**

- **PRD-08** (Activity) — `ActivityDestination.tsx` order `02 → 04 → 08`; `styles.css`
  order `01 → 02 → 08`. The chip anchors clear here (12 HIGH / 16 MEDIUM on the merge
  base, re-derive), so PRD-08 is left with its genuinely structural gaps (row tile
  background, icon sizing, row padding, section rhythm). PRD-08 D2's `needs_input` tone
  flip stacks on this PRD's label change.
- **PRD-09** (Chats) — `ChatsArchive.tsx` order `02 → 09`; PRD-09 owns the file
  afterwards. The chip anchors clear here (10 HIGH / 14 MEDIUM on the merge base,
  re-derive). PRD-09 separately owns RC-4 `modelMonoStyle` (README G1) — not this PRD.
- **PRD-10** (Projects) — `ProjectsDestination.tsx` order `02 → 03 → 07 → 10`. Its card
  chips and the missing project-detail `ChatRow` chip land on the corrected recipe
  rather than on the filled badge; PRD-10 owns whether those chips exist at all (C16).
- **PRD-11** (Tools) — `styles.css` order `… → 08 → 11`; its connector-row chip
  decisions land on the corrected recipe.

**Coordinate with (no ordering constraint):** the generative-UI surface-spec work, which
owns `Tier2Loader`'s component registry; the `"StatusPill"` key rebinding in scope item
`src/surfaces/Tier2Loader.tsx` touches that contract.
