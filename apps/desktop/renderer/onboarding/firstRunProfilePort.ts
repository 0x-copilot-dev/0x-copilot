// Desktop `FirstRunProfilePort` — the wallet-chip identity read over the
// Transport (PRD-P4 §1).
//
// chat-surface stays substrate-clean (it never calls IPC/fetch); this host
// implementation performs the single GET the port contract describes:
//   GET /v1/me/profile                                            → UserProfile
// and projects only the fields the top-bar `WalletChip` needs. The server
// returns the FULL EIP-55 `wallet_address` (`me_profile.py` uses
// `display_address`, not `truncated_display_address`), so `WalletProfileView`
// also carries the full address and truncation-for-display lives in the
// component (`WalletChip`/`truncateAddress`), never here.
//
// Identity is server-derived (the facade injects org/user from the bearer), so —
// like the desktop `FirstRunRunsPort` / `SettingsMount` profile read — the body
// carries NO identity.

import type { Transport } from "@0x-copilot/chat-transport";
import type {
  FirstRunProfilePort,
  WalletProfileView,
} from "@0x-copilot/chat-surface";
import type { UserProfile } from "@0x-copilot/api-types";

/**
 * Build the desktop `FirstRunProfilePort` bound to a Transport. Projects the
 * loaded `UserProfile` onto the chip's `WalletProfileView`; a degraded
 * response (empty/`null`) projects to an all-null view (the chip renders
 * nothing — SIWE-only), never throws.
 */
export function createFirstRunProfilePort(
  transport: Transport,
): FirstRunProfilePort {
  return {
    async get(): Promise<WalletProfileView> {
      const profile = await transport.request<UserProfile | null>({
        method: "GET",
        path: "/v1/me/profile",
      });
      return {
        walletAddress: profile?.wallet_address ?? null,
        chainId: profile?.chain_id ?? null,
        chainName: profile?.chain_name ?? null,
        authMethod: profile?.auth_method ?? null,
        emailIsPlaceholder: profile?.email_is_placeholder ?? false,
      };
    },
  };
}
