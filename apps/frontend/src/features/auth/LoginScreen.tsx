/**
 * Login screen (A9): IdP picker + email/password form.
 *
 * Renders the providers returned by ``GET /v1/auth/providers``. OIDC
 * buttons redirect to ``/v1/auth/oidc/{id}/start`` so the IdP handles
 * the dance; the local-password form drives ``AuthContext.login``.
 *
 * Bank deploys hide signup + reset links via ``hideSelfService`` (a
 * later PR threads the C1 toggle through).
 */

import type { FormEvent, ReactElement } from "react";
import { useEffect, useState } from "react";

import { listAuthProviders, type SessionIdentity } from "../../api/authApi";
import type { AuthProviderSummary } from "@enterprise-search/api-types";
import { useAuth } from "./AuthContext";

export interface LoginScreenProps {
  /** Default org slug to pull provider list from. SaaS deploys derive
   * this from the URL subdomain; single-tenant deploys hardcode the
   * singleton org id at build time. */
  defaultOrgId: string;
  /** Hide signup + reset links (bank deploys). */
  hideSelfService?: boolean;
  /** Optional path to navigate to after a successful login. The caller
   * is responsible for the actual navigation — we just emit the value
   * via the ``onAuthenticated`` callback. */
  returnTo?: string;
  onAuthenticated?(args: {
    identity: SessionIdentity;
    returnTo: string | null;
  }): void;
}

export function LoginScreen({
  defaultOrgId,
  hideSelfService = false,
  returnTo,
  onAuthenticated,
}: LoginScreenProps): ReactElement {
  const auth = useAuth();
  const [orgId, setOrgId] = useState(defaultOrgId);
  const [providers, setProviders] = useState<AuthProviderSummary[]>([]);
  const [providersError, setProvidersError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Fire the post-login callback once the AuthContext flips to
  // ``authenticated`` (covers both the OIDC redirect-back case and the
  // local-password case after MFA, if any).
  useEffect(() => {
    if (auth.status === "authenticated" && auth.identity && onAuthenticated) {
      onAuthenticated({
        identity: auth.identity,
        returnTo: returnTo ?? null,
      });
    }
  }, [auth.status, auth.identity, onAuthenticated, returnTo]);

  // Load providers when org_id changes (debounced lightly via the
  // controlled input).
  useEffect(() => {
    if (!orgId) {
      setProviders([]);
      return;
    }
    let cancelled = false;
    setProvidersError(null);
    listAuthProviders(orgId)
      .then((list) => {
        if (!cancelled) {
          setProviders(list);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message =
            err instanceof Error
              ? err.message
              : "could not load identity providers";
          setProvidersError(message);
          setProviders([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [orgId]);

  const oidcProviders = providers.filter((p) => p.kind === "oidc" && p.enabled);
  const localProviderEnabled = providers.some(
    (p) => p.kind === "local" && p.enabled,
  );
  // Default to local-on when the providers endpoint hasn't enumerated
  // them (e.g. the ``providers`` table is empty in dev) so the user
  // isn't locked out.
  const showLocalForm = providers.length === 0 || localProviderEnabled;

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) {
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      await auth.login({ orgId, email, password });
    } catch (err) {
      const message = err instanceof Error ? err.message : "login failed";
      setFormError(message);
    } finally {
      setSubmitting(false);
    }
  };

  const oidcStartUrl = (providerId: string): string => {
    const params = new URLSearchParams({
      org_id: orgId,
      // The redirect URL the IdP will bounce back to. Backend reads it
      // off ``oidc_authentications`` so the value here is informational.
      redirect_uri: window.location.origin + "/v1/auth/oidc/callback",
    });
    if (returnTo) {
      params.set("return_to", returnTo);
    }
    return `/v1/auth/oidc/${encodeURIComponent(providerId)}/start?${params}`;
  };

  return (
    <main className="auth-login" data-testid="login-screen">
      <h1 className="auth-login__title">Sign in</h1>

      <label className="auth-login__field">
        <span>Organization</span>
        <input
          type="text"
          value={orgId}
          onChange={(e) => setOrgId(e.target.value)}
          autoComplete="organization"
          data-testid="login-org"
        />
      </label>

      {providersError && (
        <p className="auth-login__error" role="alert">
          {providersError}
        </p>
      )}

      {oidcProviders.length > 0 && (
        <section className="auth-login__idp-list" data-testid="login-idp-list">
          {oidcProviders.map((provider) => (
            <a
              key={provider.provider_id}
              href={oidcStartUrl(provider.provider_id)}
              className="auth-login__idp-button"
              data-provider-id={provider.provider_id}
            >
              Sign in with {provider.display_name}
            </a>
          ))}
        </section>
      )}

      {showLocalForm && (
        <form
          className="auth-login__form"
          onSubmit={onSubmit}
          data-testid="login-form"
        >
          <label>
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              data-testid="login-email"
            />
          </label>
          <label>
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
              data-testid="login-password"
            />
          </label>
          <button
            type="submit"
            disabled={submitting || !email || !password}
            data-testid="login-submit"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
          {formError && (
            <p className="auth-login__error" role="alert">
              {formError}
            </p>
          )}
        </form>
      )}

      {!hideSelfService && showLocalForm && (
        <footer className="auth-login__self-service">
          <a href="/forgot-password">Forgot password?</a>
        </footer>
      )}
    </main>
  );
}
