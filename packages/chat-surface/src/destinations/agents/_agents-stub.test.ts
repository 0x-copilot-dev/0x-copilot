import { describe, expect, it } from "vitest";

import {
  AGENT_COST_LABELS,
  AGENT_FILTER_LABELS,
  STARTER_RECOMMENDATIONS,
  filterAgents,
  searchAgents,
  type AgentId,
  type AgentStub,
} from "./_agents-stub";

const A: AgentStub = {
  id: "a1" as AgentId,
  name: "Research Helper",
  description: "Searches the web and your library.",
  origin: "installed",
  costTier: "low",
  skills: ["web-search", "summarize"],
  installed: true,
};
const B: AgentStub = {
  id: "a2" as AgentId,
  name: "Email Drafter",
  description: "Drafts replies in your tone.",
  origin: "available",
  costTier: "free",
  skills: ["email-draft"],
  installed: false,
};
const C: AgentStub = {
  id: "a3" as AgentId,
  name: "Custom Tinkerer",
  description: "A hand-built agent.",
  origin: "custom",
  costTier: "high",
  skills: ["sheets"],
  installed: true,
};

const SAMPLE = [A, B, C] as const;

describe("filterAgents", () => {
  it("my and installed both return installed agents", () => {
    expect(filterAgents(SAMPLE, "my", null).map((a) => a.id)).toEqual([
      A.id,
      C.id,
    ]);
    expect(filterAgents(SAMPLE, "installed", null).map((a) => a.id)).toEqual([
      A.id,
      C.id,
    ]);
  });

  it("available returns only non-installed agents", () => {
    expect(filterAgents(SAMPLE, "available", null).map((a) => a.id)).toEqual([
      B.id,
    ]);
  });

  it("custom returns only custom-origin agents", () => {
    expect(filterAgents(SAMPLE, "custom", null).map((a) => a.id)).toEqual([
      C.id,
    ]);
  });

  it("by_skill with no skill returns everyone", () => {
    expect(filterAgents(SAMPLE, "by_skill", null).map((a) => a.id)).toEqual([
      A.id,
      B.id,
      C.id,
    ]);
  });

  it("by_skill matches case-insensitively against an agent's skills", () => {
    expect(
      filterAgents(SAMPLE, "by_skill", "Web-Search").map((a) => a.id),
    ).toEqual([A.id]);
    expect(filterAgents(SAMPLE, "by_skill", "sheets").map((a) => a.id)).toEqual(
      [C.id],
    );
    expect(
      filterAgents(SAMPLE, "by_skill", "nonexistent").map((a) => a.id),
    ).toEqual([]);
  });
});

describe("searchAgents", () => {
  it("returns everyone for empty query", () => {
    expect(searchAgents(SAMPLE, "").map((a) => a.id)).toEqual([
      A.id,
      B.id,
      C.id,
    ]);
  });

  it("matches name and description case-insensitively", () => {
    expect(searchAgents(SAMPLE, "EMAIL").map((a) => a.id)).toEqual([B.id]);
    expect(searchAgents(SAMPLE, "tone").map((a) => a.id)).toEqual([B.id]);
    expect(searchAgents(SAMPLE, "library").map((a) => a.id)).toEqual([A.id]);
  });
});

describe("labels", () => {
  it("exposes a stable filter-label map", () => {
    expect(AGENT_FILTER_LABELS.my).toBe("My agents");
    expect(AGENT_FILTER_LABELS.installed).toBe("Installed");
    expect(AGENT_FILTER_LABELS.available).toBe("Available");
    expect(AGENT_FILTER_LABELS.custom).toBe("Custom");
    expect(AGENT_FILTER_LABELS.by_skill).toBe("By skill");
  });

  it("exposes a stable cost-label map", () => {
    expect(AGENT_COST_LABELS.free).toBe("Free");
    expect(AGENT_COST_LABELS.per_use).toBe("Per-use");
  });
});

describe("STARTER_RECOMMENDATIONS", () => {
  it("ships 3-4 starters, all non-installed, all with descriptions", () => {
    // Per the UI/UX preamble: empty My agents → 3-4 recommended starters.
    expect(STARTER_RECOMMENDATIONS.length).toBeGreaterThanOrEqual(3);
    expect(STARTER_RECOMMENDATIONS.length).toBeLessThanOrEqual(4);
    for (const agent of STARTER_RECOMMENDATIONS) {
      expect(agent.installed).toBe(false);
      expect(agent.description.length).toBeGreaterThan(0);
    }
  });
});
