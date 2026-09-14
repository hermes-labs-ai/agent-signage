"""Where agent-signage remembers what it has already said.

Two separate suppressions, deliberately not merged:

  Session dedupe -- one sign per (session, repo, sign, state) so a sign cannot
  repeat itself while the agent works. Keyed by session, so a *new* session
  about the same repo is told again. An earlier prototype keyed this by repo
  alone, which silently muted the warning for every later session until the temp
  dir was cleared; that is the failure this split exists to prevent.

  Acknowledgement -- keyed by the observed *state*, not by the repo. Once a
  human or agent acknowledges "27 behind origin/main", that exact situation
  stays quiet. If upstream then moves, the key no longer matches and the sign
  speaks again. An ack can therefore never suppress genuinely new information,
  which is what makes "stop telling me" safe to offer at all.

Both are now bound to the state token, which up to 0.1.1 only acknowledgement
was. Session dedupe keyed on (session, repo, sign) alone, so an upstream that
advanced mid-session was suppressed for the rest of it -- the tool had said "12
behind" and would not say "40 behind" an hour later, because it had already
spoken about that repo. That also silently defeated the background refresh: the
first touch of a stale repo reports a dated count and starts a fetch, and the
whole point of the fetch is that the *next* touch can correct it. Suppressing
the correction made the refresh pointless in exactly the single-session case it
was built for. Re-firing is bounded by construction: the token only changes when
the measured situation changes, so the ceiling is one line per distinct fact.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from typing import Optional

_DIR_ENV = "AGENT_SIGNAGE_STATE_DIR"
_SESSION_PREFIX = "sess"
_ACK_PREFIX = "ack"

# Acks are not immortal. A month-old "yes I know" about a repo is not consent
# for today, and stale ack files would otherwise accumulate forever.
ACK_TTL_S = 30 * 24 * 3600


def state_dir() -> str:
    """Directory for stamps. Overridable for tests via AGENT_SIGNAGE_STATE_DIR."""
    d = os.environ.get(_DIR_ENV)
    if not d:
        d = os.path.join(tempfile.gettempdir(), "agent-signage")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return tempfile.gettempdir()
    return d


def _key(*parts: str) -> str:
    raw = "\x00".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:20]


def _path(prefix: str, key: str) -> str:
    return os.path.join(state_dir(), "%s-%s" % (prefix, key))


def _scoped_path(prefix: str, repo: str, *parts: str) -> str:
    """Stamp path carrying the repo's hash as its own segment, not folded into
    the combined key -- so a repo-only lookup (`clear(repo=...)`) can match a
    stamp without knowing the session id, sign, or state token that made it.
    """
    return _path(prefix, "%s-%s" % (_key(repo), _key(*parts)))


# --------------------------------------------------------------- session dedupe

def already_signed(session_id: str, repo: str, sign: str, state_token: str) -> bool:
    return os.path.exists(_scoped_path(_SESSION_PREFIX, repo, session_id, repo, sign, state_token))


def mark_signed(session_id: str, repo: str, sign: str, state_token: str) -> None:
    try:
        open(_scoped_path(_SESSION_PREFIX, repo, session_id, repo, sign, state_token), "w").close()
    except OSError:
        # Losing a stamp costs a duplicate sign, which is noise. Failing the
        # hook costs the sign entirely. Noise is the better failure.
        pass


# ------------------------------------------------------------- acknowledgement

def is_acknowledged(repo: str, sign: str, state_token: str) -> bool:
    """True if this exact (repo, sign, state) was acknowledged and not expired."""
    p = _scoped_path(_ACK_PREFIX, repo, repo, sign, state_token)
    try:
        age = time.time() - os.path.getmtime(p)
    except OSError:
        return False
    if age > ACK_TTL_S:
        try:
            os.remove(p)
        except OSError:
            pass
        return False
    return True


def acknowledge(repo: str, sign: str, state_token: str) -> str:
    """Silence this sign until the observed state changes. Returns the ack path."""
    p = _scoped_path(_ACK_PREFIX, repo, repo, sign, state_token)
    try:
        open(p, "w").close()
    except OSError:
        pass
    return p


# ------------------------------------------------------------- fetch cooldown

# A background refresh is only useful once. Without this guard, every file
# operation while a slow fetch is still in flight would spawn another one --
# a large repository on a slow link could accumulate a fetch per tool call.
FETCH_COOLDOWN_S = 120.0


def fetch_attempted_recently(repo: str, cooldown: float = FETCH_COOLDOWN_S) -> bool:
    p = _path("fetch", _key(repo))
    try:
        return (time.time() - os.path.getmtime(p)) < cooldown
    except OSError:
        return False


def mark_fetch_attempt(repo: str) -> None:
    try:
        p = _path("fetch", _key(repo))
        open(p, "w").close()
        os.utime(p, None)
    except OSError:
        pass


def _stamp_repo_key(name: str) -> Optional[str]:
    """Repo-hash segment of a stamp filename, or None if it isn't one we wrote.

    `sess-`/`ack-` are `{prefix}-{repo_key}-{full_key}`; `fetch-` is already
    just `{prefix}-{repo_key}`.
    """
    parts = name.split("-")
    if len(parts) == 3 and parts[0] in (_SESSION_PREFIX, _ACK_PREFIX):
        return parts[1]
    if len(parts) == 2 and parts[0] == "fetch":
        return parts[1]
    return None


def clear(repo: Optional[str] = None) -> int:
    """Remove stamps. With no `repo`, clears everything; with one, only that
    repo's session dedupe, acknowledgements, and fetch-cooldown marker.
    """
    d = state_dir()
    removed = 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    target_key = _key(repo) if repo is not None else None
    for n in names:
        repo_key = _stamp_repo_key(n)
        if repo_key is None:
            continue
        if target_key is not None and repo_key != target_key:
            continue
        try:
            os.remove(os.path.join(d, n))
            removed += 1
        except OSError:
            pass
    return removed
