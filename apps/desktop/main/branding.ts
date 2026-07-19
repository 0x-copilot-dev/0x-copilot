// Runtime brand identity for the UNPACKAGED launch paths (`npm run dev` and
// the `copilot` CLI both spawn the stock Electron binary against this
// directory). A packaged install gets its name + icon from its own bundle
// (electron-builder.yml: productName + build/icon.icns|ico), but an unpackaged
// launch inherits Electron's, so the dock/taskbar shows the Electron atom.
//
// What CAN be fixed at runtime, per platform:
//   - app.setName: userData path + menu labels (all platforms, all launches)
//   - the macOS dock ICON via app.dock.setIcon (unpackaged only; a packaged
//     bundle already carries icon.icns)
//   - the Windows AppUserModelID, so taskbar grouping + notifications
//     attribute to our appId instead of "electron"
// The macOS dock TOOLTIP ("Electron") cannot be changed from JS — the Dock
// reads it from the launched bundle's Info.plist, not from the process. The
// CLI fixes it by launching through a branded shell bundle
// (tools/cli/lib/mac-shell.mjs); plain `npm run dev` keeps the stock tooltip.
//
// Pure-inputs helpers (no electron import) so they are unit-testable without
// the electron module graph — same pattern as posture.ts.

export const APP_DISPLAY_NAME = "0xCopilot";

// Must stay equal to electron-builder.yml `appId` and the branded shell's
// CFBundleIdentifier (tools/cli/lib/mac-shell.mjs) so notifications and
// taskbar grouping attribute to one app across launch modes.
export const APP_ID = "com.0x-copilot.app";

export interface BrandableApp {
  readonly isPackaged: boolean;
  setName(name: string): void;
  setAppUserModelId(id: string): void;
  readonly dock?: { setIcon(icon: string): void };
}

export interface BrandingInputs {
  readonly platform: NodeJS.Platform;
}

/** Early identity — call before ready and before anything reads userData. */
export function applyBrandIdentity(
  app: BrandableApp,
  inputs: BrandingInputs,
): void {
  app.setName(APP_DISPLAY_NAME);
  if (inputs.platform === "win32") {
    app.setAppUserModelId(APP_ID);
  }
}

export interface DockIconInputs extends BrandingInputs {
  /** Absolute path to the PNG staged next to the main bundle (out/main). */
  readonly iconPngPath: string;
}

/** After ready — give unpackaged macOS launches the real dock icon. */
export function applyBrandDockIcon(
  app: BrandableApp,
  inputs: DockIconInputs,
): void {
  if (inputs.platform !== "darwin") return;
  if (app.isPackaged) return; // the bundle's icon.icns is already showing
  try {
    app.dock?.setIcon(inputs.iconPngPath);
  } catch (err) {
    // A missing or unreadable icon must never block boot.
    console.warn("[branding] dock icon not applied:", err);
  }
}
