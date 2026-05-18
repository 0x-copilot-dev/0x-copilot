import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentDetailView, type AgentDetailViewModel } from "./AgentDetailView";

function makeAgent(
  overrides?: Partial<AgentDetailViewModel>,
): AgentDetailViewModel {
  return {
    id: "agt_abc",
    name: "Inbox Triage",
    slug: "inbox-triage",
    description: "Triages incoming inbox items by priority.",
    icon_emoji: "📥",
    color_hue: 220,
    origin: "custom",
    status: "installed",
    viewer_is_owner: true,
    instructions: "Sort items by priority. Tag with urgency.",
    model_default: {
      model_id: "anthropic:claude-sonnet-4-7",
      reasoning_depth: "balanced",
    },
    skills: ["summarize", "extract"],
    connectors_default: ["slack"],
    permissions: {
      autonomy: "manual_approval",
      max_tool_calls_per_run: 50,
      max_output_tokens: 32_000,
      read_only: false,
      blocked_tool_families: [],
    },
    version: 3,
    updated_at: "2026-05-18T12:00:00Z",
    ...(overrides ?? {}),
  };
}

describe("<AgentDetailView />", () => {
  it("renders hero with name, slug, origin and status pills, plus version", () => {
    render(<AgentDetailView agent={makeAgent()} />);
    expect(screen.getByTestId("agent-detail-name")).toHaveTextContent(
      "Inbox Triage",
    );
    expect(screen.getByTestId("agent-detail-slug")).toHaveTextContent(
      "inbox-triage",
    );
    expect(screen.getByTestId("agent-detail-origin-pill")).toHaveTextContent(
      "custom",
    );
    expect(screen.getByTestId("agent-detail-status-pill")).toHaveTextContent(
      "installed",
    );
    expect(screen.getByTestId("agent-detail-version")).toHaveTextContent("v3");
  });

  it("renders the quick-facts grid (model/depth/skills/connectors/autonomy/read-only)", () => {
    render(<AgentDetailView agent={makeAgent()} />);
    expect(screen.getByTestId("agent-detail-fact-model")).toHaveTextContent(
      "anthropic:claude-sonnet-4-7",
    );
    expect(screen.getByTestId("agent-detail-fact-depth")).toHaveTextContent(
      "balanced",
    );
    expect(screen.getByTestId("agent-detail-fact-skills")).toHaveTextContent(
      "2",
    );
    expect(
      screen.getByTestId("agent-detail-fact-connectors"),
    ).toHaveTextContent("1");
    expect(screen.getByTestId("agent-detail-fact-autonomy")).toHaveTextContent(
      "manual_approval",
    );
    expect(screen.getByTestId("agent-detail-fact-read-only")).toHaveTextContent(
      "No",
    );
  });

  it("renders instructions as read-only <pre> (no inputs)", () => {
    render(<AgentDetailView agent={makeAgent()} />);
    const body = screen.getByTestId("agent-detail-instructions-body");
    expect(body.tagName.toLowerCase()).toBe("pre");
    expect(body).toHaveTextContent(/sort items by priority/i);
    // Hard contract: no <input> or <textarea> for instructions on the
    // detail view.
    const section = screen.getByTestId("agent-detail-instructions");
    expect(section.querySelector("input")).toBeNull();
    expect(section.querySelector("textarea")).toBeNull();
  });

  it("shows empty state when instructions is empty", () => {
    render(<AgentDetailView agent={makeAgent({ instructions: "" })} />);
    expect(
      screen.getByTestId("agent-detail-instructions-empty"),
    ).toBeInTheDocument();
  });

  it("Customize → onEdit when viewer owns a custom agent", () => {
    const onEdit = vi.fn();
    const onForkRequest = vi.fn();
    render(
      <AgentDetailView
        agent={makeAgent({ origin: "custom", viewer_is_owner: true })}
        onEdit={onEdit}
        onForkRequest={onForkRequest}
      />,
    );
    const btn = screen.getByTestId("agent-detail-customize");
    expect(btn.getAttribute("data-fork-required")).toBe("false");
    fireEvent.click(btn);
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onForkRequest).not.toHaveBeenCalled();
  });

  it("Customize → onForkRequest for system agents (fork dialog forward)", () => {
    const onEdit = vi.fn();
    const onForkRequest = vi.fn();
    render(
      <AgentDetailView
        agent={makeAgent({ origin: "system", viewer_is_owner: false })}
        onEdit={onEdit}
        onForkRequest={onForkRequest}
      />,
    );
    const btn = screen.getByTestId("agent-detail-customize");
    expect(btn.getAttribute("data-fork-required")).toBe("true");
    fireEvent.click(btn);
    expect(onForkRequest).toHaveBeenCalledTimes(1);
    expect(onEdit).not.toHaveBeenCalled();
  });

  it("Customize → onForkRequest for community agents (fork-required)", () => {
    const onForkRequest = vi.fn();
    render(
      <AgentDetailView
        agent={makeAgent({ origin: "community", viewer_is_owner: false })}
        onForkRequest={onForkRequest}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-detail-customize"));
    expect(onForkRequest).toHaveBeenCalledTimes(1);
  });

  it("Customize → onForkRequest for custom agent the viewer does not own", () => {
    const onEdit = vi.fn();
    const onForkRequest = vi.fn();
    render(
      <AgentDetailView
        agent={makeAgent({ origin: "custom", viewer_is_owner: false })}
        onEdit={onEdit}
        onForkRequest={onForkRequest}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-detail-customize"));
    expect(onForkRequest).toHaveBeenCalledTimes(1);
    expect(onEdit).not.toHaveBeenCalled();
  });

  it("renders usage and versions slot when provided", () => {
    render(
      <AgentDetailView
        agent={makeAgent()}
        usageSlot={<div data-testid="my-usage">chart</div>}
        versionsSlot={<div data-testid="my-versions">versions</div>}
        onOpenVersions={vi.fn()}
      />,
    );
    expect(screen.getByTestId("agent-detail-usage-slot")).toBeInTheDocument();
    expect(screen.getByTestId("my-usage")).toBeInTheDocument();
    expect(
      screen.getByTestId("agent-detail-versions-slot"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("my-versions")).toBeInTheDocument();
    expect(
      screen.getByTestId("agent-detail-open-versions"),
    ).toBeInTheDocument();
  });

  it("View-all-versions fires onOpenVersions", () => {
    const onOpenVersions = vi.fn();
    render(
      <AgentDetailView
        agent={makeAgent()}
        versionsSlot={<div>x</div>}
        onOpenVersions={onOpenVersions}
      />,
    );
    fireEvent.click(screen.getByTestId("agent-detail-open-versions"));
    expect(onOpenVersions).toHaveBeenCalledTimes(1);
  });
});
