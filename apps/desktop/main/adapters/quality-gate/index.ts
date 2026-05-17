// Public surface for the Phase 6 tier-2 quality gate (PRD §9.5.1, Q1-Q6).
// Pipelined by 6C (tier2-lifecycle): validateAdapterSchema → staticAnalyze →
// runSmokeRender → registerAdapter (wrapped with wrapWithBoundary) →
// on render error, markAdapterBroken.

export {
  validateAdapterSchema,
  type SchemaOk,
  type SchemaFail,
} from "./schema";

export {
  staticAnalyze,
  setDefaultAstAllowlistChecker,
  type AstAllowlistChecker,
  type AllowlistOk,
  type AllowlistFail,
  type Violation,
  type ViolationKind,
} from "./allowlist";

export {
  runSmokeRender,
  setDefaultSmokeRenderExecutor,
  DEFAULT_SMOKE_BUDGET_MS,
  type SmokeRenderExecutor,
  type SmokeRenderOk,
  type SmokeRenderFail,
  type SmokeFailKind,
  type SmokeMethod,
} from "./smoke-render";

export {
  wrapWithBoundary,
  type BoundaryError,
  type BoundaryListener,
} from "./error-boundary";

export {
  markAdapterBroken,
  type AuditEntry,
  type BrokenMarkDeps,
  type BrokenMarkFs,
  type BrokenMarkRegistry,
  type BrokenMarkEventKind,
} from "./broken-mark";
