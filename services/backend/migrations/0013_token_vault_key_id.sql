-- C6: KMS-backed token vault.
--
-- Records which CMK encrypted each row's tokens so the rotation script
-- can scan WHERE kms_key_id != $new AND re-encrypt without re-decrypting
-- the entire table. The decrypt path also uses this column to pick the
-- right key when an operator runs multiple CMKs in parallel during
-- rotation; for AWS KMS specifically the ciphertext blob is
-- self-describing, but the column is still authoritative for queryability
-- and for non-AWS adapters that ship in follow-up PRs.
--
-- Existing Fernet rows have NULL here; the LocalTokenVault decrypts them
-- transparently until the rotation script re-writes them under a CMK.
ALTER TABLE mcp_auth_connections
    ADD COLUMN IF NOT EXISTS kms_key_id TEXT;

CREATE INDEX IF NOT EXISTS idx_mcp_auth_connections_kms_key_id
    ON mcp_auth_connections (kms_key_id)
    WHERE kms_key_id IS NOT NULL;
