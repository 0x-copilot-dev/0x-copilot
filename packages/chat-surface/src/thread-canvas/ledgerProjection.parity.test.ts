// Cross-language parity — the PRD-B1 DoD's hard gate.
//
// The TypeScript `projectLedger` fold of PRD-A1's golden Work Ledger events MUST
// byte-equal PRD-A3's Python `SurfaceStoreProjection.fold` snapshot of the SAME
// events. Both fixtures are imported directly from disk (the `adapterAllowlist`
// precedent — a relative JSON import), so this test fails if EITHER language
// drifts:
//   - the golden events (A1, service-contracts): the shared fold input.
//   - the golden fold state (A3, ai-backend test fixtures): the referee,
//     serialized exactly as `SurfaceStoreState.model_dump(mode="json")`.
//
// Importing A3's own fixture directly (not a vendored copy) is what makes "no
// drift" a hard guarantee — there is one referee, checked by both suites.

import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

// A1 golden events (shared fold input, owned by service-contracts).
import goldenEvents from "../../../service-contracts/src/copilot_service_contracts/work_ledger_golden_events.json";
// A3 golden fold state (the Python referee, ai-backend test fixtures).
import goldenState from "../../../../services/ai-backend/tests/unit/agent_runtime/surfaces_v2/fixtures/surface_store_golden_state.json";

import { projectLedger, toParitySnapshot } from "./ledgerProjection";

const events = (goldenEvents as { events: unknown[] })
  .events as unknown as RuntimeEventEnvelope[];

describe("ledgerProjection ↔ Python SurfaceStore parity (PRD-B1 DoD item 1)", () => {
  it("ts fold of the golden events byte-equals the py fold snapshot", () => {
    const snapshot = toParitySnapshot(projectLedger(events));
    expect(snapshot).toEqual(goldenState);
  });

  it("re-projecting the golden events is deep-equal (idempotent fold)", () => {
    const a = toParitySnapshot(projectLedger(events));
    const b = toParitySnapshot(projectLedger(events));
    expect(a).toEqual(b);
  });
});
