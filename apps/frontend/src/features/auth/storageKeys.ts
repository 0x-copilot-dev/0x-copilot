// Storage-key constants for the auth substrate. Co-located so a rename
// updates every reader/writer in one place. Previously these strings were
// duplicated as literals across AuthContext + DevPersonaSwitcher + devIdp;
// keeping the keys here is the single source of truth.

export const BEARER_STORAGE_KEY = "enterprise.auth.bearer";
export const PERSONA_SLUG_STORAGE_KEY = "enterprise.dev.persona_slug";
