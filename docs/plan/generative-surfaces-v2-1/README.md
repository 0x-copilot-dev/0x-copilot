# Generative Surfaces v2.1 planning package

This folder contains the Generative Surfaces v2.1 implementation program.

## Generative surfaces and governed effects

The existing 17-PR A–E program defines immutable artifacts, selective
presentation, the universal Operation Gateway, no-executor staging, exact
approval, durable commit/reconcile, workspace overlays, MCP convergence,
subagents, sandbox/browser adapters, accountability, and cutover.

- [Product overview](00-overview.md)
- [System design record](01-sdr.md)
- [A–E PRD index](02-prds.md)

## Related program: agent runtime quality, efficiency, and learning

The separate program builds on the A–E contracts. It covers prompt/cache
architecture, tool discovery and execution efficiency, research/grounding,
multi-file edit planning, final-answer verification, skills, durable memory,
learning from completed and historical work, routines, goals, cross-run
orchestration, and governed extensibility.

- [Agent Runtime Quality, Efficiency, and Learning — normative README](../agent-runtime-quality/README.md)

That program's README is the source of truth for scope, PRD ownership, dependency
order, launch gates, and the complete implementation checklist.

## Shared rule

The second program must not create a parallel execution or approval path.
Every capability continues to use the A–E operation, artifact, effect,
workspace, audit, retention, and replay contracts. Where current ai-backend
behavior is already stronger—brokered desktop authority, staged effects,
citation provenance, deterministic event replay, and subagent authority
intersection—it is preserved rather than replaced.
