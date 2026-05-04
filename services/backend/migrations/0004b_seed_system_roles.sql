-- Seed system roles. yoyo applies migrations in lexicographic order, so this
-- runs after 0004_identity_foundation.sql which created the roles table.
--
-- These rows are the only rows in `roles` with org_id IS NULL; they are
-- visible to every tenant but never shadow a per-org role with the same
-- name (the unique index keys on COALESCE(org_id, '<system>')). Permission
-- scope strings come from the catalog in
-- packages/service-contracts/src/enterprise_service_contracts/scopes.py
-- (added in A10); A1 only seeds the strings as opaque values.

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
