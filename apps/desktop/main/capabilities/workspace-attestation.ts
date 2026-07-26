import {
  generateKeyPairSync,
  randomUUID,
  sign,
  type KeyObject,
} from "node:crypto";

import type { WorkspaceWriteAttestation } from "./workspace-authority";

/**
 * Private, main-process-only ingress used to renew the desktop's signed C2
 * capability statement. The renderer never receives this URL's credential or
 * the signing key.
 */
export const DESKTOP_WORKSPACE_ATTESTATION_PATH =
  "/v1/agent/desktop-workspace-attestation";

export const DESKTOP_WORKSPACE_ATTESTATION_VERSION = 1;
export const DESKTOP_WORKSPACE_ATTESTATION_TTL_MS = 5 * 60_000;

/**
 * The compact wire payload deliberately contains capability facts only. It
 * has no grant id, user id, host path, native handle, or renderer input.
 */
export interface DesktopWorkspaceAttestationClaims {
  readonly v: typeof DESKTOP_WORKSPACE_ATTESTATION_VERSION;
  readonly boot_id: string;
  readonly issued_at_ms: number;
  readonly expires_at_ms: number;
  readonly native_workspace_primitives: "available" | "unavailable";
  readonly unsafe_dev_workspace_tcb: boolean;
  readonly workspace_write_isolation: "enforced" | "unavailable";
}

/** The signed transport envelope consumed by the facade → ai-backend bridge. */
export interface DesktopWorkspaceAttestationEnvelope {
  readonly payload: string;
  readonly signature: string;
}

/** Values injected only into the supervised ai-backend child environment. */
export interface DesktopWorkspaceAttestationBootstrap extends DesktopWorkspaceAttestationEnvelope {
  readonly publicKey: string;
}

export class DesktopWorkspaceAttestationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DesktopWorkspaceAttestationError";
  }
}

export interface DesktopWorkspaceAttestationPublisherDeps {
  readonly attestation: WorkspaceWriteAttestation;
  readonly now?: () => number;
  readonly bootId?: string;
  readonly keyPair?: {
    readonly privateKey: KeyObject;
    readonly publicKey: KeyObject;
  };
  readonly fetch?: typeof fetch;
}

/**
 * Electron main signs a short-lived capability statement with a fresh Ed25519
 * keypair for every boot. The private key never crosses Electron main; only
 * the public SPKI key goes to the supervised ai-backend. This means the
 * worker cannot forge an "available" C2 authority statement merely because
 * it has the ordinary service-to-service bearer.
 */
export class DesktopWorkspaceAttestationPublisher {
  readonly #attestation: WorkspaceWriteAttestation;
  readonly #privateKey: KeyObject;
  readonly #publicKey: string;
  readonly #bootId: string;
  readonly #now: () => number;
  readonly #fetch: typeof fetch;

  constructor(deps: DesktopWorkspaceAttestationPublisherDeps) {
    this.#attestation = Object.freeze({ ...deps.attestation });
    const keyPair = deps.keyPair ?? generateKeyPairSync("ed25519");
    this.#privateKey = keyPair.privateKey;
    this.#publicKey = Buffer.from(
      keyPair.publicKey.export({ type: "spki", format: "der" }),
    ).toString("base64url");
    this.#bootId = deps.bootId ?? `dwa_${randomUUID().replaceAll("-", "")}`;
    this.#now = deps.now ?? Date.now;
    this.#fetch = deps.fetch ?? globalThis.fetch.bind(globalThis);
  }

  /** Export the non-secret bootstrap material for the supervised child only. */
  bootstrap(): DesktopWorkspaceAttestationBootstrap {
    return { ...this.envelope(), publicKey: this.#publicKey };
  }

  /** Create a fresh envelope so a later renewal cannot accidentally be stale. */
  envelope(): DesktopWorkspaceAttestationEnvelope {
    const issuedAt = this.#now();
    const claims: DesktopWorkspaceAttestationClaims = {
      v: DESKTOP_WORKSPACE_ATTESTATION_VERSION,
      boot_id: this.#bootId,
      issued_at_ms: issuedAt,
      expires_at_ms: issuedAt + DESKTOP_WORKSPACE_ATTESTATION_TTL_MS,
      native_workspace_primitives: this.#attestation.nativeWorkspacePrimitives,
      unsafe_dev_workspace_tcb:
        this.#attestation.unsafeDevWorkspaceTcb === true,
      workspace_write_isolation: this.#attestation.workspaceWriteIsolation,
    };
    const payload = Buffer.from(canonicalClaimsJson(claims), "utf8").toString(
      "base64url",
    );
    const signature = sign(
      null,
      Buffer.from(payload, "utf8"),
      this.#privateKey,
    ).toString("base64url");
    return Object.freeze({ payload, signature });
  }

  /**
   * Send the signed statement through the product facade. The ordinary
   * per-install service token gates the loopback request; the Ed25519 proof is
   * still independently verified by ai-backend and establishes provenance.
   */
  async publish(input: {
    readonly facadeBaseUrl: string;
    readonly hostToken: string;
  }): Promise<void> {
    const baseUrl = input.facadeBaseUrl.replace(/\/+$/u, "");
    if (baseUrl === "" || input.hostToken.trim() === "") {
      throw new DesktopWorkspaceAttestationError(
        "desktop workspace attestation requires a supervised facade and host token",
      );
    }
    const response = await this.#fetch(
      `${baseUrl}${DESKTOP_WORKSPACE_ATTESTATION_PATH}`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-enterprise-service-token": input.hostToken,
        },
        body: JSON.stringify(this.envelope()),
      },
    );
    if (!response.ok) {
      throw new DesktopWorkspaceAttestationError(
        `desktop workspace attestation rejected (${response.status})`,
      );
    }
  }
}

/**
 * Cross-language signature input. Python uses ``json.dumps(sort_keys=True,
 * separators=(",", ":"))`` over the same closed field set. Keep this function
 * intentionally small and exact rather than relying on incidental object-key
 * insertion order.
 */
export function canonicalClaimsJson(
  claims: DesktopWorkspaceAttestationClaims,
): string {
  return JSON.stringify({
    boot_id: claims.boot_id,
    expires_at_ms: claims.expires_at_ms,
    issued_at_ms: claims.issued_at_ms,
    native_workspace_primitives: claims.native_workspace_primitives,
    unsafe_dev_workspace_tcb: claims.unsafe_dev_workspace_tcb,
    v: claims.v,
    workspace_write_isolation: claims.workspace_write_isolation,
  });
}
