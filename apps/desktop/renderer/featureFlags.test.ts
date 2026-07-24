// PRD-E3 — the desktop `surfacesV2` canvas is default ON (opt-out, fail toward ON).

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

describe("isSurfacesV2Enabled (PRD-E3: default ON opt-out)", () => {
  it("defaults ON with no opt-out signal", () => {
    stubLocalStorage(() => null);
    expect(isSurfacesV2Enabled()).toBe(true);
  });

  it("disables when localStorage opts out with the exact string 'false'", () => {
    stubLocalStorage((key) => (key === SURFACES_V2_FLAG_KEY ? "false" : null));
    expect(isSurfacesV2Enabled()).toBe(false);
  });

  it("stays ON for any non-'false' value (fails toward the new default)", () => {
    stubLocalStorage((key) => (key === SURFACES_V2_FLAG_KEY ? "yes" : null));
    expect(isSurfacesV2Enabled()).toBe(true);
  });

  it("stays ON when localStorage throws (storage disabled)", () => {
    stubLocalStorage(() => {
      throw new Error("storage disabled");
    });
    expect(isSurfacesV2Enabled()).toBe(true);
  });
});
