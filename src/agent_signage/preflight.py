"""Attribution wording, and the check that one artifact carries it.

This module is the *checker*. It decides nothing about publishing; `publish.py`
owns that, calls this on a byte snapshot it already holds, and refuses to start
a `gh` child when this returns a rejection.

What is checked, and what is deliberately not:

  Checked   The delimited attribution block is present exactly once, is not
            hidden inside a fenced code region or an enclosing HTML comment, is
            character-clean, and is *character-for-character* the wording this
            project generates -- modulo line wrapping. Disclosure lines the
            caller names with `--preserve` are still present.

  Not       The rest of the body's prose. An earlier revision scanned the whole
  checked   artifact for phrases like "approved by" and "I reviewed", and an
            independent review was right to call that out: a keyword sweep over
            English rejects a maintainer's own true statement, misses any
            paraphrase, and buys a feeling of rigour rather than rigour. What
            actually keeps a fabricated claim out is narrower and works: the
            published wording is generated from one function, compared exactly,
            and the oversight clause appears only when the caller explicitly
            declared it.

On declarations, stated plainly because the previous revision overstated it:
`--kind` and `--oversight` are *caller declarations*. Nothing here verifies
them. An earlier revision took them from an unsigned JSON sidecar and called it
an "attestation", which was worse -- ceremony that looked like verification
while anything able to write the body could write the sidecar. The honest
mechanism is the one that remains: `--oversight` has no default, so claiming
active human oversight is always a deliberate, recorded act by whoever ran the
command, and the artifact may never state more than was declared.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, NamedTuple, Optional, Sequence, Tuple

CONTRIBUTOR = "Rolando Bosch"
CONTRIBUTOR_URL = "https://github.com/roli-lpci"
ORG = "Hermes Labs"
ORG_URL = "https://hermes-labs.ai"

# The role noun is adapted to the work, not reused across it: someone
# responsible for a review is not thereby a contributor of the change.
ROLES = {"contribution": "responsible human contributor",
         "review": "responsible human reviewer"}
KINDS = ("contribution", "review")

# "none" first, and no default anywhere in the CLI. Active oversight is the
# strongest claim this tool will publish about a person; it has to be chosen.
OVERSIGHT_LEVELS = ("none", "active")

OPEN_MARK = "<!-- hermes-labs:attribution v1 -->"
CLOSE_MARK = "<!-- /hermes-labs:attribution -->"

MAX_ARTIFACT_BYTES = 65536

_FENCE_RE = re.compile(
    r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<rest>[^\n]*)$", re.MULTILINE)
_LINK_RE = re.compile(r"\[([^\]\n]{1,120})\]\((https?://[^\s)]{1,300})\)")
_CONTRIBUTOR_RE = re.compile(r"[^\x00-\x1f\x7f\[\]()<>]{1,80}")
_PROFILE_RE = re.compile(r"https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_TICK_RUN_RE = re.compile(r"`+")
_RAW_CODE_TAG_RE = re.compile(
    r"<(?P<close>/)?(?P<tag>pre|code|script|style|textarea)\b[^>]*>", re.IGNORECASE)


class PreflightError(ValueError):
    """Input is unusable or untrusted. Never a pass."""


class Reason(NamedTuple):
    code: str
    detail: str


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reasons: Tuple[Reason, ...]

    @property
    def codes(self) -> Tuple[str, ...]:
        return tuple(r.code for r in self.reasons)


# ------------------------------------------------------------------- wording

def attribution_sentence(
    kind: str,
    oversight: str,
    contributor: str = CONTRIBUTOR,
    contributor_url: str = CONTRIBUTOR_URL,
) -> str:
    """The exact wording this project publishes, and nothing beyond it."""
    if kind not in KINDS:
        raise PreflightError("kind must be one of %s" % ", ".join(KINDS))
    if oversight not in OVERSIGHT_LEVELS:
        raise PreflightError("oversight must be one of %s" % ", ".join(OVERSIGHT_LEVELS))
    role = "[%s](%s) is the %s" % (contributor, contributor_url, ROLES[kind])
    if oversight == "active":
        role += " and provided active oversight and steering"
    return (
        "%s. This %s was selected through [%s](%s)\u2019 autonomous triage and executed "
        "through its engineering infrastructure." % (role, kind, ORG, ORG_URL)
    )


def attribution_block(
    kind: str,
    oversight: str,
    contributor: str = CONTRIBUTOR,
    contributor_url: str = CONTRIBUTOR_URL,
) -> str:
    return "%s\n%s\n%s" % (
        OPEN_MARK,
        attribution_sentence(kind, oversight, contributor, contributor_url),
        CLOSE_MARK,
    )


def validate_identity(contributor: str, contributor_url: str) -> None:
    if _CONTRIBUTOR_RE.fullmatch(contributor) is None:
        raise PreflightError("contributor must be a plain one-line name")
    if _PROFILE_RE.fullmatch(contributor_url) is None:
        raise PreflightError("contributor url must be a https://github.com/<login> profile")


def _collapse(text: str) -> str:
    """Line wrapping is a formatting choice; anything else is not."""
    return " ".join(text.split())


# ------------------------------------------------------- locating the block

def _fence_spans(text: str) -> List[Tuple[int, int]]:
    """Half-open spans of fenced code regions, so a block inside one is caught.

    A block that renders as a code sample is not an attribution: the reader of
    the pull request sees an example, not a statement. Treated as hiding.
    """
    spans = []
    opened: Optional[Tuple[int, str, int]] = None
    for match in _FENCE_RE.finditer(text):
        if opened is None:
            fence = match.group("fence")
            opened = (match.start(), fence[0], len(fence))
            continue
        start, char, length = opened
        fence = match.group("fence")
        if (fence[0] == char and len(fence) >= length
                and not match.group("rest").strip()):
            newline = text.find("\n", match.end())
            spans.append((start, len(text) if newline < 0 else newline + 1))
            opened = None
    if opened is not None:
        spans.append((opened[0], len(text)))
    return spans


def _indented_code_at(text: str, position: int) -> bool:
    """Whether the line at position is rendered as Markdown indented code."""
    line_start = text.rfind("\n", 0, position) + 1
    prefix = text[line_start:position]
    columns = 0
    for char in prefix:
        if char == " ":
            columns += 1
        elif char == "\t":
            columns += 4 - (columns % 4)
        else:
            return False
    return columns >= 4


def _comment_spans(text: str) -> List[Tuple[int, int]]:
    """Half-open spans of HTML comments, scanned the way a renderer scans them.

    Comments do not nest: a comment ends at the first `-->`. The marker pair is
    itself two well-formed comments, so in a correct artifact the sentence
    between them lands outside every span. If it lands inside one, someone
    wrapped the statement so that nobody reading the pull request will see it.
    """
    spans = []
    index = 0
    while True:
        start = text.find("<!--", index)
        if start < 0:
            return spans
        end = text.find("-->", start + 4)
        if end < 0:
            spans.append((start, len(text)))
            return spans
        spans.append((start, end + 3))
        index = end + 3


def _code_span_spans(text: str) -> List[Tuple[int, int]]:
    """CommonMark backtick spans, including spans that cross line endings."""
    runs = list(_TICK_RUN_RE.finditer(text))
    spans = []
    index = 0
    while index < len(runs):
        opener = runs[index]
        length = len(opener.group(0))
        close = index + 1
        while close < len(runs) and len(runs[close].group(0)) != length:
            close += 1
        if close < len(runs):
            spans.append((opener.start(), runs[close].end()))
            index = close + 1
        else:
            index += 1
    return spans


def _raw_code_spans(text: str) -> List[Tuple[int, int]]:
    """Raw HTML elements whose contents do not render as ordinary prose."""
    opened: dict = {}
    spans = []
    for match in _RAW_CODE_TAG_RE.finditer(text):
        tag = match.group("tag").lower()
        if match.group("close"):
            start = opened.pop(tag, None)
            if start is not None:
                spans.append((start, match.end()))
        else:
            opened.setdefault(tag, match.start())
    spans.extend((start, len(text)) for start in opened.values())
    return spans


def _inside(position: int, spans: Sequence[Tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def locate_block(text: str) -> Tuple[Optional[str], List[Reason]]:
    opens = text.count(OPEN_MARK)
    closes = text.count(CLOSE_MARK)
    if opens == 0 and closes == 0:
        return None, [Reason("attribution-missing", "no %s block in the artifact" % OPEN_MARK)]
    if opens != 1 or closes != 1:
        return None, [Reason(
            "attribution-duplicated",
            "expected exactly one attribution block, found %d open and %d close markers"
            % (opens, closes))]

    open_at = text.index(OPEN_MARK)
    close_at = text.index(CLOSE_MARK)
    inner_start = open_at + len(OPEN_MARK)
    if close_at < inner_start:
        return None, [Reason("attribution-malformed", "close marker precedes open marker")]
    inner = text[inner_start:close_at]

    block_end = close_at + len(CLOSE_MARK)
    fences = _fence_spans(text)
    if _inside(open_at, fences) or _inside(inner_start, fences):
        return None, [Reason(
            "attribution-hidden",
            "the attribution block is inside a fenced code region and renders as a sample")]
    if _inside(open_at, _code_span_spans(text)):
        return None, [Reason(
            "attribution-hidden",
            "the attribution block is inside a backtick code span and renders as a sample")]
    if _inside(open_at, _raw_code_spans(text)):
        return None, [Reason(
            "attribution-hidden",
            "the attribution block is inside a raw HTML code region")]
    if _indented_code_at(text, open_at):
        return None, [Reason(
            "attribution-hidden",
            "the attribution block is indented as code and renders as a sample")]
    offset = inner_start
    for line in inner.splitlines(keepends=True):
        content_at = offset + len(line) - len(line.lstrip(" \t"))
        if line.strip() and _indented_code_at(text, content_at):
            return None, [Reason(
                "attribution-hidden",
                "the attribution text is indented as code and renders as a sample")]
        offset += len(line)

    # In a correct artifact the only comments touching the block are the two
    # markers, which begin exactly at `open_at` and `close_at`. Any other
    # comment overlapping the block region is either wrapping the statement or
    # trying to -- both mean a reader of the pull request does not see what was
    # checked, so neither is accepted.
    for start, end in _comment_spans(text):
        if end <= open_at or start >= block_end:
            continue
        if start not in (open_at, close_at):
            return None, [Reason(
                "attribution-hidden",
                "an HTML comment at offset %d encloses or splits the attribution block" % start)]
    return inner, []


def _forbidden_characters(text: str) -> Optional[str]:
    for char in text:
        if char in "\n\r\t":
            continue
        if unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"}:
            return "U+%04X" % ord(char)
    return None


def _diagnose(inner: str, kind: str, oversight: str, contributor: str, url: str) -> List[Reason]:
    """Name which fact disagrees, not just that something does."""
    bad = _forbidden_characters(inner)
    if bad is not None:
        return [Reason("attribution-malformed", "attribution block contains %s" % bad)]

    normalised = _collapse(inner)
    if normalised == _collapse(attribution_sentence(kind, oversight, contributor, url)):
        return []

    for other_kind in KINDS:
        for other_oversight in OVERSIGHT_LEVELS:
            if normalised != _collapse(
                attribution_sentence(other_kind, other_oversight, contributor, url)
            ):
                continue
            reasons = []
            if other_kind != kind:
                reasons.append(Reason(
                    "attribution-kind-mismatch",
                    "artifact states a %r; the caller declared %r" % (other_kind, kind)))
            if other_oversight != oversight:
                if other_oversight == "active":
                    reasons.append(Reason(
                        "oversight-claimed-beyond-declaration",
                        "artifact claims active oversight and steering; the caller declared "
                        "--oversight none"))
                else:
                    reasons.append(Reason(
                        "oversight-declared-but-not-stated",
                        "--oversight active was declared but the artifact does not state it"))
            return reasons

    link = _LINK_RE.search(inner)
    if link is not None and (link.group(1) != contributor or link.group(2) != url):
        return [Reason(
            "contributor-mismatch",
            "artifact names [%s](%s); the declared contributor is [%s](%s)"
            % (link.group(1), link.group(2), contributor, url))]
    return [Reason(
        "attribution-wording-mismatch",
        "attribution text is not the wording this project publishes; regenerate it with "
        "`agent-signage attribution`")]


def check(
    text: str,
    *,
    kind: str,
    oversight: str,
    preserve: Sequence[str] = (),
    contributor: str = CONTRIBUTOR,
    contributor_url: str = CONTRIBUTOR_URL,
) -> Verdict:
    """Decide whether this artifact text may be published. Pure.

    Every failing check is reported, not just the first: a checker that surfaces
    one problem per run turns one fix into four round trips.
    """
    if kind not in KINDS:
        raise PreflightError("kind must be one of %s" % ", ".join(KINDS))
    if oversight not in OVERSIGHT_LEVELS:
        raise PreflightError("oversight must be one of %s" % ", ".join(OVERSIGHT_LEVELS))

    reasons: List[Reason] = []
    # Whitespace-normalised on both sides so an indented or trailing-space copy
    # of a disclosure still counts. Case is content and is not folded.
    present = {line.strip() for line in text.splitlines()}
    for required in preserve:
        if required.strip() not in present:
            reasons.append(Reason(
                "disclosure-dropped",
                "required disclosure line is absent: %r" % required.strip()))

    inner, located = locate_block(text)
    reasons.extend(located)
    if inner is not None:
        reasons.extend(_diagnose(inner, kind, oversight, contributor, contributor_url))
    return Verdict(ok=not reasons, reasons=tuple(reasons))


# ------------------------------------------------- the check-only entry point

def add_arguments(parser) -> None:
    parser.add_argument("--body-file", required=True,
                        help="absolute path to the artifact to check")
    parser.add_argument("--kind", required=True, choices=KINDS,
                        help="declared by the caller; nothing here verifies it")
    # No default. See the module docstring: active oversight is always chosen.
    parser.add_argument("--oversight", required=True, choices=OVERSIGHT_LEVELS,
                        help="declared by the caller; 'active' is never assumed")
    parser.add_argument("--preserve", action="append", default=[], metavar="LINE",
                        help="a disclosure line that must survive (repeatable)")


def add_attribution_arguments(parser) -> None:
    parser.add_argument("--kind", required=True, choices=KINDS)
    parser.add_argument("--oversight", required=True, choices=OVERSIGHT_LEVELS)
    parser.add_argument("--contributor", default=CONTRIBUTOR)
    parser.add_argument("--contributor-url", default=CONTRIBUTOR_URL)


def run_args(args) -> int:
    """Check only. Publishing is `agent-signage publish`, which owns execution."""
    from . import publish

    snap = publish.snapshot(args.body_file)
    verdict = check(snap.text, kind=args.kind, oversight=args.oversight,
                    preserve=tuple(args.preserve))
    print("%s  %s" % ("PASS  " if verdict.ok else "REJECT", snap.path))
    print("  sha256   %s" % snap.sha256)
    for reason in verdict.reasons:
        print("  reject   %s: %s" % (reason.code, reason.detail))
    if verdict.ok:
        print("  This is a check, not a publication. `agent-signage publish` is the "
              "only path that sends these bytes.")
    return 0 if verdict.ok else 1


def run_attribution(args) -> int:
    validate_identity(args.contributor, args.contributor_url)
    print(attribution_block(args.kind, args.oversight, args.contributor, args.contributor_url))
    return 0


def main(argv=None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="agent-signage-preflight",
        description="Check one artifact's attribution. Does not publish anything.",
        epilog="0 publishable, 1 rejected artifact, 2 rejected input.",
    )
    add_arguments(parser)
    args = parser.parse_args(None if argv is None else list(argv))
    try:
        return run_args(args)
    except PreflightError as exc:
        print("agent-signage preflight: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
