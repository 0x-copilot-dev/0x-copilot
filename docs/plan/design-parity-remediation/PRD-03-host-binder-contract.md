# PRD-03 — Host binding contract: end desktop under-binding, structurally

## Problem

Run the desktop app and the web app side by side on the same account. The desktop app is missing capabilities that are **already built, already tested, and already shipping in the binary**:

- The web rail foot shows your initial in a 26px disc. The desktop rail foot shows a generic person glyph, even though the desktop knows your display name and put it on screen during sign-in.
- Open **Projects** on desktop and click a card. Nothing opens. The project detail view is built, styled, and mounted on web; on desktop the branch that renders it is unreachable.
- Every desktop chat row shows a title, a chip and a time, and **nothing else** — no one-line preview, no model tag — and the "Pinned" section is permanently empty. Web shows all three.
- Every cross-destination project link on desktop reads the literal word **"Project"** instead of the project's name.
- Activity rows on desktop open _something_, but never the run you clicked: the binder passes a 0-arity callback where a `(runId) => void` is expected, so the id is silently discarded.

Five more instances of the identical mechanism are **owned by other PRDs** after the
program README's conflict register, and are listed here only as evidence that the
mechanism is systemic, never as work items in this PRD:

| Symptom                                                                          | Owner  |
| -------------------------------------------------------------------------------- | ------ |
| Run badge never appears on the desktop rail (`railBadges` / `useActiveRunCount`) | PRD-12 |
| Connector rows have no 30×30 logo tile on either host (`renderIcon` unbound)     | PRD-11 |
| The "Read / Read & act / Off" control is dead on desktop and 404s on web         | PRD-06 |
| The 3-step connect modal is mounted only by web                                  | PRD-11 |
| Web renders a bespoke Projects grid instead of `ProjectsDestination`             | PRD-07 |

None of these are missing features. Every one is a component in `packages/chat-surface` that works, has tests, and renders correctly the moment somebody passes it a prop. The desktop host never passes the prop, and **nothing anywhere fails** — not the compiler, not a lint, not a test. The capability ships invisible.

That is the actual defect this PRD fixes: **the seam, not the instances.** Nine props are
unbound today, two of them on **both** hosts. This PRD makes the shell and Projects
bindings total so the class cannot recur, and the surface PRDs above bind their own
instances against the contract it lands.

## Evidence

Every row opened and verified by me in this worktree (`claude/design-parity-audit-7ec82a`).
Rows marked **▸ PRD-nn** are evidence of the mechanism whose _fix_ belongs to another PRD
per the program README's conflict register; they are not work items here.

| Claim                                                                                   | File:line                                                                                                                      | What the code actually does                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Desktop `ChatShell` mount omits `railBadges` **▸ PRD-12**                               | `apps/desktop/renderer/bootstrap.tsx:318-331`                                                                                  | CONFIRMED. Props passed: `transport, router, keyValueStore, presenceSignal, activeDestination, destinations, onNavigate, onOpenSettings, onOpenCommandPalette, settingsActive`. No `railBadges`.                                                                                                                                                                                                                                                                                       |
| …and omits `railIdentity`                                                               | same mount                                                                                                                     | CONFIRMED. Absent from the same list. `props.session.displayName` is in scope — `ChatShellForSession` receives `session` at `bootstrap.tsx:142`, typed `RendererSession` at `:161`, `displayName: string \| null` at `chat-transport/src/ipc/rpc-protocol.ts:144`, populated at `apps/desktop/main/auth/index.ts:552`.                                                                                                                                                                 |
| Web binds both                                                                          | `apps/frontend/src/app/App.tsx:1217-1221`, `:1224-1226`                                                                        | CONFIRMED. `railIdentity` from `profile?.data?.display_name?.trim()`; `railBadges={activeRunCount > 0 ? { run: activeRunCount } : undefined}`.                                                                                                                                                                                                                                                                                                                                         |
| `railBadges` / `railIdentity` have exactly one call site repo-wide                      | grep across `apps/`, `packages/` excluding `chat-surface/src/shell/`                                                           | CONFIRMED. Two hits, both `App.tsx`. Zero in `apps/desktop`.                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `AppRail` renders the badge purely from the prop **▸ PRD-12**                           | `packages/chat-surface/src/shell/AppRail.tsx:246-247`, `:269-273`                                                              | CONFIRMED. `const count = badges?.[d.slug] ?? 0; const showBadge = count > 0 && !isActive;` then the `data-rail-badge` span. Style object at `:144`. `ChatShell` is a pure forwarder: `:130` declare → `:187` → `:294 badges={railBadges}`.                                                                                                                                                                                                                                            |
| `renderIcon` is declared, consumed twice, supplied by **neither** host **▸ PRD-11**     | `packages/chat-surface/src/destinations/connectors/ConnectorsDestination.tsx:134`, `:295`, `:333`                              | CONFIRMED. Repo-wide grep for `renderIcon` returns only the package's own declaration/consumption plus a doc comment. Zero hits under `apps/`.                                                                                                                                                                                                                                                                                                                                         |
| …and even if supplied, the tile chrome does not exist **▸ PRD-11**                      | `packages/chat-surface/src/destinations/connectors/ConnectorCard.tsx:128-132`, `:200-203`                                      | CONFIRMED. `icon` renders inside `<span style={iconStyle}>` where `iconStyle = { display:"inline-flex", flexShrink:0 }` — no size, no radius, no background. The design's 30×30 `--panel3` tile has no live counterpart at all.                                                                                                                                                                                                                                                        |
| The wire already carries the icon hint **▸ PRD-11**                                     | `packages/api-types/src/connectors.ts:208` (catalog), `:124-136` (connector)                                                   | CONFIRMED. `icon_hint?: string` on `ConnectorCatalogEntry`, documented "a hint string the FE may map to a built-in icon registry". Desktop already populates it (`destinationBinders.tsx:395` `icon_hint: entry.slug`).                                                                                                                                                                                                                                                                |
| …and the package already owns an icon-hint resolver **▸ PRD-11**                        | `packages/chat-surface/src/shell/PaletteHitRow.tsx:145`                                                                        | CONFIRMED. `{iconGlyph(hit.icon_hint)}` — the same problem already solved once, inside the package, without a host render-prop.                                                                                                                                                                                                                                                                                                                                                        |
| Desktop omits `onSetAccessMode` **▸ PRD-06**                                            | `apps/desktop/renderer/destinationBinders.tsx:481-489`                                                                         | CONFIRMED. Props: `items, filter, onFilterChange, onConnect, onOpenCatalogEntry, onOpenApprovalSettings, onRetry`. No `onSetAccessMode`, so `ConnectorsDestination.tsx:340-342` passes `undefined` and `ConnectorCard` renders a dead segment.                                                                                                                                                                                                                                         |
| **DISPUTED** — this is not only a desktop gap: the endpoint does not exist **▸ PRD-06** | grep `access_mode`/`access-mode` across `services/**/*.py`                                                                     | **Zero hits.** `apps/frontend/src/api/connectorsApi.ts:216` PATCHes `/v1/connectors/{id}/access-mode`; `services/backend-facade/.../connector_routes.py:40-54` enumerates every proxied path and has no access-mode entry. `packages/api-types/src/connectors.ts:129-135` says so in prose: "the facade does not serve it until the access-mode PATCH lands". **Web's control 404s.** The audit framed this as desktop under-binding; the code says it is a product gap on both hosts. |
| Desktop omits `renderDetail` **and** `focusedProjectId`                                 | `apps/desktop/renderer/destinationBinders.tsx:563-568`                                                                         | CONFIRMED. `return <ProjectsDestination items={result} onRetry={retry} />;` at `:567` — two props total.                                                                                                                                                                                                                                                                                                                                                                               |
| …making the detail branch dead code on desktop                                          | `packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx:283`                                                  | CONFIRMED. `const showingDetail = renderDetail !== undefined && focusedProjectId !== null;` — both conditions false forever on desktop. Web supplies both (`ProjectsRoute.tsx:826-828`).                                                                                                                                                                                                                                                                                               |
| `ConnectModal` is mounted only by web **▸ PRD-11**                                      | `apps/frontend/src/features/connectors/ConnectorsRoute.tsx:603-617`                                                            | CONFIRMED. Repo-wide, `ConnectModal` appears in the package (`destinations/connectors/ConnectModal.tsx`, barrels) and in exactly one host file. Desktop's `ConnectorsBinder` wires `onOpenCatalogEntry={connect}` (`destinationBinders.tsx:486`) straight to `CONNECTOR_CHANNELS.connect` (`:462`), skipping catalog/permission entirely.                                                                                                                                              |
| Desktop chats read three keys nothing writes                                            | `apps/desktop/renderer/destinationBinders.tsx:163-167`, `:178-181`                                                             | CONFIRMED. `metaString(conversation,"preview")`, `metaString(conversation,"model")`, `metadata?.pinned === true`. Repo-wide grep finds no writer for `metadata.preview` / `.model` / `.pinned`.                                                                                                                                                                                                                                                                                        |
| The backend serves all three as first-class fields                                      | `packages/api-types/src/index.ts:561-578`; `services/ai-backend/src/agent_runtime/api/conversation_query_service.py:461-490`   | CONFIRMED. `pinned?: boolean` ("Projected from a real `pinned` column (migration 0034), **not** `metadata` — the metadata path was never written, so Pinned was always empty"), `preview?: string \| null`, `model?: string \| null`. Server projects preview + model in `_with_preview`-style code at `:478-490`.                                                                                                                                                                     |
| Web already migrated to the first-class fields                                          | `apps/frontend/src/features/chats/api/chatsApi.ts:150-176`                                                                     | CONFIRMED. `isPinned` → `conversation.pinned === true`; `previewOf` → `conversation.preview ?? ""`; `modelOf` → `conversation.model ?? ""`, each with a PRD-H.4 comment. The desktop copy was never updated.                                                                                                                                                                                                                                                                           |
| Desktop never primes the project-name cache                                             | grep `cacheProjectNames` across `apps/`                                                                                        | CONFIRMED. One hit: `apps/frontend/src/features/projects/ProjectsRoute.tsx:302`. Desktop: zero.                                                                                                                                                                                                                                                                                                                                                                                        |
| …so every desktop project link says "Project"                                           | `packages/chat-surface/src/destinations/projects/index.ts:166-172`                                                             | CONFIRMED. `label: getCachedProjectName(id) ?? "Project"`. Cache is a module singleton (`projectNameCache.ts:17`) primed only by a host call.                                                                                                                                                                                                                                                                                                                                          |
| Two more props are unbound on **both** hosts                                            | `packages/chat-surface/src/shell/ChatShell.tsx:97` (`topbarLeaf`), `:138` (`walletChip`)                                       | CONFIRMED. Grep for `topbarLeaf` and `walletChip` outside the package returns nothing. The systemic count is 9, not 6, and it is not a desktop-only disease.                                                                                                                                                                                                                                                                                                                           |
| Desktop discards Activity's `runId` argument                                            | `apps/desktop/renderer/destinationBinders.tsx:367`                                                                             | CONFIRMED (audit cited `:369` — 2-line drift). `onOpenRun={() => onOpenRun?.()}` against `ActivityDestination.tsx:232` `onOpenRun?: (runId: RunId) => void` and `:543-544` `() => onOpenRun(row.run_id)`. **Types cannot catch this** — a 0-arity function is assignable to a 1-arity signature.                                                                                                                                                                                       |
| Web does not mount `ProjectsDestination` for the list at all                            | `tools/design-parity/surfaces/projects/out/FINDINGS.md:28-33`; `apps/frontend/src/features/projects/ProjectsRoute.tsx:783-829` | CONFIRMED by reading both. Web renders a bespoke grid and mounts the shared destination only when `focusedProjectId !== null`. Same class of divergence, opposite host. Assigned to PRD-07 (see Dependencies), not fixed here.                                                                                                                                                                                                                                                         |
| The measured HIGH row for the missing tile **▸ PRD-11**                                 | `tools/design-parity/surfaces/tools/out/report-default.md` (HIGH table)                                                        | CONFIRMED. `default.row.logo · missing-in-live` — "present in design, ABSENT in live". Anchor rationale at `surfaces/tools/anchors.json:57-63` names `renderIcon` as the cause.                                                                                                                                                                                                                                                                                                        |
| The rail-badge audit's own caveat                                                       | `tools/design-parity/surfaces/rail-badge/out/AUDIT.md` (R-1, Confidence §)                                                     | CONFIRMED. The harness feeds `railBadges={{run:1}}` deliberately (`lib/render-live-rail-badge.test.tsx:113`), so the parity pixels do **not** show this gap. It is a wiring finding, verified by grep, not by pixels.                                                                                                                                                                                                                                                                  |

## Design intent

Literal values from `tools/design-parity/design-kit/app-v3/`.

**Rail identity content** — `copilot-app.jsx:811-813`:

```jsx
<button className="rail-me" title={prefs.name}>
  {prefs.name.slice(0, 1)}
</button>
```

**Exactly one character**, taken raw from the display name — not upper-cased, and never a
generic person glyph. That literal (`slice(0, 1)` → length 1) is the design value this PRD
pins, because it is the value the _binding_ determines: today desktop supplies no name at
all, so `AppRail.tsx:300-306` falls through to `<Icon name="user" />`.

The disc's **chrome** — `.rail-me` 26×26 / radius 50% / `--panel3` / `--tx2` / 11px / 600 /
`1px solid --line2` (`copilot.css:366-377`), the `.toUpperCase()` at `AppRail.tsx:302`, and
the `"Account"` tooltip — is **PRD-12's** (`AppRail.tsx` is PRD-12-owned; see the README's
hot-file table). This PRD only guarantees a name reaches it on both hosts.

**Rail badge geometry** (`copilot.css:343-355`) and the **connector row tile**
(`copilot.css:1604-1616`) are specified in **PRD-12** and **PRD-11** respectively; neither
is restated here (C1, C6).

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

| Prop / duty                                                          | Why it is not host-owned                                                                                                                                                                                                                                                                                                                                               | After                                                                                                                                                                                                                        |
| -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Host call to `cacheProjectNames`                                     | `ProjectsDestination` already receives the exact `{id,name}` list the cache wants (`items: SectionResult<ProjectSummary[]>`). Asking the host to re-hand it to a module singleton is a duty with no decision in it.                                                                                                                                                    | **Deleted as a host duty.** `ProjectsDestination` primes the cache in an effect over `items`.                                                                                                                                |
| Duplicated per-row chat projection (`Conversation → ChatArchiveRow`) | It operates solely on `@0x-copilot/api-types` shapes — no `window`, no `fetch`, no navigation. `packages/chat-surface/CLAUDE.md:62` currently _instructs_ hosts to duplicate it; that instruction is what let web migrate to `conversation.pinned` in PRD-H.4 (`chatsApi.ts:150-176`) while desktop kept reading `metadata.pinned` (`destinationBinders.tsx:163-181`). | **Moved** to `chat-surface/src/projections/chats.ts` as `toChatArchiveRow`. Both hosts call one function. **`packages/chat-surface/CLAUDE.md:62` is amended**: pure projections over api-types shapes belong in the package. |

Two rows that stood here in the first draft are now owned elsewhere and have been removed
rather than duplicated: `ConnectorsDestinationProps.renderIcon` and the host mount of
`<ConnectModal>` are **PRD-11**'s (C5, C6 — desktop's renderer is denied `window.open`, so
authorization is a genuine host capability, and PRD-11 keeps `renderIcon` as an _override_
over a default `AppIcon` tile rather than deleting it); `ChatShellProps.railBadges` and the
relocation of `useActiveRunCount` are **PRD-12**'s (C1).

**Bucketing is not in this PRD.** `toChatArchiveRow` is per-row only. `bucketConversations`
moves into the SQL query and out of the client in **PRD-09 D1** (C8); shipping a shared
bucketer here would be deleted two waves later.

**The Activity projection is not in this PRD.** `buildMetaIndex` / `projectActivityRows`
land in **PRD-04** at `src/destinations/activity/activityProjection.ts` — matching the
in-tree `destinations/run/chatProjection.ts` precedent (C7). This PRD only splits the
callback signature that feeds it (Move 3).

Move 1 fixes the desktop chats dead-read and the desktop project-name fallback, and removes
two host duties as a _category_.

### Move 2 — Make what genuinely remains total

Everything left is a real host decision (identity source, navigation, persistence effects, substrate flows). For those, optionality moves from the **type** to the **value**:

```ts
// packages/chat-surface/src/contract/shellBinding.ts
export interface ShellHostBinding {
  readonly railIdentity: { readonly displayName: string } | null;
  readonly walletChip: ReactNode | null;
  readonly topbarLeaf: string | null;
  readonly settingsActive: boolean;
}
```

`railIdentity` carries the **display name**, not a pre-computed initial — PRD-12's shape
(C2), adopted here directly so the prop changes exactly once. `AppRail.tsx:99` still types
`identity?: { initial: string }` today and `AppRail.tsx` is PRD-12-owned, so `ChatShell`
carries a **one-line shim** at its `<AppRail>` call (`ChatShell.tsx:293`):

```tsx
// PRD-12 deletes this shim when AppRail takes { displayName } and derives the glyph.
identity={binding.railIdentity ? { initial: binding.railIdentity.displayName } : undefined}
```

Deriving the glyph (`charAt(0)`, no `.toUpperCase()`) and the `title`/`aria-label` are
PRD-12's, per its D5.

Required, and `undefined` is **not** in any union — so an omitted field is a compile error and an opt-out is a literal `null` that appears in the diff and gets reviewed. Same treatment for the Projects destination:

```ts
// ProjectsDestination
readonly detail:
  | { readonly mode: "disabled" }
  | { readonly mode: "enabled"; readonly focusedProjectId: ProjectId | null;
      readonly renderDetail: RenderProjectDetailSlot; readonly onCloseDetail: () => void };
```

Desktop must now write `detail: { mode: "disabled" }` — an explicit, reviewable statement that desktop has no project detail yet — instead of silently having none.

**`ConnectorsDestination` is out of scope for binding-totality in this PRD** (C6). Its props
are being rewritten by PRD-06 (`accessPort`, delete `onSetAccessMode`) and then PRD-11
(delete `filter`/`counts`/`onOpenCatalogEntry`, `useConnectFlow`); applying totality to a
prop set two PRDs are about to replace would guarantee a three-way conflict in one file.
The file order is **06 → 11**. When PRD-11 lands, its author applies the same
required-nullable discipline to the resulting props and adds them to the manifest
(Move 3) — that is the contract this PRD exists to make available.

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

This PRD ships **two** manifests — `SHELL_BINDING_FIELDS` and `PROJECTS_BINDING_FIELDS` — and no more; PRD-11 adds the connectors manifest when it lands the props it owns (C6).

Adding a field to a binding type without adding it to the manifest fails `tsc` inside the package. Each host then owns one thin conformance test (`apps/frontend/src/app/bindingContract.test.tsx`, `apps/desktop/renderer/bindingContract.test.tsx`) that mounts its **real** shell/binders against fixtures and asserts, per manifest entry, the observable effect — including `onOpenRun` being invoked **with the row's `run_id`**. Adding a capability means editing one manifest; two host tests go red until both hosts answer.

### Backend capability this PRD does **not** build

The first draft of this PRD specified the `PATCH /v1/connectors/{connector_id}/access-mode`
route, the `access_mode` column, migration `0046`, the facade proxy and the authz rule.
**All of it is PRD-06's** (C3), which additionally lands the three run-time enforcement
gates this PRD had named a non-goal. Nothing about it is restated here, and this PRD ships
**no migration** — its `0046_connector_access_mode.sql` is deleted from the program
(README migration table). Verified on disk: `services/backend/migrations` high-water mark is
**`0045`** (`0045_provider_api_keys_custom_endpoint.sql`) and `services/ai-backend/migrations`
holds only **`0001`** (`0001_runtime_baseline.sql`), so `0046` is PRD-06's to claim.

### Alternatives rejected

1. **Add the six props.** The 7th and 8th (`walletChip`, `topbarLeaf`) are already unbound on both hosts, today, with no ticket. The mechanism survives.
2. **One shared binder module both hosts import.** Impossible without breaking a hard boundary or the package's substrate rule: desktop's connectors binder needs `window.bridge` IPC (`destinationBinders.tsx:460-463`) and `../main/connectors/channels`; `chat-surface/eslint.config.js:57` bans bare `window`. Housing it in a new package re-creates `apps/*→apps/*` under a different name. The _pure_ half genuinely is shareable and does move (Move 1); the impure half is genuinely per-host.
3. **Dev-mode runtime warnings for missing props.** A console line nobody reads, no CI signal. It would have "caught" all nine and prevented none.
4. **Contract tests only, props left optional.** A test you must remember to write is the same failure mode as a prop you must remember to pass. Types first; tests only for the residue types cannot express.
5. **Making every currently-optional prop required, mechanically.** Over-fires. `renderDetail` required would force desktop to build a whole detail flow to compile; a required `renderIcon` would enshrine a host render-prop where PRD-11 lands a package-owned default tile. Classification (Move 1 vs Move 2 vs "leave it to the surface PRD") is load-bearing.

## Scope

### `packages/chat-surface`

- `src/contract/shellBinding.ts` — **new.** `ShellHostBinding` + per-destination binding types.
- `src/contract/manifest.ts` — **new.** Type-derived exhaustive field manifests consumed by both host conformance tests.
- `src/contract/index.ts` — **new.** Barrel for the above.
- `src/shell/ChatShell.tsx` — accept `binding: ShellHostBinding`; forward `railIdentity`/`walletChip`/`topbarLeaf`/`settingsActive` from it; add the one-line `{displayName} → {initial}` shim at the `<AppRail>` call (`:293`) with the `// PRD-12 deletes this shim` comment. **Does not touch `railBadges`** — PRD-12 deletes that prop (C1).
- `src/shell/AppRail.tsx` — **untouched.** Its props, rendering and identity shape are PRD-12's (README hot-file table: `01 → 03 → 12 owns`); this PRD adapts at the `ChatShell` boundary instead.
- `src/destinations/projects/ProjectsDestination.tsx` — collapse `renderDetail`/`focusedProjectId`/`onCloseDetail` into the required `detail` union; prime the name cache from `items`.
- `src/destinations/projects/projectNameCache.ts` — keep `cacheProjectNames` exported for tests; drop it from the public barrel.
- `src/projections/chats.ts` — **new.** `toChatArchiveRow(conversation): ChatArchiveRow` **only** — reading the first-class `pinned` / `preview` / `model` (`api-types/src/chats.ts:62-73`). **No `bucketConversations`** (C8 — PRD-09 D1 moves bucketing into the query).
- `src/projections/chats.test.ts` — **new.** Including a regression case asserting `metadata.preview` is **ignored** and `conversation.preview` wins.
- `src/index.ts` — new delimited export block for `contract/` + `projections/`; remove `cacheProjectNames`.
- `CLAUDE.md:62` — amend the "binders intentionally duplicate the same pure projection logic" paragraph; record that pure api-types projections now live in `src/projections/` (chats) and `src/destinations/activity/activityProjection.ts` (activity, PRD-04).

**Not in this PRD** (was in the first draft; reassigned by the README):
`src/shell/useActiveRunCount.ts` + test → **PRD-12** (C1);
`src/projections/activity.ts` → **PRD-04**, at `destinations/activity/activityProjection.ts` (C7);
`src/icons/connectorGlyph.tsx`, `ConnectorCard.tsx`, `ConnectorsDestination.tsx`, `ConnectModal.tsx` → **PRD-06** then **PRD-11** (C3, C5, C6).

### `apps/desktop`

- `renderer/bootstrap.tsx` — construct `ShellHostBinding`: `railIdentity` from `props.session.displayName?.trim()` as `{ displayName }` (`null` when blank), `walletChip: null`, `topbarLeaf: null`, `settingsActive`.
- `renderer/destinationBinders.tsx` — delete `metaString` (`:163`) and the local `toArchiveRow` (`:163-199`); call `toChatArchiveRow`; pass `detail: { mode:"disabled" }` at `ProjectsBinder` (`:563-567`); forward Activity's `runId` (`:367`). **Connectors props are untouched here** — PRD-06 then PRD-11 own that binder block (`:445-491`).
- `renderer/DestinationOutlet.tsx` — split the overloaded `onOpenRun?: () => void` (`:128`) into `onOpenRun: (runId: RunId) => void` and `onNewChat: () => void`; update the three call sites (`:230`, `:367`, `:535`).
- `renderer/destinationBinders.test.tsx` — update fixtures; assert preview/model/pinned come from first-class fields.
- `renderer/bindingContract.test.tsx` — **new.** Manifest-driven conformance test.
- `renderer/bindingContract.test-d.ts` — **new.** Type-level test (DoD 5).

### `apps/frontend`

- `src/app/App.tsx` — construct `ShellHostBinding` (`:1200-1226`) and pass `binding`. **Leaves `railBadges` and the stale `:1214-1216` comment alone** — PRD-12 deletes both (C1).
- `src/features/chats/api/chatsApi.ts` — delegate the per-row projection to `toChatArchiveRow`; keep the file as the fetch + bucket layer until PRD-09 deletes it.
- `src/features/projects/ProjectsRoute.tsx` — `detail: { mode:"enabled", … }`; drop the `cacheProjectNames` call at `:302`.
- `src/app/bindingContract.test.tsx` — **new.**

## Non-goals

- **Anything `access_mode`** — the column, the `PATCH` route, the facade proxy, the port and all three enforcement gates. **PRD-06 owns the whole thing** (C3), including the migration id `0046` this PRD no longer claims.
- **Anything under `destinations/connectors/`** — `renderIcon`, the 30×30 tile, `ConnectModal`'s mount, `useConnectFlow`, the filter props. **PRD-06 then PRD-11** (C5, C6). Desktop's custom-MCP-add gap is PRD-11's `onAddCustomServer`, not a field in this PRD's manifest.
- **The Run badge, in every respect** — the `railBadges` deletion, `useActiveRunCount`'s relocation and its data source, the `9+` cap and the badge's geometry. **PRD-12** (C1); the count's correctness is PRD-05's `active_count` endpoint feeding it.
- **`AppRail` itself** — the identity glyph derivation, `.toUpperCase()` (`AppRail.tsx:302`), the `"Account"` tooltip, the rail-foot divider/gap deltas (R-3 / R-5 / R-7). **PRD-12** (C2). This PRD only guarantees `{ displayName }` arrives.
- **Bucketing chats into pinned/recent/archived.** `toChatArchiveRow` is per-row only; **PRD-09 D1** moves bucketing into the SQL query (C8).
- **The Activity projection** (`buildMetaIndex` / `projectActivityRows`). **PRD-04**, at `destinations/activity/activityProjection.ts` (C7).
- **Building a project detail flow on desktop.** Desktop declares `detail: { mode:"disabled" }`. The point is that the gap becomes visible and reviewable, not that it closes today.
- **Collapsing web's bespoke Projects grid onto `ProjectsDestination`** (`ProjectsRoute.tsx:783-829`) — same disease, different host; owned by PRD-07.
- **Any pixel fix** to `StatusPill`, the 13px body baseline, `.sect-h`, row padding, or `--font-size-*`. PRD-01 / PRD-02.
- **Activity's run→route semantics.** PRD-03 splits the overloaded callback and forwards the id; what `onOpenRun(runId)` should navigate to is PRD-04.

## Risks & rollback

| Risk                                                                                                          | Guard / mitigation                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Making binding types required breaks every existing mount at once.                                            | Only two hosts and the package's own tests consume them (`packages/chat-surface/CLAUDE.md` barrel rule). `npm run typecheck` in all three workspaces enumerates every site; the change is mechanical and total by construction.                                                                        |
| Shared per-row projection changes desktop chats output (previews/models/pinned appear where there were none). | That is the fix. `apps/desktop/renderer/destinationBinders.test.tsx` fixtures currently encode the `metadata` shape and must be rewritten — treat a green old fixture as evidence of the bug, per PRD-05's precedent.                                                                                  |
| **`ChatShell.tsx` is edited by three PRDs** (03 → 09 → 12) and `AppRail.tsx` by three (01 → 03 → 12).         | This PRD keeps its `AppRail` footprint to **zero** by shimming at the `ChatShell` call site (`:293`); PRD-12 deletes the shim in the same commit that changes `AppRail.identity`. The `SUPPRESS_TOPBAR` / `FULL_BLEED_DESTINATIONS` split in the same file is **PRD-09's** (C14) — do not pre-empt it. |
| **`railIdentity` shape churn.** PRD-12's Risks row still says "PRD-03 lands first with `{initial}`".          | Superseded by README **C2**: this PRD binds `{ displayName }` on day one, so the prop changes exactly once. If an implementer follows PRD-12's stale row instead, `npm run typecheck --workspace @0x-copilot/desktop` catches the mismatch at PRD-12's merge.                                          |
| Two PRDs edit `destinationBinders.tsx` / `DestinationOutlet.tsx` concurrently (PRD-04 run identity).          | PRD-03 owns the **signature** split; PRD-04 owns the **navigation semantics**. Land PRD-03 first; PRD-04 rebases onto the split. `destinationBinders.tsx` is the program's hottest file (eight claimants) — one merge owner per wave.                                                                  |

**Rollback.** Two independent revert points, in reverse dependency order: (1) revert the host conformance tests and `src/contract/` — binding types stay, enforcement relaxes; (2) full revert restores optional props. Moves 1 and 2 are separable commits: Move 1 (the `cacheProjectNames` duty deletion + `toChatArchiveRow`) is behaviour-improving on its own and can ship alone if Move 2 stalls. This PRD ships **no migration and no server change**, so there is nothing to roll back below the TypeScript layer.

## Definition of Done

Every item is one command with a stated expected output, or a named assertion in a named
file. All commands run from the repo root.

1. `grep -rn "metaString\|metadata?\.pinned\|metadata\.preview\|metadata\.model" apps/desktop/renderer` → **0 lines**.
2. `grep -rn "cacheProjectNames" apps/frontend/src apps/desktop/renderer` → **0 lines**, and `grep -n "cacheProjectNames" packages/chat-surface/src/destinations/projects/ProjectsDestination.tsx` → **≥1 line**, inside a `useEffect` whose dependency array is `[items]`.
3. **One per-row projector, no bucketer.** `grep -rln "toChatArchiveRow" apps/desktop/renderer apps/frontend/src` → **2 files**; `grep -rn "function toArchiveRow" apps/desktop/renderer apps/frontend/src` → **0 lines**; `grep -n "bucketConversations" packages/chat-surface/src/projections/chats.ts` → **0 lines** (bucketing is PRD-09 D1's, per C8).
4. **Manifest exhaustiveness is enforced by the compiler.** `packages/chat-surface/src/contract/manifest.test-d.ts` declares a `SHELL_BINDING_FIELDS`-shaped literal omitting `topbarLeaf` under a `// @ts-expect-error binding manifest is missing a field` directive, and `npm run typecheck --workspace @0x-copilot/chat-surface` exits **0**. (Verified: `packages/chat-surface/tsconfig.json` includes all of `src`, and an unfired `@ts-expect-error` is itself a `tsc` error — so the item fails in both directions.)
5. **(DoD-Q1 rewrite.)** `apps/desktop/renderer/bindingContract.test-d.ts` contains a `ShellHostBinding` literal omitting `railIdentity` under `// @ts-expect-error missing railIdentity`, and `npm run typecheck --workspace @0x-copilot/desktop` exits **0**. No manual mutation of `bootstrap.tsx`. (Verified: `apps/desktop/tsconfig.json:12-18` includes `renderer/**/*.ts`, so the file is typechecked by that script.)
6. `apps/desktop/renderer/bindingContract.test.tsx` iterates `SHELL_BINDING_FIELDS` and asserts the desktop binding object has a key for every entry with a value that is not `undefined`; and asserts the declared opt-outs literally — `binding.walletChip === null`, `binding.topbarLeaf === null`, and `ProjectsBinder`'s `detail.mode === "disabled"` — so closing one is a diff, not a discovery.
7. **Regression guard that fails on `main`:** `apps/desktop/renderer/bindingContract.test.tsx` mounts the real desktop shell with `session.displayName = "Sarah Chen"` and asserts `[data-rail-initial]` is in the document (`AppRail.tsx:301`); a second case with `session.displayName = null` asserts `[data-rail-me] svg` instead. On `main` the first case fails, because `bootstrap.tsx:318-331` passes no identity at all and the person glyph always renders.
8. **Regression guard, projection:** `packages/chat-surface/src/projections/chats.test.ts` asserts that a `Conversation` with `preview: "hello"`, `model: "claude-sonnet-4.5"`, `pinned: true` **and** contradictory `metadata: { preview: "WRONG", model: "WRONG", pinned: false }` produces `{ preview: "hello", model: "claude-sonnet-4.5", pinned: true }` — the exact drift `destinationBinders.tsx:163-181` ships today.
9. **Regression guard, arity:** `apps/desktop/renderer/bindingContract.test.tsx` renders `ActivityDestination` through the desktop binder with a row whose `run_id` is `"run_abc"`, clicks it, and asserts the host spy was called with `"run_abc"` — not with `undefined` and not with zero arguments (`expect(spy).toHaveBeenCalledWith("run_abc")`). Fails on `main` at `destinationBinders.tsx:367`.
10. **Design value pinned numerically:** the same test asserts `document.querySelector('[data-rail-initial]')!.textContent!.length === 1` — the design's `prefs.name.slice(0, 1)` (`copilot-app.jsx:811-813`), exactly one character. Case, tooltip and disc chrome are asserted by PRD-12 (`AppRail.test.tsx`), not here.
11. `npm run typecheck` exits **0** for `@0x-copilot/chat-surface`, `@0x-copilot/frontend` and `@0x-copilot/desktop`.
12. `npm run test --workspace @0x-copilot/chat-surface`, `--workspace @0x-copilot/frontend` and `--workspace @0x-copilot/desktop` exit **0**, **or** the failing test ids are byte-identical to `docs/plan/design-parity-remediation/baseline-failures.txt`, which this PR does not modify.
13. `npm run lint --workspace @0x-copilot/chat-surface` exits **0** — proving `src/contract/` and `src/projections/` contain no bare `window` / `fetch` / `localStorage` (`chat-surface/eslint.config.js:57`).
14. `grep -n "intentionally duplicate" packages/chat-surface/CLAUDE.md` → **0 lines**, and `grep -n "src/projections/" packages/chat-surface/CLAUDE.md` → **≥1 line**.
15. **Parity is unmoved, and that is the claim.** Regenerate the `chats` and `rail-badge` reports per `tools/design-parity/SKILL.md`, then `git diff -- tools/design-parity/surfaces/chats/out/report-*.md tools/design-parity/surfaces/rail-badge/out/report-*.md` shows **no line added under any `## HIGH` heading**. This PRD is a wiring change; the rail-badge harness feeds `railBadges={{run:1}}` and a fixture identity directly (`lib/render-live-rail-badge.test.tsx:113`), so it is structurally blind to the defect this PRD fixes — items 7 and 9 are the guards, and item 15 only proves nothing regressed.

**Moved out of this DoD** (do not re-add; the owning PRD asserts them): the `railBadges`
grep and the badge-geometry pin → **PRD-12** (its DoD 7 and 12); the `ConnectModal` grep and
the 30×30 connector-tile pin → **PRD-11** (its DoD 3); the `access_mode` pytest, facade
proxy and migration/`schema.sql` items → **PRD-06**; the `tools` `default.row.logo` parity
row → **PRD-11**.

## Dependencies

**Must land first:** none — true only after C3/C5/C6 shrank this PRD out of `services/backend`, `services/backend-facade` and `destinations/connectors/`. **Wave 1**, in parallel with PRD-02 (disjoint file sets: 02 = `styles.css` + `StatusPill`/`statusTone` + six destination call sites; 03 = `contract/`, `shell/ChatShell`, `projections/chats`, both host binders). PRD-04 follows in the same wave.

**Coordinate with:**

- **PRD-04 (run identity)** — PRD-03 splits `onOpenRun(runId)` from `onNewChat()` and forwards the id; PRD-04 decides where `onOpenRun(runId)` navigates **and owns the Activity projection** at `destinations/activity/activityProjection.ts` (C7). PRD-04 rebases onto the signature split.
- **PRD-06 (connector access mode)** — owns the `access_mode` column, `PATCH` route, facade proxy, `ConnectorAccessPort` and all enforcement, plus migration `0046` (C3). PRD-03 ships nothing under `services/`.
- **PRD-07 (project data)** — needs `detail: { mode:"enabled" }` on desktop and the web-grid collapse; both become one-line binding flips once this lands. PRD-07 also consumes `toChatArchiveRow` for project-scoped chat rows.
- **PRD-09 (chats surface)** — consumes `toChatArchiveRow` inside `useChatsArchive`, and **deletes** any client-side bucketing when bucketing moves into the SQL query (C8). Pin write path, live refresh and pagination are PRD-09's, not this PRD's. PRD-09 also owns the `SUPPRESS_TOPBAR` / `FULL_BLEED_DESTINATIONS` split in `ChatShell.tsx` (C14) — land 03 → 09 → 12 in that file.
- **PRD-11 (tools surface)** — owns `renderIcon`'s fate, the `AppIcon` tile, `useConnectFlow` and the `ConnectModal` mount on **both** hosts (C5). It applies this PRD's required-nullable discipline to the connectors props it lands, and adds them to the manifest.
- **PRD-12 (rail & settings)** — owns `railBadges`' deletion, `useActiveRunCount`, `active_count`, the `AppRail` identity shape and all rail chrome (C1, C2). This PRD binds `railIdentity: { displayName }` on day one so the prop changes once; PRD-12 then deletes the `ChatShell` shim. **PRD-12's own Risks row still says "PRD-03 lands first with `{initial}`" — that row is stale; C2 governs.**

**Unblocks:** every "desktop is missing X" item in the audits becomes either impossible (Move 1) or a compile error (Move 2), and every surface PRD downstream gets a total binding to declare its capability in instead of an optional prop to forget.
