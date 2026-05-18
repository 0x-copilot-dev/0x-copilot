import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentsDestination } from "./AgentsDestination";
import { type AgentId, type AgentStub } from "./_agents-stub";

const INSTALLED: AgentStub = {
  id: "a-installed" as AgentId,
  name: "Email Drafter",
  description: "Drafts replies in your tone.",
  origin: "installed",
  costTier: "free",
  skills: ["email-draft"],
  installed: true,
};
const AVAILABLE: AgentStub = {
  id: "a-available" as AgentId,
  name: "Slides Builder",
  description: "Turns a Doc into a presentation.",
  origin: "available",
  costTier: "medium",
  skills: ["slides"],
  installed: false,
};
const CUSTOM: AgentStub = {
  id: "a-custom" as AgentId,
  name: "My Tinkerer",
  description: "Hand-built agent.",
  origin: "custom",
  costTier: "high",
  skills: ["sheets"],
  installed: true,
};

describe("AgentsDestination", () => {
  it("renders the page header with title and Create custom agent CTA", () => {
    render(<AgentsDestination />);
    expect(screen.getByTestId("agents-destination")).toBeInTheDocument();
    expect(screen.getByTestId("agents-header")).toHaveTextContent("Agents");
    expect(screen.getByTestId("agents-create-cta")).toBeInTheDocument();
  });

  it("renders 3-4 starter recommendations when My agents has nothing installed", () => {
    render(<AgentsDestination agents={[]} />);
    expect(screen.getByTestId("agents-starters")).toBeInTheDocument();
    const cards = screen.getAllByTestId("agent-card");
    expect(cards.length).toBeGreaterThanOrEqual(3);
    expect(cards.length).toBeLessThanOrEqual(4);
  });

  it("does not render starters once at least one agent is installed", () => {
    render(<AgentsDestination agents={[INSTALLED, AVAILABLE]} />);
    expect(screen.queryByTestId("agents-starters")).toBeNull();
    // My agents tab → only installed
    expect(screen.getAllByTestId("agent-card")).toHaveLength(1);
  });

  it("switching to Available shows only non-installed agents", () => {
    render(<AgentsDestination agents={[INSTALLED, AVAILABLE, CUSTOM]} />);
    fireEvent.click(screen.getByTestId("agents-tab-available"));
    const cards = screen.getAllByTestId("agent-card");
    expect(cards).toHaveLength(1);
    expect(cards[0].getAttribute("data-agent-id")).toBe(AVAILABLE.id);
  });

  it("switching to Custom shows only custom-origin agents", () => {
    render(<AgentsDestination agents={[INSTALLED, AVAILABLE, CUSTOM]} />);
    fireEvent.click(screen.getByTestId("agents-tab-custom"));
    const cards = screen.getAllByTestId("agent-card");
    expect(cards).toHaveLength(1);
    expect(cards[0].getAttribute("data-agent-id")).toBe(CUSTOM.id);
  });

  it("search filters by name and description (case-insensitive)", () => {
    render(<AgentsDestination agents={[INSTALLED, AVAILABLE, CUSTOM]} />);
    // Switch to "installed" to see both installed agents (so the search has
    // something to narrow).
    fireEvent.click(screen.getByTestId("agents-tab-installed"));
    expect(screen.getAllByTestId("agent-card")).toHaveLength(2);
    fireEvent.change(screen.getByTestId("agents-search"), {
      target: { value: "TINKERER" },
    });
    const cards = screen.getAllByTestId("agent-card");
    expect(cards).toHaveLength(1);
    expect(cards[0].getAttribute("data-agent-id")).toBe(CUSTOM.id);
  });

  it("renders the empty state when no agents match", () => {
    render(<AgentsDestination agents={[INSTALLED]} />);
    fireEvent.click(screen.getByTestId("agents-tab-installed"));
    fireEvent.change(screen.getByTestId("agents-search"), {
      target: { value: "nonexistent-agent" },
    });
    expect(screen.getByTestId("agents-empty")).toBeInTheDocument();
  });

  it("Create custom agent CTA fires the callback", () => {
    const onCreateCustom = vi.fn();
    render(<AgentsDestination onCreateCustom={onCreateCustom} />);
    fireEvent.click(screen.getByTestId("agents-create-cta"));
    expect(onCreateCustom).toHaveBeenCalledTimes(1);
  });

  it("clicking install on a card fires onToggleInstall with that agent", () => {
    const onToggleInstall = vi.fn();
    render(
      <AgentsDestination
        agents={[INSTALLED, AVAILABLE]}
        onToggleInstall={onToggleInstall}
      />,
    );
    fireEvent.click(screen.getByTestId("agents-tab-available"));
    fireEvent.click(screen.getByTestId("agent-card-install"));
    expect(onToggleInstall).toHaveBeenCalledWith(
      expect.objectContaining({ id: AVAILABLE.id }),
    );
  });

  it("clicking a starter install fires onToggleInstall with the starter", () => {
    const onToggleInstall = vi.fn();
    render(<AgentsDestination agents={[]} onToggleInstall={onToggleInstall} />);
    // First starter card's install button.
    const buttons = screen.getAllByTestId("agent-card-install");
    fireEvent.click(buttons[0]);
    expect(onToggleInstall).toHaveBeenCalledTimes(1);
    expect(onToggleInstall.mock.calls[0][0].installed).toBe(false);
  });

  it("clicking a card body fires onViewDetails (not onToggleInstall)", () => {
    const onViewDetails = vi.fn();
    const onToggleInstall = vi.fn();
    render(
      <AgentsDestination
        agents={[INSTALLED]}
        onViewDetails={onViewDetails}
        onToggleInstall={onToggleInstall}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-card"));
    expect(onViewDetails).toHaveBeenCalledWith(
      expect.objectContaining({ id: INSTALLED.id }),
    );
    expect(onToggleInstall).not.toHaveBeenCalled();
  });

  it("auto-focuses the search input on mount", async () => {
    render(<AgentsDestination />);
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByTestId("agents-search"));
    });
  });

  it("renders all 5 filter tabs with correct active state", () => {
    render(<AgentsDestination />);
    expect(screen.getByTestId("agents-tab-my")).toHaveAttribute(
      "data-active",
      "true",
    );
    expect(screen.getByTestId("agents-tab-installed")).toBeInTheDocument();
    expect(screen.getByTestId("agents-tab-available")).toBeInTheDocument();
    expect(screen.getByTestId("agents-tab-custom")).toBeInTheDocument();
    expect(screen.getByTestId("agents-tab-by_skill")).toBeInTheDocument();
  });
});
