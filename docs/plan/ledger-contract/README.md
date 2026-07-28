# Ledger contract — program overview

Two changes that close the defect class behind PR #413, and the P0 that PR made
user-reachable.

Both are ledger-contract work. They are one program because they share a root
cause: **the run ledger is the system's spine, but its contract is enforced in
disconnected pieces, and every gap between those pieces fails silently.**

| PRD                                             | Closes                 | Tier             | Effort  |
| ----------------------------------------------- | ---------------------- | ---------------- | ------- |
| [PRD-01](PRD-01-emit-side-vocabulary-parity.md) | GS-ARCH-07, part of 04 | P0 · leverage    | ~1 day  |
| [PRD-02](PRD-02-conversation-scoped-canvas.md)  | GS-ARCH-05             | P0 · user-facing | ~3 days |

## Why these two, in this order

PR #413 fixed two defects that were the same shape at different boundaries:

1. a causal event emitted **after** the run's terminal event, so the SSE stream
   had closed before it existed;
2. an event type missing from the client's transport tuple, so `parseEnvelope`
   dropped it **silently**.

Both were invisible at every layer. The backend logged a successful append, the
stream carried the frame, and the client simply never saw it. The Studio canvas
then correctly reported "no artifact was created" about an artifact that
demonstrably existed.

The ledger crosses four boundaries. After #413, three are guarded:

| Boundary                          | Guard                                                          |
| --------------------------------- | -------------------------------------------------------------- |
| emit → `RuntimeApiEventType(...)` | **none** ← PRD-01                                              |
| persist → ledger                  | `test_event_literal_gate_v2_1.py`                              |
| transport → client                | `ledgerTransportParity.test.ts` + `test_api_type_contracts.py` |
| client → projection               | **none** — unreachable branches still compile and pass         |

PRD-01 closes the emit boundary and adds a consumer-side reachability check.
Leaving it half-done is worse than not starting: the contract _looks_ guarded.

PRD-02 exists because #413 changed which bug a user meets first. Before it, an
artifact could never reach the canvas at all, which masked the fact that canvas
identity is run-scoped. Now the first message renders the table and the **second
message wipes it** — producing the identical error string as the bug just fixed.

## Impact

### What PRD-01 prevents

- **A whole defect class, at authoring time.** `LedgerEventType ⊆ RuntimeApiEventType`
  is a one-line invariant. Violating it today produces either a `ValueError` deep
  in an emitter or a silent client drop, depending on which side is missing.
- **A specific live failure.** `gate.opened.v2` / `gate.resolved.v2` raise on
  conversion. The workspace grant-block path emits `GATE_OPENED_V2` with no
  `try/except`, so a blocked workspace operation currently throws instead of
  opening a gate. Dormant only because `RUNTIME_ENABLE_DESKTOP_WORKSPACE=false`.
- **A false comment guiding future work.** `_build_work_ledger_emitter`'s
  docstring asserts "both enums carry identical values". That is untrue, and it
  is the sentence a future author would trust.

### What PRD-01 unlocks

- **GS-ARCH-07** — "gates and provider receipts join the operation" becomes
  achievable; today the transport cannot carry them.
- **GS-ARCH-04** — "revocation is immediate" needs a gate the client can see.
- `projectCanvasLifecycle`'s `parked` state, which is unreachable code today.

### What PRD-02 fixes

- **GS-ARCH-05's first clause** — "a chat-only follow-up preserves the open
  surface" — which is the P0 a user hits today.
- Prevents the regression report that PR #413 otherwise generates.

### What PRD-02 unlocks

- Conversation-scoped artifact listing, which **GS-ARCH-06** ("retained while
  referenced") and **GS-ARCH-12** ("reference graph authoritative for deletion,
  retention, legal hold") both need. `ArtifactListQuery.run_id` is required
  today, so no caller can ask "what artifacts belong to this conversation?"

## Evidence status

Stated so reviewers know what is proven versus reasoned:

| Claim                                                              | Status                                           |
| ------------------------------------------------------------------ | ------------------------------------------------ |
| `gate.*.v2` raise on `RuntimeApiEventType(...)`                    | **Verified** — executed against the enum         |
| Backend emits `GATE_OPENED_V2` unguarded                           | **Verified** — read at the call site             |
| Canvas identity is run-scoped; a new run replaces `session.events` | **Verified** — code + its design comment         |
| A follow-up message visibly drops the artifact tab                 | **Not reproduced** — PRD-02 AC-1 proves it first |
| `ArtifactListQuery` has no conversation scope                      | **Verified** — `run_id` is required              |

## Out of scope

Whether the v2 gate pair should be _added_ to the runtime enum or the emit
_removed_ is a D1/C2 product decision, not a contract one. PRD-01 §Decision
frames it and blocks on an answer; the guard lands either way.
