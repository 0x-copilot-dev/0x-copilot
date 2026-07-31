import type { IpcMain, IpcMainInvokeEvent } from "electron";
import type { z } from "zod";

import type { RendererSession } from "@0x-copilot/chat-transport";

import type {
  DesktopConnectorCatalogResponse,
  DesktopConnectorConnectionResult,
  DesktopRequestedProductScope,
  McpOAuthClientConfigRequest,
} from "@0x-copilot/api-types";

import type { TransportBridge } from "../transport-bridge";
import { CAPABILITY_CHANNELS } from "../capabilities/channels";
import {
  ListGrantsParamsSchema,
  RendererGrantSchema,
  RequestFolderGrantParamsSchema,
  RevokeGrantParamsSchema,
  WorkspaceApprovalHostDecisionRequestSchema,
  WorkspaceApprovalHostDecisionResultSchema,
  type RequestFolderGrantParams,
} from "../capabilities/schemas";
import type { RendererGrant } from "../capabilities/types";
import type { WorkspaceApprovalHostPort } from "../capabilities/workspace-approval";
import {
  CONNECTOR_CHANNELS,
  type ConnectorAuthorizationResult,
} from "../connectors/channels";
import {
  AuthorizeParamsSchema,
  ConnectorAuthorizationResultSchema,
  ConnectorCatalogResponseSchema,
  ListCatalogParamsSchema,
} from "../connectors/schemas";
import {
  AuthWorkspaceParamsSchema,
  AuthLinkWalletParamsSchema,
  ArtifactContentHandleParamsSchema,
  ArtifactContentOpenParamsSchema,
  ArtifactRevisionParamsSchema,
  CHANNELS,
  EmptyParamsSchema,
  IpcValidationError,
  Tier2BoundaryErrorPayloadSchema,
  TransportRequestParamsSchema,
  TransportSubscribeParamsSchema,
  TransportUnsubscribeParamsSchema,
  toTransportHttpErrorWire,
  wrapTransportError,
  wrapTransportValue,
  type AuthLinkOutcome,
  type Tier2BoundaryErrorPayload,
} from "./schemas";

export interface AuthHandlers {
  signIn(workspaceId: string): Promise<RendererSession>;
  /** "Continue with Google" — system browser + loopback handoff. */
  signInWithGoogle(workspaceId: string): Promise<RendererSession>;
  /** "Connect wallet" (SIWE) — system browser + loopback handoff. */
  signInWithWallet(workspaceId: string): Promise<RendererSession>;
  /**
   * Cancel the pending system-browser sign-in (wallet or Google). Closes
   * the armed loopback so the pending sign-in promise rejects. Idempotent.
   */
  cancelPendingSignIn(): void;
  /** "Link Google" (PRD FR-L2) — authenticated system-browser OAuth link. */
  linkGoogle(workspaceId: string): Promise<AuthLinkOutcome>;
  /** "Link a wallet" (PRD FR-L1/M1) — authenticated system-browser SIWE link. */
  linkWallet(
    workspaceId: string,
    confirmMerge: boolean,
  ): Promise<AuthLinkOutcome>;
  signOut(workspaceId: string): Promise<void>;
  getSession(workspaceId: string): Promise<RendererSession | null>;
  refresh(workspaceId: string): Promise<RendererSession | null>;
  /**
   * Read-only production/dev posture. Lets the renderer hide the dev-mint
   * "Use locally" option in production posture. Carries no secret.
   */
  getPosture(): { readonly productionPosture: boolean };
}

// Capability / host-folder grant handlers (AC5 slice 1). Every method returns
// ONLY the renderer-safe grant view — no host path, no broker token. The
// CapabilityService satisfies this structurally.
export interface CapabilityHandlers {
  requestFolderGrant(
    params: RequestFolderGrantParams,
  ): Promise<RendererGrant | null>;
  listGrants(): Promise<RendererGrant[]>;
  revokeGrant(grantId: string): Promise<RendererGrant | null>;
}

// Connector connect handlers (AC9). The renderer asks main to fetch the
// reconciled catalog and to begin the system-browser OAuth connect flow for a
// slug. Both return ONLY renderer-safe views — no provider token, no redirect
// URI. The ConnectorService satisfies this structurally.
export interface ConnectorHandlers {
  listCatalog(): Promise<DesktopConnectorCatalogResponse>;
  /** Authorize a connector by whichever identity the caller holds. The
   *  implementation picks the OAuth topology — see `ConnectorService`. */
  authorize(target: {
    readonly slug?: string;
    readonly serverId?: string;
    readonly productScope?: DesktopRequestedProductScope;
    readonly oauthClient?: McpOAuthClientConfigRequest;
    readonly callbackMode?: "loopback" | "deep_link";
  }): Promise<ConnectorAuthorizationResult>;
  /**
   * Abort the connect awaiting a redirect, causing its `authorize` to reject.
   * Idempotent and a no-op when nothing is pending.
   */
  cancelPendingAuthorize(): void;
}

export interface IpcLogger {
  info(message: string, context?: Record<string, unknown>): void;
  warn(message: string, context?: Record<string, unknown>): void;
}

const defaultLogger: IpcLogger = {
  info: (msg, ctx) => {
    console.log(`[ipc] ${msg}`, ctx ?? "");
  },
  warn: (msg, ctx) => {
    console.warn(`[ipc] ${msg}`, ctx ?? "");
  },
};

function parseOrThrow<T>(
  channel: string,
  schema: z.ZodType<T>,
  raw: unknown,
): T {
  const result = schema.safeParse(raw);
  if (!result.success) {
    throw new IpcValidationError(channel, result.error.issues);
  }
  return result.data;
}

export interface Tier2InboundDispatcher {
  onBoundaryError(payload: Tier2BoundaryErrorPayload): void;
}

export interface RegisterHandlersDeps {
  readonly ipcMain: IpcMain;
  readonly bridge: TransportBridge;
  readonly auth?: AuthHandlers;
  readonly tier2?: Tier2InboundDispatcher;
  readonly capability?: CapabilityHandlers;
  /** C3-only, path-free host port. No broker or filesystem command crosses it. */
  readonly workspaceApproval?: WorkspaceApprovalHostPort;
  readonly connectors?: ConnectorHandlers;
  readonly logger?: IpcLogger;
}

// Structural guarantee that no host path (or any other field) can leak to the
// renderer: strict-parse the outbound grant view before it crosses IPC. An
// accidental extra key throws here instead of reaching the renderer.
function toSafeRendererGrant(grant: RendererGrant): RendererGrant {
  return RendererGrantSchema.parse(grant);
}

// Registers every IPC handler the renderer's IpcTransport invokes.
// Returns a teardown function that removes the handlers AND closes any
// active subscriptions tracked by the bridge. Agent 1-A's main/index.ts
// calls this once after window creation and the returned teardown on app
// shutdown.
//
// Race-avoidance contract: the transport.subscribe handler registers the
// underlying transport subscription SYNCHRONOUSLY inside the handler body
// before resolving. The renderer-side IpcTransport registers its own
// subscription record SYNCHRONOUSLY before firing this IPC. Both ends are
// fully set up before any stream-event can be emitted; the renderer's
// listener (installed in its constructor) is always wired before any
// subscribe call lands.
export function registerIpcHandlers(deps: RegisterHandlersDeps): () => void {
  const { ipcMain, bridge } = deps;
  const logger = deps.logger ?? defaultLogger;

  ipcMain.handle(CHANNELS.transportRequest, async (_event, raw: unknown) => {
    const params = parseOrThrow(
      CHANNELS.transportRequest,
      TransportRequestParamsSchema,
      raw,
    );
    // Resolve with the transport-result envelope: Electron's rejection
    // path flattens errors to a message string, which would strip the
    // status + structured FastAPI detail hosts branch on (e.g. the
    // account-linking 409 codes). Non-HTTP errors still reject unchanged.
    try {
      return wrapTransportValue(await bridge.request(params));
    } catch (err) {
      const wire = toTransportHttpErrorWire(err);
      if (wire !== null) {
        return wrapTransportError(wire);
      }
      throw err;
    }
  });

  ipcMain.handle(
    CHANNELS.transportArtifactContentOpen,
    async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.transportArtifactContentOpen,
        ArtifactContentOpenParamsSchema,
        raw,
      );
      return bridge.openArtifactContent(params);
    },
  );
  ipcMain.handle(
    CHANNELS.transportArtifactContentRead,
    async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.transportArtifactContentRead,
        ArtifactContentHandleParamsSchema,
        raw,
      );
      return bridge.readArtifactContent(params.handle);
    },
  );
  ipcMain.handle(
    CHANNELS.transportArtifactContentClose,
    async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.transportArtifactContentClose,
        ArtifactContentHandleParamsSchema,
        raw,
      );
      await bridge.closeArtifactContent(params.handle);
      return { ok: true as const };
    },
  );
  ipcMain.handle(
    CHANNELS.transportArtifactRevision,
    async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.transportArtifactRevision,
        ArtifactRevisionParamsSchema,
        raw,
      );
      try {
        return wrapTransportValue(await bridge.createArtifactRevision(params));
      } catch (err) {
        const wire = toTransportHttpErrorWire(err);
        if (wire !== null) return wrapTransportError(wire);
        throw err;
      }
    },
  );

  ipcMain.handle(
    CHANNELS.transportSubscribe,
    async (event: IpcMainInvokeEvent, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.transportSubscribe,
        TransportSubscribeParamsSchema,
        raw,
      );
      const webContentsId = event.sender.id;
      try {
        bridge.subscribe(params.subscriptionId, webContentsId, {
          path: params.path,
          query: params.query,
          eventName: params.eventName,
        });
      } catch (err) {
        logger.warn("subscribe failed", {
          subscriptionId: params.subscriptionId,
          error: err instanceof Error ? err.message : String(err),
        });
        throw err;
      }
      return { ok: true as const };
    },
  );

  ipcMain.handle(
    CHANNELS.transportUnsubscribe,
    async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.transportUnsubscribe,
        TransportUnsubscribeParamsSchema,
        raw,
      );
      const removed = bridge.unsubscribe(params.subscriptionId);
      return { removed };
    },
  );

  ipcMain.handle(
    CHANNELS.transportSessionSnapshot,
    async (_event, raw: unknown) => {
      parseOrThrow(
        CHANNELS.transportSessionSnapshot,
        EmptyParamsSchema,
        raw ?? {},
      );
      return bridge.sessionSnapshot();
    },
  );

  const auth = deps.auth;
  if (auth) {
    ipcMain.handle(CHANNELS.authGetSession, async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.authGetSession,
        AuthWorkspaceParamsSchema,
        raw,
      );
      return auth.getSession(params.workspaceId);
    });

    ipcMain.handle(CHANNELS.authGetPosture, async (_event, raw: unknown) => {
      parseOrThrow(CHANNELS.authGetPosture, EmptyParamsSchema, raw ?? {});
      return auth.getPosture();
    });

    ipcMain.handle(CHANNELS.authSignIn, async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.authSignIn,
        AuthWorkspaceParamsSchema,
        raw,
      );
      return auth.signIn(params.workspaceId);
    });

    ipcMain.handle(CHANNELS.authSignInGoogle, async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.authSignInGoogle,
        AuthWorkspaceParamsSchema,
        raw,
      );
      return auth.signInWithGoogle(params.workspaceId);
    });

    ipcMain.handle(CHANNELS.authSignInWallet, async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.authSignInWallet,
        AuthWorkspaceParamsSchema,
        raw,
      );
      return auth.signInWithWallet(params.workspaceId);
    });

    // Cancel the pending system-browser sign-in (wallet or Google). The
    // renderer's Cancel affordances fire this; the pending sign-in channel
    // then rejects (its loopback closed), which the renderer treats as a
    // quiet return to the pick screen.
    ipcMain.handle(CHANNELS.authCancelSignIn, async (_event, raw: unknown) => {
      parseOrThrow(CHANNELS.authCancelSignIn, EmptyParamsSchema, raw ?? {});
      auth.cancelPendingSignIn();
    });

    // Account-linking (PRD FR-L2): authenticated Google LINK. Returns only a
    // renderer-safe outcome — the bearer never crosses IPC.
    ipcMain.handle(CHANNELS.authLinkGoogle, async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.authLinkGoogle,
        AuthWorkspaceParamsSchema,
        raw,
      );
      return auth.linkGoogle(params.workspaceId);
    });

    // Account-linking (PRD FR-L1/M1): authenticated wallet LINK. `confirmMerge`
    // is the FR-U2 consent; the renderer re-invokes with it after the merge
    // dialog. Returns only a renderer-safe outcome.
    ipcMain.handle(CHANNELS.authLinkWallet, async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.authLinkWallet,
        AuthLinkWalletParamsSchema,
        raw,
      );
      return auth.linkWallet(params.workspaceId, params.confirmMerge);
    });

    // User-initiated sign-out. The wired AuthService routes auth.signOut to the
    // audited signOutUserInitiated (emits one 'sign-out' audit row); the raw
    // AuthService.signOut reused by getSession eviction stays audit-free.
    ipcMain.handle(CHANNELS.authSignOut, async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.authSignOut,
        AuthWorkspaceParamsSchema,
        raw,
      );
      await auth.signOut(params.workspaceId);
      return { ok: true as const };
    });

    ipcMain.handle(CHANNELS.authRefresh, async (_event, raw: unknown) => {
      const params = parseOrThrow(
        CHANNELS.authRefresh,
        AuthWorkspaceParamsSchema,
        raw,
      );
      return auth.refresh(params.workspaceId);
    });
  }

  const tier2 = deps.tier2;
  if (tier2) {
    ipcMain.handle(
      CHANNELS.tier2BoundaryError,
      async (_event, raw: unknown) => {
        const params = parseOrThrow(
          CHANNELS.tier2BoundaryError,
          Tier2BoundaryErrorPayloadSchema,
          raw,
        );
        tier2.onBoundaryError(params);
        return { ok: true as const };
      },
    );
  }

  const capability = deps.capability;
  if (capability) {
    ipcMain.handle(
      CAPABILITY_CHANNELS.requestFolderGrant,
      async (_event, raw: unknown) => {
        const params = parseOrThrow(
          CAPABILITY_CHANNELS.requestFolderGrant,
          RequestFolderGrantParamsSchema,
          raw,
        );
        const grant = await capability.requestFolderGrant(params);
        return grant === null ? null : toSafeRendererGrant(grant);
      },
    );

    ipcMain.handle(
      CAPABILITY_CHANNELS.listGrants,
      async (_event, raw: unknown) => {
        parseOrThrow(
          CAPABILITY_CHANNELS.listGrants,
          ListGrantsParamsSchema,
          raw ?? {},
        );
        const grants = await capability.listGrants();
        return grants.map(toSafeRendererGrant);
      },
    );

    ipcMain.handle(
      CAPABILITY_CHANNELS.revokeGrant,
      async (_event, raw: unknown) => {
        const params = parseOrThrow(
          CAPABILITY_CHANNELS.revokeGrant,
          RevokeGrantParamsSchema,
          raw,
        );
        const grant = await capability.revokeGrant(params.grantId);
        return grant === null ? null : toSafeRendererGrant(grant);
      },
    );
  }

  const workspaceApproval = deps.workspaceApproval;
  if (workspaceApproval) {
    ipcMain.handle(
      CAPABILITY_CHANNELS.decideWorkspaceApproval,
      async (_event, raw: unknown) => {
        const request = parseOrThrow(
          CAPABILITY_CHANNELS.decideWorkspaceApproval,
          WorkspaceApprovalHostDecisionRequestSchema,
          raw,
        );
        // Strict-parse this narrow, path-free result before it reaches the
        // renderer. A receipt/permit/prepared-ref is retained in main only.
        return WorkspaceApprovalHostDecisionResultSchema.parse(
          await workspaceApproval.decide(request),
        );
      },
    );
  }

  const connectors = deps.connectors;
  if (connectors) {
    ipcMain.handle(
      CONNECTOR_CHANNELS.listCatalog,
      async (_event, raw: unknown) => {
        parseOrThrow(
          CONNECTOR_CHANNELS.listCatalog,
          ListCatalogParamsSchema,
          raw ?? {},
        );
        const catalog = await connectors.listCatalog();
        // Strict-parse outbound: an unexpected key (e.g. a token) throws here
        // rather than reaching the renderer.
        return ConnectorCatalogResponseSchema.parse(catalog);
      },
    );

    // One handler because there is one verb. `connectors.authorize` picks the
    // OAuth topology from the backend-owned profile overlay; the renderer
    // supplies whichever identity it holds and never names a mechanism.
    ipcMain.handle(
      CONNECTOR_CHANNELS.authorize,
      async (_event, raw: unknown) => {
        const params = parseOrThrow(
          CONNECTOR_CHANNELS.authorize,
          AuthorizeParamsSchema,
          raw,
        );
        const result = await connectors.authorize(params);
        // Only the SAFE connection metadata may cross to the renderer.
        return ConnectorAuthorizationResultSchema.parse(result);
      },
    );

    // Cancel the connect awaiting a redirect. The renderer's Cancel affordances
    // fire this; the pending `authorize` above then rejects (its loopback
    // closed), which the renderer treats as a quiet return to "Connect".
    // Argument-free for the same reason `auth.cancel-sign-in` is: one connect
    // is in flight at a time, so there is nothing to name.
    ipcMain.handle(
      CONNECTOR_CHANNELS.cancelAuthorize,
      async (_event, raw: unknown) => {
        parseOrThrow(
          CONNECTOR_CHANNELS.cancelAuthorize,
          EmptyParamsSchema,
          raw ?? {},
        );
        connectors.cancelPendingAuthorize();
      },
    );
  }

  return () => {
    const channels: string[] = [
      CHANNELS.transportRequest,
      CHANNELS.transportArtifactContentOpen,
      CHANNELS.transportArtifactContentRead,
      CHANNELS.transportArtifactContentClose,
      CHANNELS.transportArtifactRevision,
      CHANNELS.transportSubscribe,
      CHANNELS.transportUnsubscribe,
      CHANNELS.transportSessionSnapshot,
    ];
    if (auth) {
      channels.push(
        CHANNELS.authGetSession,
        CHANNELS.authGetPosture,
        CHANNELS.authSignIn,
        CHANNELS.authSignInGoogle,
        CHANNELS.authSignInWallet,
        CHANNELS.authCancelSignIn,
        CHANNELS.authSignOut,
        CHANNELS.authRefresh,
      );
    }
    if (tier2) {
      channels.push(CHANNELS.tier2BoundaryError);
    }
    if (capability) {
      channels.push(
        CAPABILITY_CHANNELS.requestFolderGrant,
        CAPABILITY_CHANNELS.listGrants,
        CAPABILITY_CHANNELS.revokeGrant,
      );
    }
    if (workspaceApproval) {
      channels.push(CAPABILITY_CHANNELS.decideWorkspaceApproval);
    }
    if (connectors) {
      // Every connector channel, so teardown actually removes what setup
      // registered — the old list omitted the second authorization channel,
      // which left a live handler behind on dispose.
      channels.push(...Object.values(CONNECTOR_CHANNELS));
    }
    for (const channel of channels) {
      ipcMain.removeHandler(channel);
    }
    bridge.closeAll();
  };
}
