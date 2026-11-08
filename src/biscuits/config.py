"""Configuration, read from a TOML file (default ~/.biscuits/config.toml).

The file is re-read when it changes, so repointing at another OpenProject or
pulling back the write gate takes effect on the next request, no restart
needed. A file that goes bad raises ConfigError rather than falling back to the
last good copy: if someone was trying to turn writes off, keeping the old
settings would be the wrong way to fail.
"""

import json
import os
import re
import secrets
import stat
import threading
import tomllib
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

DEFAULT_PATH = Path("~/.biscuits/config.toml")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9173
_ENV_NAME = re.compile(r"[A-Za-z0-9_-]{1,20}")


class ConfigError(RuntimeError):
    """The config file is missing or invalid."""


@dataclass(frozen=True)
class AgentConfig:
    """The account `biscuits agent` signs in as."""

    token: str
    project: int
    workdir: Path | None
    docs: Path | None


@dataclass(frozen=True)
class Config:
    path: Path
    op_url: str
    op_admin_token: str
    op_container: str
    secret_key: str | None
    encryption_key: str | None
    trusted_proxy: str | None
    environment: str | None
    writes_mode: str
    write_projects: frozenset[int]
    write_all_projects: bool
    host: str
    port: int
    base_path: str
    data_dir: Path
    agent: AgentConfig | None = None

    @property
    def db_file(self) -> Path:
        return self.data_dir / "biscuits.db"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"

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
    auth = _table(raw, "auth")

    url = op.get("url")
    # `api_key` is the old name, from before per-user login.
    key = op.get("admin_token", op.get("api_key"))
    if not (isinstance(url, str) and url.strip()):
        raise ConfigError(f"{path}: [openproject] url is required")
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"{path}: [openproject] url must start with http:// or https://")
    if not (isinstance(key, str) and key.strip()):
        raise ConfigError(f"{path}: [openproject] admin_token is required")
    container = op.get("container", "openproject")
    if not (isinstance(container, str) and container):
        raise ConfigError(f"{path}: [openproject] container must be a string")

    secret_key, encryption_key = auth.get("secret_key"), auth.get("encryption_key")
    if secret_key is not None and not (isinstance(secret_key, str) and len(secret_key) >= 32):
        raise ConfigError(f"{path}: [auth] secret_key must be a string of at least 32 characters")
    if encryption_key is not None:
        try:
            Fernet(encryption_key)
        except (ValueError, TypeError) as e:
            raise ConfigError(f"{path}: [auth] encryption_key isn't a valid key") from e

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

    trusted_proxy = server.get("trusted_proxy")
    if trusted_proxy is not None and not (isinstance(trusted_proxy, str) and trusted_proxy):
        raise ConfigError(f"{path}: [server] trusted_proxy must be an address")

    environment = server.get("environment")
    if environment is not None and not (
        isinstance(environment, str) and _ENV_NAME.fullmatch(environment)
    ):
        raise ConfigError(
            f'{path}: [server] environment must be a short name like "dev" or "prod" '
            "(letters, digits, - and _, up to 20)")

    data_dir = storage.get("data_dir", str(path.parent))
    if not isinstance(data_dir, str):
        raise ConfigError(f"{path}: [storage] data_dir must be a string")

    return Config(
        path=path,
        op_url=url.strip().rstrip("/"),
        op_admin_token=key.strip(),
        op_container=container,
        secret_key=secret_key,
        encryption_key=encryption_key,
        trusted_proxy=trusted_proxy,
        environment=environment,
        writes_mode=mode,
        write_projects=frozenset(projects),
        write_all_projects=everything,
        host=host,
        port=port,
        base_path=base_path.rstrip("/"),
        # A relative data_dir means relative to the config file.
        data_dir=path.parent / Path(data_dir).expanduser(),
        agent=_parse_agent(raw, path),
    )


def _parse_agent(raw: dict, path: Path) -> AgentConfig | None:
    agent = _table(raw, "agent")
    if not agent:
        return None
    token, project = agent.get("token"), agent.get("project")
    # An `mcp_server` setting in older configs is ignored: `biscuits mcp` replaced it.
    workdir, docs = agent.get("workdir"), agent.get("docs")
    if not (isinstance(token, str) and token.strip()):
        raise ConfigError(f"{path}: [agent] token is required")
    if not isinstance(project, int) or isinstance(project, bool):
        raise ConfigError(f"{path}: [agent] project must be a project id")
    if workdir is not None and not (isinstance(workdir, str) and workdir):
        raise ConfigError(f"{path}: [agent] workdir must be a path")
    if docs is not None and not (isinstance(docs, str) and docs):
        raise ConfigError(f"{path}: [agent] docs must be a path")
    return AgentConfig(
        token=token.strip(),
        project=project,
        workdir=Path(workdir).expanduser() if workdir else None,
        docs=Path(docs).expanduser() if docs else None,
    )


def _table(raw: dict, name: str) -> dict:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def permission_problem(path: Path) -> str | None:
    """A warning if the config, which holds an API key, is readable by others."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None
    if mode & 0o077:
        return f"{path} is readable by other users (mode {mode:o}). Fix with: chmod 600 {path}"
    return None


def auth_section() -> str:
    """A new [auth] table, with fresh secrets."""
    return f"""\
[auth]
# Signs login sessions. Changing it signs everyone out.
secret_key = {json.dumps(secrets.token_urlsafe(48))}
# Encrypts users' OpenProject API keys in the database. If this is lost, every
# key has to be entered again.
encryption_key = {json.dumps(Fernet.generate_key().decode())}
"""


def render(url, admin_token, mode="preview", projects=(), host=DEFAULT_HOST, port=DEFAULT_PORT,
           base_path="/") -> str:
    """Text of a new config file. JSON string syntax is valid TOML basic-string syntax."""
    projects_toml = '"*"' if projects == "*" else "[" + ", ".join(str(p) for p in projects) + "]"
    return f"""\
# biscuits configuration. It holds secrets: keep it mode 600.
# Changes apply on the next request, except [server], [storage] and [auth], which
# need `biscuits restart`.

[openproject]
url = {json.dumps(url)}
# Used only for managing accounts and looking up names, never to read or change
# tasks. Ideally a dedicated OpenProject admin account, not a person's key.
admin_token = {json.dumps(admin_token)}
# The Docker container OpenProject runs in, for the key command on the admin screen.
container = "openproject"

[writes]
# "preview": nothing is sent to OpenProject; edits apply in the browser only.
# "live":    edits are sent for the projects below.
mode = {json.dumps(mode)}
# OpenProject project ids that may be written in live mode, or "*" for all.
projects = {projects_toml}

[server]
host = {json.dumps(host)}
port = {port}
base_path = {json.dumps(base_path)}
# Behind a reverse proxy, the proxy's address. Only then is X-Forwarded-Proto
# believed, which is how biscuits knows the connection is HTTPS.
# trusted_proxy = "127.0.0.1"
# A name for this server, shown with the version in the sidebar, e.g. "dev @ 0.5.0".
# Applies without a restart.
# environment = "prod"

{auth_section()}"""


def add_auth_section(path: Path) -> bool:
    """Add [auth] secrets to an existing config. False if it already has them."""
    with open(path, "rb") as f:
        if "auth" in tomllib.load(f):
            return False
    with open(path, "a") as f:
        f.write("\n" + auth_section())
    return True


def write_new(path: Path, text: str) -> None:
    """Create the config file, private from its first byte. Won't overwrite."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
