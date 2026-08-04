import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

// The wire test below drives the real archetype entry point rather than
// `NoSpecView` directly. `toolNameFromState` returning the right string proves
// nothing on its own — the defect PRD-02 shipped with was a lookup that was
// correct and unreachable, because no wire carried what it read.
import { ARCHETYPE_ADAPTERS } from "../archetypes";
import {
  BADGE_MAX_CHARS,
  FieldRow,
  GenericFieldList,
  NoSpecView,
  SurfaceHeader,
  SurfaceLinkRow,
  TOOL_NAME_MAX_CHARS,
  toolNameFromState,
} from "./primitives";

// PRD-02 — the no-spec view. The unit-level half of the contract; the
// per-archetype half (that every fallback routes here, and stays total over
// `unknown`) lives in `archetypes/noSpecTotality.test.tsx`.
//
// The copy this file used to pin — a sentence announcing that the tool's output
// had matched no spec — is deleted by the generative-UI floor PRD (§3.8 / AC17).
// It is not recited here either: AC17 is a `grep -ri` gate over `packages/`, and
// a test quoting the old string would red it as surely as the string itself.
// The component is NOT deleted: replay of a pre-floor run, a spec dropped at the
// transport allow-list, and a non-mapping payload all still land here, and all
// three are finished views rather than faults. So the assertions below moved
// from "does it apologise correctly" to "does it render the data and name its
// provenance" — the rule the PRD replaced the copy with.

describe("NoSpecView note", () => {
  it("names the tool in a code register — as provenance, not as an excuse", () => {
    render(<NoSpecView data={{ id: 1 }} tool="pagerduty.incident.read" />);
    const tool = screen.getByTestId("surface-no-spec-tool");
    expect(tool.tagName).toBe("CODE");
    expect(tool).toHaveTextContent("pagerduty.incident.read");
    expect(screen.getByTestId("surface-no-spec-note")).toHaveTextContent(
      "The payload as pagerduty.incident.read sent it",
    );
  });

  it("states both honest limits of the generic render", () => {
    render(<NoSpecView data={{ id: 1 }} />);
    const note = screen.getByTestId("surface-no-spec-note");
    expect(note).toHaveTextContent("top-level fields only");
    expect(note).toHaveTextContent("nested objects summarised");
  });

  // AC17, asserted at the one place the string could come back. A reworded
  // apology is the failure mode this guards, not just the exact old sentence:
  // anything telling a reader that a spec was looked for and not found is the
  // same lie in a different register.
  it("never tells the user anything about a spec", () => {
    for (const tool of ["pagerduty.incident.read", undefined]) {
      const view = render(<NoSpecView data={{ id: 1 }} tool={tool} />);
      const text = screen.getByTestId("surface-no-spec-note").textContent ?? "";
      expect(text).not.toMatch(/spec/i);
      expect(text).not.toMatch(/no match|not match|unmatched|could not/i);
      // Nor a promise about machinery the reader cannot see, and which — on
      // every path that still reaches this view — will not happen.
      expect(text).not.toMatch(/will be generated|next call|cached/i);
      view.unmount();
    }
  });

  it("reads as a deliberate view, never as an error or a loading state", () => {
    const { container } = render(<NoSpecView data={{ id: 1 }} />);
    // Design rule 04: "Nothing in this pane ever renders as an error."
    expect(container.textContent ?? "").not.toMatch(
      /error|failed|unable|sorry|loading|preparing|please wait/i,
    );
    // Nor announced as one: an alert/status role is the aural version of the
    // same lie.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("carries the design's quiet icon ahead of the copy", () => {
    render(<NoSpecView data={{ id: 1 }} />);
    const icon = screen
      .getByTestId("surface-no-spec-note")
      .querySelector("svg");
    expect(icon).not.toBeNull();
    // Decorative — the sentence beside it already carries the meaning.
    expect(icon).toHaveAttribute("aria-hidden", "true");
  });
});

// Requirement 5 — the tool name arrives from tool output and is untrusted.
describe("NoSpecView tool name (untrusted)", () => {
  it("never renders the tool name as a link, even when it looks like one", () => {
    const { container } = render(
      <NoSpecView data={{ id: 1 }} tool="https://evil.example.com/pwn" />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByTestId("surface-no-spec-tool")).toHaveTextContent(
      "https://evil.example.com/pwn",
    );
  });

  it("never renders a javascript: tool name as an href", () => {
    const { container } = render(
      <NoSpecView data={{ id: 1 }} tool="javascript:alert(1)" />,
    );
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(container.querySelector("a[href]")).toBeNull();
  });

  it("caps a hostile 10k-char tool name", () => {
    render(<NoSpecView data={{ id: 1 }} tool={"x".repeat(10_000)} />);
    const text = screen.getByTestId("surface-no-spec-tool").textContent ?? "";
    // The cap plus the one-character ellipsis that states the truncation.
    expect(text.length).toBe(TOOL_NAME_MAX_CHARS + 1);
    expect(text.endsWith("…")).toBe(true);
  });

  it.each([
    ["absent", undefined],
    ["null", null],
    ["empty", ""],
    ["whitespace", "   "],
    ["a number", 42],
    ["an object", { tool: "nested" }],
    ["an array", ["a"]],
    ["a boolean", false],
  ])("reads sensibly when the tool name is %s", (_label, tool) => {
    render(<NoSpecView data={{ id: 1 }} tool={tool} />);
    const note = screen.getByTestId("surface-no-spec-note");
    expect(note).toHaveTextContent(
      "The payload as the tool sent it — top-level fields only, nested objects summarised.",
    );
    // Never the JS spelling of "we had nothing" on screen.
    expect(note.textContent ?? "").not.toMatch(
      /undefined|null|NaN|\[object Object\]/,
    );
    expect(screen.queryByTestId("surface-no-spec-tool")).toBeNull();
  });
});

// Requirement 4 — the footer is a SAFETY statement, not decoration.
describe("NoSpecView read-only footer", () => {
  it("states that a generic view never carries a write action", () => {
    render(<NoSpecView data={{ id: 1 }} />);
    const footer = screen.getByTestId("surface-read-only-footer");
    expect(footer).toHaveTextContent("Read-only.");
    expect(footer).toHaveTextContent(
      "Generic views never carry a write action.",
    );
  });

  it("offers no way to act — no control anywhere on the view", () => {
    const { container } = render(
      <NoSpecView data={{ id: 1, html_url: "https://example.com/x" }} />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("input")).toBeNull();
  });

  it("closes the card — nothing renders after it", () => {
    const { container } = render(<NoSpecView data={{ id: 1 }} />);
    expect(container.lastElementChild).toBe(
      screen.getByTestId("surface-read-only-footer"),
    );
  });
});

// The card chrome, on the design system.
//
// WHAT THESE ASSERT, AND WHERE THE REAL PROOF IS. `primitives.tsx` composes
// INLINE styles, so a token reaches the browser as the literal string
// `var(--font-size-mono-9-5, 9.5px)` in the style attribute — that string IS
// the rendered declaration, and it is what these tests read. jsdom loads no
// stylesheet, so it cannot resolve the custom property; the RESOLVED pixel is
// measured in chromium against the vendored mock by
// `tools/design-parity/surfaces/surface-language`, and that report is what says
// whether the rung was the right one. These tests say only that the value goes
// through a rung at all — which is the thing that regressed, eleven renderers
// at once, when every one of these was a bare number.
describe("card chrome type registers", () => {
  const header = (): HTMLElement => {
    render(
      <SurfaceHeader kicker="Board" title="Cycle 14" subtitle="7 issues" />,
    );
    return screen.getByTestId("surface-header");
  };

  // `.sfc-k` — the archetype's name is a machine's word for the view. It
  // shipped as sans 11px, one whole register too loud.
  it("sets the kicker in the mono caps register", () => {
    const kicker = header().querySelector("div > span:first-child")!;
    const style = (kicker as HTMLElement).style;
    expect(style.fontFamily).toContain("--font-mono");
    expect(style.fontSize).toBe("var(--font-size-mono-9-5, 9.5px)");
    expect(style.letterSpacing).toBe("var(--tracking-mono-caps, 0.12em)");
    expect(kicker).toHaveStyle({ textTransform: "uppercase" });
    // `--mut2`, a rung quieter than the subtitle it sits above.
    expect(style.color).toBe("var(--color-text-subtle)");
  });

  // The dot paints no text, so it INHERITS the kicker's whole register — which
  // is why the parity harness reads its font rows off the kicker. Its own
  // colour + halo live in chat-surface's `surface-language.css`.
  it("leaves the identity dot to inherit that register, styling none of it inline", () => {
    const dot = header().querySelector(".sf-kicker__dot") as HTMLElement;
    expect(dot).not.toBeNull();
    expect(dot.getAttribute("style")).toBeNull();
  });

  it("sets the title on the item-title rung, not a heading one", () => {
    const title = header().querySelector<HTMLElement>(
      '[data-testid="surface-title"]',
    )!;
    expect(title.style.fontSize).toBe("var(--font-size-md, 14px)");
    expect(title.style.fontWeight).toBe("var(--font-weight-semibold, 600)");
    expect(title.style.letterSpacing).toBe("var(--tracking-snug, -0.01em)");
  });

  // `.sfc-s` — a step under the title in BOTH size and colour, so it reads as
  // the title's aside rather than as a second title.
  it("keeps the subtitle a rung under the title", () => {
    const subtitle = header().querySelector<HTMLElement>(
      '[data-testid="surface-subtitle"]',
    )!;
    expect(subtitle.style.fontSize).toBe("var(--font-size-12, 12px)");
    expect(subtitle.style.color).toBe("var(--color-text-muted)");
  });

  // `.sfb` — mono, outlined, no fill, and NO weight or tracking of its own:
  // the design declares neither, so pinning either would invent a difference
  // against the thing this is copied from.
  it("sets the badge in the mono chip register with nothing pinned that the design leaves open", () => {
    render(<SurfaceHeader kicker="Board" title="Cycle 14" badge="7 cards" />);
    const badge = screen.getByTestId("surface-badge");
    expect(badge.style.fontFamily).toContain("--font-mono");
    expect(badge.style.fontSize).toBe("var(--font-size-mono-10-5, 10.5px)");
    expect(badge.style.padding).toBe(
      "var(--space-2xs, 2px) var(--space-sm, 8px)",
    );
    expect(badge.style.borderRadius).toBe("var(--radius-full, 999px)");
    expect(badge.style.color).toBe("var(--color-text-muted)");
    expect(badge.style.fontWeight).toBe("");
    expect(badge.style.letterSpacing).toBe("");
  });
});

describe("SurfaceLinkRow register", () => {
  // `.sf-lnk` — a url_path is an address a machine emitted, so it belongs in
  // the identifier register with the tool name, not in 13px sans body copy.
  it("sets a resolved link in the mono register", () => {
    render(<SurfaceLinkRow label="Open" value="https://example.com/x" />);
    const link = screen.getByTestId("surface-link");
    expect(link.tagName).toBe("A");
    expect(link.style.fontFamily).toContain("--font-mono");
    expect(link.style.fontSize).toBe("var(--font-size-mono-10-5, 10.5px)");
  });

  // The refused half of the same row. Same register — a value we would not
  // link is still the same KIND of thing — and the colour is the only channel
  // that separates them, which is why the anchor keeps the accent and this
  // does not. Collapsing both to one colour would hide the refusal.
  it("keeps a refused value in that register but never in the link colour", () => {
    render(<SurfaceLinkRow label="Open" value="javascript:alert(1)" />);
    const inert = screen.getByTestId("surface-link-text");
    expect(inert.tagName).not.toBe("A");
    expect(inert.style.fontFamily).toContain("--font-mono");
    expect(inert.style.fontSize).toBe("var(--font-size-mono-10-5, 10.5px)");
    expect(inert.style.color).toBe("var(--color-text-muted)");
    expect(inert.style.color).not.toBe("var(--color-accent)");
  });
});

// The BADGE register. Before this existed, `format: "badge"` reached the client
// correctly and died there: `formatValue` returns the string unchanged and the
// row had exactly two registers, plain and numeric, so the one hint the backend
// works hardest to get right (every curated spec types `state`/`status` as a
// badge, and rung-0 inference independently types low-cardinality tokens the
// same way) painted as undifferentiated grey text.
describe("FieldRow badge register", () => {
  it("paints a badge-formatted value as a chip inside the value slot", () => {
    render(
      <FieldRow
        fieldKey="status"
        label="Status"
        value="acknowledged"
        format="badge"
      />,
    );
    const chip = screen.getByTestId("field-status-badge");
    expect(chip).toHaveTextContent("acknowledged");
    // Inside the slot, not instead of it: the slot is the grid item, and a chip
    // put there directly would blockify and stretch the column.
    expect(screen.getByTestId("field-status-value")).toContainElement(chip);
    // One selector finds every chip on a surface, whatever archetype drew it.
    expect(chip).toHaveAttribute("data-surface-format", "badge");
  });

  it("leaves a value with no format as plain text", () => {
    render(<FieldRow fieldKey="title" label="Title" value="Elevated 5xx" />);
    expect(screen.getByTestId("field-title-value")).toHaveTextContent(
      "Elevated 5xx",
    );
    expect(screen.queryByTestId("field-title-badge")).toBeNull();
    expect(
      screen.getByTestId("field-title").querySelector("[data-surface-format]"),
    ).toBeNull();
  });

  // A badge is a presentational treatment of a value the TOOL returned. Making
  // it activatable would hand tool output a control on our surface, and this
  // package sanctions exactly one interactive element (`link.url_path`).
  it("keeps the chip inert — never an anchor, never a button", () => {
    const { container } = render(
      <FieldRow
        fieldKey="status"
        label="Status"
        value="https://evil.example.com/pwn"
        format="badge"
      />,
    );
    const chip = screen.getByTestId("field-status-badge");
    expect(chip.tagName).toBe("SPAN");
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("button")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  // The chip is the design's `.sfb` / `.ui-badge` spread wholesale, so the
  // header's chip and a value's chip cannot drift. Asserted as the declarations
  // that ship rather than through `toHaveStyle`, because jsdom resolves an
  // unknown custom property to nothing.
  it("takes the design's chip register rather than inventing one", () => {
    render(
      <FieldRow fieldKey="state" label="State" value="open" format="badge" />,
    );
    const chip = screen.getByTestId("field-state-badge");
    expect(chip.style.fontFamily).toContain("--font-mono");
    expect(chip.style.fontSize).toBe("var(--font-size-mono-10-5, 10.5px)");
    expect(chip.style.borderRadius).toBe("var(--radius-full, 999px)");
    expect(chip.style.padding).toBe(
      "var(--space-2xs, 2px) var(--space-sm, 8px)",
    );
    expect(chip.style.background).toBe("transparent");
    expect(chip.style.border).toBe("1px solid var(--color-border-strong)");
  });

  // The ONE deliberate divergence from the header badge, pinned so it stays a
  // decision: a header badge is metadata about the card and sits on `--mut`,
  // which is exactly the colour the field LABEL carries. This chip is the value,
  // so it takes the rung between — quieter than the plain value beside it, still
  // louder than the label it answers.
  it("sits a rung louder than the header badge, and above the label", () => {
    render(
      <FieldRow fieldKey="state" label="State" value="open" format="badge" />,
    );
    const chip = screen.getByTestId("field-state-badge");
    expect(chip.style.color).toBe("var(--color-text-strong)");
    expect(chip.style.color).not.toBe("var(--color-text-muted)");
  });

  // Declining the chip is a PRESENTATION decision and never a truncation: a
  // chip does not wrap, so a long value inside one widens the row instead. The
  // value still renders, in full, in the ordinary register — which is the whole
  // reason the cap is safe to have.
  it("declines the chip for a value too long to hug, and shows it in full", () => {
    const long = "x".repeat(BADGE_MAX_CHARS + 1);
    render(
      <FieldRow fieldKey="note" label="Note" value={long} format="badge" />,
    );
    expect(screen.queryByTestId("field-note-badge")).toBeNull();
    expect(screen.getByTestId("field-note-value")).toHaveTextContent(long);
  });

  it("paints the chip right up to the cap", () => {
    const atCap = "x".repeat(BADGE_MAX_CHARS);
    render(
      <FieldRow fieldKey="note" label="Note" value={atCap} format="badge" />,
    );
    expect(screen.getByTestId("field-note-badge")).toHaveTextContent(atCap);
  });

  // An empty pill asserts that there is a value here and then shows none. The
  // blank cell the plain register paints says the field was absent, which is
  // what actually happened — and is the common case on real connector payloads.
  it.each([
    ["empty", ""],
    ["whitespace", "   "],
  ])("declines the chip for a %s value", (_label, value) => {
    render(
      <FieldRow fieldKey="state" label="State" value={value} format="badge" />,
    );
    expect(screen.queryByTestId("field-state-badge")).toBeNull();
  });

  // A badge is a token, never a magnitude: the tabular-figure register means
  // "read this digit-column against its neighbours", which is a claim about a
  // status vocabulary that is simply false.
  it("never lets a badge reach the numeric register", () => {
    render(<FieldRow fieldKey="code" label="Code" value="P1" format="badge" />);
    expect(screen.getByTestId("field-code-value")).not.toHaveStyle({
      fontVariantNumeric: "tabular-nums",
    });
  });

  // The register the row already had, now chosen by the same prop — so a caller
  // passing `format` gets tabular figures without also computing them.
  it("still routes a numeric format to the tabular-figure register", () => {
    render(
      <FieldRow fieldKey="amt" label="Amount" value="1,200" format="number" />,
    );
    expect(screen.getByTestId("field-amt-value")).toHaveStyle({
      fontVariantNumeric: "tabular-nums",
    });
    expect(screen.queryByTestId("field-amt-badge")).toBeNull();
  });
});

// Requirement 3 — the design's `.sfr` grid and its two value registers.
describe("GenericFieldList registers", () => {
  it("lays each row out on the design's two-column grid", () => {
    render(<GenericFieldList data={{ incident_number: "4127" }} />);
    const row = screen.getByTestId("field-incident_number");
    expect(row.style.gridTemplateColumns).toBe("172px minmax(0, 1fr)");
    expect(row).toHaveStyle({
      display: "grid",
      alignItems: "baseline",
      gap: "14px",
    });
    // `.sfr { padding: 9px var(--sf-cx) }` — the inline half is the 12px rung,
    // the block half is not on the ladder. Asserted as the declaration that
    // ships rather than through `toHaveStyle`, because jsdom resolves an
    // unknown custom property to nothing and would report `padding: 0`.
    expect(row.style.padding).toBe("9px var(--space-md, 12px)");
  });

  it("sets the label in the mono identifier register", () => {
    render(<GenericFieldList data={{ incident_number: "4127" }} />);
    const label = screen.getByTestId("field-incident_number")
      .firstElementChild as HTMLElement;
    expect(label).toHaveTextContent("Incident Number");
    expect(label).toHaveStyle({ textTransform: "uppercase" });
    expect(label.style.fontFamily).toContain("--font-mono");
    expect(label.style.fontSize).toBe("var(--font-size-mono-9-5, 9.5px)");
    expect(label.style.letterSpacing).toBe("var(--tracking-mono-caps, 0.12em)");
  });

  it("paints a numeric-looking value with tabular figures", () => {
    render(
      <GenericFieldList data={{ count: "4127", title: "Elevated 5xx" }} />,
    );
    const numeric = screen.getByTestId("field-count-value");
    expect(numeric).toHaveAttribute("data-numeric", "true");
    expect(numeric).toHaveStyle({ fontVariantNumeric: "tabular-nums" });

    const text = screen.getByTestId("field-title-value");
    expect(text).toHaveAttribute("data-numeric", "false");
    expect(text).not.toHaveStyle({ fontVariantNumeric: "tabular-nums" });
  });

  it("summarises nested objects and arrays instead of expanding them", () => {
    render(
      <GenericFieldList
        data={{
          service: { a: 1, b: 2, c: 3, d: 4, e: 5, f: 6 },
          assignments: [{ at: 1 }, { at: 2 }],
          one: [1],
        }}
      />,
    );
    expect(screen.getByTestId("field-service-value")).toHaveTextContent(
      "{ 6 fields }",
    );
    expect(screen.getByTestId("field-assignments-value")).toHaveTextContent(
      "2 items",
    );
    expect(screen.getByTestId("field-one-value")).toHaveTextContent("1 item");
  });

  it("drops the hairline under the last row, which separates nothing", () => {
    render(<GenericFieldList data={{ first: 1, last: 2 }} />);
    expect(screen.getByTestId("field-first").style.borderBottom).toContain(
      "1px solid",
    );
    expect(screen.getByTestId("field-last").style.borderBottomStyle).toBe(
      "none",
    );
  });

  // An empty result set is ORDINARY tool output — a query that matched no
  // rows — and the band it lands in must say so. Painting nothing lets the gap
  // between the note and the footer do the talking, and what a blank gap says
  // is "something went wrong", which is rule 04 broken by omission.
  it.each([
    ["an empty object", {}, "The tool returned an empty payload."],
    ["an empty array", [], "The tool returned an empty payload."],
    ["an empty string", "", "The tool returned an empty payload."],
    ["null", null, "The tool returned no payload."],
    ["undefined", undefined, "The tool returned no payload."],
  ])("states in words that %s carried nothing", (_label, data, copy) => {
    render(<GenericFieldList data={data} />);
    const body = screen.getByTestId("surface-generic-fields");
    expect(screen.getByTestId("surface-generic-empty")).toHaveTextContent(copy);
    // Said as a fact about a finished call, never as a diagnosis.
    expect(body.textContent ?? "").not.toMatch(
      /error|failed|unable|loading|preparing/i,
    );
    // And never a lone label with nothing after it, which is what `null` used
    // to paint: a "VALUE" rail and a blank column reads as a fact cut off.
    expect(screen.queryByTestId("field-value")).toBeNull();
  });

  it("leaves no version of the body visually blank", () => {
    for (const data of [{}, [], null, undefined, ""]) {
      const view = render(<GenericFieldList data={data} />);
      expect(
        (screen.getByTestId("surface-generic-fields").textContent ?? "").trim(),
      ).not.toBe("");
      view.unmount();
    }
  });

  it("keeps the cap line as the list's unbordered last child", () => {
    const data = Object.fromEntries(
      Array.from({ length: 50 }, (_, index) => [`k${index}`, index]),
    );
    render(<GenericFieldList data={data} />);
    const cap = screen.getByTestId("surface-generic-field-cap");
    expect(cap).toHaveTextContent("Showing 40 of 50 top-level fields.");
    expect(cap.style.borderBottom).toBe("");
    // The row above it keeps its hairline: the list did not end there.
    expect(screen.getByTestId("field-k39").style.borderBottom).toContain(
      "1px solid",
    );
  });
});

describe("toolNameFromState", () => {
  it("reads the contract's SurfaceSource shape off the state", () => {
    expect(
      toolNameFromState({
        source: { server: "pagerduty", tool: "pagerduty.incident.read" },
        data: {},
      }),
    ).toBe("pagerduty.incident.read");
  });

  it("falls back to a spec that rode along but could not be used", () => {
    // No `title_path` ⇒ `specFromState` rejects it ⇒ the surface degrades. The
    // spec still knows which tool this was.
    expect(
      toolNameFromState({
        spec: { source: { server: "linear", tool: "linear.issue.read" } },
        data: {},
      }),
    ).toBe("linear.issue.read");
  });

  it.each([
    ["null", null],
    ["undefined", undefined],
    ["a primitive", 7],
    ["a string", "not a state"],
    ["an array", [1, 2, 3]],
    ["an empty object", {}],
    ["a source that is not an object", { source: "pagerduty", data: {} }],
    ["a source with no tool", { source: { server: "pagerduty" }, data: {} }],
    ["a non-string tool", { source: { tool: 42 }, data: {} }],
    ["a blank tool", { source: { tool: "  " }, data: {} }],
  ])("returns undefined for %s", (_label, state) => {
    expect(toolNameFromState(state)).toBeUndefined();
  });

  it("refuses to let a bare payload name its own tool", () => {
    // No `data` / `spec` key ⇒ the whole value is untrusted tool output, and a
    // `source` inside it is the tool's data, not the runtime's provenance.
    expect(
      toolNameFromState({
        source: { server: "webhook", tool: "stripe.charge.create" },
        incident_number: "4127",
      }),
    ).toBeUndefined();
  });

  it("caps what it returns, so no caller can paint an unbounded identifier", () => {
    const capped = toolNameFromState({
      source: { tool: "y".repeat(10_000) },
      data: {},
    });
    expect(capped?.length).toBe(TOOL_NAME_MAX_CHARS + 1);
  });
});

// The wire, end to end — and the fixture the rest of this file is pinned to.
//
// `RUNTIME_INFERRED_STATE` is not a hand-written state. It is the byte-for-byte
// `HydratedSurfaceSnapshot.state` the ai-backend serves from
// `GET /v1/agent/runs/{run_id}/surfaces` for the PagerDuty payload below — the
// value `useSurfacesV2.stateFor` hands the host mount, which is the only surface
// input the client treats as renderer payload.
//
// WHAT CHANGED, AND WHY THE OLD COMMENT PREDICTED IT. This constant used to be
// `{data, source}` with no `spec`, under a note that said: if the server stops
// emitting what we pinned, "that test fails and this one keeps passing on a
// fixture that no longer describes anything real". That is exactly what
// happened. The generative-UI floor added rung 0 — deterministic inference — so
// a mapping-shaped payload no longer arrives spec-less: the projector resolves a
// real SurfaceSpec synchronously, with no model and no failure mode, and it
// survives re-validation at the transport allow-list. The Python twin was
// inverted to assert the spec ARRIVES; this fixture is inverted with it rather
// than left describing a wire the server stopped producing.
//
// The Python half is pinned to the identical values in
// `services/ai-backend/tests/unit/runtime_api/test_run_surfaces_endpoint.py`
// (`TestSpecLessSurfaceWire` — `OUTPUT`, `SOURCE_REF`, `INFERRED_TITLE_PATH`,
// `INFERRED_FIELD_LABELS`), where the real projector + ledger emitter + endpoint
// produce it rather than a fixture asserting itself. Note what that class does
// NOT do: it stops short of the render, so the spec arriving on the wire is all
// it can prove. Whether a user then sees a shaped card is only knowable here.
//
// EDIT BOTH TOGETHER. The invariant is no longer "the server still emits
// `source`". It is now: **a mapping-shaped tool output reaches the renderer
// carrying a spec, and the renderer draws it shaped.** If the floor regresses —
// inference declines the payload, the allow-list drops the spec, or the fold
// stops carrying it — the Python test reds, and this one must red with it
// instead of quietly re-certifying the fallback as the normal case.
const RUNTIME_INFERRED_STATE = {
  data: {
    incident_number: "4127",
    title: "Elevated 5xx on checkout",
    status: "acknowledged",
    service: { id: "svc-9", name: "checkout-api" },
  },
  spec: {
    spec_version: 1,
    archetype: "record",
    source: { server: "pagerduty", tool: "pagerduty.incident.read" },
    // `title` heads the card, so the floor promotes it out of the field list
    // rather than repeating it as a row.
    title_path: "title",
    // Ranked by the floor, in this order. `status` is a short token from a small
    // vocabulary ⇒ badge; `incident_number` is an IDENTIFIER, which the floor
    // deliberately types `text` so "4127" is never reformatted as a magnitude;
    // `service` is a mapping carrying a name, so the bind is the nested path
    // rather than the object.
    fields: [
      { label: "Status", path: "status", format: "badge" },
      { label: "Incident Number", path: "incident_number", format: "text" },
      { label: "Service", path: "service.name", format: "badge" },
    ],
  },
  source: { server: "pagerduty", tool: "pagerduty.incident.read" },
};

/** `renderCurrent` is the entry point the host mount calls, and `record` is the
 * archetype the projector chose for this payload — its surface uri is
 * `record://pagerduty/pagerduty.incident.read/svc-9`. */
function renderRecord(state: unknown): void {
  const record = ARCHETYPE_ADAPTERS.find(
    (adapter) => adapter.scheme === "record",
  );
  expect(record).toBeDefined();
  render(record!.renderCurrent(state as never));
}

describe("the state the runtime actually writes for a mapping payload", () => {
  it("renders shaped, off the spec the server inferred", () => {
    renderRecord(RUNTIME_INFERRED_STATE);

    expect(screen.getByTestId("record-renderer")).toHaveAttribute(
      "data-spec",
      "present",
    );
    // The headline resolves through `title_path` rather than through a key we
    // liked the look of. "Untitled" is what a mis-bound path paints instead.
    expect(screen.getByTestId("surface-title")).toHaveTextContent(
      "Elevated 5xx on checkout",
    );
    // …and `title` is therefore NOT also a row. Excluding the path it promoted
    // is the difference between a record and a key dump with a heading.
    expect(screen.queryByTestId("field-title")).toBeNull();
  });

  it("draws the three inferred fields, bound to the paths the server chose", () => {
    renderRecord(RUNTIME_INFERRED_STATE);

    const expected: readonly (readonly [string, string, string])[] = [
      ["field-status", "Status", "acknowledged"],
      ["field-incident_number", "Incident Number", "4127"],
      // The nested bind is the part a flat key-dump gets wrong: the floor said
      // `service.name`, so the cell reads the service's NAME. Before the floor
      // this same payload put `{ 2 fields }` on screen.
      ["field-service.name", "Service", "checkout-api"],
    ];
    for (const [testId, label, value] of expected) {
      expect(screen.getByTestId(testId).firstElementChild).toHaveTextContent(
        label,
      );
      expect(screen.getByTestId(`${testId}-value`)).toHaveTextContent(value);
    }
    // Exactly those three: no fourth row invented, none silently dropped.
    // A row is named here by what it is NOT, because everything a row paints is
    // keyed on the same `field-<path>` stem: the value slot (`-value`) and the
    // chip inside it (`-badge`) are the row's own descendants, not rows.
    const rows = screen
      .getByTestId("record-renderer")
      .querySelectorAll(
        '[data-testid^="field-"]:not([data-testid$="-value"]):not([data-testid$="-badge"])',
      );
    expect(rows).toHaveLength(expected.length);
  });

  // `format: "badge"` is a purely VISUAL hint, and the two things it must do are
  // separable. It must CHANGE the register — the floor typing `status` as a
  // badge is wasted if the client paints it as the same grey text as everything
  // else — and it must change NOTHING about the value: no reroute through
  // `formatValue`'s number or datetime branches, which would reformat a token
  // into something the tool never said, and no landing in the tabular-figure
  // register, which means "read this digit-column against its neighbours".
  it("paints the badge-formatted values as chips, verbatim and out of the numeric register", () => {
    renderRecord(RUNTIME_INFERRED_STATE);

    for (const key of ["status", "service.name"]) {
      const cell = screen.getByTestId(`field-${key}-value`);
      expect(cell).not.toHaveStyle({ fontVariantNumeric: "tabular-nums" });
      const chip = screen.getByTestId(`field-${key}-badge`);
      expect(cell).toContainElement(chip);
      expect(chip.tagName).toBe("SPAN");
    }
    expect(screen.getByTestId("field-status-badge")).toHaveTextContent(
      "acknowledged",
    );
    // The counter-example the server typed `text` on purpose: an incident
    // number is an identifier, not a magnitude, so "4127" renders as itself —
    // and as plain text, because `text` is not `badge`.
    expect(screen.getByTestId("field-incident_number-value")).toHaveTextContent(
      "4127",
    );
    expect(screen.queryByTestId("field-incident_number-badge")).toBeNull();
  });

  it("shows the user no fallback chrome at all", () => {
    renderRecord(RUNTIME_INFERRED_STATE);

    // The point of the floor: for this payload the fallback is not reached, so
    // there is no note to read and nothing to apologise for.
    expect(screen.queryByTestId("surface-no-spec-note")).toBeNull();
    expect(screen.queryByTestId("surface-generic-fields")).toBeNull();
    expect(screen.getByTestId("record-renderer").textContent ?? "").not.toMatch(
      /spec/i,
    );
  });
});

// The fallback is still reachable and still tested — just no longer as the
// normal case. Its per-archetype coverage (that every renderer degrades here,
// over every hostile payload) lives in `archetypes/noSpecTotality.test.tsx`;
// what is asserted here is the one thing that test cannot see, which is that
// the SAME payload renders the same facts with the spec removed from the wire.
describe("a surface replayed from a run recorded before the floor", () => {
  it("renders the data, names the tool, and apologises for nothing", () => {
    // Runs recorded before rung 0 landed carry no spec, and never will. Their
    // replay renders here forever, which is why the component outlived its copy.
    const { spec: _spec, ...preFloor } = RUNTIME_INFERRED_STATE;
    renderRecord(preFloor);

    expect(screen.getByTestId("record-renderer")).toHaveAttribute(
      "data-spec",
      "absent",
    );
    // The DATA is the deliverable — that is the rule the deleted copy was
    // replaced BY, not merely the consolation prize for losing the spec.
    expect(screen.getByTestId("field-incident_number-value")).toHaveTextContent(
      "4127",
    );
    expect(screen.getByTestId("field-title-value")).toHaveTextContent(
      "Elevated 5xx on checkout",
    );
    // Nested objects are summarised rather than expanded, which is exactly what
    // the caption above them promises — and the whole of what it promises.
    expect(screen.getByTestId("field-service-value")).toHaveTextContent(
      "{ 2 fields }",
    );
    // Provenance, in the code register, and still inert: arriving over the wire
    // does not make a tool name a link.
    expect(screen.getByTestId("surface-no-spec-tool")).toHaveTextContent(
      "pagerduty.incident.read",
    );
    expect(screen.getByTestId("record-renderer").querySelector("a")).toBeNull();
    // AC17, on the one path that still reaches this view.
    const text = screen.getByTestId("record-renderer").textContent ?? "";
    expect(text).not.toMatch(/spec/i);
    expect(text).not.toMatch(/error|failed|unable|sorry|loading|preparing/i);
  });

  it("still renders for a surface emitted before `source` existed", () => {
    // Older still: every state persisted before PRD-02's provenance field
    // carries none. Absent means "unknown tool", not an error — the caption
    // names the thing instead of the name it does not have.
    const { spec: _spec, source: _source, ...oldest } = RUNTIME_INFERRED_STATE;
    renderRecord(oldest);

    expect(screen.queryByTestId("surface-no-spec-tool")).toBeNull();
    expect(screen.getByTestId("surface-no-spec-note")).toHaveTextContent(
      "The payload as the tool sent it — top-level fields only, nested objects summarised.",
    );
    expect(screen.getByTestId("field-incident_number-value")).toHaveTextContent(
      "4127",
    );
  });
});
