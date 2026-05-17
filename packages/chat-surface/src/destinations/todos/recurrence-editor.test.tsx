import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  buildRruleSpec,
  parseRruleSpec,
  previewRecurrence,
  RecurrenceEditor,
  type TodoRecurrence,
} from "./recurrence-editor";

const PINNED_TODAY = new Date("2026-05-20T12:00:00Z");

describe("buildRruleSpec", () => {
  it("emits FREQ-only for the default daily/interval=1", () => {
    expect(buildRruleSpec({ freq: "DAILY", interval: 1, byday: [] })).toBe(
      "FREQ=DAILY",
    );
  });

  it("emits INTERVAL only when >1", () => {
    expect(buildRruleSpec({ freq: "DAILY", interval: 3, byday: [] })).toBe(
      "FREQ=DAILY;INTERVAL=3",
    );
  });

  it("emits BYDAY only on WEEKLY and in canonical order", () => {
    expect(
      buildRruleSpec({
        freq: "WEEKLY",
        interval: 1,
        byday: ["FR", "MO", "WE"],
      }),
    ).toBe("FREQ=WEEKLY;BYDAY=MO,WE,FR");
  });

  it("ignores BYDAY on non-weekly FREQs", () => {
    expect(
      buildRruleSpec({
        freq: "MONTHLY",
        interval: 1,
        byday: ["MO"],
      }),
    ).toBe("FREQ=MONTHLY");
  });
});

describe("parseRruleSpec", () => {
  it("round-trips through buildRruleSpec", () => {
    const spec = buildRruleSpec({
      freq: "WEEKLY",
      interval: 2,
      byday: ["MO", "WE", "FR"],
    });
    expect(parseRruleSpec(spec)).toEqual({
      freq: "WEEKLY",
      interval: 2,
      byday: ["MO", "WE", "FR"],
    });
  });

  it("returns null for unknown FREQ", () => {
    expect(parseRruleSpec("FREQ=YEARLY")).toBeNull();
  });

  it("returns null for unknown BYDAY token", () => {
    expect(parseRruleSpec("FREQ=WEEKLY;BYDAY=XX")).toBeNull();
  });

  it("returns null when FREQ is missing", () => {
    expect(parseRruleSpec("INTERVAL=3")).toBeNull();
  });
});

describe("previewRecurrence", () => {
  it("formats every weekday", () => {
    expect(
      previewRecurrence(
        { rule: "every_weekday", spec: "every_weekday" },
        PINNED_TODAY,
      ),
    ).toBe("Repeats every weekday starting 2026-05-20");
  });

  it("formats every_N_days", () => {
    expect(
      previewRecurrence(
        { rule: "every_N_days", spec: "every_N_days:3" },
        PINNED_TODAY,
      ),
    ).toBe("Repeats every 3 days starting 2026-05-20");
  });

  it("formats rrule WEEKLY BYDAY=MO,WE,FR per the PRD example", () => {
    expect(
      previewRecurrence(
        { rule: "rrule", spec: "FREQ=WEEKLY;BYDAY=MO,WE,FR" },
        PINNED_TODAY,
      ),
    ).toBe("Repeats every Mon, Wed, and Fri starting 2026-05-20");
  });

  it("formats rrule MONTHLY INTERVAL=2", () => {
    expect(
      previewRecurrence(
        { rule: "rrule", spec: "FREQ=MONTHLY;INTERVAL=2" },
        PINNED_TODAY,
      ),
    ).toBe("Repeats every 2 months starting 2026-05-20");
  });
});

describe("<RecurrenceEditor>", () => {
  it("renders the disabled shell when value is null and exposes shortcuts", () => {
    const onChange = vi.fn();
    render(
      <RecurrenceEditor
        value={null}
        onChange={onChange}
        today={PINNED_TODAY}
      />,
    );
    const root = screen.getByTestId("recurrence-editor");
    expect(root).toHaveAttribute("data-state", "disabled");
    expect(
      screen.getByTestId("recurrence-shortcut-every-day"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("recurrence-shortcut-every-weekday"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("recurrence-shortcut-every-monday"),
    ).toBeInTheDocument();
  });

  it("clicking 'every day' emits every_N_days:1", () => {
    const onChange = vi.fn();
    render(
      <RecurrenceEditor
        value={null}
        onChange={onChange}
        today={PINNED_TODAY}
      />,
    );
    fireEvent.click(screen.getByTestId("recurrence-shortcut-every-day"));
    expect(onChange).toHaveBeenCalledWith({
      rule: "every_N_days",
      spec: "every_N_days:1",
    });
  });

  it("clicking 'every weekday' emits every_weekday", () => {
    const onChange = vi.fn();
    render(
      <RecurrenceEditor
        value={null}
        onChange={onChange}
        today={PINNED_TODAY}
      />,
    );
    fireEvent.click(screen.getByTestId("recurrence-shortcut-every-weekday"));
    expect(onChange).toHaveBeenCalledWith({
      rule: "every_weekday",
      spec: "every_weekday",
    });
  });

  it("clicking 'every Monday' emits FREQ=WEEKLY;BYDAY=MO", () => {
    const onChange = vi.fn();
    render(
      <RecurrenceEditor
        value={null}
        onChange={onChange}
        today={PINNED_TODAY}
      />,
    );
    fireEvent.click(screen.getByTestId("recurrence-shortcut-every-monday"));
    expect(onChange).toHaveBeenCalledWith({
      rule: "rrule",
      spec: "FREQ=WEEKLY;BYDAY=MO",
    });
  });

  it("clicking 'every 3 days' shortcut emits every_N_days:3 when already enabled", () => {
    const onChange = vi.fn();
    const enabled: TodoRecurrence = {
      rule: "rrule",
      spec: "FREQ=DAILY",
    };
    render(
      <RecurrenceEditor
        value={enabled}
        onChange={onChange}
        today={PINNED_TODAY}
      />,
    );
    fireEvent.click(screen.getByTestId("recurrence-shortcut-every-n-days"));
    expect(onChange).toHaveBeenCalledWith({
      rule: "every_N_days",
      spec: "every_N_days:3",
    });
  });

  it("BYDAY checkboxes only appear in weekly mode and toggle in order", () => {
    const onChange = vi.fn();
    const enabled: TodoRecurrence = {
      rule: "rrule",
      spec: "FREQ=WEEKLY",
    };
    render(
      <RecurrenceEditor
        value={enabled}
        onChange={onChange}
        today={PINNED_TODAY}
      />,
    );
    // All seven BYDAY chips visible.
    expect(screen.getByTestId("recurrence-byday-MO")).toBeInTheDocument();
    expect(screen.getByTestId("recurrence-byday-SU")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("recurrence-byday-WE"));
    expect(onChange).toHaveBeenLastCalledWith({
      rule: "rrule",
      spec: "FREQ=WEEKLY;BYDAY=WE",
    });
  });

  it("INTERVAL clamps to >=1 floor", () => {
    const onChange = vi.fn();
    const enabled: TodoRecurrence = {
      rule: "rrule",
      spec: "FREQ=DAILY",
    };
    render(
      <RecurrenceEditor
        value={enabled}
        onChange={onChange}
        today={PINNED_TODAY}
      />,
    );
    const intervalInput = screen.getByTestId(
      "recurrence-interval",
    ) as HTMLInputElement;
    fireEvent.change(intervalInput, { target: { value: "0" } });
    expect(onChange).toHaveBeenLastCalledWith({
      rule: "rrule",
      spec: "FREQ=DAILY",
    });

    fireEvent.change(intervalInput, { target: { value: "5" } });
    expect(onChange).toHaveBeenLastCalledWith({
      rule: "rrule",
      spec: "FREQ=DAILY;INTERVAL=5",
    });
  });

  it("switching FREQ to MONTHLY drops BYDAY", () => {
    const onChange = vi.fn();
    const enabled: TodoRecurrence = {
      rule: "rrule",
      spec: "FREQ=WEEKLY;BYDAY=MO,WE",
    };
    render(
      <RecurrenceEditor
        value={enabled}
        onChange={onChange}
        today={PINNED_TODAY}
      />,
    );
    const freqSelect = screen.getByTestId(
      "recurrence-freq",
    ) as HTMLSelectElement;
    fireEvent.change(freqSelect, { target: { value: "MONTHLY" } });
    expect(onChange).toHaveBeenLastCalledWith({
      rule: "rrule",
      spec: "FREQ=MONTHLY",
    });
  });

  it("the 'off' button clears recurrence", () => {
    const onChange = vi.fn();
    const enabled: TodoRecurrence = {
      rule: "rrule",
      spec: "FREQ=WEEKLY;BYDAY=MO",
    };
    render(
      <RecurrenceEditor
        value={enabled}
        onChange={onChange}
        today={PINNED_TODAY}
      />,
    );
    fireEvent.click(screen.getByTestId("recurrence-clear"));
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("renders the live preview matching the PRD example", () => {
    const enabled: TodoRecurrence = {
      rule: "rrule",
      spec: "FREQ=WEEKLY;BYDAY=MO,WE,FR",
    };
    render(
      <RecurrenceEditor
        value={enabled}
        onChange={vi.fn()}
        today={PINNED_TODAY}
      />,
    );
    expect(screen.getByTestId("recurrence-preview")).toHaveTextContent(
      "Repeats every Mon, Wed, and Fri starting 2026-05-20",
    );
  });
});
