# Phase 2 — Shell & IA (6-destination solo) — Implementation PRD

**Phase:** 2 · **Branch:** `feat/desktop-redesign` · **Worktree:** `/Users/parthpahwa/Documents/work/enterprise-search-redesign`
**Plan:** `docs/plan/desktop-redesign/PLAN.md` §8 "Phase 2 — Shell & IA (6-dest solo)" (2A–2E) + §5 "Target IA".
**Design source of truth:** `docs/plan/desktop-redesign/design-reference/DESIGN-SPEC.md` §0 (tokens/dims), §1 (Shell), §9 (decisions overlay).
**Template:** `docs/plan/desktop-redesign/_TEMPLATE.md`.

---

## 1. Context & problem

The desktop renderer today mounts a `<ChatShell>` wrapping a single static `DesktopPlaceholder` ("0xCopilot desktop · phase 1"), and the shell renders the **legacy 12-destination rail** (`home, chats, agents, library, inbox, tools, projects, todos, connectors, team, memory, routines`) inherited from the web product's Atlas IA. That IA is wrong for the solo local-desktop product: it exposes team surfaces, duplicated destinations (agents/inbox/todos/routines), and no **Run** cockpit. The rail/topbar geometry and active-state treatment also predate the v2 "quiet" design language (`AppRail` uses a 52px rail with a 36px accent-tint button; `Topbar` is 44px — DESIGN-SPEC §0/§1 specify **48px rail / 46px topbar / 34px icon buttons / active = `--panel2` bg + 2px left accent bar**).

This phase delivers the **6-destination profile-gated solo shell** in v2 styling and wires a real **destination outlet** on desktop (removing the placeholder). It builds on Phase 0 (design-system v2 tokens/fonts loaded on desktop — `PLAN.md` 0A/0B — and the `DeploymentProfile` port — 0D) and is independent of the Phase 1 interaction-layer hoist. It unblocks Phase 3 (Run cockpit mounts into the wired outlet) and Phase 4 (list destinations fill the outlet).

**Verified baseline (read in this worktree, not assumed):**

- `AppRail.tsx` today: `RAIL_WIDTH = 52`, `railButtonStyle` is **36×36** with active = `color-mix(in srgb, var(--color-accent) 12%, transparent)` **fill** (the accent-tint to replace), no brand mark, Settings gear only in the foot (no avatar), 18px glyphs.
- `Topbar.tsx` today is a **breadcrumb** (`<destination label> / <leaf>` with `data-testid="topbar-breadcrumb"` + `topbar-breadcrumb-leaf`), `TOPBAR_HEIGHT = 44`, no command trigger. The v2 topbar (title + subtitle + command trigger) **replaces** the breadcrumb — so its existing tests are **rewritten**, not merely "kept" (§8).
- `ChatShell.tsx` today: `FULL_BLEED_DESTINATIONS = new Set(["chats"])` only; suppresses Topbar + ContextPanel + RightRail on full-bleed.
- `destinations.ts` today: 12-slug union, `DEFAULT_SHELL_DESTINATION = "home"`; `AppRail`/`Topbar`/`ChatShell` all import `SHELL_DESTINATIONS` directly.
- `apps/desktop/renderer/bootstrap.tsx` today: seeds `activeDestination = DEFAULT_SHELL_DESTINATION` (`"home"`) and renders `<DesktopPlaceholder />` as `ChatShell`'s child.
- **Already built and exported (do NOT re-author):** `packages/chat-surface/src/shell/CommandPaletteTrigger.tsx` (a reusable "Search… ⌘K" button, `data-testid="command-palette-trigger"`, `minWidth: 200`, height 28, `onOpen` prop), plus `CommandPalette.tsx` + `useCommandPaletteHotkey.ts` — all re-exported from `packages/chat-surface/src/index.ts`. Phase 2 **mounts the existing trigger** in the topbar; the palette-open + hotkey wiring is Phase 6A. `DestinationPlaceholder.tsx` is likewise an existing exported primitive (props: `icon`, `title`, `description`, `phaseLabel`, `bridges?`, `roadmapHref?`).
- `packages/chat-surface/src/destinations/` is a **per-destination-view content dir** (one subdir per legacy slug: `home, chats, agents, …`) — it is _view content_, not a second slug registry; it holds no `run`/`activity` dir yet (Phase 4 adds those). SSOT for slug↔label stays `shell/destinations.ts`.

**Hard regression constraint:** `apps/frontend` consumes the _same_ `chat-surface` shell (`apps/frontend/src/app/App.tsx`, `apps/frontend/src/app/HashRouter.ts`, `apps/frontend/src/app/routes.ts`) and its URL routing is pinned to the legacy 12 slugs. The web app MUST stay behaviorally identical: same rail, same routes, same tests green.

---

## 2. Goals / Non-goals

### Goals

- Make `packages/chat-surface/src/shell/destinations.ts` a **profile-gated single source of truth**: `single_user_desktop` → `[Run, Chats, Projects, Activity, Tools, Skills]`; `team` adds Team/Members/Billing.
- Default the solo desktop landing destination to **Run**.
- Apply v2 "quiet" tokens/fonts/dims to `AppRail` (48px, icon-only, 34px buttons, active = panel2 + 2px left accent bar, brand mark top, Settings gear + avatar in foot) and `Topbar` (46px, command/search trigger, **suppressed on Run and Settings**).
- Wire the **Settings entry** and **avatar** in the rail foot on desktop via the existing `onOpenSettings` slot.
- **Fold/route** the deprecated destinations (home/library/inbox/todos/routines/agents) — Activity absorbs agents + inbox; the rest are not top-level on desktop.
- **Remove `DesktopPlaceholder` from the desktop mount** and wire a real **destination outlet** that renders per active destination (honest `DestinationPlaceholder` for surfaces that Phases 3–5 fill).
- Keep `apps/frontend` behaviorally identical (rail/routes/tests unchanged).

### Non-goals (explicitly deferred)

- **Run cockpit** (center surface / right-rail tabs / timeline / Studio-Focus / streaming / approvals) — **Phase 3** (3A–3G). Phase 2 only reserves the Run slot + full-height suppression.
- **List destination content** (Chats archive, Projects, Activity recast, Tools connect-flow, Skills catalog) — **Phase 4** (4A–4E). Phase 2 renders honest placeholders in the outlet.
- **Settings surface** (nav + Account/Models/Privacy/Notifications/Advanced pages) — **Phase 5**. Phase 2 only wires the rail-foot **entry** to a stub target.
- **Command palette** (`⌘K`) open behavior — **Phase 6A**. The palette machinery (`CommandPalette`, `CommandPaletteTrigger`, `useCommandPaletteHotkey`) **already exists and is exported** from `chat-surface`; Phase 2 **reuses the existing `CommandPaletteTrigger`** in the topbar with `onOpen` wired to an explicit deferred handler (a no-op placeholder that logs nothing and does not open the palette). Phase 6A supplies the real `onOpen` (palette open) and registers the `⌘K` hotkey. Phase 2 authors **no new** trigger component.
- **Design-system token value edits** — owned by **Phase 0B** (`PLAN.md`). Phase 2 _consumes_ tokens; where a token value still differs from DESIGN-SPEC §0 (e.g. `--color-bg #0f0f10` vs spec `--ink #09090b`), that is a 0B fix, flagged here not fixed here.
- Migrating the **web app** to the 6-destination IA — out of scope; web keeps its legacy 12-slug rail.

---

## 3. User stories

> Roles: **Solo user** (primary, `single_user_desktop`), **Team admin** (`team` profile only), **Developer/maintainer** (DX/architecture).

### US-2.1 — Solo 6-destination rail

_As a **Solo user**, I want the app rail to show exactly the six destinations relevant to me (Run, Chats, Projects, Activity, Tools, Skills), so that I'm not confronted with team/legacy surfaces I don't use._

- **Given** the renderer boots with `ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop`, **When** the shell mounts, **Then** the rail renders exactly 6 destination buttons in order Run, Chats, Projects, Activity, Tools, Skills, plus the Settings gear + avatar in the foot.
- **Given** the rail is rendered, **When** I inspect labels, **Then** the connectors destination is labeled **"Tools"** and the skill-catalog destination is labeled **"Skills"** (relabel, not a slug rename).
- **Given** any legacy slug (home/library/inbox/todos/routines/agents/team/memory), **When** the solo profile is active, **Then** no rail button exists for it.

### US-2.2 — Team profile adds team destinations

_As a **Team admin**, I want Team/Members/Billing to appear only when the deployment is a team deployment, so that solo installs stay clean while team installs are complete._

- **Given** `ENTERPRISE_DEPLOYMENT_PROFILE=team`, **When** the shell mounts, **Then** the rail renders the 6 solo destinations **plus** Team, Members, Billing.
- **Given** `single_user_desktop`, **When** the shell mounts, **Then** Team/Members/Billing are absent.
- **Given** an unknown/absent profile value, **When** the shell resolves destinations, **Then** it falls back to `single_user_desktop` (fail-safe to the smaller, never leaking team surfaces).

### US-2.3 — Default landing on Run

_As a **Solo user**, I want the app to open on Run, so that the flagship cockpit is the front door, not an archive list._

- **Given** a fresh desktop boot with no persisted destination, **When** the shell first renders, **Then** the active destination is **Run** and its rail button shows `aria-current="page"`.
- **Given** the solo profile, **When** the host asks for the default destination, **Then** `defaultDestinationForProfile("single_user_desktop") === "run"`.
- **Regression:** the web host's landing (`ROOT_DESTINATION` in `apps/frontend/src/app/routes.ts`) MUST remain `"chats"` — Phase 2 does not touch web defaulting.

### US-2.4 — Settings entry + avatar in the rail foot (desktop)

_As a **Solo user**, I want a Settings gear and my avatar pinned at the bottom of the rail, so that settings is always one click away and I can see which identity is active._

- **Given** the desktop shell, **When** the rail renders, **Then** the foot shows a Settings gear button (34px) above a 26px circular avatar (`data-rail-action="settings"` / `data-rail-me`), separated from the destination list by a `--line` hairline.
- **Given** I click the Settings gear, **When** `onOpenSettings` is wired on desktop, **Then** the desktop settings target opens (Phase 2 stub target; Phase 5 fills it) and no console error occurs.
- **Empty/edge:** **Given** a host that does NOT pass `onOpenSettings` (e.g. a test harness), **When** the rail renders, **Then** the Settings button is omitted (no crash), matching current behavior.

### US-2.5 — Web app unregressed (developer)

_As a **Developer/maintainer**, I want the profile-gating to be additive so the web app's rail, URLs, and tests are byte-for-byte unchanged, so that shipping the solo IA carries zero web risk._

- **Given** `apps/frontend` renders `<ChatShell>` with no profile provider, **When** the shell resolves destinations, **Then** it renders the **legacy 12-destination** list unchanged (web default path), and `apps/frontend/src/app/HashRouter.ts` slug validation is untouched.
- **Given** the existing `packages/chat-surface/src/shell/AppRail.test.tsx` / `Topbar.test.tsx`, **When** they run unchanged, **Then** they still pass (the default/legacy render path is preserved) OR they are updated in the same PR with equivalent assertions and no loss of coverage.
- **Given** `npm run typecheck --workspace @0x-copilot/frontend`, **When** run after this phase, **Then** it passes with no new errors.

### US-2.6 — Honest destination outlet (no placeholder)

_As a **Solo user**, I want each rail destination to open its own surface (or an honest "coming soon" panel for those not yet built), so that the app feels real and the static "phase 1" placeholder is gone._

- **Given** the desktop mount, **When** it renders, **Then** the static `DesktopPlaceholder` ("0xCopilot desktop · phase 1") is **no longer mounted**; a `DestinationOutlet` renders instead.
- **Given** I click Chats/Projects/Activity/Tools/Skills, **When** the destination is not yet built (Phases 4–5), **Then** the outlet shows `DestinationPlaceholder` naming the destination, its intent, and its phase — not a blank pane and not a fake dataset.
- **Given** I click Run, **When** Phase 3 has not landed, **Then** the outlet shows the Run placeholder (honest "coming in Phase 3") — Phase 3 later swaps this slot for `ThreadCanvas` with no outlet change.
- **Loading/error:** the outlet has no data fetch of its own in Phase 2; placeholders are deterministic (no spinner, no Retry), matching `DestinationPlaceholder`'s "deliberately honest" contract.

### US-2.7 — v2 "quiet" shell geometry & active state

_As a **Solo user**, I want the shell to look like the v2 design (calm neutrals, single sky accent, precise geometry), so that the desktop app is visually coherent, not the un-styled legacy shell._

- **Given** the rail, **When** measured, **Then** width = **48px**, buttons = **34×34**, brand mark = **32×32** at top → navigates to Run, gap between items = 4px, foot separated by 1px `--color-border` hairline.
- **Given** an active destination, **When** rendered, **Then** the active button shows `--color-bg-elevated`/panel2 background **and a 2px `--color-accent` bar on its left edge** (DESIGN-SPEC §1), not the current accent-tint fill.
- **Given** the topbar, **When** measured on a list destination, **Then** height = **46px**, and the left shows a **title (13.5px semibold) + subtitle (11.5px muted)** — replacing today's `<label> / <leaf>` breadcrumb; the existing `leaf` prop maps to the **subtitle** slot (em-dash/empty-string leaf → no subtitle). The right renders the **existing `CommandPaletteTrigger`** (label + `⌘K` hint, width 250px per DESIGN-SPEC §1) with a deferred `onOpen` (no palette open in Phase 2).
- **Given** Run or Settings is active, **When** the shell renders, **Then** the topbar is **suppressed** (destination owns full height), matching the current full-bleed treatment for chats.

### US-2.8 — Folded destinations absorbed

_As a **Solo user**, I want the old agents/inbox concepts to live under Activity, so that there's one place for "what the agent has done" instead of three overlapping lists._

- **Given** the solo rail, **When** rendered, **Then** there is no Agents, Inbox, Home, Library, Todos, or Routines button.
- **Given** a defensive/legacy navigation to `agents` or `inbox` (e.g. a persisted route or bridge call), **When** the desktop outlet resolves it, **Then** it routes/redirects to **Activity** rather than dead-ending.
- **Given** Activity's placeholder copy, **When** shown, **Then** it names its recast intent ("Everything the agent has done…") per DESIGN-SPEC §3.

### US-2.9 — Accessibility & keyboard

_As a **Solo user** relying on the keyboard/AT, I want the rail and topbar to be navigable and announce state, so that the shell is usable without a mouse and respects my motion settings._

- **Given** the rail `<nav aria-label="Copilot destinations">`, **When** I Tab into it, **Then** each destination button is reachable, shows a 2px `--color-accent` focus-visible ring (offset 2), and the active one exposes `aria-current="page"`.
- **Given** icon-only buttons, **When** inspected by AT, **Then** each has an accessible name (`aria-label`/`title` = destination label) and the glyph is `aria-hidden`.
- **Given** `[data-reduce-motion="1"]` or `prefers-reduced-motion: reduce`, **When** the active bar / hover transitions render, **Then** transition durations are zeroed (inherited from design-system tokens).
- **Given** light and dark themes and `[data-density=compact|spacious]`, **When** rendered, **Then** the rail/topbar use only token variables (no hardcoded hex), so both themes and all densities are correct, and no decorative color appears beyond the single sky accent.

---

## 4. Functional requirements

> Each FR maps to ≥1 user story and ≥1 test (see §8). Grouping: A destinations/profile, B AppRail, C Topbar, D ChatShell, E desktop outlet/bootstrap, F a11y/theming.

### A. Destinations / profile SSOT (`packages/chat-surface/src/shell/destinations.ts`)

- **FR-2.1** The `ShellDestinationSlug` union MUST be extended to a **superset** that adds `"run"` and `"activity"` while retaining every legacy slug the web host references (`home, chats, agents, library, inbox, tools, projects, todos, connectors, team, memory, routines`) plus team slugs `"members"`, `"billing"`. No existing slug identity is renamed. _(US-2.1, US-2.5)_
- **FR-2.2** A single canonical destination registry (slug → `{ label, icon, profiles }`) MUST exist; all per-profile lists are derived from it (no second hand-maintained list). _(US-2.5)_
- **FR-2.3** `destinationsForProfile(profile: DeploymentProfile): readonly ShellDestination[]` MUST return, for `"single_user_desktop"`, exactly `[run, chats, projects, activity, connectors→label "Tools", tools→label "Skills"]` in that order. _(US-2.1)_
- **FR-2.4** For `"team"`, `destinationsForProfile` MUST return the 6 solo destinations followed by `team`, `members`, `billing`. _(US-2.2)_
- **FR-2.5** For an unknown/undefined profile, `destinationsForProfile` MUST fall back to the `single_user_desktop` set (never leak team destinations). _(US-2.2)_
- **FR-2.6** `defaultDestinationForProfile(profile)` MUST return `"run"` for `single_user_desktop` and `team`. _(US-2.3)_
- **FR-2.7** The legacy 12-item `SHELL_DESTINATIONS` export MUST remain available and unchanged in order/labels for the web host and existing shell fallbacks. _(US-2.5)_
- **FR-2.8** In the solo/team lists, the destination with slug `connectors` MUST carry label `"Tools"` and the destination with slug `tools` MUST carry label `"Skills"`; the legacy `SHELL_DESTINATIONS` labels for those slugs MUST remain `"Connectors"`/`"Tools"`. _(US-2.1, US-2.5)_

### B. AppRail (`packages/chat-surface/src/shell/AppRail.tsx`)

- **FR-2.9** `AppRail` MUST accept the list of destinations to render as a prop (`destinations`), defaulting to `SHELL_DESTINATIONS` when omitted (web path); the desktop host passes the profile-derived list. _(US-2.1, US-2.5)_
- **FR-2.10** The rail width MUST be **48px**, destination buttons **34×34**, item gap 4px; a **32×32 brand mark** button MUST render at the top and navigate to Run. _(US-2.7)_
- **FR-2.11** The active destination button MUST render with a `--color-bg-elevated` (panel2) background and a **2px `--color-accent` left-edge bar**; inactive = transparent + `--color-text-subtle`. _(US-2.7)_
- **FR-2.12** `AppRail` MUST render `run` and `activity` glyphs and remain exhaustive over the widened union so the package typechecks. To honor DESIGN-SPEC §7 (Tools=`plug`, Skills=`skill`) **without** regressing the web rail — which shares slugs `connectors`/`tools` — the glyph for a relabeled destination MUST come from the **per-view destination metadata** (registry `icon` for the solo/team views), NOT from a slug-keyed change that would also alter the legacy `SHELL_DESTINATIONS` render. The legacy web rail glyphs for `connectors`/`tools` MUST be byte-identical. If the `plug`/`skill` glyphs are not added this phase, the existing glyphs render in the solo view and the swap is listed as an explicit Phase-2 follow-up. _(US-2.1, US-2.5)_
- **FR-2.13** When `onOpenSettings` is supplied, the foot MUST render a Settings gear (34px, `data-rail-action="settings"`) and a **26px circular avatar** (`data-rail-me`), separated from items by a 1px `--color-border` hairline; when `onOpenSettings` is absent, both are omitted. _(US-2.4)_
- **FR-2.14** Each destination button MUST keep `aria-label`, `title`, `aria-current="page"` when active, `data-destination`, and `data-state` attributes. _(US-2.9)_

### C. Topbar (`packages/chat-surface/src/shell/Topbar.tsx`)

- **FR-2.15** The topbar height MUST be **46px**; it MUST render a **title** (13.5px semibold) resolved from the destination registry (graceful for `run`/`activity`) and, when a `leaf`/subtitle is supplied, a **subtitle** (11.5px muted). This replaces the current `<label> / <leaf>` breadcrumb; the `leaf` prop is retained and now feeds the subtitle slot (em-dash/empty → no subtitle rendered). _(US-2.7)_
- **FR-2.16** The topbar MUST render, right-aligned, the **existing exported `CommandPaletteTrigger`** (`packages/chat-surface/src/shell/CommandPaletteTrigger.tsx`; `data-testid="command-palette-trigger"`, `⌘K` hint) wired to a **deferred `onOpen`** — Phase 2 mounts and styles it but does NOT open the palette (Phase 6A wires `onOpen` + the hotkey). The component ships `minWidth: 200`; Phase 2 sizes it to the DESIGN-SPEC §1 **250px** via its `className`/style override (the trigger exposes a `className` prop) rather than editing the shared component's default. No new trigger component is authored. _(US-2.7)_

### D. ChatShell (`packages/chat-surface/src/shell/ChatShell.tsx`)

- **FR-2.17** `ChatShell` MUST resolve the destination list for the rail: when a `DeploymentProfile` port/prop is available it uses `destinationsForProfile`, otherwise it uses `SHELL_DESTINATIONS` (web-safe default). The resolved list MUST be passed to `AppRail`. _(US-2.1, US-2.5)_
- **FR-2.18** `FULL_BLEED_DESTINATIONS` MUST include `run` (and `settings` handling) so the Topbar and context column are suppressed for Run/Settings, matching chats. _(US-2.7)_
- **FR-2.19** ChatShell MUST NOT crash or mis-label when `activeDestination` is `run`/`activity`; the context-panel/topbar label fallbacks MUST resolve from the registry, not `undefined`. _(US-2.7)_

### E. Desktop outlet & bootstrap (`apps/desktop/renderer/`)

- **FR-2.20** The desktop mount MUST stop rendering `DesktopPlaceholder` as the shell's children and MUST render a `DestinationOutlet` that maps the active destination to its surface (or an honest `DestinationPlaceholder`). _(US-2.6)_
- **FR-2.21** The desktop host MUST seed `activeDestination` from `defaultDestinationForProfile` (→ `run`) and pass the profile-derived destination list into the shell. _(US-2.3)_
- **FR-2.22** The desktop host MUST pass `onOpenSettings` to the shell, wired to a desktop settings target (Phase 2 stub → Phase 5 real). _(US-2.4)_
- **FR-2.23** The outlet MUST route legacy/folded slugs `agents` and `inbox` to the `activity` surface. _(US-2.8)_
- **FR-2.24** The desktop host MUST resolve the deployment profile through the **`DeploymentProfile` port** (0D, or PR-2.1's minimal port) and default to `single_user_desktop`. **Constraint (verified):** `ENTERPRISE_DEPLOYMENT_PROFILE` currently lives **only** in `apps/desktop/main/services/service-env.ts` (line 101) as env passed to the spawned Python child services — **no preload/bridge exposes it to the renderer** (grep of `apps/desktop/` finds it only in `service-env.ts` + its test). Therefore Phase 2 seeds the renderer's profile provider with the **static default `single_user_desktop`** (the only profile a desktop build ships); a real main→renderer profile bridge is explicitly **out of scope** here and only becomes necessary when a `team` desktop build exists (tracked in §11 R8). The provider seam MUST still be the port so that bridge can later supply the value without touching `chat-surface`. _(US-2.2)_

### F. a11y / theming

- **FR-2.25** All rail/topbar/outlet colors MUST reference design-system CSS variables (`--color-*`) — no hardcoded hex — so light+dark, `[data-accent]`, and `[data-density]` all follow the token system, and only the single accent is used decoratively. _(US-2.9)_
- **FR-2.26** Focus-visible on rail/topbar controls MUST show a 2px `--color-accent` ring (offset 2); glyphs stay `aria-hidden`; reduced-motion zeroes transitions. _(US-2.9)_

---

## 5. Architecture & system design

### 5.1 Single source of truth

- **Destinations (canonical owner): `packages/chat-surface/src/shell/destinations.ts`.** Today three components (`AppRail`, `Topbar`, `ChatShell`) each import the `SHELL_DESTINATIONS` constant directly. This phase keeps `destinations.ts` as the _only_ registry but changes the shape from "one flat list" to "one registry + profile-derived views":
  - one canonical `DESTINATION_REGISTRY` (slug → metadata incl. which profiles include it and the profile-specific label),
  - derived selectors `destinationsForProfile(profile)`, `defaultDestinationForProfile(profile)`,
  - the retained legacy `SHELL_DESTINATIONS` (web) is itself _derived from / consistent with_ the registry, not a parallel hand-list.
    This avoids a second source of truth for the slug↔label mapping (the original file's own stated invariant). **Not a second registry:** `packages/chat-surface/src/destinations/` holds per-destination _view content_ (one subdir per legacy slug), not slug metadata — it is untouched this phase and gains no `run`/`activity` subdir until Phase 4.
- **Command trigger (canonical owner): the existing `CommandPaletteTrigger`** (`packages/chat-surface/src/shell/CommandPaletteTrigger.tsx`, exported from the package root). Phase 2 reuses it — it MUST NOT be duplicated by a new "static trigger". The topbar imports and mounts it; `onOpen` is a deferred host handler until Phase 6A.
- **Deployment profile (canonical owner): the `DeploymentProfile` port from Phase 0D** (`PLAN.md` 0D). The renderer must not read `process.env`/`window` directly for the profile — it flows through the port so `chat-surface` stays framework-agnostic. The authoritative _value_ originates in `apps/desktop/main/services/service-env.ts` (`ENTERPRISE_DEPLOYMENT_PROFILE = "single_user_desktop"`) and the shared constant `packages/service-contracts/src/copilot_service_contracts/deployment_profile.py` (`PROFILE_SINGLE_USER_DESKTOP`). **⚠ Gap:** no TS `DeploymentProfile` port exists in `packages/chat-surface/src/ports/` yet (0D not present in this worktree) — see §11 R1 and §10.
- **Shell geometry** stays local to each component (rail width, topbar height) — geometry is not a token (per the file's existing rationale) — but all _colors/type_ move to design-system tokens (SSOT = `packages/design-system/src/styles.css`).

### 5.2 Boundaries & ports (respect `CLAUDE.md`)

- No `apps/*` imports another `apps/*` `src/`. The desktop outlet composes **only** `chat-surface` exports (`DestinationPlaceholder`, shell primitives) + desktop-local components; it never imports `apps/frontend/src`.
- `chat-surface` stays **framework-agnostic**: no bare `window`/`document`/`fetch`/`localStorage`. Profile arrives via the **`DeploymentProfile` port** (new, 0D) or as a plain prop to `ChatShell`; routing via the existing **`Router`** port (`packages/chat-surface/src/ports/Router.ts` re-exports the `Router` type whose home is `packages/chat-surface/src/routing/router.ts`), presence via **`PresenceSignal`** (`ports/PresenceSignal.ts` → `presence/presence-signal.ts`), storage via **`KeyValueStore`** (`ports/KeyValueStore.ts` → `storage/key-value-store.ts`). The rail's Settings/avatar are pure props (`onOpenSettings`, avatar data) supplied by the host.
- Ports used this phase: **Router** (rail click → host route), **KeyValueStore** (unchanged), **PresenceSignal** (unchanged), **DeploymentProfile** (new/0D), plus the `onNavigate`/`onOpenSettings` callback props.

### 5.3 Data flow & key types

- `DeploymentProfile = "single_user_desktop" | "team"` (mirror of the Python constant; TS type lives with the port).
- `ShellDestinationSlug` (widened union) and `ShellDestination { slug; label; icon? }` — `destinations.ts`.
- Flow (desktop): the renderer seeds `DeploymentProfileProvider` with the **static default `single_user_desktop`** (no main→renderer profile bridge exists today — see FR-2.24) → `useDeploymentProfile()` → `defaultDestinationForProfile` seeds `activeDestination` state; `destinationsForProfile` → `<ChatShell destinations>` → `<AppRail destinations>`. Rail click → `onNavigate(slug)` → desktop `setActiveDestination` → `<DestinationOutlet destination>` renders the surface/placeholder. (When a `team` desktop build later needs a real value, the port's provider is swapped to read a main-supplied profile via a preload bridge — no `chat-surface` change.)
- Flow (web, unchanged): no profile provider → `ChatShell` falls back to `SHELL_DESTINATIONS` → `AppRail` renders 12 → `onNavigate` → `App.tsx` `handleRailNavigate` → `router.navigate({screen:"chat", destination})`.

### 5.4 Decision — preserve slug identity, relabel per profile (regression-safe)

The plan text says "solo = [run, chats, projects, activity, tools, skills]". Read literally as _slug_ renames (`connectors`→`tools`, old `tools`→`skills`), this would break the web host, whose `HashRouter.ts` validates URL segments against the slug set and whose `routes.ts`/`App.tsx` switch on `connectors`/`tools`. **Decision:** treat "relabel connectors→Tools, tools→Skills" as a **label** change scoped to the solo/team profile views, keeping the underlying **slug identity stable** (`connectors`, `tools`). Only two genuinely new slugs are added (`run`, `activity`). This satisfies the IA intent, keeps web URLs/tests byte-identical, and is the minimal widening of the union. _(Documented divergence from a literal reading of the plan; see §11 R2.)_

### 5.5 Reuse vs new

| Concern                    | Action                                                                          | Path                                                                                   |
| -------------------------- | ------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Destination registry       | **Reuse + extend**                                                              | `packages/chat-surface/src/shell/destinations.ts`                                      |
| App rail                   | **Reuse + restyle** (v2 geometry, destinations prop, foot avatar)               | `packages/chat-surface/src/shell/AppRail.tsx`                                          |
| Topbar                     | **Reuse + restyle** (46px; breadcrumb → title/subtitle; mounts command trigger) | `packages/chat-surface/src/shell/Topbar.tsx`                                           |
| Command/search trigger     | **Reuse (do not re-author)** — mount existing, `onOpen` deferred to 6A          | `packages/chat-surface/src/shell/CommandPaletteTrigger.tsx`                            |
| Shell grid / full-bleed    | **Reuse + extend** (profile-aware list, run/settings full-bleed)                | `packages/chat-surface/src/shell/ChatShell.tsx`                                        |
| "Not built yet" panel      | **Reuse** (already the sanctioned primitive)                                    | `packages/chat-surface/src/shell/DestinationPlaceholder.tsx`                           |
| Deployment profile port    | **New** (or consume 0D)                                                         | `packages/chat-surface/src/ports/DeploymentProfile.ts` (+ provider under `providers/`) |
| Desktop destination outlet | **New**                                                                         | `apps/desktop/renderer/DestinationOutlet.tsx`                                          |
| Desktop mount wiring       | **Modify**                                                                      | `apps/desktop/renderer/bootstrap.tsx`                                                  |
| Static placeholder         | **Remove from mount** (delete file in Phase 6C)                                 | `apps/desktop/renderer/DesktopPlaceholder.tsx`                                         |
| Shell barrel exports       | **Modify** (export new selectors/port)                                          | `packages/chat-surface/src/shell/index.ts`, `packages/chat-surface/src/index.ts`       |

---

## 6. Affected files / component inventory

### Create

- `packages/chat-surface/src/ports/DeploymentProfile.ts` — `DeploymentProfile` type + port interface (**only if Phase 0D has not already delivered it**; otherwise reuse 0D's).
- `packages/chat-surface/src/providers/DeploymentProfileProvider.tsx` + `useDeploymentProfile()` (same caveat).
- `apps/desktop/renderer/DestinationOutlet.tsx` — maps active destination → surface / `DestinationPlaceholder`; folds `agents`/`inbox`→`activity`.
- `apps/desktop/renderer/DestinationOutlet.test.tsx`.
- New test files: `packages/chat-surface/src/shell/destinations.test.ts` (currently none).

### Modify

- `packages/chat-surface/src/shell/destinations.ts` — widen union (+`run`,`activity`,`members`,`billing`), registry, `destinationsForProfile`, `defaultDestinationForProfile`; keep `SHELL_DESTINATIONS`/`DEFAULT_SHELL_DESTINATION`.
- `packages/chat-surface/src/shell/AppRail.tsx` — 48px/34px geometry, `destinations` prop, active left-bar, brand mark, `run`/`activity` glyphs, foot avatar.
- `packages/chat-surface/src/shell/Topbar.tsx` — 46px; replace `<label> / <leaf>` breadcrumb with title (registry-resolved) + subtitle (from `leaf`); mount the existing `CommandPaletteTrigger` (imported, not re-authored) with a deferred `onOpen`; registry-safe title fallback for `run`/`activity`.
- `packages/chat-surface/src/shell/ChatShell.tsx` — resolve profile→destinations, pass to `AppRail`, add `run`/`settings` to full-bleed.
- `packages/chat-surface/src/shell/index.ts`, `packages/chat-surface/src/index.ts` — export new selectors/port.
- `packages/chat-surface/src/shell/AppRail.test.tsx`, `Topbar.test.tsx` — update for widened union / new geometry while preserving legacy-path coverage.
- `apps/desktop/renderer/bootstrap.tsx` — profile provider, seed default = `run`, pass `destinations` + `onOpenSettings`, render `<DestinationOutlet>` instead of `<DesktopPlaceholder>`.

### Delete (usage now; file deletion in Phase 6C)

- `apps/desktop/renderer/DesktopPlaceholder.tsx` — removed from the mount this phase; file + its test (`DesktopPlaceholder` has no separate test file found) retired in `PLAN.md` 6C to keep this phase's diff focused.

### Regression-sensitive (touched read-only — MUST NOT change behavior)

- `apps/frontend/src/app/App.tsx` (rail wiring, `handleRailNavigate`, `onOpenSettings`), `apps/frontend/src/app/HashRouter.ts` (slug validation), `apps/frontend/src/app/routes.ts` (`ROOT_DESTINATION`). These consume the widened union type; they MUST still compile and behave identically. If the widened union forces an exhaustiveness update in web switch statements, that is an allowed _type-only_ change with no behavior change — verify via web tests.

---

## 7. PR / commit breakdown

Ordered, each independently mergeable, ≤ ~1000 LOC, leaves `main` + web green.

### PR-2.1 — DeploymentProfile port (chat-surface)

- **Scope:** Add `DeploymentProfile` type + port + provider/hook to `chat-surface` (skip/rebase if Phase 0D already landed it — then this PR only re-exports it). Framework-agnostic; no `window`/env reads.
- **Files:** `packages/chat-surface/src/ports/DeploymentProfile.ts`, `packages/chat-surface/src/providers/DeploymentProfileProvider.tsx`, `packages/chat-surface/src/ports/index.ts`, `packages/chat-surface/src/index.ts`, `+ .test.ts`.
- **Deps:** none (or 0D).
- **Acceptance:** `useDeploymentProfile()` returns provider value; defaults to `single_user_desktop`; unit test covers default + team. Package typechecks; web unaffected (not consumed yet).
- **Size:** S.

### PR-2.2 — destinations.ts profile-gated registry

- **Scope:** Widen `ShellDestinationSlug` (+`run`,`activity`,`members`,`billing`); introduce registry; `destinationsForProfile`, `defaultDestinationForProfile`; keep `SHELL_DESTINATIONS`/`DEFAULT_SHELL_DESTINATION`; solo/team labels ("Tools"/"Skills").
- **Files:** `packages/chat-surface/src/shell/destinations.ts`, `packages/chat-surface/src/shell/index.ts`, `packages/chat-surface/src/index.ts`, **new** `destinations.test.ts`. Update `AppRail.tsx` `Glyph` switch for exhaustiveness (`run`/`activity` cases) so the package still typechecks.
- **Deps:** PR-2.1 (profile type).
- **Acceptance:** `destinations.test.ts` asserts solo = 6 in order with correct labels, team = 9, unknown→solo, default=`run`, legacy `SHELL_DESTINATIONS` unchanged (12, original labels). `npm run typecheck --workspace @0x-copilot/frontend` green (union widening compiles).
- **Size:** M.

### PR-2.3 — AppRail v2 restyle + destinations prop + foot

- **Scope:** 48px rail / 34px buttons / brand mark / active left-bar; `destinations` prop (default `SHELL_DESTINATIONS`); Settings gear + 26px avatar in foot; `run`/`activity` glyphs finalized; all colors via tokens.
- **Files:** `packages/chat-surface/src/shell/AppRail.tsx`, `AppRail.test.tsx`.
- **Deps:** PR-2.2.
- **Acceptance:** Legacy render (no `destinations` prop) still shows 12 (or test updated with equivalent coverage); passing a solo list renders 6 with "Tools"/"Skills" labels; active button has left-bar marker; avatar renders only with `onOpenSettings`; a11y attrs intact. Vitest green.
- **Size:** M.

### PR-2.4 — Topbar v2 (breadcrumb → title/subtitle + trigger)

- **Scope:** 46px height; replace the `<label> / <leaf>` breadcrumb with title (registry-resolved) + subtitle (from `leaf`); mount the **existing** `CommandPaletteTrigger` (import + place right-aligned, `onOpen` = deferred no-op); registry-safe title for `run`/`activity`. No new trigger component.
- **Files:** `packages/chat-surface/src/shell/Topbar.tsx`, `Topbar.test.tsx` (rewritten). Imports `CommandPaletteTrigger` (unchanged).
- **Deps:** PR-2.2.
- **Acceptance:** height=46; `command-palette-trigger` present with `⌘K` hint and does NOT open a palette when clicked (deferred handler); title resolves for all slugs incl. `run`/`activity`; subtitle shows the leaf and is omitted for em-dash/empty leaf (breadcrumb tests rewritten to the title/subtitle contract with no coverage loss). Vitest green.
- **Size:** S.

### PR-2.5 — ChatShell profile-aware grid + full-bleed run/settings

- **Scope:** Resolve profile → destinations, pass to `AppRail`; add `run` (and settings handling) to `FULL_BLEED_DESTINATIONS`; registry-safe context/topbar fallback for new slugs; optional `destinations` prop passthrough for hosts.
- **Files:** `packages/chat-surface/src/shell/ChatShell.tsx`, `ChatShell.test.tsx`.
- **Deps:** PR-2.1, PR-2.2, PR-2.3, PR-2.4.
- **Acceptance:** With no profile provider → 12-dest rail (web path) + topbar shown; with solo provider → 6-dest rail + Run suppresses topbar/context; ChatShell.test covers both. Web app renders unchanged (spot test).
- **Size:** M.

### PR-2.6 — Desktop outlet + bootstrap wiring (remove DesktopPlaceholder)

- **Scope:** New `DestinationOutlet` (maps solo destinations → `DestinationPlaceholder` stubs, folds `agents`/`inbox`→`activity`, reserves Run slot); `bootstrap.tsx` provides profile, seeds default `run`, passes `destinations` + `onOpenSettings` (stub target), renders outlet; remove `DesktopPlaceholder` from mount.
- **Files:** `apps/desktop/renderer/DestinationOutlet.tsx` (+`.test.tsx`), `apps/desktop/renderer/bootstrap.tsx`, `apps/desktop/renderer/bootstrap.test.tsx`.
- **Deps:** PR-2.1…PR-2.5.
- **Acceptance:** Renderer boots on **Run**; rail shows 6; clicking each destination swaps the outlet; `DesktopPlaceholder` no longer in the tree; Settings gear+avatar visible and gear opens the stub; `agents`/`inbox`→Activity. Vitest green + live desktop smoke (§8).
- **Size:** M.

---

## 8. Testing plan

Runner: **vitest** for all TS packages/apps via `npm run test --workspace <pkg>` (`@0x-copilot/chat-surface`, `@0x-copilot/frontend`, `@0x-copilot/desktop`). No Python in this phase.

### Unit

- `packages/chat-surface/src/shell/destinations.test.ts` (**new**):
  - `destinationsForProfile("single_user_desktop")` → slugs `[run, chats, projects, activity, connectors, tools]`, labels `[Run, Chats, Projects, Activity, Tools, Skills]` — **FR-2.3, FR-2.8**.
  - `destinationsForProfile("team")` → 9 entries ending `[team, members, billing]` — **FR-2.4**.
  - unknown/undefined profile → solo set (no team leakage) — **FR-2.5**.
  - `defaultDestinationForProfile(...)` → `"run"` — **FR-2.6**.
  - `SHELL_DESTINATIONS` still equals the legacy 12 with original labels/order — **FR-2.7**.
- `packages/chat-surface/src/shell/AppRail.test.tsx` (**update**):
  - default (no `destinations` prop) renders legacy set; with solo `destinations` renders 6 with "Tools"/"Skills" — **FR-2.9, FR-2.8**.
  - width/geometry via style assertions (48/34), brand mark present & routes to Run — **FR-2.10**.
  - active button exposes left-bar marker (data attr / style) + `aria-current="page"` — **FR-2.11, FR-2.14**.
  - `run`/`activity` buttons render a glyph (no exhaustiveness crash) — **FR-2.12**.
  - foot: Settings + avatar present iff `onOpenSettings` supplied; click fires handler — **FR-2.13**.
- `packages/chat-surface/src/shell/Topbar.test.tsx` (**rewrite for the title/subtitle contract**): height=46 (style assertion); title text resolves from the registry for `chats`, `run`, `activity`; `leaf="c-77"` renders as the subtitle, `leaf=""`/undefined renders **no** subtitle (the em-dash/empty-string cases from the old breadcrumb are re-expressed as "no subtitle", preserving that coverage); `command-palette-trigger` is present with the `⌘K` hint and a click does NOT open a palette (deferred `onOpen` spy called, no palette in the DOM) — **FR-2.15, FR-2.16**.
- `packages/chat-surface/src/shell/ChatShell.test.tsx` (**update**): no-profile → 12-dest rail + topbar; solo profile → 6-dest rail + Run suppresses topbar/context; `activity` label resolves — **FR-2.17, FR-2.18, FR-2.19**.
- `apps/desktop/renderer/DestinationOutlet.test.tsx` (**new**): each solo slug renders its placeholder/surface; `agents`/`inbox` render the Activity surface — **FR-2.20, FR-2.23**.
- `apps/desktop/renderer/bootstrap.test.tsx` (**update**): initial `activeDestination` = `run`; `DesktopPlaceholder` absent; `onOpenSettings` passed and invocable; rail has 6 — **FR-2.20, FR-2.21, FR-2.22, FR-2.24**.

### Integration

- ChatShell + AppRail + DeploymentProfileProvider (mocked profile) render together: switching the mocked profile value from solo→team re-renders 6→9 rail buttons — **FR-2.17, FR-2.4**.
- Router port: rail click on a solo destination calls `onNavigate(slug)` and the outlet swaps (mocked Router) — **FR-2.20**.

### E2E / live desktop smoke (`apps/desktop/SMOKE.md`)

Stage runtime (`node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64`), then `COPILOT_RUNTIME_DIR=… npm run dev --workspace @0x-copilot/desktop`:

1. App boots to the **Run** destination; rail shows exactly 6 icon-only buttons + Settings gear + avatar in foot; **no** "phase 1" placeholder text.
2. Click each of Chats/Projects/Activity/Tools/Skills → outlet swaps to that destination's honest placeholder; active rail button shows the left accent bar; Run/Settings hide the topbar.
3. Click Settings gear → stub settings target opens without console error.
4. Toggle OS light/dark → shell recolors via tokens (no hardcoded panels); tab through the rail → focus ring visible, active announces `aria-current`.
   _(Unit fakes have hidden real-run breakage before — this live pass is required, per MEMORY "Virtuals launch effort".)_

### Regression guard (web must stay behaviorally identical)

- `npm run test --workspace @0x-copilot/frontend` and `npm run typecheck --workspace @0x-copilot/frontend` green.
- Web rail still renders the legacy 12; URLs `#/connectors`, `#/tools`, `#/agents`, etc. still resolve (HashRouter slug set unchanged) — assert via existing `HashRouter`/`App` tests.
- `ROOT_DESTINATION` remains `"chats"`; web landing unchanged.

### FR → test coverage map

FR-2.1→typecheck+destinations.test; 2.2/2.3/2.4/2.5/2.6/2.7/2.8→destinations.test; 2.9→AppRail.test; 2.10/2.11/2.12/2.13/2.14→AppRail.test; 2.15/2.16→Topbar.test; 2.17/2.18/2.19→ChatShell.test; 2.20/2.23→DestinationOutlet.test; 2.21/2.22/2.24→bootstrap.test; 2.25/2.26→AppRail/Topbar/ChatShell style+a11y assertions + live smoke step 4.

---

## 9. UI/UX acceptance checklist

Grounded in `DESIGN-SPEC.md` §0/§1. All colors/type via design-system tokens (no hardcoded hex).

**Geometry & tokens**

- [ ] Rail width **48px**; brand mark **32×32** (top) → Run; destination buttons **34×34**; item gap 4px; foot hairline `1px --color-border`.
- [ ] Avatar **26px** circle in foot; Settings gear 34px above it.
- [ ] Topbar **46px**; title 13.5px semibold + subtitle 11.5px muted (left); the **existing `CommandPaletteTrigger`** on the right rendered at **250px** (DESIGN-SPEC §1) with the `⌘K` hint — not a re-authored control.
- [ ] Destination glyphs match DESIGN-SPEC §7 for the solo set: Run = `run`, Chats = `chats`, Projects = `folder`, Activity = `activity`, Tools (slug `connectors`) = `plug`, Skills (slug `tools`) = `skill`. New `run`/`activity` glyphs added; the `connectors`/`tools` glyphs are swapped to `plug`/`skill` for the solo/team views (legacy web rail glyphs unchanged). If a glyph swap is deferred, it is listed as a Phase-2 follow-up, not silently skipped.
- [ ] Radii `--r 8 / --r-sm 6`; base 13px; headings `--font-display` 600 / −.01em; mono only for metadata (`--font-mono`).
- [ ] Active destination = `--color-bg-elevated` (panel2) bg **+ 2px `--color-accent` left bar**; inactive text `--color-text-subtle`.

**States**

- [ ] default / hover (subtle bg lift) / **active** (left bar) / **focus-visible** (2px `--color-accent` ring, offset 2).
- [ ] Outlet: **ready** (destination surface or honest placeholder), **empty** = `DestinationPlaceholder` naming intent+phase (no fake data, no Retry), **loading/error** N/A in Phase 2 (deterministic placeholders).
- [ ] Topbar **suppressed** on Run & Settings (full-height); shown on Chats?—chats stays full-bleed as today.
- [ ] Streaming/live badge (Run "1" accent-bg mono badge) reserved but inert until Phase 3.

**a11y**

- [ ] Rail is `<nav aria-label="Copilot destinations">`; each button has accessible name (label) + `aria-current="page"` when active; glyphs `aria-hidden`.
- [ ] Full keyboard traversal (Tab/Shift-Tab) reaches every rail control incl. Settings/avatar; Enter/Space activate.
- [ ] `prefers-reduced-motion` / `[data-reduce-motion="1"]` zeroes hover/active transitions.
- [ ] Contrast: active/inactive text and accent bar meet AA on `--color-bg`/panel2 in both themes.

**Theming & discipline**

- [ ] Light + dark correct (token-driven); `[data-accent=sky|jade|ember|violet]` shifts only the accent; `[data-density=compact|spacious]` respected.
- [ ] **Single-accent discipline**: only sky accent appears decoratively; no per-destination brand color; jade/ember/amber reserved for semantic (live/destructive/warning) use only — none introduced in the shell chrome this phase.
- [ ] Component reuse: `AppRail`, `Topbar`, `ChatShell`, `DestinationPlaceholder` restyled to tokens (not re-authored).

---

## 10. Dependencies & sequencing

**Upstream (blocked by):**

- **Phase 0A** — design-system `styles.css` + fonts wired into the desktop renderer (else v2 tokens don't apply). `PLAN.md` 0A.
- **Phase 0B** — v2 token _values_ (rail/topbar consume them; exactness like `--ink #09090b` is 0B's job). `PLAN.md` 0B.
- **Phase 0D** — `DeploymentProfile` port. **Not present in this worktree** (only the Python constant + desktop-main env exist). PR-2.1 delivers it if 0D hasn't; otherwise consumes 0D. This is the one hard external dependency.

**Independent of:** Phase 1 (interaction-layer hoist) — Phase 2 can proceed in parallel.

**Downstream (blocks):**

- **Phase 3** (Run cockpit) — mounts `ThreadCanvas` into the `run` outlet slot this phase reserves; needs full-bleed suppression from PR-2.5.
- **Phase 4** (list destinations) — fill the outlet placeholders for Chats/Projects/Activity/Tools/Skills.
- **Phase 5** (Settings) — fills the `onOpenSettings` stub target.
- **Phase 6A** (⌘K palette) — wires the topbar command trigger.

**Internal PR DAG:** PR-2.1 → PR-2.2 → {PR-2.3, PR-2.4} → PR-2.5 → PR-2.6.

---

## 11. Risks & mitigations

| #   | Risk                                                                                                                                                              | Likelihood          | Impact                      | Mitigation / rollback                                                                                                                                                                                                                        |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- | --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1  | Phase 0D `DeploymentProfile` port not merged → Phase 2 can't gate                                                                                                 | High (absent today) | Blocks 2B/2E                | PR-2.1 ships a minimal port in `chat-surface`; rebase onto 0D if it lands first.                                                                                                                                                             |
| R2  | Widening/relabeling breaks the web app's slug routing/tests                                                                                                       | Med                 | Web regression              | **Decision §5.4**: preserve slug identity, relabel per-profile only; keep `SHELL_DESTINATIONS`; run web typecheck+tests each PR; AppRail defaults to legacy list when no `destinations` prop.                                                |
| R3  | Exhaustive `Glyph`/switch statements fail to compile on union widening                                                                                            | Med                 | Build red                   | Add `run`/`activity` cases in the same PR that widens the union (PR-2.2); rely on TS exhaustiveness to _find_ every switch.                                                                                                                  |
| R4  | Token values still off-spec (`--color-bg #0f0f10` vs `--ink #09090b`) make shell look wrong                                                                       | Med                 | Cosmetic                    | Out of scope (0B owns values); Phase 2 only references variables — flagged, not fixed.                                                                                                                                                       |
| R5  | Removing `DesktopPlaceholder` before Run/lists exist leaves blank surfaces                                                                                        | Low                 | UX confusion                | Outlet renders honest `DestinationPlaceholder` (names intent+phase), never blank.                                                                                                                                                            |
| R6  | Existing AppRail/Topbar/ChatShell tests hard-code "12 destinations"                                                                                               | High                | Test red                    | Update those tests in the same PR (PR-2.3/2.4/2.5) with equivalent legacy-path + new solo-path coverage; no coverage loss.                                                                                                                   |
| R7  | Settings entry wired to a non-existent Phase 5 surface                                                                                                            | Med                 | Dead click                  | Wire `onOpenSettings` to an explicit Phase-2 stub target (visible "Settings — Phase 5" placeholder), not a no-op; smoke-verify no console error.                                                                                             |
| R8  | No main→renderer profile bridge exists (`ENTERPRISE_DEPLOYMENT_PROFILE` is child-service env only, `service-env.ts:101`) → renderer can't read the "real" profile | High (confirmed)    | None for solo               | Desktop is always `single_user_desktop`; seed the provider with that static default (FR-2.24). A preload profile bridge is deferred until a `team` desktop build exists; the port seam means adding it later needs no `chat-surface` change. |
| R9  | Author re-writes a "static" command trigger, duplicating the existing exported `CommandPaletteTrigger`                                                            | Med                 | SSOT drift / dead component | FR-2.16 + §5.5 mandate **reuse** of the exported `CommandPaletteTrigger` with a deferred `onOpen`; the reuse table flags "do not re-author"; review rejects any new trigger component.                                                       |

**Flag/rollback:** each PR is behind the additive `destinations` prop / profile provider; reverting PR-2.6 restores the placeholder mount without touching `chat-surface`. No feature flag needed — web path is the untouched default.

---

## 12. Definition of done

- [ ] FR-2.1…FR-2.26 met.
- [ ] `destinations.ts` is the profile-gated SSOT: solo=6, team=9, unknown→solo, default=`run`; legacy `SHELL_DESTINATIONS` intact.
- [ ] `AppRail` (48/34/brand/left-bar/foot avatar+settings) and `Topbar` (46/command trigger) match DESIGN-SPEC §0/§1; all colors token-driven.
- [ ] Desktop boots on **Run**, renders the 6-destination outlet; `DesktopPlaceholder` no longer mounted; `agents`/`inbox` fold to Activity; Settings gear+avatar wired.
- [ ] Unit + integration vitest green for `@0x-copilot/chat-surface` and `@0x-copilot/desktop`; **live desktop smoke** (§8) passes.
- [ ] **Web unregressed:** `@0x-copilot/frontend` tests + typecheck green; legacy 12-rail, URLs, and `ROOT_DESTINATION` unchanged.
- [ ] UI/UX checklist (§9) passed in light + dark, all densities, focus/reduced-motion, single-accent.
- [ ] Docs updated: `apps/desktop/README.md` (destination outlet contract) and this PRD's checkboxes ticked; no dead code introduced (DesktopPlaceholder file deletion scheduled to Phase 6C, noted).
- [ ] Divergences flagged: slug-identity-preservation decision (§5.4) and 0D dependency (§10) recorded.
      </content>
      </invoke>
