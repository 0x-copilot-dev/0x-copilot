// PRD-P8 §8 — the first-run local-model card's "Get Ollama ↗" external open.
//
// SECURITY CONTRACT (the reason this module exists rather than a generic
// `openExternal` channel):
//
//   The renderer cannot open an external URL on desktop. `main/index.ts`'s
//   `web-contents-created` hook denies every `window.open`
//   (`setWindowOpenHandler(() => ({ action: "deny" }))`) and prevents any
//   `will-navigate` off the `app://` origin. That denial is load-bearing: it
//   means a script that reaches the renderer — an injected string in an agent
//   response, a compromised dependency — cannot drive the user's default
//   browser to an attacker-chosen origin.
//
//   So the channel registered here takes **NO URL ARGUMENT**. The destination
//   is `OLLAMA_DOWNLOAD_URL`, a constant compiled into main. The handler's
//   signature does not even bind the IPC payload, so there is no parameter for
//   a caller to influence and no validation to get wrong. A generic
//   "openExternal(url)" channel would have re-opened exactly the hole the
//   window-open denial closes.
//
// Precedent: every other system-browser open in this app is likewise
// main-owned and main-addressed — `auth/google-login.ts`, `auth/wallet-login.ts`
// and `connectors/oauth-coordinator.ts` all build their URL in main from
// facade-issued values and call `shell.openExternal` there. The renderer asks
// for an INTENT ("get Ollama", "connect this slug"); main decides the URL.
//
// NOT part of the capability-grant model (`main/capabilities/`), deliberately:
// that subsystem exists to gate the AGENT's reach into the user's filesystem —
// user-consented, per-path, encrypted-at-rest grants redeemed over an
// authenticated loopback broker that a renderer context is explicitly forbidden
// to touch, all behind the `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` opt-in. Its unit
// of consent is "which host paths may be read/written". This channel has no
// such unit: it is a user-initiated click that opens one fixed, public,
// non-sensitive vendor page, reads nothing, writes nothing, returns nothing
// about the host, and cannot be parameterized. Wrapping it in a grant would add
// a consent prompt with no decision in it while implying, falsely, that the
// renderer had gained a general host-open capability. What actually bounds the
// risk here is the constant, and that is enforced structurally below.

import { FIRST_RUN_CHANNELS } from "./first-run-channels";

/**
 * The one destination this channel can ever open. Matches the link the
 * Settings → Local models page renders on web
 * (`packages/chat-surface/src/settings/LocalModelsPage.tsx`).
 */
export const OLLAMA_DOWNLOAD_URL = "https://ollama.com/download";

export interface OpenOllamaDownloadResult {
  readonly ok: boolean;
  readonly error?: string;
}

/**
 * The slice of Electron's `ipcMain` this module uses. Declared structurally
 * (rather than `Pick<IpcMain, …>`) so the module imports no Electron types it
 * does not need and a test fake satisfies it without a cast — the real
 * `ipcMain` satisfies it too.
 */
export interface OllamaDownloadIpcMain {
  handle(
    channel: string,
    listener: (event: unknown, ...args: unknown[]) => unknown,
  ): void;
  removeHandler(channel: string): void;
}

export interface OllamaDownloadIpcDeps {
  readonly ipcMain: OllamaDownloadIpcMain;
  /** `shell.openExternal`, injected so this module stays testable. */
  readonly openExternal: (url: string) => Promise<unknown>;
  readonly logger?: { warn(message: string, err: unknown): void };
}

const defaultLogger = {
  warn(message: string, err: unknown): void {
    console.warn(`[first-run] ${message}`, err);
  },
};

/**
 * Registers the argument-free "open the Ollama download page" IPC handler.
 * Returns a teardown that removes it.
 *
 * The handler resolves a `{ ok }` result rather than rejecting: Electron
 * flattens a rejected `invoke` to a bare message string, and the renderer's
 * `onGetOllama` is fire-and-forget. A failed open is a UX nit (state ①'s watch
 * line still tells the user what to do), never a reason to surface a raw
 * main-process error string in the FTUE.
 */
export function registerOllamaDownloadIpc(
  deps: OllamaDownloadIpcDeps,
): () => void {
  const logger = deps.logger ?? defaultLogger;

  // NOTE the signature: no `_event`, no `payload`. Whatever the renderer sends
  // is unreachable from this closure — the URL is the module constant, always.
  deps.ipcMain.handle(
    FIRST_RUN_CHANNELS.openOllamaDownload,
    async (): Promise<OpenOllamaDownloadResult> => {
      try {
        await deps.openExternal(OLLAMA_DOWNLOAD_URL);
        return { ok: true };
      } catch (err) {
        logger.warn("open Ollama download page failed:", err);
        return {
          ok: false,
          error: err instanceof Error ? err.message : "unknown error",
        };
      }
    },
  );

  return () => {
    deps.ipcMain.removeHandler(FIRST_RUN_CHANNELS.openOllamaDownload);
  };
}
