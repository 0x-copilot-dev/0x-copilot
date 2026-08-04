// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { SurfaceId } from "./brands";
import { asSurfaceId, isSurfaceId } from "./surfaceId";

describe("surface identity", () => {
  it("accepts the identity the projector mints", () => {
    expect(isSurfaceId("table://incidents/list_incidents/1532a206699e")).toBe(
      true,
    );
    expect(asSurfaceId("doc://notion/get_page/abc")).toBe(
      "doc://notion/get_page/abc",
    );
  });

  it("accepts a historic id that carries no scheme at all", () => {
    // Pre-URI runs are still exactly one identity, and their transcripts still
    // have to render. The rule is about wrapping, not about vocabulary.
    expect(isSurfaceId("surf_1")).toBe(true);
    expect(isSurfaceId("connector:gmail:thread-1")).toBe(true);
  });

  it("rejects the exact defect: an id minted over another id", () => {
    // This is the live trace that motivated the brand — the canvas minted
    // `legacy-v2://<percent-encoded surface_id>` and then missed on every
    // lookup into a map keyed by the real id.
    expect(
      isSurfaceId(
        "table://legacy-v2/table%3A%2F%2Fincidents%2Flist_incidents%2F1532a206699e",
      ),
    ).toBe(false);
    // …and the unencoded form of the same mistake.
    expect(isSurfaceId("record://surfaces-v2/table://incidents/x")).toBe(false);
    expect(() => asSurfaceId("record://surfaces-v2/table://a")).toThrow(
      TypeError,
    );
  });

  it("rejects a missing or empty identity", () => {
    expect(isSurfaceId("")).toBe(false);
    expect(isSurfaceId(null)).toBe(false);
    expect(isSurfaceId(undefined)).toBe(false);
    expect(isSurfaceId(42)).toBe(false);
  });
});

// Compile-time guards (module scope so tsc evaluates them, mirroring
// `brands.test.ts`). Removing the brand — or minting a second identity without
// coming back through the constructor — must fail the build.
{
  const minted = `table://legacy-v2/${encodeURIComponent("table://a/b/c")}`;
  // @ts-expect-error — a minted string is not a SurfaceId
  const _wrapped: SurfaceId = minted;

  const plain: string = "table://a/b/c";
  // @ts-expect-error — plain string must not assign to SurfaceId
  const _plain: SurfaceId = plain;

  // The sanctioned crossing, and the widening back to string that makes the
  // brand free at runtime.
  const id: SurfaceId = asSurfaceId("table://a/b/c");
  const back: string = id;

  void _wrapped;
  void _plain;
  void back;
}
