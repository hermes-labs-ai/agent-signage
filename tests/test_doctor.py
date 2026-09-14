"""`doctor` has to be right about the tool, or it is worse than no doctor.

The failure it exists to prevent is a user reading silence as "fine" when the
hook is not wired at all. So the tests here care most about two things: that it
tells the truth about the install, and that it can never claim a sign is quiet
for a reason that does not exist.
"""

from __future__ import annotations

import json
import os
import subprocess
from argparse import Namespace

import pytest
from conftest import commit, git

from agent_signage import __main__ as cli
from agent_signage import doctor, gitfacts, more_signs, signs, state  # noqa: F401

pytestmark = pytest.mark.usefixtures("isolated_state")

# Timing spawns a real interpreter per run; one is enough to prove the path.
FAST = 1


@pytest.fixture
def no_settings(monkeypatch, tmp_path):
    """Point the settings probe at paths that do not exist."""
    monkeypatch.setattr(doctor, "SETTINGS_PATHS", (str(tmp_path / "nope.json"),))


@pytest.fixture
def wired(monkeypatch, tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"PreToolUse": [{
        "matcher": "Read|Edit",
        "hooks": [{"type": "command", "command": "python3 -m agent_signage", "timeout": 8}],
    }]}}))
    monkeypatch.setattr(doctor, "SETTINGS_PATHS", (str(p),))
    return p


# ------------------------------------------------------- the install question

def test_reports_a_missing_install_and_exits_nonzero(behind_repo, no_settings):
    lines, ok = doctor.report(str(behind_repo / "a.txt"), runs=FAST)
    assert ok is False, "an unwired hook is the one state doctor must flag"
    body = "\n".join(lines)
    assert "not wired" in body
    assert "agent-signage install" in body


def test_reports_a_present_install(behind_repo, wired):
    lines, ok = doctor.report(str(behind_repo / "a.txt"), runs=FAST)
    assert ok is True
    body = "\n".join(lines)
    assert str(wired) in body
    assert "Read|Edit" in body


def test_install_probe_ignores_unrelated_hooks(tmp_path, monkeypatch, behind_repo):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"PreToolUse": [{
        "matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]}}))
    monkeypatch.setattr(doctor, "SETTINGS_PATHS", (str(p),))
    _lines, ok = doctor.report(str(behind_repo / "a.txt"), runs=FAST)
    assert ok is False, "someone else's hook is not this hook"


def test_install_probe_ignores_malformed_hook_shapes(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    monkeypatch.setattr(doctor, "SETTINGS_PATHS", (str(p),))
    for payload in (
        {"hooks": []},
        {"hooks": {"PreToolUse": {}}},
        {"hooks": {"PreToolUse": [{"hooks": [None, "not an object"]}]}},
    ):
        p.write_text(json.dumps(payload))
        assert doctor._installs() == []


def test_install_probe_ignores_non_string_commands(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "PreToolUse": [{
                "hooks": [{
                    "type": "command",
                    "command": ["python3", "-m", "agent_signage"],
                }],
            }],
        },
    }))
    monkeypatch.setattr(doctor, "SETTINGS_PATHS", (str(settings),))

    assert doctor._installs() == []


def test_install_accepts_a_relative_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli._cmd_install(Namespace(
        settings="settings.json", command="python3 -m agent_signage", timeout=8
    )) == 0
    assert (tmp_path / "settings.json").is_file()


def test_ack_normalizes_to_git_toplevel(tmp_path, behind_repo, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = signs.stale_checkout(signs.Context(
        session_id="ack-cli", tool_name="Read", target_path=str(behind_repo / "a.txt"),
        allow_background_fetch=False))
    assert s is not None
    assert cli._cmd_ack(Namespace(
        repo=behind_repo.name, sign=s.id, token=s.state_token
    )) == 0
    assert state.is_acknowledged(str(behind_repo), s.id, s.state_token)


def test_unreadable_settings_does_not_raise(tmp_path, monkeypatch, behind_repo):
    bad = tmp_path / "settings.json"
    bad.write_text("{ not json")
    monkeypatch.setattr(doctor, "SETTINGS_PATHS", (str(bad),))
    doctor.report(str(behind_repo / "a.txt"), runs=FAST)  # must not raise


# ------------------------------------------------------ the repository answer

def test_non_repo_path_says_so_plainly(tmp_path, wired):
    f = tmp_path / "loose.txt"
    f.write_text("x")
    lines, _ok = doctor.report(str(f), runs=FAST)
    body = "\n".join(lines)
    assert "not inside a git repository" in body
    assert "This is not a fault" in body


def test_shows_the_measured_gap_and_names_it_dated(behind_repo, wired, monkeypatch):
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 4 * 86400.0)
    lines, _ok = doctor.report(str(behind_repo / "a.txt"), runs=FAST)
    body = "\n".join(lines)
    assert "behind / ahead" in body
    assert "3 / 0" in body
    assert "4 days ago" in body
    assert "dated" in body, "an old fetch must be labelled as such in the report too"


def test_speaking_sign_is_shown_with_its_text(behind_repo, wired):
    lines, _ok = doctor.report(str(behind_repo / "a.txt"), runs=FAST)
    body = "\n".join(lines)
    assert "stale_checkout           SPEAKS" in body
    assert "3 commit(s) behind origin/main" in body
    assert "ack token" in body


def test_quiet_sign_gets_a_measured_reason(behind_repo, wired):
    git(behind_repo, "merge", "-q", "origin/main")
    lines, _ok = doctor.report(str(behind_repo / "a.txt"), runs=FAST)
    body = "\n".join(lines)
    assert "level with origin/main" in body


def test_an_acknowledgement_is_named_as_the_reason(behind_repo, wired):
    """A silenced sign must be reported as silenced, not as having nothing to say."""
    from agent_signage import state

    s = signs.stale_checkout(signs.Context(
        session_id="probe", tool_name="Read", target_path=str(behind_repo / "a.txt"),
        allow_background_fetch=False))
    state.acknowledge(s.repo, s.id, s.state_token)
    gitfacts.clear_caches()

    body = "\n".join(doctor.report(str(behind_repo / "a.txt"), runs=FAST)[0])
    assert "acknowledged" in body
    assert "agent-signage clear" in body
    assert "unexpected" not in body


def test_speaks_when_behind_with_no_tracked_files(tmp_path, wired):
    """A repo can be behind with nothing tracked (e.g. only empty commits).

    `evaluate_here` used to bail out entirely whenever there was no sample
    file, so a repo-level sign like `stale_checkout` -- which only needs a
    path to resolve the repo root, not an actual file -- was reported quiet
    via `quiet_reason`'s "this is unexpected, please report it" fallback, even
    though the real hook fires correctly against the very same repository.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.email", "t@t.t")
    git(origin, "config", "user.name", "t")
    git(origin, "commit", "-q", "--allow-empty", "-m", "empty a")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(clone)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    git(clone, "config", "user.email", "t@t.t")
    git(clone, "config", "user.name", "t")

    git(origin, "commit", "-q", "--allow-empty", "-m", "empty b")
    git(clone, "fetch", "-q")

    facts = doctor.collect(str(clone))
    assert facts["sample"] is None, "fixture must have no tracked files"

    lines, _ok = doctor.report(str(clone), runs=FAST)
    body = "\n".join(lines)
    assert "stale_checkout           SPEAKS" in body
    assert "unexpected" not in body


def test_no_upstream_is_named_as_the_reason(tmp_path, wired):
    r = tmp_path / "solo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t.t")
    git(r, "config", "user.name", "t")
    commit(r, "a.txt")
    lines, _ok = doctor.report(str(r / "a.txt"), runs=FAST)
    assert "no upstream is configured for main" in "\n".join(lines)


def test_every_sign_appears_in_the_report(behind_repo, wired):
    body = "\n".join(doctor.report(str(behind_repo / "a.txt"), runs=FAST)[0])
    for name in signs.registered():
        assert name in body, "%s is registered but invisible in doctor" % name


def test_every_sign_has_a_doctor_reason(behind_repo):
    """The rot guard: a new sign cannot ship without an explanation of its
    silence, because silence with no explanation is the problem doctor exists
    to solve."""
    facts = doctor.collect(str(behind_repo / "a.txt"))
    for name in signs.registered():
        reason = doctor.quiet_reason(name, facts)
        assert reason and reason != "no measured condition to report", (
            "%s has no doctor reason; add one in doctor.quiet_reason" % name
        )


# ----------------------------------------------------------- no side effects

def test_doctor_never_fetches(behind_repo, wired, monkeypatch):
    """Inspecting the tool must not change the repository being inspected."""
    calls = []
    monkeypatch.setattr(gitfacts, "spawn_background_fetch", lambda r: calls.append(r) or True)
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 99999.0)
    monkeypatch.setenv("AGENT_SIGNAGE_NO_FETCH", "1")
    doctor.report(str(behind_repo / "a.txt"), runs=FAST)
    assert calls == [], "doctor spawned a fetch"


def test_doctor_writes_no_session_stamps(behind_repo, wired, isolated_state):
    before = set(os.listdir(isolated_state))
    doctor.report(str(behind_repo / "a.txt"), runs=FAST)
    after = {n for n in os.listdir(isolated_state) if n.startswith("sess-")}
    assert after <= {n for n in before if n.startswith("sess-")}, (
        "doctor must not consume the once-per-session budget of a real run"
    )


def test_doctor_reports_after_the_hook_already_spoke(behind_repo, wired):
    """Dedupe is per session; doctor uses its own, so it still shows the sign."""
    from agent_signage import hook
    hook.run(json.dumps({"session_id": "s1", "tool_input": {"file_path":
                                                            str(behind_repo / "a.txt")}}))
    body = "\n".join(doctor.report(str(behind_repo / "a.txt"), runs=FAST)[0])
    assert "SPEAKS" in body


# ------------------------------------------------------------- sample + cost

def test_sample_prefers_a_file_with_uncommitted_changes(behind_repo, wired):
    (behind_repo / "a.txt").write_text("dirty")
    gitfacts.clear_caches()
    facts = doctor.collect(str(behind_repo))
    assert facts["sample"] == str(behind_repo / "a.txt")


def test_sample_falls_back_to_a_tracked_file(behind_repo):
    facts = doctor.collect(str(behind_repo))
    assert facts["sample"] and os.path.isfile(facts["sample"])


def test_cost_is_measured_end_to_end(behind_repo, wired):
    t = doctor.time_invocation(str(behind_repo / "a.txt"), runs=3)
    assert t is not None
    assert t["p50"] > 0 and t["p99"] >= t["p50"], "percentiles must be ordered"
    body = "\n".join(doctor.report(str(behind_repo / "a.txt"), runs=FAST)[0])
    assert "per tool call" in body


# ----------------------------------------------------- the no-fetch escape hatch

def test_no_fetch_env_disables_the_background_refresh(behind_repo, monkeypatch):
    calls = []
    monkeypatch.setattr(gitfacts, "spawn_background_fetch", lambda r: calls.append(r) or True)
    monkeypatch.setattr(gitfacts, "fetch_age_seconds", lambda g: 99999.0)
    monkeypatch.setenv("AGENT_SIGNAGE_NO_FETCH", "1")

    s = signs.stale_checkout(signs.Context(
        session_id="s1", tool_name="Read", target_path=str(behind_repo / "a.txt")))
    assert calls == [], "AGENT_SIGNAGE_NO_FETCH must suppress the refresh"
    assert s is not None, "and must not suppress the reading already on disk"
