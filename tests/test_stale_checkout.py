"""Behavioural tests over real git repositories built in temp directories.

These build actual repos rather than mocking git, because the whole soundness
claim is "we report what git reports". A mock would only prove we are faithful
to our own assumptions about git's output.

Every guarantee promised to consumers has a test here that fails if it regresses.
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest

from agent_signage import gitfacts, hook, more_signs, signs, state  # noqa: F401

pytestmark = pytest.mark.usefixtures("isolated_state")


# --------------------------------------------------------------------- helpers

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
    return d


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


def ctx_for(path, session="s1", **kw):
    return signs.Context(session_id=session, tool_name="Read", target_path=str(path), **kw)


def payload(path, session="s1", key="file_path"):
    return json.dumps({"session_id": session, "tool_input": {key: str(path)}})


# ------------------------------------------------------------ the sign fires

def test_fires_when_behind(behind_repo):
    s = signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    assert s is not None
    assert "3 commit(s) behind origin/main" in s.text
    assert "git -C" in s.text, "sign must end in the resolving command"


def test_silent_when_current(behind_repo):
    git(behind_repo, "merge", "-q", "origin/main")
    assert signs.stale_checkout(ctx_for(behind_repo / "a.txt")) is None


def test_reported_count_matches_git(behind_repo):
    """Soundness: the number in the sign is the number git reports."""
    real = int(
        subprocess.run(
            ["git", "-C", str(behind_repo), "rev-list", "--count", "HEAD..origin/main"],
            stdout=subprocess.PIPE,
            check=True,
        ).stdout.strip()
    )
    s = signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    assert "%d commit(s) behind" % real in s.text


# ------------------------------------------------- deliberate states suppress

def test_silent_during_bisect(behind_repo):
    (behind_repo / ".git" / "BISECT_LOG").write_text("bisect")
    assert signs.stale_checkout(ctx_for(behind_repo / "a.txt")) is None


def test_silent_when_detached(behind_repo):
    sha = subprocess.run(
        ["git", "-C", str(behind_repo), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.decode().strip()
    git(behind_repo, "checkout", "-q", sha)
    assert signs.stale_checkout(ctx_for(behind_repo / "a.txt")) is None


@pytest.mark.parametrize("marker", ["MERGE_HEAD", "CHERRY_PICK_HEAD"])
def test_silent_during_in_progress_op(behind_repo, marker):
    (behind_repo / ".git" / marker).write_text("deadbeef")
    assert signs.stale_checkout(ctx_for(behind_repo / "a.txt")) is None


def test_silent_during_rebase(behind_repo):
    (behind_repo / ".git" / "rebase-merge").mkdir()
    assert signs.stale_checkout(ctx_for(behind_repo / "a.txt")) is None


@pytest.mark.parametrize(
    "rel", ["node_modules/p/i.js", "vendor/lib.go", ".venv/x.py", "dist/out.js", "build/o.o"]
)
def test_silent_for_vendored_paths(behind_repo, rel):
    p = behind_repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    assert signs.stale_checkout(ctx_for(p)) is None


def test_silent_when_no_upstream(tmp_path):
    r = tmp_path / "solo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t.t")
    git(r, "config", "user.name", "t")
    commit(r, "a.txt")
    assert signs.stale_checkout(ctx_for(r / "a.txt")) is None


def test_silent_when_repo_ignored(behind_repo, monkeypatch):
    monkeypatch.setenv("AGENT_SIGNAGE_IGNORE", str(behind_repo))
    assert signs.stale_checkout(ctx_for(behind_repo / "a.txt")) is None


def test_silent_when_agent_already_fetched_this_session(behind_repo):
    """The agent fetched after the session began, so it already knows."""
    assert signs.stale_checkout(
        ctx_for(behind_repo / "a.txt", session_started=time.time() - 3600)
    ) is None


# ------------------------------------------------------------------- dedupe

def test_deduped_within_session(behind_repo):
    p = payload(behind_repo / "a.txt")
    assert hook.run(p) is not None
    assert hook.run(p) is None, "must not repeat within a session"


def test_speaks_again_in_a_new_session(behind_repo):
    """The bug that shipped in the prototype: a repo-only key muted every
    later session until the temp dir was cleared."""
    assert hook.run(payload(behind_repo / "a.txt", session="s1")) is not None
    assert hook.run(payload(behind_repo / "a.txt", session="s2")) is not None


# --------------------------------------------------------- acknowledgement

def test_ack_silences_that_state(behind_repo):
    s = signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    state.acknowledge(s.repo, s.id, s.state_token)
    assert signs.stale_checkout(ctx_for(behind_repo / "a.txt", session="other")) is None


def test_ack_expires_when_upstream_moves(behind_repo):
    """An ack must never suppress genuinely new information."""
    s = signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    state.acknowledge(s.repo, s.id, s.state_token)

    origin = subprocess.run(
        ["git", "-C", str(behind_repo), "remote", "get-url", "origin"],
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.decode().strip()
    commit(type(behind_repo)(origin), "e.txt")
    git(behind_repo, "fetch", "-q")
    # In production the next tool call is a new process with empty caches;
    # hook.run() does this reset itself. Direct sign calls must do it here.
    gitfacts.clear_caches()

    again = signs.stale_checkout(ctx_for(behind_repo / "a.txt", session="other"))
    assert again is not None, "upstream moved; the old ack must not apply"


# ------------------------------------------------------- freshness / fetching

def test_stale_fetch_state_is_silent_and_refreshes(behind_repo, monkeypatch):
    calls = []
    monkeypatch.setattr(gitfacts, "spawn_background_fetch", lambda r: calls.append(r) or True)
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 99999.0)

    assert signs.stale_checkout(ctx_for(behind_repo / "a.txt")) is None
    assert calls == [str(behind_repo)] or len(calls) == 1


def test_no_fetch_storm(behind_repo, monkeypatch):
    calls = []
    monkeypatch.setattr(gitfacts, "spawn_background_fetch", lambda r: calls.append(r) or True)
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 99999.0)

    for _ in range(10):
        signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    assert len(calls) == 1, "cooldown must bound refreshes to one per window"


def test_never_fetches_on_the_hot_path(behind_repo, monkeypatch):
    """The measuring call must not contact the network."""
    real = subprocess.run

    def guard(cmd, *a, **k):
        if isinstance(cmd, (list, tuple)) and "fetch" in cmd:
            raise AssertionError("hot path attempted a network fetch: %r" % (cmd,))
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", guard)
    signs.stale_checkout(ctx_for(behind_repo / "a.txt"))


# ------------------------------------------------------------- hook contract

@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not json", "[1,2,3]", "null", '{"tool_input":{}}', '{"tool_input":null}',
     '{"tool_input":{"file_path":"   "}}', '{"tool_input":{"file_path":"/no/such/f.txt"}}'],
)
def test_bad_input_is_silent(raw):
    assert hook.run(raw) is None


def test_grep_style_path_key_is_accepted(behind_repo):
    assert hook.run(payload(behind_repo, key="path")) is not None


def test_output_envelope_shape(behind_repo):
    out = hook.run(payload(behind_repo / "a.txt"))
    assert set(out) == {"hookSpecificOutput"}
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert isinstance(hso["additionalContext"], str) and hso["additionalContext"]
    assert "decision" not in json.dumps(out), "agent-signage must never block"


def test_main_always_exits_zero(behind_repo, monkeypatch, capsys):
    for raw in ["", "garbage", payload(behind_repo / "a.txt")]:
        monkeypatch.setattr("sys.stdin", type("S", (), {"read": staticmethod(lambda: raw)})())
        assert hook.main() == 0
    capsys.readouterr()


def test_exits_zero_even_when_git_is_missing(behind_repo, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no git"))
    )
    assert hook.run(payload(behind_repo / "a.txt")) is None


def test_registry_is_extensible():
    before = len(signs.registered())

    @signs.register
    def _probe(ctx):
        return None

    try:
        assert len(signs.registered()) == before + 1
    finally:
        signs._REGISTRY.remove(_probe)


def test_sign_that_raises_is_dropped_not_fatal(behind_repo):
    @signs.register
    def _boom(ctx):
        raise RuntimeError("boom")

    try:
        out = signs.evaluate(ctx_for(behind_repo / "a.txt"))
        assert any(s.id == "stale_checkout" for s in out), "one bad sign must not kill the rest"
    finally:
        signs._REGISTRY.remove(_boom)


def test_hot_path_latency_budget(behind_repo):
    t0 = time.time()
    for i in range(10):
        hook.run(payload(behind_repo / "a.txt", session="perf%d" % i))
    per_call_ms = (time.time() - t0) / 10 * 1000
    assert per_call_ms < 250, "hot path too slow: %.0fms" % per_call_ms


def test_message_stays_short(behind_repo):
    out = hook.run(payload(behind_repo / "a.txt"))
    text = out["hookSpecificOutput"]["additionalContext"]
    assert len(text) < 400, "a road sign must stay scannable: %d chars" % len(text)
    assert "\n" not in text.strip(), "one line"
