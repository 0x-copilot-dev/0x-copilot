import type {
  Attachment,
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "../types";

/**
 * Atlas image-attachment adapter. Owns image attachments end-to-end —
 * accepts any `image/*` MIME, holds the `File` until the composer
 * dispatches `send`, then resolves a base64 data URL for transport.
 */
export class AtlasImageAttachmentAdapter implements AttachmentAdapter {
  public accept = "image/*";

  public async add({ file }: { file: File }): Promise<PendingAttachment> {
    return {
      id: file.name,
      type: "image",
      name: file.name,
      contentType: file.type,
      file,
      status: { type: "requires-action", reason: "composer-send" },
    };
  }

  public async send(
    attachment: PendingAttachment,
  ): Promise<CompleteAttachment> {
    return {
      ...attachment,
      status: { type: "complete" },
      content: [
        {
          type: "image",
          image: await readFileAsDataURL(attachment.file),
        },
      ],
    };
  }

  public async remove(_attachment: Attachment): Promise<void> {
    return undefined;
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
