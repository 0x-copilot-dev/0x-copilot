// Single source of truth for tier-2 adapter sandbox constraints.
// Loaded by the desktop 6A AST scanner (apps/desktop/main/adapters/ast-allowlist.ts)
// and mirrored on the AI backend by
// services/ai-backend/src/agent_runtime/capabilities/render_adapter_generator/capability.py
// (via enterprise_service_contracts.adapter_allowlist).
//
// JSON lives in the service-contracts package which is Python-primary; we
// import it directly by relative path so the same on-disk file feeds both
// runtimes.
import allowlist from "../../service-contracts/src/enterprise_service_contracts/adapter_allowlist.json";

export interface AdapterAllowlist {
  readonly schema_version: number;
  readonly allowed_imports: Readonly<Record<string, readonly string[]>>;
  readonly forbidden_globals: readonly string[];
  readonly forbidden_syntax: readonly string[];
  readonly budget_ms: number;
}

export const ADAPTER_ALLOWLIST: AdapterAllowlist =
  allowlist as AdapterAllowlist;
