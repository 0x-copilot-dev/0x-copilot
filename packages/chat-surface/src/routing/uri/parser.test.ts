import { describe, expect, it } from "vitest";

import { ARTIFACT_SCHEMES, type ArtifactScheme } from "./schemes";
import { buildArtifactUri, parseArtifactUri } from "./parser";

describe("parseArtifactUri", () => {
  it("parses every registered scheme with a representative body", () => {
    const examples: Record<ArtifactScheme, string> = {
      chat: "conv-1",
      convo: "wsp-1/conv-1",
      run: "run-1",
      subagent: "run-1/sub-1",
      "tool-result": "run-1/step-1",
      email: "draft-1",
      "sheet-row": "sf-1/row-1",
      "sf-opp": "acme/opp-1",
      slide: "deck-1/4",
      mcp: "server-1",
      "mcp-tool": "server-1/tool",
      skill: "skill-1",
      workspace: "wsp_acme",
      "time-machine": "run-1/t-0",
    };
    for (const scheme of Object.values(ARTIFACT_SCHEMES)) {
      const body = examples[scheme];
      const raw = `${scheme}://${body}`;
      expect(parseArtifactUri(raw)).toEqual({ scheme, body });
    }
  });

  it("returns null for malformed input", () => {
    const cases = [
      "",
      "no-scheme",
      "://body-only",
      "email://", // empty body
      "email:/draft-1", // single slash
      "unknown://body", // unknown scheme
      "EMAIL://draft-1", // case-sensitive
    ];
    for (const raw of cases) {
      expect(parseArtifactUri(raw)).toBeNull();
    }
  });

  it("round-trips with buildArtifactUri for each registered scheme", () => {
    for (const scheme of Object.values(ARTIFACT_SCHEMES)) {
      const body = "x";
      const built = buildArtifactUri({ scheme, body });
      expect(built).toBe(`${scheme}://${body}`);
      expect(parseArtifactUri(built)).toEqual({ scheme, body });
    }
  });

  it("buildArtifactUri throws on unknown scheme", () => {
    expect(() =>
      buildArtifactUri({ scheme: "unknown" as ArtifactScheme, body: "x" }),
    ).toThrow();
  });

  it("buildArtifactUri throws on empty body", () => {
    expect(() => buildArtifactUri({ scheme: "email", body: "" })).toThrow();
  });
});
