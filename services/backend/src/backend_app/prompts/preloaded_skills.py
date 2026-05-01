"""Built-in Skill markdown seeded into each user's Skill registry."""

from __future__ import annotations

PRELOADED_SKILL_MARKDOWNS: tuple[str, ...] = (
    """---
name: generate-status-report
description: Generate project status reports from connected work systems. Use when the user asks for weekly updates, project summaries, Jira status, blockers, progress reports, or publishing status to Confluence.
allowed_tools: [load_mcp_server, call_mcp_tool]
metadata:
  category: reporting
---
# Generate Status Report

## Workflow

1. Clarify scope before querying:
   - project or initiative
   - time period
   - audience
   - whether to publish to Confluence
2. Query the relevant connected system for completed, in-progress, blocked, and high-priority work.
3. Summarize metrics, highlights, blockers, risks, and next priorities.
4. If publishing is requested, confirm the Confluence space or destination before creating or updating a page.

## Report Structure

Use a concise report:

```markdown
# [Project] Status Report - [Date]

## Summary
[Overall status: on track, at risk, or blocked.]

## Metrics
- Completed:
- In progress:
- Blocked:
- High priority:

## Highlights
- [Key accomplishment with issue links where possible]

## Blockers And Risks
- [Blocker, impact, owner, next action]

## Next Priorities
- [Specific upcoming work]
```

## Quality Bar

Be data-driven and cite issue keys or source links. Ask before publishing to Confluence when the destination is unclear.
""",
    """---
name: incident-review
description: Build incident summaries, timelines, impact analysis, owners, and follow-up actions. Use when the user asks for an incident review, postmortem, outage summary, SEV report, or remediation plan.
allowed_tools: [load_mcp_server, call_mcp_tool]
metadata:
  category: operations
---
# Incident Review

## Workflow

1. Identify the incident scope: service, time window, severity, and audience.
2. Gather timeline evidence from connected sources such as tickets, docs, logs, or chat.
3. Separate facts from hypotheses.
4. Produce a concise review with owners and follow-up actions.

## Output Template

```markdown
# Incident Review: [Title]

## Executive Summary
[What happened, impact, current status.]

## Timeline
- [time] [event] [source]

## Impact
- Users affected:
- Duration:
- Business or operational impact:

## Root Cause
[Known cause, or state what is still unknown.]

## Remediations
- [ ] [Action] - Owner: [name] - Due: [date]
```

## Rules

Call out unknowns explicitly. Do not invent timelines or root causes.
""",
    """---
name: launch-risk-review
description: Review launch plans and identify readiness gaps, blockers, dependencies, and go/no-go risks. Use when the user asks about launch readiness, release risk, rollout planning, or pre-launch review.
allowed_tools: [load_mcp_server, call_mcp_tool]
metadata:
  category: planning
---
# Launch Risk Review

## Workflow

1. Clarify launch scope, target date, audience, and success criteria.
2. Gather plan documents, open issues, blockers, dependencies, and recent changes.
3. Assess risk across product, engineering, operations, security, support, and customer communication.
4. Return a go/no-go recommendation with concrete mitigations.

## Risk Matrix

Use this structure:

```markdown
# Launch Risk Review: [Launch]

## Recommendation
[Go / go with mitigations / no-go]

## Top Risks
- Risk: [description]
  Impact: [impact]
  Likelihood: [low/medium/high]
  Owner: [name]
  Mitigation: [action]

## Readiness Checklist
- [ ] Rollback plan
- [ ] Monitoring and alerts
- [ ] Support handoff
- [ ] Security/privacy review
- [ ] Customer communications
```

## Rules

Prefer specific owners and dates over generic advice. Highlight missing evidence.
""",
    """---
name: customer-brief
description: Create customer or account briefs from connected CRM, docs, support, and project context. Use when preparing for customer calls, account reviews, renewals, escalations, or executive briefings.
allowed_tools: [load_mcp_server, call_mcp_tool]
metadata:
  category: customer
---
# Customer Brief

## Workflow

1. Confirm the customer/account name and meeting purpose.
2. Gather recent activity, open support issues, active opportunities, risks, and stakeholders from connected systems.
3. Distill the brief for the requested audience.
4. Include suggested talking points and open questions.

## Brief Template

```markdown
# Customer Brief: [Customer]

## Context
[Relationship, plan, products, current goals.]

## Recent Activity
- [Date/source] [summary]

## Open Items
- [Issue/opportunity] - Owner: [name] - Status: [status]

## Risks And Opportunities
- Risk:
- Opportunity:

## Suggested Talking Points
- [Specific question or update]
```

## Rules

Do not expose unsupported assumptions. Mark stale or missing data clearly.
""",
    """---
name: knowledge-base-answer
description: Answer questions from enterprise knowledge sources with citations and uncertainty notes. Use when the user asks about policies, internal docs, procedures, project context, or source-grounded answers.
allowed_tools: [load_mcp_server, call_mcp_tool]
metadata:
  category: research
---
# Knowledge Base Answer

## Workflow

1. Identify the question and the likely source system.
2. Search connected knowledge sources before answering when source grounding matters.
3. Synthesize the answer with citations, conflicts, and gaps.
4. If results conflict, explain the conflict instead of choosing silently.

## Answer Format

```markdown
## Answer
[Direct answer in a few sentences.]

## Evidence
- [Source title or object]: [relevant fact]

## Caveats
- [Missing, stale, or conflicting information]
```

## Rules

Prefer concise answers. Cite source names or links whenever available. Say when the available sources are insufficient.
""",
)
