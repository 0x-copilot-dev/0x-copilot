import { createHash, randomBytes } from "node:crypto";

import { awaitLoopbackCode, type LoopbackHandle } from "./loopback-server";

export type AuthMode = "oidc" | "dev-mint";

export interface SessionClaims {
  readonly sub: string;
  readonly email: string | null;
  readonly name: string | null;
  readonly workspaceId: string;
}

export interface AuthSession {
  readonly idToken: string | null;
  readonly accessToken: string;
  readonly refreshToken: string | null;
  readonly expiresAt: number;
  readonly claims: SessionClaims;
}

export interface OidcConfig {
  readonly mode: AuthMode;
  readonly facadeBaseUrl: string;
  readonly devPersonaSlug?: string;
  readonly oidc?: OidcProviderConfig;
  readonly clock?: () => number;
  readonly random?: typeof randomBytes;
  readonly fetch?: typeof fetch;
  readonly openExternal?: (url: string) => Promise<void>;
  readonly loopback?: typeof awaitLoopbackCode;
}

export interface OidcProviderConfig {
  readonly issuer: string;
  readonly authorizationEndpoint: string;
  readonly tokenEndpoint: string;
  readonly clientId: string;
  readonly scopes: readonly string[];
}

export class OidcClient {
  readonly #mode: AuthMode;
  readonly #facadeBaseUrl: string;
  readonly #devPersonaSlug: string;
  readonly #oidc: OidcProviderConfig | undefined;
  readonly #clock: () => number;
  readonly #random: typeof randomBytes;
  readonly #fetch: typeof fetch;
  readonly #openExternal: (url: string) => Promise<void>;
  readonly #loopback: typeof awaitLoopbackCode;

  constructor(config: OidcConfig) {
    this.#mode = config.mode;
    this.#facadeBaseUrl = trimTrailingSlash(config.facadeBaseUrl);
    this.#devPersonaSlug = config.devPersonaSlug ?? "sarah_acme";
    this.#oidc = config.oidc;
    this.#clock = config.clock ?? Date.now;
    this.#random = config.random ?? randomBytes;
    this.#fetch = config.fetch ?? globalThis.fetch.bind(globalThis);
    this.#openExternal =
      config.openExternal ??
      (async () => {
        throw new Error("openExternal not configured");
      });
    this.#loopback = config.loopback ?? awaitLoopbackCode;
  }

  async signIn(workspaceId: string): Promise<AuthSession> {
    if (this.#mode === "dev-mint") {
      return this.#devMint(workspaceId);
    }
    return this.#runOidcFlow(workspaceId);
  }

  async refresh(
    workspaceId: string,
    session: AuthSession,
  ): Promise<AuthSession> {
    if (this.#mode === "dev-mint") {
      return this.#devMint(workspaceId);
    }
    if (session.refreshToken === null) {
      throw new Error("no refresh_token; sign-in required");
    }
    const oidc = this.#requireOidc();
    const body = new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: session.refreshToken,
      client_id: oidc.clientId,
    });
    const response = await this.#fetch(oidc.tokenEndpoint, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
    if (!response.ok) {
      throw new Error(`refresh failed: ${response.status}`);
    }
    const json = (await response.json()) as TokenResponse;
    return this.#tokenResponseToSession(
      json,
      session.claims.workspaceId,
      session.claims,
    );
  }

  shouldRefreshSoon(session: AuthSession, windowMs = 60_000): boolean {
    return session.expiresAt - this.#clock() < windowMs;
  }

  async #devMint(workspaceId: string): Promise<AuthSession> {
    const url = `${this.#facadeBaseUrl}/v1/dev/identity/mint`;
    const response = await this.#fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ persona_slug: this.#devPersonaSlug }),
    });
    if (!response.ok) {
      const detail = await safeText(response);
      throw new Error(`dev-mint failed: ${response.status} ${detail}`);
    }
    const json = (await response.json()) as DevMintResponse;
    const expiresAt = new Date(json.expires_at).getTime();
    return {
      idToken: null,
      accessToken: json.bearer,
      refreshToken: null,
      expiresAt,
      claims: {
        sub: json.identity.user_id,
        email: json.identity.primary_email,
        name: json.identity.display_name,
        workspaceId: workspaceId || json.identity.org_id,
      },
    };
  }

  async #runOidcFlow(workspaceId: string): Promise<AuthSession> {
    const oidc = this.#requireOidc();
    const state = base64url(this.#random(32));
    const verifier = base64url(this.#random(64));
    const challenge = base64url(createHash("sha256").update(verifier).digest());
    const handle: LoopbackHandle = await this.#loopback({
      expectedState: state,
    });
    try {
      const authUrl = new URL(oidc.authorizationEndpoint);
      authUrl.searchParams.set("response_type", "code");
      authUrl.searchParams.set("client_id", oidc.clientId);
      authUrl.searchParams.set("redirect_uri", handle.redirectUri);
      authUrl.searchParams.set("scope", oidc.scopes.join(" "));
      authUrl.searchParams.set("state", state);
      authUrl.searchParams.set("code_challenge", challenge);
      authUrl.searchParams.set("code_challenge_method", "S256");
      await this.#openExternal(authUrl.toString());
      const received = await handle.codePromise;
      const body = new URLSearchParams({
        grant_type: "authorization_code",
        code: received.code,
        redirect_uri: handle.redirectUri,
        client_id: oidc.clientId,
        code_verifier: verifier,
      });
      const response = await this.#fetch(oidc.tokenEndpoint, {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });
      if (!response.ok) {
        const detail = await safeText(response);
        throw new Error(`token exchange failed: ${response.status} ${detail}`);
      }
      const json = (await response.json()) as TokenResponse;
      const claims = decodeIdTokenClaims(json.id_token, workspaceId);
      return this.#tokenResponseToSession(json, workspaceId, claims);
    } finally {
      handle.close();
    }
  }

  #tokenResponseToSession(
    json: TokenResponse,
    workspaceId: string,
    fallbackClaims: SessionClaims,
  ): AuthSession {
    const expiresIn =
      typeof json.expires_in === "number" ? json.expires_in : 3600;
    const expiresAt = this.#clock() + expiresIn * 1000;
    const claims = json.id_token
      ? decodeIdTokenClaims(json.id_token, workspaceId, fallbackClaims)
      : fallbackClaims;
    return {
      idToken: json.id_token ?? null,
      accessToken: json.access_token,
      refreshToken: json.refresh_token ?? null,
      expiresAt,
      claims,
    };
  }

  #requireOidc(): OidcProviderConfig {
    if (this.#oidc === undefined) {
      throw new Error("oidc mode requires oidc provider config");
    }
    return this.#oidc;
  }
}

interface TokenResponse {
  readonly access_token: string;
  readonly id_token?: string;
  readonly refresh_token?: string;
  readonly expires_in?: number;
}

interface DevMintIdentity {
  readonly org_id: string;
  readonly user_id: string;
  readonly display_name: string;
  readonly primary_email: string;
}

interface DevMintResponse {
  readonly bearer: string;
  readonly expires_at: string;
  readonly persona_slug: string;
  readonly identity: DevMintIdentity;
}

function trimTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

function base64url(buf: Buffer): string {
  return buf
    .toString("base64")
    .replace(/=+$/u, "")
    .replace(/\+/gu, "-")
    .replace(/\//gu, "_");
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return "";
  }
}

function decodeIdTokenClaims(
  idToken: string | undefined,
  workspaceId: string,
  fallback?: SessionClaims,
): SessionClaims {
  if (idToken === undefined || idToken === "") {
    return (
      fallback ?? {
        sub: "",
        email: null,
        name: null,
        workspaceId,
      }
    );
  }
  const parts = idToken.split(".");
  if (parts.length < 2) {
    return fallback ?? { sub: "", email: null, name: null, workspaceId };
  }
  const payloadPart = parts[1];
  try {
    const padded = payloadPart + "=".repeat((4 - (payloadPart.length % 4)) % 4);
    const normalised = padded.replace(/-/gu, "+").replace(/_/gu, "/");
    const decoded = Buffer.from(normalised, "base64").toString("utf-8");
    const json = JSON.parse(decoded) as Record<string, unknown>;
    const sub = typeof json.sub === "string" ? json.sub : "";
    const email = typeof json.email === "string" ? json.email : null;
    const name = typeof json.name === "string" ? json.name : null;
    const wsClaim =
      typeof json.workspace_id === "string"
        ? json.workspace_id
        : typeof json.org_id === "string"
          ? json.org_id
          : workspaceId;
    return { sub, email, name, workspaceId: wsClaim };
  } catch {
    return fallback ?? { sub: "", email: null, name: null, workspaceId };
  }
}
