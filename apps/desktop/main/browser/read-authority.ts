// Electron-main authority for browser reads.
//
// The AI broker caller supplies only a verified runtime run/workspace id and a
// typed tool call. It cannot choose a profile, origin policy, action class,
// approval id, or deadline. This authority pins one workspace to each run and
// mints short-lived ephemeral bindings from a main-owned exact-origin policy.

import { createHash, randomBytes as nodeRandomBytes } from "node:crypto";

import {
  BrowserOriginPolicySchema,
  BrowserProfileMode,
  BrowserRunBindingSchema,
  type BrowserOriginPolicy,
  type BrowserRunBinding,
} from "./protocol";

const DEFAULT_BINDING_TTL_MS = 15 * 60 * 1000;
const EPHEMERAL_PROFILE_REF = "ephemeral";

export class BrowserReadAuthorityError extends Error {
  readonly code: "scope_mismatch" | "invalid_scope";

  constructor(code: "scope_mismatch" | "invalid_scope") {
    super(
      code === "scope_mismatch"
        ? "browser run scope does not match its existing authority"
        : "browser run scope is invalid",
    );
    this.name = "BrowserReadAuthorityError";
    this.code = code;
  }
}

export interface BrowserReadAuthority {
  resolveBinding(input: {
    readonly runId: string;
    readonly workspaceId: string;
  }): BrowserRunBinding;
  revoke(runId: string): void;
}

export interface MainBrowserReadAuthorityConfig {
  readonly originPolicy: BrowserOriginPolicy;
  readonly now?: () => number;
  readonly randomBytes?: (size: number) => Buffer;
  readonly bindingTtlMs?: number;
}

interface PinnedRunScope {
  readonly workspaceId: string;
  readonly expiresAtMs: number;
}

export class MainBrowserReadAuthority implements BrowserReadAuthority {
  readonly #originPolicy: BrowserOriginPolicy;
  readonly #policyRef: string;
  readonly #now: () => number;
  readonly #randomBytes: (size: number) => Buffer;
  readonly #bindingTtlMs: number;
  readonly #scopes = new Map<string, PinnedRunScope>();

  constructor(config: MainBrowserReadAuthorityConfig) {
    this.#originPolicy = BrowserOriginPolicySchema.parse(config.originPolicy);
    this.#policyRef = policyAuthorityRef(this.#originPolicy);
    this.#now = config.now ?? Date.now;
    this.#randomBytes = config.randomBytes ?? nodeRandomBytes;
    this.#bindingTtlMs = config.bindingTtlMs ?? DEFAULT_BINDING_TTL_MS;
  }

  resolveBinding(input: {
    readonly runId: string;
    readonly workspaceId: string;
  }): BrowserRunBinding {
    if (
      input.runId.trim() !== input.runId ||
      input.workspaceId.trim() !== input.workspaceId ||
      input.runId.length === 0 ||
      input.workspaceId.length === 0
    ) {
      throw new BrowserReadAuthorityError("invalid_scope");
    }
    const now = this.#now();
    this.#sweep(now);
    const existing = this.#scopes.get(input.runId);
    if (existing !== undefined && existing.workspaceId !== input.workspaceId) {
      throw new BrowserReadAuthorityError("scope_mismatch");
    }
    const expiresAtMs = now + this.#bindingTtlMs;
    this.#scopes.set(input.runId, {
      workspaceId: input.workspaceId,
      expiresAtMs,
    });
    return BrowserRunBindingSchema.parse({
      version: 1,
      runId: input.runId,
      workspaceId: input.workspaceId,
      // Read sessions are always isolated and disposable. Persistent profiles
      // require a separate explicit user-consent flow and are not reachable.
      profileId: EPHEMERAL_PROFILE_REF,
      profileMode: BrowserProfileMode.Ephemeral,
      approvalId: this.#policyRef,
      originPolicy: this.#originPolicy,
      expiresAt: new Date(expiresAtMs).toISOString(),
      nonce: this.#randomBytes(24).toString("base64url"),
    });
  }

  revoke(runId: string): void {
    this.#scopes.delete(runId);
  }

  #sweep(now: number): void {
    for (const [runId, scope] of this.#scopes) {
      if (scope.expiresAtMs <= now) this.#scopes.delete(runId);
    }
  }
}

function policyAuthorityRef(policy: BrowserOriginPolicy): string {
  const digest = createHash("sha256")
    .update(JSON.stringify(policy))
    .digest("hex");
  return `browser-origin-policy:${digest}`;
}
