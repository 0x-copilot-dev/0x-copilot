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
export { awaitLoopbackCode, type LoopbackHandle } from "./loopback-server";

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
  readonly clock?: () => number;
  readonly fetch?: typeof fetch;
}

export interface RendererSession {
  readonly workspaceId: string;
  readonly expiresAt: number;
  readonly displayName: string | null;
  readonly email: string | null;
}

const BACKEND_KIND: ServerKind = "backend";
const BACKEND_SERVER_ID = "facade";
const REFRESH_WINDOW_MS = 60_000;

export class AuthService {
  readonly #oidc: OidcClient;
  readonly #storage: SecretStorage;
  readonly #cache = new Map<string, AuthSession>();
  readonly #clock: () => number;

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

  async signOut(workspaceId: string): Promise<void> {
    this.#cache.delete(workspaceId);
    if (this.#storage.getActiveWorkspace() === workspaceId) {
      await this.#storage.delete(workspaceId, BACKEND_KIND, BACKEND_SERVER_ID);
      this.#storage.setActiveWorkspace(null);
    } else {
      await this.#storage.deleteWorkspaceSecrets(workspaceId);
    }
  }

  async getSession(workspaceId: string): Promise<RendererSession | null> {
    const session = await this.#loadSession(workspaceId);
    if (session === null) return null;
    return this.#toRenderer(workspaceId, session);
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
