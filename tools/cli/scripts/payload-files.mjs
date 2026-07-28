// Files required to execute the published desktop-runtime staging entry point.
// Both assembly and the build-free package check consume this list so adding a
// new relative import to stage.mjs cannot silently produce a broken tarball.
export const DESKTOP_RUNTIME_FILES = [
  "stage.mjs",
  "browser-runtime.mjs",
  "macos-signing.cjs",
  "manifest.json",
  "features/monty-requirements.txt",
];
