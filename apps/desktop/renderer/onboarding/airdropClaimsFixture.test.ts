// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { CompleteAttachment } from "@0x-copilot/chat-surface";
import { describe, expect, it } from "vitest";

import { createDesktopAttachmentAdapter } from "../composer/desktopAttachmentAdapter";
import {
  AIRDROP_CLAIMS_CSV_ATTACHMENT_ID,
  AIRDROP_CLAIMS_CSV_FILENAME,
  AIRDROP_CLAIMS_CSV_TEXT,
  resolveAirdropClaimsCsv,
} from "./airdropClaimsFixture";

describe("airdropClaimsFixture (desktop bundled resource → File)", () => {
  it("uses a stable chip attachment id", () => {
    expect(AIRDROP_CLAIMS_CSV_ATTACHMENT_ID).toBe("airdrop-claims.csv");
  });

  it("keeps the inlined bytes byte-identical to airdrop-claims.csv", () => {
    // Vitest runs with the package dir (apps/desktop) as the root/cwd.
    const onDisk = readFileSync(
      join(process.cwd(), "renderer", "onboarding", "airdrop-claims.csv"),
      "utf8",
    );
    expect(AIRDROP_CLAIMS_CSV_TEXT).toBe(onDisk);
  });

  it("resolves a text/csv File with the fixture contents", async () => {
    const file = await resolveAirdropClaimsCsv();
    expect(file.name).toBe(AIRDROP_CLAIMS_CSV_FILENAME);
    expect(file.type).toBe("text/csv");
    expect(await file.text()).toBe(AIRDROP_CLAIMS_CSV_TEXT);
  });

  it("end-to-end: resolved File attaches as a text/csv data-URL file part", async () => {
    const file = await resolveAirdropClaimsCsv();
    const complete = (await createDesktopAttachmentAdapter().add(
      file,
    )) as CompleteAttachment;
    const part = (complete.content ?? [])[0] as unknown as {
      type: string;
      mime: string;
    };
    expect(part.type).toBe("file");
    expect(part.mime).toBe("text/csv");
  });
});
