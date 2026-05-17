// Q2 (PRD §9.5.1). Static-analysis pass run pre-install. The real AST
// allowlist scanner lives in 6A (apps/desktop/main/adapters/ast-allowlist.ts);
// this module is the Q2 GATE — it wraps the scanner behind an injectable port
// so the lifecycle (6C) can call one function and so the two branches can
// land in any order. The default checker fails-closed if 6A's module is not
// yet on disk — a missing static analyzer must refuse the install, never
// silently allow it (D29).

export type ViolationKind =
  | "import"
  | "global"
  | "member-access"
  | "eval"
  | "function-ctor"
  | "dynamic-import"
  | "internal";

export interface Violation {
  readonly kind: ViolationKind;
  readonly message: string;
  readonly loc?: { readonly line: number; readonly column: number };
}

export interface AstAllowlistChecker {
  check(
    source: string,
  ): { ok: true } | { ok: false; violations: readonly Violation[] };
}

export interface AllowlistOk {
  readonly ok: true;
}

export interface AllowlistFail {
  readonly ok: false;
  readonly violations: readonly Violation[];
}

class StubAstAllowlistChecker implements AstAllowlistChecker {
  check(
    _source: string,
  ): { ok: true } | { ok: false; violations: readonly Violation[] } {
    return {
      ok: false,
      violations: [
        {
          kind: "internal",
          message:
            "ast-allowlist checker is not wired (6A integration pending). Refusing install fails-closed (D29).",
        },
      ],
    };
  }
}

let defaultChecker: AstAllowlistChecker = new StubAstAllowlistChecker();

export function setDefaultAstAllowlistChecker(
  checker: AstAllowlistChecker,
): void {
  defaultChecker = checker;
}

export function staticAnalyze(
  source: string,
  checker?: AstAllowlistChecker,
): AllowlistOk | AllowlistFail {
  if (typeof source !== "string" || source.length === 0) {
    return {
      ok: false,
      violations: [
        {
          kind: "internal",
          message: "staticAnalyze: source must be a non-empty string",
        },
      ],
    };
  }
  const c = checker ?? defaultChecker;
  const result = c.check(source);
  if (result.ok) {
    return { ok: true };
  }
  return { ok: false, violations: result.violations };
}
