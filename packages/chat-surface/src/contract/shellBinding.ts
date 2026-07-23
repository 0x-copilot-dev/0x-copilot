// Host binding contract (PRD-03 — Move 2).
//
// The props boundary between `@0x-copilot/chat-surface` and its two hosts
// (web `apps/frontend`, desktop `apps/desktop`) is TOTAL: every host-owned
// capability the shell needs is answered here as a REQUIRED field, and
// `undefined` is not in any union. An omitted field is therefore a compile
// error, and an opt-out is a literal `null` that shows up in the diff and gets
// reviewed — instead of a silent `?:` a host can decline forever.
//
// Why this exists: nine chat-surface capabilities shipped dark because each was
// an optional prop the desktop host simply never passed. Optionality on the
// props interface is what made the obligation invisible. Moving optionality
// from the TYPE to the VALUE (a literal `null`) is the fix.
//
// Substrate-agnostic: these are pure data shapes over `@0x-copilot/api-types` +
// `react` node types. No `window` / `fetch` / navigation lives here.

import type { ReactNode } from "react";

import type { ProjectId } from "@0x-copilot/api-types";

import type { RenderProjectDetailSlot } from "../destinations/projects/ProjectsDestination";

/**
 * Total binding for the shell's host-owned capabilities (the rail foot avatar,
 * the FTUE wallet chip, the topbar sub-crumb, and whether the Settings surface
 * is active). Every field is required; `undefined` is not permitted, so a host
 * that forgets one fails to compile.
 *
 * - `railIdentity` carries the raw DISPLAY NAME (PRD-12 owns deriving the glyph
 *   from it — `AppRail` still takes `{ initial }` today, so `ChatShell` shims at
 *   its `<AppRail>` call). `null` = no signed-in identity → the neutral glyph.
 * - `walletChip` / `topbarLeaf` are `null` on both hosts today; making them
 *   required-nullable is exactly how the "unbound on both hosts" class becomes
 *   a reviewable `null` rather than a forgotten `?:`.
 */
export interface ShellHostBinding {
  readonly railIdentity: { readonly displayName: string } | null;
  readonly walletChip: ReactNode | null;
  readonly topbarLeaf: string | null;
  readonly settingsActive: boolean;
}

/**
 * The Projects destination's detail capability, as a discriminated union so a
 * host that has no project-detail flow must say so EXPLICITLY (`{ mode:
 * "disabled" }`) instead of silently omitting `renderDetail` + `focusedProjectId`
 * and leaving the detail branch dead code. `enabled` carries every piece the
 * detail slot needs together, so a half-wired detail (a `renderDetail` with no
 * `focusedProjectId`) can no longer typecheck.
 */
export type ProjectsDetailBinding =
  | { readonly mode: "disabled" }
  | {
      readonly mode: "enabled";
      readonly focusedProjectId: ProjectId | null;
      readonly renderDetail: RenderProjectDetailSlot;
      readonly onCloseDetail: () => void;
    };

/**
 * Total binding for the Projects destination's host-owned capabilities. One
 * field today (`detail`); shaped as an interface so the manifest
 * (`PROJECTS_BINDING_FIELDS`) can derive exhaustiveness the same way the shell
 * binding does.
 */
export interface ProjectsHostBinding {
  readonly detail: ProjectsDetailBinding;
}
