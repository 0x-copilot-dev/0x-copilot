-- Generative-UI PRD-08 — durable, org-scoped SurfaceSpec registry.
--
-- One row per generated (or curated-override) SurfaceSpec, keyed by the plan
-- D10 cache identity ``(server, tool, output_shape_hash, spec_schema_version,
-- skill_version)`` partitioned by ``org_id``. ``origin`` disambiguates a
-- human ``curated-override`` from the machine ``generated`` spec for the same
-- key; the override wins on read. The spec body is validated JSON (data, not
-- code) — the ai-backend re-validates it against ``surface_spec.schema.json``
-- on write, so no CHECK constraint here beyond well-formed JSONB.

CREATE TABLE IF NOT EXISTS surface_specs (
    spec_id             TEXT         PRIMARY KEY,
    org_id              TEXT         NOT NULL REFERENCES organizations(org_id) ON DELETE CASCADE,
    user_id             TEXT         NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    server              TEXT         NOT NULL,
    tool                TEXT         NOT NULL,
    output_shape_hash   TEXT         NOT NULL,
    spec_schema_version INTEGER      NOT NULL CHECK (spec_schema_version >= 1),
    skill_version       INTEGER      NOT NULL CHECK (skill_version >= 1),
    origin              TEXT         NOT NULL CHECK (origin IN (
        'generated','curated-override'
    )),
    generator_model     TEXT         NOT NULL DEFAULT '',
    spec                JSONB        NOT NULL,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- One spec per (org, full-key, origin). PUT upserts on this identity, so a
-- generated spec and its curated override coexist while a repeated generation
-- for the same key replaces in place.
CREATE UNIQUE INDEX IF NOT EXISTS uq_surface_specs_key
    ON surface_specs (
        org_id, server, tool, output_shape_hash,
        spec_schema_version, skill_version, origin
    );

-- The projector's coarse rung-2 read: latest spec for (org, server, tool).
CREATE INDEX IF NOT EXISTS idx_surface_specs_tool
    ON surface_specs (org_id, server, tool, created_at DESC);

ALTER TABLE surface_specs ENABLE ROW LEVEL SECURITY;
CREATE POLICY surface_specs_tenant_isolation ON surface_specs
    USING (
        org_id = current_setting('app.current_org_id', true)
        OR current_setting('app.role', true) = 'admin'
    )
    WITH CHECK (org_id = current_setting('app.current_org_id', true));
