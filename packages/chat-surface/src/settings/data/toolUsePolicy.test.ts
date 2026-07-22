import { describe, expect, it, vi } from "vitest";

import type {
  ToolUsePolicyResponse,
  UpdateToolUsePolicyRequest,
} from "@0x-copilot/api-types";

import type { Transport, TypedRequest } from "../../ports/Transport";
import type { ApprovalPolicyValue } from "../ApprovalPolicy";
import {
  DEFAULT_APPROVAL_POLICY,
  approvalPolicyFromResponse,
  createToolUsePolicyPort,
  toolUsePolicyRequestFromValue,
} from "./toolUsePolicy";

function response(
  policies: ToolUsePolicyResponse["policies"],
): ToolUsePolicyResponse {
  return {
    scope: "user",
    org_id: "org-1",
    user_id: "user-1",
    policies,
  };
}

function entry(
  kind: ToolUsePolicyResponse["policies"][number]["kind"],
  mode: ToolUsePolicyResponse["policies"][number]["mode"],
): ToolUsePolicyResponse["policies"][number] {
  return {
    kind,
    mode,
    updated_at: "2026-01-01T00:00:00Z",
    updated_by_user_id: null,
  };
}

describe("approvalPolicyFromResponse — wire → UI mapping", () => {
  it("maps read/write/destructive onto readOnly/write/danger", () => {
    const value = approvalPolicyFromResponse(
      response([
        entry("read", "ask"),
        entry("write", "auto"),
        entry("destructive", "block"),
      ]),
    );
    expect(value).toEqual<ApprovalPolicyValue>({
      readOnly: "ask",
      write: "auto",
      danger: "block",
    });
  });

  it("falls back to the deployment default for a missing axis, keeps present ones", () => {
    // Only `write` is stored; read + destructive fall back to their defaults.
    const value = approvalPolicyFromResponse(
      response([entry("write", "auto")]),
    );
    expect(value).toEqual<ApprovalPolicyValue>({
      readOnly: DEFAULT_APPROVAL_POLICY.readOnly,
      write: "auto",
      danger: DEFAULT_APPROVAL_POLICY.danger,
    });
  });

  it("hydrates an empty policy to the full deployment default", () => {
    expect(approvalPolicyFromResponse(response([]))).toEqual(
      DEFAULT_APPROVAL_POLICY,
    );
  });

  it("degrades a malformed body (no policies array) to the default, no crash", () => {
    // The port's read() catch is the outer backstop, but the mapper itself
    // must never throw on a malformed response (NFR-9).
    const malformed = {} as ToolUsePolicyResponse;
    expect(approvalPolicyFromResponse(malformed)).toEqual(
      DEFAULT_APPROVAL_POLICY,
    );
  });

  it("clamps a read axis outside its UI subset to fail-open auto", () => {
    // The store CAN hold read=block; the UI can't show it → degrade to auto.
    const value = approvalPolicyFromResponse(
      response([
        entry("read", "block"),
        entry("write", "require"),
        entry("destructive", "require"),
      ]),
    );
    expect(value.readOnly).toBe("auto");
    expect(value.write).toBe("require");
    expect(value.danger).toBe("require");
  });

  it("clamps a destructive axis outside its subset to the STRICTER require", () => {
    // destructive=auto/ask is over-permissive for a danger axis → degrade to
    // require, never to an over-permissive mode (PRD-03 NFR-9).
    for (const overPermissive of ["auto", "ask"] as const) {
      const value = approvalPolicyFromResponse(
        response([entry("destructive", overPermissive)]),
      );
      expect(value.danger).toBe("require");
    }
  });
});

describe("toolUsePolicyRequestFromValue — UI → wire mapping", () => {
  it("emits the atomic three-axis replace in read/write/destructive order", () => {
    const request = toolUsePolicyRequestFromValue({
      readOnly: "auto",
      write: "block",
      danger: "block",
    });
    expect(request).toEqual<UpdateToolUsePolicyRequest>({
      policies: [
        { kind: "read", mode: "auto" },
        { kind: "write", mode: "block" },
        { kind: "destructive", mode: "block" },
      ],
    });
  });

  it("round-trips through the response projection", () => {
    const original: ApprovalPolicyValue = {
      readOnly: "ask",
      write: "require",
      danger: "block",
    };
    const request = toolUsePolicyRequestFromValue(original);
    const roundTripped = approvalPolicyFromResponse(
      response(request.policies.map((p) => entry(p.kind, p.mode))),
    );
    expect(roundTripped).toEqual(original);
  });
});

describe("createToolUsePolicyPort", () => {
  function fakeTransport(handler: (req: TypedRequest) => unknown): {
    transport: Transport;
    requests: TypedRequest[];
  } {
    const requests: TypedRequest[] = [];
    const transport = {
      request: vi.fn(async (req: TypedRequest) => {
        requests.push(req);
        return handler(req);
      }),
      subscribeServerSentEvents: vi.fn(),
      capabilities: vi.fn(),
      session: vi.fn(),
    } as unknown as Transport;
    return { transport, requests };
  }

  it("read() GETs the policy and maps it to the UI value", async () => {
    const { transport, requests } = fakeTransport(() =>
      response([
        entry("read", "auto"),
        entry("write", "ask"),
        entry("destructive", "require"),
      ]),
    );
    const port = createToolUsePolicyPort(transport);
    const value = await port.read();
    expect(requests[0]).toMatchObject({
      method: "GET",
      path: "/v1/me/policies/tool-use",
    });
    expect(value).toEqual(DEFAULT_APPROVAL_POLICY);
  });

  it("save() PUTs the atomic three-axis body", async () => {
    const { transport, requests } = fakeTransport(() =>
      response([
        entry("read", "ask"),
        entry("write", "block"),
        entry("destructive", "block"),
      ]),
    );
    const port = createToolUsePolicyPort(transport);
    await port.save({ readOnly: "ask", write: "block", danger: "block" });
    expect(requests[0]).toMatchObject({
      method: "PUT",
      path: "/v1/me/policies/tool-use",
      body: {
        policies: [
          { kind: "read", mode: "ask" },
          { kind: "write", mode: "block" },
          { kind: "destructive", mode: "block" },
        ],
      },
    });
  });

  it("save() rejects when the transport rejects (no fake success)", async () => {
    const { transport } = fakeTransport(() => {
      throw new Error("boom");
    });
    const port = createToolUsePolicyPort(transport);
    await expect(
      port.save({ readOnly: "auto", write: "ask", danger: "require" }),
    ).rejects.toThrow("boom");
  });
});
