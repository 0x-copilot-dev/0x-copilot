import type {
  Attachment,
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "../types";

/**
 * Atlas generic-file adapter. Carries office-document and PDF
 * attachments through the composer. Mirrors the implementation that
 * previously lived inline in `ChatScreen.tsx` so behavior is unchanged;
 * the reason it lives in its own module now is so the runtime layer
 * owns the full attachment-adapter surface (no more inline classes).
 */
const OFFICE_ACCEPT =
  "application/pdf," +
  "application/msword," +
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document," +
  "application/vnd.ms-excel," +
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet," +
  "application/vnd.ms-powerpoint," +
  "application/vnd.openxmlformats-officedocument.presentationml.presentation," +
  ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx";

export class AtlasFileAttachmentAdapter implements AttachmentAdapter {
  public accept = OFFICE_ACCEPT;

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
