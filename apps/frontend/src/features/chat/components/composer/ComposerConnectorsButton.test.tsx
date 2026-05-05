import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ComposerConnectorsButton } from "./ComposerConnectorsButton";

describe("ComposerConnectorsButton", () => {
  it("renders no count when nothing is active", () => {
    render(
      <ComposerConnectorsButton activeCount={0} onClick={() => undefined} />,
    );
    const button = screen.getByRole("button", {
      name: /none active for this chat/i,
    });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button.textContent).not.toMatch(/\d/);
  });

  it("renders the count badge when connectors are active", () => {
    render(
      <ComposerConnectorsButton activeCount={3} onClick={() => undefined} />,
    );
    const button = screen.getByRole("button", {
      name: /3 active for this chat/i,
    });
    expect(button.textContent).toMatch(/3/);
  });

  it("invokes onClick when pressed", () => {
    const onClick = vi.fn();
    render(<ComposerConnectorsButton activeCount={1} onClick={onClick} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("is disabled in read-only mode", () => {
    const onClick = vi.fn();
    render(
      <ComposerConnectorsButton activeCount={2} onClick={onClick} disabled />,
    );
    const button = screen.getByRole("button");
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("reflects open state via aria-expanded", () => {
    render(
      <ComposerConnectorsButton
        activeCount={0}
        open
        onClick={() => undefined}
      />,
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
  });
});
