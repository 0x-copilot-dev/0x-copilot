/**
 * Auth context for the frontend (A9).
 *
 * State machine: ``initial → loading → (authenticated | mfa_pending |
 * anonymous)``. Transitions:
 *   - On mount, ``loadCurrentSession()`` calls ``/v1/auth/session``. A
 *     successful response → ``authenticated``; a 401 → ``anonymous``.
 *   - ``login(creds)`` calls ``/v1/auth/login`` then transitions to
 *     ``mfa_pending`` (when the response says so) or ``authenticated``.
 *   - ``completeMfa(challenge)`` runs the verify flow then transitions
 *     to ``authenticated``.
 *   - ``logout()`` calls ``/v1/auth/logout`` and transitions to
 *     ``anonymous``.
 *
 * Bearer storage: in-memory by default; ``localStorage`` opt-in via
 * ``persistBearer=true`` (defaults to ``true`` in the dev profile,
 * ``false`` in single-tenant bank deploys per the C1 toggle —
 * configured at build time).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactElement, ReactNode } from "react";

import {
  configureAuthBearerProvider,
  fetchCurrentSession,
  loginWithPassword,
  logout as logoutApi,
  type SessionIdentity,
} from "../../api/authApi";

const BEARER_STORAGE_KEY = "enterprise.auth.bearer";

export type AuthStatus =
  | "initial"
  | "loading"
  | "anonymous"
  | "mfa_pending"
  | "authenticated"
  | "error";

export interface MfaPendingState {
  session_id: string;
  bearer_token: string;
  user_id: string;
}

export interface AuthState {
  status: AuthStatus;
  identity: SessionIdentity | null;
  mfaPending: MfaPendingState | null;
  error: string | null;
}

export interface AuthContextValue extends AuthState {
  login(args: {
    orgId: string;
    email: string;
    password: string;
  }): Promise<void>;
  completeMfa(): Promise<void>;
  logout(): Promise<void>;
  refresh(): Promise<void>;
  bearer(): string | null;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export interface AuthProviderProps {
  children: ReactNode;
  persistBearer?: boolean;
}

export function AuthProvider({
  children,
  persistBearer = true,
}: AuthProviderProps): ReactElement {
  const [state, setState] = useState<AuthState>({
    status: "initial",
    identity: null,
    mfaPending: null,
    error: null,
  });

  const bearerRef = useRef<string | null>(_loadStoredBearer(persistBearer));

  // Hand the bearer to the API client so every authApi call can attach
  // it. Re-runs on bearer change.
  useEffect(() => {
    configureAuthBearerProvider(() => bearerRef.current);
  }, []);

  const setBearer = useCallback(
    (value: string | null) => {
      bearerRef.current = value;
      if (!persistBearer || typeof window === "undefined") {
        return;
      }
      try {
        if (value === null) {
          window.localStorage.removeItem(BEARER_STORAGE_KEY);
        } else {
          window.localStorage.setItem(BEARER_STORAGE_KEY, value);
        }
      } catch {
        // Quota / opaque-origin storage failure — fall through with the
        // in-memory copy so the session keeps working for this tab.
      }
    },
    [persistBearer],
  );

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, status: "loading", error: null }));
    try {
      const envelope = await fetchCurrentSession();
      setState({
        status: "authenticated",
        identity: envelope.identity,
        mfaPending: null,
        error: null,
      });
    } catch (err) {
      // 401 → anonymous; everything else → error so the UI can render
      // a "couldn't reach backend" banner.
      const message = err instanceof Error ? err.message : "auth probe failed";
      const looksLike401 = /401|unauthor/i.test(message);
      setBearer(null);
      setState({
        status: looksLike401 ? "anonymous" : "error",
        identity: null,
        mfaPending: null,
        error: looksLike401 ? null : message,
      });
    }
  }, [setBearer]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(
    async (args: { orgId: string; email: string; password: string }) => {
      setState((prev) => ({ ...prev, status: "loading", error: null }));
      try {
        const result = await loginWithPassword({
          org_id: args.orgId,
          email: args.email,
          password: args.password,
        });
        setBearer(result.bearer_token);
        if (result.requires_mfa) {
          setState({
            status: "mfa_pending",
            identity: null,
            mfaPending: {
              session_id: result.session_id,
              bearer_token: result.bearer_token,
              user_id: result.user_id,
            },
            error: null,
          });
          return;
        }
        await refresh();
      } catch (err) {
        const message = err instanceof Error ? err.message : "login failed";
        setBearer(null);
        setState({
          status: "anonymous",
          identity: null,
          mfaPending: null,
          error: message,
        });
        throw err;
      }
    },
    [refresh, setBearer],
  );

  const completeMfa = useCallback(async () => {
    // Caller has already finished the verify HTTP round-trip via the
    // ``MfaPrompt`` component; we just refresh the session so the
    // satisfied scopes flow through.
    await refresh();
  }, [refresh]);

  const handleLogout = useCallback(async () => {
    try {
      await logoutApi();
    } catch {
      // Best-effort: the bearer is already revoked client-side even if
      // the server round-trip failed (e.g. offline).
    }
    setBearer(null);
    setState({
      status: "anonymous",
      identity: null,
      mfaPending: null,
      error: null,
    });
  }, [setBearer]);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      login,
      completeMfa,
      logout: handleLogout,
      refresh,
      bearer: () => bearerRef.current,
    }),
    [state, login, completeMfa, handleLogout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth() must be called inside <AuthProvider>");
  }
  return value;
}

function _loadStoredBearer(persistBearer: boolean): string | null {
  if (!persistBearer || typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage.getItem(BEARER_STORAGE_KEY);
  } catch {
    return null;
  }
}
