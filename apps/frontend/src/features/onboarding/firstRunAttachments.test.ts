// Web first-run attachment mapping — CompleteAttachment[] → RunAttachmentRequest[]
// with the "Explain a CSV" text-part reconcile. Parity with the desktop
// `firstRunAttachments` coverage, adapted to the web runtime content-part shape
// (`filename`/`mimeType`).

import { describe, expect, it } from "vitest";

import type { CompleteAttachment } from "../chat/runtime/types";
import {
  dataUrlToText,
  toReadableRunAttachment,
  toReadableRunAttachments,
} from "./firstRunAttachments";

function csvAttachment(csv: string): CompleteAttachment {
  const dataUrl = `data:text/csv;base64,${btoa(csv)}`;
  return {
    id: "airdrop-claims.csv-123",
    type: "file",
    name: "airdrop-claims.csv",
    contentType: "text/csv",
    file: new File([csv], "airdrop-claims.csv", { type: "text/csv" }),
    status: { type: "complete" },
    content: [
      {
        type: "file",
        filename: "airdrop-claims.csv",
        data: dataUrl,
        mimeType: "text/csv",
      },
    ],
  };
}

function pdfAttachment(): CompleteAttachment {
  const dataUrl = "data:application/pdf;base64,JVBERi0=";
  return {
    id: "doc.pdf-9",
    type: "file",
    name: "doc.pdf",
    contentType: "application/pdf",
    status: { type: "complete" },
    content: [
      {
        type: "file",
        filename: "doc.pdf",
        data: dataUrl,
        mimeType: "application/pdf",
      },
    ],
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
    const mapped = toReadableRunAttachment(csvAttachment(csv));

    expect(mapped.id).toBe("airdrop-claims.csv-123");
    expect(mapped.name).toBe("airdrop-claims.csv");
    expect(mapped.type).toBe("file");
    expect(mapped.content_type).toBe("text/csv");
    expect(mapped.size).toBe(csv.length);
    expect(mapped.content).toHaveLength(1);
    const part = mapped.content[0];
    expect(part.type).toBe("text");
    expect(part.text).toBe(csv);
    // The base64 `data`/`mime_type` must NOT survive — a file part would be
    // summarised by name/size only (model-invisible).
    expect(part.data).toBeUndefined();
  });

  it("keeps a binary (PDF) file part as a file content part", () => {
    const mapped = toReadableRunAttachment(pdfAttachment());
    expect(mapped.content).toHaveLength(1);
    const part = mapped.content[0];
    expect(part.type).toBe("file");
    expect(part.filename).toBe("doc.pdf");
    expect(part.mime_type).toBe("application/pdf");
    expect(part.data).toBe("data:application/pdf;base64,JVBERi0=");
    // No file on this fixture → size is null.
    expect(mapped.size).toBeNull();
  });

  it("passes an image content part through unchanged", () => {
    const att: CompleteAttachment = {
      id: "pic-1",
      type: "image",
      name: "pic.png",
      contentType: "image/png",
      status: { type: "complete" },
      content: [{ type: "image", image: "data:image/png;base64,AAAA" }],
    };
    const mapped = toReadableRunAttachment(att);
    expect(mapped.type).toBe("image");
    const part = mapped.content[0];
    expect(part.type).toBe("image");
    expect(part.image).toBe("data:image/png;base64,AAAA");
  });
});

describe("toReadableRunAttachments", () => {
  it("maps the opaque submit attachments array", () => {
    const csv = "a,b\n1,2";
    const mapped = toReadableRunAttachments([
      csvAttachment(csv),
      pdfAttachment(),
    ]);
    expect(mapped).toHaveLength(2);
    expect(mapped[0].content[0].text).toBe(csv);
    expect(mapped[1].content[0].type).toBe("file");
  });
});
