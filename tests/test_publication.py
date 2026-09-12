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
                 "FAKE_GH_MUTATE_PATH", "FAKE_GH_LOGIN", "FAKE_GH_COMMENT_LOGIN",
                 "FAKE_GH_COMMENT_ISSUE_URL"):
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
    op = case.get("op", "pr-create")
    fields = dict(
        op=op,
        target="hermes-labs-ai/agent-signage",
        kind=declared["kind"],
        oversight=declared["oversight"],
        pr=12 if op == "pr-edit" else None,
        title="feat: publication boundary" if op == "pr-create" else None,
        preserve=tuple(case.get("preserve", [])),
        gh=gh_path,
        issue=7 if op in publish.ISSUE_OPS else None,
        comment=4242 if op == "issue-comment-edit" else None,
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
        writes = [call for call in calls if _is_write(call["argv"])]
        assert len(writes) == 1
        assert writes[0]["stdin_text"] == text
        argv = writes[0]["argv"]
        if argv[0] == "api":
            assert argv[-2:] == ["-F", "body=@-"]
        else:
            assert "--body-file" in argv
            assert argv[argv.index("--body-file") + 1] == "-"
        assert "PUBLISHED" in printed


def _is_write(argv):
    return (argv[:2] in (["pr", "create"], ["pr", "edit"])
            or (argv[:1] == ["api"] and "--method" in argv
                and argv[argv.index("--method") + 1] in ("POST", "PATCH")))


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c["surface"] == "bash-boundary"],
    ids=[c["name"] for c in CASES if c["surface"] == "bash-boundary"],
)
def test_bash_boundary_matrix(case, monkeypatch):
    # Matrix cases run in an external/unknown working context unless they say
    # otherwise: the detection cases are about the shape, not the checkout.
    internal = case.get("context") == "internal"
    monkeypatch.setattr(gate, "_cwd_is_internal", lambda cwd: internal)
    field = case.get("tool_field", "cmd")
    output = gate.run(json.dumps({
        "tool_name": "Bash",
        "cwd": str(ROOT),
        "tool_input": {field: case["command"]},
    }))
    expected = case["expect"]["deny"]
    if expected is None:
        assert output is None
    else:
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "publish %s" % gate.DENY_OPS[expected] in decision["permissionDecisionReason"]


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


def test_supported_operations_are_exact():
    assert publish.OPS == ("pr-create", "pr-edit", "issue-comment-create", "issue-comment-edit")
    assert "pr-comment" not in publish.OPS
    assert gate.GUARDED_SUBCOMMANDS == ("create", "edit", "comment")


# The openai/codex#42311 regression: a direct external issue comment was
# neither guarded by the Bash adapter nor publishable through the checked path.
OBSERVED_ISSUE_COMMENT = "gh issue comment 42311 --repo openai/codex --body-file body.md"


def test_observed_direct_external_issue_comment_is_denied_and_names_the_publisher(monkeypatch):
    monkeypatch.setattr(gate, "_cwd_is_internal", lambda cwd: False)
    output = gate.run(json.dumps({
        "tool_name": "Bash", "cwd": "/a/checkout",
        "tool_input": {"command": OBSERVED_ISSUE_COMMENT},
    }))
    assert output is not None, "direct external gh issue comment was not guarded"
    decision = output["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "publish issue-comment-create" in decision["permissionDecisionReason"]


def test_issue_comments_have_a_checked_publisher_operation():
    assert "issue-comment-create" in publish.OPS
    assert "issue-comment-edit" in publish.OPS


def _issue_request(gh_path, op="issue-comment-create", **overrides):
    fields = dict(op=op, target="hermes-labs-ai/agent-signage", kind="contribution",
                  oversight="none", issue=7, gh=gh_path,
                  comment=4242 if op == "issue-comment-edit" else None)
    fields.update(overrides)
    return publish.Request(**fields)


def test_issue_comment_create_sends_the_snapshot_and_verifies_by_comment_id(
    tmp_path, gh, capsys
):
    text = "Repro notes.\n\n%s\n" % preflight.attribution_block("contribution", "none")
    path = write_body(tmp_path, text)
    result = publish.publish(str(path), _issue_request(gh.path))
    printed = capsys.readouterr().out

    assert result.exit_code == publish.EXIT_PASS, printed
    send, view = gh.calls()
    assert send["argv"] == ["api", "--method", "POST",
                            "repos/hermes-labs-ai/agent-signage/issues/7/comments",
                            "-F", "body=@-"]
    assert send["stdin_text"] == text
    assert str(path) not in json.dumps(send["argv"])
    assert view["argv"] == ["api", "repos/hermes-labs-ai/agent-signage/issues/comments/4242"]
    assert view["stdin_len"] == 0
    assert result.url == "https://github.com/hermes-labs-ai/agent-signage/issues/7#issuecomment-4242"
    assert "PUBLISHED" in printed and "matched byte for byte" in printed
    assert "hermes-labs-ai/agent-signage#7 (new issue comment)" in printed


def test_issue_comment_edit_pre_reads_patches_and_reads_back_the_concrete_id(
    tmp_path, gh, capsys
):
    text = "%s\n" % preflight.attribution_block("review", "none")
    path = write_body(tmp_path, text)
    result = publish.publish(str(path), _issue_request(
        gh.path, op="issue-comment-edit", kind="review"))
    capsys.readouterr()
    assert result.exit_code == publish.EXIT_PASS
    before, send, view = gh.calls()
    endpoint = "repos/hermes-labs-ai/agent-signage/issues/comments/4242"
    assert before["argv"] == ["api", endpoint]
    assert send["argv"] == ["api", "--method", "PATCH", endpoint, "-F", "body=@-"]
    assert send["stdin_text"] == text
    assert view["argv"] == ["api", endpoint]


def test_issue_comment_edit_preserves_live_disclosures(tmp_path, gh, capsys):
    disclosure = "Disclosure: Rolando Bosch maintains Little Canary."
    gh.store_path.write_text("Old comment.\n\n%s\n" % disclosure, encoding="utf-8")
    path = write_body(tmp_path, "%s\n" % preflight.attribution_block("review", "none"))
    result = publish.publish(str(path), _issue_request(
        gh.path, op="issue-comment-edit", kind="review"))
    printed = capsys.readouterr().out
    assert result.exit_code == publish.EXIT_REJECT
    assert "disclosure-dropped" in printed
    assert "only a read-only gh api comment read was invoked" in printed
    assert [call["argv"] for call in gh.calls()] == [
        ["api", "repos/hermes-labs-ai/agent-signage/issues/comments/4242"]]


def test_issue_comment_edit_refuses_a_comment_on_another_issue(tmp_path, gh, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_GH_COMMENT_ISSUE_URL",
                       "https://api.github.com/repos/hermes-labs-ai/agent-signage/issues/8")
    path = write_body(tmp_path, "%s\n" % preflight.attribution_block("contribution", "none"))
    result = publish.publish(str(path), _issue_request(gh.path, op="issue-comment-edit"))
    printed = capsys.readouterr().out
    assert result.exit_code == publish.EXIT_REJECT
    assert "comment-target-mismatch" in printed
    assert len(gh.calls()) == 1


def test_issue_comment_rejected_attribution_starts_no_child(tmp_path, gh, capsys):
    path = write_body(tmp_path, "Generic AI disclosure only.\n")
    result = publish.publish(str(path), _issue_request(gh.path))
    printed = capsys.readouterr().out
    assert result.exit_code == publish.EXIT_REJECT
    assert gh.calls() == [] and "attribution-missing" in printed


@pytest.mark.parametrize("mode, env, exit_code, marker", [
    ("no-json", {"FAKE_GH_NO_URL": "1"}, publish.EXIT_READBACK, "may exist"),
    ("tampered", {"FAKE_GH_VIEW_BODY": "unchecked"}, publish.EXIT_READBACK, "MISMATCH"),
    ("unreadable", {"FAKE_GH_VIEW_FAIL": "1"}, publish.EXIT_READBACK, "UNVERIFIED"),
    ("fails", {"FAKE_GH_FAIL": "7"}, publish.EXIT_CHILD, "gh exited 7"),
])
def test_issue_comment_create_never_rounds_up_an_unverified_write(
    tmp_path, gh, monkeypatch, capsys, mode, env, exit_code, marker
):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    path = write_body(tmp_path, "%s\n" % preflight.attribution_block("contribution", "none"))
    result = publish.publish(str(path), _issue_request(gh.path))
    printed = capsys.readouterr().out
    assert result.exit_code == exit_code, printed
    assert marker in printed
    assert "PUBLISHED" not in printed and "Nothing was reverted" in printed


def test_issue_comment_selection_binds_account_host_and_comment_author(
    tmp_path, gh, monkeypatch, capsys
):
    from agent_signage import contribution

    text = "Notes.\n\n%s\n" % contribution.footer("unspecified")
    path = write_body(tmp_path, text)
    request = _issue_request(gh.path, selection="unspecified", target="openai/codex", issue=42311)

    monkeypatch.setenv("FAKE_GH_LOGIN", "someone-else")
    assert publish.publish(str(path), request).exit_code == publish.EXIT_REJECT
    assert [call["argv"] for call in gh.calls()] == [
        ["api", "--hostname", "github.com", "user", "--jq", ".login"]]

    monkeypatch.delenv("FAKE_GH_LOGIN")
    gh.log_path.write_text("", encoding="utf-8")
    assert publish.publish(str(path), request).exit_code == publish.EXIT_PASS
    calls = [call["argv"] for call in gh.calls()]
    assert all(argv[:3] == ["api", "--hostname", "github.com"] for argv in calls)
    assert calls[1][3:] == ["--method", "POST", "repos/openai/codex/issues/42311/comments",
                            "-F", "body=@-"]

    monkeypatch.setenv("FAKE_GH_COMMENT_LOGIN", "someone-else")
    assert publish.publish(str(path), request).exit_code == publish.EXIT_READBACK
    assert "authored by 'someone-else'" in capsys.readouterr().out


@pytest.mark.parametrize("overrides, message", [
    ({"issue": None}, "--issue"),
    ({"issue": 0}, "--issue"),
    ({"issue": True}, "--issue"),
    ({"op": "issue-comment-edit", "comment": None}, "--comment"),
    ({"op": "issue-comment-edit", "comment": 0}, "--comment"),
    ({"op": "issue-comment-edit", "comment": 2 ** 63}, "--comment"),
    ({"comment": 5}, "--comment applies only"),
    ({"target": "../agent-signage"}, "owner/repository"),
    ({"target": "openai/.."}, "owner/repository"),
    ({"title": "t"}, "do not apply"),
    ({"pr": 3}, "do not apply"),
    ({"op": "pr-edit", "pr": 3}, "apply only to issue comments"),
])
def test_issue_comment_request_validation(overrides, message):
    with pytest.raises(preflight.PreflightError, match=message):
        publish.validate_request(_issue_request("gh", **overrides))


def test_issue_comment_cli_requires_a_concrete_comment_id_for_edit(tmp_path):
    body = write_body(tmp_path, "x\n")
    result = _cli("publish", "issue-comment-edit", "--body-file", str(body), "--target", "o/r",
                  "--issue", "3", "--kind", "contribution", "--oversight", "none")
    assert result.returncode == publish.EXIT_INPUT
    assert "--comment" in result.stderr


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
            "command": "gh pr create --repo someone/upstream --title t"}}))
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
        json.dumps({"tool_name": "Bash", "tool_input": {
            "command": "gh pr create --repo hermes-labs-ai/agent-signage --title t"}}),
    ):
        quiet = _cli("gate", stdin=payload)
        assert quiet.returncode == 0, payload
        assert quiet.stdout == "", payload


def test_gate_accepts_codex_cmd_shape_and_avoids_search_value_false_positive():
    denied = gate.run(json.dumps(
        {"tool_name": "Bash", "tool_input": {
            "cmd": "gh pr edit 12 --repo someone/r --body-file body.md"}}))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"

    assert gate.guarded_subcommand("gh pr list --search pr --label create") is None
    quiet = gate.run(json.dumps(
        {"tool_name": "Bash", "tool_input": {"cmd": "gh pr list --search pr --label create"}}))
    assert quiet is None
    assert gate.guarded_subcommand("gh\\\n pr create") == "create"
    multiline = gate.run(json.dumps({
        "tool_name": "Bash",
        "cwd": str(ROOT),
        "tool_input": {"cmd": "cd /tmp\ngh pr create --repo someone/r --title t"},
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
def test_gate_denies_indirect_publication_from_external_context(monkeypatch, command):
    monkeypatch.setattr(gate, "_cwd_is_internal", lambda cwd: False)
    denied = gate.run(json.dumps({
        "tool_name": "Bash", "cwd": "/a/fork/repo", "tool_input": {"cmd": command},
    }))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    # The same indirection is internal work in a clearly internal checkout.
    monkeypatch.setattr(gate, "_cwd_is_internal", lambda cwd: True)
    assert gate.run(json.dumps({
        "tool_name": "Bash", "cwd": "/a/hermes/repo", "tool_input": {"cmd": command},
    })) is None


@pytest.mark.parametrize("command", [
    "eval 'echo ok'",
    "bash -c 'gh pr view 3'",
    "env -S 'gh pr view 3'",
    "$(command -v gh) pr list",
    "echo $(command -v gh) pr create --title x",
])
def test_gate_keeps_nonpublication_indirection_silent(monkeypatch, command):
    monkeypatch.setattr(gate, "_cwd_is_internal", lambda cwd: False)
    quiet = gate.run(json.dumps({
        "tool_name": "Bash", "cwd": "/a/hermes/repo", "tool_input": {"cmd": command},
    }))
    assert quiet is None


def _gate_decision(command, cwd_internal):
    output = gate.run(json.dumps({
        "tool_name": "Bash", "cwd": "/a/checkout", "tool_input": {"cmd": command},
    }))
    return output["hookSpecificOutput"]["permissionDecision"] if output else None


@pytest.mark.parametrize("command,cwd_internal,expected", [
    # An explicit hermes-labs-ai/* target is internal work from any checkout.
    ("gh pr create --repo hermes-labs-ai/r --title t", False, None),
    ("gh pr create -R hermes-labs-ai/r --title t", False, None),
    ("gh pr create --repo=https://github.com/hermes-labs-ai/r.git --title t", False, None),
    ("gh pr edit 12 -Rhermes-labs-ai/r --body x", False, None),
    ("GH_REPO=hermes-labs-ai/r gh pr create --title t", False, None),
    ("gh pr edit https://github.com/hermes-labs-ai/r/pull/3 --body x", False, None),
    # An explicit non-Hermes target is external, even from an internal checkout.
    ("gh pr create --repo someone/upstream --title t", True, "deny"),
    ("gh pr create --repo roli-lpci/personal-fork --title t", True, "deny"),
    ("gh pr edit 3 -R someone/upstream --body-file b.md", True, "deny"),
    ("GH_REPO=someone/upstream gh pr create --title t", True, "deny"),
    ("gh pr edit https://github.com/someone/upstream/pull/3 --body x", True, "deny"),
    ("gh pr edit https://github.com/someone/upstream/pull/3/files --body x", True, "deny"),
    ("gh pr edit https://ghe.example.com/hermes-labs-ai/r/pull/3 --body x", True, "deny"),
    ("gh pr create --repo ghe.example.com/hermes-labs-ai/r --title t", True, "deny"),
    ("gh pr create --repo hermes-labs-ai/r --repo someone/upstream --title t", True, "deny"),
    ("unset GH_REPO; gh pr create --title t", True, "deny"),
    # A value of another option names no target.
    ("gh pr create --title --repo --body x", True, None),
    ("gh pr create --title '--repo hermes-labs-ai/r' --body x", False, "deny"),
    # Absent target: the working checkout decides, conservatively.
    ("gh pr create --title t", True, None),
    ("gh pr edit 3 --body x", True, None),
    ("gh pr create --title t", False, "deny"),
    ("gh pr edit 3 --body-file b.md", False, "deny"),
    ("cd ../upstream && gh pr create --title t", True, "deny"),
    ("GIT_DIR=/x/.git gh pr create --title t", True, "deny"),
    ("env -C ../upstream gh pr create --title t", True, "deny"),
    ("sudo -D ../upstream gh pr edit 3 --body x", True, "deny"),
    ("(pushd ../upstream && gh pr create --title t)", True, "deny"),
    # Read-only and metadata-only commands are silent everywhere.
    ("gh pr view 3 --repo someone/upstream", False, None),
    ("gh pr list", False, None),
    ("gh pr edit 3 --repo someone/upstream --add-label bug", False, None),
    ("gh pr edit 3 --add-reviewer alice", False, None),
    # Untokenisable lines mirror the parsed boundary.
    ("gh pr create --repo hermes-labs-ai/r --title \"t", False, None),
    ("gh pr create --repo someone/private --title \"t", True, "deny"),
    ("gh pr create --title \"t", True, None),
    ("gh pr create --title \"t", False, "deny"),
    ("$(command -v gh) pr create --repo hermes-labs-ai/r --title t", False, None),
    ("$(command -v gh) pr create --repo someone/upstream --title t", True, "deny"),
    # Issue and PR conversation comments with a supplied body.
    ("gh issue comment 42311 --repo openai/codex --body-file body.md", True, "deny"),
    ("gh issue comment 3 -R someone/upstream -b hi", True, "deny"),
    ("gh issue comment 3 --repo someone/upstream --body=hi", True, "deny"),
    ("gh issue comment 3 --repo someone/upstream --edit-last -F -", True, "deny"),
    ("gh issue comment https://github.com/someone/upstream/issues/3 --body x", True, "deny"),
    ("gh pr comment 3 --repo someone/upstream --body x", True, "deny"),
    ("GH_REPO=someone/upstream gh issue comment 3 --body x", True, "deny"),
    # GH_REPO binds only as an inline assignment of the guarded call itself.
    ("GH_REPO=hermes-labs-ai/r gh issue comment 3 --body x", False, None),
    ("gh issue comment 3 --body 'GH_REPO=hermes-labs-ai/r'", False, "deny"),
    ("gh issue comment 3 --body x # GH_REPO=hermes-labs-ai/r", False, "deny"),
    ("GH_REPO=hermes-labs-ai/r echo unrelated; gh issue comment 3 --body x", False, "deny"),
    ("sudo -u GH_REPO=hermes-labs-ai/r gh issue comment 3 --body x", False, "deny"),
    ("GH_REPO=hermes-labs-ai/r env -i gh issue comment 3 --body x", False, "deny"),
    ("GH_REPO=hermes-labs-ai/r env -uGH_REPO gh issue comment 3 --body x",
     False, "deny"),
    ("GH_REPO=someone/upstream echo unrelated; gh issue comment 3 "
     "--repo hermes-labs-ai/r --body x", False, None),
    ("export GH_REPO=someone/upstream; gh issue comment 3 --body x", True, "deny"),
    ("export GH_REPO=hermes-labs-ai/r; gh issue comment 3 --body x", False, None),
    ("export GH_REPO=hermes-labs-ai/r; gh issue comment 3 --body x; "
     "gh issue comment 3 --body x", False, None),
    ("export GH_REPO=someone/upstream; gh issue comment 3 --body x; "
     "gh issue comment 3 --body x", True, "deny"),
    ("false && export GH_REPO=hermes-labs-ai/r; gh issue comment 3 --body x",
     False, "deny"),
    ("GH_REPO=someone/upstream; gh issue comment 3 --body x", True, "deny"),
    ("export -n GH_REPO; gh issue comment 3 --body x", True, None),
    ("make && gh issue comment 3 --repo someone/upstream --body \"x", True, "deny"),
    ("gh issue comment 3 --repo hermes-labs-ai/r --body x", False, None),
    ("gh issue comment https://github.com/hermes-labs-ai/r/issues/3 --body x", False, None),
    ("gh issue comment 3 --body x", True, None),
    ("gh issue comment 3 --body x", False, "deny"),
    # No body flag, reads, and other issue verbs stay silent.
    ("gh issue comment 3 --repo someone/upstream --web", False, None),
    ("gh issue view 3 --repo someone/upstream --comments", False, None),
    ("gh issue create --repo someone/upstream --title t", False, None),
    ("gh issue comment 3 --repo someone/upstream --help --body x", False, None),
    # Documented remaining bypass: raw REST calls are not parsed.
    ("gh api repos/someone/upstream/issues/3/comments -F body=@b.md", False, None),
])
def test_gate_enforces_external_targets_and_exempts_internal_ones(
    monkeypatch, command, cwd_internal, expected
):
    monkeypatch.setattr(gate, "_cwd_is_internal", lambda cwd: cwd_internal)
    assert _gate_decision(command, cwd_internal) == expected


def _git_checkout(path, remotes):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for name, url in remotes:
        subprocess.run(["git", "-C", str(path), "remote", "add", name, url], check=True)
    return str(path)


def test_cwd_is_internal_only_when_every_remote_is_hermes(tmp_path):
    internal = _git_checkout(tmp_path / "internal", [
        ("origin", "git@github.com:hermes-labs-ai/agent-signage.git")])
    assert gate._cwd_is_internal(internal) is True
    # gh may pick `upstream` as the base; one internal origin is not enough.
    fork = _git_checkout(tmp_path / "fork", [
        ("origin", "https://github.com/hermes-labs-ai/tool.git"),
        ("upstream", "https://github.com/someone/tool.git")])
    assert gate._cwd_is_internal(fork) is False
    personal = _git_checkout(tmp_path / "personal", [
        ("origin", "https://github.com/roli-lpci/tool.git")])
    assert gate._cwd_is_internal(personal) is False
    assert gate._cwd_is_internal(_git_checkout(tmp_path / "bare", [])) is False
    (tmp_path / "plain").mkdir()
    assert gate._cwd_is_internal(str(tmp_path / "plain")) is False
    assert gate._cwd_is_internal(None) is False


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


def test_gate_failure_fallback_mirrors_the_external_boundary(monkeypatch, capsys):
    import io

    def boom(raw):
        raise RuntimeError("simulated adapter failure")

    monkeypatch.setattr(gate, "run", boom)
    monkeypatch.setattr(gate, "_cwd_is_internal", lambda cwd: False)

    def decision(cmd):
        payload = json.dumps({"tool_name": "Bash", "cwd": "/a/checkout",
                              "tool_input": {"cmd": cmd}})
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        assert gate.main() == 0
        out = capsys.readouterr().out
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else None

    # An explicit external target is denied from any checkout, like `run`.
    assert decision("gh pr create --repo someone/upstream --title t") == "deny"
    assert decision("make && gh pr create --repo someone/upstream --title t") == "deny"
    assert decision("gh pr edit 3 -R someone/upstream --body x") == "deny"
    # An absent target outside a clearly internal checkout is unknown: deny.
    assert decision("gh pr create --title t") == "deny"
    # The explicit internal exemption survives the failure path.
    assert decision("gh pr create --repo hermes-labs-ai/r --title t") is None
    assert decision("gh pr edit 3 -R hermes-labs-ai/r --body x") is None
    assert decision("gh pr view 3 --repo someone/upstream") is None
    assert decision("gh issue comment 3 --repo someone/upstream --body x") == "deny"
    assert decision("gh issue comment 3 --repo hermes-labs-ai/r --body x") is None

    # A clearly internal checkout exempts the plainest payload, whose command
    # follows a JSON quote rather than whitespace, but not an external target.
    monkeypatch.setattr(gate, "_cwd_is_internal", lambda cwd: True)
    assert decision("gh pr create --title t") is None
    assert decision("gh pr edit 3 --body x") is None
    assert decision("gh pr create --repo someone/upstream --title t") == "deny"
    assert decision("gh pr view 3") is None

    # If measuring the checkout itself fails, the context is unknown: deny.
    def unreadable(cwd):
        raise OSError("git unavailable")

    monkeypatch.setattr(gate, "_cwd_is_internal", unreadable)
    assert decision("gh pr create --title t") == "deny"
    assert decision("gh pr create --repo hermes-labs-ai/r --title t") is None
