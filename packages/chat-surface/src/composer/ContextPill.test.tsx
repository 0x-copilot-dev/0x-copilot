// ContextPill — what the meter states, and what it refuses to state.
//
// The negative assertions carry most of the weight here. A context meter's
// failure mode is not "renders wrong", it is "renders a confident number the
// data does not support" — a percentage against an unknown window, a zeroed
// gauge for a conversation nobody has measured, an alarm colour for a model
// that is simply missing from the pricing catalogue.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ContextPill } from "./ContextPill";
import type { ContextPillView } from "./contextPillView";

function view(over: Partial<ContextPillView> = {}): ContextPillView {
  return {
    headroomPct: 60,
    pressure: "quiet",
    modelLabel: "Claude Opus 5",
    windowTokens: 200_000,
    inputTokens: 79_240,
    cachedTokens: 21_100,
    freeTokens: 120_760,
    slices: [
      { key: "tools:linear::", segmentClass: "tools", tone: 1, pct: 11.2 },
      {
        key: "messages:tool_results::",
        segmentClass: "messages",
        tone: 1,
        pct: 10.9,
      },
    ],
    groups: [
      {
        lifecycle: "resident",
        label: "Resident",
        note: "every call",
        rows: [
          {
            key: "tools:linear::41 tools",
            label: "linear",
            detail: "41 tools",
            segmentClass: "tools",
            tone: 1,
            tokens: 22_410,
            pctOfWindow: 11.205,
            thirdParty: true,
            cacheable: true,
            approximate: false,
          },
        ],
      },
      {
        lifecycle: "per_result",
        label: "Per result",
        note: "× 24 results",
        rows: [
          {
            key: "messages:tool_results::",
            label: "tool_results",
            detail: null,
            segmentClass: "messages",
            tone: 1,
            tokens: 21_830,
            pctOfWindow: 10.915,
            thirdParty: false,
            cacheable: false,
            approximate: true,
          },
        ],
      },
    ],
    unattributedDelta: 1_240,
    undeclaredTokens: 0,
    compaction: null,
    ...over,
  };
}

function openBreakdown(): HTMLElement {
  fireEvent.click(screen.getByTestId("context-pill"));
  return screen.getByTestId("context-breakdown");
}

describe("ContextPill — the resting meter", () => {
  it("states HEADROOM, with the word, not consumption", () => {
    // "60%" alone next to a filling gauge is genuinely ambiguous about which
    // direction it runs, and `headroom_pct` is the only percentage the server
    // sanctions. Both problems are solved by naming the unit.
    render(<ContextPill view={view()} />);
    const pill = screen.getByTestId("context-pill");
    expect(pill).toHaveTextContent("60%");
    expect(pill).toHaveTextContent("free");
    expect(pill.textContent).not.toContain("40");
  });

  it("puts input, cached and free on the hover line", () => {
    // Cached earns its place: a cacheable prefix bills at roughly a tenth, so
    // omitting it makes a large-but-cached surface look like a problem.
    render(<ContextPill view={view()} />);
    expect(screen.getByTestId("context-pill")).toHaveAttribute(
      "data-tooltip",
      "79,240 in · 21,100 cached · 120,760 free",
    );
  });

  it.each([
    ["quiet", "quiet"],
    ["warm", "warm"],
    ["critical", "critical"],
  ] as const)("carries pressure %s onto the element", (pressure, expected) => {
    render(<ContextPill view={view({ pressure })} />);
    expect(screen.getByTestId("context-pill")).toHaveAttribute(
      "data-state",
      expected,
    );
  });

  describe("unknown window", () => {
    const unknown = view({
      headroomPct: null,
      windowTokens: null,
      freeTokens: null,
      slices: [],
      pressure: "quiet",
    });

    it("falls back to absolute tokens instead of inventing a percent", () => {
      render(<ContextPill view={unknown} />);
      const pill = screen.getByTestId("context-pill");
      expect(pill).toHaveTextContent("79.2k");
      expect(pill).toHaveTextContent("in");
      expect(pill.textContent).not.toContain("%");
    });

    it("omits free from the hover line rather than showing a blank", () => {
      render(<ContextPill view={unknown} />);
      expect(screen.getByTestId("context-pill")).toHaveAttribute(
        "data-tooltip",
        "79,240 in · 21,100 cached",
      );
    });

    it("draws an EMPTY gauge — not a full one and not a zero one", () => {
      const { container } = render(<ContextPill view={unknown} />);
      const gauge = container.querySelector(".atlas-ctx-gauge")!;
      expect(gauge).toHaveAttribute("data-known", "false");
      expect(gauge.querySelectorAll("i")).toHaveLength(0);
    });

    it("announces the unknown window rather than an unstated percent", () => {
      render(<ContextPill view={unknown} />);
      expect(screen.getByTestId("context-pill")).toHaveAccessibleName(
        "Context: 79,240 tokens in, window size unknown. Open breakdown.",
      );
    });
  });
});

describe("ContextPill — the breakdown", () => {
  it("groups by lifecycle and names each group's multiplier", () => {
    render(<ContextPill view={view()} />);
    const pop = openBreakdown();
    expect(pop).toHaveTextContent("Resident");
    expect(pop).toHaveTextContent("every call");
    expect(pop).toHaveTextContent("Per result");
    expect(pop).toHaveTextContent("× 24 results");
  });

  it("marks a third-party, cacheable row and an approximate one differently", () => {
    render(<ContextPill view={view()} />);
    openBreakdown();
    const linear = screen.getByTestId("context-row-linear");
    expect(within(linear).getByTitle("Third-party")).toBeInTheDocument();
    expect(within(linear).getByTitle("Cached prefix")).toBeInTheDocument();
    expect(within(linear).queryByTitle("Estimated")).toBeNull();

    // counter_source "proxy" — the ledger took a worse number over failing the
    // run. Presenting it with the same confidence as a tokenized count is the
    // thing this marker exists to prevent.
    const results = screen.getByTestId("context-row-tool_results");
    expect(within(results).getByTitle("Estimated")).toBeInTheDocument();
    expect(within(results).queryByTitle("Cached prefix")).toBeNull();
  });

  it("gives the provider delta its own row rather than absorbing it", () => {
    // The headline is the provider's authoritative count; the segments sum to
    // ours. The difference has to be visible or the rows appear not to add up.
    render(<ContextPill view={view()} />);
    const pop = openBreakdown();
    expect(pop).toHaveTextContent("provider overhead");
    expect(pop).toHaveTextContent("+1,240");
  });

  it("renders a NEGATIVE delta signed, not as an absolute value", () => {
    render(<ContextPill view={view({ unattributedDelta: -820 })} />);
    expect(openBreakdown()).toHaveTextContent("-820");
  });

  it("omits the delta row entirely when it is zero", () => {
    render(<ContextPill view={view({ unattributedDelta: 0 })} />);
    expect(openBreakdown()).not.toHaveTextContent("provider overhead");
  });

  it("shows undeclared bytes in the popover and NEVER on the pill", () => {
    // Undeclared is a first-party contract defect — our bug. It does not change
    // what the user should send, so it must not colour the control they are
    // about to press.
    render(<ContextPill view={view({ undeclaredTokens: 3_180 })} />);
    const pill = screen.getByTestId("context-pill");
    expect(pill).toHaveAttribute("data-state", "quiet");
    expect(pill.textContent).not.toContain("3,180");
    openBreakdown();
    expect(screen.getByTestId("context-undeclared")).toHaveTextContent(
      "3,180 undeclared",
    );
  });

  it("hides the undeclared notice at the expected value of zero", () => {
    render(<ContextPill view={view()} />);
    openBreakdown();
    expect(screen.queryByTestId("context-undeclared")).toBeNull();
  });

  it("reports a share under one percent as <1%, never as 0%", () => {
    const v = view();
    render(
      <ContextPill
        view={{
          ...v,
          groups: [
            {
              ...v.groups[0]!,
              rows: [{ ...v.groups[0]!.rows[0]!, pctOfWindow: 0.4 }],
            },
          ],
        }}
      />,
    );
    openBreakdown();
    expect(screen.getByTestId("context-row-linear")).toHaveTextContent("<1%");
  });

  it("leaves the share cell EMPTY when the window is unknown", () => {
    const v = view();
    render(
      <ContextPill
        view={{
          ...v,
          windowTokens: null,
          groups: [
            {
              ...v.groups[0]!,
              rows: [{ ...v.groups[0]!.rows[0]!, pctOfWindow: null }],
            },
          ],
        }}
      />,
    );
    openBreakdown();
    const row = screen.getByTestId("context-row-linear");
    expect(row).toHaveTextContent("22,410");
    expect(row.textContent).not.toContain("%");
  });

  it("renders no report link when the host supplied no navigation", () => {
    // A dead link is worse than no link; navigation is host-owned.
    render(<ContextPill view={view()} />);
    openBreakdown();
    expect(screen.queryByTestId("context-open-report")).toBeNull();
  });

  it("calls the host's report handler when it did", () => {
    const onOpenReport = vi.fn();
    render(<ContextPill view={view()} onOpenReport={onOpenReport} />);
    openBreakdown();
    fireEvent.click(screen.getByTestId("context-open-report"));
    expect(onOpenReport).toHaveBeenCalledOnce();
  });

  it("names the latest compaction in the footer", () => {
    render(
      <ContextPill
        view={view({ compaction: { before: 128_000, after: 34_000 } })}
      />,
    );
    expect(openBreakdown()).toHaveTextContent("Compacted 128k → 34k");
  });
});

// jsdom performs no layout, so a green DOM assertion says nothing about whether
// this control pushes send onto a second row in the ~300px Run rail. These
// assert the CSS CONTRACT that decides who gives way, against the real
// stylesheet — the same technique TcWriteGateRow.test.tsx uses for the header's
// clipping order, and for the same reason.
describe("ContextPill — the shrink contract", () => {
  const here =
    typeof import.meta.dirname === "string"
      ? import.meta.dirname
      : dirname(fileURLToPath(import.meta.url));

  let sheet: HTMLStyleElement | null = null;

  function renderWithRealCss(): void {
    sheet = document.createElement("style");
    sheet.textContent = readFileSync(resolve(here, "composer.css"), "utf-8");
    document.head.appendChild(sheet);
    render(<ContextPill view={view()} />);
  }

  afterEach(() => {
    sheet?.remove();
    sheet = null;
  });

  it("never gives way — the model pill is the row's sole ellipsis target", () => {
    // `.aui-composer-action-wrapper__right` is `flex: 0 1 auto` so exactly ONE
    // item can shrink, and `.atlas-model-pill__name` was built for it. A meter
    // that shrank would either ellipsize its own digits (making the number a
    // lie) or compete for the role and push send onto a second line.
    renderWithRealCss();
    const root = document.querySelector(".atlas-ctx-pill__root")!;
    // jsdom reports the longhand expansion of `flex: none`.
    expect(getComputedStyle(root).flex).toBe("0 0 auto");
  });

  it("holds the gauge at a fixed width so only the label could ever give", () => {
    renderWithRealCss();
    const gauge = document.querySelector(".atlas-ctx-gauge")!;
    const style = getComputedStyle(gauge);
    expect(style.flex).toBe("0 0 auto");
    expect(style.width).toBe("22px");
  });

  it("lets a row's LABEL ellipsize while its figures stay whole", () => {
    // A segment label is an arbitrary-length MCP server slug; the figures are
    // the reason the row exists. Cutting the number instead of the name is the
    // failure this pins.
    renderWithRealCss();
    fireEvent.click(screen.getByTestId("context-pill"));
    const row = screen.getByTestId("context-row-linear");
    const label = row.querySelector(".atlas-ctx-row__label")!;
    const tokens = row.querySelector(".atlas-ctx-row__tok")!;

    expect(getComputedStyle(label).textOverflow).toBe("ellipsis");
    expect(getComputedStyle(label).minWidth).toBe("0px");
    expect(getComputedStyle(tokens).flex).toBe("0 0 auto");
  });

  it("pins the popover to the model popover's 300px", () => {
    // They anchor to adjacent controls in the same row; a mismatch reads as a
    // mistake. `Menu` writes `min-width: <anchor width>px` inline, which no
    // class can beat — the trigger is far narrower, so the floor never lifts.
    renderWithRealCss();
    fireEvent.click(screen.getByTestId("context-pill"));
    const pop = document.querySelector(".atlas-ctx-pop")!;
    expect(getComputedStyle(pop).width).toBe("300px");
  });
});
