// Desktop host binding (PRD-03).
//
// ONE place constructs the desktop's `ShellHostBinding` from the desktop's own
// inputs (the signed-in `RendererSession` + the shell's Settings-active state),
// so the bootstrap mount and the `bindingContract.test.tsx` conformance test
// build the exact same object. Every field is required by the contract — a
// forgotten field is a compile error, not a silently-dark capability.

import type { ShellHostBinding } from "@0x-copilot/chat-surface";
import type { RendererSession } from "@0x-copilot/chat-transport";

/**
 * Build the total shell binding for the desktop host.
 *
 * - `railIdentity` — the signed-in display name (already on screen at sign-in;
 *   `main/auth` populates `RendererSession.displayName`). Blank → `null` (the
 *   neutral glyph). PRD-12 derives the glyph/title from the name.
 * - `walletChip` / `topbarLeaf` — `null` on desktop today. Making them explicit
 *   `null` (not an omitted `?:`) is the whole point: the gap is now reviewable.
 * - `settingsActive` — whether the Settings surface is full-bleed active.
 */
export function buildDesktopShellBinding(
  session: Pick<RendererSession, "displayName">,
  settingsActive: boolean,
): ShellHostBinding {
  const name = session.displayName?.trim();
  return {
    railIdentity:
      name !== undefined && name.length > 0 ? { displayName: name } : null,
    walletChip: null,
    topbarLeaf: null,
    settingsActive,
  };
}

// PRD-10 DoD 9 closed the desktop project-detail gap: `ProjectsBinder`
// (destinationBinders.tsx) now owns the focus state and builds the `enabled`
// `ProjectsDetailBinding` inline (mounting the shared `ProjectDetailView`
// through the `renderDetail` slot), so the former placeholder
// `DESKTOP_PROJECTS_DETAIL = { mode: "disabled" }` const is gone — desktop is no
// longer a host without a detail flow.
