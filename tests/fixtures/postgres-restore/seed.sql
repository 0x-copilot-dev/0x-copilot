-- C12: synthetic restore-drill fixture.
--
-- The CI workflow ``.github/workflows/postgres-restore-drill.yml`` boots
-- a clean Postgres, applies every migration in services/backend/migrations
-- and services/ai-backend/migrations, then loads this file via
-- ``psql -f seed.sql``. The two ``restore_smoke.py`` scripts then check
-- per-table COUNT(*) against ``manifest.yaml``.
--
-- A "real" pg_dump is not version-controlled because:
--   1. binary dumps are unreviewable in PRs;
--   2. plain-text dumps drift on every migration;
--   3. seeding via ``INSERT`` is functionally equivalent to ``RESTORE``
--      for the purpose of proving the schema + smoke checks pass.
--
-- Update this file when adding a new tenant-table that the smoke check
-- should verify; bump the corresponding entry in ``manifest.yaml``.

BEGIN;

-- -----------------------------------------------------------------------
-- Identity foundation (backend)
-- -----------------------------------------------------------------------

INSERT INTO organizations (
    org_id, display_name, slug, deployment_kind, status, metadata,
    created_at, updated_at
) VALUES
    ('org_drill_a', 'Drill Org A', 'drill-a', 'saas', 'active', '{}'::jsonb, now(), now()),
    ('org_drill_b', 'Drill Org B', 'drill-b', 'saas', 'active', '{}'::jsonb, now(), now())
ON CONFLICT (org_id) DO NOTHING;

-- Principals first: users.principal_id is NOT NULL + FK (ADR 0001 baseline).
INSERT INTO principals (principal_id, display_name, created_at, updated_at) VALUES
    ('prn_usr_drill_a_admin', 'Drill Admin A',    now(), now()),
    ('prn_usr_drill_a_emp',   'Drill Employee A', now(), now()),
    ('prn_usr_drill_b_admin', 'Drill Admin B',    now(), now())
ON CONFLICT (principal_id) DO NOTHING;

INSERT INTO users (
    user_id, org_id, primary_email, display_name, status, is_service_account,
    metadata, created_at, updated_at, principal_id
) VALUES
    ('usr_drill_a_admin', 'org_drill_a', 'admin@drill-a.example', 'Drill Admin A', 'active', false, '{}'::jsonb, now(), now(), 'prn_usr_drill_a_admin'),
    ('usr_drill_a_emp',   'org_drill_a', 'emp@drill-a.example',   'Drill Employee A', 'active', false, '{}'::jsonb, now(), now(), 'prn_usr_drill_a_emp'),
    ('usr_drill_b_admin', 'org_drill_b', 'admin@drill-b.example', 'Drill Admin B', 'active', false, '{}'::jsonb, now(), now(), 'prn_usr_drill_b_admin')
ON CONFLICT (user_id) DO NOTHING;

INSERT INTO role_assignments (
    assignment_id, org_id, user_id, role_id, granted_at
) VALUES
    ('asn_drill_a_admin', 'org_drill_a', 'usr_drill_a_admin', 'role_system_admin',    now()),
    ('asn_drill_a_emp',   'org_drill_a', 'usr_drill_a_emp',   'role_system_employee', now()),
    ('asn_drill_b_admin', 'org_drill_b', 'usr_drill_b_admin', 'role_system_admin',    now())
ON CONFLICT (assignment_id) DO NOTHING;

INSERT INTO auth_providers (
    provider_id, org_id, kind, display_name, enabled, config,
    created_at, updated_at
) VALUES
    ('prv_drill_a_local', 'org_drill_a', 'local', 'Local password (A)', true, '{}'::jsonb, now(), now()),
    ('prv_drill_b_local', 'org_drill_b', 'local', 'Local password (B)', true, '{}'::jsonb, now(), now())
ON CONFLICT (provider_id) DO NOTHING;

INSERT INTO identity_policies (org_id) VALUES
    ('org_drill_a'),
    ('org_drill_b')
ON CONFLICT (org_id) DO NOTHING;

-- -----------------------------------------------------------------------
-- MCP / skills (backend) — minimal, exercises the audit chain on read.
-- -----------------------------------------------------------------------

INSERT INTO mcp_servers (
    server_id, org_id, user_id, name, display_name, url, transport,
    auth_mode, auth_state, health, enabled, required_scopes,
    last_discovery, created_at, updated_at
) VALUES
    ('mcp_drill_a',
     'org_drill_a', 'usr_drill_a_admin',
     'drill_server_a', 'Drill MCP A',
     'https://drill-a.example/mcp', 'http',
     'none', 'auth_skipped', 'healthy', true,
     '[]'::jsonb, '{}'::jsonb, now(), now())
ON CONFLICT (server_id) DO NOTHING;

INSERT INTO skills (
    skill_id, org_id, user_id, name, display_name, description, markdown,
    virtual_path, enabled, scope, source_type, version, allowed_tools,
    compatibility, metadata, created_at, updated_at
) VALUES
    ('skl_drill_a_smoke',
     'org_drill_a', 'usr_drill_a_admin',
     'drill_smoke', 'Drill Smoke Skill',
     'Restore-drill placeholder skill.',
     '# Drill smoke\n\nThis skill exists only to populate the restore fixture.\n',
     'org/drill_smoke.md', true, 'org', 'system', 1,
     '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, now(), now())
ON CONFLICT (skill_id) DO NOTHING;

-- -----------------------------------------------------------------------
-- Conversations / runs / messages / events (ai-backend).
--
-- One conversation per org (so the smoke check can verify cross-tenant
-- isolation against this fixture without leaking data). Each conversation
-- has 2 messages (user + assistant) and 1 run that produced 3 events.
-- -----------------------------------------------------------------------

INSERT INTO agent_conversations (
    id, org_id, user_id, assistant_id, title, status,
    created_at, updated_at, metadata_json
) VALUES
    ('cnv_drill_a', 'org_drill_a', 'usr_drill_a_emp', 'asst_drill', 'Drill A',
     'active', now(), now(), '{}'::jsonb),
    ('cnv_drill_b', 'org_drill_b', 'usr_drill_b_admin', 'asst_drill', 'Drill B',
     'active', now(), now(), '{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

-- agent_messages.run_id has a FK to agent_runs(id) (added in 0001 via
-- ALTER TABLE), and agent_runs.user_message_id has a FK to
-- agent_messages(id). Seed in three phases so neither FK is violated:
--   1. user messages (run_id NULL — these are the run starters)
--   2. agent_runs (referencing the user messages above)
--   3. assistant messages (run_id now resolvable)

INSERT INTO agent_messages (
    id, conversation_id, org_id, run_id, role, content_text, content_format,
    content_json, attachments_json, metadata_json,
    status, created_at
) VALUES
    ('msg_drill_a_user', 'cnv_drill_a', 'org_drill_a', NULL,
     'user', 'hello from drill a', 'text', '[]'::jsonb, '[]'::jsonb, '{}'::jsonb,
     'created', now()),
    ('msg_drill_b_user', 'cnv_drill_b', 'org_drill_b', NULL,
     'user', 'hello from drill b', 'text', '[]'::jsonb, '[]'::jsonb, '{}'::jsonb,
     'created', now())
ON CONFLICT (id) DO NOTHING;

INSERT INTO agent_runs (
    id, conversation_id, org_id, user_id, user_message_id,
    trace_id, status, model_provider, model_name,
    created_at, started_at, completed_at
) VALUES
    ('run_drill_a', 'cnv_drill_a', 'org_drill_a', 'usr_drill_a_emp', 'msg_drill_a_user',
     'trc_drill_a', 'completed', 'anthropic', 'claude-sonnet-4-6',
     now(), now(), now()),
    ('run_drill_b', 'cnv_drill_b', 'org_drill_b', 'usr_drill_b_admin', 'msg_drill_b_user',
     'trc_drill_b', 'completed', 'anthropic', 'claude-sonnet-4-6',
     now(), now(), now())
ON CONFLICT (id) DO NOTHING;

INSERT INTO agent_messages (
    id, conversation_id, org_id, run_id, role, content_text, content_format,
    content_json, attachments_json, metadata_json,
    status, created_at
) VALUES
    ('msg_drill_a_assistant', 'cnv_drill_a', 'org_drill_a', 'run_drill_a',
     'assistant', 'reply from drill a', 'text', '[]'::jsonb, '[]'::jsonb, '{}'::jsonb,
     'created', now()),
    ('msg_drill_b_assistant', 'cnv_drill_b', 'org_drill_b', 'run_drill_b',
     'assistant', 'reply from drill b', 'text', '[]'::jsonb, '[]'::jsonb, '{}'::jsonb,
     'created', now())
ON CONFLICT (id) DO NOTHING;

INSERT INTO runtime_events (
    id, run_id, conversation_id, org_id, sequence_no, source, event_type,
    trace_id, payload_json_redacted, metadata_json_redacted,
    visibility, redaction_state, created_at
) VALUES
    ('evt_drill_a_1', 'run_drill_a', 'cnv_drill_a', 'org_drill_a', 1, 'runtime', 'run_started',
     'trc_drill_a', '{}'::jsonb, '{}'::jsonb, 'user', 'redacted', now()),
    ('evt_drill_a_2', 'run_drill_a', 'cnv_drill_a', 'org_drill_a', 2, 'runtime', 'final_response',
     'trc_drill_a', '{}'::jsonb, '{}'::jsonb, 'user', 'redacted', now()),
    ('evt_drill_a_3', 'run_drill_a', 'cnv_drill_a', 'org_drill_a', 3, 'runtime', 'run_completed',
     'trc_drill_a', '{}'::jsonb, '{}'::jsonb, 'user', 'redacted', now()),
    ('evt_drill_b_1', 'run_drill_b', 'cnv_drill_b', 'org_drill_b', 1, 'runtime', 'run_started',
     'trc_drill_b', '{}'::jsonb, '{}'::jsonb, 'user', 'redacted', now()),
    ('evt_drill_b_2', 'run_drill_b', 'cnv_drill_b', 'org_drill_b', 2, 'runtime', 'final_response',
     'trc_drill_b', '{}'::jsonb, '{}'::jsonb, 'user', 'redacted', now()),
    ('evt_drill_b_3', 'run_drill_b', 'cnv_drill_b', 'org_drill_b', 3, 'runtime', 'run_completed',
     'trc_drill_b', '{}'::jsonb, '{}'::jsonb, 'user', 'redacted', now())
ON CONFLICT (id) DO NOTHING;

-- C7 phase 1 added encryption_version SMALLINT NOT NULL DEFAULT 0; the
-- INSERTs above accept the default so the seed exercises the v0 read
-- path. Switching the seed to v1 envelopes is left for the operator-side
-- backfill drill.

INSERT INTO runtime_run_usage (
    id, org_id, user_id, conversation_id, run_id,
    model_provider, model_name,
    input_tokens, output_tokens, cached_input_tokens, total_tokens,
    chunk_count, duration_ms, started_at, completed_at, status,
    created_at
) VALUES
    ('run_drill_a', 'org_drill_a', 'usr_drill_a_emp', 'cnv_drill_a', 'run_drill_a',
     'anthropic', 'claude-sonnet-4-6',
     120, 45, 0, 165,
     3, 1234, now(), now(), 'completed',
     now()),
    ('run_drill_b', 'org_drill_b', 'usr_drill_b_admin', 'cnv_drill_b', 'run_drill_b',
     'anthropic', 'claude-sonnet-4-6',
     200, 80, 0, 280,
     3, 1500, now(), now(), 'completed',
     now())
ON CONFLICT (id) DO NOTHING;

COMMIT;
