# design-parity

Measure CSS/pixel parity between a Claude Design mock and the live app by diffing
**computed styles** (not screenshots). See [`.claude/skills/design-parity/SKILL.md`](../../.claude/skills/design-parity/SKILL.md)
for the full workflow. First wired surface: the first-run FTUE (`surfaces/first-run/`);
its baseline report is `surfaces/first-run/out/report.md`.

## Installing the skill for Claude Code

`.claude/` is gitignored, so the discovery copy at `.claude/skills/design-parity/SKILL.md`
is machine-local. The canonical, version-controlled skill doc is `tools/design-parity/SKILL.md`.
To (re)install locally: `mkdir -p .claude/skills/design-parity && cp tools/design-parity/SKILL.md .claude/skills/design-parity/`.
