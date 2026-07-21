import type { AttachmentAdapter } from "../types";
import { AtlasCompositeAttachmentAdapter } from "./composite";
import { AtlasFileAttachmentAdapter } from "./file";
import { AtlasImageAttachmentAdapter } from "./image";
import { AtlasTextAttachmentAdapter } from "./text";

/**
 * Attachment adapter for the FTUE onboarding composer (P3 "What should we
 * run first?" surface).
 *
 * It differs from the ChatScreen composite in ONE way: the file adapter is
 * ordered **before** the text adapter. The composite dispatches to the first
 * sub-adapter whose `accept` matches, and the file adapter now accepts
 * `text/csv` (see `file.ts`). So the "Explain a CSV" chip's `airdrop-claims.csv`
 * — resolved to a `text/csv` `File` — routes to the file adapter and lands as an
 * inline base64 data-URL `file` content part with `mime_type: text/csv` (the
 * shape the run body expects; SPEC/PRD-P3 acceptance #3), rather than the text
 * adapter's inlined `<attachment>` text block.
 *
 * Every other file type behaves exactly as in ChatScreen: images → the image
 * adapter (first), other `text/*` files (`.txt`, `.md`, JSON, …) fall through to
 * the text adapter since the file adapter does not accept them. `text/csv` is
 * the only overlap between the file and text accept lists, so re-ordering
 * changes routing for CSV alone.
 */
export function createOnboardingAttachmentAdapter(): AttachmentAdapter {
  return new AtlasCompositeAttachmentAdapter([
    new AtlasImageAttachmentAdapter(),
    new AtlasFileAttachmentAdapter(),
    new AtlasTextAttachmentAdapter(),
  ]);
}
