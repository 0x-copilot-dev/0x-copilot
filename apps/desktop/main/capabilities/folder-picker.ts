import { realpath, stat } from "node:fs/promises";
import { basename } from "node:path";

// Native folder picker (AC5 slice 1). Main owns the path end to end: it opens
// the OS "choose directory" dialog, canonicalizes the selection with realpath
// (collapsing symlinks), and confirms it is a real directory. The renderer
// never submits a path and never receives one back — it gets `label` (a
// sanitized basename) which the caller maps into a grant.

/** Generic failure that never carries the offending host path in its message. */
export class FolderPickerError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FolderPickerError";
  }
}

export interface PickedFolder {
  /** Canonical absolute directory (symlinks resolved). */
  readonly root: string;
  /** Sanitized display label derived from the folder's basename. */
  readonly label: string;
}

/** Mirrors the fields of Electron's `dialog.showOpenDialog` result we use. */
export interface ShowOpenDialogResult {
  readonly canceled: boolean;
  readonly filePaths: readonly string[];
}

export interface FolderPickerDeps {
  /** Main binds this to `dialog.showOpenDialog({ properties:['openDirectory'] })`. */
  showOpenDialog(): Promise<ShowOpenDialogResult>;
  /** Defaults to node:fs/promises realpath; injectable for tests. */
  realpath?: (path: string) => Promise<string>;
  /** Defaults to node:fs/promises stat; injectable for tests. */
  stat?: (path: string) => Promise<{ isDirectory(): boolean }>;
}

export class FolderPicker {
  readonly #showOpenDialog: () => Promise<ShowOpenDialogResult>;
  readonly #realpath: (path: string) => Promise<string>;
  readonly #stat: (path: string) => Promise<{ isDirectory(): boolean }>;

  constructor(deps: FolderPickerDeps) {
    this.#showOpenDialog = deps.showOpenDialog;
    this.#realpath = deps.realpath ?? realpath;
    this.#stat = deps.stat ?? stat;
  }

  /**
   * Open the picker. Returns null when the user cancels (or selects nothing).
   * Throws `FolderPickerError` when the selection cannot be resolved or is not
   * a directory. The returned `root` is the realpath — never the raw dialog
   * value.
   */
  async pick(): Promise<PickedFolder | null> {
    const result = await this.#showOpenDialog();
    if (result.canceled || result.filePaths.length === 0) {
      return null;
    }
    const selected = result.filePaths[0];
    if (typeof selected !== "string" || selected.length === 0) {
      throw new FolderPickerError("picker returned an empty selection");
    }

    let canonical: string;
    try {
      canonical = await this.#realpath(selected);
    } catch {
      // ENOENT (raced away) or a broken symlink — do not leak the path.
      throw new FolderPickerError("selected path could not be resolved");
    }

    let info: { isDirectory(): boolean };
    try {
      info = await this.#stat(canonical);
    } catch {
      throw new FolderPickerError("selected path could not be inspected");
    }
    if (!info.isDirectory()) {
      throw new FolderPickerError("selection is not a directory");
    }

    return { root: canonical, label: sanitizeLabel(basename(canonical)) };
  }
}

/**
 * Reduce an arbitrary string to a safe, bounded display label. Drops C0/C1
 * control characters, DEL, and path separators (defense in depth — the input
 * is only a basename), collapses whitespace, and caps length. Falls back to
 * "folder" when nothing printable survives. Codepoint-based so no control
 * characters are embedded in source.
 */
export function sanitizeLabel(raw: string): string {
  let out = "";
  for (const ch of raw) {
    const code = ch.codePointAt(0) ?? 0;
    const isControl = code < 0x20 || (code >= 0x7f && code <= 0x9f);
    if (isControl || ch === "/" || ch === "\\") {
      out += " ";
    } else {
      out += ch;
    }
  }
  const collapsed = out.replace(/\s+/gu, " ").trim().slice(0, 120);
  return collapsed.length > 0 ? collapsed : "folder";
}
