// Type-level test (PRD-03 DoD 5) — the shell binding is TOTAL for the desktop
// host. Typechecked by `npm run typecheck --workspace @0x-copilot/desktop`
// (tsconfig includes `renderer/**/*.ts`), never run.
//
// Omitting a required field must be a `tsc` error. Because an unfired
// `@ts-expect-error` is itself an error, this also fails if `railIdentity` ever
// stops being required — no manual mutation of `bootstrap.tsx` needed.

import type { ShellHostBinding } from "@0x-copilot/chat-surface";

// @ts-expect-error missing railIdentity
const _missingRailIdentity: ShellHostBinding = {
  walletChip: null,
  topbarLeaf: null,
  settingsActive: false,
};
void _missingRailIdentity;
