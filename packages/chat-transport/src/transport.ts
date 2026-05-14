import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  TransportCapabilities,
  TypedRequest,
} from "./types";

// Substrate-agnostic port between the chat surface and any HTTP/SSE-capable
// backend. Two implementations: WebTransport (fetch + same-origin SSE) and,
// in a later phase, WebviewTransport (postMessage RPC to a host extension).
//
// Why an interface and not a concrete client: the chat surface ships in two
// substrates (browser tab, VS Code webview) with different security models
// for token handling. The contract here is what's the same in both.
//
// SSE primitive is intentionally generic (raw message strings, not parsed
// envelopes). Domain-aware envelope parsing lives in the API modules above
// this layer — that keeps the Transport interface stable as new event types
// are added, and lets the desktop bridge marshal SSE without coupling to
// agent-specific schemas.
export interface Transport {
  request<TRes>(req: TypedRequest): Promise<TRes>;
  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription;
  getSession(): Session;
  capabilities(): TransportCapabilities;
}
