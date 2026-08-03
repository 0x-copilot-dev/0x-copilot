# Model guidance blocks

Behavioural guidance appended to the system prompt for **every** model.

Each `.md` file in this directory is one block. They are loaded by
`GuidanceLibrary` (`agent_runtime/prompts/guidance.py`), concatenated in the
order that class declares, and joined into the stable, cacheable prefix — so the
bytes are paid for approximately once per installation rather than once per
turn.

## Provenance

Ported from [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
`agent/prompt_builder.py`. Hermes gates several of these on model family
(`OPENAI_MODEL_EXECUTION_GUIDANCE` for GPT, `GOOGLE_MODEL_OPERATIONAL_GUIDANCE`
for Gemini, `TOOL_USE_ENFORCEMENT_MODELS` for a named list). **We apply all of
them to every model**, deliberately: the failure modes they describe — stopping
after a plan, fabricating tool output, asking instead of acting, serialising
independent calls — are not vendor-specific, and a per-family gate is a
maintenance burden that silently omits guidance from every model not on the
list.

## The one rule when editing these

**Never name a tool this runtime does not expose.** Guidance that says "use
`terminal`" on a runtime with no terminal teaches the model to plan around a
capability it does not have, which is worse than saying nothing. The upstream
text referenced `terminal`, `execute_code` and `search_files`; those are
rewritten here against the actual builtin surface:

| Upstream       | Here                                   |
| -------------- | -------------------------------------- |
| `search_files` | `grep` / `glob`                        |
| `terminal`     | — (no shell tool; bullets removed)     |
| `execute_code` | — (no code execution; bullets removed) |
| `read_file`    | `read_file` (unchanged)                |
| `web_search`   | `web_search` (unchanged)               |

`tests/unit/agent_runtime/prompts/test_guidance.py` enforces this: every
backtick-quoted tool name in every block must exist in the builtin operation
catalog. Add a tool to a block only after the runtime actually exposes it.

## Why files and not string constants

These are prose, edited by humans, reviewed as prose, and diffed as prose. A
2 000-character string literal spliced across Python source lines hides its own
whitespace and makes a wording change unreadable in review.
