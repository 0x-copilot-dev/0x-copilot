import { afterEach, describe, expect, it } from "vitest";

import {
  clearRegistry,
  registerSurface,
  resolveSurface,
} from "./SurfaceRegistry";
import type { SurfaceRendererProps } from "./types";

const FakeComponent = (_props: SurfaceRendererProps) => null;
const OtherFakeComponent = (_props: SurfaceRendererProps) => null;

describe("SurfaceRegistry", () => {
  afterEach(() => {
    clearRegistry();
  });

  it("returns null when no surface is registered for the scheme", () => {
    expect(resolveSurface("email://draft-1")).toBeNull();
  });

  it("returns the registered component for a matching URI", () => {
    registerSurface("email", FakeComponent);
    expect(resolveSurface("email://draft-1")).toBe(FakeComponent);
  });

  it("returns null for a malformed URI even if the scheme is registered", () => {
    registerSurface("email", FakeComponent);
    expect(resolveSurface("email:")).toBeNull();
    expect(resolveSurface("email://")).toBeNull();
    expect(resolveSurface("")).toBeNull();
  });

  it("is idempotent for re-registering the same component", () => {
    registerSurface("email", FakeComponent);
    expect(() => registerSurface("email", FakeComponent)).not.toThrow();
    expect(resolveSurface("email://draft-1")).toBe(FakeComponent);
  });

  it("throws when a different component is registered for an existing scheme", () => {
    registerSurface("email", FakeComponent);
    expect(() => registerSurface("email", OtherFakeComponent)).toThrow();
  });

  it("clearRegistry removes all registrations", () => {
    registerSurface("email", FakeComponent);
    clearRegistry();
    expect(resolveSurface("email://draft-1")).toBeNull();
  });
});
