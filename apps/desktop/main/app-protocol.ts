import { readFile } from "node:fs/promises";
import { extname, join, normalize, sep } from "node:path";
import { protocol, type Session } from "electron";

export const APP_SCHEME = "app";
export const APP_HOST = "app";
export const APP_ORIGIN = `${APP_SCHEME}://${APP_HOST}`;

// connect-src 'none' is the load-bearing security claim: no network from
// the renderer. Verify in DevTools console with fetch('https://example.com').
// img-src includes data: so React-flavoured CSS-in-JS / data-URI thumbnails
// keep working. style-src 'unsafe-inline' is required because chat-surface
// emits React inline `style={{...}}` attributes — documented tradeoff
// identical to what the S1-B spike accepted.
export const CONTENT_SECURITY_POLICY = [
  "default-src 'self' app:",
  "script-src 'self' app:",
  "style-src 'self' app: 'unsafe-inline'",
  "img-src 'self' app: data:",
  "font-src 'self' app: data:",
  "connect-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join("; ");

const MIME_BY_EXT: Readonly<Record<string, string>> = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".map": "application/json; charset=utf-8",
};

function contentTypeFor(filePath: string): string {
  return (
    MIME_BY_EXT[extname(filePath).toLowerCase()] ?? "application/octet-stream"
  );
}

// Must run before `app.whenReady()`. Privileged so cookies / fetch from
// the renderer treat app:// the same as https:// for same-origin policy
// and CSP enforcement.
export function registerAppProtocolPrivilege(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: APP_SCHEME,
      privileges: {
        standard: true,
        secure: true,
        supportFetchAPI: true,
        corsEnabled: true,
        stream: true,
      },
    },
  ]);
}

export function resolveRendererFile(
  rendererDir: string,
  requestUrl: string,
): string | null {
  let parsed: URL;
  try {
    parsed = new URL(requestUrl);
  } catch {
    return null;
  }
  if (parsed.host !== APP_HOST) return null;
  const pathname = parsed.pathname === "/" ? "/index.html" : parsed.pathname;
  const normalized = normalize(pathname).replace(/^[/\\]+/, "");
  const candidate = join(rendererDir, normalized);
  const rendererRoot = normalize(rendererDir) + sep;
  if (
    !(candidate + sep).startsWith(rendererRoot) &&
    candidate !== normalize(rendererDir)
  ) {
    return null;
  }
  return candidate;
}

// Defense in depth: the rendererDir join is normalised + path-traversal-
// rejected before the readFile. Renderer requests for ../something can't
// escape rendererDir even if the underlying fs would happily resolve them.
export function registerAppProtocolHandler(
  rendererDir: string,
  electronSession: Session,
): void {
  electronSession.protocol.handle(APP_SCHEME, async (req: Request) => {
    const filePath = resolveRendererFile(rendererDir, req.url);
    if (filePath === null) {
      return new Response("not found", { status: 404 });
    }
    try {
      const body = await readFile(filePath);
      return new Response(body, {
        status: 200,
        headers: {
          "Content-Type": contentTypeFor(filePath),
          "Content-Security-Policy": CONTENT_SECURITY_POLICY,
          "X-Content-Type-Options": "nosniff",
          "Cross-Origin-Opener-Policy": "same-origin",
          "Cross-Origin-Embedder-Policy": "require-corp",
        },
      });
    } catch {
      return new Response("not found", { status: 404 });
    }
  });
}

export function appUrlFor(pathname: string): string {
  const trimmed = pathname.startsWith("/") ? pathname : `/${pathname}`;
  return `${APP_ORIGIN}${trimmed}`;
}
