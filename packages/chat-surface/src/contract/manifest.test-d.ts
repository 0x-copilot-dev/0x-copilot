// Type-level test (PRD-03 DoD 4) — the manifest exhaustiveness guard fires.
//
// This file is TYPECHECKED, never run (the `.test-d.ts` suffix keeps vitest
// from picking it up while `tsconfig.json` still includes it). A manifest tuple
// that OMITS a real `ShellHostBinding` field must be a `tsc` error. Because an
// unfired `@ts-expect-error` is itself a `tsc` error, this guards both
// directions: it fails if omitting a field ever stops being an error, too.

import type { ExhaustiveBindingManifest } from "./manifest";
import type { ShellHostBinding } from "./shellBinding";

// A field list that OMITS `topbarLeaf`.
const _incompleteFields = [
  "railIdentity",
  "walletChip",
  "settingsActive",
] as const;

// For an incomplete list, `ExhaustiveBindingManifest` resolves to the error
// tuple `["binding manifest is missing a field", "topbarLeaf"]`, so assigning
// the real field list to it must fail — and the directive fires.
type _IncompleteManifest = ExhaustiveBindingManifest<
  ShellHostBinding,
  typeof _incompleteFields
>;

// @ts-expect-error binding manifest is missing a field
const _missingTopbarLeaf: _IncompleteManifest = _incompleteFields;
void _missingTopbarLeaf;
