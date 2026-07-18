/**
 * Email / magic-link login — RETAINED, UNPLUGGED FROM THE UI.
 *
 * // email login unplugged from UI per design — code retained
 *
 * The v2 "0xCopilot Login" design is wallet-first (wallet · Google ·
 * use-locally) and deliberately drops the email entry point from the
 * rendered surface. None of the auth machinery was removed: the
 * email-first discovery + magic-link components below are extracted here
 * verbatim from the previous ``LoginScreen`` so the flow is dead-but-
 * present and trivial to re-plug — import ``EmailStep`` back into
 * ``LoginScreen``'s pick view and the whole email path lights up again.
 *
 * The backend (``/v1/auth/discover``, ``/v1/auth/magic-link``, the
 * workspace-pick exchange) is untouched. ``MagicLinkCallbackStep`` and
 * ``WorkspacePickStep`` stay in ``LoginScreen`` because those completion
 * paths are still reachable (an already-sent magic-link URL, or a
 * multi-workspace return) even though no new links can be requested from
 * the UI.
 *
 * State machine (unchanged from the original email-first screen):
 *
 *   email
 *     └─ submit → kind=sso       → redirect (window.location.assign)
 *     └─ submit → kind=personal  → magic_link_sent
 *     └─ submit → kind=magic_link→ magic_link_sent
 *     └─ submit → kind=unknown   → error inline (bank-profile message)
 */

import {
  Button,
  Card,
  Field,
  TextInput,
  classNames,
} from "@0x-copilot/design-system";
import type { AuthDiscoverResponse } from "@0x-copilot/api-types";
import type { FormEvent, ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { discoverAuth, startMagicLink } from "../../api/authApi";
import { errorMessage } from "../../utils/errors";

const DEBOUNCE_MS = 450;
const EMAIL_SHAPE_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export const GOOGLE_PROVIDER_ID = "google";

// ---------------------------------------------------------------------------
// "Continue with Google" — shared between the retained email path and the
// v2 wallet-first pick view. Same mechanism as <RedirectStep>: navigate to
// the existing facade OIDC start URL and let the server drive the rest.
// Unlike the discovered-SSO path there is no org_id yet — the Google account
// decides the workspace server-side.
// ---------------------------------------------------------------------------

/** Build the facade Google OIDC start URL (no org_id pre-auth). */
export function buildGoogleStartUrl(returnTo: string | null): string {
  const params = new URLSearchParams({
    redirect_uri: window.location.origin + "/v1/auth/oidc/callback",
  });
  if (returnTo) {
    params.set("return_to", returnTo);
  }
  return `/v1/auth/oidc/${encodeURIComponent(GOOGLE_PROVIDER_ID)}/start?${params}`;
}

/** Official four-colour Google "G" mark (brand guidelines; never re-tint). */
export function GoogleGLogo({
  className,
}: {
  className?: string;
}): ReactElement {
  return (
    <svg
      className={className}
      viewBox="0 0 48 48"
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  );
}

/** Retained email-path Google entry point (dark/light branded button). */
export function GoogleSignInButton({
  returnTo,
}: {
  returnTo: string | null;
}): ReactElement {
  const onClick = useCallback((): void => {
    window.location.assign(buildGoogleStartUrl(returnTo));
  }, [returnTo]);

  return (
    <button
      type="button"
      className="login-google-btn"
      onClick={onClick}
      data-testid="login-google"
    >
      <GoogleGLogo className="login-google-btn__logo" />
      <span>Continue with Google</span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Email step
// ---------------------------------------------------------------------------

export interface EmailStepProps {
  defaultOrgId?: string;
  hideMagicLink: boolean;
  returnTo?: string;
  /** True when the unscoped providers list advertises Google login. */
  googleLogin: boolean;
  onRedirect(provider_id: string, org_id: string): void;
  onMagicLinkSent(email: string): void;
}

type DiscoveryState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: AuthDiscoverResponse }
  | { kind: "error"; message: string };

export function EmailStep({
  hideMagicLink,
  returnTo,
  googleLogin,
  onRedirect,
  onMagicLinkSent,
}: EmailStepProps): ReactElement {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [discovery, setDiscovery] = useState<DiscoveryState>({ kind: "idle" });
  const debounceRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const el = document.getElementById(
      "login-email-input",
    ) as HTMLInputElement | null;
    el?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    if (abortRef.current !== null) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    if (!_emailLooksComplete(email)) {
      setDiscovery({ kind: "idle" });
      return;
    }
    setDiscovery({ kind: "loading" });
    debounceRef.current = window.setTimeout(() => {
      const controller = new AbortController();
      abortRef.current = controller;
      discoverAuth({ email })
        .then((data) => {
          if (controller.signal.aborted) return;
          setDiscovery({ kind: "ready", data });
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          const message = errorMessage(err, "could not look up domain");
          setDiscovery({ kind: "error", message });
        });
    }, DEBOUNCE_MS);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
  }, [email]);

  const submit = useCallback(
    async (event: FormEvent<HTMLFormElement>): Promise<void> => {
      event.preventDefault();
      if (submitting || !_emailLooksComplete(email)) {
        return;
      }
      setSubmitting(true);
      setSubmitError(null);
      try {
        const result =
          discovery.kind === "ready"
            ? discovery.data
            : await discoverAuth({ email });
        if (result.kind === "sso" && result.provider_id && result.org_id) {
          onRedirect(result.provider_id, result.org_id);
          return;
        }
        if (
          (result.kind === "personal" || result.kind === "magic_link") &&
          result.magic_link_supported &&
          !hideMagicLink
        ) {
          await startMagicLink({ email, return_to: returnTo });
          onMagicLinkSent(email);
          return;
        }
        setSubmitError(
          result.message ??
            "Your workspace requires single sign-on. Contact your admin.",
        );
      } catch (err) {
        const message = errorMessage(err, "login failed");
        setSubmitError(message);
      } finally {
        setSubmitting(false);
      }
    },
    [
      submitting,
      email,
      discovery,
      hideMagicLink,
      returnTo,
      onRedirect,
      onMagicLinkSent,
    ],
  );

  const buttonLabel = _adaptiveButtonLabel(discovery, hideMagicLink);
  const data = discovery.kind === "ready" ? discovery.data : null;
  const canSubmit = !submitting && _emailLooksComplete(email);

  return (
    <Card className="login-card login-card--email" tone="default">
      <header className="login-card__head">
        <h2>Sign in to Copilot</h2>
        <p>
          Enter your work email — we&rsquo;ll route you to the right sign-in.
        </p>
      </header>
      {googleLogin && <GoogleSignInButton returnTo={returnTo ?? null} />}
      <div className="login-divider" role="separator">
        <span>or continue with email</span>
      </div>
      <form
        className="login-card__form"
        onSubmit={submit}
        data-testid="login-email-form"
        noValidate
      >
        <Field label="Email" className="login-card__field">
          <TextInput
            id="login-email-input"
            type="email"
            inputMode="email"
            autoComplete="email"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              setSubmitError(null);
            }}
            data-testid="login-email-input"
          />
        </Field>
        {data !== null && <DiscoveryCard data={data} />}
        {discovery.kind === "loading" && (
          <p className="login-card__hint" role="status">
            Checking your domain&rsquo;s directory…
          </p>
        )}
        {submitError && (
          <p
            className="login-card__error"
            role="alert"
            data-testid="login-error"
          >
            {submitError}
          </p>
        )}
        <Button
          type="submit"
          variant="primary"
          size="lg"
          disabled={!canSubmit}
          data-testid="login-submit"
          className={classNames("login-card__submit")}
        >
          {submitting ? "One moment…" : buttonLabel}
        </Button>
      </form>
    </Card>
  );
}

function _emailLooksComplete(email: string): boolean {
  return EMAIL_SHAPE_RE.test(email.trim());
}

function _adaptiveButtonLabel(
  state: DiscoveryState,
  hideMagicLink: boolean,
): string {
  if (state.kind !== "ready") return "Continue";
  const data = state.data;
  if (data.kind === "sso" && data.provider_display_name) {
    return `Continue with ${data.provider_display_name}`;
  }
  if (
    (data.kind === "personal" || data.kind === "magic_link") &&
    data.magic_link_supported &&
    !hideMagicLink
  ) {
    return "Email me a sign-in link";
  }
  return "Continue";
}

// ---------------------------------------------------------------------------
// Discovery card (shown beneath the email field once a result is in)
// ---------------------------------------------------------------------------

export function DiscoveryCard({
  data,
}: {
  data: AuthDiscoverResponse;
}): ReactElement {
  if (data.kind === "sso") {
    return (
      <div
        className="login-discovery login-discovery--sso"
        data-testid="login-discovery-sso"
      >
        <div className="login-discovery__row">
          <strong className="login-discovery__org">
            {data.org_display_name ?? data.org_id}
          </strong>
          <span className="login-discovery__domain">· {data.domain}</span>
        </div>
        <div className="login-discovery__row">
          Sign in with{" "}
          <strong>
            {data.provider_display_name ?? data.provider_kind ?? "SSO"}
          </strong>
          {data.member_count !== null && (
            <span className="login-discovery__members">
              · {data.member_count.toLocaleString()} members
            </span>
          )}
        </div>
        {data.sso_enforced && (
          <span
            className="login-discovery__badge"
            data-testid="login-discovery-sso-enforced"
          >
            SSO enforced
          </span>
        )}
      </div>
    );
  }
  if (data.kind === "personal") {
    return (
      <div
        className="login-discovery login-discovery--personal"
        data-testid="login-discovery-personal"
      >
        <strong>{data.provider_display_name ?? "Personal"} account</strong>
        <p className="login-discovery__hint">
          We&rsquo;ll email you a one-time sign-in link.
        </p>
      </div>
    );
  }
  if (data.kind === "magic_link") {
    return (
      <div
        className="login-discovery login-discovery--unknown"
        data-testid="login-discovery-unknown"
      >
        <strong>No SSO found for {data.domain}</strong>
        <p className="login-discovery__hint">
          We&rsquo;ll email you a one-time sign-in link.
        </p>
      </div>
    );
  }
  return (
    <div
      className="login-discovery login-discovery--blocked"
      data-testid="login-discovery-blocked"
      role="alert"
    >
      <strong>{data.message ?? "Single sign-on required."}</strong>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Redirect step — ships the user to the existing OIDC/SAML start URL.
// ---------------------------------------------------------------------------

export function RedirectStep({
  provider_id,
  org_id,
  returnTo,
}: {
  provider_id: string;
  org_id: string;
  returnTo: string | null;
}): ReactElement {
  useEffect(() => {
    const params = new URLSearchParams({
      org_id,
      redirect_uri: window.location.origin + "/v1/auth/oidc/callback",
    });
    if (returnTo) {
      params.set("return_to", returnTo);
    }
    window.location.assign(
      `/v1/auth/oidc/${encodeURIComponent(provider_id)}/start?${params}`,
    );
  }, [provider_id, org_id, returnTo]);

  return (
    <Card className="login-card login-card--redirect" tone="muted">
      <header className="login-card__head">
        <h2>Redirecting to your IdP…</h2>
        <p>
          You&rsquo;ll come back here once your IdP confirms it&rsquo;s you.
        </p>
      </header>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Magic-link sent
// ---------------------------------------------------------------------------

export function MagicLinkSent({
  email,
  onBack,
}: {
  email: string;
  onBack(): void;
}): ReactElement {
  return (
    <Card className="login-card login-card--magic-sent" tone="default">
      <header className="login-card__head">
        <h2>Check your email</h2>
        <p>
          We sent a one-time sign-in link to <strong>{email}</strong>. The link
          is valid for 15 minutes.
        </p>
      </header>
      <p className="login-card__hint">
        Didn&rsquo;t see it? Check your spam folder, or{" "}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onBack}
          data-testid="login-magic-back"
        >
          use a different email
        </Button>
        .
      </p>
    </Card>
  );
}
