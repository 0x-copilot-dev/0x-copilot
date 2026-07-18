import { CapabilityBroker, type CapabilityBrokerHandle } from "./broker";
import { FolderPicker, sanitizeLabel } from "./folder-picker";
import { GrantStore } from "./grant-store";
import type { RequestFolderGrantParams } from "./schemas";
import { type RendererGrant, toRendererGrant } from "./types";

// Application service that composes the folder picker, the encrypted grant
// store, and the loopback broker (AC5 slice 1). This is the object the IPC
// handlers call. Every method that returns to the renderer returns ONLY
// `RendererGrant` (no host path, no broker token).

export interface CapabilityServiceDeps {
  readonly store: GrantStore;
  readonly picker: FolderPicker;
  readonly broker: CapabilityBroker;
}

export class CapabilityService {
  readonly #store: GrantStore;
  readonly #picker: FolderPicker;
  readonly #broker: CapabilityBroker;

  constructor(deps: CapabilityServiceDeps) {
    this.#store = deps.store;
    this.#picker = deps.picker;
    this.#broker = deps.broker;
  }

  /**
   * Open the native picker and, if the user selects a folder, mint a grant.
   * Returns null when the user cancels. The renderer-supplied label is only a
   * display hint (sanitized); the authoritative path is the picker's realpath
   * and never leaves main.
   */
  async requestFolderGrant(
    params: RequestFolderGrantParams,
  ): Promise<RendererGrant | null> {
    const picked = await this.#picker.pick();
    if (picked === null) return null;
    const label =
      params.label !== undefined ? sanitizeLabel(params.label) : picked.label;
    const grant = await this.#store.create({
      root: picked.root,
      mode: params.mode,
      label,
    });
    return toRendererGrant(grant);
  }

  async listGrants(): Promise<RendererGrant[]> {
    const grants = await this.#store.list();
    return grants.map(toRendererGrant);
  }

  /** Revoke a grant. Returns the updated renderer view, or null if unknown. */
  async revokeGrant(grantId: string): Promise<RendererGrant | null> {
    const grant = await this.#store.revoke(grantId);
    return grant === null ? null : toRendererGrant(grant);
  }

  // --- broker lifecycle (main-owned) ---

  startBroker(): Promise<CapabilityBrokerHandle> {
    return this.#broker.start();
  }

  stopBroker(): Promise<void> {
    return this.#broker.stop();
  }

  /**
   * MAIN-ONLY: the per-boot broker token, handed out of band to an intended
   * child. Never expose over renderer IPC; never log.
   */
  brokerAuthToken(): string {
    return this.#broker.authToken();
  }

  /** Non-secret broker base URL. */
  brokerBaseUrl(): string {
    return this.#broker.baseUrl();
  }

  // --- per-run grant snapshot (main-owned) ---

  /**
   * MAIN-ONLY: pin the currently-active grants for a starting run and return
   * ONLY the opaque `run_capability_context` id. Hand this to the run's worker
   * out of band; a later FS op that carries it is authorized against this
   * pinned snapshot rather than live grant state. The pinned grants (which
   * include host roots) never leave main.
   */
  async beginRun(): Promise<string> {
    const ctx = await this.#broker.mintRunContext();
    return ctx.runContext;
  }

  /** Release a finished run's pinned snapshot. True if it existed. */
  endRun(runContext: string): boolean {
    return this.#broker.releaseRunContext(runContext);
  }
}
