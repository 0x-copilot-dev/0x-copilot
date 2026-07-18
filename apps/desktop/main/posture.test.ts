// @vitest-environment node
import { describe, expect, it } from "vitest";

import { isProductionPosture, resolveAuthPosture } from "./posture";

describe("isProductionPosture", () => {
  it("is true when packaged", () => {
    expect(isProductionPosture({ isPackaged: true, env: {} })).toBe(true);
  });

  it("is true for a CLI launch (COPILOT_PRODUCTION=1) even though not packaged", () => {
    expect(
      isProductionPosture({
        isPackaged: false,
        env: { COPILOT_PRODUCTION: "1" },
      }),
    ).toBe(true);
  });

  it("is false for plain monorepo dev (not packaged, no signal)", () => {
    expect(isProductionPosture({ isPackaged: false, env: {} })).toBe(false);
  });

  it("COPILOT_AUTH_MODE=dev-mint forces dev posture even when packaged", () => {
    expect(
      isProductionPosture({
        isPackaged: true,
        env: { COPILOT_AUTH_MODE: "dev-mint" },
      }),
    ).toBe(false);
  });

  it("COPILOT_DEV=1 forces dev posture even with COPILOT_PRODUCTION=1", () => {
    expect(
      isProductionPosture({
        isPackaged: false,
        env: { COPILOT_PRODUCTION: "1", COPILOT_DEV: "1" },
      }),
    ).toBe(false);
  });
});

describe("resolveAuthPosture", () => {
  it("production posture forces mode away from dev-mint and disallows dev-mint", () => {
    const posture = resolveAuthPosture({
      isPackaged: false,
      env: { COPILOT_PRODUCTION: "1" },
    });
    expect(posture.productionPosture).toBe(true);
    expect(posture.mode).toBe("oidc");
    expect(posture.mode).not.toBe("dev-mint");
    expect(posture.allowDevMint).toBe(false);
  });

  it("plain dev resolves to dev-mint with dev-mint allowed", () => {
    const posture = resolveAuthPosture({ isPackaged: false, env: {} });
    expect(posture.productionPosture).toBe(false);
    expect(posture.mode).toBe("dev-mint");
    expect(posture.allowDevMint).toBe(true);
  });

  it("explicit COPILOT_AUTH_MODE=oidc yields oidc mode in dev posture", () => {
    const posture = resolveAuthPosture({
      isPackaged: false,
      env: { COPILOT_AUTH_MODE: "oidc" },
    });
    expect(posture.productionPosture).toBe(false);
    expect(posture.mode).toBe("oidc");
    // dev-mint local option is still allowed outside production posture.
    expect(posture.allowDevMint).toBe(true);
  });
});
