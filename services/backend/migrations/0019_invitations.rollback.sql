-- PR 4.2 rollback. Drop invitations + restore original member source CHECK.

DROP INDEX IF EXISTS idx_invitations_org_email_active;
DROP INDEX IF EXISTS idx_invitations_org_pending;
DROP INDEX IF EXISTS idx_invitations_token_hash;
DROP TABLE IF EXISTS invitations;

ALTER TABLE organization_members DROP CONSTRAINT IF EXISTS organization_members_source_check;
ALTER TABLE organization_members ADD CONSTRAINT organization_members_source_check
    CHECK (source IN ('local','oidc','saml','scim','bootstrap'));
