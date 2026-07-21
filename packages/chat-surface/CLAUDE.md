# chat-surface

`@0x-copilot/chat-surface` is the **single-source-of-truth interaction layer** for the
0xCopilot product. Both deployable app substrates — `apps/frontend` (web) and
`apps/desktop` (Electron) — mount the SAME components from this package and bind
their data through their OWN host adapters. There is no second copy of the shell,
the destinations, the Run cockpit, the Settings surface, the ⌘K palette, or the
message/composer/citation/approval/subagent families. If a UI concept is shared by
web and desktop, it lives here.

> Read this before adding a component, a destination, a settings section, an
> export, or anything that touches the substrate boundary.

## The one hard rule: substrate-agnostic

This package is **framework-agnostic and browser-primitive-free**. It never touches
`window`, `document`, `history`, `navigator`, `location`, `localStorage`,
`sessionStorage`, `fetch`, `EventSource`, `XMLHttpRequest`, or `WebSocket`. Those are
banned by `eslint.config.js` (`no-restricted-globals`) and the package cannot import
from a host app (`no-restricted-imports` blocks `@0x-copilot/frontend`, `apps/*`).

Anything substrate-specific goes through a **port** (an interface defined here that
the host implements) or lives in the host app itself. The ports (`src/ports/`,
re-exported from `src/index.ts`) are:

| Port                                                                  | What the host supplies                                                |
| --------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `Transport`                                                           | HTTP + SSE to the facade (`transport.request`, `subscribe`)           |
| `Router`                                                              | URL navigation (`navigate`, `subscribe`, `current`)                   |
| `KeyValueStore`                                                       | Small persisted prefs (Run mode, collapse state)                      |
| `SecretStorage`                                                       | Secret values (web `WebSecretStorage`, desktop native keychain)       |
| `PresenceSignal`                                                      | Tab/window visibility                                                 |
| `DeploymentProfile`                                                   | Runtime profile (`single_user_desktop` \| `team`) that gates the rail |
| `PaletteSearchPort`                                                   | ⌘K search backend                                                     |
| `BadgePort` / `NotificationPort` / `FilePickerPort` / `ClipboardPort` | Phase-0.5 substrate ports                                             |

The single sanctioned substrate touchpoint inside the package is
`LocalStorageKeyValueStore` (the web reference impl of `KeyValueStore`), which uses
`globalThis.localStorage` — the deliberate `globalThis.` prefix marks it as honest
substrate code. Prefer `globalThis.X` over a bare global if you ever must add another.

Ports are exposed to components via React providers (`src/providers/`):
`TransportProvider`/`useTransport`, `RouterProvider`/`useRouter`,
`KeyValueStoreProvider`/`useKeyValueStore`, `SecretStorageProvider`,
`PresenceSignalProvider`, `DeploymentProfileProvider`/`useDeploymentProfile`.

## The web-vs-desktop host adapter pattern

Every component here is **presentational**: it takes normalized data + callbacks as
props (or reads a port via a hook) and owns no fetching. The two host substrates
each provide their own **binder** that fetches over the `Transport` port and wires
callbacks to their own navigation:

- **Web** (`apps/frontend`): `src/app/App.tsx` dispatches per-slug to a
  `features/*/Route.tsx` (or `*Gateway.tsx`) binder that fetches via the frontend's
  `src/api/*` HTTP clients.
- **Desktop** (`apps/desktop`): `renderer/DestinationOutlet.tsx` dispatches per-slug
  to a binder in `renderer/destinationBinders.tsx` that fetches through the shell's
  `Transport` port (IPC → facade).

Because `apps/* → apps/*` imports are a hard boundary, the two binders **cannot
share code** — they intentionally duplicate the same pure projection logic (over
`@0x-copilot/api-types` shapes). The shared home is this package's component
contract, not a shared binder. When you change a destination's props, update BOTH
binders.

## Module map

```
src/
  icons/          canonical line-icon SSOT: <Icon name/> + ICON_PATHS (rail, nav, ⌘K, rows)
  ports/          substrate ports (Transport / Router / KeyValueStore / …) + barrel
  providers/      React providers exposing each port via a hook
  storage/        web reference impls (LocalStorageKeyValueStore, WebSecretStorage)
  presence/       DocumentPresenceSignal (web reference impl)
  routing/        HashRouter, route-table, artifact-uri parser (Router port impls/helpers)
  shell/          AppRail · Topbar · ContextPanel · RightRail · ChatShell ·
                  DestinationPlaceholder · destinations.ts (slug↔label SSOT) ·
                  shortcuts.ts (chord SSOT) · CommandPalette + ⌘K hooks · shell primitives
  destinations/   the destination surfaces (run, chats, activity, connectors, tools,
                  projects, + folded/legacy: home, inbox, todos, agents, library,
                  memory, routines, team, skills)
  settings/       SettingsSurface + settingsNav.ts (nav SSOT) + section bodies + primitives
  thread-canvas/  ThreadCanvas + swimlanes + TcChat + eventProjector (the Run cockpit canvas)
  messages/       streaming markdown, reasoning, citation hrefs
  composer/       Composer + AssistantComposer + model/tool/depth controls
  citations/      citation registry + Sources surfaces
  subagents/      subagent/fleet cards + projectSubagents selector
  approvals/      ApprovalCard + receipt + undo countdown
  workspace/      right-rail WorkspacePane + tab bodies (Sources/Agents/Draft/Approvals/Skills)
  surfaces/       surface-renderer registry + tier-2/tier-3 loaders + adapter contract
  refs/           ItemLink cross-destination reference registry
```

## The destinations model + profile gating

`src/shell/destinations.ts` is the **single source of truth for slug ↔ label**. A
single `DESTINATION_REGISTRY` maps each slug to its canonical label (and an optional
`profileLabel`); per-view ORDER is expressed as slug-only arrays, so there is never a
second slug↔label list.

- `SHELL_DESTINATIONS` / `DEFAULT_SHELL_DESTINATION` — the frozen legacy 12-slug web
  rail. **Frozen contract** (`destinations.test.ts`, FR-2.7): slug identity, order,
  and labels must not change. It is the web-safe fallback `ChatShell` uses only when
  no `DeploymentProfile` provider is mounted, plus the URL/routing union.
- `destinationsForProfile(profile)` — the RENDERED rail, derived from the registry:
  - `single_user_desktop` (default) → the **6-destination solo view**:
    `[Run, Chats, Projects, Activity, Tools, Skills]`
    (slugs `run, chats, projects, activity, connectors, tools`).
  - `team` → the 6 solo destinations plus `Team, Members, Billing`.
  - unknown/undefined → the solo set (fail-safe: team surfaces never leak).
- `defaultDestinationForProfile(_)` → always `"run"` — both profiles land on the Run
  cockpit (the flagship front door, not an archive).

Note the deliberate relabel-without-rename: solo/team show `connectors` as **"Tools"**
and `tools` as **"Skills"**, keeping the underlying slugs (and web URLs/tests)
byte-identical. Only `run`, `activity`, `members`, `billing` are genuinely new slugs.

`ChatShell` reads `useDeploymentProfile()` (safe — falls back to `null`, not a throw,
when no provider) and renders `destinationsForProfile(profile)` when present, else
`SHELL_DESTINATIONS`. Hosts may also pass an explicit `destinations` prop (desktop
does). `run` and `chats` render **full-bleed** (they own full height — no Topbar /
ContextPanel / right rail); Settings is likewise full-height via a flag.

## The Run cockpit

`RunDestination` (`destinations/run/`) is the flagship. It is a **composition shell**
that wires three already-built pieces:

- `useRunSession` — resolves the conversation's active/selected run and streams its
  events (Transport-port SSE) into an append-only array.
- `useRunMode` — KeyValueStore-backed Studio/Focus mode + the ⌘M toggle (gated to
  `enabled`, i.e. Run is the active destination).
- `ThreadCanvas` — the single-mount, mode-driven canvas (center surface + chat column
  - bottom timeline). It projects `session.events` **once** internally
    (`useEventProjector`).

**One event projection (FR-3.3).** The whole cockpit reads exactly one event source —
`useRunSession.events` — projected once inside `ThreadCanvas`. The out-of-canvas
consumers use PURE selectors over that same array, never a second SSE subscription or
projector: `projectSubagents` (fleets + the Agents-tab "N live" count) and
`projectApprovals`/`toApprovalsQueue` (the in-chat `ApprovalCard`/conf-card + the
Approvals-tab count). `RunWorkspaceRail` recomposes the workspace `[Chat · Sources ·
Agents · Approvals]` tabs and receives the single `TcChat` as an injected `chatSlot`,
so mode/tab switches never spawn a second chat mount.

Seams the shell owns: scrub cursor (`scrubbedSeq`; `null` = live) + the "Viewing…"
banner (approvals hidden while scrubbed); the empty/idle `RunEmptyState` goal composer
(mounts when `session.runId === null`; starting a goal binds the fresh run via the
`runId` seam without remounting the shell); and `RunMultiSelect` (renders nothing for
≤1 run; picking one rebinds via `useRunSession.selectRun`).

## Settings

`SettingsSurface` + `settings/settingsNav.ts` (the nav SSOT). `settingsNav.ts` owns
the canonical `SettingsSectionSlug` union, the grouped `SETTINGS_NAV_ITEMS`, and the
profile gate (`settingsNavForProfile` / `visibleSettingsSlugs` / `resolveSettingsSlug`
— team-admin sections only render under `team`; the solo footer shows otherwise). The
surface takes a `renderSection(slug, controller)` slot; the host maps each visible
slug to its section body (both hosts import the bodies from this package's barrel —
`AppearancePage`, `ProviderKeysPage`, `LocalModelsPage`, `ModelBehaviorPage`,
`ApprovalPolicy`, `PrivacyPage`, `NotificationsPage`, `AppLockPage`,
`DeveloperTokensPage`, …). Section bodies are presentational; data-binding ports/
callbacks (`ProviderKeysPort`, `DeveloperTokensPort`, save handlers) are host-owned.

## The ⌘K palette + keyboard shortcuts

- `shell/shortcuts.ts` is the **chord SSOT** (DESIGN-SPEC §6): `SHELL_SHORTCUTS` maps
  each chord to a named `ShortcutIntent` + display metadata. Five `global` chords
  (⌘N new run, ⌘K palette, ⌘, settings, ⌘⇧M local-model picker, ⌘⇧F search activity)
  and seven `run`-scoped chords (⌘M switch mode, ⌘←/⌘→ rewind/step, ⌘L jump-live,
  ⌘. pause, ⌘↵ approve, ⌘⌫ reject). FR-6.15 forbids a second copy — add/adjust chords
  here only. `⌘K` and `⌘,` are the only `inputSafe` chords.
- `useShellShortcuts(callbacks)` attaches ONE keydown listener and dispatches each
  chord to its caller-supplied callback. The host bootstrap wires the **global**
  chords; the **run-scoped** chords are deliberately left undefined at the shell level
  and owned inside the Run cockpit (useRunMode / TcMiniTimeline / TcSwimlanes /
  approvals), so there is never double-wiring.
- `CommandPalette` + `CommandPaletteTrigger` + `useCommandPaletteHotkey` +
  `PaletteHitRow` are the substrate-shared ⌘K surface (Phase 12); the host provides a
  `PaletteSearchPort` and controls open state.

## Barrel-export discipline (`src/index.ts`)

The package's public surface is `src/index.ts`. It is organized into delimited blocks:

```ts
// === Phase N (PR-x.y) short description ===
export { … } from "./…";
// === end Phase N (PR-x.y) ===
```

Rules when adding an export:

- Add it inside the matching phase/PR block (or open a new delimited block in PR
  order). Keep the leading comment explaining what the block hoists and which seam
  stays host-owned.
- Hosts consume the package **only through this barrel** — never deep-import
  `@0x-copilot/chat-surface/src/…` from an app (that crosses the package boundary).
- Branded IDs and cross-destination `ItemRef` types are re-exported from
  `@0x-copilot/api-types` (the SoT) — re-exported here for a single import site, NOT
  redeclared.

## Adding to this package

- **New destination** → build the presentational component here behind ports/props →
  export it via a barrel block → add web `features/*/Route.tsx` binder + `App.tsx`
  dispatch → add desktop binder in `destinationBinders.tsx` + `DestinationOutlet`
  case → add the slug to `destinations.ts` (registry + the profile order arrays).
- **New settings section** → add a section body → export it → add the slug to
  `settingsNav.ts` → both hosts wire it in their `renderSection`.
- **New surface renderer** → add a tier renderer in `@0x-copilot/surface-renderers`
  and register it (`registerAdapter` / `registerSurface`); this package's
  `surfaces/` registry resolves it.

See `docs/plan/desktop-redesign/DEV-GUIDE.md` for the full step-by-step recipes and
the end-to-end architecture map.

## Validation

```bash
npm run typecheck --workspace @0x-copilot/chat-surface   # if configured
npx vitest run --root packages/chat-surface              # unit tests
```

ESLint enforces the substrate boundary — a bare `window`/`fetch`/`localStorage` or an
`apps/*` import fails the lint. Keep it that way.
