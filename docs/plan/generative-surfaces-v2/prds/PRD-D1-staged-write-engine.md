# PRD-D1 — Staged-write engine: single artifact 🎨

Build the unified write-staging model for Generative Surfaces v2, first shape: **one
artifact (a draft)**. A write proposal becomes a staged surface with numbered revisions;
the user free-form edits the full body (server-side diff produces "edited by you"
authorship spans); an approve bar pins the exact revision ("Exactly this draft — rev N —
is what sends."); approve/reject/restore are typed ledger decisions. Nothing executes:
`write.applied` is emitted by nobody in this PR (that is PRD-D2's CommitEngine), and an
adversarial suite proves no sequence of API calls or events can produce it. The v1
draft-approval flow keeps working byte-identically with the flag off.

## Implementer brief

You are implementing this in a monorepo. Work in a **fresh git worktree branched off
`main`** (never commit on `main` directly). Components touched: `services/ai-backend`
(Python 3.13, FastAPI + LangGraph), `services/backend-facade` (Python proxy),
`packages/api-types` + `packages/chat-surface` (TypeScript, vitest). Run `make setup`
once if service `.venv`s / `node_modules` are missing. Test commands (from repo root):

```bash
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/surfaces_v2/
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api/
cd services/ai-backend && .venv/bin/python -m pytest            # full suite before PR
cd services/backend-facade && .venv/bin/python -m pytest
npm run test --workspace @0x-copilot/chat-surface && npm run typecheck --workspace @0x-copilot/chat-surface && npm run lint --workspace @0x-copilot/chat-surface
npm run test --workspace @0x-copilot/api-types && npm run typecheck --workspace @0x-copilot/api-types
```

Read these files first (paths relative to repo root):

1. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 event vocabulary (authoritative,
   use verbatim), §7 S3 sequence (this PR's flow), §10 invariants, §11 compat rules.
2. `docs/plan/generative-surfaces-v2/01-problem-and-requirements.md` — FR-C2–C5,
   NFR-4/NFR-10 (the requirements this PR satisfies).
3. `services/ai-backend/src/agent_runtime/api/draft_service.py` — `DraftService.send()`
   (the propose seam you branch); `_emit_approval_requested` shows the event-emission
   pattern via the injected duck-typed `event_producer.append_api_event`.
4. `services/ai-backend/src/agent_runtime/persistence/ports.py` — `DraftStorePort`
   (`insert_version`, `latest`, `get_version`, `expect_status`), `OptimisticConflict`;
   reused as the revision-snapshot store. Record shapes: sibling
   `persistence/records/drafts.py` (`DraftRecord`, `DraftStatus`).
5. `services/ai-backend/src/runtime_api/http/drafts.py` — `register_draft_routes()`,
   the route-registration pattern to copy; and
   `src/agent_runtime/api/approval_coordinator.py` — v1 decision validation
   (`record_approval_decision` scope check → 403) whose fail-closed discipline you port.
6. `services/ai-backend/tests/unit/runtime_api/test_approval_with_edits.py` and
   `tests/unit/runtime_worker/test_draft_send_approve_with_edits.py` — the PRD-09 v1
   suites whose cases you port to the v2 path (do not delete the originals).
7. `services/ai-backend/src/runtime_api/schemas/events.py` — the payload allow-list
   projection pattern (`_surface_spec_generated_payload`) every new event type follows.
8. The merged PRD-A1/A3/B1 code (see Interfaces consumed) — contracts, ledger emitter,
   ts projector. These land before D1; locate them before writing code.
9. `services/ai-backend/CLAUDE.md`, `services/ai-backend/tests/CLAUDE.md`,
   `packages/chat-surface/CLAUDE.md` — binding engineering rules (see Guardrails) — and
   `tools/design-parity/SKILL.md` — the parity pipeline this PR's UI DoD uses.

## Context

Generative Surfaces v2 makes an agent's work on real SaaS tools visible as live artifact
surfaces on a per-run canvas: reads flow, **writes stage and are decided on the artifact
itself** with a what-you-approve-is-what-executes guarantee, and everything threads
through a typed, append-only **Work Ledger** (event-sourced on the existing per-run
runtime event log) whose projections give the canvas, receipt, and sources. See
`../01-problem-and-requirements.md` §2C and `../02-sdr.md` §2 (rewrite rationale), §3
(components), §5 (ledger vocabulary).

This PR is Wave D's opener (`../03-prds.md` PRD-D1). Wave A gave contracts (A1), the
UsageMeter (A2), and ledger emission + SurfaceStore projection behind the `SURFACES_V2`
runtime flag (A3); Wave B mounted the canvas with tabs (B1) and provenance footers (B2);
Wave C added classification + gates. D1 introduces the **WriteStager**: the server-side
engine that turns a draft-send proposal into `write.staged` / `revision.added` /
`decision.recorded` ledger events, plus the staged-draft surface UI (rev-pinned approve
bar, free-form edit, reject/restore). It deliberately stops short of execution: PRD-D2
adds the CommitEngine and `write.applied`; PRD-D3 generalizes to row-sets. The shipped
PRD-09 v1 draft-approval flow (`kind="draft_send"` approvals) remains the live path when
`SURFACES_V2` is off, per SDR §11.

## Interfaces consumed / exposed

**Consumed (must already exist on `main` from earlier waves):**

- PRD-A1: TS contracts `StagedWrite`, `Revision`, `Decision`, `SurfaceEventV2` union in
  `packages/api-types/src/index.ts`; pydantic mirrors in ai-backend; event-type string
  constants + ledger-id (`r<short>·<seq>`) formatter/parser in
  `packages/service-contracts`; the golden-event fixture file. VERIFY AT IMPL: exact
  symbol names and fixture path as merged by A1.
- PRD-A3: the v2 ledger emission chokepoint (a wrapper over
  `RuntimeEventProducer.append_api_event` stamping `v: 1` and the v2 event types), the
  `SURFACES_V2` flag accessor, the SurfaceStore fold, and
  `GET /v1/agent/runs/{id}/surfaces`. VERIFY AT IMPL: package path (expected
  `services/ai-backend/src/agent_runtime/surfaces_v2/`), emitter class name, and flag
  accessor (expected a `config.py` class mirroring
  `capabilities/surfaces/config.py::SurfaceEmissionFlag.enabled(environ)`). A3 also
  fixes the v2 `RuntimeApiEventType` convention: string values are the SDR §5 names
  verbatim (e.g. `"surface.created"`); D1 adds three members likewise.
- PRD-B1: the client-side ledger projector in `packages/chat-surface` folding golden
  events into canvas state, and the canvas tab mount. VERIFY AT IMPL: file name of the
  ts fold (referred to below as `ledgerProjector.ts`) and its extension seam.
- PRD-B2: provenance footer component (op · latency · access class · ledger id · link)
  reused on the staged-draft surface. VERIFY AT IMPL: exported component name.
- PRD-C1: classification exists, but D1's propose seam (draft send) is intrinsically a
  write; D1 takes no direct code dependency on the classifier.
- Existing v1 machinery reused, not modified: `DraftStorePort` + its three adapters
  (in-memory / postgres / file), `DraftRecord`/`DraftStatus`, `RequireScopes(RUNTIME_USE)`
  route dependency, facade proxy pattern
  (`services/backend-facade/src/backend_facade/app.py` ~L1131 approvals proxy).

**Exposed (later PRDs rely on these; do not rename after merge):**

- The three event emissions and their payload shapes (below) — consumed by D2 (apply
  gate), D3 (row scope), E1 (receipt fold), E2 (approvals-queue cards).
- `WriteStager` + `StagedWriteFold` + `StagedWriteState` (D2 injects a CommitEngine next
  to the stager; D3 extends `stage()` with `rows`).
- HTTP: `POST /v1/agent/stages/{stage_id}/decisions` (SDR §4),
  `POST /v1/agent/stages/{stage_id}/revisions` (NEW, defined here),
  `GET /v1/agent/stages/{stage_id}` (NEW, defined here) + facade passthroughs.
- The decision-validation matrix (approve pins latest rev; reject→restore; frozen after
  approve) — D2's precondition/idempotency layer builds on it.

## Design

### Ledger events (SDR §5 verbatim; payloads carry `v: 1`)

D1 emits exactly three event types. `write.applied` is intentionally absent.

```text
write.staged      {v, stage_id, surface_id, target: {connector, op}, proposal_ref}
                  # rows / agent_holds omitted for single-artifact (D3 adds them)
revision.added    {v, stage_id, rev, author: "agent"|"user", diff_ref,
                   proposal_ref,                       # additive: snapshot of THIS rev
                   authorship_spans: [{start, end, author}]}   # additive; [] for rev 1
decision.recorded {v, stage_id, decision: "approve"|"reject"|"restore",
                   scope: {rev: N}, actor: "user"}
                  # "hold" is in the SDR enum but 422s in D1 (row-scoped; arrives in D3)
```

Wire mechanics: add `RuntimeApiEventType` members `WRITE_STAGED = "write.staged"`,
`REVISION_ADDED = "revision.added"`, `DECISION_RECORDED = "decision.recorded"` in
`src/runtime_api/schemas/common.py` (member-name style must match A3's v2 members —
VERIFY AT IMPL); add allow-list payload projections + `activity_kind_for` branches +
display titles in `src/runtime_api/schemas/events.py` following
`_surface_spec_generated_payload`. `event_type` is a text column — no migration.
Ref formats (NEW): `proposal_ref = "draft://<draft_id>/v<version>"` (the snapshot is the
DraftRecord row, durable via `DraftStorePort.get_version`); `diff_ref =
"draft://<draft_id>/v<from>..v<to>"` (diff re-derivable from the two snapshots, NFR-5;
computed spans also ride inline for the client). Ledger ids shown in UI are
`r<short>·<seq>` via the A1 formatter — presentation only, never a new id system.

### Domain engine (ai-backend, in the A3 v2 package)

`src/agent_runtime/surfaces_v2/staging.py` (NEW; adjust dir to A3's actual package):

```python
class StagedWriteStatus(StrEnum):
    STAGED = "staged"; REJECTED = "rejected"; APPROVED = "approved"
    APPLIED = "applied"   # unreachable in D1; fold recognizes it for D2 forward-compat

class StagedWriteState(RuntimeContract):     # pure fold output
    stage_id: str; surface_id: str; draft_id: str
    target_connector: str; target_op: str
    latest_rev: PositiveInt; approved_rev: PositiveInt | None
    status: StagedWriteStatus
    revisions: tuple[RevisionSummary, ...]   # RevisionSummary: rev, author, created seq, spans
    decisions: tuple[DecisionSummary, ...]   # DecisionSummary: decision, scope_rev, actor, seq

# RevisionSummary / DecisionSummary are new RuntimeContract value objects defined in this
# same module; each field is projected from the revision.added / decision.recorded payloads.

class StagedWriteFold:                        # events in → state out; no IO
    @classmethod
    def fold(cls, events: Sequence[RuntimeEventEnvelope]) -> dict[str, StagedWriteState]: ...

class WriteStager:
    def __init__(self, *, draft_store, ledger, persistence): ...   # ledger = A3 emitter
    async def stage(self, *, org_id, user_id, run, draft: DraftRecord,
                    target_connector, target_op) -> StagedWriteState:
        # allocate stage_id (uuid4 hex) + surface_id (uuid4 hex). In D1 the draft is
        # agent-authored (not a tool read), so there is never a prior v2 surface for it:
        # always emit a fresh surface.created {kind:"message", source:{connector:
        # target_connector, op: target_op}, title: draft.title, payload_ref:
        # "draft://<draft_id>/v<version>"} (A3 event) — then write.staged, then
        # revision.added {rev:1, author:"agent", authorship_spans:[]}
    async def add_user_revision(self, *, org_id, user_id, stage_id, base_rev,
                                content_text, title=None) -> StagedWriteState:
        # 409 unless status==STAGED and base_rev == latest_rev (concurrent-edit guard
        # via DraftStorePort.expect_status / OptimisticConflict);
        # diff vs base snapshot -> spans; insert draft version; emit revision.added
    async def record_decision(self, *, org_id, user_id, stage_id, decision, rev
                              ) -> StagedWriteState:
        # validation matrix below; emits decision.recorded on success
```

Decision validation matrix (fail-closed; typed domain errors → HTTP codes):

| Request           | Precondition                            | Effect                                                                        |
| ----------------- | --------------------------------------- | ----------------------------------------------------------------------------- |
| approve rev N     | status==STAGED and N == latest_rev      | status→APPROVED, approved_rev=N, event emitted                                |
| approve rev N     | N != latest_rev                         | 409 `stale_revision`, **no event** (WYSIWYG pin, FR-C3)                       |
| approve           | status==APPROVED, same rev              | idempotent 200, no duplicate event                                            |
| reject rev N      | status==STAGED                          | status→REJECTED (staged write voided; draft rows untouched)                   |
| restore           | status==REJECTED                        | status→STAGED, bar re-pins latest_rev (FR-C5)                                 |
| hold              | any                                     | 422 (single-artifact; D3)                                                     |
| any decision      | status==APPROVED (different action/rev) | 409 (frozen pending D2 apply)                                                 |
| add_user_revision | status in {APPROVED, REJECTED}          | 409 (edit only while STAGED; restore first)                                   |
| any op            | unknown stage_id, or org/user mismatch  | 404 / 403 (mirror `ApprovalCoordinator.record_approval_decision` scope check) |

Stage state is a **pure fold of the run's ledger events** (SubagentStorePort precedent:
projection over `runtime_events`, no new table, no migration). `WriteStager` reads
current state by folding `event_store.list_events_after(org_id=..., run_id=...,
after_sequence=0)` (keyword-only signature, as in
`runtime_worker/tool_observations.py`); revision
_content_ lives in draft rows. Rebuildable-on-replay for free (SDR §6).

`src/agent_runtime/surfaces_v2/revision_diff.py` (NEW):

```python
class AuthorshipSpan(RuntimeContract):
    start: NonNegativeInt; end: NonNegativeInt   # char offsets into the NEW text
    author: Literal["agent", "user"]

class RevisionDiffer:
    _MAX_SPANS = 200      # cap; beyond it, one whole-body user span (honest fallback)
    @classmethod
    def spans(cls, *, old: str, new: str, author: str) -> tuple[AuthorshipSpan, ...]:
        # difflib.SequenceMatcher opcodes -> replace/insert regions in `new`
```

Deterministic, stdlib-only, unicode-safe (`str`, not bytes). Multi-edit sessions diff
each new rev against the immediately previous rev (FR-C4; agent regions stay unmarked).

### Propose seam

`DraftService.send()` (`src/agent_runtime/api/draft_service.py`): after the existing
auth-gate + host-run resolution + insert of the `SEND_PENDING_APPROVAL` version, branch:
flag off → existing steps 4–6 (`_create_approval`, `_emit_approval_requested`, audit),
byte-identical; flag on → `WriteStager.stage(...)` instead — no v1 approval row, no
`APPROVAL_REQUESTED` event, existing `_audit` draft.send.proposed kept. `WriteStager` is
injected into `DraftService` the same optional duck-typed way `event_producer` already
is (None ⇒ v1 path regardless of flag). Confirmed single propose path: the only caller
of `DraftService.send()` is the `/v1/agent/drafts/{draft_id}/send` route handler
(`src/runtime_api/http/drafts.py`); no agent-side tool invokes it directly, so branching
`send()` covers every propose route.

Do **not** reuse `SurfaceEdits`/`SurfaceEditMerger`
(`agent_runtime/capabilities/surfaces/commit.py`) or the worker's
`_apply_edits_to_draft` (`runtime_worker/handlers/approval.py`) for v2 editing:
those are field-delta merges (v1 approve_with_edits); v2 free-form editing is
whole-snapshot revisions + server diff — conflating the two models is a known trap. The
v1 commit-executor island (`agent_runtime/capabilities/surfaces/commit.py`) stays
untouched until D2 evaluates it as CommitEngine raw material.

### HTTP API

`src/runtime_api/http/stages.py` (NEW), pattern-copied from `register_draft_routes`:

- `GET  /v1/agent/stages/{stage_id}` → `StagedWriteView` (refetch after reconnect/409).
- `POST /v1/agent/stages/{stage_id}/revisions` → body
  `{base_rev: int, content_text: str, title?: str}` → `StagedWriteView`.
- `POST /v1/agent/stages/{stage_id}/decisions` → body
  `{decision: "approve"|"reject"|"restore", rev?: int}` → `StagedWriteView`. (SDR §4.)
  `rev` is **required for `approve` and `reject`** (WYSIWYG — you decide on the rev you
  see; approve 409s `stale_revision` if it is not `latest_rev`, per the matrix) and
  **optional/ignored for `restore`** (restore re-pins `latest_rev` server-side; the emitted
  `decision.recorded{restore}` carries `scope.rev = latest_rev`). Missing `rev` on
  approve/reject ⇒ 422.

All routes `Depends(RequireScopes(RUNTIME_USE))`; `register_stage_routes(router)` is
called from `RuntimeApiRouter.create_router` **only when `SURFACES_V2` is on** — flag
off ⇒ the routes do not exist (404), the cleanest byte-identical guarantee. Schemas in
`src/runtime_api/schemas/stages.py` (NEW): `StageRevisionRequest`,
`StageDecisionRequest`, `StagedWriteView` (pydantic mirrors of the A1 contracts +
`authorship_spans`). `StageDecisionRequest.decision` accepts the **full** SDR decision
enum (`approve|reject|hold|restore`) so that `hold` reaches the domain and
`WriteStager.record_decision` raises the typed 422 (`UnsupportedDecision`) with a safe
message — the adversarial matrix test asserts the typed error class, so `hold` must not be
rejected at the pydantic boundary. Facade: three passthroughs in
`services/backend-facade/src/backend_facade/app.py` next to the approvals proxy — pure
proxy, no logic. Add TS request/response mirrors additively to
`packages/api-types/src/index.ts` if A1 did not already define them.

### Client (chat-surface + hosts)

- Extend B1's ledger projector fold with the three events → `stages: Map<stageId,
StagedWriteClientState>`; extend the golden-fixture parity test (ts fold === py fold).
- NEW components in `packages/chat-surface/src/surfaces/staged/`:
  - `ApproveBar.tsx` — pinned bar: microcopy exactly "Exactly this draft — rev {N} — is
    what sends." + Approve / Reject (or Restore when rejected) + ledger id chip
    (`r<short>·<seq>`). Built from design-system kit recipes (`.ui-button`, `.ui-pill`,
    `SectionLabel`) — a kit component first, per SDR §9.
  - `StagedDraftSurface.tsx` — message-archetype draft body; free-form full-body edit in
    place (textarea takeover on "Edit"); "edited by you" span highlighting from
    `authorship_spans`; rejected state = dimmed body + Restore; B2 provenance footer
    with access class "write · held".
- Render from projector state only (one-projector invariant); actions are callbacks the
  host wires to the facade routes via the `Transport` port. On a 409 from
  `/revisions`|`/decisions`: refetch `GET /stages/{id}`, re-pin the bar to the new
  latest rev, show a non-modal "Draft changed — review rev N" notice.
- Mount: B1's canvas tab renderer maps stage surfaces (`kind: "message"` + matching
  `write.staged`) to `StagedDraftSurface`; host wiring via the existing binder pattern
  (`apps/frontend/src/features/run/RunRoute.tsx`,
  `apps/desktop/renderer/destinationBinders.tsx`) — callbacks only, no new host UI.

Error behavior: typed domain errors with safe public messages — 404 unknown stage /
flag off; 403 foreign org/user; 409 `stale_revision` | `stage_frozen` |
`edit_conflict`; 422 malformed body / `hold` / unknown decision. Every 4xx emits **no
ledger event** — the ledger records only what happened.

## Implementation plan

1. **Contracts.** Add the three `RuntimeApiEventType` members + projector allow-lists +
   display titles (`src/runtime_api/schemas/common.py`, `events.py`; new payload keys as
   `_Fields`/`Keys.Field` constants, not literals). Add `StagedWrite`-view TS mirrors to
   `packages/api-types/src/index.ts` if missing (additive only).
2. **Domain.** Create `src/agent_runtime/surfaces_v2/staging.py` and `revision_diff.py`
   (dir per merged A3). Pure fold first, then `WriteStager` methods.
3. **Propose seam.** Branch `src/agent_runtime/api/draft_service.py::send` on the flag;
   inject `WriteStager` in
   `src/runtime_api/app.py::RuntimeApiAppFactory.default_draft_service` (~L518, the
   classmethod that builds `DraftService`), where `DraftService` is constructed over the
   draft-store fallback (~L538–543). Pass it as a new optional duck-typed constructor kwarg
   on `DraftService.__init__` (whose `event_producer: object | None = None` at ~L68 is the
   pattern to mirror); `None` ⇒ v1 path regardless of flag.
4. **Routes.** Create `src/runtime_api/http/stages.py` +
   `src/runtime_api/schemas/stages.py`; register flag-gated via `register_stage_routes(router)`
   inside `src/runtime_api/http/routes.py::RuntimeApiRouter.create_router` (~L566),
   alongside the existing `register_draft_routes(router)` call (~L705).
5. **Facade.** Add passthroughs in `services/backend-facade/src/backend_facade/app.py`.
6. **Client.** Extend the B1 projector + golden fixtures; add `ApproveBar.tsx` +
   `StagedDraftSurface.tsx` under `packages/chat-surface/src/surfaces/staged/` + barrel
   exports in `packages/chat-surface/src/index.ts`; map the surface kind in B1's canvas
   renderer; wire host callbacks (web `RunRoute.tsx`, desktop `destinationBinders.tsx`).
7. **Tests + parity + smoke** (below), then port the PRD-09 suites.

## Test plan

ai-backend (`cd services/ai-backend && .venv/bin/python -m pytest <file>`; fakes/mixins
per tests/CLAUDE.md — no network, no live LLM; assert typed error class + safe message):

- `tests/unit/agent_runtime/surfaces_v2/test_write_stager.py` —
  `test_stage_emits_surface_created_write_staged_revision_one`,
  `test_user_revision_bumps_rev_and_emits_spans`,
  `test_stale_base_rev_conflicts_and_emits_nothing`, `test_decision_matrix_all_cells`
  (parametrized over the table above), `test_reject_then_restore_repins_latest_rev`,
  `test_foreign_user_cannot_decide` (403, no event).
- `tests/unit/agent_runtime/surfaces_v2/test_revision_diff.py` — insert/replace/delete
  spans; multi-edit session (agent rev1 → user rev2 → user rev3: rev3 spans diff against
  rev2 only); span-cap fallback; unicode/emoji offsets.
- `tests/unit/agent_runtime/surfaces_v2/test_staged_write_fold.py` — fold of golden
  events equals expected `StagedWriteState`; tolerant of interleaved non-stage events;
  re-fold after "restart" identical.
- `tests/unit/runtime_api/test_stage_routes.py` — happy paths; flag off ⇒ 404 on all
  three routes; 422 bodies.
- `tests/unit/runtime_api/test_stage_no_bypass.py` — **adversarial suite (DoD)**, port +
  extend PRD-09's (`test_approval_with_edits.py`, `test_draft_send_approve_with_edits.py`
  stay green untouched): `test_no_event_type_write_applied_is_emittable_in_d1`,
  `test_random_api_sequences_never_yield_write_applied` (property-style: random
  stage/revise/decide sequences → zero `write.applied` events, zero connector calls on
  a spying fake MCP client), `test_approve_stale_rev_rejected_409_no_event`,
  `test_decision_on_v1_approval_id_does_not_touch_v2_stage`, `test_edits_after_approve_rejected`.
- `tests/unit/runtime_api/test_draft_service_v2_branch.py` — flag off ⇒ emitted events +
  approval rows byte-identical to today (snapshot); flag on ⇒ no `APPROVAL_REQUESTED`,
  no approval row, v2 events present; stager absent ⇒ v1 path.
- Facade: passthrough test per the existing proxy-test convention in
  `services/backend-facade/tests/` — name it `test_stage_routes_proxy.py`, mirroring
  the sibling `test_approval_decision_proxy.py`.

chat-surface (`npm run test --workspace @0x-copilot/chat-surface`):

- `packages/chat-surface/src/surfaces/staged/ApproveBar.test.tsx` — pin microcopy with
  rev number; re-pin on new rev; Reject↔Restore swap; ledger-id chip format.
- `packages/chat-surface/src/surfaces/staged/StagedDraftSurface.test.tsx` — spans
  render as "edited by you"; rejected = dimmed + Restore; 409 → refetch → re-pin
  notice; edit submit sends `base_rev`.
- Projector fold cases + golden-fixture ts↔py parity extension in B1's projector test.

Live-smoke script (desktop stack, real services):

1. `make dev` with `SURFACES_V2=true RUNTIME_START_IN_PROCESS_WORKER=true` in
   `services/ai-backend/.env`; `export TOKEN=$(make dev-bearer)`.
2. Create a conversation + run that produces a draft (recipes: `docs/dev-testing.md`);
   `POST /v1/agent/drafts/{draft_id}/send` via facade `:8200`.
3. `GET /v1/agent/runs/{run_id}/events` → `write.staged` + `revision.added` (rev 1,
   author agent) present; Run cockpit shows the staged draft tab, bar pinned to rev 1.
4. Edit the body in the UI → bar re-pins rev 2, "edited by you" spans visible;
   `revision.added` rev 2 author user in replay.
5. Approve → `decision.recorded {approve, scope:{rev:2}}` in replay; **no
   `write.applied` event exists and the draft's `status` never becomes `sent`**.
6. Reject/restore pass on a second draft; reload the app → canvas reconstructs from
   replay. 7. Flag-off rerun: `/send` yields the v1 approval card; stage routes 404.

Design parity: vendor the staged-draft + approve-bar region of the v2 mock
(`Generative Surfaces v2.dc.html`, Claude Design project `ceb081f6`, walkthrough part 03) into `tools/design-parity/surfaces/v2-staged-draft/design/` (VERIFY AT IMPL: mock's
local mirror location; DesignSync per `tools/design-parity/SKILL.md` if absent), then
run the pipeline (render-live vitest → extract-computed → `node
tools/design-parity/lib/compare.mjs … --out surfaces/v2-staged-draft/out/report.md`).

## Definition of done

From `../03-prds.md` PRD-D1 (binding, never weakened):

- [ ] **Adversarial no-bypass suite: no sequence of events/API calls yields
      `write.applied` without a matching approve on the exact rev (port + extend PRD-09's
      suite).** Proof: `test_stage_no_bypass.py` green incl. the random-sequence case and
      the zero-connector-traffic spy; PRD-09 v1 suites still green untouched.
- [ ] **Edit → rev bump → bar re-pins; authorship spans correct for a multi-edit session
      (diff tests).** Proof: `test_revision_diff.py` multi-edit case + `ApproveBar.test.tsx`
      re-pin case + live-smoke step 4.
- [ ] **Parity: draft surface + approve bar vs mock, 0 HIGH; live desktop flow.** Proof:
      `tools/design-parity/surfaces/v2-staged-draft/out/report.md` checked in with 0 HIGH
      rows; live-smoke steps 3–6 on the desktop app.

Standard DoD (every PRD):

- [ ] Unit tests in ai-backend venv + facade venv + chat-surface/api-types workspaces
      pass; `npm run typecheck` green for both TS packages; full ai-backend suite green.
- [ ] Flags off ⇒ byte-identical behavior — proof: `test_draft_service_v2_branch.py`
      snapshot + stage-routes-404 test + live-smoke step 7.
- [ ] No service-boundary violations: apps → facade only; no cross-`src/` imports;
      chat-surface eslint clean.
- [ ] No new LLM call sites (D1 has none — diffing is `difflib`); any that appear go
      through the A2 UsageMeter seam.
- [ ] Docs: update `../02-sdr.md` §5/§7-S3 if implementation diverges (e.g. note the
      additive `authorship_spans`/`proposal_ref` payload keys under §5).

UI DoD (🎨):

- [ ] Built from design-system/chat-surface kit components — no host-app one-off
      styling, no raw font-size/letter-spacing (design-system SKILL.md rule).
- [ ] `tools/design-parity/` run vs the staged v2 mock region: **0 HIGH drift**.
- [ ] Live desktop smoke of the flow on the real stack (script above), not just tests.

Close-out DoD (2026-07-23 — D1 owns FR-A8's write-policy-preservation clause the coverage
sweep flagged as unowned; see 06-coverage-report.md):

- [ ] **FR-A8 — steering cannot relax the held-write posture.** A mid-run steering message
      cannot relax the held-write posture: a test asserts that after steering, write-classified
      ops still stage/hold and **no** connector is flipped to `allow_always` by steering.

## Out of scope

- Connector execution, `write.applied`, precondition re-check, idempotency keys,
  CommitEngine wiring (PRD-D2).
- Row-set staging, per-row/`hold` decisions, `agent_holds`, partial apply, allow-always
  auto-apply (PRD-D3, FR-C8).
- Receipt/Sources rendering of stage rows (E1); Approvals-queue cards (E2).
- Removing or altering v1 draft-approval behavior, `DraftSurfaceProjector`, or the v1
  `result["surface"]` appendage (compat window ends in E3).
- Failure-path visual polish (Phase-2 designer track; states/events only). Settings/usage UI.

## Guardrails

- **Service boundaries (hard):** apps call `backend-facade:8200` `/v1/*` only — never
  `:8000`/`:8100`; facade proxies verbatim (no AI orchestration in it); no deployable
  component imports another's `src/`; no sibling `PYTHONPATH` additions; contracts move
  only via `packages/api-types` / `packages/service-contracts`.
- **Flag-off byte-identical:** with `SURFACES_V2` unset/off, every wire payload, event
  stream, approval row, and route table is byte-for-byte today's. The snapshot test is
  the gate, not a review promise.
- **ai-backend rules** (`services/ai-backend/CLAUDE.md`): Pydantic at every IO/domain
  boundary — no long-lived `dict[str, Any]` state; production helpers live inside
  classes (no module-level helper functions); repeated keys/messages in nested `Keys` /
  message classes (new payload keys ⇒ `Keys.Field`/`_Fields` constants); broad
  exceptions → typed domain errors with safe public messages; user-submitted revision
  text is untrusted until validated; the stager never touches an MCP client.
- **ai-backend tests** (`tests/CLAUDE.md`): fakes/mixins, never network or live LLMs;
  concrete test classes contain only `test_*` methods; assert the typed error class and
  safe public message; cover permission-denial and malformed-input paths.
- **chat-surface** (`packages/chat-surface/CLAUDE.md`): substrate-agnostic — no
  `window`/`document`/`fetch`/`localStorage`/`EventSource` (eslint-enforced); all IO via
  the `Transport` port; no `apps/*` imports; one event projector — new consumers are
  pure selectors over the same event array.
- **Event hygiene:** never derive activity types from event-name prefixes;
  `RuntimeEventEnvelope` never carries `org_id`; new payloads go through projector
  allow-lists; the ledger is append-only — never mutate or retro-emit to "fix" state.

## Open questions

These are the only items not determinable from the SDR, the requirements, or the current
repo; everything else in this PRD is fully specified. None blocks starting the domain
engine + contracts (steps 1–2); resolve before the propose-seam step (3).

1. **In-flight v1 drafts at flag flip.** `SURFACES_V2` is a process-level accessor read
   from the environment at boot (per A3), not a per-draft toggle. A draft that was proposed
   under v1 (flag off) — leaving a `SEND_PENDING_APPROVAL` version + an `APPROVAL_REQUESTED`
   approval row and no v2 stage — becomes undecidable on the v2 path after a redeploy flips
   the flag on (the v1 approval card renders, but its `/decision` route is the v1 one; no
   `write.staged` exists). Options: (a) declare this unsupported and require draining
   pending v1 drafts before enabling the flag in a deployment runbook; (b) have the v2
   canvas fall back to the v1 approval card for drafts lacking a `write.staged` event.
   Proposed default: **(a)** — the flag is a deploy-time decision, mid-flight migration is
   out of D1's blast radius; document it in the SDR §11 compat notes. Confirm.
2. **`StagedWriteView` ↔ `StagedWriteState` projection ownership.** The route returns
   `StagedWriteView` (A1 wire contract) built from the domain `StagedWriteState`. The
   field-by-field mapping is mechanical, but where it lives (a `StagedWriteView.from_state`
   classmethod on the schema, vs. a mapper in `stages.py`) is unspecified. Proposed default:
   a `from_state` classmethod on `StagedWriteView` (mirrors the existing draft-view
   projection convention). Non-blocking; confirm at review.
