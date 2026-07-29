import { describe, expect, it } from "vitest";

import {
  isSurfaceHue,
  resolveSurfaceHue,
  SURFACE_HUES,
  surfaceHueForUri,
} from "./surfaceHue";

describe("surfaceHueForUri", () => {
  it("gives each archetype the hue the design assigns it", () => {
    expect(surfaceHueForUri("table://safe/batch/0x9f21")).toBe("jade");
    expect(surfaceHueForUri("record://salesforce/opportunity/006Ab")).toBe(
      "indigo",
    );
    expect(surfaceHueForUri("message://gmail/draft/18f2c")).toBe("ember");
    expect(surfaceHueForUri("doc://notion/page/9c41")).toBe("violet");
    expect(surfaceHueForUri("board://linear/cycle/14")).toBe("plum");
  });

  // Both paint a grid, and they still must not look like the same kind of
  // thing: a dataset artifact is something the run produced and owns, a table
  // is a read of somebody else's system.
  it("separates a dataset artifact from a table read", () => {
    expect(surfaceHueForUri("artifact-dataset://art_41618344@1")).toBe("sky");
    expect(surfaceHueForUri("table://safe/batch")).toBe("jade");
  });

  it("is case-insensitive about the scheme", () => {
    expect(surfaceHueForUri("TABLE://safe/batch")).toBe("jade");
  });

  it("returns none for an unmapped source rather than borrowing a hue", () => {
    expect(surfaceHueForUri("incident://pagerduty/4127")).toBe("none");
    expect(surfaceHueForUri("artifact-file://art_9@1")).toBe("none");
  });

  it("is total over malformed input", () => {
    for (const input of ["", "://", "notauri", "://leading", "a"]) {
      expect(surfaceHueForUri(input)).toBe("none");
    }
  });
});

describe("isSurfaceHue", () => {
  it("accepts every name the stylesheet defines", () => {
    for (const hue of SURFACE_HUES) expect(isSurfaceHue(hue)).toBe(true);
  });

  // The model's choice arrives here untrusted. A colour, a CSS value, or an
  // injection attempt must never reach a `data-surface-hue` attribute.
  it("rejects anything that is not one of those names", () => {
    for (const value of [
      "#ff00ff",
      "red",
      "var(--color-accent)",
      "oklch(0.76 0.1 158)",
      "jade; background: url(x)",
      "",
      null,
      undefined,
      7,
      {},
    ]) {
      expect(isSurfaceHue(value)).toBe(false);
    }
  });
});

describe("resolveSurfaceHue", () => {
  it("lets a valid choice override the scheme's default", () => {
    expect(
      resolveSurfaceHue({ uri: "artifact-dataset://a@1", choice: "ember" }),
    ).toBe("ember");
  });

  // The important negative: a bad choice must not blank out an identity the
  // artifact's kind already implies. It degrades to the default, not to `none`.
  it("falls back to the scheme's hue when the choice is invalid", () => {
    expect(
      resolveSurfaceHue({ uri: "artifact-dataset://a@1", choice: "#ff00ff" }),
    ).toBe("sky");
    expect(
      resolveSurfaceHue({ uri: "artifact-dataset://a@1", choice: null }),
    ).toBe("sky");
    expect(resolveSurfaceHue({ uri: "artifact-dataset://a@1" })).toBe("sky");
  });

  it("honours an explicit none, which is a real choice", () => {
    expect(resolveSurfaceHue({ uri: "table://x", choice: "none" })).toBe(
      "none",
    );
  });
});
