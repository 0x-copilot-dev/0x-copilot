# workspace-fs

A Node-API addon giving the capability broker the **kernel's own** root-confined,
symlink/reparse-refusing file open. Consumed only by `apps/desktop/main` —
`capabilities/host-fs.ts` requires `../../native/workspace-fs/index.cjs` relative
to the emitted `out/main/index.js`.

## Why it exists

`host-fs.ts` must open an untrusted, agent-supplied path that is _proven_ to live
beneath a grant root, without being raced by a mid-flight symlink/junction swap of
any intermediate component.

| platform | atomic primitive                                                           | addon    |
| -------- | -------------------------------------------------------------------------- | -------- |
| darwin   | `openat` + `O_NOFOLLOW_ANY` — refuses a symlink in **any** component       | optional |
| linux    | `openat2(RESOLVE_BENEATH \| RESOLVE_NO_SYMLINKS \| RESOLVE_NO_MAGICLINKS)` | required |
| win32    | per-component `NtCreateFile` walk with `FILE_OPEN_REPARSE_POINT`           | required |

`O_NOFOLLOW` guards only the **final** component on Linux and Windows, so without
the addon `host-fs.ts` falls back to `O_NOFOLLOW` plus a post-open `realpath`
recheck. That denies the same escapes, but **not atomically** — a TOCTOU window on
every confined read. darwin needs nothing extra.

## Build

```bash
npm run build:workspace-fs --workspace @0x-copilot/desktop            # host target
npm run build:workspace-fs:required --workspace @0x-copilot/desktop   # fail the build if it cannot be produced
```

`compile` and `test` both chain the first form, so a normal desktop build produces
it. Output: `prebuilds/<platform>-<arch>/workspace_fs.node` (gitignored).

**One binary per `{platform, arch}` — not per runtime.** `src/workspace_fs.c`
includes `node_api.h` and nothing else, so a plain-Node build loads unchanged in
Electron main. Measured: a Node 25 build (`process.versions.modules` 141) read
real bytes under Electron 43 (modules 148). There is deliberately no
`--runtime=electron` mode; adding a V8/`nan`/`node.h` dependency would make the
artifact per-runtime and force an `@electron/rebuild` step.

**node-gyp does not cross-compile.** `build.mjs --target <platform>-<arch>` refuses
a target that is not the host rather than emitting a host binary under the
requested target's directory name. Each target is built on a matching runner.

### Failure posture

| context                            | behaviour                                                       |
| ---------------------------------- | --------------------------------------------------------------- |
| no toolchain, default              | warn, write `prebuilds/UNAVAILABLE.txt` with the reason, exit 0 |
| no toolchain, `--require`          | exit non-zero                                                   |
| compiles but fails `selfcheck.cjs` | delete the binary, then behave as above                         |

`build.mjs` finishes by running `selfcheck.cjs` against the emitted binary and a
real temp directory: it must read real bytes back byte-exact **and** refuse a real
symlink/junction escape and a real `..`. A binary that loads but does not confine
is worse than an absent one, because `index.cjs` would treat it as a working
atomic primitive and stop warning.

```bash
node selfcheck.cjs [path/to/workspace_fs.node]   # also useful on a user's machine
```

## Load-time posture (`index.cjs`)

`loadNative()` never throws — `host-fs.ts` wraps the require in a `try/catch` that
returns `undefined`, so a throw here would be swallowed and land right back on the
silent fallback. The signal is therefore a **returned value**:

- **development / unpackaged, addon missing** → `undefined` plus one loud warning.
  Fast iteration keeps working on the non-atomic path.
- **production posture** (`app.isPackaged`, or the CLI's `COPILOT_PRODUCTION=1`, or
  a supervised `COPILOT_RUNTIME_DIR`) **on a platform that requires the addon** →
  a **fail-closed stand-in** whose `openBeneath` throws `EPERM`. Every confined
  read and write is denied rather than quietly served through the race.
  `ENOSYS`/`ENOTSUP` are deliberately not used: `host-fs.ts` maps those to "the
  kernel lacks the primitive, use the Node path", which is the outcome the
  stand-in exists to prevent.
- `COPILOT_ALLOW_NONATOMIC_WORKSPACE_FS=1` turns that denial back into a warning —
  an explicit, logged choice to accept the race.
- `COPILOT_DEV=1` forces development posture.

An unlisted platform counts as **requiring** the addon. An unknown kernel has not
been shown to close the race, and guessing permissively is how a TOCTOU window
ships.

## Packaging

| channel                              | loader                                                          | binary                                                                   |
| ------------------------------------ | --------------------------------------------------------------- | ------------------------------------------------------------------------ |
| electron-builder (`dist:*`, release) | `files:` → `<app.asar>/native/workspace-fs/index.cjs`           | `extraResources:` `prebuilds` → `<resourcesPath>/workspace-fs/<target>/` |
| `copilot` CLI (npm)                  | `assemble-payload.mjs` → `payload/desktop/native/workspace-fs/` | same directory's `prebuilds/<target>/`                                   |

The `.node` stays **out of the asar**: a native module should not be `dlopen`'d out
of an archive.

`tools/desktop-runtime/workspace-fs-audit.mjs` records presence/absence for the
staged target in `staging-manifest.json`. It audits rather than copies — nothing
reads a third location, and a staged copy no consumer resolves would be dead
weight.

### Known gap: the CLI channel is single-host

The npm tarball is assembled on one machine, so it can only carry prebuilds for
the targets that machine can compile. A Windows user installing via `npm i -g`
from a macOS-assembled publish gets **no** Windows binary, and the loader
fail-closes with a stated reason. Closing that needs a multi-OS publish matrix
that collects per-target artifacts before `npm publish`; `release-desktop.yml`
already has such a matrix, the CLI publish does not. The installer channel is
unaffected — each target is built on its own native runner.

## CI

`ci-desktop.yml`:

- `typecheck-and-test` (ubuntu) — builds best-effort and runs the addon's tests,
  which is free coverage of the `openat2` branch. Never fails for a native reason.
- `native-workspace-fs` (macos-latest, windows-latest) — builds with `--require`
  and runs `openBeneath.test.mjs` against a real temp tree, a real junction and a
  real read. **The windows-latest leg is the only evidence the `NtCreateFile` walk
  works**; before it, that branch had never been compiled by anything.

`release-desktop.yml` sets `COPILOT_REQUIRE_NATIVE_WORKSPACE_FS=1`, so a published
build cannot be missing the addon.

## Tests

| file                   | what it pins                                                                     |
| ---------------------- | -------------------------------------------------------------------------------- |
| `index.test.mjs`       | loader posture: which platforms require it, fail-closed vs warn, candidate order |
| `openBeneath.test.mjs` | real kernel behaviour: real reads, and every escape refused                      |
| `packaging.test.mjs`   | the build/packaging/CI wires still exist — the anti-orphan guard                 |

`openBeneath.test.mjs` **skips with a stated reason** when no binary is present. It
must never pass vacuously: a silent green there would be the same class of defect
as the empty directory listing that started this work.
