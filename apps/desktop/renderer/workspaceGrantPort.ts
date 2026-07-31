// The DESKTOP host implementation of `WorkspaceGrantPort` — real folder grants.
//
// The IPC this binds to has existed since AC5 slice 1 and had ZERO callers:
// `capability.request-folder-grant` / `list-grants` / `revoke-grant` are handled
// in `main/ipc/handlers.ts`, backed by `CapabilityService`, which owns the native
// picker, the realpath, the encrypted grant store and the loopback broker. The
// grant model was complete and unreachable — which is why the agent's `ls` fell
// through to its virtual memory filesystem and answered a real folder with an
// empty listing. This file is the missing wire.
//
// WHICH WAY A PATH MAY TRAVEL. `capabilities/desktop/workspace_backend.py` keeps
// the property that only a mount name and a root-relative path ever cross to the
// broker, and nothing here weakens it: this port sends NO path at all. Main owns
// the folder selection (the native dialog IS the consent) and answers with
// `RendererGrant` — grantId, mode, label, status — which by construction carries
// no host root (`RendererGrantSchema` is `.strict()`).
//
// A FAILURE IS NEVER AN EMPTY LIST. Every path below either returns real data,
// returns an explicit `cancelled`, or produces a message the surface shows. In
// particular a malformed or unreadable answer from main THROWS rather than
// degrading to `[]`: an empty array here reads to the user as "you have granted
// nothing", which is the same class of lie this subsystem exists to remove. Note
// that "the subsystem is switched off" arrives on exactly this path — the
// channels are then not registered, so `invoke` rejects with Electron's
// no-handler error and the user sees a failure instead of silence.

import type {
  WorkspaceGrant,
  WorkspaceGrantMode,
  WorkspaceGrantOutcome,
  WorkspaceGrantPort,
  WorkspaceGrantRequestInput,
  WorkspaceRevokeOutcome,
} from "@0x-copilot/chat-surface";

import { CAPABILITY_CHANNELS } from "../main/capabilities/channels";
import type { WindowBridge } from "../preload/window-bridge-types";

const GRANT_MODES: readonly WorkspaceGrantMode[] = [
  "read_only",
  "read_write_no_delete",
  "read_write",
];

/** The access a grant request defaults to when the ask named none. */
const DEFAULT_MODE: WorkspaceGrantMode = "read_only";

export class DesktopWorkspaceGrantPortError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DesktopWorkspaceGrantPortError";
  }
}

/**
 * Bind the shared `WorkspaceGrantPort` to the Electron capability IPC.
 *
 * The explicit object literal (and the explicit field-by-field parse below) is
 * deliberate: nothing gets spread across this boundary in either direction.
 */
export function createDesktopWorkspaceGrantPort(
  bridge: WindowBridge,
): WorkspaceGrantPort {
  return Object.freeze({
    async requestGrant(
      input?: WorkspaceGrantRequestInput,
    ): Promise<WorkspaceGrantOutcome> {
      const mode = input?.mode ?? DEFAULT_MODE;
      // `input.path` and `input.reason` are deliberately NOT forwarded. The
      // channel's schema is `.strict()` on `{ mode, label? }`, and the two
      // fields it would accept are the wrong homes for them:
      //   * a path is not accepted at all — main owns the selection, so the
      //     mid-run ask opens the picker rather than pre-selecting the folder
      //     the model named. The card is what shows the user that folder.
      //   * `label` would be worse than useless: a supplied label WINS over the
      //     basename main derives from the folder actually chosen
      //     (`CapabilityService.requestFolderGrant`), so passing the asked path
      //     here would let a pill read "Downloads" over a grant on Documents —
      //     a wrong claim of access, which is the defect, not the fix.
      // Targeting the picker at the asked folder needs a `path` on that schema
      // plus a `defaultPath` on `FolderPicker` (both main-owned, neither in this
      // file's reach).
      try {
        const raw = await bridge.ipc.invoke<unknown>(
          CAPABILITY_CHANNELS.requestFolderGrant,
          { mode },
        );
        // Main returns null for exactly one thing: the user dismissed the
        // native dialog. That is a decision, not a failure.
        if (raw === null) return { status: "cancelled" };
        const grant = parseGrant(raw);
        if (grant === null) {
          return {
            status: "failed",
            message: "The folder grant came back in a form we can't read.",
          };
        }
        if (grant.status !== "active") {
          // A grant minted revoked would be a main-side bug; report it rather
          // than hand back a pill that claims access nothing will honour.
          return {
            status: "failed",
            message: "That folder was granted and immediately revoked.",
          };
        }
        return { status: "granted", grant: grant.grant };
      } catch (cause) {
        return {
          status: "failed",
          message: messageOf(cause, "Couldn't ask for that folder."),
        };
      }
    },

    async listGrants(): Promise<ReadonlyArray<WorkspaceGrant>> {
      const raw = await bridge.ipc.invoke<unknown>(
        CAPABILITY_CHANNELS.listGrants,
        {},
      );
      if (!Array.isArray(raw)) {
        throw new DesktopWorkspaceGrantPortError(
          "Couldn't read your shared folders.",
        );
      }
      const active: WorkspaceGrant[] = [];
      for (const entry of raw) {
        const parsed = parseGrant(entry);
        if (parsed === null) {
          // One unreadable row invalidates the whole answer. Silently skipping
          // it would render a SHORTER list of folders than the user granted,
          // which is a quiet false claim about what the agent can reach.
          throw new DesktopWorkspaceGrantPortError(
            "Couldn't read your shared folders.",
          );
        }
        // `CapabilityService.listGrants` projects `GrantStore.list()`, which
        // includes revoked rows (`listActive()` is the filtered one). The port
        // contract is the ACTIVE set, so the filter happens here.
        if (parsed.status === "active") active.push(parsed.grant);
      }
      return active;
    },

    async revokeGrant(grantId: string): Promise<WorkspaceRevokeOutcome> {
      try {
        const raw = await bridge.ipc.invoke<unknown>(
          CAPABILITY_CHANNELS.revokeGrant,
          { grantId },
        );
        // Main answers null for an id it has never seen. Revocation is
        // idempotent and the end state is the one asked for — the agent cannot
        // read through a grant that does not exist — so this is not a failure.
        if (raw === null) return { status: "revoked" };
        const parsed = parseGrant(raw);
        if (parsed === null) {
          return {
            status: "failed",
            message: "Couldn't confirm that folder was unshared.",
          };
        }
        if (parsed.status !== "revoked") {
          return {
            status: "failed",
            message: "That folder is still shared with the agent.",
          };
        }
        return { status: "revoked" };
      } catch (cause) {
        return {
          status: "failed",
          message: messageOf(cause, "Couldn't stop sharing that folder."),
        };
      }
    },
  });
}

/** The one port per bridge — see {@link bridgeWorkspaceGrantPort}. */
let bound: {
  readonly bridge: WindowBridge;
  readonly port: WorkspaceGrantPort;
} | null = null;

/**
 * Bind the port to the Electron bridge, or `undefined` outside Electron
 * (MockTransport dev, unit tests). Consumers treat an absent port as "this
 * substrate has no folder capability" and render no folder control — the same
 * contract the web host satisfies by supplying nothing.
 *
 * IDENTITY IS PART OF THE CONTRACT, so this memoizes. The shared
 * `useWorkspaceFolderGrants` / `useWorkspaceGrantCardStates` hooks key their
 * grant read on the port's identity, and two desktop call sites need the same
 * port (the cockpit's composer for the folder pills, the cockpit itself for the
 * mid-run ask) — a fresh object per call would re-read the grant list on every
 * render. Keyed on the BRIDGE, not cached unconditionally, so a test that swaps
 * `window.bridge` gets a port bound to the new one instead of a stale closure.
 *
 * Call it wherever it is needed rather than at module scope: a module-scope call
 * is evaluated at import, which makes the folder capability depend on preload
 * having run before the renderer bundle. That happens to be true in the packaged
 * app and is not true in a test, and a wire whose presence depends on module
 * evaluation order is a wire that goes missing without failing.
 */
export function bridgeWorkspaceGrantPort(): WorkspaceGrantPort | undefined {
  if (typeof window === "undefined") return undefined;
  const win = window as unknown as { bridge?: WindowBridge };
  const bridge = win.bridge;
  if (bridge === undefined) return undefined;
  if (bound !== null && bound.bridge === bridge) return bound.port;
  const port = createDesktopWorkspaceGrantPort(bridge);
  bound = { bridge, port };
  return port;
}

/**
 * Parse one `RendererGrant` off IPC into the package's `WorkspaceGrant`, keeping
 * `status` alongside it (the shared type has no status field — it describes an
 * active grant, so the caller decides what a revoked row means).
 *
 * Returns null on ANY shape surprise. The strict key check mirrors main's
 * outbound `RendererGrantSchema.parse`: if a host root ever appeared in this
 * payload we would rather fail loudly here than render it.
 */
function parseGrant(
  value: unknown,
): { readonly grant: WorkspaceGrant; readonly status: string } | null {
  if (!isRecord(value)) return null;
  const keys = Object.keys(value).sort();
  if (
    keys.length !== 4 ||
    keys[0] !== "grantId" ||
    keys[1] !== "label" ||
    keys[2] !== "mode" ||
    keys[3] !== "status"
  ) {
    return null;
  }
  const { grantId, label, mode, status } = value;
  if (
    typeof grantId !== "string" ||
    grantId === "" ||
    typeof label !== "string" ||
    typeof status !== "string" ||
    (status !== "active" && status !== "revoked") ||
    typeof mode !== "string" ||
    !GRANT_MODES.includes(mode as WorkspaceGrantMode)
  ) {
    return null;
  }
  return {
    grant: Object.freeze({
      grantId,
      label,
      mode: mode as WorkspaceGrantMode,
      // `mount` is the broker's opaque per-boot handle, and the renderer
      // projection does not carry one (`RendererGrant` is grantId/mode/label/
      // status, and only the broker holds the salt that derives a mount). The
      // grantId is the opaque handle this host CAN honestly supply: same
      // per-grant identity, no host path, non-reversible. What it does not
      // reproduce is the "two grants on one tree share a mount" property — so a
      // surface must not infer shared trees from it. Fixing that properly means
      // projecting the broker's mount onto `RendererGrant` in main.
      mount: grantId,
    }),
    status,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A thrown bridge becomes a sentence the surface can show. */
function messageOf(cause: unknown, fallback: string): string {
  if (cause instanceof Error && cause.message.trim().length > 0) {
    return cause.message;
  }
  return fallback;
}
