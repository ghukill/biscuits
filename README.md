# biscuits

Biscuits: an Asana-flavoured web UI for [OpenProject](https://www.openproject.org/).

A small Flask app, with plain ES modules and no front-end build. It keeps a
snapshot of your OpenProject data and does its tree-building, filtering and
search locally. The browser never sees the API key: every OpenProject call goes
through the server.

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
biscuits doctor          # checks the config and the OpenProject connection (read-only)

biscuits serve           # in the foreground; Ctrl-C to stop
biscuits serve -d        # in the background
biscuits status
biscuits logs -f
biscuits restart
biscuits stop
```

Then open <http://127.0.0.1:9173/>. See [`config.example.toml`](config.example.toml)
for every setting.

## ⚠️ There is no login yet

Anyone who can reach the server acts with the configured API key, and
OpenProject records everything as that key's owner. So biscuits listens on
localhost by default, and logs a warning if you bind it to anything else. Only
expose it on a network you trust, such as a tailnet, until per-user login exists.

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
| `config.toml` | Settings, including the API key |
| `snapshot.json` | Cached OpenProject data, so restarts show data immediately |
| `write-log.jsonl` | Audit log of every edit, sent or not |
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
biscuits config show        # effective settings, API key masked
biscuits config path
biscuits snapshot info
biscuits snapshot refresh   # warm the cache before a first start
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

`config init` can read an env file with `OPENPROJECT_URL`, `OPENPROJECT_API_KEY`,
`BISCUITS_WRITES` and `BISCUITS_WRITE_PROJECTS`, **including its write settings**:

```sh
biscuits config init --from-env path/to/openproject.env --host 0.0.0.0
cp path/to/write-log.jsonl path/to/snapshot.json ~/.biscuits/   # keep the audit history and cache
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
| `client.py` | **GET-only** OpenProject client |
| `writes.py` | **The only code that sends non-GET requests**, behind the write gate |
| `snapshot.py` | Snapshot cache, with Asana-section detection |
| `static/`, `templates/` | The single-page UI |
