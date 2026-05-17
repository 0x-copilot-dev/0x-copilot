import { describe, expect, it, vi } from "vitest";

import type { ClipboardPort } from "./ClipboardPort";

describe("ClipboardPort contract", () => {
  it("resolves a copyText call with the given text", async () => {
    const copyText = vi.fn().mockResolvedValue(undefined);
    const port: ClipboardPort = { copyText };
    await port.copyText("https://example.com/share/x");
    expect(copyText).toHaveBeenCalledWith("https://example.com/share/x");
  });

  it("propagates rejection when the substrate refuses (e.g. insecure context)", async () => {
    const port: ClipboardPort = {
      copyText: async () => {
        throw new Error("clipboard write blocked");
      },
    };
    await expect(port.copyText("x")).rejects.toThrow("clipboard write blocked");
  });
});
