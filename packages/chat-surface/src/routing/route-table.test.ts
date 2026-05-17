import { describe, expect, it } from "vitest";

import type { ArtifactRoute } from "./router";
import { ROUTE_TABLE, DEFAULT_ROUTE, type RouteEntry } from "./route-table";
import { ARTIFACT_SCHEMES, isArtifactScheme } from "./uri/schemes";

const EXPECTED_KINDS: ReadonlyArray<ArtifactRoute["kind"]> = [
  "chat",
  "conversation",
  "run",
  "subagent",
  "tool-result",
  "mcp",
  "mcp-tool",
  "skill",
  "workspace",
];

describe("ROUTE_TABLE", () => {
  it("covers every ArtifactRoute kind", () => {
    for (const kind of EXPECTED_KINDS) {
      expect(ROUTE_TABLE[kind]).toBeDefined();
    }
    expect(Object.keys(ROUTE_TABLE).sort()).toEqual([...EXPECTED_KINDS].sort());
  });

  it("ties each kind to a registered ArtifactScheme", () => {
    for (const kind of EXPECTED_KINDS) {
      const entry = ROUTE_TABLE[kind];
      expect(entry.kind).toBe(kind);
      expect(isArtifactScheme(entry.scheme)).toBe(true);
    }
  });

  it("uses the conventional scheme for each kind", () => {
    const expected: Record<ArtifactRoute["kind"], string> = {
      chat: ARTIFACT_SCHEMES.chat,
      conversation: ARTIFACT_SCHEMES.conversation,
      run: ARTIFACT_SCHEMES.run,
      subagent: ARTIFACT_SCHEMES.subagent,
      "tool-result": ARTIFACT_SCHEMES.toolResult,
      mcp: ARTIFACT_SCHEMES.mcp,
      "mcp-tool": ARTIFACT_SCHEMES.mcpTool,
      skill: ARTIFACT_SCHEMES.skill,
      workspace: ARTIFACT_SCHEMES.workspace,
    };
    for (const kind of EXPECTED_KINDS) {
      expect(ROUTE_TABLE[kind].scheme).toBe(expected[kind]);
    }
  });

  it("exposes a stable, non-empty label and iconHint per entry", () => {
    for (const kind of EXPECTED_KINDS) {
      const entry: RouteEntry = ROUTE_TABLE[kind];
      expect(entry.label.length).toBeGreaterThan(0);
      expect(entry.iconHint.length).toBeGreaterThan(0);
    }
  });

  it("each entry exposes a renderable Component", () => {
    for (const kind of EXPECTED_KINDS) {
      expect(typeof ROUTE_TABLE[kind].Component).toBe("function");
    }
  });

  it("locks the canonical label set so renames are explicit", () => {
    expect(ROUTE_TABLE.chat.label).toBe("Chat");
    expect(ROUTE_TABLE.conversation.label).toBe("Conversation");
    expect(ROUTE_TABLE.run.label).toBe("Run");
    expect(ROUTE_TABLE.subagent.label).toBe("Subagent");
    expect(ROUTE_TABLE["tool-result"].label).toBe("Tool result");
    expect(ROUTE_TABLE.mcp.label).toBe("Connector");
    expect(ROUTE_TABLE["mcp-tool"].label).toBe("Tool");
    expect(ROUTE_TABLE.skill.label).toBe("Skill");
    expect(ROUTE_TABLE.workspace.label).toBe("Workspace");
  });

  it("DEFAULT_ROUTE is null until the home destination has an ArtifactRoute kind", () => {
    expect(DEFAULT_ROUTE).toBeNull();
  });
});
