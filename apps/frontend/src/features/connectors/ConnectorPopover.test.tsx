import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { ConnectorPopover } from "./ConnectorPopover";
import type { ConnectorRow } from "./projectConnectors";

function row(overrides: Partial<ConnectorRow> = {}): ConnectorRow {
  return {
    server_id: "srv_notion",
    display_name: "Notion",
    state: "active",
    current_scopes: [],
    default_scopes: [],
    ...overrides,
  };
}

function renderPopover(
  props: Partial<Parameters<typeof ConnectorPopover>[0]> = {},
) {
  const triggerRef = createRef<HTMLButtonElement>();
  const onClose = vi.fn();
  const onToggle = vi.fn();
  const onConnect = vi.fn();
  const onEnableInSettings = vi.fn();
  const onManage = vi.fn();
  const utils = render(
    <>
      <button ref={triggerRef} type="button">
        trigger
      </button>
      <ConnectorPopover
        open
        onClose={onClose}
        triggerRef={triggerRef}
        rows={[]}
        onToggle={onToggle}
        onConnect={onConnect}
        onEnableInSettings={onEnableInSettings}
        onManage={onManage}
        {...props}
      />
    </>,
  );
  return {
    ...utils,
    onClose,
    onToggle,
    onConnect,
    onEnableInSettings,
    onManage,
  };
}

describe("ConnectorPopover", () => {
  it("renders the empty state when no servers are present", () => {
    renderPopover({ rows: [] });
    expect(screen.getByRole("note")).toHaveTextContent(
      /no connectors installed yet/i,
    );
  });

  it("active row toggles to paused via the PR 1.2 patch shape", () => {
    const { onToggle } = renderPopover({
      rows: [row({ state: "active", current_scopes: ["read"] })],
    });
    const button = screen.getByRole("menuitemcheckbox", {
      name: /Notion — Active/i,
    });
    expect(button).toHaveAttribute("aria-checked", "true");
    fireEvent.click(button);
    expect(onToggle).toHaveBeenCalledWith("srv_notion", null);
  });

  it("paused row resumes with the row's default scopes", () => {
    const { onToggle } = renderPopover({
      rows: [row({ state: "paused", default_scopes: ["read"] })],
    });
    const button = screen.getByRole("menuitemcheckbox", {
      name: /Notion — Paused/i,
    });
    expect(button).toHaveAttribute("aria-checked", "false");
    fireEvent.click(button);
    expect(onToggle).toHaveBeenCalledWith("srv_notion", ["read"]);
  });

  it("disconnected row triggers Connect with the server id", () => {
    const { onConnect } = renderPopover({
      rows: [row({ state: "disconnected" })],
    });
    fireEvent.click(
      screen.getByRole("menuitem", { name: /Notion — Not connected/i }),
    );
    expect(onConnect).toHaveBeenCalledWith("srv_notion");
  });

  it("workspace_off row routes to Settings → Connectors", () => {
    const { onEnableInSettings } = renderPopover({
      rows: [row({ state: "workspace_off" })],
    });
    fireEvent.click(
      screen.getByRole("menuitem", { name: /Notion — Workspace off/i }),
    );
    expect(onEnableInSettings).toHaveBeenCalledWith("srv_notion");
  });

  it("Manage button routes through onManage and closes the popover", () => {
    const { onManage, onClose } = renderPopover({
      rows: [row()],
    });
    fireEvent.click(
      screen.getByRole("button", { name: /manage in settings/i }),
    );
    expect(onManage).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("read-only mode disables every row", () => {
    const { onToggle } = renderPopover({
      readOnly: true,
      rows: [row()],
    });
    const button = screen.getByRole("menuitemcheckbox", {
      name: /Notion — Active/i,
    });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("renders an inline error when patch failed", () => {
    renderPopover({
      rows: [row()],
      error: "Could not pause Slack",
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      /could not pause slack/i,
    );
  });

  it("ArrowDown / ArrowUp move focus through enabled rows", () => {
    renderPopover({
      rows: [
        row({ server_id: "a", display_name: "A" }),
        row({ server_id: "b", display_name: "B", state: "paused" }),
        row({ server_id: "c", display_name: "C", state: "disconnected" }),
      ],
    });
    const a = screen.getByRole("menuitemcheckbox", { name: /A — Active/i });
    const list = a.parentElement!;
    a.focus();
    fireEvent.keyDown(list, { key: "ArrowDown" });
    expect(document.activeElement).toBe(
      screen.getByRole("menuitemcheckbox", { name: /B — Paused/i }),
    );
    fireEvent.keyDown(list, { key: "ArrowDown" });
    expect(document.activeElement).toBe(
      screen.getByRole("menuitem", { name: /C — Not connected/i }),
    );
    fireEvent.keyDown(list, { key: "ArrowDown" });
    expect(document.activeElement).toBe(a); // wraps
    fireEvent.keyDown(list, { key: "ArrowUp" });
    expect(document.activeElement).toBe(
      screen.getByRole("menuitem", { name: /C — Not connected/i }),
    );
  });

  it("Home and End jump to first / last enabled row", () => {
    renderPopover({
      rows: [
        row({ server_id: "a", display_name: "A" }),
        row({ server_id: "b", display_name: "B" }),
        row({ server_id: "c", display_name: "C" }),
      ],
    });
    const list = screen.getByRole("menuitemcheckbox", {
      name: /A — Active/i,
    }).parentElement!;
    fireEvent.keyDown(list, { key: "End" });
    expect(document.activeElement).toBe(
      screen.getByRole("menuitemcheckbox", { name: /C — Active/i }),
    );
    fireEvent.keyDown(list, { key: "Home" });
    expect(document.activeElement).toBe(
      screen.getByRole("menuitemcheckbox", { name: /A — Active/i }),
    );
  });

  it("Space activates the focused row", () => {
    const { onToggle } = renderPopover({ rows: [row()] });
    const button = screen.getByRole("menuitemcheckbox", {
      name: /Notion — Active/i,
    });
    button.focus();
    fireEvent.keyDown(button, { key: " " });
    expect(onToggle).toHaveBeenCalledWith("srv_notion", null);
  });

  it("does not render when closed", () => {
    renderPopover({ open: false });
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
