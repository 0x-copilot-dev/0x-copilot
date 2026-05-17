import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  CHANNELS,
  type RendererSession,
  type WindowBridge,
} from "@enterprise-search/chat-transport";

export const DEFAULT_WORKSPACE_ID = "org_acme";

export interface SignInGateProps {
  readonly bridge: WindowBridge;
  readonly workspaceId?: string;
  readonly children: (session: RendererSession) => ReactNode;
}

type Phase =
  | { kind: "loading" }
  | { kind: "anon" }
  | { kind: "signing-in" }
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

  const signIn = useCallback(() => {
    setPhase({ kind: "signing-in" });
    bridge.ipc
      .invoke<RendererSession>(CHANNELS.authSignIn, { workspaceId })
      .then((session) => {
        setPhase({ kind: "signed-in", session });
      })
      .catch((err: unknown) => {
        setPhase({
          kind: "error",
          message: err instanceof Error ? err.message : "sign-in failed",
        });
      });
  }, [bridge, workspaceId]);

  const retry = useCallback(() => {
    setPhase({ kind: "anon" });
  }, []);

  const content = useMemo(() => {
    switch (phase.kind) {
      case "loading":
        return <SignInChrome>Checking session…</SignInChrome>;
      case "anon":
        return (
          <SignInChrome>
            <p>Sign in to your workspace to use Atlas.</p>
            <button type="button" onClick={signIn} data-testid="sign-in-button">
              Sign in
            </button>
          </SignInChrome>
        );
      case "signing-in":
        return <SignInChrome>Opening browser…</SignInChrome>;
      case "error":
        return (
          <SignInChrome>
            <p data-testid="sign-in-error">Sign-in error: {phase.message}</p>
            <button type="button" onClick={retry}>
              Try again
            </button>
          </SignInChrome>
        );
      case "signed-in":
        return children(phase.session);
    }
  }, [phase, signIn, retry, children]);

  return content;
}

function SignInChrome({
  children,
}: {
  readonly children: ReactNode;
}): ReactNode {
  return (
    <div
      data-testid="sign-in-gate"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "1rem",
        height: "100vh",
        fontFamily: "system-ui, sans-serif",
        color: "#dde",
        background: "#101113",
      }}
    >
      {children}
    </div>
  );
}
