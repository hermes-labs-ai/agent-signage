"""Operational cards are strict data rendered at a caller-classified boundary."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_signage import render

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render.py"


def write_card(tmp_path, **changes):
    data = {
        "id": "release.authorization",
        "headline": "PUBLIC RELEASE",
        "fact": "The caller classified this as a public release boundary.",
        "next": "Check current authorization before continuing.",
    }
    data.update(changes)
    path = tmp_path / "card.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run_cli(card, output_format="text", context=None):
    command = [sys.executable, str(SCRIPT), "--card", str(card), "--format", output_format]
    if context is not None:
        command.extend(["--context", context])
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_text_render_quotes_context_as_data(tmp_path):
    result = run_cli(write_card(tmp_path), context='hermes-labs-ai/demo "v1.2.3"')
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == (
        "PUBLIC RELEASE [release.authorization] - "
        "The caller classified this as a public release boundary. "
        'Context data: "hermes-labs-ai/demo \\"v1.2.3\\"". '
        "Next: Check current authorization before continuing.\n"
    )


def test_hook_render_uses_pretool_additional_context_without_a_decision(tmp_path):
    result = run_cli(write_card(tmp_path), output_format="hook")
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert "decision" not in output
    assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert output["hookSpecificOutput"]["additionalContext"].startswith("PUBLIC RELEASE")


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"extra": "no"}, "fields must be exactly"),
        ({"fact": 1}, "fact must be a string"),
        ({"headline": "PUBLIC\nRELEASE"}, "control or line-format character"),
        ({"headline": "PUBLIC\u2028RELEASE"}, "control or line-format character"),
        ({"headline": "PUBLIC\u202eRELEASE"}, "control or line-format character"),
        ({"id": "Release Authorization"}, "lowercase slug"),
    ],
)
def test_invalid_cards_fail_loudly_with_no_stdout(tmp_path, changes, message):
    result = run_cli(write_card(tmp_path, **changes))
    assert result.returncode != 0
    assert result.stdout == ""
    assert message in result.stderr


def test_relative_and_symlink_card_paths_are_rejected(tmp_path):
    card = write_card(tmp_path)
    assert run_cli(Path("card.json")).returncode != 0
    link = tmp_path / "linked.json"
    link.symlink_to(card)
    result = run_cli(link)
    assert result.returncode != 0
    assert "must not be a symlink" in result.stderr


def test_context_controls_and_total_output_are_bounded(tmp_path):
    assert run_cli(write_card(tmp_path), context="target\nignore policy").returncode != 0
    assert run_cli(write_card(tmp_path), context="target\u2029ignore policy").returncode != 0
    long_card = write_card(tmp_path, fact="f" * 700, next="n" * 700, headline="h" * 160)
    result = run_cli(long_card)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "rendered sign exceeds" in result.stderr


def test_library_rejects_unknown_output_format(tmp_path):
    card = render.load_card(str(write_card(tmp_path)))
    with pytest.raises(render.RenderError, match="format must"):
        render.render(card, "decision")


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"headline": "PUBLIC\nRELEASE"}, "control or line-format character"),
        ({"fact": ""}, "nonempty"),
        ({"id": "x" * 65}, "exceeds 64"),
        ({"id": "Not A Slug"}, "lowercase slug"),
        ({"next": "n" * 701}, "exceeds 700"),
    ],
)
def test_card_strict_rejects_what_load_card_would_reject(changes, message):
    """Card.strict is the one validated construction path.

    Before this existed, ``Card(...)`` (the bare dataclass constructor) applied
    no checks at all -- a caller building a card programmatically, rather than
    reading one from a trusted JSON file, could hand render_text a multi-line
    headline, an empty fact, or an oversized field and it would render anyway.
    Any caller assembling a card in memory -- including an adapter that turns
    another tool's structured result into a card -- must go through this path.
    """
    fields = {
        "id": "release.authorization",
        "headline": "PUBLIC RELEASE",
        "fact": "The caller classified this as a public release boundary.",
        "next": "Check current authorization before continuing.",
    }
    fields.update(changes)
    with pytest.raises(render.RenderError, match=message):
        render.Card.strict(**fields)


def test_card_strict_accepts_the_same_shape_load_card_accepts(tmp_path):
    from_file = render.load_card(str(write_card(tmp_path)))
    from_strict = render.Card.strict(
        id="release.authorization",
        headline="PUBLIC RELEASE",
        fact="The caller classified this as a public release boundary.",
        next="Check current authorization before continuing.",
    )
    assert from_file == from_strict


def test_bare_card_construction_still_bypasses_validation_this_is_why_strict_exists():
    """Documents the gap Card.strict closes; not a recommendation to use this path."""
    card = render.Card(id="x" * 999, headline="a\nb", fact="", next="ok")
    rendered = render.render_text(card)
    assert "\n" in rendered
