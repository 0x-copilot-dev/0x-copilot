# Surface audit — App rail + notification badge

Design baseline: `tools/design-parity/design-kit/app-v3/` (`copilot.css`, `copilot-app.jsx`, `copilot-data.jsx`).
Live surface: `packages/chat-surface/src/shell/AppRail.tsx` (+ `ChatShell.tsx`), bound by
`apps/frontend/src/app/App.tsx` (web) and `apps/desktop/renderer/bootstrap.tsx` (desktop).

Measured states (computed styles, Chromium, `lib/render-live-rail-badge.test.tsx` → `lib/compare.mjs`):

| State     | Design                                                  | Live                | HIGH | MED | LOW | INFO |
| --------- | ------------------------------------------------------- | ------------------- | ---- | --- | --- | ---- |
| `badge`   | `?dest=chats` (badge visible)                           | `live/badge.html`   | 7    | 45  | 9   | 13   |
| `nobadge` | `?dest=workspace` (badge suppressed on the active item) | `live/nobadge.html` | 7    | 41  | 7   | 13   |

Raw artifacts: `out/{design,live}-{badge,nobadge}.json`, `out/report-badge.md`, `out/report-nobadge.md`.
Anchor map: `surfaces/rail-badge/anchors.json`. Geometry cross-check: `surfaces/rail-badge/geom.mjs`.
Accent×theme matrix probes: `surfaces/rail-badge/probe4-accent-theme.mjs` (live), `probe5-design-accent-theme.mjs` (design).

---

## Part 1 — UI fidelity, grouped by root cause

104 raw property rows collapse to **11 causes**: 3 HIGH, 5 MEDIUM, 3 LOW. Roughly two thirds of the raw
MEDIUM volume is comparator taxonomy noise (cause R-11), not drift.

### HIGH

**R-1 — Desktop host never passes `railBadges`; the Run badge is dead on the primary substrate.**
Anchors: `rail.badge`, `rail.badge.absence`, `rail.item.run`. Fix site: `apps/desktop/renderer/bootstrap.tsx:318`.
`AppRail` renders a badge only from a host-supplied prop — declaration `AppRail.tsx:169` (`badges`), read at
`AppRail.tsx:246` (`badges?.[d.slug] ?? 0`), gated at `:247` (`showBadge = count > 0 && !isActive`), emitted at
`:269-273`. `ChatShell` is a pure forwarder (`ChatShell.tsx:130 → :187 → :294 badges={railBadges}`). A repo-wide
grep for `railBadges` outside `packages/chat-surface` returns exactly one call site: `apps/frontend/src/app/App.tsx:1224`.
`apps/desktop/renderer/bootstrap.tsx:318-331` is the complete desktop `ChatShell` prop list — transport, router,
keyValueStore, presenceSignal, activeDestination, destinations, onNavigate, onOpenSettings, onOpenCommandPalette,
settingsActive — and `railBadges` is absent. The parity pixels do **not** show this, because the harness feeds
`railBadges={{run:1}}` deliberately (`lib/render-live-rail-badge.test.tsx:113`) to measure what the rail renders
_when fed_. The defect is wiring, found by reading it.
**Do not "fix" this via `BadgePort`** — that is an OS dock/tray contract, not the rail pill (see R-1b under blockers).

**R-2 — Desktop host never passes `railIdentity` either; the rail-foot avatar is a generic glyph.**
Anchors: `rail.me`, `rail.foot`. Fix site: `apps/desktop/renderer/bootstrap.tsx:318`.
`AppRail.tsx:300-306`: with `identity` absent the avatar renders `<Icon name="user" size={14}/>` instead of the
design's initial (`copilot-app.jsx:304` → `prefs.name.slice(0,1)`; `copilot.css:75`). This is a one-line omission,
not a missing feature — `apps/desktop/renderer/SettingsMount.tsx:212` already reads `session.displayName` off the
same `props.session` that `ChatShellForSession` receives (`bootstrap.tsx:142-147`). Web does it correctly at
`apps/frontend/src/app/App.tsx:1217-1221`.

**R-3 — The rail foot draws a hairline divider + 8px pad the design does not specify.**
Anchors: `rail.foot`, `rail.foot.settings`, `rail.me`. Fix site: `packages/chat-surface/src/shell/AppRail.tsx:194-202`.
`footStyle` (verified at `AppRail.tsx:194-202`) sets `gap: 6, paddingTop: 8, borderTop: "1px solid var(--color-border)",
width: BUTTON_SIZE`. The design's entire rule is `copilot.css:74`:
`.rail-foot{margin-top:auto;display:flex;flex-direction:column;align-items:center;gap:5px}` — no border, no padding,
no width. Measured in **both** states: `borderWidth 0px → 1px 0 0 0`, `padding 0px → 8px 0 0 0`, `height 65px → 75px`,
plus the `borderColor`/`borderStyle` rows that follow from it. In a 48px rail whose thesis is hairline economy, an
unspecified full-width rule above Settings is the most conspicuous difference on this surface.
Drop `paddingTop` + `borderTop`; `width: BUTTON_SIZE` is harmless.

### MEDIUM

**R-4 — The web Run badge counts _conversations_, not runs, because no run-collection endpoint exists.**
Anchor: `rail.badge`. Fix sites: `services/ai-backend/src/runtime_api/` (new `GET /v1/agent/runs?status=active`),
`services/backend-facade/src/backend_facade/app.py:929` (proxy), `apps/frontend/src/features/activity/useActiveRunCount.ts:38`.
`useActiveRunCount.ts` is a real data source — so the adjacent comment at `App.tsx:1215-1216` ("The Run badge
(activeRunCount) still needs a run-list source and is a documented follow-up") is **stale** and should be corrected.
But it is a proxy with four compromises, all read at `useActiveRunCount.ts:11-52`: it counts conversations whose
`latest_run_status` is active (`:38-46`), so two concurrent runs in one conversation count as one; `limit: 100` (`:38`)
silently truncates and the endpoint has no cursor; `POLL_MS = 30_000` (`:18`) means the badge lags a started or
finished run by up to 30s even though the app already streams run events; and `catch {}` (`:47`) keeps the last count,
so an expired session can leave a stale badge lit. Root cause is upstream: `backend_facade/app.py:929` registers
`POST /v1/agent/runs`, and every other run route is `{run_id}`-scoped (`:1038, :1054, :1069, :1114`). There is no GET
collection. Runs are ai-backend's domain; the client is compensating for a missing server projection.

**R-5 — `.rail-me` loses the design's 1px `--line2` ring, and an inline comment enshrines the mistake.**
Anchor: `rail.me`. Fix site: `packages/chat-surface/src/shell/AppRail.tsx:217` (and the comment at `:210-211`).
`AppRail.tsx:210-217` reads `// Design .rail-me sits on --panel3 …, no border (PRD-C — previously the too-dark
elevated bg + a stray hairline)` then `border: "none"`. The comment is factually wrong: `copilot.css:75` ends with
`border:1px solid var(--line2)` — the ring is specified, not stray. Measured `borderWidth 1px → 0px`,
`borderStyle solid → none`, `borderColor rgba(255,255,255,.1) → rgb(212,212,219)` (the live colour is just
`currentColor` through a zero-width border — the same non-event counted twice; the comparator ranks the colour row
HIGH). MEDIUM on visual weight (a 26px chip), but the comment matters: whoever fixes this next will read it and revert.

**R-6 — Brand→first-item spacing is 10px; the design's 12px is the sum of two rules the live rail collapsed into one.**
Anchors: `rail.container`, `rail.brand`, `rail.item.run`. Fix site: `packages/chat-surface/src/shell/AppRail.tsx:192`.
The design expresses the gap as `copilot.css:64` `.rail{…gap:2px}` **plus** `copilot.css:65` `.rail-brand{…margin-bottom:10px}`
= 12px. The live rail sets neither: `railStyle` (`AppRail.tsx:171-185`) has no `gap`, and the whole spacing is a single
`marginTop: 10` on the items wrapper (`AppRail.tsx:192`). `geom.mjs` measured brand→first-item: **12 design / 10 live**.
This one cause is why the comparator reported `rail.container gap 2px → normal` and `rail.brand margin 0 0 10px 0 → 0px`
as two separate rows. Fix: `marginTop: 12`.

**R-7 — Rail-foot child gap is 6px; design is 5px.**
Anchor: `rail.foot`. Fix site: `AppRail.tsx:198` (`gap: 6`) vs `copilot.css:74` (`gap:5px`). `geom.mjs` measured
Settings→avatar: 5 design / 6 live. Listed separately from R-3 only because it survives the border fix.

**R-8 — Inherited base font-size is 13.6px where the design is a literal 13px (global cause, merely witnessed here).**
Anchors: `shell.body.grid`, `rail.container`, `rail.foot`. Fix site: `packages/design-system/src/styles.css:377`.
`copilot.css:36` `body{…font-size:13px}` vs `styles.css:377` `font-size: var(--font-size-sm)` where `--font-size-sm`
is `0.85rem` = 13.6px (`styles.css:65`). **Rail impact is nil** — the rail has no inherited-size text (badge sets
8.5px at `AppRail.tsx:150`, avatar 11px at `:218`). Recorded because it is a shared cause: expect the same three rows
in the other four concurrent surface audits. Fix once at the token, never per-surface. `styles.css:361` already
carries a note acknowledging the design's 13px.

### LOW

**R-9 — `data-destination` is set on two nested elements, making the attribute selector ambiguous.**
Anchors: all six `rail.item.*`. Fix site: `packages/chat-surface/src/shell/ChatShell.tsx:284`.
`ChatShell.tsx:284` puts `data-destination={activeDestination}` on the shell **root**; `AppRail.tsx:255` puts
`data-destination={d.slug}` on every rail button. `document.querySelector('[data-destination="chats"]')` therefore
returns the 1220×800 shell `<div>`, not the 34×34 button. This bit this audit's first extraction run (chats anchor
came back `tag <button> → <div>`, `width 34px → 1220px`, `flexGrow 0 → 1`) and will bite any CSS rule or e2e selector
written against it. Fix: rename the shell-root hook to `data-active-destination`, or require consumers to scope under
`nav[data-component="app-rail"]` as `anchors.json` now does.

**R-10 — Two brand marks disagree: the v3 mock's gradient ring vs the shipped brand asset's dot-in-disc.**
Anchor: `rail.brand.mark`. Fix site: `packages/chat-surface/src/shell/BrandMark.tsx:45-46` — a decision, not a patch.
Design `copilot-data.jsx:22` hub is `<circle r=20 fill="#0b0a0e" stroke="url(#grad)" strokeWidth="10">` (a gradient
**ring**) with a diagonal gradient (`x2=1 y2=1`). Live `BrandMark.tsx:45-46` is `<circle r=30 fill="#0d0c10"/>` +
`<circle r=15 fill="#5fb2ec"/>` (solid dot in a dark disc) with a vertical gradient (`x2=0 y2=1`). Computed styles
cannot see this — found by reading both SVGs. The live mark matches `apps/website/public/favicon.svg` byte-for-byte
and `BrandMark.tsx:3-10` declares that asset the single source, so the **mock** is the older glyph. Pick one canonical
mark; do not silently "fix" the rail toward the mock while the website ships the other.

**R-11 — Comparator taxonomy noise: ~30 of the 45 MEDIUM rows and 4 of the 7 HIGH rows render identically.**
Fix site: `tools/design-parity/lib/compare.mjs:113-124` (`classify`: LAYOUT/BOX rules). Recorded so the next run
suppresses rather than re-triages:

- (a) `display:grid;place-items:center` (design) vs `display:flex;align-items:center;justify-content:center`
  (`railButtonStyle`, `AppRail.tsx:109-125`) — 11 anchors × 2 rows, identical rendering.
- (b) `padding 1px 6px → 0px` on every design button is the UA-default `<button>` padding the mock never resets,
  inside a fixed 34×34 border-box — zero visual effect.
- (c) `border-radius 50% → 999px` on `.rail-me` — both circles at 26px.
- (d) `rail.foot margin 455px → 0px` — design pins with `margin-top:auto` (`copilot.css:74`), live with `flex:1` on
  the items wrapper (`AppRail.tsx:191`); `geom.mjs` proves equivalence (last-item→Settings 457 design / 458 live,
  rail-bottom→avatar 10/10), refuting the anchor inventory's "fixed spacer" worry.
- (e) the four HIGH `--tx → --accent` rows on `rail.brand` / `rail.brand.mark` come from `AppRail.tsx:238`
  `color: var(--color-accent)`, but neither mark consumes `currentColor` (both fill from a gradient) — inert; and
  `borderColor` follows `color` through a zero-width border, so it is the same non-event twice.
- (f) `shell.body.grid backgroundColor transparent → rgb(9,9,11)` — the mock's `.mw-body` is transparent over `.mw`'s
  `--ink` `#09090b`; the live shell sets `var(--color-bg)` = `#09090b` (`styles.css:168`) explicitly. Same pixel.

---

## Part 2 — Feature parity

22 features (RAIL-01…RAIL-22) were traced through five layers. Four went to adversarial refutation; **three survived
intact, one was corrected** (its claimed gap was refuted and a different, real gap found in its place). All other
features are FULLY_WIRED with no surviving gap.

| Feature                                                 | Design                                                                                           | Live UI                                                                                                                  | Web host                                                                                                                                                    | Desktop host                                                                                                                                                                                             | Facade                                                                                                                                                       | Backend                                                                                                                         | Verdict                                                                                                                                                                                                                                                                                                                                                                                                |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| RAIL-08 Run badge                                       | `copilot.css:73` `.rbadge`, `copilot-app.jsx:299`                                                | REAL — `AppRail.tsx:144-161` style, `:246-247` gate, `:269-273` render; test `AppRail.test.tsx:266-301`                  | WIRED, live data — `App.tsx:520` + `:1224-1226` ← `useActiveRunCount.ts:11-52`                                                                              | **NONE** — `bootstrap.tsx:318-331` omits `railBadges`; zero `AppRail`/`rail-btn`/`rbadge` hits in `apps/desktop`                                                                                         | `app.py:410-431` GET `/v1/agent/conversations` (real proxy); **no run-collection route** (`:929` POST-only; `:1038/:1054/:1069/:1114` are `{run_id}`-scoped) | REAL — `conversation_query_service.py:434-453` `_with_latest_run` → `postgres/runtime_api_store.py:1347-1369` tenant-scoped SQL | **HOST_GAP (desktop) — CONFIRMED**                                                                                                                                                                                                                                                                                                                                                                     |
| RAIL-14 Settings outside DEST                           | `copilot-app.jsx:303`, `copilot.css:71-72`                                                       | Button present outside the map — `AppRail.tsx:277-289`; but `:286` hard-codes `railButtonStyle(BUTTON_SIZE, false)`      | Opens at `DEFAULT_SETTINGS_SECTION` (`App.tsx:1207-1212`); **never passes `settingsActive`** (grep: only `ChatShell.tsx` + `bootstrap.tsx:195,330,332`)     | `bootstrap.tsx:326-327` `handleOpenSettings()` → `:269` `setSettingsSection(section ?? null)`; passes `settingsActive` at `:330`                                                                         | n/a (client routing)                                                                                                                                         | n/a                                                                                                                             | **HOST_GAP — CORRECTED.** Real gaps: (1) both hosts — the rail can never show a Settings active state; on web it shows the _wrong_ one (`App.tsx:739-740` collapses to `ROOT_DESTINATION` = run). (2) web-only — Settings renders **with** topbar + 224px context column because `settingsActive` is never passed, vs full-bleed on desktop (`ChatShell.tsx:237`)                                      |
| RAIL-14 "web loses last-open section, desktop keeps it" | `copilot-app.jsx:256,303` `navigate("settings", setSec)` — itself session `useState` (`:2,246`)  | —                                                                                                                        | `App.tsx:1209-1210` resets to `DEFAULT_SETTINGS_SECTION`                                                                                                    | `bootstrap.tsx:327` calls `handleOpenSettings()` with **no arg** → `:269` `?? null` → `SettingsSurface.tsx:266,271-274` → `settingsNav.ts:269-274` → `DEFAULT_SETTINGS_SLUG`                             | n/a                                                                                                                                                          | n/a                                                                                                                             | **NOT A GAP — REFUTED.** Both hosts reset identically. No persistence layer is implicated                                                                                                                                                                                                                                                                                                              |
| RAIL-15 Identity chip                                   | `copilot-app.jsx:304`, `copilot.css:75`                                                          | REAL — `AppRail.tsx:291-307`, initial at `:300-303`, glyph fallback at `:305`; tests `AppRail.test.tsx:304-316, 318-330` | WIRED — `App.tsx:1217-1221` ← `App.tsx:517` `useUserProfile()`                                                                                              | **NONE** — `bootstrap.tsx:318-331` omits `railIdentity`; only desktop test (`bootstrap.test.tsx:223`) asserts element presence, not content                                                              | `me_routes.py:54-56` GET `/v1/me/profile` (real)                                                                                                             | REAL — `me_profile.py:243-271` → `PostgresMeStore` (`me_store.py:164`)                                                          | **HOST_GAP (desktop) — CONFIRMED.** Data one prop away: `RendererSession.displayName` (`rpc-protocol.ts:140-148`, populated `main/auth/index.ts:552`), already in scope at `bootstrap.tsx:142-147` — but **nullable**, so the binding needs web's `.trim()` guard                                                                                                                                      |
| RAIL-15 tooltip + case                                  | `copilot-app.jsx:304` `title={prefs.name}`, `.slice(0,1)` un-uppercased                          | Live hardcodes `title="Account"` (`AppRail.tsx:292-297`) and force-uppercases (`:302`)                                   | both hosts                                                                                                                                                  | both hosts                                                                                                                                                                                               | n/a                                                                                                                                                          | n/a                                                                                                                             | **MED parity drift, BOTH hosts**                                                                                                                                                                                                                                                                                                                                                                       |
| RAIL-16 Accent/theme recolor                            | `copilot.css:24-30` — `[data-theme="light"]` overrides only `--accent-ink`, **keeps `--accent`** | Token-clean, zero hex literals: `AppRail.tsx:62,119-122,139,152-153,177-178,212-214`                                     | `AppearanceContext.tsx` (density/reduce-motion) + design-system `ThemeProvider` (`index.tsx:92,97`) writes theme/accent; persisted via `/v1/me/preferences` | Apply-on-change only — `SettingsMount.tsx:134-139` default `sky`, `:252-259` `applyAppearance`, called solely from `:943-951`; no mount read; `renderer/index.html` stamps no `data-theme`/`data-accent` | `me_routes.py:90-96` GET/PUT `/v1/me/preferences` (real)                                                                                                     | REAL + validated — `me_preferences.py:80-113,343-368` → `PostgresMeStore`                                                       | **PARTIAL — CONFIRMED broken.** `styles.css:307` (light) and `:335` (slate) redefine `--color-accent` **after** the `:root[data-accent=…]` blocks at `:245-288` at equal specificity → later wins. Measured: **9 distinct badge colours in dark, 1 in light, 1 in slate** (`probe4`), vs **4/4 in both** for the design (`probe5`). Plus: desktop never persists accent (resets to `sky` every launch) |
| RAIL-01…07, 09…13, 17…22                                | —                                                                                                | —                                                                                                                        | —                                                                                                                                                           | —                                                                                                                                                                                                        | —                                                                                                                                                            | —                                                                                                                               | **FULLY_WIRED**, no surviving gap. Notable net-new-over-design (not drift): badge `aria-hidden` + count folded into the button name (`AppRail.tsx:253,270`), `aria-current="page"` (`:254`), `99+` overflow clamp (`:271`), clickable avatar (`:296`)                                                                                                                                                  |

**Explicitly refuted — do not reintroduce:**

- "Desktop remembers the last-open Settings section, web does not." Both reset. `bootstrap.tsx:327` + `:269`.
- "A hard-coded `#5fb2ec` breaks rail token discipline." It does not exist in the rail. (It _does_ exist at
  `apps/desktop/renderer/BootProgress.css:9-10`, a different surface.)
- "`BadgePort` is the desktop path for the Run badge." It is not — see blockers.
- "The rail foot uses a fixed spacer." It uses `flex:1` on the items wrapper; `geom.mjs` proves equivalence to
  `margin-top:auto`.

---

## Part 3 — Remediation. No bandaids.

Ordered by (user-visible impact × blast radius). **[P]** = independently parallelizable.

### A1 — Lift active-run count into a chat-surface port, and give it a real server source. _(HIGH impact, wide blast radius)_

Covers R-1, R-4, RAIL-08, RAIL-09, RAIL-10.

The wrong fix is to copy `apps/frontend/src/features/activity/useActiveRunCount.ts` into `bootstrap.tsx`. That
duplicates a derivation _and_ a polling policy across two hosts, which is exactly the drift this monorepo's
chat-surface SSOT rule exists to prevent.

**Owning layers, in order:**

1. **Backend (`services/ai-backend/src/runtime_api/http/routes.py`)** owns the number. Add
   `GET /v1/agent/runs?status=active` (or `/v1/agent/runs/active_count`) — one indexed tenant-scoped `COUNT` over
   `agent_runs`, mirroring the status set already canonical at `runtime_adapters/postgres/runtime_api_store.py:1347-1369`
   (`queued, running, waiting_for_approval, cancelling`) and duplicated identically in the in-memory and file adapters.
   Today `routes.py:634-639` is POST-only. This also removes the N+1 that `conversation_query_service.py:201-205`
   currently pays (~3 store calls × 100 rows every 30s to compute one integer).
2. **Facade (`services/backend-facade/src/backend_facade/app.py`, alongside `:929`)** proxies it with
   `identity.scoped_params`, exactly as `:410-431` already does for conversations.
3. **chat-surface** owns the seam. The rail already consumes a prop, so the correct shape is a small
   `ActiveRunCountPort` next to `packages/chat-surface/src/ports/BadgePort.ts`, or — cheaper and preferable — a
   `useActiveRunCount()` hook inside `packages/chat-surface/src/shell/` reading the existing `Transport` port both
   hosts already pass (`bootstrap.tsx:319`, `App.tsx`). Then `ChatShell` can default `railBadges` from it, and neither
   host has to remember to wire anything. **That default is the architectural fix**: it makes the desktop gap
   structurally impossible rather than fixed-once.
4. Event-drive it. The app already streams run events (`app.py:1069` `/{run_id}/stream`); a count that polls every 30s
   next to a live SSE pipeline is an avoidable inconsistency. The existing `/v1/inbox/stream` + `/v1/inbox/unread_count`
   pair (`services/backend/src/backend_app/inbox/sse.py:515`, `inbox_routes.py:64-77`) is the right shape to copy —
   note the irony that the system's only purpose-built badge-count route + push channel today serve `inbox`, a slug
   that is **not in the rendered rail on either profile** (`destinations.ts:113-129`).

Also delete the stale comment at `apps/frontend/src/app/App.tsx:1215-1216`.

### A2 — Bind `railIdentity` on desktop through the same seam. _(HIGH impact, tiny blast radius)_ **[P]**

Covers R-2, RAIL-15. Immediate: `apps/desktop/renderer/bootstrap.tsx:318` gains
`railIdentity={props.session.displayName?.trim() ? { initial: props.session.displayName.trim().charAt(0) } : undefined}`
(the `.trim()` guard is required — `RendererSession.displayName` is nullable per `rpc-protocol.ts:140-148`).
**Architecturally**, the initial-derivation belongs in chat-surface next to A1's hook so it is written once; the host
supplies a display name, not a pre-sliced initial. While there: the design's tooltip is the user's full name
(`copilot-app.jsx:304`), not `"Account"` (`AppRail.tsx:292-297`), and the design does not uppercase (`AppRail.tsx:302`).

### A3 — Fix the CSS cascade order in the design system so accents survive light/slate. _(HIGH impact, app-wide)_ **[P]**

Covers RAIL-16. This is not a rail bug and must not be patched in the rail. `packages/design-system/src/styles.css:243-244`
states the intent — "layered on top of the scheme so a swatch override wins regardless of theme" — and the file's own
source order defeats it: `:root[data-accent="…"]` at `:245-288` (specificity 0-2-0) is followed by
`:root[data-theme="light"]` at `:291` (also 0-2-0) redefining `--color-accent` at `:307`, and `:root[data-theme="slate"]`
at `:324` redefining it at `:335`. Two options, both architectural:

- move the accent blocks **below** the theme blocks (restores the file's stated invariant), or
- drop `--color-accent` / `-strong` from the light and slate blocks entirely and derive light-mode legibility from
  `--color-accent-contrast` only — which is precisely what the design does (`copilot.css:24-30` overrides `--accent-ink`
  and leaves `--accent` alone).
  Then add the regression test that does not exist today: no test anywhere asserts a _colour_ survives a theme change
  (`AppearancePage.test.tsx:88,172` assert only that the attribute is emitted).

### A4 — Give desktop appearance persistence through the existing chat-surface seam. _(MED impact, medium radius)_ **[P]**

Covers RAIL-16 (second half), RAIL-17. `packages/chat-surface/src/settings/index.ts:32` already exports
`splitAppearancePersistence`, unit-tested at `AppearancePage.test.tsx:204-209`, with **zero host call sites**. The seam
exists; wire desktop to it and to the real, validated `GET/PUT /v1/me/preferences`
(`me_routes.py:90-96` → `me_preferences.py:80-113,343-368` → `PostgresMeStore`). Do not add a second local store.

### A5 — Thread `settingsActive` to the rail, and make web use it. _(MED impact, small radius)_

Covers RAIL-14. Two changes in one seam:

- `packages/chat-surface/src/shell/ChatShell.tsx:288-295` already holds `settingsActive` (`:92, :205, :237`) and simply
  does not forward it. Add it to `AppRailProps` (`AppRail.tsx:66-104`) and replace the hard-coded `false` at
  `AppRail.tsx:286`. The design styles this item (`copilot.css:71-72`: `--panel2` tint + a 2px accent bar), so it is
  parity work, not a new feature.
- `apps/frontend/src/app/App.tsx` must actually _pass_ `settingsActive` — today it never does, which is why Settings
  renders with the topbar and the 224px context column on web but full-bleed on desktop (`ChatShell.tsx:237`). Fixing
  the flag fixes both symptoms. Related: `App.tsx:739-740` collapses `activeDestination` to `ROOT_DESTINATION` on the
  settings screen, so the rail currently highlights **Run** while the user is in Settings — actively misleading.

### A6 — Rail CSS corrections, all in `AppRail.tsx`. _(MED impact, zero radius)_ **[P]**

Covers R-3, R-5, R-6, R-7. Four edits, no new abstraction warranted:
`:199-200` drop `paddingTop: 8` and `borderTop`; `:198` `gap: 6 → 5`; `:192` `marginTop: 10 → 12`; `:217`
`border: "none"` → `1px solid var(--color-border-subtle)` (the token whose value matches `--line2`), and **rewrite the
comment at `:210-211`, which asserts something `copilot.css:75` contradicts.** No design-system recipe covers a 48px
icon rail today; do not invent one for four literals.

### A7 — Disambiguate `data-destination`. _(LOW impact, wide radius — do it before more selectors accrete)_ **[P]**

Covers R-9. `ChatShell.tsx:284` should emit `data-active-destination` on the shell root, leaving `data-destination`
(`AppRail.tsx:255`) unambiguous for CSS, e2e and parity harnesses.

### A8 — Fix the base font-size token once, globally. _(LOW impact on this surface, app-wide radius)_

Covers R-8. `packages/design-system/src/styles.css:377` resolves to 13.6px against the design's literal 13px
(`copilot.css:36`), and `styles.css:361` already acknowledges it. Zero rail impact — **coordinate with the other four
concurrent surface audits and change it once**, not five times.

### A9 — Decide the canonical brand mark. _(LOW impact, brand-wide)_

Covers R-10. A product decision, not a patch: `BrandMark.tsx:45-46` matches `apps/website/public/favicon.svg` and
`BrandMark.tsx:3-10` declares that asset the single source, while `copilot-data.jsx:22` is an older gradient-ring
glyph. Update the design kit, not the code, unless the ring is intentionally the new mark.

### A10 — Suppress comparator taxonomy noise. _(harness hygiene)_ **[P]**

Covers R-11. `tools/design-parity/lib/compare.mjs:113-124` should treat `grid+place-items:center` ≡
`flex+center/center`, ignore UA-default `<button>` padding inside a fixed border-box, normalize `50%` ≡ `999px` on
equal-sided boxes, and skip `borderColor` when `borderWidth` is 0 on both sides. That removes ~30 MEDIUM and 4 HIGH
rows of pure re-triage cost per run, across all five surfaces.

### Do NOT change

- **`flex:1` instead of `margin-top:auto` for the foot pin** (`AppRail.tsx:191`). `geom.mjs` proves the rendered result
  is identical (457/458px, 10/10px); the comparator's `margin 455px → 0px` row is an artifact.
- **The active-bar as a real DOM element** (`AppRail.tsx:261-267`) rather than the design's `::before`. It is
  anchorable, and slightly more visible than the mock's clipped ~0.5px — an improvement.
- **The a11y additions**: badge `aria-hidden` + count in the button's accessible name (`AppRail.tsx:253,270`),
  `aria-current="page"` (`:254`), `nav aria-label` (`:224`), and the `99+` clamp (`:271`). All net-new over the design.
- **The clickable avatar** (`AppRail.tsx:296` → `onOpenSettings`) where the design's `.rail-me` is inert.
- **`width: BUTTON_SIZE` on the foot** — harmless once the border is gone.
- **`120ms ease` literals** (`AppRail.tsx:52-54`) — value-identical to `--duration-fast`/`--ease-standard`
  (`styles.css:117-119`); tokenizing is optional polish, not a defect.

### Parallelization

A2, A3, A4, A6, A7, A10 are mutually independent. A1 is the long pole (backend + facade + package + two hosts) and
should start first. A5 touches `ChatShell` props, so land it before or after A1's `ChatShell` change, not concurrently.
A8 must be coordinated across the five concurrent surface audits.

---

## Confidence and limits

**High confidence** on: every measured style row (Chromium computed styles, both states, two independent
extraction runs); the accent×theme collapse (matrix probes on both sides, `probe4`/`probe5`); every host-binding
claim (each grep re-run and each cited file opened); the RAIL-14 refutation (traced
`bootstrap.tsx:327 → :269 → SettingsSurface.tsx:266,271-274 → settingsNav.ts:269-274`).

**Could not measure:**

1. **No desktop-rendered rail.** `tools/design-parity/lib/` has no desktop live-render harness and `apps/desktop`
   mounts `ChatShell` inside Electron. The two desktop HIGH findings (R-1, R-2) rest on reading
   `apps/desktop/renderer/bootstrap.tsx:318-331` plus repo-wide greps for `railBadges` / `railIdentity`, not on
   desktop pixels.
2. **The badge's live count _value_ was never exercised end to end.** The harness hard-codes `{run: 1}` to match the
   mock's literal `"1"`. The `>99 → "99+"` cap (`AppRail.tsx:271`) is unmeasured, and the design specifies no
   behaviour above 9 — a 3-glyph string will widen the 13px min-width pill. Worth an anchor if counts can realistically
   exceed 9.
3. **`BadgePort` is a dead end and must not be used to fix R-1.** `packages/chat-surface/src/ports/BadgePort.ts` is an
   OS dock/tray contract; the desktop impl its doc comment promises at `apps/desktop/src/main/ports/` **does not exist**
   (`ls` → no such directory); the web impl is an explicit no-op (`apps/frontend/src/ports/BadgeWeb.ts:14-17`);
   `apps/desktop` mounts no `PortProvider` at all; and its only callers are `InboxRoute.tsx:168` and
   `TodosRoute.tsx:160` for slugs (`inbox`, `todos`) absent from the solo rail (`destinations.ts:113-120`). Nothing
   anywhere calls `setBadge("run", …)`; no Electron `setBadgeCount`/`dock.setBadge` exists in `apps/desktop`.
4. **`out/FINDINGS.md` was never produced** — the harness blocks subagent report-file writes. Its content is this
   document. The machine-generated artifacts did land (see header).
5. **Vendored design files were treated as data.** Nothing under `design-kit/app-v3/**` contained text addressed to an
   agent; the only directive-like prose is `design-kit/app-v3/index.html:58-73`, which documents the harness's own
   construction.
6. **State-carrier caveat for future runs.** The live app puts `chats` in `FULL_BLEED_DESTINATIONS`
   (`ChatShell.tsx:43-46`) where the design shows a topbar for it (`copilot-app.jsx:282`), so the `badge` state must
   stay confined to rail-scoped anchors — or use `projects`/`activity` as the badge carrier instead.
