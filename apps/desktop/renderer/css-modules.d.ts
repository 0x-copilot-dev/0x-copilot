// Ambient declaration for side-effect CSS imports (e.g.
// `import "@0x-copilot/design-system/styles.css"`). esbuild bundles these via
// its `.css` loader (see esbuild.config.mjs); at type-check time TypeScript
// needs a module shape for the `.css` specifier. The web frontend gets this
// from Vite's `vite/client` types, but the desktop renderer is built with
// esbuild and has no Vite typings, so declare it here.
declare module "*.css";
