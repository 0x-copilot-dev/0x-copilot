// @vitest-environment node
import { describe, expect, it, vi, type Mock } from "vitest";

import {
  APP_DISPLAY_NAME,
  APP_ID,
  applyBrandDockIcon,
  applyBrandIdentity,
  type BrandableApp,
} from "./branding";

interface FakeApp extends BrandableApp {
  readonly setName: Mock<(name: string) => void>;
  readonly setAppUserModelId: Mock<(id: string) => void>;
}

function fakeApp(
  overrides: Partial<Pick<BrandableApp, "isPackaged" | "dock">> = {},
): FakeApp {
  return {
    isPackaged: false,
    setName: vi.fn<(name: string) => void>(),
    setAppUserModelId: vi.fn<(id: string) => void>(),
    ...overrides,
  };
}

describe("applyBrandIdentity", () => {
  it("names the app 0xCopilot on every platform", () => {
    for (const platform of ["darwin", "win32", "linux"] as const) {
      const app = fakeApp();
      applyBrandIdentity(app, { platform });
      expect(app.setName).toHaveBeenCalledWith(APP_DISPLAY_NAME);
    }
  });

  it("sets the AppUserModelID to the electron-builder appId on Windows only", () => {
    const win = fakeApp();
    applyBrandIdentity(win, { platform: "win32" });
    expect(win.setAppUserModelId).toHaveBeenCalledWith(APP_ID);

    const mac = fakeApp();
    applyBrandIdentity(mac, { platform: "darwin" });
    expect(mac.setAppUserModelId).not.toHaveBeenCalled();
  });
});

describe("applyBrandDockIcon", () => {
  const inputs = {
    platform: "darwin",
    iconPngPath: "/out/main/icon.png",
  } as const;

  it("applies the dock icon for unpackaged macOS launches", () => {
    const setIcon = vi.fn();
    const app = fakeApp({ dock: { setIcon } });
    applyBrandDockIcon(app, inputs);
    expect(setIcon).toHaveBeenCalledWith("/out/main/icon.png");
  });

  it("leaves a packaged macOS app alone (bundle already carries icon.icns)", () => {
    const setIcon = vi.fn();
    const app = fakeApp({ isPackaged: true, dock: { setIcon } });
    applyBrandDockIcon(app, inputs);
    expect(setIcon).not.toHaveBeenCalled();
  });

  it("does nothing off macOS", () => {
    const setIcon = vi.fn();
    const app = fakeApp({ dock: { setIcon } });
    applyBrandDockIcon(app, { ...inputs, platform: "win32" });
    expect(setIcon).not.toHaveBeenCalled();
  });

  it("never lets a broken icon block boot", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const app = fakeApp({
        dock: {
          setIcon: () => {
            throw new Error("bad png");
          },
        },
      });
      expect(() => applyBrandDockIcon(app, inputs)).not.toThrow();

      // A dock-less app object (non-mac electron) is also safe.
      expect(() => applyBrandDockIcon(fakeApp(), inputs)).not.toThrow();
    } finally {
      warn.mockRestore();
    }
  });
});
