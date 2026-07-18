import { render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import {
  DeploymentProfileProvider,
  useDeploymentProfile,
  type DeploymentProfile,
} from "./DeploymentProfileProvider";

function ProfileReadout(): ReactNode {
  const profile = useDeploymentProfile();
  return <span data-testid="profile">{profile}</span>;
}

describe("DeploymentProfileProvider", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("supplies the profile value to descendants via the hook", () => {
    const expected: DeploymentProfile = "single_user_desktop";
    const { result } = renderHook(() => useDeploymentProfile(), {
      wrapper: ({ children }) => (
        <DeploymentProfileProvider profile={expected}>
          {children}
        </DeploymentProfileProvider>
      ),
    });
    expect(result.current).toBe(expected);
  });

  it("renders the supplied profile when consumed in a component tree", () => {
    render(
      <DeploymentProfileProvider profile="team">
        <ProfileReadout />
      </DeploymentProfileProvider>,
    );
    expect(screen.getByTestId("profile")).toHaveTextContent("team");
  });

  it("throws when the hook is used outside a provider", () => {
    // React logs the render error to console.error; silence it so the
    // expected throw does not pollute test output.
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useDeploymentProfile())).toThrow(
      /DeploymentProfileProvider missing/,
    );
  });
});
