// Web wrapper around the substrate-agnostic citation-link reducer in
// @0x-copilot/chat-surface.
//
// The core reducer is pure: same inputs → same outputs, no console / no
// DOM / no apps/frontend dependency. The diagnostic breadcrumbs it
// emits go through an injected `onDebug` callback. This wrapper binds
// that callback to the web's `citationDebug` (a `[citations]`-prefixed
// console logger, silenced under vitest) so existing apps/frontend
// callsites keep getting the same diagnostics without each one having
// to pass the logger explicitly.
//
// All non-debug functions are re-exported as-is. Consumers in this app
// continue to import from `chatModel/citationLinkReducer` exactly as
// before; the substrate boundary is invisible to them.

import {
  anyLinkForOrdinalInRun,
  applyCitationLinkEvent as coreApplyCitationLinkEvent,
  buildCitationLinkRegistry as coreBuildCitationLinkRegistry,
  emptyCitationLinkRegistry,
  isCitationLink,
  linkForOrdinal,
  linksForMessage,
  linksForRun,
  upsertCitationLink,
  type CitationLinkRegistryByRun,
  type CitationLinksByMessage,
  type CitationLinksByOffset,
} from "@0x-copilot/chat-surface";
import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { citationDebug } from "./citationDebug";

export type {
  CitationLinkRegistryByRun,
  CitationLinksByMessage,
  CitationLinksByOffset,
};
export {
  anyLinkForOrdinalInRun,
  emptyCitationLinkRegistry,
  isCitationLink,
  linkForOrdinal,
  linksForMessage,
  linksForRun,
  upsertCitationLink,
};

/** Debug-bound wrapper. Existing callers don't pass an `onDebug` arg. */
export function applyCitationLinkEvent(
  registry: CitationLinkRegistryByRun,
  event: RuntimeEventEnvelope,
): CitationLinkRegistryByRun {
  return coreApplyCitationLinkEvent(registry, event, citationDebug);
}

/** Debug-bound wrapper. Existing callers don't pass an `onDebug` arg. */
export function buildCitationLinkRegistry(
  events: Iterable<RuntimeEventEnvelope>,
): CitationLinkRegistryByRun {
  return coreBuildCitationLinkRegistry(events, citationDebug);
}
