export interface RequestIdentity {
  orgId: string;
  userId: string;
}

export function identityParams(identity: RequestIdentity): URLSearchParams {
  return new URLSearchParams({
    org_id: identity.orgId,
    user_id: identity.userId,
  });
}
