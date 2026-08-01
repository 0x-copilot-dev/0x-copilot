import { lstat, realpath, stat } from "node:fs/promises";
import { basename, isAbsolute } from "node:path";

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
  /**
   * Defaults to node:fs/promises lstat; injectable for tests. Used only by
   * {@link FolderPicker.resolve}, which must be able to tell a directory from a
   * SYMLINK to one — `stat` cannot, because it follows the link.
   */
  lstat?: (path: string) => Promise<{ isDirectory(): boolean }>;
}

export class FolderPicker {
  readonly #showOpenDialog: () => Promise<ShowOpenDialogResult>;
  readonly #realpath: (path: string) => Promise<string>;
  readonly #stat: (path: string) => Promise<{ isDirectory(): boolean }>;
  readonly #lstat: (path: string) => Promise<{ isDirectory(): boolean }>;

  constructor(deps: FolderPickerDeps) {
    this.#showOpenDialog = deps.showOpenDialog;
    this.#realpath = deps.realpath ?? realpath;
    this.#stat = deps.stat ?? stat;
    this.#lstat = deps.lstat ?? lstat;
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

  /**
   * Confirm ONE named folder, with no dialog — the mid-run "always allow" path.
   *
   * The consent already happened: the backend raised a card naming this exact
   * folder and the user chose to attach it. Re-opening a picker there would ask
   * them to find it again and would let the answer land somewhere else, which is
   * the silent widening the card exists to prevent.
   *
   * WHAT IS DISPLAYED IS WHAT IS GRANTED. That is the whole difference between
   * this method and {@link pick}, and it is why this one CONFIRMS rather than
   * resolves. In `pick` the dialog is the consent — the user navigated to the
   * folder, so whatever it resolves to is what they chose. Here the consent is a
   * STRING on a card, rendered before this process ever touched the disk, and
   * the grant is minted afterwards. If resolution were allowed to move the
   * answer, the folder attached would not be the folder read: a symlink
   * re-pointed between render and click, or an ancestor that was a link all
   * along, silently substitutes a different tree.
   *
   * So the path is resolved EXACTLY ONCE and the resolution must be the
   * identity. The three refusals below are that one rule, spelled out:
   *
   * * **relative** — `realpath` would resolve it against this process's working
   *   directory, turning `~/Downloads` or `../etc` into an absolute path nobody
   *   displayed. Rejected before any syscall.
   * * **a symlink** — `lstat`, not `stat`: `stat` follows the link and would
   *   report the target as a directory, which is precisely the substitution.
   * * **resolving to anything else** — an ancestor symlink, a case-folded
   *   spelling, a trailing separator. All of them mean the grant would name a
   *   string the user did not read.
   *
   * A refusal costs one re-ask: the folder can still be approved once, or
   * attached from the folder bar where the dialog is the consent again.
   *
   * Grantability is NOT decided here; that has exactly one home
   * (`assertGrantableRoot`, applied by `GrantStore.create` and again by the
   * service before this method is reached).
   */
  async resolve(path: string): Promise<PickedFolder> {
    if (typeof path !== "string" || path.length === 0) {
      throw new FolderPickerError("no folder was named");
    }
    if (!isAbsolute(path)) {
      throw new FolderPickerError(
        "that folder has to be named by its full path",
      );
    }
    let named: { isDirectory(): boolean };
    try {
      named = await this.#lstat(path);
    } catch {
      // Gone, or a dangling link. Never echo the path.
      throw new FolderPickerError("that folder could not be found");
    }
    if (!named.isDirectory()) {
      // Either a file, or a symlink to one — `lstat` does not follow, so a link
      // to a real directory lands here too and is refused for the right reason.
      throw new FolderPickerError("that path is not a folder");
    }
    let canonical: string;
    try {
      canonical = await this.#realpath(path);
    } catch {
      throw new FolderPickerError("that folder could not be resolved");
    }
    if (canonical !== path) {
      throw new FolderPickerError(
        "that folder is a link to a different location — open it from where it really lives",
      );
    }
    // `canonical === path`, so this returns the string that was displayed. The
    // assignment is kept as `canonical` rather than `path` so that reintroducing
    // a second resolution has to delete the equality check above to compile a
    // different answer through — it cannot be done by accident.
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
