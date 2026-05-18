import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProjectActivityTab, type ProjectActivity } from "./ProjectActivityTab";

const ACTIVITY: ReadonlyArray<ProjectActivity> = [
  {
    id: "evt-1",
    ref: { kind: "chat", id: "chat-abc" },
    label: "Q4 sales brainstorm",
    summary: "Sarah opened a new thread",
    at: new Date(Date.now() - 5 * 60_000).toISOString(),
    actorName: "Sarah Chen",
  },
  {
    id: "evt-2",
    ref: { kind: "todo", id: "todo-xyz" },
    label: "Send pricing memo",
    at: new Date(Date.now() - 2 * 3_600_000).toISOString(),
    actorName: "Marcus Wells",
  },
];

describe("ProjectActivityTab", () => {
  it("renders a skeleton list while activity is null", () => {
    render(<ProjectActivityTab activity={null} />);
    expect(screen.getByTestId("project-activity-tab")).toHaveAttribute(
      "data-state",
      "loading",
    );
    expect(screen.getAllByTestId("project-activity-skeleton")).toHaveLength(4);
  });

  it("renders the empty state when activity is an empty array", () => {
    render(<ProjectActivityTab activity={[]} />);
    expect(screen.getByTestId("project-activity-tab")).toHaveAttribute(
      "data-state",
      "empty",
    );
    expect(screen.getByTestId("project-activity-empty")).toBeInTheDocument();
  });

  it("renders one row per activity entry with label, summary, and ref data", () => {
    render(<ProjectActivityTab activity={ACTIVITY} />);
    const rows = screen.getAllByTestId("project-activity-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute("data-ref-kind", "chat");
    expect(rows[0]).toHaveAttribute("data-ref-id", "chat-abc");
    expect(rows[0]).toHaveAttribute("data-activity-id", "evt-1");
    expect(screen.getByText("Q4 sales brainstorm")).toBeInTheDocument();
    expect(screen.getByText("Sarah opened a new thread")).toBeInTheDocument();
    expect(rows[1]).toHaveAttribute("data-ref-kind", "todo");
  });

  it("wraps each row's inner content in renderItemLink when provided", () => {
    const renderItemLink = vi.fn(
      (ref: { kind: string; id: string }, children) => (
        <a
          href={`#/${ref.kind}/${ref.id}`}
          data-testid={`itemlink-${ref.kind}`}
        >
          {children}
        </a>
      ),
    );
    render(
      <ProjectActivityTab
        activity={ACTIVITY}
        renderItemLink={renderItemLink}
      />,
    );
    expect(renderItemLink).toHaveBeenCalledTimes(2);
    const chatLink = screen.getByTestId("itemlink-chat");
    expect(chatLink).toHaveAttribute("href", "#/chat/chat-abc");
    const todoLink = screen.getByTestId("itemlink-todo");
    expect(todoLink).toHaveAttribute("href", "#/todo/todo-xyz");
    // ItemLink wraps the labelled content so navigation includes the
    // label inside the link target.
    expect(chatLink.textContent).toContain("Q4 sales brainstorm");
  });

  it("renders a relative timestamp", () => {
    render(<ProjectActivityTab activity={ACTIVITY} />);
    const times = screen.getAllByTestId("project-activity-row-time");
    expect(times).toHaveLength(2);
    expect(times[0].textContent).toMatch(/m ago|just now/);
    expect(times[1].textContent).toMatch(/h ago/);
  });
});
