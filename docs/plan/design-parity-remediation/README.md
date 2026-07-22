# Design-parity remediation — program index

Thirteen PRDs that close the measured gap between the Claude Design **0xCopilot App v3**
mock and the live web + desktop apps. Every PRD was authored against the on-disk audit
evidence in `tools/design-parity/surfaces/*/out/` and the design source in
`tools/design-parity/design-kit/app-v3/`.

Standing constraint for the whole program: **no bandaids, only architectural solutions.**
If a defect appears on N surfaces, the PRD fixes the seam, not N call sites. If a
capability works on one host and not the other, the fix is a port/binder seam in
`packages/chat-surface` that both hosts feed — never a copy of the web code into desktop.

> **This README is normative for cross-PRD decisions.** The PRDs were written in
> parallel and therefore contain conflicts, duplicate ownership, and stale
> cross-references. Where a PRD disagrees with the _Conflict register_ below, this
> README wins. Apply the register's corrections to the PRD text **before** implementing
> it.

---

## The PRDs

| ID  | Title                                                    | Wave | Depends on (corrected) | Owns                                                                                                          |
| --- | -------------------------------------------------------- | ---- | ---------------------- | ------------------------------------------------------------------------------------------------------------- |
| 01  | [Design token foundation](PRD-01-design-tokens.md)       | 0    | —                      | `styles.css` token tier: accent seed/derived split, `--font-size-sm` 13px, mono micro-ladder, `--color-scrim` |
| 02  | [Status chip recipe](PRD-02-chip-recipe.md)              | 1    | 01                     | `.ui-badge` chip-exactness, `StatusPill` rewrite, one status-label SSOT                                       |
| 03  | [Host binding contract](PRD-03-host-binder-contract.md)  | 1    | —                      | Total shell/destination binding types + per-host conformance tests, shared chats projector                    |
| 04  | [Run identity](PRD-04-run-identity.md)                   | 1    | 03                     | `ItemRef` registry split (label→caller, routes→host), Activity projection, `ItemLink`                         |
| 05  | [Run history backend](PRD-05-run-history-backend.md)     | 0    | —                      | `GET /v1/agent/runs`, keyset cursor, `ActiveAgentRunStatus` narrowing, history tombstoning                    |
| 06  | [Connector access mode](PRD-06-connector-access-mode.md) | 0    | —                      | `access_mode` column + PATCH + **all** enforcement, `ConnectorAccessPort`                                     |
| 07  | [Project data](PRD-07-project-data.md)                   | 2    | 02, 03, 05(order only) | `agent_conversations.project_id`, computed rollups, project-scoped chats/files reads                          |
| 08  | [Activity surface](PRD-08-activity-surface.md)           | 2    | 04, 05                 | **`_shared/Row.tsx`**, `.ui-list-row`, run meta counters, `runtime_tool_invocations` writer                   |
| 09  | [Chats surface](PRD-09-chats-surface.md)                 | 3    | 02, 03, 08             | `useChatsArchive`, bucket+cursor list contract, conversations SSE, **topbar/full-bleed split**                |
| 10  | [Projects surface](PRD-10-projects-surface.md)           | 4    | 03, 07, 08             | `_shared/Page` + `BackLink` + `ProjectIconTile`, one Projects list, `.ui-grid3`                               |
| 11  | [Tools surface](PRD-11-tools-surface.md)                 | 3    | 01, 03, 06, 09         | Tools → row-list vocabulary, `AppIcon` tile, `useConnectFlow`, **`ConnectModal` on both hosts**               |
| 12  | [Rail & Settings](PRD-12-rail-settings.md)               | 3    | 03, 05, 09             | **`railBadges` deletion + `useActiveRunCount`**, `active_count` endpoint, rail chrome, `settingsActive`       |
| 13  | [Dead code + orphan guard](PRD-13-dead-code.md)          | 4    | 09, 10                 | `ChatsSidebar`/`ChatsDestination` deletion, `tools/check_orphan_destinations.py` + CI gate                    |

---

## Corrected implementation order

```
Wave 0  ── PRD-01 ‖ PRD-05 ‖ PRD-06                (disjoint; land together)
Wave 1  ── PRD-02 ‖ PRD-03  →  PRD-04
Wave 2  ── PRD-08            →  PRD-07              (both touch destinationBinders.tsx)
Wave 3  ── PRD-09            →  PRD-11 ‖ PRD-12
Wave 4  ── PRD-10            →  PRD-13
```

Rationale for the non-obvious edges:

- **PRD-08 before PRD-09/PRD-11/PRD-10** — PRD-08 owns `_shared/Row.tsx` (see C9). Three
  other PRDs add props to it; they must add them to the post-PRD-08 file.
- **PRD-09 before PRD-12** — PRD-09 owns the `SUPPRESS_TOPBAR` / `FULL_BLEED_DESTINATIONS`
  split (C14). PRD-12 D3 explicitly adopts it verbatim; landing PRD-12 first means writing
  the same set twice.
- **PRD-09 before PRD-11** — both add props to `Row.tsx`; PRD-11 declares itself the owner
  of `subFont`/`iconSize` and must stack on PRD-09's overflow slot.
- **PRD-07 after PRD-08** — `apps/desktop/renderer/destinationBinders.tsx` is edited by
  eight PRDs; PRD-08 deletes the audit-fan-out block that PRD-07 would otherwise re-touch.
- **PRD-13 last** — its orphan-waiver list and its "parity is unmoved" DoD are only
  computable against a settled tree (see C20 / DoD-Q10).
- **PRD-05 before PRD-09** — PRD-09 D4 makes `update_run_status` bump the conversation's
  `updated_at`, which reorders every `updated_at`-sorted list including today's Activity
  spine. PRD-05 moves Activity off that spine first, so the reorder lands once.

---

## Parallelisation plan

**Safe to run concurrently (disjoint file sets):**

| Batch | PRDs         | Why safe                                                                                                                                                                 |
| ----- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 0     | 01 ‖ 05 ‖ 06 | 01 = `design-system` only; 05 = ai-backend/facade/`api-types/index.ts`; 06 = backend/ai-backend-mcp/`api-types/connectors.ts`. Migration ids pre-assigned (C18).         |
| 1     | 02 ‖ 03      | 02 = `styles.css` + `StatusPill`/`statusTone` + 6 destination call sites; 03 = `contract/`, `shell/ChatShell`, `projections/chats`, both host binders.                   |
| 3b    | 11 ‖ 12      | 11 = `destinations/connectors/*` + `design-system` `AppIcon`; 12 = `shell/AppRail`+`ChatShell` + ai-backend `active_count`. Only shared file is `src/index.ts` (barrel). |

**Must be serialised — hot files with 2+ claimants:**

| File                                                              | Claimants                      | Owner / order                                                                 |
| ----------------------------------------------------------------- | ------------------------------ | ----------------------------------------------------------------------------- |
| `apps/desktop/renderer/destinationBinders.tsx`                    | 03, 04, 06, 07, 08, 09, 10, 11 | **Hottest file in the program.** Strict wave order; one merge owner per wave. |
| `packages/design-system/src/styles.css`                           | 01, 02, 08, 10, 11             | 01 → 02 → 08 → 11 → 10                                                        |
| `packages/chat-surface/src/destinations/_shared/Row.tsx`          | 04, 08, 09, 11                 | **08 owns**; 09 then 11 stack; 04 drops its line                              |
| `packages/chat-surface/src/shell/ChatShell.tsx`                   | 03, 09, 12                     | 03 → 09 (owns the split) → 12                                                 |
| `packages/chat-surface/src/shell/AppRail.tsx`                     | 01, 03, 12                     | 01 → 03 → **12 owns**                                                         |
| `.../destinations/connectors/ConnectorsDestination.tsx`           | 03, 06, 11                     | 06 → 11; 03 drops connectors (C6)                                             |
| `.../destinations/activity/ActivityDestination.tsx`               | 02, 04, 08                     | 02 → 04 → 08                                                                  |
| `.../destinations/projects/ProjectsDestination.tsx`               | 02, 03, 07, 10                 | 02 → 03 → 07 → **10 owns**                                                    |
| `.../destinations/projects/ProjectDetailView.tsx`                 | 07, 10                         | 07 → **10 owns** the markup (C16)                                             |
| `.../destinations/chats/ChatsArchive.tsx`                         | 02, 07, 09                     | 02 → **09 owns**; 07 drops the extraction (C16)                               |
| `packages/api-types/src/index.ts`                                 | 05, 07, 09, 12                 | 05 → 07 → 09 → 12                                                             |
| `packages/api-types/src/activity.ts`                              | 04, 05, 08                     | 04 → 05 → 08                                                                  |
| `services/ai-backend/.../conversation_query_service.py`           | 05, 07, 08, 09, 12             | 05 → 07 → 08 → 09 → 12                                                        |
| `services/ai-backend/src/runtime_api/http/routes.py`              | 05, 07, 09, 12                 | same; **register literal paths before `/{run_id}`**                           |
| `services/ai-backend/src/runtime_adapters/*/runtime_api_store.py` | 05, 07, 08, 09, 12             | same; conformance suite is the guard                                          |
| `services/backend-facade/src/backend_facade/app.py`               | 05, 07, 09, 12                 | same                                                                          |

**Migration ids — pre-assigned (C18).** On disk today: `services/backend` highest is
`0045`, `services/ai-backend` has only `0001`. `MANIFEST.lock` is checksum-guarded by
`tools/check_migration_manifest.py`, so two PRDs cannot both claim the same id.

| Service      | Id     | PRD | File                                    |
| ------------ | ------ | --- | --------------------------------------- |
| `backend`    | `0046` | 06  | `0046_connector_access_mode.sql`        |
| `backend`    | `0047` | 07  | `0047_drop_project_activity_counts.sql` |
| `ai-backend` | `0002` | 05  | `0002_run_history_index.sql`            |
| `ai-backend` | `0003` | 07  | `0003_conversation_project.sql`         |
| `ai-backend` | `0004` | 09  | `0004_conversation_keyset.sql`          |

PRD-03's `0046_connector_access_mode.sql` is **deleted** (C3). PRD-08 and PRD-12 correctly
require no migration.

---

## Conflict register (normative)

Apply each correction to the PRD text before implementing.

| #   | Conflict                                                                                                                                                                                                                                                                                                                                        | Decision                                                                                                                                                                                           |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| C1  | **`railBadges` + `useActiveRunCount`.** PRD-03 Move 1 deletes the prop and moves the hook (sourced from the conversation list). PRD-12 D1 deletes the prop and creates the hook (sourced from `GET /v1/agent/runs/active_count`). PRD-12's Dependencies claim PRD-03 lands first "with today's prop shapes" — false, PRD-03 already deleted it. | **PRD-12 owns.** PRD-03 removes `railBadges`, `src/shell/useActiveRunCount.ts`, DoD 1's `railBadges` clause, DoD 8's badge assertion and the polling risk row.                                     |
| C2  | **`railIdentity` shape.** PRD-03 → `{initial} \| null`. PRD-12 D5 → `{displayName}` with the glyph derived in-package, no `.toUpperCase()`.                                                                                                                                                                                                     | **PRD-12's shape.** PRD-03 binds `{displayName: string} \| null` directly so the prop changes once.                                                                                                |
| C3  | **Connector access-mode backend.** PRD-03 and PRD-06 both specify the same route, column, migration `0046`, authz rule and facade proxy. PRD-03 names run-time enforcement a non-goal; PRD-06 lands it whole.                                                                                                                                   | **PRD-06 owns everything** (column, route, port, all three enforcement gates). PRD-03 deletes its `services/backend`, `services/backend-facade` sections and DoD 13–15.                            |
| C4  | **`access_mode ?? "off"`.** PRD-06 DoD 13 requires zero matches. PRD-11 Non-goals say it "keeps the existing `?? \"off\"` least-privilege default." Same line, `ConnectorsDestination.tsx:338`.                                                                                                                                                 | **PRD-06 deletes it.** Strike the clause from PRD-11's Non-goals.                                                                                                                                  |
| C5  | **`ConnectModal` mount point.** PRD-03 folds it into `ConnectorsDestination` (DoD 4: zero `ConnectModal` refs under `apps/`). PRD-11 D4 has **both hosts** mount it over a lifted `useConnectFlow` + injected `authorize()`.                                                                                                                    | **PRD-11 owns.** Desktop's renderer is denied `window.open`, so authorization is a genuine host capability the destination cannot own. PRD-03 drops the fold and DoD 4.                            |
| C6  | **`ConnectorsDestination` props, three-way.** PRD-03 (required `connect` union + nullable `onSetAccessMode`), PRD-06 (`accessPort`, delete `onSetAccessMode`), PRD-11 (delete `filter`/`counts`/`onOpenCatalogEntry`).                                                                                                                          | PRD-03 declares `ConnectorsDestination` **out of scope** and applies binding-totality to the shell + projects only. Order for the file: **06 → 11**.                                               |
| C7  | **Activity projection home, three-way.** PRD-03 → `src/projections/activity.ts`; PRD-04 → `destinations/activity/activityProjection.ts`; PRD-08 references a non-existent "PRD-06 (shared Activity projection)" ×4.                                                                                                                             | **PRD-04 owns**, at `destinations/activity/activityProjection.ts` (matches the in-tree `destinations/run/chatProjection.ts` precedent). PRD-03 drops it; PRD-08 retargets every "PRD-06" → PRD-04. |
| C8  | **Chats projection / bucketing.** PRD-03 → `src/projections/chats.ts` incl. `bucketConversations`, keeps `chatsApi.ts` as a fetch layer. PRD-09 D1 moves bucketing into the SQL query and **deletes** `chatsApi.ts`.                                                                                                                            | **PRD-03 ships only the per-row `toChatArchiveRow`.** Bucketing/fetch/paging/tail is PRD-09's. PRD-03 must not ship a shared `bucketConversations` that PRD-09 deletes two waves later.            |
| C9  | **`_shared/Row.tsx`, four-way.** PRD-04 (title weight 500), PRD-08 (`trailing` + 16px reserve, `iconTone`, tile background, `.ui-list-row`), PRD-09 (overflow `⋯` menu slot), PRD-11 (`subFont`, `iconSize`).                                                                                                                                   | **PRD-08 owns the file.** PRD-04 drops its `Row.tsx` line (its Dependencies already offer this) and PRD-08 absorbs the weight change. PRD-09 then PRD-11 stack their props on top.                 |
| C10 | **Icon-tile token.** PRD-08 proves `--color-surface-muted` `#16161a` = design `--panel2` = the _hover_ colour, and `--color-surface-elevated` `#1d1d23` = `--panel3` = the tile. Verified: `styles.css:171,201`. PRD-10 D3 specifies `--color-surface-muted` and calls it "the design's `--panel3` rung".                                       | **PRD-08's mapping is correct.** PRD-10 D3's neutral branch changes to `var(--color-surface-elevated)`; fix the comment too. PRD-11's token table is already right.                                |
| C11 | **10.5px mono token name.** PRD-01 mints `--font-size-mono-10-5`; PRD-02 mints `--font-size-mono-105`. Same value, two names, both repoint `.ui-badge`.                                                                                                                                                                                         | **PRD-01's name (`--font-size-mono-10-5`)**, consistent with its `mono-8-5`/`mono-9-5` ladder. PRD-02 drops the token addition and consumes PRD-01's.                                              |
| C12 | **`.ui-badge` / `statusTone.ts` ownership.** PRD-01 retargets `.ui-badge`'s size + weight; PRD-02 makes it chip-exact; PRD-08 D2 flips `needs_input` → `warning` in `statusTone.ts` which PRD-02 rewrites wholesale.                                                                                                                            | Not contradictory. **Sequence 01 → 02 → 08** and re-run the chats + activity harnesses only after 08.                                                                                              |
| C13 | **`SectionHeader` recipe element.** PRD-01 migrates the component onto `.ui-mono-caps`. PRD-13's new finding: the `sect-h` class sits on the **wrapper `<div>`** that also holds the count pill and the New-chat button, so a real recipe there would mono-uppercase the CTA.                                                                   | Both are right. **PRD-01 must apply `.ui-mono-caps` to the label element, not the wrapper** — record that in PRD-01's Scope. PRD-13 then deletes the vestigial class from the wrapper.             |
| C14 | **Topbar suppression set.** PRD-09 D5 and PRD-12 D3 both define `SUPPRESS_TOPBAR = {"run"} ∪ settingsActive` in `ChatShell.tsx:36-46,236-237`.                                                                                                                                                                                                  | **PRD-09 owns the split** (it needs it for Chats). PRD-12 keeps only "web passes `settingsActive`" + threading it to the rail.                                                                     |
| C15 | **Topbar subtitle.** Activity `AUDIT.md` HIGH-4 ("the per-destination subtitle is structurally unreachable") is a PRD-08 non-goal attributed to a nameless "shell registry" PRD. PRD-09 D5 actually closes it via `DestinationMeta.sublabel` for all six slugs.                                                                                 | Not a conflict — a missing cross-reference. **PRD-08's non-goal must name PRD-09.**                                                                                                                |
| C16 | **Project detail chat rows.** PRD-07 extracts `destinations/chats/ChatsSection.tsx` out of `ChatsArchive.tsx:351-403` and mounts it in `ProjectDetailView`. PRD-10 D6 renders `SectionHeader` + `RowList` + `Row` directly there.                                                                                                               | **PRD-10 owns the markup.** PRD-07 supplies the data and drops the extraction — which also deletes PRD-07's risk row about regressing `ChatsArchive.test.tsx`, a file PRD-09 is rewriting anyway.  |
| C17 | **`ChatsDestination` deletion.** PRD-09 Scope deletes it; PRD-13 §1 deletes it. Both DoD-grep for zero references.                                                                                                                                                                                                                              | **PRD-13 owns** (it also owns the barrel and the guard that keeps it deleted). PRD-09 drops the deletion from Scope and DoD 14.                                                                    |
| C18 | **Migration id collisions.** `backend` `0046` claimed by PRD-03 + PRD-06 + PRD-07; `ai-backend` `0002` claimed by PRD-05 + PRD-07 + PRD-09.                                                                                                                                                                                                     | Reassigned in the table above. Re-run `tools/check_migration_manifest.py --write` in the same commit as each migration.                                                                            |
| C19 | **`updated_at` semantics.** PRD-09 D4 bumps the conversation's `updated_at` in `update_run_status`, reordering every `updated_at`-sorted list — including today's Activity spine.                                                                                                                                                               | Ordering constraint, not a contradiction: **PRD-05 must land first** so Activity is already off that spine and the reorder lands once.                                                             |
| C20 | **PRD-13 DoD 12** requires the chats parity report's counts to be byte-identical to `17/59/64/10`, while its Dependencies claim independence from PRD-01/02/09 — all three of which change those counts.                                                                                                                                        | PRD-13 lands **last**; rewrite DoD 12 as a delta against the report regenerated on PRD-13's own merge base (see DoD-Q10).                                                                          |
| C21 | **Stale PRD numbering.** PRD-05 calls the shared Activity projection "PRD-06" and the meta composite "PRD-07"; PRD-07 calls the Projects visual PRD "PRD-05"; PRD-08 says "PRD-06" ×4 for the projector and "PRD-09 names it PRD-02".                                                                                                           | Mechanical fix, but load-bearing: an implementer following PRD-05's Dependencies opens **connector access mode**. Correct in place: PRD-05 → 04/08; PRD-07 → 10; PRD-08 → 04.                      |

Wrong or unsupportable declared dependencies, beyond the above:

- **PRD-06** "Must land first: none — touches no file the sibling PRDs own." False: it
  rewrites `ConnectorsDestination.tsx` (PRD-03, PRD-11) and its migration id collided.
- **PRD-03** "Must land first: none." True only after C3/C5/C6 shrink its scope.
- **PRD-10** "If PRD-01 introduces `--space-grid-gap`, use theirs." PRD-01 does not; PRD-10
  owns that token.
- **PRD-10** records the tile-colour divergence as an `expected-divergence` note; the key
  the comparator actually reads is **`expectDivergence`** (`lib/compare.mjs:172`).

---

## Gaps — audit findings no PRD owns

| #   | Finding                                                                                                                                                                                                                                                                                                                                                        | Assign to                                                                                                                               |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| G1  | **Chats RC-4 (HIGH)** — `modelMonoStyle` (`ChatsArchive.tsx:426-430`) sets `--color-text-muted` `#98989f`; the design's `.mono` changes family only so the model tag stays `--mut2` `#64646d`. One HIGH + a derivative `borderColor`. PRD-09 defers it to "PRD-02 and siblings"; PRD-02 is chip-only.                                                          | **PRD-09**                                                                                                                              |
| G2  | **Chats RC-11 / Activity** — row glyphs render 18px; the design forces `.lrow__ic svg {width:15px;height:15px}` (`copilot.css:290`). `Row.tsx:70-79` sizes the slot, not the svg. PRD-08 sizes only the trailing chevron.                                                                                                                                      | **PRD-08** (D5)                                                                                                                         |
| G3  | **`--color-bg` `#09090b` vs the design's `#050506`** (projects RC-11, cross-surface). PRD-10 says PRD-01 owns it; PRD-01's Scope and Non-goals never mention it.                                                                                                                                                                                               | **PRD-01**                                                                                                                              |
| G4  | **Activity MEDIUM-8** — the lead paragraph lost two-thirds of its copy and the retention link swallowed a whole sentence (`ActivityDestination.tsx:59, :65-66`; `page.lead.link` width `auto → 321.97px`). PRD-08 retunes only the _empty-state_ copy.                                                                                                         | **PRD-08**                                                                                                                              |
| G5  | **Activity MEDIUM-3** — page and row padding undershoot on every axis. No PRD sets `.lrow` padding to `11px 14px`, and PRD-10 scopes Activity's `_shared/Page` migration out.                                                                                                                                                                                  | **PRD-08** (row padding) + name Activity as `Page`'s second consumer                                                                    |
| G6  | **Chats RC-10** — `ChatsArchive.tsx:150` centres the 960 column (`margin: 0 auto`); the design's `.pg` has no auto margin. PRD-09 calls it a non-goal _and_ PRD-10's new `_shared/Page` hard-codes `margin: 0 auto`, institutionalising the divergence in a shared primitive.                                                                                  | **Decide in PRD-10** before `Page` ships; if centring is intended, record `expectDivergence` on `page.container` for chats and projects |
| G7  | **rail-badge A4 — desktop appearance persistence.** `splitAppearancePersistence` is exported and unit-tested with **zero host call sites**, so desktop resets accent to `sky` every launch. PRD-01 and PRD-12 both list it as someone else's PRD; **no such PRD exists.** Without it, PRD-01's headline fix (nine working accents) is unobservable on desktop. | **New PRD-14**, or fold into PRD-12                                                                                                     |
| G8  | **Activity MEDIUM-9** — `ChatShell.tsx:318-323` mounts `<RightRail>` without `activity`, so `ActivityTabContent` renders empty on every non-full-bleed destination; `ActivityList`'s only consumer is `HomeDestination`, which has no rail slot. PRD-13's guard scope (`destinations/` component modules) does not reach `shell/`.                             | **PRD-13** (scope extension or an explicit `owner=` waiver)                                                                             |
| G9  | **Chats RC-9** — `.ui-button--sm` sets a weight and `.ui-button--primary` does not, so the CTA computes 500 where the design is 600. PRD-11 adopts `.ui-button` for the Tools CTA and would inherit the same defect.                                                                                                                                           | **PRD-01** (one line in `styles.css`)                                                                                                   |
| G10 | **Tools `report-connect.md:94-97`** — modal-shell geometry (`padding: 22px`, `display: grid`, `z-index: 60`, `position: absolute`). PRD-01 calls it "Modal-shape, not token"; PRD-11 covers the modal's logo/rows/pinned hatch but not the container.                                                                                                          | **PRD-11**                                                                                                                              |
| G11 | **Projects R9/R10** — the app-wide accent-link policy and inline-`CSSProperties` interactive chrome. PRD-10 defers `ItemLink`'s colour to "the refs PRD"; PRD-04 removes `linkStyle`'s overrides only on the Activity path. No PRD states the policy once.                                                                                                     | **PRD-04** (it owns `ItemLink.tsx`)                                                                                                     |
| G12 | **rail-badge A10 — comparator taxonomy noise** (grid-vs-flex centring, UA button padding, `50%` ≡ `999px`). Inflates every surface's MEDIUM count.                                                                                                                                                                                                             | Harness chore in `tools/design-parity/lib/compare.mjs` — backlog, not a product PRD                                                     |

---

## Definition-of-Done items that are not mechanically verifiable

Each of these must be rewritten in its PRD before implementation.

| #       | PRD / item | As written                                                                                                                                                | Rewrite as                                                                                                                                                                                                                                                                    |
| ------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| DoD-Q1  | 03 / 5     | "`typecheck --workspace @0x-copilot/desktop` **fails** when `railIdentity` is removed … (Verify by temporarily deleting the line)"                        | A committed type test: `apps/desktop/renderer/bindingContract.test-d.ts` contains `// @ts-expect-error missing railIdentity` over a `ShellHostBinding` literal omitting the field, and `npm run typecheck --workspace @0x-copilot/desktop` exits 0.                           |
| DoD-Q2  | 04 / 14    | "…pass, with the sole permitted exception of failures recorded as pre-existing in the same PR description (capture the baseline on `origin/main` first)." | "`npm run test --workspace <w>` for chat-surface / api-types / frontend / desktop exit 0, **or** the failing test ids are byte-identical to `docs/plan/design-parity-remediation/baseline-failures.txt`, which this PR does not modify."                                      |
| DoD-Q3  | 02 / 11    | "`npx vitest run --root packages/design-system` passes (**or**, if the package has no test root configured, typecheck passes …)"                          | Drop the disjunction: `packages/design-system/package.json` has only a `typecheck` script (verified), so state `npm run typecheck --workspace @0x-copilot/design-system` exits 0 and item 10 covers rendering.                                                                |
| DoD-Q4  | 02 / 16    | "`surfaces/first-run/out/` regenerates with no **new** HIGH rows versus its committed baseline"                                                           | Name the artefact and the diff: "`git diff --exit-code -- tools/design-parity/surfaces/first-run/out/report.md` shows no line added under the `## HIGH` heading."                                                                                                             |
| DoD-Q5  | 05 / 16    | "for every member of `ACTIVITY_RUN_STATUSES` source enum coverage, each of the eight `AgentRunStatus` values maps to a member …" (garbled)                | "A test iterates the eight `AgentRunStatus` members and asserts `mapRunStatus(s)` is a member of `ACTIVITY_RUN_STATUSES` for every one, with no `undefined` result."                                                                                                          |
| DoD-Q6  | 06 / 17    | "…the report shows 0 HIGH rows for anchor group 'Permission control' **attributable to** a missing/incorrect `data-value`"                                | Split it: (a) `surfaces/tools/out/report-default.json` contains no row whose `anchor` starts `default.seg` and whose `property` is `data-value`; (b) the remaining HIGH rows in that group are exactly the three background rows listed here, by anchor id.                   |
| DoD-Q7  | 08 / 19    | "A test asserts the Retry control is present in the `ready` state (not only in `error`)."                                                                 | Name the file and selector: "`ActivityDestination.test.tsx` asserts `[data-testid=\"activity-retry\"]` is in the document for `status:\"ok\"`."                                                                                                                               |
| DoD-Q8  | 09 / 17    | "**Manual acceptance, both hosts:** with a run in flight, opening Chats and waiting shows the chip change …"                                              | Automate against the fake `Transport` (DoD 8c already does half of it): assert the SSE-driven chip flip, the ⋯→Archive→Unarchive round trip, and a `loadMore("archived")` append, in `useChatsArchive.test.tsx`. Keep the manual pass as a release-checklist line, not a DoD. |
| DoD-Q9  | 10 / 15    | "…produces reports whose **HIGH sections are identical modulo the state name**"                                                                           | Make it a command: "`diff <(sed -n '/^## HIGH/,/^## /p' report-default.md                                                                                                                                                                                                     | sed 's/default/STATE/g') <(… report-default-chatsurface.md …)` exits 0." |
| DoD-Q10 | 13 / 12    | "…counts byte-identical to the pre-change report (17/59/64/10)"                                                                                           | "Regenerating the chats report on this PR's merge base and on this PR produces byte-identical HIGH/MEDIUM/LOW/INFO counts" — i.e. a delta against _this PR's_ base, not against a number frozen before PRD-01/02/09.                                                          |
| DoD-Q11 | 05 / 17    | "…**On `main`** the equivalent conversation-list path returns at most 1."                                                                                 | Prose, not a check. Keep it as a note under the DoD item; the checkable half is the 8-entry / 3-date assertion.                                                                                                                                                               |
| DoD-Q12 | 11 / 13    | "0 HIGH rows for the groups Section header, List, Row and Permission control — **down from 15 HIGH total today**"                                         | The "15 today" clause is only true pre-PRD-06/PRD-01. State the absolute target only, and cite the baseline report path + git sha instead of a number.                                                                                                                        |

---

## How to verify a PRD is done

Every PRD's DoD is written to be run from the repo root. The parity half of it always
goes through the harness in `tools/design-parity/` — full procedure in
`tools/design-parity/SKILL.md`.

**1. Re-render the live side** (real components, real `design-system/src/styles.css`,
fake ports, `renderToStaticMarkup`):

```bash
node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs
```

`vitest.config.mjs` includes `lib/render-live*.test.tsx` by glob — add a new harness file
matching that name and **do not edit the config**; it is a merge point for every PRD in
flight.

**2. Extract computed styles for both sides**, then **3. compare**:

```bash
node tools/design-parity/lib/extract-playwright.mjs …        # design + live
node tools/design-parity/lib/compare.mjs \
  surfaces/<surface>/out/design-<state>.json \
  surfaces/<surface>/out/live-<state>.json \
  --anchors surfaces/<surface>/anchors.json \
  --out surfaces/<surface>/out/report-<state>.md \
  --state <state>
```

**4. Read the report as the gate.** Severity bands come from `lib/compare.mjs:89-110`:
colours are compared as exact serialized strings (any mismatch is HIGH); `fontSize` deltas
≥ 0.4px are flagged and ≥ 2px are HIGH. A row you have decided to diverge on is recorded
in `anchors.json` under **`expectDivergence`** (read at `lib/compare.mjs:172`) with the
reason — it then reports as INFO instead of re-raising forever. `note` on the live side
does the same thing.

**5. The three non-negotiables for every PRD in this program:**

- **A regression guard that fails on `main`.** Each PRD's DoD names one — the test that
  reproduces the exact defect. If it passes before the change, it is not the guard.
- **At least one design value pinned numerically**, cited to a `copilot.css` /
  `copilot-app.jsx` line. Not "matches the design" — the literal number.
- **Every claim carries `path/file.ts:LINE`.** If the code disagrees with the audit, the
  code wins and the PRD says so in its Evidence table (each PRD already has DISPUTED rows;
  keep them).

**6. Baselines.** The committed reports under `tools/design-parity/surfaces/*/out/` are the
graded artefact. Regenerate them in the same commit as the change, so the diff shows the
parity movement the PRD claims.

---

## Program-level risks

- **`apps/desktop/renderer/destinationBinders.tsx` is edited by eight PRDs.** It is the
  single most likely source of a lost change. Assign one merge owner per wave.
- **Six PRDs touch ai-backend's three store adapters.**
  `tests/unit/runtime_adapters/test_store_conformance.py` is the only mechanism keeping
  them in sync; every PRD that adds a port method must add a case to it.
- **Three PRDs register new literal routes under `/v1/agent/runs`.** FastAPI matches in
  registration order and `run_id` is an unconstrained `str`, so `active_count` (PRD-12)
  and the `GET /runs` collection (PRD-05) must both be registered **before**
  `GET /runs/{run_id}` — in ai-backend _and_ in the facade.
- **PRD-06 ships a permission boundary.** It is the only PRD in the program whose failure
  mode is a security one; it must not be split into "store the value now, enforce later"
  (that split is the defect PRD-06 exists to fix).
