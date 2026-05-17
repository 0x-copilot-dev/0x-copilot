import type { SectionResult } from "@enterprise-search/api-types";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HomeFocusItem } from "../_home-stub";
import { TodaysFocus } from "./TodaysFocus";

const NOW_MS = Date.parse("2026-05-18T12:00:00Z");

function makeItem(overrides: Partial<HomeFocusItem> = {}): HomeFocusItem {
  return {
    todo_id: "td_001",
    title: "Review Q1 launch brief",
    kind: "todo",
    priority: "med",
    due_at: "2026-05-18T15:00:00Z",
    is_overdue: false,
    urgency_score: 55,
    ...overrides,
  };
}

describe("<TodaysFocus>", () => {
  it("renders the top-3 server-determined items when status='ok' (does NOT re-slice)", () => {
    const focus: SectionResult<HomeFocusItem[]> = {
      status: "ok",
      data: [
        makeItem({ todo_id: "td_a", priority: "high", urgency_score: 85 }),
        makeItem({ todo_id: "td_b", priority: "med", urgency_score: 50 }),
        makeItem({ todo_id: "td_c", priority: "low", urgency_score: 20 }),
      ],
    };
    render(<TodaysFocus focus={focus} now={NOW_MS} />);
    const section = screen.getByTestId("home-todays-focus");
    expect(section).toHaveAttribute("data-section-status", "ok");
    const rows = screen.getAllByTestId("home-todays-focus-row");
    expect(rows).toHaveLength(3);
    expect(rows[0].getAttribute("data-priority")).toBe("high");
    expect(rows[2].getAttribute("data-priority")).toBe("low");
  });

  it("renders kind icons via aria-label for AT (todo / approval / review)", () => {
    const focus: SectionResult<HomeFocusItem[]> = {
      status: "ok",
      data: [
        makeItem({ todo_id: "k1", kind: "todo" }),
        makeItem({ todo_id: "k2", kind: "approval" }),
        makeItem({ todo_id: "k3", kind: "review" }),
      ],
    };
    render(<TodaysFocus focus={focus} now={NOW_MS} />);
    const icons = screen.getAllByTestId("home-todays-focus-kind-icon");
    expect(icons[0]).toHaveAttribute("data-kind", "todo");
    expect(icons[1]).toHaveAttribute("data-kind", "approval");
    expect(icons[2]).toHaveAttribute("data-kind", "review");
  });

  it("flags overdue rows and uses 'error' tone for the urgency pill", () => {
    const focus: SectionResult<HomeFocusItem[]> = {
      status: "ok",
      data: [
        makeItem({
          todo_id: "od_1",
          is_overdue: true,
          urgency_score: 30, // low score but overdue overrides
          due_at: "2026-05-17T10:00:00Z",
        }),
      ],
    };
    render(<TodaysFocus focus={focus} now={NOW_MS} />);
    const row = screen.getByTestId("home-todays-focus-row");
    expect(row).toHaveAttribute("data-overdue", "true");
    const pill = screen.getByTestId("status-pill");
    expect(pill).toHaveAttribute("data-status", "error");
    expect(screen.getByTestId("home-todays-focus-due")).toHaveTextContent(
      /overdue/,
    );
  });

  it("renders empty state when status='ok' and data is empty", () => {
    const focus: SectionResult<HomeFocusItem[]> = { status: "ok", data: [] };
    render(<TodaysFocus focus={focus} />);
    expect(screen.getByTestId("home-todays-focus-empty")).toHaveAttribute(
      "data-section-status",
      "ok",
    );
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      "Nothing urgent.",
    );
  });

  it("renders the error branch with role=alert", () => {
    const focus: SectionResult<HomeFocusItem[]> = {
      status: "error",
      error: "todos service degraded",
    };
    render(<TodaysFocus focus={focus} />);
    const err = screen.getByTestId("home-todays-focus-error");
    expect(err).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("empty-state-body")).toHaveTextContent(
      "todos service degraded",
    );
  });

  it("renders the unavailable branch", () => {
    const focus: SectionResult<HomeFocusItem[]> = { status: "unavailable" };
    render(<TodaysFocus focus={focus} />);
    expect(screen.getByTestId("home-todays-focus-unavailable")).toHaveAttribute(
      "data-section-status",
      "unavailable",
    );
  });

  it("omits the due chip when due_at is undefined", () => {
    const focus: SectionResult<HomeFocusItem[]> = {
      status: "ok",
      data: [makeItem({ due_at: undefined, is_overdue: false })],
    };
    render(<TodaysFocus focus={focus} />);
    expect(
      screen.queryByTestId("home-todays-focus-due"),
    ).not.toBeInTheDocument();
  });
});
