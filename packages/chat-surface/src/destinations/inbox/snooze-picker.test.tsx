import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SnoozePicker } from "./snooze-picker";

// 2026-05-17 14:00 local — Sunday — picked so:
//   - +1h lands on the same day later that afternoon
//   - +tomorrow lands on Monday 2026-05-18 09:00
//   - +next-monday lands on the *following* Monday 2026-05-25 09:00
const REFERENCE_NOW = new Date(2026, 4, 17, 14, 0, 0, 0);

describe("<SnoozePicker>", () => {
  it("renders the four preset options + a custom input", () => {
    render(<SnoozePicker onSnooze={() => undefined} />);
    expect(screen.getByTestId("inbox-snooze-preset-one_hour")).toBeTruthy();
    expect(screen.getByTestId("inbox-snooze-preset-tomorrow")).toBeTruthy();
    expect(screen.getByTestId("inbox-snooze-preset-next_monday")).toBeTruthy();
    expect(screen.getByTestId("inbox-snooze-custom-input")).toBeTruthy();
  });

  it("emits an ISO-8601 string for the 1-hour preset", () => {
    const onSnooze = vi.fn();
    render(<SnoozePicker onSnooze={onSnooze} now={REFERENCE_NOW} />);
    fireEvent.click(screen.getByTestId("inbox-snooze-preset-one_hour"));
    expect(onSnooze).toHaveBeenCalledTimes(1);
    const iso = onSnooze.mock.calls[0][0] as string;
    const parsed = new Date(iso);
    // exactly +1 hour
    expect(parsed.getTime() - REFERENCE_NOW.getTime()).toBe(60 * 60 * 1000);
  });

  it("snoozes to 09:00 the next calendar day for tomorrow", () => {
    const onSnooze = vi.fn();
    render(<SnoozePicker onSnooze={onSnooze} now={REFERENCE_NOW} />);
    fireEvent.click(screen.getByTestId("inbox-snooze-preset-tomorrow"));
    const iso = onSnooze.mock.calls[0][0] as string;
    const parsed = new Date(iso);
    expect(parsed.getDate()).toBe(REFERENCE_NOW.getDate() + 1);
    expect(parsed.getHours()).toBe(9);
    expect(parsed.getMinutes()).toBe(0);
  });

  it("snoozes to the Monday strictly after `now` for next_monday", () => {
    const onSnooze = vi.fn();
    // REFERENCE_NOW is a Sunday — "next Monday" is the immediately
    // following day (delta = 1).
    render(<SnoozePicker onSnooze={onSnooze} now={REFERENCE_NOW} />);
    fireEvent.click(screen.getByTestId("inbox-snooze-preset-next_monday"));
    const iso = onSnooze.mock.calls[0][0] as string;
    const parsed = new Date(iso);
    expect(parsed.getDay()).toBe(1);
    expect(parsed.getHours()).toBe(9);

    // Picking a Monday as `now` must skip a week (Gmail/Linear semantics).
    onSnooze.mockClear();
    const monday = new Date(2026, 4, 18, 10, 0, 0, 0); // Monday 10:00
    render(<SnoozePicker onSnooze={onSnooze} now={monday} />);
    const presetButtons = screen.getAllByTestId(
      "inbox-snooze-preset-next_monday",
    );
    // second render produced an additional button — click the latest.
    fireEvent.click(presetButtons[presetButtons.length - 1]);
    const iso2 = onSnooze.mock.calls[0][0] as string;
    const parsed2 = new Date(iso2);
    expect(parsed2.getDay()).toBe(1);
    expect(parsed2.getDate() - monday.getDate()).toBe(7);
  });

  it("emits ISO from the custom datetime-local input on submit", () => {
    const onSnooze = vi.fn();
    render(<SnoozePicker onSnooze={onSnooze} now={REFERENCE_NOW} />);
    const input = screen.getByTestId(
      "inbox-snooze-custom-input",
    ) as HTMLInputElement;
    const submit = screen.getByTestId("inbox-snooze-custom-submit");
    // Submit is disabled while the input is empty.
    expect(submit).toBeDisabled();
    fireEvent.change(input, { target: { value: "2026-06-01T10:30" } });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    expect(onSnooze).toHaveBeenCalledTimes(1);
    const iso = onSnooze.mock.calls[0][0] as string;
    const parsed = new Date(iso);
    expect(parsed.getFullYear()).toBe(2026);
    expect(parsed.getMonth()).toBe(5); // June
    expect(parsed.getDate()).toBe(1);
    expect(parsed.getHours()).toBe(10);
    expect(parsed.getMinutes()).toBe(30);
  });

  it("does not emit on an unparseable custom value", () => {
    const onSnooze = vi.fn();
    render(<SnoozePicker onSnooze={onSnooze} />);
    const input = screen.getByTestId(
      "inbox-snooze-custom-input",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "not-a-date" } });
    fireEvent.click(screen.getByTestId("inbox-snooze-custom-submit"));
    expect(onSnooze).not.toHaveBeenCalled();
  });

  it("renders disabled buttons and refuses to emit when disabled", () => {
    const onSnooze = vi.fn();
    render(<SnoozePicker onSnooze={onSnooze} disabled={true} />);
    const preset = screen.getByTestId("inbox-snooze-preset-one_hour");
    expect(preset).toBeDisabled();
    fireEvent.click(preset);
    expect(onSnooze).not.toHaveBeenCalled();
  });

  it("renders a cancel button only when `onCancel` is wired", () => {
    const { rerender } = render(<SnoozePicker onSnooze={() => undefined} />);
    expect(screen.queryByTestId("inbox-snooze-cancel")).toBeNull();
    const onCancel = vi.fn();
    rerender(<SnoozePicker onSnooze={() => undefined} onCancel={onCancel} />);
    fireEvent.click(screen.getByTestId("inbox-snooze-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
