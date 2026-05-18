import { describe, expect, it, vi } from "vitest";

import {
  AGENT_FILTER_LABELS,
  resolveAgentItemRef,
  type AgentId,
  type AgentItemRef,
  type AgentStub,
} from "./index";

const stub: AgentStub = {
  id: "a1" as AgentId,
  name: "Research Helper",
  description: "Searches the web.",
  icon: "🔎",
  origin: "installed",
  costTier: "low",
  skills: ["web-search"],
  installed: true,
};

describe("resolveAgentItemRef", () => {
  it("returns a display payload when the stub is known", () => {
    const ref: AgentItemRef = { kind: "agent", id: "a1" as AgentId };
    const display = resolveAgentItemRef(ref, () => stub);
    expect(display).not.toBeNull();
    expect(display).toEqual({
      kind: "agent",
      id: "a1",
      label: "Research Helper",
      icon: "🔎",
      route: { kind: "agent", agentId: "a1" },
    });
  });

  it("returns null when the lookup returns null (unknown id)", () => {
    const ref: AgentItemRef = { kind: "agent", id: "missing" as AgentId };
    const display = resolveAgentItemRef(ref, () => null);
    expect(display).toBeNull();
  });

  it("passes the ref id to the lookup function", () => {
    const lookup = vi.fn(() => stub);
    resolveAgentItemRef(
      { kind: "agent", id: "a1" as AgentId } as AgentItemRef,
      lookup,
    );
    expect(lookup).toHaveBeenCalledWith("a1");
  });

  it("propagates the absence of an icon", () => {
    const noIcon: AgentStub = { ...stub, icon: undefined };
    const display = resolveAgentItemRef(
      { kind: "agent", id: "a1" as AgentId },
      () => noIcon,
    );
    expect(display).not.toBeNull();
    expect(display!.icon).toBeUndefined();
  });
});

describe("public exports", () => {
  it("re-exports labels and helpers from the stub module", () => {
    expect(AGENT_FILTER_LABELS.my).toBe("My agents");
  });
});
