import type {
  Attachment,
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "../types";
import { fileMatchesAccept } from "./match";

/**
 * Atlas composite attachment adapter. Dispatches `add` / `send` / `remove`
 * to the first sub-adapter whose `accept` string matches the file. A single
 * wildcard adapter (`accept = "*"`) is allowed only as the final entry
 * (catch-all); placing it earlier shadows real adapters and is rejected
 * at construction so the misconfiguration is visible in dev.
 */
export class AtlasCompositeAttachmentAdapter implements AttachmentAdapter {
  public accept: string;
  private readonly adapters: readonly AttachmentAdapter[];

  constructor(adapters: readonly AttachmentAdapter[]) {
    this.adapters = adapters;
    const wildcardIndex = adapters.findIndex(
      (adapter) => adapter.accept === "*",
    );
    if (wildcardIndex !== -1) {
      if (wildcardIndex !== adapters.length - 1) {
        throw new Error(
          "A wildcard adapter (accept = '*') can only be the last adapter.",
        );
      }
      this.accept = "*";
    } else {
      this.accept = adapters.map((adapter) => adapter.accept).join(",");
    }
  }

  public add(state: { file: File }): ReturnType<AttachmentAdapter["add"]> {
    for (const adapter of this.adapters) {
      if (fileMatchesAccept(state.file, adapter.accept)) {
        return adapter.add(state);
      }
    }
    throw new Error("No matching attachment adapter for file");
  }

  public async send(
    attachment: PendingAttachment,
  ): Promise<CompleteAttachment> {
    for (const adapter of this.adapters) {
      if (fileMatchesAccept(attachment.file, adapter.accept)) {
        return adapter.send(attachment);
      }
    }
    throw new Error("No matching attachment adapter for attachment");
  }

  public async remove(attachment: Attachment): Promise<void> {
    for (const adapter of this.adapters) {
      const fileLike = {
        name: attachment.name,
        type: attachment.contentType ?? "",
      };
      if (fileMatchesAccept(fileLike, adapter.accept)) {
        return adapter.remove(attachment);
      }
    }
    throw new Error("No matching attachment adapter for attachment");
  }
}
