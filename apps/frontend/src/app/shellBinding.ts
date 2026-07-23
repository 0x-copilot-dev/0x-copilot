// Web host binding (PRD-03).
//
// ONE place constructs the web host's `ShellHostBinding`, so the App.tsx mount
// and the `bindingContract.test.tsx` conformance test build the same object.
// Symmetric with the desktop `renderer/shellBinding.ts`. Every field is required
// by the contract — a forgotten field is a compile error, not a dark capability.

import type { ShellHostBinding } from "@0x-copilot/chat-surface";

/**
 * Build the total shell binding for the web host.
 *
 * - `railIdentity` — the signed-in profile's display name; blank → `null`.
 * - `walletChip` / `topbarLeaf` — unbound on web today, so an explicit `null`
 *   (a reviewable diff) rather than a silently-omitted optional prop.
 * - `settingsActive` — web renders Settings as its own route, so the shell's
 *   full-bleed Settings flag stays `false`.
 */
export function buildWebShellBinding(
  displayName: string | null | undefined,
  settingsActive: boolean,
): ShellHostBinding {
  const name = displayName?.trim();
  return {
    railIdentity:
      name !== undefined && name.length > 0 ? { displayName: name } : null,
    walletChip: null,
    topbarLeaf: null,
    settingsActive,
  };
}
