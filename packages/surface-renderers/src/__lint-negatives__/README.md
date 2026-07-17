# Lint negatives — DO NOT FIX THESE

The files in this directory deliberately violate the ESLint rule defined
in `packages/surface-renderers/eslint.config.js`. Each file targets one
ban — running ESLint over the directory MUST report errors on every
file. That is the assertion.

Excluded from:

- TypeScript compilation (`tsconfig.json` excludes this directory).
- Vitest discovery (`vitest.config.ts` excludes this directory).

Exercised by:

```bash
npm run lint:negatives --workspace @0x-copilot/surface-renderers
```

The script asserts ESLint exits non-zero and reports at least one error
per file. If ESLint passes on any of these, the rule has regressed and
the ban no longer fires for tier-1 renderers.

When a new ban is added to the rule, add a sibling file here that
violates exactly that ban. The script's per-file count is what guards
against accidental removals of an existing ban during refactors.
