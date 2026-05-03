---
name: search-subagent-logs
description: Read prior subagent activity — verbatim tool calls, search queries, conversation, and result — from `/subagents/<task_id>/`. Use whenever the user asks what a delegate did or any "all/every/complete" question about its tool history.
allowed_tools:
  - ls
  - read_file
---

# Search subagent logs

Every subagent dispatched in this conversation has a read-only directory at `/subagents/<task_id>/` containing four files:

| File              | Contents                                                                                                                                                                  |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `summary.md`      | Subagent name, terminal status, objective, result, and originating `run_id`                                                                                               |
| `tool_calls.json` | **Every** tool call the subagent made: `tool_name`, full `args` (verbatim — including exact search queries), `output` (truncated), `started_at`, `completed_at`, `status` |
| `conversation.md` | Chronological prose: model deltas interleaved with `> tool_call(...)` / `< tool_result(...)` markers                                                                      |
| `events.jsonl`    | Raw event envelopes for engineering forensics                                                                                                                             |

The directory persists **across turns** in the same conversation. Turn 2 can read everything Turn 1's subagents did.

## When to use this skill

Reach for `/subagents/` whenever the user asks questions like:

- "What search queries did the research subagent run?"
- "List every tool call subagent 2 made."
- "Show me the full transcript of the coder subagent."
- "Did the writer subagent ever call X?"
- "What was the second delegate working on?"

You should also reach for `/subagents/` when the prompt-injected "prior tool observations" snippet is missing the answer. **That snippet is truncated to the most recent ~8 tool results across all subagents** — for an exhaustive list it is incomplete. The FS has the complete set.

## Steps

1. Start with `ls /subagents/`. You'll see one directory per subagent task id.
2. If you don't already know which task id corresponds to which subagent, read `summary.md` for each — the `## Subagent` line tells you the subagent type and `## Objective` tells you what it was asked to do.
3. For an exhaustive list of queries / tool calls, read `tool_calls.json`. The `args` field holds the **verbatim** values, including search queries with their exact punctuation, `site:` filters, and quoting.
4. For the subagent's reasoning + tool flow in chronological order, read `conversation.md`.
5. **Quote `args` verbatim.** Do not paraphrase, deduplicate (unless the user asks), or invent queries. If a query is not in `tool_calls.json`, do not include it in your answer.

## Important

- `/subagents/` is **read-only**. `write_file` and `edit` will fail with `read-only` errors. Don't try.
- The "prior tool observations" prompt context is a lossy summary; the FS is the source of truth. Prefer the FS whenever the user asks for "all" / "every" / "complete" / "exhaustive".
- If the supervisor itself called tools (rather than dispatching to a subagent), those calls are NOT in `/subagents/`. Use the cross-turn observation summary in the prompt or ask the user to clarify.
