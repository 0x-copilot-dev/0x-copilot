/**
 * EIP-6963 multi-wallet discovery — no wagmi/viem, just the two
 * standard window events:
 *
 *   dispatch `eip6963:requestProvider`  → every installed wallet replies
 *   listen   `eip6963:announceProvider` ← CustomEvent{ detail: {info, provider} }
 *
 * Announcements are synchronous in practice (wallets register their
 * listener at content-script injection time), but the collector keeps a
 * short grace window so late responders still make the list. When
 * nothing announces we fall back to the legacy `window.ethereum`
 * injection, labelled "Browser wallet", so pre-6963 wallets keep
 * working.
 */

export interface Eip6963ProviderInfo {
  /** UUIDv4 unique per provider *instance* (changes across page loads). */
  uuid: string;
  /** Human-readable wallet name ("MetaMask", "Rabby", …). */
  name: string;
  /** Data/HTTPS URI of the wallet icon (≥96×96 per the EIP). */
  icon: string;
  /** Reverse-DNS identifier ("io.metamask") — stable across loads. */
  rdns: string;
}

export interface Eip1193RequestArguments {
  method: string;
  params?: unknown[] | Record<string, unknown>;
}

export interface Eip1193Provider {
  request(args: Eip1193RequestArguments): Promise<unknown>;
}

export interface WalletProviderCandidate {
  info: Eip6963ProviderInfo;
  provider: Eip1193Provider;
}

export const EIP6963_ANNOUNCE_EVENT = "eip6963:announceProvider";
export const EIP6963_REQUEST_EVENT = "eip6963:requestProvider";

/** Synthetic rdns for the legacy `window.ethereum` fallback entry. */
export const BROWSER_WALLET_RDNS = "window.ethereum";

const DEFAULT_DISCOVERY_WINDOW_MS = 250;

interface AnnouncedDetail {
  info?: Partial<Eip6963ProviderInfo> | null;
  provider?: unknown;
}

function candidateFromDetail(
  detail: AnnouncedDetail | null | undefined,
): WalletProviderCandidate | null {
  const info = detail?.info;
  const provider = detail?.provider;
  if (
    !info ||
    typeof info.uuid !== "string" ||
    typeof info.name !== "string" ||
    typeof info.rdns !== "string" ||
    provider === null ||
    typeof provider !== "object" ||
    typeof (provider as Eip1193Provider).request !== "function"
  ) {
    return null;
  }
  return {
    info: {
      uuid: info.uuid,
      name: info.name,
      icon: typeof info.icon === "string" ? info.icon : "",
      rdns: info.rdns,
    },
    provider: provider as Eip1193Provider,
  };
}

function windowEthereumFallback(): WalletProviderCandidate | null {
  const injected = (window as { ethereum?: unknown }).ethereum;
  if (
    injected === null ||
    injected === undefined ||
    typeof injected !== "object" ||
    typeof (injected as Eip1193Provider).request !== "function"
  ) {
    return null;
  }
  return {
    info: {
      uuid: "browser-wallet-fallback",
      name: "Browser wallet",
      icon: "",
      rdns: BROWSER_WALLET_RDNS,
    },
    provider: injected as Eip1193Provider,
  };
}

/**
 * Discover installed wallets. Resolves after `windowMs` with every
 * distinct provider that announced (deduped by uuid, then rdns — some
 * wallets re-announce on every request event). Falls back to
 * `window.ethereum` when the window closes empty; resolves `[]` when
 * there is no wallet at all.
 */
export function discoverWalletProviders(
  options: { windowMs?: number } = {},
): Promise<WalletProviderCandidate[]> {
  const windowMs = options.windowMs ?? DEFAULT_DISCOVERY_WINDOW_MS;
  return new Promise((resolve) => {
    const found: WalletProviderCandidate[] = [];
    const seen = new Set<string>();

    const onAnnounce = (event: Event): void => {
      const candidate = candidateFromDetail(
        (event as CustomEvent<AnnouncedDetail>).detail,
      );
      if (candidate === null) return;
      const key = candidate.info.uuid || candidate.info.rdns;
      if (seen.has(key) || seen.has(candidate.info.rdns)) return;
      seen.add(key);
      seen.add(candidate.info.rdns);
      found.push(candidate);
    };

    window.addEventListener(EIP6963_ANNOUNCE_EVENT, onAnnounce);
    window.dispatchEvent(new Event(EIP6963_REQUEST_EVENT));

    window.setTimeout(() => {
      window.removeEventListener(EIP6963_ANNOUNCE_EVENT, onAnnounce);
      if (found.length > 0) {
        resolve(found);
        return;
      }
      const fallback = windowEthereumFallback();
      resolve(fallback === null ? [] : [fallback]);
    }, windowMs);
  });
}
