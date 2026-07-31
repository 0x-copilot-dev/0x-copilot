// Composer execution-mode pill (PRD-FS-10 §4.3).
//
// The load-bearing assertions are the NEGATIVE ones, and they are made by
// accessible name rather than test id: if a user can reach "Bypass" while the
// master switch is off, it exists for them regardless of what a data attribute
// says. "Not offered" has to mean not-in-the-tree.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BYPASS_BOUND_NOTE, BYPASS_BOUND_SUB, BypassPill } from "./BypassPill";
import {
  bypassSelectionForSend,
  bypassStateAfterSend,
  MANUAL_BYPASS_STATE,
} from "./filesystemBypass";

describe("BypassPill — master switch OFF", () => {
  it("renders a disabled Manual pill", () => {
    render(
      <BypassPill mode="manual" enabled={false} onChange={() => undefined} />,
    );
    const trigger = screen.getByRole("button", {
      name: /Execution mode: Manual/,
    });
    expect(trigger).toBeDisabled();
  });

  it("offers no Bypass option at all — not even after a click", () => {
    render(
      <BypassPill mode="manual" enabled={false} onChange={() => undefined} />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Execution mode: Manual/ }),
    );
    expect(screen.queryByRole("menuitemradio", { name: /Bypass/ })).toBeNull();
    expect(screen.queryByText(BYPASS_BOUND_NOTE)).toBeNull();
  });

  it("reports Manual even when a stale mode says bypass", () => {
    // Defence against a host that persisted a selection, then had the master
    // switch turned off underneath it. The pill must not display a posture the
    // deployment no longer permits.
    const onChange = vi.fn();
    render(<BypassPill mode="bypass" enabled={false} onChange={onChange} />);
    expect(
      screen.getByRole("button", { name: /Execution mode: Manual/ }),
    ).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("points at Settings rather than silently doing nothing", () => {
    render(
      <BypassPill mode="manual" enabled={false} onChange={() => undefined} />,
    );
    expect(
      screen
        .getByRole("button", { name: /Execution mode: Manual/ })
        .getAttribute("data-tooltip"),
    ).toMatch(/Settings/);
  });
});

describe("BypassPill — master switch ON", () => {
  it("opens a menu offering Manual and Bypass", () => {
    render(<BypassPill mode="manual" enabled onChange={() => undefined} />);
    fireEvent.click(
      screen.getByRole("button", { name: /Execution mode: Manual/ }),
    );
    expect(
      screen.getByRole("menuitemradio", { name: /Manual/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitemradio", { name: /Bypass/ }),
    ).toBeInTheDocument();
  });

  it("states the standing bound as a non-selectable note", () => {
    render(<BypassPill mode="bypass" enabled onChange={() => undefined} />);
    fireEvent.click(
      screen.getByRole("button", { name: /Execution mode: Bypass/ }),
    );
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent(BYPASS_BOUND_NOTE);
    expect(note).toHaveTextContent(BYPASS_BOUND_SUB);
    // A clarifier that could be clicked would read as a fourth option.
    expect(
      screen.queryByRole("menuitemradio", {
        name: new RegExp(BYPASS_BOUND_NOTE),
      }),
    ).toBeNull();
  });

  it("reports the selection and closes", () => {
    const onChange = vi.fn();
    render(<BypassPill mode="manual" enabled onChange={onChange} />);
    fireEvent.click(
      screen.getByRole("button", { name: /Execution mode: Manual/ }),
    );
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Bypass/ }));
    expect(onChange).toHaveBeenCalledWith("bypass");
    expect(screen.queryByRole("menuitemradio", { name: /Bypass/ })).toBeNull();
  });

  it("offers the scope choice only once Bypass is the mode", () => {
    const { rerender } = render(
      <BypassPill
        mode="manual"
        enabled
        onChange={() => undefined}
        scope="message"
        onScopeChange={() => undefined}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Execution mode: Manual/ }),
    );
    expect(
      screen.queryByRole("menuitemradio", { name: /This run/ }),
    ).toBeNull();

    rerender(
      <BypassPill
        mode="bypass"
        enabled
        onChange={() => undefined}
        scope="message"
        onScopeChange={() => undefined}
      />,
    );
    expect(
      screen.getByRole("menuitemradio", { name: /This run/ }),
    ).toBeInTheDocument();
  });

  it("stays inert while the composer is otherwise disabled", () => {
    render(
      <BypassPill mode="manual" enabled disabled onChange={() => undefined} />,
    );
    const trigger = screen.getByRole("button", {
      name: /Execution mode: Manual/,
    });
    expect(trigger).toBeDisabled();
    fireEvent.click(trigger);
    expect(screen.queryByRole("menuitemradio", { name: /Bypass/ })).toBeNull();
  });
});

describe("bypassSelectionForSend", () => {
  it("sends nothing while the master switch is off", () => {
    expect(
      bypassSelectionForSend(
        { mode: "bypass", scope: "run" },
        { masterEnabled: false },
      ),
    ).toBeUndefined();
  });

  it("sends nothing for the default Manual posture", () => {
    // A host that never surfaces the pill must produce the byte-identical
    // run-create body it produced before bypass existed.
    expect(
      bypassSelectionForSend(MANUAL_BYPASS_STATE, { masterEnabled: true }),
    ).toBeUndefined();
  });

  it("files the selection under the slot that names its scope", () => {
    expect(
      bypassSelectionForSend(
        { mode: "bypass", scope: "message" },
        { masterEnabled: true },
      ),
    ).toEqual({ message: "bypass" });
    expect(
      bypassSelectionForSend(
        { mode: "bypass", scope: "run" },
        { masterEnabled: true },
      ),
    ).toEqual({ run: "bypass" });
  });

  it("sends an explicit Manual at run scope", () => {
    // "This run does not bypass" is a real statement, distinct from absence,
    // and the backend distinguishes the two.
    expect(
      bypassSelectionForSend(
        { mode: "manual", scope: "run" },
        { masterEnabled: true },
      ),
    ).toEqual({ run: "manual" });
  });
});

describe("bypassStateAfterSend", () => {
  it("spends a message-scoped selection", () => {
    expect(bypassStateAfterSend({ mode: "bypass", scope: "message" })).toEqual(
      MANUAL_BYPASS_STATE,
    );
  });

  it("keeps a run-scoped selection", () => {
    const sticky = { mode: "bypass", scope: "run" } as const;
    expect(bypassStateAfterSend(sticky)).toEqual(sticky);
  });
});
