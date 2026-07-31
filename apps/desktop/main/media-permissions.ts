import type { Session } from "electron";

import { APP_HOST, APP_SCHEME } from "./app-protocol";

function isTrustedAppUrl(value: string | undefined): boolean {
  if (value === undefined || value === "") return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === `${APP_SCHEME}:` && parsed.host === APP_HOST;
  } catch {
    return false;
  }
}

export function allowsAudioPermissionCheck(input: {
  readonly permission: string;
  readonly requestingOrigin: string;
  readonly requestingUrl?: string;
  readonly fallbackUrl?: string;
  readonly mediaType?: string;
}): boolean {
  if (input.permission !== "media") return false;
  if (input.mediaType === "video" || input.mediaType === "unknown")
    return false;
  return isTrustedAppUrl(
    input.requestingUrl || input.requestingOrigin || input.fallbackUrl,
  );
}

export function allowsAudioPermissionRequest(input: {
  readonly permission: string;
  readonly requestingUrl?: string;
  readonly fallbackUrl?: string;
  readonly mediaTypes?: readonly string[];
}): boolean {
  if (input.permission !== "media") return false;
  const mediaTypes = input.mediaTypes;
  if (
    mediaTypes !== undefined &&
    (mediaTypes.includes("video") || !mediaTypes.includes("audio"))
  ) {
    return false;
  }
  return isTrustedAppUrl(input.requestingUrl || input.fallbackUrl);
}

/**
 * Electron otherwise auto-approves every renderer permission request. Keep the
 * desktop renderer's one media capability narrow: audio-only, main app origin.
 */
export function configureMediaPermissions(electronSession: Session): void {
  electronSession.setPermissionCheckHandler(
    (webContents, permission, requestingOrigin, details) =>
      allowsAudioPermissionCheck({
        permission,
        requestingOrigin,
        requestingUrl: details.requestingUrl,
        fallbackUrl: webContents?.getURL(),
        mediaType: details.mediaType,
      }),
  );
  electronSession.setPermissionRequestHandler(
    (webContents, permission, callback, details) => {
      const mediaTypes =
        "mediaTypes" in details ? details.mediaTypes : undefined;
      callback(
        allowsAudioPermissionRequest({
          permission,
          requestingUrl: details.requestingUrl,
          fallbackUrl: webContents.getURL(),
          mediaTypes,
        }),
      );
    },
  );
}
