# PRD-03 — Host binding contract: end desktop under-binding, structurally

## Problem

Run the desktop app and the web app side by side on the same account. The desktop app is missing capabilities that are **already built, already tested, and already shipping in the binary**:

- A run is executing. The web rail shows a small accent pill on the Run icon. The desktop rail shows nothing — ever, for any count.
- The web rail foot shows your initial in a 26px disc. The desktop rail foot shows a generic person glyph, even though the desktop knows your display name and put it on screen during sign-in.
- Open **Tools**. Every connector card is a bare title — no logo tile — on both hosts. The design gives every row a 30×30 tile.
- On desktop, a connector card has no "Read / Read & act / Off" control at all. On web the control renders and moves — and then silently fails, because the endpoint it PATCHes **does not exist in any Python file in this repo**.
- Open **Projects** on desktop and click a card. Nothing opens. The project detail view is built, styled, and mounted on web; on desktop the branch that renders it is unreachable.
- Click "Connect a tool" on desktop and a browser window opens immediately — no permission step. The 3-step connect modal (catalog → OAuth → permission) exists in the shared package and is mounted only by the web app.
- Every desktop chat row shows a title, a chip and a time, and **nothing else** — no one-line preview, no model tag — and the "Pinned" section is permanently empty. Web shows all three.
- Every cross-destination project link on desktop reads the literal word **"Project"** instead of the project's name.

None of these are missing features. Every one is a component in `packages/chat-surface` that works, has tests, and renders correctly the moment somebody passes it a prop. The desktop host never passes the prop, and **nothing anywhere fails** — not the compiler, not a lint, not a test. The capability ships invisible.

That is the actual defect this PRD fixes. Six props is the symptom count today; the real number is nine, two of them unbound on **both** hosts, and the mechanism that produced them is still running.

## Evidence

Every row opened and verified by me in this worktree (`claude/design-parity-audit-7ec82a`).

| Claim                                                                      | File:line                                                                                                                      | What the code actually does                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Desktop `ChatShell` mount omits `railBadges`                               | `apps/desktop/renderer/bootstrap.tsx:318-331`                                                                                  | CONFIRMED. Props passed: `transport, router, keyValueStore, presenceSignal, activeDestination, destinations, onNavigate, onOpenSettings, onOpenCommandPalette, settingsActive`. No `railBadges`.                                                                                                                                                                                                                                                                                       |
| …and omits `railIdentity`                                                  | same mount                                                                                                                     | CONFIRMED. Absent from the same list. `props.session.displayName` is in scope — `ChatShellForSession` receives `session` at `bootstrap.tsx:142`, typed `RendererSession` at `:161`, `displayName: string \| null` at `chat-transport/src/ipc/rpc-protocol.ts:144`, populated at `apps/desktop/main/auth/index.ts:552`.                                                                                                                                                                 |
| Web binds both                                                             | `apps/frontend/src/app/App.tsx:1217-1221`, `:1224-1226`                                                                        | CONFIRMED. `railIdentity` from `profile?.data?.display_name?.trim()`; `railBadges={activeRunCount > 0 ? { run: activeRunCount } : undefined}`.                                                                                                                                                                                                                                                                                                                                         |
| `railBadges` / `railIdentity` have exactly one call site repo-wide         | grep across `apps/`, `packages/` excluding `chat-surface/src/shell/`                                                           | CONFIRMED. Two hits, both `App.tsx`. Zero in `apps/desktop`.                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `AppRail` renders the badge purely from the prop                           | `packages/chat-surface/src/shell/AppRail.tsx:246-247`, `:269-273`                                                              | CONFIRMED. `const count = badges?.[d.slug] ?? 0; const showBadge = count > 0 && !isActive;` then the `data-rail-badge` span. Style object at `:144`. `ChatShell` is a pure forwarder: `:130` declare → `:187` → `:294 badges={railBadges}`.                                                                                                                                                                                                                                            |
| `renderIcon` is declared, consumed twice, supplied by **neither** host     | `packages/chat-surface/src/destinations/connectors/ConnectorsDestination.tsx:134`, `:295`, `:333`                              | CONFIRMED. Repo-wide grep for `renderIcon` returns only the package's own declaration/consumption plus a doc comment. Zero hits under `apps/`.                                                                                                                                                                                                                                                                                                                                         |
| …and even if supplied, the tile chrome does not exist                      | `packages/chat-surface/src/destinations/connectors/ConnectorCard.tsx:128-132`, `:200-203`                                      | CONFIRMED. `icon` renders inside `<span style={iconStyle}>` where `iconStyle = { display:"inline-flex", flexShrink:0 }` — no size, no radius, no background. The design's 30×30 `--panel3` tile has no live counterpart at all.                                                                                                                                                                                                                                                        |
| The wire already carries the icon hint                                     | `packages/api-types/src/connectors.ts:208` (catalog), `:124-136` (connector)                                                   | CONFIRMED. `icon_hint?: string` on `ConnectorCatalogEntry`, documented "a hint string the FE may map to a built-in icon registry". Desktop already populates it (`destinationBinders.tsx:395` `icon_hint: entry.slug`).                                                                                                                                                                                                                                                                |
| …and the package already owns an icon-hint resolver                        | `packages/chat-surface/src/shell/PaletteHitRow.tsx:145`                                                                        | CONFIRMED. `{iconGlyph(hit.icon_hint)}` — the same problem already solved once, inside the package, without a host render-prop.                                                                                                                                                                                                                                                                                                                                                        |
| Desktop omits `onSetAccessMode`                                            | `apps/desktop/renderer/destinationBinders.tsx:481-489`                                                                         | CONFIRMED. Props: `items, filter, onFilterChange, onConnect, onOpenCatalogEntry, onOpenApprovalSettings, onRetry`. No `onSetAccessMode`, so `ConnectorsDestination.tsx:340-342` passes `undefined` and `ConnectorCard` renders a dead segment.                                                                                                                                                                                                                                         |
| **DISPUTED** — this is not only a desktop gap: the endpoint does not exist | grep `access_mode`/`access-mode` across `services/**/*.py`                                                                     | **Zero hits.** `apps/frontend/src/api/connectorsApi.ts:216` PATCHes `/v1/connectors/{id}/access-mode`; `services/backend-facade/.../connector_routes.py:40-54` enumerates every proxied path and has no access-mode entry. `packages/api-types/src/connectors.ts:129-135` says so in prose: "the facade does not serve it until the access-mode PATCH lands". **Web's control 404s.** The audit framed this as desktop under-binding; the code says it is a product gap on both hosts. |
| Desktop omits `renderDetail` **and** `focusedProjectId`                    | `apps/desktop/renderer/destinationBinders.tsx:563-568`                                                                         | CONFIRMED. `return <ProjectsDestination items={result} onRetry={retry} />;` at `:567` — two props total.                                                                                                                                                                                                                                                                                                                                                                               |
| …making the detail branch dead code on desktop                             | `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx:283`                                                  | CONFIRMED. `const showingDetail = renderDetail !== undefined && focusedProjectId !== null;` — both conditions false forever on desktop. Web supplies both (`ProjectsRoute.tsx:826-828`).                                                                                                                                                                                                                                                                                               |
| `ConnectModal` is mounted only by web                                      | `apps/frontend/src/features/connectors/ConnectorsRoute.tsx:603-617`                                                            | CONFIRMED. Repo-wide, `ConnectModal` appears in the package (`destinations/connectors/ConnectModal.tsx`, barrels) and in exactly one host file. Desktop's `ConnectorsBinder` wires `onOpenCatalogEntry={connect}` (`destinationBinders.tsx:486`) straight to `CONNECTOR_CHANNELS.connect` (`:462`), skipping catalog/permission entirely.                                                                                                                                              |
| Desktop chats read three keys nothing writes                               | `apps/desktop/renderer/destinationBinders.tsx:163-167`, `:178-181`                                                             | CONFIRMED. `metaString(conversation,"preview")`, `metaString(conversation,"model")`, `metadata?.pinned === true`. Repo-wide grep finds no writer for `metadata.preview` / `.model` / `.pinned`.                                                                                                                                                                                                                                                                                        |
| The backend serves all three as first-class fields                         | `packages/api-types/src/index.ts:561-578`; `services/ai-backend/src/agent_runtime/api/conversation_query_service.py:461-490`   | CONFIRMED. `pinned?: boolean` ("Projected from a real `pinned` column (migration 0034), **not** `metadata` — the metadata path was never written, so Pinned was always empty"), `preview?: string \| null`, `model?: string \| null`. Server projects preview + model in `_with_preview`-style code at `:478-490`.                                                                                                                                                                     |
| Web already migrated to the first-class fields                             | `apps/frontend/src/features/chats/api/chatsApi.ts:150-176`                                                                     | CONFIRMED. `isPinned` → `conversation.pinned === true`; `previewOf` → `conversation.preview ?? ""`; `modelOf` → `conversation.model ?? ""`, each with a PRD-H.4 comment. The desktop copy was never updated.                                                                                                                                                                                                                                                                           |
| Desktop never primes the project-name cache                                | grep `cacheProjectNames` across `apps/`                                                                                        | CONFIRMED. One hit: `apps/frontend/src/features/projects/ProjectsRoute.tsx:302`. Desktop: zero.                                                                                                                                                                                                                                                                                                                                                                                        |
| …so every desktop project link says "Project"                              | `packages/chat-surface/src/destinations/projects/index.ts:166-172`                                                             | CONFIRMED. `label: getCachedProjectName(id) ?? "Project"`. Cache is a module singleton (`projectNameCache.ts:17`) primed only by a host call.                                                                                                                                                                                                                                                                                                                                          |
| Two more props are unbound on **both** hosts                               | `packages/chat-surface/src/shell/ChatShell.tsx:97` (`topbarLeaf`), `:138` (`walletChip`)                                       | CONFIRMED. Grep for `topbarLeaf` and `walletChip` outside the package returns nothing. The systemic count is 9, not 6, and it is not a desktop-only disease.                                                                                                                                                                                                                                                                                                                           |
| Desktop discards Activity's `runId` argument                               | `apps/desktop/renderer/destinationBinders.tsx:367`                                                                             | CONFIRMED (audit cited `:369` — 2-line drift). `onOpenRun={() => onOpenRun?.()}` against `ActivityDestination.tsx:232` `onOpenRun?: (runId: RunId) => void` and `:543-544` `() => onOpenRun(row.run_id)`. **Types cannot catch this** — a 0-arity function is assignable to a 1-arity signature.                                                                                                                                                                                       |
| Web does not mount `ProjectsDestination` for the list at all               | `tools/design-parity/surfaces/projects/out/FINDINGS.md:28-33`; `apps/frontend/src/features/projects/ProjectsRoute.tsx:783-829` | CONFIRMED by reading both. Web renders a bespoke grid and mounts the shared destination only when `focusedProjectId !== null`. Same class of divergence, opposite host. Assigned to PRD-07 (see Dependencies), not fixed here.                                                                                                                                                                                                                                                         |
| The measured HIGH row for the missing tile                                 | `tools/design-parity/surfaces/tools/out/report-default.md` (HIGH table)                                                        | CONFIRMED. `default.row.logo · missing-in-live` — "present in design, ABSENT in live". Anchor rationale at `surfaces/tools/anchors.json:57-63` names `renderIcon` as the cause.                                                                                                                                                                                                                                                                                                        |
| The rail-badge audit's own caveat                                          | `tools/design-parity/surfaces/rail-badge/out/AUDIT.md` (R-1, Confidence §)                                                     | CONFIRMED. The harness feeds `railBadges={{run:1}}` deliberately (`lib/render-live-rail-badge.test.tsx:113`), so the parity pixels do **not** show this gap. It is a wiring finding, verified by grep, not by pixels.                                                                                                                                                                                                                                                                  |

## Design intent

Literal values from `tools/design-parity/design-kit/app-v3/`.

**Rail badge** — `copilot.css:343-355`:

```css
.rail-item .rbadge {
  position: absolute;
  top: 3px;
  right: 3px;
  min-width: 13px;
  height: 13px;
  padding: 0 3px;
  border-radius: 7px;
  background: var(--accent);
  color: var(--accent-ink);
  font-size: 8.5px;
  font-weight: 700;
  display: grid;
  place-items: center;
  font-family: var(--mono);
}
```

Dark theme: `--accent: var(--sky)` (`:27`), `--accent-ink: #08131d` (`:30`). Presence rule, `copilot-app.jsx:795-797` — `{d.badge && dest !== "workspace" && d.id === "workspace" && <span className="rbadge">{d.badge}</span>}`: badge on **Run only**, suppressed while Run is active. Fixture `copilot-app.jsx:5` — `{ id:"workspace", label:"Run", icon:"run", badge:"1" }`. `AppRail.tsx:144-162` already matches these numbers exactly; the only defect is that no desktop data reaches it.

**Rail identity disc** — `copilot.css:366-377`:

```css
.rail-me {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: var(--panel3); /* #1d1d23 dark, copilot.css:12 */
  color: var(--tx2); /* #d4d4db, :17 */
  display: grid;
  place-items: center;
  font-weight: 600;
  font-size: 11px;
  border: 1px solid var(--line2); /* rgba(255,255,255,.1), :14 */
}
```

Content, `copilot-app.jsx:811-813`: `<button className="rail-me" title={prefs.name}>{prefs.name.slice(0,1)}</button>` — the **raw** first character, not upper-cased. (`AppRail.tsx:301` calls `.toUpperCase()`. Out of scope here; noted for the rail PRD.)

**Connector row tile** — `copilot.css:1604-1616`:

```css
.lrow__logo {
  width: 30px;
  height: 30px;
  border-radius: 7px;
  display: grid;
  place-items: center;
  font-weight: 600;
  font-size: 12px;
  flex: none;
  background: var(--panel3) !important; /* #1d1d23 */
  color: var(--tx2) !important; /* #d4d4db */
  font-family: var(--body);
}
```

Read the `!important`s against the markup, `copilot-app.jsx:129-131`: `<span className="lrow__logo" style={{background:c.color}}>{c.ini}</span>`. The fixture supplies a per-connector brand colour and the stylesheet **deliberately overrides it**. The design's connector tile is a **neutral initial glyph** (`ini: "◇"`, `copilot-data.jsx:509`), not a brand logo. A host render-prop for brand glyphs is therefore not merely unbound — it is the wrong contract for what the design asks for.

**Chat row sub-line** — `copilot-app.jsx:279-281`:

```jsx
<span className="lrow__sub" style={{ fontFamily: "var(--body)" }}>
  {c.preview} · <span className="mono">{c.model}</span>
</span>
```

`.lrow__sub { color: var(--mut2) }` = `#64646d` (`copilot.css:293`); `.mono` changes family only (`:42`). Pinned bucketing is first-class in the fixture (`copilot-app.jsx:288`, `CHATS[].pinned` at `copilot-data.jsx:722-732`). Preview, model and pinned are **specified**, not incidental — which is why the desktop dead-read is a parity defect and not a cosmetic one.

## Architectural decision

**The seam that changes: the props boundary between `packages/chat-surface` and its two hosts. It becomes _total_ — every host-owned capability must be answered explicitly, and every capability the package can answer itself stops being a host duty entirely.**

Why this seam. Nine capabilities went dark because `?:` on a props interface means "the host may decline, silently, forever". Each of the nine compiled clean, linted clean and passed both suites while doing nothing. The seam is where the package's contract meets the host's obligation; making the seam optional is what made the obligation invisible. No amount of care at the call sites fixes that — care is exactly what failed nine times.

The change is three moves. They are not independent: move 1 shrinks the surface so move 2 is small enough to be total.

### Move 1 — Delete the props the package can answer itself

A prop that the host cannot meaningfully vary is a liability, not a seam.

| Prop / duty                                                                                                     | Why it is not host-owned                                                                                                                                                                                                                                                                                     | After                                                                                                                                                                                       |
| --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ConnectorsDestinationProps.renderIcon`                                                                         | The design's tile is a neutral initial (`copilot.css:1604` `!important`), the wire already carries `icon_hint` (`api-types/connectors.ts:208`), and the package already resolves icon hints (`PaletteHitRow.tsx:145`). Nothing here varies by substrate.                                                     | **Deleted.** New `connectorGlyph(displayName, iconHint)` in `chat-surface/src/icons/`; `ConnectorCard` owns the 30×30 / radius-7 / `--panel3` tile.                                         |
| `ChatShellProps.railBadges`                                                                                     | Both hosts would compute the identical number from the identical endpoint. `apps/frontend/.../useActiveRunCount.ts` is web-app-local only by accident of where it was written; it depends on nothing web-specific once it reads the `Transport` port.                                                        | **Deleted.** `useActiveRunCount()` moves into `chat-surface/src/shell/`; `ChatShell` derives `AppRail.badges` itself, from the `Transport` both hosts already pass.                         |
| Host call to `cacheProjectNames`                                                                                | `ProjectsDestination` already receives the exact `{id,name}` list the cache wants (`items: SectionResult<ProjectSummary[]>`). Asking the host to re-hand it to a module singleton is a duty with no decision in it.                                                                                          | **Deleted as a host duty.** `ProjectsDestination` primes the cache in an effect over `items`.                                                                                               |
| Host mount of `<ConnectModal>`                                                                                  | A second component the host must remember to mount is the one under-binding class the type system can never catch. The modal is part of the Tools destination in the design (`copilot-app.jsx:114-118` CTA → modal), not a host concern.                                                                     | **Folded** into `ConnectorsDestination`, which owns `open` state and the catalog it already has. Hosts supply effects only.                                                                 |
| Duplicated pure projections (`Conversation → ChatArchiveRow`, `Conversation[]+AuditEvent[] → ActivityRunRow[]`) | These operate solely on `@0x-copilot/api-types` shapes — no `window`, no `fetch`, no navigation. `packages/chat-surface/CLAUDE.md` currently _instructs_ hosts to duplicate them; that instruction is what let web migrate to `conversation.pinned` in PRD-H.4 while desktop kept reading `metadata.pinned`. | **Moved** to `chat-surface/src/projections/`. Both hosts call one function. **`packages/chat-surface/CLAUDE.md` is amended**: pure projections over api-types shapes belong in the package. |

Move 1 alone removes 5 of the 9 gaps as a _category_, and fixes the desktop chats dead-read, the desktop project-name fallback, and the missing connector tile on **both** hosts.

### Move 2 — Make what genuinely remains total

Everything left is a real host decision (identity source, navigation, persistence effects, substrate flows). For those, optionality moves from the **type** to the **value**:

```ts
// packages/chat-surface/src/contract/shellBinding.ts
export interface ShellHostBinding {
  readonly railIdentity: { readonly initial: string } | null;
  readonly walletChip: ReactNode | null;
  readonly topbarLeaf: string | null;
  readonly settingsActive: boolean;
}
```

Required, and `undefined` is **not** in any union — so an omitted field is a compile error and an opt-out is a literal `null` that appears in the diff and gets reviewed. Same treatment per destination:

```ts
// ProjectsDestination
readonly detail:
  | { readonly mode: "disabled" }
  | { readonly mode: "enabled"; readonly focusedProjectId: ProjectId | null;
      readonly renderDetail: RenderProjectDetailSlot; readonly onCloseDetail: () => void };

// ConnectorsDestination
readonly connect:
  | { readonly mode: "disabled" }
  | { readonly mode: "enabled"; readonly onConnect: (slug: ConnectorSlug, permission: ConnectorAccessMode) => void;
      readonly onAddCustomServer: ((input: CustomServerInput) => void) | null;
      readonly pending: boolean; readonly error: string | null };
readonly onSetAccessMode: ((id: ConnectorId, mode: ConnectorAccessMode) => void) | null;
```

Desktop must now write `detail: { mode: "disabled" }` — an explicit, reviewable statement that desktop has no project detail yet — instead of silently having none. Desktop's custom-MCP-add gap becomes `onAddCustomServer: null` (a known, tracked gap) rather than an absence.

### Move 3 — The enforcement gate for what types cannot catch

Types catch omission. They do **not** catch arity discard (`destinationBinders.tsx:367`) or a `null` opt-out that should not be null. One mechanism covers both:

```ts
// packages/chat-surface/src/contract/manifest.ts
export const SHELL_BINDING_FIELDS = [
  "railIdentity",
  "walletChip",
  "topbarLeaf",
  "settingsActive",
] as const;
type _Exhaustive =
  Exclude<
    keyof ShellHostBinding,
    (typeof SHELL_BINDING_FIELDS)[number]
  > extends never
    ? true
    : [
        "binding manifest is missing",
        Exclude<keyof ShellHostBinding, (typeof SHELL_BINDING_FIELDS)[number]>,
      ];
const _check: _Exhaustive = true;
```

Adding a field to a binding type without adding it to the manifest fails `tsc` inside the package. Each host then owns one thin conformance test (`apps/frontend/src/app/bindingContract.test.tsx`, `apps/desktop/renderer/bindingContract.test.tsx`) that mounts its **real** shell/binders against fixtures and asserts, per manifest entry, the observable effect — including `onOpenRun` being invoked **with the row's `run_id`**. Adding a capability means editing one manifest; two host tests go red until both hosts answer.

### Backend capability this exposes (must be built, cannot be faked)

`onSetAccessMode` cannot become required while the endpoint is missing. Full spec:

- **Route** `PATCH /v1/connectors/{connector_id}/access-mode`.
  - `services/backend/src/backend_app/connectors/routes.py` — modelled line-for-line on `patch_scopes` (`:409-460`): `dependencies=[Depends(RequireScopes(RUNTIME_USE))]`, `org_id`/`user_id` as required query params, identity via `BackendServiceAuthenticator.scoped_identity(request, org_id=..., user_id=...)`. **Caller-supplied `org_id`/`user_id` are only ever accepted through that authenticator** — never trusted raw.
  - `services/backend-facade/src/backend_facade/connector_routes.py` — add `ACCESS_MODE = "/v1/connectors/{connector_id}/access-mode"` to the path enum (`:40-54`) and a proxy mirroring the `SCOPES` proxy.
- **Request** `SetConnectorAccessModeRequest { access_mode: "read" | "read_act" | "off" }` — already declared, `packages/api-types/src/connectors.ts:303-313`. **Response** the updated connector row — already declared, `:315-318`. No api-types change is needed; the types were written ahead of the route.
- **Status codes** `200` updated row · `400 invalid_request` unknown mode · `403 owner_or_admin_only` · `404 connector_not_found` (404-not-403 for cross-tenant, matching `patch_scopes`). Idempotent: re-setting the current mode returns `200` and writes no audit row.
- **Persistence** migration `services/backend/migrations/0046_connector_access_mode.sql` (+ `.rollback.sql`), and the byte-equivalent edit to `services/backend/src/backend_app/connectors/schema.sql` — these two have diverged before and shipped a 500 on fresh installs:
  ```sql
  ALTER TABLE connectors ADD COLUMN IF NOT EXISTS access_mode TEXT NOT NULL DEFAULT 'read'
    CHECK (access_mode IN ('read', 'read_act', 'off'));
  CREATE INDEX IF NOT EXISTS connectors_tenant_access_mode_idx ON connectors (tenant_id, access_mode);
  ```
  Default `'read'` (not `'off'`): existing connectors are already authorized and reading; defaulting to `off` would silently break every live install. Regenerate `services/backend/migrations/MANIFEST.lock` via `tools/check_migration_manifest.py`. Existing RLS on `connectors` (`0044_connectors.sql:56-62`, `USING/WITH CHECK tenant_id = current_setting('app.current_org_id')`) covers the new column unchanged.
- **Audit** one `ConnectorAuditRecord` (`store.py:115-140`) per real change, `action="connector.access_mode_set"`, `before_state`/`after_state` carrying only the mode.
- **Authorization rule** owner-or-admin, identical to `service.patch_scopes` (`service.py:403`). Reads stay tenant-member.

### Alternatives rejected

1. **Add the six props.** The 7th and 8th (`walletChip`, `topbarLeaf`) are already unbound on both hosts, today, with no ticket. The mechanism survives.
2. **One shared binder module both hosts import.** Impossible without breaking a hard boundary or the package's substrate rule: desktop's connectors binder needs `window.bridge` IPC (`destinationBinders.tsx:460-463`) and `../main/connectors/channels`; `chat-surface/eslint.config.js:57` bans bare `window`. Housing it in a new package re-creates `apps/*→apps/*` under a different name. The _pure_ half genuinely is shareable and does move (Move 1); the impure half is genuinely per-host.
3. **Dev-mode runtime warnings for missing props.** A console line nobody reads, no CI signal. It would have "caught" all nine and prevented none.
4. **Contract tests only, props left optional.** A test you must remember to write is the same failure mode as a prop you must remember to pass. Types first; tests only for the residue types cannot express.
5. **Making every currently-optional prop required, mechanically.** Over-fires. `renderIcon` required would enshrine the wrong contract in two hosts; `renderDetail` required would force desktop to build a whole detail flow to compile. Classification (Move 1 vs Move 2) is load-bearing.

## Scope

### `packages/chat-surface`

- `src/contract/shellBinding.ts` — **new.** `ShellHostBinding` + per-destination binding types.
- `src/contract/manifest.ts` — **new.** Type-derived exhaustive field manifests consumed by both host conformance tests.
- `src/contract/index.ts` — **new.** Barrel for the above.
- `src/shell/ChatShell.tsx` — accept `binding: ShellHostBinding`; delete `railBadges`; keep forwarding `railIdentity`/`walletChip`/`topbarLeaf` from the binding.
- `src/shell/useActiveRunCount.ts` — **new.** Port-based move of the web hook; `ChatShell` calls it to feed `AppRail.badges`.
- `src/shell/useActiveRunCount.test.ts` — **new.** Counts active statuses; keeps last value on transport failure.
- `src/shell/AppRail.tsx` — unchanged rendering; typing follows `ChatShell`.
- `src/icons/connectorGlyph.tsx` — **new.** `icon_hint` → registry glyph, else first initial of `display_name`.
- `src/icons/index.ts` — export it.
- `src/destinations/connectors/ConnectorCard.tsx` — replace `iconStyle` (`:200-203`) with the design tile; drop the `icon` prop in favour of `slug` + `iconHint` + `displayName`.
- `src/destinations/connectors/ConnectorsDestination.tsx` — delete `renderIcon`; make `onSetAccessMode` required-nullable; add the required `connect` union; own `ConnectModal` mount + open state.
- `src/destinations/connectors/ConnectModal.tsx` — unchanged component; its props become internal to the destination.
- `src/destinations/projects/ProjectsDestination.tsx` — collapse `renderDetail`/`focusedProjectId`/`onCloseDetail` into the required `detail` union; prime the name cache from `items`.
- `src/destinations/projects/projectNameCache.ts` — keep `cacheProjectNames` exported for tests; drop it from the public barrel.
- `src/projections/chats.ts` — **new.** `conversationToArchiveRow` / `bucketConversations` reading `pinned` / `preview` / `model`.
- `src/projections/activity.ts` — **new.** `auditLabel` / `buildMetaIndex` / `projectActivityRows`.
- `src/projections/{chats,activity}.test.ts` — **new.** Including a regression case asserting `metadata.preview` is **ignored** and `conversation.preview` wins.
- `src/index.ts` — new delimited export block; remove `renderIcon`-era exports and `cacheProjectNames`.
- `CLAUDE.md` — amend the "binders intentionally duplicate the same pure projection logic" paragraph; record that pure api-types projections now live in `src/projections/`.

### `apps/desktop`

- `renderer/bootstrap.tsx` — construct `ShellHostBinding`: `railIdentity` from `props.session.displayName?.trim()`, `walletChip: null`, `topbarLeaf: null`, `settingsActive`.
- `renderer/destinationBinders.tsx` — delete `metaString`/`toArchiveRow`/`bucketConversations` and the Activity projection (call `chat-surface/projections`); pass `onSetAccessMode` (PATCH via `Transport`, optimistic + revert, mirroring `ConnectorsRoute.tsx:304-345`); pass `connect: { mode:"enabled", onConnect: …CONNECTOR_CHANNELS.connect, onAddCustomServer: null, pending, error }`; pass `detail: { mode:"disabled" }`; forward Activity's `runId`.
- `renderer/DestinationOutlet.tsx` — split the overloaded `onOpenRun?: () => void` into `onOpenRun: (runId: RunId) => void` and `onNewChat: () => void`.
- `renderer/destinationBinders.test.tsx` — update fixtures; assert preview/model/pinned come from first-class fields.
- `renderer/bindingContract.test.tsx` — **new.** Manifest-driven conformance test.

### `apps/frontend`

- `src/app/App.tsx` — construct `ShellHostBinding`; delete the `railBadges` prop and the stale comment at `:1215-1217`.
- `src/features/activity/useActiveRunCount.ts` — **deleted** (moved into the package); update `App.tsx:520` and its test.
- `src/features/chats/api/chatsApi.ts` — delegate bucketing to `chat-surface/projections` (keep the file as the fetch layer).
- `src/features/activity/api/activityApi.ts` — same for `projectActivityRows`.
- `src/features/connectors/ConnectorsRoute.tsx` — stop mounting `ConnectModal`; hand its handlers to the destination's `connect` binding.
- `src/features/projects/ProjectsRoute.tsx` — `detail: { mode:"enabled", … }`; drop the `cacheProjectNames` call at `:302`.
- `src/app/bindingContract.test.tsx` — **new.**

### `services/backend`

- `src/backend_app/connectors/routes.py` — `patch_access_mode` route.
- `src/backend_app/connectors/service.py` — `set_access_mode` (owner-or-admin, idempotent, audited).
- `src/backend_app/connectors/store.py` — `access_mode` on `ConnectorRecord` + store method.
- `src/backend_app/connectors/schema.sql` — column + index.
- `migrations/0046_connector_access_mode.sql` / `.rollback.sql` / `MANIFEST.lock`.
- `tests/.../test_connector_access_mode.py` — **new.** 200/400/403/404, idempotency, audit row, cross-tenant isolation.

### `services/backend-facade`

- `src/backend_facade/connector_routes.py` — `ACCESS_MODE` path + proxy.
- `tests/.../test_connector_routes.py` — proxy assertion.

## Non-goals

- **Run-time enforcement of `access_mode`.** This PRD persists and serves the mode; it does **not** make the agent refuse a tool call on an `off` connector. That gate lives in `ai-backend`'s MCP permission middleware and needs a cross-service contract (`backend` owns the column; `ai-backend` runs the tools). See Risks — this is the one item here that ships a control ahead of its enforcement, and it must be stated in the PR description.
- **Building a project detail flow on desktop.** Desktop declares `detail: { mode:"disabled" }`. The point is that the gap becomes visible and reviewable, not that it closes today.
- **Desktop custom-MCP add.** `onAddCustomServer: null`.
- **Collapsing web's bespoke Projects grid onto `ProjectsDestination`** (`ProjectsRoute.tsx:783-829`) — same disease, different host; owned by PRD-07.
- **Any pixel fix** to `StatusPill`, the 13px body baseline, `.sect-h`, row padding, or `--font-size-*`. PRD-01 / PRD-02.
- **Run-badge _correctness_.** The moved `useActiveRunCount` still counts conversations with an active `latest_run_status`, not runs. That is a data-source defect owned by PRD-05; this PRD only relocates the hook so both hosts share one (currently imperfect) source.
- **`AppRail`'s `.toUpperCase()` on the initial**, and the rail-foot divider/gap deltas (R-3 / R-5 / R-7 in the rail audit).
- **Activity's run→route semantics.** PRD-03 splits the overloaded callback and forwards the id; what `onOpenRun(runId)` should navigate to is PRD-04.

## Risks & rollback

| Risk                                                                                                                             | Guard / mitigation                                                                                                                                                                                                                                                                                                                                                                   |
| -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Access mode persists but does not gate.** A user sets a connector to "Off", the row says Off, and the agent can still call it. | Named non-goal, stated in the PR body and in the api-types doc comment. `packages/api-types/src/connectors.ts:129-135` already says consumers "SHOULD default to least privilege" — the default column value is `'read'`, matching today's real behaviour, so no user's effective permissions change on migrate. Enforcement is a hard dependency for any marketing of this control. |
| Making binding types required breaks every existing mount at once.                                                               | Only two hosts and the package's own tests consume them (`packages/chat-surface/CLAUDE.md` barrel rule). `npm run typecheck` in all three workspaces enumerates every site; the change is mechanical and total by construction.                                                                                                                                                      |
| `ChatShell` now polls `/v1/agent/conversations` every 30 s for every mounted shell, including tests.                             | The hook lives behind the `Transport` port; test transports return fixtures. Existing guards: `ChatShell.test.tsx`, `apps/desktop/renderer/bootstrap.test.tsx`. Add an explicit "no poll when Transport rejects" assertion in `useActiveRunCount.test.ts`.                                                                                                                           |
| Folding `ConnectModal` into the destination changes web's connect UX ordering.                                                   | `apps/frontend/src/features/connectors/ConnectorsRoute.test.tsx` covers the current 3-phase flow; the modal component itself is untouched, only its mount point moves.                                                                                                                                                                                                               |
| Shared projections change desktop chats output (previews/models/pinned appear where there were none).                            | That is the fix. `apps/desktop/renderer/destinationBinders.test.tsx` fixtures currently encode the `metadata` shape and must be rewritten — treat a green old fixture as evidence of the bug, per PRD-05's precedent.                                                                                                                                                                |
| Migration `0046` diverges from `schema.sql` (this has shipped a fresh-install 500 before).                                       | DoD item pins both files and the `MANIFEST.lock` regeneration; `tools/check_migration_manifest.py` is CI-enforced.                                                                                                                                                                                                                                                                   |
| Two PRDs edit `destinationBinders.tsx` / `DestinationOutlet.tsx` concurrently (PRD-04 run identity).                             | PRD-03 owns the **signature** split; PRD-04 owns the **navigation semantics**. Land PRD-03 first; PRD-04 rebases onto the split.                                                                                                                                                                                                                                                     |

**Rollback.** Three independent revert points, in reverse dependency order: (1) revert the backend/facade access-mode commits and set both hosts' `onSetAccessMode: null` — the segment reverts to read-only, no schema rollback needed beyond `0046_connector_access_mode.rollback.sql`; (2) revert the host conformance tests and `src/contract/` — binding types stay, enforcement relaxes; (3) full revert restores optional props. Moves 1 and 2 are separable commits: Move 1 (deletions + projections) is behaviour-improving on its own and can ship alone if Move 2 stalls.

## Definition of Done

1. `cd /Users/parthpahwa/Documents/work/enterprise-search/.claude/worktrees/adoring-rosalind-939b76 && grep -rn "railBadges\|renderIcon" packages/chat-surface/src apps/frontend/src apps/desktop/renderer` returns **zero** hits (both props deleted, not re-plumbed).
2. `grep -rn "metaString\|metadata?.pinned\|metadata\.preview" apps/desktop/renderer` returns **zero** hits.
3. `grep -rn "cacheProjectNames" apps/frontend/src apps/desktop/renderer` returns **zero** hits; `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx` calls it inside an effect keyed on `items`.
4. `grep -rn "ConnectModal" apps/frontend/src apps/desktop/renderer` returns **zero** hits (mount folded into the destination).
5. `npm run typecheck --workspace @0x-copilot/desktop` **fails** when `railIdentity` is removed from `apps/desktop/renderer/bootstrap.tsx`'s `binding` object, and **passes** with it. (Verify by temporarily deleting the line; this is the compile-time guarantee the PRD buys.)
6. `packages/chat-surface/src/contract/manifest.ts` contains the `_Exhaustive` check shown above for **every** binding type; adding a field to `ShellHostBinding` without adding it to `SHELL_BINDING_FIELDS` fails `npx vitest run --root packages/chat-surface` at type-check time (assert via a `// @ts-expect-error` fixture test).
7. `apps/desktop/renderer/bindingContract.test.tsx` asserts, for each entry in every manifest, that the desktop mount supplies a non-`undefined` value, and asserts explicitly that `detail.mode === "disabled"` and `onAddCustomServer === null` are the two declared opt-outs (so closing them is a diff, not a discovery).
8. **Regression guard for the specific bug:** `apps/desktop/renderer/bindingContract.test.tsx` mounts the desktop shell with a `Transport` fixture returning two conversations whose `latest_run_status` is `"running"`, and asserts `[data-rail-badge]` exists with text `"2"`; and a second case with `railIdentity` derived from `session.displayName = "Sarah Chen"` asserting `[data-rail-initial]` text is `"S"`.
9. **Regression guard, projections:** `packages/chat-surface/src/projections/chats.test.ts` asserts that a `Conversation` with `preview: "hello"`, `model: "claude-sonnet-4.5"`, `pinned: true` **and** contradictory `metadata: { preview: "WRONG", model: "WRONG", pinned: false }` produces a row with `preview === "hello"`, `model === "claude-sonnet-4.5"`, and lands in the `pinned` bucket.
10. **Regression guard, arity:** `apps/desktop/renderer/bindingContract.test.tsx` renders `ActivityDestination` through the desktop binder with a running row `run_id: "run_abc"`, clicks it, and asserts the host callback received `"run_abc"` (not `undefined`).
11. **Design value pinned numerically:** `packages/chat-surface/src/destinations/connectors/ConnectorCard.test.tsx` asserts the tile element's inline style is `width: 30px`, `height: 30px`, `borderRadius: 7px`, `fontSize: 12px`, `fontWeight: 600`, `background: var(--panel3-equivalent token)` — matching `copilot.css:1604-1616` — and that it renders for a connector with **no** `icon_hint` (initial fallback).
12. **Design value pinned numerically, badge:** `packages/chat-surface/src/shell/AppRail.test.tsx` (existing case at `:266`) is extended to assert `minWidth: 13`, `height: 13`, `borderRadius: 7`, `fontSize: 8.5`, `fontWeight: 700` and `fontFamily: var(--font-mono)` on `[data-rail-badge]` — matching `copilot.css:343-355`.
13. `cd services/backend && .venv/bin/python -m pytest tests/ -k access_mode` passes, covering: `200` on set, `200` idempotent re-set with **no** new audit row, `400 invalid_request`, `403 owner_or_admin_only` for a non-owner non-admin, `404 connector_not_found` for another tenant's id.
14. `cd services/backend-facade && .venv/bin/python -m pytest tests/ -k access_mode` passes (proxy forwards method, path, body, identity headers).
15. `services/backend/migrations/0046_connector_access_mode.sql` and `services/backend/src/backend_app/connectors/schema.sql` both declare `access_mode TEXT NOT NULL DEFAULT 'read' CHECK (access_mode IN ('read','read_act','off'))` and the `connectors_tenant_access_mode_idx` index; `python tools/check_migration_manifest.py` passes.
16. `npx vitest run --root packages/chat-surface`, `npm run typecheck --workspace @0x-copilot/frontend`, `npm run typecheck --workspace @0x-copilot/desktop`, and both hosts' test suites are green.
17. The design-parity report for `tools` shows **0** HIGH rows for anchor `default.row.logo` (re-run per `tools/design-parity/SKILL.md`); `surfaces/tools/anchors.json` is updated so `default.row.logo`'s `live` selector points at the real tile instead of `null`.
18. `packages/chat-surface/CLAUDE.md` no longer instructs hosts to duplicate pure projections and names `src/projections/` as their home.

## Dependencies

**Must land first:** none. This PRD is the substrate the surface PRDs bind against, and should land **before** PRD-04, PRD-07 and PRD-09 to avoid three-way conflicts in `destinationBinders.tsx` and `DestinationOutlet.tsx`.

**Coordinate with:**

- **PRD-04 (run identity)** — PRD-03 splits `onOpenRun(runId)` from `onNewChat()` and forwards the id; PRD-04 decides where `onOpenRun(runId)` navigates. PRD-04 rebases onto the split.
- **PRD-05 (run history backend)** — re-points the relocated `useActiveRunCount` at the real run-list endpoint. PRD-03 moves the hook; PRD-05 corrects it.
- **PRD-07 (project data)** — needs `detail: { mode:"enabled" }` on desktop and the web-grid collapse; both become one-line binding flips once this lands.
- **PRD-09 (chats surface)** — pin write path, live refresh and pagination all land in the shared projection + the Chats binding introduced here.

**Unblocks:** every "desktop is missing X" item in the audits becomes either impossible (Move 1) or a compile error (Move 2). The Tools-surface PRD gets a real access-mode endpoint. The run-time enforcement of `access_mode` gets a persisted, tenant-scoped, audited column to enforce against.
