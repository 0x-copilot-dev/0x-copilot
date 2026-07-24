// WC-P7 — the `runCockpitWeb` flag is flipped ON by default; the legacy
// `ChatScreen` is reachable only via an explicit fail-safe opt-out.

import { afterEach, describe, expect, it } from "vitest";

import {
  RUN_COCKPIT_WEB_FLAG_KEY,
  SURFACES_V2_FLAG_KEY,
  isRunCockpitWebEnabled,
  isSurfacesV2CanvasEnabled,
} from "./featureFlags";

// jsdom's `localStorage` here is a no-op stub whose methods are not spy-able, so
// swap the whole object via a property descriptor and restore it after each test.
const originalDescriptor = Object.getOwnPropertyDescriptor(
  window,
  "localStorage",
);

function stubLocalStorage(getItem: (key: string) => string | null): void {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: { getItem },
  });
}

afterEach(() => {
  if (originalDescriptor !== undefined) {
    Object.defineProperty(window, "localStorage", originalDescriptor);
  }
});

describe("isRunCockpitWebEnabled (WC-P7)", () => {
  it("defaults ON when there is no opt-out signal", () => {
    stubLocalStorage(() => null);
    expect(isRunCockpitWebEnabled()).toBe(true);
  });

  it("rolls back to ChatScreen when localStorage opts out with 'false'", () => {
    stubLocalStorage((key) =>
      key === RUN_COCKPIT_WEB_FLAG_KEY ? "false" : null,
    );
    expect(isRunCockpitWebEnabled()).toBe(false);
  });

  it("stays ON for any non-'false' value (fails toward the new default)", () => {
    // A stale opt-in ("true") or garbage value must NOT be read as an opt-out.
    stubLocalStorage((key) =>
      key === RUN_COCKPIT_WEB_FLAG_KEY ? "true" : null,
    );
    expect(isRunCockpitWebEnabled()).toBe(true);
  });

  it("stays ON when localStorage throws (private mode / storage disabled)", () => {
    stubLocalStorage(() => {
      throw new Error("storage disabled");
    });
    expect(isRunCockpitWebEnabled()).toBe(true);
  });
});

describe("isSurfacesV2CanvasEnabled (PRD-E3: default ON opt-out)", () => {
  it("defaults ON when there is no opt-out signal", () => {
    stubLocalStorage(() => null);
    expect(isSurfacesV2CanvasEnabled()).toBe(true);
  });

  it("disables when localStorage opts out with the exact string 'false'", () => {
    stubLocalStorage((key) => (key === SURFACES_V2_FLAG_KEY ? "false" : null));
    expect(isSurfacesV2CanvasEnabled()).toBe(false);
  });

  it("stays ON for any non-'false' value (fails toward the new default)", () => {
    // A stale opt-in ("true") or garbage value must NOT be read as an opt-out.
    stubLocalStorage((key) => (key === SURFACES_V2_FLAG_KEY ? "true" : null));
    expect(isSurfacesV2CanvasEnabled()).toBe(true);
  });

  it("stays ON when localStorage throws (private mode / storage disabled)", () => {
    stubLocalStorage(() => {
      throw new Error("storage disabled");
    });
    expect(isSurfacesV2CanvasEnabled()).toBe(true);
  });
});
