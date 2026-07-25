-- 0010_usage_attribution_edges — immutable D1–D3 usage-to-operation links.
--
-- Canonical metered usage remains in runtime_model_call_usage.  This relation
-- records append-only attribution after an operation has an artifact, stage,
-- or surface identity, so neither historical token totals nor costs are ever
-- rewritten to add context.  The organization-scoped foreign key prevents a
-- link from pointing at a usage record in another tenant.

ALTER TABLE runtime_model_call_usage
    ADD CONSTRAINT runtime_model_call_usage_org_id_id_key UNIQUE (org_id, id);

CREATE TABLE runtime_usage_attribution_edges (
    edge_id text NOT NULL,
    org_id text NOT NULL,
    usage_record_id text NOT NULL,
    operation_id text NOT NULL,
    artifact_id text,
    stage_id text,
    surface_id text,
    relationship text NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT runtime_usage_attribution_edges_pkey PRIMARY KEY (edge_id),
    CONSTRAINT runtime_usage_attribution_edges_usage_fk
        FOREIGN KEY (org_id, usage_record_id)
        REFERENCES runtime_model_call_usage (org_id, id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT runtime_usage_attribution_edges_identifiers_check CHECK (
        edge_id <> '' AND usage_record_id <> '' AND operation_id <> ''
        AND (artifact_id IS NULL OR artifact_id <> '')
        AND (stage_id IS NULL OR stage_id <> '')
        AND (surface_id IS NULL OR surface_id <> '')
    ),
    CONSTRAINT runtime_usage_attribution_edges_relationship_check CHECK (
        relationship IN ('produced', 'revised', 'proposed', 'shaped')
    ),
    CONSTRAINT runtime_usage_attribution_edges_target_check CHECK (
        (relationship = 'proposed' AND stage_id IS NOT NULL)
        OR (relationship IN ('produced', 'revised') AND artifact_id IS NOT NULL)
        OR (
            relationship = 'shaped'
            AND (artifact_id IS NOT NULL OR surface_id IS NOT NULL)
        )
    )
);

-- A deterministic natural key makes at-least-once edge publication idempotent
-- while preserving distinct retry calls, which have distinct usage_record_id.
CREATE UNIQUE INDEX idx_runtime_usage_attribution_edges_natural
    ON runtime_usage_attribution_edges (
        org_id,
        usage_record_id,
        operation_id,
        COALESCE(artifact_id, ''),
        COALESCE(stage_id, ''),
        COALESCE(surface_id, ''),
        relationship
    );
CREATE INDEX idx_runtime_usage_attribution_edges_org_usage
    ON runtime_usage_attribution_edges (org_id, usage_record_id, created_at);

ALTER TABLE runtime_usage_attribution_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_usage_attribution_edges FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON runtime_usage_attribution_edges
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

-- The application role gets no UPDATE or DELETE privilege.  The port exposes
-- append/list only, making links immutable through every production adapter.
GRANT SELECT, INSERT ON runtime_usage_attribution_edges TO enterprise_app;
