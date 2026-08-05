"""Tests for the 0.1.0 signs, over real files and real git worktrees.

Each sign is tested three ways: it fires on the real condition, it reports the
true fact, and it stays silent in the legitimate cases that look similar. The
silence cases matter more than the firing cases -- a sign that is right but
noisy gets muted, and a muted sign protects nobody.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from agent_signage import gitfacts, hook, more_signs, signs, state  # noqa: F401

pytestmark = pytest.mark.usefixtures("isolated_state")


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo)] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir()
    monkeypatch.setenv("AGENT_SIGNAGE_STATE_DIR", str(d))
    # Tests that call signs directly bypass hook.run(), which is what normally
    # resets the per-evaluation memoisation.
    gitfacts.clear_caches()
    more_signs.clear_caches()
    monkeypatch.delenv("AGENT_SIGNAGE_IGNORE", raising=False)
    return d


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t.t")
    git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed")
    git(r, "add", "seed.txt")
    git(r, "commit", "-m", "seed")
    return r


def ctx(path, tool="Edit", session="s1"):
    return signs.Context(session_id=session, tool_name=tool, target_path=str(path))


# ------------------------------------------------------------- symlink escape

def test_symlink_escape_fires(repo, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = repo / "innocent.txt"
    os.symlink(outside, link)

    s = more_signs.symlink_escape(ctx(link))
    assert s is not None
    assert "SYMLINK LEAVES REPO" in s.text
    assert str(outside) in s.text


def test_symlink_inside_repo_is_silent(repo):
    (repo / "real.txt").write_text("x")
    link = repo / "alias.txt"
    os.symlink(repo / "real.txt", link)
    assert more_signs.symlink_escape(ctx(link)) is None


def test_plain_file_is_not_a_symlink_escape(repo):
    (repo / "plain.txt").write_text("x")
    assert more_signs.symlink_escape(ctx(repo / "plain.txt")) is None


def test_symlink_escape_fires_on_read_too(repo, tmp_path):
    """Reading through an escaping symlink is already the leak."""
    outside = tmp_path / "o.txt"
    outside.write_text("s")
    link = repo / "l.txt"
    os.symlink(outside, link)
    assert more_signs.symlink_escape(ctx(link, tool="Read")) is not None


# ----------------------------------------------------------- conflict markers

CONFLICTED = "a\n<<<<<<< HEAD\nmine\n=======\ntheirs\n>>>>>>> branch\nb\n"


def test_conflict_markers_fire(repo):
    f = repo / "c.txt"
    f.write_text(CONFLICTED)
    s = more_signs.conflict_markers(ctx(f))
    assert s is not None and "UNRESOLVED CONFLICT" in s.text


def test_conflict_markers_fire_on_read(repo):
    f = repo / "c.txt"
    f.write_text(CONFLICTED)
    assert more_signs.conflict_markers(ctx(f, tool="Read")) is not None


def test_prose_mentioning_markers_inline_is_silent(repo):
    """Markers must be at line start; prose about conflicts is not a conflict."""
    f = repo / "doc.md"
    f.write_text("Resolve with <<<<<<< HEAD inline in a sentence, not at line start.\n")
    assert more_signs.conflict_markers(ctx(f)) is None


def test_clean_file_is_silent(repo):
    f = repo / "clean.txt"
    f.write_text("nothing to see\n")
    assert more_signs.conflict_markers(ctx(f)) is None


# ------------------------------------------------------- concurrent worktree

def test_concurrent_worktree_edit_fires(repo, tmp_path):
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    (wt / "seed.txt").write_text("changed by the other agent")

    s = more_signs.concurrent_worktree_edit(ctx(repo / "seed.txt"))
    assert s is not None
    assert "CONCURRENT EDIT" in s.text
    assert str(wt) in s.text


def test_clean_sibling_worktree_is_silent(repo, tmp_path):
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    assert more_signs.concurrent_worktree_edit(ctx(repo / "seed.txt")) is None


def test_single_worktree_is_silent(repo):
    assert more_signs.concurrent_worktree_edit(ctx(repo / "seed.txt")) is None


def test_concurrent_worktree_silent_on_read(repo, tmp_path):
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    (wt / "seed.txt").write_text("changed")
    assert more_signs.concurrent_worktree_edit(ctx(repo / "seed.txt", tool="Read")) is None


# ---------------------------------------------------------------- binary edit

def test_binary_edit_fires(repo):
    f = repo / "img.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    s = more_signs.binary_edit(ctx(f))
    assert s is not None and "BINARY FILE" in s.text


def test_text_file_is_silent(repo):
    f = repo / "t.txt"
    f.write_text("plain text, no NUL bytes")
    assert more_signs.binary_edit(ctx(f)) is None


def test_binary_silent_on_read(repo):
    f = repo / "img.png"
    f.write_bytes(b"\x00\x01\x02")
    assert more_signs.binary_edit(ctx(f, tool="Read")) is None


# ------------------------------------------------------------- generated file

@pytest.mark.parametrize(
    "header",
    ["// Code generated by protoc-gen-go. DO NOT EDIT.\n",
     "# @generated by sqlc\n",
     "/* AUTO-GENERATED FILE */\n"],
)
def test_generated_file_fires(repo, header):
    f = repo / "gen.go"
    f.write_text(header + "package main\n")
    s = more_signs.generated_file(ctx(f))
    assert s is not None and "GENERATED FILE" in s.text


def test_marker_deep_in_file_is_silent(repo):
    """Only the header counts; the phrase can appear legitimately in prose."""
    f = repo / "notes.md"
    f.write_text("\n".join(["line"] * 20) + "\nDO NOT EDIT this section by hand\n")
    assert more_signs.generated_file(ctx(f)) is None


def test_ordinary_file_is_silent(repo):
    f = repo / "main.py"
    f.write_text("print('hello')\n")
    assert more_signs.generated_file(ctx(f)) is None


# ------------------------------------------------------------- shared policy

def test_signs_are_deduped_per_file_per_session(repo, tmp_path):
    outside = tmp_path / "o.txt"
    outside.write_text("s")
    link = repo / "l.txt"
    os.symlink(outside, link)
    p = json.dumps({"session_id": "dd", "tool_name": "Edit",
                    "tool_input": {"file_path": str(link)}})
    assert hook.run(p) is not None
    assert hook.run(p) is None


def test_two_different_files_both_speak(repo, tmp_path):
    """Dedupe is per file, not per repo -- a second bad file is new information."""
    for name in ("a.txt", "b.txt"):
        (repo / name).write_text(CONFLICTED)
    out_a = hook.run(json.dumps({"session_id": "dd2", "tool_name": "Edit",
                                 "tool_input": {"file_path": str(repo / "a.txt")}}))
    out_b = hook.run(json.dumps({"session_id": "dd2", "tool_name": "Edit",
                                 "tool_input": {"file_path": str(repo / "b.txt")}}))
    assert out_a is not None and out_b is not None


def test_multiple_signs_combine_into_one_message(repo, tmp_path):
    """A binary file that is also generated should produce one delivery."""
    f = repo / "blob.bin"
    f.write_bytes(b"// Code generated by tool. DO NOT EDIT.\n\x00\x01")
    out = hook.run(json.dumps({"session_id": "multi", "tool_name": "Edit",
                               "tool_input": {"file_path": str(f)}}))
    assert out is not None
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "BINARY FILE" in text and "GENERATED FILE" in text


def test_vendored_paths_silence_every_sign(repo, tmp_path):
    d = repo / "node_modules" / "pkg"
    d.mkdir(parents=True)
    f = d / "x.js"
    f.write_text(CONFLICTED)
    assert signs.evaluate(ctx(f)) == []


def test_no_sign_ever_blocks(repo, tmp_path):
    outside = tmp_path / "o.txt"
    outside.write_text("s")
    link = repo / "l.txt"
    os.symlink(outside, link)
    out = hook.run(json.dumps({"session_id": "nb", "tool_name": "Edit",
                               "tool_input": {"file_path": str(link)}}))
    assert "decision" not in json.dumps(out)
    assert set(out) == {"hookSpecificOutput"}


def test_every_sign_ends_in_a_command(repo, tmp_path):
    """Actionability is a contract, not a style preference."""
    cases = []
    outside = tmp_path / "o.txt"
    outside.write_text("s")
    link = repo / "l.txt"
    os.symlink(outside, link)
    cases.append(more_signs.symlink_escape(ctx(link)))

    c = repo / "c.txt"
    c.write_text(CONFLICTED)
    cases.append(more_signs.conflict_markers(ctx(c)))

    b = repo / "b.png"
    b.write_bytes(b"\x00\x01")
    cases.append(more_signs.binary_edit(ctx(b)))

    g = repo / "g.go"
    g.write_text("// Code generated by x. DO NOT EDIT.\n")
    cases.append(more_signs.generated_file(ctx(g)))

    for s in cases:
        assert s is not None
        assert "Inspect: " in s.text, "%s has no resolving command" % s.id


def test_caches_do_not_leak_state_between_evaluations(repo, tmp_path):
    """Two runs in one process must see two different realities.

    The memoisation is only valid inside a single evaluation. If it survived
    across calls, a long-lived embedding would answer every future tool call
    with the first repository state it ever saw.
    """
    f = repo / "seed.txt"
    p = json.dumps({"session_id": "leak-a", "tool_name": "Edit",
                    "tool_input": {"file_path": str(f)}})
    assert hook.run(p) is None, "clean file, nothing to say"

    f.write_text(CONFLICTED)
    p2 = json.dumps({"session_id": "leak-b", "tool_name": "Edit",
                     "tool_input": {"file_path": str(f)}})
    out = hook.run(p2)
    assert out is not None, "second run must observe the file as it is now"
    assert "UNRESOLVED CONFLICT" in out["hookSpecificOutput"]["additionalContext"]


def test_every_memoised_function_is_registered_for_clearing():
    """A new lru_cache added without registering it would silently go stale."""
    import functools as _f

    from agent_signage import gitfacts

    memoised = {
        name for name, obj in vars(gitfacts).items()
        if hasattr(obj, "cache_clear") and isinstance(obj, _f._lru_cache_wrapper)
    }
    registered = {fn.__name__ for fn in gitfacts._CACHED}
    assert memoised == registered, "unregistered caches: %s" % (memoised - registered)
