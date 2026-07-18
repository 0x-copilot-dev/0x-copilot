// @vitest-environment node
import { describe, expect, it } from "vitest";

import { BrowserSession } from "./browser-session";
import type {
  BrowserEngine,
  DownloadCapture,
  ElementTarget,
  EngineContext,
  EnginePage,
  RawAxNode,
} from "./browser-engine";
import { StagingArea, type StagingFsPort } from "./staging";
import {
  BrowserApprovalDecision,
  type BrowserApprovalPort,
  type BrowserApprovalRequest,
} from "./action-policy";
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
  download: DownloadCapture = {
    suggestedName: "report.pdf",
    body: new Uint8Array([4, 5, 6, 7]),
  };
  readonly clicks: ElementTarget[] = [];
  readonly fills: { target: ElementTarget; text: string }[] = [];
  readonly selects: { target: ElementTarget; value: string }[] = [];
  readonly submits: ElementTarget[] = [];
  readonly downloads: ElementTarget[] = [];
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
  async clickRef(target: ElementTarget): Promise<void> {
    this.clicks.push(target);
  }
  async fillRef(target: ElementTarget, text: string): Promise<void> {
    this.fills.push({ target, text });
  }
  async selectRef(target: ElementTarget, value: string): Promise<void> {
    this.selects.push({ target, value });
  }
  async submitRef(target: ElementTarget): Promise<void> {
    this.submits.push(target);
  }
  async downloadViaRef(target: ElementTarget): Promise<DownloadCapture> {
    this.downloads.push(target);
    return this.download;
  }
}

class FakeApproval implements BrowserApprovalPort {
  readonly seen: BrowserApprovalRequest[] = [];
  constructor(private readonly decision: BrowserApprovalDecision) {}
  async requestApproval(
    request: BrowserApprovalRequest,
  ): Promise<BrowserApprovalDecision> {
    this.seen.push(request);
    return this.decision;
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

let idSeq = 0;

function makeSession(
  engine: FakeEngine,
  opts?: { approval?: BrowserApprovalPort; acceptDownloads?: boolean },
): {
  session: BrowserSession;
  staging: FakeStagingFs;
} {
  const staging = new FakeStagingFs();
  idSeq = 0;
  const session = new BrowserSession({
    engine,
    manifest: MANIFEST,
    originPolicy: ORIGIN_POLICY,
    staging: new StagingArea({
      stagingRoot: "/stg",
      runId: "run-1",
      fs: staging,
      // Unique ids so distinct staged files do not collide.
      randomId: () => `s${idSeq++}`,
    }),
    runId: "run-1",
    approval: opts?.approval,
    acceptDownloads: opts?.acceptDownloads,
    randomId: () => "id",
  });
  return { session, staging };
}

/**
 * A page whose first snapshot exposes the root button as ref `e1_0` (the first
 * snapshot bumps generation 0 -> 1, and the root converts to `e1_0`).
 */
function withButton(engine: FakeEngine): void {
  engine.page.ax = { role: "button", name: "Send" };
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

describe("BrowserSession action layer (approval gating)", () => {
  it("runs a read action WITHOUT consulting the approval port", async () => {
    const engine = new FakeEngine();
    withButton(engine);
    const approval = new FakeApproval(BrowserApprovalDecision.Approved);
    const { session } = makeSession(engine, { approval });
    const r = await session.dispatch(req("browser_snapshot", {}));
    expect(r.status).toBe("succeeded");
    // A read never asks for approval.
    expect(approval.seen).toHaveLength(0);
  });

  it("blocks a side-effecting click until approved, then dispatches it", async () => {
    const engine = new FakeEngine();
    withButton(engine);
    const approval = new FakeApproval(BrowserApprovalDecision.Approved);
    const { session } = makeSession(engine, { approval });
    // Snapshot mints the ref `e1_0` the click resolves against.
    await session.dispatch(req("browser_snapshot", {}));
    const r = await session.dispatch(
      req("browser_click", { ref: "e1_0" }, BrowserActionClass.ExternalEffect),
    );
    expect(r.status).toBe("succeeded");
    // Approval was requested BEFORE the click reached the engine.
    expect(approval.seen).toHaveLength(1);
    expect(approval.seen[0].toolName).toBe("browser_click");
    expect(engine.page.clicks).toHaveLength(1);
  });

  it("rejects a side-effecting action when approval is denied (no dispatch)", async () => {
    const engine = new FakeEngine();
    withButton(engine);
    const approval = new FakeApproval(BrowserApprovalDecision.Denied);
    const { session } = makeSession(engine, { approval });
    await session.dispatch(req("browser_snapshot", {}));
    const r = await session.dispatch(
      req("browser_submit", { ref: "e1_0" }, BrowserActionClass.Submit),
    );
    expect(r.status).toBe("denied");
    expect(r.errorCode).toBe("browser_action_approval_required");
    // The engine never saw the submit.
    expect(engine.page.submits).toHaveLength(0);
  });

  it("fails CLOSED when no approval authority is wired", async () => {
    const engine = new FakeEngine();
    withButton(engine);
    const { session } = makeSession(engine); // no approval port
    await session.dispatch(req("browser_snapshot", {}));
    const r = await session.dispatch(
      req("browser_click", { ref: "e1_0" }, BrowserActionClass.ExternalEffect),
    );
    expect(r.status).toBe("denied");
    expect(r.errorCode).toBe("browser_action_approval_required");
    expect(engine.page.clicks).toHaveLength(0);
  });

  it("returns element_stale for a ref from a prior generation", async () => {
    const engine = new FakeEngine();
    withButton(engine);
    const approval = new FakeApproval(BrowserApprovalDecision.Approved);
    const { session } = makeSession(engine, { approval });
    await session.dispatch(req("browser_snapshot", {})); // gen 1 -> e1_0
    await session.dispatch(req("browser_snapshot", {})); // gen 2 -> e2_0, e1_0 stale
    const r = await session.dispatch(
      req("browser_click", { ref: "e1_0" }, BrowserActionClass.ExternalEffect),
    );
    expect(r.status).toBe("denied");
    expect(r.errorCode).toBe("browser_element_stale");
    expect(approval.seen).toHaveLength(0); // never reached the gate
  });

  it("never types the secret text into any result field", async () => {
    const engine = new FakeEngine();
    engine.page.ax = { role: "textbox", name: "Email" };
    const approval = new FakeApproval(BrowserApprovalDecision.Approved);
    const { session } = makeSession(engine, { approval });
    await session.dispatch(req("browser_snapshot", {}));
    const r = await session.dispatch(
      req(
        "browser_type",
        { ref: "e1_0", text: "hunter2-SECRET" },
        BrowserActionClass.Input,
      ),
    );
    expect(r.status).toBe("succeeded");
    expect(JSON.stringify(r)).not.toContain("hunter2-SECRET");
    expect(JSON.stringify(approval.seen)).not.toContain("hunter2-SECRET");
    expect(engine.page.fills[0].text).toBe("hunter2-SECRET");
  });
});

describe("BrowserSession downloads (controlled location)", () => {
  it("stages an approved download under the run directory and never outside it", async () => {
    const engine = new FakeEngine();
    withButton(engine);
    engine.page.download = {
      suggestedName: "quarterly.pdf",
      body: new Uint8Array([1, 2, 3, 4, 5]),
    };
    const approval = new FakeApproval(BrowserApprovalDecision.Approved);
    const { session, staging } = makeSession(engine, {
      approval,
      acceptDownloads: true,
    });
    await session.dispatch(req("browser_snapshot", {}));
    const r = await session.dispatch(
      req("browser_download", { ref: "e1_0" }, BrowserActionClass.Download),
    );
    expect(r.status).toBe("succeeded");
    expect(r.artifactRefs[0]).toMatch(/^stg:\/\/run-1\/download_/u);
    // Exactly one file, written INSIDE the run staging directory.
    expect(staging.writes.size).toBe(1);
    const path = [...staging.writes.keys()][0];
    expect(path.startsWith("/stg/run-1/")).toBe(true);
    expect(path).not.toContain("..");
  });

  it("keeps a malicious site-suggested filename inside the run directory", async () => {
    const engine = new FakeEngine();
    withButton(engine);
    engine.page.download = {
      suggestedName: "../../../../etc/cron.d/evil.pdf",
      body: new Uint8Array([9, 9, 9]),
    };
    const approval = new FakeApproval(BrowserApprovalDecision.Approved);
    const { session, staging } = makeSession(engine, {
      approval,
      acceptDownloads: true,
    });
    await session.dispatch(req("browser_snapshot", {}));
    const r = await session.dispatch(
      req("browser_download", { ref: "e1_0" }, BrowserActionClass.Download),
    );
    expect(r.status).toBe("succeeded");
    const path = [...staging.writes.keys()][0];
    // The traversal in the suggested name never reaches the write path.
    expect(path.startsWith("/stg/run-1/download_")).toBe(true);
    expect(path).not.toContain("etc/cron.d");
    expect(path).not.toContain("..");
  });

  it("denies an executable-shaped download and stages nothing", async () => {
    const engine = new FakeEngine();
    withButton(engine);
    engine.page.download = {
      suggestedName: "totally-safe.exe",
      body: new Uint8Array([1, 2, 3]),
    };
    const approval = new FakeApproval(BrowserApprovalDecision.Approved);
    const { session, staging } = makeSession(engine, {
      approval,
      acceptDownloads: true,
    });
    await session.dispatch(req("browser_snapshot", {}));
    const r = await session.dispatch(
      req("browser_download", { ref: "e1_0" }, BrowserActionClass.Download),
    );
    expect(r.status).toBe("denied");
    expect(r.errorCode).toBe("browser_download_denied");
    expect(staging.writes.size).toBe(0);
  });
});
