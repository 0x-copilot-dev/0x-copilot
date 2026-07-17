/**
 * WalletSignIn — nonce → sign → verify against a fake EIP-1193 provider
 * announced over real EIP-6963 events. The siwe API module is spied per
 * the AuthContext.test.tsx pattern; no network.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SiweSessionResponse } from "@0x-copilot/api-types";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as siweApi from "../../api/siweApi";
import {
  EIP6963_ANNOUNCE_EVENT,
  EIP6963_REQUEST_EVENT,
  type Eip1193RequestArguments,
} from "./eip6963";
import { buildSiweMessage, defaultExpirationTime } from "./siweMessage";
import { CHAIN_NOT_ALLOWED_MESSAGE, WalletSignIn } from "./WalletSignIn";

const ADDRESS_EIP55 = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359";
const ADDRESS_WIRE = ADDRESS_EIP55.toLowerCase();
const SIGNATURE = `0x${"ab".repeat(65)}`;

const SESSION: SiweSessionResponse = {
  user_id: "usr_w1",
  session_id: "ses_w1",
  bearer_token: "brr_w1",
  expires_at: "2026-07-17T12:00:00Z",
  return_to: null,
  requires_mfa: false,
};

const teardowns: Array<() => void> = [];

afterEach(() => {
  while (teardowns.length > 0) {
    teardowns.pop()?.();
  }
  vi.restoreAllMocks();
});

interface FakeWalletOverrides {
  personalSign?: () => Promise<unknown>;
}

function installFakeWallet(overrides: FakeWalletOverrides = {}): {
  request: ReturnType<typeof vi.fn>;
} {
  const request = vi.fn(
    async ({ method }: Eip1193RequestArguments): Promise<unknown> => {
      if (method === "eth_requestAccounts") return [ADDRESS_EIP55];
      if (method === "eth_chainId") return "0x1";
      if (method === "personal_sign") {
        return overrides.personalSign ? overrides.personalSign() : SIGNATURE;
      }
      throw new Error(`unexpected wallet method: ${method}`);
    },
  );
  const onRequest = (): void => {
    window.dispatchEvent(
      new CustomEvent(EIP6963_ANNOUNCE_EVENT, {
        detail: {
          info: {
            uuid: "u-test",
            name: "TestWallet",
            icon: "",
            rdns: "dev.testwallet",
          },
          provider: { request },
        },
      }),
    );
  };
  window.addEventListener(EIP6963_REQUEST_EVENT, onRequest);
  teardowns.push(() =>
    window.removeEventListener(EIP6963_REQUEST_EVENT, onRequest),
  );
  return { request };
}

function hexToUtf8(hex: string): string {
  const bare = hex.replace(/^0x/, "");
  const bytes = new Uint8Array(bare.length / 2);
  for (let i = 0; i < bytes.length; i += 1) {
    bytes[i] = parseInt(bare.slice(i * 2, i * 2 + 2), 16);
  }
  return new TextDecoder().decode(bytes);
}

async function clickThroughPicker(): Promise<void> {
  const user = userEvent.setup();
  await user.click(screen.getByTestId("wallet-connect"));
  await waitFor(() => {
    expect(screen.getByTestId("wallet-picker")).toBeInTheDocument();
  });
  await user.click(screen.getByTestId("wallet-provider-dev.testwallet"));
}

describe("WalletSignIn", () => {
  it("runs nonce → personal_sign → verify and hands the session over", async () => {
    const { request } = installFakeWallet();
    const nonceSpy = vi.spyOn(siweApi, "requestSiweNonce").mockResolvedValue({
      nonce: "n0nceValue",
      expires_at: "2026-07-17T10:40:00Z",
    });
    const verifySpy = vi
      .spyOn(siweApi, "verifySiwe")
      .mockResolvedValue(SESSION);
    const onSession = vi.fn();

    render(<WalletSignIn onSession={onSession} discoveryWindowMs={5} />);
    await clickThroughPicker();

    await waitFor(() => {
      expect(onSession).toHaveBeenCalledWith(SESSION);
    });

    // Nonce is requested for the lowercase wire address + decimal chain.
    expect(nonceSpy).toHaveBeenCalledWith({
      address: ADDRESS_WIRE,
      chain_id: 1,
    });

    // The wallet signed exactly the message we verified.
    const signCall = request.mock.calls.find(
      ([args]) => (args as Eip1193RequestArguments).method === "personal_sign",
    );
    expect(signCall).toBeDefined();
    const [hexMessage, signingAddress] = (
      signCall![0] as Eip1193RequestArguments
    ).params as [string, string];
    expect(signingAddress).toBe(ADDRESS_EIP55);

    expect(verifySpy).toHaveBeenCalledTimes(1);
    const { message, signature } = verifySpy.mock.calls[0][0];
    expect(signature).toBe(SIGNATURE);
    expect(hexToUtf8(hexMessage)).toBe(message);

    // And that message is the frozen template filled with our values
    // (Issued At / Expiration Time are wall-clock; extract rather than
    // pinning time, then assert the expiry is the TTL offset).
    const issuedAt = /Issued At: (.+)$/m.exec(message)?.[1];
    const expirationTime = /Expiration Time: (.+)$/m.exec(message)?.[1];
    expect(issuedAt).toBeDefined();
    expect(expirationTime).toBe(defaultExpirationTime(issuedAt!));
    expect(message).toBe(
      buildSiweMessage({
        domain: window.location.host,
        uri: window.location.origin,
        address: ADDRESS_EIP55,
        chainId: 1,
        nonce: "n0nceValue",
        issuedAt: issuedAt!,
        expirationTime: expirationTime!,
      }),
    );
  });

  it("cancels quietly when the user rejects the signature (EIP-1193 4001)", async () => {
    installFakeWallet({
      personalSign: () =>
        Promise.reject({ code: 4001, message: "User rejected the request." }),
    });
    vi.spyOn(siweApi, "requestSiweNonce").mockResolvedValue({
      nonce: "n0nce",
      expires_at: "2026-07-17T10:40:00Z",
    });
    const verifySpy = vi.spyOn(siweApi, "verifySiwe");
    const onSession = vi.fn();

    render(<WalletSignIn onSession={onSession} discoveryWindowMs={5} />);
    await clickThroughPicker();

    await waitFor(() => {
      expect(screen.getByTestId("wallet-connect")).toHaveTextContent(
        "Connect wallet",
      );
    });
    expect(screen.queryByTestId("wallet-error")).not.toBeInTheDocument();
    expect(verifySpy).not.toHaveBeenCalled();
    expect(onSession).not.toHaveBeenCalled();
  });

  it("maps chain_not_allowed to the switch-network message", async () => {
    installFakeWallet();
    vi.spyOn(siweApi, "requestSiweNonce").mockRejectedValue(
      new Error("chain_not_allowed"),
    );
    const onSession = vi.fn();

    render(<WalletSignIn onSession={onSession} discoveryWindowMs={5} />);
    await clickThroughPicker();

    await waitFor(() => {
      expect(screen.getByTestId("wallet-error")).toHaveTextContent(
        CHAIN_NOT_ALLOWED_MESSAGE,
      );
    });
    expect(onSession).not.toHaveBeenCalled();
  });

  it("surfaces other verify details verbatim", async () => {
    installFakeWallet();
    vi.spyOn(siweApi, "requestSiweNonce").mockResolvedValue({
      nonce: "n0nce",
      expires_at: "2026-07-17T10:40:00Z",
    });
    vi.spyOn(siweApi, "verifySiwe").mockRejectedValue(
      new Error("signature_invalid"),
    );

    render(<WalletSignIn discoveryWindowMs={5} />);
    await clickThroughPicker();

    await waitFor(() => {
      expect(screen.getByTestId("wallet-error")).toHaveTextContent(
        "signature_invalid",
      );
    });
  });

  it("shows the no-wallet hint when nothing announces", async () => {
    const user = userEvent.setup();
    render(<WalletSignIn discoveryWindowMs={5} />);
    await user.click(screen.getByTestId("wallet-connect"));
    await waitFor(() => {
      expect(screen.getByTestId("wallet-error")).toHaveTextContent(
        /no browser wallet detected/i,
      );
    });
  });
});
