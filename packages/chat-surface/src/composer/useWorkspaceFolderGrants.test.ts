// `mostRecentFirst` — which of several folders the bar is allowed to NAME.
//
// The bar shows one name and `+N`, so something has to choose, and the choice
// must not be "whatever the broker happened to send first". `WorkspaceGrant`
// carries no timestamp (the renderer projection is deliberately grantId / mount
// / label / mode), so the only honest signal is the grant this surface watched
// the user create. These pin that, and pin what happens when there is no such
// signal — which must be "leave the list alone", never "invent an order".

import { describe, expect, it } from "vitest";

import type { WorkspaceGrant } from "../ports/WorkspaceGrantPort";
import { mostRecentFirst } from "./useWorkspaceFolderGrants";

function grant(id: string, label: string): WorkspaceGrant {
  return { grantId: id, mount: `m_${id}`, label, mode: "read_only" };
}

const NOTES = grant("g1", "notes");
const DOWNLOADS = grant("g2", "Downloads");
const KALEIDOSCOPE = grant("g3", "kaleidoscope");

describe("mostRecentFirst", () => {
  it("lifts the just-granted folder to the head", () => {
    expect(mostRecentFirst([NOTES, DOWNLOADS, KALEIDOSCOPE], "g3")).toEqual([
      KALEIDOSCOPE,
      NOTES,
      DOWNLOADS,
    ]);
  });

  it("keeps the relative order of everything else", () => {
    expect(mostRecentFirst([NOTES, DOWNLOADS, KALEIDOSCOPE], "g2")).toEqual([
      DOWNLOADS,
      NOTES,
      KALEIDOSCOPE,
    ]);
  });

  it("returns the list untouched when nothing was granted here", () => {
    const list = [NOTES, DOWNLOADS];
    // Identity, not just equality: the caller memoizes on it.
    expect(mostRecentFirst(list, null)).toBe(list);
  });

  it("returns the list untouched when the remembered id is not in it", () => {
    const list = [NOTES, DOWNLOADS];
    expect(mostRecentFirst(list, "g_gone")).toBe(list);
  });

  it("is a no-op when the remembered grant already leads", () => {
    const list = [NOTES, DOWNLOADS];
    expect(mostRecentFirst(list, "g1")).toBe(list);
  });

  it("handles the empty and single-grant lists", () => {
    expect(mostRecentFirst([], "g1")).toEqual([]);
    expect(mostRecentFirst([NOTES], "g1")).toEqual([NOTES]);
  });
});
