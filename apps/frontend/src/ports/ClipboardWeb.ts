// Web ClipboardPort — wraps `navigator.clipboard.writeText`.
//
// Source: cross-audit.md §1.2 + chats-canvas-prd §5.4. Falls back to a
// rejected promise when the substrate lacks a secure context or the
// Clipboard API entirely; the caller's UX surfaces success/failure via
// its own toast (per the ClipboardPort doc comment).

import type { ClipboardPort } from "@0x-copilot/chat-surface";

export class WebClipboardPort implements ClipboardPort {
  async copyText(text: string): Promise<void> {
    if (typeof navigator === "undefined" || navigator.clipboard === undefined) {
      throw new Error("clipboard unavailable: navigator.clipboard not present");
    }
    await navigator.clipboard.writeText(text);
  }
}
