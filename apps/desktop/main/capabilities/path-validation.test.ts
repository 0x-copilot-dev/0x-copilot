// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  assertWithinRoot,
  FS_LIMITS,
  FsError,
  modeSatisfies,
  normalizeVirtualPath,
} from "./path-validation";

// Control / confusable codepoints are spelled with \u escapes so NO literal
// control character is embedded in this source file (mirrors folder-picker's
// String.fromCharCode discipline).
const NUL = "\u0000";
const BELL = "\u0007";
const FULLWIDTH_SOLIDUS = "／"; // ／  NFKC → "/"
const FULLWIDTH_DOT = "．"; // ．  NFKC → "."
const LONE_SURROGATE = "\uD800"; // unpaired high surrogate

function code(fn: () => unknown): string | "no-throw" {
  try {
    fn();
    return "no-throw";
  } catch (err) {
    return err instanceof FsError ? err.code : `other:${String(err)}`;
  }
}

describe("normalizeVirtualPath — happy paths", () => {
  it("splits an ordinary relative path into segments", () => {
    expect(normalizeVirtualPath("a/b/c")).toEqual(["a", "b", "c"]);
  });
  it("treats empty / '.' / '/'-collapsed input as the grant root", () => {
    expect(normalizeVirtualPath("")).toEqual([]);
    expect(normalizeVirtualPath(".")).toEqual([]);
    expect(normalizeVirtualPath("./")).toEqual([]);
  });
  it("drops '.' no-op segments and collapses repeated separators", () => {
    expect(normalizeVirtualPath("a/./b")).toEqual(["a", "b"]);
    expect(normalizeVirtualPath("a//b")).toEqual(["a", "b"]);
  });
  it("accepts backslash as a separator too", () => {
    expect(normalizeVirtualPath("a\\b")).toEqual(["a", "b"]);
  });
  it("accepts ordinary names with spaces and dots inside", () => {
    expect(normalizeVirtualPath("My Notes/report.v2.txt")).toEqual([
      "My Notes",
      "report.v2.txt",
    ]);
  });
});

describe("normalizeVirtualPath — adversarial rejections", () => {
  it("rejects a NUL byte", () => {
    expect(code(() => normalizeVirtualPath(`a${NUL}b`))).toBe("invalid_path");
  });
  it("rejects a C0 control character", () => {
    expect(code(() => normalizeVirtualPath(`a${BELL}b`))).toBe("invalid_path");
  });
  it("rejects absolute POSIX paths", () => {
    expect(code(() => normalizeVirtualPath("/etc/passwd"))).toBe(
      "invalid_path",
    );
  });
  it("rejects UNC and backslash-absolute paths", () => {
    expect(code(() => normalizeVirtualPath("\\\\server\\share"))).toBe(
      "invalid_path",
    );
  });
  it("rejects Windows drive-letter paths", () => {
    expect(code(() => normalizeVirtualPath("C:\\Windows"))).toBe(
      "invalid_path",
    );
    expect(code(() => normalizeVirtualPath("c:/Windows"))).toBe("invalid_path");
  });
  it("rejects .. traversal in every position", () => {
    expect(code(() => normalizeVirtualPath(".."))).toBe("invalid_path");
    expect(code(() => normalizeVirtualPath("a/../b"))).toBe("invalid_path");
    expect(code(() => normalizeVirtualPath("a/.."))).toBe("invalid_path");
    expect(code(() => normalizeVirtualPath("../etc"))).toBe("invalid_path");
  });
  it("rejects a Unicode-confusable separator (fullwidth solidus)", () => {
    expect(code(() => normalizeVirtualPath(`a${FULLWIDTH_SOLIDUS}b`))).toBe(
      "invalid_path",
    );
  });
  it("rejects a Unicode-confusable '..' (fullwidth full stops)", () => {
    expect(
      code(() => normalizeVirtualPath(`${FULLWIDTH_DOT}${FULLWIDTH_DOT}`)),
    ).toBe("invalid_path");
  });
  it("rejects a lone surrogate (bad encoding)", () => {
    expect(code(() => normalizeVirtualPath(`a${LONE_SURROGATE}b`))).toBe(
      "invalid_path",
    );
  });
  it("rejects Windows reserved device names (with and without extension)", () => {
    for (const name of ["CON", "nul", "com3", "LPT9", "nul.txt", "COM1.log"]) {
      expect(code(() => normalizeVirtualPath(name))).toBe("invalid_path");
    }
  });
  it("rejects a reserved device name in an interior segment", () => {
    expect(code(() => normalizeVirtualPath("a/PRN/b"))).toBe("invalid_path");
  });
  it("rejects alternate-data-stream / colon segments", () => {
    expect(code(() => normalizeVirtualPath("file.txt:stream"))).toBe(
      "invalid_path",
    );
    expect(code(() => normalizeVirtualPath("a/b:c"))).toBe("invalid_path");
  });
  it("rejects trailing dot or space (Windows silently strips them)", () => {
    expect(code(() => normalizeVirtualPath("secret."))).toBe("invalid_path");
    expect(code(() => normalizeVirtualPath("secret "))).toBe("invalid_path");
    expect(code(() => normalizeVirtualPath("a/b./c"))).toBe("invalid_path");
  });
  it("rejects a non-string input", () => {
    expect(code(() => normalizeVirtualPath(42 as unknown))).toBe(
      "invalid_path",
    );
    expect(code(() => normalizeVirtualPath(null))).toBe("invalid_path");
  });
  it("rejects a path deeper than the depth ceiling", () => {
    const deep = Array.from(
      { length: FS_LIMITS.maxPathDepth + 1 },
      () => "x",
    ).join("/");
    expect(code(() => normalizeVirtualPath(deep))).toBe("invalid_path");
  });
  it("rejects an over-long segment", () => {
    expect(code(() => normalizeVirtualPath("x".repeat(256)))).toBe(
      "invalid_path",
    );
  });
  it("never echoes the offending input in the error message", () => {
    try {
      normalizeVirtualPath("/Users/secret-person/private");
      throw new Error("should have thrown");
    } catch (err) {
      expect((err as Error).message).not.toContain("secret-person");
    }
  });
});

describe("assertWithinRoot", () => {
  it("allows the root itself and any descendant", () => {
    expect(() => assertWithinRoot("/grant/root", "/grant/root")).not.toThrow();
    expect(() =>
      assertWithinRoot("/grant/root", "/grant/root/a/b"),
    ).not.toThrow();
  });
  it("rejects a sibling prefix (the /root vs /root-evil trap)", () => {
    expect(
      code(() => assertWithinRoot("/grant/root", "/grant/root-evil")),
    ).toBe("permission_denied");
  });
  it("rejects an unrelated path and a parent path", () => {
    expect(code(() => assertWithinRoot("/grant/root", "/etc/passwd"))).toBe(
      "permission_denied",
    );
    expect(code(() => assertWithinRoot("/grant/root", "/grant"))).toBe(
      "permission_denied",
    );
  });
});

describe("modeSatisfies (fail-closed grant gate)", () => {
  it("read_only is satisfied by every mode", () => {
    expect(modeSatisfies("read_only", "read_only")).toBe(true);
    expect(modeSatisfies("read_only", "read_write_no_delete")).toBe(true);
    expect(modeSatisfies("read_only", "read_write")).toBe(true);
  });
  it("a higher required mode denies a lower grant", () => {
    expect(modeSatisfies("read_write", "read_only")).toBe(false);
    expect(modeSatisfies("read_write_no_delete", "read_only")).toBe(false);
    expect(modeSatisfies("read_write", "read_write_no_delete")).toBe(false);
  });
  it("an unknown mode never satisfies anything (fail closed)", () => {
    expect(modeSatisfies("read_only", "bogus")).toBe(false);
    expect(modeSatisfies("bogus", "read_write")).toBe(false);
  });
});
