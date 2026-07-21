-- 0001_baseline — the complete backend schema (pre-launch squash, 2026-07-21).
--
-- Squashed from migrations 0001..0040 while the product had ZERO installed
-- deployments (migration history serves installed databases; there were
-- none). The old files remain in git history. Two corrections are baked in
-- rather than layered on:
--   * principal/tenant separation (ADR 0001) is native: users and every
--     auth-identity edge carry a NOT NULL principal_id.
--   * local_accounts — the "Use locally, no account" entry method is a
--     first-class identity edge like wallet/oidc/saml. At most ONE row per
--     deployment (the device account, D4-A); multi-tenant web deployments
--     never insert one.
-- Applying to a database that carries the old migration history is not
-- supported: wipe dev databases once (nothing was shipped).
--
-- Generated from pg_dump of the fully-migrated schema and verified
-- equivalent by catalog diff; future migrations start at 0002.

CREATE EXTENSION IF NOT EXISTS citext WITH SCHEMA public;

CREATE FUNCTION audit_immutable_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'audit log is append-only; % on % rejected',
    TG_OP, TG_TABLE_NAME;
END;
$$;

CREATE TABLE account_lockouts (
    lockout_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    locked_at timestamp with time zone NOT NULL,
    lock_reason text NOT NULL,
    auto_unlock_at timestamp with time zone,
    unlocked_at timestamp with time zone,
    unlocked_by_user_id text,
    unlock_reason text
);

CREATE TABLE account_merges (
    merge_id text NOT NULL,
    survivor_org_id text NOT NULL,
    survivor_user_id text NOT NULL,
    absorbed_org_id text NOT NULL,
    absorbed_user_id text NOT NULL,
    state text DEFAULT 'pending'::text NOT NULL,
    proof_ref text NOT NULL,
    error text,
    counts jsonb DEFAULT '{}'::jsonb NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT account_merges_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'backend_done'::text, 'runtime_done'::text, 'sessions_revoked'::text, 'completed'::text])))
);

CREATE TABLE adapter_candidates (
    candidate_id text NOT NULL,
    tenant_id text NOT NULL,
    submitter_user_id text NOT NULL,
    scheme text NOT NULL,
    version integer NOT NULL,
    layout text NOT NULL,
    storage_key text NOT NULL,
    source_digest text NOT NULL,
    source_bytes integer NOT NULL,
    harvest_metrics jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT adapter_candidates_layout_check CHECK ((layout = ANY (ARRAY['form'::text, 'table'::text, 'kanban'::text, 'definition-list'::text]))),
    CONSTRAINT adapter_candidates_source_bytes_check CHECK ((source_bytes > 0)),
    CONSTRAINT adapter_candidates_source_digest_check CHECK ((char_length(source_digest) = 64)),
    CONSTRAINT adapter_candidates_status_check CHECK ((status = ANY (ARRAY['submitted'::text, 'in-review'::text, 'changes-requested'::text, 'approved'::text, 'rejected'::text]))),
    CONSTRAINT adapter_candidates_version_check CHECK ((version >= 1))
);

CREATE TABLE adapter_registry_audit_events (
    audit_id text NOT NULL,
    tenant_id text NOT NULL,
    actor_user_id text NOT NULL,
    candidate_id text,
    promoted_id text,
    action text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    seq bigint,
    prev_hash bytea,
    signature bytea,
    key_version integer,
    CONSTRAINT adapter_registry_audit_events_action_check CHECK (((char_length(action) >= 1) AND (char_length(action) <= 64)))
);

CREATE TABLE adapter_reviews (
    review_id text NOT NULL,
    candidate_id text NOT NULL,
    reviewer_user_id text NOT NULL,
    reviewer_org_id text NOT NULL,
    action text NOT NULL,
    notes text,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT adapter_reviews_action_check CHECK ((action = ANY (ARRAY['approve'::text, 'reject'::text, 'request-changes'::text])))
);

CREATE TABLE api_keys (
    id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    label text NOT NULL,
    key_prefix text NOT NULL,
    secret_hash text NOT NULL,
    scopes jsonb DEFAULT '[]'::jsonb NOT NULL,
    last_used_at timestamp with time zone,
    last_used_ip text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    rotated_from_id text,
    revoked_at timestamp with time zone,
    kind text DEFAULT 'personal'::text NOT NULL,
    CONSTRAINT api_keys_kind_check CHECK ((kind = ANY (ARRAY['personal'::text, 'workspace'::text])))
);

CREATE TABLE auth_provider_domains (
    domain citext NOT NULL,
    org_id text NOT NULL,
    provider_id text NOT NULL,
    sso_enforced boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_user_id text,
    deleted_at timestamp with time zone
);

CREATE TABLE auth_providers (
    provider_id text NOT NULL,
    org_id text NOT NULL,
    kind text NOT NULL,
    display_name text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    encrypted_client_secret text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT auth_providers_kind_check CHECK ((kind = ANY (ARRAY['local'::text, 'oidc'::text, 'saml'::text, 'scim'::text])))
);

CREATE TABLE identity_audit_events (
    audit_id text NOT NULL,
    org_id text NOT NULL,
    actor_user_id text,
    subject_user_id text,
    action text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    request_ip text,
    user_agent text,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE identity_policies (
    org_id text NOT NULL,
    local_password_enabled boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    mfa_required boolean DEFAULT false NOT NULL,
    step_up_window_seconds integer DEFAULT 300 NOT NULL,
    scim_required boolean DEFAULT false NOT NULL
);

CREATE TABLE invitations (
    invite_id text NOT NULL,
    org_id text NOT NULL,
    email citext NOT NULL,
    role_id text NOT NULL,
    token_hash text NOT NULL,
    token_prefix text NOT NULL,
    created_by_user_id text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone,
    accepted_user_id text,
    revoked_at timestamp with time zone,
    revoked_by_user_id text
);

CREATE TABLE local_accounts (
    local_account_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    principal_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE local_credentials (
    credential_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    password_hash text NOT NULL,
    password_set_at timestamp with time zone NOT NULL,
    must_rotate_at timestamp with time zone,
    last_used_at timestamp with time zone,
    previous_hashes jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone
);

CREATE TABLE lockout_policies (
    policy_id text NOT NULL,
    org_id text NOT NULL,
    enforce_lockout boolean DEFAULT false NOT NULL,
    max_failures integer DEFAULT 5 NOT NULL,
    failure_window_seconds integer DEFAULT 300 NOT NULL,
    lockout_duration_seconds integer DEFAULT 900 NOT NULL,
    permanent_after_n_lockouts integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE login_attempts (
    attempt_id text NOT NULL,
    org_id text,
    email_attempted citext,
    user_id text,
    auth_kind text NOT NULL,
    outcome text NOT NULL,
    ip text,
    user_agent text,
    failure_reason text,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT login_attempts_auth_kind_check CHECK ((auth_kind = ANY (ARRAY['local'::text, 'oidc'::text, 'saml'::text, 'mfa'::text, 'scim_token'::text, 'api_key'::text, 'magic_link'::text, 'siwe'::text]))),
    CONSTRAINT login_attempts_outcome_check CHECK ((outcome = ANY (ARRAY['success'::text, 'bad_password'::text, 'unknown_user'::text, 'locked_out'::text, 'mfa_failed'::text, 'provider_rejected'::text, 'magic_link_requested'::text, 'magic_link_consumed'::text, 'invalid_token'::text, 'expired_token'::text, 'consumed_token'::text, 'rate_limited'::text, 'workspace_picker_issued'::text, 'workspace_selected'::text])))
);

CREATE TABLE magic_link_tokens (
    token_id text NOT NULL,
    org_id text,
    user_id text NOT NULL,
    email_lower citext NOT NULL,
    token_hash text NOT NULL,
    candidate_orgs jsonb DEFAULT '[]'::jsonb NOT NULL,
    return_to text,
    requested_ip text,
    requested_ua text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    consumed_session_id text
);

CREATE TABLE mcp_audit_events (
    audit_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    server_id text NOT NULL,
    action text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    seq bigint,
    prev_hash bytea,
    signature bytea,
    key_version smallint
);

CREATE TABLE mcp_auth_connections (
    connection_id text NOT NULL,
    server_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    encrypted_access_token text NOT NULL,
    encrypted_refresh_token text,
    expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    kms_key_id text
);

CREATE TABLE mcp_auth_sessions (
    session_id text NOT NULL,
    server_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    state text NOT NULL,
    code_verifier text NOT NULL,
    redirect_uri text NOT NULL,
    auth_url text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE mcp_servers (
    server_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    name text NOT NULL,
    display_name text NOT NULL,
    url text NOT NULL,
    transport text NOT NULL,
    auth_mode text NOT NULL,
    auth_state text NOT NULL,
    health text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    required_scopes jsonb DEFAULT '[]'::jsonb NOT NULL,
    last_discovery jsonb DEFAULT '{}'::jsonb NOT NULL,
    oauth_client jsonb,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    logo_url text,
    brand_color text,
    scopes_summary text,
    default_scopes jsonb DEFAULT '[]'::jsonb NOT NULL,
    admin_managed boolean DEFAULT false NOT NULL,
    description text DEFAULT ''::text NOT NULL
);

CREATE TABLE mfa_challenges (
    challenge_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    kind text NOT NULL,
    nonce text NOT NULL,
    expected_factor_id text,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT mfa_challenges_kind_check CHECK ((kind = ANY (ARRAY['totp'::text, 'webauthn'::text, 'recovery'::text])))
);

CREATE TABLE mfa_factors (
    factor_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    kind text NOT NULL,
    display_name text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    enrolled_at timestamp with time zone NOT NULL,
    last_used_at timestamp with time zone,
    disabled_at timestamp with time zone,
    CONSTRAINT mfa_factors_kind_check CHECK ((kind = ANY (ARRAY['totp'::text, 'webauthn'::text])))
);

CREATE TABLE mfa_recovery_codes (
    code_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    code_hash text NOT NULL,
    consumed_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE notification_preferences (
    user_id text NOT NULL,
    event_kind text NOT NULL,
    channel text NOT NULL,
    enabled boolean NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT notification_preferences_channel_check CHECK ((channel = ANY (ARRAY['in_app'::text, 'email'::text, 'push'::text]))),
    CONSTRAINT notification_preferences_event_kind_check CHECK ((event_kind = ANY (ARRAY['long_task_finished'::text, 'approval_requested'::text, 'mention'::text, 'connector_error'::text, 'weekly_digest'::text, 'product_updates'::text])))
);

CREATE TABLE notification_quiet_hours (
    user_id text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    from_local time without time zone DEFAULT '20:00:00'::time without time zone NOT NULL,
    to_local time without time zone DEFAULT '08:00:00'::time without time zone NOT NULL,
    tz text DEFAULT 'UTC'::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE oidc_authentications (
    auth_id text NOT NULL,
    org_id text NOT NULL,
    provider_id text NOT NULL,
    state text NOT NULL,
    nonce text NOT NULL,
    code_verifier text NOT NULL,
    redirect_uri text NOT NULL,
    return_to text,
    requested_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    ip text,
    user_agent text,
    link_org_id text,
    link_user_id text,
    link_confirm_merge boolean DEFAULT false NOT NULL
);

COMMENT ON COLUMN oidc_authentications.link_org_id IS 'Account-linking: the authenticated caller''s org at link-start (NULL = plain sign-in).';

COMMENT ON COLUMN oidc_authentications.link_user_id IS 'Account-linking: the authenticated caller''s user at link-start (NULL = plain sign-in).';

CREATE TABLE oidc_identities (
    identity_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    provider_id text NOT NULL,
    subject text NOT NULL,
    email_at_link text,
    linked_at timestamp with time zone NOT NULL,
    unlinked_at timestamp with time zone,
    claims_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    principal_id text NOT NULL
);

CREATE TABLE oidc_jwks_cache (
    cache_id text NOT NULL,
    provider_id text NOT NULL,
    jwks jsonb NOT NULL,
    fetched_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL
);

CREATE TABLE oidc_refresh_tokens (
    token_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    provider_id text NOT NULL,
    encrypted_refresh_token text NOT NULL,
    scope jsonb DEFAULT '[]'::jsonb NOT NULL,
    expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone
);

CREATE TABLE organization_members (
    member_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    joined_at timestamp with time zone NOT NULL,
    invited_by_user_id text,
    removed_at timestamp with time zone,
    source text NOT NULL,
    CONSTRAINT organization_members_source_check CHECK ((source = ANY (ARRAY['local'::text, 'oidc'::text, 'saml'::text, 'scim'::text, 'bootstrap'::text, 'invite'::text, 'siwe'::text])))
);

CREATE TABLE organizations (
    org_id text NOT NULL,
    display_name text NOT NULL,
    slug text NOT NULL,
    deployment_kind text NOT NULL,
    status text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT organizations_deployment_kind_check CHECK ((deployment_kind = ANY (ARRAY['saas'::text, 'single_tenant'::text]))),
    CONSTRAINT organizations_status_check CHECK ((status = ANY (ARRAY['active'::text, 'suspended'::text, 'deleted'::text])))
);

CREATE TABLE password_policies (
    policy_id text NOT NULL,
    org_id text NOT NULL,
    min_length integer DEFAULT 12 NOT NULL,
    require_upper boolean DEFAULT true NOT NULL,
    require_lower boolean DEFAULT true NOT NULL,
    require_digit boolean DEFAULT true NOT NULL,
    require_symbol boolean DEFAULT false NOT NULL,
    rotation_days integer,
    reuse_window integer DEFAULT 5 NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE password_reset_tokens (
    token_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    token_hash text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    request_ip text
);

CREATE TABLE principals (
    principal_id text NOT NULL,
    display_name text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    absorbed_into_principal_id text,
    merged_at timestamp with time zone
);

CREATE TABLE privacy_settings (
    org_id text NOT NULL,
    user_id text,
    training_opt_out boolean DEFAULT true NOT NULL,
    region text,
    retention_days integer,
    share_metadata boolean DEFAULT true NOT NULL,
    memory_enabled boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id text,
    CONSTRAINT privacy_settings_region_check CHECK ((region = ANY (ARRAY['us-east-1'::text, 'eu-west-1'::text, 'ap-northeast-1'::text]))),
    CONSTRAINT privacy_settings_retention_days_check CHECK (((retention_days IS NULL) OR (retention_days > 0)))
);

CREATE TABLE promoted_adapters (
    promoted_id text NOT NULL,
    scheme text NOT NULL,
    version integer NOT NULL,
    schema_version integer NOT NULL,
    layout text NOT NULL,
    storage_key text NOT NULL,
    source_digest text NOT NULL,
    source_bytes integer NOT NULL,
    origin_tenant_id text NOT NULL,
    source_candidate_id text NOT NULL,
    promoted_by_user_id text NOT NULL,
    promoted_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT promoted_adapters_layout_check CHECK ((layout = ANY (ARRAY['form'::text, 'table'::text, 'kanban'::text, 'definition-list'::text]))),
    CONSTRAINT promoted_adapters_schema_version_check CHECK ((schema_version >= 1)),
    CONSTRAINT promoted_adapters_source_bytes_check CHECK ((source_bytes > 0)),
    CONSTRAINT promoted_adapters_source_digest_check CHECK ((char_length(source_digest) = 64)),
    CONSTRAINT promoted_adapters_version_check CHECK ((version >= 1))
);

CREATE TABLE provider_api_keys (
    org_id text NOT NULL,
    user_id text NOT NULL,
    provider text NOT NULL,
    encrypted_key text NOT NULL,
    key_hint text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT provider_api_keys_provider_check CHECK ((provider = ANY (ARRAY['openai'::text, 'anthropic'::text, 'google'::text, 'openrouter'::text])))
);

CREATE TABLE role_assignments (
    assignment_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    role_id text NOT NULL,
    granted_by_user_id text,
    granted_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    reason text
);

CREATE TABLE roles (
    role_id text NOT NULL,
    org_id text,
    name text NOT NULL,
    display_name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    is_system boolean DEFAULT false NOT NULL,
    permission_scopes jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT roles_system_or_org CHECK ((((is_system = true) AND (org_id IS NULL)) OR ((is_system = false) AND (org_id IS NOT NULL))))
);

CREATE TABLE saml_authentications (
    auth_id text NOT NULL,
    org_id text NOT NULL,
    provider_id text NOT NULL,
    request_id text,
    assertion_id text NOT NULL,
    relay_state text,
    status text NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    ip text,
    user_agent text,
    CONSTRAINT saml_authentications_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'consumed'::text, 'rejected'::text])))
);

CREATE TABLE saml_identities (
    identity_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    provider_id text NOT NULL,
    name_id text NOT NULL,
    name_id_format text NOT NULL,
    linked_at timestamp with time zone NOT NULL,
    unlinked_at timestamp with time zone,
    attributes_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    principal_id text NOT NULL
);

CREATE TABLE scim_external_ids (
    mapping_id text NOT NULL,
    org_id text NOT NULL,
    user_id text,
    group_id text,
    provider_id text NOT NULL,
    external_id text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT scim_external_ids_user_xor_group CHECK (((user_id IS NOT NULL) <> (group_id IS NOT NULL)))
);

CREATE TABLE scim_group_members (
    membership_id text NOT NULL,
    org_id text NOT NULL,
    group_id text NOT NULL,
    user_id text NOT NULL,
    added_at timestamp with time zone NOT NULL,
    removed_at timestamp with time zone
);

CREATE TABLE scim_groups (
    group_id text NOT NULL,
    org_id text NOT NULL,
    display_name text NOT NULL,
    external_id text,
    mapped_role_id text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone
);

CREATE TABLE scim_tokens (
    token_id text NOT NULL,
    org_id text NOT NULL,
    provider_id text NOT NULL,
    token_hash text NOT NULL,
    token_prefix text NOT NULL,
    created_by_user_id text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    last_used_at timestamp with time zone
);

CREATE TABLE sessions (
    session_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    token_hash text NOT NULL,
    roles jsonb DEFAULT '[]'::jsonb NOT NULL,
    permission_scopes jsonb DEFAULT '[]'::jsonb NOT NULL,
    connector_scopes jsonb DEFAULT '{}'::jsonb NOT NULL,
    auth_provider_id text,
    mfa_satisfied_at timestamp with time zone,
    client_ip text,
    user_agent text,
    device_label text,
    created_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    revocation_reason text
);

CREATE TABLE siem_export_cursors (
    exporter_name text NOT NULL,
    source text NOT NULL,
    last_event_id text,
    last_processed_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE siem_export_dead_letters (
    id text NOT NULL,
    exporter_name text NOT NULL,
    source text NOT NULL,
    event_id text NOT NULL,
    payload_json jsonb NOT NULL,
    last_error text NOT NULL,
    attempts integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT siem_export_dead_letters_attempts_check CHECK ((attempts >= 1))
);

CREATE TABLE siem_exporter_controls (
    exporter_name text NOT NULL,
    paused_at timestamp with time zone,
    replay_from_id text,
    replay_to_id text,
    replay_requested_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id text
);

CREATE TABLE siwe_nonces (
    nonce_id text NOT NULL,
    nonce text NOT NULL,
    address text NOT NULL,
    chain_id bigint NOT NULL,
    issued_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    ip text,
    user_agent text
);

CREATE TABLE skill_audit_events (
    audit_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    skill_id text NOT NULL,
    action text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    seq bigint,
    prev_hash bytea,
    signature bytea,
    key_version smallint
);

CREATE TABLE skills (
    skill_id text NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    name text NOT NULL,
    display_name text NOT NULL,
    description text NOT NULL,
    markdown text NOT NULL,
    virtual_path text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    scope text NOT NULL,
    source_type text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    allowed_tools jsonb DEFAULT '[]'::jsonb NOT NULL,
    compatibility jsonb DEFAULT '[]'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE tenant_adapter_settings (
    tenant_id text NOT NULL,
    opted_out boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id text
);

CREATE TABLE tenant_settings (
    tenant_id text NOT NULL,
    namespace text NOT NULL,
    settings jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id text,
    CONSTRAINT tenant_settings_namespace_check CHECK ((namespace = ANY (ARRAY['notifications'::text, 'security.webhooks'::text])))
);

CREATE TABLE todo_audit_events (
    audit_id text NOT NULL,
    tenant_id text NOT NULL,
    actor_user_id text NOT NULL,
    action text NOT NULL,
    target_kind text DEFAULT 'todo'::text NOT NULL,
    target_id text NOT NULL,
    before_state jsonb,
    after_state jsonb,
    correlation_id text,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    seq bigint,
    prev_hash bytea,
    signature bytea,
    key_version integer
);

CREATE TABLE todo_series (
    id text NOT NULL,
    tenant_id text NOT NULL,
    owner_user_id text NOT NULL,
    rule text NOT NULL,
    spec text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    ends_at timestamp with time zone,
    last_materialized_due timestamp with time zone
);

CREATE TABLE todos (
    id text NOT NULL,
    tenant_id text NOT NULL,
    owner_user_id text NOT NULL,
    project_id text,
    text text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    priority text DEFAULT 'med'::text NOT NULL,
    due timestamp with time zone,
    source jsonb DEFAULT '{"kind": "user"}'::jsonb NOT NULL,
    parent_id text,
    sort_index_within_parent double precision,
    recurrence jsonb,
    series_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    deleted_at timestamp with time zone,
    CONSTRAINT todos_priority_check CHECK ((priority = ANY (ARRAY['low'::text, 'med'::text, 'high'::text]))),
    CONSTRAINT todos_status_check CHECK ((status = ANY (ARRAY['open'::text, 'done'::text]))),
    CONSTRAINT todos_text_check CHECK (((char_length(text) >= 1) AND (char_length(text) <= 2000)))
);

CREATE TABLE tool_use_policies (
    org_id text NOT NULL,
    user_id text,
    kind text NOT NULL,
    mode text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id text,
    CONSTRAINT tool_use_policies_kind_check CHECK ((kind = ANY (ARRAY['read'::text, 'write'::text, 'destructive'::text]))),
    CONSTRAINT tool_use_policies_mode_check CHECK ((mode = ANY (ARRAY['auto'::text, 'ask'::text, 'require'::text, 'block'::text])))
);

CREATE TABLE totp_secrets (
    secret_id text NOT NULL,
    factor_id text NOT NULL,
    encrypted_secret text NOT NULL,
    last_step bigint,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE user_avatars (
    user_id text NOT NULL,
    org_id text NOT NULL,
    content_type text NOT NULL,
    bytes bytea NOT NULL,
    size_bytes integer NOT NULL,
    etag text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT user_avatars_content_type_check CHECK ((content_type = ANY (ARRAY['image/png'::text, 'image/jpeg'::text, 'image/webp'::text]))),
    CONSTRAINT user_avatars_size_bytes_check CHECK (((size_bytes >= 1) AND (size_bytes <= 204800)))
);

CREATE TABLE user_preferences (
    user_id text NOT NULL,
    org_id text NOT NULL,
    preferences jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE user_profiles (
    user_id text NOT NULL,
    org_id text NOT NULL,
    title text,
    timezone text,
    locale text,
    working_hours jsonb,
    avatar_url text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    bio text
);

CREATE TABLE users (
    user_id text NOT NULL,
    org_id text NOT NULL,
    primary_email citext NOT NULL,
    email_verified_at timestamp with time zone,
    display_name text NOT NULL,
    status text NOT NULL,
    is_service_account boolean DEFAULT false NOT NULL,
    last_seen_at timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone,
    scim_external_id text,
    absorbed_into_user_id text,
    merged_at timestamp with time zone,
    principal_id text NOT NULL,
    CONSTRAINT users_status_check CHECK ((status = ANY (ARRAY['active'::text, 'disabled'::text, 'pending_invite'::text])))
);

COMMENT ON COLUMN users.absorbed_into_user_id IS 'Account-merge lineage: the survivor user this account was absorbed into (NULL = never merged).';

CREATE TABLE wallet_identities (
    wallet_id text NOT NULL,
    address citext NOT NULL,
    org_id text NOT NULL,
    user_id text NOT NULL,
    chain_id bigint NOT NULL,
    created_at timestamp with time zone NOT NULL,
    principal_id text NOT NULL
);

CREATE TABLE webauthn_credentials (
    credential_id text NOT NULL,
    factor_id text NOT NULL,
    credential_id_b64 text NOT NULL,
    public_key_cose bytea NOT NULL,
    sign_count bigint DEFAULT 0 NOT NULL,
    transports jsonb DEFAULT '[]'::jsonb NOT NULL,
    aaguid text,
    attestation_format text NOT NULL,
    rp_id text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    last_used_at timestamp with time zone
);

ALTER TABLE ONLY account_lockouts
    ADD CONSTRAINT account_lockouts_pkey PRIMARY KEY (lockout_id);

ALTER TABLE ONLY account_merges
    ADD CONSTRAINT account_merges_pkey PRIMARY KEY (merge_id);

ALTER TABLE ONLY adapter_candidates
    ADD CONSTRAINT adapter_candidates_pkey PRIMARY KEY (candidate_id);

ALTER TABLE ONLY adapter_registry_audit_events
    ADD CONSTRAINT adapter_registry_audit_events_pkey PRIMARY KEY (audit_id);

ALTER TABLE ONLY adapter_reviews
    ADD CONSTRAINT adapter_reviews_pkey PRIMARY KEY (review_id);

ALTER TABLE ONLY api_keys
    ADD CONSTRAINT api_keys_key_prefix_key UNIQUE (key_prefix);

ALTER TABLE ONLY api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);

ALTER TABLE ONLY auth_provider_domains
    ADD CONSTRAINT auth_provider_domains_pkey PRIMARY KEY (domain, org_id, provider_id);

ALTER TABLE ONLY auth_providers
    ADD CONSTRAINT auth_providers_pkey PRIMARY KEY (provider_id);

ALTER TABLE ONLY identity_audit_events
    ADD CONSTRAINT identity_audit_events_pkey PRIMARY KEY (audit_id);

ALTER TABLE ONLY identity_policies
    ADD CONSTRAINT identity_policies_pkey PRIMARY KEY (org_id);

ALTER TABLE ONLY invitations
    ADD CONSTRAINT invitations_pkey PRIMARY KEY (invite_id);

ALTER TABLE ONLY local_accounts
    ADD CONSTRAINT local_accounts_pkey PRIMARY KEY (local_account_id);

ALTER TABLE ONLY local_credentials
    ADD CONSTRAINT local_credentials_pkey PRIMARY KEY (credential_id);

ALTER TABLE ONLY lockout_policies
    ADD CONSTRAINT lockout_policies_org_id_key UNIQUE (org_id);

ALTER TABLE ONLY lockout_policies
    ADD CONSTRAINT lockout_policies_pkey PRIMARY KEY (policy_id);

ALTER TABLE ONLY login_attempts
    ADD CONSTRAINT login_attempts_pkey PRIMARY KEY (attempt_id);

ALTER TABLE ONLY magic_link_tokens
    ADD CONSTRAINT magic_link_tokens_pkey PRIMARY KEY (token_id);

ALTER TABLE ONLY magic_link_tokens
    ADD CONSTRAINT magic_link_tokens_token_hash_key UNIQUE (token_hash);

ALTER TABLE ONLY mcp_audit_events
    ADD CONSTRAINT mcp_audit_events_pkey PRIMARY KEY (audit_id);

ALTER TABLE ONLY mcp_auth_connections
    ADD CONSTRAINT mcp_auth_connections_pkey PRIMARY KEY (connection_id);

ALTER TABLE ONLY mcp_auth_sessions
    ADD CONSTRAINT mcp_auth_sessions_pkey PRIMARY KEY (session_id);

ALTER TABLE ONLY mcp_auth_sessions
    ADD CONSTRAINT mcp_auth_sessions_state_key UNIQUE (state);

ALTER TABLE ONLY mcp_servers
    ADD CONSTRAINT mcp_servers_pkey PRIMARY KEY (server_id);

ALTER TABLE ONLY mfa_challenges
    ADD CONSTRAINT mfa_challenges_nonce_key UNIQUE (nonce);

ALTER TABLE ONLY mfa_challenges
    ADD CONSTRAINT mfa_challenges_pkey PRIMARY KEY (challenge_id);

ALTER TABLE ONLY mfa_factors
    ADD CONSTRAINT mfa_factors_pkey PRIMARY KEY (factor_id);

ALTER TABLE ONLY mfa_recovery_codes
    ADD CONSTRAINT mfa_recovery_codes_code_hash_key UNIQUE (code_hash);

ALTER TABLE ONLY mfa_recovery_codes
    ADD CONSTRAINT mfa_recovery_codes_pkey PRIMARY KEY (code_id);

ALTER TABLE ONLY notification_preferences
    ADD CONSTRAINT notification_preferences_pkey PRIMARY KEY (user_id, event_kind, channel);

ALTER TABLE ONLY notification_quiet_hours
    ADD CONSTRAINT notification_quiet_hours_pkey PRIMARY KEY (user_id);

ALTER TABLE ONLY oidc_authentications
    ADD CONSTRAINT oidc_authentications_pkey PRIMARY KEY (auth_id);

ALTER TABLE ONLY oidc_identities
    ADD CONSTRAINT oidc_identities_pkey PRIMARY KEY (identity_id);

ALTER TABLE ONLY oidc_jwks_cache
    ADD CONSTRAINT oidc_jwks_cache_pkey PRIMARY KEY (cache_id);

ALTER TABLE ONLY oidc_refresh_tokens
    ADD CONSTRAINT oidc_refresh_tokens_pkey PRIMARY KEY (token_id);

ALTER TABLE ONLY organization_members
    ADD CONSTRAINT organization_members_pkey PRIMARY KEY (member_id);

ALTER TABLE ONLY organizations
    ADD CONSTRAINT organizations_pkey PRIMARY KEY (org_id);

ALTER TABLE ONLY password_policies
    ADD CONSTRAINT password_policies_org_id_key UNIQUE (org_id);

ALTER TABLE ONLY password_policies
    ADD CONSTRAINT password_policies_pkey PRIMARY KEY (policy_id);

ALTER TABLE ONLY password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_pkey PRIMARY KEY (token_id);

ALTER TABLE ONLY principals
    ADD CONSTRAINT principals_pkey PRIMARY KEY (principal_id);

ALTER TABLE ONLY promoted_adapters
    ADD CONSTRAINT promoted_adapters_pkey PRIMARY KEY (promoted_id);

ALTER TABLE ONLY promoted_adapters
    ADD CONSTRAINT promoted_adapters_scheme_schema_version_key UNIQUE (scheme, schema_version);

ALTER TABLE ONLY provider_api_keys
    ADD CONSTRAINT provider_api_keys_pkey PRIMARY KEY (org_id, user_id, provider);

ALTER TABLE ONLY role_assignments
    ADD CONSTRAINT role_assignments_pkey PRIMARY KEY (assignment_id);

ALTER TABLE ONLY roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (role_id);

ALTER TABLE ONLY saml_authentications
    ADD CONSTRAINT saml_authentications_pkey PRIMARY KEY (auth_id);

ALTER TABLE ONLY saml_identities
    ADD CONSTRAINT saml_identities_pkey PRIMARY KEY (identity_id);

ALTER TABLE ONLY scim_external_ids
    ADD CONSTRAINT scim_external_ids_pkey PRIMARY KEY (mapping_id);

ALTER TABLE ONLY scim_group_members
    ADD CONSTRAINT scim_group_members_pkey PRIMARY KEY (membership_id);

ALTER TABLE ONLY scim_groups
    ADD CONSTRAINT scim_groups_pkey PRIMARY KEY (group_id);

ALTER TABLE ONLY scim_tokens
    ADD CONSTRAINT scim_tokens_pkey PRIMARY KEY (token_id);

ALTER TABLE ONLY sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (session_id);

ALTER TABLE ONLY siem_export_cursors
    ADD CONSTRAINT siem_export_cursors_pkey PRIMARY KEY (exporter_name, source);

ALTER TABLE ONLY siem_export_dead_letters
    ADD CONSTRAINT siem_export_dead_letters_pkey PRIMARY KEY (id);

ALTER TABLE ONLY siem_exporter_controls
    ADD CONSTRAINT siem_exporter_controls_pkey PRIMARY KEY (exporter_name);

ALTER TABLE ONLY siwe_nonces
    ADD CONSTRAINT siwe_nonces_pkey PRIMARY KEY (nonce_id);

ALTER TABLE ONLY skill_audit_events
    ADD CONSTRAINT skill_audit_events_pkey PRIMARY KEY (audit_id);

ALTER TABLE ONLY skills
    ADD CONSTRAINT skills_org_id_user_id_name_key UNIQUE (org_id, user_id, name);

ALTER TABLE ONLY skills
    ADD CONSTRAINT skills_pkey PRIMARY KEY (skill_id);

ALTER TABLE ONLY tenant_adapter_settings
    ADD CONSTRAINT tenant_adapter_settings_pkey PRIMARY KEY (tenant_id);

ALTER TABLE ONLY tenant_settings
    ADD CONSTRAINT tenant_settings_pkey PRIMARY KEY (tenant_id, namespace);

ALTER TABLE ONLY todo_audit_events
    ADD CONSTRAINT todo_audit_events_pkey PRIMARY KEY (audit_id);

ALTER TABLE ONLY todo_series
    ADD CONSTRAINT todo_series_pkey PRIMARY KEY (id);

ALTER TABLE ONLY todos
    ADD CONSTRAINT todos_pkey PRIMARY KEY (id);

ALTER TABLE ONLY totp_secrets
    ADD CONSTRAINT totp_secrets_factor_id_key UNIQUE (factor_id);

ALTER TABLE ONLY totp_secrets
    ADD CONSTRAINT totp_secrets_pkey PRIMARY KEY (secret_id);

ALTER TABLE ONLY user_avatars
    ADD CONSTRAINT user_avatars_pkey PRIMARY KEY (user_id);

ALTER TABLE ONLY user_preferences
    ADD CONSTRAINT user_preferences_pkey PRIMARY KEY (user_id);

ALTER TABLE ONLY user_profiles
    ADD CONSTRAINT user_profiles_pkey PRIMARY KEY (user_id);

ALTER TABLE ONLY users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);

ALTER TABLE ONLY wallet_identities
    ADD CONSTRAINT wallet_identities_pkey PRIMARY KEY (wallet_id);

ALTER TABLE ONLY webauthn_credentials
    ADD CONSTRAINT webauthn_credentials_credential_id_b64_key UNIQUE (credential_id_b64);

ALTER TABLE ONLY webauthn_credentials
    ADD CONSTRAINT webauthn_credentials_pkey PRIMARY KEY (credential_id);

CREATE UNIQUE INDEX idx_account_lockouts_active ON account_lockouts USING btree (org_id, user_id) WHERE (unlocked_at IS NULL);

CREATE INDEX idx_account_lockouts_auto_unlock ON account_lockouts USING btree (auto_unlock_at) WHERE (unlocked_at IS NULL);

CREATE INDEX idx_account_lockouts_locked_at ON account_lockouts USING btree (org_id, locked_at DESC);

CREATE INDEX idx_account_merges_absorbed ON account_merges USING btree (absorbed_org_id, absorbed_user_id);

CREATE UNIQUE INDEX idx_account_merges_absorbed_active ON account_merges USING btree (absorbed_org_id, absorbed_user_id) WHERE (state <> 'completed'::text);

CREATE INDEX idx_adapter_candidates_scheme_version ON adapter_candidates USING btree (scheme, version);

CREATE INDEX idx_adapter_candidates_status ON adapter_candidates USING btree (status, created_at DESC);

CREATE INDEX idx_adapter_candidates_tenant ON adapter_candidates USING btree (tenant_id, created_at DESC);

CREATE INDEX idx_adapter_registry_audit_tenant ON adapter_registry_audit_events USING btree (tenant_id, seq DESC);

CREATE INDEX idx_adapter_reviews_candidate ON adapter_reviews USING btree (candidate_id, decided_at);

CREATE INDEX idx_api_keys_org ON api_keys USING btree (org_id) WHERE (revoked_at IS NULL);

CREATE INDEX idx_api_keys_user ON api_keys USING btree (user_id) WHERE (revoked_at IS NULL);

CREATE INDEX idx_api_keys_workspace ON api_keys USING btree (org_id) WHERE ((revoked_at IS NULL) AND (kind = 'workspace'::text));

CREATE INDEX idx_auth_provider_domains_active ON auth_provider_domains USING btree (domain) WHERE (deleted_at IS NULL);

CREATE INDEX idx_auth_providers_enabled ON auth_providers USING btree (org_id, enabled) WHERE (deleted_at IS NULL);

CREATE UNIQUE INDEX idx_auth_providers_unique ON auth_providers USING btree (org_id, kind, display_name) WHERE (deleted_at IS NULL);

CREATE INDEX idx_identity_audit_org_action ON identity_audit_events USING btree (org_id, action, created_at DESC);

CREATE INDEX idx_identity_audit_org_created ON identity_audit_events USING btree (org_id, created_at DESC);

CREATE INDEX idx_identity_audit_subject ON identity_audit_events USING btree (subject_user_id, created_at DESC);

CREATE INDEX idx_identity_policies_local_password ON identity_policies USING btree (org_id) WHERE (local_password_enabled = false);

CREATE UNIQUE INDEX idx_invitations_org_email_active ON invitations USING btree (org_id, lower((email)::text)) WHERE ((accepted_at IS NULL) AND (revoked_at IS NULL));

CREATE INDEX idx_invitations_org_pending ON invitations USING btree (org_id, expires_at DESC) WHERE ((accepted_at IS NULL) AND (revoked_at IS NULL));

CREATE UNIQUE INDEX idx_invitations_token_hash ON invitations USING btree (token_hash);

CREATE INDEX idx_local_accounts_principal ON local_accounts USING btree (principal_id);

CREATE UNIQUE INDEX idx_local_accounts_singleton ON local_accounts USING btree ((true));

CREATE UNIQUE INDEX idx_local_credentials_user ON local_credentials USING btree (org_id, user_id) WHERE (deleted_at IS NULL);

CREATE INDEX idx_login_attempts_created ON login_attempts USING btree (created_at);

CREATE INDEX idx_login_attempts_ip ON login_attempts USING btree (ip, created_at DESC);

CREATE INDEX idx_login_attempts_org_email ON login_attempts USING btree (org_id, email_attempted, created_at DESC);

CREATE INDEX idx_login_attempts_user ON login_attempts USING btree (user_id, created_at DESC);

CREATE INDEX idx_magic_link_tokens_expires ON magic_link_tokens USING btree (expires_at) WHERE (consumed_at IS NULL);

CREATE INDEX idx_magic_link_tokens_user_active ON magic_link_tokens USING btree (user_id, created_at DESC) WHERE (consumed_at IS NULL);

CREATE INDEX idx_mcp_audit_events_org_seq ON mcp_audit_events USING btree (org_id, seq);

CREATE INDEX idx_mcp_auth_connections_kms_key_id ON mcp_auth_connections USING btree (kms_key_id) WHERE (kms_key_id IS NOT NULL);

CREATE UNIQUE INDEX idx_mcp_auth_connections_server ON mcp_auth_connections USING btree (server_id);

CREATE INDEX idx_mcp_servers_scope ON mcp_servers USING btree (org_id, user_id, enabled);

CREATE INDEX idx_mfa_challenges_pending ON mfa_challenges USING btree (expires_at) WHERE (consumed_at IS NULL);

CREATE INDEX idx_mfa_challenges_user ON mfa_challenges USING btree (org_id, user_id, expires_at DESC);

CREATE INDEX idx_mfa_factors_user_active ON mfa_factors USING btree (org_id, user_id, enabled) WHERE (disabled_at IS NULL);

CREATE INDEX idx_mfa_factors_user_kind ON mfa_factors USING btree (user_id, kind, enabled);

CREATE INDEX idx_mfa_recovery_active ON mfa_recovery_codes USING btree (org_id, user_id) WHERE (consumed_at IS NULL);

CREATE INDEX idx_oidc_auth_pending ON oidc_authentications USING btree (expires_at) WHERE (consumed_at IS NULL);

CREATE UNIQUE INDEX idx_oidc_auth_state ON oidc_authentications USING btree (state);

CREATE INDEX idx_oidc_identities_principal ON oidc_identities USING btree (principal_id);

CREATE UNIQUE INDEX idx_oidc_identity_subject ON oidc_identities USING btree (provider_id, subject) WHERE (unlinked_at IS NULL);

CREATE INDEX idx_oidc_identity_user ON oidc_identities USING btree (user_id) WHERE (unlinked_at IS NULL);

CREATE INDEX idx_oidc_jwks_provider ON oidc_jwks_cache USING btree (provider_id, expires_at);

CREATE INDEX idx_oidc_refresh_active ON oidc_refresh_tokens USING btree (org_id, user_id, provider_id) WHERE (revoked_at IS NULL);

CREATE INDEX idx_oidc_refresh_expiring ON oidc_refresh_tokens USING btree (expires_at) WHERE (revoked_at IS NULL);

CREATE UNIQUE INDEX idx_org_members_active ON organization_members USING btree (org_id, user_id) WHERE (removed_at IS NULL);

CREATE UNIQUE INDEX idx_organizations_slug ON organizations USING btree (slug) WHERE (deleted_at IS NULL);

CREATE INDEX idx_password_reset_expiring ON password_reset_tokens USING btree (expires_at) WHERE (consumed_at IS NULL);

CREATE UNIQUE INDEX idx_password_reset_token_hash ON password_reset_tokens USING btree (token_hash);

CREATE INDEX idx_password_reset_user_pending ON password_reset_tokens USING btree (user_id, expires_at) WHERE (consumed_at IS NULL);

CREATE INDEX idx_promoted_adapters_scheme ON promoted_adapters USING btree (scheme, schema_version DESC);

CREATE UNIQUE INDEX idx_role_assignments_active ON role_assignments USING btree (org_id, user_id, role_id) WHERE (revoked_at IS NULL);

CREATE INDEX idx_role_assignments_role ON role_assignments USING btree (org_id, role_id);

CREATE UNIQUE INDEX idx_roles_org_name ON roles USING btree (COALESCE(org_id, '<system>'::text), name) WHERE (deleted_at IS NULL);

CREATE INDEX idx_roles_system ON roles USING btree (is_system) WHERE (deleted_at IS NULL);

CREATE UNIQUE INDEX idx_saml_assertion_replay ON saml_authentications USING btree (assertion_id);

CREATE INDEX idx_saml_identities_principal ON saml_identities USING btree (principal_id);

CREATE UNIQUE INDEX idx_saml_identity_nameid ON saml_identities USING btree (provider_id, name_id) WHERE (unlinked_at IS NULL);

CREATE INDEX idx_saml_identity_user ON saml_identities USING btree (user_id) WHERE (unlinked_at IS NULL);

CREATE INDEX idx_saml_pending ON saml_authentications USING btree (expires_at) WHERE (status = 'pending'::text);

CREATE INDEX idx_saml_request ON saml_authentications USING btree (request_id) WHERE (request_id IS NOT NULL);

CREATE INDEX idx_scim_external_group ON scim_external_ids USING btree (group_id);

CREATE UNIQUE INDEX idx_scim_external_id ON scim_external_ids USING btree (provider_id, external_id);

CREATE INDEX idx_scim_external_user ON scim_external_ids USING btree (user_id);

CREATE UNIQUE INDEX idx_scim_group_member_active ON scim_group_members USING btree (group_id, user_id) WHERE (removed_at IS NULL);

CREATE INDEX idx_scim_group_member_user ON scim_group_members USING btree (org_id, user_id) WHERE (removed_at IS NULL);

CREATE UNIQUE INDEX idx_scim_groups_name ON scim_groups USING btree (org_id, display_name) WHERE (deleted_at IS NULL);

CREATE INDEX idx_scim_groups_role ON scim_groups USING btree (mapped_role_id) WHERE (deleted_at IS NULL);

CREATE UNIQUE INDEX idx_scim_token_hash ON scim_tokens USING btree (token_hash);

CREATE INDEX idx_scim_token_org ON scim_tokens USING btree (org_id, revoked_at);

CREATE INDEX idx_sessions_expiring ON sessions USING btree (expires_at) WHERE (revoked_at IS NULL);

CREATE UNIQUE INDEX idx_sessions_token_active ON sessions USING btree (token_hash) WHERE (revoked_at IS NULL);

CREATE INDEX idx_sessions_user ON sessions USING btree (org_id, user_id, revoked_at, expires_at);

CREATE INDEX idx_siem_dead_letters_exporter ON siem_export_dead_letters USING btree (exporter_name, created_at DESC);

CREATE UNIQUE INDEX idx_siwe_nonces_nonce ON siwe_nonces USING btree (nonce);

CREATE INDEX idx_siwe_nonces_pending ON siwe_nonces USING btree (expires_at) WHERE (consumed_at IS NULL);

CREATE INDEX idx_skill_audit_events_org_seq ON skill_audit_events USING btree (org_id, seq);

CREATE INDEX idx_skills_org_scope ON skills USING btree (org_id, scope, enabled);

CREATE INDEX idx_skills_runtime_scope ON skills USING btree (org_id, user_id, enabled);

CREATE INDEX idx_tenant_settings_namespace ON tenant_settings USING btree (namespace);

CREATE INDEX idx_tool_use_policies_org_user ON tool_use_policies USING btree (org_id, user_id);

CREATE INDEX idx_user_avatars_org ON user_avatars USING btree (org_id);

CREATE INDEX idx_user_preferences_org ON user_preferences USING btree (org_id);

CREATE INDEX idx_user_profiles_org ON user_profiles USING btree (org_id);

CREATE UNIQUE INDEX idx_users_org_email ON users USING btree (org_id, lower((primary_email)::text)) WHERE (deleted_at IS NULL);

CREATE INDEX idx_users_org_last_seen ON users USING btree (org_id, last_seen_at DESC);

CREATE INDEX idx_users_org_status ON users USING btree (org_id, status);

CREATE INDEX idx_users_principal ON users USING btree (principal_id);

CREATE UNIQUE INDEX idx_users_scim_external_id ON users USING btree (org_id, scim_external_id) WHERE (scim_external_id IS NOT NULL);

CREATE UNIQUE INDEX idx_wallet_identities_address ON wallet_identities USING btree (address);

CREATE INDEX idx_wallet_identities_principal ON wallet_identities USING btree (principal_id);

CREATE INDEX idx_wallet_identities_user ON wallet_identities USING btree (user_id);

CREATE INDEX idx_webauthn_credentials_factor ON webauthn_credentials USING btree (factor_id);

CREATE INDEX provider_api_keys_user_idx ON provider_api_keys USING btree (org_id, user_id);

CREATE INDEX todo_audit_correlation_idx ON todo_audit_events USING btree (correlation_id) WHERE (correlation_id IS NOT NULL);

CREATE INDEX todo_audit_target_idx ON todo_audit_events USING btree (tenant_id, target_id, ts);

CREATE INDEX todo_audit_tenant_idx ON todo_audit_events USING btree (tenant_id, ts DESC);

CREATE UNIQUE INDEX todo_series_dedup ON todos USING btree (series_id, due) WHERE ((series_id IS NOT NULL) AND (due IS NOT NULL));

CREATE INDEX todo_series_tenant_idx ON todo_series USING btree (tenant_id, owner_user_id);

CREATE INDEX todos_tenant_parent_idx ON todos USING btree (tenant_id, parent_id) WHERE ((parent_id IS NOT NULL) AND (deleted_at IS NULL));

CREATE INDEX todos_tenant_project_idx ON todos USING btree (tenant_id, project_id, created_at DESC) WHERE (deleted_at IS NULL);

CREATE INDEX todos_tenant_status_idx ON todos USING btree (tenant_id, status, created_at DESC) WHERE (deleted_at IS NULL);

CREATE UNIQUE INDEX uniq_privacy_settings_scope ON privacy_settings USING btree (org_id, COALESCE(user_id, '__org__'::text));

CREATE UNIQUE INDEX uniq_tool_use_policies_scope_kind ON tool_use_policies USING btree (org_id, COALESCE(user_id, '__org__'::text), kind);

CREATE TRIGGER identity_audit_events_immutable BEFORE DELETE OR UPDATE ON identity_audit_events FOR EACH ROW EXECUTE FUNCTION audit_immutable_guard();

CREATE TRIGGER mcp_audit_events_immutable BEFORE DELETE OR UPDATE ON mcp_audit_events FOR EACH ROW EXECUTE FUNCTION audit_immutable_guard();

CREATE TRIGGER skill_audit_events_immutable BEFORE DELETE OR UPDATE ON skill_audit_events FOR EACH ROW EXECUTE FUNCTION audit_immutable_guard();

ALTER TABLE ONLY account_lockouts
    ADD CONSTRAINT account_lockouts_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY account_merges
    ADD CONSTRAINT account_merges_absorbed_org_id_fkey FOREIGN KEY (absorbed_org_id) REFERENCES organizations(org_id);

ALTER TABLE ONLY account_merges
    ADD CONSTRAINT account_merges_survivor_org_id_fkey FOREIGN KEY (survivor_org_id) REFERENCES organizations(org_id);

ALTER TABLE ONLY adapter_candidates
    ADD CONSTRAINT adapter_candidates_submitter_user_id_fkey FOREIGN KEY (submitter_user_id) REFERENCES users(user_id) ON DELETE RESTRICT;

ALTER TABLE ONLY adapter_candidates
    ADD CONSTRAINT adapter_candidates_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES organizations(org_id) ON DELETE CASCADE;

ALTER TABLE ONLY adapter_registry_audit_events
    ADD CONSTRAINT adapter_registry_audit_events_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES users(user_id) ON DELETE RESTRICT;

ALTER TABLE ONLY adapter_registry_audit_events
    ADD CONSTRAINT adapter_registry_audit_events_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES adapter_candidates(candidate_id) ON DELETE SET NULL;

ALTER TABLE ONLY adapter_registry_audit_events
    ADD CONSTRAINT adapter_registry_audit_events_promoted_id_fkey FOREIGN KEY (promoted_id) REFERENCES promoted_adapters(promoted_id) ON DELETE SET NULL;

ALTER TABLE ONLY adapter_registry_audit_events
    ADD CONSTRAINT adapter_registry_audit_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES organizations(org_id) ON DELETE RESTRICT;

ALTER TABLE ONLY adapter_reviews
    ADD CONSTRAINT adapter_reviews_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES adapter_candidates(candidate_id) ON DELETE CASCADE;

ALTER TABLE ONLY adapter_reviews
    ADD CONSTRAINT adapter_reviews_reviewer_org_id_fkey FOREIGN KEY (reviewer_org_id) REFERENCES organizations(org_id) ON DELETE RESTRICT;

ALTER TABLE ONLY adapter_reviews
    ADD CONSTRAINT adapter_reviews_reviewer_user_id_fkey FOREIGN KEY (reviewer_user_id) REFERENCES users(user_id) ON DELETE RESTRICT;

ALTER TABLE ONLY api_keys
    ADD CONSTRAINT api_keys_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id) ON DELETE CASCADE;

ALTER TABLE ONLY api_keys
    ADD CONSTRAINT api_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE ONLY auth_provider_domains
    ADD CONSTRAINT auth_provider_domains_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id) ON DELETE CASCADE;

ALTER TABLE ONLY auth_provider_domains
    ADD CONSTRAINT auth_provider_domains_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES auth_providers(provider_id) ON DELETE CASCADE;

ALTER TABLE ONLY identity_policies
    ADD CONSTRAINT identity_policies_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id);

ALTER TABLE ONLY invitations
    ADD CONSTRAINT invitations_accepted_user_id_fkey FOREIGN KEY (accepted_user_id) REFERENCES users(user_id);

ALTER TABLE ONLY invitations
    ADD CONSTRAINT invitations_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES users(user_id);

ALTER TABLE ONLY invitations
    ADD CONSTRAINT invitations_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id);

ALTER TABLE ONLY invitations
    ADD CONSTRAINT invitations_revoked_by_user_id_fkey FOREIGN KEY (revoked_by_user_id) REFERENCES users(user_id);

ALTER TABLE ONLY invitations
    ADD CONSTRAINT invitations_role_id_fkey FOREIGN KEY (role_id) REFERENCES roles(role_id);

ALTER TABLE ONLY local_accounts
    ADD CONSTRAINT local_accounts_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id);

ALTER TABLE ONLY local_accounts
    ADD CONSTRAINT local_accounts_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES principals(principal_id);

ALTER TABLE ONLY local_accounts
    ADD CONSTRAINT local_accounts_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY local_credentials
    ADD CONSTRAINT local_credentials_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY magic_link_tokens
    ADD CONSTRAINT magic_link_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE ONLY mcp_auth_connections
    ADD CONSTRAINT mcp_auth_connections_server_id_fkey FOREIGN KEY (server_id) REFERENCES mcp_servers(server_id) ON DELETE CASCADE;

ALTER TABLE ONLY mcp_auth_sessions
    ADD CONSTRAINT mcp_auth_sessions_server_id_fkey FOREIGN KEY (server_id) REFERENCES mcp_servers(server_id) ON DELETE CASCADE;

ALTER TABLE ONLY mfa_challenges
    ADD CONSTRAINT mfa_challenges_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY mfa_factors
    ADD CONSTRAINT mfa_factors_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY mfa_recovery_codes
    ADD CONSTRAINT mfa_recovery_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY notification_preferences
    ADD CONSTRAINT notification_preferences_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE ONLY notification_quiet_hours
    ADD CONSTRAINT notification_quiet_hours_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE ONLY oidc_authentications
    ADD CONSTRAINT oidc_authentications_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES auth_providers(provider_id);

ALTER TABLE ONLY oidc_identities
    ADD CONSTRAINT oidc_identities_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES principals(principal_id);

ALTER TABLE ONLY oidc_identities
    ADD CONSTRAINT oidc_identities_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES auth_providers(provider_id);

ALTER TABLE ONLY oidc_identities
    ADD CONSTRAINT oidc_identities_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY oidc_jwks_cache
    ADD CONSTRAINT oidc_jwks_cache_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES auth_providers(provider_id);

ALTER TABLE ONLY oidc_refresh_tokens
    ADD CONSTRAINT oidc_refresh_tokens_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES auth_providers(provider_id);

ALTER TABLE ONLY oidc_refresh_tokens
    ADD CONSTRAINT oidc_refresh_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY organization_members
    ADD CONSTRAINT organization_members_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id);

ALTER TABLE ONLY organization_members
    ADD CONSTRAINT organization_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY principals
    ADD CONSTRAINT principals_absorbed_into_principal_id_fkey FOREIGN KEY (absorbed_into_principal_id) REFERENCES principals(principal_id);

ALTER TABLE ONLY privacy_settings
    ADD CONSTRAINT privacy_settings_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id) ON DELETE CASCADE;

ALTER TABLE ONLY promoted_adapters
    ADD CONSTRAINT promoted_adapters_origin_tenant_id_fkey FOREIGN KEY (origin_tenant_id) REFERENCES organizations(org_id) ON DELETE RESTRICT;

ALTER TABLE ONLY promoted_adapters
    ADD CONSTRAINT promoted_adapters_promoted_by_user_id_fkey FOREIGN KEY (promoted_by_user_id) REFERENCES users(user_id) ON DELETE RESTRICT;

ALTER TABLE ONLY promoted_adapters
    ADD CONSTRAINT promoted_adapters_source_candidate_id_fkey FOREIGN KEY (source_candidate_id) REFERENCES adapter_candidates(candidate_id) ON DELETE RESTRICT;

ALTER TABLE ONLY provider_api_keys
    ADD CONSTRAINT provider_api_keys_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id) ON DELETE CASCADE;

ALTER TABLE ONLY provider_api_keys
    ADD CONSTRAINT provider_api_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE ONLY role_assignments
    ADD CONSTRAINT role_assignments_role_id_fkey FOREIGN KEY (role_id) REFERENCES roles(role_id);

ALTER TABLE ONLY role_assignments
    ADD CONSTRAINT role_assignments_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY saml_authentications
    ADD CONSTRAINT saml_authentications_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES auth_providers(provider_id);

ALTER TABLE ONLY saml_identities
    ADD CONSTRAINT saml_identities_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES principals(principal_id);

ALTER TABLE ONLY saml_identities
    ADD CONSTRAINT saml_identities_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES auth_providers(provider_id);

ALTER TABLE ONLY saml_identities
    ADD CONSTRAINT saml_identities_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY scim_external_ids
    ADD CONSTRAINT scim_external_ids_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES auth_providers(provider_id);

ALTER TABLE ONLY scim_external_ids
    ADD CONSTRAINT scim_external_ids_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY scim_group_members
    ADD CONSTRAINT scim_group_members_group_id_fkey FOREIGN KEY (group_id) REFERENCES scim_groups(group_id);

ALTER TABLE ONLY scim_group_members
    ADD CONSTRAINT scim_group_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY scim_groups
    ADD CONSTRAINT scim_groups_mapped_role_id_fkey FOREIGN KEY (mapped_role_id) REFERENCES roles(role_id);

ALTER TABLE ONLY scim_tokens
    ADD CONSTRAINT scim_tokens_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES auth_providers(provider_id);

ALTER TABLE ONLY sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY tenant_adapter_settings
    ADD CONSTRAINT tenant_adapter_settings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES organizations(org_id) ON DELETE CASCADE;

ALTER TABLE ONLY tenant_adapter_settings
    ADD CONSTRAINT tenant_adapter_settings_updated_by_user_id_fkey FOREIGN KEY (updated_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL;

ALTER TABLE ONLY tenant_settings
    ADD CONSTRAINT tenant_settings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES organizations(org_id) ON DELETE CASCADE;

ALTER TABLE ONLY tenant_settings
    ADD CONSTRAINT tenant_settings_updated_by_user_id_fkey FOREIGN KEY (updated_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL;

ALTER TABLE ONLY todo_audit_events
    ADD CONSTRAINT todo_audit_events_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES users(user_id) ON DELETE RESTRICT;

ALTER TABLE ONLY todo_audit_events
    ADD CONSTRAINT todo_audit_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES organizations(org_id) ON DELETE RESTRICT;

ALTER TABLE ONLY todo_series
    ADD CONSTRAINT todo_series_owner_user_id_fkey FOREIGN KEY (owner_user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE ONLY todo_series
    ADD CONSTRAINT todo_series_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES organizations(org_id) ON DELETE CASCADE;

ALTER TABLE ONLY todos
    ADD CONSTRAINT todos_owner_user_id_fkey FOREIGN KEY (owner_user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE ONLY todos
    ADD CONSTRAINT todos_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES todos(id) ON DELETE CASCADE;

ALTER TABLE ONLY todos
    ADD CONSTRAINT todos_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES organizations(org_id) ON DELETE CASCADE;

ALTER TABLE ONLY tool_use_policies
    ADD CONSTRAINT tool_use_policies_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id) ON DELETE CASCADE;

ALTER TABLE ONLY totp_secrets
    ADD CONSTRAINT totp_secrets_factor_id_fkey FOREIGN KEY (factor_id) REFERENCES mfa_factors(factor_id);

ALTER TABLE ONLY user_avatars
    ADD CONSTRAINT user_avatars_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id);

ALTER TABLE ONLY user_avatars
    ADD CONSTRAINT user_avatars_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE ONLY user_preferences
    ADD CONSTRAINT user_preferences_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id);

ALTER TABLE ONLY user_preferences
    ADD CONSTRAINT user_preferences_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE ONLY user_profiles
    ADD CONSTRAINT user_profiles_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id);

ALTER TABLE ONLY user_profiles
    ADD CONSTRAINT user_profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE ONLY users
    ADD CONSTRAINT users_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id);

ALTER TABLE ONLY users
    ADD CONSTRAINT users_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES principals(principal_id);

ALTER TABLE ONLY wallet_identities
    ADD CONSTRAINT wallet_identities_org_id_fkey FOREIGN KEY (org_id) REFERENCES organizations(org_id);

ALTER TABLE ONLY wallet_identities
    ADD CONSTRAINT wallet_identities_principal_id_fkey FOREIGN KEY (principal_id) REFERENCES principals(principal_id);

ALTER TABLE ONLY wallet_identities
    ADD CONSTRAINT wallet_identities_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(user_id);

ALTER TABLE ONLY webauthn_credentials
    ADD CONSTRAINT webauthn_credentials_factor_id_fkey FOREIGN KEY (factor_id) REFERENCES mfa_factors(factor_id);

ALTER TABLE adapter_candidates ENABLE ROW LEVEL SECURITY;

CREATE POLICY adapter_candidates_tenant_isolation ON adapter_candidates USING (((tenant_id = current_setting('app.current_org_id'::text, true)) OR (current_setting('app.role'::text, true) = 'admin'::text))) WITH CHECK ((tenant_id = current_setting('app.current_org_id'::text, true)));

ALTER TABLE adapter_registry_audit_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY adapter_registry_audit_tenant_isolation ON adapter_registry_audit_events USING (((tenant_id = current_setting('app.current_org_id'::text, true)) OR (current_setting('app.role'::text, true) = 'admin'::text))) WITH CHECK ((tenant_id = current_setting('app.current_org_id'::text, true)));

ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;

ALTER TABLE auth_provider_domains ENABLE ROW LEVEL SECURITY;

ALTER TABLE privacy_settings ENABLE ROW LEVEL SECURITY;

ALTER TABLE provider_api_keys ENABLE ROW LEVEL SECURITY;

CREATE POLICY provider_api_keys_tenant_isolation ON provider_api_keys USING (((org_id = current_setting('app.current_org_id'::text, true)) OR (current_setting('app.role'::text, true) = 'admin'::text))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

ALTER TABLE tenant_adapter_settings ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_adapter_settings_tenant_isolation ON tenant_adapter_settings USING (((tenant_id = current_setting('app.current_org_id'::text, true)) OR (current_setting('app.role'::text, true) = 'admin'::text))) WITH CHECK ((tenant_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON api_keys USING ((org_id = current_setting('app.current_org'::text, true)));

CREATE POLICY tenant_isolation ON auth_provider_domains USING ((org_id = current_setting('app.current_org'::text, true)));

CREATE POLICY tenant_isolation ON auth_providers USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON identity_audit_events USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON identity_policies USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON local_credentials USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON mcp_audit_events USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON mcp_auth_connections USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON mcp_auth_sessions USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON mcp_servers USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON oidc_authentications USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON oidc_identities USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON oidc_refresh_tokens USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON organization_members USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON organizations USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON password_policies USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON password_reset_tokens USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON privacy_settings USING ((org_id = current_setting('app.current_org'::text, true)));

CREATE POLICY tenant_isolation ON role_assignments USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON sessions USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON skill_audit_events USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON skills USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON tenant_settings USING ((tenant_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((tenant_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON tool_use_policies USING ((org_id = current_setting('app.current_org'::text, true)));

CREATE POLICY tenant_isolation ON user_avatars USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON user_preferences USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON user_profiles USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON users USING ((org_id = current_setting('app.current_org_id'::text, true))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

CREATE POLICY tenant_isolation ON wallet_identities USING ((org_id = current_setting('app.current_org'::text, true)));

CREATE POLICY tenant_isolation_or_anon ON login_attempts USING (((current_setting('app.role'::text, true) = 'auth'::text) OR (org_id IS NULL) OR (org_id = current_setting('app.current_org_id'::text, true)))) WITH CHECK (true);

CREATE POLICY tenant_isolation_or_system ON roles USING (((org_id IS NULL) OR (org_id = current_setting('app.current_org_id'::text, true)))) WITH CHECK ((org_id = current_setting('app.current_org_id'::text, true)));

ALTER TABLE todo_audit_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY todo_audit_tenant_isolation ON todo_audit_events USING (((tenant_id = current_setting('app.current_org_id'::text, true)) OR (current_setting('app.role'::text, true) = 'admin'::text))) WITH CHECK ((tenant_id = current_setting('app.current_org_id'::text, true)));

ALTER TABLE todo_series ENABLE ROW LEVEL SECURITY;

CREATE POLICY todo_series_tenant_isolation ON todo_series USING (((tenant_id = current_setting('app.current_org_id'::text, true)) OR (current_setting('app.role'::text, true) = 'admin'::text))) WITH CHECK ((tenant_id = current_setting('app.current_org_id'::text, true)));

ALTER TABLE todos ENABLE ROW LEVEL SECURITY;

CREATE POLICY todos_tenant_isolation ON todos USING (((tenant_id = current_setting('app.current_org_id'::text, true)) OR (current_setting('app.role'::text, true) = 'admin'::text))) WITH CHECK ((tenant_id = current_setting('app.current_org_id'::text, true)));

ALTER TABLE tool_use_policies ENABLE ROW LEVEL SECURITY;

ALTER TABLE user_avatars ENABLE ROW LEVEL SECURITY;

ALTER TABLE wallet_identities ENABLE ROW LEVEL SECURITY;

-- ===================================================================
-- Role + privilege bootstrap. The migration history created these DB
-- roles and grants; pg_dump --no-privileges strips them, so they are
-- reproduced here from the migrated reference database (verified by
-- the catalog diff). Cluster-level roles: guarded for idempotency.
-- ===================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'audit_writer') THEN
        CREATE ROLE audit_writer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'enterprise_admin') THEN
        CREATE ROLE enterprise_admin BYPASSRLS NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'enterprise_app') THEN
        CREATE ROLE enterprise_app NOINHERIT;
    END IF;
END
$$;

GRANT INSERT, SELECT ON mcp_audit_events TO audit_writer;
GRANT INSERT, SELECT ON skill_audit_events TO audit_writer;
GRANT DELETE, INSERT, SELECT, UPDATE ON adapter_candidates TO enterprise_app;
GRANT INSERT, SELECT ON adapter_registry_audit_events TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON adapter_reviews TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON auth_providers TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON identity_audit_events TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON identity_policies TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON local_credentials TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON login_attempts TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON mcp_audit_events TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON mcp_auth_connections TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON mcp_auth_sessions TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON mcp_servers TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON oidc_authentications TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON oidc_identities TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON oidc_jwks_cache TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON oidc_refresh_tokens TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON organization_members TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON organizations TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON password_policies TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON password_reset_tokens TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON promoted_adapters TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON provider_api_keys TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON role_assignments TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON roles TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON sessions TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON skill_audit_events TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON skills TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON tenant_adapter_settings TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON tenant_settings TO enterprise_app;
GRANT INSERT, SELECT ON todo_audit_events TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON todo_series TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON todos TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON user_avatars TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON user_preferences TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON user_profiles TO enterprise_app;
GRANT DELETE, INSERT, SELECT, UPDATE ON users TO enterprise_app;

-- ===== Bootstrap data: the four system roles (from 0004b) =====
INSERT INTO roles (
    role_id, org_id, name, display_name, description, is_system,
    permission_scopes, created_at, updated_at
)
VALUES
    (
        'role_system_admin', NULL, 'admin', 'Administrator',
        'Full administrative access for an organization.', TRUE,
        '["admin:users","admin:idp","admin:audit_export","skills:write","mcp:write","runtime:use"]'::jsonb,
        now(), now()
    ),
    (
        'role_system_employee', NULL, 'employee', 'Employee',
        'Default role for org members; can use the runtime and read shared resources.', TRUE,
        '["runtime:use","skills:read","mcp:read"]'::jsonb,
        now(), now()
    ),
    (
        'role_system_auditor', NULL, 'auditor', 'Auditor',
        'Read-only access to audit logs.', TRUE,
        '["audit:read"]'::jsonb,
        now(), now()
    ),
    (
        'role_system_service', NULL, 'service', 'Service Account',
        'Headless callers (CI, integrations) that drive the runtime.', TRUE,
        '["runtime:use"]'::jsonb,
        now(), now()
    )
ON CONFLICT (role_id) DO NOTHING;
