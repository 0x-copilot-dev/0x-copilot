// Host (web) file picker for the composer `+` menu. Extracted out of
// `AssistantComposer.tsx` (PR-1.3): the composer shell now lives in
// `@0x-copilot/chat-surface` and routes attachment picking through an
// injected `FilePickerPort` instead of touching `document` directly.
//
// This implementation reproduces the composer's original hidden
// `<input type="file">` picker **byte-for-byte** and — unlike the generic
// `WebFilePickerPort` (src/ports/FilePickerWeb.ts) — returns the real `File`
// objects. The composer's runtime attachment adapters read the picked file
// via `FileReader.readAsDataURL(file)` and key on `file.lastModified`, so a
// stream-only `FilePickerSelection` would break them; `File` is a structural
// superset of `FilePickerSelection`, so the moved core downcasts each
// selection to `File`.

import type {
  FilePickerOptions,
  FilePickerPort,
  FilePickerSelection,
} from "@0x-copilot/chat-surface";

export class ComposerFilePicker implements FilePickerPort {
  pick(
    options: FilePickerOptions,
  ): Promise<ReadonlyArray<FilePickerSelection>> {
    if (typeof document === "undefined") {
      return Promise.resolve([]);
    }
    return new Promise<ReadonlyArray<File>>((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.multiple = options.multiple ?? false;
      if (options.accept && options.accept.length > 0) {
        input.accept = options.accept.join(",");
      }
      input.hidden = true;
      document.body.appendChild(input);
      input.onchange = () => {
        const out: File[] = [];
        const files = input.files;
        if (files) {
          for (const file of files) {
            out.push(file);
          }
        }
        input.remove();
        resolve(out);
      };
      input.oncancel = () => {
        input.remove();
        resolve([]);
      };
      input.click();
    });
  }
}
