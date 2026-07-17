// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  staticAnalyze,
  type AstAllowlistChecker,
  type Violation,
} from "./allowlist";

// Test checker that mimics 6A's AST scanner — recognizes a small set of
// banned constructs by string match so Q2's gating logic can be verified
// independently of the real parser implementation.
function fakeChecker(): AstAllowlistChecker {
  return {
    check(source: string) {
      const violations: Violation[] = [];
      const bannedGlobals = ["fetch", "XMLHttpRequest", "WebSocket", "window"];
      for (const g of bannedGlobals) {
        const re = new RegExp(`\\b${g}\\b`);
        if (re.test(source)) {
          violations.push({
            kind: "global",
            message: `banned global: ${g}`,
          });
        }
      }
      if (/\beval\s*\(/.test(source)) {
        violations.push({ kind: "eval", message: "eval is forbidden" });
      }
      if (/\bnew\s+Function\b/.test(source)) {
        violations.push({
          kind: "function-ctor",
          message: "new Function is forbidden",
        });
      }
      const importRe = /from\s+["']([^"']+)["']/g;
      let m: RegExpExecArray | null;
      const allowlist = new Set([
        "react",
        "react-dom",
        "@0x-copilot/design-system",
        "@0x-copilot/chat-surface",
      ]);
      while ((m = importRe.exec(source))) {
        if (!allowlist.has(m[1])) {
          violations.push({
            kind: "import",
            message: `banned import: ${m[1]}`,
          });
        }
      }
      if (/\bimport\s*\(/.test(source)) {
        violations.push({
          kind: "dynamic-import",
          message: "dynamic import is forbidden",
        });
      }
      return violations.length === 0 ? { ok: true } : { ok: false, violations };
    },
  };
}

describe("Q2 — staticAnalyze (allowlist gate)", () => {
  it("accepts source that uses only allowlisted imports and no banned globals", () => {
    const src = `
      import React from "react";
      export const adapter = {
        scheme: "email",
        renderCurrent: () => React.createElement("div"),
      };
    `;
    const result = staticAnalyze(src, fakeChecker());
    expect(result.ok).toBe(true);
  });

  it("rejects source that references fetch", () => {
    const src = `
      import React from "react";
      fetch("/api/data");
    `;
    const result = staticAnalyze(src, fakeChecker());
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.violations.some((v) => v.kind === "global")).toBe(true);
    }
  });

  it("rejects source that references XMLHttpRequest", () => {
    const src = `new XMLHttpRequest()`;
    const result = staticAnalyze(src, fakeChecker());
    expect(result.ok).toBe(false);
  });

  it("rejects source that references WebSocket", () => {
    const src = `new WebSocket("ws://x")`;
    const result = staticAnalyze(src, fakeChecker());
    expect(result.ok).toBe(false);
  });

  it("rejects source that references window", () => {
    const src = `window.location.href = "evil"`;
    const result = staticAnalyze(src, fakeChecker());
    expect(result.ok).toBe(false);
  });

  it("rejects source that calls eval", () => {
    const src = `eval("1+1")`;
    const result = staticAnalyze(src, fakeChecker());
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.violations.some((v) => v.kind === "eval")).toBe(true);
    }
  });

  it("rejects source that constructs new Function", () => {
    const src = `new Function("return 1")`;
    const result = staticAnalyze(src, fakeChecker());
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.violations.some((v) => v.kind === "function-ctor")).toBe(
        true,
      );
    }
  });

  it("rejects source that imports a non-allowlisted module", () => {
    const src = `import fs from "node:fs"`;
    const result = staticAnalyze(src, fakeChecker());
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.violations.some((v) => v.kind === "import")).toBe(true);
    }
  });

  it("rejects source that imports child_process", () => {
    const src = `import { spawn } from "child_process"`;
    const result = staticAnalyze(src, fakeChecker());
    expect(result.ok).toBe(false);
  });

  it("rejects source with a dynamic import()", () => {
    const src = `const m = await import("/etc/passwd")`;
    const result = staticAnalyze(src, fakeChecker());
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.violations.some((v) => v.kind === "dynamic-import")).toBe(
        true,
      );
    }
  });

  it("rejects empty source string", () => {
    const result = staticAnalyze("", fakeChecker());
    expect(result.ok).toBe(false);
  });

  it("default checker fails-closed when 6A is not wired", () => {
    // No checker passed → default StubAstAllowlistChecker is used. The stub
    // refuses everything so the install pipeline cannot accidentally proceed
    // without the real AST scanner (D29).
    const result = staticAnalyze("export const adapter = {};");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.violations[0].kind).toBe("internal");
    }
  });
});
