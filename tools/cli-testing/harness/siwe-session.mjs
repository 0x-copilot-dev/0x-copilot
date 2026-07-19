// Complete a SIWE (Sign-In-With-Ethereum) session against the running
// supervised facade, then feed the desktop app's loopback so the GUI signs in.
//
// This BYPASSES the broken facade-served wallet.html (see FINDINGS) but
// exercises the REAL SIWE backend (nonce/verify), the app's loopback handoff,
// and session persistence — giving a real signed-in GUI for surface testing.
//
// Usage:
//   node siwe-session.mjs --facade http://127.0.0.1:62411 \
//     --loopback "http://127.0.0.1:<port>/wallet/cb?state=<state>" [--pk 0x...]
//
// The --loopback value is exactly the handoff target the app opened (captured
// from the driver's openedUrls after clicking "Continue with a wallet").

import { privateKeyToAccount, generatePrivateKey } from "viem/accounts";

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

const FACADE = arg("facade", "http://127.0.0.1:62411").replace(/\/$/, "");
const LOOPBACK = arg("loopback"); // http://127.0.0.1:<port>/wallet/cb?state=...
const CHAIN_ID = Number(arg("chain", "1"));
const DOMAIN = arg("domain", "localhost:5173");
const URI = arg("uri", "http://localhost:5173");
const PK = arg("pk", generatePrivateKey());

if (!LOOPBACK) {
  console.error("ERROR: --loopback is required");
  process.exit(2);
}

const account = privateKeyToAccount(PK);
const address = account.address;

function fmtTs(d) {
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

// Must match backend SIWE_STATEMENT byte-for-byte (verify rejects otherwise).
const STATEMENT = "Sign in to Copilot";

async function main() {
  const out = { address, facade: FACADE };

  // 1. nonce -----------------------------------------------------------------
  const nonceRes = await fetch(`${FACADE}/v1/auth/siwe/nonce`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ address, chain_id: CHAIN_ID }),
  });
  const nonceBody = await nonceRes.json().catch(() => ({}));
  out.nonceStatus = nonceRes.status;
  out.nonceBody = nonceBody;
  if (!nonceRes.ok) {
    console.log(JSON.stringify(out, null, 2));
    process.exit(1);
  }
  const nonce = nonceBody.nonce ?? nonceBody.value ?? nonceBody.data?.nonce;

  // 2. build + sign the EIP-4361 message ------------------------------------
  const issuedAt = new Date(Date.now() - 5000);
  const expiration = new Date(Date.now() + 9 * 60 * 1000);
  const message =
    `${DOMAIN} wants you to sign in with your Ethereum account:\n` +
    `${address}\n` +
    `\n` +
    `${STATEMENT}\n` +
    `\n` +
    `URI: ${URI}\n` +
    `Version: 1\n` +
    `Chain ID: ${CHAIN_ID}\n` +
    `Nonce: ${nonce}\n` +
    `Issued At: ${fmtTs(issuedAt)}\n` +
    `Expiration Time: ${fmtTs(expiration)}`;
  out.message = message;
  const signature = await account.signMessage({ message });

  // 3. verify ----------------------------------------------------------------
  const verifyRes = await fetch(`${FACADE}/v1/auth/siwe/verify`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message, signature }),
  });
  const verifyBody = await verifyRes.json().catch(() => ({}));
  out.verifyStatus = verifyRes.status;
  out.verifyBody = verifyBody;
  if (!verifyRes.ok) {
    console.log(JSON.stringify(out, null, 2));
    process.exit(1);
  }

  // 4. hand off to the app's loopback so the GUI signs in --------------------
  const cb = new URL(LOOPBACK);
  const fields = {
    user_id: verifyBody.user_id,
    session_id: verifyBody.session_id,
    bearer_token: verifyBody.bearer_token,
    expires_at: verifyBody.expires_at,
    requires_mfa: String(verifyBody.requires_mfa ?? false),
    return_to: verifyBody.return_to ?? "",
  };
  for (const [k, v] of Object.entries(fields)) {
    if (v !== undefined && v !== null) cb.searchParams.set(k, v);
  }
  out.loopbackUrl = cb.toString();
  const cbRes = await fetch(cb.toString(), { redirect: "manual" });
  out.loopbackStatus = cbRes.status;
  out.loopbackText = (await cbRes.text().catch(() => "")).slice(0, 200);

  console.log(JSON.stringify(out, null, 2));
}

main().catch((e) => {
  console.error("SIWE_SESSION_FAILED", e?.stack ?? e);
  process.exit(1);
});
