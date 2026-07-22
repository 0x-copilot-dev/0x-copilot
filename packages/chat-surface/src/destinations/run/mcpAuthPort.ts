// WC-P5a (AD-6) — the MCP-OAuth launcher port TYPE.
//
// Source: docs/plan/web-convergence/PRD.md — AD-6 (typed `McpAuthPort`, not a
// bare callback) + AD-7 (`mcp_auth` Connect card, distinct from `resolveApproval`)
// + FR-5 (mid-run MCP-OAuth). This file is the ONE genuinely substrate-divergent
// capability in the Run cockpit: starting a connector's OAuth flow. Everything
// else the cockpit needs (cancel, conversation-nav, citations) has zero substrate
// divergence and so is deliberately NOT a port (AD-4/AD-10).
//
// WHAT THIS IS: an interface the HOST implements. chat-surface defines the type
// and calls it from the in-chat `mcp_auth` Connect card (`TcChat`), but it NEVER
// implements it — the concrete impl lives in the host:
//   - web    (`apps/frontend`, P5b): over `createComposerConnectorsPort(identity)`
//            → `connectors.authenticate(serverId)` does a full-page redirect to
//            the vendor's consent screen after stashing the run id in
//            `sessionStorage`; the `/mcp/oauth/callback` route resumes the run.
//   - desktop (`apps/desktop`): the same three verbs over Electron IPC.
//
// WHAT NEVER ENTERS THIS PACKAGE (NFR-1/NFR-5): the full-page redirect, the
// `sessionStorage` stash, the `/mcp/oauth/callback` route detection, and the
// router/URL navigation all stay host-owned. chat-surface is browser-primitive-
// free (no `window`/`location`/`sessionStorage`/`fetch`/`EventSource`, eslint-
// banned) — the port is a pure TYPE with no runtime code, so importing it adds
// no substrate coupling.
//
// WHY A PORT, NOT A `/decision` POST (AD-7): an `mcp_auth` gate does NOT resolve
// through `POST /v1/agent/approvals/{id}/decision` like a normal `mcp_tool` /
// `tool_action` / `ask_a_question` approval. It resolves via a separate
// `mcp_auth_resolved` decision AFTER OAuth returns (the host's job in P5b), and a
// `mcp_discovery:` suggestion is never a persisted approval row at all, so a
// `/decision` POST would 404. The Connect card therefore invokes this port and
// leaves `resolveApproval` untouched.

/**
 * Host-supplied launcher for a connector server's OAuth flow. Injected into the
 * Run cockpit (`RunDestination.mcpAuthPort` → `TcChat.mcpAuthPort`) so the in-chat
 * `mcp_auth` Connect card can start / skip / install a connector WITHOUT touching
 * any browser primitive or the `/decision` POST. All three verbs are best-effort,
 * fire-and-forget from the card's perspective — the host owns the redirect,
 * error surfacing, and the post-OAuth run→conversation resume (AD-8).
 *
 * When the host does not supply a port (no launcher wired yet), the Connect card
 * degrades gracefully: it still renders the auth gate, but the Connect / Skip
 * affordances are inert (never a crash, never a `/decision` fallback).
 */
export interface McpAuthPort {
  /**
   * Begin OAuth for an already-installed connector server (the blocking
   * `mcp_auth:<run_id>:<server_id>` gate). The host stashes the run id, then
   * full-page-redirects (web) / opens the system browser (desktop) to the
   * vendor's consent screen. On return it resolves the run→conversation and
   * rebinds the stream (AD-8) — no chat-surface resume code needed.
   */
  beginAuth(serverId: string): void;

  /**
   * Dismiss / skip the auth gate for this server without connecting. The host
   * records the skip (web: `connectors.skipAuth(serverId)`), so the agent does
   * not re-prompt for it this run. Never a `/decision` POST — a `mcp_discovery:`
   * suggestion has no persisted approval row to resolve.
   */
  skipAuth(serverId: string): void;

  /**
   * Install a connector from the catalog by slug (discovery → install → OAuth),
   * the `mcp_discovery:` catalog-suggestion path. The host creates the
   * `mcp_servers` row (web: `connectors.installFromCatalog(slug)`) and then
   * begins OAuth on the freshly minted server. Provided for the host's catalog
   * launcher; the in-chat card wires Connect to {@link beginAuth} because the
   * approval payload always carries a `server_id` but not a catalog slug.
   */
  installFromCatalog(slug: string): void;
}
