import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { ToolRunGroup } from "./ToolRunGroup";

const g = (props: Partial<Parameters<typeof ToolRunGroup>[0]> = {}) => (
  <ToolRunGroup state="settled" done={3} total={3} elapsed="8.3s" {...props}>
    <span data-testid="member" />
  </ToolRunGroup>
);

const details = () =>
  screen.getByTestId("tool-run-group") as HTMLDetailsElement;
const label = () => screen.getByTestId("tool-run-group-label").textContent;

describe("ToolRunGroup", () => {
  it("collapses a settled run to one line (D-3.2)", () => {
    render(g());
    expect(details().open).toBe(false);
    expect(label()).toBe("Worked for 8.3s · 3 steps");
  });

  it("stays open while the run is working", () => {
    render(g({ state: "running", done: 1, total: 6 }));
    expect(details().open).toBe(true);
    expect(label()).toBe("Working · 1 of 6");
  });

  it("stays open when the RUN failed (D-3.5)", () => {
    render(g({ state: "failed", done: 4, total: 4, elapsed: "12s" }));
    expect(details().open).toBe(true);
    expect(label()).toBe("Failed after 12s · 4 steps");
    // Never "Worked for" over a run that did not work.
    expect(label()).not.toContain("Worked");
  });

  it("collapses when the run settles — but not once the user has toggled (D-3.3)", () => {
    const { rerender } = render(g({ state: "running", done: 1, total: 3 }));
    expect(details().open).toBe(true);

    rerender(g({ state: "settled", done: 3, total: 3 }));
    expect(details().open).toBe(false);

    // The reader opens it deliberately. jsdom does not natively toggle
    // <details> on a summary click, so set `open` too — but the PIN comes from
    // the click itself, which is the point.
    fireEvent.click(screen.getByTestId("tool-run-group-summary"));
    details().open = true;
    expect(details().dataset.pinned).toBe("true");

    // …and a later state change must not close it out from under them.
    rerender(g({ state: "settled", done: 3, total: 3, elapsed: "9s" }));
    expect(details().open).toBe(true);
  });

  // Regression: the live journey caught a settled group stuck OPEN with
  // pinned=true. `<details>` fires `toggle` for programmatic writes too, so the
  // auto-expand at run start was marking the group as user-pinned. jsdom does
  // not fire `toggle` on a property write, so this asserts the FLAG rather than
  // relying on the event to reproduce it.
  it("does not treat its own auto-expand as a user toggle", () => {
    const { rerender } = render(g({ state: "running", done: 1, total: 4 }));
    expect(details().open).toBe(true);
    expect(details().dataset.pinned).toBe("false");

    rerender(g({ state: "settled", done: 4, total: 4 }));
    expect(details().dataset.pinned).toBe("false");
    expect(details().open).toBe(false);
  });

  it("pins on Enter / Space, so keyboard users get the same guarantee", () => {
    const { rerender } = render(g({ state: "running", done: 1, total: 3 }));
    fireEvent.keyDown(screen.getByTestId("tool-run-group-summary"), {
      key: "Enter",
    });
    expect(details().dataset.pinned).toBe("true");
    rerender(g({ state: "settled", done: 3, total: 3 }));
    expect(details().open).toBe(true);
  });

  it("shows a muted retried count only once settled", () => {
    const { rerender } = render(
      g({ state: "running", done: 2, total: 4, retried: 1 }),
    );
    expect(screen.queryByTestId("tool-run-group-retried")).toBeNull();

    rerender(g({ state: "settled", done: 4, total: 4, retried: 1 }));
    expect(screen.getByTestId("tool-run-group-retried").textContent).toBe(
      "1 retried",
    );
  });

  it("shortens the label at compact", () => {
    render(g({ state: "running", done: 2, total: 6, compact: true }));
    expect(label()).toBe("2/6");
  });

  it("stays honest when elapsed is unknowable", () => {
    render(g({ elapsed: null }));
    expect(label()).toBe("3 steps");
    expect(label()).not.toContain("undefined");
    expect(label()).not.toContain("null");
  });

  it("singularises a one-step label", () => {
    render(g({ done: 1, total: 1, elapsed: "1.2s" }));
    expect(label()).toBe("Worked for 1.2s · 1 step");
  });

  // FR-3.11 — the stranded-CSS guard. `ReasoningGroup` shipped its CSS into the
  // WEB host only, so it renders unstyled on desktop. This group must not.
  it("ships every rule inside the package, not in a host stylesheet", () => {
    const root = join(__dirname, "..", "..", "..", "..");
    const hostSheets = [
      join(root, "apps", "frontend", "src", "styles.css"),
      join(root, "apps", "desktop", "renderer", "desktop.css"),
    ];
    for (const sheet of hostSheets) {
      let css = "";
      try {
        css = readFileSync(sheet, "utf8");
      } catch {
        continue; // sheet absent in this checkout — nothing to shadow
      }
      expect(
        css.includes("cs-run-group"),
        `${sheet} must not own ToolRunGroup's class names`,
      ).toBe(false);
    }
    // …and the component genuinely carries its own rules.
    const { container } = render(g());
    expect(container.querySelector("style")?.textContent).toContain(
      ".cs-run-group",
    );
  });
});
