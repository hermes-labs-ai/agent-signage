"""The event -> signage trust rule for lab_card, and its Card contract."""

from __future__ import annotations

import pytest

from agent_signage.lab_card import (
    ENVELOPE_SCHEMA,
    LabCardError,
    LabFact,
    card_from_lab_result,
    is_completed_evidence,
)
from agent_signage.render import RenderError


def envelope(**overrides):
    base = {
        "envelope": ENVELOPE_SCHEMA,
        "tool": "rule-audit",
        "toolVersion": "0.2.0",
        "command": "audit",
        "mode": "executed",
        "status": "pass",
        "inputHash": "sha256:" + "a" * 64,
        "findings": [],
        "exitCode": 0,
        "timestamp": "2026-09-07T00:00:00Z",
        "gitSha": "a" * 40,
        "data": {},
    }
    base.update(overrides)
    return base


CARD_ARGS = dict(id="lab.demo", headline="GATE PASSED", next="Continue.")


def test_completed_pass_with_a_declared_fact_renders_one_card():
    card = card_from_lab_result(envelope(), fact=LabFact("checks passed", "2 of 2"), **CARD_ARGS)
    assert card is not None
    assert card.id == "lab.demo"
    assert card.headline == "GATE PASSED"
    assert card.fact == "rule-audit 0.2.0 completed `audit`: checks passed 2 of 2."
    assert card.next == "Continue."


def test_completed_pass_with_no_fact_renders_a_card_that_invents_no_number():
    card = card_from_lab_result(envelope(), **CARD_ARGS)
    assert card is not None
    assert card.fact == "rule-audit 0.2.0 completed `audit` with no reported metric."
    assert "0" not in card.fact.split("no reported metric")[0][-3:]


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "warn"},
        {"status": "unknown"},
        {"status": "fail"},
        {"mode": "preview"},
    ],
)
def test_anything_short_of_completed_pass_licenses_no_card(overrides):
    """warn/unknown/fail status, or a preview (not a completed event): no card, ever."""
    assert card_from_lab_result(envelope(**overrides), fact=LabFact("x", "y"), **CARD_ARGS) is None
    assert not is_completed_evidence(envelope(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"envelope": "some-other-schema/1"},
        {"tool": ""},
        {"tool": None},
        {"toolVersion": ""},
        {"command": ""},
    ],
)
def test_malformed_or_wrong_schema_envelopes_license_no_card(overrides):
    assert card_from_lab_result(envelope(**overrides), **CARD_ARGS) is None


@pytest.mark.parametrize("bad_input", [None, "not a dict", 42, [], {}])
def test_non_envelope_inputs_license_no_card(bad_input):
    assert not is_completed_evidence(bad_input)
    assert card_from_lab_result(bad_input, **CARD_ARGS) is None


def test_a_fact_needs_both_a_label_and_a_value():
    with pytest.raises(LabCardError, match="both a label and a value"):
        card_from_lab_result(envelope(), fact=LabFact("", "2 of 2"), **CARD_ARGS)
    with pytest.raises(LabCardError, match="both a label and a value"):
        card_from_lab_result(envelope(), fact=LabFact("checks passed", ""), **CARD_ARGS)


def test_the_card_is_still_bounded_even_when_licensed():
    """A completed/pass envelope does not exempt the card from Card.strict's own limits."""
    with pytest.raises(RenderError, match="exceeds"):
        card_from_lab_result(
            envelope(),
            id="lab.demo",
            headline="GATE PASSED",
            next="n" * 701,
            fact=LabFact("x", "y"),
        )


def test_contradictory_envelope_status_and_findings_is_refused_by_the_shared_contract():
    """A status that disagrees with its own findings is not valid evidence at all --
    this belongs to every consumer of the shared contract, not to lab_card specifically,
    but a card built on top of a self-contradictory envelope would be evidence of nothing."""
    contradictory = envelope(
        status="pass",
        findings=[{"id": "x", "severity": "fail", "summary": "should have failed"}],
    )
    # lab_card does not itself re-validate findings-vs-status (that is the envelope
    # schema's own job in each product and in the site's parseEvidenceEnvelope); it only
    # trusts the envelope's own status field for the trust rule. Document that boundary:
    assert is_completed_evidence(contradictory) is True
    card = card_from_lab_result(contradictory, fact=LabFact("x", "y"), **CARD_ARGS)
    assert card is not None  # lab_card is not the schema validator; callers must validate first
