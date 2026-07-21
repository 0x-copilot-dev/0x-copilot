/**
 * Wallet-LINK flow (account-linking PRD FR-L1/M1/U2) — the authenticated
 * sibling of `WalletSignIn`. Same EIP-6963 picker + SIWE proof machinery
 * (shared via `walletProof.ts`), but the proven wallet binds to the
 * CURRENT account via `POST /v1/me/identities/wallet` instead of minting a
 * session with `/v1/auth/siwe/verify`.
 *
 * Merge (FR-M1/U2): a wallet already owned by ANOTHER account comes back as
 * a 409 `TransportHttpError` with `code === "merge_required"`. We surface an
 * explicit confirm ("…move that account's data into this one and disable the
 * other login"); on consent we re-run the proof (a fresh single-use nonce)
 * and re-POST with `confirm_merge: true`, which runs the merge saga.
 *
 * The wallet-link path is the ONLY client entry to the merge saga — the
 * Google callback deliberately never merges (PRD §11 confused-deputy note).
 */

import { Card } from "@0x-copilot/design-system";
import { isTransportHttpError } from "@0x-copilot/chat-transport";
import { LINK_ERROR_CODES } from "@0x-copilot/api-types";
import type { ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { linkWallet } from "../../api/meApi";
import { errorMessage } from "../../utils/errors";
import {
  discoverWalletProviders,
  type Eip1193Provider,
  type WalletProviderCandidate,
} from "./eip6963";
import { collectWalletSiweProof, isWalletUserRejection } from "./walletProof";

export const CHAIN_NOT_ALLOWED_LINK_MESSAGE =
  "Switch to a supported network (Ethereum, Base, Arbitrum, Robinhood Chain)";

const NO_WALLET_MESSAGE =
  "No browser wallet detected. Install one, then reload this page.";

const MERGE_CONFIRM_MESSAGE =
  "This wallet already belongs to another 0xCopilot account. Linking it " +
  "will move that account's data into this one and disable its separate " +
  "login. This cannot be undone.";

type LinkStep =
  | { kind: "idle" }
  | { kind: "discovering" }
  | { kind: "pick"; providers: WalletProviderCandidate[] }
  | { kind: "working"; walletName: string }
  // A merge is required — hold the chosen provider so consent can re-sign.
  | { kind: "confirm-merge"; provider: Eip1193Provider; walletName: string }
  | { kind: "merging"; walletName: string };

export interface WalletLinkFlowProps {
  /** Called after a successful link/merge so the host refreshes its list. */
  readonly onLinked: () => void;
  /** EIP-6963 collection window override (tests pass a small value). */
  readonly discoveryWindowMs?: number;
  /** Label for the trigger button. */
  readonly label?: string;
}

export function WalletLinkFlow(props: WalletLinkFlowProps): ReactElement {
  const { onLinked, discoveryWindowMs, label = "Link a wallet" } = props;
  const [step, setStep] = useState<LinkStep>({ kind: "idle" });
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

  // The one shared tail: collect a fresh proof and POST (optionally with
  // consent). Splitting "first attempt" from "confirmed merge" only differs
  // in the `confirmMerge` flag + which step we transition through.
  const submitLink = useCallback(
    async (
      provider: Eip1193Provider,
      walletName: string,
      confirmMerge: boolean,
    ): Promise<void> => {
      if (busyRef.current) return;
      busyRef.current = true;
      setError(null);
      setStep({
        kind: confirmMerge ? "merging" : "working",
        walletName,
      });
      try {
        const proof = await collectWalletSiweProof(provider);
        const result = await linkWallet(
          proof.message,
          proof.signature,
          confirmMerge,
        );
        if (!aliveRef.current) return;
        setStep({ kind: "idle" });
        // linked / already_linked / merged — all success from the UI's view.
        void result;
        onLinked();
      } catch (err) {
        if (!aliveRef.current) return;
        if (isWalletUserRejection(err)) {
          // Deliberate cancel in the wallet prompt — quiet reset.
          setStep({ kind: "idle" });
          return;
        }
        // FR-M1: the merge trigger. Show the explicit confirm (no owner ids
        // are ever in the payload) and stash the provider for the re-sign.
        if (
          !confirmMerge &&
          isTransportHttpError(err) &&
          err.code === LINK_ERROR_CODES.mergeRequired
        ) {
          setStep({ kind: "confirm-merge", provider, walletName });
          return;
        }
        const detail = errorMessage(err, "wallet link failed");
        setError(
          detail === "chain_not_allowed"
            ? CHAIN_NOT_ALLOWED_LINK_MESSAGE
            : detail,
        );
        setStep({ kind: "idle" });
      } finally {
        busyRef.current = false;
      }
    },
    [onLinked],
  );

  const cancelMerge = useCallback(() => {
    setStep({ kind: "idle" });
  }, []);

  const busy =
    step.kind === "discovering" ||
    step.kind === "working" ||
    step.kind === "merging";

  return (
    <div className="login-wallet" data-testid="wallet-link">
      <button
        type="button"
        className="me-form__inline-link"
        onClick={() => void openPicker()}
        disabled={busy}
        data-testid="wallet-link-trigger"
      >
        {linkButtonLabel(step, label)}
      </button>

      {step.kind === "pick" && (
        <Card
          className="wallet-picker"
          tone="default"
          data-testid="wallet-link-picker"
        >
          <ul className="wallet-picker__list" aria-label="Available wallets">
            {step.providers.map((candidate) => (
              <li key={candidate.info.uuid} className="wallet-picker__item">
                <button
                  type="button"
                  className="wallet-picker__row"
                  onClick={() =>
                    void submitLink(
                      candidate.provider,
                      candidate.info.name,
                      false,
                    )
                  }
                  data-testid={`wallet-link-provider-${candidate.info.rdns}`}
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

      {(step.kind === "confirm-merge" || step.kind === "merging") && (
        <Card
          className="wallet-picker"
          tone="default"
          role="alertdialog"
          aria-label="Merge accounts"
          data-testid="wallet-link-merge-confirm"
        >
          <p className="settings-meta" data-testid="wallet-link-merge-message">
            {MERGE_CONFIRM_MESSAGE}
          </p>
          <div className="me-form__actions">
            <button
              type="button"
              className="me-form__inline-link"
              onClick={cancelMerge}
              disabled={step.kind === "merging"}
              data-testid="wallet-link-merge-cancel"
            >
              Cancel
            </button>
            <button
              type="button"
              className="login-wallet-btn"
              onClick={() => {
                if (step.kind === "confirm-merge") {
                  void submitLink(step.provider, step.walletName, true);
                }
              }}
              disabled={step.kind === "merging"}
              data-testid="wallet-link-merge-confirm-btn"
            >
              {step.kind === "merging" ? "Merging…" : "Link & merge accounts"}
            </button>
          </div>
        </Card>
      )}

      {error !== null && (
        <p
          className="login-card__error"
          role="alert"
          data-testid="wallet-link-error"
        >
          {error}
        </p>
      )}
    </div>
  );
}

function linkButtonLabel(step: LinkStep, base: string): string {
  switch (step.kind) {
    case "discovering":
      return "Looking for wallets…";
    case "working":
      return `Confirm in ${step.walletName}…`;
    case "merging":
      return "Merging…";
    default:
      return base;
  }
}
