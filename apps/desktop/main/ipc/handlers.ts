import type { IpcMain, IpcMainInvokeEvent } from "electron";
import type { z } from "zod";

import type { RendererSession } from "@enterprise-search/chat-transport";

import type { TransportBridge } from "../transport-bridge";
import {
  AuthWorkspaceParamsSchema,
  CHANNELS,
  EmptyParamsSchema,
  IpcValidationError,
  Tier2BoundaryErrorPayloadSchema,
  TransportRequestParamsSchema,
  TransportSubscribeParamsSchema,
  TransportUnsubscribeParamsSchema,
  type Tier2BoundaryErrorPayload,
} from "./schemas";

export interface AuthHandlers {
  signIn(workspaceId: string): Promise<RendererSession>;
  /** "Continue with Google" — system browser + loopback handoff. */
  signInWithGoogle(workspaceId: string): Promise<RendererSession>;
  signOut(workspaceId: string): Promise<void>;
  getSession(workspaceId: string): Promise<RendererSession | null>;
  refresh(workspaceId: string): Promise<RendererSession | null>;
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
  readonly logger?: IpcLogger;
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
    return bridge.request(params);
  });

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

  return () => {
    const channels: string[] = [
      CHANNELS.transportRequest,
      CHANNELS.transportSubscribe,
      CHANNELS.transportUnsubscribe,
      CHANNELS.transportSessionSnapshot,
    ];
    if (auth) {
      channels.push(
        CHANNELS.authGetSession,
        CHANNELS.authSignIn,
        CHANNELS.authSignInGoogle,
        CHANNELS.authSignOut,
        CHANNELS.authRefresh,
      );
    }
    if (tier2) {
      channels.push(CHANNELS.tier2BoundaryError);
    }
    for (const channel of channels) {
      ipcMain.removeHandler(channel);
    }
    bridge.closeAll();
  };
}
