import { describe, expect, it } from "vitest";

import { createOnboardingAttachmentAdapter } from "../chat/runtime/attachments";
import type { CompleteAttachment, PendingAttachment } from "../chat/runtime";
import {
  AIRDROP_CLAIMS_CSV_ATTACHMENT_ID,
  AIRDROP_CLAIMS_CSV_FILENAME,
  resolveAirdropClaimsCsv,
} from "./airdropClaimsAttachment";

describe("resolveAirdropClaimsCsv (web bundled asset → File)", () => {
  it("uses a stable chip attachment id", () => {
    expect(AIRDROP_CLAIMS_CSV_ATTACHMENT_ID).toBe("airdrop-claims.csv");
  });

  it("returns the bundled fixture as a text/csv File", async () => {
    const file = await resolveAirdropClaimsCsv();
    expect(file.name).toBe(AIRDROP_CLAIMS_CSV_FILENAME);
    expect(file.type).toBe("text/csv");
    const text = await file.text();
    // Real bundled contents (Vite `?raw`): header + crypto-airdrop rows.
    expect(text.startsWith("address,token,amount,claimed,date")).toBe(true);
    expect(text).toContain("CPILOT");
  });

  it("end-to-end: resolved File attaches as a data-URL file part", async () => {
    const file = await resolveAirdropClaimsCsv();
    const adapter = createOnboardingAttachmentAdapter();
    const pending = (await adapter.add({ file })) as PendingAttachment;
    const complete = (await adapter.send(pending)) as CompleteAttachment;
    const part = complete.content[0] as { type: string; mimeType?: string };
    expect(part.type).toBe("file");
    expect(part.mimeType).toBe("text/csv");
  });
});
