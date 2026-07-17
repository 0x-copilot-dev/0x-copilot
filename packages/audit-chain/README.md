# copilot-audit-chain

HMAC-SHA256 hash-chain signing and verification for tamper-evident audit logs.

Used by `services/backend` and `services/ai-backend` so both can share one canonical implementation. Each service still owns its own keys, its own table, and its own per-(table, org_id) chain — this package is the cryptographic primitive only.

## Public API

```python
from copilot_audit_chain import (
    AuditChainSigner,
    AuditChainRow,
    ChainSignature,
    ChainVerificationResult,
)
```

## Configuring the signer

Two ways to construct:

```python
# Direct, for tests and explicit configuration:
signer = AuditChainSigner(
    keys={1: b"32-bytes-of-key-material-min-len-x"},
    active_version=1,
)

# From environment, for production wiring:
signer = AuditChainSigner.from_env(environment_env_var="RUNTIME_ENVIRONMENT")
```

`from_env` reads:

| Env var                  | Purpose                                                                                                                                        |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `AUDIT_HMAC_KEY`         | Hex-encoded active key. Required in production.                                                                                                |
| `AUDIT_HMAC_KEY_VERSION` | Integer version of the active key. Default `1`.                                                                                                |
| `AUDIT_HMAC_KEY_V<N>`    | Hex-encoded prior keys (verification only) for rotation.                                                                                       |
| `<environment_env_var>`  | Reads this var (e.g. `RUNTIME_ENVIRONMENT`). If `production`, `from_env` raises when `AUDIT_HMAC_KEY` is unset. Caller must pass the var name. |

In dev (when the environment var is anything other than `production` and no key is set), a hardcoded sentinel key is used so unconfigured local development still produces a verifiable chain. **The sentinel is byte-identical to the value the legacy in-tree implementations used.**

## What the chain proves

Given `n` rows ordered by `seq` ascending, each carrying `(seq, payload, prev_hash, signature, key_version)`:

- **Tamper of payload** breaks the row's `signature` recompute.
- **Reorder / removal of any row** breaks `prev_hash` linkage at the next row.
- **Replay of a row from a different chain** breaks `prev_hash` (and `__event_type__` binds the action identity inside the signed payload).
- **Key rotation** mid-chain works as long as the verifier holds all relevant key versions.

## What the chain does not prove

- That the row was _appended_ on time (clock skew is not constrained — `created_at` is opaque to the signer except as a payload field).
- That no other rows were inserted _between_ legitimate appends (it proves the row sequence is the one signed; if a writer inserts a row mid-stream and re-signs the chain forward, the chain still verifies). Append-only enforcement is the database's job: a Postgres role with revoked UPDATE/DELETE plus a constraint trigger. The chain layers on top of those.
- Confidentiality. Audit data is signed in plaintext (binding the signature to the _content_); column-level encryption sits on the storage layer separately.

## Tests

```bash
cd packages/audit-chain
python -m pip install -e .
python -m pytest
```

## Maintenance

Any change to the canonical signing form (`_canonicalize` or `_stringify`) is a **breaking change**. Bump the major version in `pyproject.toml` and require both consumer services to update in lockstep — historical signatures will not verify under a different canonical form.
