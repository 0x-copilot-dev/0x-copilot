# Approvals tab: inline sign-off inbox — v3 parity plan

> Scope: the Run cockpit's right-rail **Approvals** tab (`RunWorkspaceRail` →
> `ApprovalsTab`). Builds on the already-shipped single-projection cockpit
> (`useRunSession.events` → `projectApprovals`), the live in-chat approval cards
> in `TcChat`, and the wired binary HITL round-trip in `RunDestination`.
> All file:line references below were read against the current worktree
> (`main` has advanced past the audit — verified 2026-07-21).

## Problem statement

**What a user sees today.** When the agent pauses for sign-off, the pending
approval renders as a real, actionable card **only inside the chat column**
(`TcChat` → `renderStudioApprovalCard` / `renderConfCard`). The dedicated
**Approvals tab** in the right rail is a dead-end: each pending item is a
**jump-only row** — clicking it just scrolls the chat to the inline card
(`ApprovalsTab.tsx:95-124`, `onJumpToApproval`). You cannot approve or reject
from the tab. Worse, there is a **live dead seam**: `RunDestination` builds
`handleApprove`/`handleReject` and hands them to `RunWorkspaceRail`
(`RunDestination.tsx:543-544`), and `RunWorkspaceRail` even _declares_
`onApprove?`/`onReject?` in its props (`RunWorkspaceRail.tsx:127-128) — but it
never destructures them (`RunWorkspaceRail.tsx:132-152`) and never forwards them
to `<ApprovalsTab>` (`RunWorkspaceRail.tsx:276-280`). The callbacks are received
and silently dropped. The tab has no way to resolve anything.

**The v3 intent.** `copilot-run-side.jsx`'s `ApprovalsPanel` is a **sign-off
inbox**: stacked `.conf-card` blocks, each with **inline Approve / Reject** on
the card itself (`copilot-run-side.jsx:60-105`). A batch card shows one header +
one row per item with per-row actions; post/swap cards show a preview +
`.ap-actions`; an empty state reads "Nothing waiting on you — runs continue
autonomously"; a policy `.sd-note` explains the auto-approve rule with a
"change policy" link; the pending badge turns amber (`.b.hot`); Focus adds an
amber `.fx-note` "holding N actions" banner.

**Why it matters now.** Approvals are the product's trust surface — "you're
always asked before Copilot acts outside this chat." The rail tab exists
precisely so a user watching the _work surface_ (not scrolled to the chat) can
clear their sign-off queue in place. Today that tab is decorative and the seam
that would make it work is wired to nothing. This is both a **parity gap** and a
**latent bug**: the code reads as if inline resolution already works from the
rail. This plan closes the seam, feeds the tab the rich approval model it needs,
and makes the in-chat card and the tab card render from **one shared component**
so they can never drift.

## Functional requirements

- **FR-1 (MUST) — kill the dead seam.** `RunWorkspaceRail` MUST destructure
  `onApprove`/`onReject` and forward them to `<ApprovalsTab>`. After this change,
  `handleApprove`/`handleReject` from `RunDestination` reach the tab. Guarded by
  a rail test asserting a click inside the Approvals panel invokes the injected
  callback with the right `approvalId`.

- **FR-2 (MUST) — inline resolve from the tab.** A pending, resolvable approval
  in the Approvals tab MUST render **Approve** and **Reject** controls that call
  `onApprove(approvalId)` / `onReject(approvalId)` — the same callbacks the
  in-chat card uses. No jump-to-chat is required to act.

- **FR-3 (MUST) — rich model, not the thin queue item.** The tab MUST render
  from the full `RunApproval` projection (`approvalProjection.approvals`:
  title, reason, summary, category, params, target, resolved, decision), not the
  lossy `ApprovalsQueueItem` (`workspace/types.ts:61-83`, which drops
  reason/category/params). The thin `ApprovalsQueueProjection` remains ONLY the
  source of the rail's pending-count badge (`RunWorkspaceRail.tsx:173,324`).

- **FR-4 (MUST) — one shared card, two mount points.** The three inline
  renderers currently private to `TcChat`
  (`renderStudioApprovalCard` `TcChat.tsx:386`, `renderConfCard` `:428`,
  `renderApprovalReceipt` `:476`) MUST be extracted into ONE shared module
  (`approvals/InlineApprovalCards.tsx`), barrel-exported, and consumed by BOTH
  `TcChat` and `ApprovalsTab`. A resolved approval MUST collapse to the shared
  `InlineApprovalReceipt` in both places. Extraction MUST be behavior-preserving
  for the existing TcChat path (same DOM, same `data-testid`s).

- **FR-5 (MUST) — batch grouping.** `projectApprovals` MUST read `batch_id` /
  `batch_index` off the `approval_requested` / `approval_resolved` payloads
  (`api-types` `ApprovalRequestedPayload.batch_id`/`batch_index`,
  `index.ts:1602-1603`; resolved mirror `:1809-1810`), defaulting `batchId` to
  the `approvalId` and `batchIndex` to `0` when absent (an N=1 interrupt).
  `RunApproval` MUST carry `batchId: string` + `batchIndex: number`.

- **FR-6 (MUST) — one card per batch.** When two or more pending approvals share
  a `batchId` (N parallel tool-calls from one LangGraph interrupt), the tab MUST
  render **one card** with a header + one row per `batchIndex` (ascending), each
  row carrying its own Approve/Reject, plus a single **"Approve all"** control
  that loops `onApprove` over every unresolved item in the batch. A singleton
  batch (the common case) renders as a single-row card with no "Approve all".

- **FR-7 (MUST) — exclude `ask_a_question` from the approve/reject path.**
  `ask_a_question` approvals resolve to _answered / skipped_, not
  _approved / rejected_ (`approvalProjection.ts:311` maps `answered`→approved but
  the semantics are wrong for a Reject button). They MUST NOT render Approve/
  Reject controls in the batch-card path; the tab MUST fall back to a
  jump-to-chat row for them (the question UI lives in chat).

- **FR-8 (MUST) — empty state.** With zero pending and zero recent approvals the
  tab MUST show the v3 clear-state copy ("Nothing waiting on you — runs continue
  autonomously.") rather than the current terse "No pending approvals in this
  conversation." (`ApprovalsTab.tsx:27-36`).

- **FR-9 (MUST) — resolved section.** Server-resolved and optimistically-resolved
  approvals MUST render as read-only receipts (`InlineApprovalReceipt`) under a
  "Recent" header, using the existing `resolved`/`decision` fields — no fake
  "Auto-approved today" log (see Descopes).

- **FR-10 (MUST) — policy note.** The tab MUST render the static policy `.sd-note`
  ("Reads auto-approve; writes, posts & spends wait for you") with an optional
  **change-policy** link that, when an `onChangePolicy?` callback is supplied,
  navigates the host to Settings → Model behavior / approval policy. When the
  callback is absent the note renders as plain text (no dead link).

- **FR-11 (MUST) — scrub guard.** While the cockpit is scrubbed off-now
  (`scrubbed` / `isScrubbed`), the tab is already dropped from the tablist
  (`RunWorkspaceRail.tsx:184-192`) and `RunDestination` passes `approvals=[]`
  (`RunDestination.tsx:473`); the inline resolve path MUST NOT be reachable from
  a past state. No new code — a test MUST lock this.

- **FR-12 (MUST) — pending badge amber.** The rail's Approvals badge MUST switch
  to the warn/amber token (v3 `.b.hot`) when pending > 0, replacing today's
  accent-colored count (`RunWorkspaceRail.tsx:324-338,371-374`).

- **FR-13 (SHOULD) — Focus "holding N" banner.** In Focus mode, when N ≥ 1
  approvals are pending, `TcChat`'s Focus branch SHOULD render an amber
  `.fx-note` "Holding N action(s) for your sign-off" banner above the conf-cards
  (`TcChat.tsx:339-347`). N is `visibleApprovals.length` — derived, not fetched.

## Non-functional requirements

- **NFR-1 — one projection (FR-3.3).** No new SSE subscription and no second
  projector. `ApprovalsTab` consumes the memoized `projectApprovals(session.events)`
  output that `RunDestination` already computes (`RunDestination.tsx:462-469`) and
  already threads to `TcChat` as `chatApprovals`. Batch-grouping is a **pure
  view helper** over that array, not a re-projection.

- **NFR-2 — presentational tab / host owns I/O.** `ApprovalsTab` MUST NOT fetch
  or POST. Resolution stays in `RunDestination.resolveApproval`
  (`RunDestination.tsx:483-504`, `POST /v1/agent/approvals/{id}/decision` with
  optimistic `localDecisions`). The tab only invokes injected callbacks.

- **NFR-3 — substrate boundary.** No `window`/`document`/`fetch`/`localStorage`
  in any touched file. Grouping helper is pure TS. Change-policy navigation is a
  host callback (`onChangePolicy?`), never a direct navigate.

- **NFR-4 — single-mount preserved (FR-3.9).** Extraction of the inline cards
  MUST NOT change TcChat's mount identity or move the single `TcChat` in the
  React tree; it only swaps three local `function render*` calls for imported
  components. No `key`/tree-position changes.

- **NFR-5 — standalone `WorkspacePane` stays safe.** `WorkspacePane` also mounts
  `<ApprovalsTab>` (`WorkspacePane.tsx:242-244`) for the host that binds via
  `useApprovalsQueue`. New props (`approvals`, `onApprove`, `onReject`,
  `onChangePolicy`) MUST be optional; when `onApprove`/`onReject` are undefined
  the tab MUST degrade to jump-only rows (no dead buttons). `WorkspacePane` MUST
  forward whatever its host supplies.

- **NFR-6 — honest data.** No UI element wired to a field the backend does not
  produce (see Descopes). Batch grouping uses a real contract field; the policy
  note is static copy + an optional host nav; the "holding N" banner is derived
  from pending count.

- **NFR-7 — design tokens, both themes.** New card CSS uses `--color-*` /
  `--font-*` tokens only, authored as inline `CSSProperties` in the TcChat
  convention (the mock's `copilot-v3.css` is NOT in the repo). Amber uses the
  existing warn token; verify contrast in light + dark.

- **NFR-8 — a11y.** Each batch card is a `role="group"` with an
  `aria-label` naming the action; row action buttons have accessible labels
  ("Approve <title>", "Reject <title>"); "Approve all" announces the count; the
  amber badge keeps its `aria-label="N pending approvals"`.

- **NFR-9 — tests required.** Unit: batch projection, grouping helper,
  `ask_a_question` exclusion, empty/recent rendering, callback wiring, jump-only
  degradation. Integration: rail-forwarded resolve, scrub suppression. (See Test
  plan.)

## Architecture & plan

### Components / hooks introduced

| New / changed                       | Where                                                                                                                                   | Role                                                                                                                                                                                                                         |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `InlineApprovalCards.tsx` (NEW)     | `packages/chat-surface/src/approvals/`                                                                                                  | Exports `StudioApprovalCard`, `ConfApprovalCard`, `InlineApprovalReceipt` + a shared `InlineApproval` view-model type. The extracted bodies of the three private TcChat renderers.                                           |
| `InlineApproval` type (NEW)         | `approvals/InlineApprovalCards.tsx`                                                                                                     | Structural superset target = current `TcChatApproval` (`TcChat.tsx:44-65`). `RunApproval` (`approvalProjection.ts:47-77`) is assignable to it. Lives in `approvals/` to avoid a `workspace → destinations/run` import cycle. |
| `groupApprovalsByBatch()` (NEW)     | `approvalProjection.ts` (or `approvals/batchGrouping.ts`)                                                                               | Pure: `readonly RunApproval[] → readonly ApprovalBatch[]`. Preserves request order, groups by `batchId`, sorts rows by `batchIndex`.                                                                                         |
| `batchId`/`batchIndex` (NEW fields) | `approvalProjection.ts` `MutableApproval` (`:102-118`), `RunApproval` (`:47-77`), `reduceRequested` (`:196-241`), `freeze` (`:262-280`) | Read from payload; default to `approvalId`/`0`.                                                                                                                                                                              |
| `ApprovalsTab` (REWRITE)            | `workspace/ApprovalsTab.tsx`                                                                                                            | Renders batch cards + inline actions from `InlineApproval[]`; degrades to jump-only when callbacks absent.                                                                                                                   |

### Data flow (unchanged spine)

```
useRunSession.events (ONE stream)
   └─ projectApprovals(events)               [RunDestination.tsx:462, memoized]
        └─ overlayApprovalDecisions(..., localDecisions)   [optimistic]
             ├─ chatApprovals  → TcChat.approvals          [:473,522]  (in-chat cards)
             └─ approvals[]    → RunWorkspaceRail.approvals → ApprovalsTab.approvals   (NEW thread)
        toApprovalsQueue(projection) → RunWorkspaceRail.approvalsQueue  [badge count ONLY]
resolveApproval(id, decision)  [RunDestination.tsx:483]  ← onApprove/onReject
   ├─ TcChat (already wired)                 [:523-524]
   └─ RunWorkspaceRail → ApprovalsTab        [NEW forward]
```

### Exact edit points

1. **`approvalProjection.ts` — project the batch.**
   - `RunApproval` (`:47-77`): add `readonly batchId: string;` +
     `readonly batchIndex: number;`.
   - `MutableApproval` (`:102-118`): add `batchId: string; batchIndex: number;`.
   - `reduceRequested` (`:196-241`): after `approvalId` is resolved (`:202`), read
     `const batchId = stringField(payload.batch_id) ?? approvalId;` and
     `const batchIndex = numberField(payload.batch_index) ?? 0;`; set both in the
     `byId.set(...)` object (preserve via `existing?.batchId ?? batchId`).
     Add a small `numberField(value): number | null` reader (mirror
     `stringField` `:380`).
   - `freeze` (`:262-280`): copy `batchId`, `batchIndex`.
   - Add exported `interface ApprovalBatch { batchId: string; title: string;
rows: readonly RunApproval[]; pendingCount: number; }` and
     `groupApprovalsByBatch(approvals): readonly ApprovalBatch[]` (first-seen
     order; rows sorted by `batchIndex`; `title` from the first row).

2. **`approvals/InlineApprovalCards.tsx` (NEW) — extract the three renderers.**
   - Move `renderStudioApprovalCard` (`TcChat.tsx:386-426`) →
     `export function StudioApprovalCard({ approval, onApprove, onReject })`.
   - Move `renderConfCard` (`:428-474`) → `export function ConfApprovalCard(...)`.
   - Move `renderApprovalReceipt` (`:476-489`) →
     `export function InlineApprovalReceipt({ approval })`.
   - Move the associated inline styles (`approvalsWrapStyle` `:807`,
     `approvalApproveButtonStyle` `:815`, `approvalRejectButtonStyle` `:830`,
     `confCardStyle` `:851` … `confFootStyle` `:905`) into this module; keep
     `confCardsWrapStyle`/`approvalsWrapStyle` next to their consumers or export
     them. Keep `APPROVAL_REASSURANCE` (`:205`) here.
   - Define `export interface InlineApproval` = the current `TcChatApproval`
     shape (`:44-65`). Re-export a `TcChatApproval` alias from `thread-canvas`
     for wire-compat (`thread-canvas/index.ts:42`).
   - Preserve every `data-testid` verbatim (`tc-chat-approval-*`,
     `tc-chat-conf-*`, `tc-chat-approval-receipt-*`) so existing TcChat tests
     pass unchanged.

3. **`approvals/index.ts` (barrel) — export the new module** (`:1-15`):
   `export { StudioApprovalCard, ConfApprovalCard, InlineApprovalReceipt,
type InlineApproval } from "./InlineApprovalCards";`. Then add to the main
   package barrel `src/index.ts` in the approvals block (`~:854`).

4. **`TcChat.tsx` — consume the extracted cards.**
   - Delete the three local `function render*` bodies (`:386-489`) and the moved
     styles; import `StudioApprovalCard` / `ConfApprovalCard` /
     `InlineApprovalReceipt` from `../approvals`.
   - Studio map (`:367-375`) and Focus map (`:339-347`) render the imported
     components; `TcChatApproval` becomes the `InlineApproval` alias.
   - **FR-13**: in the Focus branch (`:339`), when `visibleApprovals.length > 0`,
     render an amber `.fx-note` banner (`role="status"`,
     `data-testid="tc-chat-fx-note"`, "Holding N action(s) for your sign-off")
     above `tc-chat-conf-cards`.

5. **`workspace/ApprovalsTab.tsx` — the inbox rewrite.**
   - `ApprovalsTabProps` (`:17-20`): add
     `approvals?: readonly InlineApprovalWithBatch[]` (rich; `InlineApproval` +
     `batchId`/`batchIndex`/`approvalKind`/`resolved`/`decision`/`messageId`),
     `onApprove?: (approvalId: string) => void`,
     `onReject?: (approvalId: string) => void`,
     `onChangePolicy?: () => void`. Keep `queue` + `onJumpToApproval` (badge +
     jump-only fallback).
   - Render order: policy `sd-note` (FR-10) → empty `ap-clear` (FR-8) → pending
     batch cards (FR-6) → "Recent" receipts (FR-9).
   - Pending: `groupApprovalsByBatch(pendingApprovals)`; for each batch render one
     card (`ConfApprovalCard`-style header + rows). Rows whose `approvalKind ===
"ask_a_question"` (FR-7) render a jump-only row (`onJumpToApproval`) instead
     of Approve/Reject. "Approve all" appears only when a batch has ≥2 unresolved
     resolvable rows; it loops `onApprove` over them.
   - When `onApprove`/`onReject` are undefined (standalone host, NFR-5), render
     every row as a jump-only button (today's behavior) — no dead controls.
   - Reuse `StudioApprovalCard`/`InlineApprovalReceipt` where a single-row card is
     equivalent, to keep pixel parity with the chat card.

6. **`destinations/run/RunWorkspaceRail.tsx` — forward the seam.**
   - Destructure block (`:132-152`): add `onApprove`, `onReject`, `approvals`,
     `onChangePolicy`.
   - Props (`:117-129`): add `readonly approvals?: readonly RunApproval[];` and
     `readonly onChangePolicy?: () => void;` (keep the existing
     `onApprove?`/`onReject?` at `:127-128`).
   - `<ApprovalsTab>` mount (`:276-280`): pass `approvals`, `onApprove`,
     `onReject`, `onChangePolicy` alongside `queue` + `onJumpToApproval`.
   - `approvalsBadge` (`:324-338`) + `approvalsBadgeStyle` (`:371-374`): swap the
     accent color for the warn/amber token when pending > 0 (FR-12); add
     `data-tone="warn"`.

7. **`destinations/run/RunDestination.tsx` — pass the rich array.**
   - `rightRail` (`:534-547`): add
     `approvals={isScrubbed ? [] : approvalProjection.approvals}` (reuse
     `chatApprovals` `:473`). `onApprove`/`onReject` already passed (`:543-544`).
   - Optionally thread `onChangePolicy` from a new `RunDestinationProps` callback
     (host → Settings). If not supplied this phase, omit — the note degrades to
     plain text (FR-10). Naming it now: `readonly onOpenApprovalPolicy?: () =>
void;` next to `onOpenModelSettings` (`:144`).

8. **`workspace/WorkspacePane.tsx` — forward for standalone hosts.**
   - Props (`~:83-84`): add optional `approvals?`, `onApprove?`, `onReject?`,
     `onChangePolicy?`.
   - `<ApprovalsTab>` mount (`:242-244`): forward them. Standalone host may leave
     `onApprove`/`onReject` undefined → jump-only (NFR-5).

### Contract changes

- **No new backend contract required.** `batch_id`/`batch_index` already exist on
  `ApprovalRequestedPayload` (`api-types/src/index.ts:1602-1603`) and
  `ApprovalResolvedPayload` (`:1809-1810`), emitted by
  `runtime_worker/stream_events.py` and re-projected by `approval_coordinator.py`.
  This plan only starts _reading_ them in the FE projector. **No `api-types`
  edit, no service edit.**

### Ordered, independently-shippable commits

1. **Extract inline cards** (`InlineApprovalCards.tsx` + barrels + TcChat swap).
   Pure refactor, no behavior change; green under existing TcChat approval tests.
2. **Project the batch** (`approvalProjection.ts` fields + `numberField` +
   `groupApprovalsByBatch`). Pure; new unit tests. Nothing consumes it yet.
3. **Fix the dead seam + rich feed** (`RunWorkspaceRail` forward,
   `RunDestination` `approvals` prop, `ApprovalsTab` inline-resolve rewrite,
   `WorkspacePane` forward). This is the load-bearing commit — the tab becomes
   actionable.
4. **v3 polish** (empty-state copy, policy `sd-note` + `onChangePolicy`, amber
   badge FR-12, Focus `.fx-note` FR-13). Cosmetic + copy; safe last.

## Descopes & rationale

- **Per-human-signer rows** — the payout card's `.conf-row` per person
  ("Sarah / Marcus each sign an amount", with `r.who`/`r.role`/`r.ini`/`r.amount`
  and per-signer status) — **DESCOPE**. Evidence:
  `copilot-run-side.jsx:69-78`. There is **no signer roster** in the contract.
  The only real multi-item concept is the **tool-call batch** (`batch_id` /
  `batch_index`, FR-5/6) and forwarding chains (`chain_parent_approval_id`, one
  pending at a time). The batch card reuses the _visual_ row-per-item shape but
  binds each row to a real `batchIndex` approval, never a fabricated signer.

- **"Auto-approved today" `.ap-log`** (`copilot-run-side.jsx:97-102`) —
  **DESCOPE**. There is **zero** auto-approved signal across `ai-backend`,
  `api-types`, and `chat-surface`: policy-allowed tools simply never raise
  `approval_requested`, so nothing to project. The "Recent" section (FR-9) shows
  only genuinely _requested-then-resolved_ approvals from the stream. Wiring an
  auto-approve log would require a NEW backend event ("tool auto-approved by
  policy") — **NEW-CONTRACT**, out of scope; do not fake it.

- **`.ap-meta` preview counters** — "9 posts · draft completes ~11:46"
  (`copilot-run-side.jsx:83`) — **DESCOPE**. No contract field carries a
  post-count or an ETA for a pending action. The card shows `summary` (from
  `payload.message`/`summary`) and `params` (primitive `payload.arguments`)
  only — both real. A richer body preview + progress meta is **NEW-CONTRACT**.

- **`.sd-note` "change policy" link** — **NOT descoped, but host-gated.** The
  approval-policy settings section exists (`ApprovalPolicy`, main barrel
  `~:935`), so the link is backable via an `onChangePolicy?` host callback
  (FR-10). When no callback is supplied the note renders as plain text — never a
  dead link.

## Test plan

Unit (`vitest`, `packages/chat-surface`):

- **`approvalProjection.batch.test.ts`** — `approval_requested` with
  `batch_id`/`batch_index` projects them onto `RunApproval`; absent → defaults
  (`batchId = approvalId`, `batchIndex = 0`). Guards FR-5 regressions.
- **`groupApprovalsByBatch.test.ts`** — N approvals sharing one `batchId` →
  one `ApprovalBatch` with rows sorted by `batchIndex`; distinct batches stay
  separate and in request order; singleton batch has no "Approve all". Guards
  FR-6.
- **`ApprovalsTab.inline.test.tsx`** — (a) clicking Approve/Reject on a pending
  row calls `onApprove`/`onReject` with the row's `approvalId` (FR-2/FR-4);
  (b) "Approve all" loops `onApprove` over every unresolved resolvable item in a
  multi-row batch (FR-6); (c) an `ask_a_question` row renders a jump-only button,
  not Approve/Reject (FR-7); (d) with `onApprove`/`onReject` undefined every row
  is jump-only, no action buttons (NFR-5); (e) empty projection → the v3
  clear-state copy (FR-8); (f) a resolved approval renders `InlineApprovalReceipt`
  under "Recent" (FR-9).
- **`InlineApprovalCards.test.tsx`** — the extracted components render the same
  DOM + `data-testid`s the old TcChat renderers did (behavior-preservation lock
  for commit 1, FR-4/NFR-4).

Integration (`RunWorkspaceRail` / `RunDestination`):

- **`RunWorkspaceRail.approvals.test.tsx`** — an Approve click inside the
  Approvals panel invokes the rail's injected `onApprove` (regression lock for
  the dead seam, FR-1); the badge uses the warn/amber tone when pending > 0
  (FR-12).
- **`RunDestination.approvals.test.tsx`** — resolving from the tab flips the card
  to its receipt via optimistic `localDecisions` before the trailing
  `approval_resolved` frame (reuses `resolveApproval`, NFR-2); while `isScrubbed`
  the Approvals tab is absent and no resolve path is reachable (FR-11).

## Risks & gotchas

- **Import-cycle trap.** `approvalProjection.ts` imports from `../../workspace`
  (`ApprovalsQueueItem`, `:28-31`). If `ApprovalsTab` (in `workspace/`) imported
  `RunApproval` from `destinations/run/approvalProjection`, that plus the existing
  edge risks a cycle. Mitigation: the shared inline view-model (`InlineApproval`)
  lives in `approvals/`, which both `workspace/` and `destinations/run/` may
  import; `RunApproval` stays structurally assignable to it. Do **not** import
  `destinations/run/*` from `workspace/*`.
- **`ask_a_question` decision skew.** `decisionFromResolve` maps `answered` →
  `"approved"` (`approvalProjection.ts:311`). That is fine for the _receipt_ label
  but must not leak Approve/Reject _controls_ onto question rows — FR-7 filters on
  `approvalKind`, not on decision. Keep the filter at the render boundary.
- **Optimistic double-resolve.** "Approve all" loops `onApprove` per item;
  `resolveApproval` already dedupes by decision (`RunDestination.tsx:485-491`) and
  fires one POST per id. Ensure the loop skips rows already `resolved` (server or
  optimistic) so a partially-signed batch's "Approve all" only POSTs the
  remainder.
- **TcChat test coupling.** Existing tests assert `tc-chat-approval-*` /
  `tc-chat-conf-*` test ids. The extraction MUST keep them byte-identical, or
  update the tests in the same commit — do not let commit 1 go red.
- **Amber token availability.** Confirm a warn token exists in
  `packages/design-system/src/styles.css` (v2 quiet set) before using it for the
  badge + `.fx-note`; if only `--color-danger` (ember) exists, use the dedicated
  warn/amber token, not ember (ember = reject/destructive, which would misread).
- **Focus banner vs. conf-cards duplication.** FR-13's "holding N" banner sits
  above conf-cards that already say "the agent paused here" per card — keep the
  banner terse (count only) so it summarizes rather than repeats.
- **Standalone `WorkspacePane` host.** Its binder (`useApprovalsQueue`) currently
  produces only the thin queue, not `RunApproval[]`. Until that host is upgraded,
  it will pass no `approvals` and no `onApprove`/`onReject` → the tab correctly
  degrades to jump-only (NFR-5). Do not assume the rich array is always present.
