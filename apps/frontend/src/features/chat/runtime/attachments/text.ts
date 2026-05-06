import type {
  Attachment,
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "../types";

/**
 * Atlas text-attachment adapter. Reads any `text/*` family file as
 * UTF-8 text and embeds it inline as a `<attachment>`-tagged text part
 * for the model. Mirrors the historic shape exactly so existing prompts
 * and golden tests continue to match.
 */
export class AtlasTextAttachmentAdapter implements AttachmentAdapter {
  public accept =
    "text/plain,text/html,text/markdown,text/csv,text/xml,text/json,text/css";

  public async add({ file }: { file: File }): Promise<PendingAttachment> {
    return {
      id: file.name,
      type: "document",
      name: file.name,
      contentType: file.type,
      file,
      status: { type: "requires-action", reason: "composer-send" },
    };
  }

  public async send(
    attachment: PendingAttachment,
  ): Promise<CompleteAttachment> {
    const body = await readFileAsText(attachment.file);
    return {
      ...attachment,
      status: { type: "complete" },
      content: [
        {
          type: "text",
          text: `<attachment name=${attachment.name}>\n${body}\n</attachment>`,
        },
      ],
    };
  }

  public async remove(_attachment: Attachment): Promise<void> {
    return undefined;
  }
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = (error) => reject(error);
    reader.readAsText(file);
  });
}
