// PR 4.4.6 — MCP error classifier.
//
// The backend's ``/auth/start`` and ``/install`` endpoints can fail for
// the same root cause: the vendor's MCP server doesn't expose RFC 8414
// metadata or RFC 7591 dynamic client registration, so the install
// needs a pre-registered OAuth client (client_id + client_secret).
// Surfacing the raw 4xx body to the user means dumping JSON into a
// connector card. This module classifies the error so the UI can show
// a "Setup required" CTA that opens the credentials form instead.

const SETUP_PATTERN =
  /authorization-server metadata|dynamic client registration|configured OAuth client|pre-registered OAuth client/i;

export class OAuthSetupRequiredError extends Error {
  readonly code = "OAUTH_SETUP_REQUIRED";

  constructor(
    public readonly target:
      | { kind: "server"; serverId: string }
      | { kind: "slug"; slug: string },
    message: string,
  ) {
    super(message);
    this.name = "OAuthSetupRequiredError";
  }
}

export function isOAuthSetupRequired(
  err: unknown,
): err is OAuthSetupRequiredError {
  return err instanceof OAuthSetupRequiredError;
}

/**
 * Wrap a thrown error from `/auth/start` or `/install` and convert it
 * to ``OAuthSetupRequiredError`` when its message matches the
 * vendor-setup pattern. Other errors pass through unchanged so callers
 * can still surface real network / validation failures.
 */
export function classifyMcpError(
  target: OAuthSetupRequiredError["target"],
  err: unknown,
): Error {
  if (err instanceof OAuthSetupRequiredError) {
    return err;
  }
  if (err instanceof Error && SETUP_PATTERN.test(err.message)) {
    return new OAuthSetupRequiredError(
      target,
      "Setup required — provide an OAuth client for this server.",
    );
  }
  return err instanceof Error ? err : new Error("Connector action failed.");
}
