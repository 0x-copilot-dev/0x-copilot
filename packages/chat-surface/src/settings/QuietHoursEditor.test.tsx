import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { NotificationQuietHoursBlob } from "@0x-copilot/api-types";

import { QuietHoursEditor, validateQuietHoursWindow } from "./QuietHoursEditor";

const BASE: NotificationQuietHoursBlob = {
  enabled: true,
  from_local: "22:00",
  to_local: "07:00",
  tz: "America/Los_Angeles",
};

describe("validateQuietHoursWindow", () => {
  it("accepts a midnight-wrap window (start > end)", () => {
    expect(validateQuietHoursWindow("22:00", "07:00")).toBeNull();
  });

  it("accepts a regular forward window (start < end)", () => {
    expect(validateQuietHoursWindow("09:00", "17:00")).toBeNull();
  });

  it("rejects equal start and end", () => {
    expect(validateQuietHoursWindow("09:00", "09:00")).toBe(
      "Start and end must differ.",
    );
  });

  it("rejects malformed start time", () => {
    expect(validateQuietHoursWindow("bogus", "17:00")).toMatch(/Start time/);
  });

  it("rejects malformed end time", () => {
    expect(validateQuietHoursWindow("09:00", "25:99")).toMatch(/End time/);
  });
});

describe("<QuietHoursEditor>", () => {
  it("renders a fieldset with a legend (ARIA)", () => {
    render(<QuietHoursEditor value={BASE} onChange={() => undefined} />);
    // The fieldset's accessible name comes from its <legend>.
    expect(
      screen.getByRole("group", { name: "Quiet hours" }),
    ).toBeInTheDocument();
  });

  it("calls onChange when the enabled checkbox flips", () => {
    const onChange = vi.fn();
    render(<QuietHoursEditor value={BASE} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("quiet-hours-enabled"));
    expect(onChange).toHaveBeenCalledWith({ ...BASE, enabled: false });
  });

  it("accepts a midnight-wrap window with no error rendered", () => {
    render(
      <QuietHoursEditor
        value={{ ...BASE, from_local: "22:00", to_local: "06:00" }}
        onChange={() => undefined}
      />,
    );
    expect(screen.queryByTestId("quiet-hours-error")).toBeNull();
  });

  it("renders an inline error when start === end", () => {
    render(
      <QuietHoursEditor
        value={{ ...BASE, from_local: "09:00", to_local: "09:00" }}
        onChange={() => undefined}
      />,
    );
    const err = screen.getByTestId("quiet-hours-error");
    expect(err).toHaveTextContent("Start and end must differ.");
    // ARIA: the inputs reference the error via aria-describedby.
    expect(screen.getByTestId("quiet-hours-from")).toHaveAttribute(
      "aria-describedby",
      err.id,
    );
  });

  it("includes the current tz in the dropdown options", () => {
    render(
      <QuietHoursEditor
        value={{ ...BASE, tz: "Pacific/Pago_Pago" }}
        onChange={() => undefined}
      />,
    );
    const tz = screen.getByTestId("quiet-hours-tz") as HTMLSelectElement;
    const optValues = Array.from(tz.options).map((o) => o.value);
    expect(optValues).toContain("Pacific/Pago_Pago");
  });
});
