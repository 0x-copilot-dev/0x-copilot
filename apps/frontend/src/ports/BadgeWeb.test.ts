import { describe, expect, it } from "vitest";

import { WebBadgePort } from "./BadgeWeb";

describe("WebBadgePort", () => {
  it("is a no-op for any slug/count combination (web has no dock badge)", () => {
    const port = new WebBadgePort();
    // The contract is "does not throw". Web has no dock / tray icon
    // today; the favicon-overlay variant is deferred (Wave 4+).
    expect(() => port.setBadge("chats", 0)).not.toThrow();
    expect(() => port.setBadge("inbox", 42)).not.toThrow();
    expect(() => port.setBadge("todos", -1)).not.toThrow();
  });
});
