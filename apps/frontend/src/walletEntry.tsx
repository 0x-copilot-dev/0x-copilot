/**
 * Entry for the standalone desktop wallet sign-in page (`/wallet.html`,
 * second Vite input — see `vite.config.ts`). Renders ONLY the SIWE
 * wallet flow against the same-origin facade and relays the minted
 * bearer to the desktop app's loopback listener; see
 * `features/auth/WalletHandoffPage.tsx` for the contract.
 *
 * Deliberately tiny: no App shell, no AuthProvider, no router, no OTel —
 * the page's one job is nonce → sign → verify → redirect.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@0x-copilot/design-system/styles.css";
import "./styles.css";

import { WalletHandoffPage } from "./features/auth/WalletHandoffPage";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Root element was not found");
}

const params = new URLSearchParams(window.location.search);
const rawHandoff = params.get("handoff");
// LINK mode (account-linking PRD FR-L1) — the desktop opens
// `/wallet.html?mode=link` to collect a proof for an authenticated wallet
// link instead of minting a session.
const mode = params.get("mode") === "link" ? "link" : "sign-in";

createRoot(root).render(
  <StrictMode>
    <WalletHandoffPage rawHandoff={rawHandoff} mode={mode} />
  </StrictMode>,
);
