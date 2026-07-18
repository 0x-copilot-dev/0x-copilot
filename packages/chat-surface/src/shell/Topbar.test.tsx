import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Topbar, TOPBAR_HEIGHT } from "./Topbar";

describe("Topbar", () => {
  it("is 46px tall (DESIGN-SPEC §1)", () => {
    expect(TOPBAR_HEIGHT).toBe(46);
    render(<Topbar activeDestination="chats" />);
    const bar = screen.getByRole("banner");
    expect(bar).toHaveStyle({ height: "46px" });
  });

  it("resolves the title from the registry for a legacy slug", () => {
    render(<Topbar activeDestination="chats" />);
    expect(screen.getByTestId("topbar-title")).toHaveTextContent("Chats");
  });

  it("resolves a graceful title for the new run/activity slugs", () => {
    const { rerender } = render(<Topbar activeDestination="run" />);
    expect(screen.getByTestId("topbar-title")).toHaveTextContent("Run");
    rerender(<Topbar activeDestination="activity" />);
    expect(screen.getByTestId("topbar-title")).toHaveTextContent("Activity");
  });

  it("re-labels the title when the active destination changes", () => {
    const { rerender } = render(<Topbar activeDestination="home" />);
    expect(screen.getByTestId("topbar-title")).toHaveTextContent("Home");
    rerender(<Topbar activeDestination="memory" />);
    expect(screen.getByTestId("topbar-title")).toHaveTextContent("Memory");
  });

  it("keeps the legacy (web) label for relabelled slugs", () => {
    // `connectors` is "Tools" in the solo view but must stay "Connectors" in
    // the profile-agnostic topbar so the web surface is byte-identical.
    const { rerender } = render(<Topbar activeDestination="connectors" />);
    expect(screen.getByTestId("topbar-title")).toHaveTextContent("Connectors");
    rerender(<Topbar activeDestination="tools" />);
    expect(screen.getByTestId("topbar-title")).toHaveTextContent("Tools");
  });

  it("prefers an explicit title override when supplied", () => {
    // The profile-aware host (ChatShell in solo mode) can pass the relabelled
    // label so the topbar matches the rail.
    render(<Topbar activeDestination="connectors" title="Tools" />);
    expect(screen.getByTestId("topbar-title")).toHaveTextContent("Tools");
  });

  it("renders the leaf as the subtitle when one is supplied", () => {
    render(<Topbar activeDestination="chats" leaf="c-77" />);
    expect(screen.getByTestId("topbar-subtitle")).toHaveTextContent("c-77");
  });

  it("renders no subtitle when the leaf is undefined", () => {
    render(<Topbar activeDestination="chats" />);
    expect(screen.queryByTestId("topbar-subtitle")).toBeNull();
  });

  it("renders no subtitle for an empty-string leaf", () => {
    render(<Topbar activeDestination="chats" leaf="" />);
    expect(screen.queryByTestId("topbar-subtitle")).toBeNull();
  });

  it("renders no subtitle for an em-dash leaf", () => {
    render(<Topbar activeDestination="chats" leaf="—" />);
    expect(screen.queryByTestId("topbar-subtitle")).toBeNull();
  });

  it("mounts the shared command palette trigger with the ⌘K hint", () => {
    render(<Topbar activeDestination="chats" />);
    const trigger = screen.getByTestId("command-palette-trigger");
    expect(trigger).toBeInTheDocument();
    // The trigger renders the platform hotkey hint (⌘K on Apple, Ctrl+K else).
    expect(trigger.textContent ?? "").toMatch(/⌘K|Ctrl\+K/);
  });

  it("invokes onOpenCommandPalette on click WITHOUT opening a palette (Phase 2 deferred)", () => {
    const onOpen = vi.fn();
    render(<Topbar activeDestination="chats" onOpenCommandPalette={onOpen} />);
    screen.getByTestId("command-palette-trigger").click();
    expect(onOpen).toHaveBeenCalledTimes(1);
    // The palette open behaviour is Phase 6A — nothing palette-like mounts here.
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });

  it("does not throw when clicked with no handler (deferred no-op default)", () => {
    render(<Topbar activeDestination="chats" />);
    expect(() => {
      screen.getByTestId("command-palette-trigger").click();
    }).not.toThrow();
  });
});
