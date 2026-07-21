import type {
  Attachment,
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "../types";

/**
 * Atlas generic-file adapter. Carries office-document, PDF, and CSV
 * attachments through the composer as inline base64 data-URL `file`
 * content parts (no server upload). Mirrors the implementation that
 * previously lived inline in `ChatScreen.tsx`; the reason it lives in
 * its own module now is so the runtime layer owns the full
 * attachment-adapter surface (no more inline classes).
 *
 * `text/csv` (+ the `.csv` extension) is accepted here — in addition to
 * the office/PDF set — so the FTUE "Explain a CSV" starter chip can
 * pre-attach `airdrop-claims.csv` as a `file` part with
 * `mime_type: text/csv`. Note the ChatScreen composite lists the text
 * adapter before this one, so an OS-picked `text/csv` file still routes
 * to the text adapter (unchanged); the FTUE onboarding composite
 * (`createOnboardingAttachmentAdapter`) lists this file adapter first so
 * the chip's CSV lands as a data-URL `file` part instead.
 */
const FILE_ACCEPT =
  "application/pdf," +
  "application/msword," +
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document," +
  "application/vnd.ms-excel," +
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet," +
  "application/vnd.ms-powerpoint," +
  "application/vnd.openxmlformats-officedocument.presentationml.presentation," +
  "text/csv," +
  ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.csv";

export class AtlasFileAttachmentAdapter implements AttachmentAdapter {
  public accept = FILE_ACCEPT;

  public async add({ file }: { file: File }): Promise<PendingAttachment> {
    return {
      id: `${file.name}-${file.lastModified}`,
      type: "file",
      name: file.name,
      contentType: file.type || mimeTypeForFileName(file.name),
      file,
      status: { type: "requires-action", reason: "composer-send" },
    };
  }

  public async send(
    attachment: PendingAttachment,
  ): Promise<CompleteAttachment> {
    const data = await readFileAsDataURL(attachment.file);
    return {
      ...attachment,
      status: { type: "complete" },
      content: [
        {
          type: "file",
          filename: attachment.name,
          data,
          mimeType:
            attachment.contentType ||
            mimeTypeForFileName(attachment.name) ||
            "application/octet-stream",
        },
      ],
    };
  }

  public async remove(_attachment: Attachment): Promise<void> {
    return undefined;
  }
}

export function mimeTypeForFileName(fileName: string): string {
  const extension = fileName.split(".").pop()?.toLowerCase();
  switch (extension) {
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
    case "csv":
      return "text/csv";
    default:
      return "";
  }
}

function readFileAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = (error) => reject(error);
    reader.readAsDataURL(file);
  });
}
