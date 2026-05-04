# KMS Key Rotation — MCP Token Vault

This runbook covers rotating the customer-managed encryption key (CMK) that
protects MCP OAuth tokens at rest. It applies to the C6 token-vault adapter
framework (`services/backend/src/backend_app/token_vault.py`).

## When to rotate

| Trigger                               | Cadence                        | Rotation type                                                                            |
| ------------------------------------- | ------------------------------ | ---------------------------------------------------------------------------------------- |
| Routine compliance                    | Annually (FedRAMP / SOC2 ISMS) | New CMK + re-encrypt                                                                     |
| Key compromise suspected              | Immediate                      | New CMK + re-encrypt + revoke old key                                                    |
| KMS automatic rotation                | Annually (AWS CMK auto-rotate) | None — AWS rotates the key material under the same alias; ciphertexts decrypt seamlessly |
| Migrating from local Fernet to KMS    | One-time                       | New CMK + re-encrypt all rows                                                            |
| Switching providers (AWS → GCP, etc.) | One-time                       | New CMK + re-encrypt                                                                     |

AWS KMS auto-rotation is transparent and does **not** require running the
rotation script — AWS keeps every prior key version inside the CMK and
chooses the right one at decrypt time. The script is for operator-driven
re-keying (new CMK, new alias) where the underlying ciphertext blob has to
change.

## Prerequisites

1. New CMK provisioned in the target KMS with the backend service IAM role
   granted `kms:Encrypt` and `kms:Decrypt`.
2. Network path from the backend to the new KMS endpoint validated.
3. Maintenance window scheduled — the rotation script holds a row-level
   `UPDATE` lock per row but does not block reads.
4. Backup of `mcp_auth_connections` taken (per the deployment's standard
   backup runbook).

## AWS KMS

```bash
# 1. Verify the new CMK and IAM grants.
aws kms describe-key --key-id alias/prod-mcp-cmk-v2

# 2. Run rotation.  The script uses MCP_TOKEN_VAULT_BACKEND=aws_kms; pass
#    the new key alias as --target-key-id. The source vault decrypts
#    whatever's already in the row (legacy Fernet, current CMK, or any
#    prior CMK still granted to the role).
BACKEND_DATABASE_URL='postgres://…' \
MCP_TOKEN_VAULT_BACKEND=aws_kms \
MCP_TOKEN_VAULT_KMS_KEY_ID=alias/prod-mcp-cmk-v2 \
.venv/bin/python scripts/rotate_token_vault.py rotate \
    --target-key-id alias/prod-mcp-cmk-v2 \
    --batch-size 200

# 3. After completion, flip MCP_TOKEN_VAULT_KMS_KEY_ID in deployed config
#    so all NEW writes use the v2 key.
# 4. Once you've verified zero v1-encrypted rows remain
#    (SELECT COUNT(*) FROM mcp_auth_connections WHERE kms_key_id != 'alias/prod-mcp-cmk-v2'),
#    you can remove the v1 key grant from the IAM role.
```

To migrate from local Fernet (legacy dev / pilot deploys) to AWS KMS:

```bash
# Source vault = local Fernet (set explicitly so the script doesn't try to
# parse legacy ciphertexts as kms_v1 envelopes).
MCP_TOKEN_VAULT_LEGACY_BACKEND=local \
MCP_TOKEN_VAULT_BACKEND=aws_kms \
MCP_TOKEN_VAULT_KMS_KEY_ID=alias/prod-mcp-cmk \
MCP_TOKEN_VAULT_SECRET=<legacy-secret> \
.venv/bin/python scripts/rotate_token_vault.py rotate \
    --target-key-id alias/prod-mcp-cmk
```

## GCP KMS / Azure Key Vault / HashiCorp Vault

Adapters ship in follow-up PRs (C6a, C6b, C6c). The rotation procedure will
be identical: the script's source/target abstraction was deliberately
designed to be backend-agnostic.

## Verifying success

After rotation, every row should report the new key id:

```sql
SELECT kms_key_id, COUNT(*)
  FROM mcp_auth_connections
 GROUP BY kms_key_id;
```

Expected output: a single row with `kms_key_id = '<new alias>'` and the
total count. Any rows with NULL, the old alias, or a different alias
indicate either skipped failures (check the script's logs for skip count)
or new rows that arrived during rotation; re-run the script to mop them up.

## Backout

If the rotation script fails partway, the original ciphertexts are
unchanged for any row not yet committed. Re-run with the same arguments;
the `WHERE kms_key_id IS DISTINCT FROM <target>` clause skips already-
rotated rows.

If the target KMS is unreachable after rotation, no decrypts will succeed.
Restore the old key id in `MCP_TOKEN_VAULT_KMS_KEY_ID` and ensure the
backend has IAM grants on the prior CMK; reads will work again because the
per-row `kms_key_id` column drives decrypt routing for ManagedSecret
adapters that preserve key identity in their ciphertext envelope (current
AWS adapter does).

## Cache invalidation

The token vault uses a 5-minute in-process decrypt cache (disabled under
`single_tenant_self_hosted` per the deployment profile). After a CMK
revocation you must wait for the cache TTL to expire OR roll the backend
pods. There is no manual cache flush API in this PR.

## Audit trail

Every KMS Decrypt call appears in CloudTrail (or the equivalent KMS audit
log for non-AWS adapters). That is the canonical audit trail for
"who decrypted what when"; the backend service deliberately does not
duplicate it.
