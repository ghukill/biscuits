"""The `biscuits` command line."""

import json
import os
import sys
from collections import Counter
from pathlib import Path

import click

from . import __version__, client, config, daemon, snapshot, writes


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
        except (config.ConfigError, daemon.DaemonError, client.OPError) as e:
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

    The API key is read from $OPENPROJECT_API_KEY or a hidden prompt, never from
    a command-line flag, where it would show up in `ps` and shell history. New
    configs start in preview mode unless --from-env says otherwise.
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
    key = key or click.prompt("OpenProject API key", hide_input=True)
    config.write_new(path, config.render(url, key, mode, projects, host, port, base_path))
    cfg = _load()  # catches anything invalid straight away
    click.echo(f"Wrote {path}")
    click.echo(f"Writes: {_writes_summary(cfg)}")
    click.echo("Next: `biscuits doctor`, then `biscuits serve`.")


@config_group.command("path")
def config_path():
    """Print the path of the config file in use."""
    click.echo(config.config_path())


@config_group.command("show")
def config_show():
    """Print the effective settings, with the API key masked."""
    cfg = _load()
    click.echo(f"config file:  {cfg.path}")
    click.echo(f"openproject:  {cfg.op_url}")
    click.echo(f"api key:      {'*' * 8}{cfg.op_api_key[-4:] if len(cfg.op_api_key) > 12 else ''}")
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
    if not config.is_loopback(cfg.host):
        report(None, f"listening on {cfg.host}, and biscuits has no login")

    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        writable = os.access(cfg.data_dir, os.W_OK)
    except OSError:
        writable = False
    report(writable, f"data dir is writable: {cfg.data_dir}")

    try:
        me = client.get("/users/me")
        report(True, f"OpenProject at {cfg.op_url} accepts the key, as {me.get('name')}")
        if me.get("admin"):
            report(None, "that account is an OpenProject admin")
        total = client.get("/work_packages", {"filters": "[]", "pageSize": 1}).get("total")
        report(True, f"{total} work packages visible")
    except client.OPError as e:
        report(False, f"OpenProject at {cfg.op_url}: {e}")
    sys.exit(1 if failed else 0)


# ---------- admin ----------

@main.group("snapshot")
def snapshot_group():
    """Inspect or refresh the cached copy of OpenProject data."""


@snapshot_group.command("info")
def snapshot_info():
    """Show what's in the snapshot cache file."""
    import time

    path = _load().snapshot_file
    try:
        with open(path) as f:
            blob = json.load(f)
    except OSError:
        raise click.ClickException(f"No snapshot at {path} yet") from None
    except ValueError:
        raise click.ClickException(f"{path} is damaged. `biscuits snapshot refresh` replaces it.") from None
    data = blob["data"]
    age = int(time.time() - blob["fetched_at"])
    click.echo(f"file:           {path}")
    click.echo(f"age:            {age}s")
    click.echo(f"work packages:  {len(data['work_packages'])}")
    click.echo(f"projects:       {len(data['projects'])}")
    click.echo(f"users:          {len(data['users'])}")
    click.echo(f"fetched as:     {data.get('me')}")


@snapshot_group.command("refresh")
def snapshot_refresh():
    """Pull a fresh snapshot (GETs only) and write the cache file.

    A running server keeps its copy in memory and refreshes on its own schedule;
    this is mainly for warming the cache before a first start.
    """
    click.echo("Fetching from OpenProject…")
    snapshot.refresh()
    data, _, _ = snapshot.get()
    click.echo(f"Done: {len(data['work_packages'])} work packages in {data['fetch_seconds']}s")


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
        click.echo(f"{e.get('ts')}  {e.get('op'):<8} {state:<9}{wp}  {e.get('method')} {e.get('path')}")
        if e.get("error"):
            click.echo(f"    {e['error']}")
