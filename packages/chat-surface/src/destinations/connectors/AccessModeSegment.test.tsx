// AccessModeSegment — radiogroup semantics, click + keyboard selection.

import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConnectorAccessMode } from "@0x-copilot/api-types";

import { AccessModeSegment } from "./AccessModeSegment";

// Controlled harness — mirrors real host usage (value follows onChange) so the
// keyboard-driven selection reflection can be asserted end-to-end.
function Harness({
  initial,
}: {
  readonly initial: ConnectorAccessMode;
}): React.ReactElement {
  const [value, setValue] = useState<ConnectorAccessMode>(initial);
  return (
    <AccessModeSegment
      value={value}
      onChange={setValue}
      ariaLabel="Access mode for Gmail"
    />
  );
}

describe("AccessModeSegment", () => {
  it("renders exactly 3 radios named Read / Read & act / Off in order, exactly one checked (PRD-06 DoD 16)", () => {
    // Matches tools/design-parity/design-kit/app-v3/copilot-app.jsx:139-141
    // [["read","Read"],["act","Read & act"],["off","Off"]].
    render(
      <AccessModeSegment value="read_act" onChange={vi.fn()} ariaLabel="x" />,
    );
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect(radios.map((r) => r.getAttribute("aria-label"))).toEqual([
      "Read",
      "Read & act",
      "Off",
    ]);
    const checked = radios.filter(
      (r) => r.getAttribute("aria-checked") === "true",
    );
    expect(checked).toHaveLength(1);
    expect(checked[0]).toHaveAttribute("aria-label", "Read & act");
  });

  it("renders a radiogroup with the three modes and checks the current one", () => {
    render(
      <AccessModeSegment
        value="read"
        onChange={vi.fn()}
        ariaLabel="Access mode for Gmail"
      />,
    );
    const group = screen.getByRole("radiogroup", {
      name: "Access mode for Gmail",
    });
    expect(group).toBeInTheDocument();

    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect(screen.getByRole("radio", { name: "Read" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: "Read & act" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByRole("radio", { name: "Off" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("uses roving tabindex — only the checked radio is tabbable", () => {
    render(
      <AccessModeSegment value="read_act" onChange={vi.fn()} ariaLabel="x" />,
    );
    expect(screen.getByRole("radio", { name: "Read & act" })).toHaveAttribute(
      "tabindex",
      "0",
    );
    expect(screen.getByRole("radio", { name: "Read" })).toHaveAttribute(
      "tabindex",
      "-1",
    );
  });

  it("fires onChange with the picked mode on click", () => {
    const onChange = vi.fn();
    render(
      <AccessModeSegment value="read" onChange={onChange} ariaLabel="x" />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Read & act" }));
    expect(onChange).toHaveBeenCalledWith("read_act");
  });

  it("does not re-fire onChange when the current mode is clicked", () => {
    const onChange = vi.fn();
    render(
      <AccessModeSegment value="read" onChange={onChange} ariaLabel="x" />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Read" }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("ArrowRight/ArrowDown advance to the next mode (wrapping)", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <AccessModeSegment value="read" onChange={onChange} ariaLabel="x" />,
    );
    fireEvent.keyDown(screen.getByRole("radio", { name: "Read" }), {
      key: "ArrowRight",
    });
    expect(onChange).toHaveBeenLastCalledWith("read_act");

    // From the last mode it wraps back to the first.
    rerender(
      <AccessModeSegment value="off" onChange={onChange} ariaLabel="x" />,
    );
    fireEvent.keyDown(screen.getByRole("radio", { name: "Off" }), {
      key: "ArrowDown",
    });
    expect(onChange).toHaveBeenLastCalledWith("read");
  });

  it("ArrowLeft/ArrowUp move to the previous mode (wrapping)", () => {
    const onChange = vi.fn();
    render(
      <AccessModeSegment value="read" onChange={onChange} ariaLabel="x" />,
    );
    fireEvent.keyDown(screen.getByRole("radio", { name: "Read" }), {
      key: "ArrowLeft",
    });
    expect(onChange).toHaveBeenLastCalledWith("off");
  });

  it("Home/End jump to the first/last mode", () => {
    const onChange = vi.fn();
    render(
      <AccessModeSegment value="read_act" onChange={onChange} ariaLabel="x" />,
    );
    fireEvent.keyDown(screen.getByRole("radio", { name: "Read & act" }), {
      key: "Home",
    });
    expect(onChange).toHaveBeenLastCalledWith("read");
    fireEvent.keyDown(screen.getByRole("radio", { name: "Read & act" }), {
      key: "End",
    });
    expect(onChange).toHaveBeenLastCalledWith("off");
  });

  it("reflects the newly selected mode when controlled", () => {
    render(<Harness initial="read" />);
    fireEvent.click(screen.getByRole("radio", { name: "Off" }));
    expect(screen.getByRole("radio", { name: "Off" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: "Read" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  // ── PRD-11 D5 — neutral selection (no ring, no weight reflow) ─────────────
  it("selection is neutral: the selected and unselected options carry the SAME font-weight (DoD 6)", () => {
    render(
      <AccessModeSegment value="read_act" onChange={vi.fn()} ariaLabel="x" />,
    );
    // Design (copilot.css:716-733): weight is a constant 500 for both states;
    // only background + colour change on selection. The live control must not
    // reflow type weight as selection moves.
    expect(
      getComputedStyle(screen.getByTestId("access-mode-option-read_act"))
        .fontWeight,
    ).toBe(
      getComputedStyle(screen.getByTestId("access-mode-option-off")).fontWeight,
    );
  });

  it("no option carries an accent boxShadow ring", () => {
    render(
      <AccessModeSegment value="read_act" onChange={vi.fn()} ariaLabel="x" />,
    );
    for (const mode of ["read", "read_act", "off"]) {
      expect(
        screen.getByTestId(`access-mode-option-${mode}`).style.boxShadow,
      ).toBe("");
    }
  });

  it("the selected item fills to --color-surface-elevated on a --color-surface group", () => {
    render(
      <AccessModeSegment value="read_act" onChange={vi.fn()} ariaLabel="x" />,
    );
    expect(
      screen.getByTestId("access-mode-option-read_act").style.background,
    ).toBe("var(--color-surface-elevated)");
    expect(screen.getByTestId("access-mode-segment").style.background).toBe(
      "var(--color-surface)",
    );
  });

  it("disabled renders every option disabled and fires nothing", () => {
    const onChange = vi.fn();
    render(
      <AccessModeSegment
        value="read"
        onChange={onChange}
        ariaLabel="x"
        disabled
      />,
    );
    const group = screen.getByRole("radiogroup");
    expect(group).toHaveAttribute("aria-disabled", "true");
    for (const radio of screen.getAllByRole("radio")) {
      expect(radio).toBeDisabled();
    }
    fireEvent.keyDown(screen.getByRole("radio", { name: "Read" }), {
      key: "ArrowRight",
    });
    expect(onChange).not.toHaveBeenCalled();
  });
});
