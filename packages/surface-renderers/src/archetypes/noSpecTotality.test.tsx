import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { MAX_DISPLAY_CHARS } from "../_shared/path";
import { ARCHETYPE_ADAPTERS } from "./index";

/**
 * PRD-02 — the no-spec view is the degradation target for EVERY archetype, so
 * it is tested against every one of them rather than against the one that
 * happened to be edited.
 *
 * The matrix is driven off `ARCHETYPE_ADAPTERS`, not off a hand-written list:
 * a sixth archetype inherits these cases the day it is registered, which is the
 * only way this stays true. `renderCurrent` is the same entry point the host
 * (`TcSurfaceMount`) calls, and what arrives there is untrusted tool output — a
 * value typed `SurfaceState` and shaped like anything at all.
 *
 * A throw here is not one broken surface. `TcSurfaceMount` catches it and falls
 * through to the placeholder, so a defect on this path takes down every
 * generative surface in the product at once.
 */

/** Every payload the DoD names, plus the two envelope shapes each can arrive
 * in: the `{spec?, data}` envelope, and the bare payload a legacy flat state
 * (and the parity harness) passes. */
const HOSTILE_PAYLOADS: readonly (readonly [string, unknown])[] = [
  ["null", null],
  ["an empty array", []],
  ["an empty object", {}],
  ["a string primitive", "just a string"],
  ["a number primitive", 0],
  ["a boolean primitive", false],
  ["a deeply nested object", deepObject(40)],
  ["a hostile 10k-char string", "x".repeat(10_000)],
  ["an object holding a hostile 10k-char string", { blob: "x".repeat(10_000) }],
  ["an array of rows", [{ id: 1 }, { id: 2 }]],
  ["an array whose first row is a primitive", ["bare", "row"]],
];

/** `depth` levels of `{ child: … }`. The generic list must SUMMARISE this
 * ("{ 1 field }"), never walk it — a renderer that recursed would blow the
 * stack on a payload a tool can trivially produce. */
function deepObject(depth: number): Record<string, unknown> {
  let node: Record<string, unknown> = { leaf: "bottom" };
  for (let level = 0; level < depth; level += 1) {
    node = { child: node };
  }
  return node;
}

for (const adapter of ARCHETYPE_ADAPTERS) {
  const rendererTestId = `${adapter.scheme}-renderer`;

  describe(`${adapter.scheme} spec-less fallback totality`, () => {
    it.each(HOSTILE_PAYLOADS)("renders for %s", (_label, payload) => {
      // Both shapes the same payload can arrive in, one mount at a time so the
      // queries below stay unambiguous.
      for (const state of [{ data: payload }, payload]) {
        const view = render(adapter.renderCurrent(state as never));

        const root = screen.getByTestId(rendererTestId);
        expect(root).toHaveAttribute("data-spec", "absent");
        // The three parts of the honest view, all present, in every case.
        expect(screen.getByTestId("surface-no-spec-note")).toBeInTheDocument();
        const body = screen.getByTestId("surface-generic-fields");
        expect(body).toBeInTheDocument();
        expect(
          screen.getByTestId("surface-read-only-footer"),
        ).toBeInTheDocument();
        // And the body says SOMETHING. `{}` and `[]` are what an empty result
        // set looks like, and a blank band between the note and the footer
        // reads as a broken render — rule 04 broken by omission rather than by
        // wording, which no copy assertion below would catch.
        expect((body.textContent ?? "").trim()).not.toBe("");

        const text = root.textContent ?? "";
        // Rule 04 — never an error, and never a loading state either.
        expect(text).not.toMatch(/error|failed|unable|loading|preparing/i);
        // Nothing here was understood well enough to act on, so there is
        // nothing to act with.
        expect(root.querySelector("a")).toBeNull();
        expect(root.querySelector("button")).toBeNull();

        view.unmount();
      }
    });

    it("puts the note directly after the header, ahead of the payload", () => {
      render(adapter.renderCurrent({ data: { id: 1 } } as never));
      const header = screen.getByTestId("surface-header");
      expect(header.nextElementSibling).toBe(
        screen.getByTestId("surface-no-spec-note"),
      );
      const card = screen.getByTestId(rendererTestId)
        .firstElementChild as HTMLElement;
      expect(card.lastElementChild).toBe(
        screen.getByTestId("surface-read-only-footer"),
      );
    });

    it("caps a hostile 10k-char value rather than painting it whole", () => {
      render(
        adapter.renderCurrent({
          data: { blob: "x".repeat(10_000) },
        } as never),
      );
      const value = screen.getByTestId("field-blob-value").textContent ?? "";
      expect(value.length).toBeLessThanOrEqual(MAX_DISPLAY_CHARS + 1);
    });

    it("summarises a deeply nested payload instead of walking it", () => {
      render(adapter.renderCurrent({ data: deepObject(40) } as never));
      expect(screen.getByTestId("field-child-value")).toHaveTextContent(
        "{ 1 field }",
      );
    });

    it("names the tool when the envelope carries one, and does not link it", () => {
      render(
        adapter.renderCurrent({
          source: { server: "pagerduty", tool: "pagerduty.incident.read" },
          data: { id: 1 },
        } as never),
      );
      expect(screen.getByTestId("surface-no-spec-tool")).toHaveTextContent(
        "pagerduty.incident.read",
      );
      expect(screen.getByTestId(rendererTestId).querySelector("a")).toBeNull();
    });

    it("does not let a bare payload name its own tool", () => {
      render(
        adapter.renderCurrent({
          source: { server: "webhook", tool: "stripe.charge.create" },
          id: 1,
        } as never),
      );
      expect(screen.queryByTestId("surface-no-spec-tool")).toBeNull();
      expect(screen.getByTestId("surface-no-spec-note")).toHaveTextContent(
        "No spec matched this tool result",
      );
    });
  });
}
