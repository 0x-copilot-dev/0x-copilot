// Single-stage attachment adapter for the desktop Run composer.
//
// The shared `AssistantComposer` renders attachment pills and forwards the
// resolved `CompleteAttachment[]` on submit. The web host bridges a two-stage
// runtime upload adapter; desktop does not yet have that runtime family wired,
// so this is a **single-stage** adapter: `add(file)` reads the file into a data
// URL and resolves a `CompleteAttachment` directly (no `send` step). The
// content part matches the runtime's shape — `{ type: "image", image }` for
// images, `{ type: "file", … }` otherwise — so a submit payload carries a
// well-formed block the message pipeline can read.
//
// Deferred (fast-follow): the true two-stage upload finalization
// (content-addressable blob + `send`) that the web runtime adapters perform.
// Until then large binaries ride inline as data URLs; the accept filter keeps
// this to images + small docs.

import type {
  AttachmentAdapter,
  AttachmentContentPart,
  CompleteAttachment,
} from "@0x-copilot/chat-surface";

function newId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }
  // Fallback id — uniqueness within a single composer session is enough for
  // the pill key + remove routing.
  return `att-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e6).toString(36)}`;
}

/**
 * Recover a MIME type from a filename extension. Some `File` sources leave
 * `file.type` empty — notably a `File` built from bundled bytes without an
 * explicit `type` (the browser only stamps a MIME when the OS/picker supplies
 * one). Mirrors the web adapter's `mimeTypeForFileName` so an empty-MIME
 * `.csv` (the FTUE "Explain a CSV" chip) still carries `text/csv` on the wire
 * instead of an empty `mime_type`. Falls back to `""` for unknown extensions.
 */
function mimeTypeForFileName(fileName: string): string {
  const extension = fileName.split(".").pop()?.toLowerCase();
  switch (extension) {
    case "csv":
      return "text/csv";
    case "pdf":
      return "application/pdf";
    case "doc":
      return "application/msword";
    case "docx":
      return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    case "xls":
      return "application/vnd.ms-excel";
    case "xlsx":
      return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
    case "ppt":
      return "application/vnd.ms-powerpoint";
    case "pptx":
      return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
    default:
      return "";
  }
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () =>
      reject(reader.error ?? new Error("Failed to read file"));
    reader.readAsDataURL(file);
  });
}

/**
 * A single-stage `AttachmentAdapter` for the desktop composer. Images become an
 * `{ type: "image", image: <dataUrl> }` content part; everything else — office
 * docs, PDFs, and CSVs (the FTUE "Explain a CSV" chip) — becomes an
 * `{ type: "file", name, mime, data: <dataUrl> }` part. When `file.type` is
 * empty the MIME is recovered from the filename extension so a CSV still rides
 * with `mime: text/csv`.
 */
export function createDesktopAttachmentAdapter(): AttachmentAdapter {
  return {
    async add(file: File): Promise<CompleteAttachment> {
      const dataUrl = await readAsDataUrl(file);
      const isImage = file.type.startsWith("image/");
      const mime = file.type || mimeTypeForFileName(file.name);
      const content: AttachmentContentPart = isImage
        ? { type: "image", image: dataUrl }
        : { type: "file", name: file.name, mime, data: dataUrl };
      return {
        id: newId(),
        name: file.name,
        size: file.size,
        type: mime,
        content: [content],
        status: { type: "complete" },
      };
    },
    // Single-stage: nothing is persisted server-side to tear down, so remove is
    // a no-op (the Composer drops the pill from its local set on its own).
    remove(_id: string): void {
      /* no-op — nothing to release for an inline single-stage attachment */
    },
  };
}
