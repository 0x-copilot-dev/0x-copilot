// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import type { Transport, TypedRequest } from "@0x-copilot/chat-transport";
import type { UserProfile } from "@0x-copilot/api-types";

import { createFirstRunProfilePort } from "./firstRunProfilePort";

function fakeTransport(result: unknown): {
  readonly transport: Transport;
  readonly calls: TypedRequest[];
} {
  const calls: TypedRequest[] = [];
  const request = vi.fn(async (req: TypedRequest) => {
    calls.push(req);
    return result;
  });
  return { transport: { request } as unknown as Transport, calls };
}

describe("createFirstRunProfilePort", () => {
  it("GETs /v1/me/profile with no identity in the body", async () => {
    const { transport, calls } = fakeTransport(null);
    await createFirstRunProfilePort(transport).get();
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("GET");
    expect(calls[0].path).toBe("/v1/me/profile");
    expect(calls[0].body).toBeUndefined();
  });

  it("projects a SIWE wallet profile onto WalletProfileView", async () => {
    const profile: Partial<UserProfile> = {
      wallet_address: "0x7f3C0000000000000000000000000000000000a92C",
      chain_id: 1,
      chain_name: "Ethereum",
      auth_method: "siwe",
      email_is_placeholder: true,
    };
    const { transport } = fakeTransport(profile);
    const view = await createFirstRunProfilePort(transport).get();
    expect(view).toEqual({
      walletAddress: "0x7f3C0000000000000000000000000000000000a92C",
      chainId: 1,
      chainName: "Ethereum",
      authMethod: "siwe",
      emailIsPlaceholder: true,
    });
  });

  it("degrades a null/empty response to an all-null view (renders no chip)", async () => {
    const { transport } = fakeTransport(null);
    const view = await createFirstRunProfilePort(transport).get();
    expect(view).toEqual({
      walletAddress: null,
      chainId: null,
      chainName: null,
      authMethod: null,
      emailIsPlaceholder: false,
    });
  });
});
