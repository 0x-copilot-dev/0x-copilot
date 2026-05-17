/**
 * W0.1 — Dev IdP persona persistence.
 *
 * The HTTP client moved to `src/api/devIdpApi.ts` so all `fetch`-callers
 * live under `api/*` (frontend CLAUDE.md). This module keeps only the
 * substrate-portable persona-slug persistence; the API module is the
 * single owner of the wire calls.
 */

import type { KeyValueStore } from "@enterprise-search/chat-surface";

import { PERSONA_SLUG_STORAGE_KEY } from "./storageKeys";

const DEFAULT_PERSONA_SLUG = "sarah_acme";

/** Read the most-recently-selected persona slug, falling back to the default. */
export function loadActivePersonaSlug(store: KeyValueStore): string {
  try {
    return store.get(PERSONA_SLUG_STORAGE_KEY) ?? DEFAULT_PERSONA_SLUG;
  } catch {
    return DEFAULT_PERSONA_SLUG;
  }
}

export function persistActivePersonaSlug(
  store: KeyValueStore,
  slug: string,
): void {
  try {
    store.set(PERSONA_SLUG_STORAGE_KEY, slug);
  } catch {
    // Substrate storage failure (private browsing, quota): mint will
    // still succeed but the choice won't persist across reloads.
  }
}

// Re-exports for backward compatibility — callers can keep importing
// `mintDevBearer` / `listDevPersonas` from here while we move them
// over to the api module in a follow-up sweep.
export {
  listDevPersonas,
  mintDevBearer,
  type DevMintResponse,
  type DevPersonaSummary,
} from "../../api/devIdpApi";
