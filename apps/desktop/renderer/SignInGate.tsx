import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

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
  readonly children: (session: RendererSession) => ReactNode;
}

/** Which option the user picked — drives the "waiting" copy. */
type SignInMethod = "wallet" | "google" | "local";

type Phase =
  | { kind: "loading" }
  | { kind: "anon" }
  | { kind: "signing-in"; method: SignInMethod }
  | { kind: "signed-in"; session: RendererSession }
  | { kind: "error"; message: string };

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
          message: err instanceof Error ? err.message : "auth lookup failed",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [bridge, workspaceId]);

  // Every option drives the SAME IPC round-trip shape — main opens the
  // external flow (system browser / loopback) and returns a renderer-safe
  // session (the bearer never crosses IPC). Only the "waiting" copy and the
  // failure fallback differ per method.
  const startSignIn = useCallback(
    (
      method: SignInMethod,
      channel: (typeof CHANNELS)[keyof typeof CHANNELS],
      failMessage: string,
    ) => {
      setPhase({ kind: "signing-in", method });
      bridge.ipc
        .invoke<RendererSession>(channel, { workspaceId })
        .then((session) => {
          setPhase({ kind: "signed-in", session });
        })
        .catch((err: unknown) => {
          setPhase({
            kind: "error",
            message: err instanceof Error ? err.message : failMessage,
          });
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

  const retry = useCallback(() => {
    setPhase({ kind: "anon" });
  }, []);

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
            <WaitView {...WAIT_COPY[phase.method]} />
          </SignInChrome>
        );
      case "error":
        return (
          <SignInChrome>
            <ErrorView message={phase.message} onRetry={retry} />
          </SignInChrome>
        );
      case "signed-in":
        return children(phase.session);
    }
  }, [
    phase,
    signInWithWallet,
    signInWithGoogle,
    signInLocally,
    retry,
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
    title: "Opening your browser…",
    subtitle: "Finish signing in with Google in the browser window.",
  },
  local: {
    title: "Setting up your workspace…",
    subtitle: "Everything stays on this device.",
  },
};

function WaitView({
  title,
  subtitle,
}: {
  title: string;
  subtitle?: string;
}): ReactElement {
  return (
    <div className="loginx-wait" data-testid="sign-in-waiting">
      <Spinner />
      <h1 className="loginx-title">{title}</h1>
      {subtitle !== undefined && <p className="loginx-sub">{subtitle}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Error view — honest failure copy + a retry back to the pick screen.
// ---------------------------------------------------------------------------

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
  return (
    <div className="loginx-mark" aria-hidden="true">
      <svg viewBox="0 0 32 32" focusable="false">
        <path
          className="loginx-mark__hex"
          d="M16 2.5 27.7 9.25v13.5L16 29.5 4.3 22.75V9.25z"
        />
        <path
          className="loginx-mark__cursor"
          d="M12 10.5 22 16l-4.4 1.5L15.8 22z"
        />
      </svg>
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
