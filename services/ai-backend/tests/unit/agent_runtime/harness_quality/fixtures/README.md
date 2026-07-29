# Agent runtime baseline traces

`active_path_baseline_traces.v1.json` freezes the Step 0 observable milestones
for ordinary chat, MCP read/auth/write staging, local tools, approval resume,
large-result admission, subagents, workspace drafts, timeout, cancellation, and
provider failure.

The fixture is deliberately content-free. Each compact step is:

```text
[event_type, source, capability_id | null, payload_shape_digest]
```

`payload_shape_digest` binds the reviewed synthetic shape descriptor
`{event_type, journey_id, sequence_no, shape_revision}`. It is not a copy of a
user event body. Raw prompts, tool arguments/results, credentials, answer text,
and runtime IDs do not belong in this catalog.

The domain loader validates the complete journey set, contiguous ordering,
known event/source enums, per-trace digests, the catalog digest, and the pinned
Deep Agents/LangChain/LangGraph revisions. The fixture is then materialized as
the same `TrajectoryManifest` contract used by F1 evaluation.

Do not update a digest merely to make a test pass. A baseline revision requires
an intentional runtime-contract change, corresponding journey regression
tests, a new revision, and a reviewed regenerated digest.
