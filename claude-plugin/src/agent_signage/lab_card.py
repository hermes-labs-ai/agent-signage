"""Render an operational card from a Hermes Reliability Lab result envelope.

This is the completion-and-communication end of the local Hermes Reliability
Lab: another product (rule-audit, quick-gate-python, quick-gate-js,
hermes-jailbench, hermes-blind) already emitted a
``hermes.reliability-lab.result/1`` envelope describing one run of itself.
This module decides only one thing -- whether that envelope licenses a
completion card -- and otherwise stays out of the way.

The event -> signage trust rule, matching this project's own guarantee table
(sound, quiet, fails open):

  * A card is issued only for an envelope whose ``mode`` is ``"executed"``
    (a real run, not a preview) and whose ``status`` is ``"pass"`` -- the
    contract's own definition of "no finding worse than pass". Anything else
    -- ``preview`` mode, ``warn``/``unknown``/``fail`` status, a malformed
    envelope, or the wrong schema string -- returns ``None``: no card, same
    as this project's signs when there is nothing worth saying.
  * This module does not interpret what a product's native data means. A
    "test count" or "checks passed" figure is a fact about *that* product's
    output that only its own emitter (or a caller who already parsed it)
    can state correctly; asking a generic adapter to guess would be exactly
    the kind of inferred-not-measured claim this project's signs refuse to
    make. The caller declares the fact (a label and a value) already
    computed from the envelope's own accepted fields; this module only
    proves the envelope is genuinely completed evidence and renders through
    Card.strict, the one validated card-construction path, so the result
    carries the same bounds and shape guarantees as any other card here.
  * Declaring no fact is legitimate and renders a card that says only that
    the run completed -- never a zero, never an invented number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .render import Card

ENVELOPE_SCHEMA = "hermes.reliability-lab.result/1"


class LabCardError(ValueError):
    """The envelope or the caller's declared fact cannot be turned into a card."""


@dataclass(frozen=True)
class LabFact:
    """A caller-declared, precisely labeled measurement from an envelope's own data.

    Both fields are required together: a label with no value, or a value with
    no label, is not a fact, it is an incomplete claim. ``label`` should name
    what was counted precisely (``"checks passed"``, ``"attacks refused"``),
    never a vague or promotional term (``"users"``, ``"adoption"``).
    """

    label: str
    value: str


def is_completed_evidence(envelope: Any) -> bool:
    """True only for a genuinely completed, clean run of the shared contract.

    ``status`` on this contract is already defined as the worst finding the
    run carries (see envelope.ts / evidence.py worst_status in each product);
    ``"pass"`` therefore means every finding, if any, was itself a pass. A
    ``preview`` is explicitly not a completed event even when nothing in it
    looks wrong -- nothing was actually run.
    """
    return (
        isinstance(envelope, dict)
        and envelope.get("envelope") == ENVELOPE_SCHEMA
        and envelope.get("mode") == "executed"
        and envelope.get("status") == "pass"
        and isinstance(envelope.get("tool"), str)
        and bool(envelope.get("tool"))
        and isinstance(envelope.get("toolVersion"), str)
        and bool(envelope.get("toolVersion"))
        and isinstance(envelope.get("command"), str)
        and bool(envelope.get("command"))
    )


def card_from_lab_result(
    envelope: Any,
    *,
    id: str,
    headline: str,
    next: str,
    fact: Optional[LabFact] = None,
) -> Optional[Card]:
    """Build a completion Card from a lab envelope, or return None.

    Returns ``None`` -- never a card that claims success -- unless
    ``is_completed_evidence(envelope)`` is true. ``id``, ``headline``, and
    ``next`` are the caller's own card framing (what boundary this
    completion matters at); ``fact`` is the caller's declared measurement,
    or omitted when the run completed with nothing worth quoting a number
    for. The returned Card has already passed the same bounds and shape
    checks a card loaded from JSON would.
    """
    if not is_completed_evidence(envelope):
        return None

    tool = envelope["tool"]
    tool_version = envelope["toolVersion"]
    command = envelope["command"]
    if fact is None:
        stated = "%s %s completed `%s` with no reported metric." % (tool, tool_version, command)
    else:
        if not fact.label or not fact.value:
            raise LabCardError("a declared fact must have both a label and a value")
        stated = "%s %s completed `%s`: %s %s." % (
            tool,
            tool_version,
            command,
            fact.label,
            fact.value,
        )
    return Card.strict(id=id, headline=headline, fact=stated, next=next)
