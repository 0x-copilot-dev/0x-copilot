# PRD-FS-12 — Projects own folders; agent state lives on disk

**Status:** decisions LOCKED with the user, implementation not started
**Obsoletes:** the `.copilot`-inside-a-granted-folder scratch directory (see §2)
**Interacts with:** PRD-FS-10 (composer folder bar — see §6)

---

## 1. Why

Two problems that turn out to be one.

**A grant has no home.** Today a granted folder is a floating, per-user, machine-level
fact with no relationship to what you are working on. But for most real work the
folder _is_ the project. Opening a project should mean the agent already has the
folder — one act of context-switching instead of four.

**The agent's own work is invisible.** `/large_tool_results/`, `/subagents/` and
`/drafts/` are virtual namespaces backed by ephemeral state. Offloaded tool
results and subagent transcripts — the exact material you want when asking "what
did it actually do?" — cannot be opened, diffed or kept. Making them real files
is the same direction the file-native store already took for memory.

## 2. Decisions (locked)

| #   | Decision                                                                                                                                                                                                                                                                                                     |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| D1  | **A project OWNS its folders.** One or many. Not a reference to a user-level grant.                                                                                                                                                                                                                          |
| D2  | **Deleting a project REVOKES its grants.** Destructive, so the confirm dialog must say so. Silently retaining filesystem access after deleting the thing that justified it is worse than the warning.                                                                                                        |
| D3  | **Per-chat agent state lives at `$COPILOT_HOME/.tmp/<conversation_id>/`.** `COPILOT_HOME` is `~/.0xcopilot` — NOT the install directory, which an upgrade can replace and which may be read-only.                                                                                                            |
| D4  | **The directory is named by `conversation_id`, never the chat title.** A title is user content: it can carry names, client names, anything, and a title-derived path leaks that into the filesystem, into logs, and into every error message that prints a path. Titles also change; the directory must not. |
| D5  | **Each directory carries a `meta.json`** with the human-readable name and whatever else the agent needs to orient. The opaque id names the _path_; the metadata inside carries the _meaning_.                                                                                                                |
| D6  | **Deletion cascades.** Deleting a chat deletes its `.tmp` directory. Deleting a project revokes its grants (D2).                                                                                                                                                                                             |
| D7  | **`.copilot` inside a granted folder is DROPPED.** Per-chat ephemera belongs in `.tmp`. This removes "we write into the folder you shared" entirely and stops read-only grants being a special case.                                                                                                         |

## 3. Namespace re-rooting

Everything becomes real files. What differs is what each is rooted at — the
namespaces do not share a lifetime, and rooting them all at the run would break
three of them.

| Namespace                            | Rooted at    | Location                          | Why                                           |
| ------------------------------------ | ------------ | --------------------------------- | --------------------------------------------- |
| `/large_tool_results/`               | run          | `.tmp/<conv>/<run>/tool-results/` | offloaded results of THIS run's calls         |
| `/subagents/`                        | run          | `.tmp/<conv>/<run>/subagents/`    | this run's subagent transcripts + artifacts   |
| `/drafts/`                           | conversation | `.tmp/<conv>/drafts/`             | a draft outlives the run that started it      |
| `/memories/` `/policies/` `/skills/` | user         | existing file store               | **memory that dies with a run is not memory** |
| `/workspace/`                        | host grant   | the user's own folder             | already real files                            |

**Verify before building:** `factory._composed_deep_backend` documents
`/memories/` · `/policies/` · `/skills/` as already file-backed on desktop via
`FileMemoryBackendFactory` (persisting as `memory/<scope>/<key>.json` plus a
human `.md`). If that holds, the user tier is already done and the work is only
the run/conversation tier. Confirm by reading and running it — this program has
twice had "already implemented" turn out to be false when checked.

## 4. Retention — D8: no cleanup

**Decision: `.tmp` is never cleaned on a timer.** It is deleted only when the
chat that owns it is deleted (D6). Nothing ages out, nothing is size-capped.

This matches Claude Code, which keeps every transcript indefinitely with no
retention setting. Measured on this machine: `~/.claude/projects` holds 2,404
transcripts totalling **1.4 GB** accumulated over **17 days** — roughly
82 MB/day with no cleanup mechanism at all.

**The accepted risk, recorded so it is a decision and not an oversight.** That
policy is cheaper for Claude Code than for us: its transcripts are JSONL text,
whereas `/large_tool_results/` holds content that was offloaded _precisely
because it was too large to inline_. Same policy, steeper curve. If `.tmp`
growth becomes a support issue, the first lever is an age-out on
`tool-results/` alone — it is cache (the ledger holds the reference), while
`subagents/` and `drafts/` are record and explain what the agent did.

A force-quit mid-run needs no separate sweep: the directory belongs to a
conversation that exists, so D6 already collects it when that chat is deleted.

Retention questions that remain open belong to the ledger and payload refs, not
here — this PRD does not change those.

## 5. Permissions

`.tmp` is the agent's own working area, so it is a direct-write allow — the same
reasoning that justified `.copilot`, now applied somewhere that is not the
user's folder. Requirements:

- the allow is scoped to `$COPILOT_HOME/.tmp/**` and nothing above it;
- it must NOT rely on a glob catch-all: `.tmp` is a dotted segment and deepagents
  matches under `wcmatch` with no `DOTGLOB`, so `**` will not match it. This is
  the exact hole that let `~/.ssh/id_rsa` through — write the rule with a
  literal path and pin it with a test that fails if the pattern stops matching;
- host writes outside `.tmp` and outside a writable grant stay on the staged
  C3 → ledger → C2 lane. This PRD does not widen that.

## 6. Effect on PRD-FS-10

The composer folder bar stays, but its meaning changes: from "attach a folder"
to "you are in the _kaleidoscope_ project, here is its folder". That is the
difference between a per-chat control and a project indicator, and it is worth
settling before the bar ships — a bar that teaches "folders are per-chat" will
have to be re-taught once projects own them.

Open: does the bar still offer ad-hoc attach for a chat with no project, or does
it prompt to pick a project? Ad-hoc attach is the most common first use, so it
should keep working; the bar likely shows the project when there is one and the
plain attach affordance when there is not.

### 6.1 Moving a chat between projects

Allowed, and normal — chats get miscategorised and reorganising is expected.
But because a project OWNS its folders (D1), moving a chat CHANGES WHAT IT CAN
READ. A chat that read files from project A, moved to B, can no longer re-read
them: the transcript still shows the reads, and re-running fails. Two rules make
that honest rather than surprising.

- **D9 — the move changes access going FORWARD only, never retroactively.** Do
  not rewrite, re-scope or re-authorise history. What the chat already did stays
  exactly as recorded.
- **D10 — the move dialog says what changes**, in one line: "This chat will lose
  access to _kaleidoscope_ and gain _atlas_." A silent access change is how
  someone ends up debugging why the agent suddenly cannot see a file it read
  yesterday.

**Ad-hoc grants survive the move.** They were granted to THAT CHAT, not to the
project, so moving it must not revoke them.

## 7. Definition of done

- [ ] A project owns ≥1 folder; opening it makes those folders readable without prompting
- [ ] Deleting a project revokes its grants, with the confirm dialog saying so
- [x] `.tmp/<conversation_id>/` created on first need, with `meta.json`
- [ ] Tool results, subagent artifacts and drafts are inspectable files at the paths in §3
- [x] Deleting a chat removes its `.tmp` directory
- [x] `.copilot` creation removed; read-only grants no longer a special case
- [x] A test proves the `.tmp` allow survives the dotted-segment matching trap (§5)
- [x] No chat title appears in any path, log line or error message
- [ ] Moving a chat between projects changes access forward-only and states what changes
- [ ] Ad-hoc grants survive a project move

### 7.1 What shipped, and what §3 still needs

The `.tmp` foundation is in: `agent_runtime/capabilities/desktop/agent_scratch.py`
owns the layout, the naming rule (D4 enforced structurally — an id that is not
opaque RAISES rather than being sanitised), the `meta.json` (D5), the deletion
verb (D6) and the literal allow rule (§5). `host_filesystem` no longer sites a
`.copilot` anywhere (D7); `host_floor` admits the scratch for read and write and
nothing beside it. Provisioning runs from `runtime_worker/agent_scratch_wiring.py`
on the desktop gate only; deletion cascades from BOTH file-store deletion sites.

**One correction to §5 worth recording**, found by mutating the rule and watching
the tests stay green: because `COPILOT_HOME` is itself dotted (`~/.0xcopilot`),
_every_ path beneath it is matcher-blind, so on the default configuration
`_check_fs_permission` answers `allow` for the scratch whether or not the rule
exists. `HostFilesystemFloor` is what genuinely decides there. The literal rule is
still load-bearing — for a visible scratch name, and for the day upstream adds
`DOTGLOB` — but the PRD's framing ("write the rule with a literal path and pin it
with a test") is only half the job. The other half is that the floor must be
keyed on the same root, which it now is. Tests assert each half against the layer
that actually owns it.

**The three namespace re-rootings are NOT done.** Each is its own change:

| Namespace              | What blocks it                                                                                                                                                                                                                                                                                                                                    |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/large_tool_results/` | The writer is `OffloadWriter = Callable[[str], str]` with no run scope, and the `/large_tool_results/<sha>` locator is parsed by `file/repair.py`, `file/_deletion.py`, `file/migration.py` and the object GC. Re-rooting means threading run scope through `ToolResultAdmissionAdapter` AND keeping the ledger's existing references resolvable. |
| `/subagents/`          | `FileSubagentTraceBackend` is conversation-scoped over hashed JSONL and is read by the Run cockpit's Agents panel. Re-rooting to `<run>/subagents/` changes a read path the UI depends on.                                                                                                                                                        |
| `/drafts/`             | `FileDraftStore` carries versioning plus `DRAFT_UPDATED` events and feeds the drafts→artifact lane. Moving the bytes must preserve both.                                                                                                                                                                                                          |

The `.tmp` run/conversation tier directories (`<run>/tool-results/`,
`<run>/subagents/`, `<conv>/drafts/`) are created and permission-correct, so each
re-rooting is a write-site change rather than a foundation change.
