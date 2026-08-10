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
from conftest import commit, git

from agent_signage import gitfacts, hook, more_signs, signs, state  # noqa: F401

pytestmark = pytest.mark.usefixtures("isolated_state")


# --------------------------------------------------------------------- helpers

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


def test_reported_count_matches_git(behind_repo, monkeypatch):
    """Soundness: the number in the sign is the number git reports."""
    immutable_tip = gitfacts.upstream_sha(str(behind_repo), "origin/main")
    real_tip_age = gitfacts.upstream_commit_age
    age_targets = []

    def record_tip_age(root, target):
        age_targets.append(target)
        return real_tip_age(root, target)

    monkeypatch.setattr(gitfacts, "upstream_commit_age", record_tip_age)
    real = int(
        subprocess.run(
            ["git", "-C", str(behind_repo), "rev-list", "--count", "HEAD..origin/main"],
            stdout=subprocess.PIPE,
            check=True,
        ).stdout.strip()
    )
    s = signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    assert "%d commit(s) behind" % real in s.text
    assert age_targets == [immutable_tip]


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

def test_stale_fetch_state_still_reports_and_refreshes(behind_repo, monkeypatch):
    """The 0.1.1 defect, inverted into a guarantee.

    Up to 0.1.1 an old fetch made this return None before `commits_behind` was
    ever called, so a session that touched a repo once -- the common case -- got
    nothing. The measurement was on disk the whole time. It is now reported and
    dated, and the background refresh still happens.
    """
    calls = []
    monkeypatch.setattr(gitfacts, "spawn_background_fetch", lambda r: calls.append(r) or True)
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 4 * 86400.0)

    s = signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    assert s is not None, "an old fetch must not discard a measurement already on disk"
    assert "3 commit(s) behind origin/main" in s.text
    assert "FETCH_HEAD was 4 days old when checked" in s.text, "the reading must carry its date"
    assert "the current gap is unmeasured" in s.text, "must not imply the count is current"
    assert "fetch &&" in s.text, "the resolving command must start by fetching"
    assert len(calls) == 1, "and the background refresh still happens"


def test_stale_reading_is_dated_not_inflated(behind_repo, monkeypatch):
    """Soundness under an old fetch: report what git counted, claim nothing more.

    'At least N behind' would be an inference -- upstream can be rewound, which
    makes the true gap smaller, not larger. The sign states the count git
    computed and the moment it was computed, and calls the present unmeasured.
    """
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 4 * 86400.0)
    monkeypatch.setattr(gitfacts, "spawn_background_fetch", lambda r: True)
    real = int(
        subprocess.run(
            ["git", "-C", str(behind_repo), "rev-list", "--count", "HEAD..origin/main"],
            stdout=subprocess.PIPE,
            check=True,
        ).stdout.strip()
    )
    s = signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    assert "%d commit(s) behind" % real in s.text
    assert "at least" not in s.text.lower()


def test_stale_reading_pins_one_immutable_upstream_tip(behind_repo, monkeypatch):
    """A ref move cannot splice a count from one tip to the SHA of another."""
    old_tip = gitfacts.upstream_sha(str(behind_repo), "origin/main")
    origin = subprocess.run(
        ["git", "-C", str(behind_repo), "remote", "get-url", "origin"],
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.decode().strip()
    real_behind = gitfacts.commits_behind
    real_ahead = gitfacts.commits_ahead
    measured_targets = []

    def move_ref_after_count(root, target):
        measured_targets.append(("behind", target))
        count = real_behind(root, target)
        commit(type(behind_repo)(origin), "moved-after-count.txt")
        git(behind_repo, "fetch", "-q")
        return count

    def record_ahead(root, target):
        measured_targets.append(("ahead", target))
        return real_ahead(root, target)

    monkeypatch.setattr(gitfacts, "commits_behind", move_ref_after_count)
    monkeypatch.setattr(gitfacts, "commits_ahead", record_ahead)
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda _gitdir: 4 * 86400.0)
    monkeypatch.setenv("AGENT_SIGNAGE_NO_FETCH", "1")

    s = signs.stale_checkout(ctx_for(behind_repo / "a.txt"))

    assert s is not None
    assert "3 commit(s) behind origin/main" in s.text
    assert "at %s" % old_tip[:12] in s.text
    assert measured_targets == [("behind", old_tip), ("ahead", old_tip)]


def test_never_fetched_repo_says_so(behind_repo, monkeypatch):
    """No FETCH_HEAD means the ref is whatever `git clone` wrote. Say that."""
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: None)
    monkeypatch.setattr(gitfacts, "spawn_background_fetch", lambda r: True)
    s = signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    assert s is not None
    assert "never fetched" in s.text


def test_current_repo_stays_silent_however_old_the_fetch(behind_repo, monkeypatch):
    """Silence still means 'no drift known'. Reporting an old reading must not
    turn into asserting drift that the ref on disk does not show."""
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 400 * 86400.0)
    monkeypatch.setattr(gitfacts, "spawn_background_fetch", lambda r: True)
    git(behind_repo, "merge", "-q", "origin/main")
    gitfacts.clear_caches()
    assert signs.stale_checkout(ctx_for(behind_repo / "a.txt")) is None


def test_reports_ahead_when_diverged(behind_repo):
    """A diverged tree needs a different resolution than one that is purely
    behind, and `ahead` is one more integer git already knows."""
    commit(behind_repo, "local.txt")
    gitfacts.clear_caches()
    s = signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    assert "3 commit(s) behind origin/main and 1 ahead" in s.text


def test_no_ahead_clause_when_purely_behind(behind_repo):
    s = signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    assert " ahead" not in s.text


def test_ack_survives_a_local_commit(behind_repo):
    """`ahead` is deliberately outside the ack token: committing locally is not
    new information about how far behind upstream this tree is."""
    s = signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    state.acknowledge(s.repo, s.id, s.state_token)
    commit(behind_repo, "local.txt")
    gitfacts.clear_caches()
    assert signs.stale_checkout(ctx_for(behind_repo / "a.txt", session="other")) is None


# --------------------------------------------------------- session dedupe

def test_speaks_again_when_upstream_advances_mid_session(behind_repo, monkeypatch):
    """Session dedupe must not outlive the fact it was suppressing.

    Up to 0.1.1 the session stamp was keyed by (session, repo, sign) with no
    state, so the first sign of a session muted that repo for the rest of it --
    including a later, larger, genuinely different drift.
    """
    origin = subprocess.run(
        ["git", "-C", str(behind_repo), "remote", "get-url", "origin"],
        stdout=subprocess.PIPE, check=True,
    ).stdout.decode().strip()

    first = hook.run(payload(behind_repo / "a.txt", session="live"))
    assert "3 commit(s) behind" in first["hookSpecificOutput"]["additionalContext"]

    assert hook.run(payload(behind_repo / "a.txt", session="live")) is None, (
        "an unchanged situation must still be said only once"
    )

    commit(type(behind_repo)(origin), "e.txt")
    git(behind_repo, "fetch", "-q")

    second = hook.run(payload(behind_repo / "a.txt", session="live"))
    assert second is not None, "a larger drift is new information, not a repeat"
    assert "4 commit(s) behind" in second["hookSpecificOutput"]["additionalContext"]


def test_the_background_refresh_can_correct_itself_in_one_session(behind_repo, monkeypatch):
    """The refresh exists so the *next* touch is accurate. Prove it lands.

    First touch: the fetch is old, so the count is reported with its date and a
    refresh is started. Then the refresh completes. The second touch must be
    able to deliver the corrected, current reading -- which the old dedupe key
    made impossible in a single session.
    """
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 4 * 86400.0)
    monkeypatch.setattr(gitfacts, "spawn_background_fetch", lambda r: True)

    first = hook.run(payload(behind_repo / "a.txt", session="heal"))
    body = first["hookSpecificOutput"]["additionalContext"]
    assert "FETCH_HEAD was 4 days old when checked" in body

    origin = subprocess.run(
        ["git", "-C", str(behind_repo), "remote", "get-url", "origin"],
        stdout=subprocess.PIPE, check=True,
    ).stdout.decode().strip()
    commit(type(behind_repo)(origin), "e.txt")
    git(behind_repo, "fetch", "-q")
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 1.0)

    second = hook.run(payload(behind_repo / "a.txt", session="heal"))
    assert second is not None, "the correction the refresh was started for must reach the agent"
    body = second["hookSpecificOutput"]["additionalContext"]
    assert "4 commit(s) behind" in body
    assert "unmeasured" not in body, "a fresh fetch reports a current count, undated"


# ------------------------------------------------------------------ deadline

def test_evaluate_stops_starting_signs_past_the_deadline(behind_repo):
    """The bound is real work stopped, not output withheld."""
    ran = []

    def slow(ctx):
        ran.append("slow")
        time.sleep(0.05)
        return None

    def after(ctx):
        ran.append("after")
        return None

    original = list(signs._REGISTRY)
    try:
        signs._REGISTRY[:] = [slow, after]
        ctx = ctx_for(behind_repo / "a.txt", deadline=time.time() + 0.01)
        signs.evaluate(ctx)
    finally:
        signs._REGISTRY[:] = original

    assert ran == ["slow"], "a sign must not be started once the deadline has passed"


def test_a_partial_measurement_is_still_reported(behind_repo):
    """Whatever was measured before time ran out is true, so it is kept.

    0.1.1 did the opposite: it ran every sign and then discarded the lot if the
    clock had run out, which paid the full cost and delivered nothing.
    """
    spoke = signs.Sign(id="x", text="X - true fact", state_token="t", repo=str(behind_repo))

    def first(ctx):
        time.sleep(0.05)
        return spoke

    original = list(signs._REGISTRY)
    try:
        signs._REGISTRY[:] = [first, lambda ctx: pytest.fail("second sign must not start")]
        out = signs.evaluate(ctx_for(behind_repo / "a.txt", deadline=time.time() + 0.01))
    finally:
        signs._REGISTRY[:] = original

    assert out == [spoke], "the fact measured before the deadline must survive it"


def test_an_already_expired_deadline_starts_nothing(behind_repo):
    original = list(signs._REGISTRY)
    try:
        signs._REGISTRY[:] = [lambda ctx: pytest.fail("must not run")]
        out = signs.evaluate(ctx_for(behind_repo / "a.txt", deadline=time.time() - 1))
    finally:
        signs._REGISTRY[:] = original
    assert out == []


def test_stale_checkout_stops_between_git_calls_when_deadline_expires(
    behind_repo, monkeypatch
):
    """A completed Git call may cross the deadline; no second call may start."""
    real_repo_root = gitfacts.repo_root

    def slow_repo_root(path):
        root = real_repo_root(path)
        time.sleep(0.03)
        return root

    monkeypatch.setattr(gitfacts, "repo_root", slow_repo_root)
    monkeypatch.setattr(
        gitfacts,
        "git_dir",
        lambda _root: pytest.fail("must not start another Git call past the deadline"),
    )

    ctx = ctx_for(behind_repo / "a.txt", deadline=time.time() + 0.01)
    assert signs.stale_checkout(ctx) is None


def test_hook_hands_a_deadline_to_the_signs(behind_repo, monkeypatch):
    seen = {}

    def capture(ctx):
        seen["deadline"] = ctx.deadline
        return None

    original = list(signs._REGISTRY)
    try:
        signs._REGISTRY[:] = [capture]
        started = time.time()
        hook.run(payload(behind_repo / "a.txt"), now=started)
    finally:
        signs._REGISTRY[:] = original

    assert seen["deadline"] == pytest.approx(started + hook.DEADLINE_S)


def test_worktree_scan_honours_the_deadline(repo, tmp_path):
    """The one sign whose cost scales with the repo must check the clock."""
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    (wt / "seed.txt").write_text("changed by the other agent")
    gitfacts.clear_caches()

    ctx = signs.Context(
        session_id="dl", tool_name="Edit", target_path=str(repo / "seed.txt"),
        deadline=time.time() - 1,
    )
    assert more_signs.concurrent_worktree_edit(ctx) is None, (
        "an expired deadline must stop the per-worktree scan before it starts"
    )


def test_no_deadline_means_no_limit(behind_repo):
    """Direct callers and tests get the unbounded behaviour by default."""
    assert ctx_for(behind_repo / "a.txt").out_of_time() is False


def test_no_fetch_storm(behind_repo, monkeypatch):
    calls = []
    monkeypatch.setattr(gitfacts, "spawn_background_fetch", lambda r: calls.append(r) or True)
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 99999.0)

    for _ in range(10):
        signs.stale_checkout(ctx_for(behind_repo / "a.txt"))
    assert len(calls) == 1, "cooldown must bound refreshes to one per window"


# Every git subcommand that can open a connection. The measuring path may use
# none of them; the background refresh may use exactly `fetch`, and only via the
# detached Popen.
NETWORK_VERBS = frozenset(
    {"fetch", "pull", "push", "clone", "ls-remote", "remote-https", "submodule"}
)


def _network_guard(monkeypatch, seen):
    """Trip on any network-capable git call made through *either* primitive.

    0.1.1's version of this test patched `subprocess.run` alone and looked only
    for "fetch". `spawn_background_fetch` uses `subprocess.Popen`, so the guard
    was structurally incapable of seeing the one call in the codebase that does
    contact the network -- it asserted over a path that had nothing to find.
    """
    real_run, real_popen = subprocess.run, subprocess.Popen

    def note(kind, cmd):
        if isinstance(cmd, (list, tuple)):
            for word in cmd:
                if word in NETWORK_VERBS:
                    seen.append((kind, word, list(cmd)))

    def run_guard(cmd, *a, **k):
        note("run", cmd)
        return real_run(cmd, *a, **k)

    def popen_guard(cmd, *a, **k):
        note("popen", cmd)
        return real_popen(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", run_guard)
    monkeypatch.setattr(subprocess, "Popen", popen_guard)
    return seen


def test_never_fetches_on_the_measuring_path(behind_repo, monkeypatch):
    """No sign's measurement may contact the network -- not just stale_checkout.

    Exercises the whole registry through `hook.run`, with the background refresh
    disabled, which is the exact claim the README makes.
    """
    seen = _network_guard(monkeypatch, [])
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 99999.0)
    monkeypatch.setenv("AGENT_SIGNAGE_NO_FETCH", "1")

    out = hook.run(payload(behind_repo / "a.txt"))
    assert out is not None, "the repo really is behind; this must be the speaking path"
    assert seen == [], "measuring path made a network-capable git call: %r" % (seen,)


def test_the_only_network_call_is_the_detached_refresh(behind_repo, monkeypatch):
    """Scope the network claim precisely rather than overstating it.

    agent-signage does make one network call, and this pins what it is allowed
    to be: `git fetch`, spawned through Popen so it is never awaited, at most
    once per cooldown window, and only when the refresh is permitted.
    """
    seen = _network_guard(monkeypatch, [])
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 99999.0)

    for _ in range(5):
        hook.run(payload(behind_repo / "a.txt", session="net"))

    assert [kind for kind, _v, _c in seen] == ["popen"], (
        "the refresh must be detached, and nothing else may reach the network: %r" % (seen,)
    )
    assert seen[0][1] == "fetch"
    assert "--quiet" in seen[0][2] and "--no-tags" in seen[0][2]


def test_no_network_at_all_when_the_refresh_is_disabled(behind_repo, monkeypatch):
    seen = _network_guard(monkeypatch, [])
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 99999.0)
    monkeypatch.setenv("AGENT_SIGNAGE_NO_FETCH", "1")

    for _ in range(5):
        hook.run(payload(behind_repo / "a.txt", session="nonet"))
    assert seen == []


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
