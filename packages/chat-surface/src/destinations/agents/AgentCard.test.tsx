import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentCard } from "./AgentCard";
import { type AgentId, type AgentStub } from "./_agents-stub";

function makeAgent(overrides: Partial<AgentStub> = {}): AgentStub {
  return {
    id: "a1" as AgentId,
    name: "Research Helper",
    description: "Searches the web and your library.",
    icon: "🔎",
    origin: "available",
    costTier: "low",
    skills: ["web-search"],
    installed: false,
    ...overrides,
  };
}

describe("AgentCard", () => {
  it("renders the name, description, cost chip, and icon", () => {
    render(
      <AgentCard
        agent={makeAgent()}
        onToggleInstall={vi.fn()}
        onViewDetails={vi.fn()}
      />,
    );
    expect(screen.getByTestId("agent-card-name")).toHaveTextContent(
      "Research Helper",
    );
    expect(screen.getByTestId("agent-card-description")).toHaveTextContent(
      "Searches the web and your library.",
    );
    expect(screen.getByTestId("agent-card-cost")).toHaveTextContent("Low cost");
    expect(screen.getByTestId("agent-card-icon")).toHaveTextContent("🔎");
  });

  it("shows Install when not installed and Uninstall when installed", () => {
    const { rerender } = render(
      <AgentCard
        agent={makeAgent({ installed: false })}
        onToggleInstall={vi.fn()}
        onViewDetails={vi.fn()}
      />,
    );
    expect(screen.getByTestId("agent-card-install")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-card-uninstall")).toBeNull();

    rerender(
      <AgentCard
        agent={makeAgent({ installed: true })}
        onToggleInstall={vi.fn()}
        onViewDetails={vi.fn()}
      />,
    );
    expect(screen.getByTestId("agent-card-uninstall")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-card-install")).toBeNull();
  });

  it("clicking Install fires onToggleInstall and not onViewDetails", () => {
    const onToggleInstall = vi.fn();
    const onViewDetails = vi.fn();
    render(
      <AgentCard
        agent={makeAgent()}
        onToggleInstall={onToggleInstall}
        onViewDetails={onViewDetails}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-card-install"));
    expect(onToggleInstall).toHaveBeenCalledTimes(1);
    expect(onViewDetails).not.toHaveBeenCalled();
  });

  it("clicking the card body triggers View Details", () => {
    const onViewDetails = vi.fn();
    render(
      <AgentCard
        agent={makeAgent()}
        onToggleInstall={vi.fn()}
        onViewDetails={onViewDetails}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-card"));
    expect(onViewDetails).toHaveBeenCalledWith(
      expect.objectContaining({ id: "a1" }),
    );
  });

  it("Enter on the card triggers View Details", () => {
    const onViewDetails = vi.fn();
    render(
      <AgentCard
        agent={makeAgent()}
        onToggleInstall={vi.fn()}
        onViewDetails={onViewDetails}
      />,
    );
    fireEvent.keyDown(screen.getByTestId("agent-card"), { key: "Enter" });
    expect(onViewDetails).toHaveBeenCalledTimes(1);
  });

  it("renders cost tone classes consistent with the cost tier", () => {
    const { rerender } = render(
      <AgentCard
        agent={makeAgent({ costTier: "free" })}
        onToggleInstall={vi.fn()}
        onViewDetails={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("agent-card-cost").getAttribute("data-cost-tier"),
    ).toBe("free");
    rerender(
      <AgentCard
        agent={makeAgent({ costTier: "per_use" })}
        onToggleInstall={vi.fn()}
        onViewDetails={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("agent-card-cost").getAttribute("data-cost-tier"),
    ).toBe("per_use");
  });

  it("falls back to the first letter when no icon is provided", () => {
    render(
      <AgentCard
        agent={makeAgent({ icon: undefined, name: "zoom-thing" })}
        onToggleInstall={vi.fn()}
        onViewDetails={vi.fn()}
      />,
    );
    expect(screen.getByTestId("agent-card-icon")).toHaveTextContent("Z");
  });
});
