import { describe, expect, it } from "vitest";

import {
  ACTIVITY_DIGEST_MAX_PHRASES,
  describeActivity,
  type ActivityDigestMember,
} from "./activityDigest";

const tool = (
  toolName: string,
  args?: Record<string, unknown>,
): ActivityDigestMember => ({ kind: "tool", toolName, args });

const fleet = (total: number): ActivityDigestMember => ({
  kind: "fleet",
  total,
});

describe("describeActivity", () => {
  it("says nothing when nothing was folded", () => {
    expect(describeActivity([])).toBeNull();
  });

  it("names the turn from the live capture that motivated it", () => {
    // One web search and one subagent used to settle into
    // "Thought process · 2 steps" — a count, with no record of either.
    expect(describeActivity([tool("web_search"), fleet(1)])).toBe(
      "Searched the web · Dispatched 1 subagent",
    );
  });

  it("reads in the order the run went, not by a priority table", () => {
    expect(
      describeActivity([
        tool("read_file", { file_path: "/w/a.py" }),
        tool("web_search"),
        tool("edit_file", { file_path: "/w/a.py" }),
      ]),
    ).toBe("Read 1 file · Searched the web · Edited 1 file");
  });

  it("counts FILES, so three edits to one file are one file", () => {
    expect(
      describeActivity([
        tool("edit_file", { file_path: "/w/a.py" }),
        tool("edit_file", { file_path: "/w/a.py" }),
        tool("edit_file", { file_path: "/w/b.py" }),
      ]),
    ).toBe("Edited 2 files");
  });

  it("counts each call when a file tool carries no path", () => {
    // A streamed call whose args have not parsed yet still happened.
    expect(describeActivity([tool("read_file"), tool("read_file")])).toBe(
      "Read 2 files",
    );
  });

  it("counts subagents, not dispatches", () => {
    expect(describeActivity([fleet(2), fleet(3)])).toBe(
      "Dispatched 5 subagents",
    );
  });

  it("rounds an unknown fleet size up to one rather than claiming zero", () => {
    expect(describeActivity([fleet(0)])).toBe("Dispatched 1 subagent");
  });

  it("repeats a verb with a multiplier instead of a fake noun", () => {
    expect(
      describeActivity([tool("web_search"), tool("web_search"), tool("grep")]),
    ).toBe("Searched the web ×2 · Searched files");
  });

  it("files a shell alias under commands with run_command", () => {
    expect(describeActivity([tool("run_command"), tool("shell")])).toBe(
      "Ran 2 commands",
    );
  });

  it("keeps a connector call's own name — it is not '1 step'", () => {
    // Title case is `humanizeIdentifier`'s house style, reused as-is so a
    // connector reads the same here as it does in the subagent rows.
    expect(describeActivity([tool("create_issue"), tool("create_issue")])).toBe(
      "Create Issue ×2",
    );
  });

  it("folds the long tail into '+N more', counting STEPS", () => {
    const line = describeActivity([
      tool("web_search"),
      tool("read_file", { file_path: "/w/a.py" }),
      tool("edit_file", { file_path: "/w/a.py" }),
      tool("run_command"),
      tool("run_command"),
      fleet(1),
    ]);
    // Three phrases shown; the two commands and the dispatch are the rest.
    expect(line).toBe(
      "Searched the web · Read 1 file · Edited 1 file · +3 more",
    );
    expect(line?.split(" · ")).toHaveLength(ACTIVITY_DIGEST_MAX_PHRASES + 1);
  });
});
