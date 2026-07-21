// @vitest-environment jsdom
import type { CompleteAttachment } from "@0x-copilot/chat-surface";
import { describe, expect, it } from "vitest";

import { createDesktopAttachmentAdapter } from "../composer/desktopAttachmentAdapter";
import {
  AIRDROP_CLAIMS_CSV_TEXT,
  resolveAirdropClaimsCsv,
} from "./airdropClaimsFixture";
import {
  dataUrlToText,
  toReadableRunAttachment,
  toReadableRunAttachments,
} from "./firstRunAttachments";

function csvFilePart(csv: string): CompleteAttachment {
  const dataUrl = `data:text/csv;base64,${btoa(csv)}`;
  return {
    id: "csv-1",
    name: "airdrop-claims.csv",
    size: csv.length,
    type: "text/csv",
    content: [
      {
        type: "file",
        name: "airdrop-claims.csv",
        mime: "text/csv",
        data: dataUrl,
      },
    ],
    status: { type: "complete" },
  };
}

describe("dataUrlToText", () => {
  it("decodes a base64 text data URL", () => {
    expect(dataUrlToText(`data:text/csv;base64,${btoa("a,b\n1,2")}`)).toBe(
      "a,b\n1,2",
    );
  });

  it("decodes a URL-encoded (non-base64) data URL", () => {
    expect(dataUrlToText("data:text/plain,a%2Cb")).toBe("a,b");
  });

  it("returns a non-data-URL string unchanged", () => {
    expect(dataUrlToText("already text")).toBe("already text");
  });
});

describe("toReadableRunAttachment", () => {
  it("routes a text/csv file part to a model-READABLE text part (no base64)", () => {
    const csv = "address,token\n0xabc,CPILOT";
    const mapped = toReadableRunAttachment(csvFilePart(csv));

    expect(mapped.id).toBe("csv-1");
    expect(mapped.name).toBe("airdrop-claims.csv");
    expect(mapped.content_type).toBe("text/csv");
    expect(mapped.content).toHaveLength(1);
    const part = mapped.content[0];
    expect(part.type).toBe("text");
    expect(part.text).toBe(csv);
    // The base64 payload must NOT ride along — a `file` part is model-invisible.
    expect(part).not.toHaveProperty("data");
    expect(part.type).not.toBe("file");
  });

  it("keeps a binary (pdf) file part as a base64 file part", () => {
    const att: CompleteAttachment = {
      id: "pdf-1",
      name: "doc.pdf",
      size: 3,
      type: "application/pdf",
      content: [
        {
          type: "file",
          name: "doc.pdf",
          mime: "application/pdf",
          data: "data:application/pdf;base64,AAAA",
        },
      ],
    };
    const mapped = toReadableRunAttachment(att);
    expect(mapped.content[0].type).toBe("file");
    expect(mapped.content[0].mime_type).toBe("application/pdf");
    expect(mapped.content[0].data).toBe("data:application/pdf;base64,AAAA");
  });

  it("passes an image part through unchanged", () => {
    const att: CompleteAttachment = {
      id: "img-1",
      name: "pic.png",
      size: 4,
      type: "image/png",
      content: [{ type: "image", image: "data:image/png;base64,BBBB" }],
    };
    const mapped = toReadableRunAttachment(att);
    expect(mapped.content[0].type).toBe("image");
    expect((mapped.content[0] as { image?: string }).image).toBe(
      "data:image/png;base64,BBBB",
    );
  });
});

describe("CSV chip end-to-end (fixture → adapter → readable text attachment)", () => {
  it("resolves the airdrop-claims chip to one readable text content part", async () => {
    // The exact host path: chip → resolveAttachment → adapter.add → submit map.
    const file = await resolveAirdropClaimsCsv();
    const complete = (await createDesktopAttachmentAdapter().add(
      file,
    )) as CompleteAttachment;

    const [mapped] = toReadableRunAttachments([complete]);

    expect(mapped.content).toHaveLength(1);
    expect(mapped.content[0].type).toBe("text");
    expect(mapped.content[0].text).toBe(AIRDROP_CLAIMS_CSV_TEXT);
    expect(mapped.content[0]).not.toHaveProperty("data");
  });
});
