# Chats — design-parity findings (root-cause grouped)

Surface: **`chats`** · state **`default`** (the only state the design mock ships).
Machine report: [`report-default.md`](./report-default.md) — **🔴 17 HIGH · 🟠 59 MEDIUM · 🟡 64 LOW · ⚪ 10 INFO**.

- Design baseline: `tools/design-parity/design-kit/app-v3/` (`copilot.css`, `copilot-app.jsx`, `copilot-data.jsx`).
- Live side: `packages/chat-surface/src/destinations/chats/ChatsArchive.tsx`, rendered by
  `tools/design-parity/lib/render-live-chats.test.tsx` → `surfaces/chats/live/default.html`.
- Anchors: [`../anchors.json`](../anchors.json) (23 rows; 22/23 matched on design, 21/21 on live).

The 150 raw property diffs collapse into **15 root causes**. Ranked worst first.
Every claim below cites a file:line that was opened.

---

## 🔴 RC-1 — `StatusPill` is a filled, uppercase, sans **badge**; the design chip is a transparent, mono, lowercase **tag**

**Blast radius: 12 of the 17 HIGH + 12 MEDIUM + 12 LOW.** One component drives all four chip
anchors (`chip.running`, `chip.running.dot`, `chip.paused`, `chip.archived`) and, through the
live-only count pills, three more nodes.

Fix site: **`packages/chat-surface/src/shell/StatusPill.tsx:66-84`** (`pillStyle`).

| Property                            | Design rule                                                                                                                                                            | Live rule                                                                                                                                                                                                                                                         | Delta                                  |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| `font-family`                       | `.chip{font-family:var(--mono)}` `copilot.css:112`                                                                                                                     | `pillStyle` sets **none** → inherits the body sans                                                                                                                                                                                                                | mono → sans (**HIGH ×4**)              |
| `background`                        | `.chip{background:transparent}` `copilot.css:112`                                                                                                                      | `backgroundColor: palette.bg` `StatusPill.tsx:73` → `--color-success-bg #1a2f23` / `--color-warning-bg #322615` / `--color-surface-muted #16161a`                                                                                                                 | transparent → **filled** (**HIGH ×3**) |
| `border-color`                      | 25 %-alpha tint: `.chip--ok{border-color:rgba(87,199,133,.25)}` `copilot.css:114`; `.chip--warn` `:116`; `.chip--off` inherits `--line2` `rgba(255,255,255,.1)` `:117` | `border: 1px solid ${palette.border}` `StatusPill.tsx:77` → **full-opacity** `--color-success`/`--color-warning`; muted uses `--color-border rgba(255,255,255,.06)` (`styles.css:174`) instead of `--color-border-strong rgba(255,255,255,.1)` (`styles.css:175`) | 25 % → 100 % (**HIGH ×3**)             |
| `text-transform` / `letter-spacing` | neither                                                                                                                                                                | `textTransform:"uppercase"` `StatusPill.tsx:81`, `letterSpacing:0.3` `:80`                                                                                                                                                                                        | added (LOW ×4 each)                    |
| `font-size` / `font-weight`         | `10.5px` `copilot.css:112`; weight inherited `500` from `.lrow__name` `copilot.css:292`                                                                                | `var(--font-size-2xs)` = 11.2px `StatusPill.tsx:78`, `fontWeight:600` `:79`                                                                                                                                                                                       | +0.7px, +100 (MED ×8)                  |
| `padding` / `gap` / `height`        | inline `padding:1px 8px` on every chat chip (`copilot-app.jsx:118`), `gap:5px` `copilot.css:112`                                                                       | `padding:"0 8px"`, `height:20`, `gap:6` `StatusPill.tsx:70-72`                                                                                                                                                                                                    | MED ×8                                 |

Net visual: the design's chips are _quiet outlines_ that let the row title lead; live renders
three saturated pills per row. This is **not** a Chats-local defect — `StatusPill` is the shared
tone primitive for every destination (`StatusPill.tsx:1-11`), so fixing it re-skins the whole app
and needs a cross-surface check.

---

## 🔴 RC-2 — the 28×28 row icon **tile lost its surface**

Design `.lrow__ic{…;background:var(--panel3);…}` `copilot.css:289`.
Live `iconSlotStyle` at **`packages/chat-surface/src/destinations/_shared/Row.tsx:70-79`** sets
width/height/radius/colour but **no `backgroundColor`** → computed `rgba(0,0,0,0)`.

- **HIGH ×2** — `row.running.ic` + `row.done.ic` `backgroundColor: rgb(29,29,35) → transparent`.
- **MED ×2** — `borderRadius: 7px → 8px`, because `Row.tsx:77` uses `var(--radius-md)` (`0.5rem` = 8px,
  `styles.css:109`) where the design uses a literal `7px`. There is no 7px step on the radius ladder.
- MED ×2 — `display: grid → flex` (design `place-items:center`, live `inline-flex`); cosmetically equal.

Consequence: the glyph floats on the card instead of sitting in a raised tile — which is what makes
a `.lrow` read as a row of _objects_ in the design.

---

## 🔴 RC-3 — the "live run" jade tint is applied one level **below** the tile, so the tile itself stays muted

Design applies the tint to the tile: `style={{color:'var(--jade)'}}` on `.lrow__ic`
(`copilot-app.jsx:116`).
Live applies it to a nested `<span>` **inside** Row's icon slot —
`liveIconStyle` at **`ChatsArchive.tsx:418-423`** (`color: var(--color-success)`) — while the slot
keeps `color: var(--color-text-muted)` (`Row.tsx:78`).

- **HIGH ×1** — `row.running.ic` `color: rgb(87,199,133) → rgb(152,152,159)`.
- **HIGH ×1 (derivative/artifact)** — `row.running.ic` `borderColor` follows `color` because neither
  side draws a border; it is not an independent defect. Same for `row.running.sub.mono` `borderColor`
  under RC-4. Two of the 17 HIGH are therefore echoes, not separate breakage.

The _glyph_ colour is correct (`row.running.ic.svg` shows no colour diff), so this only matters
once RC-2 is fixed — but it must be fixed **with** RC-2, or the tile will get a `--panel3` fill and
the wrong (muted) foreground.

---

## 🔴 RC-4 — the model marker is **brighter** than the sub-line it sits in

Design: `.lrow__sub{color:var(--mut2) /* #64646d */}` `copilot.css:293`; `.mono{font-family:var(--mono)}`
`copilot.css:42` changes **only the family**, so the model text stays `--mut2`.
Live: `modelMonoStyle` at **`ChatsArchive.tsx:426-430`** sets `color: var(--color-text-muted)`
(`#98989f`, `styles.css:177`).

- **HIGH ×1** — `row.running.sub.mono` `color: #64646d → #98989f` (+ the derivative `borderColor`).

Fix: drop the `color` from `modelMonoStyle` (inherit from `Row.subStyle`, which already uses
`--color-text-subtle` = `#64646d`, `Row.tsx:108`).

---

## 🔴 RC-5 — Chats has **no destination title bar and no visible search affordance**

Design renders `TITLES.chats = ["Chats", "every conversation with the agent"]`
(`copilot-app.jsx:237`) in the topbar (`.tb-title h1` 13.5px/600, `.sub` 11.5px `--mut2`,
`copilot.css:80-82`), alongside the ⌘K "Search & commands" button — **the only search on this surface**.

Live: `chats` is in `FULL_BLEED_DESTINATIONS`
(**`packages/chat-surface/src/shell/ChatShell.tsx:43-46`**) and the shell hard-suppresses the Topbar
for full-bleed destinations (`ChatShell.tsx:236-237`, `304-311`). Neither host substitutes one:

- web — `apps/frontend/src/app/App.tsx:1042-1054` renders a bare `<section data-testid="destination-outlet">`;
- desktop — `apps/desktop/renderer/destinationBinders.tsx:223` mounts `<ChatsArchive>` with no header.

Because `CommandPaletteTrigger` is mounted **only** inside the Topbar
(`packages/chat-surface/src/shell/Topbar.tsx:3,160`; no other mount exists in `packages/chat-surface/src`,
`apps/frontend/src`, or `apps/desktop/renderer`), the Chats destination ships with **zero visible
search entry point**. The ⌘K _chord_ still works via `useShellShortcuts`, but the discoverable
control the design puts on this page is gone.

- **HIGH ×1** — `topbar.title` present in design, absent in live.

Fix site: `ChatShell.tsx:43-46` (drop `chats` from full-bleed, or add a header slot for full-bleed
destinations). Note the live `PageLead` was explicitly designed as the substitute for a _page title_
(`PageLead.tsx:4-5`, `ChatsArchive.tsx:283-284`) — but the design has **both** the topbar title **and**
the `.pg-lead`, so that substitution is a genuine loss, not an equivalent trade.

---

## 🟠 RC-6 — the `--font-size-*` rem ladder has **no step** at the design's two mono micro sizes

`packages/design-system/src/styles.css:62-71` defines
`--font-size-3xs: 0.5625rem` (9px), `--font-size-2xs: 0.7rem` (**11.2px**),
`--font-size-mono-10: 0.625rem` (10px).
`--font-size-2xs` is then made to do **two different jobs**:

| Anchor(s)                                     | Design px                    | Live token        | Live px | Site                   |
| --------------------------------------------- | ---------------------------- | ----------------- | ------- | ---------------------- |
| `sect.pinned`, `sect.recent`, `sect.archived` | `9.5px` (`copilot.css:282`)  | `--font-size-2xs` | 11.2    | `SectionHeader.tsx:40` |
| `chip.running/.dot/.paused/.archived`         | `10.5px` (`copilot.css:112`) | `--font-size-2xs` | 11.2    | `StatusPill.tsx:78`    |
| `row.running.time`                            | `10.5px` (`copilot.css:295`) | `--font-size-2xs` | 11.2    | `Row.tsx:117`          |

**MED ×8.** Section heads are **+1.7px (18 % too big)** — the single largest type error on the page,
and it is compounded by RC-8's weight bump. Closer steps already exist: `--font-size-3xs` (9px) for
`.sect-h`, `--font-size-mono-10` (10px) for chips/time.

---

## 🟠 RC-7 — inherited body size is **13.6px**, the design's is **13px**

`styles.css:377` sets `body{font-size: var(--font-size-sm)}` = `0.85rem` = 13.6px. The comment at
`styles.css:365-368` is explicit that this is an **approximation** ("the closest token on the existing
scale to the design's 13px"). Design: `body{…font-size:13px…}` `copilot.css:36`.

**MED ×5** — every anchor whose size is purely inherited: `page.container`, `header.row`,
`list.pinned`, `row.running`, `row.running.ic`(+`.svg`), `row.done.ic` → `13px → 13.6px`.

Cosmetically small on its own, but it is the app-wide baseline: nothing that inherits will ever match
until a 13px step (`0.8125rem`) exists. Fix site `styles.css:62-71` + `:377`.

---

## 🟠 RC-8 — "semibold everywhere" overrides the design's quiet 400/500 register

| Anchor             | Design                                                            | Live                                                                                                                                                    | Site                       |
| ------------------ | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| `sect.*` (×3)      | `.sect-h` sets **no** weight → inherits `400` (`copilot.css:282`) | `fontWeight: var(--font-weight-semibold)` = **600** (`styles.css:75`)                                                                                   | `SectionHeader.tsx:41`     |
| `row.running.name` | `.lrow__name{font-weight:500}` `copilot.css:292`                  | `var(--font-weight-semibold)` = 600                                                                                                                     | `Row.tsx:98`               |
| chips (×4)         | inherit 500                                                       | 600                                                                                                                                                     | `StatusPill.tsx:79` (RC-1) |
| `btn.newChat`      | `.cbtn--pri{font-weight:600}` `copilot.css:94`                    | `.ui-button--sm{font-weight: var(--font-weight-medium)}` = **500** at `styles.css:446` **overrides** the `.ui-button` 650 CTA weight (`styles.css:425`) | `styles.css:446`           |

**MED ×5.** Note the direction flips: micro-labels are _too heavy_, the one real CTA is _too light_.
600 on 11.2px uppercase wide-tracked mono (RC-6 + RC-1) is the reason the section heads read as
shouting rather than as the design's quiet index tabs.

---

## 🟠 RC-9 — margin-based vertical rhythm was replaced by flex gaps, with different numbers

Architecturally intended (gap-based layout is cleaner); the **values** drifted.

| Gap                        | Design                                            | Live                           | Site                   |
| -------------------------- | ------------------------------------------------- | ------------------------------ | ---------------------- |
| lead → first section       | `.pg-lead{margin:-2px 0 18px}` `copilot.css:281`  | `containerStyle gap: 20`       | `ChatsArchive.tsx:155` |
| section → section          | `.sect-h{margin:22px 0 10px}` `copilot.css:282`   | `sectionsStyle gap: 24`        | `ChatsArchive.tsx:161` |
| head → list                | `10px` (same rule)                                | `sectionWrapStyle gap: 10` ✅  | `ChatsArchive.tsx:348` |
| header row → list (Pinned) | inline `margin-bottom:14px` `copilot-app.jsx:132` | `10` (same `sectionWrapStyle`) | `ChatsArchive.tsx:348` |
| page padding               | `20px 24px 40px` `copilot.css:280`                | `24px 28px 96px`               | `ChatsArchive.tsx:151` |
| row padding                | `11px 14px` `copilot.css:285`                     | `10px 12px`                    | `Row.tsx:62`           |

**MED ×9 + LOW (all the height/width deltas: rows 61.25→56px, list card 63.25→58px).** The row
height loss is the visible one — the list is ~9 % denser than the design.

---

## 🟠 RC-10 — the 960px column is **centred**; the design left-aligns it

`ChatsArchive.tsx:150` — `margin: "0 auto"`. Design `.pg{padding:20px 24px 40px;max-width:960px}`
`copilot.css:280` has **no** auto margin, so it hugs the left edge of the main column.

**MED ×1** (`page.container margin: 0px → 0px 110px` at the 1180px harness frame). Real in any main
column wider than 960px, which is every desktop width above ~1250.

---

## 🟡 RC-11 — row glyphs render **18px**; the design forces **15px**

Design: `.lrow__ic svg{width:15px;height:15px}` `copilot.css:290` — it deliberately **beats** the
`width=18` attribute on both `Icon.chats` and the brand `<Mark size={18}/>`.
Live has no equivalent rule (`Row.tsx:70-79` sizes the slot, not the svg), so
`<BrandMark size={18}/>` (`ChatsArchive.tsx:468`) and `<Icon name="chats" size={18}/>`
(`ChatsArchive.tsx:472`) render at 18.

**LOW ×2** (`row.running.ic.svg width/height 15px → 18px`). Combined with RC-2's missing tile fill,
the icon column is the most visually divergent region of the row.

---

## 🟡 RC-12 — three design class names survive in the live DOM with **zero CSS behind them**

`.pg-lead` (`PageLead.tsx:36`), `.sect-h` (`SectionHeader.tsx:64`), `.rowlist` (`RowList.tsx:56`) are
emitted on the live nodes, but
`grep -n '\.rowlist\|\.pg-lead\|\.sect-h\|\.lrow' packages/design-system/src/styles.css` returns
**no matches** — every style is an inline `CSSProperties` object.

Consequences:

- **MED ×2** — `list.pinned display: flex/column → block`, purely because the live `.rowlist` class
  carries no rule and a `<ul>` defaults to `block` (`RowList.tsx:28-36` sets border/radius/bg only).
  Harmless for block `<li>`s, but it proves the class is decorative.
- The surface is untunable from CSS or a theme, and this parity harness had to be entirely
  `data-testid`-driven.
- It is the exact pattern the ui-kit consolidation (`#219`/`#221`) set out to remove: either promote
  these to real recipes in `packages/design-system/src/styles.css`, or drop the class names so they
  stop implying styling.

---

## 🟡 RC-13 — copy + time formatting diverge, and the time format **changes the layout**

- Status labels: `statusLabel()` returns Title Case (`ChatsArchive.tsx:79-86`) and `StatusPill.tsx:81`
  then uppercases → **`RUNNING` / `PAUSED` / `ARCHIVED`** vs the design's lowercase
  `running` / `paused` / `archived` (`copilot-data.jsx` fixture).
- Timestamps: `formatRelativeTime` uses `Intl.RelativeTimeFormat` with `numeric:"always", style:"narrow"`
  (**`packages/chat-surface/src/util/time.ts:26-53`**) → `"just now"`, `"2 hr. ago"`, `"1 day ago"`.
  The design's vocabulary is terse: `now / 2h / 3h / 1d / Mon` (`copilot.css:295` + fixture).

**INFO ×6 in the report, but the second one is a layout finding**: `row.running.time` widens
**18.9px → 53.8px** (+185 %), stealing ~35px from the title column on every row. Fix site
`time.ts:34-53` (add a compact mode) + `ChatsArchive.tsx:79`.

---

## ⚪ RC-14 — live-only: a count pill on every section header

`ChatsArchive.tsx:369-376` renders a muted `<StatusPill showDot={false}>` with the row count after
every section label — 3 pills the design does not have (live: 11 `status-pill`s vs design: 8 `.chip`s).
Reported as `extra-in-live`. It is an **addition**, not drift — but it inherits RC-1's filled/uppercase
styling, so it currently reads as loudly as a real status. The header text also concatenates to
`"Pinned1New chat"`.

---

## ⚪ RC-15 — `ChatsDestination.tsx` is dead code

`packages/chat-surface/src/destinations/chats/ChatsDestination.tsx` is exported from both barrels
(`destinations/chats/index.ts:2`, `src/index.ts:486`) but mounted by **neither** host — a grep for
`ChatsDestination` across `apps/frontend/src` and `apps/desktop/renderer` returns nothing, while both
hosts mount `<ChatsArchive>` directly (`apps/frontend/src/features/chats/ChatsArchiveRoute.tsx:36,142`
via `App.tsx:1054`; `apps/desktop/renderer/destinationBinders.tsx:31,223`). Not a parity defect —
a maintenance trap that will mislead the next parity pass.

---

## Fix order (highest parity gain per edit)

1. `StatusPill.tsx:66-84` — mono family, transparent fill, 25 %-alpha borders, drop uppercase/tracking,
   10px size, weight 500, `padding:1px 8px`, `gap:5`. **(RC-1: 12 HIGH + 12 MED)** — verify across all destinations.
2. `Row.tsx:70-79` — add the `--panel3`-equivalent tile fill, radius 7px; move the live tint onto the
   slot together with `ChatsArchive.tsx:418-423`. **(RC-2 + RC-3: 4 HIGH)**
3. `ChatShell.tsx:43-46` — restore a title/search header for `chats`. **(RC-5: 1 HIGH + the missing ⌘K affordance)**
4. `ChatsArchive.tsx:428` — drop the model `color`. **(RC-4: 1 HIGH)**
5. `SectionHeader.tsx:40-41`, `Row.tsx:98,117`, `styles.css:446` — type scale + weights. **(RC-6 + RC-8: 13 MED)**
6. `styles.css:62-71,377` — mint a 13px body step. **(RC-7: 5 MED)**
7. `ChatsArchive.tsx:150,151,155,161`, `Row.tsx:62` — spacing values. **(RC-9 + RC-10: 10 MED)**
8. `ChatsArchive.tsx:468,472` — `size={15}`. **(RC-11)**

---

## Honest limits — what could NOT be measured

1. **Only the `default` state exists.** The design mock ships a single populated Chats fixture
   (`copilot-data.jsx:192-201`); there is no design baseline for the live component's `loading`,
   `error`, `unavailable`, or `empty` branches (`ChatsArchive.tsx:175-249`). Those four states are
   **unverified against any design**, not "passing".
2. **`topbar.title` cannot be measured**, only reported missing (RC-5) — there is no live node.
3. **`rail.badge` is out of harness scope** by design: this harness mounts `ChatsArchive` alone, and the
   rail is `ChatShell` chrome measured by the sibling `lib/render-live-rail-badge.test.tsx`. Marked
   `expectDivergence` in `anchors.json`; it is **not** evidence the badge is correct.
4. **Interaction states are unmeasured.** The extractor reads static computed styles, so
   `.lrow:hover{background:var(--panel2)}` (`copilot.css:287`), focus rings, and the row's
   `cursor:pointer` path are untested. The live row's `:hover` treatment was not verified at all.
5. **Frame-relative geometry.** The live side renders in a 1180×820 harness frame
   (`render-live-chats.test.tsx:209-220`); the design renders in its own shell column. Raw `width`/
   `height`/`margin` deltas are therefore partly harness noise — I only raised them (RC-9, RC-10) where
   a CSS rule on one side backs the difference.
6. **Semantic deltas treated as intended, not drift:** design rows are native `<button>`s, live rows are
   `role="button"` `<div>`s (deliberate, documented at `Row.tsx:12-18` so nested links compose);
   `.sect-h` `<div>` → `<h2>` (`SectionHeader.tsx:69`) and `.rowlist` `<div>` → `<ul>` (`RowList.tsx:55`)
   are accessibility improvements. Reported LOW; do not "fix" them.
7. **No text addressed to the reader** was found in any vendored design file.
