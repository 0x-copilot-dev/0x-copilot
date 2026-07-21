// Desktop host resolution for the FTUE "Explain a CSV" starter chip.
//
// The chip carries `attachmentId === AIRDROP_CLAIMS_CSV_ATTACHMENT_ID`; the
// onboarding composer calls the host's `resolveAttachment(id)` which turns this
// bundled fixture into a real `File`, then `ComposerHandle.addAttachment(file)`
// runs it through `createDesktopAttachmentAdapter` → an inline base64 data-URL
// `file` content part with `mime: text/csv` (no server upload).
//
// Unlike web (Vite `?url` bundled asset + `fetch`), the desktop renderer is an
// esbuild bundle with no asset-URL loader, so the CSV bytes are inlined here as
// a string constant — the "bundled resource". The sibling `airdrop-claims.csv`
// is the human-authored source of truth; `airdropClaimsFixture.test.ts` reads
// it and asserts byte-equality with this constant so the two never drift.

/** Chip attachment id — matches the `attachmentId` on the CSV starter chip. */
export const AIRDROP_CLAIMS_CSV_ATTACHMENT_ID = "airdrop-claims.csv";

/** Display filename for the resolved attachment (and the run content part). */
export const AIRDROP_CLAIMS_CSV_FILENAME = "airdrop-claims.csv";

/** MIME the resolved `File` is stamped with (→ the `file` part's `mime`). */
export const AIRDROP_CLAIMS_CSV_MIME = "text/csv";

/**
 * The `airdrop-claims.csv` fixture bytes, inlined so the esbuild renderer bundle
 * ships them with no asset pipeline. Kept byte-identical to the sibling
 * `airdrop-claims.csv` by `airdropClaimsFixture.test.ts`.
 */
export const AIRDROP_CLAIMS_CSV_TEXT = `address,token,amount,claimed,date
0x7f3C91a2De4b5C8f0A1b2c3D4e5F6a7B8c9D0e1F,CPILOT,12500,true,2026-06-14
0x4A9d3B77e1C2f5A6b8D9e0F1a2B3c4D5e6F7a8B9,CPILOT,8200,false,2026-06-14
0x91EeC3f0A1b2C3d4E5f6A7b8C9d0E1f2A3b4C5d6,CPILOT,45000,true,2026-06-15
0x2b6F9dA1B2c3D4e5F6a7B8c9D0e1F2a3B4c5D6e7,CPILOT,3100,false,2026-06-15
0xD40c77E2A3b4C5d6E7f8A9b0C1d2E3f4A5b6C7d8,CPILOT,21750,true,2026-06-16
`;

/**
 * Build the `airdrop-claims.csv` fixture as a `File` stamped `text/csv`.
 * Synchronous (the bytes are already in the bundle), but typed `Promise<File>`
 * so it drops straight into the composer's async `resolveAttachment` port.
 */
export function resolveAirdropClaimsCsv(): Promise<File> {
  const file = new File(
    [AIRDROP_CLAIMS_CSV_TEXT],
    AIRDROP_CLAIMS_CSV_FILENAME,
    {
      type: AIRDROP_CLAIMS_CSV_MIME,
    },
  );
  return Promise.resolve(file);
}
