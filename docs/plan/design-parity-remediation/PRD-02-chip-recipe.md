# PRD-02 — Status chip recipe: one chip spec, matching the design

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

| Claim                                                                          | File:line                                                                                                              | What the code actually does                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `StatusPill` styles itself with an inline object, not a recipe class           | `packages/chat-surface/src/shell/StatusPill.tsx:66-92`                                                                 | `pillStyle()` returns a `CSSProperties` literal; `className` is only a pass-through (`:103`). No design-system class is applied anywhere in the file.                                                                                                                                                                                                                                                                                                         |
| Filled background                                                              | `packages/chat-surface/src/shell/StatusPill.tsx:75`                                                                    | `backgroundColor: palette.bg` → `--color-success-bg #1a2f23` / `--color-warning-bg #322615` / `--color-surface-muted #16161a` (`packages/design-system/src/styles.css:171,192,195`).                                                                                                                                                                                                                                                                          |
| Full-opacity tone border                                                       | `packages/chat-surface/src/shell/StatusPill.tsx:77`                                                                    | `border: 1px solid ${palette.border}` where `palette.border` is the **solid** `var(--color-success)` / `var(--color-warning)` (`:42,51`). Muted uses `--color-border` `rgba(255,255,255,.06)` — one step weaker than the design's `--line2` `rgba(255,255,255,.1)`.                                                                                                                                                                                           |
| Sans font                                                                      | `packages/chat-surface/src/shell/StatusPill.tsx:66-84`                                                                 | No `fontFamily` declared at all → inherits the body sans. Confirmed HIGH `mono → sans` on every chip anchor.                                                                                                                                                                                                                                                                                                                                                  |
| 11.2 px / weight 600 / uppercase / 0.3 px tracking                             | `packages/chat-surface/src/shell/StatusPill.tsx:78-81`                                                                 | `fontSize: "var(--font-size-2xs, 11px)"` (`--font-size-2xs: 0.7rem` = 11.2 px, `styles.css:63`), `fontWeight: 600`, `letterSpacing: 0.3`, `textTransform: "uppercase"`.                                                                                                                                                                                                                                                                                       |
| Fixed height + zero vertical padding                                           | `packages/chat-surface/src/shell/StatusPill.tsx:72-73`                                                                 | `height: 20`, `padding: "0 8px"`, `gap: 6`.                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Labels are Title-Case at source, then uppercased by CSS (drift twice over)     | `packages/chat-surface/src/shell/statusTone.ts:41-55`                                                                  | `running → "Running"`, `done → "Done"`, `paused → "Paused"`, `stopped → "Stopped"`, `archived → "Archived"`. Combined with `textTransform: uppercase` the chip renders `RUNNING`. Design renders `running`.                                                                                                                                                                                                                                                   |
| A **second**, divergent label map exists for Activity                          | `packages/chat-surface/src/destinations/activity/ActivityDestination.tsx:87-100`                                       | `activityStatusLabel()` re-declares the same five labels, and disagrees with the SSOT on one: `needs_input → "Needs input"` here vs `"Needs you"` at `statusTone.ts:49`.                                                                                                                                                                                                                                                                                      |
| A **third** label map exists for Chats                                         | `packages/chat-surface/src/destinations/chats/ChatsArchive.tsx:79-90`                                                  | `statusLabel()` re-declares `Running/Paused/Done/Archived` even though the file already imports `runStatusTone` (`:38`) and uses its `tone` + `showDot`.                                                                                                                                                                                                                                                                                                      |
| A **fourth** label map exists for Projects                                     | `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx:577-580`                                      | `STATUS_LABEL = { active: "Active", archived: "Archived" }`.                                                                                                                                                                                                                                                                                                                                                                                                  |
| Tone semantics already match the design and must be preserved                  | `packages/chat-surface/src/shell/statusTone.ts:40-56`                                                                  | `done → ok` (jade, not grey), `stopped/cancelled/archived → muted` (not red), `paused/waiting_for_approval → warning`. This is correct against `copilot-app.jsx:15-19,257-260`. **Do not touch the tone map.**                                                                                                                                                                                                                                                |
| `.ui-badge` already IS the design `.chip`                                      | `packages/design-system/src/styles.css:554-588`                                                                        | `background: transparent`, `border: 1px solid var(--color-border-strong)`, `font-family: var(--font-mono)`, `border-radius: var(--radius-full)`, `padding: 2px 8px`, tone variants recolour text + border only. Docblock: _"design `.chip` — mono, bordered, NO fill"_.                                                                                                                                                                                       |
| …but `.ui-badge` is not yet chip-exact                                         | `packages/design-system/src/styles.css:562-563,566,570-588`                                                            | `font-size: var(--font-size-2xs)` (11.2 px vs 10.5), `font-weight: var(--font-weight-semibold)` (600 vs 500), `padding: 2px 8px` (rows use 1px 8px), no `gap`, no dot element, **solid** tone border-colors, no `muted`/`off` tone, `--accent` uses `--color-accent-strong` where the design uses `--accent`.                                                                                                                                                 |
| `.ui-badge` has exactly one consumer today                                     | `packages/design-system/src/index.tsx:180-193`; `packages/chat-surface/src/settings/ProviderKeysPage.test.tsx:161,179` | `<Badge tone>` renders `ui-badge ui-badge--${tone}`; tones `neutral \| success \| warning \| danger \| accent`. Only ProviderKeysPage uses it. Low blast radius to extend.                                                                                                                                                                                                                                                                                    |
| There are **two different components named `StatusPill`**                      | `packages/design-system/src/index.tsx:299-323` and `packages/chat-surface/src/shell/StatusPill.tsx:94`                 | design-system's takes `tone: "running" \| "ready" \| "idle"` and renders `.ui-status-pill` (`styles.css:809-843`, a filled/pulsing pill). chat-surface's takes `status: ok\|error\|warning\|info\|muted`. Same name, different props, different CSS. Eight call sites use the design-system one (`ToolEditor.tsx:298`, `ToolDetailView.tsx:202`, `AgentDetailView.tsx:136,141`, `AgentEditor.tsx:355`, `ForkDialog.tsx:58`, `VersionHistoryTab.tsx:136,187`). |
| `showDot` defaults to `true` — backwards vs the design                         | `packages/chat-surface/src/shell/StatusPill.tsx:98`                                                                    | Only 3 call sites pass it (`ActivityDestination.tsx:528`, `ChatsArchive.tsx:374,480`). All ~100 other `<StatusPill>` sites get a dot the design never draws (design draws `.dotk` only when `status === "running"`, `copilot-app.jsx:65,272`).                                                                                                                                                                                                                |
| The tone→colour tokens are already byte-identical to the design                | `packages/design-system/src/styles.css:174-195` vs `design-kit/app-v3/copilot.css:11-25`                               | `--color-success #57c785` = `--jade`; `--color-warning #e8b45e` = `--amber`; `--color-text-subtle #64646d` = `--mut2`; `--color-text-muted #98989f` = `--mut`; `--color-border-strong rgba(255,255,255,.1)` = `--line2`; `--color-accent #5fb2ec` = `--sky`. **No new colour tokens are needed.**                                                                                                                                                             |
| The 25 %-alpha border idiom already exists in the kit                          | `packages/design-system/src/styles.css:218`                                                                            | `--color-accent-line: color-mix(in srgb, var(--color-accent) 35%, transparent)` — matches the design's `--accent-line` exactly. The same construction at 25 % gives the `ok`/`warn`/`danger` chip borders.                                                                                                                                                                                                                                                    |
| Measured deltas, Chats                                                         | `tools/design-parity/surfaces/chats/out/report-default.md:18-30,68-94,131-163`                                         | 11 HIGH (fontFamily ×4, backgroundColor ×3, borderColor ×3 + dot), 12 MED (fontSize +0.7, fontWeight 500→600, padding `1px 8px`→`0px 8px`, gap 5→6), LOW (`letterSpacing normal → 0.3px`, `textTransform none → uppercase`, `height 19.75→20`) and INFO text `"running" → "Running"`.                                                                                                                                                                         |
| Measured deltas, Activity                                                      | `tools/design-parity/surfaces/activity/out/report-default.md:19-33,68-90,132-155`                                      | Identical delta set on `row.live.chip`, `row.done.chip`, `chip.paused`, `chip.stopped`. `AUDIT.md:16` attributes **12 of the surface's 20 HIGH rows** to this one component.                                                                                                                                                                                                                                                                                  |
| **DISPUTED — brief line numbers**                                              | `tools/design-parity/design-kit/app-v3/copilot.css:575-605`                                                            | The brief cites `.chip` at "~lines 354-368". In this worktree `.chip` is at **575-586**, `.chip svg` 587-590, `.chip--ok` 591-594, `.chip--sky` 595-598, `.chip--warn` 599-602, `.chip--off` 603-605, `.dotk` 606-612. (`surfaces/chats/out/AUDIT.md:18` cites 575-586 correctly; `surfaces/chats/out/FINDINGS.md:26-31` still cites the stale `:112-118`.)                                                                                                   |
| **DISPUTED — "the INLINE `padding:1px 8px` override the Activity rows apply"** | `tools/design-parity/design-kit/app-v3/copilot-app.jsx:64-66` **and** `:274`                                           | **Both** Activity rows and Chats rows apply it. In the whole v3 app there is no chip rendered at the base `2px 8px` — every `.chip` instance carries `style={{padding:"1px 8px"}}`. That makes `1px 8px` the real spec, not an exception.                                                                                                                                                                                                                     |
| **DISPUTED — design chip weight**                                              | `tools/design-parity/design-kit/app-v3/copilot.css:575-586,1635-1642`                                                  | `.chip` declares **no** `font-weight`; the measured design value of 500 is **inherited** from `.lrow__name { font-weight: 500 }` (`:1637`). The recipe must therefore land on `--font-weight-medium` (500), not on `semibold` and not on "unset".                                                                                                                                                                                                             |
| **DISPUTED — audit R1 recommends a new `.ui-pill--outline`**                   | `tools/design-parity/surfaces/activity/out/AUDIT.md:258,282-288`                                                       | The Activity audit proposes adding an outline variant to `.ui-pill`. The Chats audit (`chats/out/AUDIT.md:34`) correctly identifies `.ui-badge` as the already-shipped recipe. **The code agrees with Chats** — `styles.css:554-588` exists and is unused-but-correct. Adding `.ui-pill--outline` would create a **third** chip recipe. Rejected; see Architectural decision.                                                                                 |
| Project cards render a chip the design does not have                           | `tools/design-parity/surfaces/projects/out/report-default-chatsurface.md:108`; `ProjectsDestination.tsx:481-484,551`   | `default.x.card.statuspill` = `extra-in-live`. The design's Projects grid card (`copilot-app.jsx:388-400`) carries no chip. Also `viewer_role` renders through `StatusPill` (`:551`) — a role tag, not a status.                                                                                                                                                                                                                                              |
| Project **detail** chat rows are missing their chip entirely                   | `tools/design-parity/surfaces/projects/out/report-detail.md:31`                                                        | `detail.chatrow.chip` = `missing-in-live`. Design reuses `ChatRow` (`copilot-app.jsx:363-366`); live's project detail renders no status chip. Structural gap, not a styling gap.                                                                                                                                                                                                                                                                              |
| Comparator tolerances (why exact values matter)                                | `tools/design-parity/lib/compare.mjs:89-110`                                                                           | `fontSize` diffs ≥ 0.4 px are flagged (≥ 2 px = HIGH); colours are compared as **exact serialized strings** — any `color`/`backgroundColor`/`borderColor` mismatch is HIGH; `letterSpacing`/`lineHeight` need a parseable px on both sides to be tolerated.                                                                                                                                                                                                   |

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
| `font-size`               | `10.5px`                                                   | **new** `--font-size-mono-105: 0.65625rem`                  |
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

1. **Make `.ui-badge` chip-exact** (`packages/design-system/src/styles.css:554-588`).
   Add `gap: 5px`, `line-height: 1.5`, `.ui-badge svg { width:10px; height:10px }`, a
   `.ui-badge__dot`, retarget `font-size` to a new `--font-size-mono-105`, `font-weight`
   to `--font-weight-medium`, `padding` to `1px 8px`, add a `--muted` tone
   (`--color-text-subtle`, base border), and swap the four tone borders onto
   25 %-alpha `--color-*-line` tokens built with the **existing** `color-mix(in srgb, …)`
   idiom at `styles.css:218`. `.ui-badge--accent` moves from `--color-accent-strong` to
   `--color-accent` to match `.chip--sky`.
   New tokens (three colour-line tokens + one type step) go next to their siblings:
   `--color-success-line`, `--color-warning-line`, `--color-danger-line` beside
   `--color-accent-line` (`:218`), and `--font-size-mono-105` beside
   `--font-size-mono-10` (`:71`).

2. **Delete the duplicate.** Remove design-system's `StatusPill` + `StatusTone`
   (`index.tsx:299-323`) and its `.ui-status-pill*` block (`styles.css:809-843`), and
   migrate its eight call sites to `<Badge>`. Two components sharing one name with
   different prop shapes is itself the defect; a deprecation alias would preserve it.
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
- _Retarget the existing `--font-size-mono-10` (10 px) to 10.5 px._ The design genuinely
  has both steps: `.mw-chip` is 10 px (`copilot.css:233-239`) and `.chip`/`.lrow__time`
  are 10.5 px (`:575-586`, `:1655-1660`). `--font-size-mono-10`'s consumers
  (`onboarding.css:575`, atlas-model-pill) are the 10 px family. Two design steps → two
  tokens.
- _Accept 10 px for the chip and add no token._ `compare.mjs:94` flags any ≥ 0.4 px
  delta; 0.5 px would leave a permanent MEDIUM row on three surfaces.

**No backend, no contract, no migration.** This PRD is entirely presentational: the
status strings already arrive from `ai-backend` and are already projected correctly
(`chats/out/AUDIT.md:117` marks `chats.status.fourStates` PARITY end-to-end). No
`packages/api-types` change, no facade route, no schema.

## Scope

### `packages/design-system`

| File             | Reason                                                                                                                                                                                                                                                    |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/styles.css` | Add `--font-size-mono-105` (near `:71`) + `--color-{success,warning,danger}-line` (near `:218`, both themes as required); make `.ui-badge` chip-exact and add `.ui-badge--muted` + `.ui-badge__dot` (`:554-588`); delete `.ui-status-pill*` (`:809-843`). |
| `src/index.tsx`  | `Badge` gains `dot?: boolean` and the `muted` tone; delete `StatusPill` + `export type StatusTone` (`:299-323`).                                                                                                                                          |
| `SKILL.md`       | Record `<Badge>` as the canonical status/metadata chip and that `.ui-status-pill` is gone, so the agent guide stops pointing at a deleted recipe.                                                                                                         |
| `CLAUDE.md`      | Same, for the producer rules.                                                                                                                                                                                                                             |

### `packages/chat-surface`

| File                                                     | Reason                                                                                                      |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `src/shell/StatusPill.tsx`                               | Delete `PALETTE`/`pillStyle`/`dotStyle`; render `<Badge>`; tone map; `showDot` default → `false`.           |
| `src/shell/StatusPill.test.tsx`                          | Assert recipe classes + no-fill + default-no-dot instead of the old inline-style behaviour (`:23-27`).      |
| `src/shell/statusTone.ts`                                | Lowercase every label (`:41-55`); `titleCase` → `normaliseLabel` lowercaser (`:58-61`).                     |
| `src/shell/statusTone.test.ts`                           | Update label expectations; add the lowercase-invariant assertion.                                           |
| `src/destinations/activity/ActivityDestination.tsx`      | Delete `activityStatusLabel` (`:87-100`); use `runStatusTone(status).label`.                                |
| `src/destinations/activity/ActivityDestination.test.tsx` | `:152-154` assert the deleted function; retarget to the SSOT labels.                                        |
| `src/destinations/chats/ChatsArchive.tsx`                | Delete local `statusLabel` (`:79-90`); use `presentation.label` (already computed at `:476-482`).           |
| `src/destinations/projects/ProjectsDestination.tsx`      | `STATUS_LABEL` → lowercase (`:577-580`).                                                                    |
| `src/destinations/tools/ToolEditor.tsx`                  | Migrate design-system `StatusPill` → `<Badge>` (`:43,298`).                                                 |
| `src/destinations/tools/ToolDetailView.tsx`              | Same (`:39,202`); local `statusTone` (`:441`) returns the new Badge tone union.                             |
| `src/destinations/agents/AgentDetailView.tsx`            | Same (`:21,136,141,271`).                                                                                   |
| `src/destinations/agents/AgentEditor.tsx`                | Same (`:45,355`).                                                                                           |
| `src/destinations/agents/ForkDialog.tsx`                 | Same (`:19,58`).                                                                                            |
| `src/destinations/agents/VersionHistoryTab.tsx`          | Same (`:26,136,187`).                                                                                       |
| `src/surfaces/Tier2Loader.tsx`                           | `"StatusPill"` registry key rebinds to chat-surface's `StatusPill` + legacy-`tone` prop adapter (`:22,86`). |
| `src/surfaces/tier2Worker.ts`                            | Keep `"StatusPill"` in the allowlist (`:55,84`); no rename.                                                 |
| `src/index.ts`                                           | Barrel unchanged for `StatusPill`/`runStatusTone`; drop any re-export of the deleted design-system symbols. |

### `tools/design-parity`

| File                                                          | Reason                                                             |
| ------------------------------------------------------------- | ------------------------------------------------------------------ |
| `surfaces/{chats,activity}/out/report-default.md` (+ `.json`) | Regenerated post-change; they are the graded artefact for the DoD. |

### Not touched

`apps/frontend`, `apps/desktop`, any service. `StatusPill`'s prop signature and DOM
contract are preserved, so neither host binder changes — which is the point of fixing
the seam rather than the call sites.

## Non-goals

- **Font-size ladder anywhere else.** `.sect-h` 9.5 px vs `--font-size-3xs` 9 px, and
  `.lrow__time` / `Row.tsx metaStyle` 10.5 px vs 11.2 px, are the same class of defect
  but belong to the row/section PRD. This PRD introduces `--font-size-mono-105` and uses
  it only in `.ui-badge`; the row PRD may consume it.
- **`Row.tsx` / `SectionHeader.tsx` weights** (`--font-weight-semibold` where the design
  is 400/500). Same root cause family, different recipe.
- **Projects card composition.** Deleting the design-absent `card.statuspill`
  (`ProjectsDestination.tsx:481-484`) and the `viewer_role` chip (`:551`), and adding the
  missing project-detail `ChatRow` chip (`projects/out/report-detail.md:31`), are
  structural decisions owned by the Projects PRD. Here those chips simply get the
  correct recipe.
- **`.ui-pill`** (`styles.css:1141-1170`) and `<Pill>`. Different object, stays.
- **Dot animation.** The design's chip dot is static (`copilot.css:606-612`, no
  keyframes); `.ui-status-pill--running`'s `ui-pulse` dies with that recipe. Reviving a
  pulse anywhere is out of scope.
- **Live-status freshness.** `chats/out/AUDIT.md:120` records that neither host polls or
  subscribes on Chats, so a `running` chip goes stale. Data problem, separate PRD.
- **Light theme audit.** Tokens are set for both themes; the parity harness measures dark
  only, and this PRD is graded on dark.

## Risks & rollback

| Risk                                                                                                                           | Guard                                                                                                                                                                                                                                                                         |
| ------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ~100 `<StatusPill>` call sites lose their dot when the default flips.                                                          | Intended. `statusTone.test.ts:35-44` already pins `showDot: false` for non-live states; the three explicit sites are unchanged. A new `StatusPill.test.tsx` case asserts default = no dot.                                                                                    |
| Snapshot/text assertions break on lowercase labels.                                                                            | `ActivityDestination.test.tsx:152-154` is the known one and is in scope. Sweep: `npx vitest run --root packages/chat-surface` — any residual `"Running"`/`"Done"` expectation is a real drift to fix, not to re-pin.                                                          |
| `color-mix` serializes as `color(srgb …)` and the comparator's exact-string colour check (`compare.mjs:108`) still flags HIGH. | Mandate `in srgb` (the form already proven by `--color-accent-line`, `styles.css:218`). If a computed value still fails to serialize as `rgba(…)`, fall back to literal `rgba(87,199,133,0.25)` etc. in the three new tokens — the values are fixed by the design either way. |
| Deleting design-system `StatusPill` breaks generated tier-2 surface specs that pass `tone="running"`.                          | The Tier2Loader adapter keeps the `"StatusPill"` key and accepts legacy `tone`. `surfaces/` tests plus `tier2Worker` allowlist tests cover the registry.                                                                                                                      |
| `.ui-badge` padding `2px 8px → 1px 8px` changes ProviderKeysPage's existing chip.                                              | Correct direction (the design has no `2px` chip). `ProviderKeysPage.test.tsx:161,179` asserts classes, not padding, so it stays green.                                                                                                                                        |
| Desktop and web diverge.                                                                                                       | Structurally impossible here: both hosts mount the same `StatusPill` from `@0x-copilot/chat-surface` and both load `design-system/src/styles.css`. Nothing host-side changes.                                                                                                 |

**Rollback.** Three self-contained commits — (1) design-system tokens + `.ui-badge`,
(2) `StatusPill` rewrite + label lowercasing, (3) design-system `StatusPill` deletion +
eight call-site migrations. Reverting (3) alone restores the old tools/agents pills;
reverting (2) restores the filled chip without touching the kit; reverting (1) is safe
only after (2). No data, no persisted state, no feature flag.

## Definition of Done

1. `packages/design-system/src/styles.css` declares `--font-size-mono-105: 0.65625rem`
   (10.5 px) and `--color-success-line`, `--color-warning-line`, `--color-danger-line`
   as `color-mix(in srgb, var(--color-{success,warning,danger}) 25%, transparent)`,
   adjacent to `--color-accent-line`.
2. `.ui-badge` resolves, in dark theme, to exactly: `font-family` mono,
   `font-size: 10.5px`, `font-weight: 500`, `line-height: 15.75px`,
   `letter-spacing: normal`, `text-transform: none`,
   `background-color: rgba(0, 0, 0, 0)`, `padding: 1px 8px`, `gap: 5px`,
   `border-radius: 999px`, `border-color: rgba(255, 255, 255, 0.1)`, computed
   `height: 19.75px`. **(design-value pin)**
3. `.ui-badge--success` computes `color: rgb(87, 199, 133)` and
   `border-color: rgba(87, 199, 133, 0.25)`; `.ui-badge--warning` computes
   `rgb(232, 180, 94)` / `rgba(232, 180, 94, 0.25)`; `.ui-badge--muted` computes
   `color: rgb(100, 100, 109)` with `border-color: rgba(255, 255, 255, 0.1)`.
   **(design-value pin)**
4. `grep -n "ui-status-pill" packages/design-system/src/styles.css packages/design-system/src/index.tsx`
   returns nothing, and
   `grep -rn "StatusPill" packages/design-system/src` returns nothing.
5. `grep -rn "@0x-copilot/design-system\"" packages/chat-surface/src | xargs grep -l StatusPill`
   returns nothing — no file imports `StatusPill` from design-system.
6. `packages/chat-surface/src/shell/StatusPill.tsx` contains no `CSSProperties` literal
   and no `backgroundColor` (`grep -c "backgroundColor" … == 0`); the rendered element
   carries `class="ui-badge ui-badge--success"` for `status="ok"`.
7. `packages/chat-surface/src/shell/StatusPill.test.tsx` asserts that
   `render(<StatusPill status="ok" label="running" />)` produces **zero**
   `[aria-hidden="true"]` children (dot off by default) and that
   `showDot` renders exactly one `.ui-badge__dot`. **(regression guard for the
   dot-by-default bug)**
8. `packages/chat-surface/src/shell/statusTone.test.ts` asserts
   `statusTone(s).label === statusTone(s).label.toLowerCase()` for every key in
   `STATUS_MAP` plus an unknown status, and pins
   `statusTone("running").label === "running"`, `statusTone("done").label === "done"`,
   `statusTone("needs_input").label === "needs you"`. **(regression guard for the
   double-casing bug)**
9. `grep -rn "\"Running\"\|\"Done\"\|\"Paused\"\|\"Stopped\"\|\"Archived\"" packages/chat-surface/src`
   returns no hits in `statusTone.ts`, `ActivityDestination.tsx`, `ChatsArchive.tsx`,
   `ProjectsDestination.tsx`; `activityStatusLabel` no longer exists
   (`grep -rn "activityStatusLabel" packages/chat-surface/src` → 0 hits).
10. `npx vitest run --root packages/chat-surface` passes.
11. `npx vitest run --root packages/design-system` passes (or, if the package has no
    test root configured, `npm run typecheck --workspace @0x-copilot/design-system`
    passes and the chat-surface suite in item 10 covers it).
12. `npm run typecheck --workspace @0x-copilot/chat-surface` and
    `npm run build --workspace @0x-copilot/frontend` both pass — proving the deleted
    design-system exports have no remaining consumer.
13. Re-running the Chats harness (steps in `tools/design-parity/SKILL.md`;
    `node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs`, then
    `lib/extract-playwright.mjs` for both sides, then `lib/compare.mjs`) yields
    `surfaces/chats/out/report-default.md` with **0 HIGH and 0 MEDIUM rows** in the
    `Status pills` group (anchors `chip.running`, `chip.running.dot`, `chip.paused`,
    `chip.archived`) — down from 11 HIGH / 12 MEDIUM.
14. The same re-run for Activity yields **0 HIGH and 0 MEDIUM rows** for
    `row.live.chip`, `row.done.chip`, `chip.paused`, `chip.stopped` in
    `surfaces/activity/out/report-default.md` — down from 12 HIGH.
15. The Chats report's INFO text rows read `"running" → "running"`, `"paused" → "paused"`,
    `"archived" → "archived"` (i.e. no text divergence), and no `Status pills` row
    reports `textTransform` or `letterSpacing`.
16. `tools/design-parity/surfaces/first-run/out/` regenerates with no **new** HIGH rows
    versus its committed baseline — proving the new type token and the `.ui-badge`
    padding change did not regress the FTUE gate.

## Dependencies

**Blocked by:** nothing. This PRD is self-contained in `packages/design-system` +
`packages/chat-surface` and needs no other PRD to land first.

**Unblocks:**

- The **Chats** and **Activity** surface PRDs — 12 of Activity's 20 HIGH rows and 11 of
  Chats' 17 disappear here, so those PRDs can be scoped to their genuinely structural
  gaps (row tile background, icon sizing, section rhythm) without chip noise.
- The **Projects** PRD — its card chips and the missing project-detail `ChatRow` chip
  land on the corrected recipe rather than on the filled badge.
- The **type-scale** PRD — `--font-size-mono-105` exists after this, so `Row.tsx`
  `metaStyle` and `.sect-h` can be retargeted without inventing a token.

**Coordinate with (no ordering constraint):** the generative-UI surface-spec work, which
owns `Tier2Loader`'s component registry; the `"StatusPill"` key rebinding in scope item
`src/surfaces/Tier2Loader.tsx` touches that contract.
