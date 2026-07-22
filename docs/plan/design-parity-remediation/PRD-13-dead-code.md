# PRD-13 — Dead code removal: barrel-exported components mounted by nobody

## Problem

Nothing a user clicks is broken by this PRD's subject matter — and that is exactly the
problem. `packages/chat-surface` advertises, through its public barrel, three things
that do not exist as product:

1. A **Chats sidebar** (a project tree with search, 498 lines) that no host has ever
   mounted, and whose only data call — `GET /v1/chats/projects` — is served by **no
   service in this repo**. If anyone ever mounted it, the user would see
   "Loading chats…" forever, then a transport error. It is a live landmine, not
   merely unused code.
2. A **`ChatsDestination`** wrapper exported from both barrels that neither host
   mounts. Both hosts mount `ChatsArchive` directly. Every future reader — human or
   agent — who looks for "the Chats destination" finds the wrong file first. The
   design-parity harness hit this and had to leave a comment explaining why it
   deliberately rendered a different component
   (`tools/design-parity/lib/render-live-chats.test.tsx:15-17`).
3. Three design class names (`.pg-lead`, `.sect-h`, `.rowlist`) stamped onto live DOM
   nodes with **zero CSS rules behind them** anywhere in the shipped app. They imply a
   styling contract that does not exist; the surface is actually 100% inline
   `CSSProperties`. Two unit tests assert on these dead class names, so the illusion is
   load-bearing on paper and load-bearing on nothing in practice.

A prior repo-wide audit put dead code at ~95k LOC. Deleting these 993 lines is the
small half of this PRD. The large half is the **CI gate that makes the next one
impossible to merge silently**.

## Evidence

Every row opened and verified by me on `claude/design-parity-audit-7ec82a`.

| Claim                                                                        | File:line                                                                                                                                                | What the code actually does                                                                                                                                                                                                                                                                                                                                      |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ChatsSidebar` exists and is exported                                        | `packages/chat-surface/src/destinations/chats/ChatsSidebar.tsx:191`; `destinations/chats/index.ts:5`; `src/index.ts:487-488`                             | CONFIRMED. 498 lines. Exported from the package barrel as `ChatsSidebar` + `ChatsSidebarProps`.                                                                                                                                                                                                                                                                  |
| `ChatsSidebar` fetches a route no service serves                             | `ChatsSidebar.tsx:218-223`                                                                                                                               | CONFIRMED. `transport.request({method:"GET", path:"/v1/chats/projects"})`. `grep -rn "chats" services/backend-facade/src services/backend/src services/ai-backend/src --include="*.py"` returns only unrelated `chats_with_grant` / `chats:` count fields. No route, no proxy passthrough.                                                                       |
| The stub was formally retired                                                | `packages/api-types/src/chats.ts:13`                                                                                                                     | CONFIRMED. Comment: the archive binds to `/v1/agent/conversations` "until a dedicated bucketed endpoint exists (PRD §11 — `/v1/chats/projects` stub retired)". The consumer was never deleted with it.                                                                                                                                                           |
| `ChatsSidebar` is mounted nowhere                                            | repo-wide grep                                                                                                                                           | CONFIRMED. Only hits: its own file, `ChatsSidebar.test.tsx` (373 lines), the two barrels, and a prose comment at `ChatsDestination.tsx:16`. Zero hits in `apps/frontend/src`, `apps/desktop/renderer`, or anywhere in `chat-surface/src` outside the barrel.                                                                                                     |
| The comment claims the sidebar is kept "for Run's own thread rail if needed" | `ChatsDestination.tsx:16-18`                                                                                                                             | CONFIRMED as text, FALSIFIED as fact. The Run cockpit does not reference `ChatsSidebar`. "If needed" never became needed; that is the whole failure mode.                                                                                                                                                                                                        |
| `ChatsDestination.tsx` is 48 lines and forwards to `ChatsArchive`            | `ChatsDestination.tsx:28-48`                                                                                                                             | CONFIRMED. A `Partial<ChatsArchiveProps>` defaulting shim: `archive=null`, `onReopen`/`onNewChat` = `noop`. It adds nothing but default props.                                                                                                                                                                                                                   |
| `ChatsDestination` is exported from BOTH barrels                             | `destinations/chats/index.ts:1-4`; `src/index.ts:486` (value) and `src/index.ts:1141` (`type ChatsDestinationProps`)                                     | CONFIRMED — and the type is re-exported from a _second, later_ barrel block, so it is published twice.                                                                                                                                                                                                                                                           |
| Neither host mounts `ChatsDestination`                                       | `apps/frontend/src/app/App.tsx:1054`; `apps/frontend/src/features/chats/ChatsArchiveRoute.tsx:36`; `apps/desktop/renderer/destinationBinders.tsx:31,223` | CONFIRMED. Web mounts `<ChatsArchiveRoute>` → `<ChatsArchive>`; desktop mounts `<ChatsArchive>` at `:223`. Zero references to `ChatsDestination` under `apps/`.                                                                                                                                                                                                  |
| Three design class names are emitted with no CSS behind them                 | `_shared/PageLead.tsx:37`, `_shared/SectionHeader.tsx:64`, `_shared/RowList.tsx:56`                                                                      | CONFIRMED **with a line correction**: the audit cited `PageLead.tsx:36`; the `className=` line is **37**. `grep -rn "pg-lead\|sect-h\|rowlist" --include="*.css"` over all 13 shipped stylesheets returns matches **only** inside `tools/design-parity/design-kit/` (the vendored mock, never loaded by the app).                                                |
| `.sect-h` is on the wrong element anyway                                     | `SectionHeader.tsx:63-64` vs design `copilot-app.jsx:300`                                                                                                | NEW FINDING (not in the audit). Live puts `sect-h` on the **wrapper `<div>`** that also holds the count pill and the action button; the design puts `.sect-h` on the **label element itself**, inside a bespoke flex wrapper. A real `.sect-h` rule would therefore mono-uppercase the New-chat button. This kills the "promote it to a recipe" option outright. |
| Tests assert on the dead class names                                         | `_shared/PageLead.test.tsx:13,36`; `_shared/SectionHeader.test.tsx:18`                                                                                   | CONFIRMED. `expect(el).toHaveClass("pg-lead")` ×2, `expect(...).toHaveClass("sect-h")` ×1. `RowList.test.tsx` has no such assertion.                                                                                                                                                                                                                             |
| The parity harness does **not** depend on these class names                  | `tools/design-parity/surfaces/*/anchors.json`                                                                                                            | CONFIRMED. No anchor selector contains `pg-lead`/`sect-h`/`rowlist`; the live side is entirely `data-testid`-driven (stated at `surfaces/chats/out/FINDINGS.md` RC-12). Deleting the classes cannot move a parity number.                                                                                                                                        |
| No call site passes `className` to these three                               | `ChatsArchive.tsx:300,367,387`; `ActivityDestination.tsx:315,460`; `ProjectDetailView.tsx:960,973`                                                       | CONFIRMED. All seven call sites pass children/`data-testid`/`count`/`action` only. The bespoke `className === undefined ? "x" : \`x ${className}\`` merge at all three sites exists **solely** to prepend the dead class.                                                                                                                                        |
| "Ten unmounted Projects components"                                          | `packages/chat-surface/src/destinations/projects/`                                                                                                       | **DISPUTED (count).** My count is **7 of 12** with zero host references: `ProjectEditor`, `ProjectFilterChip`, `ProjectsPanel`, `TemplateEditor`, `ArchiveBlockedDialog`, `ForkFromTemplateDialog`, `TransferOwnershipDialog`. Not ten. Disposition is deferred to PRD-10 either way — see Non-goals.                                                            |
| `ChatsDestination` is not the only orphaned `*Destination` export            | `src/index.ts` (17 `*Destination` value exports)                                                                                                         | **NEW FINDING.** Three have zero host references: `ChatsDestination`, `WebhooksDestination`, `MemoryDestination`. The guard this PRD adds will fail on all three on day one; the PRD must (and does) dispose of all three. See Architectural decision §3.                                                                                                        |
| Cost of the deletion                                                         | `wc -l`                                                                                                                                                  | `ChatsSidebar.tsx` 498 + `ChatsSidebar.test.tsx` 373 + `ChatsDestination.tsx` 48 + `ChatsDestination.test.tsx` 74 = **993 lines**.                                                                                                                                                                                                                               |

## Design intent

The mock is unambiguous on both counts.

**There is no Chats sidebar.** `ChatsSurface` (`design-kit/app-v3/copilot-app.jsx:287-330`)
renders exactly: one `.pg-lead` paragraph, a flex header row containing a `.sect-h`
"Pinned" label plus one `cbtn cbtn--pri cbtn--sm` "New chat" button, then three
`.rowlist` cards (Pinned / Recent / `Archived · history`). No project tree, no panel, no
search input, no fullscreen toggle. The only search affordance on this destination is
the topbar ⌘K button (`copilot-app.jsx:66`) — which is a separate defect owned by PRD-09.
`ChatsSidebar`'s entire information architecture (projects → threads) was superseded by
the Projects destination.

**The design's styling contract is CSS classes; ours is tokens + `.ui-*` recipes.**
`design-kit/app-v3/copilot.css:1552-1580`:

```css
.pg {
  padding: 20px 24px 40px;
  max-width: 960px;
}
.pg-lead {
  font-size: 12px;
  color: var(--mut);
  margin: -2px 0 18px;
  max-width: 72ch;
  line-height: 1.6;
}
.sect-h {
  font-family: var(--mono);
  font-size: 9.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--mut2);
  margin: 22px 0 10px;
}
.sect-h:first-child {
  margin-top: 0;
}
.rowlist {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line);
  border-radius: var(--r);
  overflow: hidden;
  background: var(--panel);
}
```

Those **values** are the parity target and they are owned elsewhere: PRD-01 mints the
`9.5px` mono rung (`--font-size-mono-9-5`) and repoints the existing `.ui-mono-caps`
recipe (`packages/design-system/src/styles.css:1097-1104`) at it; PRD-09 owns the Chats
surface's spacing and 12px lead. Those **selector names** are not the target — the
shipped design system deliberately namespaces every recipe `.ui-*`. This PRD's design
intent is therefore narrow and negative: **the app must not carry a second, unstyled,
un-namespaced copy of the mock's class vocabulary.**

## Architectural decision

Three decisions. Each names the seam.

### 1. Delete `ChatsSidebar` + `ChatsDestination` outright (not deprecate, not flag)

The seam that changes is the **package barrel** (`packages/chat-surface/src/index.ts`),
which is the package's only sanctioned contract with hosts. Both components leave the
tree together with their tests and both barrel entries.

- Rejected: _keep `ChatsSidebar`, add the missing `/v1/chats/projects` route._ The
  archive read model already superseded it (`packages/api-types/src/chats.ts:13`
  records the stub as retired). Building a backend endpoint to feed a component the
  design does not contain is the definition of a bandaid.
- Rejected: `@deprecated` JSDoc. A deprecation marker is a note; the guard in §3 is an
  enforcement. Notes are what produced 993 dead lines here and ~95k repo-wide — see
  the comment at `ChatsDestination.tsx:16-18` that kept the sidebar alive on a
  hypothetical.
- Rejected: _keep `ChatsDestination` as the "official" destination and rewire the
  hosts to it._ It contributes only default props. Two host binders already supply
  every prop it defaults. Preserving the wrapper trades 48 dead lines for one extra
  indirection on the flagship archive path.
- No contract change: `ChatsArchiveProps` is already the exported prop type
  (`src/index.ts:1134-1142`). `ChatsDestinationProps` was an alias of it, so removing
  the alias is source-compatible for both hosts (neither imports it).

### 2. Delete the three vestigial class names; do **not** promote them

The seam is `packages/chat-surface/src/destinations/_shared/{PageLead,SectionHeader,RowList}.tsx`.
Each drops its hardcoded class default and lets the inherited `className` from
`HTMLAttributes` flow through `{...rest}` (`RowList` keeps its explicit `className`
prop and simply forwards it).

Why delete rather than add rules to `packages/design-system/src/styles.css`:

- The design-system already owns this role under a different name. `.ui-mono-caps`
  (`styles.css:1097-1104`) **is** `.sect-h`; `.ui-badge` (`styles.css:555-568`) **is**
  `.chip`. Adding `.sect-h` would create a second vocabulary for the same role — the
  exact parallel-token anti-pattern the ui-kit consolidation (#219/#221) closed.
- The live markup is structurally incompatible. `.sect-h` sits on the wrapper `<div>`
  (`SectionHeader.tsx:64`) that contains the count pill and the action slot; the
  design's `.sect-h` is the label element (`copilot-app.jsx:300`). A faithful `.sect-h`
  rule would apply mono/uppercase/9.5px to the "New chat" button.
- The classes are unreferenced by every selector in the repo, including the parity
  harness. They cost a merge branch in three components and buy nothing.

The two tests that assert `toHaveClass` are rewritten to assert the thing that actually
governs rendering — the computed inline style contract — not a decorative attribute.

### 3. The recurrence guard: `tools/check_orphan_destinations.py`

**This is the deliverable that matters.** It follows the repo's existing guard pattern
exactly — six precedents (`tools/check_dark_capabilities.py`,
`check_route_scopes.py`, `check_reader_methods.py`, `check_audit_in_transaction.py`,
`check_llm_provider_imports.py`, `check_migration_manifest.py`), each with a
`tools/test_check_*.py` companion and a paths-filtered workflow.

**Rule.** For every PascalCase **value** export in `packages/chat-surface/src/index.ts`
whose source module lives under `src/destinations/` and whose defining module is a
`.tsx` file exporting `function <Name>(…): ReactElement`:

> the identifier must appear, as a whole word, in at least one non-test file under
> `apps/frontend/src/` or `apps/desktop/renderer/`, **or** in at least one non-test,
> non-`index.ts` file inside `packages/chat-surface/src/`.

**Waiver.** `// orphan-destination-waiver: owner=<PRD or issue> — <reason>` on the
export line in `src/index.ts`, mirroring
`# dark-capability-waiver:` (`check_dark_capabilities.py` docstring). The waiver lives
at the export site so it shows up in the diff that would otherwise hide the orphan.

**Why this scope and not "all unused exports".** I prototyped the broad rule (every
value export sourced from `destinations/`): it flags **49** identifiers, most of them
copy constants and hooks legitimately hoisted for host or test use. A 49-entry
allowlist rots into noise within a quarter. Narrowing to _component modules that render_
is the class where the audited defect actually lives, is decidable from the file's own
`export function … : ReactElement` signature, and produces a waiver list small enough
to read.

**Landing state (mandatory, honest).** The guard is red on the current tree. Disposition:

| Orphan                                                                                                                                                                                                                                                                                                  | Action                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ChatsDestination`, `ChatsSidebar`                                                                                                                                                                                                                                                                      | **Deleted** by §1.                                                                                                                                                                                                                                                                                                                                         |
| `WebhooksDestination`, `MemoryDestination` and the other legacy-IA component modules (`AgentsPanel`, `WebhookDetailView`, `TeamPanel`, `TeamInviteWizard`, `OffboardingWizard`, `PersonDetailView`, `MemoryPanel`, `MemoryDetailView`, `MemoryProposalToast*`, `RoutinesPanel`, `SaveToLibraryPopover`) | **Waived, in one contiguous block, with one shared reason**: folded IA surfaces (`destinationsForProfile` renders neither `memory` nor `webhooks` in `single_user_desktop` or `team` — `shell/destinations.ts`), disposition owned by the DEAD-1 audit. The implementer MUST verify each entry against the rule before waiving it; do not paste this list. |

The waiver count is then a number CI prints on every run — the ~95k-LOC problem gets a
visible, monotonically-shrinking counter instead of remaining invisible.

**Rejected alternatives for the guard.** `knip`/`ts-prune` — adds a dependency, reports
across the whole monorepo (thousands of hits), and is not steerable to the
"mounted by a host" question, which is the actual invariant. An ESLint rule — cannot
see across package boundaries into `apps/*`, and `packages/chat-surface/eslint.config.js`
**bans** importing `apps/*` (`no-restricted-imports`), so a lint-side check would have
to violate the boundary rule it exists to protect. A vitest assertion inside
`chat-surface` — same boundary violation. A repo-level stdlib script reading files (not
importing them) is the only form that respects the boundary while checking across it.

No API contract, migration, route, or `api-types` change is required by this PRD.

## Scope

**`packages/chat-surface`**

| File                                               | Reason                                                                                                                                                                                                                                                                        |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/destinations/chats/ChatsSidebar.tsx`          | DELETE — 498 lines; fetches `/v1/chats/projects`, served by no service.                                                                                                                                                                                                       |
| `src/destinations/chats/ChatsSidebar.test.tsx`     | DELETE — 373 lines testing a deleted component.                                                                                                                                                                                                                               |
| `src/destinations/chats/ChatsDestination.tsx`      | DELETE — 48-line defaulting shim over `ChatsArchive`, mounted by neither host.                                                                                                                                                                                                |
| `src/destinations/chats/ChatsDestination.test.tsx` | DELETE — 74 lines testing a deleted component.                                                                                                                                                                                                                                |
| `src/destinations/chats/index.ts`                  | Drop lines 1-5 (both `ChatsDestination` and `ChatsSidebar` re-exports); keep the `ChatsArchive` block.                                                                                                                                                                        |
| `src/index.ts`                                     | Remove `ChatsDestination`/`ChatsSidebar`/`ChatsSidebarProps` at `:486-488`; remove `type ChatsDestinationProps` at `:1141`; update the block comments at `:1133-1136` that describe the forwarding relationship; add the `orphan-destination-waiver` comments required by §3. |
| `src/destinations/_shared/PageLead.tsx`            | Drop the `"pg-lead"` class default at `:37`; stop destructuring `className` and let it ride `{...rest}`.                                                                                                                                                                      |
| `src/destinations/_shared/SectionHeader.tsx`       | Same at `:64` for `"sect-h"`.                                                                                                                                                                                                                                                 |
| `src/destinations/_shared/RowList.tsx`             | Same at `:56` for `"rowlist"`; keep the explicit `className` prop and forward it unchanged.                                                                                                                                                                                   |
| `src/destinations/_shared/index.ts`                | Update the header comment at `:2-3` — it advertises the `.pg-lead/.sect-h/.rowlist/.lrow` class vocabulary as the contract.                                                                                                                                                   |
| `src/destinations/_shared/PageLead.test.tsx`       | Replace the `toHaveClass("pg-lead")` assertions at `:13,36` with the inline-style contract + `className` passthrough.                                                                                                                                                         |
| `src/destinations/_shared/SectionHeader.test.tsx`  | Replace `toHaveClass("sect-h")` at `:18` likewise.                                                                                                                                                                                                                            |
| `src/destinations/_shared/RowList.test.tsx`        | Add the `className` passthrough assertion (no class assertion exists today to remove).                                                                                                                                                                                        |

**`tools/` (repo-level, no service deps)**

| File                                      | Reason                                                                                                                            |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `tools/check_orphan_destinations.py`      | NEW — the guard from §3. Pure stdlib, mirrors `check_dark_capabilities.py` structure and CLI.                                     |
| `tools/test_check_orphan_destinations.py` | NEW — unit tests: orphan detected, host-mounted passes, in-package-consumer passes, waiver honoured, real-tree baseline is green. |

**`.github/workflows/`**

| File                                           | Reason                                                                                                                                                                                                                                    |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.github/workflows/ci-orphan-destinations.yml` | NEW — paths-filtered on `packages/chat-surface/src/**`, `apps/frontend/src/**`, `apps/desktop/renderer/**`, and the two tool files; runs the guard's own pytest first, then the guard. Copy the shape of `ci-dark-capabilities-gate.yml`. |

**`tools/design-parity/`**

| File                             | Reason                                                                                                          |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `lib/render-live-chats.test.tsx` | Update the `:15-17` "WHY `ChatsArchive` AND NOT `ChatsDestination`" comment — the alternative no longer exists. |

**Not touched:** `apps/frontend`, `apps/desktop`, `packages/design-system`,
`packages/api-types`, any service. Neither host references either deleted symbol, so no
host edit is required — verified by grep, and the guard will keep it that way.

## Non-goals

- **The Projects unmounted components.** All seven (`ProjectEditor`,
  `ProjectFilterChip`, `ProjectsPanel`, `TemplateEditor`, `ArchiveBlockedDialog`,
  `ForkFromTemplateDialog`, `TransferOwnershipDialog`) are a _decide-or-delete_ call —
  several are dialogs for real product decisions (template forking, ownership transfer)
  that may be intended-but-unwired rather than dead. **PRD-10 owns that decision.** This
  PRD's guard would flag them, so PRD-10 must land its verdict before or with the guard;
  see Dependencies.
- **Any parity value fix.** Type scale, weights, spacing, chip styling, the missing
  topbar/⌘K affordance: PRD-01 (tokens/recipes) and PRD-09 (Chats surface). This PRD
  changes zero computed styles — deleting an unstyled class name cannot.
- **A general unused-export sweep of `chat-surface`.** The broad rule flags 49
  identifiers; triaging them is a separate exercise. This PRD narrows deliberately to
  component modules under `destinations/`.
- **Extending the guard to other packages** (`design-system`, `surface-renderers`) or to
  a "route the client calls that no service serves" check. The latter is the _other_
  half of the `ChatsSidebar` sin and is genuinely valuable — specify it as a follow-up
  once the facade route inventory can be computed without false positives from
  templated paths.
- **Deleting the folded legacy destinations** (memory, webhooks, team, routines, agents,
  library). They get waivers, not deletions. Their disposition belongs to the DEAD-1
  IA-fold work.

## Risks & rollback

| Risk                                                                                                                        | Guard                                                                                                                                                                                                                                                                              |
| --------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| An out-of-tree or in-flight branch imports `ChatsDestination`/`ChatsSidebar` from the barrel and breaks on rebase.          | Removing a barrel export is a compile-time break, not a runtime one: `npm run typecheck --workspaces` fails loudly in the offending workspace. Both current hosts are grep-clean.                                                                                                  |
| Dropping the class names changes rendering.                                                                                 | Impossible by construction — no CSS rule anywhere in the shipped app selects them (verified across all 13 stylesheets). Guarded by the existing `_shared/*.test.tsx` inline-style assertions plus the parity harness, which is `data-testid`-driven and cannot observe the change. |
| Some _external_ consumer (a snapshot test, an e2e selector, a stylesheet in `apps/*`) uses `.pg-lead`/`.sect-h`/`.rowlist`. | Verified none exist: the only hits outside the three components are prose comments (`src/index.ts:1396-1397`, `_shared/index.ts:2`) and the vendored mock. `ActivityDestination.test.tsx:245` uses the `activity-day-rowlist` **testid**, not the class.                           |
| The new guard is flaky or over-fires and gets disabled — the classic fate of a bad gate.                                    | It ships with its own pytest suite (required to pass before the guard runs, exactly as `ci-dark-capabilities-gate.yml` does), a real-tree baseline test, and an inline waiver escape hatch. Word-boundary matching over file text only; no TS parsing, no network, no deps.        |
| The waiver block becomes a permanent dumping ground.                                                                        | Each waiver requires `owner=<PRD or issue>`. DoD item 9 pins the count so growth is a visible diff.                                                                                                                                                                                |

**Rollback.** The whole PRD is one revert: `git revert <sha>` restores the four deleted
files, the barrel entries, the three class defaults, and removes the guard + workflow.
Nothing persists — no migration, no config, no stored state.

## Definition of Done

1. `test ! -e packages/chat-surface/src/destinations/chats/ChatsSidebar.tsx && test ! -e packages/chat-surface/src/destinations/chats/ChatsSidebar.test.tsx && test ! -e packages/chat-surface/src/destinations/chats/ChatsDestination.tsx && test ! -e packages/chat-surface/src/destinations/chats/ChatsDestination.test.tsx` exits 0.
2. `grep -rn "ChatsSidebar\|ChatsDestination" packages apps --include="*.ts" --include="*.tsx" | grep -v node_modules` returns **no** matches (barrels, comments, and the design-parity harness comment all cleaned).
3. `grep -rn "chats/projects" packages apps --include="*.ts" --include="*.tsx" | grep -v node_modules` returns only `packages/api-types/src/chats.ts:13` (the historical note recording the stub's retirement).
4. `grep -n '"pg-lead"\|"sect-h"\|"rowlist"' packages/chat-surface/src/destinations/_shared/*.tsx` returns no matches, and `grep -rn "pg-lead\|sect-h\|rowlist" packages apps --include="*.tsx" --include="*.ts" --include="*.css" | grep -v node_modules | grep -v design-parity` returns no matches.
5. `packages/chat-surface/src/destinations/_shared/PageLead.test.tsx` no longer contains `toHaveClass`, and instead asserts a `className` passed by the caller reaches the rendered `<p>` (`render(<PageLead className="x">…)` → `expect(screen.getByTestId("page-lead")).toHaveClass("x")`); the same passthrough assertion exists in `SectionHeader.test.tsx` and `RowList.test.tsx`.
6. **Design value pinned numerically.** `packages/chat-surface/src/destinations/_shared/PageLead.test.tsx` asserts the lead's computed `max-width` is `72ch` and its `line-height` resolves to the loose token — matching `design-kit/app-v3/copilot.css:1556-1562` (`max-width:72ch; line-height:1.6`) — proving the class removal moved no style. (The `12px`/`9.5px` size targets remain PRD-01's DoD, not this one's.)
7. `npx vitest run --root packages/chat-surface` passes with **zero** failures, and the reported test-file count is exactly two lower than on the merge base (the two deleted spec files).
8. `npm run typecheck --workspace @0x-copilot/chat-surface && npm run typecheck --workspace @0x-copilot/frontend && npm run typecheck --workspace @0x-copilot/desktop` all pass.
9. **Regression guard for this exact bug.** `python tools/check_orphan_destinations.py` exits 0 on the tree, and `python -m pytest tools/test_check_orphan_destinations.py -q` passes. That suite contains, at minimum: (a) a fixture reproducing the `ChatsDestination` shape — a `.tsx` under `destinations/` exporting `function X(): ReactElement`, re-exported from `src/index.ts`, referenced by neither host — asserted to be reported as an orphan with a non-zero exit; (b) a fixture with a host reference, asserted clean; (c) a fixture whose only consumer is another non-test file inside `chat-surface`, asserted clean; (d) a fixture with `// orphan-destination-waiver: owner=… — …` on the export line, asserted clean; (e) a baseline test that runs the guard against the real repo tree and asserts exit 0.
10. `python tools/check_orphan_destinations.py --print-waivers` lists every waived identifier with its `owner=` value, and `tools/test_check_orphan_destinations.py` asserts the waiver count is `<= N` where `N` is the count committed in this PR — so a later PR cannot add a waiver without editing that assertion.
11. `.github/workflows/ci-orphan-destinations.yml` exists, triggers on `pull_request` and `push:main` filtered to `packages/chat-surface/src/**`, `apps/frontend/src/**`, `apps/desktop/renderer/**`, `tools/check_orphan_destinations.py`, `tools/test_check_orphan_destinations.py`, and itself; and runs `python -m pytest tools/test_check_orphan_destinations.py -q` **before** `python tools/check_orphan_destinations.py`.
12. **Parity is unmoved.** Re-running the chats surface per `tools/design-parity/SKILL.md` yields a `surfaces/chats/out/report-default.md` whose HIGH/MEDIUM/LOW/INFO counts are byte-identical to the pre-change report (17/59/64/10) — this PRD is a pure-deletion PRD and must not shift a single computed style.
13. `npx vitest run --root apps/desktop` and the frontend suite pass at their pre-change pass counts (no host file is edited, so any delta is a real regression).
14. `pre-commit run --all-files` passes (prettier over the new workflow YAML and ruff/ruff-format over the two new Python files).

## Dependencies

**Must land first / with:**

- **PRD-10 (Projects decide-or-delete).** Independent for the deletions, but the §3
  guard will flag the seven unmounted Projects component modules. Either PRD-10 lands
  its verdict first, or this PR ships those seven as `owner=PRD-10` waivers and PRD-10
  removes them from the waiver block as it disposes of each. Prefer the former; the
  latter is acceptable and keeps this PRD unblocked.

**Independent of:** PRD-01 (tokens/recipes) and PRD-09 (Chats surface). They change
values; this changes existence. They can land in any order — but note the ordering
courtesy: if PRD-09 lands first it will touch `ChatsArchive.tsx`, not the deleted files,
so there is no textual conflict either way.

**This unblocks:**

- **PRD-09**, by removing the two decoys a reader hits when looking for "the Chats
  destination", and by deleting the last consumer of the retired `/v1/chats/projects`
  path so that a future facade-route inventory check can be written without a known
  false positive.
- **The DEAD-1 IA-fold work**, by giving it a CI-visible, monotonically-shrinking
  orphan counter to burn down instead of an untracked ~95k-LOC estimate.
- **The follow-up "client calls a route no service serves" guard** named in Non-goals,
  which reuses this PR's tool + workflow shape.
