import { CapabilityBroker, type CapabilityBrokerHandle } from "./broker";
import { FolderPicker, sanitizeLabel } from "./folder-picker";
import { GrantStore } from "./grant-store";
import type { RequestFolderGrantParams } from "./schemas";
import { type RendererGrant, toRendererGrant } from "./types";
import {
  LocalWorkspaceAuthority,
  type WorkspaceCommitPermit,
  type WorkspaceReadCapability,
  type WorkspaceRunFacts,
  type WorkspaceWriteAttestation,
} from "./workspace-authority";
import type { WorkspaceApprovalPermitHandoff } from "./workspace-approval";

// Application service that composes the folder picker, the encrypted grant
// store, and the loopback broker (AC5 slice 1). This is the object the IPC
// handlers call. Every method that returns to the renderer returns ONLY
// `RendererGrant` (no host path, no broker token).

export interface CapabilityServiceDeps {
  readonly store: GrantStore;
  readonly picker: FolderPicker;
  readonly broker: CapabilityBroker;
  /** Main-only workspace authority; it never crosses renderer IPC. */
  readonly workspaceAuthority: LocalWorkspaceAuthority;
}

export class CapabilityService {
  readonly #store: GrantStore;
  readonly #picker: FolderPicker;
  readonly #broker: CapabilityBroker;
  readonly #workspaceAuthority: LocalWorkspaceAuthority;

  constructor(deps: CapabilityServiceDeps) {
    this.#store = deps.store;
    this.#picker = deps.picker;
    this.#broker = deps.broker;
    this.#workspaceAuthority = deps.workspaceAuthority;
  }

  /**
   * Mint a grant for a folder — chosen in the native picker, or NAMED by the
   * caller for the mid-run "always allow" ask.
   *
   * Returns null when the user cancels the picker. The authoritative path is
   * always a realpath resolved here and never leaves main.
   *
   * THE NAMED-PATH BRANCH is the durable half of a filesystem approval: the
   * backend raised a card naming one folder, the user chose to attach it, and
   * the grant must cover THAT folder — not a parent they might have picked by
   * accident, and not a wider tree. It is deliberately more constrained than
   * the picker branch on both axes a caller could otherwise abuse:
   *
   * * **read_only, always.** The named-path lane is reachable only from a
   *   filesystem READ approval, and a filesystem interrupt must never authorize
   *   a mutation — host writes go through the staged/attested workspace
   *   protocol, not through a grant minted off a read card. So the requested
   *   mode is ignored here rather than trusted.
   * * **main derives the label.** A caller-supplied label WINS over the
   *   basename, so honouring it would let a pill read "Downloads" over a grant
   *   on Documents — a wrong claim of access, which is the defect rather than
   *   the fix.
   *
   * * **the grant covers the string that was shown.** `FolderPicker.resolve`
   *   confirms the named folder rather than resolving it: one realpath, and it
   *   must be the identity. Nothing between the card and the grant is allowed
   *   to move the answer, so the folder attached is the folder read. See that
   *   method for why a second resolution is the defect and not a safeguard.
   *
   * `assertGrantableRoot` runs TWICE on this path — once here on the named
   * string, before any filesystem lookup, and again inside `GrantStore.create`
   * on the root about to be stored. Same pure function, so there is still one
   * decision; the early call is what keeps a refused class (a system tree, a
   * volume root, another account's home) from being probed at all, and keeps
   * the sentence the user reads about the POLICY rather than about whether the
   * folder happened to exist.
   */
  async requestFolderGrant(
    params: RequestFolderGrantParams,
  ): Promise<RendererGrant | null> {
    if (params.path !== undefined) {
      this.#store.assertGrantable(params.path);
      const named = await this.#picker.resolve(params.path);
      const grant = await this.#store.create({
        root: named.root,
        mode: "read_only",
        label: named.label,
      });
      return toRendererGrant(grant);
    }
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

  /** Main-only: named local child credential; never renderer IPC. */
  brokerClientCredential(service: string): string {
    return this.#broker.clientCredential(service);
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

  // --- workspace v2 authority (main-only; never renderer IPC) ---

  /**
   * C3 uses this after it derives run/user/device facts from the verified
   * desktop session. The loopback bearer alone can never mint this capability.
   */
  createWorkspaceReadCapability(
    facts: WorkspaceRunFacts,
    grantIds: readonly string[],
  ): Promise<WorkspaceReadCapability> {
    return this.#workspaceAuthority.createReadCapability(facts, grantIds);
  }

  /**
   * C3 calls this only after Electron main has verified the server's exact
   * decision receipt. The AI backend cannot access this method or mint permits.
   */
  authorizeWorkspaceCommit(
    facts: WorkspaceRunFacts,
    preparedRef: string,
    decision: {
      readonly stageId: string;
      readonly revision: number;
      readonly decisionLedgerId: string;
    },
  ): Promise<WorkspaceCommitPermit> {
    return this.#workspaceAuthority.authorizeCommitFromUserDecision(
      facts,
      preparedRef,
      decision,
    );
  }

  workspaceWriteAttestation(): WorkspaceWriteAttestation {
    return this.#workspaceAuthority.startupAttestation();
  }

  /** Main-only launch gate for the C3 approval host. */
  workspaceWritesAvailable(): boolean {
    return this.#workspaceAuthority.writableAvailable();
  }

  /**
   * Main-only C3 wiring. The handoff source is never represented in an IPC
   * type, renderer grant, or broker response; it is retained solely by the
   * authenticated local broker while consuming a prepared effect.
   */
  installWorkspaceApprovalPermitHandoff(
    handoff: WorkspaceApprovalPermitHandoff,
  ): void {
    this.#broker.installWorkspaceApprovalPermitHandoff(handoff);
  }
}
