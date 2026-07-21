import { describe, expect, it } from "vitest";

import { AtlasFileAttachmentAdapter, mimeTypeForFileName } from "./file";
import { fileMatchesAccept } from "./match";
import { createOnboardingAttachmentAdapter } from "./onboardingAdapter";
import type { CompleteAttachment, PendingAttachment } from "../types";

// FTUE P3 CSV-attach enablement: the "Explain a CSV" starter chip pre-attaches
// `airdrop-claims.csv`. These tests pin the accept-list change on the file
// adapter and the onboarding composite's CSV → data-URL `file` part routing.

function csvFile(type = "text/csv"): File {
  return new File(["address,token\n0xabc,CPILOT\n"], "airdrop-claims.csv", {
    type,
  });
}

describe("AtlasFileAttachmentAdapter — CSV accept", () => {
  it("accepts text/csv and the .csv extension", () => {
    const adapter = new AtlasFileAttachmentAdapter();
    expect(adapter.accept).toContain("text/csv");
    expect(adapter.accept).toContain(".csv");
    // fileMatchesAccept resolves both a typed and an extension-only CSV.
    expect(
      fileMatchesAccept(
        { name: "airdrop-claims.csv", type: "text/csv" },
        adapter.accept,
      ),
    ).toBe(true);
    expect(
      fileMatchesAccept(
        { name: "airdrop-claims.csv", type: "" },
        adapter.accept,
      ),
    ).toBe(true);
    // Regression: the office/PDF set still matches.
    expect(
      fileMatchesAccept({ name: "deck.pptx", type: "" }, adapter.accept),
    ).toBe(true);
  });

  it("maps the .csv extension to text/csv", () => {
    expect(mimeTypeForFileName("airdrop-claims.csv")).toBe("text/csv");
    expect(mimeTypeForFileName("report.PDF")).toBe("application/pdf");
    expect(mimeTypeForFileName("noext")).toBe("");
  });

  it("emits an inline base64 data-URL file part with mime text/csv", async () => {
    const adapter = new AtlasFileAttachmentAdapter();
    const pending = await adapter.add({ file: csvFile() });
    expect(pending.type).toBe("file");
    const complete = await adapter.send(pending);
    expect(complete.content).toHaveLength(1);
    const part = complete.content[0] as {
      type: string;
      filename: string;
      data: string;
      mimeType: string;
    };
    expect(part.type).toBe("file");
    expect(part.filename).toBe("airdrop-claims.csv");
    expect(part.mimeType).toBe("text/csv");
    expect(part.data.startsWith("data:")).toBe(true);
  });

  it("recovers text/csv for an empty-MIME .csv on add", async () => {
    const adapter = new AtlasFileAttachmentAdapter();
    const pending = await adapter.add({ file: csvFile("") });
    expect(pending.contentType).toBe("text/csv");
  });
});

describe("createOnboardingAttachmentAdapter — CSV routing", () => {
  it("routes a text/csv file to the file adapter (data-URL file part)", async () => {
    const adapter = createOnboardingAttachmentAdapter();
    const pending = (await adapter.add({
      file: csvFile(),
    })) as PendingAttachment;
    // The file adapter tags its pending attachment `type: "file"`; the text
    // adapter would tag it `type: "document"`. Ordering the file adapter first
    // is what makes the CSV land as a `file` part.
    expect(pending.type).toBe("file");
    const complete = (await adapter.send(pending)) as CompleteAttachment;
    const part = complete.content[0] as { type: string; mimeType?: string };
    expect(part.type).toBe("file");
    expect(part.mimeType).toBe("text/csv");
  });

  it("still routes a non-CSV text file to the text adapter", async () => {
    const adapter = createOnboardingAttachmentAdapter();
    const pending = (await adapter.add({
      file: new File(["hello"], "notes.txt", { type: "text/plain" }),
    })) as PendingAttachment;
    expect(pending.type).toBe("document");
  });

  it("routes images to the image adapter", async () => {
    const adapter = createOnboardingAttachmentAdapter();
    const pending = (await adapter.add({
      file: new File([new Uint8Array([1, 2, 3])], "chart.png", {
        type: "image/png",
      }),
    })) as PendingAttachment;
    expect(pending.type).toBe("image");
  });
});
