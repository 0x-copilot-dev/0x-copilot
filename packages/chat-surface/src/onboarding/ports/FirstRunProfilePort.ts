// FirstRunProfilePort — the host-injected read of the signed-in user's identity
// for the first-run wallet chip (PRD-P4 §1).
//
// chat-surface stays substrate-clean: it never calls `fetch`/IPC directly. The
// HOST implements this port over its Transport against `GET /v1/me/profile`
// (`UserProfile`), projecting only the fields the chip needs. The server returns
// the FULL EIP-55 checksummed `wallet_address` (`me_profile.py:506,527` uses
// `display_address`, NOT `truncated_display_address`) — so `WalletProfileView`
// also carries the full address and truncation-for-display lives in the
// component (`WalletChip`/`truncateAddress`), never in the transport.
//
// Identity is server-derived (the facade overrides org/user from the verified
// session) — the surface never sends identity, and never treats these fields as
// authoritative for anything but rendering the chip.

export interface WalletProfileView {
  /**
   * Full EIP-55 checksummed wallet address, or `null` for email/Google
   * accounts (mirrors `UserProfile.wallet_address`). The chip is SIWE-only:
   * `null` here renders nothing.
   */
  readonly walletAddress: string | null;
  /** EVM chain id the wallet linked on (`null` when there is no wallet). */
  readonly chainId: number | null;
  /** Human chain label ("Ethereum", "Base"…) for the chip tooltip. */
  readonly chainName: string | null;
  /**
   * Durable auth origin ("siwe", "google", "password"…) — mirrors
   * `UserProfile.auth_method`. `null` on older servers.
   */
  readonly authMethod: string | null;
  /**
   * True when `email` is the `<address>@wallet.invalid` placeholder a SIWE
   * account carries (mirrors `UserProfile.email_is_placeholder`) — the FE
   * renders the wallet anchor instead of the undeliverable email.
   */
  readonly emailIsPlaceholder: boolean;
}

/**
 * The single-method first-run profile read. The host implements it over its
 * Transport and is expected to dedupe/cache; the provider calls it once per
 * mount.
 */
export interface FirstRunProfilePort {
  get(): Promise<WalletProfileView>;
}
