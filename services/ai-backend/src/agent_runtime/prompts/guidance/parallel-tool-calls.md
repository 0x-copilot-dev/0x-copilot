# Parallel tool calls

When you need several pieces of information that don't depend on each other,
request them together in a single response instead of one tool call per turn.
Independent reads, searches, and web fetches should be batched into the same
assistant turn — the runtime executes independent calls concurrently, and
batching avoids resending the whole conversation on every extra round-trip.

Only serialize calls when a later call genuinely depends on an earlier call's
result (e.g. you must `read_file` before you can `edit_file`). When in doubt and
the calls are independent, batch them.
