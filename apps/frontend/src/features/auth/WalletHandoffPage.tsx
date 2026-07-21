/**
 * Standalone wallet sign-in page for the desktop app (`/wallet.html`).
 *
 * The desktop main process binds an ephemeral loopback listener (see
 * `apps/desktop/main/auth/loopback-server.ts`), then opens this page in
 * the system browser as
 *
 *   {origin}/wallet.html?handoff=http://127.0.0.1:<port>/<path>
 *
 * The page renders ONLY `<WalletSignIn>` against the same-origin facade.
 * On success it redirects the browser to the loopback with the bearer
 * handoff in the query string — the same delivery mechanism the Google
 * desktop flow uses (browser → GET loopback with query params, loopback
 * replies with its own "you can close this window" page) and the same
 * field names as the OIDC callback handoff JSON
 * (`user_id, session_id, bearer_token, expires_at, requires_mfa,
 * return_to` — see `CallbackHandoff` in
 * `apps/desktop/main/auth/google-login.ts` / backend `OidcCallbackResult`).
 *
 * Safety: the bearer is only ever sent to a loopback target
 * (127.0.0.1 / [::1] / localhost over http). Anything else in `?handoff=`
 * is rejected before sign-in starts, so a crafted link cannot exfiltrate
 * a session to a remote host.
 */

import { Card } from "@0x-copilot/design-system";
import type { SiweSessionResponse } from "@0x-copilot/api-types";
import type { ReactElement } from "react";
import { useCallback, useState } from "react";

import { WalletSignIn } from "./WalletSignIn";

const LOOPBACK_HOSTNAMES = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

/**
 * Validate a raw `?handoff=` value. Returns the normalised loopback URL,
 * or null when it is absent, unparsable, or not an http loopback target
 * (never start a sign-in whose redirect we would refuse).
 */
export function validateLoopbackHandoff(
  raw: string | null | undefined,
): string | null {
  if (raw === null || raw === undefined || raw === "") return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "http:") return null;
  if (!LOOPBACK_HOSTNAMES.has(url.hostname)) return null;
  return url.toString();
}

/** Extract + validate the `?handoff=` target from a query string. */
export function parseHandoffTarget(search: string): string | null {
  return validateLoopbackHandoff(new URLSearchParams(search).get("handoff"));
}

/**
 * Append the session handoff to the loopback URL as query parameters,
 * mirroring the OIDC callback handoff's field names.
 */
export function buildHandoffRedirectUrl(
  handoffUrl: string,
  session: SiweSessionResponse,
): string {
  const url = new URL(handoffUrl);
  url.searchParams.set("bearer_token", session.bearer_token);
  url.searchParams.set("user_id", session.user_id);
  url.searchParams.set("session_id", session.session_id);
  url.searchParams.set("expires_at", session.expires_at);
  url.searchParams.set("requires_mfa", String(session.requires_mfa));
  if (session.return_to !== null && session.return_to !== undefined) {
    url.searchParams.set("return_to", session.return_to);
  }
  return url.toString();
}

/**
 * LINK mode (account-linking PRD FR-L1): append the raw SIWE proof to the
 * loopback URL — NO bearer. The desktop main process reads `message` +
 * `signature` (state already round-trips in the handoff target) and POSTs
 * the link with the caller's own bearer.
 */
export function buildProofRedirectUrl(
  handoffUrl: string,
  proof: { message: string; signature: string },
): string {
  const url = new URL(handoffUrl);
  url.searchParams.set("message", proof.message);
  url.searchParams.set("signature", proof.signature);
  return url.toString();
}

export interface WalletHandoffPageProps {
  /** Raw `?handoff=` query value — validated here, not trusted. */
  rawHandoff: string | null;
  /**
   * LINK mode (account-linking PRD FR-L1): sign-and-relay-the-PROOF instead
   * of verify-and-relay-the-session. The desktop opens this page with
   * `?mode=link` when the user links a wallet from Settings.
   */
  mode?: "sign-in" | "link";
  /** Injectable for tests; defaults to a hard navigation. */
  navigate?: (url: string) => void;
}

export function WalletHandoffPage(props: WalletHandoffPageProps): ReactElement {
  const { rawHandoff, mode = "sign-in", navigate } = props;
  const target = validateLoopbackHandoff(rawHandoff);
  const [done, setDone] = useState(false);
  const isLink = mode === "link";

  const go = navigate ?? ((url: string) => window.location.assign(url));

  const onSession = useCallback(
    (session: SiweSessionResponse): void => {
      if (target === null) return;
      const redirect = buildHandoffRedirectUrl(target, session);
      // Show the "return to the app" state first — it stays on screen
      // while the loopback loads (and if the app already shut the
      // listener down, the user still gets a sensible page).
      setDone(true);
      go(redirect);
    },
    [target, go],
  );

  const onProof = useCallback(
    (proof: { message: string; signature: string }): void => {
      if (target === null) return;
      const redirect = buildProofRedirectUrl(target, proof);
      setDone(true);
      go(redirect);
    },
    [target, go],
  );

  const heading = isLink ? "Link your wallet" : "Sign in with your wallet";
  const instruction = isLink
    ? "Approve the connection and signature in your wallet. You’ll be sent back to the Atlas app to finish linking."
    : "Approve the connection and signature in your wallet. You’ll be sent back to the Atlas app when it’s done.";
  const doneText = isLink
    ? "Wallet signed — return to the app to finish linking."
    : "You’re signed in — return to the app.";

  return (
    <div className="wallet-page" data-testid="wallet-page">
      <Card className="wallet-page__card" tone="default">
        <header className="login-card__head">
          <h2>{heading}</h2>
          {target === null ? (
            <p
              className="login-card__error"
              role="alert"
              data-testid="wallet-page-bad-handoff"
            >
              This page was opened without a valid app handoff target. Return to
              the Atlas app and start wallet sign-in again.
            </p>
          ) : done ? (
            <p data-testid="wallet-page-done">{doneText}</p>
          ) : (
            <p>{instruction}</p>
          )}
        </header>
        {target !== null &&
          !done &&
          (isLink ? (
            <WalletSignIn onProof={onProof} />
          ) : (
            <WalletSignIn onSession={onSession} />
          ))}
      </Card>
    </div>
  );
}
