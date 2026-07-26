# Local Generative Workflows fixture connector

This is a **test-only, stdio MCP server** for the Desktop Generative Workflows
release journeys. It is intentionally not a production connector and no
production service imports it.

It loads the deterministic fictional data in
[`../scenarios/local-communications.json`](../scenarios/local-communications.json)
into memory. There is no listener, HTTP client, filesystem effect outside that
one read-only scenario load, provider credential, or account configuration.

## Safety contract

- Every mutable target must exactly equal a known `fixture://` resource target.
  `https://`, SMTP, Discord, X, local paths, target traversal, and unfamiliar
  fixture paths are rejected.
- Argument keys resembling credentials (`token`, `password`, `api_key`,
  `authorization`, and related forms) are rejected before dispatch.
- Mutable data lives only in process memory. Restarting the process starts from
  the seed. `fixture_reset` resets state in-process and appends a reset record
  rather than deleting history.
- The audit log is append-only and SHA-256 hash chained. Values returned from
  `fixture_audit` are detached copies; mutation by a test cannot alter the
  connector's chain. `fixture_audit.valid` verifies the chain.
- The only fault behaviour comes from the checked-in scenario: first workspace
  read returns `grant_expired`, the declared unknown operation returns
  `unknown_operation`, and first Discord publish returns a retryable failure.

## Run as MCP stdio

```bash
python3 tools/desktop-journeys/generative-workflows/local-fixture-connector/server.py
```

The command accepts JSON-RPC over stdin and writes JSON-RPC to stdout. It
implements the MCP methods `initialize`, `tools/list`, and `tools/call`.
The scenario path is fixed in source to prevent a journey from selecting an
arbitrary local file or a URL.

For a supervised Desktop journey, the temporary MCP registration must use a
stdio command whose absolute path points at this worktree's `server.py`:

```json
{
  "transport": "stdio",
  "command": "python3",
  "args": [
    "/absolute/worktree/tools/desktop-journeys/generative-workflows/local-fixture-connector/server.py"
  ]
}
```

The future runner must register this only in its throwaway Desktop user-data
profile, reset it before every case, and remove the registration during
teardown. Do not put this server in the product connector catalog.

## Tool contract

All tool inputs are JSON objects. Read operations return source targets so the
run can show exact provenance. Staged writes create a `stage_id`; effects require
the same `stage_id`, its exact target, and `approved: true`. Repeating a
successful effect is idempotent.

| Domain    | Read tools                                           | Stage tools                                          | Approved local effect                                                                |
| --------- | ---------------------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Fixture   | `fixture_manifest`, `fixture_audit`                  | `fixture_reset`                                      | N/A                                                                                  |
| Mail      | `mail_list_threads`, `mail_get_thread`               | `mail_draft_reply`                                   | `mail_send_draft`                                                                    |
| Timeline  | `timeline_list_posts`, `timeline_get_post`           | `timeline_draft_reply_post`                          | `timeline_publish_draft`                                                             |
| Discord   | `discord_list_channels`, `discord_get_messages`      | `discord_draft_announcement`                         | `discord_publish_announcement`                                                       |
| Workspace | `workspace_list`, `workspace_read`, `workspace_stat` | `workspace_write_revision`, `workspace_apply_rowset` | repeat the same workspace tool with `stage_id`, exact `target`, and `approved: true` |

### Required arguments

- Resource reads take an exact `target` and the resource id/path. Example:
  `mail_get_thread({"thread_id":"thr_q3_renewal","target":"fixture://generative-workflows/launch-week/mail/threads/thr_q3_renewal"})`.
- Drafts take the specific id/path, body/content, and exact target. They do not
  affect the resource.
- Commit calls take `stage_id`, the same exact `target`, and `approved: true`.
- Row-set staging takes `path`, `row_key`, `changes`, optional `holds`, and
  target. Applying a held row is rejected; applying an approved subset returns
  `status: "partial"` and exact held/applied rows.

The `fixture_manifest` response supplies both allowed roots. The canonical
workspace root is `fixture://workspace/launch-week`; communication targets are
under `fixture://generative-workflows/launch-week`.

## Journey runner integration requirements

1. Launch the Desktop app with a fresh user-data subdirectory and register the
   fixture through the existing authenticated facade MCP registration flow.
2. Call `fixture_reset` through the registered MCP before each deterministic or
   BYOK case. Save `fixture_audit` output and run-event replay as artifacts.
3. For the keyless deterministic pass, use the product's deterministic model
   test route—not a provider key or a mock UI.
4. For the later BYOK pass, use `load_env_key` and `ftue_add_key` only. Do not
   print, persist, include, or serialize the value anywhere in the scenario,
   audit, screenshot name, or prompt.
5. Treat `grant_expired`, `unknown_operation`, and the first Discord
   `retryable_failure` as required journey assertions, then verify that replay
   and the fixture audit tell the same story.

## Test

```bash
python3 -m unittest discover \
  -s tools/desktop-journeys/generative-workflows/local-fixture-connector/tests \
  -p 'test_*.py' -v
```

This checks stage/approval/idempotency behaviour, target confinement, secret
rejection, deterministic faults, audit-chain integrity, and the actual stdio
MCP transcript. It does not use provider keys or launch the Desktop app.
