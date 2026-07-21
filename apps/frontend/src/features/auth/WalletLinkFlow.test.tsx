/**
 * WalletLinkFlow — nonce → sign → POST /v1/me/identities/wallet against a
 * fake EIP-1193 provider announced over real EIP-6963 events. The siwe +
 * me APIs are spied (no network), mirroring WalletSignIn.test.tsx.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TransportHttpError } from "@0x-copilot/chat-transport";
import type { LinkWalletResult } from "@0x-copilot/api-types";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as meApi from "../../api/meApi";
import * as siweApi from "../../api/siweApi";
import {
  EIP6963_ANNOUNCE_EVENT,
  EIP6963_REQUEST_EVENT,
  type Eip1193RequestArguments,
} from "./eip6963";
import { WalletLinkFlow } from "./WalletLinkFlow";

const ADDRESS_EIP55 = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359";
const SIGNATURE = `0x${"ab".repeat(65)}`;

const LINKED: LinkWalletResult = {
  status: "linked",
  wallet_id: "wid_1",
  address: ADDRESS_EIP55,
  chain_id: 1,
  chain_name: "Ethereum",
};

const teardowns: Array<() => void> = [];

afterEach(() => {
  while (teardowns.length > 0) teardowns.pop()?.();
  vi.restoreAllMocks();
});

function installFakeWallet(): void {
  const request = vi.fn(
    async ({ method }: Eip1193RequestArguments): Promise<unknown> => {
      if (method === "eth_requestAccounts") return [ADDRESS_EIP55];
      if (method === "eth_chainId") return "0x1";
      if (method === "personal_sign") return SIGNATURE;
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
}

async function clickThroughPicker(): Promise<void> {
  const user = userEvent.setup();
  await user.click(screen.getByTestId("wallet-link-trigger"));
  await waitFor(() =>
    expect(screen.getByTestId("wallet-link-picker")).toBeInTheDocument(),
  );
  await user.click(screen.getByTestId("wallet-link-provider-dev.testwallet"));
}

describe("WalletLinkFlow", () => {
  it("links a wallet: nonce → sign → POST, then calls onLinked", async () => {
    installFakeWallet();
    vi.spyOn(siweApi, "requestSiweNonce").mockResolvedValue({
      nonce: "n0nce",
      expires_at: "2030-01-01T00:00:00Z",
    });
    const linkSpy = vi.spyOn(meApi, "linkWallet").mockResolvedValue(LINKED);
    const onLinked = vi.fn();

    render(<WalletLinkFlow onLinked={onLinked} discoveryWindowMs={5} />);
    await clickThroughPicker();

    await waitFor(() => expect(onLinked).toHaveBeenCalledTimes(1));
    expect(linkSpy).toHaveBeenCalledTimes(1);
    // First (unconfirmed) attempt does not consent to a merge.
    const [, , confirmMerge] = linkSpy.mock.calls[0];
    expect(confirmMerge).toBe(false);
  });

  it("shows the merge-confirm on 409 and re-links with consent on confirm", async () => {
    installFakeWallet();
    vi.spyOn(siweApi, "requestSiweNonce").mockResolvedValue({
      nonce: "n0nce",
      expires_at: "2030-01-01T00:00:00Z",
    });
    const linkSpy = vi
      .spyOn(meApi, "linkWallet")
      .mockRejectedValueOnce(
        new TransportHttpError(409, "already owned", {
          code: "merge_required",
          safe_message: "already owned",
        }),
      )
      .mockResolvedValueOnce({ ...LINKED, status: "merged" });
    const onLinked = vi.fn();

    render(<WalletLinkFlow onLinked={onLinked} discoveryWindowMs={5} />);
    await clickThroughPicker();

    // The explicit confirm appears; nothing merged yet.
    await screen.findByTestId("wallet-link-merge-confirm");
    expect(onLinked).not.toHaveBeenCalled();

    const user = userEvent.setup();
    await user.click(screen.getByTestId("wallet-link-merge-confirm-btn"));

    await waitFor(() => expect(onLinked).toHaveBeenCalledTimes(1));
    expect(linkSpy).toHaveBeenCalledTimes(2);
    // The confirmed re-submit carries consent AND a fresh signature (the
    // nonce is single-use — a replay would be rejected server-side).
    expect(linkSpy.mock.calls[1][2]).toBe(true);
  });

  it("cancel on the merge-confirm does not merge", async () => {
    installFakeWallet();
    vi.spyOn(siweApi, "requestSiweNonce").mockResolvedValue({
      nonce: "n0nce",
      expires_at: "2030-01-01T00:00:00Z",
    });
    const linkSpy = vi.spyOn(meApi, "linkWallet").mockRejectedValueOnce(
      new TransportHttpError(409, "already owned", {
        code: "merge_required",
        safe_message: "already owned",
      }),
    );
    const onLinked = vi.fn();

    render(<WalletLinkFlow onLinked={onLinked} discoveryWindowMs={5} />);
    await clickThroughPicker();
    await screen.findByTestId("wallet-link-merge-confirm");

    const user = userEvent.setup();
    await user.click(screen.getByTestId("wallet-link-merge-cancel"));
    await waitFor(() =>
      expect(screen.queryByTestId("wallet-link-merge-confirm")).toBeNull(),
    );
    expect(linkSpy).toHaveBeenCalledTimes(1);
    expect(onLinked).not.toHaveBeenCalled();
  });
});
