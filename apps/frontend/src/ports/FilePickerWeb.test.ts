import { describe, expect, it, vi } from "vitest";

import { WebFilePickerPort } from "./FilePickerWeb";

/**
 * Helper: capture the synthesised `<input type="file">` that the port
 * appends to the document, push a fake `FileList`, and dispatch the
 * change event so the picker resolves.
 */
function deliverFiles(files: ReadonlyArray<File>): HTMLInputElement {
  const input = document.querySelector(
    'input[type="file"]',
  ) as HTMLInputElement | null;
  if (input === null) {
    throw new Error(
      "WebFilePickerPort did not append a hidden <input type=file>",
    );
  }
  // jsdom won't let us write `input.files` directly; faking via the
  // descriptor is the standard workaround.
  Object.defineProperty(input, "files", {
    configurable: true,
    value: {
      length: files.length,
      item: (i: number) => files[i] ?? null,
      [Symbol.iterator]: function* () {
        for (const f of files) yield f;
      },
    },
  });
  input.dispatchEvent(new Event("change"));
  return input;
}

describe("WebFilePickerPort", () => {
  it("resolves with selections mapped to the substrate-portable shape", async () => {
    const port = new WebFilePickerPort();
    const promise = port.pick({ multiple: true, accept: ["text/plain"] });
    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    deliverFiles([file]);
    const out = await promise;
    expect(out.length).toBe(1);
    expect(out[0].name).toBe("hello.txt");
    expect(out[0].type).toBe("text/plain");
    expect(out[0].size).toBe(5);
    expect(typeof out[0].stream).toBe("function");
  });

  it("returns an empty array when the user cancels", async () => {
    const port = new WebFilePickerPort();
    const promise = port.pick({ multiple: false });
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement | null;
    if (input === null) throw new Error("input missing");
    input.dispatchEvent(new Event("cancel"));
    const out = await promise;
    expect(out).toEqual([]);
  });

  it("returns an empty array when document is unavailable (SSR safety)", async () => {
    // Simulate SSR by spying on `document` and forcing the early-return.
    const originalDocument = globalThis.document;
    // @ts-expect-error — deliberately removing the global for the test
    delete globalThis.document;
    try {
      const port = new WebFilePickerPort();
      const out = await port.pick({});
      expect(out).toEqual([]);
    } finally {
      globalThis.document = originalDocument;
    }
  });

  it("joins the accept list with a comma when passed", async () => {
    const port = new WebFilePickerPort();
    const promise = port.pick({ accept: ["image/png", "image/jpeg"] });
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    expect(input.accept).toBe("image/png,image/jpeg");
    // Cancel to clean up the pending promise.
    input.dispatchEvent(new Event("cancel"));
    await promise;
  });

  // Suppress unused-import lint for the unused symbol — keeps the
  // testfile self-contained without an `import type { … }`.
  void vi;
});
