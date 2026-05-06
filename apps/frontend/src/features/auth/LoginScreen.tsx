/**
 * Login screen (PR 5.1) — email-first IdP discovery + magic-link +
 * workspace picker, plus brand right pane.
 *
 * State machine (local to this component, narrows on each transition):
 *
 *   email
 *     └─ submit → kind=sso       → redirect (window.location.assign)
 *     └─ submit → kind=personal  → magic_link_sent
 *     └─ submit → kind=magic_link→ magic_link_sent
 *     └─ submit → kind=unknown   → error inline (bank-profile message)
 *
 *   redirect          (one-shot; the OIDC/SAML start URL handles the rest)
 *   magic_link_sent   ("Check your email"; dead-end until user clicks the URL)
 *   magic_link_cb     (consumes ?token= on mount)
 *   workspace_pick    (driven by auth.workspacePick from AuthContext)
 *
 * MFA is owned by ``AuthGate`` (status === "mfa_pending" → ``<MfaPrompt>``);
 * we don't render it here. Same for the authenticated path.
 *
 * Reuse:
 *   - ``ThemeProvider`` accent / theme already set globally; the brand
 *     pane reads from existing CSS tokens.
 *   - ``MfaPrompt`` mounts after a session is minted with requires_mfa=true;
 *     no extra wiring here.
 *   - ``auth.consumeMagicLink`` handles the bearer write + refresh.
 *   - ``auth.selectWorkspaceFromPick`` handles the pick-token exchange.
 */

import {
  Button,
  Card,
  Field,
  TextInput,
  classNames,
} from "@enterprise-search/design-system";
import type {
  AuthDiscoverResponse,
  WorkspaceCandidate,
} from "@enterprise-search/api-types";
import type { FormEvent, ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { discoverAuth, startMagicLink } from "../../api/authApi";
import { useAuth } from "./AuthContext";

const DEBOUNCE_MS = 450;
const MAGIC_LINK_CALLBACK_PATH = "/auth/magic-link/callback";
const EMAIL_SHAPE_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type LoginStep =
  | { kind: "email" }
  | { kind: "redirect"; provider_id: string; org_id: string }
  | { kind: "magic_link_sent"; email: string }
  | { kind: "magic_link_cb"; token: string }
  | { kind: "workspace_pick" };

export interface LoginScreenProps {
  /** Default org slug — kept for backwards compat with the legacy URL hint
   * (``?org_id=acme``). The new flow doesn't require it; if present, we
   * pre-narrow discovery toward the matching workspace. */
  defaultOrgId?: string;
  /** Hide the magic-link CTA entirely (bank deploys with strict SSO). */
  hideMagicLink?: boolean;
  /** Optional path to navigate to after a successful login. Carried into
   * the magic-link URL as a signed claim on the token (server-side). */
  returnTo?: string;
}

export function LoginScreen(props: LoginScreenProps): ReactElement {
  const auth = useAuth();
  const [step, setStep] = useState<LoginStep>(() => _initialStep(auth));

  // Re-anchor on workspace_pick when the AuthContext flips into it
  // (consumeMagicLink → kind=workspace_pick_required).
  useEffect(() => {
    if (auth.status === "workspace_pick" && step.kind !== "workspace_pick") {
      setStep({ kind: "workspace_pick" });
    }
  }, [auth.status, step.kind]);

  return (
    <div className="login-shell" data-testid="login-screen">
      <Brand />
      <main className="login-pane">
        {step.kind === "email" && (
          <EmailStep
            defaultOrgId={props.defaultOrgId}
            hideMagicLink={props.hideMagicLink ?? false}
            returnTo={props.returnTo}
            onRedirect={(provider_id, org_id) =>
              setStep({ kind: "redirect", provider_id, org_id })
            }
            onMagicLinkSent={(email) =>
              setStep({ kind: "magic_link_sent", email })
            }
          />
        )}
        {step.kind === "redirect" && (
          <RedirectStep
            provider_id={step.provider_id}
            org_id={step.org_id}
            returnTo={props.returnTo ?? null}
          />
        )}
        {step.kind === "magic_link_sent" && (
          <MagicLinkSent
            email={step.email}
            onBack={() => setStep({ kind: "email" })}
          />
        )}
        {step.kind === "magic_link_cb" && (
          <MagicLinkCallbackStep
            token={step.token}
            onError={() => setStep({ kind: "email" })}
          />
        )}
        {step.kind === "workspace_pick" && <WorkspacePickStep />}
      </main>
    </div>
  );
}

function _initialStep(auth: ReturnType<typeof useAuth>): LoginStep {
  if (auth.status === "workspace_pick") {
    return { kind: "workspace_pick" };
  }
  if (typeof window !== "undefined") {
    const url = new URL(window.location.href);
    if (url.pathname === MAGIC_LINK_CALLBACK_PATH) {
      const token = url.searchParams.get("token");
      if (token) {
        return { kind: "magic_link_cb", token };
      }
    }
  }
  return { kind: "email" };
}

// ---------------------------------------------------------------------------
// Brand pane
// ---------------------------------------------------------------------------

function Brand(): ReactElement {
  return (
    <aside className="login-brand" aria-label="Atlas">
      <div className="login-brand__head">
        <div className="login-brand__mark" aria-hidden="true">
          A
        </div>
        <div className="login-brand__name">Atlas</div>
      </div>
      <div className="login-brand__body">
        <div className="login-brand__eyebrow">
          Agentic search for the rest of the company
        </div>
        <h1 className="login-brand__h">
          One place to ask, <em>find,</em> and act — across every tool your team
          already uses.
        </h1>
        <p className="login-brand__lede">
          Atlas reads across your connected tools. It drafts, summarises and
          follows up — with citations, approvals, and a clear paper trail.
        </p>
      </div>
      <footer className="login-brand__foot">
        <span>© 2026 Atlas Labs</span>
        <ul className="login-brand__compliance" aria-label="Compliance">
          <li>SOC 2 Type II</li>
          <li>ISO 27001</li>
          <li>GDPR</li>
          <li>HIPAA</li>
        </ul>
      </footer>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Email step
// ---------------------------------------------------------------------------

interface EmailStepProps {
  defaultOrgId?: string;
  hideMagicLink: boolean;
  returnTo?: string;
  onRedirect(provider_id: string, org_id: string): void;
  onMagicLinkSent(email: string): void;
}

type DiscoveryState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: AuthDiscoverResponse }
  | { kind: "error"; message: string };

function EmailStep({
  hideMagicLink,
  returnTo,
  onRedirect,
  onMagicLinkSent,
}: EmailStepProps): ReactElement {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [discovery, setDiscovery] = useState<DiscoveryState>({ kind: "idle" });
  const debounceRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Auto-focus without scrolling. The login layout overrides body scroll
  // (login.css opt-out) so the page stays anchored. We query by id rather
  // than ref-forwarding so we don't have to widen the design-system primitive.
  useEffect(() => {
    const el = document.getElementById(
      "login-email-input",
    ) as HTMLInputElement | null;
    el?.focus({ preventScroll: true });
  }, []);

  // Debounced discovery as the user types. We cancel the in-flight request
  // when the email changes so only the latest value resolves.
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
          const message =
            err instanceof Error ? err.message : "could not look up domain";
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
        // If discovery already ran and we have a result, use it. Otherwise
        // fire a synchronous discover so the submit isn't a no-op when the
        // user hits Enter before the debounce settles.
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
        // unknown / SSO-required / magic-link disabled — surface the
        // server's message verbatim if present, otherwise a generic
        // SSO-required string.
        setSubmitError(
          result.message ??
            "Your workspace requires single sign-on. Contact your admin.",
        );
      } catch (err) {
        const message = err instanceof Error ? err.message : "login failed";
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
        <h2>Sign in to Atlas</h2>
        <p>
          Enter your work email — we&rsquo;ll route you to the right sign-in.
        </p>
      </header>
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

function DiscoveryCard({ data }: { data: AuthDiscoverResponse }): ReactElement {
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
  // unknown
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

function RedirectStep({
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
    // OIDC start is the first ramp; deploys with SAML can branch here on
    // the provider kind. v1 sends both through the OIDC path because the
    // existing facade route is the same for both.
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

function MagicLinkSent({
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

// ---------------------------------------------------------------------------
// Magic-link callback step (consumes ?token= on mount)
// ---------------------------------------------------------------------------

function MagicLinkCallbackStep({
  token,
  onError,
}: {
  token: string;
  onError(): void;
}): ReactElement {
  const auth = useAuth();
  const [error, setError] = useState<string | null>(null);
  const consumedRef = useRef(false);

  useEffect(() => {
    if (consumedRef.current) return;
    consumedRef.current = true;
    void (async () => {
      try {
        await auth.consumeMagicLink(token);
        // On session_minted, AuthContext flips to authenticated → AuthGate
        // remounts the app shell. On workspace_pick_required, AuthContext
        // flips to workspace_pick → the parent re-anchors. Nothing else
        // for us to do here.
        if (typeof window !== "undefined") {
          // Strip ?token= from the URL so a back-button doesn't replay.
          const url = new URL(window.location.href);
          url.searchParams.delete("token");
          window.history.replaceState({}, "", url.pathname);
        }
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "could not consume link";
        setError(message);
      }
    })();
  }, [auth, token]);

  return (
    <Card className="login-card login-card--magic-cb" tone="default">
      <header className="login-card__head">
        <h2>Signing you in…</h2>
        {error === null ? (
          <p>Hang tight — verifying your sign-in link.</p>
        ) : (
          <>
            <p role="alert" className="login-card__error">
              {error}
            </p>
            <Button
              type="button"
              variant="primary"
              size="md"
              onClick={onError}
              data-testid="login-magic-cb-back"
            >
              Try again
            </Button>
          </>
        )}
      </header>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Workspace picker (post-magic-link, multi-workspace)
// ---------------------------------------------------------------------------

function WorkspacePickStep(): ReactElement {
  const auth = useAuth();
  const [submittingOrg, setSubmittingOrg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pick = auth.workspacePick;

  if (pick === null) {
    return (
      <Card className="login-card" tone="muted">
        <p>Workspace pick state expired. Please request a new link.</p>
      </Card>
    );
  }

  const onSelect = async (org_id: string): Promise<void> => {
    if (submittingOrg !== null) return;
    setSubmittingOrg(org_id);
    setError(null);
    try {
      await auth.selectWorkspaceFromPick(org_id);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "could not select workspace";
      setError(message);
    } finally {
      setSubmittingOrg(null);
    }
  };

  return (
    <Card className="login-card login-card--pick" tone="default">
      <header className="login-card__head">
        <h2>Pick a workspace</h2>
        <p>
          Choose where you want to land. We&rsquo;ll remember your last one.
        </p>
      </header>
      <ul
        className="login-pick__list"
        aria-label="Your workspaces"
        data-testid="login-pick-list"
      >
        {pick.workspaces.map((ws) => (
          <WorkspaceRow
            key={ws.org_id}
            workspace={ws}
            disabled={submittingOrg !== null}
            submitting={submittingOrg === ws.org_id}
            onSelect={() => onSelect(ws.org_id)}
          />
        ))}
      </ul>
      {error && (
        <p
          className="login-card__error"
          role="alert"
          data-testid="login-pick-error"
        >
          {error}
        </p>
      )}
    </Card>
  );
}

function WorkspaceRow({
  workspace,
  disabled,
  submitting,
  onSelect,
}: {
  workspace: WorkspaceCandidate;
  disabled: boolean;
  submitting: boolean;
  onSelect(): void;
}): ReactElement {
  return (
    <li className="login-pick__row">
      <button
        type="button"
        className="login-pick__btn"
        onClick={onSelect}
        disabled={disabled}
        data-testid={`login-pick-${workspace.org_id}`}
        data-org-id={workspace.org_id}
      >
        <span className="login-pick__avatar" aria-hidden="true">
          {workspace.display_name.charAt(0).toUpperCase()}
        </span>
        <span className="login-pick__col">
          <span className="login-pick__name">{workspace.display_name}</span>
          <span className="login-pick__sub">
            {workspace.role} · {workspace.member_count.toLocaleString()} member
            {workspace.member_count === 1 ? "" : "s"}
            {workspace.last_active_at !== null && (
              <> · last active {_formatLastActive(workspace.last_active_at)}</>
            )}
          </span>
        </span>
        <span className="login-pick__chev" aria-hidden="true">
          {submitting ? "…" : "›"}
        </span>
      </button>
    </li>
  );
}

function _formatLastActive(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return "moments ago";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  const days = Math.floor(seconds / 86_400);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}
