/**
 * Google-link callback landing screen (account-linking PRD FR-L2 client
 * half). The facade 302-redirects a Google LINK outcome to
 * `/oauth/link/callback?link_status=…`; this screen reads that query and
 * shows the result in product UI (instead of the raw JSON the browser used
 * to land on), then returns the user to where they started.
 *
 * Rendered by `AuthGate` when the pathname matches — before the app shell,
 * so it works whether or not the session has finished rehydrating (the
 * sensitive link already happened server-side; this only communicates it).
 */

import { Button, Card } from "@0x-copilot/design-system";
import type { ReactElement } from "react";

import {
  SETTINGS_PROFILE_ROUTE,
  parseGoogleLinkOutcome,
  type GoogleLinkOutcome,
} from "./googleLinkLanding";

function headline(outcome: GoogleLinkOutcome): string {
  switch (outcome.status) {
    case "linked":
      return "Google account linked";
    case "already_linked":
      return "Google account already linked";
    case "merge_required":
      return "This Google account belongs to another account";
    default:
      return "Couldn’t finish linking";
  }
}

function body(outcome: GoogleLinkOutcome): string {
  switch (outcome.status) {
    case "linked":
      return outcome.emailUpgraded
        ? "You can now sign in with Google, and your verified Google email is now on this account."
        : "You can now sign in with Google.";
    case "already_linked":
      return "This Google account was already linked to your account — nothing changed.";
    case "merge_required":
      return (
        "It’s already tied to a different 0xCopilot account. To combine them, " +
        "link that account’s wallet from Settings and confirm the merge there — " +
        "we don’t merge from a Google sign-in for your security."
      );
    default:
      return (
        outcome.message ??
        "The link didn’t complete. Please return to Settings and try again."
      );
  }
}

export interface GoogleLinkLandingProps {
  /** The `?link_status=…` query string. Defaults to the live location. */
  readonly search?: string;
  /** Injectable for tests; defaults to a hard navigation. */
  readonly navigate?: (url: string) => void;
}

export function GoogleLinkLanding(props: GoogleLinkLandingProps): ReactElement {
  const search =
    props.search ??
    (typeof window !== "undefined" ? window.location.search : "");
  const outcome = parseGoogleLinkOutcome(search);
  const back = outcome.returnTo ?? SETTINGS_PROFILE_ROUTE;
  const go = props.navigate ?? ((url: string) => window.location.assign(url));
  const isError = outcome.status === "error";

  return (
    <main className="app-loading" data-testid="google-link-landing">
      <Card
        className="login-card"
        tone={isError ? "danger" : "default"}
        data-testid={`google-link-landing-${outcome.status}`}
      >
        <header className="login-card__head">
          <h2>{headline(outcome)}</h2>
          <p>{body(outcome)}</p>
        </header>
        <Button
          type="button"
          variant="primary"
          size="lg"
          onClick={() => go(back)}
          data-testid="google-link-landing-continue"
        >
          {outcome.status === "merge_required"
            ? "Go to Settings"
            : "Back to Copilot"}
        </Button>
      </Card>
    </main>
  );
}
