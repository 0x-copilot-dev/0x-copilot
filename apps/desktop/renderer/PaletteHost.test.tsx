// @vitest-environment jsdom
// PR-6.4 — the desktop PaletteHost mounts one canonical CommandPalette over the
// local static registry port, owns `open` (⌘K + topbar trigger), and dispatches
// the palette's non-entity hits back to the host: destination navigation,
// Settings deep-links, and the four action flow launchers. The topbar trigger is
// suppressed on Run and Settings (FR-6.7).

import { act } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PaletteHost, type PaletteHostProps } from "./PaletteHost";

// Desktop vitest runs with `globals: false`, so testing-library's automatic
// afterEach cleanup does not self-register — do it explicitly.
afterEach(() => {
  cleanup();
});

function setup(overrides: Partial<PaletteHostProps> = {}) {
  const onNavigateDestination = vi.fn();
  const onOpenSettings = vi.fn();
  const actions = {
    onNewChat: vi.fn(),
    onAddProviderKey: vi.fn(),
    onDownloadLocalModel: vi.fn(),
    onConnectTool: vi.fn(),
  };
  const props: PaletteHostProps = {
    // A non-suppressed destination so the topbar trigger renders by default.
    activeDestination: "chats",
    settingsActive: false,
    onNavigateDestination,
    onOpenSettings,
    actions,
    ...overrides,
  };
  const utils = render(<PaletteHost {...props} />);
  return { onNavigateDestination, onOpenSettings, actions, ...utils };
}

function pressCmdK(): void {
  // Mirror the useCommandPaletteHotkey tests: dispatch on document (the hook
  // listens there). The keydown flips host state, so wrap it in act().
  act(() => {
    document.dispatchEvent(
      new KeyboardEvent("keydown", { key: "k", metaKey: true }),
    );
  });
}

function openViaTrigger(): void {
  fireEvent.click(screen.getByTestId("command-palette-trigger"));
}

function typeQuery(value: string): void {
  fireEvent.change(screen.getByTestId("command-palette-input"), {
    target: { value },
  });
}

describe("<PaletteHost>", () => {
  it("mounts closed — no palette modal until it is opened", () => {
    setup();
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });

  it("opens the palette on ⌘K", () => {
    setup();
    pressCmdK();
    expect(screen.queryByTestId("command-palette")).not.toBeNull();
  });

  it("opens the palette when the topbar trigger is clicked", () => {
    setup();
    expect(screen.queryByTestId("command-palette")).toBeNull();
    openViaTrigger();
    expect(screen.queryByTestId("command-palette")).not.toBeNull();
  });

  it("suppresses the topbar trigger on the Run destination", () => {
    setup({ activeDestination: "run" });
    expect(screen.queryByTestId("command-palette-trigger")).toBeNull();
    // ⌘K still works even when the trigger is suppressed.
    pressCmdK();
    expect(screen.queryByTestId("command-palette")).not.toBeNull();
  });

  it("suppresses the topbar trigger while Settings is active", () => {
    setup({ activeDestination: "chats", settingsActive: true });
    expect(screen.queryByTestId("command-palette-trigger")).toBeNull();
  });

  it("routes the 'Go to Tools' hit to the connectors slug (solo relabel) and closes", () => {
    const { onNavigateDestination } = setup();
    openViaTrigger();
    // Present in the empty-query starter list (first 8 of PALETTE_COMMANDS).
    fireEvent.click(screen.getByText("Go to Tools"));
    expect(onNavigateDestination).toHaveBeenCalledWith("connectors");
    // Activating a hit closes the palette.
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });

  it("opens Settings at the 'Appearance' section", () => {
    const { onOpenSettings } = setup();
    openViaTrigger();
    fireEvent.click(screen.getByText("Appearance"));
    expect(onOpenSettings).toHaveBeenCalledWith("appearance");
  });

  it("opens Settings at the default section for the bare 'Open Settings' hit", async () => {
    const { onOpenSettings } = setup();
    openViaTrigger();
    typeQuery("Open Settings");
    fireEvent.click(await screen.findByText("Open Settings"));
    expect(onOpenSettings).toHaveBeenCalledWith(undefined);
  });

  it("launches the 'Add a provider key' action flow", async () => {
    const { actions } = setup();
    openViaTrigger();
    typeQuery("provider key");
    fireEvent.click(await screen.findByText("Add a provider key"));
    expect(actions.onAddProviderKey).toHaveBeenCalledTimes(1);
  });

  it("launches the 'Download a local model' action flow", async () => {
    const { actions } = setup();
    openViaTrigger();
    typeQuery("local model");
    fireEvent.click(await screen.findByText("Download a local model"));
    expect(actions.onDownloadLocalModel).toHaveBeenCalledTimes(1);
  });

  it("launches the 'Connect a tool' action flow", async () => {
    const { actions } = setup();
    openViaTrigger();
    typeQuery("Connect a tool");
    fireEvent.click(await screen.findByText("Connect a tool"));
    expect(actions.onConnectTool).toHaveBeenCalledTimes(1);
  });

  it("launches the 'New chat' action flow", async () => {
    const { actions } = setup();
    openViaTrigger();
    typeQuery("New chat");
    fireEvent.click(await screen.findByText("New chat"));
    expect(actions.onNewChat).toHaveBeenCalledTimes(1);
  });

  it("runs the connect-tool flow from the empty-state 'Connect a tool →' hint and closes", async () => {
    const { actions } = setup();
    openViaTrigger();
    // A query with zero registry matches shows the "No results" hint.
    typeQuery("zzz-nothing-matches");
    fireEvent.click(await screen.findByTestId("palette-connect-tool-hint"));
    expect(actions.onConnectTool).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });
});
