import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom doesn't ship `ResizeObserver`; recharts' `<ResponsiveContainer>`
// uses it for layout. Provide a no-op stub so chart-mounting tests don't
// throw. The chart never observes anything in tests — we assert against
// the static SVG output, not pixel measurements.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
}

// jsdom doesn't ship `Element.prototype.scrollIntoView`; the workspace
// pane's focus-jump effect calls it (SourcesTab + AgentsTab). Provide a
// no-op shim so component tests don't crash when focus is wired.
if (
  typeof Element !== "undefined" &&
  typeof Element.prototype.scrollIntoView !== "function"
) {
  Element.prototype.scrollIntoView = function scrollIntoView(): void {};
}

afterEach(() => {
  cleanup();
});
