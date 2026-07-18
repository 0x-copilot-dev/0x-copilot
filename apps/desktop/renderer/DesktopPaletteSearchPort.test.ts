import { describe, expect, it } from "vitest";

import type { PaletteHit } from "@0x-copilot/api-types";

import { createDesktopPaletteSearchPort } from "./DesktopPaletteSearchPort";
import { PALETTE_COMMANDS } from "./palette-commands";

// Helpers -------------------------------------------------------------------

const titles = (hits: readonly PaletteHit[]) => hits.map((hit) => hit.title);

async function search(q: string, limit?: number) {
  const port = createDesktopPaletteSearchPort();
  return port.search({ q, limit });
}

// ---------------------------------------------------------------------------

describe("DesktopPaletteSearchPort — empty query (starter list)", () => {
  it("returns the whole registry (6 nav + 3 settings + 4 actions) in order", async () => {
    const res = await search("");
    expect(res.hits).toHaveLength(PALETTE_COMMANDS.length);
    expect(res.hits).toHaveLength(13);
    expect(res.hits).toEqual(PALETTE_COMMANDS);
  });

  it("treats a whitespace-only query as empty", async () => {
    const res = await search("   ");
    expect(res.hits).toEqual(PALETTE_COMMANDS);
  });

  it("reports a non-negative took_ms", async () => {
    const res = await search("");
    expect(typeof res.took_ms).toBe("number");
    expect(res.took_ms).toBeGreaterThanOrEqual(0);
  });
});

describe("DesktopPaletteSearchPort — substring filter", () => {
  it("filters to a single match on title ('appear' → Appearance)", async () => {
    const res = await search("appear");
    expect(titles(res.hits)).toEqual(["Appearance"]);
  });

  it("matches across entries ('model' → Model & behavior + Download a local model)", async () => {
    const res = await search("model");
    expect(titles(res.hits)).toEqual([
      "Model & behavior",
      "Download a local model",
    ]);
  });

  it("is case-insensitive", async () => {
    expect(titles((await search("APPEAR")).hits)).toEqual(["Appearance"]);
    expect(titles((await search("ActIvItY")).hits)).toEqual(["Go to Activity"]);
  });

  it("matches on subtitle text as well as title", async () => {
    // "ollama" appears only in the Download-a-local-model subtitle.
    const res = await search("ollama");
    expect(titles(res.hits)).toEqual(["Download a local model"]);
  });

  it("returns an empty list when nothing matches", async () => {
    const res = await search("zzzz-no-such-command");
    expect(res.hits).toEqual([]);
  });

  it("clamps the result to `limit`", async () => {
    const res = await search("go to", 2);
    expect(res.hits).toHaveLength(2);
    // A limit of 0 clamps to nothing.
    expect((await search("go to", 0)).hits).toEqual([]);
  });
});

describe("DesktopPaletteSearchPort — never throws (FR-6.4/6.5)", () => {
  it("resolves with an empty list when the registry throws", async () => {
    const throwingPort = createDesktopPaletteSearchPort(() => {
      throw new Error("registry unavailable");
    });
    const res = await throwingPort.search({ q: "anything" });
    expect(res.hits).toEqual([]);
    expect(res.took_ms).toBeGreaterThanOrEqual(0);
  });

  it("does not reject even for an empty query against a throwing registry", async () => {
    const throwingPort = createDesktopPaletteSearchPort(() => {
      throw new Error("registry unavailable");
    });
    await expect(throwingPort.search({ q: "" })).resolves.toMatchObject({
      hits: [],
    });
  });

  it("accepts an injected registry and searches over it", async () => {
    const custom: readonly PaletteHit[] = [
      {
        id: "x-one",
        kind: "action",
        title: "Alpha",
        action_token: "a",
        score: 1,
      },
      {
        id: "x-two",
        kind: "action",
        title: "Beta",
        action_token: "b",
        score: 1,
      },
    ];
    const port = createDesktopPaletteSearchPort(() => custom);
    expect(titles((await port.search({ q: "" })).hits)).toEqual([
      "Alpha",
      "Beta",
    ]);
    expect(titles((await port.search({ q: "beta" })).hits)).toEqual(["Beta"]);
  });
});
