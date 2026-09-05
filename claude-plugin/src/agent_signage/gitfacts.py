"""Git facts, measured. Nothing in this module infers or guesses.

Every function returns something git actually reported, or None. A None means
"not known", never "known to be fine" -- callers must not treat absence of a
fact as evidence of its opposite. That distinction is the whole soundness
argument: agent-signage speaks only from measurements, so when it speaks it
cannot be wrong about the fact it states.
"""

from __future__ import annotations

import functools
import os
import subprocess
import time
from typing import NamedTuple, Optional

# Hard ceiling on any single git call. A hook that hangs is worse than a hook
# that says nothing, so every subprocess is bounded and every timeout is a
# silent None.
GIT_TIMEOUT_S = 2.0

# Six signs run per invocation and ask overlapping questions about the same
# repository. Without memoisation each one re-spawns git, which measured at
# ~119ms for a single edit inside a repo.
#
# The cache is correct only *within* one evaluation. Repository state changes
# between tool calls, so anything embedding this library in a long-lived
# process must reset it each time -- `hook.run()` does. The caches are listed
# explicitly rather than discovered, so adding a memoised function without
# registering it here fails the test that asserts they are all reset.


def _git(root: Optional[str], *args: str, timeout: float = GIT_TIMEOUT_S) -> Optional[str]:
    """Run git, return stripped stdout, or None on any failure whatsoever."""
    cmd = ["git"]
    if root:
        cmd += ["-C", root]
    cmd += list(args)
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    try:
        return p.stdout.decode("utf-8", "replace").strip()
    except Exception:  # pragma: no cover - decode of bytes basically cannot raise here
        return None


@functools.lru_cache(maxsize=256)
def _has_git_ancestor(start: str) -> bool:
    """Cheap filesystem check for a `.git` entry at or above `start`.

    Spawning git costs ~10ms; walking parent directories costs microseconds.
    Since most paths an agent touches are either not in a repo at all or are
    resolved many times over a session, this short-circuit keeps the silent
    path close to bare interpreter startup. A `.git` *file* counts as well as a
    directory -- that is what worktrees and submodules use.
    """
    d = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


@functools.lru_cache(maxsize=64)
def repo_root(path: str) -> Optional[str]:
    """Toplevel of the repo containing `path`, or None if it is not in one."""
    d = path if os.path.isdir(path) else os.path.dirname(path)
    if not d or not os.path.isdir(d):
        return None
    if not _has_git_ancestor(d):
        return None
    # Still ask git for the answer: only git resolves worktrees, submodules and
    # GIT_DIR overrides correctly. The walk above is a filter, not a substitute.
    return _git(d, "rev-parse", "--show-toplevel")


@functools.lru_cache(maxsize=64)
def git_dir(root: str) -> Optional[str]:
    """Absolute .git directory for `root`. Worktrees do not have a .git dir at
    the toplevel -- they have a .git *file* pointing elsewhere -- so this must
    ask git rather than assume a path."""
    d = _git(root, "rev-parse", "--absolute-git-dir")
    return d if d and os.path.isdir(d) else None


@functools.lru_cache(maxsize=64)
def current_branch(root: str) -> Optional[str]:
    """Branch name, or None when HEAD is detached."""
    b = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    return b or None


@functools.lru_cache(maxsize=64)
def upstream_ref(root: str) -> Optional[str]:
    """Configured upstream for HEAD, e.g. 'origin/main'. None if unset."""
    return _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")


@functools.lru_cache(maxsize=64)
def upstream_sha(root: str, upstream: str) -> Optional[str]:
    return _git(root, "rev-parse", upstream)


@functools.lru_cache(maxsize=64)
def commits_behind(root: str, upstream: str) -> Optional[int]:
    """How many commits HEAD is behind `upstream`, per the last fetch.

    This never contacts the network. It reports what the remote-tracking ref
    already knows, so it can *undercount* when the last fetch is old -- it
    cannot invent drift that does not exist. Undercounting is a silent miss;
    overcounting would be a false alarm. The asymmetry is deliberate.
    """
    out = _git(root, "rev-list", "--count", "HEAD..%s" % upstream)
    if out is None:
        return None
    try:
        return int(out)
    except ValueError:
        return None


@functools.lru_cache(maxsize=64)
def commits_ahead(root: str, upstream: str) -> Optional[int]:
    """How many commits HEAD is ahead of `upstream`, per the last fetch.

    Only asked on the path where a sign is already speaking. It changes what
    the reader should do about it -- a tree that is purely behind fast-forwards,
    a tree that is behind *and* ahead has diverged and needs a rebase or merge
    decision -- and across this project's own survey of 83 local repositories
    (49 with an upstream), 13 of the 16 that were behind were also ahead. The
    diverged case is the common one, and it was the one being described least
    usefully.
    """
    out = _git(root, "rev-list", "--count", "%s..HEAD" % upstream)
    if out is None:
        return None
    try:
        return int(out)
    except ValueError:
        return None


@functools.lru_cache(maxsize=64)
def upstream_commit_age(root: str, upstream: str) -> Optional[str]:
    """Human-readable age of the upstream tip, e.g. '3 hours ago'."""
    return _git(root, "log", "-1", "--format=%cr", upstream)


def fetch_age_seconds(gitdir: str) -> Optional[float]:
    """Seconds since this repo last fetched, or None if it never has.

    FETCH_HEAD's mtime is the honest record of when remote knowledge was last
    refreshed. If it is missing, the repo has never fetched in this clone and
    every remote-tracking ref is whatever `git clone` set.
    """
    fh = os.path.join(gitdir, "FETCH_HEAD")
    try:
        return max(0.0, time.time() - os.path.getmtime(fh))
    except OSError:
        return None


class InProgress(NamedTuple):
    """Operations that mean the user deliberately put the tree in an odd state."""

    bisecting: bool
    rebasing: bool
    merging: bool
    cherry_picking: bool

    @property
    def any(self) -> bool:
        return self.bisecting or self.rebasing or self.merging or self.cherry_picking

    @property
    def name(self) -> Optional[str]:
        if self.bisecting:
            return "bisect"
        if self.rebasing:
            return "rebase"
        if self.merging:
            return "merge"
        if self.cherry_picking:
            return "cherry-pick"
        return None


def in_progress(gitdir: str) -> InProgress:
    """Detect an in-flight git operation from marker files git itself writes."""
    j = os.path.join

    def has(*names: str) -> bool:
        return any(os.path.exists(j(gitdir, n)) for n in names)

    return InProgress(
        bisecting=has("BISECT_LOG"),
        rebasing=has("rebase-merge", "rebase-apply"),
        merging=has("MERGE_HEAD"),
        cherry_picking=has("CHERRY_PICK_HEAD"),
    )


def spawn_background_fetch(root: str) -> bool:
    """Start a detached `git fetch` and return immediately.

    Freshness is the binding constraint on coverage: the hook can only report
    drift the last fetch already knew about. Fetching inline would put the
    network on the hot path of every file read, which is unacceptable. So when
    knowledge is stale we refresh it out-of-band and stay silent this turn --
    the next invocation has fresh data. The mechanism is self-healing and costs
    the caller nothing.

    Returns True if the child was launched (not that it succeeded).
    """
    try:
        subprocess.Popen(
            ["git", "-C", root, "fetch", "--quiet", "--no-tags"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


_CACHED = (
    _has_git_ancestor,
    repo_root,
    git_dir,
    current_branch,
    upstream_ref,
    upstream_sha,
    commits_behind,
    commits_ahead,
    upstream_commit_age,
)


def clear_caches() -> None:
    """Drop every memoised git answer. Call once per evaluation."""
    for fn in _CACHED:
        fn.cache_clear()
