import { afterEach, describe, expect, it, vi } from "vitest";

import { WebClipboardPort } from "./ClipboardWeb";

const originalClipboard = (
  globalThis as { navigator?: { clipboard?: unknown } }
).navigator?.clipboard;

function stubClipboard(write: (text: string) => Promise<void>): void {
  Object.defineProperty(globalThis.navigator, "clipboard", {
    configurable: true,
    value: { writeText: write },
  });
}

afterEach(() => {
  Object.defineProperty(globalThis.navigator, "clipboard", {
    configurable: true,
    value: originalClipboard,
  });
});

describe("WebClipboardPort", () => {
  it("forwards the text to navigator.clipboard.writeText", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);
    const port = new WebClipboardPort();
    await port.copyText("https://example.com/share/x");
    expect(writeText).toHaveBeenCalledWith("https://example.com/share/x");
  });

  it("propagates rejection from the underlying API", async () => {
    stubClipboard(() => Promise.reject(new Error("blocked by permission")));
    const port = new WebClipboardPort();
    await expect(port.copyText("x")).rejects.toThrow("blocked by permission");
  });

  it("throws a clear error when navigator.clipboard is unavailable", async () => {
    Object.defineProperty(globalThis.navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    const port = new WebClipboardPort();
    await expect(port.copyText("x")).rejects.toThrow(/clipboard unavailable/);
  });
});
