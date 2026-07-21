# v3 visual fidelity pass — v3 parity plan

> Scope: a batch of confirmed visual deltas that keep the Run cockpit off the v3
> "quiet" look. Each delta is small; this plan enumerates them as discrete FRs with
> exact edit points. It BUILDS ON the already-shipped cockpit wiring (single event
> projection, live-streaming `TcChat` presentational via `messages`, `useRunSources`
> Sources fold, v3 type/composer/tab-strip). It changes **presentation only** — no
> new event stream, no second projector, no faked data.
>
> Design source of truth (fetched, byte-exact): `copilot-v3.css` +
> `copilot.css` (DesignSync project `73f810d9-7b77-4849-9087-f7f8e366c48a`) and the
> staged mockups `copilot-run-side.jsx` / `copilot-workspace3.jsx`.

## Problem statement

The cockpit is _functionally_ at parity — chat streams, Sources fill, tabs switch —
but it still reads as a different product than the v3 design at the pixel level. A
user flipping between the v3 mockup and the running app sees six concrete tells:

1. **Tab badges look like web pills, not cockpit chips.** The Agents/Approvals count
   badges render in the UI sans face at `--font-size-2xs` (11.2px) inside a fully
   round `999px` pill. v3 badges are a tight **JetBrains-Mono 9px/700** glyph in a
   fixed **15×15** rounded-8 chip — the difference between "a web notification dot"
   and "a terminal-grade status chip". Worse, a **pending Approvals** badge is the
   one place the design shouts: v3 fills it **amber** (`.b.hot`) because an approval
   is _blocking a run and waiting on you_. Today it renders in the calm **sky**
   accent, so the single most action-demanding signal in the cockpit is
   indistinguishable from a neutral count.
2. **Assistant turns have no author.** v3 stamps a quiet mono `0xCopilot` label over
   every assistant message (`.msg.bot .who`) so a scrolled-back transcript reads as a
   dialogue. Our `renderMessage` emits the parts only — assistant prose floats with
   no attribution, and in Focus mode (no bubble, flush text) it can blur into the
   user's own words.
3. **Steering the run gives no receipt.** In v3, sending a steer/goal flashes a mono
   accent line with a live dot — _"Routed to the run — agent acknowledged, adjusting
   the plan"_ — that self-clears after ~4.2s (`.ack2`). It is the micro-confirmation
   that your words reached the agent. We show nothing: you type, hit send, and stare
   at an unchanged transcript wondering if it landed.
4. **Plan checklist (Studio).** v3 draws a tick-mark plan (`.plan-step`:
   jade-check / accent-spin / muted-chevron). We draw none — but there is **no
   run-scoped plan/checklist in the event contract** to draw from (see Descopes).
5. **Source rows are catalog cards, not receipts.** v3 Sources is a compact boxed
   `.rowlist` of `.lrow` receipts (30px connector chip · mono name · mono
   "read 11:40 · 42 cells" meta · trailing ↗). Ours is the web citation **Card**
   catalog (`[N]` badge · favicon · neutral connector badge · snippet · "Cited N×"
   footnote). The cockpit rail is a _run ledger_, not a citation library; the card
   skin makes it feel like the latter and wastes the narrow rail width.
6. **Brand strings drift.** In-cockpit copy still says bare "Copilot" (and legacy
   surfaces say "Atlas"). v3 is uniformly **0xCopilot**.

These are all "quiet look" regressions: individually cosmetic, collectively the
reason the cockpit doesn't yet feel like the v3 design. All are backable by the
current contract **except #4**, which is honestly descoped.

## Functional requirements

- **FR-1 (tab badge chip).** The workspace tab badge MUST render as JetBrains-Mono
  9px/700, in a fixed 15×15 pill (`min-width:15px; height:15px; padding:0 4px`),
  `border-radius: 8px`, `background: var(--color-surface-elevated)`,
  `color: var(--color-text-muted)`, grid-centered. It MUST NOT use the round
  `999px`/`--font-size-2xs`/sans styling it has today. Both light and dark themes.
- **FR-2 (pending-Approvals is amber).** When `pendingApprovals > 0`, the Approvals
  tab badge MUST render in the amber/"hot" variant (`background: var(--color-warning)`,
  `color: var(--color-bg)`) — NOT the sky accent. The Agents badge and a zero-pending
  Approvals badge MUST stay neutral. The amber tone MUST be driven declaratively from
  `pendingApprovals > 0`, not from an inline color style on the badge node.
- **FR-3 (assistant who-label).** For a message with `role === "assistant"`,
  `renderMessage` MUST render a `0xCopilot` label above the message parts: mono
  9.5px, uppercase, `letter-spacing: .1em`, `color: var(--color-text-subtle)`,
  `margin-bottom: 4px`. It MUST NOT render for `user`/`system`/`tool` roles. It MUST
  NOT alter the streaming-cursor render of any part (the label is a sibling node
  emitted before the parts map; `MarkdownText`/`PlainText` status handling is
  untouched). Studio and Focus both show it (single mount — FR-3.9).
- **FR-4 (steer acknowledgement line).** On composer send in an active run, the
  cockpit MUST show a `.ack2` line — mono 10px, `color: var(--color-accent)`, with a
  leading 5px accent dot — reading _"Routed to the run — agent acknowledged,
  adjusting the plan"_. It MUST auto-clear after 4200ms (±). Re-sending before the
  timer elapses MUST restart the line/timer, not stack a second line. It MUST render
  in both Studio and Focus, between the transcript and the composer. It MUST derive
  from the local send action, never from a fabricated backend field.
- **FR-5 (compact source row).** The Run rail's Sources tab MUST render each source
  as a compact `.lrow`-style row: a 30×30 connector chip (the `SourceFavicon`
  fallback glyph), a mono 11px name (`title ?? source_doc_id`), a mono meta line, and
  a trailing external-link affordance shown only when `source.source_url` is present.
  Rows MUST sit in a boxed, hairline-bordered rowlist with per-row dividers. This
  MUST be a **rail-gated variant** — the web citation catalog card
  (`citations/SourceRow.tsx`) MUST render unchanged everywhere else.
- **FR-6 (source meta is honest).** The meta line MUST be composed only from fields
  the contract produces: `sourceFreshnessLabel(...)` (already used) plus a
  `Cited N×` prefix when `citation_count > 1`. The mockup's "· 42 cells" / "· 6.2 KB"
  fragments MUST NOT appear (no such field) — see Descopes.
- **FR-7 (brand normalization, in-cockpit).** All user-facing "Copilot" strings on
  cockpit-reachable surfaces MUST read "0xCopilot": `TcChat` approval reassurance,
  `ApprovalsTab` empty copy, `SourcesTab`/`SourcesPanel` empty copy. Their unit-test
  expectations MUST be updated in lockstep.
- **FR-8 (no second projection / no remount).** None of FR-1…FR-7 may open a second
  SSE or projector, and the single `TcChat` MUST NOT remount on a Studio↔Focus or
  tab switch. The who-label, `.ack2`, and badge changes are pure render/props over
  the existing single projection.

## Non-functional requirements

- **NFR-1 (one event projection — FR-3.3).** The `.ack2` signal is the only new piece
  of "state" and it is **local UI**, not run state: it originates from the composer
  send action and lives in a shell-owned hook, not a new event read. Sources/badges
  continue to read the existing `useRunSources` fold and `approvalsQueue` /
  `subagents` projections. No `useEventProjector` or `Transport.subscribe` is added.
- **NFR-2 (single mount — FR-3.9).** The who-label and `.ack2` are emitted inside the
  one `TcChat`; positioning is by DOM order within that component (transcript →
  ack → composer), never by moving `TcChat` in the tree. The badge lives in the
  always-mounted tablist. Nothing here adds a mount/unmount keyed on mode or tab.
- **NFR-3 (substrate boundary).** All new code lands in `@0x-copilot/chat-surface` and
  touches no `window/document/fetch/localStorage`. `setTimeout`/`clearTimeout` (used
  for the 4.2s ack clear) are standard timers, not in the `no-restricted-globals`
  ban list, and are already used in the package. `SourceFavicon` keeps its existing
  `<img src>` (not `fetch`) fallback chain. No `apps/*` import.
- **NFR-4 (host-fed / presentational).** `TcChat` stays presentational: `steerAck` is
  a prop; the who-label is derived from `role`. The compact row is a drop-in
  `SourceRowSlot` component swapped by the host binder — no fetching moves into a
  component. The ack timer lives in `destinations/run/useSteerAck.ts` (shared by both
  hosts, one home) so the web/desktop binders do not each re-implement it.
- **NFR-5 (design tokens only, both themes).** Every color/size goes through
  `packages/design-system/src/styles.css` tokens. Mapping of the design's literals to
  tokens (verified against `copilot.css` `:root` + the DS `:root`):
  - `.b` bg `var(--panel3)` → `--color-surface-elevated` (`#1d1d23` dark / `#ebebee`
    light); `.b` text `var(--mut)` → `--color-text-muted`.
  - `.b.hot` bg `var(--amber)` → `--color-warning`; text `#1d1607` → `--color-bg`
    (near-black in dark; a light neutral on the darkened light-theme amber — both
    legible, and it themes automatically, unlike a frozen `#1d1607`).
  - `.who` `#64646d` → `--color-text-subtle`.
  - `.ack2` `var(--accent)` → `--color-accent`.
  - `.plan-step` check `var(--jade)` → `--color-success`; spin `var(--accent)` →
    `--color-accent`; future `var(--mut2)` → `--color-text-subtle` (descoped — listed
    for completeness).
  - `.lrow__logo` `var(--panel3)`/`var(--tx2)` → `--color-surface-elevated` /
    `--color-text-strong`; `.src-nm` mono 11px `--color-text`; `.lrow__sub`
    `var(--mut2)` → `--color-text-subtle`; container border `--color-border`.
- **NFR-6 (a11y).** The amber badge keeps its existing `aria-label`
  (`"N pending approvals"`); tone is conveyed by color AND the label, not color
  alone. The who-label is decorative text (the assistant turn already carries
  `data-role="assistant"`); it needs no ARIA and MUST NOT be announced as a separate
  live region. The `.ack2` line SHOULD live inside the existing `aria-live="polite"`
  chat container so it is announced once, then cleared. The compact row's external
  affordance is a `<button>`/`<a>` with an explicit `aria-label`
  (`"Open <name>"`), keyboard-activatable; the row keeps a focus-visible ring.
- **NFR-7 (perf).** No new renders on the stream hot path: badges/who-label are pure
  functions of already-computed props; the ack timer fires at most once per send.
  The compact row does the same work as the card row (one `SourceFavicon`), minus the
  `Card`/two `Badge` subtrees — strictly cheaper.
- **NFR-8 (tests required).** Every FR ships with a unit test (see Test plan); the
  amber-tone, who-label-role-gating, ack-autoclear, and rail-vs-catalog-variant
  behaviors each guard a specific regression.

## Architecture & plan

### Components / hooks introduced

- `destinations/run/useSteerAck.ts` — **new**. `useSteerAck(copy: string): { ack:
string | null; fire: () => void }`. `fire()` sets `ack = copy` and (re)schedules a
  4200ms `setTimeout` → `ack = null`, clearing any prior timer; cleans up on unmount.
  One home for the timer so both host binders reuse it.
- `citations/CompactSourceRow.tsx` — **new**. `SourceRowSlot`-shaped
  (`forwardRef<HTMLLIElement, SourceRowProps>`). Renders `.atlas-source-lrow`
  (a `<button>` row): connector chip via `SourceFavicon` (fallback glyph, sized 30px),
  mono `.src-nm` name, mono meta (`sourceFreshnessLabel` + optional `Cited N×`), and a
  trailing ↗ when `source.source_url` (calls `onJumpToChat ?? onSelect`).
- No new hook for who-label/badge — they are inline render changes.

### Data flow (unchanged topology)

`useRunSession.events` → (single projection) → `useRunSources` fold, `approvalsQueue`,
`subagents` → `RunDestination` threads them to `RunWorkspaceRail` and the one `TcChat`.
FR-1/2 restyle the badge in the tablist path; FR-3/4 add render inside `TcChat`;
FR-5/6 swap the `SourceRowComponent` the rail hands to `SourcesTab`. The steer ack is
the only new signal and it flows _out_ of the composer send, not _in_ from a stream.

### Exact edit points (current line numbers verified)

**FR-1/FR-2 — tab badge chip + amber pending**

- `packages/chat-surface/src/workspace/workspace.css` **L76–89**: replace
  `.atlas-workspace-tabs__badge` body with the v3 chip:
  `min-width:15px; height:15px; padding:0 4px; border-radius:8px;
display:grid; place-items:center; line-height:1; font-family:var(--font-mono);
font-size:9px; font-weight:var(--font-weight-bold);
background:var(--color-surface-elevated); color:var(--color-text-muted);`
  Keep the active-tab override (L86–89) but retarget it to the chip
  (`background:var(--color-bg); color:var(--color-text)`). Add a new modifier:
  `.atlas-workspace-tabs__badge--hot { background:var(--color-warning);
color:var(--color-bg); }` — and ensure it wins under an active tab.
- `packages/chat-surface/src/workspace/WorkspaceTabs.tsx` **L21–28** (`WorkspaceTabsItem`):
  add `badgeTone?: "neutral" | "hot";`. **L152–156**: apply
  `classNames("atlas-workspace-tabs__badge", item.badgeTone === "hot" &&
"atlas-workspace-tabs__badge--hot")` to the badge wrapper.
- `packages/chat-surface/src/destinations/run/RunWorkspaceRail.tsx`:
  - **L183–193**: on the `approvals` tab item, add
    `badgeTone: pendingApprovals > 0 ? "hot" : undefined`.
  - **L323–338** (`approvalsBadge`): return a **plain** node (`{pending}` in a `<span
data-testid="run-rail-approvals-badge" aria-label={...}>`) — DROP `data-tone="accent"`
    and the inline `style={approvalsBadgeStyle}`.
  - **L371–374**: delete `approvalsBadgeStyle` (color now comes from the CSS `--hot`
    modifier). Confirm no other reference.
  - `agentsBadge` (**L306–321**) unchanged — it inherits the neutral chip.

**FR-3 — assistant who-label**

- `packages/chat-surface/src/thread-canvas/TcChat.tsx` `renderMessage` **L535–582**:
  immediately inside the `<li>` (after **L545**, before the parts map at L546), emit:
  `{m.role === "assistant" ? <div style={whoLabelStyle}>0xCopilot</div> : null}`.
  Add `whoLabelStyle` to the style block (near L767):
  `{ fontFamily:"var(--font-mono)", fontSize:"9.5px", textTransform:"uppercase",
letterSpacing:".1em", color:"var(--color-text-subtle)", marginBottom:4 }`.
  No change to the `parts.map` — cursor/status path (L546–579) untouched.

**FR-4 — steer ack line**

- `TcChat.tsx`:
  - Props (**L181–200 region**): add `readonly steerAck?: string | null;` and extend
    the `renderComposer` ctx type (**L197–200**) with `readonly onSteer: () => void;`.
  - Destructure `steerAck` (**L217–230**).
  - Render: define `const ackLine = steerAck ? <div style={ackLineStyle}
data-testid="tc-chat-ack">{steerAck}</div> : null;` and place it between
    `{transcript}`/approvals and `{composer}` in BOTH the Focus return (**L326–351**,
    before `{composer}` at L348) and the Studio return (**L353–378**, before
    `{composer}` at L376). Pass `onSteer` into the ctx at the `renderComposer(...)`
    call (**L314**): `renderComposer({ disabled: ghost, placeholder:
composerPlaceholder, onSteer })` where `onSteer` comes from props; for the base
    `<Composer>` path (L316) also fire it in `onSend` (`(text)=>{ onSend?.(text); }`
    — RunDestination will pass `onSend={fireSteerAck}` so standalone still works).
  - Styles: `ackLineStyle = { fontFamily:"var(--font-mono)", fontSize:10,
color:"var(--color-accent)", display:"flex", gap:7, alignItems:"center",
flexShrink:0, padding:"0 8px" }` plus a `::before` 5px accent dot. Since
    inline-style pseudo-elements are impossible, render the dot as an explicit
    sibling `<span aria-hidden style={ackDotStyle}/>` (`width:5,height:5,
borderRadius:"50%",background:"var(--color-accent)",flexShrink:0`).
- `destinations/run/useSteerAck.ts` (**new**) as specified above; export from
  `destinations/run/index.ts`.
- `RunDestination.tsx`:
  - Add `const STEER_ACK_COPY = "Routed to the run — agent acknowledged, adjusting the plan";`
    and `const { ack: steerAck, fire: fireSteerAck } = useSteerAck(STEER_ACK_COPY);`.
  - `TcChat` element (**L515–528**): add `steerAck={steerAck}` and
    `onSend={fireSteerAck}` (base-composer fallback). The `renderComposer` prop is
    threaded verbatim (L526) — the host composer will call `ctx.onSteer`.
  - `handleStartGoal` (empty-state goal, ~**L602**): call `fireSteerAck()` on submit
    so the very first goal also acks.
- **Host binders (both, cross-host):** the web `features/run/*Route.tsx` and desktop
  `renderer/destinationBinders.tsx` `renderComposer` implementations must call
  `ctx.onSteer()` inside their `AssistantComposer` `onSubmit`, alongside the existing
  steer POST. This is the one edit outside the package; keep the two in lockstep.

**FR-5/FR-6 — compact source row (rail-gated)**

- `citations/CompactSourceRow.tsx` (**new**) — see component spec. Meta =
  `[citation_count > 1 ? \`Cited ${citation_count}× · \` : "", sourceFreshnessLabel({
  connectorSlug: source_connector, freshnessAt: freshness_at, lastCitedAt:
  last_cited_at })].join("")`.
- `workspace/SourcesTab.tsx`: add `variant?: "card" | "compact"` (default `"card"`)
  to `SourcesTabProps` (**L59–67 region**); destructure with default (**L74–84**);
  stamp `data-variant={variant}` on the `.atlas-workspace-tab` root (**L126**) and on
  each `<ul className="atlas-workspace-tab__list">` (**L137–141**, **L170**). Row swap
  needs NO change here — it already renders `SourceRowComponent` (**L145**, **L174**).
- `destinations/run/RunWorkspaceRail.tsx`: add `readonly sourcesVariant?: "card" |
"compact";` to props; pass `variant={sourcesVariant}` into `<SourcesTab>` (**L239–247**).
- `RunDestination.tsx` `rightRail` (**L534–547**): pass
  `SourceRowComponent={CompactSourceRow}` and `sourcesVariant="compact"`.
- `workspace/workspace.css`: add scoped compact skin (both themes):
  `.atlas-workspace-tab[data-variant="compact"] .atlas-workspace-tab__list{
display:flex; flex-direction:column; border:1px solid var(--color-border);
border-radius:var(--radius-md); overflow:hidden; background:var(--color-surface); }`
  and `.atlas-source-lrow` rules (row `display:flex; align-items:center; gap:12px;
padding:9px 11px; border-bottom:1px solid var(--color-border);` + `:last-child`
  no-border, `:hover` `--color-surface-muted`), `.atlas-source-lrow__logo` (30×30,
  radius 7, `--color-surface-elevated`/`--color-text-strong`), `.src-nm`
  (`font-family:var(--font-mono); font-size:11px; color:var(--color-text)`),
  `.atlas-source-lrow__sub` (mono, `--color-text-subtle`), trailing icon
  `color:var(--color-text-subtle)`.
- `citations/SourceRow.tsx` — **untouched** (web catalog card unchanged).
- Barrel: export `CompactSourceRow` from `src/index.ts` (Sources block) so the web
  binder can also opt in later.

**FR-7 — brand strings (in-cockpit)**

- `thread-canvas/TcChat.tsx` **L206**: `APPROVAL_REASSURANCE` "…before Copilot acts…"
  → "…before 0xCopilot acts…".
- `workspace/ApprovalsTab.tsx` **L45**: "Copilot is waiting on you." → "0xCopilot is
  waiting on you."
- `workspace/SourcesTab.tsx` **L109** + `citations/SourcesPanel.tsx` **L52**: "…as
  Copilot finds them." → "…as 0xCopilot finds them."
- Update matching test expectations: `thread-canvas/TcChat.test.tsx` (L505 fixture is
  a card `reason`, not the reassurance — leave unless asserting reassurance),
  `approvals/ApprovalCard.test.tsx` (reassurance strings), any `ApprovalsTab`/`Sources`
  copy assertions.

### api-types / service-contract changes

- **None.** FR-1…FR-3, FR-5…FR-7 are presentation over existing shapes (`SourceEntry`
  already carries `title`, `source_doc_id`, `source_url`, `source_connector`,
  `citation_count`, `freshness_at`, `last_cited_at`). FR-4's ack is local UI state.
- The **only** contract that would be needed is a run-scoped plan/checklist (FR-4-plan,
  delta d) — explicitly **NOT** added here (Descopes).

### Ordered, independently-shippable commits

1. **feat(chat-surface): v3 tab-badge chip + amber pending-approvals badge** — FR-1/2
   (`workspace.css`, `WorkspaceTabs.tsx`, `RunWorkspaceRail.tsx`) + tests.
2. **feat(chat-surface): assistant `0xCopilot` who-label in TcChat** — FR-3 + test.
3. **feat(chat-surface): steer-ack line (`useSteerAck` + TcChat `.ack2`)** — FR-4,
   package side (RunDestination + hook + TcChat) + tests.
4. **feat(run): wire `ctx.onSteer` in web + desktop composers** — FR-4 host binders
   (the two-host lockstep edit); tiny, lands right after commit 3.
5. **feat(chat-surface): compact source-row variant for the Run rail** — FR-5/6
   (`CompactSourceRow`, `SourcesTab` variant seam, `RunWorkspaceRail`,
   `RunDestination`, `workspace.css`) + tests.
6. **chore(chat-surface): normalize in-cockpit brand strings to 0xCopilot** — FR-7 +
   test updates.

Each commit is green on its own; 4 depends on 3, 5 is standalone, 6 is standalone.

## Descopes & rationale

- **Tick-mark plan `.plan-step` (delta d) — DESCOPE / NEW-CONTRACT.** Evidence:
  `packages/api-types/src` has **no** run-scoped plan/checklist type — `grep` finds
  only `Todo*` (a separate destination concept) and `ProjectTemplateSeededTodo`; the
  run event projector (`thread-canvas/eventProjector.ts`) produces subagents/approvals/
  chat/sources folds, no plan. The mockup's `PLAN` (copilot-workspace3.jsx L80–87) is
  static fixture data. Drawing tick-marks would require **faking** step state, which
  NFR-1/HONEST-DATA forbid. To ship it we would first need a new cross-stack contract:
  ai-backend emits ordered plan-step events (id, label, status
  `done|running|future`), `api-types` adds a `RunPlanStep`/`RunPlanProjection`, and a
  pure `projectPlan(events)` selector feeds a new `.plan-step` block in `TcChat`
  (Studio only). Tracked as a separate NEW-CONTRACT item, out of this visual-fidelity
  pass.
- **Source meta "· 42 cells" / "· 6.2 KB" (delta e) — DESCOPE.** Evidence:
  `SourceEntry` (api-types `index.ts` L2308–2318) has no cell-count / byte-size /
  range field; the mockup values (copilot-run-side.jsx L109–112) are literals. FR-6
  renders only contract-backed meta (`sourceFreshnessLabel` + `Cited N×`). Adding the
  fragment would be fabrication.
- **"read 11:40" wall-clock exactness — accept freshness label.** The design shows a
  read time; the contract gives `freshness_at`/`last_cited_at`, surfaced via the
  existing `sourceFreshnessLabel`. We use that (honest, already shipped) rather than
  inventing a per-read timestamp.
- **Legacy "Atlas" strings (delta f, non-cockpit) — DESCOPE from this front.**
  Evidence: the remaining `Atlas` hits are all in folded/legacy destination **tests**
  (`destinations/home|memory|inbox|todos/*.test.tsx`) — not on any cockpit-reachable
  surface. Normalizing the whole product's brand copy is a separate sweep; FR-7 scopes
  to cockpit surfaces to keep this batch tight and reviewable.

## Test plan

- **FR-1/2 (`RunWorkspaceRail.test.tsx`, `WorkspaceTabs.test.tsx`):** with
  `approvalsQueue.pending.length > 0`, the Approvals tab item carries
  `badgeTone="hot"` and the rendered badge wrapper has class
  `atlas-workspace-tabs__badge--hot`; with 0 pending, no hot class and the tab item is
  absent-badged; Agents badge stays neutral. Guards the regression where pending
  approvals render in calm sky and disappear into neutral counts. (CSS px/mono is
  visual; assert the class + a computed-style smoke check if the harness supports it.)
- **FR-3 (`TcChat.test.tsx`):** an `assistant` message renders a `0xCopilot` node
  (mono/uppercase style) before its parts; a `user`/`system`/`tool` message renders
  **no** who-label; a streaming assistant part (`status.type === "running"`) still
  renders its cursor (existing assertion unbroken). Guards role-leak + cursor
  regression.
- **FR-4 (`useSteerAck.test.ts` + `TcChat.test.tsx`):** `fire()` sets `ack`, and with
  fake timers advancing 4200ms clears it; a second `fire()` before expiry keeps a
  single line and resets the timer. In `TcChat`, `steerAck="…"` renders one
  `[data-testid=tc-chat-ack]` with the dot; `null` renders none; the line sits between
  transcript and composer in both modes. Guards "no receipt on send" + timer-stacking.
- **FR-5/6 (`CompactSourceRow.test.tsx`, `SourcesTab.test.tsx`):** CompactSourceRow
  renders the connector chip + mono `.src-nm` + mono meta; the ↗ affordance appears
  only when `source_url` is set and is labeled; meta contains `Cited 3×` when
  `citation_count===3` and **never** a "cells"/"KB" fragment. `SourcesTab` with
  `variant="compact"` stamps `data-variant="compact"` on root + list (boxed skin);
  default `variant` leaves the catalog card path unchanged (web catalog regression
  guard).
- **FR-7 (copy tests):** updated assertions read "0xCopilot" on the reassurance,
  ApprovalsTab-empty, and Sources-empty strings; a grep-style test (if present) finds
  no bare "Copilot" on cockpit surfaces.

Run: `npx vitest run --root packages/chat-surface`; `npm run typecheck --workspace
@0x-copilot/chat-surface`; ESLint must stay green (substrate boundary).

## Risks & gotchas

- **`--color-bg` as amber-badge text.** The design's frozen `#1d1607` on `#e8b45e` is
  dark-theme-only; using `--color-bg` keeps contrast in both themes (near-black in
  dark, light-neutral on the darkened light-theme amber `#9a6400`). Verify the light
  theme visually — if `--color-bg` reads low-contrast on light-amber, fall back to
  `--color-warning-bg`-vs-`--color-warning` inversion, but do **not** hardcode
  `#1d1607`.
- **Active-tab badge override collision (FR-1).** The existing
  `.atlas-workspace-tabs__tab--active .atlas-workspace-tabs__badge` rule (workspace.css
  L86–89) will otherwise recolor the hot badge when Approvals is the active tab.
  Order/selector-specificity the `--hot` rule to win under an active tab, or scope the
  active override to `:not(.atlas-workspace-tabs__badge--hot)`.
- **`ctx.onSteer` is a new required-ish ctx field (FR-4).** Type it as required on the
  ctx but have both host binders call it; the base `<Composer>` path uses `onSend`
  instead, so standalone `TcChat` (no `renderComposer`) still acks via
  `onSend={fireSteerAck}`. Forgetting the host-binder edit (commit 4) means the ack
  silently never fires in the real app while unit tests (which drive the hook
  directly) stay green — call this out in review.
- **Who-label + Focus flush text (FR-3).** In Focus the assistant message is flush
  (no bubble); the who-label must sit tight above it without adding a gap that breaks
  the reading column rhythm (`margin-bottom:4px` matches v3, no top margin).
- **ack timer leak.** `useSteerAck` must `clearTimeout` on unmount and on re-`fire()`;
  a dangling timer setting state after unmount throws in tests. Covered by the hook
  test with fake timers.
- **Compact-row ref forwarding.** `SourceRowSlot` requires a `forwardRef<HTMLLIElement>`
  — CompactSourceRow's outer element must be the `<li>` receiving the ref (SourcesTab
  scrolls the focused row into view). Rendering the button as the ref target instead
  breaks focus-scroll silently.
- **Do not restyle the shared `.atlas-workspace-tab__list`.** The boxed rowlist rule is
  gated behind `[data-variant="compact"]`; an unscoped edit would box the web catalog
  too. Keep the gate.
