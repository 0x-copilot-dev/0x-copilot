# PRD-E2 — Approvals queue + Agents tab 🎨

Make everything the user still has to decide visible in **one queue**: the Run cockpit's
right-rail **Approvals tab** shows a card for every pending gate (C2), held draft (D1),
and staged row-set (D3) — across **all** of the user's runs — each with a preview and a
**Review →** action that flips the canvas to the right surface (navigating to the owning
run first when needed). A **pending counter chip** ("N waiting") tracks the merged total.
The rail's **Agents tab** becomes the fleet view: this run plus other runs with pending or
in-flight work, noting that held work from any agent lands in this one queue (FR-E5,
FR-E6, FR-B2). E2 emits **no new ledger events** — it is a read-side PR: one cross-run
endpoint plus pure projections, behind `SURFACES_V2` and the B1 client canvas flag.

## Implementer brief

You are implementing one PR in the `0x-copilot` monorepo. Work in a **fresh git worktree
branched off `main`**; never commit on `main`. Run `make setup` once if per-service
`.venv`s / `node_modules` are missing. Components touched: `services/ai-backend`
(pending-work fold + endpoint), `services/backend-facade` (one passthrough),
`packages/api-types` (response mirrors), `packages/chat-surface` (cards, chip, rail +
cockpit wiring), both host binders. Test commands (from repo root):

```bash
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/surfaces_v2/
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api/
cd services/ai-backend && .venv/bin/python -m pytest        # full suite before PR
cd services/backend-facade && .venv/bin/python -m pytest
npm run test --workspace @0x-copilot/chat-surface && npm run typecheck --workspace @0x-copilot/chat-surface && npm run lint --workspace @0x-copilot/chat-surface
npm run test --workspace @0x-copilot/api-types && npm run typecheck --workspace @0x-copilot/api-types
node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs   # UI DoD
```

Read these files first (repo-relative; one line each on why):

1. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 event vocabulary (authoritative, verbatim), §3 Projections row, §6 replay rules.
2. `docs/plan/generative-surfaces-v2/01-problem-and-requirements.md` — FR-E5/E6, FR-B2, FR-F3, NFR-7 (the contract).
3. `packages/chat-surface/src/destinations/run/RunWorkspaceRail.tsx` — the EXISTING right rail (`RunRailTabId = "chat"|"agents"|"approvals"|"sources"`); E2 extends it, never forks it.
4. `packages/chat-surface/src/workspace/types.ts` — `ApprovalsQueueItem`/`ApprovalsQueueProjection` (v1 shapes that keep working flag-off) and `ApprovalsTab.tsx`/`AgentsTab.tsx` (the tab bodies).
5. `packages/chat-surface/src/destinations/run/approvalProjection.ts` — `projectApprovals`/`toApprovalsQueue`: the pure-selector pattern your v2 card selector copies (one-projector invariant, SDR §1).
6. `packages/chat-surface/src/destinations/run/RunDestination.tsx` — rail construction ~L1089–1102 (`<RunWorkspaceRail approvalsQueue=…>` fed by `toApprovalsQueue`) and `rightRail` threading into `ThreadCanvas` ~L1249; your wiring lands beside it.
7. `services/ai-backend/src/runtime_api/http/routes.py` — `list_approvals` (L392) + `stream_inbox` (L429): the existing cross-run approval read paths; copy their `scoped_identity` discipline.
8. `services/ai-backend/src/agent_runtime/api/approval_coordinator.py` — `list_assigned_approvals` (L106): read-ACL + clamped paging discipline to mirror.
9. `services/ai-backend/src/agent_runtime/api/ports.py` — `EventStorePort` (L1001, `list_events_after`), `PersistencePort` (`list_conversations` L115, `get_active_run_for_conversation` L192, `list_runs_for_conversation` L510).
10. The merged PRD-A1/A3/B1/C2/D1(/D3) code — contracts, `SURFACES_V2` accessor, ts ledger projector, gate events, `StagedWriteFold`. Locate them before writing code (Interfaces below).
11. `services/ai-backend/CLAUDE.md`, `services/ai-backend/tests/CLAUDE.md`, `packages/chat-surface/CLAUDE.md`, `tools/design-parity/SKILL.md` — binding rules + parity pipeline.

## Context

Generative Surfaces v2 renders an agent's work on real SaaS tools as live artifact
surfaces on a per-run canvas; reads flow, writes stage and are decided on the artifact;
tool access gates park runs only when auth is actually unusable; everything threads
through a typed, append-only **Work Ledger** on the existing per-run runtime event log,
with all user-visible state defined as projections of it (../02-sdr.md §2–§3, §5). The
requirements contract is ../01-problem-and-requirements.md — this PR satisfies FR-E5
(Approvals queue as separate, lazily-accumulating cards with jump-to-surface), FR-E6
(fleet view; held work from any agent in one queue), FR-B2 (gates appear lazily as
discovered), FR-F3 ("N waiting" counter), NFR-7 (pending decisions accumulate visibly
rather than blocking or getting lost).

E2 sits in Wave E (../03-prds.md). Everything it shows already exists as ledger events:
C2 ships `gate.opened`/`gate.resolved`, D1 ships `write.staged`/`revision.added`/
`decision.recorded` (single artifact), D2 ships `write.applied`, D3 adds row-sets
(`rows`, `agent_holds`, row-scoped decisions). E2 adds zero events and zero writes — it
is the queue **read model**: a server-side cross-run fold + endpoint (because the client
only streams the open run), a client-side pure selector for the open run (live via SSE),
compact queue cards, the counter chip, and the fleet list. The existing v1 rail content
(`toApprovalsQueue` over v1 approval events, subagent fleet in `AgentsTab`) keeps
working byte-identically when flags are off, per SDR §11.

## Interfaces consumed / exposed

**Consumed (must exist on the base branch — reconcile exact names against merged code
before writing yours):**

- PRD-A1: event-type constants for the SDR §5 names, payload types, the ledger-id
  formatter producing `r<short>·<seq>`, the golden ledger-events fixture.
  `VERIFY AT IMPL:` exact exported symbol names (grep `packages/api-types/src` and
  `services/ai-backend` for `gate.opened` / `write.staged`).
- PRD-A3: the v2 domain package (expected
  `services/ai-backend/src/agent_runtime/surfaces_v2/`), the `SURFACES_V2` flag accessor
  (config class mirroring `capabilities/surfaces/config.py::SurfaceEmissionFlag`), the
  `RuntimeApiEventType` v2 members. `VERIFY AT IMPL:` package path + accessor name.
- PRD-B1 (`packages/chat-surface`): the client ledger fold
  (`src/thread-canvas/ledgerProjection.ts` — `projectLedger`, `LedgerProjection`,
  `tabUriForSurface`), the `surfacesV2` prop on `RunDestinationProps`, host flag helpers
  `isSurfacesV2CanvasEnabled()` (web `apps/frontend/src/app/featureFlags.ts`) /
  `isSurfacesV2Enabled()` (desktop `apps/desktop/renderer/featureFlags.ts`).
- PRD-C2: `gate.opened`/`gate.resolved` payloads (`gate_id`, `connector`, `purpose`,
  `scopes[]`, `auth_state`; `outcome`, `write_policy?`); the projector's gate fold +
  how gate cards mount on the canvas. `VERIFY AT IMPL:` whether C2 folded gates into
  `LedgerProjection` (expected `gates: ReadonlyMap<gateId, …>`) and whether the canvas
  gate card is a tab or an overlay — E2's same-run "Review" must target whichever it is.
- PRD-D1 (and D3 if merged): `StagedWriteFold.fold(events) -> dict[str, StagedWriteState]`
  - `StagedWriteStatus` in `services/ai-backend/src/agent_runtime/surfaces_v2/staging.py`;
    the client-side stage fold B1's projector gained in D1; `GET /v1/agent/stages/{id}`.
    `VERIFY AT IMPL:` if D3 is merged, `StagedWriteState` carries row decisions — reuse its
    pending-row accounting; if not, single-artifact only (rows fields stay `None`).
- Existing (verified in this worktree): `RunWorkspaceRail` + `RunRailTabId`
  (`packages/chat-surface/src/destinations/run/RunWorkspaceRail.tsx`), `ApprovalsTab` /
  `AgentsTab` / `WorkspaceTabs` + `ApprovalsQueueItem`/`ApprovalsQueueProjection`
  (`packages/chat-surface/src/workspace/`), `projectApprovals`/`toApprovalsQueue`
  (`destinations/run/approvalProjection.ts`), `RunDestination` rail wiring (~L1089),
  `EventStorePort.list_events_after`, `PersistencePort.list_conversations` /
  `get_active_run_for_conversation` / `list_runs_for_conversation`,
  `GET /v1/agent/approvals?assigned_to_me=true` (v1 inbox — untouched), facade proxy
  pattern (`services/backend-facade/src/backend_facade/app.py` ~L1131), `Transport`
  port + `useTransport`.

**Exposed (later PRDs / hosts rely on these — keep names stable):**

- `GET /v1/agent/pending-work` (runtime_api + facade passthrough) — E3's live E2E
  drives it; the future scheduled-agents work extends its `agents` list.
- `PendingWorkFold` (py) — E3's audit-hardening property tests re-fold with it.
- `projectPendingCards` + `PendingCard` (chat-surface) — Phase-2 failure-path designs
  restyle these cards; E3's cutover deletes the v1 queue path around them.
- `PendingCounterChip`, `PendingCardList`, `usePendingWork` barrel exports; the
  `pendingV2` prop on `RunWorkspaceRailProps`; the `focusSurfaceId` seam on
  `RunDestinationProps`; the `onOpenRun` host callback.

## Design

### 1. Events consumed (SDR §5 verbatim — E2 emits none)

```text
gate.opened        {gate_id, connector, purpose, scopes[], auth_state: missing|expired|insufficient}
gate.resolved      {gate_id, outcome: connected|cancelled, write_policy?: ask_first|allow_always}
write.staged       {stage_id, surface_id, target{connector,op}, proposal_ref, rows?: n, agent_holds: [{row_key, reason}]}
revision.added     {stage_id, rev, author: agent|user, diff_ref}
decision.recorded  {stage_id, decision: approve|reject|hold|restore, scope: {rev}|{row_keys[]}, actor: user|policy}
write.applied      {stage_id, rev, row_keys?, result: applied|partial|failed, connector_receipt_ref}
```

**Pending predicate (one definition, both languages, tested against the same golden
fixture):**

- A **gate** is pending iff a `gate.opened` has no later `gate.resolved` with the same
  `gate_id`.
- A **single-artifact stage** is pending iff its folded status is `STAGED` (D1
  vocabulary): approved (awaiting D2 apply), rejected, and applied stages are not
  waiting on the user. `restore` returns a rejected stage to pending.
- A **row-set stage** (D3) is pending iff any row is undecided; `rows_pending` = rows
  with no row-scoped decision and no `write.applied` covering them (reuse D3's
  accounting — do not re-derive; `VERIFY AT IMPL` against the merged fold).

### 2. Server — pending-work fold + service (ai-backend)

New file `services/ai-backend/src/agent_runtime/surfaces_v2/pending_work.py` (helpers
inside classes; keys via `Keys`-style constant classes per service CLAUDE.md):

```python
class PendingItemKind(StrEnum):
    GATE = "gate"; STAGED_WRITE = "staged_write"

class PendingWorkItem(RuntimeContract):
    v: Literal[1] = 1
    item_kind: PendingItemKind
    run_id: str
    conversation_id: str
    conversation_title: str | None    # ConversationRecord.title (str | None)
    gate_id: str | None = None        # GATE only
    stage_id: str | None = None       # STAGED_WRITE only
    surface_id: str | None = None     # STAGED_WRITE only (canvas jump target)
    title: str                        # gate: purpose line; stage: draft/target title
    connector: str
    op: str | None = None
    ledger_id: str                    # r<short>·<seq> of the opening event (A1 formatter)
    opened_sequence_no: PositiveInt
    opened_at: datetime
    rows_pending: int | None = None   # row-sets only
    rows_total: int | None = None

class PendingAgentRow(RuntimeContract):
    v: Literal[1] = 1
    run_id: str
    conversation_id: str
    conversation_title: str | None
    run_status: str                   # AgentRunStatus value, presentation-ready
    pending_count: int                # this run's items in the queue

class PendingWorkFold:
    """Pure: one run's ledger events -> pending items. No IO, no clock."""
    @classmethod
    def fold(cls, events: Sequence[RuntimeEventEnvelope]) -> tuple[PendingWorkItem, ...]:
        ...  # composes StagedWriteFold (D1/D3) + the gate pairing above; malformed
             # payloads skipped, never raised; deterministic order by opened_sequence_no

class PendingWorkService:
    def __init__(self, *, persistence: PersistencePort, event_store: EventStorePort): ...
    async def list_pending(self, *, org_id: str, user_id: str) -> PendingWorkResponse:
        ...
```

**Field derivation (the fold is pure — every field comes from the ledger events
themselves; `*_ref` values are NEVER dereferenced here, that would be IO):**

- `opened_at` ← the opening envelope's `created_at` (`RuntimeEventEnvelope.created_at:
datetime`, events.py L1122); `opened_sequence_no` ← its `sequence_no`
  (`RuntimeEventEnvelope.sequence_no: PositiveInt`, events.py L1120); `ledger_id` ←
  A1 formatter over `(run_id, sequence_no)`. "Opening event" = `gate.opened` for a gate,
  `write.staged` for a stage.
- gate: `title` ← `gate.opened.purpose`; `connector` ← `gate.opened.connector`;
  `op` stays `None`.
- stage: `connector`/`op` ← `write.staged.target{connector, op}`; `title` ←
  `StagedWriteState`'s title if D1's fold exposes one (`VERIFY AT IMPL:` grep the merged
  `staging.py` for a title/label field), else fall back to the human `target` line
  (e.g. `"{connector} · {op}"`). Never resolve `proposal_ref` to a payload — the fold
  has no store handle.
- `surface_id` ← `write.staged.surface_id` (stage only; gate has none → `None`).

`list_pending` algorithm (fold-on-read; no new table, no migration — NFR-11 solo
posture makes O(runs) folds acceptable, and the ledger stays the only truth, which is
exactly the DoD's "cards match ledger state"):

1. Candidate runs: `persistence.list_conversations(org_id=…, user_id=…, limit=CAP_CONVERSATIONS)`
   (keyword-only signature confirmed at ports.py L115:
   `list_conversations(*, org_id, user_id, limit, include_archived=False, include_deleted=False)`),
   then per conversation
   `list_runs_for_conversation(org_id=…, conversation_id=…, limit=CAP_RUNS_PER_CONVERSATION)`
   (keyword-only, ports.py L510). Caps
   (`30`/`5`) live in a `Values` constant class; older pending work is outside the v0
   window.
2. Ownership: only runs belonging to `user_id` — both `RunRecord` (runs.py L341) and
   `ConversationRecord` (conversations.py L114) carry a `user_id` field, so scope at
   either step; `list_conversations` already filters by `user_id`.
   Cross-tenant impossible: every read is `org_id`-scoped.
3. Per run: `event_store.list_events_after(org_id=…, run_id=…, after_sequence=0)` (keyword-only, ports.py L1035) →
   `PendingWorkFold.fold(events)`; runs with no v2 event types short-circuit to zero.
4. Fleet: per candidate conversation, `get_active_run_for_conversation` — every
   non-terminal run becomes a `PendingAgentRow`, plus any run that contributed pending
   items even if terminal (a completed run with a held stage still has work waiting).
5. Sort: items newest-first by `(opened_at, opened_sequence_no)`; agents running-first
   then newest.

```python
class PendingWorkResponse(RuntimeContract):
    v: Literal[1] = 1
    items: tuple[PendingWorkItem, ...] = ()
    agents: tuple[PendingAgentRow, ...] = ()
```

### 3. Server — route + facade

- New `services/ai-backend/src/runtime_api/http/pending_work.py`:
  `register_pending_work_routes(router)` adding
  `GET /v1/agent/pending-work` → `PendingWorkResponse`, `Depends(RequireScopes(RUNTIME_USE))`,
  identity via the same `scoped_identity` pattern as `list_approvals` (routes.py L392).
  Registered from `RuntimeApiRouter.create_router` **only when `SURFACES_V2` is on**
  (D1's stage-routes pattern: flag off ⇒ route absent ⇒ 404 ⇒ byte-identical).
- Schemas live with the domain models above; the route module holds no logic.
- Facade: one verbatim passthrough `GET /v1/agent/pending-work` in
  `services/backend-facade/src/backend_facade/app.py` beside the approvals proxy
  (~L1131). Pure proxy, no logic.
- api-types: additive mirrors `PendingWorkItem`, `PendingAgentRow`,
  `PendingWorkResponse` + guard `isPendingWorkResponse` in
  `packages/api-types/src/index.ts` (new delimited block; snake_case fields exactly as
  the wire).
- Errors: 401/403 via existing scope machinery; a per-run fold failure is logged and
  that run skipped (queue degrades, never 500s on one bad run); empty state is
  `{v:1, items: [], agents: []}`, never 404 when the flag is on.

### 4. Client — open-run selector + cross-run hook (chat-surface)

- **`projectPendingCards`** — NEW pure selector
  `packages/chat-surface/src/destinations/run/pendingCardsProjection.ts`, peer of
  `projectApprovals` over the SAME `session.events` array (one-projector invariant, SDR §1 — never a second SSE
  subscription):

```ts
export interface PendingCard {
  readonly itemKind: "gate" | "staged_write";
  readonly runId: string; // current run for this selector
  readonly gateId: string | null;
  readonly stageId: string | null;
  readonly surfaceId: string | null;
  readonly title: string;
  readonly connector: string;
  readonly ledgerId: string; // r<short>·<seq> (A1 formatter)
  readonly openedSeq: number;
  readonly rowsPending: number | null;
  readonly rowsTotal: number | null;
}
export function projectPendingCards(
  events: readonly RuntimeEventEnvelope[],
  runId: string | null,
): readonly PendingCard[];
```

Implements the §1 pending predicate exactly (reuse B1/C2/D1's client fold state —
`VERIFY AT IMPL:` extend `projectLedger`'s output rather than re-folding if C2/D1
already track gates/stages there). Malformed payloads skipped; hostile strings are
data (rendered as text only). `title`/`connector`/`openedSeq`/`ledgerId` derive from
the SAME in-event fields as the py fold (§2 "Field derivation") so the ts ⇄ py
pending-parity test holds — the client also never dereferences `proposal_ref`.

- **`usePendingWork`** — NEW hook
  `packages/chat-surface/src/destinations/run/usePendingWork.ts`:

```ts
export interface UsePendingWorkResult {
  readonly cards: readonly PendingCard[]; // merged: live open-run + fetched others
  readonly agents: readonly PendingAgentRow[];
  readonly status: "idle" | "loading" | "ready" | "error";
  readonly refresh: () => void;
}
export function usePendingWork(
  transport: Transport,
  enabled: boolean, // surfacesV2 flag
  currentRunId: string | null,
  liveCards: readonly PendingCard[], // projectPendingCards output
  refreshKey: number, // open run's last ledger seq — bump ⇒ refetch
): UsePendingWorkResult;
```

Behavior: when `enabled`, GET `/v1/agent/pending-work` via
`transport.request` on mount, on `refreshKey` advance (coalesced: one in flight, one
queued), and on `refresh()` (wired to Approvals-tab activation). Merge: fetched items
for `run_id === currentRunId` are **replaced** by `liveCards` (SSE is fresher);
dedupe key `runId + (gateId ?? stageId)`. Errors fail soft (`status:"error"`, keep
last data, no retry storm, never throw into React). No timers, no polling in v0 —
refresh points are mount / open-run activity / tab activation / `refresh()`.
Cross-run push is an E3 concern (inbox stream carries only v1 approvals today).

### 5. Client — cards, chip, rail + cockpit wiring 🎨

- **`PendingCardList`** — NEW `packages/chat-surface/src/workspace/PendingCardList.tsx`.
  Pure presentational: `{cards, onReview(card), emptyCopy?}`. One compact card per item
  (FR-E5 "separate cards"): kind eyebrow (`.ui-eyebrow` — "GATE" / "HELD DRAFT" /
  "STAGED CHANGES"), title, connector `.ui-badge`, row-count pill for row-sets
  ("5 of 8 waiting"), ledger-id chip (`.ui-mono-caps`), `.ui-button--sm` **Review →**.
  Kit recipes only; titles render as text nodes — never `dangerouslySetInnerHTML`.
- **`PendingCounterChip`** — NEW
  `packages/chat-surface/src/destinations/run/PendingCounterChip.tsx`: `.ui-pill`
  "N waiting"; hidden at N=0; `onClick` opens the Approvals rail tab. Mounts beside
  C2's `PostureChip` (`VERIFY AT IMPL:` its mount point in `RunDestination`).
- **`AgentFleetList`** — NEW `packages/chat-surface/src/workspace/AgentFleetList.tsx`:
  `{agents, currentRunId, onOpenRun(agent)}` — one row per `PendingAgentRow`
  (`StatusPill` status, conversation title, "N waiting" when > 0, "This run" marker) +
  static footer note "Held work from any agent lands in Approvals." A
  `scheduledSlot?: ReactNode` prop reserves the scheduled section. A routines scheduler
  DOES exist server-side (`services/ai-backend/src/runtime_worker/jobs/routine_scheduler.py`,
  facade `routines_routes`), but it is NOT a `runtime_api` pending-work source and E2
  does not fold it — so hosts pass nothing here and no section renders (never a stub
  list). Wiring a scheduled-agents feed is out of scope (see Out of scope).
- **Rail wiring** — `RunWorkspaceRailProps` gains one optional prop (flag-off ⇒ absent
  ⇒ byte-identical rail):

```ts
readonly pendingV2?: {
  readonly cards: readonly PendingCard[];
  readonly agents: readonly PendingAgentRow[];
  readonly onReview: (card: PendingCard) => void;
  readonly onOpenRun: (agent: PendingAgentRow) => void;
};
```

When present: the Approvals panel renders `<PendingCardList>` above the existing v1
`ApprovalsTab` content, the approvals badge count adds `cards.length`, and the Agents
panel renders `<AgentFleetList>` above the existing subagent `AgentsTab` content
(subagents stay — they are this run's fleet detail). Note: this file is the **rail**
Agents tab (`src/workspace/AgentsTab.tsx` composition) — do NOT touch
`src/destinations/agents/AgentsDestination.tsx` (a different, legacy destination).

- **Cockpit wiring** — `RunDestination.tsx`: memoize
  `projectPendingCards(session.events, session.runId)` (the current run id from
  `useRunSession`) beside the existing `approvalsQueue`
  memo (~L791); call `usePendingWork` (enabled = `surfacesV2`); pass `pendingV2` into
  `<RunWorkspaceRail>` (~L1090) only when `surfacesV2`. **Review routing:**
  - same run, stage card → activate the surface tab for `surfaceId`. `activeUri` is a
    DERIVED const (scrub → pin → follow-diff → newest, RunDestination ~L935–950), so
    there is no `activeUri` setter — activate a specific tab by calling
    `setPinnedUri(uri)` (the `[pinnedUri, setPinnedUri]` state at ~L448; existing
    open-tab handler at ~L462), which flows into `activeUri` via `effectivePin`. Map
    `surfaceId → uri` with B1 `tabUriForSurface` (`VERIFY AT IMPL:` B1's `tabUriForSurface`
    export, not yet merged);
  - same run, gate card → focus the canvas gate card per C2's mount (see consumed
    VERIFY);
  - other run → call new host prop
    `onOpenRun?: (target: {runId, conversationId, surfaceId: string | null}) => void`
    on `RunDestinationProps`.

  Naming note — `onOpenRun` exists at two layers with two signatures, do not conflate:
  the rail/fleet callback is `onOpenRun(agent: PendingAgentRow)` (carries the domain row
  the user clicked); `RunDestination` handles it by resolving the row into the flat
  target shape and calling the **host** prop `onOpenRun(target: {runId, conversationId,
surfaceId})`. The rail prop and the `AgentFleetList` prop are the same domain-row
  callback; only `RunDestinationProps.onOpenRun` takes the flat target.

- **Focus seam** — `RunDestinationProps` gains
  `focusSurfaceId?: string | null`: when it transitions to a value matching a known v2
  tab, activate that tab once (mirrors the existing `WorkspacePaneOpenOptions.focus*`
  one-shot pattern in `src/workspace/types.ts`).
- **Hosts (update BOTH):** web `apps/frontend/src/features/run/RunRoute.tsx` — `RunRoute`
  itself does not read the URL; the app router owns conversation selection as
  `/run/<conversationId>`, reached via `App.tsx`'s `openConversation(id)` (~L821, navigates
  with `subPath: conversationId`), and `RunRoute` binds from its `conversationId` prop.
  Wire `onOpenRun` through that host navigation for the target conversation, then re-enter
  with `focusSurfaceId` (adding a surface query param on top of the `/run/<id>` slug is new
  — `VERIFY AT IMPL:` the exact param encoding); desktop
  `apps/desktop/renderer/destinationBinders.tsx` `RunBinder` — the shell nav passes
  `conversationId` into `RunBinder` and the outlet re-keys/remounts it on change (see
  `RunBinder`'s `conversationId`/`onConversationCreated` props, L594); route `onOpenRun`
  through the same nav, then pass `focusSurfaceId`. Both pass through only when their B1
  flag helper is on.
- Barrel: export `PendingCardList`, `AgentFleetList`, `PendingCounterChip`,
  `projectPendingCards`, `usePendingWork`, `PendingCard` types from
  `packages/chat-surface/src/index.ts` in a new delimited `// === Surfaces v2 (PRD-E2)`
  block.

### 6. Flags & error behavior

- Runtime `SURFACES_V2` off ⇒ `/v1/agent/pending-work` unregistered (404); no fold
  runs. Client `surfacesV2` off ⇒ `pendingV2` never constructed, zero new requests,
  rail/chip byte-identical (existing rail tests stay green unmodified).
- Endpoint error ⇒ chip shows the live open-run count only; queue shows live cards +
  a quiet retry affordance (no modal, no toast storm).
- Scrub mode: like the v1 queue (`approvalsQueue` is `undefined` while scrubbed,
  RunDestination ~L791), `pendingV2` is withheld while `isScrubbed` — historical
  browsing never shows actionable cards.

## Implementation plan

1. **Server fold.** Create
   `services/ai-backend/src/agent_runtime/surfaces_v2/pending_work.py`
   (`PendingWorkFold`, `PendingWorkItem`, `PendingAgentRow`, `PendingWorkResponse`,
   `PendingWorkService`), composing D1's `StagedWriteFold`.
2. **Route + wiring.** Create `services/ai-backend/src/runtime_api/http/pending_work.py`;
   register flag-gated in `services/ai-backend/src/runtime_api/http/routes.py`
   (`create_router`); construct `PendingWorkService` where A3's services are wired in
   `services/ai-backend/src/runtime_api/app.py` (`VERIFY AT IMPL:` A3's construction
   site).
3. **Facade.** Add the passthrough in
   `services/backend-facade/src/backend_facade/app.py`.
4. **Contracts.** Add TS mirrors + guard to `packages/api-types/src/index.ts`.
5. **Client selector + hook.** Create
   `packages/chat-surface/src/destinations/run/pendingCardsProjection.ts` and
   `usePendingWork.ts`.
6. **Components.** Create `packages/chat-surface/src/workspace/PendingCardList.tsx`,
   `packages/chat-surface/src/workspace/AgentFleetList.tsx`,
   `packages/chat-surface/src/destinations/run/PendingCounterChip.tsx`; extend
   `packages/chat-surface/src/destinations/run/RunWorkspaceRail.tsx` (`pendingV2`
   prop); extend `packages/chat-surface/src/destinations/run/RunDestination.tsx`
   (memo, hook, Review routing, `focusSurfaceId`, chip mount); barrel exports in
   `packages/chat-surface/src/index.ts`.
7. **Hosts.** Wire `onOpenRun`/`focusSurfaceId` in
   `apps/frontend/src/features/run/RunRoute.tsx` and
   `apps/desktop/renderer/destinationBinders.tsx`.
8. **Parity + smoke + docs.** Vendor the mock's Approvals-rail region into
   `tools/design-parity/surfaces/v2-approvals-rail/`; run the pipeline to 0 HIGH; run
   the live smoke; add `/v1/agent/pending-work` to SDR §4's endpoint list (it is new —
   the standard-DoD docs item).

## Test plan

ai-backend (`cd services/ai-backend && .venv/bin/python -m pytest <file>`; fakes/mixins
per tests/CLAUDE.md — no network, no live LLM; assert typed errors + safe messages):

- `tests/unit/agent_runtime/surfaces_v2/test_pending_work_fold.py`
  - `test_open_gate_pending_resolved_gate_absent` (both `connected` and `cancelled`)
  - `test_staged_stage_pending_approved_rejected_applied_absent`
  - `test_restore_returns_rejected_stage_to_pending`
  - `test_row_set_pending_counts_partial_decisions` (D3 shapes; skip-with-reason if D3
    unmerged)
  - `test_fold_of_golden_events_matches_checked_in_expectation` (A1 fixture)
  - **`test_every_event_prefix_matches_incremental_state`** — for each prefix of the
    golden sequence the fold equals the expected pending set (the DoD "cards
    appear/disappear exactly with ledger state" property, server side)
  - adversarial: `test_malformed_payloads_skipped_never_raise`,
    `test_interleaved_non_v2_events_tolerated`,
    `test_gate_resolved_without_opened_ignored`
- `tests/unit/runtime_api/test_pending_work_route.py`
  - `test_flag_off_route_absent_404`
  - `test_two_runs_items_aggregate_into_one_response` (two conversations, one parked
    gate + one held stage)
  - `test_foreign_user_runs_excluded` / `test_foreign_org_never_visible`
  - `test_one_bad_run_fold_skipped_response_still_200`
  - `test_agents_rows_running_first_and_pending_counts_match_items`
  - `test_caps_bound_candidate_scan`
- Facade: one passthrough test following the existing `test_<feature>_proxy.py`
  convention in `services/backend-facade/tests/` (e.g. `test_approval_decision_proxy.py`,
  `test_inbox_proxy.py`) — add `test_pending_work_proxy.py`.

chat-surface (`npm run test --workspace @0x-copilot/chat-surface`):

- `packages/chat-surface/src/destinations/run/pendingCardsProjection.test.ts` — same
  case list as the py fold **including the prefix property test** against the shared
  golden fixture (ts ⇄ py pending-parity; the DoD projection test, client side);
  hostile title strings survive as plain text.
- `packages/chat-surface/src/destinations/run/usePendingWork.test.ts` — disabled ⇒ zero
  requests; refetch on refreshKey advance coalesces; open-run items replaced by live
  cards (dedupe by run+id); error keeps last data, `status:"error"`, no throw.
- `packages/chat-surface/src/workspace/PendingCardList.test.tsx` — one card per item;
  kind labels; row counts; ledger-id chip; Review fires with the card.
- `packages/chat-surface/src/workspace/AgentFleetList.test.tsx` — running-first, "This
  run" marker, held-work note, no scheduled section when slot absent.
- `packages/chat-surface/src/destinations/run/PendingCounterChip.test.tsx` — hidden at
  0; "N waiting"; click opens the tab.
- `packages/chat-surface/src/destinations/run/RunDestination.test.tsx` (extend) —
  `surfacesV2` off: no `pendingV2`, zero pending-work requests, all pre-existing rail
  assertions green **unmodified** (byte-identity proof); on: same-run stage Review
  activates the surface tab; other-run Review calls `onOpenRun` with
  `{runId, conversationId, surfaceId}`; `focusSurfaceId` activates once; scrubbed ⇒
  cards withheld.

**Live smoke (desktop stack, DoD item 2):**

1. `SURFACES_V2=true RUNTIME_START_IN_PROCESS_WORKER=true` in
   `services/ai-backend/.env`; `make dev`; `export TOKEN=$(make dev-bearer)`; enable
   the client flag (`localStorage.setItem("enterprise.flags.surfaces-v2","true")`,
   reload).
2. Conversation A: start a run against a connector with unusable auth (C2 smoke step 3
   recipe) → run parks on a gate.
3. Conversation B: start a run that stages a draft (`POST /v1/agent/drafts/{id}/send`,
   D1 smoke recipe) → held draft, run continues/completes.
4. With B's run open: Approvals tab shows **both** cards (A's gate + B's draft); chip
   reads "2 waiting"; `curl -H "Authorization: Bearer $TOKEN"
http://127.0.0.1:8200/v1/agent/pending-work` shows both items + both agent rows.
5. Review on B's draft card → canvas flips to the staged-draft surface. Review on A's
   gate card → navigates to A's run, gate card focused (host `onOpenRun` path).
6. Resolve A's gate (connect) and approve B's draft → both cards disappear on refresh
   of the queue; chip hides; Agents tab drops the pending counts.
7. Flag-off rerun: rail and chip absent-of-v2 content, `/v1/agent/pending-work` 404s.

## Definition of done

From ../03-prds.md PRD-E2 (binding minimums, never weakened):

- [ ] **Cards appear/disappear exactly with ledger state (projection test).** Proof:
      `test_every_event_prefix_matches_incremental_state` (py) + the matching prefix
      property case in `pendingCardsProjection.test.ts` (ts), both over the shared A1
      golden fixture.
- [ ] **Live: two concurrent runs' held writes both land in one queue; Review jumps to
      the right run's surface.** Proof: smoke steps 2–5 transcript + the
      `/v1/agent/pending-work` curl output attached to the PR body.

Standard DoD (every PRD):

- [ ] Unit tests pass in ai-backend + facade venvs and chat-surface + api-types
      workspaces; typecheck + build green; full ai-backend suite green.
- [ ] Flags off ⇒ byte-identical behavior. Proof: `test_flag_off_route_absent_404` +
      RunDestination flag-off case with pre-existing rail assertions unmodified.
- [ ] No service-boundary violations (apps→facade only; no cross-`src/` imports;
      chat-surface eslint clean; all HTTP via the Transport port).
- [ ] New LLM call sites: **none** (E2 is read-side; any would need the A2 seam).
- [ ] Docs: SDR §4 gains `GET /v1/agent/pending-work`; §3 Projections row updated if
      the fold's shape diverged.

UI DoD (🎨):

- [ ] Built from design-system/chat-surface kit components (`.ui-eyebrow`, `.ui-badge`,
      `.ui-pill`, `.ui-button--sm`, `StatusPill`); no host-app one-off styling; no raw
      font-size/letter-spacing.
- [ ] `tools/design-parity/` run vs the staged v2 mock's Approvals-rail region:
      **0 HIGH drift**. Artifact:
      `tools/design-parity/surfaces/v2-approvals-rail/out/report.md` checked in.
- [ ] Live desktop smoke of the flow on the real stack (script above), not just tests.

## Out of scope

- Any new ledger event, any write path, any decision endpoint — deciding still happens
  on the surface (D1/D2/D3 routes) or the gate (C2); the queue only routes attention.
- Receipt + Sources rendering (E1); audit hashing, `/v1/usage/*`, v1 retirement (E3).
- Cross-run **push** updates (inbox-stream extension) — E2 refreshes on open-run
  activity/tab activation; live push is an E3-or-later follow-up.
- A scheduled-agents backend; the fleet's scheduled section is a reserved slot only.
- Pagination/virtualization of the queue beyond the service caps; multi-user/team
  assignment semantics (NFR-11 solo posture); failure-path visual polish (Phase-2
  designer track).
- Modifying the v1 approvals queue (`projectApprovals`/`toApprovalsQueue`/
  `ApprovalsTab` internals) or the legacy `destinations/agents/` destination.

## Guardrails

- **Service boundaries (hard):** apps call `backend-facade:8200` `/v1/*` only — never
  `:8000`/`:8100`; the facade passthrough carries no logic; no deployable component
  imports another's `src/`; no sibling `PYTHONPATH`; contracts move only via
  `packages/api-types` / `packages/service-contracts`.
- **Flag-off byte-identical (SDR §11):** with `SURFACES_V2` off the route does not
  exist; with the client flag off the rail renders today's bytes and issues zero new
  requests — pre-existing tests unmodified are the proof, not review promises.
- **One projector invariant (SDR §1):** `projectPendingCards` is a pure selector over
  the same `session.events` array; `usePendingWork` is the only new fetch and goes
  through the `Transport` port; a second SSE subscription anywhere is a defect.
- **ai-backend rules** (`services/ai-backend/CLAUDE.md`): pydantic at every boundary;
  helpers inside classes; repeated keys/messages in `Keys`/`Messages`/`Values`
  constant classes; typed domain errors with safe public messages; event payloads are
  untrusted input to the fold — validate, skip, never trust titles/purposes.
- **ai-backend tests** (`tests/CLAUDE.md`): fakes/mixins, never network or live LLMs;
  concrete test classes contain only `test_*` methods; cover permission-denied and
  malformed-input paths.
- **chat-surface** (`packages/chat-surface/CLAUDE.md`): substrate-agnostic — no
  `window`/`document`/`fetch`/`localStorage`/`EventSource` (eslint-enforced); pure
  presentational components + host binders (update BOTH hosts); barrel exports only;
  hosts never deep-import `src/…`.
- **Ledger hygiene:** the queue is a projection — it never writes, never re-emits,
  never "fixes" state; disagreement between queue and canvas means a fold bug, and the
  shared golden fixture is the referee. Ledger ids shown are `r<short>·<seq>` via the
  A1 formatter — never a new id scheme.

## Open questions

1. **Where is the v2 design mock's Approvals-rail region?** The UI DoD and Implementation
   plan step 8 require vendoring "the mock's Approvals-rail region" into
   `tools/design-parity/surfaces/v2-approvals-rail/` and running the parity pipeline to
   0 HIGH. SDR §9 names the source `Generative Surfaces v2.dc.html` as "already mirrored
   locally", but no such file (nor any `*.dc.html`) is checked into this repo, and neither
   this PRD nor the linked docs give a path. The existing `tools/design-parity/surfaces/`
   baselines (`run-empty`, `rail-badge`, `login`, …) show the vendor pattern, but the v2
   mock itself must be supplied before this DoD item can be met. Needs: the mock file (or
   its export) committed / pointed at. This is a program-level dependency shared by every
   🎨 PRD in Waves B–E, not unique to E2 — resolve once, at the wave level. (The DoD item
   stands as written; this only flags the missing input.)
