import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";

export interface LoopbackHandle {
  readonly port: number;
  readonly redirectUri: string;
  readonly codePromise: Promise<LoopbackCode>;
  /**
   * Arm (or replace) the expected `state` after the server is already
   * listening. Needed by flows where the state is produced by a server
   * that must first be told the loopback's redirect_uri (facade-brokered
   * Google login): bind → call /start with the redirectUri → arm the
   * returned state → open the browser.
   */
  armState(state: string): void;
  close(): void;
}

export interface LoopbackCode {
  readonly code: string;
  readonly state: string;
}

export interface RandomPortOptions {
  /** Bind attempts before giving up (default 5). */
  readonly attempts?: number;
  /** Port picker, injectable for tests. Default: random in [16384, 65535). */
  readonly pick?: () => number;
}

export interface AwaitLoopbackOptions {
  /**
   * Expected `state` known up-front. Optional — flows that only learn the
   * state after binding pass nothing here and call `handle.armState()`
   * later. Requests arriving while no state is armed are answered with
   * the failure page but do NOT reject `codePromise` (the sign-in cannot
   * legitimately have started yet).
   */
  readonly expectedState?: string;
  readonly callbackPath?: string;
  readonly timeoutMs?: number;
  readonly successHtml?: string;
  readonly failureHtml?: string;
  /**
   * When set, bind an explicitly chosen random port and retry on
   * EADDRINUSE instead of delegating to the OS with port 0. Use for
   * flows against OAuth clients where a bounded, retryable port choice
   * is preferable to a fully OS-assigned one.
   */
  readonly randomPorts?: RandomPortOptions;
}

const DEFAULT_CALLBACK_PATH = "/cb";
const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;
const DEFAULT_BIND_ATTEMPTS = 5;
const RANDOM_PORT_MIN = 16384;
const RANDOM_PORT_MAX = 65535;
const DEFAULT_SUCCESS_HTML =
  "<!doctype html><meta charset=utf-8><title>Atlas</title>" +
  '<body style="font-family:system-ui;padding:2rem">' +
  "<h1>Signed in.</h1><p>You can close this window and return to Atlas.</p></body>";
const DEFAULT_FAILURE_HTML =
  "<!doctype html><meta charset=utf-8><title>Atlas</title>" +
  '<body style="font-family:system-ui;padding:2rem">' +
  "<h1>Sign-in failed.</h1><p>Check the Atlas window for details.</p></body>";

function defaultPickPort(): number {
  return (
    RANDOM_PORT_MIN +
    Math.floor(Math.random() * (RANDOM_PORT_MAX - RANDOM_PORT_MIN))
  );
}

function isAddrInUse(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code: unknown }).code === "EADDRINUSE"
  );
}

async function listenOnce(server: Server, port: number): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
}

// Binds the server. Default: port 0 (OS-assigned — conflicts impossible).
// With `randomPorts`: explicitly picked random ports, retried on
// EADDRINUSE up to `attempts` times.
async function bindServer(
  server: Server,
  randomPorts: RandomPortOptions | undefined,
): Promise<void> {
  if (randomPorts === undefined) {
    await listenOnce(server, 0);
    return;
  }
  const attempts = randomPorts.attempts ?? DEFAULT_BIND_ATTEMPTS;
  const pick = randomPorts.pick ?? defaultPickPort;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await listenOnce(server, pick());
      return;
    } catch (err) {
      if (!isAddrInUse(err) || attempt === attempts) {
        throw err instanceof Error && isAddrInUse(err) && attempt === attempts
          ? new Error(
              `loopback bind failed: no free port after ${attempts} attempts`,
            )
          : err;
      }
    }
  }
}

export async function awaitLoopbackCode(
  options: AwaitLoopbackOptions,
): Promise<LoopbackHandle> {
  const callbackPath = options.callbackPath ?? DEFAULT_CALLBACK_PATH;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const successHtml = options.successHtml ?? DEFAULT_SUCCESS_HTML;
  const failureHtml = options.failureHtml ?? DEFAULT_FAILURE_HTML;

  let expectedState: string | null = options.expectedState ?? null;

  let resolveCode: (value: LoopbackCode) => void = () => {};
  let rejectCode: (err: Error) => void = () => {};
  const codePromise = new Promise<LoopbackCode>((resolve, reject) => {
    resolveCode = resolve;
    rejectCode = reject;
  });

  const server: Server = createServer(
    (req: IncomingMessage, res: ServerResponse) => {
      const url = new URL(req.url ?? "/", "http://127.0.0.1");
      if (url.pathname !== callbackPath) {
        res.statusCode = 404;
        res.end();
        return;
      }
      if (expectedState === null) {
        // Not armed yet — the browser has not been opened, so this cannot
        // be the legitimate redirect. Answer without killing the flow.
        res.statusCode = 400;
        res.setHeader("content-type", "text/html; charset=utf-8");
        res.end(failureHtml);
        return;
      }
      const error = url.searchParams.get("error");
      if (error !== null) {
        res.statusCode = 400;
        res.setHeader("content-type", "text/html; charset=utf-8");
        res.end(failureHtml);
        rejectCode(new Error(`oidc redirect error: ${error}`));
        return;
      }
      const code = url.searchParams.get("code");
      const state = url.searchParams.get("state");
      if (code === null || state === null) {
        res.statusCode = 400;
        res.setHeader("content-type", "text/html; charset=utf-8");
        res.end(failureHtml);
        rejectCode(new Error("oidc redirect missing code or state"));
        return;
      }
      if (state !== expectedState) {
        res.statusCode = 400;
        res.setHeader("content-type", "text/html; charset=utf-8");
        res.end(failureHtml);
        rejectCode(new Error("oidc state mismatch"));
        return;
      }
      res.statusCode = 200;
      res.setHeader("content-type", "text/html; charset=utf-8");
      res.end(successHtml);
      resolveCode({ code, state });
    },
  );

  await bindServer(server, options.randomPorts);

  const address = server.address() as AddressInfo | null;
  if (address === null || typeof address === "string") {
    server.close();
    throw new Error("loopback server failed to bind");
  }
  const port = address.port;
  const redirectUri = `http://127.0.0.1:${port}${callbackPath}`;

  let closed = false;
  const close = (): void => {
    if (closed) return;
    closed = true;
    rejectCode(new Error("loopback server closed before redirect"));
    server.close();
  };

  const timeoutHandle = setTimeout(() => {
    rejectCode(new Error("loopback redirect timed out"));
    close();
  }, timeoutMs);
  timeoutHandle.unref();

  codePromise
    .catch(() => {})
    .finally(() => {
      clearTimeout(timeoutHandle);
      close();
    });

  return {
    port,
    redirectUri,
    codePromise,
    armState: (state: string): void => {
      expectedState = state;
    },
    close,
  };
}
