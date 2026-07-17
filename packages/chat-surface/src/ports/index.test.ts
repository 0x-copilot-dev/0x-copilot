import {
  UnauthorizedError as TransportUnauthorizedError,
  type HttpMethod as TransportHttpMethod,
  type QueryParamValue as TransportQueryParamValue,
  type Session as TransportSession,
  type SseSubscribeOptions as TransportSseSubscribeOptions,
  type SseSubscription as TransportSseSubscription,
  type Transport as TransportTransport,
  type TransportCapabilities as TransportTransportCapabilities,
  type TypedRequest as TransportTypedRequest,
} from "@0x-copilot/chat-transport";
import { describe, expect, it } from "vitest";

import type {
  ArtifactRoute as RouterArtifactRoute,
  NavigateOptions as RouterNavigateOptions,
  Router as RouterRouter,
} from "../routing/router";
import type { PresenceSignal as PresencePresenceSignal } from "../presence/presence-signal";
import type { PresenceState as PresencePresenceState } from "../presence/presence-signal";
import type { KeyValueStore as StorageKeyValueStore } from "../storage/key-value-store";

import {
  UnauthorizedError,
  type ArtifactRoute,
  type HttpMethod,
  type KeyValueStore,
  type NavigateOptions,
  type PresenceSignal,
  type PresenceState,
  type QueryParamValue,
  type Router,
  type Session,
  type SseSubscribeOptions,
  type SseSubscription,
  type SurfaceEvent,
  type SurfaceHandle,
  type SurfaceHost,
  type Transport,
  type TransportCapabilities,
  type TypedRequest,
} from "./index";

describe("ports facade", () => {
  it("re-exports the chat-transport UnauthorizedError class identity", () => {
    expect(UnauthorizedError).toBe(TransportUnauthorizedError);
    const err = new UnauthorizedError("nope");
    expect(err).toBeInstanceOf(TransportUnauthorizedError);
    expect(err.status).toBe(401);
  });

  it("round-trips transport types: facade ↔ producer assignment compatible", () => {
    const a: Transport = null as unknown as TransportTransport;
    const b: TransportTransport = null as unknown as Transport;
    const session: Session = null as unknown as TransportSession;
    const producerSession: TransportSession = null as unknown as Session;
    const sub: SseSubscribeOptions =
      null as unknown as TransportSseSubscribeOptions;
    const producerSub: TransportSseSubscribeOptions =
      null as unknown as SseSubscribeOptions;
    const subscription: SseSubscription =
      null as unknown as TransportSseSubscription;
    const producerSubscription: TransportSseSubscription =
      null as unknown as SseSubscription;
    const caps: TransportCapabilities =
      null as unknown as TransportTransportCapabilities;
    const producerCaps: TransportTransportCapabilities =
      null as unknown as TransportCapabilities;
    const req: TypedRequest = null as unknown as TransportTypedRequest;
    const producerReq: TransportTypedRequest = null as unknown as TypedRequest;
    const method: HttpMethod = "GET" satisfies TransportHttpMethod;
    const producerMethod: TransportHttpMethod = "POST" satisfies HttpMethod;
    const qp: QueryParamValue = "v" satisfies TransportQueryParamValue;
    const producerQp: TransportQueryParamValue = 42 satisfies QueryParamValue;
    expect([
      a,
      b,
      session,
      producerSession,
      sub,
      producerSub,
      subscription,
      producerSubscription,
      caps,
      producerCaps,
      req,
      producerReq,
      method,
      producerMethod,
      qp,
      producerQp,
    ]).toHaveLength(16);
  });

  it("round-trips router types: facade ↔ producer assignment compatible", () => {
    const r: Router<string> = null as unknown as RouterRouter<string>;
    const producerR: RouterRouter<string> = null as unknown as Router<string>;
    const route: ArtifactRoute = null as unknown as RouterArtifactRoute;
    const producerRoute: RouterArtifactRoute = null as unknown as ArtifactRoute;
    const opts: NavigateOptions = null as unknown as RouterNavigateOptions;
    const producerOpts: RouterNavigateOptions =
      null as unknown as NavigateOptions;
    expect([
      r,
      producerR,
      route,
      producerRoute,
      opts,
      producerOpts,
    ]).toHaveLength(6);
  });

  it("round-trips storage and presence ports: facade ↔ producer assignment compatible", () => {
    const kv: KeyValueStore = null as unknown as StorageKeyValueStore;
    const producerKv: StorageKeyValueStore = null as unknown as KeyValueStore;
    const presence: PresenceSignal = null as unknown as PresencePresenceSignal;
    const producerPresence: PresencePresenceSignal =
      null as unknown as PresenceSignal;
    const state: PresenceState = "visible" satisfies PresencePresenceState;
    const producerState: PresencePresenceState =
      "hidden" satisfies PresenceState;
    expect([
      kv,
      producerKv,
      presence,
      producerPresence,
      state,
      producerState,
    ]).toHaveLength(6);
  });

  it("exposes SurfaceHost / SurfaceHandle / SurfaceEvent with the frozen shape", () => {
    const handle: SurfaceHandle = { id: "h-1" };
    const event: SurfaceEvent = {
      surfaceId: "s-1",
      type: "rect-changed",
      payload: { width: 800, height: 600 },
    };
    const host: SurfaceHost = {
      async mountSurface(args) {
        return { id: args.id };
      },
      async unmountSurface() {},
      async pauseSurface() {},
      async resumeSurface() {},
      async snapshotSurface() {
        return new Blob();
      },
      onSurfaceEvent() {
        return () => {};
      },
    };
    expect(handle.id).toBe("h-1");
    expect(event.surfaceId).toBe("s-1");
    expect(typeof host.mountSurface).toBe("function");
    expect(typeof host.unmountSurface).toBe("function");
    expect(typeof host.pauseSurface).toBe("function");
    expect(typeof host.resumeSurface).toBe("function");
    expect(typeof host.snapshotSurface).toBe("function");
    expect(typeof host.onSurfaceEvent).toBe("function");
  });
});
