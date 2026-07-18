# 0xCopilot Desktop Redesign — Developer Guide

How the redesigned app is wired, and how to extend it. This is the practical
companion to [PLAN.md](./PLAN.md) (north star + locked decisions) and
[design-reference/DESIGN-SPEC.md](./design-reference/DESIGN-SPEC.md) (exact
tokens/dims). Read those for intent; read this for "where does the code live and how
do I add to it."

> **Supersession.** The older `docs/plan/desktop/` PRD set (custom-Electron substrate
> decision, three renderer tiers, agent-generated adapters) is **superseded** for the
> product shell by `docs/plan/desktop-redesign/`. Where the two disagree on shell IA,
> tokens, destinations, or the interaction layer, this redesign is authoritative. The
> older plan remains the reference for the desktop _substrate_ (embedded Postgres +
> supervised Python services, renderer tiers, signing/update) — see
> `apps/desktop/README.md`.

---

## 1. Request path & service boundaries

```
web:      browser → Vite proxy (nginx in prod) ─┐
desktop:  renderer → IPC → main process ─────────┤→ backend-facade:8200 ┬→ backend:8100      (MCP / skills / OAuth / audit)
                                                                        └→ ai-backend:8000  (conversations / runs / events / approvals)
```

Hard rules (also in the root `CLAUDE.md`):

- **Apps call `backend-facade` only** — never `backend` (`:8100`) or `ai-backend`
  (`:8000`) directly, even in dev.
- No deployable component imports another's `src/`. Cross-component integration is
  HTTP, generated contracts (`packages/api-types`), or constants-only
  (`packages/service-contracts`).
- `apps/* → apps/*` is a hard boundary. The web app and the desktop app never import
  each other; they share only the `packages/*` layer.

Both substrates reach the facade through the **`Transport` port** defined in
`chat-surface`. Web binds `getAppTransport()` (an HTTP/SSE client in
`apps/frontend/src/api/`); desktop binds `IpcTransport` (`@0x-copilot/chat-transport`,
proxying over Electron IPC to the main process, which attaches the bearer per request).

## 2. Architecture map

| Package / app                | Role                                                                                                                                                                          |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `packages/chat-surface`      | **SSOT UI layer.** The shell, destinations, Run cockpit, Settings, ⌘K palette, and every message/composer/citation/approval/subagent family. Framework-agnostic — ports only. |
| `packages/design-system`     | **Token SoT** (`styles.css` `:root`) + shared primitives. The v2 "quiet" tokens.                                                                                              |
| `packages/api-types`         | TypeScript contracts for app-facing payloads + branded IDs + `ItemRef`.                                                                                                       |
| `packages/surface-renderers` | Per-SaaS tier renderers (Email / Salesforce / Sheet / Slide) implementing the frozen `SaaSRendererAdapter` contract.                                                          |
| `packages/chat-transport`    | Transport client impls (`IpcTransport`, session types) for the `Transport` port.                                                                                              |
| `apps/frontend`              | Web host. Mounts `ChatShell`; binds data via `features/*/Route.tsx`.                                                                                                          |
| `apps/desktop`               | Electron host. Mounts `ChatShell`; binds data via `renderer/destinationBinders.tsx`. Supervises the embedded Postgres + three Python services.                                |

**Both hosts mount the same shell.** The flow is identical in shape:

```
Host root
 └─ DeploymentProfileProvider  (profile → gates the rail)
     └─ port providers (Transport / KeyValueStore / SecretStorage / Presence / Ports)
         └─ ChatShell (rail + topbar + context + right rail chrome)
             └─ <destination dispatch>   ← the ONE place web & desktop differ
                 web:     App.tsx  if/else on route.destination → features/*/Route.tsx binder
                 desktop: DestinationOutlet.tsx switch on slug → destinationBinders.tsx binder
             └─ PaletteHost (⌘K, mounted once)
```

The **only** substantial divergence between web and desktop is the destination
dispatch + the binders. Everything below the binder — the actual surface component —
is the same code from `chat-surface`.

## 3. The six destinations

Rendered rail for the solo profile (`single_user_desktop`, the desktop default and
the web default): **Run · Chats · Projects · Activity · Tools · Skills**.

| Rail label | Slug         | Surface (chat-surface)         | Web binder               | Desktop binder          |
| ---------- | ------------ | ------------------------------ | ------------------------ | ----------------------- |
| Run        | `run`        | `RunDestination` (the cockpit) | `ChatScreen` under `run` | `RunDestination` direct |
| Chats      | `chats`      | `ChatsArchive`                 | `ChatsArchiveRoute`      | `ChatsBinder`           |
| Projects   | `projects`   | `ProjectsDestination`          | `ProjectsRoute`          | `ProjectsBinder`        |
| Activity   | `activity`   | `ActivityDestination`          | `ActivityRoute`          | `ActivityBinder`        |
| Tools      | `connectors` | `ConnectorsDestination`        | `ConnectorsGateway`      | `ConnectorsBinder`      |
| Skills     | `tools`      | `SkillsDestination`            | `SkillsRoute`            | `SkillsBinder`          |

Note the relabel-without-rename: **Tools** is slug `connectors` and **Skills** is slug
`tools` — the underlying slugs are frozen so web URLs/tests stay byte-identical
(`packages/chat-surface/src/shell/destinations.ts`, `destinations.test.ts`). The
`team` profile adds `Team · Members · Billing`; `defaultDestinationForProfile` is
always `run`.

**Folded slugs.** The old `home · library · inbox · todos · routines · agents ·
memory` surfaces were folded into the six. Web redirects deep-links to them
(`foldedRedirectFor` in `apps/frontend/src/app/routes.ts`); desktop folds
`agents`/`inbox` onto `activity` (`FOLDED_DESTINATIONS` in `DestinationOutlet.tsx`).
Their components remain in `chat-surface` and are exported, but are not on the rail.

### How dispatch works

- **Desktop** — `renderer/DestinationOutlet.tsx` takes the active slug, folds legacy
  slugs, then `switch`es: `run` → `<RunDestination>`; `chats/projects/activity/
connectors/tools` → the matching binder from `destinationBinders.tsx`; anything else
  → the sanctioned `DestinationPlaceholder` (never a blank pane). Each binder uses a
  shared `useSectionLoad` hook driving the 4-state machine (loading / ok / empty /
  error) and fetches over `useTransport()`.
- **Web** — `apps/frontend/src/app/App.tsx` (`CopilotApp`) dispatches on
  `route.destination` in a long if/else, mounting a `features/*/Route.tsx` (or
  `*Gateway.tsx`) binder wrapped in a `data-testid="destination-outlet"` section. The
  binders fetch via `apps/frontend/src/api/*`. Host navigation seams
  (`openRun`, `openRetentionSettings`, `openApprovalSettings`, `openSkillEditor`) are
  passed into the binders as props so the surfaces stay decoupled from the `AppRoute`
  union.

## 4. Recipes

### Add a new destination

1. **Component (chat-surface).** Build the presentational surface under
   `packages/chat-surface/src/destinations/<name>/`. It takes normalized data
   (`SectionResult<T> | null`) + callbacks as props and reads ports via hooks
   (`useTransport`, etc.) — **no** bare `window`/`fetch`/`localStorage`.
2. **Export via a barrel block.** Add a delimited
   `// === Phase N (PR-x.y) … ===` block to `packages/chat-surface/src/index.ts`
   exporting the component + its props type.
3. **Slug (destinations.ts).** Add the slug to `ShellDestinationSlug`, to
   `DESTINATION_REGISTRY` (with `profileLabel` if the rail relabels it), and to the
   appropriate order array (`SOLO_ORDER` / `TEAM_ORDER` / `LEGACY_ORDER`). Update
   `destinations.test.ts` if you touched the frozen legacy contract.
4. **Web binder.** Add `apps/frontend/src/features/<name>/<Name>Route.tsx` (fetch via
   `src/api/*`), then add an `else if (route.destination === "<slug>")` branch in
   `App.tsx` that mounts it inside a `destination-outlet` section.
5. **Desktop binder.** Add a `<Name>Binder` to
   `apps/desktop/renderer/destinationBinders.tsx` (fetch over `useTransport`, drive
   `useSectionLoad`), then add a `case "<slug>":` to `DestinationOutlet.tsx`.

Because web and desktop binders can't share code, keep their projection logic
identical and operate only on `@0x-copilot/api-types` shapes.

### Add a Settings section

1. **Section body (chat-surface).** Add a presentational page under
   `packages/chat-surface/src/settings/`; keep data-binding behind a port/callbacks.
2. **Export it** in the Settings barrel block of `src/index.ts`.
3. **Nav SSOT.** Add the slug to `SettingsSectionSlug`, and an entry to
   `SETTINGS_NAV_ITEMS` in `settings/settingsNav.ts` (pick a `group`, `icon`, optional
   `tag`, optional `profileGate: "team"`). The nav, content router, profile gate, and
   ⌘K palette all read this list — do not add a second section list.
4. **Wire `renderSection`.** In BOTH hosts' Settings mount
   (`apps/desktop/renderer/SettingsMount.tsx` and the web `SettingsScreen`/Gateway),
   add a `case "<slug>":` to the `renderSection(slug, controller)` switch that returns
   the section body with its host-bound props.

### Add a surface renderer (tier)

1. Add the renderer + diff under `packages/surface-renderers/src/<saas>/`, implementing
   the frozen `SaaSRendererAdapter` contract and using **design-system tokens only**
   (`var(--color-…)`), never literal hex.
2. Register it in `registerAll` (surface-renderers) so both hosts pick it up — desktop
   calls `registerSurfaceRenderers()` in `renderer/bootstrap.tsx`.
3. The `chat-surface` `surfaces/` registry (`registerAdapter` / `resolveAdapter`,
   `registerSurface` / `resolveSurface`) resolves the renderer at mount; tier-2
   (agent-generated) loads via `Tier2Loader`, tier-3 generic via
   `GenericStructuredDiff`.

## 5. Run cockpit anatomy

`RunDestination` (`packages/chat-surface/src/destinations/run/RunDestination.tsx`) is a
composition shell over three pieces:

- `useRunSession({ conversationId, runId, enabled })` — resolves the active/selected
  run and streams its events (Transport-port SSE) into an append-only
  `session.events` array; exposes `runs`, `selectRun`, `retry`, `status`, `error`.
- `useRunMode({ conversationId, enabled })` — KeyValueStore-backed Studio/Focus mode +
  the ⌘M toggle (gated to `enabled`).
- `ThreadCanvas` — the single-mount, mode-driven canvas. It projects `session.events`
  **once** internally via `useEventProjector`.

**Single event projection (FR-3.3).** The cockpit has exactly one event source and one
projection. The three out-of-canvas consumers are PURE selectors over the same
`session.events` array — no second SSE subscription, no second projector:

- `projectSubagents(events)` → the inline `SubagentFleetCard` fleets + the Agents-tab
  "N live" count.
- `projectApprovals(events)` (+ `overlayApprovalDecisions` for optimistic
  Approve/Reject + `toApprovalsQueue`) → the in-chat `ApprovalCard` (Studio) /
  conf-card (Focus) + the Approvals-tab count.

`RunWorkspaceRail` recomposes the `[Chat · Sources · Agents · Approvals]` tabs and
takes the single `TcChat` as an injected `chatSlot`, mounted into `ThreadCanvas`'s
`rightRail` slot — mode/tab switches never spawn a second chat mount. `RunHeader` is
the single mode control (`showModeSwitcher={false}` on the canvas).

Seams owned by the shell:

- **Scrub** — `scrubbedSeq` (`null` = live); a cheap `sequence_no → {atMs, surfaceUri}`
  index (NOT a second projection) answers "which surface / when"; scrubbing off-now
  shows the "Viewing…" banner and hides approvals.
- **Empty/idle** — `RunEmptyState` goal composer mounts when `session.runId === null`;
  submitting a goal starts a run and binds it via the `runId` seam
  (`setStartedRunId`) so empty→live swaps in place without remounting the shell.
- **Multi-run** — `RunMultiSelect` renders nothing for ≤1 run; picking one rebinds via
  `useRunSession.selectRun`.

On web the `run` slug mounts the working conversation surface (`ChatScreen`) rather
than `RunDestination` directly; the desktop `DestinationOutlet` mounts
`RunDestination` bound to a default conversation.

## 6. ⌘K palette & keyboard shortcuts

- `packages/chat-surface/src/shell/shortcuts.ts` is the **chord SSOT** (DESIGN-SPEC
  §6): `SHELL_SHORTCUTS` maps each chord to a `ShortcutIntent`. Five `global` chords,
  seven `run`-scoped chords. Add/adjust chords here only (FR-6.15).
- `useShellShortcuts(callbacks)` attaches ONE keydown listener. The **host bootstrap
  wires the global chords** (see `apps/desktop/renderer/bootstrap.tsx` — ⌘N/⌘K/⌘,/⌘⇧M/
  ⌘⇧F). The **run-scoped chords are owned by the cockpit** (useRunMode / TcMiniTimeline
  / TcSwimlanes / approvals) and left undefined at the shell level, so there is never
  double-wiring.
- The palette is a single controlled `CommandPalette` (via `PaletteHost`) mounted once
  at the shell root; the host supplies a `PaletteSearchPort` and lifts open state so
  ⌘K flows through exactly one listener.

## 7. Where the SSOTs live (quick reference)

| Concern                                       | Single source of truth                               |
| --------------------------------------------- | ---------------------------------------------------- |
| Rail slug ↔ label + profile gating            | `chat-surface/src/shell/destinations.ts`             |
| Settings sections + profile gating            | `chat-surface/src/settings/settingsNav.ts`           |
| Keyboard chords                               | `chat-surface/src/shell/shortcuts.ts`                |
| Package public API                            | `chat-surface/src/index.ts` (delimited phase blocks) |
| Design tokens (color/type/space/motion)       | `design-system/src/styles.css` `:root`               |
| App-facing payload contracts + branded IDs    | `packages/api-types`                                 |
| Substrate boundary (banned globals / imports) | `chat-surface/eslint.config.js`                      |

## 8. Validation

```bash
npm run typecheck --workspace @0x-copilot/frontend
npm run build --workspace @0x-copilot/frontend
npx vitest run --root packages/chat-surface
# desktop supervised boot: see apps/desktop/README.md + SMOKE.md
```
