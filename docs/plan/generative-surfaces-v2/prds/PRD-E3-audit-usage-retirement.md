# PRD-E3 — Audit hardening + usage endpoints + v1 retirement

**Goal.** Close the Generative Surfaces v2 loop: (1) make the run's accountability
artifact tamper-evident — a **receipt export** whose rows are HMAC-chained with the
existing `packages/audit-chain` signer, so flipping one byte anywhere in the export makes
verification fail; (2) finish the **usage endpoint** story — the `/v1/usage/*` family
(already facade-proxied) returns correct per-user / per-chat / per-run rollups covering
the v2 purposes (`view_shaping`, `shape_request`) and exposes `purpose`/`surface_id` on
per-call rows, proven by a seeded multi-run fixture through the facade; and (3) **retire
v1 surface emission** — delete the `result["surface"]` payload appendage and
`DraftSurfaceProjector` now that both hosts render exclusively from Work-Ledger events
(the SDR §11 compat window ends here). No UI is added (FR-G4: the Settings usage screen
stays out).

## Implementer brief

You are working in a **fresh git worktree branched off `main`** of the
`enterprise-search` monorepo (repo root = worktree root). Run `make setup` once if
`services/*/.venv` or `node_modules` are missing. Components touched:
`services/ai-backend`, `services/backend-facade`, `packages/api-types` (mirror only),
host flag files in `apps/frontend` + `apps/desktop` (default flip only),
`packages/chat-surface` (verification only). Test commands:

```bash
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/surfaces_v2/
cd services/ai-backend && .venv/bin/python -m pytest        # full suite before merge
cd services/backend-facade && .venv/bin/python -m pytest
npm run typecheck --workspace @0x-copilot/api-types
npm run test --workspace @0x-copilot/api-types
npm run test --workspace @0x-copilot/chat-surface           # must stay green after retirement
python tools/check_dark_capabilities.py                     # dark-cap gate, from repo root
```

Read these files first (repo-relative):

1. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 event vocabulary (authoritative), §8 usage design, §10.6 + §11 (audit hardening + compat-window end: this PR's charter).
2. `docs/plan/generative-surfaces-v2/03-prds.md` — PRD-E3 summary; its DoD items are binding minimums.
3. `packages/audit-chain/src/copilot_audit_chain/signer.py` — `AuditChainSigner`, `AuditChainRow`, `ChainVerificationResult`, `from_env`, canonicalization rules (TypeError on non-JSON-native).
4. `services/ai-backend/src/runtime_api/http/routes.py` — `UsageApiRoutes` (line 748, `usage_run` at 846 builds `by_call` at ~884), the `/v1/usage` router assembly (~1429), and `RuntimeApiRouter.create_router` (~566) where the export route registers.
5. `services/ai-backend/src/runtime_api/schemas/usage.py` — `RunUsageBreakdown` (239), `RunUsageCallRow` (261): the row you extend.
6. `services/ai-backend/src/agent_runtime/api/usage_service.py` — `UsageQueryService.rollup_purpose_rows` (225): purpose is a string dimension; v2 purposes flow through untouched.
7. `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py` — `_attach_surface` (234) + call site (224) + `SurfaceEmissionFlag` gate (252): the v1 emission you remove; `_surface_projector` (276) survives.
8. `services/ai-backend/src/agent_runtime/capabilities/backends/draft_backend.py` — `DraftSurfaceProjector` (428), `make_event_emitter` attach (590): deleted here.
9. `services/ai-backend/src/runtime_worker/stream_tools.py` — `_lift_surface_fields` (562, called at 557): dead after retirement.
10. `services/backend-facade/src/backend_facade/app.py` — `/v1/usage/*` inline routes (1170–1275) + `forward_json`: the passthrough pattern for the export route.
11. `services/ai-backend/src/runtime_api/http/audit_list_routes.py` — existing chain-fields-on-the-wire precedent (`_AuditChainView`, line 27).
12. `services/ai-backend/CLAUDE.md` + `services/ai-backend/tests/CLAUDE.md` — binding engineering + test rules (Keys/Values/Messages constant classes, no module-level helpers, typed errors, safe messages).

## Context

Generative Surfaces v2 re-founds the surface layer on a typed, append-only **Work
Ledger** riding the runtime's existing per-run event log; canvas, receipts, sources, and
usage totals are all projections of it (../02-sdr.md §2–§6). The requirements contract is
../01-problem-and-requirements.md: NFR-6 (immutable, session-accurate ledger; receipt
assembled from the ledger), FR-E2 ("Assembled from the run ledger · immutable"), and §2G
FR-G1–G4 (usage attribution plumbing, no UI).

This is **Wave E, PRD-E3** — the final PR of the program (../03-prds.md). Everything
else has merged: A1 contracts, A2 UsageMeter, A3 ledger emission + SurfaceStore, B1–B4
canvas/view lifecycle, C1/C2 classifier + gates, D1–D3 staged writes + CommitEngine, E1
receipt/sources folds, E2 approvals queue. E3 adds no new product behavior: it hardens
the receipt into a tamper-evident export (SDR §10.6 "audit-chain hashing hardens export
in the last wave"), proves the usage rollups end-to-end, and performs the sanctioned
cutover of SDR §11 — removing the v1 `result["surface"]` appendage and
`DraftSurfaceProjector` once both hosts read only ledger events. The retirement is the
**one deliberate behavior change** in this PR; it is flag-independent by design (dead
code is deleted, not gated).

## Interfaces consumed / exposed

**Consumed (must already be on `main`; VERIFY AT IMPL each symbol as actually merged —
earlier PRDs carried their own VERIFY markers, names may have drifted):**

- PRD-A1: `agent_runtime.surfaces_v2.ledger_ids.LedgerIdCodec.format/parse` (ledger id
  `r<short>·<seq>`); `copilot_service_contracts.work_ledger` (`LEDGER_EVENT_TYPES`
  14-value tuple, `load_ledger_golden_events()`); api-types `packages/api-types/src/ledger.ts`
  (`formatLedgerId`, entity types) — E3's mirror additions go in this file.
- PRD-A2: `RuntimeApiEventType.USAGE_RECORDED`; `Purpose.VIEW_SHAPING` /
  `Purpose.SHAPE_REQUEST`; `user_id` + `surface_id` columns on
  `runtime_model_call_usage` (migration `0002_usage_call_attribution.sql`); `UsageMeter`;
  the seam-gate test `tests/unit/test_llm_seam_gate.py` (must stay green).
- PRD-A3: `SurfacesV2Flag` (env `SURFACES_V2`, `agent_runtime/surfaces_v2/config.py`)
  and A2's `RuntimeExecutionSettings.surfaces_v2` — both read the same env var; VERIFY
  AT IMPL which reader(s) survived reconciliation; `WorkLedgerEmitter` (+ ContextVar
  `active()`), `CallMcpTool._emit_ledger` (the v2 hook that currently reads the
  v1-attached envelope off the result dict — restructured here, D4.1),
  `SurfaceStoreProjection.fold`.
- PRD-B1: host client flags (web `apps/frontend/src/app/featureFlags.ts`, key
  `enterprise.flags.surfaces-v2`, default **OFF** opt-in; desktop twin) — flipped to
  default-ON here (D5). VERIFY AT IMPL: exact helper names and whether a later 🎨 wave
  already flipped them.
- PRD-E1: `ReceiptFold.fold(*, run_id, events) -> RunReceipt` / `fold_raw` (pure, no
  IO, `agent_runtime/surfaces_v2/receipt.py`); the A1 entity `RunReceipt` (tiles + rows +
  `fold_ref` + `generated_at`; `through_seq` is embedded in `fold_ref` =
  `"ledger://<run_id>@<through_seq>"`, not a separate field); `receipt.emitted {v,
surface_id, fold_ref}` is emitted on **every terminal path**
  (completed/failed/cancelled/timed_out — E1 §3), so a receipt exists for any terminal
  run. E1 ships **no** `/receipt` HTTP route — the receipt is a canvas surface rendered
  from a client-side fold (E1 §4/§5); E3's export route below is the **first and only**
  receipt wire surface, and it re-folds via E1's `ReceiptFold`, never a stored blob.
- Existing (verified in this worktree): `AuditChainSigner` / `AuditChainRow` /
  `ChainVerificationResult` (`copilot_audit_chain`; on ai-backend's PYTHONPATH via root
  `Makefile:13-15`, wheel-installed in CI); `UsageQueryService` + `/v1/usage` router
  (`runtime_api/http/routes.py` 748/1429); facade `/v1/usage/{me,me/conversations,runs/{id},conversations/{id},org,org/subagents,org/purpose}`
  passthroughs (`backend_facade/app.py:1170–1275`); `forward_json`;
  `ConversationQueryService.replay_events` scope-check pattern.

**Exposed (this is the last PRD; consumers are operators + the future Settings UI):**

- `GET /v1/agent/runs/{run_id}/receipt/export` (runtime_api + facade) returning
  `ReceiptExportBundle` — the durable, tamper-evident audit artifact.
- `ReceiptExportBuilder` / `ReceiptExportVerifier` in NEW
  `services/ai-backend/src/agent_runtime/surfaces_v2/receipt_export.py`.
- `RunUsageCallRow.purpose` + `RunUsageCallRow.surface_id` (additive wire fields) — the
  future Settings → Usage screen reads these with zero backfill.
- A v1-free runtime: no `result["surface"]`, no `DraftSurfaceProjector`, no
  `SurfaceEmissionFlag` / `RUNTIME_SURFACE_EMISSION`.

## Design

### D1 — Receipt export bundle (audit-chain over the ledger)

NEW `services/ai-backend/src/agent_runtime/surfaces_v2/receipt_export.py`. Models are
Pydantic `RuntimeContract`s; all key strings in nested `Keys`/`Values` constant classes
per service rules. All names below are NEW.

```python
class ReceiptExportRow(RuntimeContract):
    seq: PositiveInt            # 1-based position in the export chain
    ledger_id: str              # LedgerIdCodec.format(run_id, sequence_no) -> "r<short>·<seq>"
    event_type: str             # SDR §5 wire value, e.g. "decision.recorded"
    sequence_no: PositiveInt    # the run-stream sequence
    created_at: str             # ISO-8601
    payload: dict[str, object]  # the envelope payload, model_dump(mode="json")
    prev_hash: str | None       # hex; None on the first row
    signature: str              # hex HMAC-SHA256
    key_version: int

class ReceiptExportBundle(RuntimeContract):
    export_version: Literal[1] = 1
    run_id: str
    generated_at: str           # ISO-8601
    receipt: RunReceipt         # E1's fold output (A1 entity), re-folded at export time
    rows: tuple[ReceiptExportRow, ...]
    head_hash: str              # hex of the last row's signature

class ReceiptExportBuilder:
    def __init__(self, *, signer: AuditChainSigner) -> None: ...
    def build(self, *, run_id: str, events: Sequence[RuntimeEventEnvelope],
              receipt: RunReceipt) -> ReceiptExportBundle: ...

class ReceiptExportVerifier:
    def __init__(self, *, signer: AuditChainSigner) -> None: ...
    def verify(self, bundle: Mapping[str, object]) -> ChainVerificationResult: ...
```

Chain semantics (export-time signing — stateless, works on all three runtime adapters,
no migration): rows are the run's ledger events in `sequence_no` order, **filtered to
the Work-Ledger vocabulary** (`event_type in LEDGER_EVENT_TYPES` — v2 events only; model
deltas/tool internals are not the accountability record). Per-row signing payload is
exactly `{"run_id", "event_type", "sequence_no", "created_at", "payload"}`, JSON-native
(`model_dump(mode="json")` first; the audit-chain canonicalizer raises `TypeError` on
anything else — treat as a bug, never catch). `sig = signer.sign(prev_hash=
<previous row's `.signature` bytes>, payload=...)` returns a `ChainSignature`
(`signer.py`: fields `prev_hash`/`signature`/`key_version`); the row's `signature` is
`sig.signature.hex()`, its `prev_hash` is `sig.prev_hash.hex()` (None on the first row),
per the `AuditChainRow` linkage model.
The **final chained row** is synthetic: `event_type = "receipt.export"`, `payload =
receipt.model_dump(mode="json")` — so tampering with the receipt object itself also
breaks verification. Because a synthetic row has no run-stream event, the builder assigns
its export-row fields deterministically: `seq = <count of ledger rows> + 1` (its position
in the export chain), `sequence_no = <highest folded ledger event's sequence_no> + 1`
(when the export has zero ledger rows, use `1`), `created_at = bundle.generated_at`,
`ledger_id = LedgerIdCodec.format(run_id, sequence_no)`. Its signing payload uses the same
`{"run_id", "event_type", "sequence_no", "created_at", "payload"}` shape as every other
row, with `payload = receipt.model_dump(mode="json")`.

`verify` reconstructs, **per row**, the exact same signing payload the builder signed —
`{"run_id": bundle["run_id"], "event_type", "sequence_no", "created_at", "payload"}`
(taking `event_type`/`sequence_no`/`created_at`/`payload` from the stored export row, NOT
a bare `row["payload"]`) — and builds `AuditChainRow(seq=row["seq"], payload=<that signing
dict>, prev_hash=..., signature=..., key_version=row["key_version"])`. Per `signer.py`,
`AuditChainRow.prev_hash` is `bytes | None` and `AuditChainRow.signature` is `bytes`, so
hex-decode the export row's hex-string `prev_hash`/`signature` back to bytes via
`bytes.fromhex` (`prev_hash` stays `None` on the first row). Return
`signer.verify_chain(rows)` — first break wins, `broken_at_seq` populated with that row's
`seq`.

Signer construction: `AuditChainSigner.from_env(environment_env_var="RUNTIME_ENVIRONMENT")`
— the same call the three runtime adapters make (`runtime_adapters/*/runtime_api_store.py`).
Call it inside `ConversationQueryService.export_run_receipt` (D2) to construct the
`ReceiptExportBuilder`; `from_env` raises `RuntimeError` when the environment is
`production` and `AUDIT_HMAC_KEY` is unset. Catch that `RuntimeError` there and re-raise a
NEW typed domain error (defined in `receipt_export.py`, e.g. `ReceiptExportUnavailable`)
carrying a safe public message with **no env/key detail**; the route maps it to HTTP 503.
Dev sentinel works out of the box (no key configured, non-production ⇒ verifiable chain).

### D2 — Export endpoint

- **runtime_api:** `GET /v1/agent/runs/{run_id}/receipt/export` registered in
  `RuntimeApiRouter.create_router` (`src/runtime_api/http/routes.py` ~566) beside
  `/runs/{run_id}/events` and A3's `/runs/{run_id}/surfaces` (E1 ships no `/receipt`
  route — this export is the receipt's first wire surface); router-level
  `RequireScopes(RUNTIME_USE)` applies. Handler follows `get_events`: scoped identity →
  NEW `ConversationQueryService.export_run_receipt(*, org_id, user_id, run_id)` in
  `src/agent_runtime/api/conversation_query_service.py` — run scope check (404 on
  wrong-tenant/unknown), `event_store.list_events_after(org_id=org_id, run_id=run_id, after_sequence=0)`
  (the port method is keyword-only — `list_events_after(*, org_id, run_id, after_sequence)`,
  the same call `ConversationQueryService.replay_events` makes), re-fold via
  `ReceiptFold.fold` (never a stored blob — E1's "no hand-assembled state" invariant),
  `ReceiptExportBuilder.build`. 409 (typed error, safe message) when the run is **not in a
  terminal status** (i.e. still running/queued) — E1 emits `receipt.emitted` on every
  terminal path (completed/failed/cancelled/timed_out, E1 §3), so a terminal run always has
  a foldable receipt while a non-terminal run does not; mirror E1's "409-on-non-terminal"
  rule (E1 Exposed §, not COMPLETED-only). Reuse the run's terminal-status check that E1's
  `ReceiptEmitter` gate and `RunTerminationCoordinator` already define; VERIFY AT IMPL the
  exact terminal-status set/predicate as merged.
- **facade:** inline route in `src/backend_facade/app.py` beside the other `/v1/agent/*`
  passthroughs: `authenticate_request` → `forward_json(app, "GET",
f"/v1/agent/runs/{run_id}/receipt/export", target="ai_backend",
params=identity.scoped_params(), identity=identity)`.
- **api-types:** `ReceiptExportBundle` / `ReceiptExportRow` interfaces added to
  `packages/api-types/src/ledger.ts` (A1's v2 module), exported from `src/index.ts`.
  Type-only mirror; no fetch wrapper.

### D3 — Usage endpoints: close the FR-G loop

The `/v1/usage/*` family already exists end-to-end (runtime_api router ~1429; facade
1170–1275). E3 adds **no new usage routes**. Deltas that make the rollups sufficient for
the future UI:

1. `RunUsageCallRow` (`src/runtime_api/schemas/usage.py:261`): add
   `purpose: str = "main"` and `surface_id: str | None = None`, populated in the
   `by_call` builder inside `UsageApiRoutes.usage_run` (`routes.py:846`, rows built at
   ~884) from the per-call record (`purpose`/`surface_id` columns exist on
   `runtime_model_call_usage` since A2). **Vocabulary note:** this row-level `purpose`
   uses A2's `Purpose` StrEnum (`agent_runtime/observability/attribution.py`) —
   `main`, `subagent_work`, `view_shaping`, `shape_request`, … — which is the _usage-row
   query dimension_ and is deliberately distinct from the closed 4-value `LedgerPurpose`
   (`run`/`subagent`/`view_shaping`/`shape_request`) carried on the SDR §5 `usage.recorded`
   _event_ (A2 maps `main`→`run`, `subagent_work`→`subagent`). Use the `Purpose` values
   here verbatim; do NOT normalize `main`→`run` for the row field.
2. Pin that `rollup_purpose_rows` (`usage_service.py:225`) buckets `view_shaping` and
   `shape_request` (string dimension — expected zero code change, test-only).
3. Seeded multi-run fixture proof through the facade (T3/T7): two users × two
   conversations × three runs with calls across purposes `main`, `subagent_work`,
   `view_shaping`, `shape_request` — per-user, per-conversation, per-run totals each
   equal the independent sum of seeded rows, asserted at the runtime_api boundary and
   re-asserted as facade passthrough.

No pricing changes (`ModelPricingCatalog` untouched; FR-G3 — tokens stored, dollars are
presentation-time).

### D4 — v1 retirement (the cutover)

Delete everything that emits or transports the v1 surface appendage. Exact sites
(line numbers verified in this worktree pre-E-wave — **re-grep every one at impl**):

1. `src/agent_runtime/capabilities/mcp/middleware/call_tool.py` — remove
   `_attach_surface` (234) and its `ainvoke` call site (224); remove the
   `SurfaceEmissionFlag` import (37) and gate (252). **Keep envelope computation**:
   `_surface_projector` (276) survives — the ladder (builtin → store → schedule
   generation) still feeds v2 `surface.created`/`view.derived`. Restructure: `ainvoke`
   computes the envelope only when `WorkLedgerEmitter.active()` is not `None`, then
   passes it **directly** to `_emit_ledger(...)` — A3's hook currently reads
   `result.get("surface")`/`result.get("surface_uri")`, which stop existing. VERIFY AT
   IMPL: `_emit_ledger`'s merged signature; and whether B3's ViewDeriver already rehomed
   derivation away from `SurfaceProjector.resolve` — if so, delete the projector call
   here and take the envelope from B3's path instead.
2. `src/runtime_worker/stream_tools.py` — remove `_lift_surface_fields` (562) and its
   call (557); `tool_result` payloads keep `{tool_name, call_id, status, output}`.
3. `src/agent_runtime/capabilities/backends/draft_backend.py` — delete
   `DraftSurfaceProjector` (428) and the `attach` call in `make_event_emitter` (590);
   drop the `SurfaceEmissionFlag` import (26). Draft surfaces render from D1-wave
   `write.staged`/`revision.added` events only.
4. `src/runtime_worker/handlers/run.py` (1087/1111) and
   `src/runtime_worker/handlers/approval.py` (877/894) — remove the
   `DraftSurfaceProjector.attach` calls + imports.
5. `src/agent_runtime/capabilities/backends/__init__.py` (7/10) and
   `src/agent_runtime/capabilities/surfaces/__init__.py` (37/86) — drop exports.
6. `src/agent_runtime/capabilities/surfaces/config.py` — delete `SurfaceEmissionFlag`
   (nothing else gates on it after 1–5; delete the file if empty). Update the
   comment-only references in
   `src/agent_runtime/capabilities/render_adapter_generator/config.py` (15/27). VERIFY
   AT IMPL: `grep -rn RUNTIME_SURFACE_EMISSION` across the whole repo (docs, deploy env
   files, desktop supervisor env) and scrub every hit.
7. Tests: rework `tests/unit/agent_runtime/capabilities/mcp/test_call_tool_surface.py`
   (attach assertions → envelope-computation + v2-emission assertions); delete
   `tests/unit/agent_runtime/capabilities/test_draft_surface_emission.py` and
   `tests/unit/runtime_worker/test_draft_surface_emitters.py` (their invariants are
   covered by D-wave staged-write tests — confirm before deleting; port anything unique).

**Preconditions (verify before starting; abort the retirement commit if any fails):**
(a) both hosts mount the v2 canvas (B1 flags exist and are ON in the validation stack);
(b) the v2 client projector sources surface **data** from ledger events + `payload_ref`
resolution against `tool_result.output`, never from `payload.surface` — enforced by T4;
(c) D1's staged-draft surface renders without `DRAFT_UPDATED` surface decoration in
both hosts.

### D5 — Flag default flips

With v1 gone, `SURFACES_V2=false` would mean "no surfaces at all". Flip defaults to
**on**, keeping explicit kill switches:

- Server: default `True` in the authoritative `SURFACES_V2` reader (A3's
  `SurfacesV2Flag` and/or A2's `RuntimeExecutionSettings.surfaces_v2` — VERIFY AT IMPL
  which survived; flip in one place, reconcile if both exist). `SURFACES_V2=false`
  remains the kill switch and gets explicit tests, so both branches stay lit for the
  dark-cap gate.
- **Dual-reader reconciliation (2026-07-23 close-out).** E3 is the one place the two
  `SURFACES_V2` readers — A2's `RuntimeExecutionSettings.surfaces_v2` settings field and
  A3's `SurfacesV2Flag.enabled()` — are **collapsed to a single authoritative reader**:
  E3 keeps exactly one, routes the other's call sites through it, and flips the default
  there (both read the same env var, so this is a code-path merge, not a rename). Deferred
  to here by design — see 05-consistency-report.md close-out item 2 and the A2/A3 flag notes.
- Client: flip B1's host flags from default-OFF opt-in to default-ON opt-out
  (web `apps/frontend/src/app/featureFlags.ts`; desktop twin — VERIFY AT IMPL exact
  helper names/paths as B1 merged them, and whether a later wave already flipped them).
  localStorage key `enterprise.flags.surfaces-v2` becomes the opt-out, mirroring the
  `isRunCockpitWebEnabled` fail-toward-ON pattern.

This flip is the SDR §11 consequence ("once both hosts are on v2") — record it in
../02-sdr.md §11 and flag it in the PR description for program sign-off.

## Implementation plan

1. **Export core.** NEW `services/ai-backend/src/agent_runtime/surfaces_v2/receipt_export.py`
   (D1: models, builder, verifier, `Keys`/`Values` constants, typed errors).
2. **Export endpoint.** Modify `services/ai-backend/src/agent_runtime/api/conversation_query_service.py`
   (`export_run_receipt`), `services/ai-backend/src/runtime_api/http/routes.py`
   (handler + registration + route-name constant beside A3's), and
   `services/backend-facade/src/backend_facade/app.py` (passthrough).
3. **Contracts.** Modify `packages/api-types/src/ledger.ts` + `packages/api-types/src/index.ts`.
4. **Usage deltas.** Modify `services/ai-backend/src/runtime_api/schemas/usage.py`
   (`RunUsageCallRow` fields) + the `by_call` builder in
   `services/ai-backend/src/runtime_api/http/routes.py` (~884).
5. **Retirement.** Apply D4 items 1–6 in one commit; rework/delete tests (D4.7) in the
   same commit so the suite never goes red between commits.
6. **Flag flips.** D5: `services/ai-backend/src/agent_runtime/settings.py` and/or
   `src/agent_runtime/surfaces_v2/config.py`; `apps/frontend/src/app/featureFlags.ts`;
   the desktop flag twin.
7. **Docs.** Update `docs/plan/generative-surfaces-v2/02-sdr.md` §11 (window closed,
   defaults flipped) and the §2 "deprecated" row; note divergences in the PR body.
8. **Gates.** Full suites, `npm run typecheck --workspace @0x-copilot/api-types`,
   chat-surface tests, `python tools/check_dark_capabilities.py`, pre-commit lint.

## Test plan

ai-backend (`cd services/ai-backend && .venv/bin/python -m pytest <path>`; fakes, no
network, typed-error + safe-message assertions per `tests/CLAUDE.md`):

- **T1** NEW `tests/unit/agent_runtime/surfaces_v2/test_receipt_export.py` —
  `test_build_chains_ledger_events_in_sequence_order` ·
  `test_final_row_covers_receipt_fold` · `test_verify_roundtrip_ok` ·
  `test_non_ledger_event_types_excluded` · adversarial (the DoD tamper suite):
  `test_flipped_byte_in_row_payload_fails_verification` (mutate one char in one row's
  `payload`, assert `ok=False` + `broken_at_seq` of that row) ·
  `test_flipped_byte_in_receipt_fails_verification` ·
  `test_reordered_rows_fail_verification` · `test_dropped_row_fails_verification` ·
  `test_forged_signature_with_wrong_key_fails` (second signer, different key) ·
  `test_key_rotation_verifies_old_rows` (rotation-key map, mirroring
  `packages/audit-chain/tests/test_signer.py` conventions).
- **T2** NEW `tests/unit/runtime_api/test_receipt_export_endpoint.py` (real
  `InMemoryRuntimeApiStore`) — scope mismatch ⇒ 404; non-terminal (still-running) run ⇒
  409; a terminal-but-failed/cancelled run ⇒ 200 (receipt exists per E1); happy path
  returns a bundle that `ReceiptExportVerifier` verifies; production env without
  `AUDIT_HMAC_KEY` ⇒ 503 with safe message (monkeypatched env).
- **T3** NEW `tests/unit/runtime_api/test_usage_rollups_e3.py` — the D3.3 seeded
  multi-run fixture through the runtime_api TestClient: per-user / per-conversation /
  per-run totals equal independent sums; `by_call` rows carry `purpose` + `surface_id`;
  `/v1/usage/org/purpose` has `view_shaping` + `shape_request` buckets.
- **T4** NEW `tests/unit/agent_runtime/surfaces_v2/test_v1_free_ledger.py` — the cutover
  keystone: drive a run with the fake model + a fake MCP read tool (pattern:
  `tests/unit/runtime_worker/test_fake_model_run_stream.py`), assert **no event payload
  anywhere in the run contains a `surface` or top-level `surface_uri` key**, while
  `surface.created`/`view.derived` v2 events still appear and
  `SurfaceStoreProjection.fold` yields complete canvas state (title, kind, payload_ref)
  — proving v2 needs nothing from v1.
- **T5** REWORK `tests/unit/agent_runtime/capabilities/mcp/test_call_tool_surface.py` —
  envelope computed only when emitter bound; result dict never mutated;
  `SURFACES_V2=false` ⇒ no envelope computation, no generation scheduling.
- **T6** Update the hermetic keystones (`test_fake_model_run_stream.py` + file twin) if
  their snapshots pinned `surface` keys — re-baseline deliberately, in the retirement
  commit, with a PR-body note (this is the sanctioned behavior change).

backend-facade (`cd services/backend-facade && .venv/bin/python -m pytest`):

- **T7** NEW `tests/test_receipt_export_proxy.py` — capture-`forward_json` pattern from
  `tests/test_approval_decision_proxy.py`: method/path/`target="ai_backend"`,
  `org_id`/`user_id` params, 401 without bearer, upstream 404/409/503 passthrough.
- **T8** EXTEND `tests/test_public_route_contract.py` —
  `/v1/agent/runs/{run_id}/receipt/export` present; `/v1/usage/*` family still present.

chat-surface: full package suite green post-retirement (no source change expected; if
v2 projector tests seeded `payload.surface`, fix the fixtures, not the projector).

**Live desktop E2E (the S1–S6 story, DoD item 3):**

1. Stage + launch (`node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64`,
   then `COPILOT_RUNTIME_DIR="$PWD/apps/desktop/resources" npm run dev --workspace
@0x-copilot/desktop`) with a BYOK key, `SURFACE_SPEC_MODEL` set, and `SURFACES_V2`
   **unset** (default-on proof).
2. S1: prompt hitting an authenticated MCP read tool → canvas tab renders; chat shows
   "auto-ran (read)"; `usage.recorded` events stream mid-run.
3. S2: revoke a connector token first → gate card, run parks; reconnect ("Ask me
   first") → run resumes at the same call.
4. S3: draft write → rev-pinned approve bar → free-form edit → rev 2 → approve →
   applied; receipt row "rev 2 · you approved".
5. S4: bulk staged write → override one pre-held row → "Apply N" → partial accounting
   correct.
6. S5: uncurated tool → generic view now, shaped upgrade later; "Suggest a shape" from
   the fallback works and is metered.
7. S6 + export, against a `make dev` stack: `export TOKEN=$(make dev-bearer)`; `curl -H
"Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/agent/runs/<run_id>/receipt/export`
   → save bundle; verify with `ReceiptExportVerifier` in a REPL; flip one byte in the
   saved JSON, re-verify ⇒ fails.
8. Usage via :8200: `/v1/usage/me`, `/v1/usage/runs/<run_id>`,
   `/v1/usage/conversations/<conv_id>` — totals consistent; `by_call` rows show
   `purpose: view_shaping` for the shaping calls.
9. Kill switch: relaunch with `SURFACES_V2=false` → no canvas surfaces, no v2 events,
   runs still complete (chat-only degradation, no crash).
10. Grep the streamed `/events` payloads of steps 2–7 for top-level `"surface"` /
    `"surface_uri"` keys ⇒ zero hits (v1 gone live, not just in tests).

## Definition of done

From ../03-prds.md PRD-E3 (binding minimums, never weakened):

- [ ] **Receipt export verifies against the chain (tamper test flips a byte,
      verification fails).** Proof: T1 adversarial suite + live step 7.
- [ ] **Usage endpoints return correct rollups for a seeded multi-run fixture via
      facade.** Proof: T3 (correctness at source) + T7/T8 (facade passthrough intact) +
      live step 8.
- [ ] **v1 emission removed; full-suite green; dark-cap/lint gates 0; live desktop E2E
      of the whole S1–S6 story.** Proof: D4 diff (no `_attach_surface`, no
      `DraftSurfaceProjector`, no `SurfaceEmissionFlag`, no `_lift_surface_fields`,
      repo-wide `RUNTIME_SURFACE_EMISSION` grep zero); T4 + live step 10 grep-zero; both
      service suites + all workspace suites green; `python tools/check_dark_capabilities.py`
      exits 0; pre-commit (ruff/prettier) clean; chat-surface eslint + surface-renderers
      `npm run lint:negatives` clean; the live script executed end-to-end and logged in the
      PR body.

Standard DoD (every PRD):

- [ ] Unit tests pass in each owning component's venv/workspace; typecheck + build green
      (`api-types`, `chat-surface`).
- [ ] Flags off ⇒ byte-identical behavior — **re-scoped for this PR per SDR §11:** the
      export/usage additions are additive and flag-independent; the v1 removal is the
      sanctioned end of the compat window and changes flag-off behavior deliberately (T6
      re-baseline + live step 9 prove the kill switch degrades cleanly, not
      byte-identically). This re-scope is called out in the PR description.
- [ ] No service-boundary violations: facade calls ai-backend over HTTP only;
      audit-chain consumed as the shared package (never vendored); nothing surface-related
      enters `services/backend`.
- [ ] No new LLM call sites (A2 seam-gate test green — vacuously satisfied).
- [ ] Docs: ../02-sdr.md §10.6/§11 updated (export design, window closed, default
      flips); divergences recorded in the PR description.

(Not a 🎨 PRD — no new UI is built, so the design-parity DoD does not apply; existing
canvas parity baselines must simply stay green.)

## Out of scope

- The Settings → Usage screen or any usage UI (FR-G4); pricing tables / dollar math.
- New usage routes or aggregation dimensions beyond D3's additive fields.
- Removing FE legacy v1 projection paths in `packages/chat-surface`
  (`eventProjector.ts` spec-merge on `payload.surface`, `_approvals-stub.ts` TODO) —
  they receive nothing after this PR; deletion is a tracked follow-up, not folded in.
- Signed export for anything other than runs (org-wide audit export stays on the
  per-adapter `write_audit_log` chains + `audit_list_routes.py`).
- Chain-signing ledger events **at write time** — tamper evidence is export-time by
  design; revisit only if a compliance review demands at-rest chaining.
- Key management/rotation tooling beyond `AuditChainSigner.from_env`.

## Guardrails

- **Service boundaries (hard):** apps → facade only; facade never imports Python from
  either backend; ai-backend owns export + usage logic; `packages/audit-chain` consumed
  via its package (PYTHONPATH in dev, wheel in CI) — no copied signer code.
- **ai-backend rules** (`services/ai-backend/CLAUDE.md`): Pydantic at every boundary; no
  module-level helper functions (builder/verifier are classes); `Keys`/`Values`/
  `Messages` constant classes, no inline strings; typed domain errors with safe public
  messages — never leak `AUDIT_HMAC_KEY` state or paths in HTTP responses; content never
  logged at INFO+ (ledger ids and counts only).
- **Test rules** (`services/ai-backend/tests/CLAUDE.md`): fakes/mixins, no network, no
  live LLM; assert typed error classes and safe messages; this service's `.venv` only.
- **Audit-chain discipline:** payloads JSON-native before signing
  (`model_dump(mode="json")`); the dev sentinel key stays byte-identical across releases
  (pinned in `packages/audit-chain/tests/test_signer.py` — do not touch); verification
  uses the package's constant-time compare, never hand-rolled.
- **Retirement discipline:** delete, don't gate — no `if legacy_surface_enabled`
  resurrection paths; the retirement commit is atomic (code + tests together); re-grep
  every D4 line number before editing (E-wave PRs will have shifted them).
- **Vocabulary freeze:** the 14-event v2 vocabulary and payload fields stay
  additive-only; the export's synthetic `"receipt.export"` row is an export-format
  construct, NOT a new ledger event type — never appended to the run stream nor added
  to `LEDGER_EVENT_TYPES`.
- **Facade contract:** every new public route mirrored in `packages/api-types` and added
  to `test_public_route_contract.py`; error passthrough via `_upstream_error_detail`
  (never invent facade-side error shapes).

## Open questions

Non-blocking design choices surfaced during the implementability pass — none block
starting the PR; each has a safe default noted so a first cut is unambiguous.

- **Export response-size bound.** `GET /receipt/export` returns the _entire_ ledger-event
  chain plus the synthetic receipt row in one JSON body. A run with a very large ledger
  (thousands of `read.executed`/`write.staged` events) could produce a multi-MB signed
  bundle with no cap, pagination, or streaming. The SDR and requirements are silent on an
  upper bound. **Default for this PR:** return the full bundle unpaginated (matches the
  "durable, tamper-evident artifact" intent — the whole chain must sign as one unit);
  revisit a size cap or chunked/streamed export only if a large-run measurement or a
  compliance review demands it. Decide whether to add a defensive `413`-style guard now.
