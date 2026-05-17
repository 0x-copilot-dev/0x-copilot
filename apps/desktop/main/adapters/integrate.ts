import { astAllowlistScan, type AstViolation } from "./ast-allowlist";
import {
  setDefaultAstAllowlistChecker,
  type AstAllowlistChecker,
  type ViolationKind,
} from "./quality-gate";

const KIND_BY_6A: Record<string, ViolationKind> = {
  import: "import",
  "dynamic-import": "dynamic-import",
  global: "global",
  "member-access": "member-access",
  eval: "eval",
  "function-ctor": "function-ctor",
};

function mapViolationKind(kind: string): ViolationKind {
  return KIND_BY_6A[kind] ?? "internal";
}

const realAstAllowlistChecker: AstAllowlistChecker = {
  check(source) {
    const result = astAllowlistScan(source);
    if (result.ok) return { ok: true };
    return {
      ok: false,
      violations: result.violations.map((v: AstViolation) => ({
        kind: mapViolationKind(v.kind),
        message: v.detail,
        loc: { line: v.line, column: 0 },
      })),
    };
  },
};

export function wireQualityGateForTier2(): void {
  setDefaultAstAllowlistChecker(realAstAllowlistChecker);
}
