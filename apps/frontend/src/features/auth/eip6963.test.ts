/**
 * EIP-6963 discovery — fake wallets announce in response to the
 * request event, exactly like real content scripts do.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  discoverWalletProviders,
  EIP6963_ANNOUNCE_EVENT,
  EIP6963_REQUEST_EVENT,
  BROWSER_WALLET_RDNS,
  type Eip6963ProviderInfo,
  type Eip1193Provider,
} from "./eip6963";

const teardowns: Array<() => void> = [];

afterEach(() => {
  while (teardowns.length > 0) {
    teardowns.pop()?.();
  }
  delete (window as { ethereum?: unknown }).ethereum;
  vi.restoreAllMocks();
});

function fakeProvider(): Eip1193Provider {
  return { request: vi.fn(async () => null) };
}

function installFakeWallet(
  info: Partial<Eip6963ProviderInfo>,
  provider: unknown = fakeProvider(),
  announcesPerRequest = 1,
): void {
  const onRequest = (): void => {
    for (let i = 0; i < announcesPerRequest; i += 1) {
      window.dispatchEvent(
        new CustomEvent(EIP6963_ANNOUNCE_EVENT, { detail: { info, provider } }),
      );
    }
  };
  window.addEventListener(EIP6963_REQUEST_EVENT, onRequest);
  teardowns.push(() =>
    window.removeEventListener(EIP6963_REQUEST_EVENT, onRequest),
  );
}

describe("discoverWalletProviders", () => {
  it("collects every announced provider in announce order", async () => {
    installFakeWallet({
      uuid: "u-1",
      name: "MetaMask",
      icon: "data:image/svg+xml,mm",
      rdns: "io.metamask",
    });
    installFakeWallet({
      uuid: "u-2",
      name: "Rabby",
      icon: "data:image/svg+xml,rb",
      rdns: "io.rabby",
    });
    const providers = await discoverWalletProviders({ windowMs: 5 });
    expect(providers.map((p) => p.info.name)).toEqual(["MetaMask", "Rabby"]);
    expect(providers.map((p) => p.info.rdns)).toEqual([
      "io.metamask",
      "io.rabby",
    ]);
  });

  it("dedupes re-announcements of the same wallet", async () => {
    installFakeWallet(
      { uuid: "u-1", name: "MetaMask", icon: "", rdns: "io.metamask" },
      fakeProvider(),
      3,
    );
    const providers = await discoverWalletProviders({ windowMs: 5 });
    expect(providers).toHaveLength(1);
  });

  it("ignores malformed announcements (no request function, missing info)", async () => {
    installFakeWallet(
      { uuid: "u-1", name: "Broken", icon: "", rdns: "io.broken" },
      { notRequest: true },
    );
    installFakeWallet({ name: "NoUuid" } as Partial<Eip6963ProviderInfo>);
    const providers = await discoverWalletProviders({ windowMs: 5 });
    expect(providers).toEqual([]);
  });

  it("falls back to window.ethereum labelled 'Browser wallet' when nothing announces", async () => {
    const injected = fakeProvider();
    (window as { ethereum?: unknown }).ethereum = injected;
    const providers = await discoverWalletProviders({ windowMs: 5 });
    expect(providers).toHaveLength(1);
    expect(providers[0].info.name).toBe("Browser wallet");
    expect(providers[0].info.rdns).toBe(BROWSER_WALLET_RDNS);
    expect(providers[0].provider).toBe(injected);
  });

  it("prefers announced providers over window.ethereum", async () => {
    (window as { ethereum?: unknown }).ethereum = fakeProvider();
    installFakeWallet({
      uuid: "u-1",
      name: "MetaMask",
      icon: "",
      rdns: "io.metamask",
    });
    const providers = await discoverWalletProviders({ windowMs: 5 });
    expect(providers.map((p) => p.info.name)).toEqual(["MetaMask"]);
  });

  it("resolves empty when there is no wallet at all", async () => {
    const providers = await discoverWalletProviders({ windowMs: 5 });
    expect(providers).toEqual([]);
  });
});
