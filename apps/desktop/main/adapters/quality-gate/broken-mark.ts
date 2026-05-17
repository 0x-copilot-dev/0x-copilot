// Q6 invalidation primitive (PRD §9.5.1, §9.5.4). When the error-boundary
// instrumentation surfaces a render failure (or the lifecycle decides the
// adapter must be demoted for any other reason), the orchestrator (6C) calls
// `markAdapterBroken(scheme, version, reason)`. This module owns two side
// effects, both deliberately small:
//
//   1. Append one JSON Lines record to {userData}/audit/adapter-lifecycle.log.
//      The log is APPEND-ONLY — we use fs.appendFile and never write, truncate,
//      unlink, or otherwise mutate prior records. Replaying the log is how 6C
//      reconstructs adapter lifecycle on startup.
//
//   2. Call registry.markBroken(scheme, version, reason) so subsequent
//      resolveAdapter() calls in chat-surface skip this version (Phase 4-A
//      already implements the skip).
//
// All dependencies are injected so unit tests can write to tmp dirs without
// loading Electron. Electron's app.getPath('userData') is resolved lazily
// inside the default deps factory.

import { dirname } from "node:path";

export type BrokenMarkEventKind = "broken-marked";

export interface AuditEntry {
  readonly ts: number;
  readonly kind: BrokenMarkEventKind;
  readonly scheme: string;
  readonly version: number;
  readonly reason: string;
}

export interface BrokenMarkFs {
  appendFile(path: string, data: string): Promise<void>;
  mkdir(path: string, opts: { recursive: true }): Promise<string | undefined>;
}

export interface BrokenMarkRegistry {
  markBroken(scheme: string, version: number, reason: string): void;
}

export interface BrokenMarkDeps {
  readonly logPath: string;
  readonly clock: () => number;
  readonly registry: BrokenMarkRegistry;
  readonly fs: BrokenMarkFs;
}

export async function markAdapterBroken(
  scheme: string,
  version: number,
  reason: string,
  deps: BrokenMarkDeps,
): Promise<void> {
  const entry: AuditEntry = {
    ts: deps.clock(),
    kind: "broken-marked",
    scheme,
    version,
    reason,
  };
  const line = `${JSON.stringify(entry)}\n`;

  await deps.fs.mkdir(dirname(deps.logPath), { recursive: true });
  await deps.fs.appendFile(deps.logPath, line);

  deps.registry.markBroken(scheme, version, reason);
}
