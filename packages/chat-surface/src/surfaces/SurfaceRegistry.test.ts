import { createElement, type ReactElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { TIER3_SCHEME, type SaaSRendererAdapter } from "./SaaSRendererAdapter";
import {
  clearRegistry,
  markBroken,
  registerAdapter,
  registerSurface,
  resolveAdapter,
  resolveSurface,
  unregisterAdapter,
} from "./SurfaceRegistry";
import type { SurfaceRendererProps } from "./types";

function makeAdapter(
  scheme: string,
  version: number,
  opts: { readonly matches?: (uri: string) => boolean } = {},
): SaaSRendererAdapter {
  const matches =
    opts.matches ?? ((uri: string) => uri.startsWith(`${scheme}://`));
  return {
    scheme,
    matches,
    renderCurrent: (): ReactElement =>
      createElement("div", { "data-scheme": scheme, "data-version": version }),
    renderDiff: (): ReactElement =>
      createElement("div", { "data-diff": `${scheme}-v${version}` }),
    metadata: {
      origin: "first-party",
      schemaVersion: version,
    },
  };
}

function makeWildcard(version: number): SaaSRendererAdapter {
  return {
    scheme: TIER3_SCHEME,
    matches: () => true,
    renderCurrent: (): ReactElement =>
      createElement("div", { "data-wildcard": true, "data-version": version }),
    renderDiff: (): ReactElement =>
      createElement("div", { "data-wildcard-diff": true }),
    metadata: { origin: "first-party", schemaVersion: version },
  };
}

const NoopComponent = (_p: SurfaceRendererProps) => null;
const OtherNoopComponent = (_p: SurfaceRendererProps) => null;

describe("SurfaceRegistry — adapter API", () => {
  afterEach(() => {
    clearRegistry();
  });

  describe("registerAdapter / resolveAdapter", () => {
    it("returns null for unknown scheme", () => {
      expect(resolveAdapter("email://draft-1")).toBeNull();
    });

    it("returns null for malformed URI", () => {
      registerAdapter(makeAdapter("email", 1));
      expect(resolveAdapter("")).toBeNull();
      expect(resolveAdapter("email:")).toBeNull();
      expect(resolveAdapter("://nothing")).toBeNull();
    });

    it("returns the registered adapter on scheme match", () => {
      const email = makeAdapter("email", 1);
      registerAdapter(email);
      expect(resolveAdapter("email://draft-1")).toBe(email);
    });

    it("returns highest-version adapter when multiple are registered", () => {
      const v1 = makeAdapter("email", 1);
      const v3 = makeAdapter("email", 3);
      const v2 = makeAdapter("email", 2);
      registerAdapter(v1);
      registerAdapter(v3);
      registerAdapter(v2);
      expect(resolveAdapter("email://draft-1")).toBe(v3);
    });

    it("falls through to the next-highest version when matches() returns false", () => {
      const v2OnlyDrafts = makeAdapter("email", 2, {
        matches: (uri) => uri.startsWith("email://draft-"),
      });
      const v1Any = makeAdapter("email", 1);
      registerAdapter(v2OnlyDrafts);
      registerAdapter(v1Any);
      expect(resolveAdapter("email://draft-1")).toBe(v2OnlyDrafts);
      expect(resolveAdapter("email://archive-1")).toBe(v1Any);
    });

    it("replaces an existing entry of the same {scheme, version} (hot-swap)", () => {
      const before = makeAdapter("email", 1);
      registerAdapter(before);
      const after = makeAdapter("email", 1);
      registerAdapter(after);
      expect(resolveAdapter("email://draft-1")).toBe(after);
    });

    it("re-registering a previously broken {scheme, version} clears the broken flag", () => {
      const v1 = makeAdapter("email", 1);
      registerAdapter(v1);
      markBroken("email", 1, "synthetic failure");
      expect(resolveAdapter("email://draft-1")).toBeNull();
      const v1Fresh = makeAdapter("email", 1);
      registerAdapter(v1Fresh);
      expect(resolveAdapter("email://draft-1")).toBe(v1Fresh);
    });
  });

  describe("tier-3 wildcard fallback", () => {
    it("returns the wildcard adapter when no exact scheme matches", () => {
      const generic = makeWildcard(1);
      registerAdapter(generic);
      expect(resolveAdapter("hubspot-deal://abc")).toBe(generic);
    });

    it("prefers exact scheme adapter over the wildcard", () => {
      const email = makeAdapter("email", 1);
      const generic = makeWildcard(1);
      registerAdapter(generic);
      registerAdapter(email);
      expect(resolveAdapter("email://draft-1")).toBe(email);
      expect(resolveAdapter("hubspot-deal://abc")).toBe(generic);
    });

    it("falls back to wildcard when every exact-scheme version is broken", () => {
      const email = makeAdapter("email", 1);
      const generic = makeWildcard(1);
      registerAdapter(email);
      registerAdapter(generic);
      markBroken("email", 1, "synthetic");
      expect(resolveAdapter("email://draft-1")).toBe(generic);
    });

    it("falls back to wildcard when exact-scheme matches() returns false for every version", () => {
      const emailDrafts = makeAdapter("email", 1, {
        matches: (uri) => uri.startsWith("email://draft-"),
      });
      const generic = makeWildcard(1);
      registerAdapter(emailDrafts);
      registerAdapter(generic);
      expect(resolveAdapter("email://archive-99")).toBe(generic);
    });

    it("prefers the highest wildcard version", () => {
      const v1 = makeWildcard(1);
      const v2 = makeWildcard(2);
      registerAdapter(v1);
      registerAdapter(v2);
      expect(resolveAdapter("unknown://x")).toBe(v2);
    });
  });

  describe("unregisterAdapter", () => {
    it("removes all versions for a scheme when version is omitted", () => {
      registerAdapter(makeAdapter("email", 1));
      registerAdapter(makeAdapter("email", 2));
      unregisterAdapter("email");
      expect(resolveAdapter("email://draft-1")).toBeNull();
    });

    it("removes only the specified version", () => {
      const v1 = makeAdapter("email", 1);
      const v2 = makeAdapter("email", 2);
      registerAdapter(v1);
      registerAdapter(v2);
      unregisterAdapter("email", 2);
      expect(resolveAdapter("email://draft-1")).toBe(v1);
    });

    it("is a no-op for unknown scheme", () => {
      expect(() => unregisterAdapter("does-not-exist")).not.toThrow();
      expect(() => unregisterAdapter("does-not-exist", 1)).not.toThrow();
    });

    it("works for the wildcard scheme", () => {
      registerAdapter(makeWildcard(1));
      registerAdapter(makeWildcard(2));
      unregisterAdapter(TIER3_SCHEME, 1);
      expect(resolveAdapter("unknown://x")?.metadata.schemaVersion).toBe(2);
      unregisterAdapter(TIER3_SCHEME);
      expect(resolveAdapter("unknown://x")).toBeNull();
    });
  });

  describe("markBroken", () => {
    it("hides a broken version from resolve", () => {
      const v1 = makeAdapter("email", 1);
      const v2 = makeAdapter("email", 2);
      registerAdapter(v1);
      registerAdapter(v2);
      markBroken("email", 2, "schema drift");
      expect(resolveAdapter("email://draft-1")).toBe(v1);
    });

    it("is a no-op for unknown scheme / version", () => {
      expect(() => markBroken("nope", 1, "x")).not.toThrow();
      registerAdapter(makeAdapter("email", 1));
      expect(() => markBroken("email", 99, "x")).not.toThrow();
      expect(resolveAdapter("email://draft-1")).not.toBeNull();
    });

    it("works for the wildcard scheme", () => {
      registerAdapter(makeWildcard(1));
      markBroken(TIER3_SCHEME, 1, "synthetic");
      expect(resolveAdapter("unknown://x")).toBeNull();
    });
  });

  describe("clearRegistry", () => {
    it("removes every adapter and every legacy registration", () => {
      registerAdapter(makeAdapter("email", 1));
      registerAdapter(makeWildcard(1));
      registerSurface("legacy-scheme", NoopComponent);
      clearRegistry();
      expect(resolveAdapter("email://draft-1")).toBeNull();
      expect(resolveAdapter("unknown://x")).toBeNull();
      expect(resolveSurface("legacy-scheme://x")).toBeNull();
    });
  });

  describe("deprecated registerSurface / resolveSurface", () => {
    it("returns null when nothing is registered", () => {
      expect(resolveSurface("email://draft-1")).toBeNull();
    });

    it("round-trips a legacy component through resolveSurface", () => {
      registerSurface("email", NoopComponent);
      expect(resolveSurface("email://draft-1")).toBe(NoopComponent);
    });

    it("returns null for malformed URI even when scheme is registered", () => {
      registerSurface("email", NoopComponent);
      expect(resolveSurface("email:")).toBeNull();
      expect(resolveSurface("")).toBeNull();
    });

    it("is idempotent when re-registering the same component", () => {
      registerSurface("email", NoopComponent);
      expect(() => registerSurface("email", NoopComponent)).not.toThrow();
      expect(resolveSurface("email://draft-1")).toBe(NoopComponent);
    });

    it("throws when a different component is registered for an existing scheme", () => {
      registerSurface("email", NoopComponent);
      expect(() => registerSurface("email", OtherNoopComponent)).toThrow();
    });

    it("also installs an adapter wrapper visible to resolveAdapter", () => {
      registerSurface("email", NoopComponent);
      const adapter = resolveAdapter("email://draft-1");
      expect(adapter).not.toBeNull();
      expect(adapter?.scheme).toBe("email");
      expect(adapter?.metadata.origin).toBe("first-party");
      // The wrapper deliberately rejects renderCurrent — see SurfaceRegistry.ts.
      expect(() => adapter?.renderCurrent({})).toThrow();
    });

    it("unregisterAdapter removes the legacy component registration too", () => {
      registerSurface("email", NoopComponent);
      unregisterAdapter("email");
      expect(resolveSurface("email://draft-1")).toBeNull();
    });
  });
});
