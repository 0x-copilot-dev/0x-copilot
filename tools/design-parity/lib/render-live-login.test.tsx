/* design-parity · live LOGIN render harness (vitest + jsdom)
 * =========================================================================
 * Renders the REAL web sign-in card (the `SignInCard` inside
 * apps/frontend/src/features/auth/LoginScreen.tsx) to static HTML, one file
 * per reachable view state, so the browser extractor reads the exact
 * computed styles the shipping app produces. This is the "live" side of the
 * login parity diff; the "design" side is the vendored Claude Design mock
 * (../surfaces/login/design/).
 *
 * Run: node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs
 * Output: surfaces/login/live/<state>.html  (+ copied ds.css / styles.css)
 *
 * Approach — mirror apps/frontend AuthContext.test.tsx / WalletSignIn.test.tsx:
 * render the REAL `<LoginScreen/>` inside the REAL `<AuthProvider>`, drive the
 * five-state wallet machine with @testing-library `fireEvent`, and control the
 * async surface by (a) spying the auth API modules and (b) announcing a FAKE
 * EIP-1193 wallet over real EIP-6963 events. `connectWallet` / `personalSign`
 * are module-internal to LoginScreen, so they are NOT mocked — the fake
 * provider's `.request()` is the only lever, exactly as the app uses it.
 *
 * SignInCard is module-internal (not exported), so we render the exported
 * `<LoginScreen/>`; `_initialStep` lands on the sign-in card because the
 * session probe 401s (anonymous) and window.location is the default "/".
 * ========================================================================= */
import { createElement as h } from "react";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../apps/frontend/src/features/auth/AuthContext";
import { LoginScreen } from "../../../apps/frontend/src/features/auth/LoginScreen";
import * as authApi from "../../../apps/frontend/src/api/authApi";
import * as siweApi from "../../../apps/frontend/src/api/siweApi";
import * as devIdpApi from "../../../apps/frontend/src/api/devIdpApi";
import { UnauthorizedError } from "../../../apps/frontend/src/api/http";
import {
  EIP6963_ANNOUNCE_EVENT,
  EIP6963_REQUEST_EVENT,
  type Eip1193RequestArguments,
} from "../../../apps/frontend/src/features/auth/eip6963";

const HERE = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const REPO = (p: string) => HERE("../../../" + p); // tools/design-parity/lib -> repo root
const LIVE = (p: string) => HERE("../surfaces/login/live/" + p);

// A real, EIP-55-checksummed address (from WalletSignIn.test.tsx) so the pure
// `buildSiweMessage` / `toWireAddress` validators don't throw in jsdom.
const ADDRESS = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359";
const CHAIN_HEX = "0x2105"; // 8453 = Base (present in CHAIN_NAMES)
const SIGNATURE = `0x${"ab".repeat(65)}`;

const GOOGLE_PROVIDER = {
  provider_id: "google",
  kind: "oidc",
  display_name: "Google",
  enabled: true,
} as const;

/** Wrap a state's card HTML with the REAL stylesheets (design-system tokens
 *  first, then the app sheet that references them) + a fixed, dark, centred
 *  frame mimicking the host the login mounts inside. Typography/color/border/
 *  padding do not depend on the frame; width/height are treated as noise. */
function shell(cardHTML: string): string {
  return `<!doctype html><html data-theme="dark"><head><meta charset=utf-8><link rel=stylesheet href="./ds.css"><link rel=stylesheet href="./styles.css"><style>html,body{margin:0;height:100%;background:#050506}#frame{width:960px;height:640px;display:flex;flex-direction:column;background:var(--color-bg,#09090b);color:var(--color-text,#ececf1);font-family:var(--font-sans)}</style></head><body><div id="frame">${cardHTML}</div></body></html>`;
}

/** Serialize the sign-in card subtree (never the whole document). */
function captureCard(): string {
  const card = document.querySelector(".loginx-card");
  return card ? card.outerHTML : "";
}

function writeState(name: string, cardHTML: string): void {
  writeFileSync(LIVE(`${name}.html`), shell(cardHTML));
}

/** Announce a fake EIP-1193 wallet on the next EIP-6963 request event.
 *  `requestImpl` is the provider's `.request` — the single lever the card
 *  pulls (eth_requestAccounts / eth_chainId / personal_sign). Returns a
 *  teardown that removes the listener. */
function installFakeWallet(
  requestImpl: (args: Eip1193RequestArguments) => Promise<unknown>,
): () => void {
  const request = vi.fn(requestImpl);
  const onRequest = (): void => {
    window.dispatchEvent(
      new CustomEvent(EIP6963_ANNOUNCE_EVENT, {
        detail: {
          info: {
            uuid: "u-metamask",
            name: "MetaMask",
            icon: "",
            rdns: "io.metamask",
          },
          provider: { request },
        },
      }),
    );
  };
  window.addEventListener(EIP6963_REQUEST_EVENT, onRequest);
  return () => window.removeEventListener(EIP6963_REQUEST_EVENT, onRequest);
}

/** request impl that resolves the whole SIWE handshake. */
const resolveAll = async ({
  method,
}: Eip1193RequestArguments): Promise<unknown> => {
  if (method === "eth_requestAccounts") return [ADDRESS];
  if (method === "eth_accounts") return [ADDRESS];
  if (method === "eth_chainId") return CHAIN_HEX;
  if (method === "personal_sign") return SIGNATURE;
  return null;
};

/** request impl that hangs forever on the connect step → view stays
 *  "connecting" (waiting for the extension). */
const hangOnConnect = async ({
  method,
}: Eip1193RequestArguments): Promise<unknown> => {
  if (method === "eth_requestAccounts") return new Promise<unknown>(() => {});
  if (method === "eth_chainId") return CHAIN_HEX;
  if (method === "personal_sign") return SIGNATURE;
  return null;
};

function renderLogin(): void {
  render(
    h(AuthProvider, { persistBearer: false }, h(LoginScreen, {})),
  );
}

/** Click the wallet option and wait for the EIP-6963-discovered row. */
async function openWalletPicker(): Promise<void> {
  fireEvent.click(await screen.findByTestId("login-option-wallet"));
  await screen.findByTestId(
    "wallet-provider-io.metamask",
    {},
    { timeout: 4000 },
  );
}

const teardowns: Array<() => void> = [];

describe("live login — SignInCard states → static HTML", () => {
  beforeAll(() => {
    mkdirSync(LIVE(""), { recursive: true });
    // Copy the REAL stylesheets next to the harness (ds tokens first).
    copyFileSync(REPO("packages/design-system/src/styles.css"), LIVE("ds.css"));
    copyFileSync(REPO("apps/frontend/src/styles.css"), LIVE("styles.css"));
  });

  beforeEach(() => {
    try {
      window.localStorage?.clear?.();
    } catch {
      /* no localStorage in this env */
    }
    vi.restoreAllMocks();
    // Default: no session (anonymous → sign-in card), Google advertised so the
    // pick view renders all three options.
    vi.spyOn(authApi, "fetchCurrentSession").mockRejectedValue(
      new UnauthorizedError("Missing bearer token"),
    );
    vi.spyOn(authApi, "listAuthProviders").mockResolvedValue([GOOGLE_PROVIDER]);
    // SIWE handshake resolves by default; per-state tests override as needed.
    vi.spyOn(siweApi, "requestSiweNonce").mockResolvedValue({
      nonce: "88213",
      expires_at: "2026-07-17T10:40:00Z",
    });
    vi.spyOn(siweApi, "verifySiwe").mockResolvedValue({
      user_id: "u",
      session_id: "s",
      bearer_token: "t",
      expires_at: "2099-01-01T00:00:00Z",
      return_to: null,
      requires_mfa: false,
    });
  });

  afterEach(() => {
    while (teardowns.length > 0) teardowns.pop()?.();
    cleanup();
  });

  it("pick — the sign-in options screen", async () => {
    renderLogin();
    // Wait for the Google option so all three entries are in the snapshot.
    await screen.findByTestId("login-google");
    expect(screen.getByTestId("login-option-wallet")).toBeTruthy();
    expect(screen.getByTestId("login-option-local")).toBeTruthy();
    const html = captureCard();
    expect(html).toContain("loginx-title");
    writeState("pick", html);
  });

  it("pick-error — inline .login-card__error in the pick view", async () => {
    // The ONLY persistent inline pick error is the local-sign-in failure:
    // wallet errors call reset() which clears the error (see report). Drive a
    // failing dev-persona mint so `signInLocally`'s catch sets the error and
    // leaves the view on "pick".
    vi.spyOn(devIdpApi, "mintDevBearer").mockRejectedValue(
      new Error("Local sign-in is unavailable in this build."),
    );
    renderLogin();
    fireEvent.click(await screen.findByTestId("login-option-local"));
    const err = await screen.findByTestId("login-error");
    expect(err.className).toContain("login-card__error");
    writeState("pick-error", captureCard());
  });

  it("wallets — EIP-6963 discovered wallet list", async () => {
    teardowns.push(installFakeWallet(resolveAll));
    renderLogin();
    await openWalletPicker();
    expect(screen.getByText(/Choose a wallet/)).toBeTruthy();
    writeState("wallets", captureCard());
  });

  it("connecting — waiting for the extension", async () => {
    teardowns.push(installFakeWallet(hangOnConnect));
    renderLogin();
    await openWalletPicker();
    fireEvent.click(screen.getByTestId("wallet-provider-io.metamask"));
    await screen.findByTestId("wallet-connecting", {}, { timeout: 4000 });
    writeState("connecting", captureCard());
  });

  it("sign — signature-request review", async () => {
    teardowns.push(installFakeWallet(resolveAll));
    renderLogin();
    await openWalletPicker();
    fireEvent.click(screen.getByTestId("wallet-provider-io.metamask"));
    await screen.findByTestId("wallet-address", {}, { timeout: 4000 });
    expect(screen.getByTestId("wallet-message")).toBeTruthy();
    writeState("sign", captureCard());
  });

  it("done — signed-in confirmation", async () => {
    // Mount probe 401s (anonymous); the post-adopt refresh succeeds.
    vi.spyOn(authApi, "fetchCurrentSession")
      .mockRejectedValueOnce(new UnauthorizedError("Missing bearer token"))
      .mockResolvedValue({
        identity: {
          org_id: "org_a",
          user_id: "usr_wallet",
          roles: ["employee"],
          permission_scopes: ["runtime:use"],
        },
      });
    teardowns.push(installFakeWallet(resolveAll));
    renderLogin();
    await openWalletPicker();
    fireEvent.click(screen.getByTestId("wallet-provider-io.metamask"));
    fireEvent.click(await screen.findByTestId("wallet-sign-submit", {}, { timeout: 4000 }));
    await screen.findByTestId("wallet-done", {}, { timeout: 4000 });
    writeState("done", captureCard());
  });
});
