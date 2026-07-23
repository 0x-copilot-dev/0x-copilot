// Binding manifests (PRD-03 — Move 3, the enforcement gate).
//
// Types catch OMISSION (a missing required field is a compile error). They do
// NOT catch two things: (1) a manifest that drifts out of sync with its binding
// type, and (2) arity discard at a host call site (a 0-arity callback silently
// assignable to a 1-arity slot). This module handles (1); the host conformance
// tests (`bindingContract.test.tsx` in each app) handle (2).
//
// Each manifest is the runtime-enumerable list of a binding type's fields. The
// `ExhaustiveBindingManifest` helper makes the manifest fail `tsc` INSIDE this
// package the moment it omits a field the binding type declares — so adding a
// field to a binding without adding it to the manifest is a compile error, and
// the two host conformance tests then iterate the manifest and go red until
// both hosts answer the new field.

import type { ProjectsHostBinding, ShellHostBinding } from "./shellBinding";

/**
 * A manifest tuple is "exhaustive" for a binding type when every key of the
 * binding appears in the tuple. When it is, this resolves to the tuple's own
 * type (assignment succeeds). When a key is MISSING, it resolves to an error
 * tuple whose second element names the missing key, so assigning the real
 * `as const` manifest to it fails `tsc` — pointing straight at the omission.
 */
export type ExhaustiveBindingManifest<
  TBinding,
  TFields extends readonly (keyof TBinding)[],
> =
  Exclude<keyof TBinding, TFields[number]> extends never
    ? TFields
    : [
        "binding manifest is missing a field",
        Exclude<keyof TBinding, TFields[number]>,
      ];

// === Shell binding manifest =================================================

export const SHELL_BINDING_FIELDS = [
  "railIdentity",
  "walletChip",
  "topbarLeaf",
  "settingsActive",
] as const;

// Compile-time exhaustiveness guard: if `ShellHostBinding` grows a field that
// is not added above, this assignment fails `tsc` inside chat-surface.
const _shellManifestExhaustive: ExhaustiveBindingManifest<
  ShellHostBinding,
  typeof SHELL_BINDING_FIELDS
> = SHELL_BINDING_FIELDS;
void _shellManifestExhaustive;

// === Projects binding manifest ==============================================

export const PROJECTS_BINDING_FIELDS = ["detail"] as const;

const _projectsManifestExhaustive: ExhaustiveBindingManifest<
  ProjectsHostBinding,
  typeof PROJECTS_BINDING_FIELDS
> = PROJECTS_BINDING_FIELDS;
void _projectsManifestExhaustive;
