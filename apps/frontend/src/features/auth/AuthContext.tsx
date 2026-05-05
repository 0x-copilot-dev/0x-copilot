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
import { configureUnauthorizedHandler } from "../../api/http";
import { loadActivePersonaSlug, mintDevBearer } from "./devIdp";

const BEARER_STORAGE_KEY = "enterprise.auth.bearer";

/**
 * W0.1 — In dev, ensure a bearer exists before the first /v1/auth/session
 * probe by minting one for the active persona via the dev IdP. Returns the
 * (possibly newly-minted) bearer or ``null`` if minting failed or we're
 * not in dev mode. Production builds tree-shake the dev IdP module.
 */
async function _devEnsureBearer(): Promise<string | null> {
  if (!import.meta.env.DEV) return null;
  try {
    const slug = loadActivePersonaSlug();
    const result = await mintDevBearer(slug);
    return result.bearer;
  } catch {
    return null;
  }
}

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
  /**
   * PR 3.5 (closes PR 2.2 G4) — rotate the active workspace.
   *
   * Called from UserCard's WorkspacePicker. v1 hard-navigates to
   * `?workspace=<orgId>` and lets <AuthGate> re-discover the session;
   * PR 2.2 §3.7 explicitly authorised this path while the auth team's
   * session-rotation endpoint is still in flight. Once
   * `POST /v1/auth/sessions { workspace_id }` lands the implementation
   * upgrades to in-place rotation without callers changing — the prop
   * surface is identical.
   *
   * No-op when `orgId === identity.org_id` (already the active workspace).
   */
  switchWorkspace(orgId: string): Promise<void>;
}

// Exported so consumers that need to *peek* at auth state without
// requiring an ``<AuthProvider>`` parent (e.g. ``<MentionLabel>`` —
// storybook, shared-thread preview, tests) can read it via
// ``useContext(AuthContext)`` and gracefully degrade to anonymous.
// App code should still prefer the typed ``useAuth()`` helper.
export const AuthContext = createContext<AuthContextValue | null>(null);

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

  // Register the 401 interceptor so any other API helper (agentApi,
  // mcpApi, skillsApi, etc.) that sees a 401 drops the bearer and
  // flips back to anonymous.  Wrapped in a ref-stable closure so the
  // interceptor doesn't capture stale state.
  useEffect(() => {
    configureUnauthorizedHandler(() => {
      bearerRef.current = null;
      setState({
        status: "anonymous",
        identity: null,
        mfaPending: null,
        error: null,
      });
    });
    return () => {
      configureUnauthorizedHandler(null);
    };
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
      const message = err instanceof Error ? err.message : "auth probe failed";
      const looksLike401 = /401|unauthor/i.test(message);
      // W0.1 — in dev, a 401 means there is no bearer. Mint one for the
      // active persona via the dev IdP and retry once. Production builds
      // tree-shake _devEnsureBearer; the catch above handles all real
      // 401s (expired bearer, invalid signature, missing IdP).
      if (looksLike401 && import.meta.env.DEV) {
        const minted = await _devEnsureBearer();
        if (minted) {
          setBearer(minted);
          try {
            const envelope = await fetchCurrentSession();
            setState({
              status: "authenticated",
              identity: envelope.identity,
              mfaPending: null,
              error: null,
            });
            return;
          } catch {
            // Fall through to anonymous below if the retry also fails.
          }
        }
      }
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

  const switchWorkspace = useCallback(
    async (orgId: string): Promise<void> => {
      // No-op when already on the requested workspace — prevents a
      // pointless reload from accidental clicks on the current row.
      if (state.identity !== null && state.identity.org_id === orgId) {
        return;
      }
      // Hard-nav fallback (PR 2.2 §3.7). The new tab inherits the bearer
      // from localStorage if persisted; <AuthGate> re-runs `refresh()` on
      // mount which reads the org from the bearer's claims, so the URL
      // hint is informational. We use `assign` (not `replace`) so the
      // back button still returns to the prior workspace's URL.
      if (typeof window === "undefined") {
        return;
      }
      const url = new URL(window.location.href);
      url.searchParams.set("workspace", orgId);
      window.location.assign(url.toString());
    },
    [state.identity],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      login,
      completeMfa,
      logout: handleLogout,
      refresh,
      bearer: () => bearerRef.current,
      switchWorkspace,
    }),
    [state, login, completeMfa, handleLogout, refresh, switchWorkspace],
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
