// "Never suggest this again", persisted from the card where the intent forms.
//
// The per-slug mute already existed server-side — `list_suggestible_connectors`
// has always honoured `discoverable_connectors.overrides`, and a per-slug mute
// outranks even the `always` appetite, because "show me everything" is a default
// and "never this one" is a decision. What it lacked was a place to be SET at
// the moment the user forms the opinion. Denying an unsolicited suggestion is
// exactly that moment; sending them to Settings to repeat themselves is not.
//
// This is a plain function rather than a hook because the mute is fire-and-
// forget: nothing on screen depends on the result. The card has already moved to
// `denied` through the consent-state machine, and the effect of the mute is
// visible only in a future run's suggestions.
//
// It writes ONLY `{discoverable_connectors: {overrides: {slug: false}}}`. The
// backend merge is depth-2 and recurses into nested dicts, so a slug-scoped
// patch leaves the global appetite (`mode`) and every other slug intact — which
// matters because the appetite has its own writer in Settings and the two must
// not clobber each other.

import type { Transport } from "@0x-copilot/chat-transport";

const PREFERENCES_PATH = "/v1/me/preferences";

/**
 * Mute a catalog connector so it is never suggested again.
 *
 * Reversible in Settings → Tools, which is why a denial can afford to be this
 * decisive: the alternative (a mute that only lives for this run) makes the
 * agent re-ask for something the user already turned down.
 *
 * Rejections are the caller's to swallow — see `onError`-style handling at the
 * call site. A failed mute is not worth interrupting a run for; the card's
 * `denied` state is already truthful about the decision itself.
 */
export async function muteConnectorSuggestion(
  transport: Transport,
  catalogSlug: string,
): Promise<void> {
  await transport.request<unknown>({
    method: "PUT",
    path: PREFERENCES_PATH,
    body: { discoverable_connectors: { overrides: { [catalogSlug]: false } } },
  });
}
