import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom does not implement Element.prototype.scrollIntoView; provide a no-op so
// components that scroll a focused element into view (AgentsTab, SourcesTab,
// scrollChatToCitation, …) don't throw under test.
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

afterEach(() => {
  cleanup();
});
