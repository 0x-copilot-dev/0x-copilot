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

  // PRD-09 D5 — a slug WITHOUT a registry sublabel (e.g. `home`) renders no
  // subtitle when there's no leaf; a slug WITH one (e.g. `chats`) falls back to
  // the registry sublabel.
  it("renders no subtitle when the leaf is undefined and the slug has no sublabel", () => {
    render(<Topbar activeDestination="home" />);
    expect(screen.queryByTestId("topbar-subtitle")).toBeNull();
  });

  it("falls back to the registry sublabel for a no-leaf slug that has one (D5)", () => {
    render(<Topbar activeDestination="chats" />);
    expect(screen.getByTestId("topbar-subtitle")).toHaveTextContent(
      "every conversation with the agent",
    );
  });

  it("falls back to the registry sublabel for an empty-string leaf", () => {
    render(<Topbar activeDestination="chats" leaf="" />);
    expect(screen.getByTestId("topbar-subtitle")).toHaveTextContent(
      "every conversation with the agent",
    );
  });

  it("falls back to the registry sublabel for an em-dash leaf", () => {
    render(<Topbar activeDestination="chats" leaf="—" />);
    expect(screen.getByTestId("topbar-subtitle")).toHaveTextContent(
      "every conversation with the agent",
    );
  });

  // PRD-09 DoD #11 — the Chats subtitle is sourced from destinations.ts.
  it("reads exactly 'every conversation with the agent' for chats, from the registry (DoD #11)", () => {
    render(<Topbar activeDestination="chats" />);
    expect(screen.getByTestId("topbar-subtitle").textContent).toBe(
      "every conversation with the agent",
    );
  });

  // PRD-09 DoD #12 — design values pinned numerically against the inline styles.
  it("pins the design box + type values (baseline row, gaps, padding, subtitle tone) (DoD #12)", () => {
    render(<Topbar activeDestination="chats" />);
    const group = screen.getByTestId("topbar-title-group");
    expect(group.style.alignItems).toBe("baseline");
    expect(group.style.gap).toBe("9px");

    const header = screen.getByRole("banner");
    expect(header).toHaveAttribute("data-component", "topbar");
    expect(header.style.gap).toBe("12px");
    expect(header.style.padding).toBe("0px 18px");

    expect(screen.getByTestId("topbar-subtitle").style.color).toBe(
      "var(--color-text-subtle)",
    );
    // The title keeps the existing sm token + semibold weight (no new token).
    expect(screen.getByTestId("topbar-title").style.fontSize).toBe(
      "var(--font-size-sm)",
    );
    expect(screen.getByTestId("topbar-title").style.fontWeight).toBe(
      "var(--font-weight-semibold)",
    );
    expect(TOPBAR_HEIGHT).toBe(46);
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

  // FTUE P4 — additive `walletChip` slot.
  it("renders the walletChip between the title group and the command trigger when supplied", () => {
    render(
      <Topbar
        activeDestination="chats"
        walletChip={<span data-testid="wc">0x7f3C…a92C</span>}
      />,
    );
    const slot = screen.getByTestId("topbar-wallet-chip");
    expect(slot).toBeInTheDocument();
    expect(screen.getByTestId("wc")).toHaveTextContent("0x7f3C…a92C");

    // DOM order: title group → wallet chip → command trigger.
    const titleGroup = screen.getByTestId("topbar-title-group");
    const trigger = screen.getByTestId("command-palette-trigger");
    expect(
      titleGroup.compareDocumentPosition(slot) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      slot.compareDocumentPosition(trigger) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("renders NO wallet-chip slot when walletChip is absent (layout unchanged)", () => {
    render(<Topbar activeDestination="chats" />);
    expect(screen.queryByTestId("topbar-wallet-chip")).toBeNull();
    // The row still carries exactly the pre-slot structure: the sizing
    // <style>, the title group, and the command trigger — no extra node.
    const bar = screen.getByRole("banner");
    expect(bar.children).toHaveLength(3);
    expect(screen.getByTestId("topbar-title-group")).toBeInTheDocument();
    expect(screen.getByTestId("command-palette-trigger")).toBeInTheDocument();
  });

  it("adds exactly one node (the chip wrapper) when walletChip is supplied", () => {
    const { rerender } = render(<Topbar activeDestination="chats" />);
    expect(screen.getByRole("banner").children).toHaveLength(3);
    rerender(
      <Topbar
        activeDestination="chats"
        walletChip={<span data-testid="wc">chip</span>}
      />,
    );
    expect(screen.getByRole("banner").children).toHaveLength(4);
  });
});
