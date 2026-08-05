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
share their impure half** (fetch over the host `Transport`, host navigation).
But the PURE half — projections over `@0x-copilot/api-types` shapes, with no
`window` / `fetch` / navigation — belongs in **this package**, not duplicated per
host. A duplicated projection is exactly how desktop kept reading stale
`metadata.*` keys for chat preview/model/pinned while web migrated to the
first-class fields. Pure api-types projections therefore live here: chats in
`src/projections/` (`toChatArchiveRow`), activity in
`src/destinations/activity/activityProjection.ts` (PRD-04), the composer's model
catalog in `src/composer/modelCatalog.ts`. When you change a destination's props,
update BOTH binders.

`modelCatalog.ts` is the cautionary tale for why this rule is not pedantry. Both
hosts had their own `defaultSelectedModelId`; the desktop copy learned to rank by
the backend's `tier` (a per-provider preferred rung, never an off-ladder
specialty row) while the web FTUE copy stayed "first configured row in catalog
order" — so the same catalog opened an Anthropic-only user on Sonnet in one host
and on Claude Fable 5, the dearest model Anthropic sells, in the other. Neither
copy was buggy in isolation; the duplication was the bug.

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
projector: `projectSubagents` (fleets + the Agents-tab "N live" count),
`projectApprovals`/`toApprovalsQueue` (the in-chat ask card + the
Approvals-tab count), and `projectRunTodos` (the pinned checklist). `RunWorkspaceRail`
recomposes the workspace `[Chat · Sources · Agents · Approvals]` tabs and receives the
single `TcChat` as an injected `chatSlot`, so mode/tab switches never spawn a second
chat mount.

**Approvals are inline, and there is ONE ask card.** Every approval card (the ask,
question, workspace grant, MCP auth) interleaves into the transcript through
`mergeStream`, anchored on `TcChatApproval.createdAtMs`. The two pinned strips are
**deleted** and `renderApprovalItem` is the single renderer — it now takes **no
`mode`**, because Studio and Focus render byte-identical approvals (pinned by
`TcChat.test.tsx` "renders the SAME card in Focus as in Studio, to the byte").

What it branches on is the **kind** of ask, never a skin for it, because each kind
resolves through a different seam: an ask → Approve/Decline and the host `/decision`
POST; a question → an ANSWER the wire carries; a folder ask → `WorkspaceGrantPort`
and an OS dialog; a connector ask → `McpAuthPort` and an OAuth redirect. Collapsing
any of those into the ask card posts the wrong thing (for an `mcp_discovery:` id, a
`/decision` POST 404s and resumes a run that still has no grant). Branch ORDER is
load-bearing: the write gate goes first because its wire shape is `ask_a_question`,
so the question branch would otherwise render a yes/no about a real side effect as a
free-text box.

The ask itself is `TcWriteGateRow` — one compact card for a parked MCP write AND for
an ordinary `tool_action`/`mcp_tool`. Every gate-specific prop is optional and
degrades to omission, never to an empty frame. Three safety properties live in it and
must not dissolve: the header is identical collapsed and expanded (the actions never
move out from under a cursor); **no approve control for an irreversible write is
reachable in one click from the collapsed card** — which is why the header approve
(one click) and the body approve (only after the payload rendered) are DISTINCT
testids, since one shared name would make every "no blind approval" assertion pass
over a button that is one click away; and **the header never clips its own decision**.

That last one is a layout rule with a safety consequence. The frame is
`overflow: hidden`, so an over-wide row is cut at its END — where the buttons are — and
the row carries one unbounded string, the `linear · write` meta, whose vendor half is
an arbitrary-length MCP server slug. It is therefore the item that gives way
(`flex: 0 1 auto`, `min-width: 0`, ellipsis) while the chip and `.tc-write-gate__actions`
stay `flex: none`. Anything added to that row must be bounded or shrinkable; an
unbounded `flex: none` item makes Approve the thing that disappears in a narrow column,
which is an approval nobody can act on. jsdom runs no layout, so
`TcWriteGateRow.test.tsx` asserts the shrink CONTRACT via `getComputedStyle` against
the real `review-surfaces.css` — that contract is what decides the clipping order.

The meta's second half is the ACCESS AXIS, and it is not a word this package chooses.
`buildCategory` derives it from the payload's `read_only` boolean exactly as the
producer does (`stream_events._approval_category`: `True → READ`, `False → WRITE`), and
emits **nothing** when the payload carried no boolean — an `mcp_auth` gate names a
connector and says nothing about access, so the card renders the bare vendor. The
richer three-value `ApprovalCategory` (`read`/`write`/`action`) never reaches the
client: `_approval_requested_payload` drops `category` and `vendor` from the
allow-list. Do not print a third word here without widening that allow-list first.

Two ghosts to not resurrect. `renderStudioApprovalCard`/`renderConfCard` rendered the
SAME `ConsentCard`, differing in a wrapper testid, two button testids, one sentence of
visually-hidden copy, and a `.conf-card` class **that has no CSS rule anywhere in the
product** — a mode split that painted nothing. And the comments here and in `TcChat`
called the Studio arm "the 4-zone `ApprovalCard`" for a long time while `ApprovalCard`
sat unused; it survives on the barrel only for the deprecated web `ApprovalTool` path,
and its `.atlas-approval-card*` CSS is declared only in `apps/frontend/src/styles.css`,
so anything that mounts it on desktop renders unstyled.

Two consequences worth keeping: a SETTLED approval renders nothing, either way it went — the
`ApprovalReceipt` line it used to collapse to was bare text in a transcript made of
cards, restating the tool card below it when approved and standing as the only
non-card row when denied; the decision survives on the run's event stream, which the
Approvals tab and the audit views project from. And `MessageListBody`'s load/error
notice must never early-return past the cards — inline, that hid a parked run's only
way out. Reachability moved to a `tc-chat-approvals-waiting` line above the composer.
When an approval renders nothing, its `<li>` wrapper is skipped too — an empty row
still contributes the stream's margin and would leave the gap the receipt used to
fill. What stays a card is the ASK: the surface that takes Approve/Decline is a card
and must not degrade into a line of text.

**The testid scheme, before you rename anything here.** One rule: **anything that
takes a DECISION is named after the approval it decides; everything structural on the
card keeps a global name.** Two asks parked at once is a drawn state, and a global
`tc-write-gate-approve` is ambiguous in it — Playwright refuses an ambiguous selector,
so "two cards parked" becomes a decision that never happened.

| Node                                                  | testid                               | driven by                                                         |
| ----------------------------------------------------- | ------------------------------------ | ----------------------------------------------------------------- |
| ask wrapper                                           | `tc-chat-approval-<id>`              | 7 fs journeys, by the `tc-chat-approval-` PREFIX                  |
| stream `<li>`                                         | `tc-chat-approval-item-<id>`         | `agent-todos/todos_with_gate.py`                                  |
| Approve (header, reversible only)                     | `tc-chat-approval-approve-<id>`      | jA/jF/jH/jI/jJ, by the `tc-chat-approval-approve-` PREFIX         |
| Decline                                               | `tc-chat-approval-reject-<id>`       | `connectors/gate_audit_events.py`, EXACT with the id interpolated |
| Approve (body, irreversible only)                     | `tc-chat-approval-body-approve-<id>` | `write-gate-inline/inline_gate.py`                                |
| card root / row / title / connector / review / body\* | `tc-write-gate*` (global)            | `write-gate-inline/inline_gate.py`, at DOCUMENT level             |

Three consequences that are load-bearing:

- The body approve is `…-body-approve-<id>`, **never** `…-approve-body-<id>`: the
  latter would match `[data-testid^=tc-chat-approval-approve-]`, and the five journeys
  that press Approve by that prefix would press the one control an irreversible write
  withholds until its payload has rendered. The safety property is carried by the
  SHAPE of the name, not only by the branch that renders the button.
- `TcWriteGateRow` takes those three names as props (`approveTestId` / `declineTestId`
  / `bodyApproveTestId`) and `renderAskCard` supplies them. The `tc-write-gate-*`
  defaults are for a STANDALONE mount and are exercised only by
  `TcWriteGateRow.test.tsx`; nothing in the mounted app emits them, so a negative
  assertion written against one passes **vacuously**. There is no second, unscoped
  alias — do not add one.
- Query controls through `within(wrapper)` anyway. The scoped name proves the button
  exists; `within` proves it belongs to the card carrying that id.

`tc-chat-conf-*` and `tc-chat-approval-receipt-*` have no emitter at all. A leftover
negative on either passes vacuously — re-point it at something product code really
paints (`.atlas-approval-receipt` for the receipt) or delete it.

When you assert "this approval did NOT render the ask card", prefer the CARD over the
id: `tc-write-gate` is `TcWriteGateRow`'s own root and is stamped for every ask, every
id, both hosts, so a rename of the id-scoped decision names cannot silence it. Pair it
with a POSITIVE assertion naming the card that did render (`tc-chat-mcp-auth-<id>` +
`tc-chat-connector-<id>` for a connector consent, `tc-chat-workspace-grant-*` for a
folder ask). A negative alone only says "not that name"; the pair says which of the
branches in `renderApprovalItem` was taken.

**The agent todo panel.** `TcTodoList` renders the agent's working checklist, the ONLY
pinned element above the composer inside `TcChat` — single-mount, identical in Focus
and Studio, and stationary now that approvals no longer insert above it. It reads
`projectRunTodos(session.events)` held across runs by `useConversationTodos` (the
projection is run-scoped, so a follow-up message rebinds to a fresh run and the panel
vanished mid-thread until the last snapshot was retained). `blocked` — a pending
approval — makes the in-progress row read _waiting_ rather than spinning. The
server's `todo_list_updated`
snapshots, which the worker resolves from `write_todos` (LangChain's
`TodoListMiddleware` replaces the whole list per call and carries no list identity, so
the backend assigns `list_id`/`generation`: a write landing on an already-complete list
opens the next one). The panel is **read-only** — the list is agent-owned, so there is
no host callback surface. It replaced `FocusPlan`, which was **deleted**: that surface
invented steps from tool-call frames, so it showed tool names where a plan belonged.
Do not reintroduce a client-derived plan; if the cockpit needs to show intent, it comes
from an event the agent actually produced.

Seams the shell owns: scrub cursor (`scrubbedSeq`; `null` = live) + the "Viewing…"
banner (approvals hidden while scrubbed); the empty/idle `RunEmptyState` goal composer
(mounts when `session.runId === null`; starting a goal binds the fresh run via the
`runId` seam without remounting the shell).

The cockpit is **single-run**: it binds the conversation head and renders NO run-picker
chrome in either mode. `RunMultiSelect` — the old "N RUNS" chip rail above the canvas —
was deleted (FR-3.26 withdrawn), not merely unmounted. Rebinding still works and is
still tested: `useRunSession.selectRun` and the `runId` prop swap the session's SSE tail
without remounting the canvas, driven from surfaces whose job is picking a run (the
Pending Work card, the Agents stage). Don't add a persistent selector rail back.

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
