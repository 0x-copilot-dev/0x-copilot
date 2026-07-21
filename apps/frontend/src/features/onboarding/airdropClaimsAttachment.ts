// Web host resolution for the FTUE "Explain a CSV" starter chip.
//
// The chip (P3 `SuggestionChips`) carries `attachmentId ===
// AIRDROP_CLAIMS_CSV_ATTACHMENT_ID`; the onboarding composer calls the host's
// `resolveAttachment(id)` which turns the bundled `airdrop-claims.csv` asset
// into a real `File`, then `ComposerHandle.addAttachment(file)` runs it through
// the onboarding attachment adapter (`createOnboardingAttachmentAdapter`) → an
// inline base64 data-URL `file` content part with `mime_type: text/csv` (no
// server upload).
//
// The CSV bytes are pulled in with Vite's `?raw` — the file is bundled and its
// contents inlined as a string at build time. We deliberately avoid `?url` +
// `fetch`: bare `fetch` is eslint-banned inside `features/*` (the substrate
// primitive lives behind the Transport port, which is for backend calls, not a
// static bundled asset). `?raw` keeps the resolver substrate-clean and
// synchronous. The file adapter is ordered before the text adapter in the
// onboarding composite, so a `text/csv`-typed File lands as a `file` part
// (SPEC/PRD-P3 acceptance #3), not the text adapter's inlined `<attachment>`
// block.
import airdropClaimsCsvText from "./airdrop-claims.csv?raw";

/** Chip attachment id — matches the `attachmentId` on the CSV starter chip. */
export const AIRDROP_CLAIMS_CSV_ATTACHMENT_ID = "airdrop-claims.csv";

/** Display filename for the resolved attachment (and the run content part). */
export const AIRDROP_CLAIMS_CSV_FILENAME = "airdrop-claims.csv";

/** MIME the resolved `File` is stamped with so the composite routes it to the
 *  data-URL file adapter. */
export const AIRDROP_CLAIMS_CSV_MIME = "text/csv";

/**
 * Build the bundled `airdrop-claims.csv` fixture as a `File` stamped
 * `text/csv` so the onboarding attachment adapter carries it as a base64
 * data-URL `file` content part.
 *
 * Synchronous under the hood (the bytes are inlined at build time), but typed
 * `Promise<File>` so it drops straight into the composer's async
 * `resolveAttachment` port.
 */
export function resolveAirdropClaimsCsv(): Promise<File> {
  const file = new File([airdropClaimsCsvText], AIRDROP_CLAIMS_CSV_FILENAME, {
    type: AIRDROP_CLAIMS_CSV_MIME,
  });
  return Promise.resolve(file);
}
