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

## 4. Retention — decide explicitly, do not inherit

`/large_tool_results/` is the sharp edge. Those files are offloaded **tool
output**: potentially large, and potentially containing anything a tool read.
Once they are real files they need the same treatment as the rest of the
deletion surface the compliance rules enumerate (conversations, messages, runs,
events, outbox rows, payload refs, memory, checkpoints, approvals, tool
invocations).

Answer before implementing: how long does `.tmp` survive a completed run; is
chat deletion best-effort or guaranteed; what happens to `.tmp` when the app is
force-quit mid-run.

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

## 7. Definition of done

- [ ] A project owns ≥1 folder; opening it makes those folders readable without prompting
- [ ] Deleting a project revokes its grants, with the confirm dialog saying so
- [ ] `.tmp/<conversation_id>/` created on first need, with `meta.json`
- [ ] Tool results, subagent artifacts and drafts are inspectable files at the paths in §3
- [ ] Deleting a chat removes its `.tmp` directory
- [ ] `.copilot` creation removed; read-only grants no longer a special case
- [ ] A test proves the `.tmp` allow survives the dotted-segment matching trap (§5)
- [ ] No chat title appears in any path, log line or error message
