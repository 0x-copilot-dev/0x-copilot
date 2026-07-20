-- 0037_oidc_link_binding — bind an in-flight OAuth flow to the CURRENT user.
--
-- Account-linking (docs/plan/account-linking/PRD.md FR-L2 / §6.2): an
-- AUTHENTICATED "link Google" start writes the verified session's identity
-- onto the state row; the public callback recovers the link intent
-- server-side from the consumed row (the browser round-trip never carries
-- it). NULL on both columns = a plain sign-in flow, byte-identical to the
-- pre-0037 behavior.

ALTER TABLE oidc_authentications
    ADD COLUMN link_org_id TEXT,
    ADD COLUMN link_user_id TEXT;

COMMENT ON COLUMN oidc_authentications.link_org_id IS
    'Account-linking: the authenticated caller''s org at link-start (NULL = plain sign-in).';
COMMENT ON COLUMN oidc_authentications.link_user_id IS
    'Account-linking: the authenticated caller''s user at link-start (NULL = plain sign-in).';
