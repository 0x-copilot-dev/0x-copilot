// Host (desktop) file picker for the composer `+` menu.
//
// The Run cockpit mounts the shared `AssistantComposer`
// (@0x-copilot/chat-surface), whose Attach Image / Attach File actions route
// through an injected `FilePickerPort`. The composer's attachment adapter reads
// the picked file via `FileReader.readAsDataURL(file)`, so — like the web
// `ComposerFilePicker` — this returns the real `File` objects rather than the
// stream-only `FilePickerSelection` quartet. `File` is a structural superset of
// `FilePickerSelection`, so the composer downcasts each selection to `File`.
//
// The Electron renderer has DOM, so this reproduces the web hidden
// `<input type="file">` picker. The substrate-agnostic package never touches
// `document`; this DOM-bound piece lives host-side (apps/desktop), which is the
// point of the port.

import type {
  FilePickerOptions,
  FilePickerPort,
  FilePickerSelection,
} from "@0x-copilot/chat-surface";

export class DesktopComposerFilePicker implements FilePickerPort {
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
