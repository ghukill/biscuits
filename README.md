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

# a specific release (what prod runs):
uv tool install --force git+https://github.com/ghukill/biscuits.git@v0.5.0

# to pick up new code later:
uv tool install --force --reinstall .   # installed from a checkout
```

Releases are listed in `CHANGELOG.md`. See "Releases" below for how they're made.

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
| `biscuits.db` | Accounts (with encrypted keys), API token hashes, archived projects and section order. SQLite, migrated automatically |
| `agent-mcp.json` | The `biscuits agent` session's MCP settings, including its key or token |
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
refuses a token that belongs to an OpenProject admin. The session's MCP settings
are written to `agent-mcp.json` in the data directory, mode 600, which keeps them
out of `ps` and the session's environment.

With `[agent] mcp_url` and `mcp_token`, the session reaches OpenProject through a
biscuits server's MCP endpoint, below, as that token's account. Without them it
uses `biscuits mcp-direct`, straight to OpenProject.

The session runs as your own Unix user, so the agent's permissions are limited in
OpenProject but not on this machine. It's told not to use other credentials, but
nothing stops it from reading them, so check what you approve.

## MCP: how programs use biscuits

Programs (agent sessions, the Mattermost bots, other Claude Code sessions) reach
OpenProject **through biscuits**, over [MCP](https://modelcontextprotocol.io/), and
not by calling OpenProject themselves. Each one signs in as a biscuits account, so:

- OpenProject sees that account's own key, and shows it as the author.
- Its writes pass the `[writes]` gate and are in the write log, marked `"via": "mcp"`.
- It knows biscuits' own data: archived projects and the order of sections.

There are 14 tools: `list_projects`, `search_work_packages`, `get_work_package`,
`get_comments`, `create_work_package`, `update_work_package`, `add_comment`,
`add_attachment`, `count_work_packages`, `list_statuses`, `list_types`,
`list_priorities`, `list_users` and `list_sections`. None of them deletes anything.
The first 13 have the same names and arguments as the old standalone server, so
existing skills keep working. What's new:

- `list_projects` marks each project `archived` (in biscuits), and with
  `active_only` (the default) leaves archived ones out.
- `get_work_package` gives the task's `section`, if it's in one.
- `list_sections` lists a project's sections in biscuits' order. To put a task in a
  section, give the section's id as `parent_id`.
- Comment authors are named even when the account's key can't see other users.
- Writes don't email people (`notify=false`), as before.

`add_attachment` takes `content_base64` plus `file_name`, up to 10 MB, and returns a
`markdown` snippet to put in a description or comment. `file_path` works through the
`biscuits mcp` client, which reads the file on your machine.

### The endpoint

The server answers MCP at `<base_path>/mcp` (Streamable HTTP, JSON replies, no
sessions). Two checks come first:

- **Where from:** only addresses in `[mcp] allow_from`, by default this machine and
  the tailnet (`100.64.0.0/10`, `fd7a:115c:a1e3::/48`). Behind a public proxy
  (`trusted_proxy`), the real client address is checked, so the public internet is
  refused even with a good token.
- **Who:** a biscuits API token, as `Authorization: Bearer bsk_...`. Bad tokens are
  rate limited like bad passwords.

```sh
biscuits tokens add USERNAME --name mattermost [--out FILE]   # shown or written once
biscuits tokens list
biscuits tokens revoke ID
```

Only a hash of each token is kept. A token stops working when it's revoked or its
account is disabled. The account needs an OpenProject key (`biscuits users
set-key`). Bots share one account, linked to the "Biscuits Agent" OpenProject user:

```sh
biscuits users add biscuits-agent --name "Biscuits Agent" --op-user 16 --key-stdin < agent.key
biscuits tokens add biscuits-agent --name mattermost --out ~/.biscuits/mattermost.token
```

An MCP client that speaks HTTP can use the endpoint directly. For Claude Code:

```json
"openproject": {"type": "http", "url": "http://your-biscuits-host:8000/mcp",
                "headers": {"Authorization": "Bearer bsk_..."}}
```

### `biscuits mcp`: the stdio client

For clients that can only start a local process. It passes each message to the
endpoint and never talks to OpenProject. Standard library only, no config file.

| Variable | What |
|---|---|
| `BISCUITS_MCP_URL` | The endpoint, e.g. `http://your-biscuits-host:8000/mcp` |
| `BISCUITS_MCP_TOKEN_FILE` | A file holding the token (better: keeps it out of the environment) |
| `BISCUITS_MCP_TOKEN` | The token itself, if there's no file |

```json
"openproject": {
  "command": "/home/you/.local/bin/biscuits",
  "args": ["mcp"],
  "env": {"BISCUITS_MCP_URL": "http://your-biscuits-host:8000/mcp",
          "BISCUITS_MCP_TOKEN_FILE": "/home/you/.biscuits/mattermost.token"}
}
```

**For now**, `biscuits mcp` with no `BISCUITS_MCP_URL` but with `OPENPROJECT_*`
variables set runs `biscuits mcp-direct`, and says so on stderr, so setups from
before this keep working until they're switched over.

### `biscuits mcp-direct`: break glass

The old standalone server: straight to OpenProject, for when biscuits is down. It
**bypasses the `[writes]` gate and the write log**, and the key's own user is the
author. Credentials come from `OPENPROJECT_URL` and `OPENPROJECT_API_KEY`, or a file
of `KEY=value` lines named by `OPENPROJECT_ENV_FILE`. Without them it still starts
and lists its tools, and every tool call returns a "not configured" error.

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

## Releases

A **release** is the only thing that changes the version, and every release goes
to prod. Day to day:

```
task branch ──→ dev        throwaway, runs on the dev server for testing
     └────────→ main       once approved; the version doesn't change
                 └─ release: bump + CHANGELOG, tag vX.Y.Z, push,
                    deploy_release vX.Y.Z on prod
```

- **Task branches** start from `main`. To try one, merge it into `dev` and install
  that on the dev server: `uv tool install --force --reinstall . && biscuits restart`.
  `dev` is reset to `main` from time to time, so never build on it.
- **Once a task is approved,** its own branch is merged to `main`, not `dev`. Tasks
  can be approved one at a time, in any order.
- **A release** ships what's on `main`. It bumps the version (minor for anything
  new to people, patch for fixes only), adds a `CHANGELOG.md` section, and is tagged
  `vX.Y.Z` and pushed. The agent does this with its `release` skill
  (`.claude/skills/release/`), and a person approves it.
- **Prod** installs the tag, never the tip of `main`. `deploy/deploy_release vX.Y.Z`,
  copied to the server's `~/.local/bin`, installs the tag, restarts the systemd
  unit, and waits until `/healthz` reports the new version. If it doesn't come up,
  it reinstalls the previous build. It prints every step and logs each run to
  `~/.biscuits/deploys.log`.

**Which build is running:** the sidebar and `/healthz` show the release version. A
server installed from a checkout that's past the last release shows how far it is,
e.g. `dev @ 0.5.0+3 (abc1234)`: three commits after 0.5.0, at commit abc1234, with
`modified` if there were uncommitted changes. That comes from a build hook,
`hatch_build.py`, which records `git describe` in the package.

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
| `build.py` | Which build is running: the release version, or how far past it |
| `mcp_http.py`, `mcp_tools.py` | The MCP endpoint at `/mcp`: token and address checks, and the tools, on `client.py` and `writes.py` |
| `mcp_schema.py` | The tools' names and arguments, shared by both MCP servers |
| `mcp_proxy.py` | `biscuits mcp`, the stdio client for the endpoint. Standard library only |
| `mcp_server.py` | `biscuits mcp-direct`, break glass. Standalone: standard library only, no config, and its own writes outside the gate |
| `tokens.py` | API tokens for MCP clients (hashes only) |
| `snapshot.py` | Per-account snapshot caches, with Asana-section detection |
| `db.py`, `migrations/` | biscuits' own SQLite database and its Alembic migrations |
| `archive.py` | Archived projects (a biscuits flag; OpenProject isn't touched) |
| `sections.py` | The order of each project's sections (stored in biscuits; OpenProject isn't touched) |
| `static/`, `templates/` | The single-page UI |
