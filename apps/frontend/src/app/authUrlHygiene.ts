// Web-substrate-only URL hygiene for auth flows.
//
// Lives in apps/frontend/src/app/ (not features/) so the auth code can
// invoke it without a `window.history` reference inside features/. The
// desktop substrate uses real OIDC + system-browser loopback callbacks
// and never sees these query params, so there's no substrate-portable
// interface needed — the helper simply doesn't exist on desktop.

export function stripMagicLinkTokenFromUrl(): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("token")) {
      return;
    }
    // Drop the entire query string — preserves the prior LoginScreen
    // behavior, and the magic-link callback URL never carries unrelated
    // query params in practice. Back-button replay is the failure mode
    // we're guarding against.
    window.history.replaceState({}, "", url.pathname);
  } catch {
    // URL parsing or history failures must not break the surrounding
    // auth flow; the only consequence is a stale ?token= in the URL bar.
  }
}
