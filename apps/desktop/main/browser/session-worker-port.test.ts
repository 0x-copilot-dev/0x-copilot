// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  BrowserActionClass,
  BrowserErrorCode,
  BrowserProfileMode,
  type BrowserActionRequest,
} from "./protocol";
import type { BrowserSession } from "./browser-session";
import { SessionWorkerPort } from "./session-worker-port";

const CLICK: BrowserActionRequest = {
  version: 1,
  requestId: "rq_click",
  binding: {
    version: 1,
    runId: "run-1",
    workspaceId: "workspace-1",
    profileId: "profile-1",
    profileMode: BrowserProfileMode.Ephemeral,
    approvalId: "approval-1",
    originPolicy: {
      version: 1,
      topLevelOrigins: ["https://example.com"],
      subresourceOrigins: [],
      denyPrivateNetworks: true,
      serviceWorkers: "block",
    },
    expiresAt: "2099-01-01T00:00:00Z",
    nonce: "nonce-1",
  },
  actionClass: BrowserActionClass.ExternalEffect,
  toolName: "browser_click",
  arguments: { ref: "e1_0" },
  deadlineMs: 5_000,
};

describe("SessionWorkerPort staged-action boundary", () => {
  it("rejects a direct generic click without creating a browser session", async () => {
    let created = 0;
    const port = new SessionWorkerPort({
      includeActionTools: true,
      createSession: async () => {
        created += 1;
        return {} as BrowserSession;
      },
    });

    const result = await port.dispatch(CLICK);

    expect(result.status).toBe("denied");
    expect(result.errorCode).toBe(BrowserErrorCode.ToolNotImplemented);
    expect(created).toBe(0);
  });
});
