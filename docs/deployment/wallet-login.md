# Wallet login (Sign-In with Ethereum / SIWE)

0xCopilot supports passwordless wallet sign-in using **Sign-In with Ethereum**
([EIP-4361](https://eips.ethereum.org/EIPS/eip-4361)). The user connects any
[EIP-6963](https://eips.ethereum.org/EIPS/eip-6963) browser wallet (MetaMask,
Rabby, …; a legacy `window.ethereum` injection is offered as "Browser wallet"),
signs a structured message, and the backend mints a session.

Source of truth:

- Frontend: [`apps/frontend/src/features/auth/WalletSignIn.tsx`](../../apps/frontend/src/features/auth/WalletSignIn.tsx),
  [`eip6963.ts`](../../apps/frontend/src/features/auth/eip6963.ts),
  [`siweMessage.ts`](../../apps/frontend/src/features/auth/siweMessage.ts).
- Backend: [`services/backend/src/backend_app/identity/siwe.py`](../../services/backend/src/backend_app/identity/siwe.py).
- Facade routes: [`services/backend-facade/src/backend_facade/auth_routes.py`](../../services/backend-facade/src/backend_facade/auth_routes.py).

The **Connect wallet** button always renders (no server probe), unlike Google
sign-in — but a session is only minted when the message verifies.

---

## How the flow works

```
1. POST /v1/auth/siwe/nonce   { address, chain_id }        → { nonce, ... }
2. build the EIP-4361 message (see template below), filling in the nonce
3. wallet.personal_sign(message, address)                   → signature
4. POST /v1/auth/siwe/verify  { message, signature }        → session (bearer)
```

- **Nonce (step 1).** Single-use, minted server-side, bound to `address` +
  `chain_id`. The mint **rejects a `chain_id` that is not allowlisted**
  (`SiweChainNotAllowed`). Nonce TTL is capped at 10 minutes.
- **Message (step 2).** Built from a frozen template (below). `Expiration Time`
  is **required** by the backend parser; clients set it to `Issued At + 5
minutes`, matching the nonce TTL. The address is rendered EIP-55 checksummed.
- **Sign (step 3).** `personal_sign` over the exact message bytes.
- **Verify (step 4).** The backend re-parses the message and rejects any drift
  (whitespace, field order, casing, statement text, domain), recovers the
  signer, links or provisions the account, and mints the session.

### The EIP-4361 message template

```
{domain} wants you to sign in with your Ethereum account:
{address}

Sign in to Copilot

URI: {uri}
Version: 1
Chain ID: {chain_id}
Nonce: {nonce}
Issued At: {issued_at}
Expiration Time: {expiration_time}
```

The statement line is exactly `Sign in to Copilot`. There are no `Not Before`,
`Request ID`, or `Resources` lines. `{domain}` is the serving origin's host
(`window.location.host`) and `{uri}` is the serving origin
(`window.location.origin`).

---

## ⚠️ Template freeze: the message lives in two files, byte-identical

The signed text is a **wire contract**. The backend re-parses it on
`POST /v1/auth/siwe/verify` and rejects any byte-level drift. The template is
therefore duplicated in **two files that MUST stay byte-identical**:

- `apps/frontend/src/features/auth/siweMessage.ts` — `SIWE_MESSAGE_TEMPLATE` /
  `SIWE_STATEMENT`
- `services/backend/src/backend_app/identity/siwe.py` — the server-side builder
  and `SIWE_STATEMENT`

If you change either side (statement wording, field order, spacing, a new line),
**change both in the same PR** and update the fixture in
`apps/frontend/src/features/auth/siweMessage.test.ts`. A one-character mismatch
breaks every wallet login with a parser or `statement` error.

---

## Chain allowlist

The nonce mint only accepts allowlisted EIP-155 chain IDs. The default set is:

| Chain           | ID      |
| --------------- | ------- |
| Ethereum        | `1`     |
| Base            | `8453`  |
| Arbitrum One    | `42161` |
| Robinhood Chain | `4663`  |

Override it with **`SIWE_ALLOWED_CHAIN_IDS`** — a comma-separated list of decimal
integers:

```dotenv
# Allow only Ethereum mainnet and Base
SIWE_ALLOWED_CHAIN_IDS=1,8453
```

Rules (see `parse_allowed_chain_ids` in `siwe.py`):

- **Empty or unset ⇒ the default set above.**
- **Non-integer tokens fail loudly at startup** — a typo'd allowlist must never
  silently lock every chain out (or in).
- An allowlist that resolves to an empty set is a hard error.

A `chain_id` outside the allowlist is refused at nonce mint with
`SiweChainNotAllowed`, before any message is signed.

---

## `SIWE_ORIGIN` (must match the serving origin)

**`SIWE_ORIGIN`** is the absolute origin the backend expects the signed message
to reference. It must be `scheme://host[:port]` (an absolute origin — a bare host
is rejected at startup):

```dotenv
SIWE_ORIGIN=https://copilot.example.com
```

At verify time the backend compares the message's `{domain}` (its `host`
authority) and `{uri}` (its origin) against `SIWE_ORIGIN`, matching scheme and
authority case-insensitively. A mismatch fails with `domain_mismatch`. So
`SIWE_ORIGIN` **must equal the origin the browser is actually served from**,
including port when non-default:

- Self-host behind a domain on 443: `https://<your-host>` (no port).
- Local self-host on the default gateway port: `http://localhost:8090`.

For the **packaged desktop app**, wallet sign-in uses the facade-served
`wallet.html` page over an ephemeral loopback handoff (see
[`apps/desktop/README.md`](../../apps/desktop/README.md), "Connect wallet"); the
embedded backend is wired to the local serving origin, so operators do not set
`SIWE_ORIGIN` themselves for the desktop build.

---

## Verification checklist

- [ ] **Connect wallet** appears on the login screen (it always renders).
- [ ] `SIWE_ORIGIN` exactly matches the browser's origin (scheme + host + port).
- [ ] The wallet's chain is in the allowlist (default or `SIWE_ALLOWED_CHAIN_IDS`).
- [ ] Signing and verifying yields a session with no `domain_mismatch`,
      `statement`, or chain error.
- [ ] After any template edit, both `siweMessage.ts` and `siwe.py` changed
      together and `siweMessage.test.ts` still passes.
