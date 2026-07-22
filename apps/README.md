# Apps

Apps are product clients. They own user experience, local app concerns, and
client-side state, but they do not own backend orchestration or persistence.

## Current Apps

- `frontend`: `@0x-copilot/frontend` — shipping web app built with React,
  TypeScript, and Vite. It calls `backend-facade` over `/v1/*` and imports
  shared contracts from `@0x-copilot/api-types`.
- `desktop`: `@0x-copilot/desktop` — the flagship shipping product, an Electron
  desktop client installed via the `copilot` CLI.
- `website`: `@0x-copilot/website` — the Astro marketing site for
  `0xcopilot.tech`, deployed to GitHub Pages.

## Engineering Rules

- Apps call `backend-facade`; they must not call `backend` or `ai-backend`
  directly without an accepted spec.
- Apps may import shared packages such as `@0x-copilot/api-types` and
  `@0x-copilot/design-system`.
- Apps must not import implementation code from `services/*`.
- Each app owns its build config, dependency environment, Dockerfile or native
  packaging, tests, and deploy path.

See also:

- `../docs/architecture/workspace-topology.md`
- `../docs/architecture/service-boundaries.md`
