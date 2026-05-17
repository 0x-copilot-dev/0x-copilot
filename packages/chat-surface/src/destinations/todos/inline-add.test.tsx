import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  InlineAdd,
  parseQuickAddDate,
  type ProjectId,
  type TodoQuickAddInput,
} from "./inline-add";

// 2026-01-15 is a Thursday — picked so "next monday" lands on 2026-01-19
// and "next thursday" lands on 2026-01-22 (next week, never today).
const FIXED_NOW = new Date("2026-01-15T12:00:00.000Z").getTime();

describe("parseQuickAddDate", () => {
  it("returns the input untouched when no phrase matches", () => {
    expect(parseQuickAddDate("Draft renewal narrative")).toEqual({
      text: "Draft renewal narrative",
    });
  });

  it("strips and parses 'tomorrow'", () => {
    const result = parseQuickAddDate("Buy milk tomorrow", FIXED_NOW);
    expect(result.text).toBe("Buy milk");
    expect(result.due).toBe("2026-01-16");
  });

  it("strips and parses 'today'", () => {
    const result = parseQuickAddDate("Sync with Sam today", FIXED_NOW);
    expect(result.text).toBe("Sync with Sam");
    expect(result.due).toBe("2026-01-15");
  });

  it("strips and parses 'next monday' (any weekday)", () => {
    // 2026-01-15 = Thursday, so next Monday is 2026-01-19.
    const monday = parseQuickAddDate("Ship beta next monday", FIXED_NOW);
    expect(monday.text).toBe("Ship beta");
    expect(monday.due).toBe("2026-01-19");

    // "next thursday" on a Thursday → 7 days out, not today.
    const thursday = parseQuickAddDate("Standup next thursday", FIXED_NOW);
    expect(thursday.text).toBe("Standup");
    expect(thursday.due).toBe("2026-01-22");
  });

  it("strips and parses 'in N days'", () => {
    const three = parseQuickAddDate("Follow up in 3 days", FIXED_NOW);
    expect(three.text).toBe("Follow up");
    expect(three.due).toBe("2026-01-18");

    const ten = parseQuickAddDate("Q2 plan in 10 days", FIXED_NOW);
    expect(ten.due).toBe("2026-01-25");

    // singular "1 day" is also accepted.
    const one = parseQuickAddDate("Ping legal in 1 day", FIXED_NOW);
    expect(one.text).toBe("Ping legal");
    expect(one.due).toBe("2026-01-16");
  });

  it("ignores out-of-range 'in N days'", () => {
    const result = parseQuickAddDate("Long horizon in 999 days", FIXED_NOW);
    expect(result.due).toBeUndefined();
    expect(result.text).toBe("Long horizon in 999 days");
  });

  it("matches case-insensitively and only at the end", () => {
    // Trailing-only: 'tomorrow' in the middle stays in the text.
    const middle = parseQuickAddDate(
      "Reschedule the tomorrow review",
      FIXED_NOW,
    );
    expect(middle.due).toBeUndefined();
    expect(middle.text).toBe("Reschedule the tomorrow review");

    // Mixed case.
    const upper = parseQuickAddDate("CALL Sam TOMORROW", FIXED_NOW);
    expect(upper.text).toBe("CALL Sam");
    expect(upper.due).toBe("2026-01-16");
  });
});

describe("InlineAdd", () => {
  it("renders an empty composer with Unfiled hint when no defaultProject is given", () => {
    const onSubmit = vi.fn();
    render(<InlineAdd onSubmit={onSubmit} />);

    const form = screen.getByTestId("todo-inline-add");
    expect(form).toHaveAttribute("data-project-id", "unfiled");
    expect(
      screen.getByTestId("todo-inline-add-project-hint"),
    ).toHaveTextContent(/unfiled/i);
    expect(screen.getByTestId("todo-inline-add-submit")).toBeDisabled();
  });

  it("uses the prop-supplied default project (panel-context wins)", () => {
    const onSubmit = vi.fn();
    const projectId = "proj-abc" as ProjectId;
    render(<InlineAdd defaultProject={projectId} onSubmit={onSubmit} />);

    const form = screen.getByTestId("todo-inline-add");
    expect(form).toHaveAttribute("data-project-id", "proj-abc");
  });

  it("submits on Enter with parsed due date and resets the field", () => {
    const onSubmit = vi.fn();
    render(<InlineAdd onSubmit={onSubmit} nowMs={FIXED_NOW} />);

    const input = screen.getByTestId(
      "todo-inline-add-input",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Buy milk tomorrow" } });

    // Live preview should surface the parsed date.
    expect(screen.getByTestId("todo-inline-add-due-preview")).toHaveAttribute(
      "data-due",
      "2026-01-16",
    );

    fireEvent.keyDown(input, { key: "Enter" });

    const submitted: TodoQuickAddInput = onSubmit.mock.calls[0]?.[0];
    expect(submitted).toEqual({
      text: "Buy milk",
      project_id: null,
      due: "2026-01-16",
    });
    expect(input.value).toBe("");
  });

  it("submits with project_id from defaultProject and omits due when none parsed", () => {
    const onSubmit = vi.fn();
    const projectId = "proj-acme" as ProjectId;
    render(
      <InlineAdd
        defaultProject={projectId}
        onSubmit={onSubmit}
        nowMs={FIXED_NOW}
      />,
    );

    const input = screen.getByTestId("todo-inline-add-input");
    fireEvent.change(input, { target: { value: "Draft Q3 plan" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onSubmit).toHaveBeenCalledWith({
      text: "Draft Q3 plan",
      project_id: projectId,
    });
  });

  it("does not submit empty or whitespace-only input", () => {
    const onSubmit = vi.fn();
    render(<InlineAdd onSubmit={onSubmit} />);

    const input = screen.getByTestId("todo-inline-add-input");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("clears the field on Escape without submitting", () => {
    const onSubmit = vi.fn();
    render(<InlineAdd onSubmit={onSubmit} />);

    const input = screen.getByTestId(
      "todo-inline-add-input",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "draft something" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input.value).toBe("");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("also submits via the form's Add button", () => {
    const onSubmit = vi.fn();
    render(<InlineAdd onSubmit={onSubmit} nowMs={FIXED_NOW} />);

    const input = screen.getByTestId("todo-inline-add-input");
    fireEvent.change(input, { target: { value: "Ship beta next monday" } });
    fireEvent.click(screen.getByTestId("todo-inline-add-submit"));

    expect(onSubmit).toHaveBeenCalledWith({
      text: "Ship beta",
      project_id: null,
      due: "2026-01-19",
    });
  });
});
