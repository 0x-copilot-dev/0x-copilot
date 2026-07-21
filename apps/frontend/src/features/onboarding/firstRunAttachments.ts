// First-run attachment mapping (web): composer `CompleteAttachment[]` →
// `RunAttachmentRequest[]`, with the FTUE "Explain a CSV" reconcile.
//
// The web onboarding attachment adapter (`createOnboardingAttachmentAdapter`)
// turns the picked/chip CSV into an inline base64 data-URL `file` content part
// (`{ type:"file", filename, data, mimeType }`). For the FTUE that isn't
// enough: a base64 `file` part is summarised by name/size only — the model
// never sees the rows (per `FirstRunRunsPort` / `SuggestionChips` docs). So
// here, on the way to the run body, a **text-like** file part (the `text/csv`
// airdrop-claims fixture) is decoded and re-emitted as a model-READABLE
// `{ type:"text", text }` content part. Non-text attachments (images, PDFs,
// office docs) keep the ChatScreen `file`/`image` wire shape unchanged.
//
// This mirrors the desktop `firstRunAttachments`, adjusted for the web runtime
// content-part shape (`filename`/`mimeType`, vs the desktop adapter's
// `name`/`mime`); the CSV rows must be model-visible for the "chart the top
// movers" prompt to work.

import type {
  RunAttachmentRequest,
  RunContentPart,
} from "@0x-copilot/api-types";

import type {
  AttachmentContentPart,
  CompleteAttachment,
  FileContentPart,
} from "../chat/runtime/types";

/** Text-like MIME → route the bytes as a readable `text` part. `text/csv`
 *  (the airdrop-claims chip) is the one that matters for v1. */
function isTextLikeMime(mime: string): boolean {
  return mime.startsWith("text/");
}

function base64ToUtf8(base64: string): string {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new TextDecoder().decode(bytes);
}

/**
 * Decode a `data:` URL to its text payload. Handles both the base64 form
 * (`FileReader.readAsDataURL` output — `data:text/csv;base64,…`) and the rare
 * URL-encoded form. A non-data-URL string is returned as-is.
 */
export function dataUrlToText(dataUrl: string): string {
  if (!dataUrl.startsWith("data:")) {
    return dataUrl;
  }
  const comma = dataUrl.indexOf(",");
  if (comma < 0) {
    return dataUrl;
  }
  const meta = dataUrl.slice(5, comma);
  const payload = dataUrl.slice(comma + 1);
  if (/;base64/i.test(meta)) {
    return base64ToUtf8(payload);
  }
  try {
    return decodeURIComponent(payload);
  } catch {
    return payload;
  }
}

function toReadableContentPart(part: AttachmentContentPart): RunContentPart {
  if (part.type === "file") {
    const file = part as FileContentPart;
    const mime = String(file.mimeType ?? "");
    const data = String(file.data ?? "");
    if (isTextLikeMime(mime)) {
      // Model-READABLE: inline the decoded rows as a `text` part so the runtime
      // worker actually reads them — NOT a base64 `file` part (model-invisible).
      return { type: "text", text: dataUrlToText(data) };
    }
    // Binary file (PDF, office doc) → ChatScreen's `file` wire shape unchanged.
    return {
      type: "file",
      filename: String(file.filename ?? ""),
      data,
      mime_type: mime,
    };
  }
  // Image parts (`{ type:"image", image }`) and any already-text parts pass
  // through unchanged (opaque payload the runtime pipeline reads directly).
  return { ...part } as unknown as RunContentPart;
}

/** Map one composer attachment to a run-attachment request, routing text-like
 *  files to readable `text` content parts. */
export function toReadableRunAttachment(
  att: CompleteAttachment,
): RunAttachmentRequest {
  return {
    id: att.id,
    type: att.type,
    name: att.name,
    content_type: att.contentType ?? null,
    size: att.file?.size ?? null,
    content: (att.content ?? []).map(toReadableContentPart),
  };
}

/** Map the composer's submit `attachments` (opaque `CompleteAttachment[]`) to
 *  the run-create wire shape. */
export function toReadableRunAttachments(
  attachments: ReadonlyArray<unknown>,
): RunAttachmentRequest[] {
  return (attachments as ReadonlyArray<CompleteAttachment>).map(
    toReadableRunAttachment,
  );
}
