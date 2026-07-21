import { dirname } from "node:path";

import type { AdapterLayoutTemplate } from "@0x-copilot/api-types";

// PRD-10 (Wave 4) — read-vs-write install review gate.
//
// Product resolution (plan §4 / PRD-10 scope): read-only generated adapters
// auto-install after the quality gates; an adapter whose scheme renders a
// write/diff surface requires a one-time human consent acknowledgment before it
// is registered. The signal available at install time is the generated
// `layout`: `form` is the write/edit archetype (data entry destined for a
// mutation the user approves), while `table` / `kanban` / `definition-list` are
// read-only displays. This is a deliberately conservative classifier — an
// explicit read/write flag on `AdapterGeneratedPayload` would be strictly
// better, but that contract is frozen for this PRD (api-types is out of scope),
// so the layout heuristic is the in-scope classifier.

export type InstallReviewClass = "read" | "write";

/** Classify a generated adapter's install as read-only or write/diff. */
export function classifyAdapterReview(
  layout: AdapterLayoutTemplate,
): InstallReviewClass {
  return layout === "form" ? "write" : "read";
}

export interface InstallConsentRequest {
  readonly scheme: string;
  readonly version: number;
  readonly generatorModel: string;
}

/**
 * Consulted only for write-classified adapters, immediately before the adapter
 * is dispatched to the renderer for `registerAdapter`. Resolving `false` means
 * the install is refused; the adapter is neither persisted nor registered.
 */
export interface InstallReviewGate {
  requireConsent(request: InstallConsentRequest): Promise<boolean>;
}

/**
 * Persistent one-time acknowledgment record keyed by scheme. Once a scheme is
 * acknowledged, future write adapters for that scheme install without a repeat
 * prompt.
 */
export interface ConsentAckStore {
  isAcknowledged(scheme: string): Promise<boolean>;
  recordAcknowledged(scheme: string): Promise<void>;
}

export interface ConsentAckFs {
  readFile(path: string, encoding: "utf8"): Promise<string>;
  writeFile(path: string, data: string): Promise<void>;
  mkdir(path: string, opts: { recursive: true }): Promise<string | undefined>;
}

interface AckFileShape {
  readonly acknowledged: string[];
}

function isMissingFileError(err: unknown): boolean {
  if (err === null || typeof err !== "object") return false;
  return (err as { code?: unknown }).code === "ENOENT";
}

/**
 * File-backed acknowledgment store: a small JSON document
 * (`{ acknowledged: string[] }`) under userData. Writes are whole-file (the set
 * is tiny); reads tolerate a missing/corrupt file by treating it as empty.
 */
export function createFileConsentAckStore(deps: {
  readonly filePath: string;
  readonly fs: ConsentAckFs;
}): ConsentAckStore {
  const read = async (): Promise<Set<string>> => {
    let raw: string;
    try {
      raw = await deps.fs.readFile(deps.filePath, "utf8");
    } catch (err) {
      if (isMissingFileError(err)) return new Set();
      throw err;
    }
    try {
      const parsed = JSON.parse(raw) as Partial<AckFileShape>;
      if (!parsed || !Array.isArray(parsed.acknowledged)) return new Set();
      return new Set(
        parsed.acknowledged.filter((s): s is string => typeof s === "string"),
      );
    } catch {
      return new Set();
    }
  };

  return {
    async isAcknowledged(scheme) {
      return (await read()).has(scheme);
    },
    async recordAcknowledged(scheme) {
      const set = await read();
      if (set.has(scheme)) return;
      set.add(scheme);
      const body: AckFileShape = { acknowledged: [...set].sort() };
      await deps.fs.mkdir(dirname(deps.filePath), { recursive: true });
      await deps.fs.writeFile(deps.filePath, `${JSON.stringify(body)}\n`);
    },
  };
}

export interface ConsentGateDeps {
  readonly store: ConsentAckStore;
  /**
   * Surfaces the one-time consent UI (the desktop uses a native message-box,
   * the equivalent of the folder-grant consent already in the app) and resolves
   * to the user's decision. Injected so the electron dependency stays in the
   * process entry point and this module remains unit-testable.
   */
  readonly prompt: (request: InstallConsentRequest) => Promise<boolean>;
}

/**
 * Build the production review gate: check the one-time acknowledgment store,
 * prompt on a miss, and record the acknowledgment on a grant so the prompt is
 * genuinely one-time per scheme.
 */
export function createInstallReviewGate(
  deps: ConsentGateDeps,
): InstallReviewGate {
  return {
    async requireConsent(request) {
      if (await deps.store.isAcknowledged(request.scheme)) return true;
      const granted = await deps.prompt(request);
      if (granted) await deps.store.recordAcknowledged(request.scheme);
      return granted;
    },
  };
}
