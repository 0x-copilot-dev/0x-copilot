-- 0013_audit_export_verification_sampling — D7/D12 safe export catalog + outcomes.
--
-- No bundle body is retained here.  V2 rows are the existing safe projection;
-- legacy v1 rows retain only signed envelope metadata and a payload digest.  A
-- worker rehydrates v1 payloads in memory from the authoritative event stream.

CREATE TABLE IF NOT EXISTS runtime_audit_export_verification_manifests (
    org_id          text NOT NULL,
    bundle_ref      text NOT NULL,
    bundle_digest   text NOT NULL,
    run_id          text NOT NULL,
    format          text NOT NULL,
    legacy_version_key text,
    -- Keep the exact exported spelling as it participates in the signed
    -- bundle digest (for example, an older bundle may use a trailing ``Z``).
    generated_at_wire text NOT NULL,
    generated_at    timestamptz NOT NULL,
    captured_at     timestamptz NOT NULL,
    key_id          text,
    head_hash       text NOT NULL,
    receipt_digest  text,
    rows_json       jsonb NOT NULL,
    PRIMARY KEY (org_id, bundle_ref, bundle_digest),
    CONSTRAINT runtime_audit_export_verification_manifest_ref_check
        CHECK (bundle_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$'),
    CONSTRAINT runtime_audit_export_verification_manifest_digest_check
        CHECK (bundle_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_audit_export_verification_manifest_head_check
        CHECK (head_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_audit_export_verification_manifest_receipt_check
        CHECK (receipt_digest IS NULL OR receipt_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_audit_export_verification_manifest_format_check
        CHECK (format IN ('receipt_v1', 'receipt_v2')),
    CONSTRAINT runtime_audit_export_verification_manifest_version_key_check
        CHECK (
            (format = 'receipt_v1' AND legacy_version_key IN ('export_version', 'bundle_version'))
            OR (format = 'receipt_v2' AND legacy_version_key IS NULL)
        ),
    CONSTRAINT runtime_audit_export_verification_manifest_rows_check
        CHECK (jsonb_typeof(rows_json) = 'array' AND jsonb_array_length(rows_json) BETWEEN 1 AND 10000)
);

CREATE INDEX IF NOT EXISTS idx_runtime_audit_export_verification_scan
    ON runtime_audit_export_verification_manifests (
        captured_at ASC, org_id ASC, bundle_ref ASC
    );

CREATE TABLE IF NOT EXISTS runtime_audit_export_verification_outcomes (
    org_id          text NOT NULL,
    bundle_ref      text NOT NULL,
    bundle_digest   text NOT NULL,
    format          text NOT NULL,
    outcome         text NOT NULL,
    failure_class   text NOT NULL,
    broken_at_seq   bigint,
    sampled_at      timestamptz NOT NULL,
    attempts        integer NOT NULL DEFAULT 1,
    PRIMARY KEY (org_id, bundle_ref, bundle_digest),
    FOREIGN KEY (org_id, bundle_ref, bundle_digest)
        REFERENCES runtime_audit_export_verification_manifests (org_id, bundle_ref, bundle_digest)
        ON DELETE CASCADE,
    CONSTRAINT runtime_audit_export_verification_outcome_format_check
        CHECK (format IN ('receipt_v1', 'receipt_v2')),
    CONSTRAINT runtime_audit_export_verification_outcome_value_check
        CHECK (outcome IN ('verified', 'failed', 'unavailable')),
    CONSTRAINT runtime_audit_export_verification_outcome_failure_check
        CHECK (failure_class IN (
            'none', 'chain_invalid', 'bundle_malformed', 'source_mismatch',
            'source_unavailable', 'signing_material_unavailable',
            'key_version_unavailable', 'internal_error'
        )),
    CONSTRAINT runtime_audit_export_verification_outcome_consistency_check
        CHECK (
            (outcome = 'verified' AND failure_class = 'none')
            OR (outcome <> 'verified' AND failure_class <> 'none')
        ),
    CONSTRAINT runtime_audit_export_verification_outcome_attempts_check
        CHECK (attempts >= 1),
    CONSTRAINT runtime_audit_export_verification_outcome_broken_check
        CHECK (broken_at_seq IS NULL OR broken_at_seq >= 1)
);

-- One worker-owned cursor and lease.  The source is fixed so an accidental
-- second job cannot reuse/overwrite this safe scan state.
CREATE TABLE IF NOT EXISTS runtime_audit_export_verification_scan_state (
    source              text PRIMARY KEY,
    after_captured_at   timestamptz,
    after_org_id        text,
    after_bundle_ref    text,
    lease_owner_id      text,
    lease_expires_at    timestamptz,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT runtime_audit_export_verification_scan_source_check
        CHECK (source = 'audit_export_verification'),
    CONSTRAINT runtime_audit_export_verification_scan_cursor_check
        CHECK (
            (after_captured_at IS NULL AND after_org_id IS NULL AND after_bundle_ref IS NULL)
            OR
            (after_captured_at IS NOT NULL AND after_org_id IS NOT NULL AND after_bundle_ref IS NOT NULL)
        ),
    CONSTRAINT runtime_audit_export_verification_scan_lease_check
        CHECK (
            (lease_owner_id IS NULL AND lease_expires_at IS NULL)
            OR
            (lease_owner_id IS NOT NULL AND lease_expires_at IS NOT NULL)
        )
);
