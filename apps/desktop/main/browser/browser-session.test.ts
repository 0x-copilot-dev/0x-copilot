// @vitest-environment node
import { describe, expect, it } from "vitest";

import { BrowserSession } from "./browser-session";
import type {
  BrowserEngine,
  EngineContext,
  EnginePage,
  RawAxNode,
} from "./browser-engine";
import { StagingArea, type StagingFsPort } from "./staging";
import {
  BrowserActionClass,
  BrowserProfileMode,
  type BrowserActionRequest,
  type BrowserOriginPolicy,
} from "./protocol";
import type { ProfileManifest } from "./profile-store";

const ORIGIN_POLICY: BrowserOriginPolicy = {
  version: 1,
  topLevelOrigins: ["https://example.com"],
  subresourceOrigins: [],
  denyPrivateNetworks: true,
  serviceWorkers: "block",
};

const MANIFEST: ProfileManifest = {
  version: 1,
  profileId: "prf_1",
  workspaceId: "ws",
  mode: BrowserProfileMode.Ephemeral,
  browserVersion: "chromium-1",
  policyVersion: 1,
  createdAt: 0,
  lastUsedAt: 0,
  userDataDir: "/eph/prf_1",
};

class FakePage implements EnginePage {
  url = "about:blank";
  ax: RawAxNode | null = null;
  shot = new Uint8Array([1, 2, 3]);
  async goto(
    url: string,
  ): Promise<{ url: string; title: string; status: number }> {
    this.url = url;
    return { url, title: "Example", status: 200 };
  }
  async accessibilitySnapshot(): Promise<RawAxNode | null> {
    return this.ax;
  }
  async screenshot(): Promise<Uint8Array> {
    return this.shot;
  }
  async waitFor(): Promise<void> {}
  currentUrl(): string {
    return this.url;
  }
  async currentTitle(): Promise<string> {
    return "Example";
  }
}

class FakeEngine implements BrowserEngine {
  readonly page = new FakePage();
  closed = false;
  version(): string {
    return "chromium-1";
  }
  async newContext(): Promise<EngineContext> {
    const page = this.page;
    return {
      newPage: async () => page,
      close: async () => {
        this.closed = true;
      },
    };
  }
  async close(): Promise<void> {}
}

class FakeStagingFs implements StagingFsPort {
  readonly writes = new Map<string, Uint8Array>();
  async mkdir(): Promise<void> {}
  async writeFile(path: string, data: Uint8Array): Promise<void> {
    this.writes.set(path, data);
  }
  async rm(): Promise<void> {}
}

function makeSession(engine: FakeEngine): {
  session: BrowserSession;
  staging: FakeStagingFs;
} {
  const staging = new FakeStagingFs();
  const session = new BrowserSession({
    engine,
    manifest: MANIFEST,
    originPolicy: ORIGIN_POLICY,
    staging: new StagingArea({
      stagingRoot: "/stg",
      runId: "run-1",
      fs: staging,
    }),
    runId: "run-1",
    randomId: () => "id",
  });
  return { session, staging };
}

function req(
  toolName: string,
  args: unknown,
  actionClass: BrowserActionRequest["actionClass"] = BrowserActionClass.Read,
): BrowserActionRequest {
  return {
    version: 1,
    requestId: `rq_${toolName}`,
    binding: {
      version: 1,
      runId: "run-1",
      workspaceId: "ws",
      profileId: "prf_1",
      profileMode: BrowserProfileMode.Ephemeral,
      approvalId: "ap",
      originPolicy: ORIGIN_POLICY,
      expiresAt: "2099-01-01T00:00:00Z",
      nonce: "n",
    },
    actionClass,
    toolName,
    arguments: args,
    deadlineMs: 5000,
  };
}

describe("BrowserSession read-only actions", () => {
  it("navigates to an approved origin and bumps the generation", async () => {
    const engine = new FakeEngine();
    const { session } = makeSession(engine);
    const r = await session.dispatch(
      req(
        "browser_navigate",
        { url: "https://example.com/docs" },
        BrowserActionClass.Navigate,
      ),
    );
    expect(r.status).toBe("succeeded");
    expect(r.currentOrigin).toBe("https://example.com");
    expect(r.nextGeneration).toBe(1);
  });

  it("denies an off-policy origin with origin_approval_required", async () => {
    const engine = new FakeEngine();
    const { session } = makeSession(engine);
    const r = await session.dispatch(
      req(
        "browser_navigate",
        { url: "https://evil.example.net" },
        BrowserActionClass.Navigate,
      ),
    );
    expect(r.status).toBe("denied");
    expect(r.errorCode).toBe("browser_origin_approval_required");
  });

  it("denies a non-https scheme with network_denied", async () => {
    const engine = new FakeEngine();
    const { session } = makeSession(engine);
    const r = await session.dispatch(
      req(
        "browser_navigate",
        { url: "file:///etc/passwd" },
        BrowserActionClass.Navigate,
      ),
    );
    expect(r.status).toBe("denied");
    expect(r.errorCode).toBe("browser_network_denied");
  });

  it("captures a bounded snapshot and omits input values", async () => {
    const engine = new FakeEngine();
    engine.page.ax = {
      role: "document",
      name: "root",
      children: [
        { role: "textbox", name: "Email", value: "secret@user.com" },
        { role: "button", name: "Submit" },
      ],
    };
    const { session } = makeSession(engine);
    const r = await session.dispatch(req("browser_snapshot", {}));
    expect(r.status).toBe("succeeded");
    const json = JSON.stringify(r.snapshot);
    expect(json).not.toContain("secret@user.com");
    expect(json).toContain("Email");
    expect(r.snapshot?.ref).toMatch(/^e\d+_0$/u);
  });

  it("stages a screenshot by reference (never inlined)", async () => {
    const engine = new FakeEngine();
    const { session, staging } = makeSession(engine);
    const r = await session.dispatch(
      req("browser_screenshot", { fullPage: false }),
    );
    expect(r.status).toBe("succeeded");
    expect(r.artifactRefs[0]).toMatch(/^stg:\/\/run-1\//u);
    expect(staging.writes.size).toBe(1);
  });

  it("refuses a side-effecting action class in the read-only foundation", async () => {
    const engine = new FakeEngine();
    const { session } = makeSession(engine);
    const r = await session.dispatch(
      req("browser_click", { ref: "e1_0" }, BrowserActionClass.Submit),
    );
    expect(r.status).toBe("denied");
    expect(r.errorCode).toBe("browser_tool_not_implemented");
  });

  it("closes the session and tears down the context", async () => {
    const engine = new FakeEngine();
    const { session } = makeSession(engine);
    await session.dispatch(
      req(
        "browser_navigate",
        { url: "https://example.com" },
        BrowserActionClass.Navigate,
      ),
    );
    const r = await session.dispatch(req("browser_close", {}));
    expect(r.status).toBe("succeeded");
    expect(engine.closed).toBe(true);
  });
});
