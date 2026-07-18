// PR 3.2.2 — shared SubagentCard component tests. Moved down with the
// component (PR-1.5); the same assertions run from chat-surface.
//
// Covers AC-2, 3, 4, 5, 6, 7, 8 from the PRD.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SubagentActivityRecord } from "./subagentHelpers";
import { SubagentCard } from "./SubagentCard";
import type { SubagentCardViewModel } from "./subagentCardViewModel";

function vm(
  overrides: Partial<SubagentCardViewModel> = {},
): SubagentCardViewModel {
  return {
    taskId: "task_doc_reader",
    name: "doc_reader",
    status: "completed",
    terminal: true,
    task: "Read positioning + GTM plan, extract claims",
    finding: "Hero claim: time-to-answer + citation trust.",
    fullResult: "Full text the user could see when activities is empty.",
    startedAt: "2026-05-06T10:00:00Z",
    completedAt: "2026-05-06T10:00:18Z",
    durationMs: 18000,
    isError: false,
    ...overrides,
  };
}

function activity(
  overrides: Partial<SubagentActivityRecord> = {},
): SubagentActivityRecord {
  return {
    id: "call_a",
    kind: "tool",
    title: "search_notion",
    status: "completed",
    summary: "4 hits",
    inputSummary: null,
    result: null,
    isError: false,
    ...overrides,
  };
}

describe("SubagentCard", () => {
  it("renders task and finding lines for a completed subagent", () => {
    render(<SubagentCard view={vm()} activities={[]} />);
    expect(
      screen.getByText("Read positioning + GTM plan, extract claims"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Hero claim: time-to-answer + citation trust."),
    ).toBeInTheDocument();
  });

  it("hides the finding line for non-terminal subagents", () => {
    render(
      <SubagentCard
        view={vm({ status: "running", terminal: false, finding: null })}
        activities={[]}
      />,
    );
    expect(
      screen.queryByText("Hero claim: time-to-answer + citation trust."),
    ).not.toBeInTheDocument();
  });

  it("renders the disclosure closed by default", () => {
    render(<SubagentCard view={vm()} activities={[activity()]} />);
    const details = screen.getByTestId("subagent-card-details-task_doc_reader");
    expect(details).toBeInstanceOf(HTMLDetailsElement);
    expect((details as HTMLDetailsElement).open).toBe(false);
  });

  it("auto-opens the disclosure when defaultOpen is true", () => {
    render(<SubagentCard view={vm()} activities={[activity()]} defaultOpen />);
    const details = screen.getByTestId("subagent-card-details-task_doc_reader");
    expect((details as HTMLDetailsElement).open).toBe(true);
  });

  it("renders the activity timeline inside the disclosure when activities is non-empty", async () => {
    const user = userEvent.setup();
    render(
      <SubagentCard
        view={vm()}
        activities={[
          activity({ id: "a", title: "search_notion", summary: "4 hits" }),
          activity({
            id: "b",
            title: "read_file",
            summary: "GTM plan",
          }),
        ]}
      />,
    );
    const details = screen.getByTestId(
      "subagent-card-details-task_doc_reader",
    ) as HTMLDetailsElement;
    await user.click(details.querySelector("summary")!);
    expect(details.open).toBe(true);
    expect(
      details.querySelectorAll(".aui-tool-card__timeline-item").length,
    ).toBe(2);
    expect(details).toHaveTextContent("4 hits");
    expect(details).toHaveTextContent("GTM plan");
  });

  it("falls back to the full result text when activities is empty (AC-4)", async () => {
    const user = userEvent.setup();
    render(
      <SubagentCard
        view={vm({
          fullResult: "The full prime-checker code goes here.",
        })}
        activities={[]}
      />,
    );
    const details = screen.getByTestId(
      "subagent-card-details-task_doc_reader",
    ) as HTMLDetailsElement;
    await user.click(details.querySelector("summary")!);
    expect(details.open).toBe(true);
    expect(details).toHaveTextContent("The full prime-checker code goes here.");
    expect(
      screen.queryByText(/No detailed activity was reported/),
    ).not.toBeInTheDocument();
  });

  it("shows the calm fallback when activities is empty AND fullResult is empty (AC-5)", async () => {
    const user = userEvent.setup();
    render(<SubagentCard view={vm({ fullResult: null })} activities={[]} />);
    await user.click(
      screen
        .getByTestId("subagent-card-details-task_doc_reader")
        .querySelector("summary")!,
    );
    expect(
      screen.getByText("Single-shot response — no inner tool calls."),
    ).toBeInTheDocument();
  });

  it("composes the timeline className when provided (AC-6 pane variant)", () => {
    const { container } = render(
      <SubagentCard
        view={vm()}
        activities={[activity()]}
        timelineClassName="atlas-workspace-agent__timeline aui-tool-card__timeline"
      />,
    );
    const timeline = container.querySelector(
      ".atlas-workspace-agent__timeline",
    );
    expect(timeline).not.toBeNull();
    expect(timeline?.classList.contains("aui-tool-card__timeline")).toBe(true);
  });

  it("renders status badges for non-completed lifecycle states (AC-8)", () => {
    const { rerender } = render(
      <SubagentCard
        view={vm({ status: "running", terminal: false })}
        activities={[]}
      />,
    );
    expect(screen.getByText("Running")).toBeInTheDocument();
    rerender(
      <SubagentCard view={vm({ status: "completed" })} activities={[]} />,
    );
    expect(screen.queryByText("Done")).not.toBeInTheDocument();
    expect(screen.getByText("Completed in 18s")).toBeInTheDocument();
    rerender(
      <SubagentCard
        view={vm({ status: "failed", isError: true })}
        activities={[]}
      />,
    );
    expect(screen.getByText("Failed")).toBeInTheDocument();
    rerender(
      <SubagentCard view={vm({ status: "cancelled" })} activities={[]} />,
    );
    expect(screen.getByText("Cancelled")).toBeInTheDocument();
    rerender(
      <SubagentCard
        view={vm({ status: "queued", terminal: false })}
        activities={[]}
      />,
    );
    expect(screen.getByText("Queued")).toBeInTheDocument();
  });

  it("invokes onJumpToThread when provided (AC-9)", async () => {
    const onJumpToThread = vi.fn();
    render(
      <SubagentCard
        view={vm()}
        activities={[]}
        onJumpToThread={onJumpToThread}
      />,
    );
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: /Open doc_reader in thread/ }),
    );
    expect(onJumpToThread).toHaveBeenCalledOnce();
  });

  it("omits the jump-to-thread button when no callback is provided", () => {
    render(<SubagentCard view={vm()} activities={[]} />);
    expect(
      screen.queryByRole("button", { name: /Open .* in thread/ }),
    ).not.toBeInTheDocument();
  });

  it("renders meta text 'working…' for running subagents", () => {
    render(
      <SubagentCard
        view={vm({ status: "running", terminal: false, durationMs: null })}
        activities={[]}
      />,
    );
    expect(screen.getByText(/working…/)).toBeInTheDocument();
  });

  it("renders meta text with formatted duration when terminal", () => {
    render(<SubagentCard view={vm({ durationMs: 4200 })} activities={[]} />);
    expect(screen.getByText(/Completed in 4.2s/)).toBeInTheDocument();
  });
});
