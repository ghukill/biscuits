from biscuits import build
from biscuits.app import create_app


def test_a_release_shows_just_its_version():
    assert build.label("v0.5.0-0-gabc1234", "0.5.0") == "0.5.0"
    assert build.label(None, "0.5.0") == "0.5.0"
    assert build.label("", "0.5.0") == "0.5.0"
    assert build.label("not git output", "0.5.0") == "0.5.0"


def test_a_checkout_past_the_release_says_how_far_and_at_which_commit():
    assert build.label("v0.5.0-3-gabc1234def\n", "0.5.0") == "0.5.0+3 (abc1234)"
    assert build.label("v0.5.0-3-gabc1234-dirty", "0.5.0") == "0.5.0+3 (abc1234, modified)"
    assert build.label("v0.5.0-0-gabc1234-dirty", "0.5.0") == "0.5.0 (abc1234, modified)"


def test_healthz_gives_the_release_version_and_the_build(write_config):
    from biscuits import __version__
    write_config()
    got = create_app().test_client().get("/healthz").json
    assert got["version"] == __version__
    assert got["build"].startswith(__version__)


def test_the_build_hook_records_git_describe(tmp_path):
    import subprocess
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1]))
    try:
        import hatch_build
    finally:
        sys.path.pop(0)
    git = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    assert hatch_build.describe(str(tmp_path)) is None  # not a repo
    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a")
    assert hatch_build.describe(str(tmp_path)) is None  # no release tag yet
    git("tag", "v1.2.3")
    assert hatch_build.describe(str(tmp_path)).startswith("v1.2.3-0-g")
