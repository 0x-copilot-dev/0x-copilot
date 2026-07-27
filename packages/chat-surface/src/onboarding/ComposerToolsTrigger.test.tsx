import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { FirstRunConnectorsPort } from "./ports/FirstRunConnectorsPort";
import { ComposerToolsTrigger } from "./ComposerToolsTrigger";

function port(): FirstRunConnectorsPort {
  return {
    listServers: vi.fn().mockResolvedValue([]),
    listCatalog: vi.fn().mockResolvedValue([]),
    installFromCatalog: vi.fn(),
    addCustomServer: vi.fn(),
    beginAuth: vi.fn(),
    deleteServer: vi.fn(),
  };
}

function renderTrigger() {
  const onToggleWebSearch = vi.fn();
  render(
    <ComposerToolsTrigger
      port={port()}
      webSearchEnabled
      onToggleWebSearch={onToggleWebSearch}
      activeConnectorIds={[]}
      onToggleConnector={vi.fn()}
      onConnectCatalog={vi.fn()}
      onAddCustom={vi.fn()}
    />,
  );
  return { onToggleWebSearch };
}

describe("ComposerToolsTrigger", () => {
  it("uses the Tools pill to toggle Web Search in the portaled panel", () => {
    const { onToggleWebSearch } = renderTrigger();

    const button = screen.getByTestId("first-run-tools-button");
    const anchor = button.parentElement as HTMLSpanElement;
    const rect = {
      x: 24,
      y: 400,
      left: 24,
      top: 400,
      right: 101,
      bottom: 426,
      width: 77,
      height: 26,
    };
    anchor.getBoundingClientRect = () => ({ ...rect, toJSON: () => rect });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.getByTestId("first-run-tools-button-badge"),
    ).toHaveTextContent("1");

    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByTestId("composer-tools-popover");
    expect(panel).toBeInTheDocument();
    // Focus places the composer at the canvas's left edge. Keep the panel's
    // left edge at the pill instead of moving a 318px panel off-screen left.
    expect(panel).toHaveStyle({ left: "24px" });
    expect((panel as HTMLDivElement).style.right).toBe("");

    fireEvent.click(screen.getByTestId("first-run-tools-websearch"));
    expect(onToggleWebSearch).toHaveBeenCalledWith(false);
  });

  it("closes its body portal on click-out and Escape", () => {
    renderTrigger();
    const button = screen.getByTestId("first-run-tools-button");

    fireEvent.click(button);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByTestId("composer-tools-popover")).toBeNull();

    fireEvent.click(button);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("composer-tools-popover")).toBeNull();
  });
});
