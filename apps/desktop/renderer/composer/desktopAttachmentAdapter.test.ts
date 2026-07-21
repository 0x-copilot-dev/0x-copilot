// @vitest-environment jsdom
import type { CompleteAttachment } from "@0x-copilot/chat-surface";
import { describe, expect, it } from "vitest";

import { createDesktopAttachmentAdapter } from "./desktopAttachmentAdapter";

// FTUE P3 CSV-attach enablement (desktop host): the "Explain a CSV" chip's
// `airdrop-claims.csv` must attach as an inline base64 data-URL `file` part with
// `mime: text/csv` — including when the File carries no explicit MIME.

interface FilePart {
  type: "file";
  name: string;
  mime: string;
  data: string;
}

function firstPart(complete: CompleteAttachment): Record<string, unknown> {
  const parts = complete.content ?? [];
  expect(parts).toHaveLength(1);
  return parts[0] as unknown as Record<string, unknown>;
}

describe("createDesktopAttachmentAdapter — CSV", () => {
  it("carries a text/csv File as a data-URL file part", async () => {
    const adapter = createDesktopAttachmentAdapter();
    const complete = (await adapter.add(
      new File(["address,token\n0xabc,CPILOT\n"], "airdrop-claims.csv", {
        type: "text/csv",
      }),
    )) as CompleteAttachment;
    expect(complete.type).toBe("text/csv");
    const part = firstPart(complete) as unknown as FilePart;
    expect(part.type).toBe("file");
    expect(part.name).toBe("airdrop-claims.csv");
    expect(part.mime).toBe("text/csv");
    expect(part.data.startsWith("data:")).toBe(true);
  });

  it("recovers text/csv for an empty-MIME .csv File", async () => {
    const adapter = createDesktopAttachmentAdapter();
    const complete = (await adapter.add(
      new File(["address,token\n0xabc,CPILOT\n"], "airdrop-claims.csv"),
    )) as CompleteAttachment;
    expect(complete.type).toBe("text/csv");
    const part = firstPart(complete) as unknown as FilePart;
    expect(part.mime).toBe("text/csv");
  });

  it("still carries images as an image part", async () => {
    const adapter = createDesktopAttachmentAdapter();
    const complete = (await adapter.add(
      new File([new Uint8Array([1, 2, 3])], "chart.png", { type: "image/png" }),
    )) as CompleteAttachment;
    expect(firstPart(complete).type).toBe("image");
  });
});
