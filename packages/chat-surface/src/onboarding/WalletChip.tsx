// WalletChip — the top-bar SIWE identity pill (SPEC §Copy · PRD-P4 §1).
//
// Pure presentational: `0x7f3C…a92C` + jade status dot. Rendered into the
// FirstRunSurface `walletChipSlot` for wallet (SIWE) accounts; renders NOTHING
// for email/Google accounts (`address === null`). No I/O, no ports, no globals
// — the connected variant lives in `providers/FirstRunProfileProvider`.
//
// The server returns the FULL EIP-55 address (`me_profile.py` uses
// `display_address`, not `truncated_display_address`), so truncation is a
// display concern and lives HERE.

import type { ReactElement } from "react";

/**
 * Truncate a full EIP-55 address to the SPEC `0x{4}…{4}` chip form
 * (`0x7f3C…a92C`). Input is the full address — never a pre-truncated value.
 */
export function truncateAddress(addr: string): string {
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

export interface WalletChipProps {
  /**
   * Full EIP-55 checksummed address, or `null` for email/Google accounts —
   * in which case the chip renders `null` (SIWE-only affordance).
   */
  readonly address: string | null;
  /** Chain label surfaced in the native tooltip ("Ethereum", "Base"…). */
  readonly chainName?: string | null;
  /**
   * Wallet is linked/connected → show the jade status dot. Defaults to `true`
   * (an address present means a linked wallet); a host may pass `false` to
   * render the address without the live dot.
   */
  readonly connected?: boolean;
}

export function WalletChip({
  address,
  chainName,
  connected = true,
}: WalletChipProps): ReactElement | null {
  // SIWE-only: email/Google accounts have no wallet → nothing renders.
  if (address === null) {
    return null;
  }

  // The tooltip reveals the full (untruncated) address plus the chain label.
  const title = chainName ? `${address} · ${chainName}` : address;

  return (
    <span
      className="fr-wchip"
      data-testid="first-run-wallet-chip"
      title={title}
    >
      {connected ? (
        <span
          className="fr-wchip__dot"
          data-testid="first-run-wallet-dot"
          aria-hidden="true"
        />
      ) : null}
      <span className="fr-wchip__addr">{truncateAddress(address)}</span>
    </span>
  );
}
