import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VersionHistoryTab, type AgentVersionRow } from "./VersionHistoryTab";

function makeVersion(overrides?: Partial<AgentVersionRow>): AgentVersionRow {
  return {
    id: "ver_1",
    agent_id: "agt_abc",
    version: 1,
    label: "Initial",
    created_at: "2026-05-01T12:00:00Z",
    created_by: "Sarah",
    instructions_snapshot: "Do the thing.",
    model_default_snapshot: {
      model_id: "anthropic:claude-sonnet-4-7",
      reasoning_depth: "balanced",
    },
    skills_snapshot: ["summarize"],
    connectors_default_snapshot: [],
    permissions_snapshot: {
      autonomy: "manual_approval",
      max_tool_calls_per_run: 50,
      max_output_tokens: 32_000,
      read_only: false,
      blocked_tool_families: [],
    },
    ...(overrides ?? {}),
  };
}

describe("<VersionHistoryTab />", () => {
  it("renders empty state when no versions", () => {
    render(<VersionHistoryTab versions={[]} />);
    expect(
      screen.getByTestId("agent-version-history-empty"),
    ).toBeInTheDocument();
  });

  it("renders the version list and snapshot for the highest version by default", () => {
    const v1 = makeVersion({ id: "ver_1", version: 1, label: "v1" });
    const v2 = makeVersion({
      id: "ver_2",
      version: 2,
      label: "Pre-Q3",
      instructions_snapshot: "Pre-Q3 instructions.",
    });
    const v3 = makeVersion({
      id: "ver_3",
      version: 3,
      label: "Current",
      instructions_snapshot: "Current instructions.",
    });
    render(<VersionHistoryTab versions={[v1, v2, v3]} />);

    expect(screen.getByTestId("agent-version-row-ver_1")).toBeInTheDocument();
    expect(screen.getByTestId("agent-version-row-ver_2")).toBeInTheDocument();
    expect(screen.getByTestId("agent-version-row-ver_3")).toBeInTheDocument();
    // Default selection is the highest version (v3).
    expect(
      screen.getByTestId("agent-version-snapshot-ver_3"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("agent-version-snapshot-ver_1"),
    ).not.toBeInTheDocument();
  });

  it("clicking a version row shows that version's read-only snapshot", () => {
    const v1 = makeVersion({
      id: "ver_1",
      version: 1,
      label: "v1",
      instructions_snapshot: "First instructions.",
    });
    const v2 = makeVersion({
      id: "ver_2",
      version: 2,
      label: "v2",
      instructions_snapshot: "Second instructions.",
    });
    render(<VersionHistoryTab versions={[v1, v2]} />);
    fireEvent.click(screen.getByTestId("agent-version-row-ver_1-button"));
    // Now v1's snapshot is rendered.
    const snapshot = screen.getByTestId("agent-version-snapshot-ver_1");
    expect(snapshot).toBeInTheDocument();
    expect(
      screen.getByTestId("agent-version-snapshot-ver_1-instructions"),
    ).toHaveTextContent("First instructions.");
    // Read-only pill renders to signal immutability.
    expect(
      screen.getByTestId("agent-version-snapshot-ver_1-read-only-pill"),
    ).toHaveTextContent(/read-only/i);
    // aria-readonly is wired on the instructions block.
    expect(
      screen
        .getByTestId("agent-version-snapshot-ver_1-instructions")
        .getAttribute("aria-readonly"),
    ).toBe("true");
  });

  it("snapshot is read-only — no inputs/textareas/buttons that mutate", () => {
    const v1 = makeVersion({
      id: "ver_1",
      version: 1,
      label: "v1",
      instructions_snapshot: "Frozen.",
    });
    render(<VersionHistoryTab versions={[v1]} />);
    const snapshot = screen.getByTestId("agent-version-snapshot-ver_1");
    expect(snapshot.querySelectorAll("input").length).toBe(0);
    expect(snapshot.querySelectorAll("textarea").length).toBe(0);
    // Only allowed control is the "Use for routine" button, and it's
    // gated by the onUseForRoutine prop; when absent, no buttons.
    expect(snapshot.querySelectorAll("button").length).toBe(0);
  });

  it("renders 'Use this version in a routine' when onUseForRoutine is provided", () => {
    const onUseForRoutine = vi.fn();
    const v1 = makeVersion({ id: "ver_1", version: 1, label: "v1" });
    render(
      <VersionHistoryTab versions={[v1]} onUseForRoutine={onUseForRoutine} />,
    );
    fireEvent.click(
      screen.getByTestId("agent-version-snapshot-ver_1-use-for-routine"),
    );
    expect(onUseForRoutine).toHaveBeenCalledTimes(1);
    expect(onUseForRoutine).toHaveBeenCalledWith("ver_1");
  });

  it("respects initialSelectedId when supplied", () => {
    const v1 = makeVersion({ id: "ver_1", version: 1, label: "v1" });
    const v2 = makeVersion({ id: "ver_2", version: 2, label: "v2" });
    render(<VersionHistoryTab versions={[v1, v2]} initialSelectedId="ver_1" />);
    expect(
      screen.getByTestId("agent-version-snapshot-ver_1"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("agent-version-snapshot-ver_2"),
    ).not.toBeInTheDocument();
  });

  it("renders facts for model, depth, skills, connectors, autonomy, read-only", () => {
    const v1 = makeVersion({
      id: "ver_1",
      version: 1,
      label: "v1",
      skills_snapshot: ["a", "b"],
      connectors_default_snapshot: ["slack"],
      permissions_snapshot: {
        autonomy: "auto_apply",
        max_tool_calls_per_run: 10,
        max_output_tokens: 8000,
        read_only: true,
        blocked_tool_families: [],
      },
    });
    render(<VersionHistoryTab versions={[v1]} />);
    expect(
      screen.getByTestId("agent-version-snapshot-ver_1-skills"),
    ).toHaveTextContent("2");
    expect(
      screen.getByTestId("agent-version-snapshot-ver_1-connectors"),
    ).toHaveTextContent("1");
    expect(
      screen.getByTestId("agent-version-snapshot-ver_1-autonomy"),
    ).toHaveTextContent("auto_apply");
    expect(
      screen.getByTestId("agent-version-snapshot-ver_1-read-only"),
    ).toHaveTextContent("Yes");
  });
});
