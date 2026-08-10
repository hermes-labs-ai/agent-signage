"""Shared fixtures, and the import shim that makes the package importable.

The git helpers and the repository fixtures live here rather than in one test
module because three modules now need them. `conftest` is the one place pytest
makes visible to every test without an import, so sharing through it avoids
cross-importing fixtures between test files -- which works, but shadows the
imported name with the parameter name in every test that uses it.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from agent_signage import gitfacts, more_signs  # noqa: E402


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo)] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def commit(repo, name, content="x"):
    (repo / name).write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "-m", "add %s" % name)


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Never touch the developer's real stamp directory."""
    d = tmp_path / "state"
    d.mkdir()
    monkeypatch.setenv("AGENT_SIGNAGE_STATE_DIR", str(d))
    # Tests that call signs directly bypass hook.run(), which is what normally
    # resets the per-evaluation memoisation.
    gitfacts.clear_caches()
    more_signs.clear_caches()
    monkeypatch.delenv("AGENT_SIGNAGE_IGNORE", raising=False)
    monkeypatch.delenv("AGENT_SIGNAGE_SESSION_START", raising=False)
    monkeypatch.delenv("AGENT_SIGNAGE_NO_FETCH", raising=False)
    return d


@pytest.fixture
def repo(tmp_path):
    """A plain repository with one commit and no remote."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t.t")
    git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed")
    git(r, "add", "seed.txt")
    git(r, "commit", "-m", "seed")
    return r


@pytest.fixture
def behind_repo(tmp_path):
    """A clone that is genuinely 3 commits behind its origin, freshly fetched."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.email", "t@t.t")
    git(origin, "config", "user.name", "t")
    commit(origin, "a.txt")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(clone)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    git(clone, "config", "user.email", "t@t.t")
    git(clone, "config", "user.name", "t")

    for n in ("b.txt", "c.txt", "d.txt"):
        commit(origin, n)

    git(clone, "fetch", "-q")
    return clone
