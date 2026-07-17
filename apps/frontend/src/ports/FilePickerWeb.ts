// Web FilePickerPort — wraps a hidden `<input type="file">`.
//
// Source: cross-audit.md §1.2 + chats-canvas-prd §5.4. The contract is
// substrate-agnostic: destinations get a `ReadonlyArray<FilePickerSelection>`,
// each exposing `name / size / type / stream()`. They never see a `File`
// (which is web-only). The stream() method calls `File.stream()` so
// destinations consume bytes the same way on every substrate.

import type {
  FilePickerOptions,
  FilePickerPort,
  FilePickerSelection,
} from "@0x-copilot/chat-surface";

export class WebFilePickerPort implements FilePickerPort {
  pick(
    options: FilePickerOptions,
  ): Promise<ReadonlyArray<FilePickerSelection>> {
    if (typeof document === "undefined") {
      return Promise.resolve([]);
    }
    return new Promise<ReadonlyArray<FilePickerSelection>>((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.multiple = options.multiple ?? false;
      if (options.accept && options.accept.length > 0) {
        input.accept = options.accept.join(",");
      }
      input.hidden = true;
      input.style.display = "none";

      const cleanup = (): void => {
        // Defer remove() to a microtask so the change event listener
        // doesn't see a detached node mid-dispatch on Safari.
        Promise.resolve().then(() => {
          if (input.parentNode !== null) {
            input.parentNode.removeChild(input);
          }
        });
      };

      input.addEventListener("change", () => {
        const files = input.files;
        if (files === null) {
          cleanup();
          resolve([]);
          return;
        }
        const out: FilePickerSelection[] = [];
        for (let i = 0; i < files.length; i += 1) {
          const file = files.item(i);
          if (file === null) continue;
          out.push(toSelection(file));
        }
        cleanup();
        resolve(out);
      });
      // `cancel` is a modern event (Chromium 113+, Firefox 91+) that
      // fires when the user dismisses the picker. We treat it as an
      // empty selection. Browsers without `cancel` simply leave the
      // promise pending until garbage collection — acceptable since the
      // caller's UX (a single picker invocation) won't open another
      // picker while this one is unresolved.
      input.addEventListener("cancel", () => {
        cleanup();
        resolve([]);
      });

      document.body.appendChild(input);
      input.click();
    });
  }
}

function toSelection(file: File): FilePickerSelection {
  return {
    name: file.name,
    size: file.size,
    type: file.type,
    stream: () => file.stream(),
  };
}
