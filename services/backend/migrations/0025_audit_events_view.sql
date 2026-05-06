-- PR B5: unified audit-log read-model.
--
-- The four append-only chained streams (identity / mcp / skill /
-- deploy) keep their per-domain semantics; this view is the read-model
-- powering the customer-facing `Settings → Audit log` table and the
-- `GET /v1/workspace/audit-log` endpoint. SIEM exports continue to
-- read the per-domain tables directly so chain integrity stays at
-- per-domain granularity.
--
-- Shape:
--   event_id      synthetic; derived per stream so cursoring is stable.
--   event_kind    'identity' | 'mcp' | 'skill' | 'deploy' (extends in
--                 future PRs with 'approval' / 'tool_policy' / etc.)
--   action        verb form ('member.added', 'mcp_server.connected', …)
--   actor_user_id who did it (nullable for system actions).
--   subject       opaque identifier the action refers to (member id,
--                 server id, skill id, …); null for global actions.
--   metadata      JSONB blob with the per-stream specifics.
--   occurred_at   wall-clock; UI orders desc.
--   chain_seq     per-stream monotonic seq from the source row's
--                 chained `seq` column. Cursor pagination uses
--                 (occurred_at desc, event_id) as the keyset.
--
-- The view is `org_id`-scoped via the existing tenant_isolation
-- policies on each underlying table — RLS flows through.

CREATE OR REPLACE VIEW audit_events_view AS
SELECT
    'identity:' || id::text AS event_id,
    'identity'::TEXT        AS event_kind,
    org_id,
    action,
    actor_user_id,
    subject_user_id::TEXT   AS subject,
    metadata,
    occurred_at,
    seq::BIGINT             AS chain_seq
FROM identity_audit_events

UNION ALL

SELECT
    'mcp:' || id::text      AS event_id,
    'mcp'::TEXT             AS event_kind,
    org_id,
    action,
    actor_user_id,
    server_id::TEXT         AS subject,
    metadata,
    occurred_at,
    seq::BIGINT             AS chain_seq
FROM mcp_audit_events

UNION ALL

SELECT
    'skill:' || id::text    AS event_id,
    'skill'::TEXT           AS event_kind,
    org_id,
    action,
    actor_user_id,
    skill_id::TEXT          AS subject,
    metadata,
    occurred_at,
    seq::BIGINT             AS chain_seq
FROM skill_audit_events

UNION ALL

SELECT
    'deploy:' || id::text   AS event_id,
    'deploy'::TEXT          AS event_kind,
    org_id,
    action,
    actor_user_id,
    NULL                    AS subject,
    metadata,
    occurred_at,
    seq::BIGINT             AS chain_seq
FROM deploy_audit_events;

-- Read-only role grant — the existing `audit_writer` role only
-- inserts into the per-domain tables; the view is consumed by app
-- service identity which already has SELECT.
COMMENT ON VIEW audit_events_view IS
    'PR B5: unified workspace audit read-model. UNION-ALL across the four chained streams; '
    'chain integrity is preserved at the per-domain table level. SIEM exports continue to '
    'consume the per-domain tables directly.';
