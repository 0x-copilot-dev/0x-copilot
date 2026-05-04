# PR 23 — C6: Managed Token Vault — KMS Adapter Framework + AWS KMS

**Spec ID:** C6 | **Track:** Deployment & DB | **Wave:** 6 (Security Hardening) | **Estimated effort:** L
**Depends on:** C1 (profile toggles), C2
**Required for:** C7 (field-level encryption uses the same KMS), all bank/gov deploys
**Follow-ups:** C6a (GCP KMS), C6b (Azure Key Vault), C6c (HashiCorp Vault) — same interface, separate small PRs

---

## 1. Functional Specification

### 1.1 Goal

Replace the `raise RuntimeError` stub in `ManagedSecretTokenVault` at [services/backend/src/backend_app/token_vault.py:104](../../services/backend/src/backend_app/token_vault.py#L104) with a real adapter framework. Ship AWS KMS first; the same interface is implemented for GCP/Azure/HashiCorp Vault in follow-ups. Customer-managed encryption keys (BYOK) are mandatory for bank and government deployments.

### 1.2 User-visible behavior

- **Operator (SaaS):** sets `MCP_TOKEN_VAULT_BACKEND=aws_kms` + `MCP_TOKEN_VAULT_KMS_KEY_ID=arn:aws:kms:...` and tokens are encrypted with our KMS CMK.
- **Operator (single-tenant):** customer provisions their own CMK; sets the same env var to their key ARN. We never see the key material.
- **Existing dev/test:** Fernet-based `LocalTokenVault` continues to work when `MCP_TOKEN_VAULT_BACKEND=local`.

### 1.3 Out of scope

- GCP/Azure/Vault adapters (separate small follow-up PRs).
- HSM-backed KMS (covered by HashiCorp Vault adapter).
- Customer-managed key rotation orchestration (we support rotation; orchestration is documented).

---

## 2. Technical Specification

### 2.1 Architecture

- `TokenVault` interface unchanged.
- `ManagedSecretTokenVault` becomes an abstract base; `AwsKmsTokenVault` extends it.
- Factory dispatches on `MCP_TOKEN_VAULT_BACKEND`.
- In-memory short-TTL cache of decrypted plaintexts (5min, max 10k entries) keyed by `sha256(ciphertext)` to mitigate per-request KMS cost. Cache disabled in `single_tenant_self_hosted` profile per customer audit policy (every decrypt audited at the customer's KMS).
- Fail-closed on writes when KMS unavailable; reads serve from cache up to 5min then fail-closed.
- Per-row `kms_key_id` column added to `mcp_auth_connections` for rotation.

### 2.2 Schema changes

Migration `services/backend/migrations/0012_token_vault_key_id.sql`:

```sql
ALTER TABLE mcp_auth_connections ADD COLUMN kms_key_id TEXT;
```

Existing rows: `kms_key_id` stays NULL (Fernet legacy); reads detect via prefix or NULL and use the right vault.

### 2.3 Endpoints

None.

### 2.4 Code changes

**Modify** [services/backend/src/backend_app/token_vault.py:14-130](../../services/backend/src/backend_app/token_vault.py):

```python
class TokenVault(ABC):
    @abstractmethod
    def encrypt(self, plaintext: str) -> str: ...
    @abstractmethod
    def decrypt(self, ciphertext: str) -> str: ...

class LocalTokenVault(TokenVault):
    """Dev only. Fernet symmetric. Refuses in production profiles."""
    ...

class ManagedSecretTokenVault(TokenVault, ABC):
    """Base for KMS-backed adapters."""
    @abstractmethod
    def _kms_encrypt(self, plaintext: bytes) -> tuple[bytes, str]: ...   # returns (ciphertext, key_id)
    @abstractmethod
    def _kms_decrypt(self, ciphertext: bytes) -> bytes: ...

class AwsKmsTokenVault(ManagedSecretTokenVault):
    """boto3 KMS client; key_id from env."""
    ...
```

**New** `services/backend/src/backend_app/token_vault_metrics.py`:

- `token_vault_encrypt_total{backend, result}`
- `token_vault_decrypt_total{backend, result}`
- `token_vault_kms_latency_seconds{backend, op}`
- `token_vault_cache_hit_ratio{backend}`

**Factory update:**

```python
class TokenVaultFactory:
    @staticmethod
    def create(profile: DeploymentFeatureToggles) -> TokenVault:
        backend = os.getenv("MCP_TOKEN_VAULT_BACKEND", "local")
        if backend == "local":
            if profile.require_kms_token_vault:
                raise RuntimeError("local token vault forbidden by deployment profile")
            return LocalTokenVault(...)
        if backend == "aws_kms":
            return AwsKmsTokenVault(key_id=os.environ["MCP_TOKEN_VAULT_KMS_KEY_ID"])
        if backend in ("gcp_kms", "azure_kv", "hashicorp_vault"):
            raise NotImplementedError(f"{backend} adapter ships in follow-up PR")
        raise ValueError(f"unknown MCP_TOKEN_VAULT_BACKEND: {backend}")
```

**Caching:** internal `_DecryptCache` with `(ttl=300, max_size=10000)`; disabled when `profile.audit_every_decrypt` (new toggle, default true for self_hosted).

**Rotation helper script** `services/backend/scripts/rotate_token_vault.py`:

- Iterate `mcp_auth_connections` (paginated) where `kms_key_id != $new_key OR kms_key_id IS NULL`.
- Decrypt with appropriate vault (legacy Fernet or old key).
- Re-encrypt with new vault/key.
- UPDATE in batches.
- Idempotent; resumable.

**Dependency:** `boto3` under `[kms-aws]` extras in `services/backend/requirements.txt` so the base image stays slim.

### 2.5 Trust model & failure semantics

- KMS unavailable → encrypt raises immediately (fail-closed write).
- Decrypt: prefer cache; on miss, KMS call; on KMS failure, raise.
- Cache TTL bounded so revoked KMS access propagates within 5min.
- No plaintext token ever logged. Test asserts.
- Per-row `kms_key_id` enables rotation: decrypt uses the row's key, not env var.

### 2.6 Tenant isolation

N/A — vault is global. (Per-tenant KMS keys are an enterprise feature for a follow-up PR; the schema column allows it.)

### 2.7 Observability

- Metrics above.
- KMS API calls visible in customer's KMS audit log (CloudTrail for AWS) — that's the canonical audit, not us.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] `MCP_TOKEN_VAULT_BACKEND=aws_kms` round-trip: encrypt → decrypt returns original.
- [ ] KMS unavailable → write raises typed error.
- [ ] Read cache serves recent decrypts; misses go to KMS.
- [ ] Profile `single_tenant_managed` rejects `MCP_TOKEN_VAULT_BACKEND=local`.
- [ ] Rotation script: encrypt with key A, rotate to key B, decrypt still works for old ciphertexts (uses per-row `kms_key_id`); new writes use key B.
- [ ] Existing Fernet ciphertexts continue decryptable until rotation script runs.

### 3.2 Test plan

**Unit (botocore stubber for fake KMS):**

- Round-trip.
- 5xx from KMS → write raises.
- Cache hit reduces KMS calls.
- Plaintext never appears in `caplog`.

**Integration:**

- Insert encrypted token via `LocalTokenVault`; switch to `AwsKmsTokenVault`; rotation script re-encrypts; reads work seamlessly.
- Profile rejection in production (`single_tenant_managed` + `local` → boot fails per C1).

### 3.3 Compliance evidence produced

- BYOK / customer-managed KMS supported for both managed and self-hosted profiles.
- Rotation procedure documented + tested.
- No plaintext token logged.

### 3.4 Rollout plan

- Adapter framework additive; default `local` keeps dev unchanged.
- SaaS production: schedule maintenance window for `rotate_token_vault.py`.
- Single-tenant deploys: customer provisions KMS key before first boot; documented in `docs/security/key-rotation.md`.

### 3.5 Backout plan

Set `MCP_TOKEN_VAULT_BACKEND=local`. Existing KMS-encrypted rows fail to decrypt — accept downtime to restore data via reverse rotation.

### 3.6 Definition of done

- [ ] Migration 0012 (kms_key_id column) applied.
- [ ] AwsKmsTokenVault implemented + tested.
- [ ] Rotation script tested.
- [ ] `boto3` under extras.
- [ ] `docs/security/key-rotation.md` written with steps for each backend.
- [ ] Metrics dashboarded.

---

## 4. Critical files

- New: `services/backend/migrations/0012_token_vault_key_id.sql` (+ rollback)
- Modify: [services/backend/src/backend_app/token_vault.py](../../services/backend/src/backend_app/token_vault.py)
- New: `services/backend/src/backend_app/token_vault_metrics.py`
- New: `services/backend/scripts/rotate_token_vault.py`
- Modify: `services/backend/requirements.txt` — add `boto3` under `[kms-aws]`.
- New: `docs/security/key-rotation.md`
