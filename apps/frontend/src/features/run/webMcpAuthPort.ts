// WC-P5b (AD-6/AD-8) — the WEB host implementation of `McpAuthPort`.
//
// chat-surface defines the `McpAuthPort` TYPE and calls it from the in-chat
// `mcp_auth` Connect card (P5a), but never implements it: the full-page redirect,
// the `sessionStorage` stash, and the `/mcp/oauth/callback` route are all
// substrate-specific and stay host-owned (NFR-5). This factory is the web
// launcher — the desktop host supplies the same three verbs over Electron IPC.
//
// The card hands the port only a `serverId` (AD-6), but the blocking gate's
// approval id — which the resume needs — is `mcp_auth:<run_id>:<server_id>`
// (services/ai-backend .../capabilities/mcp/middleware/auth_mcp.py). So `beginAuth`
// resolves the bound conversation's active run id (`resolveActiveRunId`, over the
// same conversation-head seam `useRunSession` reads) and rebuilds that same
// deterministic id, which `rememberPendingMcpAuthAction` (mcpAuthAction.ts,
// reused UNCHANGED) parses to derive the run id for the stash. On OAuth return
// App's `/mcp/oauth/callback` effect reads the stash back and mints
// `completedMcpAuthAction`; `RunRoute` maps run→conversation and re-opens it, and
// `useRunSession` self-resumes the stream from its cursor (AD-8) — no resume code
// in the package, and NEVER the `/v1/agent/approvals/{id}/decision` POST (that
// route resolves `mcp_tool` / `ask_a_question` approvals, not connector auth).
//
// The connector operations + the redirect are injected as narrow functions so the
// port stays substrate-honest and unit-testable; `RunRoute` wires them over
// `api/mcpApi` (the same facade layer `composerConnectorsPort` uses) + a
// `window.location` assignment. `apps/frontend` calls the facade only.

import type { McpAuthPort } from "@0x-copilot/chat-surface";

import {
  clearPendingMcpAuthAction,
  readPendingMcpAuthAction,
  rememberPendingMcpAuthAction,
} from "../chat/mcpAuthAction";

/**
 * Host-supplied plumbing the web {@link McpAuthPort} drives. Each is an injected
 * function (not a concrete port) so the launcher is testable with plain mocks and
 * never reaches for a browser primitive itself.
 */
export interface WebMcpAuthPortDeps {
  /**
   * Resolve the active run of the conversation the Connect card belongs to, or
   * `null` when there is none. The resume maps run→conversation (AD-8), and the
   * `mcp_auth:<run_id>:<server_id>` approval id embeds the run id, but the card
   * only hands the port a `serverId`; we recover the run id here from the bound
   * conversation's head (`GET /v1/agent/conversations/{id}` → `latest_run_id`,
   * the seam `useRunSession` reads). `null` (no active run / probe failed) simply
   * skips the stash — the launcher still starts OAuth, it just cannot self-resume.
   */
  readonly resolveActiveRunId: () => Promise<string | null>;
  /** Start the connector OAuth round-trip; resolves to the vendor authorization URL. */
  readonly startAuth: (serverId: string) => Promise<string>;
  /** Record a skip so the run does not re-prompt for this connector this run. */
  readonly recordSkip: (serverId: string) => Promise<void>;
  /** Install a connector from the catalog; resolves to the freshly minted server id. */
  readonly installConnector: (slug: string) => Promise<string>;
  /** Host redirect to the vendor consent screen (web: a `window.location` assignment). */
  readonly redirect: (url: string) => void;
  /**
   * Surface a launch failure to the host (best-effort). The card's Connect/Skip
   * are fire-and-forget, so a rejection is swallowed here rather than thrown back
   * into the render; the host can log it or show a status line.
   */
  readonly onError?: (error: unknown) => void;
}

// Rebuild the blocking auth gate's approval id exactly as the runtime mints it
// (`mcp_auth:<run_id>:<server_id>`) so `rememberPendingMcpAuthAction` derives the
// run id from it. This keeps the stash shape unchanged — we feed the existing
// helper the id it expects rather than inventing a new stash format.
function mcpAuthApprovalId(runId: string, serverId: string): string {
  return `mcp_auth:${runId}:${serverId}`;
}

/**
 * Build the web {@link McpAuthPort}. Injected into the Run cockpit
 * (`RunRoute` → `RunDestination.mcpAuthPort` → `TcChat`) so the in-chat
 * `mcp_auth` Connect card can start / skip / install a connector.
 */
export function createWebMcpAuthPort(deps: WebMcpAuthPortDeps): McpAuthPort {
  function beginAuth(serverId: string): void {
    void (async () => {
      try {
        const runId = await deps.resolveActiveRunId();
        if (runId !== null) {
          // Stash BEFORE the redirect: the full-page navigation to the vendor's
          // consent screen tears the app down, so `sessionStorage` is the only
          // breadcrumb App's callback can read back to resume the run (AD-8).
          rememberPendingMcpAuthAction({
            approvalId: mcpAuthApprovalId(runId, serverId),
            serverId,
          });
        }
        const authUrl = await deps.startAuth(serverId);
        deps.redirect(authUrl);
      } catch (error) {
        deps.onError?.(error);
      }
    })();
  }

  function skipAuth(serverId: string): void {
    // Dismiss the gate WITHOUT a `/v1/agent/approvals/{id}/decision` POST — a
    // connector-auth gate never resolves that way, and a `mcp_discovery:`
    // suggestion has no persisted approval row (the POST would 404, AD-7). Clear
    // any stash we placed for this server, then record the skip so the run does
    // not re-prompt (best-effort; a discovery suggestion has nothing to skip
    // server-side, so a rejection is swallowed rather than surfaced as a crash).
    if (readPendingMcpAuthAction(serverId) !== null) {
      clearPendingMcpAuthAction();
    }
    void (async () => {
      try {
        await deps.recordSkip(serverId);
      } catch (error) {
        deps.onError?.(error);
      }
    })();
  }

  function installFromCatalog(slug: string): void {
    // Catalog-suggestion path (`mcp_discovery:`): install the connector, then
    // begin OAuth on the freshly minted server. The in-chat Connect card wires
    // Connect→`beginAuth` because the approval payload always carries a
    // `server_id` but not a catalog slug, so this method is exercised only by a
    // host catalog launcher; implemented here for completeness + symmetry with
    // the port contract.
    void (async () => {
      try {
        const serverId = await deps.installConnector(slug);
        beginAuth(serverId);
      } catch (error) {
        deps.onError?.(error);
      }
    })();
  }

  return { beginAuth, skipAuth, installFromCatalog };
}
