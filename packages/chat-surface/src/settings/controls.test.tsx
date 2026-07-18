import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  AccentSwatch,
  ProgressBar,
  SegmentedControl,
  ThemeTile,
} from "./controls";

describe("<SegmentedControl>", () => {
  const OPTIONS = [
    { value: "auto", label: "Auto" },
    { value: "quick", label: "Quick" },
    { value: "deep", label: "Deep" },
  ] as const;

  it("renders a radiogroup with a radio per option and checks the selected one", () => {
    render(
      <SegmentedControl
        options={OPTIONS}
        value="quick"
        onChange={vi.fn()}
        ariaLabel="Reasoning depth"
      />,
    );
    const group = screen.getByRole("radiogroup", { name: "Reasoning depth" });
    expect(group).toBeInTheDocument();
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect(screen.getByRole("radio", { name: "Quick" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: "Auto" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("calls onChange with the picked value", () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        options={OPTIONS}
        value="quick"
        onChange={onChange}
        ariaLabel="Reasoning depth"
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Deep" }));
    expect(onChange).toHaveBeenCalledWith("deep");
  });

  it("does not fire onChange when the already-selected option is clicked", () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        options={OPTIONS}
        value="quick"
        onChange={onChange}
        ariaLabel="Reasoning depth"
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Quick" }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("does not fire onChange for a disabled option", () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        options={[
          { value: "on", label: "On" },
          { value: "off", label: "Off", disabled: true },
        ]}
        value="on"
        onChange={onChange}
        ariaLabel="Web access"
      />,
    );
    const off = screen.getByRole("radio", { name: "Off" });
    expect(off).toBeDisabled();
    fireEvent.click(off);
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("<AccentSwatch>", () => {
  it("is a checkable radio that renders its runtime accent color", () => {
    render(
      <AccentSwatch swatch="#5fb2ec" label="Sky" selected onSelect={vi.fn()} />,
    );
    const swatch = screen.getByRole("radio", { name: "Sky" });
    expect(swatch).toHaveAttribute("aria-checked", "true");
    const dot = swatch.querySelector("span");
    expect(dot).toHaveAttribute("data-swatch", "#5fb2ec");
  });

  it("fires onSelect when clicked", () => {
    const onSelect = vi.fn();
    render(
      <AccentSwatch
        swatch="#57c785"
        label="Jade"
        selected={false}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Jade" }));
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});

describe("<ThemeTile>", () => {
  it("renders label + caption and reflects selection via aria-checked", () => {
    render(
      <ThemeTile
        label="System"
        caption="Match macOS"
        selected
        onSelect={vi.fn()}
      />,
    );
    const tile = screen.getByRole("radio", { name: /System/ });
    expect(tile).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Match macOS")).toBeInTheDocument();
  });

  it("fires onSelect when clicked", () => {
    const onSelect = vi.fn();
    render(<ThemeTile label="Dark" selected={false} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});

describe("<ProgressBar>", () => {
  it("exposes progressbar semantics with a rounded value", () => {
    render(<ProgressBar value={42.7} ariaLabel="Downloading llama3" />);
    const bar = screen.getByRole("progressbar", { name: "Downloading llama3" });
    expect(bar).toHaveAttribute("aria-valuenow", "43");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
  });

  it("clamps the value and drives the fill width", () => {
    const { rerender } = render(<ProgressBar value={150} ariaLabel="x" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "100",
    );
    expect(screen.getByTestId("progress-bar-fill")).toHaveStyle({
      width: "100%",
    });
    rerender(<ProgressBar value={-10} ariaLabel="x" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "0",
    );
    expect(screen.getByTestId("progress-bar-fill")).toHaveStyle({
      width: "0%",
    });
  });

  it("supports an ember danger tone for interrupted downloads", () => {
    render(<ProgressBar value={30} ariaLabel="x" tone="danger" />);
    expect(screen.getByTestId("progress-bar-fill")).toHaveAttribute(
      "data-tone",
      "danger",
    );
  });
});
