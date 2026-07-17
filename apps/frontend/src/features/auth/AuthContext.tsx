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
  consumeMagicLink as consumeMagicLinkApi,
  fetchCurrentSession,
  loginWithPassword,
  logout as logoutApi,
  selectWorkspace as selectWorkspaceApi,
  type SessionIdentity,
} from "../../api/authApi";
import type {
  MagicLinkCallbackResponse,
  WorkspaceCandidate,
} from "@0x-copilot/api-types";
import {
  configureUnauthorizedHandler,
  UnauthorizedError,
} from "../../api/http";
import {
  useKeyValueStore,
  useSecretStorage,
  type KeyValueStore,
  type SecretStorage,
} from "@0x-copilot/chat-surface";

import { stripMagicLinkTokenFromUrl } from "../../app/authUrlHygiene";
import { loadActivePersonaSlug, mintDevBearer } from "./devIdp";
import { BEARER_STORAGE_KEY } from "./storageKeys";
import { errorMessage } from "../../utils/errors";

/**
 * W0.1 — In dev, ensure a bearer exists before the first /v1/auth/session
 * probe by minting one for the active persona via the dev IdP. Returns the
 * (possibly newly-minted) bearer or ``null`` if minting failed or we're
 * not in dev mode. Production builds tree-shake the dev IdP module.
 */
async function _devEnsureBearer(store: KeyValueStore): Promise<string | null> {
  if (!import.meta.env.DEV) return null;
  try {
    const slug = loadActivePersonaSlug(store);
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
  | "workspace_pick"
  | "authenticated"
  | "error";

export interface MfaPendingState {
  session_id: string;
  bearer_token: string;
  user_id: string;
}

/** PR 5.1 — magic-link callback returned multiple workspaces. The user
 * picks one and we exchange the ``pick_token`` for a final bearer. */
export interface WorkspacePickState {
  pick_token: string;
  user_id: string;
  workspaces: WorkspaceCandidate[];
  return_to: string | null;
  expires_in_seconds: number;
}

export interface AuthState {
  status: AuthStatus;
  identity: SessionIdentity | null;
  mfaPending: MfaPendingState | null;
  workspacePick: WorkspacePickState | null;
  error: string | null;
}

export interface AuthContextValue extends AuthState {
  login(args: {
    orgId: string;
    email: string;
    password: string;
  }): Promise<void>;
  completeMfa(): Promise<void>;
  /** PR 5.1 — consume the plaintext magic-link token from the email URL.
   * On single-workspace returns: bearer set, status flips to authenticated.
   * On multi-workspace returns: status flips to ``workspace_pick`` with
   * the candidate list.
   * Throws if the upstream returns 401 (invalid / expired / consumed). */
  consumeMagicLink(token: string): Promise<MagicLinkCallbackResponse>;
  /** PR 5.1 — exchange the workspace_pick state's ``pick_token`` plus a
   * chosen org for a final session bearer. Refreshes after success. */
  selectWorkspaceFromPick(orgId: string): Promise<void>;
  /** SIWE wallet sign-in — adopt an already-minted session handoff (the
   * OIDC-callback-shaped ``{bearer_token, session_id, user_id,
   * requires_mfa}`` payload ``POST /v1/auth/siwe/verify`` returns).
   * Mirrors the tail of ``login``: sets the bearer, then either flips to
   * ``mfa_pending`` (AuthGate mounts ``<MfaPrompt>``) or refreshes into
   * ``authenticated``. */
  adoptSession(handoff: {
    bearer_token: string;
    session_id: string;
    user_id: string;
    requires_mfa: boolean;
  }): Promise<void>;
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
  // Two substrate-portable stores. The split is the contract — the type
  // system rejects accidental misuse:
  //   - kvStore (KeyValueStore) — non-secret prefs (persona slug for
  //     the dev IdP). Inspectable in devtools is acceptable here.
  //   - secrets (SecretStorage) — bearer tokens. Same shape as the KV
  //     store today (web reference impl wraps localStorage), but the
  //     distinct interface lets the desktop substrate route this to
  //     the OS keychain via VS Code's SecretStorage API later without
  //     touching any callsite.
  const kvStore = useKeyValueStore();
  const secrets = useSecretStorage();

  const [state, setState] = useState<AuthState>({
    status: "initial",
    identity: null,
    mfaPending: null,
    workspacePick: null,
    error: null,
  });

  const bearerRef = useRef<string | null>(
    _loadStoredBearer(secrets, persistBearer),
  );

  // Hand the bearer to the API client so every authApi call can attach
  // it. Re-runs on bearer change.
  useEffect(() => {
    configureAuthBearerProvider(() => bearerRef.current);
  }, []);

  const setBearer = useCallback(
    (value: string | null) => {
      bearerRef.current = value;
      if (!persistBearer) {
        return;
      }
      // SecretStorage swallows substrate errors (private-mode quota,
      // opaque origins, …) internally — the in-memory bearerRef above
      // keeps the session working for this tab even if persistence
      // fails.
      secrets.set(BEARER_STORAGE_KEY, value);
    },
    [persistBearer, secrets],
  );

  // W0.1 — in dev, a 401 means the bearer is missing or stale. Mint a
  // fresh one for the active persona via the dev IdP, attach it, and
  // re-probe the session so the identity matches the new bearer.
  // Returns true on full recovery (bearer + identity refreshed) so
  // callers can short-circuit any "go to anonymous" path. Production
  // builds tree-shake _devEnsureBearer.
  const _devReauthAndRestoreSession =
    useCallback(async (): Promise<boolean> => {
      const minted = await _devEnsureBearer(kvStore);
      if (!minted) return false;
      setBearer(minted);
      try {
        const envelope = await fetchCurrentSession();
        setState({
          status: "authenticated",
          identity: envelope.identity,
          mfaPending: null,
          workspacePick: null,
          error: null,
        });
        return true;
      } catch {
        return false;
      }
    }, [kvStore, setBearer]);

  // Register the 401 interceptor so any other API helper (agentApi,
  // mcpApi, skillsApi, etc.) that sees a 401 attempts a silent dev
  // re-auth before falling back to anonymous. In prod the dev mint
  // returns null, so this collapses to the original "drop bearer +
  // flip to anonymous" behavior.
  useEffect(() => {
    configureUnauthorizedHandler(() => {
      void (async () => {
        if (await _devReauthAndRestoreSession()) return;
        bearerRef.current = null;
        setState({
          status: "anonymous",
          identity: null,
          mfaPending: null,
          workspacePick: null,
          error: null,
        });
      })();
    });
    return () => {
      configureUnauthorizedHandler(null);
    };
  }, [_devReauthAndRestoreSession]);

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, status: "loading", error: null }));
    try {
      const envelope = await fetchCurrentSession();
      setState({
        status: "authenticated",
        identity: envelope.identity,
        mfaPending: null,
        workspacePick: null,
        error: null,
      });
    } catch (err) {
      const message = errorMessage(err, "auth probe failed");
      // Authoritative 401 detection: every API helper routes 401s through
      // `assertOk` → `throw new UnauthorizedError(...)`. Sniffing message
      // text was brittle (it broke when the facade started returning a
      // structured `{"detail":"Missing bearer token"}` body that no longer
      // contained the substring "401" or "unauthor").
      const looksLike401 = err instanceof UnauthorizedError;
      if (looksLike401 && (await _devReauthAndRestoreSession())) {
        return;
      }
      setBearer(null);
      // PR 5.1 — don't stomp on a pending interactive flow (mfa_pending,
      // workspace_pick) that may have transitioned while ``refresh`` was
      // in flight. Those states are owned by ``login`` / ``consumeMagicLink``
      // and outlive a 401 from the session probe (the bearer hasn't been
      // minted yet by definition).
      setState((prev) => {
        if (prev.status === "mfa_pending" || prev.status === "workspace_pick") {
          return prev;
        }
        return {
          status: looksLike401 ? "anonymous" : "error",
          identity: null,
          mfaPending: null,
          workspacePick: null,
          error: looksLike401 ? null : message,
        };
      });
    }
  }, [_devReauthAndRestoreSession, setBearer]);

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
            workspacePick: null,
            error: null,
          });
          return;
        }
        await refresh();
      } catch (err) {
        const message = errorMessage(err, "login failed");
        setBearer(null);
        setState({
          status: "anonymous",
          identity: null,
          mfaPending: null,
          workspacePick: null,
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

  const consumeMagicLink = useCallback(
    async (token: string): Promise<MagicLinkCallbackResponse> => {
      setState((prev) => ({ ...prev, status: "loading", error: null }));
      try {
        const result = await consumeMagicLinkApi(token);
        // Strip ?token= from the URL on success so the back button can't
        // replay magic-link consumption. Lives in app/ not features/ so
        // the substrate-boundary lint rule isn't tripped here.
        stripMagicLinkTokenFromUrl();
        if (result.outcome === "session_minted") {
          if (!result.bearer_token) {
            throw new Error("session_minted response missing bearer_token");
          }
          setBearer(result.bearer_token);
          await refresh();
          return result;
        }
        // workspace_pick_required
        if (!result.pick_token || !result.workspaces) {
          throw new Error(
            "workspace_pick_required response missing pick_token / workspaces",
          );
        }
        setState({
          status: "workspace_pick",
          identity: null,
          mfaPending: null,
          workspacePick: {
            pick_token: result.pick_token,
            user_id: result.user_id,
            workspaces: result.workspaces,
            return_to: result.return_to ?? null,
            expires_in_seconds: result.expires_in_seconds ?? 300,
          },
          error: null,
        });
        return result;
      } catch (err) {
        const message = errorMessage(err, "could not consume magic link");
        setBearer(null);
        setState({
          status: "anonymous",
          identity: null,
          mfaPending: null,
          workspacePick: null,
          error: message,
        });
        throw err;
      }
    },
    [refresh, setBearer],
  );

  const selectWorkspaceFromPick = useCallback(
    async (orgId: string): Promise<void> => {
      const pick = state.workspacePick;
      if (pick === null) {
        throw new Error("not in workspace_pick state");
      }
      setState((prev) => ({ ...prev, status: "loading", error: null }));
      try {
        const result = await selectWorkspaceApi({
          pick_token: pick.pick_token,
          org_id: orgId,
        });
        setBearer(result.bearer_token);
        await refresh();
      } catch (err) {
        const message = errorMessage(err, "could not select workspace");
        setState({
          status: "workspace_pick",
          identity: null,
          mfaPending: null,
          workspacePick: pick,
          error: message,
        });
        throw err;
      }
    },
    [refresh, setBearer, state.workspacePick],
  );

  const adoptSession = useCallback(
    async (handoff: {
      bearer_token: string;
      session_id: string;
      user_id: string;
      requires_mfa: boolean;
    }): Promise<void> => {
      setBearer(handoff.bearer_token);
      if (handoff.requires_mfa) {
        // Same shape `login` produces: the session carries `mfa:pending`
        // and AuthGate routes to <MfaPrompt> off this state.
        setState({
          status: "mfa_pending",
          identity: null,
          mfaPending: {
            session_id: handoff.session_id,
            bearer_token: handoff.bearer_token,
            user_id: handoff.user_id,
          },
          workspacePick: null,
          error: null,
        });
        return;
      }
      await refresh();
    },
    [refresh, setBearer],
  );

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
      workspacePick: null,
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
      consumeMagicLink,
      selectWorkspaceFromPick,
      adoptSession,
      logout: handleLogout,
      refresh,
      bearer: () => bearerRef.current,
      switchWorkspace,
    }),
    [
      state,
      login,
      completeMfa,
      consumeMagicLink,
      selectWorkspaceFromPick,
      adoptSession,
      handleLogout,
      refresh,
      switchWorkspace,
    ],
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

function _loadStoredBearer(
  secrets: SecretStorage,
  persistBearer: boolean,
): string | null {
  if (!persistBearer) {
    return null;
  }
  return secrets.get(BEARER_STORAGE_KEY);
}
