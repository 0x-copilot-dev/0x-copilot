// Row — the design `.lrow` list row (PRD-G FR-G.1).

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Icon } from "../../icons/Icon";

import { resolveDesignToken } from "./resolveDesignToken.testutil";
import { Row } from "./Row";

describe("<Row>", () => {
  it("renders the leading icon slot, title, sub, chip, and meta", () => {
    render(
      <Row
        icon={<Icon name="clock" />}
        title="Draft reply"
        chip={<span data-testid="chip">Done</span>}
        sub="preview · gpt-4o"
        meta="2h ago"
      />,
    );
    const row = screen.getByTestId("row");
    expect(within(row).getByTestId("row-icon")).toBeInTheDocument();
    expect(within(row).getByTestId("row-title")).toHaveTextContent(
      "Draft reply",
    );
    expect(within(row).getByTestId("row-sub")).toHaveTextContent(
      "preview · gpt-4o",
    );
    expect(within(row).getByTestId("row-chip")).toHaveTextContent("Done");
    expect(within(row).getByTestId("row-meta")).toHaveTextContent("2h ago");
  });

  it("uses 12.5px (xs) title + 11px (2xs) subtle sub, body font (not mono)", () => {
    render(<Row title="T" sub="s" />);
    expect(screen.getByTestId("row-title").style.fontSize).toBe(
      "var(--font-size-xs)",
    );
    const sub = screen.getByTestId("row-sub");
    expect(sub.style.fontSize).toBe("var(--font-size-2xs)");
    expect(sub.style.color).toBe("var(--color-text-subtle)");
    // sub-line is BODY font — it must NOT carry the mono family.
    expect(sub.style.fontFamily).toBe("");
  });

  it("renders meta in the mono font", () => {
    render(<Row title="T" meta="2h" />);
    expect(screen.getByTestId("row-meta").style.fontFamily).toBe(
      "var(--font-mono)",
    );
  });

  it("omits optional slots when not provided (title-only row)", () => {
    render(<Row title="Only title" />);
    expect(screen.queryByTestId("row-icon")).toBeNull();
    expect(screen.queryByTestId("row-sub")).toBeNull();
    expect(screen.queryByTestId("row-chip")).toBeNull();
    expect(screen.queryByTestId("row-meta")).toBeNull();
  });

  it("as a button: fires onActivate on click and on Enter/Space", () => {
    const onActivate = vi.fn();
    render(<Row title="Open me" onActivate={onActivate} ariaLabel="Open me" />);
    const row = screen.getByTestId("row");
    expect(row).toHaveAttribute("role", "button");
    expect(row).toHaveAttribute("tabindex", "0");
    expect(row).toHaveAttribute("aria-label", "Open me");

    fireEvent.click(row);
    expect(onActivate).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(row, { key: "Enter" });
    expect(onActivate).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(row, { key: " " });
    expect(onActivate).toHaveBeenCalledTimes(3);
  });

  it("does not activate on an unrelated key", () => {
    const onActivate = vi.fn();
    render(<Row title="X" onActivate={onActivate} />);
    fireEvent.keyDown(screen.getByTestId("row"), { key: "a" });
    expect(onActivate).not.toHaveBeenCalled();
  });

  it("inert mode has no role/tabindex/handlers", () => {
    render(<Row title="Static" />);
    const row = screen.getByTestId("row");
    expect(row).not.toHaveAttribute("role");
    expect(row).not.toHaveAttribute("tabindex");
  });

  // ── PRD-08 D4 — trailing slot ────────────────────────────────────────────
  it("renders `trailing` content, and reserves an always-16px slot even when empty", () => {
    const { rerender } = render(
      <Row title="Navigable" trailing={<Icon name="chevronRight" />} />,
    );
    const filled = screen.getByTestId("row-trailing");
    expect(filled.querySelector("svg")).not.toBeNull();
    // The slot is present + 16px wide.
    expect(getComputedStyle(filled).width).toBe("16px");

    // No `trailing` → slot still rendered (reserved), but empty. This is the
    // design's `<span style={{width:16}}/>` on non-navigable rows; without the
    // reservation the meta column would rag on the rows that have no chevron.
    rerender(<Row title="Inert" />);
    const empty = screen.getByTestId("row-trailing");
    expect(empty).toBeInTheDocument();
    expect(empty).toBeEmptyDOMElement();
    expect(getComputedStyle(empty).width).toBe("16px");
  });

  // ── PRD-08 D6 — `.ui-list-row` recipe ────────────────────────────────────
  it("carries the `ui-list-row` className (hover/focus + 15px-glyph recipe hook)", () => {
    render(<Row title="T" />);
    expect(screen.getByTestId("row").className).toContain("ui-list-row");
  });

  it("merges a caller className with `ui-list-row`", () => {
    render(<Row title="T" className="mine" />);
    const cls = screen.getByTestId("row").className;
    expect(cls).toContain("ui-list-row");
    expect(cls).toContain("mine");
  });

  it("moves cursor out of the inline style object (recipe owns it)", () => {
    render(<Row title="T" onActivate={() => undefined} ariaLabel="x" />);
    // `cursor: pointer` now comes from `.ui-list-row[role="button"]`, not the
    // inline style — the inline style no longer sets cursor.
    expect(screen.getByTestId("row").style.cursor).toBe("");
  });

  // ── PRD-08 D5 — icon tile surface + tone reaches the tile ─────────────────
  it("gives the icon tile a `--color-surface-elevated` surface with a 7px radius", () => {
    render(<Row title="T" icon={<Icon name="clock" />} />);
    const slot = screen.getByTestId("row-icon");
    expect(slot.style.background).toBe("var(--color-surface-elevated)");
    expect(slot.style.borderRadius).toBe("7px");
  });

  it("iconTone='success' tints the TILE itself (not a descendant)", () => {
    render(<Row title="T" icon={<Icon name="clock" />} iconTone="success" />);
    const slot = screen.getByTestId("row-icon");
    // The tone lands on the slot's own color, so it reaches the tile AND the
    // glyph — the old inner coloured <span> never reached the tile.
    expect(slot.style.color).toBe("var(--color-success)");
  });

  it("iconTone defaults to muted on the tile", () => {
    render(<Row title="T" icon={<Icon name="clock" />} />);
    expect(screen.getByTestId("row-icon").style.color).toBe(
      "var(--color-text-muted)",
    );
  });

  // ── PRD-09 D2 — overflow slot (persistent, click-isolated) ───────────────
  it("renders the overflow trigger without any pointer interaction (persistent, not hover-revealed)", () => {
    render(
      <Row
        title="Chat"
        overflow={
          <button type="button" data-testid="ov-trigger">
            ⋯
          </button>
        }
      />,
    );
    // In the document at initial render — no hover/focus needed (D2).
    expect(screen.getByTestId("ov-trigger")).toBeInTheDocument();
    expect(screen.getByTestId("row-overflow")).toBeInTheDocument();
  });

  it("clicking the overflow button or a role=menuitem inside it does NOT invoke onActivate", () => {
    const onActivate = vi.fn();
    render(
      <Row
        title="Chat"
        onActivate={onActivate}
        ariaLabel="Open"
        overflow={
          <div>
            <button type="button" data-testid="ov-trigger">
              ⋯
            </button>
            <button type="button" role="menuitem" data-testid="ov-item">
              Pin
            </button>
          </div>
        }
      />,
    );
    fireEvent.click(screen.getByTestId("ov-trigger"));
    fireEvent.click(screen.getByTestId("ov-item"));
    expect(onActivate).not.toHaveBeenCalled();
    // The row itself still activates on a direct click.
    fireEvent.click(screen.getByTestId("row"));
    expect(onActivate).toHaveBeenCalledTimes(1);
  });

  // ── PRD-11 D1 — subFont + iconSize ───────────────────────────────────────
  it("subFont='mono' puts the sub-line in the mono face (connectors keep .lrow__sub mono)", () => {
    render(
      <Row title="Safe{Wallet}" sub="3-of-5 multisig · Base" subFont="mono" />,
    );
    expect(screen.getByTestId("row-sub").style.fontFamily).toBe(
      "var(--font-mono)",
    );
  });

  it("subFont defaults to body (no mono family) — Activity/Chats behaviour", () => {
    render(<Row title="T" sub="s" />);
    expect(screen.getByTestId("row-sub").style.fontFamily).toBe("");
  });

  it("iconSize=30 sizes the tile slot at 30px (the .lrow__logo connector tile)", () => {
    render(<Row title="T" icon={<Icon name="clock" />} iconSize={30} />);
    const slot = screen.getByTestId("row-icon");
    expect(getComputedStyle(slot).width).toBe("30px");
    expect(getComputedStyle(slot).height).toBe("30px");
  });

  it("iconSize defaults to 28px (.lrow__ic)", () => {
    render(<Row title="T" icon={<Icon name="clock" />} />);
    const slot = screen.getByTestId("row-icon");
    expect(getComputedStyle(slot).width).toBe("28px");
  });

  // ── PRD-08 D9 — title weight + row padding ───────────────────────────────
  it("uses the medium (500) title weight and 11px/14px row padding (DoD 23)", () => {
    render(<Row title="T" />);
    const title = screen.getByTestId("row-title");
    // (1) The primitive applies the MEDIUM token, not the old semibold.
    expect(title.style.fontWeight).toBe("var(--font-weight-medium)");
    // (2) The title's COMPUTED font-weight is "500". jsdom's getComputedStyle
    // does not substitute var(), so we resolve the token through the same
    // design-system SoT the browser reads (`--font-weight-medium: 500`,
    // styles.css:99). This pins the resolved number the DoD names — and fails
    // if the token is ever redefined off 500 — matching `.lrow__name
    // { font-weight: 500 }` (copilot.css:1637). Real-Chromium confirmation of
    // the same 500 is DoD 20 (`row.live.name`).
    expect(resolveDesignToken(title.style.fontWeight)).toBe("500");
    // Row padding is a literal inline value, so getComputedStyle resolves it.
    expect(getComputedStyle(screen.getByTestId("row")).padding).toBe(
      "11px 14px",
    );
  });
});
