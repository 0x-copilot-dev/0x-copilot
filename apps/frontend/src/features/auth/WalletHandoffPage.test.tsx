/**
 * Standalone desktop wallet page — loopback validation, handoff query
 * format, and the end-to-end redirect after a fake wallet signs.
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
import {
  buildHandoffRedirectUrl,
  validateLoopbackHandoff,
  WalletHandoffPage,
} from "./WalletHandoffPage";

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

describe("validateLoopbackHandoff", () => {
  it("accepts loopback http targets", () => {
    expect(validateLoopbackHandoff("http://127.0.0.1:49321/wallet/cb")).toBe(
      "http://127.0.0.1:49321/wallet/cb",
    );
    expect(validateLoopbackHandoff("http://localhost:4000/cb")).toBe(
      "http://localhost:4000/cb",
    );
    expect(validateLoopbackHandoff("http://[::1]:4000/cb")).toBe(
      "http://[::1]:4000/cb",
    );
  });

  it("rejects anything that could exfiltrate the bearer", () => {
    expect(validateLoopbackHandoff(null)).toBeNull();
    expect(validateLoopbackHandoff("")).toBeNull();
    expect(validateLoopbackHandoff("not a url")).toBeNull();
    expect(validateLoopbackHandoff("https://127.0.0.1:4000/cb")).toBeNull();
    expect(validateLoopbackHandoff("http://evil.example/cb")).toBeNull();
    expect(
      validateLoopbackHandoff("http://127.0.0.1.evil.example/cb"),
    ).toBeNull();
    expect(validateLoopbackHandoff("javascript:alert(1)")).toBeNull();
  });
});

describe("buildHandoffRedirectUrl", () => {
  it("mirrors the OIDC callback handoff field names as query params", () => {
    const url = new URL(
      buildHandoffRedirectUrl("http://127.0.0.1:49321/wallet/cb", SESSION),
    );
    expect(url.origin + url.pathname).toBe("http://127.0.0.1:49321/wallet/cb");
    expect(url.searchParams.get("bearer_token")).toBe("brr_w1");
    expect(url.searchParams.get("user_id")).toBe("usr_w1");
    expect(url.searchParams.get("session_id")).toBe("ses_w1");
    expect(url.searchParams.get("expires_at")).toBe("2026-07-17T12:00:00Z");
    expect(url.searchParams.get("requires_mfa")).toBe("false");
    expect(url.searchParams.has("return_to")).toBe(false);
  });

  it("carries return_to when present", () => {
    const url = new URL(
      buildHandoffRedirectUrl("http://127.0.0.1:1/cb", {
        ...SESSION,
        return_to: "/inbox",
      }),
    );
    expect(url.searchParams.get("return_to")).toBe("/inbox");
  });
});

describe("WalletHandoffPage", () => {
  it("refuses to render the wallet flow without a valid loopback handoff", () => {
    render(<WalletHandoffPage rawHandoff="https://evil.example/cb" />);
    expect(screen.getByTestId("wallet-page-bad-handoff")).toBeInTheDocument();
    expect(screen.queryByTestId("wallet-signin")).not.toBeInTheDocument();
  });

  it("redirects the bearer handoff to the loopback after sign-in", async () => {
    // Fake wallet over real EIP-6963 events.
    const request = vi.fn(
      async ({ method }: Eip1193RequestArguments): Promise<unknown> => {
        if (method === "eth_requestAccounts") {
          return ["0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"];
        }
        if (method === "eth_chainId") return "0x2105"; // Base
        if (method === "personal_sign") return `0x${"cd".repeat(65)}`;
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

    vi.spyOn(siweApi, "requestSiweNonce").mockResolvedValue({
      nonce: "n0nce",
      expires_at: "2026-07-17T10:40:00Z",
    });
    vi.spyOn(siweApi, "verifySiwe").mockResolvedValue(SESSION);
    const navigate = vi.fn();

    render(
      <WalletHandoffPage
        rawHandoff="http://127.0.0.1:49321/wallet/cb"
        navigate={navigate}
      />,
    );

    const user = userEvent.setup();
    await user.click(screen.getByTestId("wallet-connect"));
    await waitFor(() => {
      expect(screen.getByTestId("wallet-picker")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("wallet-provider-dev.testwallet"));

    await waitFor(() => {
      expect(navigate).toHaveBeenCalledTimes(1);
    });
    const target = new URL(navigate.mock.calls[0][0] as string);
    expect(target.origin + target.pathname).toBe(
      "http://127.0.0.1:49321/wallet/cb",
    );
    expect(target.searchParams.get("bearer_token")).toBe("brr_w1");
    expect(target.searchParams.get("requires_mfa")).toBe("false");

    // The page flips to its "return to the app" state.
    expect(screen.getByTestId("wallet-page-done")).toBeInTheDocument();
  });
});
