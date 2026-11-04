"""Run the server, in the foreground or as a background daemon.

The pid file carries an flock for as long as the server lives. "Is it running?"
means "is the lock held?", which can't be fooled by a stale file or a recycled
pid, and the kernel releases the lock however the process dies.
"""

import fcntl
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config

log = logging.getLogger("biscuits")

START_TIMEOUT = 15
STOP_TIMEOUT = 15


class DaemonError(RuntimeError):
    pass


def running_pid(cfg) -> int | None:
    """The pid of the running server, or None."""
    try:
        fd = os.open(cfg.pid_file, os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            text = os.read(fd, 32).decode().strip()
            return int(text) if text.isdigit() else -1  # locked, pid not written yet
        return None
    finally:
        os.close(fd)  # also drops our probe lock


def _acquire_pid_lock(cfg) -> int:
    cfg.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(cfg.pid_file, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        pid = os.read(fd, 32).decode().strip()
        os.close(fd)
        raise DaemonError(f"biscuits is already running (pid {pid or 'unknown'})") from None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    return fd  # stays open for the life of the server


def serve_foreground(host=None, port=None, threads=8) -> None:
    from waitress import serve

    from .app import create_app

    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = config.load()
    host = host or cfg.host
    port = port or cfg.port
    pid_fd = _acquire_pid_lock(cfg)

    # waitress treats SystemExit as "finish active requests, then return".
    def stop(signum, _frame):
        log.info("Received %s, shutting down", signal.Signals(signum).name)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)

    if problem := config.permission_problem(cfg.path):
        log.warning(problem)
    log.info(
        "biscuits starting on http://%s:%s%s/ (writes: %s; config: %s)",
        host, port, cfg.base_path, cfg.writes_mode, cfg.path,
    )
    app = create_app()
    # waitress only believes X-Forwarded-* from the configured proxy, and strips
    # those headers from anyone else, so they can't be spoofed.
    proxy = {"trusted_proxy": cfg.trusted_proxy, "trusted_proxy_count": 1,
             "trusted_proxy_headers": "x-forwarded-proto x-forwarded-for"} if cfg.trusted_proxy else {}
    try:
        serve(app, host=host, port=port, threads=threads, ident="biscuits", **proxy)
    except OSError as e:
        raise DaemonError(f"Can't listen on {host}:{port}: {e.strerror}") from e
    finally:
        os.ftruncate(pid_fd, 0)
    log.info("biscuits stopped")
    logging.shutdown()
    # Don't let interpreter exit wait on a snapshot fetch still in progress. The
    # snapshot caches and write log are safe: active requests have finished.
    os._exit(0)


def start_background(host=None, port=None) -> int:
    """Launch a detached server, wait until it answers, and return its pid."""
    cfg = config.load()
    if (pid := running_pid(cfg)) is not None:
        raise DaemonError(f"biscuits is already running (pid {pid})")
    host, port = host or cfg.host, port or cfg.port
    cfg.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    cmd = [sys.executable, "-m", "biscuits", "--config", str(cfg.path),
           "serve", "--host", host, "--port", str(port)]
    log_fd = os.open(cfg.log_file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=log_fd, stderr=log_fd,
            start_new_session=True, cwd="/",
        )
    finally:
        os.close(log_fd)

    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise DaemonError(
                f"biscuits exited during startup (code {proc.returncode}). See {cfg.log_file}"
            )
        if health(host, port):
            return proc.pid
        time.sleep(0.2)
    proc.terminate()
    raise DaemonError(f"biscuits didn't answer within {START_TIMEOUT}s. See {cfg.log_file}")


def stop(cfg) -> int | None:
    """Stop the running server. Returns the pid it stopped, or None if none was running."""
    pid = running_pid(cfg)
    if pid is None:
        return None
    if pid < 0:
        raise DaemonError(f"{cfg.pid_file} is locked but holds no pid; can't tell what to stop")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline:
        if running_pid(cfg) is None:
            return pid
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)
    while running_pid(cfg) is not None and time.monotonic() < deadline + 5:
        time.sleep(0.1)
    if running_pid(cfg) is not None:
        raise DaemonError(f"pid {pid} wouldn't stop")
    return pid


def health(host, port) -> bool:
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    # Skip any HTTP proxy in the environment: this is always a direct check.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{host}:{port}/healthz", timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def tail(path: Path, lines: int, follow: bool) -> None:
    try:
        f = open(path, errors="replace")
    except OSError as e:
        raise DaemonError(f"Can't read {path}: {e.strerror}") from e
    with f:
        for line in f.readlines()[-lines:]:
            sys.stdout.write(line)
        sys.stdout.flush()
        while follow:
            line = f.readline()
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                time.sleep(0.5)
