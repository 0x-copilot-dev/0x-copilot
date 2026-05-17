// FilePickerPort — substrate-agnostic file picker.
// Source: cross-audit.md §1.2 (binding 2026-05-17).
//
// The returned objects expose a `stream()` that yields the file bytes
// in chunks. Both substrates can satisfy this: web via
// `File.stream()`, desktop via a Node `ReadStream`-to-WebStream adapter
// in the main process. Destinations never see a `File` or a path
// string — only the `name / size / type / stream()` quartet.

export interface FilePickerSelection {
  readonly name: string;
  readonly size: number;
  /** MIME type as reported by the substrate; may be the empty string. */
  readonly type: string;
  /** A fresh `ReadableStream` each call; destinations consume once. */
  stream(): ReadableStream<Uint8Array>;
}

export interface FilePickerOptions {
  readonly multiple?: boolean;
  /** MIME types to filter the picker by (e.g. `["text/plain", "image/*"]`). */
  readonly accept?: ReadonlyArray<string>;
}

export interface FilePickerPort {
  /**
   * Open the substrate's file picker. Resolves with the user's
   * selection (possibly empty if they cancelled). The host implements
   * the underlying picker: web → `<input type="file">`, desktop →
   * native OS dialog via the main process.
   */
  pick(options: FilePickerOptions): Promise<ReadonlyArray<FilePickerSelection>>;
}
