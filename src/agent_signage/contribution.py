"""Exact contribution footer approved in DELEGATED-CONTRIBUTIONS.md (2026-09-06).

Selection and authority remain caller declarations; the publisher checks the
actual authenticated account separately. This is not an authorization service.
"""
from . import preflight

SELECTIONS = ("autonomous", "owner", "unspecified")
ACCOUNT = "roli-lpci"
SELECTION_MARK = "<!-- hermes-labs:selection autonomous -->"
POLICY = "ai-infra/commitment-gate/DELEGATED-CONTRIBUTIONS.md"


def footer(selection):
    if selection not in SELECTIONS:
        raise preflight.PreflightError("selection must be autonomous, owner, or unspecified")
    # Unspecified selection makes only the verified agent-production claim.
    verb = "was autonomously selected and produced" if selection == "autonomous" else "was produced"
    sentence = (
        "This contribution %s by agents through [Hermes Labs](https://hermes-labs.ai)’ "
        "engineering infrastructure. [Rolando Bosch](https://github.com/roli-lpci) is the "
        "responsible human contributor and authorized publication from his personal GitHub account."
        % verb
    )
    lines = [preflight.OPEN_MARK]
    if selection == "autonomous":
        lines.append(SELECTION_MARK)
    return "\n".join(lines + [sentence, preflight.CLOSE_MARK])


def check(text, selection, preserve=()):
    expected = footer(selection)
    # The approved selection comment is allowed only in the exact footer. Remove
    # it for the existing code/comment visibility checks, which reject comments.
    visible = text.replace(expected, expected.replace(SELECTION_MARK + "\n", ""), 1)
    _, reasons = preflight.locate_block(visible)
    if (not text.rstrip().endswith(expected) or text.count(expected) != 1
            or text.count(SELECTION_MARK) != (1 if selection == "autonomous" else 0)):
        reasons.append(preflight.Reason(
            "contribution-footer-mismatch",
            "Submission blocked: append the exact %s footer from %s to the final PR body; "
            "then retry this publisher. No footer is added automatically." % (selection, POLICY)))
    present = {line.strip() for line in text.splitlines()}
    for required in preserve:
        if required.strip() not in present:
            reasons.append(preflight.Reason("disclosure-dropped", "required disclosure is absent: %r" % required))
    return preflight.Verdict(ok=not reasons, reasons=tuple(reasons))
