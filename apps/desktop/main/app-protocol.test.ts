// @vitest-environment node
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { protocolMock } = vi.hoisted(() => {
  return {
    protocolMock: {
      registerSchemesAsPrivileged: vi.fn(),
    },
  };
});

vi.mock("electron", () => {
  return {
    protocol: {
      registerSchemesAsPrivileged: protocolMock.registerSchemesAsPrivileged,
    },
  };
});

import {
  APP_ORIGIN,
  APP_SCHEME,
  CONTENT_SECURITY_POLICY,
  appUrlFor,
  registerAppProtocolHandler,
  registerAppProtocolPrivilege,
  resolveRendererFile,
} from "./app-protocol";

describe("registerAppProtocolPrivilege", () => {
  beforeEach(() => {
    protocolMock.registerSchemesAsPrivileged.mockClear();
  });

  it("registers app:// as standard / secure / fetch-capable", () => {
    registerAppProtocolPrivilege();
    expect(protocolMock.registerSchemesAsPrivileged).toHaveBeenCalledTimes(1);
    const call = protocolMock.registerSchemesAsPrivileged.mock.calls[0]?.[0] as
      | Array<{
          scheme: string;
          privileges: Record<string, boolean>;
        }>
      | undefined;
    expect(call).toBeDefined();
    expect(call?.[0]?.scheme).toBe(APP_SCHEME);
    expect(call?.[0]?.privileges.standard).toBe(true);
    expect(call?.[0]?.privileges.secure).toBe(true);
    expect(call?.[0]?.privileges.supportFetchAPI).toBe(true);
    expect(call?.[0]?.privileges.corsEnabled).toBe(true);
    expect(call?.[0]?.privileges.stream).toBe(true);
  });
});

describe("resolveRendererFile", () => {
  it("maps / to /index.html under the renderer dir", () => {
    const out = resolveRendererFile("/abs/renderer", "app://app/");
    expect(out).toBe("/abs/renderer/index.html");
  });

  it("rejects requests with a non-app host", () => {
    expect(resolveRendererFile("/abs/renderer", "app://other/index.html")).toBe(
      null,
    );
  });

  it("URL-normalises .. segments before joining (defense-in-depth)", () => {
    // The WHATWG URL parser folds /../etc/passwd into /etc/passwd before
    // we see it. After joining with the renderer root, the resolved path
    // remains under the renderer dir. The handler will 404 on the
    // missing file. The root-check below stays as belt-and-braces in
    // case a future change loosens this normalisation.
    const resolved = resolveRendererFile(
      "/abs/renderer",
      "app://app/../etc/passwd",
    );
    expect(resolved).toBe("/abs/renderer/etc/passwd");
  });

  it("keeps the resolved path inside the renderer dir for normal requests", () => {
    const out = resolveRendererFile(
      "/abs/renderer",
      "app://app/static/bundle.js",
    );
    expect(out).toBe("/abs/renderer/static/bundle.js");
    expect(out?.startsWith("/abs/renderer/")).toBe(true);
  });

  it("rejects garbage URLs", () => {
    expect(resolveRendererFile("/abs/renderer", "::::not-a-url::::")).toBe(
      null,
    );
  });

  it("returns the joined path for plain pathnames", () => {
    const out = resolveRendererFile("/abs/renderer", "app://app/bootstrap.js");
    expect(out).toBe("/abs/renderer/bootstrap.js");
  });
});

describe("appUrlFor", () => {
  it("prepends the app:// origin and ensures a leading slash", () => {
    expect(appUrlFor("/index.html")).toBe(`${APP_ORIGIN}/index.html`);
    expect(appUrlFor("foo")).toBe(`${APP_ORIGIN}/foo`);
  });
});

describe("CONTENT_SECURITY_POLICY", () => {
  it("forbids network requests via connect-src 'none'", () => {
    expect(CONTENT_SECURITY_POLICY).toContain("connect-src 'none'");
  });

  it("scopes default-src to self and the app scheme", () => {
    expect(CONTENT_SECURITY_POLICY).toContain("default-src 'self' app:");
  });

  it("denies framing", () => {
    expect(CONTENT_SECURITY_POLICY).toContain("frame-ancestors 'none'");
  });
});

describe("registerAppProtocolHandler", () => {
  let rendererDir: string;

  beforeEach(async () => {
    rendererDir = await mkdtemp(join(tmpdir(), "app-protocol-test-"));
    await writeFile(
      join(rendererDir, "index.html"),
      "<!doctype html><html><body>ok</body></html>",
      "utf-8",
    );
    await mkdir(join(rendererDir, "nested"), { recursive: true });
    await writeFile(join(rendererDir, "nested", "deep.txt"), "deep", "utf-8");
  });

  afterEach(async () => {
    await rm(rendererDir, { recursive: true, force: true });
  });

  it("serves a file with the expected CSP header and content type", async () => {
    let captured: ((req: Request) => Promise<Response>) | null = null;
    const sessionStub = {
      protocol: {
        handle: (_scheme: string, h: (req: Request) => Promise<Response>) => {
          captured = h;
        },
      },
    };
    registerAppProtocolHandler(
      rendererDir,
      sessionStub as unknown as Electron.Session,
    );
    expect(captured).not.toBeNull();
    const handle = captured as unknown as (req: Request) => Promise<Response>;
    const res = await handle(new Request("app://app/index.html"));
    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Security-Policy")).toBe(
      CONTENT_SECURITY_POLICY,
    );
    expect(res.headers.get("Content-Type")).toBe("text/html; charset=utf-8");
    expect(res.headers.get("X-Content-Type-Options")).toBe("nosniff");
    expect(await res.text()).toContain("ok");
  });

  it("returns 404 for path traversal", async () => {
    let captured: ((req: Request) => Promise<Response>) | null = null;
    const sessionStub = {
      protocol: {
        handle: (_scheme: string, h: (req: Request) => Promise<Response>) => {
          captured = h;
        },
      },
    };
    registerAppProtocolHandler(
      rendererDir,
      sessionStub as unknown as Electron.Session,
    );
    const handle = captured as unknown as (req: Request) => Promise<Response>;
    const res = await handle(new Request("app://app/../etc/passwd"));
    expect(res.status).toBe(404);
  });

  it("returns 404 when the file is missing", async () => {
    let captured: ((req: Request) => Promise<Response>) | null = null;
    const sessionStub = {
      protocol: {
        handle: (_scheme: string, h: (req: Request) => Promise<Response>) => {
          captured = h;
        },
      },
    };
    registerAppProtocolHandler(
      rendererDir,
      sessionStub as unknown as Electron.Session,
    );
    const handle = captured as unknown as (req: Request) => Promise<Response>;
    const res = await handle(new Request("app://app/does-not-exist.js"));
    expect(res.status).toBe(404);
  });
});
