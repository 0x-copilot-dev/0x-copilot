/**
 * Login screen — v2 "0xCopilot Login" (wallet-first, quiet aesthetic).
 *
 * A single centered card with five view states, wallet-first:
 *
 *   pick        heading + three options:
 *                 · Continue with a wallet   (PRIMARY, accent-filled)
 *                 · Continue with Google     (gated on /v1/auth/providers)
 *                 · Use locally, no account  (dev/desktop persona sign-in)
 *   wallets     EIP-6963-discovered wallet list (or an honest empty state)
 *   connecting  waiting for the extension to approve the connection
 *   sign        signature-request review (address chip + message preview)
 *   done        signed-in confirmation, workspace opening
 *
 * All three options wire to the REAL auth machinery — nothing is faked:
 *   - Wallet → the same SIWE (EIP-4361) flow the standalone
 *     ``WalletSignIn`` uses: EIP-6963 discovery (``discoverWalletProviders``),
 *     ``requestSiweNonce`` / ``verifySiwe`` over ``/v1/auth/siwe/*``, the
 *     frozen ``buildSiweMessage`` template, then ``auth.adoptSession`` — the
 *     shared "bearer in hand, refresh the session" tail. The only reason
 *     this doesn't render ``<WalletSignIn>`` verbatim is the v2 design's
 *     explicit signature-review step, which that component (auto-sign)
 *     structurally can't produce; the underlying primitives are reused.
 *   - Google → ``buildGoogleStartUrl`` → ``/v1/auth/oidc/google/start``,
 *     the same entry point the retained email path used. Renders only when
 *     the unscoped ``/v1/auth/providers`` list advertises ``google``.
 *   - Use locally → mint a dev-persona bearer (``mintDevBearer`` for the
 *     active persona slug) and hand it to ``auth.adoptSession`` — the exact
 *     dev-IdP path ``AuthContext`` uses on a 401, made an explicit choice.
 *
 * Email is DROPPED from the rendered UI per the v2 design, but the whole
 * email/magic-link flow is retained in ``emailLogin.tsx`` (dead-but-present,
 * trivial to re-plug). ``MagicLinkCallbackStep`` and ``WorkspacePickStep``
 * stay here because those completion paths are still reachable (an
 * already-sent magic-link URL, or a multi-workspace return) even though no
 * new links can be requested from the UI.
 */

import { BrandMark, useKeyValueStore } from "@0x-copilot/chat-surface";
import type { WorkspaceCandidate } from "@0x-copilot/api-types";
import type { ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { listAuthProviders } from "../../api/authApi";
import { mintDevBearer } from "../../api/devIdpApi";
import { requestSiweNonce, verifySiwe } from "../../api/siweApi";
import { errorMessage } from "../../utils/errors";
import { toWireAddress } from "../../utils/eip55";
import { useAuth } from "./AuthContext";
import { loadActivePersonaSlug } from "./devIdp";
import {
  buildGoogleStartUrl,
  GoogleGLogo,
  GOOGLE_PROVIDER_ID,
} from "./emailLogin";
import {
  discoverWalletProviders,
  type Eip1193Provider,
  type WalletProviderCandidate,
} from "./eip6963";
import { buildSiweMessage, defaultExpirationTime } from "./siweMessage";
import { CHAIN_NOT_ALLOWED_MESSAGE } from "./WalletSignIn";

const MAGIC_LINK_CALLBACK_PATH = "/auth/magic-link/callback";

/** Frontend build version (mono footer line). */
const APP_VERSION = "0.1.0";

/** SIWE_ALLOWED_CHAIN_IDS (see backend siwe config) → display names. */
const CHAIN_NAMES: Record<number, string> = {
  1: "Ethereum",
  8453: "Base",
  42_161: "Arbitrum One",
  4663: "Robinhood Chain",
};

function chainName(chainId: number): string {
  return CHAIN_NAMES[chainId] ?? `Chain ${chainId}`;
}

type LoginStep =
  | { kind: "choose" }
  | { kind: "magic_link_cb"; token: string }
  | { kind: "workspace_pick" };

export interface LoginScreenProps {
  /** Default org slug — retained for backwards compat with the legacy URL
   * hint (``?org_id=acme``). Unused by the wallet-first flow. */
  defaultOrgId?: string;
  /** Hide the magic-link CTA entirely (bank deploys with strict SSO).
   * Retained for the re-pluggable email path; the v2 UI never renders it. */
  hideMagicLink?: boolean;
  /** Optional path to navigate to after a successful login (carried into
   * the Google start URL). */
  returnTo?: string;
}

export function LoginScreen(props: LoginScreenProps): ReactElement {
  const auth = useAuth();
  const [step, setStep] = useState<LoginStep>(() => _initialStep(auth));
  const [googleLogin, setGoogleLogin] = useState(false);

  // Re-anchor on workspace_pick when AuthContext flips into it
  // (consumeMagicLink → workspace_pick_required).
  useEffect(() => {
    if (auth.status === "workspace_pick" && step.kind !== "workspace_pick") {
      setStep({ kind: "workspace_pick" });
    }
  }, [auth.status, step.kind]);

  // One-shot public provider probe. Any failure degrades silently to
  // "no Google button" — wallet + use-locally are the always-on entries.
  useEffect(() => {
    let cancelled = false;
    listAuthProviders()
      .then((providers) => {
        if (cancelled) return;
        setGoogleLogin(
          providers.some(
            (p) => p.provider_id === GOOGLE_PROVIDER_ID && p.enabled,
          ),
        );
      })
      .catch(() => {
        /* degrade silently */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The login route needs normal page scroll (the chat shell scroll-locks
  // <body>); opt out for as long as the screen is mounted.
  useEffect(() => {
    const { documentElement, body } = document;
    documentElement.classList.add("login-html");
    body.classList.add("login-body");
    return () => {
      documentElement.classList.remove("login-html");
      body.classList.remove("login-body");
    };
  }, []);

  return (
    <div className="loginx-shell" data-testid="login-screen">
      <main className="loginx-pane">
        {step.kind === "choose" && (
          <SignInCard
            googleLogin={googleLogin}
            returnTo={props.returnTo ?? null}
          />
        )}
        {step.kind === "magic_link_cb" && (
          <MagicLinkCallbackStep
            token={step.token}
            onError={() => setStep({ kind: "choose" })}
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
  return { kind: "choose" };
}

// ---------------------------------------------------------------------------
// The wallet-first sign-in card — five view states in one component.
// ---------------------------------------------------------------------------

type WalletView =
  | { kind: "pick" }
  | { kind: "discovering" }
  | { kind: "wallets"; providers: WalletProviderCandidate[] }
  | { kind: "connecting"; walletName: string }
  | {
      kind: "sign";
      walletName: string;
      provider: Eip1193Provider;
      address: string;
      chainId: number;
      message: string;
    }
  | { kind: "verifying"; walletName: string }
  // Wallet failure recovery — design `werr` view. Lands here (a persistent
  // VIEW) instead of a self-clearing inline error, so the failure is actually
  // visible and offers a Try-again / Choose-another path.
  | { kind: "error"; walletName: string; message: string }
  // Google is a full-page redirect, so the waiting view flashes just before
  // navigation; the error view is only reachable if the callback returns to
  // this route with an error signal (see `_readGoogleError`).
  | { kind: "google_wait" }
  | { kind: "google_error"; message: string }
  | { kind: "done" };

interface SignInCardProps {
  googleLogin: boolean;
  returnTo: string | null;
}

function SignInCard({ googleLogin, returnTo }: SignInCardProps): ReactElement {
  const auth = useAuth();
  const kvStore = useKeyValueStore();
  // Seed the Google-error recovery view if the login route was reached with an
  // error signal in the URL (see `_readGoogleError` + the TODO on the facade
  // OIDC callback below).
  const [view, setView] = useState<WalletView>(() => {
    const gErr = _readGoogleError();
    return gErr === null
      ? { kind: "pick" }
      : { kind: "google_error", message: gErr };
  });
  const [error, setError] = useState<string | null>(null);
  const [localBusy, setLocalBusy] = useState(false);
  // The wallet last selected — so the error view's "Try again" can re-run the
  // whole connect→sign flow (design `werr` retries `pickWallet(wallet)`).
  const selectedWalletRef = useRef<WalletProviderCandidate | null>(null);

  const reset = useCallback(() => {
    setError(null);
    setView({ kind: "pick" });
  }, []);

  // --- Wallet: EIP-6963 discovery → picker -------------------------------
  const openWallets = useCallback(async (): Promise<void> => {
    setError(null);
    setView({ kind: "discovering" });
    const providers = await discoverWalletProviders();
    // Always land on the list view — an empty list renders the honest
    // "no wallet detected" state rather than an error toast.
    setView({ kind: "wallets", providers });
  }, []);

  // --- Wallet: connect + nonce + build message → signature review --------
  const selectWallet = useCallback(
    async (candidate: WalletProviderCandidate): Promise<void> => {
      const walletName = candidate.info.name;
      selectedWalletRef.current = candidate;
      setError(null);
      setView({ kind: "connecting", walletName });
      try {
        const { address, chainId } = await connectWallet(candidate.provider);
        const nonce = await requestSiweNonce({
          address: toWireAddress(address),
          chain_id: chainId,
        });
        const issuedAt = new Date().toISOString();
        const message = buildSiweMessage({
          domain: window.location.host,
          uri: window.location.origin,
          address,
          chainId,
          nonce: nonce.nonce,
          issuedAt,
          expirationTime: defaultExpirationTime(issuedAt),
        });
        setView({
          kind: "sign",
          walletName,
          provider: candidate.provider,
          address,
          chainId,
          message,
        });
      } catch (err) {
        _handleWalletError(err, walletName, setView, reset);
      }
    },
    [reset],
  );

  // Retry from the wallet-error view — re-run the connect flow for the wallet
  // that just failed (design `werr` → `pickWallet(wallet, true)`).
  const retryWallet = useCallback((): void => {
    const candidate = selectedWalletRef.current;
    if (candidate === null) {
      reset();
      return;
    }
    void selectWallet(candidate);
  }, [reset, selectWallet]);

  // --- Wallet: personal_sign + verify + session handoff ------------------
  const signAndContinue = useCallback(async (): Promise<void> => {
    if (view.kind !== "sign") return;
    const { provider, message, address, walletName } = view;
    setError(null);
    setView({ kind: "verifying", walletName });
    try {
      const signature = await personalSign(provider, message, address);
      const session = await verifySiwe({ message, signature });
      setView({ kind: "done" });
      // Same tail as magic-link / workspace-pick completion.
      await auth.adoptSession({
        bearer_token: session.bearer_token,
        session_id: session.session_id,
        user_id: session.user_id,
        requires_mfa: session.requires_mfa,
      });
    } catch (err) {
      _handleWalletError(err, walletName, setView, reset);
    }
  }, [auth, reset, view]);

  // --- Use locally: dev-persona bearer → adoptSession --------------------
  const signInLocally = useCallback(async (): Promise<void> => {
    if (localBusy) return;
    setLocalBusy(true);
    setError(null);
    try {
      const slug = loadActivePersonaSlug(kvStore);
      const mint = await mintDevBearer(slug);
      await auth.adoptSession({
        bearer_token: mint.bearer,
        // session_id is only read by adoptSession on the requires_mfa
        // branch (dev personas never require MFA), so an empty value is
        // inert here; user_id is the real minted identity.
        session_id: "",
        user_id: mint.identity.user_id,
        requires_mfa: false,
      });
    } catch (err) {
      setError(
        errorMessage(err, "Local sign-in is unavailable in this build."),
      );
    } finally {
      setLocalBusy(false);
    }
  }, [auth, kvStore, localBusy]);

  // --- Google: show the "authorizing" view, then redirect to OIDC start --
  const continueWithGoogle = useCallback((): void => {
    // Flash the design's "Authorizing with Google…" waiting view for the
    // instant before the browser navigates away.
    setView({ kind: "google_wait" });
    window.location.assign(buildGoogleStartUrl(returnTo));
  }, [returnTo]);

  return (
    <div className="loginx-card">
      <CopilotMark />

      {view.kind === "pick" && (
        <PickView
          googleLogin={googleLogin}
          localBusy={localBusy}
          error={error}
          onWallet={() => void openWallets()}
          onGoogle={continueWithGoogle}
          onLocal={() => void signInLocally()}
        />
      )}

      {view.kind === "discovering" && (
        <WalletsView
          providers={null}
          error={error}
          onBack={reset}
          onSelect={() => {}}
        />
      )}

      {view.kind === "wallets" && (
        <WalletsView
          providers={view.providers}
          error={error}
          onBack={reset}
          onSelect={(c) => void selectWallet(c)}
        />
      )}

      {view.kind === "connecting" && (
        <ConnectingView walletName={view.walletName} onCancel={reset} />
      )}

      {view.kind === "sign" && (
        <SignView
          walletName={view.walletName}
          address={view.address}
          chainId={view.chainId}
          message={view.message}
          error={error}
          onCancel={reset}
          onSign={() => void signAndContinue()}
        />
      )}

      {view.kind === "verifying" && (
        <ConnectingView
          walletName={view.walletName}
          verifying
          onCancel={reset}
        />
      )}

      {view.kind === "error" && (
        <WErrView
          walletName={view.walletName}
          message={view.message}
          onChooseAnother={() => void openWallets()}
          onRetry={retryWallet}
          onBack={reset}
        />
      )}

      {view.kind === "google_wait" && <GoogleWaitView onCancel={reset} />}

      {view.kind === "google_error" && (
        <GErrView
          message={view.message}
          onUseWallet={() => void openWallets()}
          onRetry={continueWithGoogle}
          onBack={reset}
        />
      )}

      {view.kind === "done" && <DoneView />}
    </div>
  );
}

/**
 * Read a Google sign-in error signalled back on the login route.
 *
 * TODO(auth): the facade OIDC callback (`GET /v1/auth/oidc/callback`,
 * services/backend-facade/.../auth_routes.py) currently *raises 400* on an
 * IdP error, so the browser lands on that facade error page — it never
 * returns to this SPA route. Until that callback is changed to redirect a
 * failed SIGN-IN back here with an error param (e.g.
 * `/login?login_error=google&error_description=…`, mirroring the LINK flow's
 * `/oauth/link/callback?link_status=…` landing), the `google_error` view is
 * only reachable via that param and stays dormant in production. The reader
 * below is intentionally forgiving so wiring the redirect later is a
 * backend-only change.
 */
function _readGoogleError(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const params = new URL(window.location.href).searchParams;
    const signal = params.get("login_error") ?? params.get("auth_error");
    if (signal !== "google") return null;
    return (
      params.get("error_description") ??
      params.get("error") ??
      "Google sign-in didn’t finish."
    );
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Pick view — the three sign-in options.
// ---------------------------------------------------------------------------

function PickView({
  googleLogin,
  localBusy,
  error,
  onWallet,
  onGoogle,
  onLocal,
}: {
  googleLogin: boolean;
  localBusy: boolean;
  error: string | null;
  onWallet(): void;
  onGoogle(): void;
  onLocal(): void;
}): ReactElement {
  return (
    <>
      <header className="loginx-head">
        <h1 className="loginx-title">
          Welcome to <span className="loginx-zx">0x</span>Copilot
        </h1>
        <p className="loginx-sub">
          Choose how to sign in — either way, it runs on your machine.
        </p>
      </header>

      <div className="loginx-options">
        <OptionButton
          variant="primary"
          testId="login-option-wallet"
          icon={<WalletGlyph />}
          label="Continue with a wallet"
          subtitle="MetaMask · Rabby · WalletConnect · Ledger"
          onClick={onWallet}
        />

        {googleLogin && (
          <OptionButton
            testId="login-google"
            icon={<GoogleGLogo className="loginx-opt__glyph" />}
            label="Continue with Google"
            subtitle="for encrypted settings sync"
            onClick={onGoogle}
          />
        )}

        <div className="loginx-divider ui-mono-caps" role="separator">
          <span>or</span>
        </div>

        <OptionButton
          testId="login-option-local"
          icon={<ChipGlyph />}
          label="Use locally, no account"
          subtitle="everything stays on this device"
          busy={localBusy}
          onClick={onLocal}
        />
      </div>

      {error !== null && (
        <p className="login-card__error" role="alert" data-testid="login-error">
          {error}
        </p>
      )}

      <footer className="loginx-foot">
        <p className="loginx-note">
          <strong>No seed phrase, ever.</strong> Wallet sign-in is a signed
          message — no transaction, no gas. You can link an account later in
          Settings.
        </p>
        <p className="loginx-version">
          v{APP_VERSION} · {import.meta.env.DEV ? "local build" : "main"}
        </p>
      </footer>
    </>
  );
}

function OptionButton({
  variant = "secondary",
  testId,
  icon,
  label,
  subtitle,
  busy = false,
  onClick,
}: {
  variant?: "primary" | "secondary";
  testId: string;
  icon: ReactElement;
  label: string;
  subtitle: string;
  busy?: boolean;
  onClick(): void;
}): ReactElement {
  return (
    <button
      type="button"
      className="loginx-opt"
      data-variant={variant}
      data-testid={testId}
      onClick={onClick}
      disabled={busy}
    >
      <span className="loginx-opt__icon" aria-hidden="true">
        {icon}
      </span>
      <span className="loginx-opt__body">
        <span className="loginx-opt__label">{label}</span>
        <small className="loginx-opt__sub">
          {busy ? "One moment…" : subtitle}
        </small>
      </span>
      <span className="loginx-opt__chev" aria-hidden="true">
        <ChevronRight />
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Wallets view — EIP-6963 discovered list, or the honest empty state.
// ---------------------------------------------------------------------------

function WalletsView({
  providers,
  error,
  onBack,
  onSelect,
}: {
  /** null while discovery is still running. */
  providers: WalletProviderCandidate[] | null;
  error: string | null;
  onBack(): void;
  onSelect(candidate: WalletProviderCandidate): void;
}): ReactElement {
  return (
    <>
      <BackLink onClick={onBack} />
      <header className="loginx-head">
        <h1 className="loginx-title">Choose a wallet</h1>
        <p className="loginx-sub">
          We&rsquo;ll ask it to sign a one-line message. Nothing is broadcast
          on-chain.
        </p>
      </header>

      {providers === null ? (
        <p className="loginx-status" role="status">
          Looking for wallets…
        </p>
      ) : providers.length === 0 ? (
        <div className="loginx-empty" data-testid="wallet-empty">
          <p className="loginx-empty__title">No wallet detected</p>
          <p className="loginx-empty__hint">
            Install a browser wallet (MetaMask, Rabby, …), then reload this
            page. Or use one of the other sign-in options.
          </p>
        </div>
      ) : (
        <ul className="loginx-wallets" aria-label="Available wallets">
          {providers.map((candidate) => (
            <li key={candidate.info.uuid}>
              <button
                type="button"
                className="loginx-wallet-row"
                onClick={() => onSelect(candidate)}
                data-testid={`wallet-provider-${candidate.info.rdns}`}
              >
                {candidate.info.icon ? (
                  <img
                    className="loginx-wallet-row__icon"
                    src={candidate.info.icon}
                    alt=""
                    aria-hidden="true"
                  />
                ) : (
                  <span
                    className="loginx-wallet-row__icon loginx-wallet-row__icon--letter"
                    aria-hidden="true"
                  >
                    {candidate.info.name.charAt(0).toUpperCase()}
                  </span>
                )}
                <span className="loginx-wallet-row__body">
                  <span className="loginx-wallet-row__name">
                    {candidate.info.name}
                  </span>
                  <span className="loginx-wallet-row__sub">
                    {candidate.info.rdns}
                  </span>
                </span>
                <span className="loginx-opt__chev" aria-hidden="true">
                  <ChevronRight />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {error !== null && (
        <p
          className="login-card__error"
          role="alert"
          data-testid="wallet-error"
        >
          {error}
        </p>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Connecting / verifying view — a spinner while the extension is busy.
// ---------------------------------------------------------------------------

function ConnectingView({
  walletName,
  verifying = false,
  onCancel,
}: {
  walletName: string;
  verifying?: boolean;
  onCancel(): void;
}): ReactElement {
  return (
    <div className="loginx-wait" data-testid="wallet-connecting">
      <Spinner />
      <h3 className="loginx-title">
        {verifying ? "Verifying signature…" : `Waiting for ${walletName}…`}
      </h3>
      <p className="loginx-sub">
        {verifying
          ? "Confirming your signature with the server."
          : "Approve the connection request in the extension."}
      </p>
      {!verifying && (
        <button
          type="button"
          className="cbtn cbtn--ghost cbtn--sm"
          onClick={onCancel}
        >
          Cancel
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sign view — signature-request review before personal_sign.
// ---------------------------------------------------------------------------

function SignView({
  walletName,
  address,
  chainId,
  message,
  error,
  onCancel,
  onSign,
}: {
  walletName: string;
  address: string;
  chainId: number;
  message: string;
  error: string | null;
  onCancel(): void;
  onSign(): void;
}): ReactElement {
  return (
    <>
      <header className="loginx-head">
        <h1 className="loginx-title">Signature request</h1>
        <p className="loginx-sub">
          Signing proves you own this address. It never leaves your machine.
        </p>
      </header>

      <div className="loginx-addr" data-testid="wallet-address">
        <span className="loginx-addr__dot" aria-hidden="true" />
        <span className="loginx-addr__hex">{shortenAddress(address)}</span>
        <span className="loginx-addr__meta">
          {walletName} · {chainName(chainId)}
        </span>
      </div>

      <pre className="loginx-message" data-testid="wallet-message">
        {message}
      </pre>

      {error !== null && (
        <p
          className="login-card__error"
          role="alert"
          data-testid="wallet-error"
        >
          {error}
        </p>
      )}

      <div className="loginx-actions">
        <button type="button" className="cbtn" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="cbtn cbtn--pri"
          onClick={onSign}
          data-testid="wallet-sign-submit"
        >
          <CheckGlyph /> Sign &amp; continue
        </button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Done view — jade check, workspace opening.
// ---------------------------------------------------------------------------

function DoneView(): ReactElement {
  return (
    <div className="loginx-done" data-testid="wallet-done">
      <span className="loginx-done__check" aria-hidden="true">
        <CheckGlyph />
      </span>
      <h3 className="loginx-title">Signed in</h3>
      <p className="loginx-sub">Opening your workspace…</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Wallet-error recovery (design `werr`) + Google waiting/error (design
// `google`/`gerr`). Persistent VIEWS, not self-clearing inline errors.
// ---------------------------------------------------------------------------

function WErrView({
  walletName,
  message,
  onChooseAnother,
  onRetry,
  onBack,
}: {
  walletName: string;
  message: string;
  onChooseAnother(): void;
  onRetry(): void;
  onBack(): void;
}): ReactElement {
  return (
    <div className="loginx-wait" data-testid="wallet-error" role="alert">
      <span className="loginx-wait__warn" aria-hidden="true">
        <WarnGlyph />
      </span>
      <h3 className="loginx-title">No response from {walletName}</h3>
      <p className="loginx-sub">{message} Nothing was signed.</p>
      <div className="loginx-actions">
        <button
          type="button"
          className="cbtn"
          onClick={onChooseAnother}
          data-testid="wallet-error-choose"
        >
          Choose another wallet
        </button>
        <button
          type="button"
          className="cbtn cbtn--pri"
          onClick={onRetry}
          data-testid="wallet-error-retry"
        >
          Try again
        </button>
      </div>
      <button
        type="button"
        className="loginx-back"
        onClick={onBack}
        data-testid="wallet-error-back"
      >
        <span aria-hidden="true">‹</span> Back to sign-in
      </button>
    </div>
  );
}

function GoogleWaitView({ onCancel }: { onCancel(): void }): ReactElement {
  return (
    <div className="loginx-wait" data-testid="google-wait">
      <Spinner />
      <h3 className="loginx-title">Authorizing with Google…</h3>
      <p className="loginx-sub">
        Finish signing in from the browser window that just opened.
      </p>
      <button
        type="button"
        className="loginx-back"
        onClick={onCancel}
        data-testid="google-wait-cancel"
      >
        Cancel — use a different method
      </button>
    </div>
  );
}

function GErrView({
  message,
  onUseWallet,
  onRetry,
  onBack,
}: {
  message: string;
  onUseWallet(): void;
  onRetry(): void;
  onBack(): void;
}): ReactElement {
  return (
    <div className="loginx-wait" data-testid="google-error" role="alert">
      <span className="loginx-wait__warn" aria-hidden="true">
        <WarnGlyph />
      </span>
      <h3 className="loginx-title">Google didn&rsquo;t finish</h3>
      <p className="loginx-sub">{message} No account was linked.</p>
      <div className="loginx-actions">
        <button
          type="button"
          className="cbtn"
          onClick={onUseWallet}
          data-testid="google-error-wallet"
        >
          Use a wallet instead
        </button>
        <button
          type="button"
          className="cbtn cbtn--pri"
          onClick={onRetry}
          data-testid="google-error-retry"
        >
          Try again
        </button>
      </div>
      <button
        type="button"
        className="loginx-back"
        onClick={onBack}
        data-testid="google-error-back"
      >
        <span aria-hidden="true">‹</span> Back to sign-in
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// EIP-1193 plumbing (standard wallet glue — the frozen SIWE contract lives
// in siweMessage.ts / siweApi.ts, not here). Mirrors WalletSignIn's private
// helpers so this card can drive the flow with an explicit review step.
// ---------------------------------------------------------------------------

async function connectWallet(
  provider: Eip1193Provider,
): Promise<{ address: string; chainId: number }> {
  const accounts = await provider.request({ method: "eth_requestAccounts" });
  if (
    !Array.isArray(accounts) ||
    accounts.length === 0 ||
    typeof accounts[0] !== "string"
  ) {
    throw new Error("wallet returned no accounts");
  }
  const address = accounts[0];

  const chainHex = await provider.request({ method: "eth_chainId" });
  const chainId =
    typeof chainHex === "string" ? Number.parseInt(chainHex, 16) : Number.NaN;
  if (!Number.isInteger(chainId) || chainId <= 0) {
    throw new Error("wallet returned an invalid chain id");
  }
  return { address, chainId };
}

async function personalSign(
  provider: Eip1193Provider,
  message: string,
  address: string,
): Promise<string> {
  const signature = await provider.request({
    method: "personal_sign",
    params: [hexEncodeUtf8(message), address],
  });
  if (typeof signature !== "string" || !signature.startsWith("0x")) {
    throw new Error("wallet returned an invalid signature");
  }
  return signature;
}

function hexEncodeUtf8(text: string): string {
  let hex = "0x";
  for (const byte of new TextEncoder().encode(text)) {
    hex += byte.toString(16).padStart(2, "0");
  }
  return hex;
}

/** EIP-1193 ProviderRpcError code 4001 — "User Rejected Request". */
function isUserRejection(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code: unknown }).code === 4001
  );
}

function _handleWalletError(
  err: unknown,
  walletName: string,
  setView: (view: WalletView) => void,
  reset: () => void,
): void {
  if (isUserRejection(err)) {
    // Deliberate cancel — back to the picker, quietly.
    reset();
    return;
  }
  const detail = errorMessage(err, "wallet sign-in failed");
  const message =
    detail === "chain_not_allowed" ? CHAIN_NOT_ALLOWED_MESSAGE : detail;
  // Land on the persistent error VIEW (design `werr`) — NOT a self-clearing
  // inline error. This is what makes a wallet failure actually visible.
  setView({ kind: "error", walletName, message });
}

function shortenAddress(address: string): string {
  if (address.length <= 12) return address;
  return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

// ---------------------------------------------------------------------------
// Magic-link callback step (RETAINED — consumes ?token= on mount).
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
  const [consumed, setConsumed] = useState(false);

  useEffect(() => {
    if (consumed) return;
    setConsumed(true);
    void (async () => {
      try {
        await auth.consumeMagicLink(token);
      } catch (err) {
        setError(errorMessage(err, "could not consume link"));
      }
    })();
  }, [auth, token, consumed]);

  return (
    <div className="loginx-card">
      <CopilotMark />
      <header className="loginx-head">
        <h1 className="loginx-title">Signing you in…</h1>
        {error === null ? (
          <p className="loginx-sub">
            Hang tight — verifying your sign-in link.
          </p>
        ) : (
          <>
            <p role="alert" className="login-card__error">
              {error}
            </p>
            <button
              type="button"
              className="cbtn cbtn--pri"
              onClick={onError}
              data-testid="login-magic-cb-back"
            >
              Try again
            </button>
          </>
        )}
      </header>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Workspace picker (RETAINED — post-magic-link, multi-workspace).
// ---------------------------------------------------------------------------

function WorkspacePickStep(): ReactElement {
  const auth = useAuth();
  const [submittingOrg, setSubmittingOrg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pick = auth.workspacePick;

  if (pick === null) {
    return (
      <div className="loginx-card">
        <CopilotMark />
        <p className="loginx-sub">
          Workspace pick state expired. Please request a new link.
        </p>
      </div>
    );
  }

  const onSelect = async (org_id: string): Promise<void> => {
    if (submittingOrg !== null) return;
    setSubmittingOrg(org_id);
    setError(null);
    try {
      await auth.selectWorkspaceFromPick(org_id);
    } catch (err) {
      setError(errorMessage(err, "could not select workspace"));
    } finally {
      setSubmittingOrg(null);
    }
  };

  return (
    <div className="loginx-card">
      <CopilotMark />
      <header className="loginx-head">
        <h1 className="loginx-title">Pick a workspace</h1>
        <p className="loginx-sub">
          Choose where you want to land. We&rsquo;ll remember your last one.
        </p>
      </header>
      <ul
        className="loginx-wallets"
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
    </div>
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
    <li>
      <button
        type="button"
        className="loginx-wallet-row"
        onClick={onSelect}
        disabled={disabled}
        data-testid={`login-pick-${workspace.org_id}`}
        data-org-id={workspace.org_id}
      >
        <span
          className="loginx-wallet-row__icon loginx-wallet-row__icon--letter"
          aria-hidden="true"
        >
          {workspace.display_name.charAt(0).toUpperCase()}
        </span>
        <span className="loginx-wallet-row__body">
          <span className="loginx-wallet-row__name">
            {workspace.display_name}
          </span>
          <span className="loginx-wallet-row__sub">
            {workspace.role} · {workspace.member_count.toLocaleString()} member
            {workspace.member_count === 1 ? "" : "s"}
            {workspace.last_active_at !== null && (
              <> · last active {_formatLastActive(workspace.last_active_at)}</>
            )}
          </span>
        </span>
        <span className="loginx-opt__chev" aria-hidden="true">
          {submitting ? "…" : <ChevronRight />}
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

// ---------------------------------------------------------------------------
// Small presentational bits.
// ---------------------------------------------------------------------------

function BackLink({ onClick }: { onClick(): void }): ReactElement {
  return (
    <button
      type="button"
      className="loginx-back"
      onClick={onClick}
      data-testid="wallet-back"
    >
      <span aria-hidden="true">‹</span> Back
    </button>
  );
}

/** Hexagonal 0xCopilot copilot mark, sky accent. */
function CopilotMark(): ReactElement {
  // The canonical 0xCopilot turbine — the SAME `BrandMark` the desktop shell and
  // sign-in render (source of truth: 0xCopilot-kit/brand/favicon.svg). One brand
  // mark everywhere, never a per-surface hand-rolled glyph.
  return (
    <div className="loginx-mark" aria-hidden="true">
      <BrandMark size={44} />
    </div>
  );
}

function ChevronRight(): ReactElement {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
    >
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

function WalletGlyph(): ReactElement {
  return (
    <svg
      className="loginx-opt__glyph"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      focusable="false"
    >
      <rect x="3" y="6" width="18" height="13" rx="2.5" />
      <path d="M16 3.8H6.2A3.2 3.2 0 0 0 3 7v1" />
      <circle cx="16.6" cy="12.6" r="1.15" fill="currentColor" stroke="none" />
    </svg>
  );
}

function ChipGlyph(): ReactElement {
  return (
    <svg
      className="loginx-opt__glyph"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      focusable="false"
    >
      <rect x="6" y="6" width="12" height="12" rx="2" />
      <path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" />
    </svg>
  );
}

function CheckGlyph(): ReactElement {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
    >
      <path d="m5 12.5 4.5 4.5L19 7" />
    </svg>
  );
}

/** Triangle warning glyph (ember) for the wallet/Google error screens. */
function WarnGlyph(): ReactElement {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
    >
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </svg>
  );
}

function Spinner(): ReactElement {
  return <span className="loginx-spinner" aria-hidden="true" />;
}
