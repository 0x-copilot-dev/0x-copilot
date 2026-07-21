// @vitest-environment node
import { describe, expect, it } from "vitest";

import { isProductionPosture, resolveAuthPosture } from "./posture";
import { shouldSupervise } from "./services/boot-mode";

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

  it("is true for the shipped CLI launch (COPILOT_RUNTIME_DIR + COPILOT_PRODUCTION=1)", () => {
    // tools/cli/lib/launch.mjs sets BOTH; posture must be production.
    expect(
      isProductionPosture({
        isPackaged: false,
        env: {
          COPILOT_RUNTIME_DIR: "/home/u/.0xcopilot",
          COPILOT_PRODUCTION: "1",
        },
      }),
    ).toBe(true);
  });

  it("is true for a staged supervised runtime even without COPILOT_PRODUCTION (F5 regression)", () => {
    // The documented `COPILOT_RUNTIME_DIR=… npm run dev` recipe supervises a
    // production-configured stack (service-env.ts pins *_ENVIRONMENT=production,
    // so /v1/dev/identity/mint is never registered). Posture MUST be production
    // so the default "Sign in (local)" routes to the SIWE local-key flow that
    // works against that stack, not to a dev-mint endpoint that does not exist.
    expect(
      isProductionPosture({
        isPackaged: false,
        env: { COPILOT_RUNTIME_DIR: "/repo/apps/desktop/resources" },
      }),
    ).toBe(true);
  });

  it("is false for plain monorepo dev (not packaged, no signal)", () => {
    expect(isProductionPosture({ isPackaged: false, env: {} })).toBe(false);
  });

  it("is false for plain dev with only COPILOT_FACADE_URL (no supervisor)", () => {
    // `npm run dev` against a separately-run facade: no supervision, dev-mint.
    expect(
      isProductionPosture({
        isPackaged: false,
        env: { COPILOT_FACADE_URL: "http://127.0.0.1:8200" },
      }),
    ).toBe(false);
  });

  it("treats an empty COPILOT_RUNTIME_DIR as not-supervised (dev posture)", () => {
    expect(
      isProductionPosture({
        isPackaged: false,
        env: { COPILOT_RUNTIME_DIR: "" },
      }),
    ).toBe(false);
  });

  it("COPILOT_AUTH_MODE=dev-mint forces dev posture even when packaged", () => {
    expect(
      isProductionPosture({
        isPackaged: true,
        env: { COPILOT_AUTH_MODE: "dev-mint" },
      }),
    ).toBe(false);
  });

  it("COPILOT_AUTH_MODE=dev-mint forces dev posture even with a staged runtime", () => {
    expect(
      isProductionPosture({
        isPackaged: false,
        env: {
          COPILOT_RUNTIME_DIR: "/repo/apps/desktop/resources",
          COPILOT_AUTH_MODE: "dev-mint",
        },
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

  it("COPILOT_DEV=1 forces dev posture even when packaged", () => {
    expect(
      isProductionPosture({ isPackaged: true, env: { COPILOT_DEV: "1" } }),
    ).toBe(false);
  });
});

// The invariant that closes the divergence class: absent an explicit dev
// override, whenever the app supervises a local stack it MUST be in production
// posture (the supervised children are production-configured). Enumerate the
// full CLI-launch matrix and assert the two decisions never contradict.
describe("supervise ⟹ production posture (no silent divergence)", () => {
  const bools = [true, false];
  const runtimeDirs = [undefined, "", "/repo/apps/desktop/resources"];
  const productionFlags = [undefined, "1"];

  for (const isPackaged of bools) {
    for (const runtimeDir of runtimeDirs) {
      for (const productionFlag of productionFlags) {
        const env: Record<string, string | undefined> = {};
        if (runtimeDir !== undefined) env.COPILOT_RUNTIME_DIR = runtimeDir;
        if (productionFlag !== undefined)
          env.COPILOT_PRODUCTION = productionFlag;
        const inputs = { isPackaged, env };
        const label = JSON.stringify({ isPackaged, ...env });

        it(`no override: supervising implies production — ${label}`, () => {
          if (shouldSupervise(inputs)) {
            expect(isProductionPosture(inputs)).toBe(true);
          }
        });

        it(`explicit dev override always yields dev posture — ${label}`, () => {
          expect(
            isProductionPosture({
              ...inputs,
              env: { ...env, COPILOT_DEV: "1" },
            }),
          ).toBe(false);
        });
      }
    }
  }
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

  it("staged supervised runtime forces oidc mode / disallows dev-mint (F5 regression)", () => {
    // The supervised dev recipe must NOT offer dev-mint; the default sign-in
    // then routes to signInLocal (SIWE local-key) which works against the stack.
    const posture = resolveAuthPosture({
      isPackaged: false,
      env: { COPILOT_RUNTIME_DIR: "/repo/apps/desktop/resources" },
    });
    expect(posture.productionPosture).toBe(true);
    expect(posture.mode).toBe("oidc");
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

  it("COPILOT_DEV=1 keeps dev-mint even alongside a staged runtime (explicit override)", () => {
    const posture = resolveAuthPosture({
      isPackaged: false,
      env: {
        COPILOT_RUNTIME_DIR: "/repo/apps/desktop/resources",
        COPILOT_DEV: "1",
      },
    });
    expect(posture.productionPosture).toBe(false);
    expect(posture.mode).toBe("dev-mint");
    expect(posture.allowDevMint).toBe(true);
  });
});
