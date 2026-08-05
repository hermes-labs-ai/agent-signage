"""Sign definitions and the registry that evaluates them.

A *sign* is one true, checkable statement an agent would not otherwise know at
the moment it acts -- the road-sign model: you are about to do X, here is a
fact about X. A sign must satisfy three properties, and anything that cannot is
not a sign and does not belong here:

  Sound      -- it reports a measurement, never an inference. If it fires, the
                stated fact is true.
  Silent     -- it produces nothing at all when there is nothing to say. The
                cost of the mechanism in the common case is zero tokens.
  Actionable -- it ends in the command that resolves it, so the reader is never
                left holding a problem with no next step.

Signs never block. `evaluate()` returning nothing is the overwhelmingly common
outcome and is the design working, not failing.
"""

from __future__ import annotations

import os
import re
import time
from typing import Callable, List, NamedTuple, Optional

from . import gitfacts, state

# Paths whose staleness is not the reader's problem: dependencies, build
# output, and virtualenvs are either vendored or regenerated, so "this tree is
# behind" carries no action for someone editing inside them.
VENDORED_RE = re.compile(
    r"(^|/)(node_modules|vendor|\.venv|venv|site-packages|dist|build|"
    r"\.next|\.tox|target|__pycache__|\.git)(/|$)"
)

# How stale remote knowledge may be before a background refresh is triggered.
DEFAULT_FETCH_TTL_S = 30 * 60


class Sign(NamedTuple):
    id: str
    text: str
    # Identifies the observed situation, so an acknowledgement can be bound to
    # the state rather than to the repository.
    state_token: str
    repo: str


class Context(NamedTuple):
    session_id: str
    tool_name: str
    target_path: str
    session_started: Optional[float] = None
    fetch_ttl_s: float = DEFAULT_FETCH_TTL_S
    allow_background_fetch: bool = True


SignFn = Callable[["Context"], Optional[Sign]]
_REGISTRY: List[SignFn] = []


def register(fn: SignFn) -> SignFn:
    """Register a sign. Third parties can add signs without forking."""
    _REGISTRY.append(fn)
    return fn


def registered() -> List[str]:
    return [f.__name__ for f in _REGISTRY]


def evaluate(ctx: Context) -> List[Sign]:
    """Run every registered sign. A sign that raises is dropped, never fatal."""
    out: List[Sign] = []
    for fn in _REGISTRY:
        try:
            s = fn(ctx)
        except Exception:
            continue
        if s is not None:
            out.append(s)
    return out


def _ignored_repos() -> List[str]:
    raw = os.environ.get("AGENT_SIGNAGE_IGNORE", "")
    return [os.path.abspath(os.path.expanduser(p)) for p in raw.split(os.pathsep) if p.strip()]


# --------------------------------------------------------------------- signs

@register
def stale_checkout(ctx: Context) -> Optional[Sign]:
    """The working copy is behind its upstream, so it may not be what is live.

    Origin: 2026-08-05. A consistency pass was run against a checkout 26 commits
    behind the deployed branch; the stale tree described routes replaced months
    earlier. It was caught incidentally, not by design.
    """
    if VENDORED_RE.search(ctx.target_path):
        return None

    root = gitfacts.repo_root(ctx.target_path)
    if root is None:
        return None
    if os.path.abspath(root) in _ignored_repos():
        return None

    gitdir = gitfacts.git_dir(root)
    if gitdir is None:
        return None

    # A deliberately odd tree is not a stale tree. Someone mid-bisect is behind
    # on purpose and telling them so is noise.
    if gitfacts.in_progress(gitdir).any:
        return None
    if gitfacts.current_branch(root) is None:
        return None  # detached HEAD: the old commit is the point

    upstream = gitfacts.upstream_ref(root)
    if not upstream:
        return None

    # If the agent already fetched during this session it has current knowledge
    # and does not need to be told.
    fetch_age = gitfacts.fetch_age_seconds(gitdir)
    if ctx.session_started is not None and fetch_age is not None:
        session_age = max(0.0, time.time() - ctx.session_started)
        if fetch_age < session_age:
            return None

    # Remote knowledge is old enough that a "current" reading would be
    # meaningless. Refresh out of band and stay quiet; the next touch is
    # accurate. Silence here is honest -- it means "not known", not "fine".
    if fetch_age is None or fetch_age > ctx.fetch_ttl_s:
        if ctx.allow_background_fetch and not state.fetch_attempted_recently(root):
            state.mark_fetch_attempt(root)
            gitfacts.spawn_background_fetch(root)
        return None

    behind = gitfacts.commits_behind(root, upstream)
    if not behind:
        return None

    sha = gitfacts.upstream_sha(root, upstream) or "unknown"
    token = "%s@%s:%d" % (upstream, sha[:12], behind)

    if state.is_acknowledged(root, "stale_checkout", token):
        return None
    if state.already_signed(ctx.session_id, root, "stale_checkout"):
        return None

    age = gitfacts.upstream_commit_age(root, upstream) or "unknown age"
    text = (
        "STALE CHECKOUT - {name} is {n} commit(s) behind {up} (upstream tip {age}). "
        "This working copy may not be what is deployed; confirm which source is "
        "authoritative before treating work here as fixing the live system. "
        "Inspect: git -C {root} log --oneline HEAD..@{{u}}"
    ).format(name=os.path.basename(root), n=behind, up=upstream, age=age, root=root)

    return Sign(id="stale_checkout", text=text, state_token=token, repo=root)
