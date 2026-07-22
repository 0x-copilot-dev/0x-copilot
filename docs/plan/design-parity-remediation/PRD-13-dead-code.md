# PRD-13 — Dead code removal: barrel-exported components mounted by nobody

> **Reconciled against the normative README.** Rulings applied: **C13** (PRD-01 puts
> `.ui-mono-caps` on the label element; this PRD deletes the vestigial class from the
> wrapper), **C17** (this PRD owns the `ChatsDestination` deletion, the barrel edit and
> the guard; PRD-09 does not delete it), **C20 / DoD-Q10** (DoD 12 is now a delta against
> _this PR's_ merge base, not the frozen `17/59/64/10`), **G8** (the orphan guard's scope
> is extended to `src/shell/**` — with the README's own G8 premise corrected against the
> code, see Evidence), and the **wave order** (Wave 4, immediately after PRD-10; depends
> on PRD-09 and PRD-10). **No migration**: this PRD adds none, and needs none. Verified
> high-water marks on disk — `services/backend/migrations` highest is `0045`,
> `services/ai-backend/migrations` has only `0001`; the README's reassignment table
> allocates nothing to PRD-13.

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
3. Four design class names (`.pg-lead`, `.sect-h`, `.rowlist`, `.act-day`) stamped onto live DOM
   nodes with **zero CSS rules behind them** anywhere in the shipped app. They imply a
   styling contract that does not exist; the surface is actually 100% inline
   `CSSProperties`. Five assertions across four unit-test files assert on these dead
   class names, so the illusion is load-bearing on paper and load-bearing on nothing in
   practice.

A prior repo-wide audit put dead code at ~95k LOC. Deleting these 993 lines is the
small half of this PRD. The large half is the **CI gate that makes the next one
impossible to merge silently**.

## Evidence

Every row opened and verified by me on `claude/design-parity-audit-7ec82a`.

| Claim                                                                                                                                                       | File:line                                                                                                                                                       | What the code actually does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ChatsSidebar` exists and is exported                                                                                                                       | `packages/chat-surface/src/destinations/chats/ChatsSidebar.tsx:191`; `destinations/chats/index.ts:5`; `src/index.ts:487-488`                                    | CONFIRMED. 498 lines. Exported from the package barrel as `ChatsSidebar` + `ChatsSidebarProps`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `ChatsSidebar` fetches a route no service serves                                                                                                            | `ChatsSidebar.tsx:218-223`                                                                                                                                      | CONFIRMED. `transport.request({method:"GET", path:"/v1/chats/projects"})`. `grep -rn "chats" services/backend-facade/src services/backend/src services/ai-backend/src --include="*.py"` returns only unrelated `chats_with_grant` / `chats:` count fields. No route, no proxy passthrough.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| The stub was formally retired                                                                                                                               | `packages/api-types/src/chats.ts:13`                                                                                                                            | CONFIRMED. Comment: the archive binds to `/v1/agent/conversations` "until a dedicated bucketed endpoint exists (PRD §11 — `/v1/chats/projects` stub retired)". The consumer was never deleted with it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `ChatsSidebar` is mounted nowhere                                                                                                                           | repo-wide grep                                                                                                                                                  | CONFIRMED. Only hits: its own file, `ChatsSidebar.test.tsx` (373 lines), the two barrels, and a prose comment at `ChatsDestination.tsx:16`. Zero hits in `apps/frontend/src`, `apps/desktop/renderer`, or anywhere in `chat-surface/src` outside the barrel.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| The comment claims the sidebar is kept "for Run's own thread rail if needed"                                                                                | `ChatsDestination.tsx:16-18`                                                                                                                                    | CONFIRMED as text, FALSIFIED as fact. The Run cockpit does not reference `ChatsSidebar`. "If needed" never became needed; that is the whole failure mode.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `ChatsDestination.tsx` is 48 lines and forwards to `ChatsArchive`                                                                                           | `ChatsDestination.tsx:28-48`                                                                                                                                    | CONFIRMED. A `Partial<ChatsArchiveProps>` defaulting shim: `archive=null`, `onReopen`/`onNewChat` = `noop`. It adds nothing but default props.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `ChatsDestination` is exported from BOTH barrels                                                                                                            | `destinations/chats/index.ts:1-4`; `src/index.ts:486` (value) and `src/index.ts:1141` (`type ChatsDestinationProps`)                                            | CONFIRMED — and the type is re-exported from a _second, later_ barrel block, so it is published twice.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Neither host mounts `ChatsDestination`                                                                                                                      | `apps/frontend/src/app/App.tsx:1054`; `apps/frontend/src/features/chats/ChatsArchiveRoute.tsx:36`; `apps/desktop/renderer/destinationBinders.tsx:31,223`        | CONFIRMED. Web mounts `<ChatsArchiveRoute>` → `<ChatsArchive>`; desktop mounts `<ChatsArchive>` at `:223`. Zero references to `ChatsDestination` under `apps/`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Three design class names are emitted with no CSS behind them                                                                                                | `_shared/PageLead.tsx:37`, `_shared/SectionHeader.tsx:64`, `_shared/RowList.tsx:56`                                                                             | CONFIRMED **with a line correction**: the audit cited `PageLead.tsx:36`; the `className=` line is **37**. `grep -rn "pg-lead\|sect-h\|rowlist" --include="*.css"` over all 13 shipped stylesheets returns matches **only** inside `tools/design-parity/design-kit/` (the vendored mock, never loaded by the app).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| There is a **fourth** live stamp, outside `_shared/`                                                                                                        | `activity/ActivityDestination.tsx:451`; `ActivityDestination.test.tsx:229-230`; design `copilot.css:1683-1697`                                                  | **NEW FINDING on re-check — the code wins over my own first pass**, which claimed the vocabulary lived only in the three `_shared/` components. `className="act-day sect-h"` is emitted on the Activity day divider, and a test asserts `toHaveClass("act-day")`. Neither class has a rule in any shipped stylesheet; the divider is drawn entirely by the inline styles at `:652-670`. Scoped in.                                                                                                                                                                                                                                                                                                                                                                                                |
| `.sect-h` is on the wrong element anyway                                                                                                                    | `SectionHeader.tsx:63-64` vs design `copilot-app.jsx:300`                                                                                                       | NEW FINDING (not in the audit), and the origin of README **C13**. Live puts `sect-h` on the **wrapper `<div>`** that also holds the count pill and the action button; the design puts `.sect-h` on the **label element itself**, inside a bespoke flex wrapper. A rule on the wrapper would therefore mono-uppercase the New-chat button. **C13's ruling:** PRD-01 applies the existing `.ui-mono-caps` recipe to the **label element** (`SectionHeader.tsx:70-76`, `data-testid="section-header-label"`); this PRD deletes the vestigial class from the **wrapper** only. Complementary, not exclusive.                                                                                                                                                                                          |
| Tests assert on the dead class names                                                                                                                        | `_shared/PageLead.test.tsx:13,36`; `_shared/SectionHeader.test.tsx:18`                                                                                          | CONFIRMED, **with a correction to my own earlier count — the code wins**: it is **three** files, not two. `PageLead.test.tsx:13,36` `toHaveClass("pg-lead")` ×2, `SectionHeader.test.tsx:18` `toHaveClass("sect-h")` ×1, **and `RowList.test.tsx:21` `expect(card).toHaveClass("rowlist")`** — which my first pass recorded as absent. With `ActivityDestination.test.tsx:230`'s `toHaveClass("act-day")` (row above) that is **five assertions across four files**, all of which must go.                                                                                                                                                                                                                                                                                                        |
| The parity harness does **not** depend on these class names                                                                                                 | `tools/design-parity/surfaces/*/anchors.json`                                                                                                                   | CONFIRMED. No anchor selector contains `pg-lead`/`sect-h`/`rowlist`; the live side is entirely `data-testid`-driven (stated at `surfaces/chats/out/FINDINGS.md` RC-12). Deleting the classes cannot move a parity number.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| No call site passes `className` to these three                                                                                                              | `ChatsArchive.tsx:300,367,387`; `ActivityDestination.tsx:315,460`; `ProjectDetailView.tsx:960,973`                                                              | CONFIRMED. All seven call sites pass children/`data-testid`/`count`/`action` only. The bespoke `className === undefined ? "x" : \`x ${className}\`` merge at all three sites exists **solely** to prepend the dead class.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| "Ten unmounted Projects components"                                                                                                                         | `packages/chat-surface/src/destinations/projects/`                                                                                                              | **DISPUTED (count), and corrected again on re-check.** Zero _host_ references is the wrong test — this PRD's rule also clears an identifier with an in-package non-test consumer. Under the real rule only **`ProjectsPanel`** is an orphan: `ProjectFilterChip` is live in two Library surfaces (`destinations/library/LibraryPanel.tsx:189`, `SaveToLibraryPopover.tsx:390`), and `ProjectEditor` / `TemplateEditor` / the three dialogs are **not exported from `src/index.ts` at all** (only `ProjectFilterChip:531` and `ProjectsPanel:533` are), so the rule never reaches them. PRD-10 deletes `ProjectsPanel` and wires+exports `ProjectEditor` / `TransferOwnershipDialog` / `ArchiveBlockedDialog` — so post-PRD-10 the Projects directory contributes **zero** waivers. See Non-goals. |
| `ChatsDestination` is not the only orphaned `*Destination` export                                                                                           | `src/index.ts` (17 `*Destination` value exports)                                                                                                                | **NEW FINDING, now measured.** A prototype of §3's exact rule, run on this branch over `destinations/` ∪ `shell/`, reports **12** orphan identifiers: `AgentsPanel`, `ChatsDestination`, `MemoryPanel`, `MemoryProposalCard`, `MemoryProposalToastStack`, `PersonDetailView`, `ProjectsPanel`, `SaveToLibraryPopover`, `TeamInviteWizard`, `TeamPanel`, `WebhookDetailView`, `WebhooksDestination` — **zero of them under `shell/`**. The guard is red on day one; §3's landing-state table disposes of every entry.                                                                                                                                                                                                                                                                              |
| README **G8**: "`ChatShell.tsx:318-323` mounts `<RightRail>` without `activity`, so `ActivityTabContent` renders empty on every non-full-bleed destination" | `ChatShell.tsx:323`; `RightRail.tsx:196-197, :247, :265, :283-296`                                                                                              | **HALF-FALSIFIED — the code wins.** The mount without `activity` is real (`ChatShell.tsx:323` passes only `open`/`onToggle`). The consequence is not: `tabsEnabled = children === undefined && activity !== undefined && approvals !== undefined` (`RightRail.tsx:196-197`), so with neither array supplied the rail renders `<EmptyStateMessage>` ("Per-destination context surfaces here.", `:283-296`) — **not** an empty `ActivityTabContent`. `ActivityTabContent entries={activity ?? []}` (`:265`) is unreachable in that state. There is no dead render and nothing for a dead-code PRD to delete.                                                                                                                                                                                        |
| README **G8**: "`ActivityList`'s only consumer is `HomeDestination`"                                                                                        | `destinations/connectors/ConsumersTab.tsx:112`; `destinations/home/sections/LiveActivityRail.tsx:76`; `WhatsNewDigest.tsx:142`; `team/PersonDetailView.tsx:317` | **FALSIFIED — the code wins.** Four in-package consumers, two of them outside Home. Under §3's rule `ActivityList` is not an orphan and could not become one under any scope extension. G8's assignment is still honoured — §3's scope is extended to `src/shell/**` — but the honest measured result is that the extension flags **nothing** today (see the orphan row above). The residual real gap ("no host ever supplies `activity`/`approvals` to the shell rail") is a binding-totality question owned by **PRD-03**, not a dead-code one.                                                                                                                                                                                                                                                 |
| Cost of the deletion                                                                                                                                        | `wc -l`                                                                                                                                                         | `ChatsSidebar.tsx` 498 + `ChatsSidebar.test.tsx` 373 + `ChatsDestination.tsx` 48 + `ChatsDestination.test.tsx` 74 = **993 lines**.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |

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
`9.5px` mono rung (`--font-size-mono-9-5`), repoints the existing `.ui-mono-caps`
recipe (`packages/design-system/src/styles.css:1097-1104`) at it, and applies that recipe
to `SectionHeader`'s **label element** rather than its wrapper (README **C13**); PRD-09 owns the Chats
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

### 2. Delete the four vestigial class names; do **not** promote them

The seam is `packages/chat-surface/src/destinations/_shared/{PageLead,SectionHeader,RowList}.tsx`.
Each drops its hardcoded class default and lets the inherited `className` from
`HTMLAttributes` flow through `{...rest}` (`RowList` keeps its explicit `className`
prop and simply forwards it). The fourth stamp — `className="act-day sect-h"` at
`activity/ActivityDestination.tsx:451` — is a plain literal with no merge logic and is
simply removed; the divider's appearance is entirely inline (`:652-670`).

Why delete rather than add rules to `packages/design-system/src/styles.css`:

- The design-system already owns this role under a different name. `.ui-mono-caps`
  (`styles.css:1097-1104`) **is** `.sect-h`; `.ui-badge` (`styles.css:555-568`) **is**
  `.chip`. Adding `.sect-h` would create a second vocabulary for the same role — the
  exact parallel-token anti-pattern the ui-kit consolidation (#219/#221) closed.
- The live markup is structurally incompatible **at the wrapper**. `.sect-h` sits on the
  wrapper `<div>` (`SectionHeader.tsx:64`) that contains the count pill and the action
  slot; the design's `.sect-h` is the label element (`copilot-app.jsx:300`). A faithful
  `.sect-h` rule on the wrapper would apply mono/uppercase/9.5px to the "New chat"
  button. Per README **C13** the recipe belongs on the **label** (`SectionHeader.tsx:70-76`)
  and PRD-01 puts it there in Wave 0; by the time this PRD lands in Wave 4 the label
  already carries `.ui-mono-caps`, and the wrapper's `sect-h` is unambiguously vestigial.
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
whose source module lives under `src/destinations/` **or `src/shell/`** and whose
defining module is a `.tsx` file exporting `function <Name>(…): ReactElement`:

> the identifier must appear, as a whole word, in at least one non-test file under
> `apps/frontend/src/` or `apps/desktop/renderer/`, **or** in at least one non-test,
> non-`index.ts` file inside `packages/chat-surface/src/`.

**Waiver.** `// orphan-destination-waiver: owner=<PRD or issue> — <reason>` on the
export line in `src/index.ts`, mirroring
`# dark-capability-waiver:` (`check_dark_capabilities.py` docstring). The waiver lives
at the export site so it shows up in the diff that would otherwise hide the orphan.

**Why `src/shell/` is in scope (README G8).** The README assigns G8 to this PRD with the
choice "scope extension **or** an explicit `owner=` waiver". This PRD takes the scope
extension: it is one path in a tuple, it costs nothing, and it closes the stated hole
("the guard's scope does not reach `shell/`") permanently. What it does **not** do is
close G8's underlying claim, because that claim is falsified by the code (see the two
G8 Evidence rows): `ActivityList` has four in-package consumers, and `RightRail` renders
`<EmptyStateMessage>` — not an empty Activity pane — when the host supplies no tab data
(`RightRail.tsx:196-197, :283-296`). The measured result of the extension is **zero new
orphans** today. The remaining, real half of G8 — no host ever passes `activity` /
`approvals` into the shell rail — is a **host-binding totality** defect and belongs to
**PRD-03**, which owns `ShellHostBinding`; it is recorded here, not fixed here, because
fixing it means adding a host data source, which is the opposite of a deletion PRD.

**Why this scope and not "all unused exports".** I prototyped the broad rule (every
value export sourced from `destinations/`): it flags **49** identifiers, most of them
copy constants and hooks legitimately hoisted for host or test use. A 49-entry
allowlist rots into noise within a quarter. Narrowing to _component modules that render_
is the class where the audited defect actually lives, is decidable from the file's own
`export function … : ReactElement` signature, and produces a waiver list small enough
to read.

**Landing state (mandatory, honest).** The guard is red on the current tree — a prototype
of the rule reports the **12** identifiers listed in the Evidence table. Disposition, as
of the post-PRD-09 / post-PRD-10 tree this PRD lands on:

| Orphan                                                                                                                                                                                                                                                                | Action                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --- |
| `ChatsDestination`, `ChatsSidebar`                                                                                                                                                                                                                                    | **Deleted** by §1 (README **C17** gives this PRD the deletion, the barrel edit and the guard; PRD-09 does not delete it). Note `ChatsSidebar` is currently _text-clean_ only because `ChatsDestination.tsx:16` names it in a comment — deleting both together is what makes the tree green.                                                                                                                                                                         |
| `ProjectsPanel`                                                                                                                                                                                                                                                       | **Deleted by PRD-10** (its D9), which lands immediately before this PRD. No waiver, no action here. If PRD-10 slipped, this PRD waives it as `owner=PRD-10` rather than deleting it.                                                                                                                                                                                                                                                                                |     |
| The legacy-IA component modules the prototype actually flags: `AgentsPanel`, `MemoryPanel`, `MemoryProposalCard`, `MemoryProposalToastStack`, `PersonDetailView`, `SaveToLibraryPopover`, `TeamInviteWizard`, `TeamPanel`, `WebhookDetailView`, `WebhooksDestination` | **Waived, in one contiguous block, with one shared reason**: folded IA surfaces (`destinationsForProfile` renders neither `memory` nor `webhooks` in `single_user_desktop` or `team` — `shell/destinations.ts`), disposition owned by the DEAD-1 audit. Each waiver carries `owner=DEAD-1`. The implementer MUST re-run the guard and waive exactly what it reports — this list is the prototype's output on the pre-PRD-09/PRD-10 tree, not an allowlist to paste. |

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

| File                                                     | Reason                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --- |
| `src/destinations/chats/ChatsSidebar.tsx`                | DELETE — 498 lines; fetches `/v1/chats/projects`, served by no service.                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `src/destinations/chats/ChatsSidebar.test.tsx`           | DELETE — 373 lines testing a deleted component.                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `src/destinations/chats/ChatsDestination.tsx`            | DELETE — 48-line defaulting shim over `ChatsArchive`, mounted by neither host.                                                                                                                                                                                                                                                                                                                                                                                                               |
| `src/destinations/chats/ChatsDestination.test.tsx`       | DELETE — 74 lines testing a deleted component.                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `src/destinations/chats/index.ts`                        | Drop lines 1-5 (both `ChatsDestination` and `ChatsSidebar` re-exports); keep the `ChatsArchive` block.                                                                                                                                                                                                                                                                                                                                                                                       |
| `src/index.ts`                                           | Remove `ChatsDestination`/`ChatsSidebar`/`ChatsSidebarProps` at `:486-488`; remove `type ChatsDestinationProps` at `:1141`; update the block comments at `:1133-1136` that describe the forwarding relationship; add the `orphan-destination-waiver` comments required by §3.                                                                                                                                                                                                                |
| `src/destinations/_shared/PageLead.tsx`                  | Drop the `"pg-lead"` class default at `:37`; stop destructuring `className` and let it ride `{...rest}`.                                                                                                                                                                                                                                                                                                                                                                                     |
| `src/destinations/_shared/SectionHeader.tsx`             | Same at `:64` for `"sect-h"` — the **wrapper** class only. PRD-01 has already moved `.ui-mono-caps` onto the label element (`:70-76`) by Wave 0 (README **C13**); do not touch the label.                                                                                                                                                                                                                                                                                                    |     |
| `src/destinations/_shared/RowList.tsx`                   | Same at `:56` for `"rowlist"`; keep the explicit `className` prop and forward it unchanged.                                                                                                                                                                                                                                                                                                                                                                                                  |
| `src/destinations/activity/ActivityDestination.tsx`      | Drop the `className="act-day sect-h"` stamp at `:451` — a **fourth** live stamp of the dead vocabulary (`act-day` has no CSS rule in the shipped app either; the design's `.act-day` lives only in `design-kit/app-v3/copilot.css:1683-1697`). The inline styles at `:652-670` already implement the divider and stay. **PRD-08 owns this file** (README hot-file table: 02 → 04 → 08); this PRD lands in Wave 4 _after_ PRD-08, so this is a one-line stacking edit, not a competing claim. |
| `src/destinations/activity/ActivityDestination.test.tsx` | Remove `expect(dividers[0]).toHaveClass("act-day")` at `:230` and its `:229` comment; keep the surrounding divider assertions. Same PRD-08 ordering note.                                                                                                                                                                                                                                                                                                                                    |
| `src/destinations/_shared/index.ts`                      | Update the header comment at `:2-3` — it advertises the `.pg-lead/.sect-h/.rowlist/.lrow` class vocabulary as the contract.                                                                                                                                                                                                                                                                                                                                                                  |
| `src/destinations/_shared/PageLead.test.tsx`             | Replace the `toHaveClass("pg-lead")` assertions at `:13,36` with the inline-style contract + `className` passthrough.                                                                                                                                                                                                                                                                                                                                                                        |
| `src/destinations/_shared/SectionHeader.test.tsx`        | Replace `toHaveClass("sect-h")` at `:18` likewise.                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `src/destinations/_shared/RowList.test.tsx`              | Remove `toHaveClass("rowlist")` at `:21` (my first pass wrongly said none existed) and add the `className` passthrough assertion; the surrounding inline-style assertions at `:23-25` stay as the real contract.                                                                                                                                                                                                                                                                             |     |

**`tools/` (repo-level, no service deps)**

| File                                      | Reason                                                                                                                                                       |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `tools/check_orphan_destinations.py`      | NEW — the guard from §3, scoped to `src/destinations/` ∪ `src/shell/` (README **G8**). Pure stdlib, mirrors `check_dark_capabilities.py` structure and CLI.  |
| `tools/test_check_orphan_destinations.py` | NEW — unit tests: orphan detected, host-mounted passes, in-package-consumer passes, waiver honoured, a `shell/`-scoped fixture, real-tree baseline is green. |

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

- **The Projects unmounted components.** **PRD-10 owns that decision and has already made
  it** (its disposition table): `ProjectFilterChip` KEEP (live at `LibraryPanel.tsx:189`,
  `SaveToLibraryPopover.tsx:390`), `ProjectEditor` / `TransferOwnershipDialog` /
  `ArchiveBlockedDialog` WIRE + export, `ProjectsPanel` DELETE, `TemplateGallery` /
  `TemplateEditor` / fork dialog WIRE in a later PRD (they are not barrel-exported, so
  this PRD's rule never sees them). PRD-10 lands immediately before this PRD (Wave 4), so
  the Projects directory contributes **zero** entries to the waiver block. This PRD
  neither deletes nor waives any of them.

- **The shell right rail's missing `activity` / `approvals` data (README G8).** The rail
  is honest scaffolding, not a dead render — `RightRail.tsx:196-197` gates the tabbed view
  on the host supplying both arrays and otherwise renders `<EmptyStateMessage>` (`:283-296`).
  Making the shell rail live means adding a host data source through `ShellHostBinding`,
  which is **PRD-03**'s seam. This PRD takes only G8's guard-scope half (`src/shell/**`,
  §3).
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

| Risk                                                                                                                                   | Guard                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --- |
| An out-of-tree or in-flight branch imports `ChatsDestination`/`ChatsSidebar` from the barrel and breaks on rebase.                     | Removing a barrel export is a compile-time break, not a runtime one: `npm run typecheck --workspaces` fails loudly in the offending workspace. Both current hosts are grep-clean.                                                                                                                                                                                                                                                                |
| Dropping the class names changes rendering.                                                                                            | Impossible by construction — no CSS rule anywhere in the shipped app selects them (verified across all 13 stylesheets). Guarded by the existing `_shared/*.test.tsx` inline-style assertions plus the parity harness, which is `data-testid`-driven and cannot observe the change.                                                                                                                                                               |
| Some _external_ consumer (a snapshot test, an e2e selector, a stylesheet in `apps/*`) uses `.pg-lead`/`.sect-h`/`.rowlist`/`.act-day`. | Verified by the repo-wide grep in DoD 4: outside the four stamps the only hits are prose comments (`src/index.ts:1396-1397`, `_shared/index.ts:2`, and per-file header comments) plus the vendored mock. Nothing in `apps/` and no `.css` file selects them. `ActivityDestination.test.tsx:245` uses the `activity-day-rowlist` **testid**, not the class — only `:230`'s `toHaveClass("act-day")` is a real class assertion, and it is removed. |     |
| The new guard is flaky or over-fires and gets disabled — the classic fate of a bad gate.                                               | It ships with its own pytest suite (required to pass before the guard runs, exactly as `ci-dark-capabilities-gate.yml` does), a real-tree baseline test, and an inline waiver escape hatch. Word-boundary matching over file text only; no TS parsing, no network, no deps.                                                                                                                                                                      |
| The waiver block becomes a permanent dumping ground.                                                                                   | Each waiver requires `owner=<PRD or issue>`. DoD item 10 pins the count to an exact integer literal in the test file, so growth cannot land without editing that assertion.                                                                                                                                                                                                                                                                      |

**Rollback.** The whole PRD is one revert: `git revert <sha>` restores the four deleted
files, the barrel entries, the four class stamps, and removes the guard + workflow.
Nothing persists — no migration, no config, no stored state.

## Definition of Done

1. `test ! -e packages/chat-surface/src/destinations/chats/ChatsSidebar.tsx && test ! -e packages/chat-surface/src/destinations/chats/ChatsSidebar.test.tsx && test ! -e packages/chat-surface/src/destinations/chats/ChatsDestination.tsx && test ! -e packages/chat-surface/src/destinations/chats/ChatsDestination.test.tsx` exits 0.
2. `grep -rn "ChatsSidebar\|ChatsDestination" packages apps --include="*.ts" --include="*.tsx" | grep -v node_modules` returns **no** matches (barrels and comments cleaned), and `grep -rn "ChatsDestination\|ChatsSidebar" tools/design-parity/lib` returns **no** matches (the `render-live-chats.test.tsx:15-17` "WHY `ChatsArchive` AND NOT `ChatsDestination`" comment is rewritten, since the alternative no longer exists).
3. `grep -rn "chats/projects" packages apps --include="*.ts" --include="*.tsx" | grep -v node_modules` returns only `packages/api-types/src/chats.ts:13` (the historical note recording the stub's retirement).
4. **No live stamp of the dead vocabulary remains.** `grep -rn 'className=.*\b\(pg-lead\|sect-h\|rowlist\|act-day\)\b' packages apps --include="*.tsx" | grep -v node_modules` returns **no** matches — covering `_shared/PageLead.tsx:37`, `_shared/SectionHeader.tsx:64`, `_shared/RowList.tsx:56` **and `activity/ActivityDestination.tsx:451`** (`className="act-day sect-h"`, a fourth live stamp found on re-check). Prose comments naming the design's class vocabulary are **not** in scope for this grep — an explanatory comment citing `copilot.css` is documentation, not a fake styling contract; the two comments that _advertise the vocabulary as this package's contract_ (`_shared/index.ts:2-3`, `src/index.ts:1396-1397`) are rewritten per Scope.
5. `grep -rn 'toHaveClass("pg-lead")\|toHaveClass("sect-h")\|toHaveClass("rowlist")\|toHaveClass("act-day")' packages/chat-surface/src` returns **no** matches (all five assertions removed: `PageLead.test.tsx:13,36`, `SectionHeader.test.tsx:18`, `RowList.test.tsx:21`, `activity/ActivityDestination.test.tsx:230`), and each of `PageLead.test.tsx` / `SectionHeader.test.tsx` / `RowList.test.tsx` contains a test named `"forwards className from the caller"` asserting `render(<X className="x" …/>)` → `expect(screen.getByTestId(<testid>)).toHaveClass("x")` for testids `page-lead` / `section-header` / `row-list` respectively.
6. **Design value pinned numerically.** A test named `"pins the design's lead geometry"` in `packages/chat-surface/src/destinations/_shared/PageLead.test.tsx` asserts, on the rendered `<p data-testid="page-lead">`, `style.maxWidth === "72ch"` and `style.lineHeight === "var(--line-height-loose)"`, and asserts `--line-height-loose` is `1.7` in `packages/design-system/src/styles.css:97` — the design's `max-width:72ch; line-height:1.6` at `design-kit/app-v3/copilot.css:1556-1562` (the `1.7`-vs-`1.6` delta is PRD-01's, recorded here so the class removal is provably style-neutral). Source values verified: `PageLead.tsx:21-27`, `styles.css:97`.
7. `npx vitest run --root packages/chat-surface` exits 0 with zero failures. (The file-count delta claim is dropped — item 1 already pins the two spec deletions by path, which is the checkable form.)
8. `npm run typecheck --workspace @0x-copilot/chat-surface && npm run typecheck --workspace @0x-copilot/frontend && npm run typecheck --workspace @0x-copilot/desktop` all pass.
9. **Regression guard for this exact bug.** `python tools/check_orphan_destinations.py` exits 0 on the tree, and `python -m pytest tools/test_check_orphan_destinations.py -q` passes. That suite contains, at minimum: (a) a fixture reproducing the `ChatsDestination` shape — a `.tsx` under `destinations/` exporting `function X(): ReactElement`, re-exported from `src/index.ts`, referenced by neither host — asserted to be reported as an orphan with a non-zero exit; (b) a fixture with a host reference, asserted clean; (c) a fixture whose only consumer is another non-test file inside `chat-surface`, asserted clean; (d) a fixture with `// orphan-destination-waiver: owner=… — …` on the export line, asserted clean; (e) a baseline test that runs the guard against the real repo tree and asserts exit 0; (f) **README G8** — a fixture whose defining module sits under `src/shell/` (not `src/destinations/`) with no host and no in-package consumer, asserted reported as an orphan, proving the scope extension is live.
10. `python tools/check_orphan_destinations.py --print-waivers` prints one `<Identifier> owner=<value>` line per waiver and exits 0, and `tools/test_check_orphan_destinations.py` contains a test named `"waiver count does not grow"` asserting `len(waivers) == <N>` where `<N>` is an **integer literal** committed in this PR (expected `10` on the post-PRD-10 tree; the implementer commits whatever the guard actually reports). A later PR adding a waiver fails that assertion.
11. `.github/workflows/ci-orphan-destinations.yml` exists, triggers on `pull_request` and `push:main` filtered to `packages/chat-surface/src/**`, `apps/frontend/src/**`, `apps/desktop/renderer/**`, `tools/check_orphan_destinations.py`, `tools/test_check_orphan_destinations.py`, and itself; and runs `python -m pytest tools/test_check_orphan_destinations.py -q` **before** `python tools/check_orphan_destinations.py`.
12. **Parity is unmoved — as a delta against _this PR's_ merge base** (README **C20** / **DoD-Q10**; the frozen `17/59/64/10` is stale, both because PRD-01/02/09 move those counts and because `lib/extract-computed.js` now captures `boxShadow`/`backdropFilter`/`transition`/`textDecorationLine` and `lib/compare.mjs` no longer emits phantom `borderColor` rows). Check: regenerate `tools/design-parity/surfaces/chats/out/report-default.md` per `tools/design-parity/SKILL.md` at `git merge-base HEAD origin/main` and again on this PR's HEAD, then `diff <(grep -E '^## .+ \([0-9]+\)$' base/report-default.md) <(grep -E '^## .+ \([0-9]+\)$' head/report-default.md)` exits 0 — those four heading lines carry the HIGH/MEDIUM/LOW/INFO counts (`report-default.md:10` reads `## 🔴 HIGH (15)` on this branch today, which is itself proof the frozen `17` is stale). Additionally `git diff --exit-code -- tools/design-parity/surfaces/chats/out/report-default.md` exits 0 on this PR. A pure-deletion PRD must not shift a single computed style.
13. `npx vitest run --root apps/desktop` and `npm run test --workspace @0x-copilot/frontend` exit 0, **or** their failing test ids are byte-identical to `docs/plan/design-parity-remediation/baseline-failures.txt`, which this PR does not modify (same form as README **DoD-Q2**). No host file is edited, so any other delta is a real regression.
14. `pre-commit run --all-files` passes (prettier over the new workflow YAML and ruff/ruff-format over the two new Python files).

## Dependencies

**Wave 4, and last in the program** (README _Corrected implementation order_): both the
orphan-waiver list and DoD 12's parity delta are only computable against a settled tree.

**Must land first:**

- **PRD-10 (Projects decide-or-delete)** — immediately before this PRD, same wave. It
  deletes `ProjectsPanel` and wires + exports `ProjectEditor` / `TransferOwnershipDialog`
  / `ArchiveBlockedDialog`, which is what makes the Projects directory contribute zero
  waivers here. If PRD-10 slips, this PR waives `ProjectsPanel` as `owner=PRD-10` and
  bumps DoD 10's integer; it is not otherwise blocked.
- **PRD-09 (Chats surface)** — it rewrites `ChatsArchive.tsx` and `ChatsArchive.test.tsx`
  and owns the topbar/full-bleed split; landing it first means this PRD's greps and its
  DoD-12 parity delta run against the final chats tree. Per README **C17** PRD-09 does
  **not** delete `ChatsDestination` — that is this PRD's, together with the barrel edit
  and the guard that keeps it deleted.
- **PRD-01 (tokens/recipes)** — ordering only, no file conflict. Per README **C13** it
  moves `.ui-mono-caps` onto `SectionHeader`'s **label** element; this PRD then deletes
  the vestigial `sect-h` from the **wrapper**. If this PRD landed first, PRD-01 would be
  applying a recipe to a component whose wrapper class had just changed shape.

**No migration and no contract change.** Verified on disk: `services/backend/migrations`
highest is `0045`, `services/ai-backend/migrations` has only `0001`; the README's
reassignment table (C18) allocates `0046`/`0047` to PRD-06/PRD-07 and `0002`–`0004` to
PRD-05/07/09. This PRD claims none of them.

**This unblocks:**

- **Readers of the Chats tree**, by removing the two decoys a reader hits when looking
  for "the Chats destination", and by deleting the last consumer of the retired
  `/v1/chats/projects` path so that a future facade-route inventory check can be written
  without a known false positive. (This PRD lands _after_ PRD-09, so the benefit accrues
  to whoever touches chats next, not to PRD-09 itself.)
- **The DEAD-1 IA-fold work**, by giving it a CI-visible, monotonically-shrinking
  orphan counter to burn down instead of an untracked ~95k-LOC estimate.
- **The follow-up "client calls a route no service serves" guard** named in Non-goals,
  which reuses this PR's tool + workflow shape.
