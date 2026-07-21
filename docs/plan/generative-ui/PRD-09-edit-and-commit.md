# PRD-09 — Edit-on-surface + gated commit (Wave 3)

**Goal:** the _edit_ in accept/decline/edit, end-to-end on the flagship journey (email draft → review → **edit** → approve → send), generalized so record-field edits work the same way. Reviewer edits flow INTO the committed payload; commit is idempotent, precondition-checked, audited, fail-closed.

**Depends on:** PRD-01, 03, 04 (and 06 for hunk display). **Scope:** `packages/api-types`, `services/backend-facade` (passthrough only), `services/ai-backend`, `packages/chat-surface`.

## Contract additions (api-types)

- `ApprovalDecisionRequest` gains `decision: "approve" | "reject" | "approve_with_edits"` and `edits?: SurfaceEdits` where `SurfaceEdits = { fields?: Record<string,string>, body?: string, accepted_hunk_ids?: string[] }`.
- `approval_resolved` payload mirrors the applied edits (audit-visible).

## Backend (ai-backend)

| Area                                                                           | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| approvals handler (`runtime_worker/handlers/approval.py` + the decision route) | Accept `approve_with_edits`; merge `edits` into the pending proposal payload **server-side** (never trust the client to send the merged artifact — server re-derives final payload = proposal ⊕ edits); reject unknown edit keys (422)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| commit executor                                                                | NEW module `agent_runtime/capabilities/surfaces/commit.py`: given an approved proposal → (1) **precondition re-check**: re-read the remote resource when the connector supports it (draft version / record fields captured at propose-time vs now); drift ⇒ abort commit, emit a re-propose event, never partial-write; (2) **idempotency key** = approval_id, stored before the side-effect call; a retry/crash replay must not double-send (check-then-act around the store write); (3) execute the underlying tool call (send email via the draft's target connector; field writes via the originating MCP tool); (4) emit `tool_result` + terminal approval events; (5) append propose→decision→commit records to the audit-events path (`services/backend` audit ingestion — via the existing audit event emission, not a new channel) |
| fail-closed                                                                    | No approval token/record ⇒ no commit path exists; edits without `approve` are inert; `RUNTIME_SURFACE_EMISSION=false` does not disable the approval gate (independent axes)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

## Facade

- Passthrough only: the decision route already proxies; extend request-model to the new fields (no logic).

## Frontend (chat-surface)

| File                                                                                            | Change                                                                                                                                                                                                                                                                                                                                                                                 |
| ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/surfaces/edit/EditOverlay.tsx` + per-archetype forms (`MessageEditForm`, `RecordEditForm`) | NEW — host-owned edit UI mounted by `TcSurfaceMount` in an `editSlot` OVER the pure adapter (adapters stay input-free per D28). MessageEditForm: to/subject readonly v1, body textarea seeded from the proposal, hunk list (PRD-06 `DiffText` with `onHunkToggle`) → derives `accepted_hunk_ids` + edited `body`. RecordEditForm: per-changed-field inputs seeded with proposed values |
| `src/destinations/run/RunDestination.tsx`                                                       | EXTEND — `onSuggestChanges(diffId)` opens the overlay for the active archetype; overlay submit calls the decision endpoint with `approve_with_edits` + edits; cancel returns to pending. Optimistic + SSE reconcile as with plain approve                                                                                                                                              |

## Acceptance criteria

1. ai-backend unit: `approve_with_edits` merges body/field edits server-side; committed tool-call args contain the EDITED values (fake connector asserts); plain `approve` commits the unedited proposal.
2. Idempotency: replaying the commit handler for the same approval_id performs zero additional tool calls.
3. Precondition: fake connector reports remote drift ⇒ no write, re-propose event emitted, approval marked superseded.
4. Audit: propose→decision(with edits)→commit visible as ordered events for the run (assert sequence + payload fields).
5. FE tests: overlay opens from Suggest changes; hunk toggle excludes a hunk from `accepted_hunk_ids`; submit posts the right body; reject path unchanged.
6. Fail-closed test: decision endpoint with unknown approval 404s; commit executor without a stored approval record raises, sends nothing.
7. All touched suites green; facade contract test updated.

## Non-goals / guardrails

- v1 edit surfaces: message body + record fields only (table per-row editing rides the existing sheet per-row approve; doc/board editing later).
- No undo-after-commit (undo_requested flow exists separately — don't entangle).
- No new SSE concepts; decisions and results ride existing event types.
