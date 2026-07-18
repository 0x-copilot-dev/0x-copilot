// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import {
  FolderPicker,
  FolderPickerError,
  sanitizeLabel,
  type ShowOpenDialogResult,
} from "./folder-picker";

function dirStat() {
  return { isDirectory: () => true };
}
function fileStat() {
  return { isDirectory: () => false };
}

function hasControlChar(s: string): boolean {
  return [...s].some((c) => {
    const code = c.codePointAt(0) ?? 0;
    return code < 0x20 || (code >= 0x7f && code <= 0x9f);
  });
}

describe("FolderPicker", () => {
  it("returns the realpath as root and a sanitized basename label", async () => {
    const picker = new FolderPicker({
      showOpenDialog: async (): Promise<ShowOpenDialogResult> => ({
        canceled: false,
        filePaths: ["/Users/me/link-to-proj"],
      }),
      realpath: async () => "/Volumes/Data/Projects/atlas",
      stat: async () => dirStat(),
    });
    const picked = await picker.pick();
    expect(picked).not.toBeNull();
    expect(picked!.root).toBe("/Volumes/Data/Projects/atlas");
    expect(picked!.label).toBe("atlas");
  });

  it("canonicalizes symlinks — root is the resolved path, never the raw selection", async () => {
    const realpath = vi.fn(async () => "/real/canonical/target");
    const picker = new FolderPicker({
      showOpenDialog: async () => ({
        canceled: false,
        filePaths: ["/tmp/symlink"],
      }),
      realpath,
      stat: async () => dirStat(),
    });
    const picked = await picker.pick();
    expect(realpath).toHaveBeenCalledWith("/tmp/symlink");
    expect(picked!.root).toBe("/real/canonical/target");
  });

  it("returns null when the user cancels", async () => {
    const picker = new FolderPicker({
      showOpenDialog: async () => ({ canceled: true, filePaths: [] }),
    });
    expect(await picker.pick()).toBeNull();
  });

  it("returns null when no path was selected", async () => {
    const picker = new FolderPicker({
      showOpenDialog: async () => ({ canceled: false, filePaths: [] }),
    });
    expect(await picker.pick()).toBeNull();
  });

  it("rejects a selection that cannot be resolved, without leaking the path", async () => {
    const picker = new FolderPicker({
      showOpenDialog: async () => ({
        canceled: false,
        filePaths: ["/Users/secret-person/gone"],
      }),
      realpath: async () => {
        throw new Error("ENOENT: no such file, /Users/secret-person/gone");
      },
      stat: async () => dirStat(),
    });
    await expect(picker.pick()).rejects.toBeInstanceOf(FolderPickerError);
    await picker.pick().catch((err: unknown) => {
      expect((err as Error).message).not.toContain("secret-person");
    });
  });

  it("rejects a non-directory selection", async () => {
    const picker = new FolderPicker({
      showOpenDialog: async () => ({
        canceled: false,
        filePaths: ["/etc/hosts"],
      }),
      realpath: async () => "/etc/hosts",
      stat: async () => fileStat(),
    });
    await expect(picker.pick()).rejects.toThrow(/not a directory/u);
  });

  it("sanitizes control characters in the derived label", async () => {
    // basename with an embedded newline (0x0A) — constructed via fromCharCode
    // so no control character is embedded in source.
    const controlName = `we${String.fromCharCode(10)}ird`;
    const picker = new FolderPicker({
      showOpenDialog: async () => ({ canceled: false, filePaths: ["/x"] }),
      realpath: async () => `/data/${controlName}`,
      stat: async () => dirStat(),
    });
    const picked = await picker.pick();
    expect(picked!.label).toBe("we ird");
    expect(hasControlChar(picked!.label)).toBe(false);
  });
});

describe("sanitizeLabel", () => {
  it("strips path separators and collapses whitespace", () => {
    expect(sanitizeLabel("a/b\\c   d")).toBe("a b c d");
  });
  it("falls back to 'folder' when nothing printable survives", () => {
    expect(sanitizeLabel(" ")).toBe("folder");
  });
  it("caps length at 120 characters", () => {
    expect(sanitizeLabel("x".repeat(500)).length).toBe(120);
  });
});
