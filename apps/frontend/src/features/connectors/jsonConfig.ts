// JSON-config representation of the user's connector list. Mirrors
// what Cursor / Claude Desktop expose as ``mcp.json``: a flat list the
// user can edit as text. Round-trips through the existing per-server
// HTTP endpoints — diff is computed client-side and applied as a
// sequence of POST / PATCH / DELETE calls.

import type { McpServer } from "@0x-copilot/api-types";
import { errorMessage } from "../../utils/errors";

export interface JsonConfigEntry {
  /** Stable server id. Required for updates / deletes. Omit for creates. */
  id?: string;
  /** User-facing name. */
  name: string;
  url: string;
  transport: "http" | "sse" | "stdio";
  auth_mode: "none" | "oauth2" | "api_key" | "service_account";
  enabled: boolean;
}

export interface JsonConfig {
  version: 1;
  servers: JsonConfigEntry[];
}

export interface DiffPlan {
  creates: JsonConfigEntry[];
  /** Each update carries the server id and the patch payload. */
  updates: Array<{
    id: string;
    patch: { display_name?: string; enabled?: boolean };
  }>;
  deletes: Array<{ id: string; name: string }>;
}

const INDENT = 2;

export function serializeServers(servers: McpServer[]): string {
  const config: JsonConfig = {
    version: 1,
    servers: servers.map(toEntry),
  };
  return JSON.stringify(config, null, INDENT) + "\n";
}

function toEntry(server: McpServer): JsonConfigEntry {
  return {
    id: server.server_id,
    name: server.display_name,
    url: server.url,
    transport: server.transport,
    auth_mode: server.auth_mode,
    enabled: server.enabled,
  };
}

export class JsonConfigError extends Error {}

export function parseConfig(text: string): JsonConfig {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (err) {
    throw new JsonConfigError(
      `Invalid JSON: ${errorMessage(err, "parse error")}`,
    );
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new JsonConfigError("Expected a JSON object at the top level.");
  }
  const obj = raw as Record<string, unknown>;
  if (obj.version !== 1) {
    throw new JsonConfigError("Unsupported config version. Expected 1.");
  }
  if (!Array.isArray(obj.servers)) {
    throw new JsonConfigError('"servers" must be an array.');
  }
  const seenIds = new Set<string>();
  const servers: JsonConfigEntry[] = obj.servers.map((entry, index) => {
    const validated = validateEntry(entry, index);
    if (validated.id !== undefined) {
      if (seenIds.has(validated.id)) {
        throw new JsonConfigError(
          `Duplicate id "${validated.id}" at servers[${index}].`,
        );
      }
      seenIds.add(validated.id);
    }
    return validated;
  });
  return { version: 1, servers };
}

function validateEntry(value: unknown, index: number): JsonConfigEntry {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new JsonConfigError(`servers[${index}] must be an object.`);
  }
  const entry = value as Record<string, unknown>;
  const id = optionalString(entry.id, "id", index);
  const name = requiredString(entry.name, "name", index);
  const url = requiredString(entry.url, "url", index);
  if (!isHttpUrl(url)) {
    throw new JsonConfigError(
      `servers[${index}].url must be a valid http(s) URL.`,
    );
  }
  const transport = enumString(entry.transport ?? "http", "transport", index, [
    "http",
    "sse",
    "stdio",
  ]) as JsonConfigEntry["transport"];
  const auth_mode = enumString(
    entry.auth_mode ?? "oauth2",
    "auth_mode",
    index,
    ["none", "oauth2", "api_key", "service_account"],
  ) as JsonConfigEntry["auth_mode"];
  const enabled = typeof entry.enabled === "boolean" ? entry.enabled : false;
  return { id, name, url, transport, auth_mode, enabled };
}

function requiredString(value: unknown, key: string, index: number): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new JsonConfigError(
      `servers[${index}].${key} must be a non-empty string.`,
    );
  }
  return value.trim();
}

function optionalString(
  value: unknown,
  key: string,
  index: number,
): string | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (typeof value !== "string" || value.trim() === "") {
    throw new JsonConfigError(
      `servers[${index}].${key} must be a non-empty string when present.`,
    );
  }
  return value.trim();
}

function enumString(
  value: unknown,
  key: string,
  index: number,
  options: readonly string[],
): string {
  if (typeof value !== "string" || !options.includes(value)) {
    throw new JsonConfigError(
      `servers[${index}].${key} must be one of: ${options.join(", ")}.`,
    );
  }
  return value;
}

function isHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

export function diff(existing: McpServer[], next: JsonConfig): DiffPlan {
  const existingById = new Map(
    existing.map((server) => [server.server_id, server]),
  );
  const seen = new Set<string>();
  const creates: JsonConfigEntry[] = [];
  const updates: DiffPlan["updates"] = [];
  for (const entry of next.servers) {
    if (entry.id === undefined) {
      creates.push(entry);
      continue;
    }
    seen.add(entry.id);
    const current = existingById.get(entry.id);
    if (!current) {
      // Treat IDs that don't exist on the server as creates so the user
      // can paste a config from another workspace; the backend will
      // assign a fresh id and the seed:* convention is preserved only
      // when it matches an actual record.
      creates.push({ ...entry, id: undefined });
      continue;
    }
    const patch: { display_name?: string; enabled?: boolean } = {};
    if (entry.name !== current.display_name) {
      patch.display_name = entry.name;
    }
    if (entry.enabled !== current.enabled) {
      patch.enabled = entry.enabled;
    }
    if (Object.keys(patch).length > 0) {
      updates.push({ id: entry.id, patch });
    }
  }
  const deletes = existing
    .filter((server) => !seen.has(server.server_id))
    .map((server) => ({ id: server.server_id, name: server.display_name }));
  return { creates, updates, deletes };
}

export function isNoOp(plan: DiffPlan): boolean {
  return (
    plan.creates.length === 0 &&
    plan.updates.length === 0 &&
    plan.deletes.length === 0
  );
}
