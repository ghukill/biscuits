"""Build hook: record where in git history this build came from.

Writes `git describe` (e.g. "v0.5.0-3-gabc1234-dirty") into the wheel as
`biscuits/_build.txt`, so a server installed from a checkout can show how far it
is past the last release. A build with no git, or no release tag in reach, gets no
file and shows the plain version. See `biscuits.build`.
"""

import subprocess
import tempfile
from pathlib import Path

try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ImportError:  # imported outside a build, e.g. by the tests
    BuildHookInterface = object


def describe(root: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--long", "--dirty", "--match", "v[0-9]*"],
            cwd=root, capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        found = describe(self.root)
        if found is None:
            return
        path = Path(tempfile.mkdtemp()) / "_build.txt"
        path.write_text(found + "\n")
        build_data["force_include"][str(path)] = "biscuits/_build.txt"
