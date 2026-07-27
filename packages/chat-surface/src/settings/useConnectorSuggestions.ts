// Suggestion appetite, bound to `GET`/`PUT /v1/me/preferences`.
//
// Sibling of `useAppearanceSettings` and deliberately the same shape: the
// round-trip is substrate-agnostic (Transport port only), so both hosts pass
// their own transport and share one implementation rather than each growing a
// preferences fetch of its own.
//
// Autosaved, not deferred behind the SaveBar: this is a three-option Select,
// so every change is a complete decision. The tool-call cap on the same page
// is deferred precisely because it is free text, where every keystroke would
// otherwise be a PUT.
//
// The PUT sends only `discoverable_connectors.mode`. The server merges depth-2,
// so a write here cannot clobber the per-slug `overrides` the suggestion card
// writes — the two controls edit the same block from different surfaces and
// must not race each other's state.
//
// It also surfaces the MUTED slugs, because the card's "Deny" now persists one.
// A mute that could only be set and never seen again would be a one-way door: a
// single misclick would silently remove a connector from every future run's
// suggestions with no way to find out. The same GET already returns them, so
// the reversal costs a list, not a round-trip.

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ConnectorSuggestionMode,
  UserPreferences,
} from "@0x-copilot/api-types";

import type { Transport } from "../ports/Transport";

const PREFERENCES_PATH = "/v1/me/preferences";

/**
 * Shown before the round-trip resolves, and after a load failure.
 *
 * It matches the server's own default rather than `off`: a user whose
 * preferences failed to load should still hear a suggestion that would
 * unblock their run, and rendering `off` would state a preference they never
 * expressed.
 */
export const DEFAULT_CONNECTOR_SUGGESTIONS: ConnectorSuggestionMode =
  "unblock_only";

export interface MutedConnector {
  readonly slug: string;
  /**
   * Derived from the slug, not fetched. The overrides map stores slugs alone,
   * and a catalog round-trip to prettify a label the user muted themselves is
   * not worth the coupling — `google-drive` reads fine as "Google Drive".
   */
  readonly displayName: string;
}

export interface ConnectorSuggestionsController {
  readonly value: ConnectorSuggestionMode;
  readonly loading: boolean;
  readonly error: string | null;
  readonly change: (next: ConnectorSuggestionMode) => void;
  /** Connectors the user muted, so the decision stays reversible. */
  readonly muted: readonly MutedConnector[];
  /** Drop a slug's override entirely — back to the catalog default. */
  readonly unmute: (slug: string) => void;
}

/** `google-drive` → `Google Drive`. */
function displayNameForSlug(slug: string): string {
  return slug
    .split(/[-_]/)
    .filter((part) => part !== "")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function mutedFrom(prefs: UserPreferences): readonly MutedConnector[] {
  const overrides = prefs.discoverable_connectors?.overrides ?? {};
  return (
    Object.entries(overrides)
      // `false` is the mute. A `true` override is the opposite decision — the
      // user asking for a connector the catalog does not suggest by default —
      // and listing it here would invite them to "unmute" something they opted
      // INTO.
      .filter(([, enabled]) => enabled === false)
      .map(([slug]) => ({ slug, displayName: displayNameForSlug(slug) }))
      .sort((a, b) => a.displayName.localeCompare(b.displayName))
  );
}

function errorMessage(err: unknown): string {
  return err instanceof Error && err.message
    ? err.message
    : "Could not load connector-suggestion preferences.";
}

export function useConnectorSuggestions(
  transport: Transport,
): ConnectorSuggestionsController {
  const [value, setValue] = useState<ConnectorSuggestionMode>(
    DEFAULT_CONNECTOR_SUGGESTIONS,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [muted, setMuted] = useState<readonly MutedConnector[]>([]);

  // Ref so `change` reads the live transport without re-creating the callback
  // when a host passes a fresh identity each render.
  const transportRef = useRef(transport);
  transportRef.current = transport;

  useEffect(() => {
    let cancelled = false;
    transportRef.current
      .request<UserPreferences>({ method: "GET", path: PREFERENCES_PATH })
      .then((prefs) => {
        if (cancelled) return;
        setValue(
          prefs.discoverable_connectors?.mode ?? DEFAULT_CONNECTOR_SUGGESTIONS,
        );
        setMuted(mutedFrom(prefs));
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // Keep the shipped default on screen; do not invent `off`.
        setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const change = useCallback((next: ConnectorSuggestionMode) => {
    // Optimistic: the Select should not lag a network round-trip. A failed
    // save surfaces an error rather than silently reverting, because
    // snapping the control back mid-interaction reads as a UI bug.
    setValue(next);
    void transportRef.current
      .request({
        method: "PUT",
        path: PREFERENCES_PATH,
        body: { discoverable_connectors: { mode: next } },
      })
      .then(() => setError(null))
      .catch((err: unknown) => setError(errorMessage(err)));
  }, []);

  const unmute = useCallback((slug: string) => {
    // Optimistic, like `change` above.
    setMuted((prev) => prev.filter((entry) => entry.slug !== slug));
    void transportRef.current
      .request<UserPreferences>({
        method: "PUT",
        path: PREFERENCES_PATH,
        // `true`, not a delete: the merge is depth-2 and recursive, so an
        // omitted key is left alone rather than removed. Restoring the catalog
        // default would need a delete verb the endpoint does not have — and
        // `true` is what the user is asking for anyway ("suggest this again").
        body: { discoverable_connectors: { overrides: { [slug]: true } } },
      })
      .then(() => setError(null))
      .catch((err: unknown) => {
        // Put it back: unlike the appetite Select, a row that vanished and did
        // not save would leave the user believing a mute was lifted.
        setMuted((prev) =>
          prev.some((entry) => entry.slug === slug)
            ? prev
            : [...prev, { slug, displayName: displayNameForSlug(slug) }].sort(
                (a, b) => a.displayName.localeCompare(b.displayName),
              ),
        );
        setError(errorMessage(err));
      });
  }, []);

  return { value, loading, error, change, muted, unmute };
}
