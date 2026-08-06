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

# How old remote knowledge may get before a background refresh is triggered.
#
# This is a *refresh* threshold, not a reporting gate. Up to 0.1.1 it was both:
# a repo whose last fetch was older than this returned silence before
# `commits_behind` was ever consulted, so on a session that touches a repo once
# -- the common case -- the flagship sign could not fire at all.
#
# Surveying 83 local repositories (49 with an upstream) found 16 measurably
# behind. How many of those the old gate could report was not a property of the
# repositories at all, but of when you happened to look: this machine bulk-
# fetches every 6 hours, so the 30-minute window covered 8% of wall-clock time
# -- 14 of 16 inside it, 0 of 16 outside. The measurement was being discarded,
# not absent: the remote-tracking ref on disk already held it. 0.1.2 reports it
# and dates it. See `_when` below.
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
    # Absolute `time.time()` after which no further work may be started. None
    # disables the check, which is what direct callers in tests want.
    deadline: Optional[float] = None

    def out_of_time(self) -> bool:
        return self.deadline is not None and time.time() >= self.deadline


SignFn = Callable[["Context"], Optional[Sign]]
_REGISTRY: List[SignFn] = []


def register(fn: SignFn) -> SignFn:
    """Register a sign. Third parties can add signs without forking."""
    _REGISTRY.append(fn)
    return fn


def registered() -> List[str]:
    return [f.__name__ for f in _REGISTRY]


def evaluate(ctx: Context) -> List[Sign]:
    """Run every registered sign. A sign that raises is dropped, never fatal.

    The deadline is checked *before* each sign is started, and signs that loop
    over repository objects check it as they go. That is what makes the bound
    real: up to 0.1.1 the deadline was consulted only after every sign had
    already run, so it suppressed output rather than stopping work. Whatever has
    been measured when time runs out is still reported -- a partial answer from
    a sound check is sound, and dropping it would trade a true fact for nothing.
    """
    out: List[Sign] = []
    for fn in _REGISTRY:
        if ctx.out_of_time():
            break
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


def _background_fetch_allowed(ctx: Context) -> bool:
    """Whether this invocation may spawn the out-of-band refresh.

    `AGENT_SIGNAGE_NO_FETCH` turns it off globally, for a metered connection, a
    CI runner with no credentials for the remote, or any context where a
    surprise subprocess is unwelcome. It does not make the tool quieter: the
    reading already on disk is still reported, still dated. `doctor` sets it so
    that inspecting the tool never has a side effect on the repository.
    """
    if os.environ.get("AGENT_SIGNAGE_NO_FETCH", "").strip():
        return False
    return ctx.allow_background_fetch


def human_age(seconds: Optional[float]) -> str:
    """Coarse, honest duration: '4 days', '35 minutes', 'never fetched'."""
    if seconds is None:
        return "never fetched"
    s = max(0.0, seconds)
    for size, unit in ((86400.0, "day"), (3600.0, "hour"), (60.0, "minute")):
        if s >= size:
            n = int(s // size)
            return "%d %s%s" % (n, unit, "" if n == 1 else "s")
    return "under a minute"


# --------------------------------------------------------------------- signs

@register
def stale_checkout(ctx: Context) -> Optional[Sign]:
    """The working copy is behind its upstream, so it may not be what is live.

    Origin: 2026-08-05. A consistency pass was run against a checkout 26 commits
    behind the deployed branch; the stale tree described routes replaced months
    earlier. It was caught incidentally, not by design.

    What it measures is `git rev-list --count HEAD..@{u}` against the
    remote-tracking ref that is already on disk. That never touches the network,
    which is why the sign is safe in front of every file read -- and it is why
    the reading is always *as of the last fetch*. 0.1.2 states that date in the
    text rather than withholding the reading, which is what 0.1.1 did whenever
    the fetch was older than the refresh threshold.
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

    # Remote knowledge is old. Refresh out of band so the next touch is current
    # -- but do not throw away the reading that is already on disk. Whatever
    # `git clone` or the last `git fetch` wrote into the remote-tracking ref is
    # a measurement git took at a knowable moment, and a repo that was 27 behind
    # four days ago is worth saying so long as the sentence carries its date.
    is_stale = fetch_age is None or fetch_age > ctx.fetch_ttl_s
    if is_stale and _background_fetch_allowed(ctx) and not state.fetch_attempted_recently(root):
        state.mark_fetch_attempt(root)
        gitfacts.spawn_background_fetch(root)

    behind = gitfacts.commits_behind(root, upstream)
    if not behind:
        return None

    sha = gitfacts.upstream_sha(root, upstream) or "unknown"
    # Deliberately not keyed on `ahead`: an ack means "I know this repo is N
    # behind that upstream tip", and a local commit is not new information about
    # that. Keying on it would expire every ack the moment its holder committed.
    token = "%s@%s:%d" % (upstream, sha[:12], behind)

    if state.is_acknowledged(root, "stale_checkout", token):
        return None
    if state.already_signed(ctx.session_id, root, "stale_checkout", token):
        return None

    ahead = gitfacts.commits_ahead(root, upstream) or 0
    text = (
        "STALE CHECKOUT - {name} is {n} commit(s) behind {up}{div}{when}. "
        "This working copy may not be what is deployed; confirm which source is "
        "authoritative. Inspect: {cmd}"
    ).format(
        name=os.path.basename(root),
        n=behind,
        up=upstream,
        div=" and %d ahead" % ahead if ahead else "",
        when=_when(root, upstream, fetch_age, is_stale),
        cmd=(
            "git -C {r} fetch && git -C {r} log --oneline HEAD..@{{u}}"
            if is_stale
            else "git -C {r} log --oneline HEAD..@{{u}}"
        ).format(r=root),
    )

    return Sign(id="stale_checkout", text=text, state_token=token, repo=root)


def _when(root: str, upstream: str, fetch_age: Optional[float], is_stale: bool) -> str:
    """Date the reading, so the number is never presented as current truth.

    Two honest sentences, not one. A fetch inside the refresh threshold makes
    the count effectively current, and the useful extra fact is how old the
    upstream tip itself is. A fetch outside it makes the count a historical
    observation, and saying so is the whole reason this sign is allowed to speak
    from an old ref at all: the count is what git recorded then, and the gap
    now is genuinely unmeasured -- it can be larger if upstream advanced, and
    smaller if upstream was rewound. Claiming "at least N" would be an
    inference; naming the date is a measurement.
    """
    if not is_stale:
        age = gitfacts.upstream_commit_age(root, upstream)
        return " (upstream tip %s)" % age if age else ""
    if fetch_age is None:
        # No FETCH_HEAD: the remote-tracking ref is whatever `git clone` wrote.
        return " as of the clone; this repo has never fetched, so the gap now is unmeasured"
    return " as of its last fetch, %s ago; the gap now is unmeasured" % human_age(fetch_age)
