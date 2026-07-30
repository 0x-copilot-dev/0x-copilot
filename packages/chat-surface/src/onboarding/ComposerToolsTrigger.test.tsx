import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { McpServer } from "@0x-copilot/api-types";

import type { FirstRunConnectorsPort } from "./ports/FirstRunConnectorsPort";
import { ComposerToolsTrigger } from "./ComposerToolsTrigger";

function connectedServer(): McpServer {
  return {
    server_id: "seed:linear",
    name: "Linear",
    display_name: "Linear",
    url: "https://linear.test/mcp",
    transport: "http",
    auth_mode: "oauth2",
    auth_state: "authenticated",
    health: "healthy",
    enabled: true,
    oauth_client_configured: true,
    access_mode: "read",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function port(
  over: Partial<FirstRunConnectorsPort> = {},
): FirstRunConnectorsPort {
  return {
    listServers: vi.fn().mockResolvedValue([]),
    listCatalog: vi.fn().mockResolvedValue([]),
    installFromCatalog: vi.fn(),
    addCustomServer: vi.fn(),
    beginAuth: vi.fn(),
    deleteServer: vi.fn(),
    ...over,
  };
}

function renderTrigger(over: Partial<FirstRunConnectorsPort> = {}) {
  const onToggleWebSearch = vi.fn();
  render(
    <ComposerToolsTrigger
      port={port(over)}
      webSearchEnabled
      onToggleWebSearch={onToggleWebSearch}
      pausedConnectorIds={[]}
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

  // The pill and the panel it opens must answer from one projection. The badge
  // used to count an opt-in set that started empty, so a closed pill read "1"
  // (web search) while the panel listed a connected connector — the same
  // disagreement as the Settings-vs-composer mismatch, one level up.
  it("counts connected connectors in the badge without opening the panel", async () => {
    renderTrigger({
      listServers: vi.fn().mockResolvedValue([connectedServer()]),
    });
    const badge = screen.getByTestId("first-run-tools-button-badge");
    expect(badge).toHaveTextContent("1");
    await vi.waitFor(() => expect(badge).toHaveTextContent("2"));
    expect(screen.queryByTestId("composer-tools-popover")).toBeNull();
  });
});
