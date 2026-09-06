"""The publication boundary: the publisher that owns `gh`, and the Bash adapter.

Everything here exercises a surface that can actually reach GitHub, driven by
`tests/fake_gh.py`. The properties under test are the ones a prepare-only
design could not offer: that a locally rejected artifact starts no child at all, that
the bytes on the child's stdin are the bytes that were checked, that a failing
child is not rounded up to success, and that success waits for a readback.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_signage import gate, preflight, publish

ROOT = Path(__file__).resolve().parents[1]
FAKE_GH = Path(__file__).resolve().parent / "fake_gh.py"
MATRIX = json.loads((ROOT / "evals" / "publication-boundary.json").read_text(encoding="utf-8"))
CASES = MATRIX["cases"]


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def gh(tmp_path, monkeypatch):
    """An executable stand-in for `gh`, plus the log of what it received."""
    shim = tmp_path / "gh"
    shim.write_text(
        "#!%s\nimport runpy, sys\nsys.argv[0] = 'gh'\nrunpy.run_path(%r, run_name='__main__')\n"
        % (sys.executable, str(FAKE_GH)),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    log = tmp_path / "gh.log"
    log.touch()
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    store = tmp_path / "published.txt"
    monkeypatch.setenv("FAKE_GH_STORE", str(store))
    for name in ("FAKE_GH_FAIL", "FAKE_GH_NO_URL", "FAKE_GH_VIEW_BODY", "FAKE_GH_VIEW_FAIL",
                 "FAKE_GH_MUTATE_PATH"):
        monkeypatch.delenv(name, raising=False)

    class Gh:
        path = str(shim)

        @staticmethod
        def calls():
            return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                    if line.strip().startswith("{")]

        log_path = log
        store_path = store

    return Gh


def body_text(case):
    """Assemble the artifact a case describes, the way a real body reads."""
    parts = ["Summary of the change."]
    spec = case.get("block")
    if spec:
        block = preflight.attribution_block(
            spec["kind"], spec["oversight"],
            spec.get("contributor", preflight.CONTRIBUTOR),
            spec.get("contributor_url", preflight.CONTRIBUTOR_URL),
        )
        wrap = case.get("wrap", "none")
        if wrap == "fence":
            block = "```\n%s\n```" % block
        elif wrap == "comment":
            block = "<!--\n%s\n-->" % block
        parts.extend([block] * case.get("block_count", 1))
    if not case.get("drop_preserved", False):
        parts.extend(case.get("preserve", []))
    return "\n\n".join(parts) + "\n"


def write_body(tmp_path, text, name="body.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def request_for(case, gh_path, **overrides):
    declared = case.get("declare", {"kind": "contribution", "oversight": "active"})
    fields = dict(
        op=case.get("op", "pr-create"),
        target="hermes-labs-ai/agent-signage",
        kind=declared["kind"],
        oversight=declared["oversight"],
        pr=12 if case.get("op") == "pr-edit" else None,
        title=None if case.get("op") == "pr-edit" else "feat: publication boundary",
        preserve=tuple(case.get("preserve", [])),
        gh=gh_path,
    )
    fields.update(overrides)
    return publish.Request(**fields)


def apply_gh_mode(case, monkeypatch):
    mode = case.get("gh_mode", "ok")
    if mode == "fail":
        monkeypatch.setenv("FAKE_GH_FAIL", "7")
    elif mode == "tampered-readback":
        monkeypatch.setenv("FAKE_GH_VIEW_BODY", "something a human never checked")
    elif mode == "view-fails":
        monkeypatch.setenv("FAKE_GH_VIEW_FAIL", "1")
    elif mode == "no-url":
        monkeypatch.setenv("FAKE_GH_NO_URL", "1")


# --------------------------------------------------------------- the matrix

@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c["surface"] == "publish"],
    ids=[c["name"] for c in CASES if c["surface"] == "publish"],
)
def test_publish_matrix(case, tmp_path, gh, monkeypatch, capsys):
    apply_gh_mode(case, monkeypatch)
    text = body_text(case)
    path = write_body(tmp_path, text)
    result = publish.publish(str(path), request_for(case, gh.path))
    printed = capsys.readouterr().out
    expect = case["expect"]

    assert result.exit_code == expect["exit"], printed
    calls = gh.calls()
    assert len(calls) == expect["gh_calls"], calls

    for code in expect.get("codes", []):
        assert code in printed
    if expect.get("no_success"):
        assert "PUBLISHED" not in printed
    if expect.get("child_status") is not None:
        assert result.child_status == expect["child_status"]
        assert str(expect["child_status"]) in printed
    if expect.get("stdin_is_snapshot"):
        writes = [call for call in calls
                  if call["argv"][:2] in (["pr", "create"], ["pr", "edit"])]
        assert len(writes) == 1
        assert writes[0]["stdin_text"] == text
        assert "--body-file" in writes[0]["argv"]
        assert writes[0]["argv"][writes[0]["argv"].index("--body-file") + 1] == "-"
        assert "PUBLISHED" in printed


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c["surface"] == "bash-boundary"],
    ids=[c["name"] for c in CASES if c["surface"] == "bash-boundary"],
)
def test_bash_boundary_matrix(case):
    field = case.get("tool_field", "cmd")
    cwd = "/tmp" if case.get("scope") == "unrelated" else str(ROOT)
    output = gate.run(json.dumps({
        "tool_name": "Bash",
        "cwd": cwd,
        "tool_input": {field: case["command"]},
    }))
    expected = case["expect"]["deny"]
    if expected is None:
        assert output is None
    else:
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "publish pr-%s" % expected in decision["permissionDecisionReason"]


def test_matrix_exercises_both_surfaces_in_both_directions():
    surfaces = {c["surface"] for c in CASES}
    assert surfaces == {"publish", "bash-boundary"}
    published = [c for c in CASES if c["expect"].get("exit") == 0]
    stopped = [c for c in CASES if c["expect"].get("gh_calls") == 0]
    assert len(published) >= 3 and len(stopped) >= 5
    assert any(c["expect"].get("deny") for c in CASES)
    assert any(c["expect"].get("deny") is None for c in CASES if c["surface"] == "bash-boundary")


# ------------------------------------------------------- the ordered contract

def test_create_sends_the_checked_bytes_and_verifies_the_readback(tmp_path, gh, capsys):
    text = "Adds a thing.\n\n%s\n" % preflight.attribution_block("contribution", "active")
    path = write_body(tmp_path, text)
    result = publish.publish(str(path), request_for(
        {"op": "pr-create", "declare": {"kind": "contribution", "oversight": "active"}}, gh.path))
    printed = capsys.readouterr().out

    assert result.exit_code == publish.EXIT_PASS
    send, view = gh.calls()
    assert send["argv"] == [
        "pr", "create", "--repo", "hermes-labs-ai/agent-signage",
        "--title", "feat: publication boundary", "--body-file", "-"]
    assert send["stdin_text"] == text
    assert view["argv"][:3] == ["pr", "view", publish._PR_URL_RE.search(
        "https://github.com/hermes-labs-ai/agent-signage/pull/99").group(0)]
    assert "--json" in view["argv"] and "body" in view["argv"]
    # The path is never handed to gh; only the bytes are.
    assert str(path) not in json.dumps(send["argv"])
    assert "PUBLISHED" in printed and "matched byte for byte" in printed


def test_edit_targets_the_pull_request_number(tmp_path, gh, capsys):
    text = "%s\n" % preflight.attribution_block("review", "none")
    path = write_body(tmp_path, text)
    result = publish.publish(str(path), request_for(
        {"op": "pr-edit", "declare": {"kind": "review", "oversight": "none"}}, gh.path))
    capsys.readouterr()
    assert result.exit_code == publish.EXIT_PASS
    before, send, view = gh.calls()
    assert before["argv"][:3] == ["pr", "view", "12"]
    assert send["argv"] == [
        "pr", "edit", "12", "--repo", "hermes-labs-ai/agent-signage", "--body-file", "-"]
    assert view["argv"][:3] == ["pr", "view", "12"]


@pytest.mark.parametrize("disclosure", [
    "Co-Authored-By: A Maintainer <m@example.com>",
    "Disclosure: Rolando Bosch maintains Little Canary.",
])
def test_edit_preserves_live_conventional_disclosures_without_caller_help(
    tmp_path, gh, capsys, disclosure
):
    gh.store_path.write_text("Old body.\n\n%s\n" % disclosure, encoding="utf-8")
    text = "%s\n" % preflight.attribution_block("review", "none")
    path = write_body(tmp_path, text)

    rejected = publish.publish(str(path), request_for(
        {"op": "pr-edit", "declare": {"kind": "review", "oversight": "none"}}, gh.path))
    printed = capsys.readouterr().out
    assert rejected.exit_code == publish.EXIT_REJECT
    assert "disclosure-dropped" in printed
    assert "only a read-only gh pr view was invoked" in printed
    assert [call["argv"][:2] for call in gh.calls()] == [["pr", "view"]]


def test_edit_with_live_disclosure_intact_publishes(tmp_path, gh, capsys):
    disclosure = "Signed-off-by: Upstream Maintainer <u@example.com>"
    gh.store_path.write_text("Old body.\n\n%s\n" % disclosure, encoding="utf-8")
    text = "%s\n\n%s\n" % (preflight.attribution_block("review", "none"), disclosure)
    path = write_body(tmp_path, text)
    result = publish.publish(str(path), request_for(
        {"op": "pr-edit", "declare": {"kind": "review", "oversight": "none"}}, gh.path))
    capsys.readouterr()
    assert result.exit_code == publish.EXIT_PASS
    assert [call["argv"][:2] for call in gh.calls()] == [
        ["pr", "view"], ["pr", "edit"], ["pr", "view"]]


def test_only_the_snapshot_can_reach_gh(tmp_path, gh, capsys):
    """Mutation resistance, proved rather than argued.

    The body is rewritten on disk after the snapshot is taken and before the
    child runs. Because the snapshot is the only thing downstream sees, and gh
    is given `--body-file -` rather than the path, the mutation cannot reach
    GitHub -- there is no second read for it to land in.
    """
    original = "Original.\n\n%s\n" % preflight.attribution_block("contribution", "active")
    path = write_body(tmp_path, original)
    snap = publish.snapshot(str(path))

    path.write_text("REPLACED after the check, with no attribution at all.\n", encoding="utf-8")

    result = publish.publish_snapshot(snap, request_for(
        {"op": "pr-create", "declare": {"kind": "contribution", "oversight": "active"}}, gh.path))
    capsys.readouterr()
    assert result.exit_code == publish.EXIT_PASS
    send = gh.calls()[0]
    assert send["stdin_text"] == original
    assert "REPLACED" not in send["stdin_text"]
    assert send["stdin_sha256"] == snap.sha256


def test_a_forged_snapshot_cannot_send_different_bytes_than_the_checked_text(gh):
    checked = preflight.attribution_block("contribution", "active")
    data = b"Unchecked bytes with no attribution.\n"
    forged = publish.Snapshot(
        path="/forged/body.md",
        data=data,
        text=checked,
        sha256=__import__("hashlib").sha256(data).hexdigest(),
    )
    with pytest.raises(preflight.PreflightError, match="decoding"):
        publish.publish_snapshot(forged, request_for(
            {"op": "pr-create",
             "declare": {"kind": "contribution", "oversight": "active"}}, gh.path))
    assert gh.calls() == []


@pytest.mark.parametrize("data, message", [
    (b"x\x00y", "NUL"),
    (b"x" * (preflight.MAX_ARTIFACT_BYTES + 1), "exceeds"),
])
def test_a_consistent_but_untrusted_forged_snapshot_is_still_rejected(gh, data, message):
    text = data.decode("utf-8")
    forged = publish.Snapshot(
        path="/forged/body.md",
        data=data,
        text=text,
        sha256=__import__("hashlib").sha256(data).hexdigest(),
    )
    with pytest.raises(preflight.PreflightError, match=message):
        publish.publish_snapshot(forged, request_for(
            {"op": "pr-create",
             "declare": {"kind": "contribution", "oversight": "active"}}, gh.path))
    assert gh.calls() == []


def test_child_time_mutation_still_cannot_change_the_sent_snapshot(
    tmp_path, gh, monkeypatch, capsys
):
    original = "Original.\n\n%s\n" % preflight.attribution_block("contribution", "active")
    path = write_body(tmp_path, original)
    monkeypatch.setenv("FAKE_GH_MUTATE_PATH", str(path))

    result = publish.publish(str(path), request_for(
        {"op": "pr-create", "declare": {"kind": "contribution", "oversight": "active"}},
        gh.path,
    ))
    capsys.readouterr()

    assert result.exit_code == publish.EXIT_PASS
    assert "MUTATED by the child" in path.read_text(encoding="utf-8")
    assert gh.calls()[0]["stdin_text"] == original


def test_a_rejected_artifact_starts_no_child(tmp_path, gh, capsys):
    path = write_body(tmp_path, "No attribution anywhere.\n")
    result = publish.publish(str(path), request_for(
        {"op": "pr-create", "declare": {"kind": "contribution", "oversight": "active"}}, gh.path))
    printed = capsys.readouterr().out
    assert result.exit_code == publish.EXIT_REJECT
    assert gh.calls() == []
    assert result.gh_invocations == []
    assert "gh was not invoked" in printed
    assert "PUBLISHED" not in printed


def test_a_failing_child_is_surfaced_with_its_status(tmp_path, gh, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_GH_FAIL", "7")
    text = "%s\n" % preflight.attribution_block("contribution", "none")
    path = write_body(tmp_path, text)
    result = publish.publish(str(path), request_for(
        {"op": "pr-create", "declare": {"kind": "contribution", "oversight": "none"}}, gh.path))
    printed = capsys.readouterr().out
    assert result.exit_code == publish.EXIT_CHILD
    assert result.child_status == 7
    assert "gh exited 7" in printed
    assert "refusing to publish (simulated)" in printed
    assert "PUBLISHED" not in printed
    assert len(gh.calls()) == 1  # no readback after a failed send


def test_a_timeout_after_child_start_is_reported_as_uncertain(tmp_path, gh, monkeypatch,
                                                               capsys):
    text = "%s\n" % preflight.attribution_block("contribution", "none")
    path = write_body(tmp_path, text)

    def timeout(argv, payload, seconds):
        raise subprocess.TimeoutExpired(argv, seconds)

    monkeypatch.setattr(publish, "_run", timeout)
    result = publish.publish(str(path), request_for(
        {"op": "pr-create", "declare": {"kind": "contribution", "oversight": "none"}},
        gh.path,
    ))
    printed = capsys.readouterr().out
    assert result.exit_code == publish.EXIT_CHILD
    assert "UNCERTAIN" in printed
    assert "may exist" in printed
    assert "Nothing was reverted" in printed
    assert "PUBLISHED" not in printed


def test_missing_create_url_reports_possible_external_effect(tmp_path, gh, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_GH_NO_URL", "1")
    text = "%s\n" % preflight.attribution_block("contribution", "none")
    path = write_body(tmp_path, text)
    result = publish.publish(str(path), request_for(
        {"op": "pr-create", "declare": {"kind": "contribution", "oversight": "none"}},
        gh.path,
    ))
    printed = capsys.readouterr().out
    assert result.exit_code == publish.EXIT_READBACK
    assert "may exist" in printed
    assert "Nothing was reverted" in printed


def test_a_readback_mismatch_is_not_success_and_says_what_is_true(tmp_path, gh, monkeypatch,
                                                                  capsys):
    monkeypatch.setenv("FAKE_GH_VIEW_BODY", "a body nobody checked")
    text = "%s\n" % preflight.attribution_block("contribution", "active")
    path = write_body(tmp_path, text)
    result = publish.publish(str(path), request_for(
        {"op": "pr-create", "declare": {"kind": "contribution", "oversight": "active"}}, gh.path))
    printed = capsys.readouterr().out
    assert result.exit_code == publish.EXIT_READBACK
    assert "MISMATCH" in printed
    assert "PUBLISHED" not in printed
    # It must not pretend the pull request does not exist either.
    assert "Nothing was reverted" in printed
    assert "/pull/99" in printed


def test_the_sign_is_emitted_before_the_child_runs(tmp_path, gh):
    """Ordering, observed in one file both the publisher and the child append to."""
    text = "%s\n" % preflight.attribution_block("contribution", "active")
    path = write_body(tmp_path, text)
    with open(gh.log_path, "a", encoding="utf-8") as stream:
        result = publish.publish(
            str(path),
            request_for({"op": "pr-create",
                         "declare": {"kind": "contribution", "oversight": "active"}}, gh.path),
            stream=stream,
        )
    assert result.exit_code == publish.EXIT_PASS

    lines = gh.log_path.read_text(encoding="utf-8").splitlines()
    sign_at = next(i for i, line in enumerate(lines) if line.startswith("PUBLIC UPDATE"))
    child_at = next(i for i, line in enumerate(lines)
                    if line.startswith("{") and json.loads(line)["argv"][:2] == ["pr", "create"])
    assert sign_at < child_at
    assert "sha256" in lines[sign_at] and "hermes-labs-ai/agent-signage" in lines[sign_at]
    assert "declared oversight 'active'" in lines[sign_at]


def test_the_sign_reports_the_declaration_not_a_verification(tmp_path, gh):
    text = "%s\n" % preflight.attribution_block("contribution", "none")
    snap = publish.snapshot(str(write_body(tmp_path, text)))
    sign = publish.action_sign(snap, request_for(
        {"op": "pr-create", "declare": {"kind": "contribution", "oversight": "none"}}, gh.path))
    assert "declared oversight 'none'" in sign
    assert "verified" not in sign.lower()


# --------------------------------------------------------------- the snapshot

def test_snapshot_refuses_symlinks_directories_and_oversized_bodies(tmp_path):
    body = write_body(tmp_path, "x\n")
    link = tmp_path / "link.md"
    link.symlink_to(body)
    with pytest.raises(preflight.PreflightError, match="cannot open body file"):
        publish.snapshot(str(link))
    with pytest.raises(preflight.PreflightError, match="regular file"):
        publish.snapshot(str(tmp_path))
    with pytest.raises(preflight.PreflightError, match="must be absolute"):
        publish.snapshot("body.md")

    big = tmp_path / "big.md"
    big.write_bytes(b"x" * (preflight.MAX_ARTIFACT_BYTES + 1))
    with pytest.raises(preflight.PreflightError, match="exceeds"):
        publish.snapshot(str(big))

    for name, payload, message in (
        ("nul.md", b"a\x00b", "NUL"),
        ("bad.md", b"\xff\xfe", "not valid UTF-8"),
        ("empty.md", b"", "empty"),
    ):
        path = tmp_path / name
        path.write_bytes(payload)
        with pytest.raises(preflight.PreflightError, match=message):
            publish.snapshot(str(path))


def test_snapshot_refuses_a_fifo_without_blocking(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are not available on this platform")
    fifo = tmp_path / "body.pipe"
    os.mkfifo(fifo)
    result = subprocess.run(
        [sys.executable, "-m", "agent_signage", "preflight", "--body-file", str(fifo),
         "--kind", "contribution", "--oversight", "none"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": os.environ.get("PATH", "")},
        timeout=2,
    )
    assert result.returncode == 2
    assert "regular file" in result.stderr


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"op": "pr-comment"}, "operation must be one of"),
        ({"target": "not-a-repo"}, "owner/repository"),
        ({"target": "o/r; rm -rf /"}, "owner/repository"),
        ({"op": "pr-create", "title": None}, "--title"),
        ({"op": "pr-create", "title": "--body-file=/etc/passwd"}, "--title"),
        ({"op": "pr-create", "title": "ok\rINJECTED"}, "--title"),
        ({"op": "pr-create", "title": "ok\tINJECTED"}, "--title"),
        ({"op": "pr-create", "base": "--upload-pack=x"}, "--base"),
        ({"op": "pr-create", "head": "--upload-pack=x"}, "--head"),
        ({"op": "pr-edit", "pr": 0, "title": None}, "--pr"),
        ({"kind": "release"}, "--kind"),
        ({"oversight": "spot-check"}, "--oversight"),
    ],
)
def test_request_validation_refuses_what_it_cannot_state(overrides, message):
    case = {"op": overrides.get("op", "pr-create"),
            "declare": {"kind": "contribution", "oversight": "active"}}
    with pytest.raises(preflight.PreflightError, match=message):
        publish.validate_request(request_for(case, "gh", **overrides))


def test_pr_comment_is_not_a_supported_operation():
    assert publish.OPS == ("pr-create", "pr-edit")
    assert "pr-comment" not in publish.OPS
    assert gate.GUARDED_SUBCOMMANDS == ("create", "edit")


# ------------------------------------------------------------- the CLI surface

def _cli(*args, stdin=None):
    env = {"PYTHONPATH": str(ROOT / "src"), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    for name in ("FAKE_GH_LOG", "FAKE_GH_STORE"):
        if name in os.environ:
            env[name] = os.environ[name]
    return subprocess.run([sys.executable, "-m", "agent_signage"] + list(args),
                          input=stdin, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env)


def test_oversight_has_no_default_on_any_command(tmp_path):
    body = write_body(tmp_path, "x\n")
    for args in (
        ["preflight", "--body-file", str(body), "--kind", "contribution"],
        ["attribution", "--kind", "contribution"],
        ["publish", "pr-create", "--body-file", str(body), "--target", "o/r",
         "--kind", "contribution", "--title", "t"],
    ):
        result = _cli(*args)
        assert result.returncode != 0
        assert "--oversight" in result.stderr


def test_attribution_command_emits_a_block_the_checker_accepts():
    result = _cli("attribution", "--kind", "review", "--oversight", "active")
    assert result.returncode == 0
    assert result.stdout.strip() == preflight.attribution_block("review", "active")
    assert "[Hermes Labs](https://hermes-labs.ai)\u2019 autonomous triage" in result.stdout
    assert "responsible human reviewer" in result.stdout


def test_gate_subcommand_denies_and_stays_silent_over_stdin():
    denied = _cli("gate", stdin=json.dumps(
        {"tool_name": "Bash", "tool_input": {
            "command": "gh pr create --repo hermes-labs-ai/agent-signage --title t"}}))
    assert denied.returncode == 0
    decision = json.loads(denied.stdout)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert "agent_signage publish pr-create" in decision["permissionDecisionReason"]
    assert "|" not in decision["permissionDecisionReason"]

    for payload in (
        json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls -la"}}),
        json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/tmp/gh pr create"}}),
        "not json at all",
        "[1,2,3]",
        "",
        json.dumps({"tool_name": "Bash", "tool_input": {"command": 42}}),
        json.dumps({"tool_name": "Bash"}),
    ):
        quiet = _cli("gate", stdin=payload)
        assert quiet.returncode == 0, payload
        assert quiet.stdout == "", payload


def test_gate_accepts_codex_cmd_shape_and_avoids_search_value_false_positive():
    denied = gate.run(json.dumps(
        {"tool_name": "Bash", "tool_input": {
            "cmd": "gh pr edit 12 --repo hermes-labs-ai/r --body-file body.md"}}))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"

    assert gate.guarded_subcommand("gh pr list --search pr --label create") is None
    quiet = gate.run(json.dumps(
        {"tool_name": "Bash", "tool_input": {"cmd": "gh pr list --search pr --label create"}}))
    assert quiet is None
    assert gate.guarded_subcommand("gh\\\n pr create") == "create"
    multiline = gate.run(json.dumps({
        "tool_name": "Bash",
        "cwd": str(ROOT),
        "tool_input": {"cmd": "cd /tmp\ngh pr create --repo hermes-labs-ai/r --title t"},
    }))
    assert multiline["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert gate.guarded_subcommand("echo `gh pr create --title t`") == "create"
    assert gate.guarded_subcommand(
        "gh pr create --title --help --body x") == "create"
    assert gate.guarded_subcommand(
        "cat <<'EOF'\ngh pr create --repo hermes-labs-ai/r\nEOF") is None
    assert gate.guarded_subcommand(
        "cat <<EOF\n$(gh pr create --title t)\nEOF") == "create"
    assert gate.guarded_subcommand(
        "cat <<-EOF\n\t`gh pr create --title t`\n\tEOF") == "create"
    assert gate.guarded_subcommand('echo "$(gh pr create --title t)"') == "create"
    assert gate.guarded_subcommand('x="$(gh pr create --title t)"') == "create"
    assert gate.guarded_subcommand('echo "`gh pr create --title t`"') == "create"


@pytest.mark.parametrize("command", [
    "eval 'gh pr create --title x'",
    "bash -c 'gh pr create --title x'",
    "zsh -lc 'gh pr edit 3 --body x'",
    "env -S 'gh pr create --title x'",
    "env --split-string='gh pr edit 3 --body x'",
    "sudo env -S 'gh pr create --title x'",
    "$(command -v gh) pr create --title x",
    "gh_cmd=$(command -v gh); \"$gh_cmd\" pr edit 3 --body x",
])
def test_gate_denies_indirect_publication_from_scoped_repo(monkeypatch, command):
    monkeypatch.setattr(gate, "_cwd_repo", lambda cwd: "hermes-labs-ai/agent-signage")
    denied = gate.run(json.dumps({
        "tool_name": "Bash", "cwd": "/a/hermes/repo", "tool_input": {"cmd": command},
    }))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", [
    "eval 'echo ok'",
    "bash -c 'gh pr view 3'",
    "env -S 'gh pr view 3'",
    "$(command -v gh) pr list",
    "echo $(command -v gh) pr create --title x",
])
def test_gate_keeps_nonpublication_indirection_silent(monkeypatch, command):
    monkeypatch.setattr(gate, "_cwd_repo", lambda cwd: "hermes-labs-ai/agent-signage")
    quiet = gate.run(json.dumps({
        "tool_name": "Bash", "cwd": "/a/hermes/repo", "tool_input": {"cmd": command},
    }))
    assert quiet is None


def test_gate_scope_does_not_force_hermes_attribution_on_unrelated_prs(monkeypatch):
    monkeypatch.setattr(gate, "_cwd_repo", lambda cwd: "someone/personal")
    personal = gate.run(json.dumps({
        "tool_name": "Bash",
        "cwd": "/a/personal/repo",
        "tool_input": {"cmd": "gh pr create --repo someone/private-repo --title t"},
    }))
    assert personal is None

    metadata = gate.run(json.dumps({
        "tool_name": "Bash",
        "cwd": "/a/personal/repo",
        "tool_input": {"cmd": "gh pr edit 12 --repo hermes-labs-ai/r --add-label bug"},
    }))
    assert metadata is None

    monkeypatch.setattr(gate, "_cwd_repo", lambda cwd: "hermes-labs-ai/agent-signage")
    implicit = gate.run(json.dumps({
        "tool_name": "Bash",
        "cwd": "/a/hermes/repo",
        "tool_input": {"cmd": "gh pr create --title t"},
    }))
    assert implicit["hookSpecificOutput"]["permissionDecision"] == "deny"

    monkeypatch.setattr(gate, "_cwd_repo", lambda cwd: "roli-lpci/hermes-infra")
    upstream = gate.run(json.dumps({
        "tool_name": "Bash",
        "cwd": "/a/hermes/infra",
        "tool_input": {"cmd": "gh pr create --repo samvitgersappa/Orbit --title t"},
    }))
    assert upstream["hookSpecificOutput"]["permissionDecision"] == "deny"

    monkeypatch.setattr(gate, "_cwd_repo", lambda cwd: "someone/personal")
    unrelated = gate.run(json.dumps({
        "tool_name": "Bash",
        "cwd": "/a/personal/repo",
        "tool_input": {"cmd": "gh pr create --title t"},
    }))
    assert unrelated is None

    # Untokenisable lines: the raw fallback honours an explicit scoped target
    # and stays silent for a personal one, exactly like the parsed path.
    unparseable_scoped = gate.run(json.dumps({
        "tool_name": "Bash",
        "cwd": "/a/personal/repo",
        "tool_input": {"cmd": "gh pr create --repo hermes-labs-ai/r --title \"t"},
    }))
    assert unparseable_scoped["hookSpecificOutput"]["permissionDecision"] == "deny"
    unparseable_personal = gate.run(json.dumps({
        "tool_name": "Bash",
        "cwd": "/a/personal/repo",
        "tool_input": {"cmd": "gh pr create --repo someone/private --title \"t"},
    }))
    assert unparseable_personal is None


def test_codex_gate_installer_preserves_existing_hooks_and_is_idempotent(tmp_path):
    hooks = tmp_path / "hooks.json"
    existing_group = {
        "matcher": "^Bash$",
        "hooks": [{
            "type": "command",
            "command": "/opt/hermes-gate hook --event pre-tool",
            "timeout": 5,
            "statusMessage": "Checking Hermes Gate receipts",
        }],
    }
    original = {
        "description": "keep this",
        "hooks": {"PreToolUse": [existing_group], "Stop": [{"hooks": []}]},
    }
    hooks.write_text(json.dumps(original), encoding="utf-8")

    first = _cli("install-publication-gate", "--hooks", str(hooks),
                 "--command", "/opt/agent-signage gate", "--timeout", "5")
    assert first.returncode == 0, first.stderr
    installed = json.loads(hooks.read_text(encoding="utf-8"))
    assert installed["description"] == "keep this"
    assert installed["hooks"]["PreToolUse"][0] == existing_group
    assert installed["hooks"]["Stop"] == original["hooks"]["Stop"]
    agent_groups = [group for group in installed["hooks"]["PreToolUse"]
                    if any(handler.get("command") == "/opt/agent-signage gate"
                           for handler in group.get("hooks", []))]
    assert agent_groups == [{
        "matcher": "^Bash$",
        "hooks": [{
            "type": "command",
            "command": "/opt/agent-signage gate",
            "timeout": 5,
            "statusMessage": "Checking public contribution attribution",
        }],
    }]
    backups = list(tmp_path.glob("hooks.json.bak-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == original

    second = _cli("install-publication-gate", "--hooks", str(hooks),
                  "--command", "/opt/agent-signage gate")
    assert second.returncode == 0, second.stderr
    assert "already installed" in second.stdout
    assert json.loads(hooks.read_text(encoding="utf-8")) == installed
    assert list(tmp_path.glob("hooks.json.bak-*")) == backups


def test_codex_gate_installer_refuses_malformed_config_without_replacing_it(tmp_path):
    hooks = tmp_path / "hooks.json"
    before = b'{"hooks":{"PreToolUse":{}}}\n'
    hooks.write_bytes(before)
    result = _cli("install-publication-gate", "--hooks", str(hooks),
                  "--command", "/opt/agent-signage gate")
    assert result.returncode == 1
    assert "is not a list" in result.stderr
    assert hooks.read_bytes() == before
    assert list(tmp_path.glob("hooks.json.bak-*")) == []


def test_codex_gate_installer_refuses_a_symlink_without_changing_its_target(tmp_path):
    target = tmp_path / "managed-hooks.json"
    before = b'{"description":"managed","hooks":{}}\n'
    target.write_bytes(before)
    hooks = tmp_path / "hooks.json"
    hooks.symlink_to(target)

    result = _cli("install-publication-gate", "--hooks", str(hooks),
                  "--command", "/opt/agent-signage gate")
    assert result.returncode == 1
    assert "symlink" in result.stderr
    assert hooks.is_symlink()
    assert target.read_bytes() == before


def test_codex_gate_installer_does_not_dedupe_a_command_under_the_wrong_matcher(tmp_path):
    hooks = tmp_path / "hooks.json"
    wrong = {
        "matcher": "^Read$",
        "hooks": [{"type": "command", "command": "/opt/agent-signage gate"}],
    }
    hooks.write_text(json.dumps({"hooks": {"PreToolUse": [wrong]}}), encoding="utf-8")

    result = _cli("install-publication-gate", "--hooks", str(hooks),
                  "--command", "/opt/agent-signage gate")
    assert result.returncode == 0, result.stderr
    groups = json.loads(hooks.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert groups[0] == wrong
    assert len(groups) == 2
    assert groups[1]["matcher"] == "^Bash$"
    assert groups[1]["hooks"][0]["type"] == "command"


# ------------------------------------ the passive hook is untouched by any of it

def test_the_generic_hook_is_unchanged_and_cannot_deny():
    from agent_signage import hook

    source = (ROOT / "src" / "agent_signage" / "hook.py").read_text(encoding="utf-8")
    for name in ("preflight", "publish", "gate", "permissionDecision", "deny"):
        assert name not in source
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "gh pr create"}})
    assert hook.run(payload) is None
    assert "deny" not in json.dumps(hook.build_output(["t"]))


def test_the_boundary_registers_no_sign():
    from agent_signage import signs

    assert not any("preflight" in name or "publish" in name or "gate" in name
                   for name in signs.registered())


def test_edit_pre_read_failure_is_a_child_failure_with_no_update(tmp_path, gh, monkeypatch,
                                                                 capsys):
    # The read-only pre-read failed, so nothing was mutated. That is a failed
    # child (exit 3), not an unverified publication (exit 4).
    monkeypatch.setenv("FAKE_GH_VIEW_FAIL", "1")
    text = "%s\n" % preflight.attribution_block("review", "none")
    path = write_body(tmp_path, text)
    result = publish.publish(str(path), request_for(
        {"op": "pr-edit", "declare": {"kind": "review", "oversight": "none"}}, gh.path))
    printed = capsys.readouterr().out
    assert result.exit_code == publish.EXIT_CHILD
    assert result.child_status == 1
    assert "FAILED" in printed
    assert "No update was attempted" in printed
    assert "PUBLISHED" not in printed
    assert [call["argv"][:2] for call in gh.calls()] == [["pr", "view"]]


def test_gate_failure_fallback_honours_an_explicit_scoped_target(monkeypatch, capsys):
    import io

    def boom(raw):
        raise RuntimeError("simulated adapter failure")

    monkeypatch.setattr(gate, "run", boom)
    monkeypatch.setattr(gate, "_cwd_repo", lambda cwd: "someone/personal")

    def decision(cmd):
        payload = json.dumps({"tool_name": "Bash", "cwd": "/a/personal/repo",
                              "tool_input": {"cmd": cmd}})
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        assert gate.main() == 0
        out = capsys.readouterr().out
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else None

    # `run` would deny an explicit Hermes target from any cwd; the fallback must too.
    assert decision("gh pr create --repo hermes-labs-ai/r --title t") == "deny"
    assert decision("make && gh pr create --repo hermes-labs-ai/r --title t") == "deny"
    assert decision("gh pr edit 3 -R hermes-labs-ai/r --body x") == "deny"
    # And it stays silent for a personal target, exactly like `run`.
    assert decision("gh pr create --repo someone/private --title t") is None
    assert decision("gh pr create --title t") is None

    # A Hermes work context denies the plainest payload, whose command follows a
    # JSON quote rather than whitespace.
    monkeypatch.setattr(gate, "_cwd_repo", lambda cwd: "hermes-labs-ai/agent-signage")
    assert decision("gh pr create --title t") == "deny"
    assert decision("gh pr edit 3 --body x") == "deny"
    assert decision("gh pr view 3") is None
