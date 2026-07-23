import { describe, expect, it } from "vitest";

import { formatActivityMeta } from "./meta";

describe("formatActivityMeta (PRD-08 D1)", () => {
  it("composes the design's exact running-row string (copilot-data.jsx:606)", () => {
    expect(
      formatActivityMeta({
        connector_count: 4,
        step_count: 7,
        pending_approval_count: 1,
      }),
    ).toBe("4 apps · 7 steps · awaiting 1 approval");
  });

  it("returns '' when everything is unknown/zero (no '0 apps · 0 steps')", () => {
    expect(
      formatActivityMeta({
        connector_count: null,
        step_count: null,
        pending_approval_count: 0,
      }),
    ).toBe("");
  });

  it("omits the apps clause when connector_count is 0 (native-only run)", () => {
    // A run that used only connector-less native tools did steps, not apps.
    expect(
      formatActivityMeta({
        connector_count: 0,
        step_count: 5,
        pending_approval_count: 0,
      }),
    ).toBe("5 steps");
  });

  it("singularises app / step / approval", () => {
    expect(
      formatActivityMeta({
        connector_count: 1,
        step_count: 1,
        pending_approval_count: 1,
      }),
    ).toBe("1 app · 1 step · awaiting 1 approval");
  });

  it("pluralises approvals beyond one", () => {
    expect(
      formatActivityMeta({
        connector_count: 3,
        step_count: 12,
        pending_approval_count: 2,
      }),
    ).toBe("3 apps · 12 steps · awaiting 2 approvals");
  });

  it("keeps '0 steps' when steps is a resolved 0 but omits unknown apps", () => {
    // step_count is a real 0 (writer ran, no tools); connector_count unknown.
    expect(
      formatActivityMeta({
        connector_count: null,
        step_count: 0,
        pending_approval_count: 0,
      }),
    ).toBe("0 steps");
  });

  it("drops the apps clause but keeps steps when only connectors are unknown", () => {
    expect(
      formatActivityMeta({
        connector_count: null,
        step_count: 7,
        pending_approval_count: 0,
      }),
    ).toBe("7 steps");
  });
});
