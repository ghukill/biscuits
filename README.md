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
| `biscuits.log`, `biscuits.pid` | Background server log and lock |

## Writes

Edits only reach OpenProject when `[writes] mode = "live"` **and** every project
they touch is in `[writes] projects` (a list of ids, or `"*"` for all). Otherwise
they apply in the browser as a preview and vanish on reload. New configs start
in preview. Every attempt is logged, and there is no delete operation in the code.

```sh
biscuits writes status    # is the gate open? what's been sent?
biscuits writes log -n 50
```

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
| `writes.py` | **The only code that sends non-GET requests**, behind the write gate |
| `snapshot.py` | Per-account snapshot caches, with Asana-section detection |
| `db.py`, `migrations/` | biscuits' own SQLite database and its Alembic migrations |
| `archive.py` | Archived projects (a biscuits flag; OpenProject isn't touched) |
| `static/`, `templates/` | The single-page UI |
