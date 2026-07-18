import { randomBytes as nodeRandomBytes } from "node:crypto";

import type { Grant, GrantSnapshot } from "./types";

// Per-run grant snapshot (AC5 slice 3b).
//
// A run must operate against ONE consistent view of the user's grants for its
// whole lifetime: the set of active grants CAPTURED AT RUN START. Without this,
// a grant revoked (or a mode narrowed) mid-run could retroactively change what
// an in-flight mutation is authorized against — a torn read of the authority
// list. `RunContextStore` mints, on run start, an immutable, run-bound snapshot
// behind an OPAQUE, unguessable `run_capability_context` id. A later FS op that
// carries that id is authorized against the frozen snapshot the run started
// with, NOT live grant state.
//
// SECURITY / LIFECYCLE INVARIANTS:
//   - RAM-ONLY. Contexts live in a main-process Map and are NEVER persisted to
//     disk, never logged, and (like the canonical grant `root`) never crossed
//     to the renderer. The broker projects a path-free `BrokerGrant` view when
//     it hands the pinned set to the token-holding worker.
//   - Opaque, unguessable id: 256 bits of CSPRNG, base64url, `rcx_`-prefixed.
//     A caller cannot forge or enumerate another run's context.
//   - Immutable: the pinned grant list (and each grant) is deep-frozen, so a
//     handle to a context cannot be mutated after minting.
//   - Rotates / clears: every mint yields a fresh id; a finished run is
//     released (`release`), and `clear()` drops every context (called when the
//     broker stops, so a boot never inherits a prior boot's run contexts).
//
// NOTE (deliberate): binding to the run-start snapshot means a grant revoked
// AFTER a run started still authorizes that run's context-bound ops until the
// run ends. That is the point — a run gets a consistent authority view. Ops
// that do NOT carry a run context still resolve against the LIVE active
// snapshot, so a revoke takes effect immediately for them.

const CONTEXT_ID_BYTES = 32; // 256-bit

export interface RunContextStoreConfig {
  /** Injectable CSPRNG for tests. Defaults to node:crypto randomBytes. */
  readonly randomBytes?: (size: number) => Buffer;
  /** Injectable clock for tests. Defaults to Date.now. */
  readonly clock?: () => number;
}

/**
 * An immutable, run-bound snapshot of the active grants. Held MAIN-SIDE only —
 * `grants` carry the canonical host `root`, which never leaves the main
 * process (the broker projects a path-free view before returning anything).
 */
export interface RunCapabilityContext {
  /** Opaque, unguessable per-run id (`rcx_<base64url>`). */
  readonly runContext: string;
  /** Epoch millis the snapshot was pinned (run start). */
  readonly capturedAt: number;
  /** Id of the underlying grant snapshot. */
  readonly snapshotId: string;
  /** Frozen copy of the active grants at run start (includes host `root`). */
  readonly grants: readonly Grant[];
}

export class RunContextStore {
  readonly #contexts = new Map<string, RunCapabilityContext>();
  readonly #randomBytes: (size: number) => Buffer;
  readonly #clock: () => number;

  constructor(config: RunContextStoreConfig = {}) {
    this.#randomBytes = config.randomBytes ?? nodeRandomBytes;
    this.#clock = config.clock ?? Date.now;
  }

  /**
   * Pin `snapshot`'s active grants under a fresh, unguessable run-context id
   * and return the immutable context. The stored list is deep-frozen.
   */
  mint(snapshot: GrantSnapshot): RunCapabilityContext {
    const runContext = `rcx_${this.#randomBytes(CONTEXT_ID_BYTES).toString(
      "base64url",
    )}`;
    const grants = Object.freeze(
      snapshot.grants.map((g) => Object.freeze({ ...g })),
    );
    const context: RunCapabilityContext = Object.freeze({
      runContext,
      capturedAt: this.#clock(),
      snapshotId: snapshot.snapshotId,
      grants,
    });
    this.#contexts.set(runContext, context);
    return context;
  }

  /** The pinned context for an id, or null if unknown / already released. */
  get(runContext: string): RunCapabilityContext | null {
    return this.#contexts.get(runContext) ?? null;
  }

  /** Release one run's context. True if it existed and was removed. */
  release(runContext: string): boolean {
    return this.#contexts.delete(runContext);
  }

  /** Drop every context (e.g. on broker stop). */
  clear(): void {
    this.#contexts.clear();
  }

  /** Number of live contexts (observability / tests). */
  size(): number {
    return this.#contexts.size;
  }
}
