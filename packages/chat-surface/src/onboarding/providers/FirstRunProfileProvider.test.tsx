// FirstRunProfileProvider — loads once via the injected port, exposes the
// snapshot through the hook, and drives the connected `FirstRunWalletChip`
// (PRD-P4 §1).

import { render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import type {
  FirstRunProfilePort,
  WalletProfileView,
} from "../ports/FirstRunProfilePort";
import {
  FirstRunProfileProvider,
  FirstRunWalletChip,
  useFirstRunProfile,
} from "./FirstRunProfileProvider";

const WALLET: WalletProfileView = {
  walletAddress: "0x7f3C4b1e2a9d8c7b6a5f4e3d2c1b0a9f8e7d6c5b",
  chainId: 8453,
  chainName: "Base",
  authMethod: "siwe",
  emailIsPlaceholder: true,
};

const EMAIL: WalletProfileView = {
  walletAddress: null,
  chainId: null,
  chainName: null,
  authMethod: "google",
  emailIsPlaceholder: false,
};

function resolvingPort(view: WalletProfileView): FirstRunProfilePort {
  return { get: vi.fn(async () => view) };
}

function ProfileProbe(): ReactElement {
  const { profile, loading, error } = useFirstRunProfile();
  return (
    <>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="addr">{profile?.walletAddress ?? "none"}</span>
      <span data-testid="chain">{profile?.chainName ?? "none"}</span>
      <span data-testid="error">{error ? error.message : "none"}</span>
    </>
  );
}

describe("<FirstRunProfileProvider>", () => {
  it("resolves the profile once and exposes it via useFirstRunProfile", async () => {
    const port = resolvingPort(WALLET);
    render(
      <FirstRunProfileProvider port={port}>
        <ProfileProbe />
      </FirstRunProfileProvider>,
    );

    // Starts loading before the port settles.
    expect(screen.getByTestId("loading").textContent).toBe("true");

    await waitFor(() =>
      expect(screen.getByTestId("loading").textContent).toBe("false"),
    );
    expect(screen.getByTestId("addr").textContent).toBe(WALLET.walletAddress);
    expect(screen.getByTestId("chain").textContent).toBe("Base");
    expect(screen.getByTestId("error").textContent).toBe("none");
    expect(port.get).toHaveBeenCalledTimes(1);
  });

  it("calls port.get() exactly once across a re-render (memoized)", async () => {
    const port = resolvingPort(WALLET);
    const tree = (
      <FirstRunProfileProvider port={port}>
        <ProfileProbe />
      </FirstRunProfileProvider>
    );
    const { rerender } = render(tree);
    await waitFor(() =>
      expect(screen.getByTestId("loading").textContent).toBe("false"),
    );
    rerender(tree);
    rerender(tree);
    expect(port.get).toHaveBeenCalledTimes(1);
  });

  it("surfaces a rejection as `error` with profile null", async () => {
    const boom = new Error("profile unavailable");
    const port: FirstRunProfilePort = { get: vi.fn().mockRejectedValue(boom) };
    render(
      <FirstRunProfileProvider port={port}>
        <ProfileProbe />
      </FirstRunProfileProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("error").textContent).toBe(
        "profile unavailable",
      ),
    );
    expect(screen.getByTestId("loading").textContent).toBe("false");
    expect(screen.getByTestId("addr").textContent).toBe("none");
  });
});

describe("<FirstRunWalletChip>", () => {
  it("renders the truncated chip for a wallet account", async () => {
    render(
      <FirstRunProfileProvider port={resolvingPort(WALLET)}>
        <FirstRunWalletChip />
      </FirstRunProfileProvider>,
    );
    const chip = await screen.findByTestId("first-run-wallet-chip");
    expect(chip.textContent).toContain("0x7f3C…6c5b");
    expect(screen.getByTestId("first-run-wallet-dot")).toBeTruthy();
    expect(chip.getAttribute("title")).toContain("Base");
  });

  it("renders nothing for an email/Google account", async () => {
    const port = resolvingPort(EMAIL);
    render(
      <FirstRunProfileProvider port={port}>
        <FirstRunWalletChip />
      </FirstRunProfileProvider>,
    );
    await waitFor(() => expect(port.get).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("first-run-wallet-chip")).toBeNull();
  });
});
