# Generative UI — session state (compacted)

**Date:** 2026-08-05 · **Branch:** `claude/dynamic-generative-ui-audit-28d256` · **PR:** #562 (open, not merged)
Companion: [FINDINGS.md](FINDINGS.md) (evidence) · [HERMES-COMPARISON.md](HERMES-COMPARISON.md) ·
[../../plan/generative-ui-floor/PRD.md](../../plan/generative-ui-floor/PRD.md)

## One paragraph

An MCP read is supposed to become a rendered table. It never has. Fourteen defects sat between the tool
result and the screen; thirteen are now fixed and verified, and the fourteenth is root-caused with hard
evidence. The backend is proven correct on the live wire. The remaining break is a compatibility shim
that mis-claims every current surface.

## Verified working (live wire, packaged app)

`surface.created` carries `state{spec, source, data}` with real rows. Confirmed by reading a live run's
`events.jsonl`, and by replaying those events through the endpoint's own code path
(`SurfaceStoreProjection` refs → `SurfaceContentProjection.fold`) → `['data', 'source', 'spec']`.

The client is also correct: it fetches, receives `hasState: [true]`, and stores under the right key.
Proven by instrumenting `useSurfacesV2` and reading `window.__DIAG` from the running app.

## THE REMAINING DEFECT — `projectLegacyV2Replay` claims every modern surface

Live client trace:

```
fetch:   ids: ["table://incidents/list_incidents/1532a206699e"]  hasState: [true]
stored:  keys: ["table://incidents/list_incidents/1532a206699e"]
resolve: uri: "table://legacy-v2/table%3A%2F%2Fincidents%2F…"  legacy: true  resolved: false
```

The canvas asks for a percent-encoded `legacy-v2://` URI, is flagged `legacy: true`, and therefore looks
in `legacyV2StateByUri` (empty) instead of the hydration map (correct).

**Cause.** `isLegacySurfaceCreated` / `hasLegacyProof` decide "is this record historic?" from five signals,
and every one matches CURRENT data:

| Signal                     | Why it matches current data                                                                                     |
| -------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `isCallRef(payload_ref)`   | `call:<id>` is today's format (`emitter.py`)                                                                    |
| `LEGACY_PROOF_EVENT_TYPES` | contains `action.classified`, `read.executed`, `gate.opened`, `receipt.emitted`, `usage.recorded` — all current |
| `isConnectorSubject`       | `connector:` prefix guess                                                                                       |
| `legacyStreamProven`       | derived from the above                                                                                          |

So the answer is **yes for every live connector read**, permanently. This predates all of today's work.

## RECOMMENDED FIX — delete the lane, don't patch it

| Piece                                                                                                     | Size     |
| --------------------------------------------------------------------------------------------------------- | -------- |
| `packages/api-types/src/legacyV2Replay.ts`                                                                | 588      |
| `services/ai-backend/src/agent_runtime/surfaces_v2/legacy_v2_replay.py`                                   | 652      |
| `packages/api-types/src/legacyV2Replay.test.ts`                                                           | 122      |
| `legacy_v2_replay_corpus.json` (pinned cross-language)                                                    | 11.5 KB  |
| client consumers (`legacyUris`, `legacyV2StateByUri`, `legacyV2ReadOnlyStream`, `legacy-v2:` tab minting) | 27 sites |

**Deleting cannot make anything worse.** A genuinely historic surface renders an empty card today (the
shim mis-claims it too). After deletion it takes the canonical path — at worst the same, at best correct.
The shim has never successfully served a historic surface to a user; if it had, someone would have seen a
legacy tab with content in it.

Nothing replaces it: the canonical projector already owns `surface.created` end to end.

**Verify first (cheap):** nothing outside surfaces consumes `LegacyV2ReplayProjection` (receipts and
artifacts have their own lanes), and no journey or CI gate asserts on legacy replay mode.

**Attempted alternative, rejected:** replacing the five signals with one (`carries inline state ⇒ current`)
works — 122/123 — but the last failure is the pinned Python/TS corpus, so it needs a coordinated
three-file reader-version bump to keep behaviour we do not want. Reverted; tree is green.

## Landed this session

- rung 0 deterministic inference (`capabilities/surfaces/infer.py`) — no model, never fails
- spec + full state delivered on `surface.created` (transport allow-list was silently stripping it)
- `_output_of` no longer drops successful MCP calls
- shape matching (curated specs as templates) + learned cache
- model REFINES the inferred spec; BYOK credential finally reaches the shaping model
- archetype handshake — generator licensed from a shared contract (5, not 10)
- "No spec matched" deleted; fallback renders the payload
- badge chips; title falls back to the tool name
- **−1,068 lines** of verified-dead code (`view_deriver.derive` path, `SurfaceCommitExecutor`)
- guardrails: `check_dark_capabilities` predicate widened (immediately found 2 hidden dark flags),
  5 orphaned CI gates wired, hermetic e2e test driving `append_api_event`
- `tools/desktop-journeys/surface-floor/` — the live journey that found defects 11–14

**9,094 tests passing.** Uncommitted. Client diagnostics still in tree, marked `TEMP DIAGNOSTIC`.

## Why unit tests never caught any of this

Every defect sat at an injected seam — a scheduler, a store, a completion port, a tuple half, a call id.
Tests inject past seams. The fix is structural: fake at the EXTERNAL boundary (what the journey does),
not at internal ones. That is the single most valuable thing to carry forward.
