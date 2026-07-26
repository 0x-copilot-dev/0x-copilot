-- 0023_draft_effect_supersessions
--
-- Canonical owner-scoped safety correlation for F-006 Artifact draft sends.
-- ``host_run_id`` is audit metadata only: a legacy draft may be re-homed, but
-- a prior v1 approval must continue to see the immutable effect supersession.

CREATE TABLE runtime_draft_effect_supersessions (
    org_id          text NOT NULL,
    user_id         text NOT NULL,
    draft_id        text NOT NULL,
    stage_id        text NOT NULL,
    host_run_id     text NOT NULL,
    artifact_id     text NOT NULL,
    proposal_digest text NOT NULL,
    target_digest   text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id, draft_id, stage_id),
    CONSTRAINT runtime_draft_effect_supersessions_proposal_digest_check
        CHECK (proposal_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT runtime_draft_effect_supersessions_target_digest_check
        CHECK (target_digest ~ '^[0-9a-f]{64}$')
);

-- This is the approval-time lookup. It deliberately contains no host run,
-- conversation, or mutable DraftRecord reference in its predicate.
CREATE INDEX runtime_draft_effect_supersessions_owner_draft_idx
    ON runtime_draft_effect_supersessions (org_id, user_id, draft_id);

ALTER TABLE runtime_draft_effect_supersessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_draft_effect_supersessions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON runtime_draft_effect_supersessions
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
GRANT SELECT, INSERT ON runtime_draft_effect_supersessions TO enterprise_app;
