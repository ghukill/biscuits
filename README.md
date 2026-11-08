# biscuits

Biscuits: an Asana-flavoured web UI for [OpenProject](https://www.openproject.org/).

A small Flask app, with plain ES modules and no front-end build. Each person signs
in with their own account and acts in OpenProject as themselves. biscuits keeps a
snapshot of what each person can see and does its tree-building, filtering and
search locally. Every OpenProject call goes through the server.

## Install

Requires [uv](https://docs.astral.sh/uv/). Python 3.11+ is handled by uv.

```sh
uv tool install git+ssh://git@github.com/ghukill/biscuits.git
# or, from a checkout:
uv tool install .

# to pick up new code later:
uv tool upgrade biscuits            # installed from git
uv tool install --force .           # installed from a checkout
```

## Set up and run

```sh
biscuits config init     # creates ~/.biscuits/config.toml (mode 600)
biscuits users add NAME --admin   # the first admin; prints a temporary password
biscuits doctor          # checks the config, accounts and OpenProject connection (read-only)

biscuits serve           # in the foreground; Ctrl-C to stop
biscuits serve -d        # in the background
biscuits status
biscuits logs -f
biscuits restart
biscuits stop
```

Then open <http://127.0.0.1:9173/>. See [`config.example.toml`](config.example.toml)
for every setting.

## Accounts and login

biscuits has its own accounts, kept in `biscuits.db`. Each one links to an
OpenProject user and holds that user's API key, encrypted with `[auth]
encryption_key`, so a leaked database alone doesn't give up the keys.

Tasks are always read and written with the signed-in person's own key.
OpenProject decides what they can see and edit, and its history shows who really
made each change. The `[writes]` gate applies on top.

The `[openproject] admin_token` is used only for managing accounts and looking up
names. It's never used for tasks, and never as a retry after a person's own key
is refused. That's all in `opadmin.py`, and a test holds it to that.

Admins manage accounts under **Accounts** in the sidebar, or with:

```sh
biscuits users add NAME [--admin] [--op-user ID] [--key-stdin]
biscuits users set-key NAME [--op-user ID] [--key-stdin]
biscuits users list
biscuits users reset-password NAME
biscuits users disable NAME
biscuits users enable NAME
```

**Giving someone an API key.** OpenProject's API can't issue keys, so run this on
the OpenProject host and paste the result into the admin screen, which shows the
exact command:

```sh
docker exec <container> bundle exec rails runner \
  "puts Token::API.create!(user: User.find(ID), token_name: 'biscuits').plain_value"
```

Saving a key checks with OpenProject that it belongs to the linked user, so a
mis-pasted key can't attribute someone's edits to the wrong person.

**Offboarding.** Disable the account and tick "also lock in OpenProject". Locking
kills the key at once, everywhere. If someone is locked directly in OpenProject,
biscuits drops its copy of their data the next time it refreshes, about two minutes
at most while they're active, and shows them an "ask your admin" message.

**HTTPS.** biscuits runs on plain HTTP, and shows a small note on every page when
the connection isn't HTTPS. That includes a VPN such as Tailscale: it does encrypt
the traffic, but the app can't tell. Behind a reverse proxy that terminates HTTPS,
set `[server] trusted_proxy` to the proxy's address, which clears the note and
marks session cookies `Secure`. Without it, `X-Forwarded-Proto` is ignored, so it
can't be spoofed.

## Configuration

The config file is found in this order: `--config FILE`, `$BISCUITS_CONFIG`,
`~/.biscuits/config.toml`.

Edits apply on the next request, without a restart, so you can repoint
OpenProject or pull back writes on a running server. `[server]` and `[storage]`
settings need `biscuits restart`. If an edit leaves the file invalid, requests
return an error until it's fixed; biscuits does not fall back to the old
settings, so a botched "turn writes off" can't leave writes on.

By default, everything biscuits keeps lives next to the config file:

| File | What |
|---|---|
| `config.toml` | Settings and secrets |
| `snapshots/<account id>.json` | Each person's cached OpenProject data, so restarts show data immediately |
| `write-log.jsonl` | Audit log of every edit, sent or not |
| `biscuits.db` | Accounts (with encrypted keys) and archived projects. SQLite, migrated automatically |
| `agent-mcp.json` | The `biscuits agent` session's connection settings, including its key |
| `biscuits.log`, `biscuits.pid` | Background server log and lock |

## Writes

Edits only reach OpenProject when `[writes] mode = "live"` **and** every project
they touch is in `[writes] projects` (a list of ids, or `"*"` for all). Otherwise
they apply in the browser as a preview and vanish on reload. New configs start
in preview. Every attempt is logged, and there is no delete operation in the code.

The same gate covers **file uploads** in the task pane (the Add files button,
dragging files onto the pane, or pasting an image). Each is up to 10 MB
(`writes.MAX_UPLOAD`); OpenProject's own limit may be lower. The log records the
file's name, type and size, never its contents. Files are shown through biscuits
with the viewer's own key, so OpenProject decides who sees them. Only plain image
types (PNG, JPEG, GIF, WebP, AVIF, BMP) open in the browser; everything else,
including SVG, downloads. Nothing is cached on the server, and browsers may keep a
private copy for five minutes.

```sh
biscuits writes status    # is the gate open? what's been sent?
biscuits writes log -n 50
```

## The agent account

`biscuits agent` starts a Claude Code session signed in as a dedicated OpenProject
account, so tasks can be assigned to it. It lists the open tasks assigned to it and
asks which to work on. You approve its work as it goes, and nothing runs unattended.

To set it up, create a non-admin OpenProject user, make it a member of the project,
and add an `[agent]` section to the config (see `config.example.toml`). biscuits
refuses a token that belongs to an OpenProject admin. The session's key is also
written to `agent-mcp.json` in the data directory, mode 600. The session reaches
OpenProject through `biscuits mcp`, below.

The session runs as your own Unix user, so the agent's permissions are limited in
OpenProject but not on this machine. It's told not to use other credentials, but
nothing stops it from reading them, so check what you approve.

## The OpenProject MCP server

`biscuits mcp` runs an [MCP](https://modelcontextprotocol.io/) server over stdio, so
AI tools can read and change OpenProject tasks. It has 13 tools: `list_projects`,
`search_work_packages`, `get_work_package`, `get_comments`, `create_work_package`,
`update_work_package`, `add_comment`, `add_attachment`, `count_work_packages`,
`list_statuses`, `list_types`, `list_priorities` and `list_users`. None of them
deletes anything.

`add_attachment` uploads a local file (`file_path`) or base64 content
(`content_base64` plus `file_name`), up to 10 MB, and returns a `markdown`
snippet. Put that in a description or comment to show the file there, in both
biscuits and OpenProject. `get_work_package` lists a task's attachments.

It's independent of the web app. It uses only the standard library and doesn't read
the biscuits config, so it runs without a config file and while the web server is
down. Credentials come from these environment variables:

| Variable | What |
|---|---|
| `OPENPROJECT_URL`, `OPENPROJECT_API_KEY` | The server and key to use |
| `OPENPROJECT_ENV_FILE` | A file of `KEY=value` lines, used for whichever of those two isn't set |

```json
"openproject": {
  "command": "/home/you/.local/bin/biscuits",
  "args": ["mcp"],
  "env": {"OPENPROJECT_ENV_FILE": "/path/to/openproject.env"}
}
```

Without credentials it still starts and lists its tools, and every tool call returns
a "not configured" error.

**Its writes bypass the `[writes]` gate and the write log,** because it doesn't read
the config. OpenProject's own permissions for the key are the only limit, and its
history shows that key's user as the author. Give it a key with no more access than
it needs.

### OpenProject API findings

Checked against our instance in November 2026. These are why the server's requests
look the way they do.

- `offset` is a 1-based **page number**, not a row offset, so keep the page size the
  same across requests.
- With no `filters` parameter, `/work_packages` and `/projects/{id}/work_packages`
  return **open** tasks only. Send `filters=[]` to get everything.
- Boolean filter values are `"t"` and `"f"`, not `"true"` and `"false"`.
- `sortBy` needs its JSON form, `sortBy=[["id","desc"]]`. The shorthand `id:desc` is
  rejected.
- `dueDate <>d ["2026-10-01","2026-10-31"]` works as a date range. `dueDate <t- ["0"]`
  also returned tasks due today, so check it before using it to mean "overdue".
- Archived projects answer 403 on their work package endpoints, even for an admin,
  and their tasks don't appear in the global list.
- Creating a task with `"description": null` answers 500: leave the key out.
- PATCH needs the current `lockVersion`.
- An assignee is written as `{"href": "/api/v3/users/{id}"}`.
- `subjectOrId` with operator `**` searches subjects and ids together.

## Other admin commands

```sh
biscuits config show        # effective settings, secrets masked
biscuits config path
biscuits config add-auth-keys   # add login secrets to a config from before login existed
biscuits snapshot info      # each account's cached snapshot
```

## Running it long-term

`serve -d` doesn't survive a reboot. For a server, use a systemd unit that runs
`serve` in the foreground. `~/.config/systemd/user/biscuits.service`:

```ini
[Unit]
Description=biscuits
After=network-online.target

[Service]
ExecStart=%h/.local/bin/biscuits serve
Restart=on-failure

[Install]
WantedBy=default.target
```

```sh
systemctl --user enable --now biscuits
loginctl enable-linger "$USER"    # keep it running when logged out
```

To serve under a sub-path behind a reverse proxy, set `[server] base_path`, e.g.
`"/biscuits"`, and have the proxy pass the path through unchanged.

## Moving from an env-file setup

`config init` can read an env file with `OPENPROJECT_URL`, `OPENPROJECT_API_KEY`
(used as the admin token), `BISCUITS_WRITES` and `BISCUITS_WRITE_PROJECTS`,
**including its write settings**:

```sh
biscuits config init --from-env path/to/openproject.env --host 0.0.0.0
cp path/to/write-log.jsonl ~/.biscuits/   # keep the audit history
```

These environment variables aren't read at runtime: settings live in the config file.

## Development

```sh
uv sync
uv run pytest           # uses a stub OpenProject; never touches a real server
uv run biscuits --config ./dev/config.toml serve
```

| Path (`src/biscuits/`) | Role |
|---|---|
| `cli.py` | The `biscuits` command |
| `config.py` | Finds, validates and re-reads the config file |
| `daemon.py` | Server lifecycle (waitress, flock-held pid file, background mode) |
| `app.py`, `bp.py` | Flask app; the UI routes and JSON API |
| `auth.py` | Login, the gate in front of every route, account and admin APIs |
| `accounts.py` | Accounts: passwords, encrypted keys, rate limiting |
| `client.py` | **GET-only** OpenProject client. Always needs a key; there's no default |
| `opadmin.py` | **The only code that may use the admin token** |
| `writes.py` | **The only web app code that sends non-GET requests**, behind the write gate |
| `agent.py` | `biscuits agent`, the Claude Code session signed in as the agent account |
| `mcp_server.py` | `biscuits mcp`. Standalone: standard library only, no config, and its own writes outside the gate |
| `snapshot.py` | Per-account snapshot caches, with Asana-section detection |
| `db.py`, `migrations/` | biscuits' own SQLite database and its Alembic migrations |
| `archive.py` | Archived projects (a biscuits flag; OpenProject isn't touched) |
| `sections.py` | The order of each project's sections (stored in biscuits; OpenProject isn't touched) |
| `static/`, `templates/` | The single-page UI |
