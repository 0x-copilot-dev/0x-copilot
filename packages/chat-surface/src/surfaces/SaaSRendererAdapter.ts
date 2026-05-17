import type { ReactElement } from "react";

// PURE RENDER ONLY (PRD D28 — frozen in Phase 4-A). Adapters MUST NOT:
//   - import or call Transport, MCP, fetch, XMLHttpRequest, EventSource, WebSocket
//   - access window, document, history, navigator, location, localStorage,
//     sessionStorage, document.cookie, navigator.clipboard.write*
//   - emit side effects of any kind (no I/O, no persistence, no IPC)
//
// All actions live in the host (TcSurfaceMount inside chat-surface):
//   - The host fetches current state via Transport / MCP.
//   - The host subscribes to the agent's run stream for proposed diffs.
//   - The host renders the Approve / Reject / Suggest-changes controls
//     AROUND the adapter's output (the adapter does not render them).
//   - The host calls Transport on approve; queues regen on reject /
//     suggest-changes; falls back to tier-3 on adapter throw or render
//     budget overrun (PRD D29).
//
// Same contract for tier-1 (hand-built), tier-2 (agent-generated, sandboxed
// in Phase 6), and tier-3 (GenericStructuredDiff — the generic fallback).
// All three implement this interface; the host does not branch on tier.

export type SaaSRendererAdapterOrigin =
  | "first-party"
  | "agent-generated"
  | "community";

export interface SaaSRendererAdapterMetadata {
  readonly origin: SaaSRendererAdapterOrigin;
  readonly generatedAt?: string;
  readonly generatorModel?: string;
  readonly schemaVersion: number;
}

export interface SaaSRendererAdapter<TResource = unknown, TDiff = unknown> {
  readonly scheme: string;
  readonly matches: (uri: string) => boolean;
  readonly renderCurrent: (state: TResource) => ReactElement;
  readonly renderDiff: (diff: TDiff) => ReactElement;
  readonly metadata: SaaSRendererAdapterMetadata;
}

// Wildcard scheme reserved for the tier-3 GenericStructuredDiff fallback.
// SurfaceRegistry.resolveAdapter consults wildcards only after every exact
// scheme match has missed or been marked broken (PRD §3.3, §3.4).
export const TIER3_SCHEME = "*";
