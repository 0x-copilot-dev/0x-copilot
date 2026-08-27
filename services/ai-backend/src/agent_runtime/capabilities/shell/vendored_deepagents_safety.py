"""Shell-safety data vendored from LangChain's deepagents, verbatim.

SOURCE: langchain-ai/deepagents, `libs/code/deepagents_code/config.py`
        (`RECOMMENDED_SAFE_SHELL_COMMANDS`, `DANGEROUS_SHELL_PATTERNS`,
        `contains_dangerous_patterns`). MIT licensed, as is this repository.
        `deepagents==0.7.4` is already a declared dependency of this service —
        this file vendors from a SIBLING package in the same repo
        (`deepagents_code`) that is NOT installed here.

WHY VENDORED RATHER THAN IMPORTED. `deepagents_code` is a separate distribution
and is not in this service's environment. Adding it would mean a new dependency
edge for what is, in substance, two data tables and a nine-line predicate — and
this repository's lockfile does not survive regeneration, so a dependency edge
is a CI hazard out of proportion to the payload. If `deepagents_code` ever
becomes a dependency for other reasons, delete this file and import instead.

WHY THIS SHAPE IS BETTER THAN WHAT IT REPLACES, which is the whole point of
carrying it. Our own first attempt was a DENY-list: whole-command globs
enumerating dangerous commands, which cannot be complete and needs a documented
"known-miss corpus" saying so. This is the inverse and it is the stronger
primitive:

    an ALLOW-list of readers  +  a ban on the metacharacters that let a
    command chain out of the allow-list

A deny-list fails open on anything nobody thought of. An allow-list fails
closed, and the pattern check exists precisely to stop `ls; rm -rf ~` from
riding in on an allowed `ls`.

WHAT THIS IS NOT. `RECOMMENDED_SAFE_SHELL_COMMANDS` is upstream's
**auto-approve** set for non-interactive mode — the commands safe enough to run
WITHOUT asking. It is not a list of everything a user may run. Phase 1 of
PRD-shell-execution asks for every command regardless, so this list is not the
gate today; it is the honest basis for a later auto-approve tier, and the
pattern check below is useful immediately.

KEEPING IT HONEST. Do not edit these tables to "improve" them. They are a
verbatim copy so they can be diffed against upstream when deepagents moves. Any
0xCopilot-specific addition belongs in a separate table in the calling module,
so the vendored half stays comparable.
"""

from __future__ import annotations

import re
import shlex
from typing import Final

#: Read-only commands upstream auto-approves in non-interactive mode.
#:
#: Upstream's own docstring: "Only includes readers and formatters — shells,
#: editors, interpreters, package managers, network tools, archivers, and
#: anything on GTFOBins/LOOBins is intentionally excluded. File-write and
#: injection vectors are blocked separately by `DANGEROUS_SHELL_PATTERNS`."
RECOMMENDED_SAFE_SHELL_COMMANDS: Final[tuple[str, ...]] = (
    # Directory listing
    "ls",
    "dir",
    # File content viewing (read-only)
    "cat",
    "head",
    "tail",
    # Text searching (read-only)
    "grep",
    "wc",
    "strings",
    # Text processing (read-only, no shell execution)
    "cut",
    "tr",
    "diff",
    "md5sum",
    "sha256sum",
    # Path utilities
    "pwd",
    "which",
    # System info (read-only)
    "uname",
    "hostname",
    "whoami",
    "id",
    "groups",
    "uptime",
    "nproc",
    "lscpu",
    "lsmem",
    # Process viewing (read-only)
    "ps",
)

#: Literal substrings that indicate shell injection risk.
#:
#: These are what let an allowed base command execute something else, so they
#: are checked EVEN WHEN the command name is on the allow-list.
DANGEROUS_SHELL_PATTERNS: Final[tuple[str, ...]] = (
    "$(",  # Command substitution
    "`",  # Backtick command substitution
    "$'",  # ANSI-C quoting (can encode dangerous chars via escape sequences)
    "\n",  # Newline (command injection)
    "\r",  # Carriage return (command injection)
    "\t",  # Tab (can be used for injection in some shells)
    "<(",  # Process substitution (input)
    ">(",  # Process substitution (output)
    "<<<",  # Here-string
    "<<",  # Here-doc (can embed commands)
    ">>",  # Append redirect
    ">",  # Output redirect
    "<",  # Input redirect
    "${",  # Variable expansion with braces (can run commands via ${var:-$(cmd)})
)

#: Bare variable expansion, which the literal table above cannot express.
#: Catches `$HOME`, `$IFS` and friends — `$IFS` in particular is a standard way
#: to smuggle a separator past a naive tokenizer.
_BARE_VARIABLE = re.compile(r"\$[A-Za-z_]")


def contains_dangerous_patterns(command: str) -> bool:
    """Whether ``command`` embeds a shell-injection vector.

    Verbatim in behaviour from upstream: the literal table, plus a regex for
    bare ``$VAR`` (``${`` and ``$(`` are already covered above). Upstream also
    treats the background operator as dangerous; that is expressed in the
    calling module rather than here so this file stays a straight copy.
    """

    if any(pattern in command for pattern in DANGEROUS_SHELL_PATTERNS):
        return True
    return _BARE_VARIABLE.search(command) is not None


def is_shell_command_allowed(command: str, allow_list: list[str] | None) -> bool:
    """Whether every segment of ``command`` starts with an allowed executable.

    Vendored verbatim in behaviour from upstream, and this is the function that
    makes the allow-list a real defence rather than a first-token check:

    * dangerous patterns are rejected BEFORE any parsing, so an injection
      cannot reach the tokenizer at all;
    * the command is split on every chaining operator (``&&``, ``||``, ``|``,
      ``;``) and EACH segment's executable is checked — which is what stops
      ``ls; rm -rf ~`` from riding in on an allowed ``ls``. The literal pattern
      table alone does not catch ``;``; this does;
    * ``shlex`` does the tokenizing, so quoting is handled rather than guessed;
    * an unparseable segment returns False. Fails CLOSED.

    Upstream note kept: a standalone ``&`` (background) is caught by
    ``contains_dangerous_patterns`` rather than here.
    """

    if not allow_list or not command or not command.strip():
        return False
    if contains_dangerous_patterns(command):
        return False

    allow_set = set(allow_list)
    found_command = False
    for raw_segment in re.split(r"&&|\|\||[|;]", command):
        segment = raw_segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            # Unbalanced quoting. Conservative: refuse rather than guess.
            return False
        if tokens:
            found_command = True
            if tokens[0] not in allow_set:
                return False
    return found_command


__all__ = [
    "DANGEROUS_SHELL_PATTERNS",
    "RECOMMENDED_SAFE_SHELL_COMMANDS",
    "contains_dangerous_patterns",
    "is_shell_command_allowed",
]
