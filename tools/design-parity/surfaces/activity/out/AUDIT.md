# Activity destination — design-parity audit

**Surface:** solo rail slot 4 (`run, chats, projects, activity, connectors→"Tools", tools→"Skills"`)
**Live component:** `packages/chat-surface/src/destinations/activity/ActivityDestination.tsx` (702 lines, pure presentation per its own FR-4.3 header)
**Design baseline:** `tools/design-parity/design-kit/app-v3/copilot-app.jsx:14-40` + `copilot.css` + `copilot-data.jsx:149-164`
**Hosts:** web `apps/frontend/src/features/activity/`; desktop `apps/desktop/renderer/destinationBinders.tsx` → `ActivityBinder`
**Measured artefacts:** `out/design-default.json`, `out/live-default.json`, `out/report-default.md`, `out/report-default.json`, `anchors.json`
**Empirical render test:** `tools/design-parity/lib/render-live-activity.test.tsx` (3/3 pass)

---

## Part 1 — UI fidelity (design parity)

### How to read the numbers

The comparator emitted **20 HIGH / 52 MEDIUM / 68 LOW / 11 INFO** property rows across 19 matched anchors. That is a property count, not a defect count. Grouped by root cause the surface has **5 HIGH and 10 MEDIUM defects, plus 2 clusters that are NOT defects**. Twelve of the twenty comparator HIGHs and roughly twenty MED/LOW rows collapse into a single cause (the status chip).

**States audited: 1 of 5.** The design's `ActivitySurface` maps its fixture unconditionally — it has no loading, error, unavailable, or empty branch. The live component implements all five (`ActivityDestination.tsx:335-470`). Three live states have **no design baseline at all**; they are _unaudited_, not passing.

---

### HIGH-1 — `StatusPill`'s inline chip is a different chip spec from the design's `.chip` (8 properties diverge at once)

**Anchors:** `row.live.chip`, `row.done.chip`, `chip.paused`, `chip.stopped`, `row.live.dot`
**Fix site:** `packages/chat-surface/src/shell/StatusPill.tsx:66-84` (`pillStyle`), palette `:38-64`

The design's `.chip` (`design-kit/app-v3/copilot.css:112-118`) is a **hairline mono outline on the panel**: `font-family: var(--mono)`, transparent background, border = a 25 %-alpha tint of the tone (`rgba(87,199,133,.25)` :114, `rgba(232,180,94,.25)` :116; `chip--off` keeps `--line2` :117), 10.5 px, weight 500, no letter-spacing, no text-transform, gap 5 px, inline padding `1px 8px`.

`pillStyle` sets **no `fontFamily`** (inherits sans), `backgroundColor: palette.bg` (`--color-success-bg #1a2f23` / `--color-warning-bg #322615` / `--color-surface-muted #16161a`), `border: 1px solid palette.fg` at **full opacity**, `font-size: var(--font-size-2xs)` = 11.2 px, `fontWeight: 600` (:79), `letterSpacing: 0.3` + `textTransform: uppercase` (:80-81), `gap: 6` (:71), `padding: "0 8px"` + fixed `height: 20` (:72-73).

**Net effect:** the design renders quiet mono outlines; the live app renders **filled, solid-bordered, UPPERCASE sans badges**. This is the single largest visual divergence on the surface.

> **Consolidation note.** `packages/design-system/src/styles.css:1145-1157` already ships the canonical `.ui-pill` recipe (dot at :1164) whose own docblock says it collapses `aui-status-pill` / `me-radio-pill` / `settings-pill` / `ui-connector-chip` / `aui-attachment-pill` into one recipe. `StatusPill` is **the copy that got missed** by the UI-kit consolidation. But `.ui-pill` _also_ disagrees with the design (`--font-weight-medium`, `gap .4rem`, `padding .25rem .6rem`, no mono) — so migrating onto it does not by itself close this. See Remediation R1.

---

### HIGH-2 — the 28×28 icon slot has no surface, and the live-run jade tint never reaches the tile

**Anchor:** `row.live.ic`
**Fix sites:** `packages/chat-surface/src/destinations/_shared/Row.tsx:70-79` (`iconSlotStyle`); misplaced tint at `ActivityDestination.tsx:675-680` (`liveIconStyle`), applied `:493-500`

Design `.lrow__ic` (`copilot.css:289`) = 28×28, radius **7px**, `display: grid`, `background: var(--panel3)`, `color: var(--mut)`; the live row overrides the **tile's** colour inline to `var(--jade)` (`copilot-app.jsx:28`).

`iconSlotStyle` sets **no background at all** and hard-codes `color: var(--color-text-muted)`. `ActivityDestination` wraps the `BrandMark` in a _separate_ `liveIconStyle` span, so the measured tile computes `backgroundColor: transparent` (vs `rgb(29,29,35)` `--panel3`) and `color: rgb(152,152,159)` `--mut` (vs `rgb(87,199,133)` `--jade`). `borderColor` reports the same swap because it derives from `color` — **one defect counted twice** in the comparator. Also `border-radius: var(--radius-md)` = 8px (`styles.css:109`) vs 7px, and `display: flex` vs `grid`.

**Consequence:** with no `--panel3` tile, the leading-icon column stops reading as a column.

---

### HIGH-3 — `Row` has no trailing slot, so the live-run chevron (the only "this navigates" affordance) does not exist

**Anchors:** `row.live.chevron`, `row.done.spacer`
**Fix site:** `packages/chat-surface/src/destinations/_shared/Row.tsx:146-191` (renders icon → main → meta and stops; `RowProps` :32-53 has no `trailing`)

The design renders `Icon.chevR` (15×15, `--mut2`, `copilot.css:296`) on **live rows only**, plus a `<span style={{width:16}}/>` on every other row (`copilot-app.jsx:31`).

**Honest split.** The chevron is a **real defect** — live rows are activatable (`role="button"`, `Row.tsx:149`) but show nothing signalling it. The spacer is **not independently a defect**: its only job is to reserve the chevron's 16px so the time column stays flush; since live renders neither, columns still align. The fix must therefore add **both** — adding the chevron alone would misalign the time column on done/paused/stopped rows.

Corroborating absence (from the feature lens, ACT-13): `chevronRight` is declared in the icon SSOT at `packages/chat-surface/src/icons/paths.tsx:42,157` — byte-identical to the design's `Icon.chevR` — with **zero call sites repo-wide**.

---

### HIGH-4 — the per-destination topbar subtitle is structurally unreachable

**Anchor:** `topbar.sub` (the only live anchor that failed to match — 18/19 matched)
**Fix site:** `packages/chat-surface/src/shell/Topbar.tsx:74-79` (`resolveSubtitle`) + `:34` (`TITLE_BY_SLUG`)

The design carries a static per-destination tagline: `activity: ["Activity", "every action the agent has taken"]` (`copilot-app.jsx:239`, rendered :310).

Live `Topbar` has `TITLE_BY_SLUG` but **no subtitle map**. The subtitle renders only when `leaf` is non-empty (`Topbar.tsx:141-145`), and `leaf` comes from `ChatShell`'s `topbarLeaf` (`ChatShell.tsx:308`) — a **breadcrumb leaf, not a tagline**. `grep -rn topbarLeaf apps/frontend/src apps/desktop/renderer` returns **nothing**: neither host passes it. All six destinations render a bare title on both hosts _by construction_, not by data.

---

### HIGH-5 — desktop drops the run id when opening a running Activity row; web forwards it (behavioural; no computed-style anchor)

**Fix site:** `apps/desktop/renderer/destinationBinders.tsx:369`

`ActivityDestination` calls `onOpenRun(row.run_id)` (`:543-546`). Web forwards it — `apps/frontend/src/features/activity/ActivityRoute.tsx:116`, signature `(runId: RunId) => void` at `:48`. Desktop binds `onOpenRun={() => onOpenRun?.()}` — argument discarded — so clicking a running row on desktop opens the Run destination on **whatever run is already active**, not the clicked one.

This is the exact host-asymmetry class the audit brief called out. (Part 2 / ACT-12 shows the web side is _also_ broken, differently: it forwards a **run id into a conversation-id slot**.)

---

### MEDIUM-1 — the design-system type scale is rem-based where the design uses px literals

**Anchors:** 15 (`page.container`, `page.lead`, `page.lead.link`, `day.head`, `rowlist`, `row.live`, `row.live.ic`, `row.live.ic.svg`, `row.live.chip`, `row.done.chip`, `chip.paused`, `chip.stopped`, `row.live.time`, `row.live.dot`, `row.done.ic.svg`)
**Fix site:** `packages/design-system/src/styles.css:63-65`

`--font-size-2xs: 0.7rem` (11.2px), `--font-size-xs: 0.78rem` (12.48px), `--font-size-sm: 0.85rem` (13.6px) vs the design's literals in `copilot.css:280-300`.

| Element                      | Design | Live    | Δ                |
| ---------------------------- | ------ | ------- | ---------------- |
| body                         | 13px   | 13.6px  | +0.6             |
| lead (`:281`)                | 12px   | 12.48px | +0.5             |
| day divider (`:300`)         | 10px   | 11.2px  | **+1.2 (worst)** |
| chip / time (`:112`, `:295`) | 10.5px | 11.2px  | +0.7             |

**Not uniformly wrong.** `--font-size-xs` 12.48 matches `.lrow__name` 12.5px almost exactly — which is why `row.live.name` produced _no_ font-size finding. The misses concentrate in `--font-size-2xs`, which **collapses the design's two small steps** (10px divider, 10.5px chip/time) into one 11.2px step. Same root cause the FTUE-gate parity baseline recorded; fixing it moves every surface.

> A canonical 10px step **already exists**: `--font-size-mono-10: 0.625rem` (`styles.css:71`), documented as "canonical small-mono pill metadata … deliberately off the main ladder". Neither the day divider nor the chip uses it. See R2.

---

### MEDIUM-2 — the day divider is styled as a SECTION LABEL, not as `.act-day`

**Anchor:** `day.head`
**Fix site:** `ActivityDestination.tsx:654-666` (`dayDividerStyle`); the class hook at `:451` literally reads `className="act-day sect-h"`

Design has two distinct treatments: `.act-day` (`copilot.css:300`) = mono 10px `--mut2`, weight 400, **no** letter-spacing, **no** text-transform, margin `18px 0 8px`; and `.sect-h` (`copilot.css:282`) = 9.5px, `letter-spacing .12em`, uppercase — the _other_ surfaces' header.

`dayDividerStyle` applies `fontWeight: 600` (:659), `letterSpacing: 0.4` (:660), `textTransform: uppercase` (:661), so the design's quiet "Today" renders as bold **"TODAY"**. Mono family and `--mut2` colour **are** correct.

The `margin 18px 0 8px → 0` row is **not** part of this defect: live drives rhythm with flex gap (`groupsWrapStyle` gap 20 `:640-644`, `dayGroupStyle` gap 8 `:646-650`) giving 20/8 vs the design's 18/8 — equivalent.

---

### MEDIUM-3 — page and row padding undershoot on every axis

**Anchors:** `page.container`, `row.live`
**Fix sites:** `ActivityDestination.tsx:611-621` (`innerStyle`); `Row.tsx:55-66` (`rowStyle`)

`innerStyle` padding `16px 20px 32px` vs `.pg { padding: 20px 24px 40px }` (`copilot.css:280`). `rowStyle` padding `10px 12px` vs `.lrow { padding: 11px 14px }` (`copilot.css:285`). Row `gap: var(--space-md)` = 12px **does** match `.lrow { gap: 12px }` — no finding there. Net: the surface reads denser than designed.

---

### MEDIUM-4 — row title weight is semibold; the design is medium

**Anchor:** `row.live.name` · **Fix site:** `Row.tsx:96-104`

`titleStyle` `fontWeight: var(--font-weight-semibold)` = 600 (`styles.css:75`) vs `.lrow__name { font-weight: 500 }` (`copilot.css:292`). One property — but it hits **every row on every list destination** that uses the shared `Row` (Chats, Connectors, Skills).

---

### MEDIUM-5 — status label casing drifts twice over

**Anchors:** `row.live.chip`, `row.done.chip`, `chip.paused`, `chip.stopped`
**Fix sites:** `ActivityDestination.tsx:86-98` (`activityStatusLabel`) **and** `StatusPill.tsx:81`

Design chips read lowercase `running` / `done` / `paused` / `stopped` (`copilot-app.jsx:15`). `activityStatusLabel` returns Title-Case `"Running"` / `"Done"` / …, then `pillStyle` applies `textTransform: uppercase`, so the pill renders **"RUNNING"**. Two independent decisions stack into one visible drift — fixing either alone still misses.

---

### MEDIUM-6 — row time is relative where the design shows wall-clock, inside an already day-grouped feed

**Anchor:** `row.live.time` · **Fix site:** `ActivityDestination.tsx:538` → `packages/chat-surface/src/util/time.ts`

Design fixture carries `11:44` / `09:02` (`copilot-data.jsx:149-164`). Because the group heading already states the day, "1d ago" under a "Yesterday" heading is redundant; the design's wall clock is the more informative form. Also drives the `row.live.time` width delta 31.5px → 47px.

**Caveat — this is a product decision, not an oversight.** `docs/plan/desktop-redesign/phase-4/PRD.md:113` (FR-4.4) and `:133` (FR-4.15) _specify_ relative time via `formatRelativeTime(iso, now)`. Log as **design-vs-PRD drift**, and route it to a product decision rather than a fix ticket. (The lens claim that variable-width relative strings break a fixed time column is **refuted**: `.lrow__time { … flex: none }` at `copilot.css:295` is content-sized, exactly like `Row.tsx:115` `flex: "0 0 auto"`.)

---

### MEDIUM-7 — explicit-date day dividers lose the weekday and gain a year

**Anchor:** `day.head` · **Fix site:** `ActivityDestination.tsx:138-142`

`Intl.DateTimeFormat(locale, { year:"numeric", month:"short", day:"numeric" })` renders **"Jul 14, 2026"**; the design renders **"Mon, Jul 14"**. Add `weekday: "short"`, drop `year` (or emit year only when the row falls in a previous calendar year).

---

### MEDIUM-8 — the lead paragraph lost two-thirds of its copy, and the retention link swallowed a whole sentence

**Anchors:** `page.lead`, `page.lead.link` · **Fix site:** `ActivityDestination.tsx:59` + `:65-66`, rendered `:471-511`

The design's lead is three sentences. The middle one — the sentence that explains why Activity is a _destination_ rather than a log ("This is the record the old build buried in an audit log — here it is a place you visit") — **is gone**; `ACTIVITY_LEAD_COPY` is just "Everything the agent has done." The dropped clause also carries the **"most recent first"** ordering promise, which matters because ordering is exactly what the data path breaks (Part 2 / ACT-04).

The design links **only** the phrase "Settings → Privacy". Live makes the **entire final sentence** the link, so ~322px of underlined accent text sits in a 12px muted paragraph (`page.lead.link` width `auto → 321.969px`).

> **Correction to the render stage's prediction:** the accent **colour is correct** — both sides compute `rgb(95,178,236)` (`--color-accent = #5fb2ec`, `styles.css:180`). There is **no colour finding** on this anchor.

---

### MEDIUM-9 — a second, permanently-empty Activity surface ships in the shell right rail

**Fix site:** `packages/chat-surface/src/shell/ChatShell.tsx:323` (shell-level; outside this destination's anchor map)

This answers the brief's `ActivityList` / `ActivityTabContent` question. They are a **different, parallel surface, and it is dead**:

- `RightRail.tsx:265` renders `<ActivityTabContent entries={activity ?? []} now={now}/>`, but `ChatShell` constructs `<RightRail open onToggle/>` **only** — `activity` is never passed, so `entries` is always `[]` and the tab renders `EmptyStateMessage` (`RightRail.tsx:272,283`).
- `ChatShell.tsx:318-321` admits it: _"empty scaffolding until Activity/Approvals is wired"_.
- `ActivityTabContent.tsx:12-18` documents that it deliberately does **not** use `ActivityList` (which demands an `ItemRef` per row).
- `ActivityList`'s only live consumer is `destinations/home/sections/LiveActivityRail.tsx:76` inside `HomeDestination`, mounted only by `apps/frontend/src/features/home/HomeRoute.tsx:270` — and **Home has no slot in the solo rail**.

**Verdict:** no duplication risk to the audited surface, but two built-and-unmounted activity renderers in the package _plus_ a visible-but-always-empty "Activity" tab on every non-full-bleed destination.

---

### MEDIUM-10 — both hosts silently swallow an audit authorization failure, so the row meta line can vanish with no error (LATENT)

**Anchor:** `row.live.sub` · **Fix sites:** `apps/frontend/src/features/activity/api/activityApi.ts:207-211`; `apps/desktop/renderer/destinationBinders.tsx:336-344`

**Data path established** (the brief's load-bearing question): there is **no `/v1/activity` facade route**. The only repo hits for `v1/activity` are comments saying it does not exist yet (`activityApi.ts:7,15,81`; `packages/api-types/src/activity.ts:13`; `index.ts:4554`). Both hosts compose `GET /v1/agent/conversations` (run spine) + `GET /v1/audit` (meta enrichment) into `ActivityRunRow[]` — web `activityApi.ts:143-224`, desktop `destinationBinders.tsx:292-349`. **The projection (`auditLabel` / `buildMetaIndex` / `projectActivityRows`) is duplicated verbatim across the two hosts** — a real cross-host drift hazard.

**`/v1/audit` is real and persistent** (against the repo's compliance rule): `backend-facade/audit_routes.py:95` fans out to backend's `/internal/v1/audit/list` (`services/backend/src/backend_app/routes/audit_list.py:108`) **and** ai-backend's (`services/ai-backend/src/runtime_api/http/audit_list_routes.py:169`), merging + sorting. Persistence is hash-chained and disk-backed on both store adapters (`postgres/runtime_api_store.py:3158-3205`; `file/runtime_api_store.py:1952-1959` + `:752`). Not no-op, not in-memory-only.

**The risk:** backend's handler is gated `Depends(RequireScopes(ADMIN_AUDIT_EXPORT))` (`audit_list.py:110`) and the facade deliberately surfaces backend 401/403 to the caller (`audit_routes.py:138-141`). Both clients then `.catch(() => [])`. Under `RBAC_MODE=enforce` a non-admin gets a feed whose `meta` line is empty on **every** row — and the design's `.lrow__sub` ("4 apps · 7 steps · awaiting 1 approval") is load-bearing — **with no error, no degraded badge, no client log**.

**Honest scoping:** `RBAC_MODE` defaults to `audit` (log-and-pass-through, `services/backend/src/backend_app/identity/rbac.py:41-49`), so this does **not** fire in the default deployment today. Reported as **latent MEDIUM**, not a live HIGH. I did not trace which roles are granted `admin:audit_export`.

---

### NOT DEFECTS (recorded so they are not re-litigated)

**N1 — `row.live` borderColor/borderWidth is a measurement artefact.** The comparator reports it HIGH. Design puts the separator on the row (`.lrow { border-bottom: 1px solid var(--line) }`, `copilot.css:285`); live moves it to the `<li>` wrapper (`RowList.tsx:38-43`, `rowItemStyle`). `Row` therefore declares no border, so computed `borderColor` falls back to `color` (`--tx`) and `borderWidth` to 0 — a colour swap with **zero pixel consequence**. The hairline is still drawn, in the same place, in `--color-border`. **Do not "fix" this.** Same class: `page.container` margin `0 → 0 110px` is pure frame arithmetic (harness frame 1180px, `max-width: 960` centred); both sides compute `width: 960px`.

**N2 — tag changes and the 18px icon size are live-side improvements or design-side accidents.**
`div→h2` (day head), `div→ul` (rowlist), `button→div` (row), `h1→span` (topbar title), `a→button` (retention link) are all deliberate or better: `Row` is a `role="button"` div **on purpose** so nested `ItemLink`s compose (`Row.tsx:13-16,146-153`), and the retention control invokes a host callback rather than a URL (`ActivityDestination.tsx:499-506`).
`row.live.ic.svg` 15×15 → 18×18: **the design intends 18** (`copilot-app.jsx:28` passes `size={18}`) but `.lrow__ic svg { width:15px; height:15px }` (`copilot.css:290`) overrides it — live honours the author's intent. Do not "fix" either side from the JSX alone.
`row.live.name` display/gap/alignItems/width is structural and declared in `anchors.json` (the design's `.lrow__name` is one flex row carrying title + chip _and_ the typography; live splits it into `titleRowStyle` `Row.tsx:89-94` + `titleStyle` `:96-104`, and the anchor binds the typography node). Only its `fontWeight` diff is real (MEDIUM-4).
Separately, `lineHeight → "normal"` on 7 anchors is a **real omission** (no `line-height` in any inline style object) but cosmetically small at these sizes — fold it into R2.

---

## Part 2 — Feature parity

24 features extracted from the design. Ten went through adversarial refutation; the rest are single-pass merged views. **Refuted claims are marked NOT A GAP and are not smuggled back in.**

| Feature                                                  | Design                                                                          | Live UI                                                                                                                                  | Web host                                                                                 | Desktop host                                                                                            | Facade                                                                                                                                        | Backend                                                                                                                                                                   | Verdict                                                                                                                                                                                                                                                                                                                                                                                                                             |
| -------------------------------------------------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ACT-01** Activity as a first-class destination         | rail slot + topbar title/subtitle                                               | `destinations.ts:91,119`; `ActivityDestination.tsx:254`                                                                                  | `App.tsx:1072-1090`                                                                      | `DestinationOutlet.tsx:197-203`                                                                         | none dedicated (**no `/v1/activity`**)                                                                                                        | composed from 2 routes                                                                                                                                                    | ⚠️ **WIRED, subtitle unreachable** (HIGH-4). Extra 224px empty ContextPanel on both hosts (`ChatShell.tsx:250-252`)                                                                                                                                                                                                                                                                                                                 |
| **ACT-02** Explanatory lead copy                         | 3 sentences                                                                     | `:59`, `:286`, `:315-316`                                                                                                                | unconditional                                                                            | unconditional                                                                                           | n/a                                                                                                                                           | n/a                                                                                                                                                                       | ⚠️ **COPY DRIFT** — middle sentence + "most recent first" dropped (MEDIUM-8)                                                                                                                                                                                                                                                                                                                                                        |
| **ACT-03** Deep-link to Settings → Privacy               | inline anchor                                                                   | `:65-66`, `:317-330`                                                                                                                     | `App.tsx:1086→843-845`                                                                   | `bootstrap.tsx:364→268-271`                                                                             | `/v1/retention/effective` real; `POST /v1/agent/workspace/export`; `DELETE /v1/agent/workspace/data`                                          | export = v1 **stub**; delete-all = hard **501** (`workspace_data_routes.py:105,124,131,151`)                                                                              | ⚠️ **NAV WIRED, DESTINATION HOLLOW** — link works; 2 of 3 promised actions unavailable                                                                                                                                                                                                                                                                                                                                              |
| **ACT-04** Reverse-chronological run history             | 8 rows, newest first                                                            | sort real: `:196`, `:193`, `:205-211`                                                                                                    | `activityApi.ts:182`, `:150-162`                                                         | `destinationBinders.tsx:323`, `:298-309`                                                                | `app.py:410` (no cursor). _Refuted sub-claim:_ an all-status run list **does** exist at `app.py:468` — per-conversation, uncalled by Activity | `conversation_query_service.py:441` → `get_active_run_for_conversation`; non-terminal only in **all three** adapters (pg `:1361-1363`, file `:1483-1488`, mem `:666-671`) | 🔴 **ORDERING FULLY WIRED / CONTENT STRUCTURALLY BROKEN.** Every finished run is invisible; one row **per conversation**, not per run. Sort key is `updated_at`, not run start                                                                                                                                                                                                                                                      |
| **ACT-05** Day grouping w/ relative labels               | Today / Yesterday / Mon, Jul 14                                                 | `:166-203`, `:130-143`; hairline is a **real span** `:458`/`:668-672`                                                                    | `ActivityRoute.tsx:84,96,114`                                                            | `destinationBinders.tsx:357,359,366`                                                                    | n/a (client-side)                                                                                                                             | n/a                                                                                                                                                                       | ✅ **BUILT** — but degenerate: ACT-04 makes "Today" the only reachable group. Live-only "Earlier" bucket (`:152,:180`); label format drifts (MEDIUM-7)                                                                                                                                                                                                                                                                              |
| **ACT-06** Per-run title                                 | 8 distinct names                                                                | `:511-515` — running row renders `row.title`; **all others** go through `<ItemLink kind:"run">`                                          | no `registerItemRefResolver("run")` in `apps/frontend/src`                               | none in `apps/desktop` at all                                                                           | n/a                                                                                                                                           | n/a                                                                                                                                                                       | 🔴 **WIRED BUT BROKEN.** Only shipping resolver is the placeholder `destinations/home/index.ts:53-60` returning the constant **"Run"**. Proven empirically: `render-live-activity.test.tsx` — all 7 non-running rows render "Run"; projected titles absent from the DOM                                                                                                                                                             |
| **ACT-07** Four-state status pill                        | running/done/paused/stopped                                                     | `statusTone.ts:40-56` + `StatusPill.tsx:38-64`; colour semantics match the design **1:1**                                                | `activityApi.ts:56-71` (all statuses mapped)                                             | `destinationBinders.tsx:242-258` (dup)                                                                  | `app.py:410`. _Refuted:_ backend is **NOT** stubbed — all terminal statuses persist and are served by `app.py:468-482`                        | active-only projection is the feed spine                                                                                                                                  | 🔴 **PILL CORRECT / 3 OF 5 STATES UNREACHABLE.** `done`+`stopped` blocked by the ACT-04 spine (proven by passing test `test_fastapi_runtime_api.py:341-355`); `paused` absent from `AgentRunStatus` entirely; live adds an un-designed 5th state `needs_input`. **Host-binder gap, not a backend stub**                                                                                                                             |
| **ACT-08** Live-run indicator dot                        | 1 dotted row of 8                                                               | `statusTone.ts:41` → `StatusPill.tsx:84-90,107`                                                                                          | derived                                                                                  | derived                                                                                                 | `app.py:410`                                                                                                                                  | pg `:1361`                                                                                                                                                                | ⚠️ **BUILT, INVERTED IN PRACTICE** — with the current spine nearly every row is dotted, so the dot stops discriminating. Static, not pulsing                                                                                                                                                                                                                                                                                        |
| **ACT-09** Live↔historic icon swap                       | Mark ↔ clock                                                                    | `:494-506`; glyph real at `icons/paths.tsx:53,190`                                                                                       | derived                                                                                  | derived                                                                                                 | n/a                                                                                                                                           | n/a                                                                                                                                                                       | ✅ **BUILT.** Clock branch nearly unreachable (ACT-04). 18px vs design's rendered 15px is a **design-side CSS accident** — see N2                                                                                                                                                                                                                                                                                                   |
| **ACT-10** Per-run meta summary                          | "4 apps · 7 steps · awaiting 1 approval"                                        | slot real `:519-522` → `Row.tsx:169-173`                                                                                                 | `activityApi.ts:83-99,108-125,164-174`                                                   | `destinationBinders.tsx:260-290` (byte-dup)                                                             | `audit_routes.py:95` real, hash-chained, persistent                                                                                           | join keys are disjoint (`audit.py:79,102,221`; `audit_reader.py:323,348,374,406`)                                                                                         | 🔴 **NOT BUILT** (downgraded from BACKEND_STUB). Four independent blockers: always-miss join; the sole `tool_name` emitter `emit_tool_call_outcome` (`runtime_worker/audit.py:194`) has **zero production call sites**; `run_id` persisted (`:290`) but excluded from the `extra="forbid"` wire model (`audit_list_routes.py:38-56`); scope gate + `.catch(()=>[])`. Step count & outcome clause have **no backing field anywhere** |
| **ACT-11** Per-run timestamp                             | absolute HH:MM                                                                  | `:532-540` semantic `<time>` + `util/time.ts:24-53`; `Row.tsx:114-120` mono column                                                       | `ActivityRoute.tsx:96,114`                                                               | `destinationBinders.tsx:357,366`                                                                        | `app.py:410`                                                                                                                                  | `RunRecord.started_at/completed_at` persisted (`runs.py:356-359`) but **dropped** by the list projection (`conversation_query_service.py:474-489`)                        | ⚠️ **WIRED, WRONG FIELD.** Both binders send `conversation.updated_at` while `api-types/src/activity.ts:73-75` promises "server-stamped run start". **Format divergence is NOT a gap** — relative time is specified by `PRD.md:113,133`. **Alignment rationale REFUTED** — `copilot.css:295 flex:none` is content-sized                                                                                                             |
| **ACT-12** Click-through live row → Run cockpit          | chevron + navigate                                                              | surface complete + unit-tested (`:232,:543-546,:559-562`)                                                                                | forwards the id — but into a **conversation slot** (`App.tsx:1085→834-840→822-828→1022`) | **discards** the id (`DestinationOutlet.tsx:84` types it `() => void`; `destinationBinders.tsx:367`)    | n/a (client nav)                                                                                                                              | n/a                                                                                                                                                                       | 🔴 **BROKEN ON BOTH HOSTS, DIFFERENTLY.** Neither lands the user on the run they clicked. Remediation primitive already in-repo: `RunRoute.tsx:288-309` resolves run→conversation. Non-live rows are worse — on web `ItemLink` falls through `HashRouter.ts:258-261` to **`/settings`**                                                                                                                                             |
| **ACT-13** Chevron ↔ 16px-spacer swap                    | `copilot-app.jsx:31`                                                            | **none** — `Row.tsx:32-53` + `:146-192` have no trailing slot; `chevronRight` declared at `icons/paths.tsx:42,157` with **0 call sites** | none                                                                                     | none                                                                                                    | n/a                                                                                                                                           | n/a                                                                                                                                                                       | 🔴 **MISSING** (= HIGH-3). Only surviving cue is `Row.tsx:156` `cursor: pointer`                                                                                                                                                                                                                                                                                                                                                    |
| **ACT-14** Row hover feedback                            | `.lrow:hover{background:var(--panel2)}`                                         | **none** — inline styles cannot express `:hover`; no stylesheet targets `.lrow`/`rowlist`                                                | none                                                                                     | none                                                                                                    | n/a                                                                                                                                           | n/a                                                                                                                                                                       | ⚠️ **MISSING** (untested — see limitations). Live correctly gives pointer only to activatable rows, which is _better_ than the design's mock inconsistency; but the one clickable row gets no feedback                                                                                                                                                                                                                              |
| **ACT-15** Cross-destination active-run badge            | `.rbadge` on Run                                                                | `AppRail.tsx:106,246-247,271-273`, tested `AppRail.test.tsx:266-300`                                                                     | **WIRED** — `App.tsx:520,1224-1226` + `useActiveRunCount.ts`                             | **NOT WIRED** — `bootstrap.tsx:318-331` passes no `railBadges`; 0 grep hits repo-wide in `apps/desktop` | `app.py:410`                                                                                                                                  | `conversations.py:272,285-301` real                                                                                                                                       | 🔴 **HOST_GAP (desktop).** ~4-line binding, no new plumbing. Desktop also omits `railIdentity` + `walletChip` — **systematic rail-prop under-feeding**. Stale comment `App.tsx:1215-1217` calls it unwired 7 lines above the line that wires it                                                                                                                                                                                     |
| **ACT-16** ⌘K palette reachable                          | topbar trigger                                                                  | `ChatShell.tsx:236,304-311`                                                                                                              | `App.tsx:1213`                                                                           | `bootstrap.tsx:329,388`                                                                                 | n/a                                                                                                                                           | n/a                                                                                                                                                                       | ✅ **PARITY.** ⌘⇧F "Search activity" **is** bound on desktop (`bootstrap.tsx:305-310`) — lens claim of a dead chord **REFUTED for desktop**; it is dead on web only                                                                                                                                                                                                                                                                 |
| **ACT-17** Scrolling body, 960px measure                 | `.pg`                                                                           | `:598-620`, `maxWidth: 960`                                                                                                              | `ActivityRoute.tsx:110` + `App.tsx:1079`                                                 | `destinationBinders.tsx:364`                                                                            | n/a                                                                                                                                           | n/a                                                                                                                                                                       | ✅ **PARITY on measure.** Two notes: padding drift (MEDIUM-3) and **three nested `overflow:auto`** on web vs one scroll owner in the design                                                                                                                                                                                                                                                                                         |
| **ACT-18** _Absent by design:_ no empty state            | none                                                                            | live **invents two** — `:401-410` "No activity yet", `:387-398` "Activity unavailable"                                                   | `ActivityRoute.tsx:60-67`                                                                | `destinationBinders.tsx:362`                                                                            | n/a                                                                                                                                           | n/a                                                                                                                                                                       | ⚠️ **UN-DESIGNED ADDITION — no baseline, grade separately.** High-traffic: ACT-04 makes "No activity yet" the **default screen** for a user with a full completed history — an actively false statement. `unavailable` branch is unreachable (neither binder constructs it)                                                                                                                                                         |
| **ACT-19** _Absent by design:_ no loading/error state    | none                                                                            | live invents both — `:352-367` skeleton, `:370-384` error+Retry                                                                          | `ActivityRoute.tsx:78-80,118`                                                            | `destinationBinders.tsx:91-117,369`                                                                     | facade returns `degraded_streams` (`audit_routes.py:189`) — **neither binder reads it**                                                       | n/a                                                                                                                                                                       | ⚠️ **UN-DESIGNED ADDITION.** Real behavioural hole inside the invention: **Retry renders only on the error branch**, so a stale successful list has no refresh control                                                                                                                                                                                                                                                              |
| **ACT-20** _Absent by design:_ no filter/search/sort     | none                                                                            | none (`:217-247`, `:276-292`)                                                                                                            | `ActivityRoute.tsx:112-119`                                                              | `destinationBinders.tsx:327-345`                                                                        | filters exist on `/v1/audit` (`audit_routes.py:43-50`)                                                                                        | `audit_reader.py:52-59`                                                                                                                                                   | ✅ **NOT A GAP — parity holds.** Two lens sub-claims **REFUTED**: the filter capability **is** reached by a shipped client (`AuditLogSettings.tsx:68-79,150-200`, team-profile-gated); and ⌘⇧F is **not** dead on desktop                                                                                                                                                                                                           |
| **ACT-21** _Absent by design:_ no pagination             | none                                                                            | none (`:415` unsliced)                                                                                                                   | `activityApi.ts:39,41,196-212` — `has_more` discarded                                    | `destinationBinders.tsx:330-348` — same                                                                 | `app.py:410-415` no cursor, `le=200`                                                                                                          | `conversation_query_service.py:189-190` "not implemented yet"                                                                                                             | ✅ **NOT A GAP vs design.** Sub-claims **REFUTED**: cursor plumbing exists on the audit half Activity already imports (`auditApi.ts:31`) and ships in 3 other surfaces; keyset codec exists at `conversation_query_service.py:41-64`. Real production ceiling: silent 50-conversation truncation, worsened because chat-only conversations consume the budget then get filtered out                                                 |
| **ACT-22** _Absent by design:_ no per-row actions        | none (contrast same file's `.lrow__act` on Connectors, `copilot-app.jsx:56-62`) | none — `:547-563` over `Row.tsx:32-53,146-192`                                                                                           | `ActivityRoute.tsx:112-119`                                                              | `destinationBinders.tsx:363-371`                                                                        | n/a                                                                                                                                           | n/a                                                                                                                                                                       | ✅ **NOT A GAP — parity holds.** Caveat: read-only posture is enforced only by client omission; cancel + conversation DELETE routes already exist                                                                                                                                                                                                                                                                                   |
| **ACT-23** _Absent by design:_ no counts on day headings | none                                                                            | none rendered; count exists only as `data-row-count` test hook (`:446`)                                                                  | none                                                                                     | none                                                                                                    | n/a                                                                                                                                           | n/a                                                                                                                                                                       | ✅ **NOT A GAP.** Cosmetic risk: heading carries `act-day sect-h` — inert today, diverges the moment `.sect-h` gets real CSS                                                                                                                                                                                                                                                                                                        |
| **ACT-24** _Absent by design:_ no polling/auto-refresh   | static fixture                                                                  | inert — sole hook is `useMemo` `:268-272`                                                                                                | one-shot `ActivityRoute.tsx:87-103`                                                      | one-shot `destinationBinders.tsx:96-113`                                                                | per-run SSE exists (`app.py:1069`), unusable as a feed                                                                                        | n/a                                                                                                                                                                       | ✅ **NOT A GAP vs design.** Residual real defect: the **web** rail badge polls every 30s (`useActiveRunCount.ts:18,52`) against the same data while the list stays frozen, so badge and rows can visibly disagree. Desktop has neither — web-only asymmetry. _Refuted:_ a completed run does **not** "vanish"; the projection maps `completed→done` — the narrower truth is a stale "running" until remount                         |

**Score:** 7 confirmed gaps (ACT-04, 06, 07, 10, 12, 13, 15) · 6 partial/latent (01, 02, 03, 08, 11, 14) · 3 un-designed additions needing a design decision (18, 19, and the `needs_input` 5th state) · 8 at parity (05, 09, 16, 17, 20, 21, 22, 23, 24).

---

## Part 3 — Remediation

Constraint honoured: **no bandaids, only architectural solutions.** Each item names the layer that should own it and the seam both hosts feed.

### Ordering (user-visible impact × blast radius)

| #      | Fix                                                                                               | Impact                                 | Blast radius                           | Parallel?      |
| ------ | ------------------------------------------------------------------------------------------------- | -------------------------------------- | -------------------------------------- | -------------- |
| **R0** | Run-spine projection (`latest_run_status_any_status`)                                             | 🔴 makes the surface _show data_       | ai-backend + both binders              | blocks R7      |
| **R1** | `.ui-pill--outline` tone recipe in design-system; migrate `StatusPill`                            | 🔴 12/20 comparator HIGHs              | every status chip app-wide             | ✅ independent |
| **R2** | `--font-size-2xs` split + `--font-size-mono-10` adoption + line-height                            | 🟠 15 anchors here, every surface      | design-system tokens                   | ✅ independent |
| **R3** | `Row` trailing slot + icon-tile surface                                                           | 🔴 navigability + column legibility    | shared `Row` (Chats/Connectors/Skills) | ✅ independent |
| **R4** | Widen the desktop `onOpenRun` seam to `(runId) => void`; resolve run→conversation in chat-surface | 🔴 both hosts land wrong               | 2 host binders + 1 hook                | ✅ independent |
| **R5** | `subtitle` on `DESTINATION_REGISTRY`; `railBadges` port                                           | 🟠 all 6 destinations, desktop rail    | shell registry + `ChatShell`           | ✅ independent |
| **R6** | Extract the Activity projection into `packages/chat-surface`                                      | 🟠 removes a verbatim host duplication | both binders                           | ✅ independent |
| **R7** | Real `GET /v1/activity` (run-keyset + meta counters)                                              | 🔴 meta line, pagination, per-run rows | new facade contract                    | after R0       |

---

### R0 — the run-history spine (owner: **ai-backend**, not the clients)

**Root cause of ACT-04, ACT-06 (row absence), ACT-07, ACT-08, ACT-09, ACT-18.** Six "separate" feature gaps are one server projection choice.

Both binders require `latest_run_id` **and** `latest_run_status` and `continue` otherwise (`activityApi.ts:150-162`; `destinationBinders.tsx:298-309`). The server populates those only from `get_active_run_for_conversation` — non-terminal in **all three** adapters. `packages/api-types/src/index.ts:526-531` states it in prose.

**Owner:** `services/ai-backend/src/agent_runtime/api/conversation_query_service.py:474-489`. It already fetches the terminal run via `get_latest_run_for_conversation` and keeps only `model_name` + `run_id`. Add a **status** projection alongside the existing `latest_run_id_any_status` (`schemas/conversations.py:281`), plus `started_at` / `completed_at` (**this also closes ACT-11's contract-honesty defect at zero extra query cost**). Then declare the fields in `packages/api-types/src/activity.ts` and read them in the shared projection (R6).

**Do NOT** fix this by swapping the id field in the binders — `latest_run_id_any_status` has no matching status field, so rows would render with no status. Necessary but not sufficient.

**Also correct in the same change:** `apps/frontend/src/features/activity/ActivityRoute.test.tsx:94` and `apps/desktop/renderer/destinationBinders.test.tsx:311` build fixtures with `latest_run_status: "completed"` — a response **no adapter can emit**. The green suite is evidence of a fixture/server contract mismatch, not of a working feed.

---

### R1 — the chip recipe (owner: **`packages/design-system/src/styles.css`**)

`.ui-pill` already exists at `:1145-1157` and its docblock claims to have collapsed five chip copies. `StatusPill.tsx` is the copy that got missed — **that is the architectural fix for HIGH-1**, not editing `pillStyle` in place.

But `.ui-pill` as shipped is a _filled selection_ chip and disagrees with the design's hairline mono outline. So:

1. Add a **tone-parameterised outline variant** to design-system — `.ui-pill--outline` + `.ui-pill--ok / --warn / --off` — using the `color-mix(in oklab, var(--color-success) 25%, transparent)` idiom **already used** by `.ui-pill--active` (`:1160`) and `.ui-chip--accent` (`:1183`). `--color-success #57c785` and `--color-warning #e8b45e` (`styles.css:191,194`) are byte-identical to the design's `--jade` / `--amber`, so no new colour tokens are needed.
2. Add `font-family: var(--font-mono)`, `font-weight: var(--font-weight-medium)`, `letter-spacing: var(--tracking-normal)`, no `text-transform`.
3. Rewrite `StatusPill.tsx:66-84` to emit `className` only — deleting `pillStyle` and the `PALETTE` triple. Tone→class stays in `statusTone.ts` (already the SSOT).
4. Fix casing at the **source**, `ActivityDestination.tsx:86-98` → lowercase labels, and drop `textTransform` (MEDIUM-5 needs both, or it still misses).

**Do NOT** keep the filled variant for Activity "because other surfaces use it" — add the variant, and let each call site choose.

---

### R2 — the type scale (owner: **`packages/design-system/src/styles.css:63-71`**)

`--font-size-2xs` (11.2px) collapses the design's **two** small steps. A canonical 10px mono step **already exists and is unused here**: `--font-size-mono-10: 0.625rem` (`:71`), documented as "canonical small-mono pill metadata … deliberately off the main ladder".

**Fix:** adopt `--font-size-mono-10` for the day divider (`dayDividerStyle`) and the chip/time (`.ui-pill--outline`, `Row.tsx` `metaStyle`); consider a `--font-size-2xs-alt` at 10.5px _or_ accept 10px for both (the design's 0.5px split is likely incidental). Fold the missing `line-height` (7 anchors, N2) into the same recipe change — inline style objects declare none anywhere.

This is a **design-system decision that moves every surface**, and it is the same root cause the FTUE-gate baseline recorded. It should be decided once, globally, not patched per-surface. **Independent of everything else — parallelize first.**

---

### R3 — the row primitive (owner: **`packages/chat-surface/src/destinations/_shared/Row.tsx`**)

Three defects live here and should land as one change:

- **Trailing slot** (HIGH-3): add `readonly trailing?: ReactNode` to `RowProps:32-53` and render it after `meta` in `:146-191`. `ActivityDestination` then passes `<Icon name="chevronRight" size={15}/>` on running rows and `<span style={{width:16}}/>` otherwise — **both, or the time column ragged-edges**. The icon already exists at `icons/paths.tsx:42,157` with zero call sites.
- **Icon tile** (HIGH-2): give `iconSlotStyle:70-79` `background: var(--color-surface-muted)`, `borderRadius: 7`, `display: grid`, and accept a `iconTone` prop so the jade tint colours the **tile**, not an inner span. Delete `liveIconStyle` (`ActivityDestination.tsx:675-680`).
- **Weight + padding** (MEDIUM-3/4): `titleStyle` 600 → `var(--font-weight-medium)`; `rowStyle` padding `10px 12px` → `11px 14px`.

Blast radius is real — Chats (`ChatsArchive.tsx:405`), Connectors and Skills all consume `Row` — which is exactly why it belongs in the primitive rather than in Activity.

**Hover (ACT-14)** also belongs here, and it forces an architectural choice: inline style objects **cannot** express `:hover`. Either introduce a small `row.css` in chat-surface, or add a `.ui-list-row` recipe to design-system (none exists — I checked the full `.ui-*` inventory). Prefer the design-system recipe: `Row` is the last major primitive still fully inline-styled.

---

### R4 — the run-open seam (owner: **`packages/chat-surface`**, fed by both hosts)

**Not** "copy the web code into desktop" — the web code is _also_ wrong.

The type seam is `apps/desktop/renderer/DestinationOutlet.tsx:84`, which declares `onOpenRun?: () => void`. Widen it to `(runId: RunId) => void`, matching `ActivityDestination.tsx:232` and the web route's `ActivityRoute.tsx:48`. Then fix the **shared** defect: a run id must not be fed into a conversation slot (`App.tsx:822-828` → `:1022` → `RunRoute.tsx:359-360`).

Two architecturally clean options:

- **(a)** Use the deep-link seam that already exists and is unused: `RunDestination.tsx:205` + `useRunSession.ts:174,223-231` accept a `runId`. Route `activity → run` through _that_, not through `subPath`.
- **(b)** Resolve run→conversation before navigating. The primitive is already in-repo: `apps/frontend/src/features/run/RunRoute.tsx:288-309` does exactly this via `GET /v1/agent/runs/{run_id}` for the MCP-OAuth resume path. Lift it into chat-surface as a shared helper so both hosts call one implementation.

Prefer **(a)** — it needs no extra round-trip and the seam is already designed for it. Add a test that asserts the cockpit **binds the clicked run**; today neither host has one (both stop at the callback boundary).

Also fix the non-live path in the same change: on web `ItemLink`'s `{kind:"run"}` route falls through `apps/frontend/src/app/HashRouter.ts:258-261` to **`/settings`**; on desktop `bootstrap.tsx:224-236` ignores non-conversation route kinds.

---

### R5 — shell registry + rail-badge port (owner: **`packages/chat-surface/src/shell/`**)

**Subtitle (HIGH-4).** Do **not** add a `SUBTITLE_BY_SLUG` map to `Topbar.tsx`. `TITLE_BY_SLUG` (`:34`) is _derived_ from `DESTINATION_REGISTRY` in `destinations.ts:75-93`, whose header states it is "the ONLY place a slug's label lives". Add `readonly subtitle?: string` to `DestinationMeta:63-72`, set `activity: { label: "Activity", subtitle: "every action the agent has taken" }`, and have `resolveSubtitle` (`Topbar.tsx:74-79`) fall back to the registry when `leaf` is null. One change, all six destinations, both hosts, zero host edits.

**Rail badge (ACT-15).** `apps/frontend/src/features/activity/useActiveRunCount.ts` lives in the **web app** and has no desktop twin — that is the architectural error. Move it into `packages/chat-surface` as an **`ActiveRunCountPort`** (or a hook over the existing transport), and have `ChatShell` feed `AppRail.badges` itself. Both hosts then get the badge by mounting the shell. Copying 4 lines into `bootstrap.tsx:318-331` would work today and re-break at the next prop.

While there: desktop also omits `railIdentity` and `walletChip` — same systematic under-feeding, same seam. And delete the stale comment at `App.tsx:1215-1217` that calls the badge unwired seven lines above the line that wires it.

**Accuracy caveat that survives the fix on both hosts:** the count is derived client-side from a 100-row conversation page (`useActiveRunCount.ts:38`) and is one-per-_conversation_ on an independent 30s poll (`:18,:52`). It undercounts past 100 conversations, undercounts concurrent runs in one conversation, and can visibly disagree with the frozen list (ACT-24). Fold a real count into R7.

---

### R6 — de-duplicate the host projection (owner: **`packages/chat-surface`**)

`auditLabel` / `buildMetaIndex` / `projectActivityRows` are **duplicated verbatim** between `apps/frontend/src/features/activity/api/activityApi.ts:56-224` and `apps/desktop/renderer/destinationBinders.tsx:241-349`. Same `mapRunStatus`, same skip rule, same `.catch(() => [])`, same key mismatch. Every fix above has to be applied twice, and ACT-12's host divergence is what happens when they drift.

Move the projection into `packages/chat-surface/src/destinations/activity/projectActivity.ts`, taking a transport port. Both hosts then supply only identity + transport. This is the same SSOT pattern `packages/chat-surface/CLAUDE.md` mandates for the surfaces themselves.

Do this **before** R0's field additions, or you will write the new field-reading twice.

---

### R7 — the real `GET /v1/activity` (owner: **facade contract + ai-backend**)

`activityApi.ts:80-81` already names this endpoint as the intended home. It is the only architectural answer to ACT-10, and it subsumes the pagination and count ceilings.

Four things must land together, because ACT-10 has **four independent blockers** and fixing any one alone changes nothing:

1. **Call the dead emitter.** `emit_tool_call_outcome` (`services/ai-backend/src/runtime_worker/audit.py:194`) is the sole writer of `metadata.tool_name` and has **zero production call sites** — only `tests/unit/runtime_worker/test_worker_audit.py:204,226,280`. Invoke it from the tool middleware.
2. **Put `run_id` on the wire.** It is persisted (`audit.py:290`, selected `postgres/runtime_api_store.py:3186`) but excluded from the `extra="forbid"` response model (`runtime_api/http/audit_list_routes.py:38-56`, `:109-134`) and from `AuditEvent` in `packages/api-types/src/index.ts:3736-3752`.
3. **Re-key the index on `run_id`,** not `resource_id` — the current key is structurally always-miss (`activityApi.ts:115`).
4. **Project step / approval counters server-side.** `ActivityRunRow` (`packages/api-types/src/activity.ts:66-80`) has five fields; the design's "7 steps" and "stopped — you rejected 2 of 6 payouts" have no backing field at any layer.

Build it as a **run keyset**, not a conversation page. The keyset codec already exists in the same service (`conversation_query_service.py:41-64`, used for messages at `:221-258`), and the audit half is already cursor-paginated end-to-end (`audit_routes.py:51-76`) with three shipped consumers (`AuditLogSettings.tsx:254-262`, `ReadAuditTab.tsx:145-152`, `ToolInvocationsTable.tsx:238`). This is **unwired capability, not missing capability**.

**Also fix the silent-degradation posture** (MEDIUM-10) here: the facade already returns `degraded_streams` (`audit_routes.py:189`, mirrored `api-types/src/index.ts:3775`) and **neither binder reads it**. A partially-degraded audit read is currently indistinguishable from "this run touched nothing". Surface it; do not `.catch(() => [])`.

---

### Do NOT change (deliberate divergence)

| Item                                                    | Why                                                                                                                                                       |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `row.live` borderColor / borderWidth                    | Measurement artefact — the hairline moved to the `<li>` (`RowList.tsx:38-43`) and is drawn identically. Zero pixel consequence (N1)                       |
| `page.container` margin `0 → 0 110px`                   | Harness frame arithmetic; both sides compute `width: 960px` (N1)                                                                                          |
| `div→h2`, `div→ul`, `button→div`, `h1→span`, `a→button` | Deliberate: `Row` is a `role="button"` div so nested `ItemLink`s compose (`Row.tsx:13-16`); the retention control invokes a host callback, not a URL (N2) |
| Icon 18px vs design's rendered 15px                     | The design **intends** 18 (`copilot-app.jsx:28`); its own CSS (`copilot.css:290`) overrides it. Live honours author intent (N2)                           |
| Relative timestamps                                     | Specified by `docs/plan/desktop-redesign/phase-4/PRD.md:113,133`. Route to a **product decision**, not a fix ticket (MEDIUM-6)                            |
| `stopped` = muted, not red                              | `statusTone.ts:33-38` documents this as a deliberate correction, and it matches the design (`copilot.css:354-368` `chip--off` = `--mut2`)                 |
| Pointer cursor only on activatable rows                 | The design's mock gives 7 inert rows a pointer that does nothing. Live is **more** correct (ACT-14)                                                       |
| Read-only posture (no per-row actions)                  | Parity with the design, which reserves `.lrow__act` for Connectors (`copilot-app.jsx:56-62`) and points export/delete at Settings → Privacy               |
| Absence of filter / pagination / auto-refresh           | Parity — the design has none either (ACT-20/21/24). Do not "add value" here                                                                               |

### Needs a design decision, not a fix

`needs_input` (the folded-Inbox 5th state, `statusTone.ts:48` — whose own comment admits "design has no such state"); the `"No activity yet"` and `"Activity unavailable"` empty states; the loading skeleton and error/Retry treatment; the `"Untitled run"` and `"Earlier"` fallback strings. All are un-designed inventions with **no baseline to grade against**. Several are high-traffic (ACT-18's empty state is the default screen today).

---

## Confidence & limitations

**Confidence: HIGH** on everything cited. Every claim carries a `path:LINE` I opened. Feature-parity verdicts survived an explicit adversarial refutation pass, and ACT-06 was confirmed **empirically** (not by grep) via a passing render test at `tools/design-parity/lib/render-live-activity.test.tsx`, which resolves the ref through the production registry captured at import time.

**Could not measure:**

1. **4 of 5 live states.** The design has no loading / error / unavailable / empty branch. Those three live states are **unaudited, not passing**.
2. **Hover / focus / active.** `lib/extract-computed.js` reads resting `getComputedStyle` only. Design has `.lrow:hover{background:var(--panel2)}` (`copilot.css:287`); the live `Row` has none, and inline style objects cannot express `:hover` — so ACT-14 is an **untested gap**, not a measured finding.
3. **The `needs_input` chip.** No row in the design fixture, so its tone/label was never compared.
4. **`rail.badge`.** Out of this harness's scope (`AppRail` is not mounted here; sibling `surfaces/rail-badge/` owns it). Declared with `expectDivergence`, reports as INFO — that INFO row is **not** a claim that the live app lacks a run badge.
5. **Which roles hold `admin:audit_export`.** I did not trace grants, so I cannot say who would lose the meta line under `RBAC_MODE=enforce`.
6. **`out/FINDINGS.md`.** The harness blocks subagents from writing report `.md` files at that path; the measured artefacts (`anchors.json`, `design-default.json`, `live-default.json`, `report-default.md/json`) **are** on disk and are the evidence base for Part 1.

**Vendored design files contained no text addressed to the reader** — nothing injection-shaped to flag.
