import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { BrandMark } from "@0x-copilot/chat-surface";
import {
  CHANNELS,
  type RendererSession,
  type WindowBridge,
} from "@0x-copilot/chat-transport";

import "./signin.css";

export const DEFAULT_WORKSPACE_ID = "org_acme";

/**
 * Desktop build version for the mono footer line. Mirrors
 * apps/desktop/package.json — the renderer has no channel that surfaces the
 * packaged app version, so a static "local build" channel is the honest label.
 */
const APP_VERSION = "0.1.0";

export interface SignInGateProps {
  readonly bridge: WindowBridge;
  readonly workspaceId?: string;
  /**
   * Render prop for the signed-in app. Receives the session and a `signOut`
   * that clears the PERSISTED session (via the authSignOut IPC) and returns the
   * gate to the sign-in screen — the gate owns the auth phase, so sign-out must
   * route through here, not just clear a view.
   */
  readonly children: (
    session: RendererSession,
    signOut: () => void,
  ) => ReactNode;
}

/** Which option the user picked — drives the waiting/failure copy. */
type SignInMethod = "wallet" | "google" | "local";

/**
 * Mirrors the "0xCopilot Login" design's view machine
 * (pick · connecting/google · werr/gerr · done), adapted to the desktop's
 * re-homed wallet architecture (the wallet picker + signature live in the
 * system-browser wallet page, so the app renders waiting/failure/done):
 *
 * - `signing-in.canceling` — the user hit Cancel; the pending IPC promise
 *   is about to reject (main closed the loopback) and that rejection must
 *   land back on `anon` quietly, NOT on the error screen.
 * - `done` — the design's post-sign-in beat ("Signed in / Opening your
 *   workspace…") shown briefly before the shell mounts. Only entered from
 *   an ACTIVE sign-in; a restored session skips it.
 * - `error.method` — which flow failed, so the failure screen can render
 *   the design's method-specific guidance (werr/gerr) instead of one
 *   generic message. `null` = session-lookup failure (no method picked).
 */
type Phase =
  | { kind: "loading" }
  | { kind: "anon" }
  | { kind: "signing-in"; method: SignInMethod; canceling: boolean }
  | { kind: "done"; session: RendererSession }
  | { kind: "signed-in"; session: RendererSession }
  | { kind: "error"; method: SignInMethod | null; message: string };

/** Design's `done` beat duration before the workspace mounts. */
const DONE_BEAT_MS = 900;

export function SignInGate(props: SignInGateProps): ReactNode {
  const { bridge, children } = props;
  const workspaceId = props.workspaceId ?? DEFAULT_WORKSPACE_ID;
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    bridge.ipc
      .invoke<RendererSession | null>(CHANNELS.authGetSession, { workspaceId })
      .then((session) => {
        if (cancelled) return;
        if (session === null) {
          setPhase({ kind: "anon" });
        } else {
          setPhase({ kind: "signed-in", session });
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setPhase({
          kind: "error",
          method: null,
          message: err instanceof Error ? err.message : "auth lookup failed",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [bridge, workspaceId]);

  // Attempt fencing: each sign-in click gets an id; a settled promise from
  // a superseded attempt (canceled, replaced by another click) must not
  // clobber the current phase. The done-beat timer is fenced the same way.
  const attemptRef = useRef(0);
  const doneTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    return () => {
      attemptRef.current += 1;
      if (doneTimerRef.current !== null) clearTimeout(doneTimerRef.current);
    };
  }, []);

  // Every option drives the SAME IPC round-trip shape — main opens the
  // external flow (system browser / loopback) and returns a renderer-safe
  // session (the bearer never crosses IPC). Only the waiting/failure copy
  // differs per method. On success the design's `done` beat renders briefly
  // before the workspace mounts.
  const startSignIn = useCallback(
    (
      method: SignInMethod,
      channel: (typeof CHANNELS)[keyof typeof CHANNELS],
      failMessage: string,
    ) => {
      const attempt = ++attemptRef.current;
      setPhase({ kind: "signing-in", method, canceling: false });
      bridge.ipc
        .invoke<RendererSession>(channel, { workspaceId })
        .then((session) => {
          if (attempt !== attemptRef.current) return;
          setPhase({ kind: "done", session });
          doneTimerRef.current = setTimeout(() => {
            if (attempt !== attemptRef.current) return;
            setPhase({ kind: "signed-in", session });
          }, DONE_BEAT_MS);
        })
        .catch((err: unknown) => {
          if (attempt !== attemptRef.current) return;
          // Sanitize main's error before showing it. Electron wraps IPC
          // rejections as "Error invoking remote method 'X': <Name>: <msg>";
          // strip that wrapper + the error-class name so the user never sees a
          // raw stack-shaped string (e.g. the leaked
          // "GoogleLoginError: oidc redirect error: access_denied").
          const raw = err instanceof Error ? err.message : "";
          const clean = raw
            .replace(/^Error invoking remote method '[^']*':\s*/i, "")
            .replace(/^[A-Za-z]+Error:\s*/, "")
            .trim();
          // A canceled flow rejects by design — whether the user hit the app's
          // Cancel (prev.canceling) OR declined/closed the provider's own
          // consent page (access_denied). Both land back on the pick screen
          // quietly, not on the error screen.
          const providerCanceled = /access_denied|cancell?ed/i.test(clean);
          setPhase((prev) =>
            (prev.kind === "signing-in" && prev.canceling) || providerCanceled
              ? { kind: "anon" }
              : {
                  kind: "error",
                  method,
                  message: clean || failMessage,
                },
          );
        });
    },
    [bridge, workspaceId],
  );

  const signInWithWallet = useCallback(() => {
    startSignIn("wallet", CHANNELS.authSignInWallet, "Wallet sign-in failed");
  }, [startSignIn]);

  const signInWithGoogle = useCallback(() => {
    startSignIn("google", CHANNELS.authSignInGoogle, "Google sign-in failed");
  }, [startSignIn]);

  const signInLocally = useCallback(() => {
    startSignIn("local", CHANNELS.authSignIn, "sign-in failed");
  }, [startSignIn]);

  // The design's Cancel affordances (wallet-waiting "Cancel", Google
  // "Cancel — use a different method"). Flag the phase first so the pending
  // promise's rejection is treated as a quiet return, then ask main to
  // close the loopback. Even if the IPC call itself failed, the flag makes
  // the eventual timeout rejection land on `anon` instead of the error view.
  const cancelSignIn = useCallback(() => {
    setPhase((prev) =>
      prev.kind === "signing-in" ? { ...prev, canceling: true } : prev,
    );
    bridge.ipc.invoke<void>(CHANNELS.authCancelSignIn, {}).catch(() => {
      /* the canceling flag already routes the rejection to `anon` */
    });
  }, [bridge]);

  const backToPick = useCallback(() => {
    setPhase({ kind: "anon" });
  }, []);

  // Sign out for real: clear the PERSISTED session in main (authSignOut deletes
  // the stored bearer — auth/index.ts signOut()), then drop back to the pick
  // screen. Without this the "Sign out" button only cleared the renderer view
  // and the app booted straight back in (the stored session was never deleted).
  const signOut = useCallback(() => {
    setPhase({ kind: "loading" });
    bridge.ipc
      .invoke<void>(CHANNELS.authSignOut, { workspaceId })
      .then(() => {
        setPhase({ kind: "anon" });
      })
      .catch((err: unknown) => {
        setPhase({
          kind: "error",
          method: null,
          message: err instanceof Error ? err.message : "sign-out failed",
        });
      });
  }, [bridge, workspaceId]);

  const content = useMemo(() => {
    switch (phase.kind) {
      case "loading":
        return (
          <SignInChrome>
            <WaitView title="Checking your session…" />
          </SignInChrome>
        );
      case "anon":
        return (
          <SignInChrome>
            <PickView
              onWallet={signInWithWallet}
              onGoogle={signInWithGoogle}
              onLocal={signInLocally}
            />
          </SignInChrome>
        );
      case "signing-in":
        return (
          <SignInChrome>
            <WaitView
              {...WAIT_COPY[phase.method]}
              cancel={
                phase.method === "local"
                  ? undefined
                  : {
                      style: phase.method === "google" ? "backlink" : "button",
                      label:
                        phase.method === "google"
                          ? "Cancel — use a different method"
                          : "Cancel",
                      disabled: phase.canceling,
                      onCancel: cancelSignIn,
                    }
              }
            />
          </SignInChrome>
        );
      case "error":
        return (
          <SignInChrome>
            {phase.method === "wallet" ? (
              <WalletErrorView
                message={phase.message}
                onRetry={signInWithWallet}
                onBack={backToPick}
              />
            ) : phase.method === "google" ? (
              <GoogleErrorView
                message={phase.message}
                onRetry={signInWithGoogle}
                onWalletInstead={signInWithWallet}
                onBack={backToPick}
              />
            ) : (
              <ErrorView message={phase.message} onRetry={backToPick} />
            )}
          </SignInChrome>
        );
      case "done":
        return (
          <SignInChrome>
            <DoneView />
          </SignInChrome>
        );
      case "signed-in":
        return children(phase.session, signOut);
    }
  }, [
    phase,
    signInWithWallet,
    signInWithGoogle,
    signInLocally,
    cancelSignIn,
    backToPick,
    signOut,
    children,
  ]);

  return content;
}

// ---------------------------------------------------------------------------
// Pick view — the three sign-in options, wallet-first.
// ---------------------------------------------------------------------------

function PickView({
  onWallet,
  onGoogle,
  onLocal,
}: {
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
          testId="sign-in-wallet-button"
          icon={<WalletGlyph />}
          label="Continue with a wallet"
          subtitle="MetaMask · Rabby · WalletConnect · Ledger"
          onClick={onWallet}
        />

        <OptionButton
          testId="sign-in-google-button"
          icon={<GoogleGlyph />}
          label="Continue with Google"
          subtitle="for encrypted settings sync"
          onClick={onGoogle}
        />

        <div className="loginx-divider" role="separator">
          <span>or</span>
        </div>

        <OptionButton
          testId="sign-in-button"
          icon={<ChipGlyph />}
          label="Use locally, no account"
          subtitle="everything stays on this device"
          onClick={onLocal}
        />
      </div>

      <footer className="loginx-foot">
        <p className="loginx-note">
          <strong>No seed phrase, ever.</strong> Wallet sign-in is a signed
          message — no transaction, no gas. You can link an account later in
          Settings.
        </p>
        <p className="loginx-version">v{APP_VERSION} · local build</p>
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
  onClick,
}: {
  variant?: "primary" | "secondary";
  testId: string;
  icon: ReactElement;
  label: string;
  subtitle: string;
  onClick(): void;
}): ReactElement {
  return (
    <button
      type="button"
      className="loginx-opt"
      data-variant={variant}
      data-testid={testId}
      onClick={onClick}
    >
      <span className="loginx-opt__icon" aria-hidden="true">
        {icon}
      </span>
      <span className="loginx-opt__body">
        <span className="loginx-opt__label">{label}</span>
        <span className="loginx-opt__sub">{subtitle}</span>
      </span>
      <span className="loginx-opt__chev" aria-hidden="true">
        <ChevronRight />
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Waiting view — spinner + honest per-method copy while main drives the
// external (system-browser / loopback) round-trip. The desktop wallet *sign*
// step happens out-of-process, so there is no inline signature review here.
// ---------------------------------------------------------------------------

const WAIT_COPY: Record<SignInMethod, { title: string; subtitle: string }> = {
  wallet: {
    title: "Waiting for your wallet…",
    subtitle:
      "Approve the signature request in your wallet, then come back here.",
  },
  google: {
    // Design `google` view copy — the browser window is already opening
    // when this renders (main fires /start + openExternal immediately).
    title: "Authorizing with Google…",
    subtitle: "Finish signing in from the browser window that just opened.",
  },
  local: {
    title: "Setting up your workspace…",
    subtitle: "Everything stays on this device.",
  },
};

/** Cancel affordance for a waiting view — the design's connecting-state
 * ghost "Cancel" (wallet) and the Google backlink variant. */
interface WaitCancel {
  readonly style: "button" | "backlink";
  readonly label: string;
  readonly disabled: boolean;
  onCancel(): void;
}

function WaitView({
  title,
  subtitle,
  cancel,
}: {
  title: string;
  subtitle?: string;
  cancel?: WaitCancel;
}): ReactElement {
  return (
    <div className="loginx-wait" data-testid="sign-in-waiting">
      <Spinner />
      <h1 className="loginx-title">{title}</h1>
      {subtitle !== undefined && <p className="loginx-sub">{subtitle}</p>}
      {cancel !== undefined && (
        <button
          type="button"
          className={
            cancel.style === "backlink"
              ? "loginx-backlink"
              : "loginx-btn loginx-btn--ghost loginx-btn--sm"
          }
          onClick={cancel.onCancel}
          disabled={cancel.disabled}
          data-testid="sign-in-cancel-button"
        >
          {cancel.label}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Failure views — the design's method-specific werr/gerr states, plus the
// generic fallback for session-lookup/local failures. The raw error detail
// stays visible (mono line) — honest detail under design copy.
// ---------------------------------------------------------------------------

function WalletErrorView({
  message,
  onRetry,
  onBack,
}: {
  message: string;
  onRetry(): void;
  onBack(): void;
}): ReactElement {
  return (
    <div className="loginx-error">
      <span className="loginx-error__badge" aria-hidden="true">
        <AlertGlyph />
      </span>
      <header className="loginx-head">
        <h1 className="loginx-title">No response from your wallet</h1>
        <p className="loginx-sub">
          The request was dismissed or timed out before your wallet approved it.
          Nothing was signed.
        </p>
      </header>
      <p
        className="loginx-error__message"
        role="alert"
        data-testid="sign-in-error"
      >
        {message}
      </p>
      <div className="loginx-row">
        <button
          type="button"
          className="loginx-btn"
          onClick={onRetry}
          data-testid="sign-in-retry-button"
        >
          Try again
        </button>
      </div>
      <button
        type="button"
        className="loginx-backlink"
        onClick={onBack}
        data-testid="sign-in-back-button"
      >
        Back to sign-in
      </button>
    </div>
  );
}

function GoogleErrorView({
  message,
  onRetry,
  onWalletInstead,
  onBack,
}: {
  message: string;
  onRetry(): void;
  onWalletInstead(): void;
  onBack(): void;
}): ReactElement {
  return (
    <div className="loginx-error">
      <span className="loginx-error__badge" aria-hidden="true">
        <AlertGlyph />
      </span>
      <header className="loginx-head">
        <h1 className="loginx-title">Google didn&rsquo;t finish</h1>
        <p className="loginx-sub">
          The browser window closed or timed out before confirming. No account
          was linked.
        </p>
      </header>
      <p
        className="loginx-error__message"
        role="alert"
        data-testid="sign-in-error"
      >
        {message}
      </p>
      <div className="loginx-row">
        <button
          type="button"
          className="loginx-btn loginx-btn--ghost"
          onClick={onWalletInstead}
          data-testid="sign-in-wallet-fallback-button"
        >
          Use a wallet instead
        </button>
        <button
          type="button"
          className="loginx-btn"
          onClick={onRetry}
          data-testid="sign-in-retry-button"
        >
          Try again
        </button>
      </div>
      <button
        type="button"
        className="loginx-backlink"
        onClick={onBack}
        data-testid="sign-in-back-button"
      >
        Back to sign-in
      </button>
    </div>
  );
}

function ErrorView({
  message,
  onRetry,
}: {
  message: string;
  onRetry(): void;
}): ReactElement {
  return (
    <div className="loginx-error">
      <span className="loginx-error__badge" aria-hidden="true">
        <AlertGlyph />
      </span>
      <header className="loginx-head">
        <h1 className="loginx-title">Sign-in didn&rsquo;t go through</h1>
        <p className="loginx-sub">
          Nothing was signed and no account was created. You can try again.
        </p>
      </header>
      <p
        className="loginx-error__message"
        role="alert"
        data-testid="sign-in-error"
      >
        {message}
      </p>
      <button
        type="button"
        className="loginx-btn"
        onClick={onRetry}
        data-testid="sign-in-retry-button"
      >
        Try again
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Done beat — the design's post-sign-in confirmation shown briefly before
// the workspace mounts (jade check, then auto-advance).
// ---------------------------------------------------------------------------

function DoneView(): ReactElement {
  return (
    <div className="loginx-wait" data-testid="sign-in-done">
      <span className="loginx-done__badge" aria-hidden="true">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
          focusable="false"
        >
          <path d="M5 12l5 5L20 7" />
        </svg>
      </span>
      <h1 className="loginx-title">Signed in</h1>
      <p className="loginx-sub">Opening your workspace…</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card chrome + the 0xCopilot mark.
// ---------------------------------------------------------------------------

function SignInChrome({
  children,
}: {
  readonly children: ReactNode;
}): ReactElement {
  return (
    <div className="loginx-shell" data-testid="sign-in-gate">
      <main className="loginx-pane">
        <section className="loginx-card">
          <CopilotMark />
          {children}
        </section>
      </main>
    </div>
  );
}

/** Hexagonal 0xCopilot copilot mark, sky accent on near-black. */
function CopilotMark(): ReactElement {
  // The canonical 0xCopilot turbine — the SAME `BrandMark` the shell rail
  // renders (source of truth: 0xCopilot-kit/brand/favicon.svg). One brand mark
  // everywhere, not a hand-rolled per-surface copy.
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

/** Google "G" — brand marks stay literal (not theme tokens) on purpose. */
function GoogleGlyph(): ReactElement {
  return (
    <svg
      className="loginx-opt__glyph"
      viewBox="0 0 24 24"
      focusable="false"
      aria-hidden="true"
    >
      <path
        fill="#4285F4"
        d="M23 12.27c0-.79-.07-1.54-.2-2.27H12v4.51h6.16a5.27 5.27 0 0 1-2.28 3.46v2.88h3.68C21.7 18.98 23 15.92 23 12.27z"
      />
      <path
        fill="#34A853"
        d="M12 23c3.08 0 5.66-1.02 7.55-2.77l-3.68-2.88c-1.02.69-2.33 1.1-3.87 1.1-2.98 0-5.5-2.01-6.4-4.71H1.79v2.96A11 11 0 0 0 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.6 13.74a6.6 6.6 0 0 1 0-4.22V6.56H1.79a11 11 0 0 0 0 9.88l3.81-2.96z"
      />
      <path
        fill="#EA4335"
        d="M12 5.51c1.68 0 3.19.58 4.38 1.72l3.27-3.27C17.66 2.09 15.08 1 12 1A11 11 0 0 0 1.79 6.56l3.81 2.96C6.5 7.52 9.02 5.51 12 5.51z"
      />
    </svg>
  );
}

function AlertGlyph(): ReactElement {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
    >
      <path d="M12 8v5" />
      <path d="M12 16.5h.01" />
      <path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    </svg>
  );
}

function Spinner(): ReactElement {
  return <span className="loginx-spinner" aria-hidden="true" />;
}
