"""The never-list: one data table, two readers (PRD-shell-execution §9).

**What this module is for, stated the way §9.4 asks for it.** It is defence in
depth against the plausible accident and the low-effort injection. It is **not**
a boundary against a determined adversary, and nothing here should be read as
claiming otherwise. The controls that actually hold against someone trying are
the constructed environment (§11), the scratch ``HOME`` and the OS profile
(Phase 2) — and the human reading the command. This module's job is to make the
handful of shapes that are *never* a legitimate agent action unreachable, and to
be honest in its own test corpus about everything it lets past
(``test_never_list_corpus.py``).

Two readers, different powers
-----------------------------
* **The pre-PDP lexical screen — the primary mechanism (§9.3).**
  :meth:`CommandNeverList.screen` sees the full, untruncated command string and
  **tokenises** it, so it can say "``argv[0]`` is ``sudo``", "this ``rm`` operand
  resolves to ``/``", "this pipeline's sink is an interpreter". It runs *before*
  the PEP is entered, so a hit means **no approval card is ever created**
  (AC7.1) — there is nothing to click through.
* **The ``_never`` ruleset — the floor.** :meth:`CommandNeverList.floor`
  compiles the same table for a reader that cannot tokenise. A
  ``PermissionRule`` ``fullmatch``es two globs and ANDs them
  (``policy/rules.py:148-153``); the subject half sees one opaque string, so its
  rows are coarse whole-command patterns. Kept because it survives a bug in the
  screen and it is the rung ``PdpPolicyService`` evaluates above every rule and
  every posture (``policy/service.py:328-333``), so it holds if a second call
  path is ever wired that skips the screen.

One table, two readers — never two tables, because a second table is a second
thing to keep correct and it is the copy that will be forgotten.

⚠️ A floor row is a WHOLE-COMMAND glob, not a path pattern
----------------------------------------------------------
Three mechanical facts, each verified against ``policy/rules.py`` and each the
reason an obvious authoring of this table does not work:

1. **There is one subject and it is the entire command line.**
   ``PolicySubjects.of`` folds the URN plus every top-level string argument
   (``rules.py:345-359``). ``RunCommandInput`` has exactly one command field, so
   the subject list is ``(urn, "cat ~/.ssh/id_rsa", label, grant-subject)``.
   There is no path subject for a path pattern to match against.
2. **Matching is ``fullmatch`` over that whole subject** (``rules.py:98-101``).
   So ``~/.ssh/**`` never fires on ``cat ~/.ssh/id_rsa``; ``*/.ssh/*`` does.
   Machine-checked, and pinned by ``test_never_list.py``.
3. **``Wildcard.expand`` rewrites a LEADING ``~`` / ``$HOME`` on the PATTERN
   side only** (``rules.py:104-122``); the command *text* is never expanded,
   because the child shell expands it at exec time. A row authored ``~/.ssh/**``
   is therefore rewritten to ``/Users/<me>/.ssh/**`` and can never meet the
   literal ``~`` the model typed. **Every row here begins with ``*`` or with a
   literal binary name**, and a test asserts every row survives
   ``Wildcard.expand`` byte-identically.

The pattern vocabulary is exactly: ``*`` → ``.*``, ``?`` → ``.``, everything in
``.+^${}()|[]\\`` escaped to a literal, compiled with ``re.DOTALL``
(``rules.py:125-134``). No character classes, no alternation, no anchors, no
word boundaries, and case-sensitive. ``-rf`` and ``-fr`` are two rows.

**Over-refusal here does NOT cost a click — it costs the command.** A floor hit
is unappealable by construction. That is why the rows are deliberately narrow,
why the screen (which tokenises) is the mechanism that decides, and why the
known over-refusals the rows still carry are pinned as *tests* rather than left
to be discovered: see ``test_never_list_corpus.py::KNOWN_OVER_REFUSALS``.

Ordering (§9.5)
---------------
``PermissionRuleset.evaluate`` is **last-match-wins** and ``merge``
concatenates, so the shipped floor must be merged **LAST** into ``_never`` or a
user row of ``{"pattern": "*", "action": "allow"}`` would sit after it and win.
``ShellCommandPolicyGate._pdp`` does the merge in that order
(``never=authored_never.merge(self._never_list.floor())``); this module only
supplies the rows, and ``test_never_list.py`` asserts the property end to end.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar, Final

from agent_runtime.capabilities.policy.rules import (
    PermissionRule,
    PermissionRuleset,
    RuleAction,
)
from agent_runtime.capabilities.shell.contracts import (
    ShellRefusal,
    ShellRefusalReason,
)


class _Note:
    """Model-facing refusal sentences, one per hazard class.

    Authored constants, never interpolated from the command: the command may
    carry connector-ingested content, and a note is model-visible output.

    Differentiated rather than collapsed to one line on purpose. The closed
    ``reason`` code stays ``COMMAND_NOT_PERMITTED`` for every one of these, so
    nothing about deployment configuration is distinguishable; what differs is
    the sentence that tells the model *which shape* it hit, which is the
    difference between one retry and six. Every line says the refusal is
    permanent, because a deterministic refusal described as temporary sends a
    model into a loop it cannot win.
    """

    _SUFFIX: Final = (
        " Nothing was run, there is no approval to ask for, and retrying will "
        "not change it."
    )

    PRIVILEGE: Final = "Commands are never run with elevated privileges." + _SUFFIX
    DESTRUCTIVE_DELETE: Final = (
        "A recursive delete of the filesystem root or the home directory is "
        "never permitted." + _SUFFIX
    )
    FILESYSTEM_DESTRUCTION: Final = (
        "Commands that write to a raw device or format a filesystem are never "
        "permitted." + _SUFFIX
    )
    MACHINE_STATE: Final = (
        "Commands that shut down, reboot or halt the machine are never "
        "permitted." + _SUFFIX
    )
    FORK_BOMB: Final = "That command is a fork bomb and is never permitted." + _SUFFIX
    PIPE_TO_INTERPRETER: Final = (
        "Piping a download straight into an interpreter is never permitted. "
        "Download to a file, and the file can then be read before anything "
        "runs it." + _SUFFIX
    )
    CREDENTIAL_PATH: Final = (
        "That command names a credential directory, which is never readable "
        "through a command." + _SUFFIX
    )
    CREDENTIAL_FILE: Final = (
        "That command names a credential or key file, which is never readable "
        "through a command." + _SUFFIX
    )


class SensitivePathPolicy:
    """The credential-path and credential-filename policy — **a duplicate**.

    ⚠️ **Change this together with**
    ``apps/desktop/main/capabilities/path-validation.ts``:
    ``SENSITIVE_ROOT_SEGMENTS`` (``:313-323``), ``SENSITIVE_FILE_RULES``
    (``:854-871``) and ``isSensitiveFileName`` (``:879-890``). Python cannot
    import TypeScript and ``packages/service-contracts`` is Python-only, so this
    is the SIWE precedent — a byte-identical duplicate with a "change both
    together" comment (root ``CLAUDE.md``). **The divergence is not left to a
    comment**: ``test_never_list.py::TestTypeScriptParity`` parses the ``.ts``
    file and asserts these three tables equal it, so drift reds CI rather than
    silently un-refusing a credential file.

    §5 recommends option 2 (ship the JSON in ``service-contracts`` and assert
    equality from a desktop test) and records it as **OQ-3, unresolved**. This
    module deliberately does not invent that cross-language artefact ahead of
    the sign-off; it takes the already-accepted pattern and adds the equality
    test, which is the half of option 2 that carries the actual guarantee.

    Why a command must obey a *read* rule: the broker already refuses to return
    these files' contents, and a command must not be the way around a rule the
    read path enforces.
    """

    #: Directory basenames that must never appear anywhere in a path.
    #: Case-INSENSITIVE in the TypeScript source (``s.toLowerCase()``), so this
    #: screen lowercases too. The floor cannot: ``Wildcard.match`` is
    #: case-sensitive. See :meth:`CommandNeverList._segment_rows`.
    ROOT_SEGMENTS: Final = (
        ".ssh",
        ".aws",
        ".gnupg",
        ".gpg",
        ".password-store",
        ".docker",
        ".kube",
        ".azure",
        "keychains",
    )

    #: Suffixes denoting key / certificate / keystore material. ``endsWith``.
    FILE_SUFFIXES: Final = (
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".pkcs12",
        ".keystore",
        ".keychain",
        ".asc",
        ".ppk",
    )

    #: Prefixes for conventional SSH private-key files. ``startsWith``.
    FILE_PREFIXES: Final = ("id_rsa", "id_ed25519", "id_dsa", "id_ecdsa")

    #: Exact credential-store filenames. Equality.
    FILE_EXACT: Final = (
        "credentials",
        ".netrc",
        ".pgpass",
        ".htpasswd",
        ".dockercfg",
    )

    #: ``isSensitiveFileName``'s first check, special-cased there and here:
    #: ``.env`` itself plus every ``.env.<variant>``. It is not a suffix, a
    #: prefix or an exact entry, so nothing else in this class covers it.
    DOTENV: Final = ".env"

    @classmethod
    def is_sensitive_file_name(cls, name: str) -> bool:
        """Port of ``isSensitiveFileName`` (``path-validation.ts:879-890``).

        The three groups use three DIFFERENT predicates and that difference is
        load-bearing both here and in the floor rows: ``prefixes`` is
        ``startsWith``, ``suffixes`` is ``endsWith``, ``exact`` is equality.
        """

        lower = name.lower()
        if lower == cls.DOTENV or lower.startswith(f"{cls.DOTENV}."):
            return True
        if lower in cls.FILE_EXACT:
            return True
        if lower.startswith(cls.FILE_PREFIXES):
            return True
        return lower.endswith(cls.FILE_SUFFIXES)

    @classmethod
    def has_sensitive_segment(cls, token: str) -> bool:
        """True when any ``/``-separated part of ``token`` is a credential dir.

        Backslashes are folded to ``/`` first, mirroring ``Wildcard.match``'s
        own ``value.replace("\\\\", "/")`` (``rules.py:101``) so the screen and
        the floor agree about what a path separator is.
        """

        folded = token.replace("\\", "/")
        return any(part.lower() in cls.ROOT_SEGMENTS for part in folded.split("/"))

    @classmethod
    def leaf_is_sensitive(cls, token: str) -> bool:
        """True when ``token``'s leaf name satisfies :meth:`is_sensitive_file_name`."""

        folded = token.replace("\\", "/")
        return cls.is_sensitive_file_name(folded.rsplit("/", 1)[-1])


@dataclass(frozen=True, slots=True)
class CommandToken:
    """One lexed token: a word, or a control/redirection operator.

    ``text`` is the token with quoting and escaping removed — the string the
    shell would actually pass to ``execve`` — and ``raw`` is the token exactly
    as written. Both are kept because they answer different questions:
    ``"sudo" rm -rf /`` really does invoke ``sudo`` (so hazard detection reads
    ``text``), while a run-scoped grant may only be offered for a command whose
    literal text is what a glob will later be matched against (so
    :meth:`CommandNeverList.always_grant_patterns` requires ``raw == text``).
    """

    text: str
    raw: str
    is_operator: bool = False

    @property
    def is_quoted(self) -> bool:
        """True when quoting or escaping changed the token."""

        return self.raw != self.text


class CommandLexer:
    """A total POSIX-ish tokeniser: quotes, escapes, operators, substitutions.

    Total by construction, not by ``try``: an unterminated quote runs to the end
    of the string and a trailing backslash is kept as a literal. Nothing here
    raises, because the Protocol this module implements requires the narrowing
    answer rather than an exception on the tool path — a lexer that threw would
    turn a malformed command into a broken run instead of a refusal.

    Deliberately not ``shlex``: ``shlex.split`` raises on an unbalanced quote,
    discards operators entirely (so ``&&`` and ``|`` become invisible, which is
    the one distinction §8.3 is built on), and cannot report the raw spelling of
    a token.
    """

    #: Multi-character operators, longest-first so ``&&`` wins over ``&``.
    #: ``$(`` is handled separately because its first character is not an
    #: operator character; ``${`` is NOT an operator — a parameter expansion is
    #: part of the word — but it is still tracked as an expansion for §8.3.
    _OPERATORS: Final = (
        "&&",
        "||",
        ";;",
        "|&",
        ">>",
        "<<",
        ">&",
        "<&",
        ";",
        "|",
        "&",
        "<",
        ">",
        "(",
        ")",
        "`",
        "\n",
        "\r",
    )

    _COMMAND_SUBSTITUTION: Final = "$("
    _PARAMETER_EXPANSION: Final = "${"
    _WHITESPACE: Final = " \t"
    _SINGLE: Final = "'"
    _DOUBLE: Final = '"'
    _ESCAPE: Final = "\\"
    #: Inside double quotes a backslash only escapes these four.
    _DOUBLE_ESCAPABLE: Final = '\\"$`'

    @classmethod
    def tokenize(cls, command: str) -> tuple[CommandToken, ...]:
        """Split ``command`` into words and operators. Never raises."""

        tokens: list[CommandToken] = []
        text: list[str] = []
        raw: list[str] = []
        index = 0
        size = len(command)

        def flush() -> None:
            if raw:
                tokens.append(CommandToken(text="".join(text), raw="".join(raw)))
                text.clear()
                raw.clear()

        while index < size:
            char = command[index]
            if char in cls._WHITESPACE:
                flush()
                index += 1
                continue
            if char == cls._ESCAPE:
                escaped = command[index + 1 : index + 2]
                raw.append(char + escaped)
                text.append(escaped)
                index += 2 if escaped else 1
                continue
            if char == cls._SINGLE:
                index = cls._read_single(command, index, text, raw)
                continue
            if char == cls._DOUBLE:
                index = cls._read_double(command, index, text, raw)
                continue
            if command.startswith(cls._COMMAND_SUBSTITUTION, index):
                flush()
                tokens.append(
                    CommandToken(
                        text=cls._COMMAND_SUBSTITUTION,
                        raw=cls._COMMAND_SUBSTITUTION,
                        is_operator=True,
                    )
                )
                index += len(cls._COMMAND_SUBSTITUTION)
                continue
            operator = cls._operator_at(command, index)
            if operator is not None:
                flush()
                tokens.append(
                    CommandToken(text=operator, raw=operator, is_operator=True)
                )
                index += len(operator)
                continue
            raw.append(char)
            text.append(char)
            index += 1

        flush()
        return tuple(tokens)

    @classmethod
    def _read_single(
        cls, command: str, index: int, text: list[str], raw: list[str]
    ) -> int:
        """Consume a ``'...'`` run; an unterminated quote runs to end of string."""

        close = command.find(cls._SINGLE, index + 1)
        end = len(command) if close == -1 else close
        text.append(command[index + 1 : end])
        raw.append(command[index : end + 1])
        return end + 1

    @classmethod
    def _read_double(
        cls, command: str, index: int, text: list[str], raw: list[str]
    ) -> int:
        """Consume a ``"..."`` run, honouring the four escapes the shell honours."""

        cursor = index + 1
        size = len(command)
        while cursor < size:
            char = command[cursor]
            if (
                char == cls._ESCAPE
                and command[cursor + 1 : cursor + 2] in cls._DOUBLE_ESCAPABLE
                and cursor + 1 < size
            ):
                text.append(command[cursor + 1])
                cursor += 2
                continue
            if char == cls._DOUBLE:
                break
            text.append(char)
            cursor += 1
        raw.append(command[index : cursor + 1])
        return cursor + 1

    @classmethod
    def _operator_at(cls, command: str, index: int) -> str | None:
        """The longest operator starting at ``index``, or ``None``."""

        for operator in cls._OPERATORS:
            if command.startswith(operator, index):
                return operator
        return None

    @classmethod
    def has_parameter_expansion(cls, command: str) -> bool:
        """True when ``${`` appears anywhere.

        ``${VAR}`` is part of a word, not an operator, so it never shows up in
        :meth:`tokenize`'s operator stream — but §8.3 lists it among the
        metacharacters that forfeit a run-scoped grant, because the text a human
        approved and the text the shell runs are then different strings.
        """

        return cls._PARAMETER_EXPANSION in command


@dataclass(frozen=True, slots=True)
class CommandSegment:
    """One command in the line, plus the operator that introduced it.

    ``lead`` is ``""`` for the first segment and otherwise the operator token
    that preceded it, which is how :meth:`CommandNeverList._pipe_to_interpreter`
    tells a pipeline (``|``) from a list (``&&``, ``;``) without re-lexing.
    """

    lead: str
    words: tuple[CommandToken, ...]
    redirect_targets: tuple[str, ...]

    @property
    def argv(self) -> tuple[str, ...]:
        """The dequoted word texts, redirection targets already removed."""

        return tuple(word.text for word in self.words)


class ParsedCommandLine:
    """Command-position structure over a lexed line.

    The one property the ``_never`` floor cannot express and this class exists
    to supply: **command position**. It is what separates ``sudo rm -rf /`` from
    ``git commit -m "no sudo here"``, and a glob with no word boundaries cannot
    tell a binary from a substring (Hermes solves the same problem with its
    ``_CMDPOS`` regex, ``approval.py:381-392``).
    """

    #: Operators after which the next word starts a new command.
    _SEPARATORS: Final = frozenset(
        {"&&", "||", ";;", ";", "|", "|&", "&", "(", ")", "`", "$(", "\n", "\r"}
    )
    #: Operators whose next word is a redirection TARGET, never a command. Kept
    #: apart so ``echo x > sudo`` does not read as running ``sudo``, and so the
    #: target is available to the raw-device check.
    _REDIRECTIONS: Final = frozenset({">", ">>", "<", "<<", ">&", "<&"})
    #: Reserved words and wrapper binaries that are followed by the command
    #: actually being run. Skipping them WIDENS hazard detection (``time sudo
    #: x`` is a ``sudo`` invocation) and is safe in that direction only — §8.3's
    #: always-grant deliberately does NOT see through them.
    _INTRODUCERS: Final = frozenset(
        {
            "if",
            "then",
            "else",
            "elif",
            "while",
            "until",
            "do",
            "!",
            "{",
            "}",
            "time",
            "env",
            "nice",
            "nohup",
            "xargs",
            "timeout",
            "stdbuf",
            "script",
        }
    )
    _ASSIGNMENT: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

    __slots__ = ("_segments", "_tokens")

    def __init__(self, tokens: tuple[CommandToken, ...]) -> None:
        self._tokens = tokens
        self._segments = self._split(tokens)

    @classmethod
    def of(cls, command: str) -> "ParsedCommandLine":
        """Lex and structure ``command``."""

        return cls(CommandLexer.tokenize(command))

    @classmethod
    def is_assignment(cls, word: str) -> bool:
        """True for a ``NAME=value`` prefix, which is not the command."""

        return cls._ASSIGNMENT.match(word) is not None

    @classmethod
    def is_introducer(cls, word: str) -> bool:
        """True for a reserved word or wrapper that precedes the real command."""

        return word in cls._INTRODUCERS

    @property
    def tokens(self) -> tuple[CommandToken, ...]:
        return self._tokens

    @property
    def segments(self) -> tuple[CommandSegment, ...]:
        return self._segments

    @property
    def words(self) -> tuple[CommandToken, ...]:
        """Every word token in the line, including redirection targets."""

        return tuple(token for token in self._tokens if not token.is_operator)

    @property
    def operators(self) -> tuple[str, ...]:
        return tuple(token.text for token in self._tokens if token.is_operator)

    def command_candidates(self, segment: CommandSegment) -> tuple[str, ...]:
        """The words in ``segment`` that could be the command being run.

        Assignment prefixes are skipped (``FOO=1 sudo x`` runs ``sudo``), and so
        is each :data:`_INTRODUCERS` word together with its own flags, so the
        walk keeps going until it reaches something that is not a wrapper. Every
        word it passed through is returned, not just the last, because
        ``xargs`` is itself worth refusing in some classes and ``sudo`` is worth
        refusing in all of them.
        """

        candidates: list[str] = []
        walking = False
        for word in segment.argv:
            if self.is_assignment(word) or word.startswith("-"):
                continue
            if walking and word[:1].isdigit():
                # A wrapper's own operand: the ``5`` in ``nice -n 5 pytest``,
                # the ``30`` in ``timeout 30 pytest``. Only skipped once a
                # wrapper has been passed, so a command whose name starts with
                # a digit (``7z``) is still read as the command.
                continue
            candidates.append(word)
            if not self.is_introducer(word):
                break
            walking = True
        return tuple(candidates)

    def operands(self, segment: CommandSegment, *, after: str) -> tuple[str, ...]:
        """Non-flag words following the ``after`` word in ``segment``.

        Everything after a bare ``--`` is an operand, matching the convention
        every one of these binaries follows.
        """

        argv = segment.argv
        if after not in argv:
            return ()
        operands: list[str] = []
        end_of_flags = False
        for word in argv[argv.index(after) + 1 :]:
            if word == "--":
                end_of_flags = True
                continue
            if not end_of_flags and word.startswith("-"):
                continue
            operands.append(word)
        return tuple(operands)

    def short_flags(self, segment: CommandSegment) -> frozenset[str]:
        """Every letter in every ``-abc`` cluster, plus every ``--long`` word."""

        letters: set[str] = set()
        for word in segment.argv:
            if word.startswith("--"):
                letters.add(word)
            elif word.startswith("-") and len(word) > 1:
                letters.update(word[1:])
        return frozenset(letters)

    @classmethod
    def _split(cls, tokens: tuple[CommandToken, ...]) -> tuple[CommandSegment, ...]:
        """Group tokens into segments, pulling redirection targets aside."""

        segments: list[CommandSegment] = []
        lead = ""
        words: list[CommandToken] = []
        targets: list[str] = []
        pending_redirection = False

        for token in tokens:
            if token.is_operator:
                if token.text in cls._REDIRECTIONS:
                    pending_redirection = True
                    continue
                if token.text in cls._SEPARATORS:
                    segments.append(
                        CommandSegment(
                            lead=lead,
                            words=tuple(words),
                            redirect_targets=tuple(targets),
                        )
                    )
                    lead = token.text
                    words = []
                    targets = []
                    pending_redirection = False
                continue
            if pending_redirection:
                targets.append(token.text)
                pending_redirection = False
                continue
            words.append(token)

        segments.append(
            CommandSegment(
                lead=lead, words=tuple(words), redirect_targets=tuple(targets)
            )
        )
        return tuple(segments)


class CommandNeverList:
    """The §9 floor: the tokenising screen, the compiled rows, the grant patterns.

    Implements the ``CommandNeverList`` Protocol that
    ``capabilities/shell/policy_gate.py`` declares. Every method is total; a
    tokeniser that cannot parse returns the narrowing answer rather than raising
    into the tool path.
    """

    # -- hazard vocabulary -------------------------------------------------

    _PRIVILEGE: Final = frozenset({"sudo", "doas", "su"})
    #: ``-S`` makes ``sudo`` read a password from stdin. We close stdin, so this
    #: is credential brute-forcing rather than an invocation (Hermes
    #: ``approval.py:486-515``). Checked anywhere, not only in command position.
    _STDIN_PASSWORD_FLAG: Final = "S"
    _MACHINE_STATE: Final = frozenset({"shutdown", "reboot", "halt", "poweroff"})
    _SYSTEMCTL: Final = "systemctl"
    _SYSTEMCTL_TARGETS: Final = frozenset({"poweroff", "reboot", "halt"})
    _INIT: Final = "init"
    _INIT_RUNLEVELS: Final = frozenset({"0", "6"})
    _REMOVE: Final = "rm"
    _RECURSIVE_FLAGS: Final = frozenset({"r", "R", "--recursive"})
    #: The whole set of operands that make a recursive delete catastrophic,
    #: after :meth:`_normalise_operand` has stripped a trailing ``/`` or ``/*``.
    _CATASTROPHIC_ROOTS: Final = frozenset({"/", "~", "$HOME", "${HOME}"})
    _MKFS: Final = "mkfs"
    _DD: Final = "dd"
    _DD_OUTPUT: Final = "of="
    _DEVICE_ROOT: Final = "/dev/"
    #: Raw block devices. ``rdisk`` is macOS's raw device node and is the one
    #: that matters on the platform this app ships on; §9.2 lists
    #: ``disk|sd|nvme`` and the addition is called out in the report.
    _RAW_DEVICES: Final = ("disk", "rdisk", "sd", "nvme", "hd", "vd")
    _FETCHERS: Final = frozenset({"curl", "wget", "iwr", "invoke-webrequest", "aria2c"})
    _INTERPRETERS: Final = frozenset(
        {
            "sh",
            "bash",
            "zsh",
            "dash",
            "ksh",
            "fish",
            "csh",
            "tcsh",
            "perl",
            "ruby",
            "node",
            "deno",
            "bun",
            "php",
            "osascript",
            "pwsh",
            "powershell",
            "iex",
        }
    )
    _INTERPRETER_PREFIX: Final = "python"
    _PIPE_OPERATORS: Final = frozenset({"|", "|&"})
    #: ADDED (not in §9.2). A shell invoked with ``-c`` carries a whole command
    #: line inside ONE quoted word, so every tokenising judgement above is blind
    #: to it and every end-anchored floor row misses it too: machine-checked,
    #: ``sh -c "rm -rf /"`` passed both readers before this. One level of
    #: recursion closes it; the payload is screened as what it is, a command
    #: line, so this adds no false-positive shape that the top level does not
    #: already have.
    _SHELL_BINARIES: Final = frozenset(
        {"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh"}
    )
    #: The short-flag letter that means "the next operand is a command string".
    #: Matched inside a cluster so ``bash -lc "…"`` is seen as well as ``-c``.
    _COMMAND_STRING_FLAG: Final = "c"
    #: One level only. A shell inside a shell inside a shell is not a shape we
    #: owe coverage to, and an unbounded walk is an unbounded loop on the tool
    #: path.
    _MAX_NESTED_DEPTH: Final = 1
    #: ``name () { ... }`` with a body that pipes the name into itself and
    #: backgrounds it. Every metacharacter is matched literally; the body is
    #: bounded so a pathological line cannot make this quadratic.
    _FORK_BOMB: ClassVar[re.Pattern[str]] = re.compile(
        r"([^\s(){};|&]{1,32})\s*\(\s*\)\s*\{(.{0,256}?)\}", re.DOTALL
    )
    #: Binaries that forfeit the §8.3 always-grant because ``argv[0]`` is not
    #: the command actually being run. §8.3's list verbatim, with ``make``
    #: resolved IN (OQ-2, the narrowing direction: it runs a file the agent may
    #: have written), plus the additions marked below.
    _WRAPPER_BINARIES: Final = frozenset(
        {
            "env",
            "sh",
            "bash",
            "zsh",
            "sudo",
            "doas",
            "nice",
            "nohup",
            "xargs",
            "time",
            "timeout",
            "script",
            "ssh",
            "docker",
            "make",
            # additions, each the same class as an entry above:
            "su",
            "dash",
            "ksh",
            "fish",  # other spellings of "a shell"
            "command",
            "exec",
            "eval",
            "builtin",  # POSIX re-dispatchers
            "npx",
            "bunx",
            "uvx",
            "pipx",  # fetch-then-run launchers
        }
    )
    #: Characters that cannot appear in an ``argv[0]`` a grant pattern is built
    #: from. ``*`` and ``?`` are the only two ``_compile`` treats as
    #: metacharacters, so either would silently widen the grant to everything.
    _PATTERN_METACHARACTERS: Final = "*?"

    _floor_rules: ClassVar[PermissionRuleset | None] = None

    # -- the Protocol ------------------------------------------------------

    def screen(self, command: str) -> ShellRefusal | None:
        """The §9.3 lexical screen. A refusal means **no card is ever drawn**.

        Runs on the full, untruncated command string, before the PEP is entered
        and before the capability is even described — a never-listed command
        must not be carded, and the 1024-character subject cap that blinds the
        floor (``PolicySubjects._MAX_CHARS``, ``rules.py:342``) does not apply
        here.
        """

        return self._screen(command, depth=0)

    def _screen(self, command: str, *, depth: int) -> ShellRefusal | None:
        """One pass of the screen, plus one recursion into a ``sh -c`` payload."""

        line = ParsedCommandLine.of(command)
        for detector in (
            self._privilege,
            self._destructive_delete,
            self._filesystem_destruction,
            self._machine_state,
            self._pipe_to_interpreter,
            self._credential_path,
            self._credential_file,
        ):
            note = detector(line)
            if note is not None:
                return self._refusal(note)
        if self._fork_bomb(command):
            return self._refusal(_Note.FORK_BOMB)
        if depth >= self._MAX_NESTED_DEPTH:
            return None
        for payload in self._command_strings(line):
            nested = self._screen(payload, depth=depth + 1)
            if nested is not None:
                return nested
        return None

    def floor(self) -> PermissionRuleset:
        """The coarse whole-command DENY rows for the PDP's ``_never`` ruleset.

        Compiled once and shared: the rows are constants and
        ``PermissionRuleset`` is frozen, so a per-call rebuild would only add
        allocation to the hottest path in the runtime.
        """

        cached = CommandNeverList._floor_rules
        if cached is None:
            cached = PermissionRuleset(
                rules=tuple(
                    PermissionRule(pattern=pattern, action=RuleAction.DENY)
                    for pattern in self.rows()
                )
            )
            CommandNeverList._floor_rules = cached
        return cached

    def always_grant_patterns(self, command: str) -> tuple[str, ...]:
        """``argv[0]``-keyed rule patterns for a run-scoped grant, or ``()``.

        ``()`` means this command may not earn a standing yes. Parsing never
        widens here — it only narrows what we offer — so every ambiguity
        returns ``()`` and costs a click rather than granting one.

        The pair is ``argv0`` and ``argv0 *``, never ``argv0*``: a rule matches
        by ``fullmatch`` over the whole command line, and the trailing space is
        the only word boundary the vocabulary has. Without it a ``pytest`` grant
        would also cover ``pytest-watch --exec "curl … | sh"`` (§8.3).
        """

        line = ParsedCommandLine.of(command)
        if line.operators or CommandLexer.has_parameter_expansion(command):
            return ()
        words = line.words
        if not words:
            return ()
        head = words[0]
        name = head.text
        if head.is_quoted or not name:
            return ()
        if ParsedCommandLine.is_assignment(name):
            return ()
        if any(char in name for char in self._PATTERN_METACHARACTERS):
            return ()
        if name in self._WRAPPER_BINARIES or ParsedCommandLine.is_introducer(name):
            return ()
        if self.screen(command) is not None:
            return ()
        return (name, f"{name} *")

    # -- the rows ----------------------------------------------------------

    @classmethod
    def rows(cls) -> tuple[str, ...]:
        """Every whole-command glob merged into ``_never``, in table order.

        Exposed so tests can assert on the literal rows: that none of them
        begins with ``~`` or ``$HOME`` (so ``Wildcard.expand`` leaves every one
        byte-identical), and that the deliberate misses stay misses.
        """

        rows: list[str] = []
        # Root/home recursive delete. Anchored at end of line on purpose, so
        # `rm -rf /Users/me/build` passes; `rm -rf / --no-preserve-root` is a
        # deliberate floor MISS that the screen catches.
        for flags in ("-rf", "-fr"):
            rows += [f"*rm {flags} {root}" for root in ("/", "~", "$HOME")]
        # Filesystem destruction.
        rows += [
            "mkfs*",
            "*mkfs *",
            "*dd *of=/dev/*",
            "* >/dev/disk*",
            "* > /dev/disk*",
        ]
        # Machine state. Two rows per binary: the bare invocation, and the
        # invocation with any argument.
        for binary in sorted(cls._MACHINE_STATE):
            rows += [binary, f"*{binary} *"]
        rows += ["*init 0", "*init 6", "*systemctl poweroff*", "*systemctl reboot*"]
        # Privilege. `su` is omitted on purpose: `*su *` fires on `echo su x`,
        # and a two-letter binary has no usable glob form. The screen has it.
        rows += ["sudo", "*sudo *", "doas", "*doas *", "*sudo -S*"]
        # Fork bomb. Every metacharacter in this row is escaped to a literal by
        # `_compile`, which is exactly why the one row works.
        rows += ["*:(){*};:*"]
        # Pipe-to-interpreter from the network. `|` compiles to a literal pipe.
        rows += [
            "*curl *|*sh*",
            "*curl *|*bash*",
            "*curl *|*python*",
            "*wget *|*sh*",
            "*wget *|*bash*",
            # ADDED (not in §9.2): the curl table has a python row and the wget
            # table did not, so `wget -qO- https://x | python3` was a floor
            # miss. Machine-checked: same shape, same false-positive class.
            "*wget *|*python*",
            "*iwr *|*iex*",
        ]
        rows += cls._segment_rows()
        rows += cls._filename_rows()
        return tuple(rows)

    @classmethod
    def _segment_rows(cls) -> tuple[str, ...]:
        """Three rows per credential directory — end, deeper, then an argument.

        A ``fullmatch`` needs one row for each way a segment can end the line,
        carry more path, or be followed by a space. **Never author these as
        ``~/.ssh/**``**: ``Wildcard.expand`` rewrites the leading ``~`` on the
        pattern side while the command text keeps its literal ``~``, so pattern
        and subject can never agree.

        The capitalised variant is emitted for any segment without a leading
        dot, which today means ``keychains`` only. It is not cosmetic: the
        TypeScript rule is case-INSENSITIVE and ``Wildcard.match`` is not, and
        the real macOS path is ``~/Library/Keychains`` — so without this row the
        entire ``keychains`` entry is inert on the one platform that has it.
        """

        rows: list[str] = []
        for segment in SensitivePathPolicy.ROOT_SEGMENTS:
            spellings = [segment]
            if not segment.startswith("."):
                spellings.append(segment.capitalize())
            for spelling in spellings:
                rows += [f"*/{spelling}", f"*/{spelling}/*", f"*/{spelling} *"]
        return tuple(rows)

    @classmethod
    def _filename_rows(cls) -> tuple[str, ...]:
        """Two rows per credential filename, and a third for prefixes only.

        ⚠️ The third form ``*<entry>.*`` is faithful to ``startsWith`` and to
        nothing else. ``suffixes`` is ``endsWith`` and ``exact`` is equality, so
        emitting it there converts an end-anchor into an infix and equality into
        a prefix — machine-checked, that over-generalisation refuses thirteen
        probe files ``isSensitiveFileName`` calls **not** sensitive
        (``cert.pem.bak``, ``tls.pem.example``, ``docs/credentials.md``,
        ``server.key.tpl``, ``.htpasswd.example``, ``.netrc.sample``,
        ``.pgpass.template``). A floor row is unappealable, so each of those
        would be a command nobody can run and nobody can approve.

        ``.env`` is the reason the third form exists at all: it is not a suffix,
        a prefix or an exact entry, so the first two forms emit nothing for it.
        Machine-checked — ``*.env`` matches ``cat .env`` but not
        ``cat .env.local``; ``*.env *`` matches neither; ``*.env.*`` matches
        ``cat .env.local`` but not ``cat .env``. **Do not collapse the triple to
        ``*.env*``**: that fires on ``echo x.envelope``.
        """

        rows: list[str] = []
        two_form = (
            *SensitivePathPolicy.FILE_SUFFIXES,
            *SensitivePathPolicy.FILE_EXACT,
        )
        for entry in two_form:
            rows += [f"*{entry}", f"*{entry} *"]
        for entry in SensitivePathPolicy.FILE_PREFIXES:
            rows += [f"*{entry}", f"*{entry} *", f"*{entry}.*"]
        dotenv = SensitivePathPolicy.DOTENV
        rows += [f"*{dotenv}", f"*{dotenv} *", f"*{dotenv}.*"]
        return tuple(rows)

    # -- detectors ---------------------------------------------------------

    def _privilege(self, line: ParsedCommandLine) -> str | None:
        """``sudo`` / ``doas`` / ``su`` in command position, or ``sudo -S``.

        Command position is the whole point: ``git commit -m "no sudo here"``
        lexes to four words, the last of which is the single token
        ``no sudo here``, so nothing here fires on it. (The ``*sudo *`` floor
        row does — that is the coarseness §9.2 accepts, and the corpus pins it.)
        """

        for segment in line.segments:
            candidates = line.command_candidates(segment)
            if self._PRIVILEGE & set(candidates):
                return _Note.PRIVILEGE
            if self._PRIVILEGE & set(segment.argv) and (
                self._STDIN_PASSWORD_FLAG in line.short_flags(segment)
            ):
                return _Note.PRIVILEGE
        return None

    def _destructive_delete(self, line: ParsedCommandLine) -> str | None:
        """A recursive ``rm`` whose operand resolves to ``/``, ``~`` or ``$HOME``.

        §9.2 specifies "a recursive+force flag set". This requires **recursive
        only**: the operand set is exactly three values for which no legitimate
        invocation exists, so ``rm -r /`` and ``rm -r ~`` cost nothing to refuse
        and are catastrophic to miss. The widening is deliberate and reported.
        """

        for segment in line.segments:
            if self._REMOVE not in line.command_candidates(segment):
                continue
            if not (self._RECURSIVE_FLAGS & line.short_flags(segment)):
                continue
            for operand in line.operands(segment, after=self._REMOVE):
                if self._normalise_operand(operand) in self._CATASTROPHIC_ROOTS:
                    return _Note.DESTRUCTIVE_DELETE
        return None

    def _filesystem_destruction(self, line: ParsedCommandLine) -> str | None:
        """``mkfs*``, ``dd of=`` under ``/dev/``, or a redirect to a raw device."""

        for segment in line.segments:
            for candidate in line.command_candidates(segment):
                if candidate.startswith(self._MKFS):
                    return _Note.FILESYSTEM_DESTRUCTION
                if candidate != self._DD:
                    continue
                for word in segment.argv:
                    if word.startswith(self._DD_OUTPUT) and word[
                        len(self._DD_OUTPUT) :
                    ].startswith(self._DEVICE_ROOT):
                        return _Note.FILESYSTEM_DESTRUCTION
            for target in segment.redirect_targets:
                if self._is_raw_device(target):
                    return _Note.FILESYSTEM_DESTRUCTION
        return None

    def _machine_state(self, line: ParsedCommandLine) -> str | None:
        """``shutdown`` / ``reboot`` / ``halt`` / ``poweroff``, ``init 0|6``, ``systemctl``."""

        for segment in line.segments:
            candidates = set(line.command_candidates(segment))
            if self._MACHINE_STATE & candidates:
                return _Note.MACHINE_STATE
            if self._INIT in candidates and (
                self._INIT_RUNLEVELS & set(line.operands(segment, after=self._INIT))
            ):
                return _Note.MACHINE_STATE
            if self._SYSTEMCTL in candidates and (
                self._SYSTEMCTL_TARGETS
                & set(line.operands(segment, after=self._SYSTEMCTL))
            ):
                return _Note.MACHINE_STATE
        return None

    def _pipe_to_interpreter(self, line: ParsedCommandLine) -> str | None:
        """A pipeline whose source fetches from the network and whose sink runs code.

        Grouped by ``|`` specifically, not by every control operator:
        ``curl x && sh y`` is two commands and is a documented miss, while
        ``curl x | tee /tmp/a | sh`` is one pipeline and is a hit.
        """

        fetched = False
        for segment in line.segments:
            if segment.lead not in self._PIPE_OPERATORS:
                fetched = False
            candidates = line.command_candidates(segment)
            if fetched and any(self._is_interpreter(name) for name in candidates):
                return _Note.PIPE_TO_INTERPRETER
            if self._FETCHERS & set(candidates):
                fetched = True
        return None

    def _credential_path(self, line: ParsedCommandLine) -> str | None:
        """Any word naming a path under a credential directory."""

        for word in line.words:
            if any(
                SensitivePathPolicy.has_sensitive_segment(spelling)
                for spelling in (word.text, word.raw)
            ):
                return _Note.CREDENTIAL_PATH
        return None

    def _credential_file(self, line: ParsedCommandLine) -> str | None:
        """Any word whose leaf name satisfies ``isSensitiveFileName``."""

        for word in line.words:
            if any(
                SensitivePathPolicy.leaf_is_sensitive(spelling)
                for spelling in (word.text, word.raw)
            ):
                return _Note.CREDENTIAL_FILE
        return None

    def _command_strings(self, line: ParsedCommandLine) -> tuple[str, ...]:
        """The ``-c`` payloads of any shell invoked in this line.

        Only a shell: ``python -c`` carries Python source, not a command line,
        and screening it as one would be a category error rather than extra
        coverage.
        """

        payloads: list[str] = []
        for segment in line.segments:
            if not (self._SHELL_BINARIES & set(line.command_candidates(segment))):
                continue
            argv = segment.argv
            for index, word in enumerate(argv):
                if not self._is_command_string_flag(word):
                    continue
                payload = next(
                    (
                        operand
                        for operand in argv[index + 1 :]
                        if not operand.startswith("-")
                    ),
                    None,
                )
                if payload:
                    payloads.append(payload)
                break
        return tuple(payloads)

    @classmethod
    def _is_command_string_flag(cls, word: str) -> bool:
        """True for ``-c`` or any short cluster containing it, never ``--c…``."""

        return (
            word.startswith("-")
            and not word.startswith("--")
            and cls._COMMAND_STRING_FLAG in word[1:]
        )

    @classmethod
    def _fork_bomb(cls, command: str) -> bool:
        """``:(){ :|:& };:`` and its spelling variants.

        Read off the raw text rather than the tokens because the shape *is*
        punctuation: a definition whose body pipes its own name into itself and
        backgrounds it. Whitespace is removed from the body before the check, so
        ``: () { : | : & }; :`` is the same hit.
        """

        for match in cls._FORK_BOMB.finditer(command):
            name = match.group(1)
            body = "".join(match.group(2).split())
            if f"{name}|{name}" in body and "&" in body:
                return True
        return False

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _refusal(note: str) -> ShellRefusal:
        """Every screen hit is the same closed reason; only the sentence differs."""

        return ShellRefusal.refused(ShellRefusalReason.COMMAND_NOT_PERMITTED, note)

    @staticmethod
    def _normalise_operand(operand: str) -> str:
        """Strip a trailing ``/`` or ``/*`` so ``/``, ``/*`` and ``~/`` compare equal."""

        stripped = operand.rstrip("*").rstrip("/")
        return stripped or "/"

    @classmethod
    def _is_raw_device(cls, target: str) -> bool:
        """True for ``/dev/disk2``, ``/dev/rdisk0``, ``/dev/sda`` and friends."""

        if not target.startswith(cls._DEVICE_ROOT):
            return False
        node = target[len(cls._DEVICE_ROOT) :]
        return node.startswith(cls._RAW_DEVICES)

    @classmethod
    def _is_interpreter(cls, name: str) -> bool:
        """True for a shell, ``python*``, or any other code-running binary."""

        leaf = name.rsplit("/", 1)[-1]
        return leaf in cls._INTERPRETERS or leaf.startswith(cls._INTERPRETER_PREFIX)


__all__ = [
    "CommandLexer",
    "CommandNeverList",
    "CommandSegment",
    "CommandToken",
    "ParsedCommandLine",
    "SensitivePathPolicy",
]
