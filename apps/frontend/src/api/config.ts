export const DEFAULT_ORG_ID = "org_123";
export const DEFAULT_USER_ID = "user_123";

export interface RequestIdentity {
  orgId: string;
  userId: string;
}

export const DEFAULT_IDENTITY: RequestIdentity = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID
};

export function identityParams(identity: RequestIdentity = DEFAULT_IDENTITY): URLSearchParams {
  return new URLSearchParams({
    org_id: identity.orgId,
    user_id: identity.userId
  });
}
