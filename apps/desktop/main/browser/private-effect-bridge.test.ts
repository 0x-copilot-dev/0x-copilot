// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  BrowserEffectActionKind,
  BrowserEffectOutcome,
  type BrowserActionPlan,
} from "./protocol";
import {
  BrowserPrivateEffectBridge,
  type BrowserPrivateEffectWorkerPort,
} from "./private-effect-bridge";

const PLAN: BrowserActionPlan = {
  sessionRef: "browser-session://ses_123",
  pageRef: "browser-page://pg_123",
  origin: "https://example.com",
  topLevelOrigin: "https://example.com",
  actionKind: BrowserEffectActionKind.Click,
  elementRef: "e3_0",
  elementFingerprint: "a".repeat(64),
  canonicalFieldsRef:
    "operation://op_00000000-0000-4000-8000-000000000001/args",
  fieldsDigest: "b".repeat(64),
  uploadArtifactRefs: [],
  uploadArtifacts: [],
  precondition: {
    pageGeneration: 3,
    origin: "https://example.com",
    elementFingerprint: "a".repeat(64),
  },
  preconditionDigest: "c".repeat(64),
  userVisibleSummary: "Review browser click on https://example.com.",
};

class FakeWorker implements BrowserPrivateEffectWorkerPort {
  prepares = 0;
  applies = 0;
  reconciles = 0;
  async prepareAction() {
    this.prepares += 1;
    return {
      preparedRef: "browser-prepared://ses_123/one",
      observedPreconditionDigest: "c".repeat(64),
      preconditionDrift: false,
    };
  }
  async applyPrepared() {
    this.applies += 1;
    return { outcome: BrowserEffectOutcome.Applied };
  }
  async reconcileAction() {
    this.reconciles += 1;
    return { outcome: BrowserEffectOutcome.Indeterminate };
  }
}

describe("BrowserPrivateEffectBridge", () => {
  it("has no credential surface and only forwards validated staged plans", async () => {
    const worker = new FakeWorker();
    const bridge = new BrowserPrivateEffectBridge(worker);
    const prepared = await bridge.prepareAction(PLAN);
    expect(prepared.preparedRef).toBe("browser-prepared://ses_123/one");
    await bridge.applyPrepared(prepared.preparedRef!);
    expect(worker.prepares).toBe(1);
    expect(worker.applies).toBe(1);
    expect("authToken" in bridge).toBe(false);
  });

  it("rejects a host-path session reference before it reaches the worker", async () => {
    const worker = new FakeWorker();
    const bridge = new BrowserPrivateEffectBridge(worker);
    await expect(
      bridge.prepareAction({
        ...PLAN,
        sessionRef: "file:///Users/alice/Cookies",
      }),
    ).rejects.toThrow(/opaque and scoped/u);
    expect(worker.prepares).toBe(0);
  });
});
