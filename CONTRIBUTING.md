# Contributing to 0xCopilot

Anyone can fork the repository and open a pull request. A maintainer reviews it
and merges it.

## Branches

Pull requests go to `dev`, not `main`.

```
feature ──PR──▶ dev ──promote-to-main.yml──▶ main ──release-cli.yml──▶ npm
```

- `dev` is the integration branch. Every change lands there first, and CI runs
  on it.
- `main` is the released branch. It moves only when the promotion workflow
  runs, and that workflow will not promote a `dev` commit until all of its
  checks have finished and passed. So `main` is usually behind `dev`.

Start your work from `dev`:

```bash
git checkout dev && git pull
git checkout -b feat/your-change
gh pr create --base dev
```

## Checks and reviews

Four checks must pass on every pull request: `lint-and-secrets`,
`tenants-lint`, `repo-gates` and `host-typecheck`. They run in parallel and
usually finish within a few minutes.

`lint-and-secrets` runs the repository's pre-commit hooks — ruff for Python,
prettier for JavaScript, TypeScript, CSS, Markdown and YAML — and a secret
scan. To catch the same problems before you push, install the hooks with
`make setup-hooks`.

Merging needs write access, which the maintainers hold. A pull request from an
outside contributor needs two approvals.

## Commit messages

Use [Conventional Commit](https://www.conventionalcommits.org) subjects:
`feat:`, `fix:`, `feat!:`, or a `BREAKING CHANGE:` footer. The CLI changelog
and the next version number are both built from them, and a subject that does
not follow the format is skipped rather than guessed at.

## Releases

Releases are started by hand, and run as a dry run unless told otherwise. While
the CLI is at `0.x`, a breaking change bumps the minor number
(`0.1.4 → 0.2.0`) and everything else bumps the patch number, because npm
reads `^0.1.4` as `>=0.1.4 <0.2.0`.

[Branching and release](docs/ci-cd/branching-and-release.md) has the full
detail, including how promotion and publishing are run.

## Development setup

The commands for installing dependencies, running the stack locally and running
each service's tests are in [CLAUDE.md](CLAUDE.md#commands).
[API testing](docs/dev-testing.md) shows how to call the local API with curl or
Postman.
