"""Configuration, read from a TOML file (default ~/.biscuits/config.toml).

The file is re-read when it changes, so repointing at another OpenProject or
pulling back the write gate takes effect on the next request, no restart
needed. A file that goes bad raises ConfigError rather than falling back to the
last good copy: if someone was trying to turn writes off, keeping the old
settings would be the wrong way to fail.
"""

import ipaddress
import json
import os
import stat
import threading
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path("~/.biscuits/config.toml")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9173


class ConfigError(RuntimeError):
    """The config file is missing or invalid."""


@dataclass(frozen=True)
class Config:
    path: Path
    op_url: str
    op_api_key: str
    writes_mode: str
    write_projects: frozenset[int]
    write_all_projects: bool
    host: str
    port: int
    base_path: str
    data_dir: Path

    @property
    def snapshot_file(self) -> Path:
        return self.data_dir / "snapshot.json"

    @property
    def write_log_file(self) -> Path:
        return self.data_dir / "write-log.jsonl"

    @property
    def pid_file(self) -> Path:
        return self.data_dir / "biscuits.pid"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "biscuits.log"


_lock = threading.Lock()
_override: Path | None = None
_cache: tuple[tuple, Config] | None = None


def set_path(path) -> None:
    """Use this config file instead of the default (the CLI's --config)."""
    global _override, _cache
    with _lock:
        _override = Path(path).expanduser().absolute() if path else None
        _cache = None


def config_path() -> Path:
    if _override:
        return _override
    env = os.environ.get("BISCUITS_CONFIG")
    return Path(env or DEFAULT_PATH).expanduser().absolute()


def load() -> Config:
    """The current config. Cheap: only re-parses when the file has changed."""
    global _cache
    path = config_path()
    try:
        st = path.stat()
    except OSError as e:
        raise ConfigError(
            f"No config file at {path} ({e.strerror}). Create one with `biscuits config init`."
        ) from e
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _lock:
        if _cache and _cache[0] == key:
            return _cache[1]
        cfg = _parse(path)
        _cache = (key, cfg)
        return cfg


def _parse(path: Path) -> Config:
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"Can't read {path}: {e.strerror}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from e

    op = _table(raw, "openproject")
    writes = _table(raw, "writes")
    server = _table(raw, "server")
    storage = _table(raw, "storage")

    url = op.get("url")
    key = op.get("api_key")
    if not (isinstance(url, str) and url.strip()):
        raise ConfigError(f"{path}: [openproject] url is required")
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"{path}: [openproject] url must start with http:// or https://")
    if not (isinstance(key, str) and key.strip()):
        raise ConfigError(f"{path}: [openproject] api_key is required")

    mode = writes.get("mode", "preview")
    if mode not in ("preview", "live"):
        raise ConfigError(f'{path}: [writes] mode must be "preview" or "live"')

    projects = writes.get("projects", [])
    everything = projects == "*"
    if everything:
        projects = []
    elif not isinstance(projects, list) or not all(
        isinstance(p, int) and not isinstance(p, bool) for p in projects
    ):
        raise ConfigError(f'{path}: [writes] projects must be a list of project ids or "*"')

    port = server.get("port", DEFAULT_PORT)
    if not (isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536):
        raise ConfigError(f"{path}: [server] port must be a port number")
    host = server.get("host", DEFAULT_HOST)
    if not (isinstance(host, str) and host):
        raise ConfigError(f"{path}: [server] host must be a string")
    base_path = server.get("base_path", "/")
    if not (isinstance(base_path, str) and base_path.startswith("/")):
        raise ConfigError(f'{path}: [server] base_path must start with "/"')

    data_dir = storage.get("data_dir", str(path.parent))
    if not isinstance(data_dir, str):
        raise ConfigError(f"{path}: [storage] data_dir must be a string")

    return Config(
        path=path,
        op_url=url.strip().rstrip("/"),
        op_api_key=key.strip(),
        writes_mode=mode,
        write_projects=frozenset(projects),
        write_all_projects=everything,
        host=host,
        port=port,
        base_path=base_path.rstrip("/"),
        # A relative data_dir means relative to the config file.
        data_dir=path.parent / Path(data_dir).expanduser(),
    )


def _table(raw: dict, name: str) -> dict:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def permission_problem(path: Path) -> str | None:
    """A warning if the config, which holds an API key, is readable by others."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None
    if mode & 0o077:
        return f"{path} is readable by other users (mode {mode:o}). Fix with: chmod 600 {path}"
    return None


def render(url, api_key, mode="preview", projects=(), host=DEFAULT_HOST, port=DEFAULT_PORT,
           base_path="/") -> str:
    """Text of a new config file. JSON string syntax is valid TOML basic-string syntax."""
    projects_toml = '"*"' if projects == "*" else "[" + ", ".join(str(p) for p in projects) + "]"
    return f"""\
# biscuits configuration. It holds an API key: keep it mode 600.
# Changes apply on the next request, except [server] and [storage], which need
# `biscuits restart`.

[openproject]
url = {json.dumps(url)}
api_key = {json.dumps(api_key)}

[writes]
# "preview": nothing is sent to OpenProject; edits apply in the browser only.
# "live":    edits are sent for the projects below.
mode = {json.dumps(mode)}
# OpenProject project ids that may be written in live mode, or "*" for all.
projects = {projects_toml}

[server]
# biscuits has no login yet: anyone who can reach this address acts with the
# API key above. Think before binding to anything but localhost.
host = {json.dumps(host)}
port = {port}
base_path = {json.dumps(base_path)}
"""


def write_new(path: Path, text: str) -> None:
    """Create the config file, private from its first byte. Won't overwrite."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
