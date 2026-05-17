import { astAllowlistScan, type AstViolation } from "./ast-allowlist";
import {
  setDefaultAstAllowlistChecker,
  setDefaultSmokeRenderExecutor,
  type AstAllowlistChecker,
  type ViolationKind,
} from "./quality-gate";
import { MainProcessSmokeRenderExecutor } from "./smoke-render-executor";

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

// Phase 6C — wires 6D's smoke-render gate to a main-process executor backed
// by 6A's vm sandbox. Live render preemption is the Tier2Loader Web
// Worker's job; install-time smoke render uses a measured timer.
export function wireSmokeRenderExecutorForTier2(): void {
  setDefaultSmokeRenderExecutor(new MainProcessSmokeRenderExecutor());
}
