import { describe, expect, it } from "vitest";

import {
  SYNTAX_LANGUAGES,
  resolveSyntaxLanguage,
  syntaxLanguageForPath,
} from "./syntaxTokens";

describe("resolveSyntaxLanguage", () => {
  it("resolves a grammar we carry by its own name", () => {
    expect(resolveSyntaxLanguage("python")).toBe("python");
    expect(resolveSyntaxLanguage("  YAML ")).toBe("yaml");
  });

  it("folds the whole TypeScript/JavaScript family onto tsx", () => {
    // The consolidation the bounded set depends on: four fence hints, one
    // 181 KB grammar. If this ever splits back apart, the desktop bundle grows
    // by half a megabyte with nothing in the diff saying so.
    for (const hint of ["ts", "typescript", "js", "javascript", "jsx", "tsx"]) {
      expect(resolveSyntaxLanguage(hint)).toBe("tsx");
    }
  });

  it("returns null for an unknown or absent hint", () => {
    expect(resolveSyntaxLanguage("plaintext")).toBeNull();
    expect(resolveSyntaxLanguage("")).toBeNull();
    expect(resolveSyntaxLanguage(undefined)).toBeNull();
    expect(resolveSyntaxLanguage("brainfuck")).toBeNull();
  });

  it("resolves every alias onto a grammar the bounded set actually carries", () => {
    // Guards the shape of the bug where an alias points at a grammar nobody
    // loads: `tokenize` would keep returning null and the block would read as
    // "highlighting is broken" rather than "we do not carry that language".
    for (const language of SYNTAX_LANGUAGES) {
      expect(resolveSyntaxLanguage(language)).toBe(language);
    }
  });
});

describe("syntaxLanguageForPath", () => {
  it("reads the grammar off the extension", () => {
    expect(syntaxLanguageForPath("/a/b/TcChat.tsx")).toBe("tsx");
    expect(syntaxLanguageForPath("C:\\repo\\main.py")).toBe("python");
    expect(syntaxLanguageForPath("deploy/values.yml")).toBe("yaml");
    expect(syntaxLanguageForPath("app/styles.css")).toBe("css");
  });

  it("knows the extension-less files that are themselves a language", () => {
    expect(syntaxLanguageForPath("services/backend/Dockerfile")).toBe("docker");
    expect(syntaxLanguageForPath("Dockerfile.dev")).toBe("docker");
  });

  it("returns null when the path carries no usable evidence", () => {
    expect(syntaxLanguageForPath(undefined)).toBeNull();
    expect(syntaxLanguageForPath("/etc/hosts")).toBeNull();
    expect(syntaxLanguageForPath("/a/.gitignore")).toBeNull();
    expect(syntaxLanguageForPath("notes.bin")).toBeNull();
  });
});
