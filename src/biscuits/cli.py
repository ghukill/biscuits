"""The `biscuits` command line."""

import json
import os
import sys
from collections import Counter
from pathlib import Path

import click

from . import __version__, accounts, agent, build, client, config, daemon, opadmin, tokens, writes


def _load() -> config.Config:
    return config.load()


def _dotenv(path: str) -> dict:
    vals = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip().strip("'\"")
    except OSError as e:
        raise click.ClickException(f"Can't read {path}: {e.strerror}") from e
    return vals


class _Group(click.Group):
    """Print our own errors as one clean line rather than a traceback."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except (config.ConfigError, daemon.DaemonError, client.OPError, accounts.AccountError,
                tokens.TokenError) as e:
            raise click.ClickException(str(e)) from e


@click.group(cls=_Group)
@click.version_option(__version__, prog_name="biscuits")
@click.option("--config", "config_file", type=click.Path(dir_okay=False),
              help="Config file. [default: $BISCUITS_CONFIG, then ~/.biscuits/config.toml]")
def main(config_file):
    """biscuits: an Asana-flavoured web UI for OpenProject."""
    if config_file:
        config.set_path(config_file)


# ---------- server ----------

@main.command()
@click.option("-d", "--daemon", "background", is_flag=True, help="Run in the background.")
@click.option("--host", help="Address to listen on. [default: from config]")
@click.option("--port", type=click.IntRange(1, 65535), help="Port. [default: from config]")
def serve(background, host, port):
    """Run the web server."""
    if background:
        cfg = _load()
        pid = daemon.start_background(host, port)
        click.echo(f"biscuits started (pid {pid}) on {_url(cfg, host, port)}")
        click.echo(f"Logs: {cfg.log_file}")
    else:
        daemon.serve_foreground(host, port)


@main.command()
def stop():
    """Stop the running server."""
    pid = daemon.stop(_load())
    click.echo(f"biscuits stopped (pid {pid})" if pid else "biscuits is not running")


@main.command()
@click.option("--host", help="Address to listen on. [default: from config]")
@click.option("--port", type=click.IntRange(1, 65535), help="Port. [default: from config]")
def restart(host, port):
    """Stop the server if it's running, then start it in the background."""
    cfg = _load()
    daemon.stop(cfg)
    pid = daemon.start_background(host, port)
    click.echo(f"biscuits started (pid {pid}) on {_url(cfg, host, port)}")


@main.command()
def status():
    """Show whether the server is running. Exits 1 if it isn't."""
    cfg = _load()
    pid = daemon.running_pid(cfg)
    if pid is None:
        click.echo("biscuits is not running")
        sys.exit(1)
    click.echo(f"biscuits is running (pid {pid})")
    click.echo(f"  configured address: {_url(cfg)}")
    click.echo(f"  writes: {_writes_summary(cfg)}")
    click.echo(f"  OpenProject: {cfg.op_url}")


@main.command()
@click.option("-n", "--lines", default=50, show_default=True, help="Lines to show.")
@click.option("-f", "--follow", is_flag=True, help="Keep printing new lines.")
def logs(lines, follow):
    """Show the server log (background mode)."""
    try:
        daemon.tail(_load().log_file, lines, follow)
    except KeyboardInterrupt:
        pass


def _url(cfg, host=None, port=None):
    return f"http://{host or cfg.host}:{port or cfg.port}{cfg.base_path}/"


def _writes_summary(cfg):
    if cfg.writes_mode != "live":
        return "preview (nothing is sent to OpenProject)"
    if cfg.write_all_projects:
        return "LIVE for all projects"
    if cfg.write_projects:
        return "LIVE for projects " + ", ".join(map(str, sorted(cfg.write_projects)))
    return "live, but no projects are allowed"


# ---------- config ----------

@main.group("config")
def config_group():
    """Create and inspect the config file."""


@config_group.command("init")
@click.option("--url", help="OpenProject base URL.")
@click.option("--from-env", "env_file", type=click.Path(exists=True, dir_okay=False),
              help="Take the settings from an env file with OPENPROJECT_URL, "
                   "OPENPROJECT_API_KEY, BISCUITS_WRITES and BISCUITS_WRITE_PROJECTS.")
@click.option("--host", default=config.DEFAULT_HOST, show_default=True)
@click.option("--port", default=config.DEFAULT_PORT, show_default=True,
              type=click.IntRange(1, 65535))
@click.option("--base-path", default="/", show_default=True)
def config_init(url, env_file, host, port, base_path):
    """Create the config file (mode 600). Won't overwrite an existing one.

    The admin token is read from $OPENPROJECT_API_KEY or a hidden prompt, never
    from a command-line flag, where it would show up in `ps` and shell history. New
    configs start in preview mode unless --from-env says otherwise. Login secrets
    are generated for you.
    """
    path = config.config_path()
    if path.exists():
        raise click.ClickException(f"{path} already exists. Edit it, or remove it first.")
    mode, projects = "preview", []
    key = os.environ.get("OPENPROJECT_API_KEY")
    if env_file:
        env = _dotenv(env_file)
        url = url or env.get("OPENPROJECT_URL")
        key = env.get("OPENPROJECT_API_KEY") or key
        mode = env.get("BISCUITS_WRITES", "preview").strip().lower()
        raw = env.get("BISCUITS_WRITE_PROJECTS", "").replace(" ", "").split(",")
        projects = "*" if "*" in raw else [int(p) for p in raw if p.isdigit()]
    url = url or click.prompt("OpenProject URL")
    key = key or click.prompt("OpenProject admin token", hide_input=True)
    config.write_new(path, config.render(url, key, mode, projects, host, port, base_path))
    cfg = _load()  # catches anything invalid straight away
    click.echo(f"Wrote {path}")
    click.echo(f"Writes: {_writes_summary(cfg)}")
    click.echo("Next: `biscuits users add NAME --admin`, `biscuits doctor`, then `biscuits serve`.")


@config_group.command("add-auth-keys")
def config_add_auth_keys():
    """Add login secrets to a config from before login existed."""
    cfg = _load()
    if config.add_auth_section(cfg.path):
        click.echo(f"Added [auth] secrets to {cfg.path}. They apply after `biscuits restart`.")
    else:
        click.echo(f"{cfg.path} already has an [auth] section. Nothing changed.")


@config_group.command("path")
def config_path():
    """Print the path of the config file in use."""
    click.echo(config.config_path())


@config_group.command("show")
def config_show():
    """Print the effective settings, with secrets masked."""
    cfg = _load()
    click.echo(f"config file:  {cfg.path}")
    click.echo(f"environment:  {cfg.environment or 'not set'} (biscuits {build.version_label()})")
    click.echo(f"openproject:  {cfg.op_url}")
    click.echo(f"admin token:  {'*' * 8}{cfg.op_admin_token[-4:] if len(cfg.op_admin_token) > 12 else ''}")
    click.echo(f"login keys:   {'set' if cfg.secret_key and cfg.encryption_key else 'MISSING'}")
    click.echo(f"agent:        {f'project {cfg.agent.project}, token set' if cfg.agent else 'not set up'}")
    click.echo(f"writes:       {_writes_summary(cfg)}")
    click.echo(f"listen:       {_url(cfg)}")
    click.echo(f"data dir:     {cfg.data_dir}")


@main.command()
def doctor():
    """Check the config file and the connection to OpenProject (read-only)."""
    failed = False

    def report(ok, text):
        nonlocal failed
        failed = failed or ok is False
        mark = {True: "ok  ", False: "FAIL", None: "warn"}[ok]
        click.echo(f"[{mark}] {text}")

    try:
        cfg = _load()
    except config.ConfigError as e:
        report(False, str(e))
        sys.exit(1)
    report(True, f"config is valid: {cfg.path}")
    problem = config.permission_problem(cfg.path)
    report(None, problem) if problem else report(True, "config is private (no group/other access)")
    report(True, f"writes: {_writes_summary(cfg)}")

    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        writable = os.access(cfg.data_dir, os.W_OK)
    except OSError:
        writable = False
    report(writable, f"data dir is writable: {cfg.data_dir}")

    if cfg.secret_key and cfg.encryption_key:
        report(True, "login secrets are set")
    else:
        report(False, "no [auth] secrets. Add them with `biscuits config add-auth-keys`")

    users = accounts.all_users()
    active = [u for u in users if u.disabled_at is None]
    if not any(u.is_admin for u in active):
        report(False, "no admin account, so nobody can manage logins. Run `biscuits users add NAME --admin`")
    else:
        report(True, f"{len(active)} active account(s)")
    if cfg.encryption_key:
        no_key = [u.username for u in active if not accounts.get_key(u)]
        if no_key:
            report(None, "no usable OpenProject key for: " + ", ".join(no_key))

    try:
        me = opadmin.whoami()
        report(True, f"OpenProject at {cfg.op_url} accepts the admin token, as {me.get('name')}")
        if not me.get("admin"):
            report(None, "that account isn't an OpenProject admin, so account management won't work")
        if me.get("id") in {u.op_user_id for u in users}:
            report(None, "the admin token belongs to someone with a biscuits account; a dedicated account is better")
    except client.OPError as e:
        report(False, f"OpenProject at {cfg.op_url}: {e}")
    sys.exit(1 if failed else 0)


# ---------- agent ----------

@main.command("agent")
def agent_session():
    """Start a Claude Code session signed in as the agent account from [agent]."""
    agent.launch()


@main.command("mcp")
def mcp_stdio():
    """Run an MCP server over stdio that passes everything to a biscuits server.

    Needs no config file. Set $BISCUITS_MCP_URL to the server's /mcp endpoint, and
    $BISCUITS_MCP_TOKEN_FILE to a file holding a biscuits API token (see
    `biscuits tokens add`).

    For now, with no $BISCUITS_MCP_URL but OpenProject credentials in the
    environment, it runs `biscuits mcp-direct` instead and says so, so existing
    setups keep working until they're switched over.
    """
    if not os.environ.get("BISCUITS_MCP_URL") and any(
            os.environ.get(v) for v in ("OPENPROJECT_URL", "OPENPROJECT_API_KEY", "OPENPROJECT_ENV_FILE")):
        sys.stderr.write("biscuits mcp: no BISCUITS_MCP_URL, but OpenProject credentials are set, so this is "
                         "talking to OpenProject directly, outside biscuits. Switch to BISCUITS_MCP_URL, "
                         "or use `biscuits mcp-direct` to mean it.\n")
        from . import mcp_server  # reads the credentials on import, so only load it here

        return mcp_server.main()
    from . import mcp_proxy

    mcp_proxy.main()


@main.command("mcp-direct")
def mcp_direct():
    """Break glass: an MCP server over stdio that talks to OpenProject directly.

    Bypasses biscuits entirely: no [writes] gate, no write log, and the key's own
    OpenProject user as the author. Use `biscuits mcp` normally. Credentials come
    from $OPENPROJECT_URL and $OPENPROJECT_API_KEY, then the file named by
    $OPENPROJECT_ENV_FILE. Needs no config file or running web server.
    """
    from . import mcp_server  # reads the credentials on import, so only load it here

    mcp_server.main()


# ---------- API tokens ----------

@main.group("tokens")
def tokens_group():
    """API tokens, for programs that use biscuits' MCP endpoint as an account."""


@tokens_group.command("add")
@click.argument("username")
@click.option("--name", required=True, help='What the token is for, e.g. "mattermost".')
@click.option("--out", "out_file", type=click.Path(dir_okay=False, writable=True),
              help="Write the token to this file (mode 600) instead of printing it.")
def tokens_add(username, name, out_file):
    """Make a token that acts as USERNAME. It's shown (or written) only once."""
    user = _need_user(username)
    if not accounts.get_key(user):
        raise click.ClickException(f"{username} has no OpenProject key yet. Set one with `biscuits users set-key`.")
    row, token = tokens.create(user.id, name)
    if out_file:
        fd = os.open(out_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
        click.echo(f"Token #{row.id} for {user.username} written to {out_file}")
    else:
        click.echo(f"Token #{row.id} for {user.username} ({name}). It won't be shown again:")
        click.echo(token)


@tokens_group.command("list")
def tokens_list():
    """List tokens (never the tokens themselves)."""
    for row, username in tokens.all_tokens():
        used = row.last_used_at.strftime("%Y-%m-%d %H:%M") if row.last_used_at else "never used"
        state = "REVOKED" if row.revoked_at else used
        click.echo(f"#{row.id:<4} {username:<24} {row.name:<24} {state}")


@tokens_group.command("revoke")
@click.argument("token_id", type=int)
def tokens_revoke(token_id):
    """Stop a token working, at once."""
    row = tokens.revoke(token_id)
    click.echo(f"Token #{row.id} ({row.name}) is revoked.")


# ---------- admin ----------

@main.group("snapshot")
def snapshot_group():
    """Inspect or refresh the cached copy of OpenProject data."""


@snapshot_group.command("info")
def snapshot_info():
    """Show each account's cached snapshot. Snapshots are fetched when that person uses biscuits."""
    import time

    directory = _load().snapshot_dir
    files = sorted(directory.glob("*.json")) if directory.exists() else []
    if not files:
        click.echo(f"No snapshots in {directory} yet")
        return
    for path in files:
        try:
            with open(path) as f:
                blob = json.load(f)
            data = blob["data"]
            age = int(time.time() - blob["fetched_at"])
            click.echo(f"account {path.stem}: {data.get('me')}, {len(data['work_packages'])} work packages, "
                       f"{len(data['projects'])} projects, {age}s old")
        except (OSError, ValueError, KeyError):
            click.echo(f"account {path.stem}: damaged; biscuits will replace it")


@main.group("writes")
def writes_group():
    """Inspect the write gate and the write log."""


@writes_group.command("status")
def writes_status():
    """Show whether edits reach OpenProject, and a summary of the log."""
    cfg = _load()
    click.echo(f"gate: {_writes_summary(cfg)}")
    entries = writes.recent(100_000)
    counts = Counter("sent" if e.get("sent") else "preview" for e in entries)
    failed = sum(1 for e in entries if e.get("ok") is False)
    click.echo(f"log:  {cfg.write_log_file}")
    click.echo(f"      {counts['sent']} sent ({failed} failed), {counts['preview']} not sent")


@writes_group.command("log")
@click.option("-n", "--lines", default=20, show_default=True, help="Entries to show.")
@click.option("--json", "as_json", is_flag=True, help="Print raw JSON lines.")
def writes_log(lines, as_json):
    """Show recent write-log entries, newest first."""
    for e in writes.recent(lines):
        if as_json:
            click.echo(json.dumps(e))
            continue
        state = "sent" if e.get("sent") else "not sent"
        if e.get("ok") is False:
            state = "FAILED"
        wp = f" #{e['wp']}" if e.get("wp") else ""
        actor = e.get("actor") or {}
        who = actor.get("username") or actor.get("system") or "-"
        click.echo(f"{e.get('ts')}  {who:<16} {e.get('op'):<8} {state:<9}{wp}  {e.get('method')} {e.get('path')}")
        if e.get("error"):
            click.echo(f"    {e['error']}")


# ---------- accounts ----------

@main.group("users")
def users_group():
    """Manage biscuits accounts. The web screens do the same, once an admin exists."""


def _need_user(username):
    user = accounts.by_username(username)
    if user is None:
        raise click.ClickException(f"No account called {username}")
    return user


def _read_key(from_stdin):
    if from_stdin:
        return sys.stdin.readline().strip()
    return click.prompt("OpenProject API key", hide_input=True)


@users_group.command("add")
@click.argument("username")
@click.option("--admin", is_flag=True, help="Make this a biscuits admin.")
@click.option("--name", help="Display name. [default: the username]")
@click.option("--op-user", type=int, help="The OpenProject user id this account acts as.")
@click.option("--key-stdin", is_flag=True, help="Read the API key from stdin (needs --op-user).")
def users_add(username, admin, name, op_user, key_stdin):
    """Create an account and print its temporary password."""
    key = _read_key(key_stdin) if op_user is not None else None
    if key is not None:  # check first, so a bad key doesn't leave a half-made account
        name = name or accounts.validate_key(key, op_user)
    user, password = accounts.create(username, display_name=name, is_admin=admin)
    if key is not None:
        user = accounts.set_key(user.id, key, op_user)
    click.echo(f"Created {user.username}{' (admin)' if admin else ''}")
    click.echo(f"Temporary password: {password}")
    click.echo("They'll be asked to choose their own when they first sign in.")


@users_group.command("list")
def users_list():
    """List accounts."""
    for u in accounts.all_users():
        flags = [f for f, on in (("admin", u.is_admin), ("disabled", u.disabled_at), ("new password pending", u.must_change_password)) if on]
        op = f"OpenProject #{u.op_user_id} {u.op_user_name or ''}".strip() if u.op_user_id else "not linked"
        key = "key ok" if accounts.get_key(u) else "NO KEY"
        click.echo(f"{u.username:<24} {op:<36} {key:<8} {', '.join(flags)}")


@users_group.command("set-key")
@click.argument("username")
@click.option("--op-user", type=int, help="Link to this OpenProject user id.")
@click.option("--key-stdin", is_flag=True, help="Read the API key from stdin.")
def users_set_key(username, op_user, key_stdin):
    """Save an account's OpenProject API key, after checking whose it is."""
    user = accounts.set_key(_need_user(username).id, _read_key(key_stdin), op_user)
    click.echo(f"Saved. {user.username} acts as {user.op_user_name} (OpenProject #{user.op_user_id}).")


@users_group.command("reset-password")
@click.argument("username")
def users_reset_password(username):
    """Set a new temporary password, and sign the person out everywhere."""
    click.echo(f"Temporary password: {accounts.reset_password(_need_user(username).id)}")


@users_group.command("disable")
@click.argument("username")
def users_disable(username):
    """Stop an account signing in. Doesn't touch OpenProject."""
    accounts.set_disabled(_need_user(username).id, True)
    click.echo(f"{username} is disabled.")


@users_group.command("enable")
@click.argument("username")
def users_enable(username):
    """Let a disabled account sign in again."""
    accounts.set_disabled(_need_user(username).id, False)
    click.echo(f"{username} is enabled.")
