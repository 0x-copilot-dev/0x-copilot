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
  close(): void;
}

export interface LoopbackCode {
  readonly code: string;
  readonly state: string;
}

export interface AwaitLoopbackOptions {
  readonly expectedState: string;
  readonly callbackPath?: string;
  readonly timeoutMs?: number;
  readonly successHtml?: string;
  readonly failureHtml?: string;
}

const DEFAULT_CALLBACK_PATH = "/cb";
const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;
const DEFAULT_SUCCESS_HTML =
  "<!doctype html><meta charset=utf-8><title>Atlas</title>" +
  '<body style="font-family:system-ui;padding:2rem">' +
  "<h1>Signed in.</h1><p>You can close this window and return to Atlas.</p></body>";
const DEFAULT_FAILURE_HTML =
  "<!doctype html><meta charset=utf-8><title>Atlas</title>" +
  '<body style="font-family:system-ui;padding:2rem">' +
  "<h1>Sign-in failed.</h1><p>Check the Atlas window for details.</p></body>";

export async function awaitLoopbackCode(
  options: AwaitLoopbackOptions,
): Promise<LoopbackHandle> {
  const callbackPath = options.callbackPath ?? DEFAULT_CALLBACK_PATH;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const successHtml = options.successHtml ?? DEFAULT_SUCCESS_HTML;
  const failureHtml = options.failureHtml ?? DEFAULT_FAILURE_HTML;

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
      if (state !== options.expectedState) {
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

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });

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

  return { port, redirectUri, codePromise, close };
}
