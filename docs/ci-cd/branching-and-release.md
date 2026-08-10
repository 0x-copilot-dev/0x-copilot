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

> **Pull a stale checkout before you judge anything in it.** A working tree that
> has not been pulled in days is not merely behind — it makes **deletions look
> like additions**. Diff it against a newer `dev` and every file deleted in the
> meantime shows up as a file that "exists on no branch", which is the same
> signature as genuinely new work.
>
> This cost most of a day on 2026-08-10: a tree last pulled on 08-06 resurfaced
> all 35 modules deleted that day by `e5f8ef2b`, and they read as 12k LOC of
> recovered feature work — tests and all — until the blob hashes showed all 63
> files byte-identical to the versions that had been deleted on purpose. The
> tell, applied to any one file:
>
> ```bash
> git log --diff-filter=AD --follow -- <path>   # an A directly above a D = resurrection
> ```
>
> See `docs/audit/ai-backend-smells/PENDING-WIRINGS.md` §Re-adjudicated.

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

## What each branch actually enforces

|                            | `main`  | `dev`                                            |
| -------------------------- | ------- | ------------------------------------------------ |
| deletion / force-push      | blocked | blocked                                          |
| linear history             | —       | —                                                |
| pull request + 2 approvals | —       | required                                         |
| status checks              | —       | `lint-and-secrets`, `tenants-lint`, `repo-gates` |

**`main` deliberately carries fewer rules than `dev`**, which reads backwards
until you try the alternative. Review and checks live on `dev`, and `main` only
ever receives commits that already passed them — so repeating the rules on `main`
adds nothing and breaks the promotion path, because the two workflows that
legitimately move `main` cannot satisfy them:

- a `pull_request` rule blocks `promote-to-main.yml`'s fast-forward outright;
- `required_status_checks` blocks `release-cli.yml`'s version-bump commit, which
  is new and so has no checks of its own.

The usual escape is a GitHub Actions bypass actor. It is **unavailable here** —
the API rejects it with _"Actor GitHub Actions integration must be part of the
ruleset source or owner organization"_. Integration bypass actors require an
organization, and this repo is owned by a user. Same root cause as the CODEOWNERS
teams problem.

`required_linear_history` is absent from `main` for a related reason, found by
running the promotion for real: it forbids merge commits, and every PR merged
into `dev` creates one that promotion then fast-forwards onto `main`
(`remote: - This branch must not contain merge commits.`). Squash-only merges
into `dev` would satisfy the rule, but `tools/cli_release.py` builds the
changelog from the individual Conventional Commits inside a PR, so squashing
would coarsen every release note to one line per PR.

So `main`'s quality gate is `promote-to-main.yml`, not the ruleset. The residual
risk is worth naming plainly: **a collaborator can still push directly to `main`.**
Nothing prevents that except the two-person collaborator list. Moving the repo
into an organization would restore the bypass actor and let the stricter rules
come back.

## Why so few required status checks

`dev` requires only `lint-and-secrets`, `tenants-lint` and `repo-gates`.

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
5. Publishes to npm as the trusted publisher (provenance is automatic).
6. **Then** commits the version bump and changelog, tags `cli-v<version>`, pushes.
7. Creates a GitHub Release with the changelog entry as its body.
8. Back-merges the release commit into `dev`.

Publish precedes tagging on purpose: tagging first leaves a tag pointing at a
version that does not exist on the registry if the publish then fails. Step 8
matters because the release commit lands on `main`, leaving it one commit ahead
of `dev`; without the back-merge the next fast-forward promotion is rejected.

> **Step 8 currently fails, and the run goes red after a successful publish.**
> The workflow pushes the release commit straight to `dev`, which the ruleset
> refuses:
>
> ```
> remote: error: GH013: Repository rule violations found for refs/heads/dev.
> remote: - Changes must be made through a pull request.
> ```
>
> Observed on `v0.2.1` (2026-08-10). **npm, `main`, the tag and the GitHub Release
> were all correct** — only the housekeeping failed, so a red run here does not
> mean a failed release. Check npm before re-dispatching: re-running a publish
> that already succeeded is refused by step 3 anyway.
>
> The consequence is the one this section warns about: `main` is left one commit
> ahead and **stops being an ancestor of `dev`**, so the next promotion is blocked
> with no obvious cause. Until the workflow opens a PR (or gains a ruleset bypass),
> finish a release by hand:
>
> ```bash
> git checkout -b chore/back-merge-cli-v<version> origin/dev
> git merge origin/main && git push -u origin HEAD
> gh pr create --base dev --title "chore: back-merge the cli v<version> release commit"
> ```
>
> That PR needs `--admin` to merge: the release commit carries `[skip ci]`, so it
> has **zero** check runs and the four required checks can never appear on it.

### Where the changelog goes

Three places, from one generated entry: `tools/cli/CHANGELOG.md` (committed and
shipped in the tarball), the GitHub Release body, and the workflow run summary.

## Applying branch protection

`deploy/branch-protection.json` is the source of truth; the reconciler is
`tools/apply_branch_protection.py`. It is idempotent — a second run against an
unchanged repo prints `No-op` — so any diff it shows is real drift. That property
is load-bearing and tested: GitHub materializes defaults it was never sent
(`required_reviewers: []`) and reorders `bypass_actors`, and without normalizing
both the tool claimed drift on every run, which is the same as reporting nothing.

**Applying needs a credential `GITHUB_TOKEN` cannot have.** Repository
administration is not a delegable Actions permission, so ruleset writes require a
fine-grained PAT with _Administration: read and write_. Two ways to run it —
locally as a repo admin, with no PAT to create or rotate:

```bash
GITHUB_REPOSITORY=0x-copilot-dev/0x-copilot PYTHONPATH=tools python tools/apply_branch_protection.py --dry-run
```

then `--apply`. Or through the workflow, which needs a `REPO_ADMIN_TOKEN` secret
to apply but can always dry-run without one:

```bash
gh workflow run apply-branch-protection.yml -f dry_run=true
```

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
