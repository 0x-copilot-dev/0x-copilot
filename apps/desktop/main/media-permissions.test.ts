// @vitest-environment node

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import {
  allowsAudioPermissionCheck,
  allowsAudioPermissionRequest,
} from "./media-permissions";

describe("desktop media permission policy", () => {
  it("allows audio checks from the trusted app origin", () => {
    expect(
      allowsAudioPermissionCheck({
        permission: "media",
        requestingOrigin: "app://app",
        requestingUrl: "app://app/",
        mediaType: "audio",
      }),
    ).toBe(true);
  });

  it("denies video and every non-media permission", () => {
    expect(
      allowsAudioPermissionCheck({
        permission: "media",
        requestingOrigin: "app://app",
        mediaType: "video",
      }),
    ).toBe(false);
    expect(
      allowsAudioPermissionCheck({
        permission: "notifications",
        requestingOrigin: "app://app",
      }),
    ).toBe(false);
  });

  it("denies audio checks from any other origin", () => {
    expect(
      allowsAudioPermissionCheck({
        permission: "media",
        requestingOrigin: "https://example.com",
        mediaType: "audio",
      }),
    ).toBe(false);
  });

  it("allows only audio-only media requests from app://app", () => {
    expect(
      allowsAudioPermissionRequest({
        permission: "media",
        requestingUrl: "app://app/",
        mediaTypes: ["audio"],
      }),
    ).toBe(true);
    expect(
      allowsAudioPermissionRequest({
        permission: "media",
        requestingUrl: "app://app/",
        mediaTypes: ["audio", "video"],
      }),
    ).toBe(false);
    expect(
      allowsAudioPermissionRequest({
        permission: "media",
        requestingUrl: "https://example.com/",
        mediaTypes: ["audio"],
      }),
    ).toBe(false);
  });

  it("ships the macOS microphone usage copy and audio-input entitlement", () => {
    const builderConfig = readFileSync(
      new URL("../electron-builder.yml", import.meta.url),
      "utf8",
    );
    const entitlements = readFileSync(
      new URL("../build/entitlements.mac.plist", import.meta.url),
      "utf8",
    );

    expect(builderConfig).toContain("NSMicrophoneUsageDescription");
    expect(entitlements).toContain(
      "<key>com.apple.security.device.audio-input</key>",
    );
    expect(entitlements).toMatch(
      /<key>com\.apple\.security\.device\.audio-input<\/key>\s*<true\/>/u,
    );
  });
});
