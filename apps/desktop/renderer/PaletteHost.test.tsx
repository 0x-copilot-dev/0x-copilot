// @vitest-environment jsdom
// PR-6.4 — the desktop PaletteHost mounts one canonical CommandPalette over the
// local static registry port and dispatches the palette's non-entity hits back
// to the host: destination navigation, Settings deep-links, and the four action
// flow launchers.
//
// PR-6.6 / minor-ui — PaletteHost is MODAL-ONLY: `open` is CONTROLLED by the host
// (`open` / `onOpenChange`) and it renders no trigger of its own. The single
// search affordance is the shell topbar's `CommandPaletteTrigger`, wired via
// `ChatShell.onOpenCommandPalette` (covered in bootstrap.test.tsx). These tests
// drive open/close through a controlled harness whose "open" button stands in for
// that shell trigger, so hit-activation close paths still exercise the state.

import { useState, type ReactElement } from "react";
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

  // Controlled harness: the host lifts `open` state (as bootstrap does). The
  // "open" button stands in for the shell topbar's ⌘K trigger.
  function Harness(): ReactElement {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button
          type="button"
          data-testid="harness-open"
          onClick={() => setOpen(true)}
        >
          open
        </button>
        <PaletteHost
          open={open}
          onOpenChange={setOpen}
          onNavigateDestination={onNavigateDestination}
          onOpenSettings={onOpenSettings}
          actions={actions}
          {...overrides}
        />
      </>
    );
  }

  const utils = render(<Harness />);
  return { onNavigateDestination, onOpenSettings, actions, ...utils };
}

function openPalette(): void {
  fireEvent.click(screen.getByTestId("harness-open"));
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

  it("renders no trigger of its own (the shell topbar owns the single one)", () => {
    setup();
    expect(screen.queryByTestId("command-palette-trigger")).toBeNull();
  });

  it("opens the palette when the host requests it (shell trigger / ⌘K)", () => {
    setup();
    expect(screen.queryByTestId("command-palette")).toBeNull();
    openPalette();
    expect(screen.queryByTestId("command-palette")).not.toBeNull();
  });

  it("routes the 'Go to Tools' hit to the connectors slug (solo relabel) and closes", () => {
    const { onNavigateDestination } = setup();
    openPalette();
    // Present in the empty-query starter list (first 8 of PALETTE_COMMANDS).
    fireEvent.click(screen.getByText("Go to Tools"));
    expect(onNavigateDestination).toHaveBeenCalledWith("connectors");
    // Activating a hit closes the palette.
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });

  it("opens Settings at the 'Appearance' section", () => {
    const { onOpenSettings } = setup();
    openPalette();
    fireEvent.click(screen.getByText("Appearance"));
    expect(onOpenSettings).toHaveBeenCalledWith("appearance");
  });

  it("opens Settings at the default section for the bare 'Open Settings' hit", async () => {
    const { onOpenSettings } = setup();
    openPalette();
    typeQuery("Open Settings");
    fireEvent.click(await screen.findByText("Open Settings"));
    expect(onOpenSettings).toHaveBeenCalledWith(undefined);
  });

  it("launches the 'Add a provider key' action flow", async () => {
    const { actions } = setup();
    openPalette();
    typeQuery("provider key");
    fireEvent.click(await screen.findByText("Add a provider key"));
    expect(actions.onAddProviderKey).toHaveBeenCalledTimes(1);
  });

  it("launches the 'Download a local model' action flow", async () => {
    const { actions } = setup();
    openPalette();
    typeQuery("local model");
    fireEvent.click(await screen.findByText("Download a local model"));
    expect(actions.onDownloadLocalModel).toHaveBeenCalledTimes(1);
  });

  it("launches the 'Connect a tool' action flow", async () => {
    const { actions } = setup();
    openPalette();
    typeQuery("Connect a tool");
    fireEvent.click(await screen.findByText("Connect a tool"));
    expect(actions.onConnectTool).toHaveBeenCalledTimes(1);
  });

  it("launches the 'New chat' action flow", async () => {
    const { actions } = setup();
    openPalette();
    typeQuery("New chat");
    fireEvent.click(await screen.findByText("New chat"));
    expect(actions.onNewChat).toHaveBeenCalledTimes(1);
  });

  it("runs the connect-tool flow from the empty-state 'Connect a tool →' hint and closes", async () => {
    const { actions } = setup();
    openPalette();
    // A query with zero registry matches shows the "No results" hint.
    typeQuery("zzz-nothing-matches");
    fireEvent.click(await screen.findByTestId("palette-connect-tool-hint"));
    expect(actions.onConnectTool).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });
});
