# PRD-12 — Rail and Settings: badge source, identity, active state, pane chrome, appearance boot

> **Reconciled against the normative program index** (`README.md`) — C1, C2, C14, C18, the
> migration table, the corrected wave order (Wave 3: PRD-09 → PRD-11 ‖ PRD-12) and gap **G7**
> (desktop appearance persistence, previously owned by no PRD, now owned here as D9).

## Problem

Five things the user sees on every screen, all wrong in different ways.

1. **The Run badge lies.** It shows the number of _conversations_ whose most recent run
   is in flight — so two runs in one conversation read as "1", and a run started 5
   seconds ago does not appear for up to 30 seconds. If the user has more than 100
   conversations, runs on the 101st are invisible. If their session expires, the badge
   freezes on its last value and keeps glowing forever.
2. **Opening Settings highlights the wrong thing.** On web, clicking the gear leaves the
   rail highlighting **Run** — a destination the user is not on — and the gear itself
   never lights up, on either host. The design highlights the gear.
3. **Settings is a different screen on each host.** Same component, same package: on
   desktop it is full-height; on web it renders squeezed inside a top bar and a 224px
   context column that has nothing to do with Settings.
4. **The rail has furniture the design does not.** A full-width hairline rule and 8px of
   pad sit above the gear — in a 48px rail whose whole thesis is hairline economy, it is
   the loudest thing on the surface. The avatar has lost its ring, the brand sits 2px
   too close to the first item, and its tooltip says "Account" instead of the user's
   name.
5. **Desktop forgets how the app looks, every launch** (README G7 / rail-badge `AUDIT.md:229`).
   The Appearance section paints `data-theme`/`data-accent`/`data-density` on change and
   writes them nowhere — no `Transport` PUT, no `KeyValueStore` write — and nothing reads
   them at boot, so every launch snaps back to `theme:"system"` + `accent:"sky"`. The
   package already exports the classifier built for exactly this
   (`splitAppearancePersistence`) and it has **zero call sites in any host**. This is not
   only cosmetic: desktop mounts no design-system `ThemeProvider` (it is denied by
   `apps/desktop/main/adapters/ast-allowlist.ts:18`), so the `:root[data-accent]` attribute
   _is_ the desktop theming mechanism — PRD-01's nine restored accents
   (`styles.css:245-288`) are unobservable on the primary substrate until this lands.

## Evidence

Every row opened and re-verified against working-tree HEAD (`claude/design-parity-audit-7ec82a`).

| Claim                                                                            | File:line                                                                                                                                                                                                                        | What the code actually does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Web counts conversations, not runs                                               | `apps/frontend/src/features/activity/useActiveRunCount.ts:38-46`                                                                                                                                                                 | CONFIRMED. `listConversations(identity, { limit: 100 })` then `.filter(c => ACTIVE_RUN_STATUSES.has(c.latest_run_status)).length`. One conversation contributes at most 1.                                                                                                                                                                                                                                                                                                                                      |
| It polls every 30s                                                               | `useActiveRunCount.ts:18,52`                                                                                                                                                                                                     | CONFIRMED. `POLL_MS = 30_000`; `window.setInterval`. No visibility gate — it polls a hidden tab.                                                                                                                                                                                                                                                                                                                                                                                                                |
| A failure keeps a stale badge lit                                                | `useActiveRunCount.ts:47-49`                                                                                                                                                                                                     | CONFIRMED. Bare `catch {}` with the comment "keep the last known count". A 401 after session expiry is indistinguishable from a network blip.                                                                                                                                                                                                                                                                                                                                                                   |
| No run-collection endpoint exists                                                | `services/backend-facade/src/backend_facade/app.py:929,1054,1069,1114`                                                                                                                                                           | CONFIRMED. `POST /v1/agent/runs` only; every other run route is `{run_id}`-scoped. Same in ai-backend: `runtime_api/http/routes.py:633-638` registers `/runs` POST, `:640-646` registers `GET /runs/{run_id}`.                                                                                                                                                                                                                                                                                                  |
| `App.tsx` carries a stale comment 2 lines above the code that disproves it       | `apps/frontend/src/app/App.tsx:1214-1216`                                                                                                                                                                                        | CONFIRMED, exact text: "The Run badge (activeRunCount) still needs a run-list source and is a documented follow-up" — immediately above `railBadges={activeRunCount > 0 ? …}` at `:1224-1226`, fed by `:520`.                                                                                                                                                                                                                                                                                                   |
| `>99 → "99+"` is unmeasured and the design has no rule above 9                   | `packages/chat-surface/src/shell/AppRail.tsx:271`; `design-kit/app-v3/copilot.css:343-358`                                                                                                                                       | CONFIRMED. `{count > 99 ? "99+" : count}`. The design's `.rbadge` is `min-width:13px; height:13px; padding:0 3px; font-size:8.5px` — a 3-glyph string makes a ~21px stadium out of a 13px circle. The mock only ever renders `"1"` (`copilot-app.jsx:796`).                                                                                                                                                                                                                                                     |
| `BadgePort` is a dead end                                                        | `packages/chat-surface/src/ports/BadgePort.ts:6-8`; `apps/frontend/src/ports/BadgeWeb.ts:15`                                                                                                                                     | CONFIRMED. The doc comment promises an impl at `apps/desktop/src/main/ports/` — `ls` says no such directory. Web impl is an explicit no-op. Only callers: `InboxRoute.tsx:168`, `TodosRoute.tsx:160`. Neither `inbox` nor `todos` is in `SOLO_ORDER`/`TEAM_ORDER` (`destinations.ts:115-129`). Nothing calls `setBadge("run", …)` anywhere.                                                                                                                                                                     |
| Settings rail item hard-codes inactive                                           | `AppRail.tsx:286`                                                                                                                                                                                                                | CONFIRMED. `style={railButtonStyle(BUTTON_SIZE, false)}` — literal `false`, no `data-state`, no `aria-current`, no active bar.                                                                                                                                                                                                                                                                                                                                                                                  |
| `AppRailProps` has no prop to carry it                                           | `AppRail.tsx:67-107`                                                                                                                                                                                                             | CONFIRMED. Props are `activeDestination`, `onNavigate`, `onOpenSettings`, `destinations`, `identity`, `badges`. Nothing settings-related.                                                                                                                                                                                                                                                                                                                                                                       |
| `ChatShell` holds `settingsActive` and does not forward it                       | `ChatShell.tsx:92,153,183,205,220,237` vs `:288-295`                                                                                                                                                                             | CONFIRMED. The value threads all the way into `ShellGrid` and is consumed only by `fullBleed` at `:237`. The `<AppRail …/>` call at `:288-295` passes six props; `settingsActive` is not among them.                                                                                                                                                                                                                                                                                                            |
| Web never passes `settingsActive`                                                | `App.tsx:1200-1226`                                                                                                                                                                                                              | CONFIRMED. The full `<ChatShell>` prop list is transport, router, keyValueStore, presenceSignal, activeDestination, onNavigate, onOpenSettings, onOpenCommandPalette, railIdentity, railBadges. Repo grep for `settingsActive` outside chat-surface hits only `apps/desktop/renderer/bootstrap.tsx:195,330`.                                                                                                                                                                                                    |
| Web collapses `activeDestination` to Run on Settings                             | `App.tsx:739-740`                                                                                                                                                                                                                | CONFIRMED. `route.screen === "chat" ? route.destination : ROOT_DESTINATION`, and `ROOT_DESTINATION = "run"` (`routes.ts:79`). The comment at `:733-738` asserts "the rail itself is hidden visually for those screens" — **it is not**; the rail renders on every screen inside `ChatShell`.                                                                                                                                                                                                                    |
| Settings is full-bleed on desktop, chromed on web                                | `bootstrap.tsx:330`; `App.tsx:907-927`; `ChatShell.tsx:236-237,250-252,271`                                                                                                                                                      | CONFIRMED. Desktop passes `settingsActive={settingsActive}`; web renders `SettingsBinder` as `body` with `settingsActive` undefined → `fullBleed = false` → 4-column grid + `TOPBAR_HEIGHT` row.                                                                                                                                                                                                                                                                                                                |
| Design styles the Settings item active                                           | `copilot-app.jsx:802-810`; `copilot.css:328-342`                                                                                                                                                                                 | CONFIRMED. `data-active={dest === "settings" \|\| undefined}` on the foot `.rail-item`, and `.rail-item[data-active]` sets `color:var(--tx); background:var(--panel2)` plus the `::before` accent bar.                                                                                                                                                                                                                                                                                                          |
| No CSS or test covers a Settings active state                                    | `AppRail.test.tsx:178-207`, `ChatShell.test.tsx:202-216,250-268`                                                                                                                                                                 | CONFIRMED. Tests assert the gear _exists_ and that `settingsActive` makes the shell full-bleed. Nothing asserts a rail highlight.                                                                                                                                                                                                                                                                                                                                                                               |
| Rail foot draws an unspecified divider + pad                                     | `AppRail.tsx:194-202` vs `copilot.css:359-365`                                                                                                                                                                                   | CONFIRMED. Live: `gap:6, paddingTop:8, borderTop:"1px solid var(--color-border)", width:34`. Design: `.rail-foot{margin-top:auto;display:flex;flex-direction:column;align-items:center;gap:5px}` — that is the entire rule.                                                                                                                                                                                                                                                                                     |
| `.rail-me` lost the design's ring, and a comment enshrines it                    | `AppRail.tsx:210-211,217` vs `copilot.css:376`                                                                                                                                                                                   | CONFIRMED. Comment reads "no border (PRD-C — previously the too-dark elevated bg + a stray hairline)"; code is `border: "none"`. `copilot.css:366-378` ends `border:1px solid var(--line2)`. The hairline is specified, not stray.                                                                                                                                                                                                                                                                              |
| **DISPUTED — the replacement token.** The audit says use `--color-border-subtle` | `packages/design-system/src/styles.css:174,175,228`                                                                                                                                                                              | **The audit is wrong.** `--color-border-subtle: var(--color-border)` = `rgba(255,255,255,.06)` = design `--line` (`copilot.css:13`). Design `--line2` is `rgba(255,255,255,.1)` (`copilot.css:14`) = **`--color-border-strong`** (`styles.css:175`), and both match in light too (`.12` at `styles.css:300` vs `copilot.css:78`). Use `--color-border-strong`.                                                                                                                                                  |
| Brand→first-item is 10px live, 12px design                                       | `AppRail.tsx:190-192` vs `copilot.css:285,293`                                                                                                                                                                                   | CONFIRMED. Live: `itemsStyle.gap = 2`, `marginTop: 10`, and `railStyle` (`:171-185`) sets no `gap`. Design: `.rail{gap:2px}` + `.rail-brand{margin-bottom:10px}` = 12px. `geom.mjs` measured 12 design / 10 live.                                                                                                                                                                                                                                                                                               |
| Rail-foot child gap is 6 vs 5                                                    | `AppRail.tsx:198` vs `copilot.css:364`                                                                                                                                                                                           | CONFIRMED.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Live force-uppercases the initial; design does not                               | `AppRail.tsx:302` vs `copilot-app.jsx:812`                                                                                                                                                                                       | CONFIRMED. Live `identity.initial.slice(0,1).toUpperCase()`; design `{prefs.name.slice(0, 1)}`. `AppRail.test.tsx:311-316` pins the uppercase behaviour with `identity={{initial:"sasha"}}` → `"S"`.                                                                                                                                                                                                                                                                                                            |
| Live tooltip is "Account"; design is the user's name                             | `AppRail.tsx:294,298` vs `copilot-app.jsx:811`                                                                                                                                                                                   | CONFIRMED. Live `aria-label="Account" title="Account"`; design `title={prefs.name}`.                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **`data-destination` is worse than "two nested elements"**                       | `ChatShell.tsx:284`; `AppRail.tsx:255`; `App.tsx:954,1050,1065,1079,1099,1118,1132,1153`; `apps/desktop/renderer/DestinationOutlet.tsx:135`; `apps/frontend/src/features/settings/NotificationDefaultsPanel.tsx:172,179,216,223` | The audit said 2 carriers. There are **five kinds**: the shell root, every rail button, web per-destination wrapper sections, the desktop outlet, and unrelated notification-preference rows. `querySelector('[data-destination="chats"]')` returns the 1220px shell `<div>`.                                                                                                                                                                                                                                   |
| A shipped CSS rule depends on the shell-root carrier                             | `apps/frontend/src/styles.css:8748-8749`                                                                                                                                                                                         | **NEW — the audit missed this.** `[data-component="chat-shell"][data-destination="chats"] .aui-sidebar__header{display:none}`. Renaming the shell-root attribute without updating this rule un-hides the chats sidebar header/footer.                                                                                                                                                                                                                                                                           |
| Two brand marks disagree                                                         | `packages/chat-surface/src/shell/BrandMark.tsx:35,45-46` vs `copilot-data.jsx:13,22-29`                                                                                                                                          | CONFIRMED, with a correction: live is `<circle r=30 fill="#0d0c10"/> + <circle r=15 fill="#5fb2ec"/>`, gradient `x1=0 y1=0 x2=0 y2=1`; mock is `<circle r=20 fill="#0b0a0e" stroke="url(#grad)" strokeWidth=10/>`, gradient `x2=1 y2=1`. The audit's "byte-for-byte identical to `apps/website/public/favicon.svg`" is **imprecise** — the favicon adds a `<rect rx=92 fill="#17161c"/>` app container the component omits — but the hub geometry and gradient direction match the favicon exactly.             |
| Desktop `displayName` is nullable                                                | `apps/desktop/main/auth/index.ts:108-113,552`                                                                                                                                                                                    | CONFIRMED. `readonly displayName: string \| null`, populated from `session.claims.name`.                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Measured parity baseline                                                         | `tools/design-parity/surfaces/rail-badge/out/report-{badge,nobadge}.md:8`                                                                                                                                                        | On-disk at time of writing: `badge` 5 HIGH / 45 MED / 9 LOW; `nobadge` 5 HIGH / 41 MED / 7 LOW. **Indicative only.** The comparator has been tightened since the audit prose (which quotes 7 HIGH): `lib/extract-computed.js` now also captures `boxShadow`/`backdropFilter`/`transition`/`textDecorationLine`, and `lib/compare.mjs` no longer emits phantom `borderColor` rows for borderless elements. Every DoD item below is therefore a **delta against this PR's own merge base**, never a frozen count. |
| **G7 — desktop appearance never persists and never loads**                       | `apps/desktop/renderer/SettingsMount.tsx:386-387`, `:940-952`, `:251-259`                                                                                                                                                        | CONFIRMED, and worse than the README's summary. `:386-387` is `useState<AppearanceValue>(DEFAULT_APPEARANCE)` (`:134-139` = `theme:"system"`, `accent:"sky"`, `density:"comfortable"`). The `onChange` at `:944-950` calls `applyAppearance(next)` (`:251-259`, which stamps the four `:root` attributes) and returns — **no `Transport` write, no `KeyValueStore` write at all**. So the value is not merely "reset on boot"; it is never written, and nothing ever reads it back.                             |
| `splitAppearancePersistence` has zero host call sites                            | `packages/chat-surface/src/settings/AppearancePage.tsx:212-241`; `settings/index.ts:32`; `src/index.ts:910`                                                                                                                      | CONFIRMED. Exported twice, unit-tested at `AppearancePage.test.tsx:204-243`, and a repo grep for the identifier outside `packages/chat-surface` returns **0 hits**. The classifier that routes "contract field → `Transport`, off-contract field → `KeyValueStore`" exists and nothing calls it.                                                                                                                                                                                                                |
| Desktop's boot document stamps nothing                                           | `apps/desktop/renderer/index.html:2,9`                                                                                                                                                                                           | CONFIRMED. `<html lang="en">` carries no `data-theme`/`data-accent`/`data-density`/`data-reduce-motion`; the only pre-hydration visual commitment is `<body style="margin:0;background:#101113">`, which is not a token value.                                                                                                                                                                                                                                                                                  |
| Attributes are desktop's **only** theming mechanism                              | `apps/desktop/main/adapters/ast-allowlist.ts:18`; `ast-allowlist.test.ts:33-37`; `packages/design-system/src/styles.css:165,245-288,291,324`; `packages/design-system/src/index.tsx:45-59`                                       | CONFIRMED. The desktop allowlist explicitly excludes `ThemeProvider` and a test pins that exclusion, so theme/accent resolve purely from `:root[data-theme="dark"\|"light"\|"slate"]` and the **nine** `:root[data-accent=…]` blocks whose runtime twin is `ACCENT_SCHEMES`.                                                                                                                                                                                                                                    |
| The preferences round-trip already exists and is validated                       | `services/backend-facade/src/backend_facade/me_routes.py:90-96`; `services/backend/src/backend_app/routes/me_preferences.py:81-140,335-380`; `apps/frontend/src/api/meApi.ts:91,97`                                              | CONFIRMED. The facade proxies `GET`/`PUT /v1/me/preferences`; backend validates `theme`/`accent`/`density` against `THEME_SCHEMES`/`ACCENT_SCHEMES` and rejects unknowns with `invalid_request`. Web already uses it. **D9 needs no new endpoint and no migration.**                                                                                                                                                                                                                                            |
| Web already has a boot path; desktop does not                                    | `apps/frontend/src/features/appearance/AppearanceContext.tsx:74-79,91-121,161-172`                                                                                                                                               | CONFIRMED. `AppearanceProvider` mirrors the server snapshot onto the document + the design-system provider on mount and saves with a 300ms debounce. It is web-only — it depends on `useUserPreferencesState` and design-system `useTheme`, neither of which the desktop renderer mounts. G7 is a **desktop-only** hole.                                                                                                                                                                                        |
| `document` is a banned global inside `packages/chat-surface`                     | `packages/chat-surface/eslint.config.js:56-59`                                                                                                                                                                                   | CONFIRMED. `no-restricted-globals` bans `window`, `document`, `localStorage`, `fetch`, … D9's load/persist logic may therefore live in the package (ports only), but the `document.documentElement` write must stay in the host — which is exactly where `applyAppearance` already sits (`SettingsMount.tsx:251-259`, comment: "host concern").                                                                                                                                                                 |

## Design intent

Literal source: `tools/design-parity/design-kit/app-v3/`.

**Rail geometry** (`copilot.css:277-286`):

```css
.rail {
  width: 48px;
  background: var(--ink2);
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 10px 0;
  gap: 2px;
}
```

plus `.rail-brand{…margin-bottom:10px}` (`:287-298`) → **brand-to-first-item = 12px**.

**Foot** (`copilot.css:359-365`) — no border, no padding, `gap:5px`:

```css
.rail-foot {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
}
```

**Avatar** (`copilot.css:366-378`) — 26px circle, `--panel3` (`#1d1d23` = `--color-surface-elevated`), text `--tx2` (`#d4d4db` = `--color-text-strong`), and a ring: `border:1px solid var(--line2)` where `--line2 = rgba(255,255,255,.1)` dark (`:14`) / `rgba(10,10,14,.12)` light (`:78`) — i.e. **`--color-border-strong`**.

**Active item, including Settings** (`copilot.css:328-342`, `copilot-app.jsx:802-810`):

```css
.rail-item[data-active] {
  color: var(--tx);
  background: var(--panel2);
}
.rail-item[data-active]::before {
  content: "";
  position: absolute;
  left: -8px;
  top: 50%;
  transform: translateY(-50%);
  width: 2px;
  height: 16px;
  border-radius: 0 2px 2px 0;
  background: var(--accent);
}
```

`--panel2 = #16161a` = `--color-surface-muted`. The mock has **one** `dest` variable; `"settings"` is one of its values, so exactly one rail item can be active at a time.

**Settings chrome** (`copilot-app.jsx:739`): `const showTopbar = dest !== "workspace" && dest !== "settings";` — Settings has no top bar, on any substrate.

**Identity** (`copilot-app.jsx:811-813`): `<button className="rail-me" title={prefs.name}>{prefs.name.slice(0, 1)}</button>` — tooltip is the full name, glyph is `charAt(0)` with **no case transform**.

**Badge** (`copilot.css:343-358`, `copilot-app.jsx:795-797`): `min-width:13px; height:13px; padding:0 3px; border-radius:7px; background:var(--accent); color:var(--accent-ink); font-size:8.5px; font-weight:700; font-family:var(--mono)`, rendered only when `dest !== "workspace"`. The only value the mock ever renders is `"1"`.

## Architectural decision

### D1 — The active-run count is a server projection, owned by the shell, fed by one signal-driven hook

Three moves, in dependency order.

**(a) ai-backend owns the number.** New route, registered in `RuntimeApiRouter.create_router()`
**before** `/runs/{run_id}` (`routes.py:640`) — FastAPI matches in registration order and
`run_id` is an unconstrained `str`, so a later registration would be shadowed:

```
GET /v1/agent/runs/active_count
  query: org_id str? / user_id str?   (non-service path only)
  200  ActiveRunCountResponse
  400  org_id and user_id are required        (scoped_identity, routes.py:543-559)
  403  missing runtime:use scope              (router-level RequireScopes, routes.py:572-576)
```

```python
class ActiveRunCountResponse(RuntimeContract):
    active_run_count: int
```

declared in `runtime_api/schemas/runs.py`; route-name constant
`ACTIVE_RUN_COUNT = "active_run_count"` in `agent_runtime/api/constants.py`
(`Keys.RouteName`, beside `CREATE_RUN` at `:127`).

Port method on the persistence port (`agent_runtime/api/ports.py`, beside
`get_active_run_for_conversation` at `:192`):

```python
async def count_active_runs(self, *, org_id: str, user_id: str) -> int: ...
```

Postgres, under `_tenant_connection(org_id=…)` so RLS `tenant_isolation` also binds:

```sql
SELECT count(*)
  FROM agent_runs r
  JOIN agent_conversations c ON c.id = r.conversation_id AND c.org_id = r.org_id
 WHERE r.org_id  = %(org_id)s
   AND r.user_id = %(user_id)s
   AND c.deleted_at IS NULL
   AND r.status IN ('queued','running','waiting_for_approval','cancelling')
```

The status tuple is not a fourth literal: extract
`ACTIVE_RUN_STATUSES: frozenset[AgentRunStatus]` into `runtime_api/schemas/common.py`
beside the `AgentRunStatus` enum (`:34-45`) and have the new query **and** the existing
`get_active_run_for_conversation` literal (`postgres/runtime_api_store.py:1360-1362`,
and its file/in-memory twins) read it. `c.deleted_at IS NULL` is required or
`DELETE /v1/agent/history` leaves a lit badge (PRD-05 makes that route actually tombstone).

**No migration — verified on disk.** `ls services/backend/migrations` tops out at `0045`
(`0045_provider_api_keys_custom_endpoint.sql`) and `ls services/ai-backend/migrations`
contains only `0001_runtime_baseline.sql`, matching the README's migration table. The ids
`backend 0046/0047` and `ai-backend 0002/0003/0004` belong to PRD-06/07/05/07/09 respectively
(README C18); **PRD-12 claims none of them and adds no `.sql` file**, so it never touches
`MANIFEST.lock` or `tools/check_migration_manifest.py`. PRD-05 already adds
`idx_agent_runs_org_user_created (org_id, user_id,
created_at DESC, id DESC)` under `ai-backend 0002`; this count is an index scan on its leading `(org_id, user_id)`
with a status filter. A partial index `WHERE status IN (…)` is the escape hatch if run
volume ever makes that scan hot — deliberately not built on speculation.

**Authorization** is byte-identical to PRD-05: router-level `RequireScopes(RUNTIME_USE)`,
`scoped_identity` ignoring query params when service headers are present, facade
`identity.scoped_params()` overriding any client-supplied `org_id`/`user_id`, RLS beneath.
The endpoint returns the caller's own runs only.

**(b) The facade proxies it**, registered before `GET /v1/agent/runs/{run_id}` (`app.py:1054`)
for the same shadowing reason, using the `identity.scoped_params()` idiom of `:410-431`.

**(c) `packages/chat-surface` owns the client seam — and `ChatShellProps.railBadges` is deleted.**

> **Ownership (README C1).** PRD-12 owns the active-run count end to end: the endpoint, the
> hook, and the source of the number. PRD-03 lands first (Wave 1) and by ruling it **removes
> `ChatShellProps.railBadges` from the host binding contract and does not create
> `src/shell/useActiveRunCount.ts`** — it no longer moves the web hook into the package and
> its DoD carries no `railBadges` clause, no badge assertion and no polling-risk row. So when
> PRD-12 lands in Wave 3 the prop is already gone; PRD-12 supplies the replacement source.
> If PRD-03 has not landed, PRD-12 performs the prop deletion itself — DoD 7 is written to
> hold either way.

New `packages/chat-surface/src/shell/useActiveRunCount.ts` reads the `Transport` port via
`useTransport()` (the precedent is `useRunSession`, which already fetches through the port
inside this package — the "presentational" rule in `packages/chat-surface/CLAUDE.md`
explicitly permits "reads a port via a hook"). `ShellGrid` calls it and passes
`badges={count > 0 ? { run: count } : undefined}` to `AppRail`.

Deleting the host prop — rather than defaulting it — is the point. A default still lets a
host pass a competing value and drift back apart; removing it makes the desktop gap
_structurally impossible_, not fixed-once. `AppRail.badges` stays: it is a pure view prop
and the parity harness measures the rail through it.

**Revalidation is signal-driven, not a poll.** New
`packages/chat-surface/src/shell/runActivityBus.tsx`: a `{ publish(): void; subscribe(fn): () => void }`
context mounted by `ChatShell` **outside** `ShellGrid` so the rail (subscriber) and the
cockpit in `children` (publisher) share one instance; `useRunActivityBus()` falls back to
an inert no-op bus when no provider is mounted, so existing `useRunSession` tests need no
wrapper. `useRunSession` publishes when its bound `runId` changes and when its derived run
status changes (`useRunSession.ts:359-399`). `useActiveRunCount` revalidates on: mount, a
bus publish (250ms trailing debounce), a `PresenceSignal` hidden→visible transition, and a
30s interval **only while visible**. That kills the 30s lag on the user's own run (the
common case), and the 30s safety net is now one indexed `COUNT` instead of a 100-row page
with per-row latest-run lookups (`conversation_query_service.py:201-205`) — strictly less
load than today.

Error handling changes on purpose: `UnauthorizedError` (exported from `@0x-copilot/chat-transport`)
sets the count to **0**; any other error keeps the last value. Today's bare `catch {}`
cannot tell those apart and leaves an expired session glowing.

**Rejected:**

- _Copy `useActiveRunCount.ts` into `bootstrap.tsx`._ Duplicates a derivation **and** a
  polling policy across two hosts — the exact drift the chat-surface SSOT rule exists to prevent.
- _Use `BadgePort`._ It is an OS dock/tray contract with no desktop implementation, a no-op
  web implementation, and callers only on slugs that are not in the rail. Wrong contract,
  wrong layer. (OS dock badging is listed as a future item below.)
- _Add `status=active` + a `total` to PRD-05's `GET /v1/agent/runs`._ PRD-05 is keyset-paginated
  and deliberately returns no total; bolting one on forces a second scan and couples a list
  endpoint's shape to a badge.
- _A user-scoped run SSE stream._ Genuinely the endgame, and genuinely a new capability:
  ai-backend's SSE is per-run over the `sequence_no` events table, and `backend`'s
  `/v1/inbox/stream` precedent (`inbox/sse.py:515`) lives across a hard service boundary.
  Named as a future item with its reason rather than half-specified here.

### D2 — `settingsActive` becomes a rail input, and "active" becomes one value

`AppRailProps` gains `readonly settingsActive?: boolean`. `ChatShell.tsx:288-295` forwards
the `settingsActive` it already holds. Inside `AppRail`, destination activity becomes
`const isActive = !settingsActive && d.slug === activeDestination`, and the Settings button
takes `railButtonStyle(BUTTON_SIZE, settingsActive)` plus the same `data-state` /
`aria-current="page"` / `data-rail-active-bar` treatment every destination gets. This
reproduces the mock's single-`dest` semantics without collapsing Settings into
`ShellDestinationSlug` (it is not a destination — it has no route, no context panel, no
topbar title).

Web must actually pass it. `apps/frontend/src/app/routes.ts` gains one exported predicate
`isSettingsScreen(route: AppRoute): boolean` (true for `screen === "settings"` and
`"settings-p12"`); `App.tsx` uses it for `settingsActive` **and** replaces the
`ROOT_DESTINATION` collapse comment at `:733-740`, which is factually wrong about the rail
being hidden. One predicate, so the flag and the collapse can never disagree.

### D3 — Settings chrome is decided once, and it agrees with PRD-09

The design predicate is `dest !== "workspace" && dest !== "settings"` (`copilot-app.jsx:739`).

> **Ownership (README C14).** **PRD-09 owns the split** — `SUPPRESS_TOPBAR = {"run"} ∪
settingsActive` and the side-column set in `ChatShell.tsx:36-46,236-237` are defined once,
> by PRD-09, which needs them for Chats. **PRD-12 defines no set and edits no chrome
> predicate.** The corrected wave order makes this a hard edge, not a hedge: Wave 3 is
> PRD-09 → PRD-11 ‖ PRD-12, and the `ChatShell.tsx` claimant order is 03 → 09 → **12 last**.
> Landing PRD-12 before PRD-09 would mean writing the same set twice, which is exactly the
> duplication this ruling removes.

PRD-12's entire contribution to Settings chrome is therefore one thing: **web actually
supplies `settingsActive`** (see D2), which is what makes web's Settings adopt PRD-09's
already-shipped suppression and stop rendering the topbar + the 224px context column that
desktop has never rendered (`bootstrap.tsx:330`). PRD-12 also threads the same flag into the
rail — that half is D2 and is not chrome.

### D4 — Badge overflow caps at 9, not 99

The pill is a 13px circle. `"9+"` (2 glyphs at 8.5px mono + 6px padding ≈ 16px) stays a pill;
`"99+"` ≈ 21px turns it into a stadium 1.6× the height. The design specifies no behaviour
above one digit. `AppRail.tsx:271` becomes `count > 9 ? "9+" : count`; the accessible name
(`:253`) keeps the **exact** number, so nothing is lost to assistive tech.

### D5 — Identity: the host supplies a name, the package derives the glyph

> **Ownership (README C2).** The **prop shape lands with PRD-03**, in Wave 1, as
> `readonly railIdentity: { readonly displayName: string } | null` — PRD-03 binds the final
> shape directly on both hosts so the prop changes exactly once, rather than PRD-03 shipping
> `{initial}` and PRD-12 re-cutting it two waves later. `| null` is required: desktop's
> `displayName` is nullable (`apps/desktop/main/auth/index.ts:108-113,552`).
> **PRD-12 owns what `AppRail` does with that name** — the glyph derivation, the case rule
> and the tooltip — because `AppRail.tsx` is PRD-12's file (claimant order 01 → 03 → **12
> owns**). If PRD-03 ships `{initial}` after all, PRD-12 changes the type as a fallback and
> the risk row below applies.

`AppRail` derives `displayName.trim().charAt(0)` — **no
`.toUpperCase()`**, matching `copilot-app.jsx:812`; silently re-casing a user's own initial
is a data edit, not a style. `title`/`aria-label` become the display name. Empty/whitespace
name → the existing neutral glyph and `title="Account"` (a live-only signed-in-without-a-name
state the mock has no equivalent for). This puts the derivation in one place so neither host
can slice differently.

### D6 — Rail chrome literals (`AppRail.tsx`, no new abstraction)

`:199-200` drop `paddingTop: 8` and `borderTop`; `:198` `gap: 6 → 5`; `:192` `marginTop: 10 → 12`;
`:217` `border: "none"` → `1px solid var(--color-border-strong)` (see the DISPUTED evidence
row — **not** `--color-border-subtle`); rewrite the comment at `:210-211`, which asserts the
opposite of `copilot.css:376`. `* { box-sizing: border-box }` (`styles.css:352-354`) means the
new ring does not grow the 26px box. No design-system recipe covers a 48px icon rail; do not
mint one for four literals. `width: BUTTON_SIZE` on the foot stays — harmless once the border
is gone.

### D7 — Disambiguate `data-destination`

`ChatShell.tsx:284` emits `data-active-destination`, leaving `data-destination` to mean
"a button/section FOR this destination". The rename is only safe with
`apps/frontend/src/styles.css:8748-8749` updated in the same commit — that shipped rule
selects the shell root by the old name.

### D8 — Brand mark: flagged, not changed

`BrandMark.tsx` matches the shipped `apps/website/public/favicon.svg` hub and gradient
direction; `copilot-data.jsx:22-29` is a different, older glyph (ring hub, diagonal gradient).
This PRD changes **neither**. It is a brand decision: either refresh the design kit or
re-cut the asset, website, and component together. Silently converging the rail toward the
mock while the website ships the other mark is the failure mode being avoided.

### D9 — Desktop appearance loads at boot and persists, through the seam that already exists

Absorbed from README **G7** (rail-badge `AUDIT.md:229`), which no PRD owned. It is folded here
rather than into a separate PRD because it is the same shell-boot seam D1/D2 already touch, and
because **PRD-01's headline fix is unobservable without it**: desktop mounts no
`ThemeProvider` (`ast-allowlist.ts:18`), so `:root[data-accent]` is the only accent mechanism,
and today no accent survives a relaunch.

The rule from the program's cross-cutting review applies in full: **use what exists.** The
classifier (`splitAppearancePersistence`, `AppearancePage.tsx:212-241`), the attribute
contract (`appearanceAttributes`, `:156-179`), the endpoint (`GET`/`PUT /v1/me/preferences`,
`me_routes.py:90-96`), the wire types (`UserPreferences` / `UpdateUserPreferencesRequest`,
`packages/api-types/src/index.ts:3472,3511,3596`) and both ports (`Transport`,
`KeyValueStore`) are already shipped. Nothing new is invented; three call sites are added.

**(a) `packages/chat-surface` gains the controller** —
`src/settings/useAppearanceSettings.ts`, ports-only:

```ts
export interface AppearanceSettingsPorts {
  readonly transport: Transport;
  readonly keyValueStore: KeyValueStore;
  /** Host paints the attributes; the package must not touch `document`. */
  readonly onApply: (attrs: AppearanceAttributes) => void;
}
export interface AppearanceSettingsController {
  readonly value: AppearanceValue;
  readonly loading: boolean;
  readonly error: string | null;
  readonly change: (patch: AppearancePatch) => void;
}
```

- **Boot:** `transport.request<UserPreferences>({ method: "GET", path: "/v1/me/preferences" })`,
  then overlay the off-contract fields read from `keyValueStore` under the single key
  `chat-surface.appearance.local` (the fields `splitAppearancePersistence` classifies as
  `local` — today `jade`/`ember` accents and `spacious` density, `AppearancePage.tsx:194-203`).
  Call `onApply(appearanceAttributes(value))` once resolved.
- **Change:** optimistic merge → `onApply` → `splitAppearancePersistence(patch)` →
  `PUT /v1/me/preferences` with `{ appearance: split.profile }` when `profile` is non-empty
  **and** `keyValueStore.set("chat-surface.appearance.local", …)` when `local` is non-empty.
  One 300ms trailing debounce for the PUT, mirroring web's `SAVE_DEBOUNCE_MS`
  (`AppearanceContext.tsx:34`), so a run of swatch clicks is one round-trip.
- **Errors:** the optimistic paint stays (the user's click is not undone), `error` is set, and
  nothing reports success. A failed PUT must not leave the KV write claiming the value
  persisted — write KV only for the `local` half, never as a shadow copy of contract fields.

`onApply` exists because `document` is an eslint-banned global in this package
(`eslint.config.js:56-59`); the host keeps the two-line DOM write it already has.

**(b) Desktop mounts it at the renderer root, not inside Settings.** `bootstrap.tsx:119`
already constructs `LocalStorageKeyValueStore` and holds the `Transport`; it calls
`useAppearanceSettings` there and passes `value` + `change` down to `SettingsMount`, whose
local `useState(DEFAULT_APPEARANCE)` (`SettingsMount.tsx:386-387`) is **deleted** and whose
`AppearancePage` block (`:940-952`) becomes a pass-through. `applyAppearance`
(`:251-259`) moves to `apps/desktop/renderer/appearance.ts` and is supplied as `onApply`.
Mounting at the root is the load-bearing half: attributes must be correct on every screen at
launch, not only after the user opens Settings.

**(c) `apps/desktop/renderer/index.html:2` stamps static defaults** —
`<html lang="en" data-theme="dark" data-accent="sky" data-density="comfortable"
data-reduce-motion="auto">` — so the pre-hydration frame paints token values instead of an
unattributed `:root` + the hard-coded `background:#101113` at `:9`. Static markup only: **no
inline script reading storage**, because that would be a second, desktop-only persistence
path — precisely what this decision refuses.

**Rejected:**

- _A desktop-only `appearanceStore.ts`._ A second store for a value the shipped
  `/v1/me/preferences` contract already holds, and a guaranteed drift from web.
- _Copying `apps/frontend/src/features/appearance/AppearanceContext.tsx` into the renderer._
  It depends on `useUserPreferencesState` and design-system `useTheme`; the desktop allowlist
  denies `ThemeProvider` outright (`ast-allowlist.ts:18`), so the copy would be a fork on
  arrival.
- _Converging web onto the new hook in this PRD._ Web's provider additionally drives the
  design-system `ThemeProvider` that desktop does not mount, so converging is a real
  refactor with its own risk surface — named as a non-goal, with the hook written
  host-agnostic so a later PRD can do it in one move.
- _Widening the profile contract so `jade`/`ember`/`spacious` persist server-side._ The
  backend validates against `ACCENT_SCHEMES`/`THEME_SCHEMES` and rejects unknowns with
  `invalid_request` (`me_preferences.py:92-140`). Widening it is PRD-01's accent question,
  not this PRD's; `splitAppearancePersistence` already routes those three values to KV.

## Scope

### `packages/chat-surface`

| File                                               | Reason                                                                                                                                                                                                                         |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `src/shell/useActiveRunCount.ts` (new)             | The one active-run-count hook: Transport + PresenceSignal + run-activity bus.                                                                                                                                                  |
| `src/shell/useActiveRunCount.test.ts` (new)        | 401→0, other errors→last value, visible-only interval, bus-triggered revalidation.                                                                                                                                             |
| `src/shell/runActivityBus.tsx` (new)               | Publish/subscribe context + inert no-op fallback.                                                                                                                                                                              |
| `src/shell/runActivityBus.test.tsx` (new)          | Subscribe/unsubscribe, no-provider fallback does not throw.                                                                                                                                                                    |
| `src/shell/ChatShell.tsx`                          | Mount the bus; call the hook; feed `AppRail.badges`; forward `settingsActive`; delete `railBadges`; `data-active-destination`. (`railIdentity`'s `{displayName}` shape is declared by PRD-03 — C2; consumed here, not re-cut.) |
| `src/shell/ChatShell.test.tsx`                     | Badge-from-transport, settings highlight forwarding, root attribute rename.                                                                                                                                                    |
| `src/shell/AppRail.tsx`                            | `settingsActive` prop, one-active semantics, `9+` cap, identity shape + tooltip + no uppercase, foot/avatar/spacing literals, comment rewrite.                                                                                 |
| `src/shell/AppRail.test.tsx`                       | New assertions + update the two `identity` tests (`:304-330`) to the new shape.                                                                                                                                                |
| `src/destinations/run/useRunSession.ts`            | Publish to the bus on `runId` / run-status transitions.                                                                                                                                                                        |
| `src/destinations/run/useRunSession.test.ts`       | Assert exactly one publish per transition.                                                                                                                                                                                     |
| `src/settings/useAppearanceSettings.ts` (new)      | D9: boot-load + persist controller over `Transport` + `KeyValueStore`, composing the existing `appearanceAttributes` + `splitAppearancePersistence`. No `document` reference.                                                  |
| `src/settings/useAppearanceSettings.test.ts` (new) | D9: boot load applies attributes; contract fields PUT; off-contract fields go to `KeyValueStore`; PUT failure keeps the optimistic value and sets `error`; the 300ms debounce coalesces.                                       |
| `src/settings/index.ts`                            | Export the controller beside `appearanceAttributes` / `splitAppearancePersistence` (`:31-32`).                                                                                                                                 |
| `src/index.ts`                                     | New barrel block exporting the bus provider and the D9 controller (hosts do not need the run-count hook).                                                                                                                      |

### `packages/api-types`

| File           | Reason                                                                                                      |
| -------------- | ----------------------------------------------------------------------------------------------------------- |
| `src/index.ts` | `ActiveRunCountResponse { active_run_count: number }` + a comment naming `GET /v1/agent/runs/active_count`. |

### `services/ai-backend`

| File                                                  | Reason                                                                 |
| ----------------------------------------------------- | ---------------------------------------------------------------------- |
| `src/runtime_api/schemas/common.py`                   | `ACTIVE_RUN_STATUSES` frozenset beside `AgentRunStatus` (`:34-45`).    |
| `src/runtime_api/schemas/runs.py`                     | `ActiveRunCountResponse`.                                              |
| `src/runtime_api/http/routes.py`                      | `GET /runs/active_count`, registered before `/runs/{run_id}` (`:640`). |
| `src/agent_runtime/api/constants.py`                  | `Keys.RouteName.ACTIVE_RUN_COUNT`.                                     |
| `src/agent_runtime/api/ports.py`                      | `count_active_runs` on the persistence port.                           |
| `src/agent_runtime/api/conversation_query_service.py` | `get_active_run_count(org_id, user_id)`.                               |
| `src/runtime_adapters/postgres/runtime_api_store.py`  | SQL count; replace the inline status literal at `:1360-1362`.          |
| `src/runtime_adapters/file/runtime_api_store.py`      | In-process scan; same status source.                                   |
| `src/runtime_adapters/in_memory/runtime_api_store.py` | Same.                                                                  |
| `tests/unit/runtime_api/test_fastapi_runtime_api.py`  | Route shape, 400/403, `active_count` not shadowed by `{run_id}`.       |
| the store-conformance suite                           | `count_active_runs` conformance across all three adapters.             |

### `services/backend-facade`

| File                                    | Reason                                                            |
| --------------------------------------- | ----------------------------------------------------------------- |
| `src/backend_facade/app.py`             | Proxy, registered before `GET /v1/agent/runs/{run_id}` (`:1054`). |
| `tests/test_public_route_contract.py`   | Add the path to the required tuple.                               |
| `tests/test_tenant_isolation_facade.py` | Client-supplied `org_id`/`user_id` overridden by the session.     |

### `apps/frontend`

| File                                              | Reason                                                                                                                                                                                                                     |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/features/activity/useActiveRunCount.ts`      | **Delete.**                                                                                                                                                                                                                |
| `src/features/activity/useActiveRunCount.test.ts` | **Delete.**                                                                                                                                                                                                                |
| `src/app/App.tsx`                                 | Drop the import (`:33`), `:520`, `railBadges` (`:1222-1226`) and the stale comment (`:1214-1216`); pass `settingsActive`; fix `:733-740`. **`railIdentity`'s `{displayName}` binding is PRD-03's (C2)** — not re-cut here. |
| `src/app/routes.ts`                               | `isSettingsScreen(route)` predicate.                                                                                                                                                                                       |
| `src/styles.css`                                  | `:8748-8749` → `[data-active-destination="chats"]`.                                                                                                                                                                        |

### `apps/desktop`

| File                              | Reason                                                                                                                                                                                                                                                      |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `renderer/bootstrap.tsx`          | No `railBadges`; mount D9's `useAppearanceSettings` at the renderer root using the existing `LocalStorageKeyValueStore` (`:119`) + `Transport`, and pass `value`/`change` down. `railIdentity={{displayName}}` is PRD-03's call site (C2), not re-cut here. |
| `renderer/bootstrap.test.tsx`     | Assert the initial renders, the gear highlights when `settingsActive`, and the boot-load stamps `data-accent` from the fake `Transport` (D9).                                                                                                               |
| `renderer/appearance.ts` (new)    | D9: the host `applyAppearance` lifted out of `SettingsMount.tsx:251-259` (the one `document.documentElement` write), supplied as `onApply`.                                                                                                                 |
| `renderer/SettingsMount.tsx`      | D9: delete `DEFAULT_APPEARANCE` state (`:386-387`); `AppearancePage` (`:940-952`) becomes a pass-through over the boot controller's `value`/`change`.                                                                                                       |
| `renderer/SettingsMount.test.tsx` | D9: changing accent issues `PUT /v1/me/preferences`; an off-contract accent writes `KeyValueStore` instead.                                                                                                                                                 |
| `renderer/index.html`             | D9: static `data-theme`/`data-accent`/`data-density`/`data-reduce-motion` defaults on `<html>` (`:2`) so the pre-hydration frame is token-valued. No inline script.                                                                                         |

### Migrations

**None.** Verified on disk: `services/backend/migrations` tops out at `0045`,
`services/ai-backend/migrations` contains only `0001`. Ids `0046`/`0047` (backend) and
`0002`–`0004` (ai-backend) are pre-assigned to PRD-06/07 and PRD-05/07/09 (README C18).
PRD-12 adds no `.sql` file and therefore does not touch `MANIFEST.lock`.

### `tools/design-parity`

| File                                  | Reason                                                                                                                                                               |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `lib/render-live-rail-badge.test.tsx` | `railBadges` is gone and effects do not run under `renderToStaticMarkup`; render `AppRail` directly with `badges` for both states, and add a third `settings` state. |
| `surfaces/rail-badge/anchors.json`    | Drop `shell.body.grid` (its only rows are the identical-pixel bg artifact and PRD-01's global font token, both anchored elsewhere); add `rail.foot.settings.active`. |
| `surfaces/rail-badge/geom.mjs`        | Pin brand→first-item at 12 and Settings→avatar at 5.                                                                                                                 |

## Non-goals

- **OS dock/tray badging.** `BadgePort` is untouched and unused by the rail. If a real dock
  badge is wanted, it is a separate item: implement `apps/desktop/src/main/ports/BadgeDesktop.ts`,
  mount a `PortProvider` in the desktop renderer (there is none today), and aggregate slugs
  into `app.dock.setBadge`. PRD-13 owns the verdict on whether the current port + its two
  folded-slug callers are dead code.
- **Deleting `BadgePort`, `WebBadgePort`, `InboxRoute`/`TodosRoute` badge wiring.** PRD-13.
- **A user-scoped run-event SSE stream.** Named in D1's rejected list with its cost.
- **The `GET /v1/agent/runs` list itself, its cursor, its index, or `DELETE /v1/agent/history`
  tombstoning.** PRD-05 owns all of it; this PRD only reuses the index.
- **Activity's run rows and status fold.** Corrected per README C21 (the stale-numbering
  ruling): the Activity **rows** are PRD-08 (`_shared/Row.tsx`, `.ui-list-row`), the Activity
  **projection** is PRD-04 (`destinations/activity/activityProjection.ts`), the run-status
  **narrowing** is PRD-05 (`ActiveAgentRunStatus`) and the status **chip** is PRD-02. None of
  it is PRD-06, which is connector access mode.
- **The base font-size token** (`styles.css:377`, 13.6px vs the design's 13px). PRD-01, once,
  globally — it has zero rail impact (the rail sets every size explicitly: badge 8.5px at
  `AppRail.tsx:153`, avatar 11px at `:218`).
- **Accent survival across light/slate themes** (`styles.css:307,335` overriding the
  `:root[data-accent]` blocks at `:245-288`). App-wide cascade bug, not a rail bug; PRD-01.
- ~~**Desktop appearance persistence**~~ — **now in scope as D9** (README G7). It was listed
  here and in PRD-01 as "someone else's PRD"; no such PRD existed.
- **Converging web's `AppearanceProvider` onto D9's controller.**
  `apps/frontend/src/features/appearance/AppearanceContext.tsx` also drives the design-system
  `ThemeProvider`, which desktop deliberately does not mount (`ast-allowlist.ts:18`). The D9
  controller is written host-agnostic so a later PRD can converge them in one move; doing it
  here doubles this PRD's web blast radius for no parity gain.
- **Widening the appearance contract** so `jade`/`ember`/`spacious` persist server-side
  (`me_preferences.py:92-140` rejects them). PRD-01 owns the accent set; D9 routes them to
  `KeyValueStore` exactly as `splitAppearancePersistence` already classifies them.
- **Changing the brand mark.** D8 — flagged for a decision, deliberately not made here.
- **Comparator taxonomy noise** (grid-vs-flex centering, UA button padding, `50%`≡`999px`).
  Harness hygiene in `tools/design-parity/lib/compare.mjs`, not product code.

## Risks & rollback

| Risk                                                                                                                              | Guarded by                                                                                                                                                                                                                           | Revert                                                                                             |
| --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| **`data-active-destination` rename un-hides the Chats sidebar header/footer.** A shipped rule selects the old name.               | `apps/frontend/src/styles.css:8748-8749` updated in the same commit; add an `App` render test asserting the shell root carries the new attribute.                                                                                    | Revert D7 alone — it is one attribute name and two CSS selectors, independent of everything else.  |
| **`GET /runs/active_count` is shadowed by `GET /runs/{run_id}`** and 404s (or worse, returns a run lookup for id `active_count`). | An explicit ai-backend test asserting a 200 count shape on the literal path, and a facade contract test. Registration order is the only fix.                                                                                         | Remove the two registrations; the client hook falls back to 0 and the badge simply never shows.    |
| **Badge goes permanently dark** if the hook throws before the first successful fetch on a host with an unusual Transport.         | `useActiveRunCount.test.ts` initial-state assertion; `ChatShell.test.tsx` badge-from-transport test with a fake transport.                                                                                                           | Same as above — the count defaults to 0, which is the pre-PRD desktop behaviour, not a regression. |
| **Deleting `ChatShellProps.railBadges` breaks an unknown consumer.**                                                              | `npm run typecheck` in `@0x-copilot/frontend`, `@0x-copilot/desktop`, `@0x-copilot/chat-surface`; repo grep for `railBadges` must return only chat-surface internals.                                                                | Re-add the prop as an override. Do not — it re-opens the drift door D1 closes.                     |
| **Settings on web loses the topbar and the 224px column**, and a settings section silently depended on that width.                | `ChatShell.test.tsx:250-268` (full-bleed via `settingsActive`) plus a new `App` test for the settings route; desktop has shipped this layout since PR-5.9, so the surface is known to work full-bleed.                               | Narrow `isSettingsScreen` to return `false` for `settings-p12` — one line in `routes.ts`.          |
| **`identity` shape drift between PRD-03 and PRD-12.**                                                                             | Removed by ruling C2: PRD-03 binds the final `{displayName: string} \| null` shape in Wave 1, so the prop changes once and PRD-12 only owns the in-`AppRail` derivation. Guard: `npm run typecheck --workspace @0x-copilot/desktop`. | If PRD-03 shipped `{initial}` anyway, the fallback shape change is 3 files; revert together.       |
| **Dropping `.toUpperCase()` renders a lowercase initial** for lowercase display names.                                            | `AppRail.test.tsx:304-316` is rewritten to pin the design behaviour — this is a deliberate contract change, recorded here so it is not "fixed" back.                                                                                 | One call site.                                                                                     |
| **The parity harness stops exercising `ChatShell`**, so a shell-level regression escapes this surface.                            | The `chats`, `projects`, `activity`, and `connectors` surfaces all render through `ChatShell` and keep shell-frame anchors.                                                                                                          | n/a — harness-only.                                                                                |

| **D9's boot `GET /v1/me/preferences` fails (offline first launch) and the app paints unthemed.** | The controller falls back to `DEFAULT_APPEARANCE` + the `KeyValueStore` overlay, and `index.html:2` now carries static token defaults, so the worst case is today's behaviour, not a blank `:root`. Asserted in `useAppearanceSettings.test.ts`. | Revert D9 alone — it is one hook plus three desktop call sites, independent of D1–D8. |
| **D9 double-writes a contract field to both the server and `KeyValueStore`**, so a later contract widening reads a stale shadow. | `splitAppearancePersistence` is the only classifier; the KV write takes `split.local` only. `useAppearanceSettings.test.ts` asserts a contract accent (`sky`) produces **no** `KeyValueStore` write. | Delete the KV branch; off-contract accents then stop persisting, which is today's behaviour. |
| **D9 lifts appearance state above `SettingsMount`**, so a Settings-local consumer of `appearance` breaks. | `npm run typecheck --workspace @0x-copilot/desktop`; `SettingsMount.test.tsx` renders the Appearance section from injected props. | Re-seed the deleted `useState` from the injected `value`; the boot stamp survives independently. |

**Clean revert order:** D9 (appearance boot) → D7 (attribute) → D6/D4/D5 (`AppRail` literals)
→ D2 (settings active) → D1 (route + hook + prop deletion). D1's server half is inert without a client; D1's client
half degrades to a permanent 0.

## Definition of Done

1. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api/test_fastapi_runtime_api.py -k active_run_count` passes, covering: `GET /v1/agent/runs/active_count` → `{"active_run_count": N}`; 400 when neither service headers nor `org_id`+`user_id` are supplied; and an explicit assertion that the response is the count shape and **not** a `RunStatusResponse` (proving `/runs/{run_id}` does not shadow it).
2. The ai-backend store-conformance suite asserts `count_active_runs` returns **2** for a fixture with two `running` runs in **one** conversation — the exact undercount today's web hook produces (`apps/frontend/src/features/activity/useActiveRunCount.ts:38-46`, deleted by this PRD, returns 1) — and returns 0 once that conversation's `deleted_at` is stamped. Passes on all three adapters.
3. `grep -rl "waiting_for_approval" services/ai-backend/src --include="*.py"` prints **exactly two** paths — `src/runtime_api/schemas/common.py` and `src/agent_runtime/persistence/records/common.py` (the two enum homes). Today it prints three; the third is the inline status tuple at `src/runtime_adapters/postgres/runtime_api_store.py:1362`, which this PRD replaces with `ACTIVE_RUN_STATUSES`.
4. `cd services/backend-facade && .venv/bin/python -m pytest tests/test_public_route_contract.py tests/test_tenant_isolation_facade.py` passes with `"/v1/agent/runs/active_count"` in the required-paths tuple and a test asserting a request carrying `?org_id=other_org&user_id=other_user` is forwarded with the **session's** org/user.
5. `packages/chat-surface/src/shell/useActiveRunCount.test.ts` asserts: an `UnauthorizedError` sets the count to `0`; a generic transport error leaves the previous count unchanged; no interval fires while `PresenceSignal` reports hidden; and a `runActivityBus.publish()` triggers exactly one refetch after the 250ms debounce.
6. `packages/chat-surface/src/shell/ChatShell.test.tsx` asserts a `[data-rail-badge]` appears inside `[data-destination="run"]` when a fake `Transport` answers `/v1/agent/runs/active_count` with `{active_run_count: 3}` and `activeDestination` is `"chats"` — with **no `railBadges` prop passed**, because the prop no longer exists.
7. `grep -rn "railBadges" apps packages --include="*.ts" --include="*.tsx" | grep -v node_modules` returns **0 hits**, and `apps/frontend/src/features/activity/useActiveRunCount.ts` + `.test.ts` no longer exist.
8. `grep -n "still needs a run-list source" apps/frontend/src/app/App.tsx` returns **0 hits**.
9. `packages/chat-surface/src/shell/AppRail.test.tsx` asserts the Settings button carries `data-state="active"`, `aria-current="page"` and a `[data-rail-active-bar]` child when `settingsActive` is true, **and** that `[data-destination="run"]` simultaneously carries `data-state="inactive"` when `activeDestination="run"` and `settingsActive` — the regression guard for the "rail highlights Run while in Settings" bug (`App.tsx:739-740`).
10. `packages/chat-surface/src/shell/AppRail.test.tsx` asserts `badges={{run: 137}}` renders the text `"9+"` and an accessible name containing `"137"`.
11. `packages/chat-surface/src/shell/AppRail.test.tsx` asserts `identity={{ displayName: "sasha chen" }}` renders the glyph `"s"` (**not** `"S"`) and `title` / `aria-label` exactly `"sasha chen"` — matching `copilot-app.jsx:811-812`.
12. **Design values pinned numerically.** Two halves, because jsdom does not resolve `var()`:

    (a) **In jsdom** — `AppRail.test.tsx` asserts on the rendered inline styles (all of these are `CSSProperties` on the element, so jsdom reads them verbatim; `var()` is **not** resolved there, which is why the colour lives in (b)): foot `gap: 5px`, no `border-top` and no `padding-top` (`copilot.css:359-365`); items wrapper `margin-top: 12px` with `gap: 2px`, summing to the design's `.rail{gap:2px}` + `.rail-brand{margin-bottom:10px}` = 12px (`copilot.css:285,293`); avatar `border` is the exact string `1px solid var(--color-border-strong)` (`copilot.css:376`); badge `min-width: 13px`, `height: 13px`, `font-size: 8.5px`, `border-radius: 7px` (`copilot.css:343-358`).
    (b) **In the harness** — the avatar ring's resolved colour is pinned in `tools/design-parity/surfaces/rail-badge/out/report-badge.json`, `jq '[.findings[] | select(.label == "rail.me" and (.prop | test("^border")))] | length'` prints `0`, i.e. the live `rgba(255,255,255,0.1)` equals the design's `--line2` (`copilot.css:14`, light `:78`; token verified at `packages/design-system/src/styles.css:175` dark / `:300` light). `--color-border-subtle` is **not** the token: it aliases `--color-border` = `rgba(255,255,255,0.06)` = design `--line` (`styles.css:174,228`).

13. `grep -n "no border (PRD-C" packages/chat-surface/src/shell/AppRail.tsx` returns **0 hits** — the comment that contradicts `copilot.css:376` is gone.
14. `apps/frontend/src/app/App.test.tsx` asserts that for the route `{screen:"settings"}` the rendered `[data-component="chat-shell"]` contains **no** `[data-component="topbar"]` and no `[data-component="context-panel"]`, and that `isSettingsScreen` from `src/app/routes.ts` returns `true` for both `screen:"settings"` and `screen:"settings-p12"` and `false` for `screen:"chat"` — i.e. web reaches the layout desktop already ships (`bootstrap.tsx:330`), using PRD-09's suppression sets unchanged (C14).
15. `grep -rn 'data-destination' packages/chat-surface/src/shell/ChatShell.tsx` returns **0 hits** for the shell root (it emits `data-active-destination`), and `apps/frontend/src/styles.css` contains no `[data-component="chat-shell"][data-destination=` selector.
16. `npm run typecheck` passes for `@0x-copilot/chat-surface`, `@0x-copilot/frontend`, `@0x-copilot/desktop`, `@0x-copilot/api-types`.
17. `npm run test --workspace @0x-copilot/chat-surface` and `npm run test --workspace @0x-copilot/desktop` exit 0, **or** the failing test ids are byte-identical to `docs/plan/design-parity-remediation/baseline-failures.txt` (the program-wide baseline defined by README DoD-Q2), which this PR does not modify. `apps/desktop/renderer/bootstrap.test.tsx` additionally asserts the rail-foot avatar renders the session initial (today it asserts only element presence at `:223`).
18. Re-run the harness per `tools/design-parity/SKILL.md`, then for `S` in `badge` and `nobadge`:
    `jq '[.findings[] | select(((.label | startswith("rail.foot")) and (.prop | test("border|padding|gap"))) or (.label == "rail.me" and (.prop | test("^border"))))] | length' tools/design-parity/surfaces/rail-badge/out/report-$S.json`
    prints `0` for both.
19. From `tools/design-parity/`: `node surfaces/rail-badge/geom.mjs | jq '.live._brandToFirstItem, .live._settingsToMe'` prints `12` then `5`, equal to `.design._brandToFirstItem` / `.design._settingsToMe`. Today the live side prints `10` and `6`.
20. A new `settings` state exists in `surfaces/rail-badge/` (design + live JSON + `report-settings.json`), and from `tools/design-parity/`: `jq '[.findings[] | select(.severity == "high" and (.label | startswith("rail.foot.settings")))] | length' surfaces/rail-badge/out/report-settings.json` prints `0` — the anchor `rail.foot.settings.active` mapped to the design's `.rail-foot .rail-item[data-active]`.
21. **Parity does not regress overall** — a delta against _this PR's_ merge base, never a frozen count (the comparator and the extractor have both been tightened during this program, so any absolute number goes stale):

    ```bash
    B=$(git merge-base HEAD origin/main)
    for S in badge nobadge; do
      P=tools/design-parity/surfaces/rail-badge/out/report-$S.json
      jq -n --slurpfile base <(git show "$B:$P") --slurpfile now "$P" \
        'if $now[0].counts.high <= $base[0].counts.high
            and $now[0].counts.medium <= $base[0].counts.medium
         then "OK" else error("parity regressed") end'
    done
    ```

    prints `"OK"` twice and exits `0`, with the regenerated reports committed in the same commit as the change.

22. **G7 / D9 regression guard — fails on `main`.** `packages/chat-surface/src/settings/useAppearanceSettings.test.ts` asserts that with a fake `Transport` answering `GET /v1/me/preferences` with `{appearance:{theme:"dark",accent:"violet",density:"compact",reduce_motion:"auto"}}`, the controller calls `onApply` **once on mount** with `{"data-theme":"dark","data-accent":"violet","data-density":"compact","data-reduce-motion":"auto"}` — before any user interaction. There is no boot read on `main`, so this test cannot pass there.
23. `packages/chat-surface/src/settings/useAppearanceSettings.test.ts` further asserts: `change({accent:"violet"})` issues exactly one `PUT /v1/me/preferences` with body `{appearance:{accent:"violet"}}` after the 300ms debounce and **no** `KeyValueStore.set`; `change({accent:"ember"})` (off-contract per `AppearancePage.tsx:212-241`) issues **no** `PUT` and one `KeyValueStore.set` under `chat-surface.appearance.local`; and a rejected `PUT` leaves `value` at the optimistic value with `error` non-null.
24. **The desktop host is actually wired.** (a) `grep -rn "splitAppearancePersistence\|useAppearanceSettings" apps/desktop/renderer` returns at least one hit (today `grep -rn "splitAppearancePersistence" apps packages --include="*.ts" --include="*.tsx" | grep -v node_modules | grep -v "packages/chat-surface"` returns **0**); (b) `grep -n "useState<AppearanceValue>" apps/desktop/renderer/SettingsMount.tsx` returns **0 hits**; (c) `grep -n "data-accent" apps/desktop/renderer/index.html` returns 1 hit on the `<html>` element; (d) `apps/desktop/renderer/bootstrap.test.tsx` asserts `document.documentElement.getAttribute("data-accent")` equals the value the fake `Transport` returned, after boot and **without** opening Settings.

## Dependencies

Corrected per the README's wave order — **Wave 3: PRD-09 → PRD-11 ‖ PRD-12** — and its hot-file
claimant lists (`ChatShell.tsx` 03 → 09 → 12; `AppRail.tsx` 01 → 03 → **12 owns**;
`api-types/src/index.ts` 05 → 07 → 09 → 12; `routes.py` / `runtime_api_store.py` /
`conversation_query_service.py` / facade `app.py` all 05 → … → 12 last). PRD-12 is the **last**
claimant on every file it shares, and its only overlap with the parallel PRD-11 is the
`packages/chat-surface/src/index.ts` barrel.

**Must land first:**

- **PRD-03** (host binding contract) — by ruling C1 it **removes** `ChatShellProps.railBadges`
  and does **not** create `src/shell/useActiveRunCount.ts`; by ruling C2 it binds
  `railIdentity: {displayName: string} | null` — the final shape — on both hosts. PRD-12 then
  supplies the count source and the in-`AppRail` glyph/tooltip semantics. (The superseded text
  here previously claimed PRD-03 lands "with today's prop shapes"; the README calls that false.)
- **PRD-05** (`GET /v1/agent/runs` + `idx_agent_runs_org_user_created` under `ai-backend 0002` +
  `DELETE /v1/agent/history` tombstoning) — PRD-12 reuses its index and depends on its deletion
  semantics for the `c.deleted_at IS NULL` predicate to mean anything. Both PRDs register
  literal paths under `/v1/agent/runs`: `GET /runs` (PRD-05) and `GET /runs/active_count`
  (PRD-12) must **both** precede `GET /runs/{run_id}` in ai-backend _and_ in the facade
  (program-level risk in the README).
- **PRD-09** (Chats surface) — owns the `SUPPRESS_TOPBAR` / side-column split (C14). PRD-12
  defines no suppression set; it only makes web pass `settingsActive`. Landing PRD-12 first
  would duplicate PRD-09's set.

**Coordinate with (no hard order):**

- **PRD-01** — owns the base font-size token, `--color-bg`, and the accent/theme cascade order,
  none of which is fixed here. Note the dependency runs the other way for D9: PRD-01's nine
  restored accents (`styles.css:245-288`) are only demonstrable on desktop **after** D9 lands,
  because desktop has no `ThemeProvider` and no boot stamp today.
- **PRD-11** — runs in parallel in Wave 3; the only shared file is
  `packages/chat-surface/src/index.ts`.

**Unblocks:**

- Any future OS dock/tray badge: once the count has one owner, a desktop `BadgePort` impl
  subscribes to the same hook instead of inventing a second source.
- PRD-13's dead-code verdict on `BadgePort` + `WebBadgePort` + the inbox/todos badge wiring:
  after this PRD, nothing in the rail can be argued to need them.
- Converging web's `AppearanceProvider` onto D9's controller: once one host proves the
  ports-only controller, the web provider's remaining unique job is the design-system
  `ThemeProvider` mirror.
