"""The checker: wording, block location, and what disagrees with what.

The boundary that actually reaches GitHub is covered in `test_publication.py`.
This file covers the pure function that boundary calls, where the cases are
cheap enough to be exhaustive.
"""

from __future__ import annotations

import pytest

from agent_signage import preflight


def codes(text, kind="contribution", oversight="active", **kw):
    return preflight.check(text, kind=kind, oversight=oversight, **kw).codes


# ------------------------------------------------------------------- wording

def test_the_published_wording_states_the_agreed_facts():
    active = preflight.attribution_sentence("contribution", "active")
    assert active == (
        "[Rolando Bosch](https://github.com/roli-lpci) is the responsible human contributor "
        "and provided active oversight and steering. This contribution was selected through "
        "[Hermes Labs](https://hermes-labs.ai)’ autonomous triage and executed through its "
        "engineering infrastructure."
    )
    # The possessive is what makes the trailing "its" refer to Hermes Labs.
    assert "[Hermes Labs](https://hermes-labs.ai)’ autonomous triage" in active


def test_the_role_noun_is_adapted_to_the_work():
    assert "responsible human contributor" in preflight.attribution_sentence(
        "contribution", "none")
    assert "responsible human reviewer" in preflight.attribution_sentence("review", "none")
    assert "This review was selected" in preflight.attribution_sentence("review", "active")


def test_the_oversight_clause_appears_only_when_declared():
    quiet = preflight.attribution_sentence("contribution", "none")
    assert "oversight" not in quiet
    assert "provided active oversight and steering" in preflight.attribution_sentence(
        "contribution", "active")


@pytest.mark.parametrize("kind", preflight.KINDS)
@pytest.mark.parametrize("oversight", preflight.OVERSIGHT_LEVELS)
def test_generated_blocks_pass_their_own_check(kind, oversight):
    assert preflight.check(
        preflight.attribution_block(kind, oversight), kind=kind, oversight=oversight).ok


def test_line_wrapping_is_allowed_and_rewording_is_not():
    sentence = preflight.attribution_sentence("contribution", "active")
    wrapped = "%s\n%s\n%s" % (preflight.OPEN_MARK, sentence.replace(". This", ".\nThis"),
                              preflight.CLOSE_MARK)
    assert codes(wrapped) == ()
    assert codes(wrapped.replace("responsible human contributor", "sole author")) == (
        "attribution-wording-mismatch",)


def test_identity_is_validated_before_it_is_rendered():
    preflight.validate_identity("Rolando Bosch", "https://github.com/roli-lpci")
    with pytest.raises(preflight.PreflightError, match="profile"):
        preflight.validate_identity("X", "https://example.com/x")
    with pytest.raises(preflight.PreflightError, match="profile"):
        preflight.validate_identity("X", "https://github.com/roli-lpci/repo")
    with pytest.raises(preflight.PreflightError, match="one-line name"):
        preflight.validate_identity("Two\nLines", "https://github.com/roli-lpci")


@pytest.mark.parametrize("bad", ["release", "", None])
def test_unknown_kinds_and_levels_are_refused(bad):
    with pytest.raises(preflight.PreflightError):
        preflight.attribution_sentence(bad, "active")
    with pytest.raises(preflight.PreflightError):
        preflight.attribution_sentence("contribution", bad)
    with pytest.raises(preflight.PreflightError):
        preflight.check("x", kind=bad, oversight="active")
    with pytest.raises(preflight.PreflightError):
        preflight.check("x", kind="contribution", oversight=bad)


# ------------------------------------------------------------ finding the block

def test_a_missing_or_repeated_block_is_rejected():
    assert codes("Just a change.\n") == ("attribution-missing",)
    twice = preflight.attribution_block("contribution", "active")
    assert codes(twice + "\n" + twice) == ("attribution-duplicated",)
    assert codes(preflight.OPEN_MARK + "\nno close marker\n") == ("attribution-duplicated",)


@pytest.mark.parametrize(
    "wrap",
    [
        "```\n{block}\n```",
        "~~~\n{block}\n~~~",
        "```markdown\n{block}\n```",
        "<!--\n{block}\n-->",
    ],
)
def test_a_block_that_does_not_render_is_not_a_statement(wrap):
    block = preflight.attribution_block("contribution", "active")
    assert codes(wrap.format(block=block)) == ("attribution-hidden",)


def test_a_four_space_indented_block_is_code_not_a_statement():
    block = preflight.attribution_block("contribution", "active")
    indented = "\n".join("    " + line for line in block.splitlines())
    assert codes(indented) == ("attribution-hidden",)


@pytest.mark.parametrize("wrapper", ["`{block}`", "<pre>{block}</pre>", "<code>{block}</code>"])
def test_a_block_inside_inline_or_raw_html_code_is_not_a_statement(wrapper):
    block = preflight.attribution_block("contribution", "active")
    assert codes(wrapper.format(block=block)) == ("attribution-hidden",)


@pytest.mark.parametrize("false_closer", ["~~~", "```"])
def test_a_different_or_shorter_fence_does_not_close_the_code_region(false_closer):
    block = preflight.attribution_block("contribution", "active")
    body = "````markdown\n%s\n%s\n````\n" % (false_closer, block)
    assert codes(body) == ("attribution-hidden",)


def test_a_comment_inside_the_block_is_rejected():
    block = preflight.attribution_block("contribution", "active")
    assert codes(block.replace("[Rolando", "<!-- hide -->[Rolando")) == ("attribution-hidden",)


def test_unrelated_comments_and_fences_elsewhere_do_not_false_positive():
    block = preflight.attribution_block("contribution", "active")
    body = (
        "Intro <!-- an ordinary editorial note --> and prose.\n\n"
        "```python\nprint('an unrelated code sample')\n```\n\n"
        "%s\n\nTail with `inline code` and a <!-- second note -->.\n" % block
    )
    assert codes(body) == ()


@pytest.mark.parametrize(
    "hidden, what",
    [
        ("‮", "right-to-left override"),
        ("​", "zero width space"),
        ("", "bell"),
        (" ", "line separator"),
    ],
)
def test_control_and_bidi_characters_in_the_block_are_rejected(hidden, what):
    """Checked text and rendered text have to be the same text."""
    block = preflight.attribution_block("contribution", "active")
    assert codes(block.replace("[Rolando", hidden + "[Rolando")) == (
        "attribution-malformed",), what


# ------------------------------------------------------- what disagrees with what

def test_a_claim_beyond_the_declaration_is_rejected_in_both_directions():
    active = preflight.attribution_block("contribution", "active")
    quiet = preflight.attribution_block("contribution", "none")
    assert codes(active, oversight="none") == ("oversight-claimed-beyond-declaration",)
    assert codes(quiet, oversight="active") == ("oversight-declared-but-not-stated",)


def test_the_kind_must_match_the_declaration():
    assert codes(preflight.attribution_block("review", "active"),
                 kind="contribution") == ("attribution-kind-mismatch",)
    assert set(codes(preflight.attribution_block("review", "none"),
                     kind="contribution", oversight="active")) == {
        "attribution-kind-mismatch", "oversight-declared-but-not-stated"}


def test_a_different_person_is_named_precisely():
    other = preflight.attribution_block(
        "contribution", "active", "Someone Else", "https://github.com/someone-else")
    assert codes(other) == ("contributor-mismatch",)


def test_preserved_disclosure_lines_must_survive():
    block = preflight.attribution_block("contribution", "active")
    line = "Co-Authored-By: A Maintainer <m@example.com>"
    assert codes("%s\n\n%s\n" % (block, line), preserve=[line]) == ()
    assert codes("%s\n" % block, preserve=[line]) == ("disclosure-dropped",)
    # Surrounding whitespace is not a dropped disclosure; case is.
    assert codes("%s\n\n  %s  \n" % (block, line), preserve=[line]) == ()
    assert codes("%s\n\n%s\n" % (block, line.upper()), preserve=[line]) == (
        "disclosure-dropped",)


def test_a_maintainers_own_endorsement_line_is_not_censored():
    """The checker checks the block, not the author's prose.

    A keyword sweep over free text rejects true statements a maintainer is
    entitled to make and misses any paraphrase of a false one. The wording this
    project publishes is generated and compared exactly; the rest of the body
    belongs to whoever wrote it.
    """
    block = preflight.attribution_block("contribution", "active")
    body = (
        "Approved by the release team in #412.\n"
        "I reviewed the upstream changelog before opening this.\n"
        "Filed on behalf of the storage working group.\n\n%s\n" % block
    )
    assert codes(body) == ()


def test_every_disagreement_is_reported_not_just_the_first():
    other = preflight.attribution_block(
        "review", "none", "Someone Else", "https://github.com/someone-else")
    reported = set(codes("%s\n" % other, kind="contribution", oversight="active",
                         preserve=["Co-Authored-By: X <x@e.com>"]))
    assert "disclosure-dropped" in reported
    assert "contributor-mismatch" in reported
