# PR 4.4.6.4 — Approval card: 60-second undo window + protocol

> **Status:** Draft (PRD + Spec)
> **Plan reference:** Phase 4 of the consent-card redesign. Phases 1, 2, 3 shipped:
>
> - PR 4.4.6.1 — `ApprovalCard` + `ApprovalReceipt` components, copy helpers, button hierarchy.
> - PR 4.4.6.2 — structured wire payload (`vendor`, `category`, `reason_code`, `reversible`, `params`).
> - PR 4.4.6.3 — vendor-specific param recognisers (Slack / GitHub / Linear / Notion / Atlassian).
>
> **Owner:** ai-backend (extend `ApprovalDecisionResponse` with `undo_expires_at`; new endpoint + service method `request_undo`; new audit event; new `RuntimeApiEventType.APPROVAL_UNDO_REQUESTED`) · api-types (mirror two fields, one event-type literal) · frontend (`useUndoCountdown` hook; `ApprovalReceipt` undo button; `requestApprovalUndo` API client; `ApprovalTool` reads decision response). · backend / backend-facade / design-system (zero — backend-facade proxies the new route; primitives unchanged).
> **Size:** **M.** ~340 LoC across backend + FE + tests. No DB migration.
> **Depends on:**
>
> - ✅ PR 4.4.6.2 — `ApprovalReversible` enum on the wire; `reversible="yes"` is the trigger for an undo window.
> - ✅ PR 4.4.6.3 — recogniser registry; vendors opt into `reversible="yes"` per recogniser. (Phase 4.1+ adds real compensators per vendor; this PR is the protocol they plug into.)
>
> **Reads alongside:**
>
> - [`pr-4.4.6.2-approval-card-structured-payload.md`](pr-4.4.6.2-approval-card-structured-payload.md) — `ApprovalReversible.YES/NO/N_A` semantics.
> - [`pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md) — approval state machine, audit chain.
> - [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — typed errors at the API boundary, Pydantic at every IO edge, no silent broad-except.

---

## 0 · TL;DR

Phase 2 introduced `reversible: "yes" | "no" | "n/a"` on the consent-card payload. Today no vendor reports `yes`, and the FE has nowhere to render an "Undo" affordance even if one did. Phase 4 makes the marker actionable end-to-end:

1. **Decision response** carries `undo_expires_at: datetime | None`. Set to `decided_at + 60s` when status=`approved` AND the original request had `reversible="yes"`. Otherwise `null`.
2. **New endpoint** `POST /v1/agent/approvals/{approval_id}/undo` validates the window, audits, emits an `approval_undo_requested` stream event. Returns 410 if expired, 422 if the approval was never reversible, 404 if missing, 403 across users.
3. **Frontend countdown** on `ApprovalReceipt`. While `now < undo_expires_at`, a "Undo (Ns)" button ticks down. Click → POST → optimistic flip to a one-line "Undo requested · 10:43" receipt.
4. **One opt-in vendor**: Slack's `post_message` recogniser sets `reversible="yes"` so the flow renders end-to-end out of the box. Other vendors stay `reversible="no"` — the button never appears.

| Surface                                                | Today                                                                                         | After this PR                                                                                                                                                                                                   |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ApprovalDecisionResponse`                             | `approval_id`, `run_id`, `status`, `decided_at`, `forwarded_to_user_id`, `child_approval_id`. | + `undo_expires_at: datetime \| None`.                                                                                                                                                                          |
| `POST /v1/agent/approvals/{id}/decision`               | Records decision; resumes worker.                                                             | **No change** to the existing semantics. The response now carries `undo_expires_at` when applicable.                                                                                                            |
| `POST /v1/agent/approvals/{id}/undo`                   | Doesn't exist.                                                                                | New. Records intent + emits `approval_undo_requested`. 410 expired, 422 not undoable, 404 missing, 403 cross-user. Idempotent.                                                                                  |
| `RuntimeApiEventType`                                  | …                                                                                             | + `APPROVAL_UNDO_REQUESTED = "approval_undo_requested"`.                                                                                                                                                        |
| Audit                                                  | `approval_decision_recorded` on decide.                                                       | + `approval_undo_requested` on undo.                                                                                                                                                                            |
| Slack `post_message`                                   | `reversible="no"`.                                                                            | `reversible="yes"`. (Other vendors unchanged.)                                                                                                                                                                  |
| FE `ApprovalReceipt`                                   | One-line scrollback for resolved approvals.                                                   | Optionally renders an active-button row with countdown when `undoUntil > now`.                                                                                                                                  |
| MCP-level revert (e.g., calling Slack's `chat.delete`) | N/A.                                                                                          | **Out of scope.** This PR records the user's intent + audits it; actual side-effect revert per vendor is Phase 4.1+. The endpoint name (`/undo`) and the receipt copy (`Undo requested`) are honest about this. |

LoC: backend ≈ 180 (schema +20, service +80, route +30, audit/stream +20, recogniser tweak +5, validators +25) · api-types ≈ 20 · frontend ≈ 90 (hook +30, receipt +30, API client +10, tool +15, styles +5) · tests ≈ 230 (backend route+service+sweep, FE countdown). No DB migration; `undo_expires_at` lives inside the existing JSON-ish decision metadata.

The four runtime / streaming invariants (frozen at run-start, binary at runtime, single PATCH endpoint, replay-by-sequence) are preserved. The new event-type is additive.

---

## 1 · PRD

### 1.1 Problem

Phase 2 ships the `reversible` enum. Phase 3 ships richer params. Neither delivers what the design's "Yes — Atlas will keep an undo for 60s" promises. Three concrete gaps:

1. **No window persistence.** The decision response doesn't tell anyone (FE, audit, future workers) that this approved write is undoable for the next 60 seconds. There's no `undo_expires_at` anywhere.
2. **No protocol to act on it.** The user has nowhere to signal "I changed my mind". The forwarding endpoints don't fit because forwarding is a forward-decision, not a post-decision flip.
3. **No event vocabulary.** Audit chains and run-stream consumers can't observe "user requested undo within window" — there's no event type for it.

The fix is small and protocol-first: extend the decision response with `undo_expires_at`, ship a new endpoint that validates the window and emits the event, and surface the button in `ApprovalReceipt`. The actual MCP-level compensation (call Slack's `chat.delete`, restore the deleted Linear issue, etc.) is intentionally **not** in this PR — each vendor's compensator is its own focused follow-up that plugs into the protocol established here.

### 1.2 Goals

1. **`undo_expires_at` is a first-class wire field** on `ApprovalDecisionResponse`, populated only when status=`approved` AND the original approval request had `reversible="yes"`.
2. **`POST /v1/agent/approvals/{approval_id}/undo`** is the only way to act on the window. Inputs: approval_id (path) + decided_by_user_id (resolved from session). 200 on success, 410 expired, 422 never undoable, 404 missing, 403 cross-user.
3. **One stream event** — `approval_undo_requested` — replaces "the FE has to poll": run-stream subscribers learn about undo intent in the same channel as `approval_resolved`.
4. **One audit event** — `approval_undo_requested` — joins the existing `approval_decision_recorded` chain. Compliance can grep the user's full trail per approval_id.
5. **Honest receipt copy.** The receipt shows `Undo requested` after a successful POST, **not** `Undone`. Vendor-specific revert lands in Phase 4.1+; the protocol exists today.
6. **Idempotent endpoint.** Calling `/undo` twice within the window is a no-op on the second call (returns the same response). Calling after expiry returns 410.
7. **Slack `post_message` opts in to `reversible="yes"`** so the flow renders end-to-end out of the box. Other vendors stay `reversible="no"` until each lands its compensator + opt-in.
8. **No DB migration.** `undo_expires_at` is computed on the response; the source of truth is the existing `ApprovalRequestRecord.metadata` (`reversible` field) + `ApprovalDecisionRecord.decided_at`. Persisted state on the decision row uses the existing JsonObject metadata column.

### 1.3 Non-goals

- **Vendor-specific compensator execution.** No call to Slack's `chat.delete`, no Linear issue archive, etc. Phase 4.1+ adds those one vendor at a time, plugging into the protocol shipped here.
- **User-extendable window.** Always 60 seconds. Server constant, not configurable per vendor.
- **Multi-step undo.** A single approval has at most one undo. Re-undo is rejected.
- **Pre-approval cancellation.** `/undo` is post-decision only. Pre-decision cancellation goes through the existing decline path.
- **AskAQuestion / non-MCP approvals.** `undo_expires_at` is set only for `approval_kind="mcp_tool"`. Other kinds get `null` regardless of `reversible`.
- **Forwarded approvals.** A forwarded parent doesn't carry `undo_expires_at` — the leaf decision does. This matches the existing chain semantics.
- **i18n.** "Undo (Ns)" copy stays English.

### 1.4 Success criteria

- ✅ `ApprovalDecisionResponse.undo_expires_at: datetime | None` lives in `runtime_api/schemas/approvals.py`.
- ✅ `UNDO_WINDOW_SECONDS = 60` constant lives in `runtime_api/schemas/approvals.py`.
- ✅ `record_approval_decision` populates `undo_expires_at` on the response only when (a) `decision.value == approved` AND (b) `approval.metadata["reversible"] == "yes"`.
- ✅ `RuntimeApiEventType.APPROVAL_UNDO_REQUESTED = "approval_undo_requested"` registered.
- ✅ `POST /v1/agent/approvals/{approval_id}/undo` route registered. Route name `approval_undo`. Wired through `RuntimeApiRouter.create_router`.
- ✅ Endpoint contract:
  - 200 OK with `{approval_id, run_id, undo_requested_at}` on success (idempotent).
  - 410 Gone when `now > undo_expires_at`.
  - 422 Unprocessable when the approval was never reversible (no window was set).
  - 404 Not Found when the approval doesn't exist for the org.
  - 403 Forbidden when `decided_by_user_id` mismatches the approval's user.
- ✅ Endpoint emits `approval_undo_requested` as a `RuntimeEventEnvelope`. Replay via `?after_sequence=N` works.
- ✅ Endpoint writes `approval_undo_requested` audit row keyed off the approval_id.
- ✅ Idempotency is **FE-side**: the receipt's Undo button disables after first click and shows the "Undo requested" chip. Server does not deduplicate on double-POST; each legitimate request within the window writes an audit row + emits an event. (Persistence-based dedupe would require a schema migration; dropped per the zero-migration goal in §1.4.)
- ✅ Slack `post_message`: `reversible="yes"`. Other recognisers unchanged.
- ✅ api-types mirrors `undo_expires_at` and the new event-type literal.
- ✅ FE `useUndoCountdown(undoUntil)` returns `{ secondsRemaining, expired }` ticking once per second; no leak on unmount.
- ✅ FE `ApprovalReceipt` accepts optional `undoUntil: Date | null` and `onUndo: () => void`; renders an active button row when in window; renders a passive "Undo requested · HH:mm" line after click; renders nothing extra when window expired or absent.
- ✅ FE `requestApprovalUndo(approvalId, identity)` POSTs to the new route.
- ✅ FE `ApprovalTool` reads `result.undo_expires_at` from the decision response and threads it into the receipt.
- ✅ All Python tests under `services/ai-backend/tests/unit/runtime_api/` pass; new `test_approval_undo.py` adds 8 cases.
- ✅ All FE tests pass; new `useUndoCountdown.test.ts` + `ApprovalReceipt` test cases for countdown render + click flow.
- ✅ Typecheck on `@0x-copilot/api-types` and `@0x-copilot/frontend` clean.

### 1.5 User stories

| #    | Persona                         | Story                                                                                                                                                                                                                                                                                                                                         |
| ---- | ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | Sarah · Slack write             | Atlas drafts a launch announcement. Card lands; she taps "Approve & continue". Receipt shows `Approved · Post to #launch-aurora · 10:42 · Undo (60s)`. The countdown ticks. At 14s left she clicks Undo. Receipt updates to `Undo requested · 10:43`. (Compensator follow-up will actually delete the message; today, audit captures intent.) |
| US-2 | Marcus · read-only call         | Atlas fetches Linear issues. Card lands; he approves. Receipt is the regular one-liner — no undo button (read-only is `reversible="n/a"`).                                                                                                                                                                                                    |
| US-3 | Sarah · expired window          | Sarah taps Approve, then forgets to come back for two minutes. Receipt no longer shows the button (countdown hit 0). The receipt looks like any other resolved approval. If she POSTs `/undo` directly via API at this point, server returns 410.                                                                                             |
| US-4 | Marcus · auditor                | An audit query for `approval_id=abc` shows `approval_decision_recorded` (status=approved) followed by `approval_undo_requested` (within 60s). The chain is unbroken.                                                                                                                                                                          |
| US-5 | Compliance officer              | Filtering audit by `event_type=approval_undo_requested` enumerates every undo intent. Combined with `decided_by_user_id` it answers "who undoes most often, and against which connector?"                                                                                                                                                     |
| US-6 | Sarah · double-click            | She accidentally clicks Undo twice. Two POSTs go out. Server returns the same response on both. Receipt shows one `Undo requested` line, not two. Audit shows one row.                                                                                                                                                                        |
| US-7 | Engineer · adding a compensator | Phase 4.1 ships the Slack compensator. The endpoint contract doesn't change; only the runtime worker grows a `chat.delete` call. FE / wire schema / audit untouched.                                                                                                                                                                          |

---

## 2 · Spec

### 2.1 Wire — `runtime_api/schemas/approvals.py`

```python
# Server constant; not configurable per vendor.
UNDO_WINDOW_SECONDS: int = 60


class ApprovalDecisionResponse(RuntimeContract):
    approval_id: str
    run_id: str
    status: ApprovalStatus
    decided_at: datetime
    forwarded_to_user_id: str | None = None
    child_approval_id: str | None = None
    # PR 4.4.6.4 — non-null only when status==APPROVED AND the original
    # request was tagged reversible=YES. Computed by the service layer
    # at decision time; persisted via the existing decision metadata.
    undo_expires_at: datetime | None = None
```

Plus a new response model:

```python
class ApprovalUndoResponse(RuntimeContract):
    approval_id: str
    run_id: str
    undo_requested_at: datetime
    # echo-back of the window for the FE; the FE has the same value
    # already, but the server is authoritative on the response.
    undo_expires_at: datetime
```

### 2.2 Wire — `runtime_api/schemas/common.py`

```python
class RuntimeApiEventType(StrEnum):
    # … existing …
    APPROVAL_UNDO_REQUESTED = "approval_undo_requested"
```

### 2.3 Service — `agent_runtime/api/service.py`

`record_approval_decision`:

- After persisting the decision, if `record.status == ApprovalStatus.APPROVED` and `approval.metadata.get("reversible") == "yes"`, compute `undo_expires_at = record.decided_at + timedelta(seconds=UNDO_WINDOW_SECONDS)` and include it on the response. Persist on the decision record's `metadata` so subsequent `get_approval_decision` reads see the same value (no recompute, no clock drift).

```python
return ApprovalDecisionResponse(
    approval_id=record.approval_id,
    run_id=record.run_id,
    status=record.status,
    decided_at=record.decided_at,
    undo_expires_at=cls._undo_expires_at_for(approval, record),
)
```

New method `request_undo`:

- Look up the approval. 404 if missing or scope-mismatch.
- 403 if `decided_by_user_id` mismatches `approval.user_id`.
- Fetch the persisted decision. If absent → 422 `"Approval has no decision yet"` (the user can't undo something not approved).
- If `decision.status != APPROVED` → 422 `"Only approved decisions are reversible"`.
- Read `undo_expires_at` from decision metadata; if missing → 422 `"This approval was not flagged reversible"`; if `now > undo_expires_at` → 410 `"Undo window expired"`.
- If a prior undo request is recorded → return the previously stored `undo_requested_at` (idempotent).
- Else write `undo_requested_at = now()` to the decision record's metadata, append the audit row + stream event, and return.

The decision metadata gains two keys:

- `undo_expires_at: ISO8601 datetime`
- `undo_requested_at: ISO8601 datetime` (only after first request)

Both are part of the existing `JsonObject metadata` slot — no schema migration.

### 2.4 Route — `runtime_api/http/routes.py`

```python
@classmethod
async def approval_undo(
    cls,
    request: Request,
    approval_id: str,
    org_id: str | None = Query(None, min_length=1),
) -> ApprovalUndoResponse:
    org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=None)
    return await cls.service(request).request_approval_undo(
        org_id=org_id, approval_id=approval_id, decided_by_user_id=user_id
    )
```

Registered in `RuntimeApiRouter.create_router`:

```python
router.add_api_route(
    "/approvals/{approval_id}/undo",
    RuntimeApiRoutes.approval_undo,
    methods=["POST"],
    response_model=ApprovalUndoResponse,
    name=Keys.RouteName.APPROVAL_UNDO,
)
```

`Keys.RouteName.APPROVAL_UNDO = "approval_undo"`.

### 2.5 Audit + stream

Audit row:

```json
{
  "event_type": "approval_undo_requested",
  "org_id": "org_acme",
  "user_id": "user_sarah",
  "resource_type": "approval",
  "resource_id": "approval_xyz",
  "run_id": "run_abc",
  "outcome": "success",
  "metadata": {
    "approval_kind": "mcp_tool",
    "vendor": "SLACK",
    "tool_name": "post_message",
    "undo_expires_at": "2026-05-07T19:30:00Z",
    "undo_requested_at": "2026-05-07T19:29:54Z"
  }
}
```

Stream event payload:

```json
{
  "approval_id": "approval_xyz",
  "approval_kind": "mcp_tool",
  "decided_by_user_id": "user_sarah",
  "undo_requested_at": "2026-05-07T19:29:54Z",
  "undo_expires_at": "2026-05-07T19:30:00Z"
}
```

The event flows through the existing run-stream pipeline (same `RuntimeEventEnvelope`, same `?after_sequence` reconnect contract).

### 2.6 Vendor opt-in — `runtime_worker/approval_recognisers.py`

Slack post-message marks itself reversible. We add a tool-name gate so other Slack tools (e.g., `users.list`) don't accidentally inherit the flag:

```python
class SlackApprovalRecogniser(ApprovalParamRecogniser):
    vendor_tokens = ("slack",)
    reversible_tools: ClassVar[frozenset[str]] = frozenset({"post_message", "chat.postMessage"})

    @classmethod
    def reversibility(cls, tool_name: str, read_only: bool) -> ApprovalReversible | None:
        if read_only:
            return None  # falls back to caller default
        if tool_name in cls.reversible_tools:
            return ApprovalReversible.YES
        return None  # falls back
```

The base ABC grows a default `reversibility(tool_name, read_only) -> ApprovalReversible | None` returning `None`. The worker's `_approval_reversible` checks the recogniser's hint first; if `None`, it uses the existing read-only / no fallback.

### 2.7 api-types

```ts
export interface ApprovalDecisionResponse {
  approval_id: string;
  run_id: string;
  status: ApprovalStatus;
  decided_at: string;
  forwarded_to_user_id?: string | null;
  child_approval_id?: string | null;
  undo_expires_at?: string | null; // PR 4.4.6.4
}

export interface ApprovalUndoResponse {
  approval_id: string;
  run_id: string;
  undo_requested_at: string;
  undo_expires_at: string;
}
```

Plus the new event-type literal in `RuntimeApiEventType`.

### 2.8 Frontend

**`useUndoCountdown(undoUntil: Date | null)`**:

```ts
export function useUndoCountdown(undoUntil: Date | null): {
  secondsRemaining: number;
  expired: boolean;
} {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!undoUntil) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [undoUntil]);
  if (!undoUntil) return { secondsRemaining: 0, expired: true };
  const remainingMs = undoUntil.getTime() - now;
  return {
    secondsRemaining: Math.max(0, Math.ceil(remainingMs / 1000)),
    expired: remainingMs <= 0,
  };
}
```

**`ApprovalReceipt` — additive**:

```ts
interface ApprovalReceiptProps {
  // … existing …
  undoUntil?: Date | null;
  undoRequestedAt?: Date | null;
  onUndo?: () => void;
}
```

If `undoRequestedAt` is set → render `Undo requested · HH:mm` chip after the standard receipt line. Else if `undoUntil > now` → render an inline Undo button with countdown. Else nothing.

**API client** in `apps/frontend/src/api/`:

```ts
export async function requestApprovalUndo(
  approvalId: string,
  identity: ApiIdentity,
): Promise<ApprovalUndoResponse> {
  const response = await fetch(
    `/v1/agent/approvals/${encodeURIComponent(approvalId)}/undo`,
    { method: "POST", headers: identityHeaders(identity) },
  );
  if (!response.ok) {
    throw await classifyApprovalUndoError(response);
  }
  return (await response.json()) as ApprovalUndoResponse;
}
```

**`ApprovalTool` integration**:

When the approval is resolved as `approved` AND the latest decision response carries `undo_expires_at`, thread it as a `Date` into the receipt. Local state tracks `undoRequestedAt` after a successful POST.

### 2.9 Failure modes

| Path                                                                                               | Behaviour                                                                                                                                                                                    |
| -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Window expired before user clicks                                                                  | Receipt countdown hits 0 → button hides. If user POSTs anyway → 410 → toast "Undo window has expired."                                                                                       |
| Approval never reversible (no window)                                                              | Receipt has no button. If POST is forced → 422 "This approval was not flagged reversible."                                                                                                   |
| Cross-user undo attempt                                                                            | 403 "Approval decision user does not match approval scope." (Mirrors existing decision endpoint message.)                                                                                    |
| Idempotent double-click                                                                            | Second POST returns same `undo_requested_at`; FE re-renders idempotently.                                                                                                                    |
| Approval doesn't exist                                                                             | 404.                                                                                                                                                                                         |
| Approval status not approved (rejected / forwarded)                                                | 422.                                                                                                                                                                                         |
| Server emits `reversible=yes` but the run never resumed (e.g., agent crashed before tool executed) | The user sees the button. The audit captures the user's intent regardless. Since this PR doesn't actually call MCP, the side-effect state is unchanged from "tool never ran" — no harm done. |

---

## 3 · Architecture & invariants

### 3.1 Service boundaries

- `runtime_api/schemas/approvals.py` owns the wire types. New fields are optional; old clients ignore.
- `runtime_api/http/routes.py` owns the new route. Registered via `RuntimeApiRouter`.
- `agent_runtime/api/service.py` owns `request_approval_undo`. Audit + stream events fan out through the existing producer.
- `runtime_worker/approval_recognisers.py` owns vendor opt-in (`reversibility` hook on the ABC).
- `apps/frontend` owns the countdown hook + receipt UX. backend / backend-facade / design-system unchanged (facade proxies).
- api-types mirrors. **No `/internal/v1/*` change.**

### 3.2 Persistence

`ApprovalDecisionRecord.metadata` is already a `JsonObject`. We stash:

- `"undo_expires_at": "2026-05-07T19:30:00Z"` when the decision is approved + reversible.
- `"undo_requested_at": "..."` after a successful undo.

No new column. `Optional` retrieval — if the keys are absent the response simply doesn't include the field. Round-trippable across the in-memory and postgres adapters because both already round-trip the metadata blob.

### 3.3 Untrusted input

- `approval_id` is path-validated. The service looks it up against `org_id`. Cross-org reads return 404.
- `decided_by_user_id` is sourced from the verified session, never the request body.
- `undo_expires_at` is computed server-side; the client cannot influence it.

### 3.4 Streaming invariants

- New event-type added; no existing type changes.
- Replay-by-sequence still works.
- The undo event is emitted after the audit row commits (consistent with other approvals events).

### 3.5 What this PR doesn't do (explicitly)

- **Does not call any compensating MCP tool.** No Slack `chat.delete`, no Linear archive, no GitHub PR close. Adding those is **per-vendor** in Phase 4.1+ — they extend `request_approval_undo` to look up a compensator from a registry similar to `ApprovalParamRecogniserRegistry`, invoke it, and emit a richer outcome.
- **Does not change the agent run.** The run continues as it would have. Undo is a side-channel intent.
- **Does not block the agent.** The endpoint is fully asynchronous w.r.t. the LangGraph harness.

The receipt copy is "Undo requested" so the UX doesn't lie about side-effect status. Phase 4.1+ can flip the copy to "Undone" when a real compensator confirms.

---

## 4 · Test plan

### 4.1 ai-backend

`tests/unit/runtime_api/test_approval_undo.py` (NEW, ~8 tests):

- `test_decision_response_includes_undo_expires_at_when_reversible_yes_and_approved`
- `test_decision_response_omits_undo_expires_at_when_reversible_no`
- `test_decision_response_omits_undo_expires_at_when_rejected`
- `test_undo_endpoint_records_intent_within_window`
- `test_undo_endpoint_410_when_expired`
- `test_undo_endpoint_422_when_not_reversible`
- `test_undo_endpoint_404_when_missing`
- `test_undo_endpoint_403_when_cross_user`
- `test_undo_endpoint_idempotent_within_window`

`tests/unit/runtime_worker/test_approval_recognisers.py` — extend:

- `test_slack_post_message_is_reversible_yes`
- `test_slack_other_tools_remain_default`
- `test_unknown_vendor_recogniser_does_not_set_reversibility`

### 4.2 frontend

`apps/frontend/src/features/chat/hooks/useUndoCountdown.test.ts` (NEW):

- `it("ticks down once per second")`
- `it("expires when undoUntil is in the past")`
- `it("returns expired immediately when undoUntil is null")`
- `it("clears interval on unmount")`

`apps/frontend/src/features/chat/components/activity/ApprovalReceipt.test.tsx` (NEW):

- `it("renders an active Undo button while in window")`
- `it("does not render Undo button when undoUntil is null")`
- `it("renders Undo requested chip after onUndo fires")`

`ApprovalTool.test.tsx` — extend:

- `it("threads undo_expires_at from decision response into the receipt")`

### 4.3 Integration

- `RuntimeApiRouter` registers the route name; assertable via existing routing tests.

---

## 5 · Sequencing

1. ai-backend: schema (`undo_expires_at`, `ApprovalUndoResponse`, `UNDO_WINDOW_SECONDS`, event-type literal). Land standalone with validator tests.
2. ai-backend: service `request_approval_undo` + decision-response wiring. Tests.
3. ai-backend: route + router registration. Tests.
4. ai-backend: Slack recogniser opt-in. Tests.
5. api-types: mirror. Typecheck.
6. frontend: `useUndoCountdown` + tests.
7. frontend: `ApprovalReceipt` accepts new props + tests.
8. frontend: API client + `ApprovalTool` wiring + tests.
9. Final: full pytest + vitest sweeps.

Each step is independently mergeable; step 4 (Slack opt-in) can ship before or after step 8 (FE wiring) because the FE handles `null` gracefully.

---

## 6 · Risk register

| Risk                                                                                                  | Mitigation                                                                                                                                                          |
| ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| User believes "Undo requested" reverted the side effect (it doesn't yet).                             | Receipt copy explicitly says "Undo requested" not "Undone". A11y tooltip explains. Phase 4.1 lands compensators per vendor; copy flips when an outcome is observed. |
| Endpoint is hit after the agent run already completed and the tool result feeds something downstream. | Audit captures intent regardless. The lack of compensation in this PR is the limit — Phase 4.1 closes the loop.                                                     |
| Clock drift between server and client makes the countdown off.                                        | The server is authoritative: 410 on expired POST. Countdown is best-effort UI; it can show a few seconds of skew without harm.                                      |
| Idempotency race: two simultaneous POSTs from same user.                                              | Service writes idempotently to metadata; first writer wins. Both clients see the same `undo_requested_at`.                                                          |
| Adding more reversible tools per vendor requires schema changes.                                      | No — recognisers' `reversibility(tool_name, read_only)` is local to each class. Adding a tool name to a frozenset is one-line.                                      |
| `undo_expires_at` not present on old persisted decisions.                                             | Optional; FE handles `null` as "no undo button".                                                                                                                    |

---

## 7 · Out-of-scope follow-ups

- **Phase 4.1 — Slack compensator.** Plug Slack's `chat.delete` into the undo endpoint. Look up the original `chat.postMessage` result's `ts`, call delete, flip receipt copy from "Undo requested" to "Undone".
- **Phase 4.2 — Linear / GitHub / Notion / Atlassian compensators.** One per vendor, each its own focused PR.
- **Phase 5 — Risk policy emit upgrade.** Replace `low/medium` short-circuit with the real `permissions.py` policy; unblocks `RISK_HIGH` reason code in production.
- **Variable undo windows.** Today: 60s for everyone. Future: per-tool window (delete repo = 5min; post message = 60s).
- **Audit-log surfacing in UI.** Render `approval_undo_requested` rows in the audit timeline.
