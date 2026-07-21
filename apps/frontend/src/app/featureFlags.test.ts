// PRD-05 — the `runCockpitWeb` flag must be OFF by default (Non-goals: flipping
// it is a product decision after Wave-1 verification).

import { describe, expect, it } from "vitest";

import { isRunCockpitWebEnabled } from "./featureFlags";

describe("isRunCockpitWebEnabled (PRD-05)", () => {
  it("defaults OFF when neither the env flag nor localStorage opts in", () => {
    // `VITE_RUN_COCKPIT_WEB` is unset in the test env and jsdom's localStorage
    // is a no-op stub, so the localStorage read fails safe. The default must be
    // OFF so the legacy `ChatScreen` path stays the baseline.
    expect(isRunCockpitWebEnabled()).toBe(false);
  });
});
