"""Reliability Lab envelope contract for agent-signage's card-from-evidence CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_signage import __version__, evidence

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "fixtures" / "lab"

DEFAULT_ARGS = dict(card_id="lab.demo", headline="GATE PASSED", next_step="Continue.")


def _ids(result: dict) -> list[str]:
    return [f["id"] for f in result["findings"]]


def test_completed_pass_with_a_fact_renders_one_supported_measured_fact():
    result = evidence.envelope_for(
        FIXTURES / "completed-pass-with-metric.json",
        fact_label="checks passed",
        fact_value="2 of 2",
        **DEFAULT_ARGS,
    )
    assert result["envelope"] == "hermes.reliability-lab.result/1"
    assert result["tool"] == "agent-signage"
    assert result["toolVersion"] == __version__
    assert result["command"] == "card"
    assert result["mode"] == "executed"
    assert result["status"] == "pass" == evidence.worst_status(result["findings"])
    assert result["exitCode"] == 0
    assert result["data"]["licensed"] is True
    assert result["data"]["card"] == {
        "id": "lab.demo",
        "headline": "GATE PASSED",
        "fact": "rule-audit 0.2.0 completed `audit`: checks passed 2 of 2.",
        "next": "Continue.",
    }
    assert json.loads(json.dumps(result)) == result


def test_completed_pass_with_no_fact_renders_a_card_with_no_invented_number():
    result = evidence.envelope_for(FIXTURES / "completed-pass-no-metric.json", **DEFAULT_ARGS)
    assert result["status"] == "pass"
    assert result["exitCode"] == 0
    assert result["data"]["card"]["fact"] == "rule-audit 0.2.0 completed `audit` with no reported metric."


def test_warn_source_licenses_no_card_and_is_reported_as_unknown_not_a_failure():
    result = evidence.envelope_for(FIXTURES / "completed-warn.json", **DEFAULT_ARGS)
    assert result["status"] == "unknown"
    assert result["exitCode"] == 0
    assert result["data"]["licensed"] is False
    assert result["data"]["card"] is None
    assert _ids(result) == ["source.not-completed-evidence"]


def test_preview_source_is_not_a_completed_event_and_licenses_no_card():
    result = evidence.envelope_for(FIXTURES / "preview.json", **DEFAULT_ARGS)
    assert result["status"] == "unknown"
    assert result["exitCode"] == 0
    assert result["data"]["card"] is None
    assert result["data"]["source"]["mode"] == "preview"


def test_wrong_schema_and_unparseable_and_missing_source_are_input_errors_not_no_card():
    for name, finding_id in [
        ("malformed-schema.json", "source.wrong-schema"),
        ("malformed-json.txt", "source.unreadable"),
        ("does-not-exist.json", "source.not-found"),
    ]:
        result = evidence.envelope_for(FIXTURES / name, **DEFAULT_ARGS)
        assert result["status"] == "fail", name
        assert result["exitCode"] == 1, name
        assert _ids(result) == [finding_id], name
        assert result["data"]["card"] is None, name


def test_contradictory_status_and_findings_is_refused_before_any_card_is_considered():
    result = evidence.envelope_for(FIXTURES / "contradictory.json", **DEFAULT_ARGS)
    assert result["status"] == "fail"
    assert result["exitCode"] == 1
    assert _ids(result) == ["source.contradictory-status"]
    assert result["data"]["licensed"] is False


def test_a_declared_fact_needs_both_label_and_value():
    result = evidence.envelope_for(
        FIXTURES / "completed-pass-with-metric.json", fact_label="checks passed", **DEFAULT_ARGS
    )
    assert result["status"] == "fail"
    assert result["exitCode"] == 1
    assert _ids(result) == ["input.incomplete-fact"]


def test_long_but_bounded_content_still_renders_one_line_under_the_cap():
    result = evidence.envelope_for(
        FIXTURES / "completed-pass-long.json",
        card_id="lab.demo.long",
        headline="GATE PASSED FOR THE DEMO TENANT ONBOARDING RELEASE",
        next_step=(
            "Review the full checked-in envelope before treating this as authorization to "
            "publish; nothing here grants that on its own."
        ),
        fact_label="checks passed across lint and typecheck gates in canary mode",
        fact_value="2 of 2, with pytest deferred to full mode and not included in this count",
    )
    assert result["status"] == "pass"
    text = result["data"]["rendered"]["text"]
    assert 300 < len(text) < 1500
    assert text.count("\n") == 0


def test_content_over_the_bound_is_a_reported_failure_not_a_silent_truncation():
    result = evidence.envelope_for(
        FIXTURES / "completed-pass-with-metric.json",
        card_id="lab.demo.overflow",
        headline="GATE PASSED",
        next_step="N" * 700,
        fact_label="characters in this label to push the line past the bound",
        fact_value="V" * 700,
    )
    assert result["status"] == "fail"
    assert result["exitCode"] == 1
    assert _ids(result) == ["card.rejected"]
    assert "exceeds" in result["findings"][0]["summary"]


def test_deterministic_text_json_and_hook_renderings_agree_on_the_same_card():
    result = evidence.envelope_for(
        FIXTURES / "completed-pass-with-metric.json",
        fact_label="checks passed",
        fact_value="2 of 2",
        **DEFAULT_ARGS,
    )
    rendered = result["data"]["rendered"]
    assert rendered["text"] == (
        "GATE PASSED [lab.demo] - rule-audit 0.2.0 completed `audit`: checks passed 2 of 2. "
        "Next: Continue."
    )
    parsed_json = json.loads(rendered["json"])
    assert parsed_json == result["data"]["card"]
    hook = json.loads(rendered["hook"])
    assert hook["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert hook["hookSpecificOutput"]["additionalContext"] == rendered["text"]
    assert "decision" not in hook["hookSpecificOutput"]

    again = evidence.envelope_for(
        FIXTURES / "completed-pass-with-metric.json",
        fact_label="checks passed",
        fact_value="2 of 2",
        **DEFAULT_ARGS,
    )
    assert again["data"]["rendered"] == rendered
    assert again["status"] == result["status"]
    assert again["exitCode"] == result["exitCode"]
    assert again["inputHash"] == result["inputHash"]


def test_input_hash_follows_source_content_and_declared_fact():
    a = evidence.envelope_for(FIXTURES / "completed-pass-with-metric.json", **DEFAULT_ARGS)["inputHash"]
    with_fact = evidence.envelope_for(
        FIXTURES / "completed-pass-with-metric.json", fact_label="x", fact_value="y", **DEFAULT_ARGS
    )["inputHash"]
    different_headline = evidence.envelope_for(
        FIXTURES / "completed-pass-with-metric.json",
        card_id="lab.demo",
        headline="OTHER",
        next_step="Continue.",
    )["inputHash"]
    assert len({a, with_fact, different_headline}) == 3


def test_overall_status_is_the_worst_finding_present():
    assert evidence.worst_status([]) == "pass"
    assert (
        evidence.worst_status(
            [evidence.finding("a", "warn", "x"), evidence.finding("b", "unknown", "y")]
        )
        == "unknown"
    )
    with pytest.raises(ValueError):
        evidence.finding("a", "bad", "x")


def test_cli_requires_all_of_source_id_headline_and_next(capsys):
    with pytest.raises(SystemExit) as exit_info:
        evidence.main(["--id", "x", "--headline", "y", "--next", "z"])
    assert exit_info.value.code == 2
    capsys.readouterr()
    code = evidence.main(
        [
            "--source",
            str(FIXTURES / "completed-pass-with-metric.json"),
            "--id",
            "lab.demo",
            "--headline",
            "GATE PASSED",
            "--next",
            "Continue.",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


def test_a_run_writes_nothing_and_makes_no_network_call(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_signage.evidence",
            "--source",
            str(FIXTURES / "completed-pass-with-metric.json"),
            "--id",
            "lab.demo",
            "--headline",
            "GATE PASSED",
            "--next",
            "Continue.",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "pass"
    assert payload["data"]["effects"] == {"writes": "none", "network": "none"}
    assert list(tmp_path.iterdir()) == []


def test_git_sha_marks_a_tree_whose_commit_does_not_describe_the_code(tmp_path):
    assert evidence.git_sha(tmp_path) is None

    def run(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    run("init", "-q")
    run("config", "user.email", "test@example.invalid")
    run("config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("one\n")
    run("add", "-A")
    run("commit", "-qm", "first")
    clean = evidence.git_sha(tmp_path)
    assert clean and len(clean) == 40
    (tmp_path / "a.txt").write_text("two\n")
    assert evidence.git_sha(tmp_path) == "%s-dirty" % clean
