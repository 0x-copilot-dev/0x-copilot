// PRD-B1 — the desktop `surfacesV2` opt-in (default OFF, fail-safe).

import { afterEach, describe, expect, it } from "vitest";

import { SURFACES_V2_FLAG_KEY, isSurfacesV2Enabled } from "./featureFlags";

const originalDescriptor = Object.getOwnPropertyDescriptor(
  globalThis,
  "localStorage",
);

function stubLocalStorage(getItem: (key: string) => string | null): void {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: { getItem },
  });
}

afterEach(() => {
  if (originalDescriptor !== undefined) {
    Object.defineProperty(globalThis, "localStorage", originalDescriptor);
  }
});

describe("isSurfacesV2Enabled (PRD-B1)", () => {
  it("defaults OFF with no opt-in signal", () => {
    stubLocalStorage(() => null);
    expect(isSurfacesV2Enabled()).toBe(false);
  });

  it("enables when localStorage opts in with the exact string 'true'", () => {
    stubLocalStorage((key) => (key === SURFACES_V2_FLAG_KEY ? "true" : null));
    expect(isSurfacesV2Enabled()).toBe(true);
  });

  it("stays OFF for any non-'true' value (fail-safe)", () => {
    stubLocalStorage((key) => (key === SURFACES_V2_FLAG_KEY ? "yes" : null));
    expect(isSurfacesV2Enabled()).toBe(false);
  });

  it("stays OFF when localStorage throws (storage disabled)", () => {
    stubLocalStorage(() => {
      throw new Error("storage disabled");
    });
    expect(isSurfacesV2Enabled()).toBe(false);
  });
});
