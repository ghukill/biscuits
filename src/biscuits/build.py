"""Which build this is: the release version, or how far past it.

A release install (from a `vX.Y.Z` tag) shows just the version. An install from a
checkout that's ahead of the last release shows e.g. "0.5.0+3 (abc1234)": three
commits past 0.5.0, at commit abc1234. "modified" means the checkout had changes
that weren't committed. The version itself only changes in a release.
"""

import re
from functools import cache
from importlib import resources

from . import __version__

_DESCRIBE = re.compile(r"^v.+-(\d+)-g([0-9a-f]+)(-dirty)?$")


def label(described: str | None, version: str = __version__) -> str:
    """Turn `git describe --long --dirty` output into the version to show."""
    m = _DESCRIBE.match((described or "").strip())
    if not m:
        return version
    ahead, commit, dirty = int(m[1]), m[2][:7], bool(m[3])
    if not ahead and not dirty:
        return version
    plus = f"+{ahead}" if ahead else ""
    return f"{version}{plus} ({commit}{', modified' if dirty else ''})"


@cache
def version_label() -> str:
    try:
        described = (resources.files("biscuits") / "_build.txt").read_text()
    except (OSError, FileNotFoundError):
        described = None
    return label(described)
