import { createHash } from "node:crypto";

import type { AuthAuditLog, SignInMode } from "./audit-log";
import { runGoogleLogin } from "./google-login";
import { runGoogleLink, type GoogleLinkResult } from "./google-link";
import { runLocalLogin } from "./local-login";
import { runWalletLogin } from "./wallet-login";
import { runWalletLink, type WalletLinkResult } from "./wallet-link";
import {
  OidcClient,
  type AuthMode,
  type AuthSession,
  type OidcProviderConfig,
} from "./oidc-client";
import {
  SecretStorage,
  type SafeStorageLike,
  type SecretAuditLog,
  type ServerKind,
} from "./secret-storage";

export type { AuthMode, AuthSession, SessionClaims } from "./oidc-client";
export type { SafeStorageLike, ServerKind } from "./secret-storage";
export { OidcClient } from "./oidc-client";
export { SecretStorage } from "./secret-storage";
export {
  awaitLoopbackCode,
  awaitLoopbackHandoff,
  type LoopbackHandle,
  type LoopbackHandoff,
  type LoopbackHandoffHandle,
} from "./loopback-server";
export {
  GoogleLoginError,
  runGoogleLogin,
  type GoogleLoginDeps,
} from "./google-login";
export {
  WalletLoginError,
  runWalletLogin,
  type WalletLoginDeps,
} from "./wallet-login";
export {
  GoogleLinkError,
  runGoogleLink,
  type GoogleLinkDeps,
  type GoogleLinkResult,
  type GoogleLinkStatus,
} from "./google-link";
export {
  WalletLinkError,
  runWalletLink,
  type WalletLinkDeps,
  type WalletLinkResult,
  type WalletLinkStatus,
} from "./wallet-link";
export {
  LocalLoginError,
  runLocalLogin,
  type LocalLoginDeps,
} from "./local-login";

export {
  createFileAuthAuditLog,
  type AuthAuditEntry,
  type AuthAuditEvent,
  type AuthAuditLog,
  type FileAuthAuditLogOptions,
  type SignInMode,
} from "./audit-log";

export interface AuthServiceConfig {
  readonly mode: AuthMode;
  readonly facadeBaseUrl: string;
  readonly devPersonaSlug?: string;
  readonly oidc?: OidcProviderConfig;
  readonly userDataDir: string;
  readonly safeStorage: SafeStorageLike;
  readonly openExternal: (url: string) => Promise<void>;
  readonly allowPlaintextFallback?: boolean;
  readonly audit?: SecretAuditLog;
  /** Auth event trail (sign-in success/failure, …). Optional. */
  readonly authAudit?: AuthAuditLog;
  readonly clock?: () => number;
  readonly fetch?: typeof fetch;
  /** Injectable for tests; defaults to the real facade-brokered flow. */
  readonly googleLoginFlow?: typeof runGoogleLogin;
  /** Loopback redirect timeout for the Google flow (user-cancel bound). */
  readonly googleTimeoutMs?: number;
  /** Injectable for tests; defaults to the real wallet-page flow. */
  readonly walletLoginFlow?: typeof runWalletLogin;
  /** Loopback redirect timeout for the wallet flow (user-cancel bound). */
  readonly walletTimeoutMs?: number;
  /** Injectable for tests; defaults to the real local-key SIWE flow. */
  readonly localLoginFlow?: typeof runLocalLogin;
  /**
   * Per-install host secret (ENTERPRISE_SERVICE_TOKEN) from the supervisor's
   * boot secrets — required for the device-account mint ("Use locally").
   * Absent in unsupervised dev shells, where signInLocal fails closed.
   */
  readonly hostToken?: string;
  /** Injectable for tests; defaults to the real authenticated Google-link flow. */
  readonly googleLinkFlow?: typeof runGoogleLink;
  /** Injectable for tests; defaults to the real authenticated wallet-link flow. */
  readonly walletLinkFlow?: typeof runWalletLink;
}

export interface RendererSession {
  readonly workspaceId: string;
  readonly expiresAt: number;
  readonly displayName: string | null;
  readonly email: string | null;
}

const BACKEND_KIND: ServerKind = "backend";
const BACKEND_SERVER_ID = "facade";
// Where the per-install local-identity private key is stored (SecretStorage,
const REFRESH_WINDOW_MS = 60_000;

export class AuthService {
  readonly #oidc: OidcClient;
  readonly #storage: SecretStorage;
  readonly #cache = new Map<string, AuthSession>();
  readonly #clock: () => number;
  readonly #config: AuthServiceConfig;
  readonly #authAudit: AuthAuditLog | undefined;
  readonly #googleLoginFlow: typeof runGoogleLogin;
  readonly #walletLoginFlow: typeof runWalletLogin;
  readonly #localLoginFlow: typeof runLocalLogin;
  readonly #googleLinkFlow: typeof runGoogleLink;
  readonly #walletLinkFlow: typeof runWalletLink;
  // One slot shared by every system-browser flow (Google, wallet): the
  // newest click always wins, whichever button it came from.
  #cancelPendingBrowserLogin: (() => void) | null = null;

  constructor(config: AuthServiceConfig) {
    this.#oidc = new OidcClient({
      mode: config.mode,
      facadeBaseUrl: config.facadeBaseUrl,
      devPersonaSlug: config.devPersonaSlug,
      oidc: config.oidc,
      clock: config.clock,
      fetch: config.fetch,
      openExternal: config.openExternal,
    });
    this.#storage = new SecretStorage({
      userDataDir: config.userDataDir,
      safeStorage: config.safeStorage,
      allowPlaintextFallback: config.allowPlaintextFallback,
      audit: config.audit,
    });
    this.#clock = config.clock ?? Date.now;
    this.#config = config;
    this.#authAudit = config.authAudit;
    this.#googleLoginFlow = config.googleLoginFlow ?? runGoogleLogin;
    this.#walletLoginFlow = config.walletLoginFlow ?? runWalletLogin;
    this.#localLoginFlow = config.localLoginFlow ?? runLocalLogin;
    this.#googleLinkFlow = config.googleLinkFlow ?? runGoogleLink;
    this.#walletLinkFlow = config.walletLinkFlow ?? runWalletLink;
  }

  async signIn(workspaceId: string): Promise<RendererSession> {
    this.#storage.setActiveWorkspace(workspaceId);
    const session = await this.#oidc.signIn(workspaceId);
    await this.#storage.set(
      workspaceId,
      BACKEND_KIND,
      BACKEND_SERVER_ID,
      session,
    );
    this.#cache.set(workspaceId, session);
    return this.#toRenderer(workspaceId, session);
  }

  // "Continue with Google" — facade-brokered system-browser flow. A second
  // invocation while one is pending cancels the first (its loopback closes
  // and its promise rejects) so the newest click always wins; a successful
  // sign-in overwrites whatever session was stored before.
  async signInWithGoogle(workspaceId: string): Promise<RendererSession> {
    return this.#signInViaSystemBrowser(workspaceId, "google", (onCancel) =>
      this.#googleLoginFlow(workspaceId, {
        facadeBaseUrl: this.#config.facadeBaseUrl,
        openExternal: this.#config.openExternal,
        fetch: this.#config.fetch,
        clock: this.#config.clock,
        timeoutMs: this.#config.googleTimeoutMs,
        returnTo: "atlas-desktop",
        onCancelAvailable: onCancel,
      }),
    );
  }

  // "Connect wallet" — SIWE via the facade-served wallet page in the
  // system browser + loopback bearer handoff. Same cancel semantics as
  // Google: the newest sign-in click (either mode) replaces the pending
  // one.
  async signInWithWallet(workspaceId: string): Promise<RendererSession> {
    return this.#signInViaSystemBrowser(workspaceId, "wallet", (onCancel) =>
      this.#walletLoginFlow(workspaceId, {
        facadeBaseUrl: this.#config.facadeBaseUrl,
        openExternal: this.#config.openExternal,
        fetch: this.#config.fetch,
        clock: this.#config.clock,
        timeoutMs: this.#config.walletTimeoutMs,
        onCancelAvailable: onCancel,
      }),
    );
  }

  // "Link Google" (account-linking PRD FR-L2) — authenticated system-browser
  // OAuth that attaches a Google identity to the CURRENT account. The caller's
  // bearer authenticates the link/start POST and never leaves main; the flow
  // returns only a renderer-safe outcome. Shares the newest-click-wins cancel
  // slot with the sign-in browser flows.
  async linkGoogle(workspaceId: string): Promise<GoogleLinkResult> {
    const bearer = await this.getBearer(workspaceId);
    if (bearer === null) {
      throw new Error("Sign in before linking a Google account.");
    }
    return this.#runLinkViaSystemBrowser((onCancel) =>
      this.#googleLinkFlow({
        facadeBaseUrl: this.#config.facadeBaseUrl,
        bearer,
        openExternal: this.#config.openExternal,
        fetch: this.#config.fetch,
        timeoutMs: this.#config.googleTimeoutMs,
        returnTo: "atlas-desktop",
        onCancelAvailable: onCancel,
      }),
    );
  }

  // "Link a wallet" (account-linking PRD FR-L1/M1) — authenticated SIWE proof
  // collected in the system browser (wallet page in link mode) then POSTed to
  // the facade with the caller's bearer. `confirmMerge` is the FR-U2 consent
  // the renderer sets only after the user confirms the merge dialog.
  async linkWallet(
    workspaceId: string,
    confirmMerge: boolean,
  ): Promise<WalletLinkResult> {
    const bearer = await this.getBearer(workspaceId);
    if (bearer === null) {
      throw new Error("Sign in before linking a wallet.");
    }
    return this.#runLinkViaSystemBrowser((onCancel) =>
      this.#walletLinkFlow({
        facadeBaseUrl: this.#config.facadeBaseUrl,
        bearer,
        confirmMerge,
        openExternal: this.#config.openExternal,
        fetch: this.#config.fetch,
        timeoutMs: this.#config.walletTimeoutMs,
        onCancelAvailable: onCancel,
      }),
    );
  }

  // Shared cancel handling for the LINK browser flows (mirrors
  // #signInViaSystemBrowser, but without the session persist/audit tail — a
  // link mints no session). The newest browser flow (sign-in OR link) wins.
  async #runLinkViaSystemBrowser<T>(
    run: (onCancelAvailable: (cancel: () => void) => void) => Promise<T>,
  ): Promise<T> {
    this.#cancelPendingBrowserLogin?.();
    this.#cancelPendingBrowserLogin = null;
    let myCancel: (() => void) | null = null;
    try {
      return await run((cancel) => {
        myCancel = cancel;
        this.#cancelPendingBrowserLogin = cancel;
      });
    } finally {
      if (myCancel !== null && this.#cancelPendingBrowserLogin === myCancel) {
        this.#cancelPendingBrowserLogin = null;
      }
    }
  }

  // Renderer-driven cancel for the pending system-browser sign-in (the
  // design's "Cancel" on the wallet-waiting screen and "Cancel — use a
  // different method" on the Google screen). Closes the armed loopback so
  // the pending sign-in promise rejects and the port frees. Idempotent:
  // no-op when nothing is pending. Reuses the same slot the
  // newest-click-wins replacement path uses — one cancel mechanism, two
  // callers.
  cancelPendingSignIn(): void {
    this.#cancelPendingBrowserLogin?.();
    this.#cancelPendingBrowserLogin = null;
  }

  // "Use locally, no account" — the device account (production posture).
  // One host-token-gated POST mints a real session for the deployment's
  // single device account; the server-side singleton makes re-entry land on
  // the SAME account across restarts and reinstalls (D4-A) — no local key
  // material to lose. Distinct from dev-mint (which needs the dev IdP and
  // seeds a dev persona); this is the production path.
  async signInLocal(workspaceId: string): Promise<RendererSession> {
    this.#storage.setActiveWorkspace(workspaceId);
    try {
      const session = await this.#localLoginFlow(workspaceId, {
        facadeBaseUrl: this.#config.facadeBaseUrl,
        hostToken: this.#config.hostToken ?? "",
        fetch: this.#config.fetch,
        clock: this.#config.clock,
      });
      await this.#storage.set(
        workspaceId,
        BACKEND_KIND,
        BACKEND_SERVER_ID,
        session,
      );
      this.#cache.set(workspaceId, session);
      await this.#appendAudit({
        kind: "sign-in-success",
        workspaceId,
        sub: session.claims.sub,
        mode: "local",
      });
      return this.#toRenderer(workspaceId, session);
    } catch (err) {
      await this.#appendAudit({
        kind: "sign-in-failure",
        workspaceId,
        mode: "local",
        reason: err instanceof Error ? err.message : String(err),
      });
      throw err;
    }
  }

  // Shared tail of every system-browser sign-in: cancel-the-previous,
  // persist, cache, audit success/failure, clear our own cancel hook.
  async #signInViaSystemBrowser(
    workspaceId: string,
    mode: SignInMode,
    run: (
      onCancelAvailable: (cancel: () => void) => void,
    ) => Promise<AuthSession>,
  ): Promise<RendererSession> {
    this.#cancelPendingBrowserLogin?.();
    this.#cancelPendingBrowserLogin = null;
    let myCancel: (() => void) | null = null;
    try {
      const session = await run((cancel) => {
        myCancel = cancel;
        this.#cancelPendingBrowserLogin = cancel;
      });
      this.#storage.setActiveWorkspace(workspaceId);
      await this.#storage.set(
        workspaceId,
        BACKEND_KIND,
        BACKEND_SERVER_ID,
        session,
      );
      this.#cache.set(workspaceId, session);
      await this.#appendAudit({
        kind: "sign-in-success",
        workspaceId,
        sub: session.claims.sub,
        mode,
      });
      return this.#toRenderer(workspaceId, session);
    } catch (err) {
      await this.#appendAudit({
        kind: "sign-in-failure",
        workspaceId,
        mode,
        reason: err instanceof Error ? err.message : String(err),
      });
      throw err;
    } finally {
      // Only clear our own hook — a newer sign-in may have installed its
      // cancel function after ours was invalidated.
      if (myCancel !== null && this.#cancelPendingBrowserLogin === myCancel) {
        this.#cancelPendingBrowserLogin = null;
      }
    }
  }

  // Audit failures must never break the sign-in result path.
  async #appendAudit(
    event: Parameters<AuthAuditLog["append"]>[0],
  ): Promise<void> {
    if (this.#authAudit === undefined) return;
    try {
      await this.#authAudit.append(event);
    } catch {
      // swallow — the audit trail is best-effort on the client
    }
  }

  // The audit-free sign-out mechanism: drop the cached bearer and delete the
  // persisted session. getSession() reuses this to silently evict a stale or
  // facade-rejected session, so it must NOT emit a 'sign-out' audit row — that
  // would conflate an automatic eviction with a real user sign-out. A
  // user-initiated sign-out goes through signOutUserInitiated() below.
  async signOut(workspaceId: string): Promise<void> {
    this.#cache.delete(workspaceId);
    if (this.#storage.getActiveWorkspace() === workspaceId) {
      await this.#storage.delete(workspaceId, BACKEND_KIND, BACKEND_SERVER_ID);
      this.#storage.setActiveWorkspace(null);
    } else {
      await this.#storage.deleteWorkspaceSecrets(workspaceId);
    }
  }

  // User-initiated sign-out — the renderer/IPC entry point (wired in
  // main/index.ts). Same teardown as signOut() plus a best-effort 'sign-out'
  // audit row. Deliberately distinct from signOut() so the getSession()
  // eviction paths, which call signOut() directly, never emit a user
  // sign-out event.
  async signOutUserInitiated(workspaceId: string): Promise<void> {
    await this.signOut(workspaceId);
    await this.#appendAudit({ kind: "sign-out", workspaceId });
  }

  // Boot-time session lookup. Fails CLOSED: a persisted session is returned
  // only after it is validated against the facade. A stale/rejected bearer
  // (e.g. the leftover "Sarah Chen" dev session on a now-production install)
  // is dropped and null is returned so SignInGate shows the sign-in screen —
  // rather than loading a dead identity that every subsequent API call 401s.
  async getSession(workspaceId: string): Promise<RendererSession | null> {
    const session = await this.#loadSession(workspaceId);
    if (session === null) return null;
    // Locally-known expiry: fail closed without a network round-trip.
    if (session.expiresAt <= this.#clock()) {
      await this.signOut(workspaceId);
      return null;
    }
    const verdict = await this.#probePersistedSession(session);
    if (verdict === "rejected") {
      await this.signOut(workspaceId);
      return null;
    }
    // "valid" or "unknown" (facade unreachable / non-401 error): keep the
    // still-unexpired session. The live transport's 401 interceptor handles a
    // later rejection; a transient network blip must not nuke a good session.
    return this.#toRenderer(workspaceId, session);
  }

  // Probe the facade with the persisted bearer. Returns "rejected" ONLY on a
  // definitive 401/403 (drop the session), "valid" on 2xx, and "unknown" for
  // anything inconclusive (network error, 5xx) so the caller keeps the session.
  async #probePersistedSession(
    session: AuthSession,
  ): Promise<"valid" | "rejected" | "unknown"> {
    const base = this.#config.facadeBaseUrl.endsWith("/")
      ? this.#config.facadeBaseUrl.slice(0, -1)
      : this.#config.facadeBaseUrl;
    const fetchImpl = this.#config.fetch ?? globalThis.fetch.bind(globalThis);
    try {
      const response = await fetchImpl(`${base}/v1/me/profile`, {
        method: "GET",
        headers: { authorization: `Bearer ${session.accessToken}` },
      });
      if (response.status === 401 || response.status === 403) return "rejected";
      if (response.ok) return "valid";
      return "unknown";
    } catch {
      return "unknown";
    }
  }

  async refresh(workspaceId: string): Promise<RendererSession | null> {
    const current = await this.#loadSession(workspaceId);
    if (current === null) return null;
    const next = await this.#oidc.refresh(workspaceId, current);
    await this.#storage.set(workspaceId, BACKEND_KIND, BACKEND_SERVER_ID, next);
    this.#cache.set(workspaceId, next);
    return this.#toRenderer(workspaceId, next);
  }

  activeWorkspace(): string | null {
    return this.#storage.getActiveWorkspace();
  }

  // Stable, non-reversible per-account key for main-process-owned UX flags
  // (the first-run / FTUE completion flag). Resolves the VERIFIED session via
  // the ASYNC load path (#loadSession) — NOT a bare sync cache read, which is
  // cold right after a restart — then returns a truncated SHA-256 of the
  // verified `claims.sub`. Never returns the raw sub. Returns null when no
  // verified session is loaded (no sub to key on), so the caller can fall back
  // to a coarser namespacing key. The renderer never sees or supplies this key.
  async accountKey(workspaceId: string): Promise<string | null> {
    const session = await this.#loadSession(workspaceId);
    if (session === null) return null;
    const sub = session.claims.sub;
    // A blank sub is not a verified identity (the OIDC fallback claims use "");
    // refuse to key on it so unrelated blank-sub sessions never collide on the
    // hash of the empty string.
    if (sub.trim() === "") return null;
    // Truncated to 128 bits: collision-safe for the handful of accounts on one
    // install, and short enough to keep the on-disk key compact.
    return createHash("sha256").update(sub, "utf8").digest("hex").slice(0, 32);
  }

  // Sync read of the in-memory bearer cache for WebTransport, whose Transport
  // contract requires a sync bearer. Returns null when the session hasn't been
  // loaded yet — the 401 from the server then drives withBearerRefresh, which
  // calls the async refresh() above and primes the cache for the retry.
  getBearerCachedSync(workspaceId: string): string | null {
    const cached = this.#cache.get(workspaceId);
    if (cached === undefined) return null;
    if (cached.expiresAt <= this.#clock()) return null;
    return cached.accessToken;
  }

  async getBearer(workspaceId: string): Promise<string | null> {
    const session = await this.#loadSession(workspaceId);
    if (session === null) return null;
    if (this.#oidc.shouldRefreshSoon(session, REFRESH_WINDOW_MS)) {
      try {
        const next = await this.#oidc.refresh(workspaceId, session);
        await this.#storage.set(
          workspaceId,
          BACKEND_KIND,
          BACKEND_SERVER_ID,
          next,
        );
        this.#cache.set(workspaceId, next);
        return next.accessToken;
      } catch {
        if (session.expiresAt > this.#clock()) {
          return session.accessToken;
        }
        return null;
      }
    }
    return session.accessToken;
  }

  async #loadSession(workspaceId: string): Promise<AuthSession | null> {
    const cached = this.#cache.get(workspaceId);
    if (cached !== undefined) return cached;
    if (this.#storage.getActiveWorkspace() === null) {
      this.#storage.setActiveWorkspace(workspaceId);
    }
    const raw = (await this.#storage.get(
      workspaceId,
      BACKEND_KIND,
      BACKEND_SERVER_ID,
    )) as AuthSession | null;
    if (raw === null) return null;
    this.#cache.set(workspaceId, raw);
    return raw;
  }

  #toRenderer(workspaceId: string, session: AuthSession): RendererSession {
    return {
      workspaceId,
      expiresAt: session.expiresAt,
      displayName: session.claims.name,
      email: session.claims.email,
    };
  }
}
