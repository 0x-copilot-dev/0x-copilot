// ClipboardPort — text-only clipboard write.
// Source: cross-audit.md §1.2 (binding 2026-05-17).
//
// Used by share-link copy, Routines webhook URL copy, and similar
// "copy this string" flows. Read is deliberately omitted (no
// destination needs it; reading without explicit user consent is also
// a privacy concern on every platform).

export interface ClipboardPort {
  /**
   * Write `text` to the system clipboard. Resolves when the write
   * completes; rejects when the substrate can't satisfy the request
   * (e.g. web without secure context or insufficient permissions).
   * Destinations surface success/failure via their own UI toast.
   */
  copyText(text: string): Promise<void>;
}
