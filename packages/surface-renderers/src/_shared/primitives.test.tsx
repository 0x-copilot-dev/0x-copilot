import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import {
  GenericFieldList,
  NoSpecView,
  TOOL_NAME_MAX_CHARS,
  toolNameFromState,
} from "./primitives";

// PRD-02 — the no-spec view. The unit-level half of the contract; the
// per-archetype half (that every fallback routes here, and stays total over
// `unknown`) lives in `archetypes/noSpecTotality.test.tsx`.

describe("NoSpecView note", () => {
  it("names the unmatched tool in a code register", () => {
    render(<NoSpecView data={{ id: 1 }} tool="pagerduty.incident.read" />);
    const tool = screen.getByTestId("surface-no-spec-tool");
    expect(tool.tagName).toBe("CODE");
    expect(tool).toHaveTextContent("pagerduty.incident.read");
    expect(screen.getByTestId("surface-no-spec-note")).toHaveTextContent(
      "No spec matched pagerduty.incident.read, so this is the payload as the tool sent it",
    );
  });

  it("states both honest limits of the generic render", () => {
    render(<NoSpecView data={{ id: 1 }} />);
    const note = screen.getByTestId("surface-no-spec-note");
    expect(note).toHaveTextContent("top-level fields only");
    expect(note).toHaveTextContent("nested objects summarised");
  });

  it("says a spec will be generated and cached — the view is not waiting on one", () => {
    render(<NoSpecView data={{ id: 1 }} />);
    expect(screen.getByTestId("surface-no-spec-note")).toHaveTextContent(
      "A spec will be generated and cached on the next call.",
    );
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
      "No spec matched this tool result, so this is the payload as the tool sent it",
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
      padding: "9px 12px",
    });
  });

  it("sets the label in the mono identifier register", () => {
    render(<GenericFieldList data={{ incident_number: "4127" }} />);
    const label = screen.getByTestId("field-incident_number")
      .firstElementChild as HTMLElement;
    expect(label).toHaveTextContent("Incident Number");
    expect(label).toHaveStyle({
      fontSize: "9.5px",
      letterSpacing: "0.11em",
      textTransform: "uppercase",
    });
    expect(label.style.fontFamily).toContain("--font-mono");
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
