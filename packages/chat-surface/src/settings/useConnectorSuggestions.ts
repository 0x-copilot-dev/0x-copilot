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

export interface ConnectorSuggestionsController {
  readonly value: ConnectorSuggestionMode;
  readonly loading: boolean;
  readonly error: string | null;
  readonly change: (next: ConnectorSuggestionMode) => void;
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

  return { value, loading, error, change };
}
