// Declared agents — agent-as-configuration wire contract.
//
// Mirrors `services/ai-backend/src/runtime_api/http/subagent_definition_routes.py`
// as proxied by `backend-facade`:
//
//   GET    /v1/agent/subagents         -> DeclaredSubagentListResponse
//   PUT    /v1/agent/subagents/{name}  -> SubagentDefinition (body + response)
//   DELETE /v1/agent/subagents/{name}  -> 204
//
// The server model is `agent_runtime.delegation.subagents.contracts
// .SubagentDefinition`, mirrored field-for-field. Field names are the wire
// names — snake_case — because this package describes payloads rather than
// view models; an app shapes its own camelCase view from these.
//
// Not mirrored, because the server does not serve them: a per-agent model or
// prompt override. A declared agent selects an existing `graph_id`; it does not
// carry its own runner. Adding either means a server field first.

/**
 * How a declared agent's graph is reached. `asgi` is the in-process default
 * every file-declared agent uses today.
 */
export type SubagentTransport = "asgi" | "http";

/**
 * One approval-policy mode. Ordered `auto` < `ask` < `require` < `block`; the
 * runtime keeps the stricter of parent and definition on every axis, so a
 * value here can only ever tighten what the parent already allowed.
 */
export type SubagentPolicyMode = "auto" | "ask" | "require" | "block";

/**
 * One filesystem permission rule granted to a declared agent — the domain
 * mirror of deepagents' `FilesystemPermission`.
 *
 * `paths` must each start with `/`. The field exists here because the server
 * model carries it and an app round-tripping a definition must not drop it: a
 * PUT replaces, so a dropped rule silently changes the child's filesystem
 * reach.
 */
export interface SubagentFilesystemPermission {
  readonly operations?: readonly ("read" | "write" | "execute")[];
  readonly paths?: readonly string[];
  readonly mode?: "allow" | "deny";
}

/**
 * The three approval-policy ceilings a declared agent's child must obey.
 * Omit it to inherit the run's own resolved tool-use policy rather than a
 * fresh default — see `SubagentAuthorityPolicy.narrow`.
 */
export interface SubagentPolicyGrant {
  readonly read?: SubagentPolicyMode;
  readonly write?: SubagentPolicyMode;
  readonly destructive?: SubagentPolicyMode;
}

/**
 * A declared agent, exactly as `subagent_defs/<name>.json` holds it.
 *
 * `tools` and `skills` are a **capability ceiling**, not a hint: the runtime
 * intersects a child's requested tools against this set before dispatch. An
 * app that edits a definition must send the whole set it means to grant —
 * a PUT replaces, it does not merge.
 */
export interface SubagentDefinition {
  readonly name: string;
  readonly description: string;
  readonly graph_id: string;
  readonly transport?: SubagentTransport;
  readonly tools?: readonly string[];
  readonly skills?: readonly string[];
  readonly required_scopes?: readonly string[];
  /**
   * Empty means "inherit the parent's scope ceiling". A non-empty set is an
   * additional definition-owned ceiling and never an expansion.
   */
  readonly allowed_scopes?: readonly string[];
  readonly policy?: SubagentPolicyGrant;
  readonly timeout_seconds?: number;
  readonly concurrency_limit?: number;
  readonly enabled?: boolean;
  readonly fs_permissions?: readonly SubagentFilesystemPermission[];
}

/** Response body of `GET /v1/agent/subagents`, sorted by `name`. */
export interface DeclaredSubagentListResponse {
  readonly subagents: readonly SubagentDefinition[];
}

/**
 * Narrow an unknown payload to a declared agent.
 *
 * Checks only the three fields the server requires; every other field is
 * optional on the wire and a guard that demanded them would reject a
 * definition the server itself accepts.
 */
export function isSubagentDefinition(
  value: unknown,
): value is SubagentDefinition {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Partial<SubagentDefinition>;
  return (
    typeof candidate.name === "string" &&
    typeof candidate.description === "string" &&
    typeof candidate.graph_id === "string"
  );
}

/** Narrow an unknown payload to the list response. */
export function isDeclaredSubagentListResponse(
  value: unknown,
): value is DeclaredSubagentListResponse {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Partial<DeclaredSubagentListResponse>;
  return (
    Array.isArray(candidate.subagents) &&
    candidate.subagents.every(isSubagentDefinition)
  );
}
