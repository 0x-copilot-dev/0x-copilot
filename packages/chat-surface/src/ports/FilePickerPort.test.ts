import { describe, expect, it, vi } from "vitest";

import type {
  FilePickerOptions,
  FilePickerPort,
  FilePickerSelection,
} from "./FilePickerPort";

describe("FilePickerPort contract", () => {
  it("returns an array of selections with name/size/type/stream", async () => {
    const fakeStream = (): ReadableStream<Uint8Array> =>
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new Uint8Array([1, 2, 3]));
          controller.close();
        },
      });
    const pick = vi.fn(
      async (
        _opts: FilePickerOptions,
      ): Promise<ReadonlyArray<FilePickerSelection>> => [
        { name: "a.txt", size: 3, type: "text/plain", stream: fakeStream },
      ],
    );
    const port: FilePickerPort = { pick };
    const result = await port.pick({ multiple: false, accept: ["text/plain"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.name).toBe("a.txt");
    expect(result[0]!.size).toBe(3);
    expect(result[0]!.type).toBe("text/plain");
    expect(typeof result[0]!.stream).toBe("function");
  });

  it("permits an empty selection (user cancelled)", async () => {
    const port: FilePickerPort = { pick: async () => [] };
    const result = await port.pick({});
    expect(result).toEqual([]);
  });
});
