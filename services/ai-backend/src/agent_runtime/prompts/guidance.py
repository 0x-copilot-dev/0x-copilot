"""Behavioural guidance blocks appended to every model's system prompt.

The prose lives in ``guidance/*.md`` (see that directory's README for
provenance and the tool-naming rule). This module is only the loader: it reads
the blocks once per process, in a declared order, and joins them.

WHY EVERY MODEL, NOT A FAMILY GATE
----------------------------------
Upstream gates several of these on model family — one block for GPT, another for
Gemini, a named list for tool-use enforcement. We apply all of them to every
model on purpose. The behaviours they correct (stopping after a plan,
fabricating tool output, asking instead of acting, serialising independent
calls) are not vendor-specific, and a family gate quietly omits the guidance
from every model not on the list — including every model added after the list
was written.

WHY IT IS READ ONCE
-------------------
The joined text is installation-scoped immutable policy: identical for every
user, every conversation and every turn. It joins the STABLE prefix the factory
marks cacheable, so the tokens are paid for approximately once rather than once
per turn. Re-reading the files per request would also make the prompt bytes
depend on disk state mid-run, which breaks byte-stable prompt caching.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path


class GuidanceLibrary:
    """Loads and joins the model-guidance blocks."""

    #: Directory holding one ``.md`` per block.
    DIRECTORY = Path(__file__).parent / "guidance"

    #: Render order. Explicit rather than a directory glob: the order is part of
    #: the prompt's meaning (enforcement before completion before the detailed
    #: discipline), and a glob would silently reorder on rename and silently
    #: include any stray file someone drops in the directory.
    BLOCKS: tuple[str, ...] = (
        "tool-use-enforcement",
        "task-completion",
        "parallel-tool-calls",
        "execution-discipline",
        "operational-directives",
    )

    @classmethod
    def block(cls, name: str) -> str:
        """Return one block's text, without trailing whitespace."""

        path = cls.DIRECTORY / f"{name}.md"
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            # A packaging miss (md files not shipped in the wheel or the desktop
            # stage) must be loud. Silently returning "" would degrade every
            # model's behaviour with nothing in the logs pointing at the cause.
            raise RuntimeError(
                f"model guidance block '{name}' is missing at {path}. "
                "The prompts/guidance/*.md files must ship with the package."
            ) from exc

    @classmethod
    @cache
    def text(cls) -> str:
        """The joined guidance, in :attr:`BLOCKS` order."""

        return "\n\n".join(cls.block(name) for name in cls.BLOCKS)
