# Troubleshooting 0xCopilot

0xCopilot runs entirely on your machine — the `copilot` command stages a private
runtime (Python + PostgreSQL + the app's services) and launches the app. Because
everything is local, the occasional hiccup is fixable from your terminal without
losing any of your data.

**The reset ladder — try these in order (top = safest):**

| Command                    | What it does                                                              | Your data              |
| -------------------------- | ------------------------------------------------------------------------- | ---------------------- |
| `copilot doctor`           | Diagnoses the install and prints what's wrong                             | untouched              |
| `copilot repair`           | Unblocks a stuck launch (orphaned database, stale lock)                   | **kept**               |
| `copilot repair --session` | Also signs you out (clears a stuck sign-in)                               | kept (just re-sign-in) |
| `copilot uninstall`        | Wipes the staged runtime **and** local app data (database, logs, secrets) | **deleted**            |

---

## "0xCopilot could not start" — Postgres won't start

> `postgres start failed … pg_ctl: another server might be running; trying to start server anyway; pg_ctl: could not start server`

**What happened:** the app was force-quit or crashed last time, so its embedded
database was left running and is still holding its data folder. The next launch
can't start a second one on top of it.

The app now clears this **automatically on the next launch**, so it should be
rare. If a launch still won't come up:

```bash
copilot repair      # stops the orphaned database and frees the lock
copilot             # start again
```

Manual fallback (only if `copilot repair` can't):

```bash
# macOS / Linux — stop the leftover database, then relaunch
"$HOME/.0xcopilot/runtime/$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m | sed 's/x86_64/x64/;s/aarch64/arm64/')/postgres/bin/pg_ctl" \
  -D "$HOME/Library/Application Support/0xCopilot/pgdata" -m fast stop
copilot
```

If even that fails, `copilot uninstall` resets everything (this deletes your
local database).

---

## "Applying database migrations…" on startup — is this a bug?

**No — this is normal.** On first launch (and briefly on later ones) the app sets
up its bundled database's schema. It's the same thing any app with a built-in
database does on first run. Let it finish; it moves on to "Starting services…"
and opens the app.

---

## Stuck on the wrong account, or "Invalid bearer token" in Chats

> `Couldn't load chats … UnauthorizedError: Invalid bearer token`

This means a saved sign-in is no longer valid. The app is designed to drop a
rejected session and show the sign-in screen on the next launch. If it doesn't,
force it:

```bash
# In the app: Profile (bottom-left) → Sign out
# …or from the terminal:
copilot repair --session     # clears saved sign-ins (keeps everything else)
copilot                      # start again → sign-in screen
```

Then sign in with a wallet (**Connect with a wallet** — a signed message, no
transaction) or Google.

---

## Where are the logs?

Point support (or yourself) at the log folder in the app's data directory:

| OS      | Logs folder                                    |
| ------- | ---------------------------------------------- |
| macOS   | `~/Library/Application Support/0xCopilot/logs` |
| Windows | `%APPDATA%\0xCopilot\logs`                     |
| Linux   | `~/.config/0xCopilot/logs`                     |

Open the newest file there; the failing step's error is usually near the end.

---

## Nothing works — start completely fresh

```bash
copilot uninstall            # removes the staged runtime + all local app data
npm install -g @0x-copilot/cli   # (or: reinstall the version you want)
copilot                      # re-stages from scratch, first run is slower
```

To remove the command itself afterward: `npm rm -g @0x-copilot/cli`.

---

Still stuck? Open an issue with your `copilot doctor` output and the tail of the
newest log file: https://github.com/0x-copilot-dev/0x-copilot/issues
