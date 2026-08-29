/**
 * Keep a broken stdout/stderr pipe from killing the app.
 *
 * WHY THIS EXISTS, because it looks like defensive noise until you know the
 * launch shape. `tools/cli/bin/copilot.mjs` spawns the app with
 * `stdio: "inherit"`, so the GUI process writes straight to the terminal that
 * ran `copilot`. That terminal is not the app's lifetime — the window outlives
 * it. Close the terminal (or end the ssh session, or quit the shell) and the
 * app keeps running with stdout pointing at a pipe whose reader is gone.
 *
 * The next `console.error` in the main process then raises `EPIPE`, and with no
 * listener on the stream Node turns it into an uncaught exception that takes
 * down the whole app. Observed exactly that way:
 *
 *     Uncaught Exception:
 *     Error: write EPIPE
 *         at afterWriteDispatched (node:internal/stream_base_commons:159:15)
 *         ...
 *         at console.error (node:internal/console/constructor:444:26)
 *         at replyWithError (node:electron/js2c/browser_init)
 *
 * Read that trace bottom-up: the main process was REPORTING an IPC error, and
 * the reporting killed it. A failure in the error path is the worst place to
 * have one, because it converts a handled problem into a crash.
 *
 * There are ~30 `console.*` call sites in `main/`, so guarding them one by one
 * is not the fix; attaching a listener to the stream is. A stream with an
 * `error` listener no longer throws, and EPIPE genuinely has nothing to do
 * about it — the reader is gone and there is nowhere to log the fact that
 * there is nowhere to log. Anything that is NOT EPIPE is re-thrown, because a
 * disk-full or permission error on stderr is a real failure and swallowing
 * every stream error to fix one of them is how you lose the next one.
 */

import type { Writable } from "node:stream";

/** The one error code that means "the reader went away, and that is fine". */
const BROKEN_PIPE = "EPIPE";

/**
 * Attach EPIPE-tolerant error handling to the process's output streams.
 *
 * Call this as early in main as possible — before any logging, because the
 * write it protects can be the first one. Returns a disposer so a test can
 * remove the listeners it added rather than leaking them across cases.
 */
export function installStdioResilience(
  streams: readonly Writable[] = [process.stdout, process.stderr],
): () => void {
  const attached: Array<[Writable, (error: Error) => void]> = [];

  for (const stream of streams) {
    const onError = (error: Error): void => {
      if ((error as NodeJS.ErrnoException).code === BROKEN_PIPE) {
        return;
      }
      throw error;
    };
    stream.on("error", onError);
    attached.push([stream, onError]);
  }

  return () => {
    for (const [stream, onError] of attached) {
      stream.off("error", onError);
    }
  };
}
