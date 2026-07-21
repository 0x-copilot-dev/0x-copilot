// FirstRunProfileProvider — reads the wallet-chip identity ONCE through the
// host-injected `FirstRunProfilePort` and exposes it via `useFirstRunProfile`
// (PRD-P4 §1).
//
// The provider owns the load lifecycle so the chip (and any host chrome) can
// render off a memoized snapshot without re-hitting `GET /v1/me/profile` on
// every render. `FirstRunWalletChip` is the connected sink: it maps the loaded
// `WalletProfileView` onto the pure `WalletChip`, so the profile→props mapping
// lives in ONE place across both hosts (each host supplies only the port).

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import type {
  FirstRunProfilePort,
  WalletProfileView,
} from "../ports/FirstRunProfilePort";
import { WalletChip } from "../WalletChip";

export interface FirstRunProfileState {
  /** The loaded identity projection, or `null` before the first resolve. */
  readonly profile: WalletProfileView | null;
  /** `true` until `port.get()` settles (resolve or reject). */
  readonly loading: boolean;
  /** The load error, if `port.get()` rejected; otherwise `null`. */
  readonly error: Error | null;
}

const DEFAULT_STATE: FirstRunProfileState = {
  profile: null,
  loading: true,
  error: null,
};

const FirstRunProfileContext =
  createContext<FirstRunProfileState>(DEFAULT_STATE);

export interface FirstRunProfileProviderProps {
  /** Host-injected profile read (over its Transport). */
  readonly port: FirstRunProfilePort;
  readonly children: ReactNode;
}

export function FirstRunProfileProvider({
  port,
  children,
}: FirstRunProfileProviderProps): ReactElement {
  const [state, setState] = useState<FirstRunProfileState>(DEFAULT_STATE);
  // Fetch exactly once per provider mount — the chip is boot-time chrome, not
  // a live feed. `requestedRef` guards against a re-fetch if `port` identity
  // churns; `active` ignores a late resolve after unmount.
  const requestedRef = useRef(false);

  useEffect(() => {
    if (requestedRef.current) {
      return;
    }
    requestedRef.current = true;
    let active = true;
    port
      .get()
      .then((profile) => {
        if (active) {
          setState({ profile, loading: false, error: null });
        }
      })
      .catch((err: unknown) => {
        if (active) {
          setState({
            profile: null,
            loading: false,
            error: err instanceof Error ? err : new Error(String(err)),
          });
        }
      });
    return () => {
      active = false;
    };
  }, [port]);

  const value = useMemo<FirstRunProfileState>(() => state, [state]);

  return (
    <FirstRunProfileContext.Provider value={value}>
      {children}
    </FirstRunProfileContext.Provider>
  );
}

/** Read the once-loaded first-run profile snapshot. */
export function useFirstRunProfile(): FirstRunProfileState {
  return useContext(FirstRunProfileContext);
}

/**
 * Connected wallet chip — the single consumer of `useFirstRunProfile`. Renders
 * nothing until the profile loads and nothing for email/Google accounts
 * (`walletAddress === null` → `WalletChip` returns `null`). Drop it into the
 * FirstRunSurface `walletChipSlot` under a `FirstRunProfileProvider`.
 */
export function FirstRunWalletChip(): ReactElement | null {
  const { profile } = useFirstRunProfile();
  if (profile === null) {
    return null;
  }
  return (
    <WalletChip
      address={profile.walletAddress}
      chainName={profile.chainName}
      connected
    />
  );
}
