# Cross-PRD Consistency Report — Generative Surfaces v2

**Stage 3 sweep · 2026-07-23.** Enforced across all 15 PRDs (`prds/PRD-*.md`) against the
authoritative contracts: `02-sdr.md` §5 (event vocabulary, verbatim), the ledger-id format
`r<short>·<seq>`, the runtime flag `SURFACES_V2`, and `01-problem-and-requirements.md`.
Seven enforcement axes; each finding below is tagged with its axis:

1. event names/fields match SDR §5 verbatim
2. flag names identical (runtime `SURFACES_V2` + one chat-surface canvas flag)
3. endpoint paths identical between producer PRD and consumer PRDs
4. ledger id format `r<short>·<seq>` everywhere
5. every "Interfaces consumed" item is actually "Interfaces exposed" by the named PRD
6. no two PRDs create the same new file with conflicting content plans
7. later-wave additive payload fields reflected back into A1 as versioned additions

---

## Summary of fixes applied

| #   | File(s) edited     | Axis | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| --- | ------------------ | ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| F1  | `PRD-E3` (×2)      | 3, 5 | Removed the false claim that E1 ships `GET /v1/agent/runs/{run_id}/receipt`. E1 explicitly ships **no** receipt route (receipt is a canvas surface + client fold); E3's `…/receipt/export` is the first receipt wire surface. Re-pointed "registered beside" to `/runs/{run_id}/events` + A3's `/runs/{run_id}/surfaces`.                                                                                                                                   |
| F2  | `PRD-B4`           | 5    | Test `test_model_fallback_chain` contradicted B4's own design (it fell back to a bare `SURFACE_SPEC_MODEL` read; the design forbids that and routes through B3's `ShapingModelResolver`). Renamed to `test_model_resolution_chain` and corrected to the resolver chain → `shaping_unavailable` (422).                                                                                                                                                       |
| F3  | `PRD-A1`           | 7    | Added **D6 — Forward-additive registry**: a documentation-only table recording every additive field/event later PRDs introduce (D1 `proposal_ref`/`authorship_spans`; D2 `failure`/`decided_by`; D3 `rowset`/`apply`/`row_results`/activated `row_keys`; B3 `gen.ms`/`spec_ref`; **B4 new event `shape.resolved`**), all `v:1`-additive, with the note that E3 freezes the vocabulary and the export's synthetic `receipt.export` row is not a ledger type. |
| F4  | `PRD-B2`, `PRD-B3` | 3    | Aligned the shape-request path param from `{id}` to B4's canonical `{surface_id}` (`POST /v1/agent/surfaces/{surface_id}/shape-request`).                                                                                                                                                                                                                                                                                                                   |

---

## Per-PRD findings

### PRD-A1 — Ledger vocabulary + contracts

- **Verified:** owns the SDR §5 vocabulary verbatim (14 events, field names, enum values incl. the `class` alias and `actor: Literal["user"]` on `view.preference`/`shape.requested`). Ledger-id codec `r<short>·<seq>` defined here (axis 4 root). Exposes the exact symbols A2/A3/B/C/D/E consume.
- **Fixed (F3):** added the forward-additive registry (D6) so A1 is the contract home for the later-wave additions rather than each PRD asserting them in isolation.
- **Note (axis 6, low):** A1 creates `packages/api-types/src/ledger.ts` (v2 domain module, re-exported from `index.ts`). Several later PRDs (B3, B4, C2, D1, D3, E2) say "add to `packages/api-types/src/index.ts`". This is a target-module nuance, not a file-creation conflict — additions belong in `ledger.ts` (A1/A3/E3 correctly target it). Left as-is; flagged for implementers.

### PRD-A2 — UsageMeter seam + store

- **Verified:** `usage.recorded {purpose, model, tokens_in, tokens_out, surface_id?}` matches SDR §5. Purpose vocabulary correctly split — closed 4-value `LedgerPurpose` on the event vs 12-value `Purpose` store dimension (E3 D3 re-states this split consistently). Flag `SURFACES_V2` (axis 2). Exposes `MeteredModelInvocation`, `Purpose.VIEW_SHAPING`/`SHAPE_REQUEST` that B3/B4 consume verbatim.
- **Note (reconciliation, tracked in E3):** A2 registers the flag as `RuntimeExecutionSettings.surfaces_v2` while A3 ships a separate `SurfacesV2Flag.enabled()` reader — **same env var `SURFACES_V2`**, so the flag _name_ is consistent; the dual-reader reconciliation is explicitly deferred to E3 D5 ("VERIFY AT IMPL which reader(s) survived"). Not a naming drift.

### PRD-A3 — Ledger emission + SurfaceStore projection

- **Verified:** produces `GET /v1/agent/runs/{run_id}/surfaces` (axis 3 root) consumed identically by B1/B2/B3/B4/E1. Emits `action.classified`/`read.executed`/`surface.created`/`view.derived` with SDR §5 field names; `view.derived.gen` carries `model` only (B3 completes `ms`/`spec_ref` — reflected in A1 D6).
- **Open coordination (not drift):** A3 OQ1 + B1 OQ1 — `Surface` (A1 entity) vs `SurfaceSnapshot` (A3 fold output) and the shared parity-snapshot schema. Both PRDs flag it as an owner decision; recommendation (keep `SurfaceSnapshot` additive) is consistent across both. Left for sign-off.

### PRD-B1 — Canvas mount + tabs

- **Verified (axis 2, canvas flag definer):** B1 is the source of the chat-surface canvas flag — package prop `surfacesV2` (one name), host helpers `isSurfacesV2CanvasEnabled()` (web) / `isSurfacesV2Enabled()` (desktop), localStorage key `enterprise.flags.surfaces-v2`. B2/B3/C2/E1/E2/E3 all reference this exact set. The web/desktop helper asymmetry is intentional per B1 and consistently cited everywhere.
- Consumes A1 + A3 exports that are actually exposed. ✓

### PRD-B2 — Provenance footers + raw fallback

- **Fixed (F4):** `{id}` → `{surface_id}` on the reserved shape-request path.
- **Verified:** footer fields all sourced from SDR §5 payloads (`surface.created`/`read.executed`/`action.classified`/`view.derived`); ledger id via A1 formatter. Exposes `onCopyText`/`onSaveFile` that E1 consumes. ✓

### PRD-B3 — View lifecycle

- **Fixed (F4):** `{id}` → `{surface_id}` in Out-of-scope shape-request reference.
- **Verified:** produces `POST /v1/agent/surfaces/{surface_id}/regenerate` and `…/view-preference`; B4 consumes the "same pattern" note and `ShapingModelResolver`, both actually exposed by B3. `view.preference {surface_id, keep, actor: user}` matches SDR §5. Adds `gen.ms`/`spec_ref` (A1 D6).
- **Open (both sides agree):** B3 OQ1 (surface→run resolution — endpoints keyed on `surface_id` need `run_id`; default = run_id query param) and OQ2 (raw-surface `surface.created` owner). B4's runner also depends on B3's `payload_ref` loader; both flag the same seam. Consistent.

### PRD-B4 — Suggest-a-shape

- **Fixed (F2):** resolver-chain test corrected.
- **Fixed (F3, A1 D6):** `shape.resolved` (the NEW outcome event B4 adds) is now registered in A1's forward-additive table as the appended 15th event type, keeping A1 the contract home. B4 still owns the SDR §5 update + JSON/enum/union append at merge.
- **Verified:** produces `POST /v1/agent/surfaces/{surface_id}/shape-request` (SDR §4, axis 3). Consumes B3/A2/A3 symbols that are exposed. Metering `usage.recorded {purpose: shape_request}` matches SDR §5.

### PRD-C1 — ActionClassifier + policy

- **Verified:** `action.classified {call_id, connector, op, class, basis}` verbatim; enum values (`read|write|unknown`, `catalog|annotation|default`) match A1's JSON. Exposes `ActionClassifier.classify`, `EffectiveActionPolicyResolver`, backend `PUT /internal/v1/mcp/servers/{server_id}/write-policy` + `write_policy` on `McpServerResponse` — all consumed by C2/D3 as exposed. Launch catalog = the 7 builtin-spec connectors (asana, atlassian, github, intercom, linear, notion, sentry) covering the 12 builtin specs — matches the resolved decision #3. ✓

### PRD-C2 — ToolAccessGate

- **Verified:** `gate.opened`/`gate.resolved` field names + enums (`missing|expired|insufficient`, `connected|cancelled`, `ask_first|allow_always`) verbatim from SDR §5. Consumes C1's classifier + `server_id`-keyed override endpoint exactly as C1 exposes them. `gate_id` == deterministic approval id. Consumes B1 projector/flag as exposed. Exposes gate events + `TcGateCard`/`PostureChip` that E2 consumes. ✓
- OQ1 (`INSUFFICIENT` never emitted at launch) and OQ2 (slug↔server_id when two servers share a connector) are honest launch-scope deferrals, not drift.

### PRD-D1 — Staged-write engine (single artifact)

- **Verified:** produces `POST /v1/agent/stages/{stage_id}/decisions` (SDR §4), `…/revisions`, `GET /v1/agent/stages/{stage_id}` — consumed identically by D2/D3/E2. Additive `proposal_ref`/`authorship_spans` on `revision.added` now in A1 D6 (axis 7). `write.applied` intentionally absent (D2 owns the sole producer). ✓

### PRD-D2 — CommitEngine

- **Verified:** sole producer of `write.applied`; consumes D1's exposed engine/routes/fold verbatim. Additive `failure`/`decided_by` + `connector_receipt_ref` format now in A1 D6. Registers **no** apply route (approve enqueues) — D3 correctly notes this and adds `/apply`. ✓

### PRD-D3 — Bulk row-set

- **Verified:** produces `POST /v1/agent/stages/{stage_id}/apply` + extends `…/decisions` with row scope. Activates SDR §5 dormant fields (`rows`, `agent_holds`, `row_keys`, `result: partial`) + additive `rowset`/`apply`/`row_results` — all in A1 D6. `actor: "policy"` for allow-always matches SDR §5 + C1. Consumes D1/D2/C1 as exposed; agent pre-holds never auto-approved (FR-C8) — consistent with C1/C2 policy semantics. ✓

### PRD-E1 — Receipt + Sources

- **Verified:** emits `surface.created {kind: receipt}` + `receipt.emitted {surface_id, fold_ref}` (SDR §5). `ReceiptAttribution` union values match A1 exactly. Folds D1/D2/D3 payloads as exposed. Exposes `ReceiptFold.fold` + the expected-receipt fixture that E3 re-folds.
- **Fixed indirectly (F1):** E1 correctly states it ships no `/receipt` HTTP route; E3 no longer contradicts this.

### PRD-E2 — Approvals queue + Agents tab

- **Verified:** produces `GET /v1/agent/pending-work`; consumes only SDR §5 events + D1's `StagedWriteFold` + B1's `tabUriForSurface`, all exposed. Emits no new events. Pending predicate defined once, both languages, against the shared A1 golden fixture. ✓

### PRD-E3 — Audit hardening + usage + retirement

- **Fixed (F1):** removed the two references to a non-existent E1 `GET /v1/agent/runs/{run_id}/receipt` route. E3's 409-on-non-terminal correctly mirrors E1's emit-at-every-terminal rule (E1 _does_ expose that rule).
- **Verified:** produces `GET /v1/agent/runs/{run_id}/receipt/export`; additive `RunUsageCallRow.purpose`/`surface_id`; flag-default flip (D5) is the documented SDR §11 consequence. Freezes the 14(+`shape.resolved`)-event vocabulary; the synthetic `receipt.export` row is explicitly not a ledger type (consistent with A1 D6). ✓

---

## Axis-level verdicts

- **Axis 1 (event names/fields):** consistent after F2/F3. Every event/field/enum traces to SDR §5 verbatim; all later additions are optional/appended (`v:1`) and now catalogued in A1 D6.
- **Axis 2 (flag names):** consistent. Runtime `SURFACES_V2` everywhere; one chat-surface canvas flag (`surfacesV2` prop; B1's `isSurfacesV2CanvasEnabled`/`isSurfacesV2Enabled` host helpers) referenced identically by all consumers.
- **Axis 3 (endpoint paths):** consistent after F1/F4. Producer↔consumer paths match for `/runs/{id}/surfaces`, `/surfaces/{surface_id}/{regenerate,view-preference,shape-request}`, `/stages/{stage_id}/{decisions,revisions,apply}`, `GET /stages/{stage_id}`, `/pending-work`, `/runs/{id}/receipt/export`.
- **Axis 4 (ledger id):** consistent — `r<short>·<seq>` everywhere, single codec in A1.
- **Axis 5 (consumed↔exposed):** consistent after F1/F2. The only remaining forward-references (B3↔B4 `shape_request` seam; B3 surface→run resolver) are mutually acknowledged open questions with agreed defaults, not contradictions.
- **Axis 6 (file conflicts):** no two PRDs create the same file with conflicting plans. One low-severity target-module nuance (`ledger.ts` vs `index.ts` for api-types additions) noted for implementers.
- **Axis 7 (additive → A1):** done via A1 D6.

## Unreconcilable / needs owner sign-off (out of a docs sweep's authority)

1. **SDR §4 lists `/v1/agent/surfaces/{surface_id}` (get/replay)** which **no PRD produces** — the PRDs resolve a surface via `GET /v1/agent/runs/{run_id}/surfaces` (A3) and key mutating routes on `surface_id` + a `run_id` query param (B3 OQ1). SDR §5 is frozen-authoritative and was not edited; this is an SDR-vs-PRD divergence to reconcile at the SDR level, not a PRD-to-PRD drift.
2. **Dual `SURFACES_V2` reader** (A2 settings field vs A3 `SurfacesV2Flag`) — same env var, deliberately deferred to E3 D5 for one-place reconciliation.
3. **`Surface` vs `SurfaceSnapshot`** api-types shape + shared parity-snapshot schema (A3/B1 OQ1) — needs an owner call; both PRDs carry the same recommendation.
4. **api-types module target** (`ledger.ts` vs `index.ts`) for later-wave additions — implementer nuance; A1's `ledger.ts` is the intended home.

---

## Close-out resolutions (2026-07-23)

All four sign-off items above are now **resolved in-doc**, each following the
recommendation already recorded — no new architecture, no DoD weakened. The changes were
written into the affected docs:

1. **SDR §4 phantom endpoint → RESOLVED.** `02-sdr.md` §4 no longer lists the
   `/v1/agent/surfaces/{surface_id}` (get/replay) route that no PRD produces. It now names
   the actual surface-resolution contract the PRDs implement — `GET
/v1/agent/runs/{run_id}/surfaces` (A3, lists **and** replays a run's surfaces) — and
   states that per-surface **mutating** routes are keyed on `{surface_id}` with a `run_id`
   query param (B3 `…/regenerate` + `…/view-preference`, B4 `…/shape-request`). SDR §5 was
   **not** edited (frozen). One-line "(§4 reconciled 2026-07-23)" note added.

2. **`Surface` vs `SurfaceSnapshot` → RESOLVED (option (a)).** `SurfaceSnapshot` /
   `SurfaceViewState` stay A3's **distinct, additive** SurfaceStore-fold output (carrying
   `first_sequence_no` / `last_sequence_no`); A1's `Surface` entity is left untouched. A3's
   and B1's OQ1 open-questions were converted to **RESOLVED** notes stating the decision
   (A3 serves `SurfaceSnapshot`; B1's client fold + parity snapshot target the same
   `SurfaceSnapshot` metadata shape — snake_case, sorted by `surface_id`, metadata-only, no
   hydrated payload). PRD-A1 gained a one-line entity-vs-fold-projection note.

3. **api-types module target → RESOLVED.** PRD-A1 (D2) now pins
   `packages/api-types/src/ledger.ts` as the canonical home for **all** v2 ledger/domain
   type additions (re-exported from `index.ts`), with a one-line note that later PRDs
   saying "add to `index.ts`" mean "add to `ledger.ts`, re-export from `index.ts`".

4. **Dual `SURFACES_V2` reader → confirmed deferred to E3 D5, now stated explicitly.**
   PRD-E3 D5 gains an explicit line: E3 is the one place the two readers (A2's
   `RuntimeExecutionSettings.surfaces_v2` and A3's `SurfacesV2Flag.enabled()`) are
   **collapsed to a single authoritative reader** (same env var — a code-path merge, not a
   rename). Left otherwise as-is per the recommendation.
