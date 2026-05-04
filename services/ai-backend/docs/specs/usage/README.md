# Usage track specs (B-series)

Functional + technical specs live in [docs/roadmap/](../../../../../docs/roadmap/). The files here are the _implementation contracts_ — what the change adds, what it reuses, what it deliberately skips. Read both before changing behavior.

| PR  | Spec                                           | Roadmap                                                                                      |
| --- | ---------------------------------------------- | -------------------------------------------------------------------------------------------- |
| B1  | (in roadmap)                                   | [11-b1-runtime-run-usage.md](../../../../../docs/roadmap/11-b1-runtime-run-usage.md)         |
| B2  | (in roadmap)                                   | [12-b2-per-step-usage.md](../../../../../docs/roadmap/12-b2-per-step-usage.md)               |
| B3  | (in roadmap)                                   | [13-b3-pricing-and-cost.md](../../../../../docs/roadmap/13-b3-pricing-and-cost.md)           |
| B4  | (in roadmap)                                   | [14-b4-aggregation-endpoints.md](../../../../../docs/roadmap/14-b4-aggregation-endpoints.md) |
| B5  | [B5-context-command.md](B5-context-command.md) | [19-b5-context-command.md](../../../../../docs/roadmap/19-b5-context-command.md)             |
| B6  | [B6-usage-command.md](B6-usage-command.md)     | [20-b6-usage-command.md](../../../../../docs/roadmap/20-b6-usage-command.md)                 |
| B7  | [B7-budgets.md](B7-budgets.md)                 | [21-b7-budgets.md](../../../../../docs/roadmap/21-b7-budgets.md)                             |
| B8  | [B8-tool-budget.md](B8-tool-budget.md)         | [22-b8-tool-budget.md](../../../../../docs/roadmap/22-b8-tool-budget.md)                     |

## Migration numbering note

The roadmap numbers B7 as `0008_usage_budgets.sql` and B8 as `0009_runtime_tool_budgets.sql`, but `0008_rls_tenant_isolation.sql` (C5) already shipped first in this branch. The actual migration numbers are:

- B7 → `0009_usage_budgets.sql`
- B8 → `0010_runtime_tool_budgets.sql`

Both new migrations are added to the C5 RLS policy list at write time.
