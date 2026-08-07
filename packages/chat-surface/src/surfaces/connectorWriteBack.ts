// Save on a CONNECTOR-origin surface — the half of the design's Save table that
// cannot be local.
//
// An artifact origin splices the user's cell edits into its own source and makes
// a new revision: no model, no approval, nothing leaves the machine. A connector
// origin has to become a real request to a vendor, so Save here does not write —
// it POSTs the batch to `/v1/agent/surfaces/{surface_id}/write-back`, which maps
// it onto one connector op and STAGES it. The rows come back proposed. Applying
// them is the write gate's job and a second, deliberate gesture the user makes
// there; nothing in this module can reach it.
//
// Three properties this file exists to hold:
//
// 1. **The values are the user's, verbatim.** `changes[].new` is what was typed
//    into the cell and `row` is that row exactly as the connector read it. The
//    model downstream picks the op and maps field NAMES; it never retypes a
//    value, and the `target_args` the user approves at the gate are composed
//    from these two halves alone. A caller that pre-formats a value here has
//    quietly changed what gets written.
//
// 2. **A failure is loud and keeps the batch.** Every rejection resolves to an
//    `{status: "error"}` with a safe sentence, never a throw and never a silent
//    success — a save the user believes happened is the failure mode that
//    matters when the target is their real Linear. The caller keeps the pending
//    edits on screen, so nothing they typed is lost to a 503.
//
// 3. **Editability is the HOST's to grant.** `attachConnectorEditor` is how a
//    live save function reaches a pure renderer: it rides on the render STATE,
//    which the host built, never on the `SurfaceSpec`, which a model authored.
//    A surface the host did not open carries no editor and renders read-only.

import type {
  StagedWriteView,
  SurfaceRowEdit,
  SurfaceWriteBackRequest,
} from "@0x-copilot/api-types";
import {
  isTransportHttpError,
  type Transport,
} from "@0x-copilot/chat-transport";

/**
 * The write-back path for one surface.
 *
 * A surface id is a URI (`table://linear/list_issues/ENG-4`), so it is encoded
 * whole — slashes included. The route is registered with a plain `{surface_id}`
 * segment, and the server decodes it back before folding the run's ledger for
 * that surface.
 */
export function surfaceWriteBackPath(surfaceId: string): string {
  return `/v1/agent/surfaces/${encodeURIComponent(surfaceId)}/write-back`;
}

/** What a Save resolved to. Never a throw — see property 2 in the header. */
export type ConnectorWriteBackResult =
  | {
      readonly status: "staged";
      readonly stageId: string;
      /** Rows proposed by this save (`row_counts.total`, or `rows.length`). */
      readonly rowCount: number;
      /** Rows the agent or a policy pre-held; they need a decision at the gate. */
      readonly heldCount: number;
    }
  | { readonly status: "error"; readonly message: string };

/**
 * The host's permission to stage a connector write, and the one call that does.
 *
 * Deliberately the same SHAPE as `ArtifactEditorActions` in `surface-renderers`
 * — `{disabled, <one save>}` — because a renderer should not learn a second
 * grammar for "the host opened this". What differs is the verb, and it differs
 * honestly: an artifact `saveRevision` RETURNS a saved revision, this one
 * returns a PROPOSAL.
 */
export interface ConnectorSurfaceEditorActions {
  readonly disabled: boolean;
  /**
   * The surface this grant writes to. Carried so the renderer has an identity
   * to reset its batch against — a different surface is a different set of
   * rows, and edits typed against one must never be spliced into another.
   */
  readonly surfaceId: string;
  readonly saveEdits: (
    edits: readonly SurfaceRowEdit[],
  ) => Promise<ConnectorWriteBackResult>;
}

/** The safe sentences this module owns. Constants — never server prose. */
const MESSAGES = {
  EMPTY: "There is nothing to save.",
  UNCONFIGURED:
    "Saving to this connector is not configured for this deployment. Nothing was staged.",
  GONE: "This surface is no longer available, so nothing was staged.",
  UNREADABLE: "The server did not return a staged write. Nothing was staged.",
  FAILED: "The save could not be prepared. Nothing was staged.",
} as const;

/**
 * The server's own sentence when it is safe to show, else ours.
 *
 * The write-back route answers a refusal with a constant it owns (no provider
 * key, no write op on this connector, this surface has no write target) and
 * those are the sentences worth reading — they name what the user can fix. A
 * detail that is not a plain string is a shape we did not write, so it is
 * replaced rather than rendered.
 */
function messageFor(error: unknown): string {
  if (!isTransportHttpError(error)) return MESSAGES.FAILED;
  if (error.status === 503) return MESSAGES.UNCONFIGURED;
  if (error.status === 404) return MESSAGES.GONE;
  const detail = error.detail;
  if (typeof detail === "string" && detail.trim() !== "") return detail;
  return MESSAGES.FAILED;
}

/** Narrow the response without trusting it: a body we cannot read is a failure. */
function stagedFrom(response: unknown): ConnectorWriteBackResult {
  if (typeof response !== "object" || response === null) {
    return { status: "error", message: MESSAGES.UNREADABLE };
  }
  const view = response as Partial<StagedWriteView>;
  if (typeof view.stage_id !== "string" || view.stage_id === "") {
    return { status: "error", message: MESSAGES.UNREADABLE };
  }
  const rows = Array.isArray(view.rows) ? view.rows : [];
  const counts = view.row_counts;
  return {
    status: "staged",
    stageId: view.stage_id,
    rowCount: typeof counts?.total === "number" ? counts.total : rows.length,
    heldCount: typeof counts?.held === "number" ? counts.held : 0,
  };
}

export interface ConnectorSurfaceEditorConfig {
  readonly transport: Transport;
  /** The run that owns the surface. `null` ⇒ no editor is granted. */
  readonly runId: string | null;
  /** The surface id, which IS the canvas tab's URI. */
  readonly surfaceId: string;
  /**
   * Renders the cells inert while leaving the grant in place. No host sets it
   * today: the cockpit withholds the whole resolver while the canvas is
   * scrubbed, so a time-travelled surface has no editor by construction rather
   * than a live one told to behave. Kept for the host that needs to disable
   * without un-granting, and defaults to enabled so absence means nothing.
   */
  readonly disabled?: boolean;
}

/**
 * Build the grant for one connector surface, or `null` when there is no run to
 * write against — an unbound canvas renders read-only rather than offering a
 * Save that has nowhere to go.
 */
export function createConnectorSurfaceEditor(
  config: ConnectorSurfaceEditorConfig,
): ConnectorSurfaceEditorActions | null {
  const { transport, runId, surfaceId } = config;
  if (runId === null || runId === "" || surfaceId === "") return null;
  return {
    disabled: config.disabled === true,
    surfaceId,
    saveEdits: async (
      edits: readonly SurfaceRowEdit[],
    ): Promise<ConnectorWriteBackResult> => {
      if (edits.length === 0) {
        return { status: "error", message: MESSAGES.EMPTY };
      }
      const body: SurfaceWriteBackRequest = { run_id: runId, edits };
      try {
        const response = await transport.request<unknown>({
          method: "POST",
          path: surfaceWriteBackPath(surfaceId),
          body,
        });
        return stagedFrom(response);
      } catch (error) {
        return { status: "error", message: messageFor(error) };
      }
    },
  };
}

/**
 * The field a renderer reads its connector grant from.
 *
 * One name, exported, because the attach and the read live in different
 * packages and a literal in each is how they come to disagree.
 */
export const CONNECTOR_EDITOR_FIELD = "connectorEditor";

/**
 * Attach the grant to a surface's render state.
 *
 * Duck-typed onto the state object rather than added to `SurfaceState`, and
 * that is the whole design: `SurfaceState` is the api-types WIRE contract, and
 * a live function the host closed over its own transport is emphatically not on
 * the wire. It is also not on the `SurfaceSpec` — the model authors specs, so a
 * spec able to carry a handler would be a model-authored side effect.
 *
 * Total: a state that is not an object, or a `null` editor, is returned
 * unchanged, which is the read-only answer and the answer for every surface the
 * host did not deliberately open.
 */
export function attachConnectorEditor(
  state: unknown,
  editor: ConnectorSurfaceEditorActions | null,
): unknown {
  if (editor === null || typeof state !== "object" || state === null) {
    return state;
  }
  return {
    ...(state as Record<string, unknown>),
    [CONNECTOR_EDITOR_FIELD]: editor,
  };
}
