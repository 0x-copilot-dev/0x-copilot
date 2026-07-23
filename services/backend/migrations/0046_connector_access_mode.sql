-- PRD-06 — Connector access mode: the permission control's durable column.
--
-- Adds the per-connector agent access mode (Tools destination 3-way
-- segment: Read / Read & act / Off) as a durable column on the existing
-- `connectors` denormalized read model (added in 0044_connectors.sql).
--
-- Default is 'read', not 'off': existing rows were installed under a regime
-- where the connector was fully usable, so defaulting to 'off' would
-- silently break every deployed workspace on migrate, and 'read_act' would
-- grant more than the user ever saw. 'read' is the honest middle — nothing
-- that already worked read-only breaks, and every act is newly gated until
-- the user opts in.
--
-- The module mirror (src/backend_app/connectors/schema.sql) is kept
-- byte-for-semantics identical so fresh installs and migrated installs
-- converge (the 0043_projects incident: a migration/mirror divergence
-- shipped a 500 on fresh installs).

ALTER TABLE connectors
    ADD COLUMN IF NOT EXISTS access_mode TEXT NOT NULL DEFAULT 'read' CHECK (access_mode IN ('read', 'read_act', 'off'));

-- Enforcement lookup: which of a tenant's connectors are still usable
-- (access_mode <> 'off'). Partial so the common case isn't indexed twice.
CREATE INDEX IF NOT EXISTS connectors_tenant_access_mode_idx
    ON connectors (tenant_id, access_mode)
    WHERE access_mode <> 'off';
