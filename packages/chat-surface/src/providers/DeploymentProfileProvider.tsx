import { createContext, useContext, type ReactNode } from "react";

// Substrate-agnostic access to the active DeploymentProfile. The host app
// (single-user desktop, or a team/web deployment) decides the profile and
// feeds it in via the provider; descendants read it through the hook.
//
// Why a React context and not a build-time constant: the same chat-surface
// build ships in both the desktop bundle and the hosted web app. Gating a
// component on the profile (e.g. hiding Workspace / Members / Billing on
// `single_user_desktop`) must be a runtime value supplied by the host, not
// a compile-time flag baked into this shared package. This mirrors the
// TransportProvider decision: swap the value per substrate, don't fork the
// imports.

export type DeploymentProfile = "single_user_desktop" | "team";

const DeploymentProfileContext = createContext<DeploymentProfile | null>(null);
DeploymentProfileContext.displayName = "DeploymentProfileContext";

export function DeploymentProfileProvider({
  profile,
  children,
}: {
  profile: DeploymentProfile;
  children: ReactNode;
}): ReactNode {
  return (
    <DeploymentProfileContext.Provider value={profile}>
      {children}
    </DeploymentProfileContext.Provider>
  );
}

export function useDeploymentProfile(): DeploymentProfile {
  const value = useContext(DeploymentProfileContext);
  if (value === null) {
    throw new Error(
      "useDeploymentProfile: DeploymentProfileProvider missing in the tree above this component",
    );
  }
  return value;
}

/**
 * Null-safe read for components that may render without a provider (tests, or
 * mounts outside the shell). Prefer `useDeploymentProfile` inside the shell; use
 * this only for profile-aware polish (e.g. the palette placeholder copy) that
 * must not throw when the provider is absent.
 */
export function useOptionalDeploymentProfile(): DeploymentProfile | null {
  return useContext(DeploymentProfileContext);
}
