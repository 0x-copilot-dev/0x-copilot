# Branching, protection and release

How code reaches `main` and how `@0x-copilot/cli` reaches npm.

## The model

```
feature branch ──PR──▶ dev ──promote-to-main.yml──▶ main ──release-cli.yml──▶ npm
                        │                            │
                   CI runs here                 released state
```

- **`dev`** is the integration branch. Every PR merges here and CI runs on it.
- **`main`** is the released branch. It moves only via `promote-to-main.yml`,
  which fast-forwards it to a `dev` commit whose checks are all green, plus the
  version-bump commit `release-cli.yml` writes.
- **npm** is published only from `main`, by manual dispatch.

## Who can merge

Merging requires write access. Write is granted to exactly two collaborators:
`0x-copilot-dev` and `parthpahwa1`. Anyone may fork and open a PR; nobody else
can merge one.

This is enforced by **repository access, not by rulesets** — a ruleset cannot
express "only these logins may merge". Keep the collaborator list short and the
property holds. Adding a collaborator with write access grants merge rights, so
that action is the real control.

Reviews:

| Author                            | Approvals needed                                   |
| --------------------------------- | -------------------------------------------------- |
| `0x-copilot-dev` or `parthpahwa1` | none — both are ruleset bypass actors              |
| anyone else                       | 2, which means both collaborators, plus CODEOWNERS |

GitHub has no per-author review count; bypass actors are the only mechanism that
expresses "no review for these two, full review for everyone else".

## Why so few required status checks

`main` and `dev` require only `lint-and-secrets`, `tenants-lint` and
`repo-gates`.

That is deliberate. **Every other workflow is path-filtered, and GitHub treats a
required check that never runs as pending** — requiring `ci-desktop` would block
every PR that does not touch `apps/desktop/`, forever, on a check that will never
report. Only unconditional checks can safely be required per-PR.

The real gate is therefore `promote-to-main.yml`, which asks a question a PR
rule cannot: _is every check that ran on this dev commit green?_ It enumerates
the commit's check runs and refuses on any failure — and on any check still
running, because a yellow tab is not a pass. That is precisely the question
nobody asked when `ci-desktop` and `ci-e2-final-conformance` went red on `main`.

## Releasing to npm

`release-cli.yml`, dispatched manually against `main`.

### Authentication: trusted publishing, not a token

There is no `NPM_TOKEN`. `@0x-copilot/cli` is configured on npmjs.com with a
**trusted publisher** (OIDC) naming this repository and this workflow file, and
the npm CLI exchanges the job's short-lived GitHub OIDC token for publish rights.
Nothing long-lived exists to leak or rotate.

Three consequences worth knowing before something fails at the last step:

- **The workflow filename is part of the trust record.** Renaming
  `release-cli.yml` breaks publishing until the npm-side record is updated.
- **npm >= 11.5.1 and Node >= 22.14.0 are required.** Node 22 ships npm 10.x,
  which has no OIDC support and falls through to token auth that does not exist
  here — `ENEEDAUTH`, after the entire payload has already been built. The
  workflow upgrades npm explicitly rather than trusting the runtime's default.
- **`--provenance` is not passed.** Publishing through a trusted publisher
  generates and publishes the attestation automatically; the flag is redundant.

Settings live at npmjs.com → the package → Settings → Trusted Publisher:
publisher `GitHub Actions`, `0x-copilot-dev` / `0x-copilot`, workflow
`release-cli.yml`, allowed action `npm publish`. Environment name is left blank;
setting one adds a second approval gate but requires declaring the same
`environment:` on the job.

Inputs:

- **`bump`** — `auto` (default), `patch`, `minor`, `major`
- **`dry_run`** — `true` by default

A dry run computes the version, renders the changelog into the job summary, and
changes nothing. Read it, then re-dispatch with `dry_run=false`.

### Versioning while the package is 0.x

npm resolves `^0.1.4` as `>=0.1.4 <0.2.0`. Before 1.0 the **minor** digit is what
actually breaks consumers, so it plays the role major will play later:

| Change                                              | Bump  | Example         |
| --------------------------------------------------- | ----- | --------------- |
| breaking (`feat!:`, or a `BREAKING CHANGE:` footer) | minor | `0.1.4 → 0.2.0` |
| feature or fix                                      | patch | `0.1.4 → 0.1.5` |
| forcing `major` by hand                             | major | `0.1.4 → 1.0.0` |

Forcing `major` is how the package graduates to 1.0.0. After that the same code
applies ordinary semver, because the arithmetic keys off whether the current
major is 0.

`auto` derives the bump from Conventional Commits since the last `cli-v*` tag. A
commit whose subject is not conventional is ignored rather than guessed at — it
cannot silently force a bump.

### What a release does, in order

1. Runs the release tooling's own tests.
2. Computes the plan and writes it to the job summary.
3. Refuses if that version already exists on npm.
4. Builds the payload (`desktop` + `frontend`, then `assemble`, then
   `check:packed`) — the same sequence `ci-cli.yml` proves on every PR.
5. Publishes to npm with `--provenance`.
6. **Then** commits the version bump and changelog, tags `cli-v<version>`, pushes.
7. Creates a GitHub Release with the changelog entry as its body.
8. Back-merges the release commit into `dev`.

Publish precedes tagging on purpose: tagging first leaves a tag pointing at a
version that does not exist on the registry if the publish then fails. Step 8
matters because the release commit lands on `main`, leaving it one commit ahead
of `dev`; without the back-merge the next fast-forward promotion is rejected.

### Where the changelog goes

Three places, from one generated entry: `tools/cli/CHANGELOG.md` (committed and
shipped in the tarball), the GitHub Release body, and the workflow run summary.

## Applying branch protection

`deploy/branch-protection.json` is the source of truth. Apply it with
`apply-branch-protection.yml` — dry run first:

```bash
gh workflow run apply-branch-protection.yml -f dry_run=true
```

Then `dry_run=false` to apply. The reconciler is `tools/apply_branch_protection.py`,
covered by `tools/test_apply_branch_protection.py`.

> The previous version of that logic lived in a YAML heredoc, carried a
> module-level `return`, and raised `SyntaxError` on every dispatch — so the
> rulesets were never applied and `main` sat unprotected while red commits
> merged. Nothing caught it because a heredoc is invisible to ruff and pytest.
> Workflow logic belongs in `tools/` with a test; `repo-gates` now fails the
> build if any workflow grows an embedded script that does not compile, or one
> that interpolates `${{ }}` into a Python program body.

## Setup checklist

Ordering matters — protection last, or it locks out the setup itself.

- [x] `parthpahwa1` accepts the collaborator invite (write access)
- [x] Configure the npm trusted publisher for `@0x-copilot/cli`
      (GitHub Actions, `0x-copilot-dev/0x-copilot`, `release-cli.yml`,
      allow `npm publish`). No repository secret is needed.
- [ ] Push the `dev` branch from `main`
- [ ] Push the baseline tag `cli-v0.1.4` at the current `main`, so the first
      automated release describes only what came after it
- [ ] Dry-run `apply-branch-protection.yml`, read the diff, then apply
- [ ] Dry-run `release-cli.yml` and confirm the computed version and changelog
