/**
 * Wallet sign-in entry point (SIWE, EIP-4361) — "Connect wallet" button
 * plus a small provider picker fed by EIP-6963 discovery. No wagmi/viem;
 * the wallet is driven through raw EIP-1193 `request` calls.
 *
 * Flow per selected provider:
 *
 *   eth_requestAccounts → eth_chainId
 *     → POST /v1/auth/siwe/nonce   { address (lowercase), chain_id }
 *     → buildSiweMessage(...)      (frozen template, EIP-55 address)
 *     → personal_sign(hex(msg), address)
 *     → POST /v1/auth/siwe/verify  { message, signature }
 *     → session handoff (OIDC-callback shape)
 *
 * The handoff is handed to `AuthContext.adoptSession` — the same
 * "bearer in hand, refresh the session" tail the magic-link and
 * workspace-pick completions use — unless the host passes `onSession`
 * (the standalone desktop wallet page does, to redirect the bearer to
 * its loopback listener instead).
 *
 * Error surface:
 *   - EIP-1193 code 4001 (user rejected)  → quiet cancel, no error line
 *   - detail "chain_not_allowed"          → friendly switch-network copy
 *   - anything else                       → server detail verbatim
 */

import { Card } from "@enterprise-search/design-system";
import type { SiweSessionResponse } from "@enterprise-search/api-types";
import type { ReactElement } from "react";
import { useCallback, useContext, useEffect, useRef, useState } from "react";

import { requestSiweNonce, verifySiwe } from "../../api/siweApi";
import { errorMessage } from "../../utils/errors";
import { toWireAddress } from "../../utils/eip55";
import { AuthContext } from "./AuthContext";
import {
  discoverWalletProviders,
  type Eip1193Provider,
  type WalletProviderCandidate,
} from "./eip6963";
import { buildSiweMessage } from "./siweMessage";

export const CHAIN_NOT_ALLOWED_MESSAGE =
  "Switch to a supported network (Ethereum, Base, Arbitrum, Robinhood Chain)";

const NO_WALLET_MESSAGE =
  "No browser wallet detected. Install one, then reload this page.";

type WalletStep =
  | { kind: "idle" }
  | { kind: "discovering" }
  | { kind: "pick"; providers: WalletProviderCandidate[] }
  | { kind: "connecting"; walletName: string }
  | { kind: "signing"; walletName: string }
  | { kind: "verifying"; walletName: string }
  | { kind: "done" };

export interface WalletSignInProps {
  /** When set, the minted session handoff is delivered here instead of
   * being adopted into AuthContext — the standalone wallet page uses
   * this to relay the bearer to the desktop loopback. */
  onSession?: (session: SiweSessionResponse) => void | Promise<void>;
  /** EIP-6963 collection window override (tests pass a small value). */
  discoveryWindowMs?: number;
}

export function WalletSignIn(props: WalletSignInProps): ReactElement {
  const { onSession, discoveryWindowMs } = props;
  // Nullable on purpose: the standalone wallet page mounts without an
  // <AuthProvider> and always supplies `onSession`. The login screen
  // mounts inside the provider and relies on adoptSession.
  const auth = useContext(AuthContext);
  const [step, setStep] = useState<WalletStep>({ kind: "idle" });
  const [error, setError] = useState<string | null>(null);
  const aliveRef = useRef(true);
  const busyRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const openPicker = useCallback(async (): Promise<void> => {
    if (busyRef.current) return;
    setError(null);
    if (step.kind === "pick") {
      setStep({ kind: "idle" });
      return;
    }
    setStep({ kind: "discovering" });
    busyRef.current = true;
    try {
      const providers = await discoverWalletProviders(
        discoveryWindowMs === undefined ? {} : { windowMs: discoveryWindowMs },
      );
      if (!aliveRef.current) return;
      if (providers.length === 0) {
        setError(NO_WALLET_MESSAGE);
        setStep({ kind: "idle" });
        return;
      }
      setStep({ kind: "pick", providers });
    } finally {
      busyRef.current = false;
    }
  }, [discoveryWindowMs, step.kind]);

  const selectProvider = useCallback(
    async (candidate: WalletProviderCandidate): Promise<void> => {
      if (busyRef.current) return;
      busyRef.current = true;
      setError(null);
      const walletName = candidate.info.name;
      try {
        setStep({ kind: "connecting", walletName });
        const { address, chainId } = await _connect(candidate.provider);

        const nonce = await requestSiweNonce({
          address: toWireAddress(address),
          chain_id: chainId,
        });

        const message = buildSiweMessage({
          domain: window.location.host,
          uri: window.location.origin,
          address,
          chainId,
          nonce: nonce.nonce,
          issuedAt: new Date().toISOString(),
        });

        setStep({ kind: "signing", walletName });
        const signature = await _personalSign(
          candidate.provider,
          message,
          address,
        );

        setStep({ kind: "verifying", walletName });
        const session = await verifySiwe({ message, signature });

        if (!aliveRef.current) return;
        setStep({ kind: "done" });
        if (onSession) {
          await onSession(session);
        } else if (auth !== null) {
          await auth.adoptSession({
            bearer_token: session.bearer_token,
            session_id: session.session_id,
            user_id: session.user_id,
            requires_mfa: session.requires_mfa,
          });
        }
      } catch (err) {
        if (!aliveRef.current) return;
        if (_isUserRejection(err)) {
          // EIP-1193 4001 — the user closed/rejected the wallet prompt.
          // That's a deliberate cancel, not a failure: back to idle, quietly.
          setStep({ kind: "idle" });
          return;
        }
        const detail = errorMessage(err, "wallet sign-in failed");
        setError(
          detail === "chain_not_allowed" ? CHAIN_NOT_ALLOWED_MESSAGE : detail,
        );
        setStep({ kind: "idle" });
      } finally {
        busyRef.current = false;
      }
    },
    [auth, onSession],
  );

  return (
    <div className="login-wallet" data-testid="wallet-signin">
      <button
        type="button"
        className="login-wallet-btn"
        onClick={() => void openPicker()}
        disabled={_busy(step)}
        data-testid="wallet-connect"
      >
        <WalletGlyph />
        <span>{_buttonLabel(step)}</span>
      </button>
      {step.kind === "pick" && (
        <Card
          className="wallet-picker"
          tone="default"
          data-testid="wallet-picker"
        >
          <ul className="wallet-picker__list" aria-label="Available wallets">
            {step.providers.map((candidate) => (
              <li key={candidate.info.uuid} className="wallet-picker__item">
                <button
                  type="button"
                  className="wallet-picker__row"
                  onClick={() => void selectProvider(candidate)}
                  data-testid={`wallet-provider-${candidate.info.rdns}`}
                >
                  {candidate.info.icon ? (
                    <img
                      className="wallet-picker__icon"
                      src={candidate.info.icon}
                      alt=""
                      aria-hidden="true"
                    />
                  ) : (
                    <span
                      className="wallet-picker__icon wallet-picker__icon--letter"
                      aria-hidden="true"
                    >
                      {candidate.info.name.charAt(0).toUpperCase()}
                    </span>
                  )}
                  <span className="wallet-picker__name">
                    {candidate.info.name}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
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
    </div>
  );
}

function _busy(step: WalletStep): boolean {
  return (
    step.kind === "discovering" ||
    step.kind === "connecting" ||
    step.kind === "signing" ||
    step.kind === "verifying" ||
    step.kind === "done"
  );
}

function _buttonLabel(step: WalletStep): string {
  switch (step.kind) {
    case "discovering":
      return "Looking for wallets…";
    case "connecting":
      return `Waiting for ${step.walletName}…`;
    case "signing":
      return `Confirm the signature in ${step.walletName}…`;
    case "verifying":
      return "Verifying signature…";
    case "done":
      return "Signed in";
    default:
      return "Connect wallet";
  }
}

// ---------------------------------------------------------------------------
// EIP-1193 plumbing
// ---------------------------------------------------------------------------

async function _connect(
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

async function _personalSign(
  provider: Eip1193Provider,
  message: string,
  address: string,
): Promise<string> {
  // personal_sign takes the hex-encoded UTF-8 message first, then the
  // signing address (the reverse of eth_sign — a classic wallet gotcha).
  const signature = await provider.request({
    method: "personal_sign",
    params: [_hexEncodeUtf8(message), address],
  });
  if (typeof signature !== "string" || !signature.startsWith("0x")) {
    throw new Error("wallet returned an invalid signature");
  }
  return signature;
}

function _hexEncodeUtf8(text: string): string {
  let hex = "0x";
  for (const byte of new TextEncoder().encode(text)) {
    hex += byte.toString(16).padStart(2, "0");
  }
  return hex;
}

/** EIP-1193 ProviderRpcError code 4001 — "User Rejected Request". */
function _isUserRejection(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code: unknown }).code === 4001
  );
}

// ---------------------------------------------------------------------------
// Neutral wallet glyph (theme-tinted via currentColor — unlike the Google
// mark there are no brand-colour rules here).
// ---------------------------------------------------------------------------

function WalletGlyph(): ReactElement {
  return (
    <svg
      className="login-wallet-btn__glyph"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
    >
      <rect x="3" y="6" width="18" height="13" rx="2.5" />
      <path d="M3 9.5h18" opacity="0" />
      <path d="M16 3.8H6.2A3.2 3.2 0 0 0 3 7v1" />
      <circle cx="16.6" cy="12.6" r="1.15" fill="currentColor" stroke="none" />
    </svg>
  );
}
