"""Render a trusted local operational card at a caller-classified boundary.

This module deliberately does not decide when a card applies. The caller owns
classification, authorization, and enforcement; the renderer only validates a
small static card and gives it a consistent, bounded presentation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from .hook import build_output

CARD_FIELDS = frozenset({"id", "headline", "fact", "next"})
MAX_CARD_BYTES = 8192
MAX_CONTEXT_CHARS = 400
MAX_OUTPUT_CHARS = 1500
FIELD_LIMITS = {
    "id": 64,
    "headline": 160,
    "fact": 700,
    "next": 700,
}


class RenderError(ValueError):
    """The card or context cannot be rendered safely and predictably."""


@dataclass(frozen=True)
class Card:
    id: str
    headline: str
    fact: str
    next: str

    @classmethod
    def strict(cls, id: str, headline: str, fact: str, next: str) -> "Card":
        """Build a Card with the same bounds and shape checks ``load_card`` applies.

        ``Card(...)`` (the bare dataclass constructor) performs no validation at
        all -- a caller building a card programmatically, rather than loading one
        from a trusted JSON file, could otherwise hand ``render_text`` a
        multi-line headline, an oversized field, or an empty fact, silently
        breaking every guarantee this module documents. This is the one
        validated path into a ``Card``; ``load_card`` uses it too, so the two
        construction routes cannot drift apart.
        """
        card_id = _one_line("id", id, FIELD_LIMITS["id"])
        if re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", card_id) is None:
            raise RenderError("id must be a lowercase slug using letters, digits, '.', '_' or '-'")
        return cls(
            id=card_id,
            headline=_one_line("headline", headline, FIELD_LIMITS["headline"]),
            fact=_one_line("fact", fact, FIELD_LIMITS["fact"]),
            next=_one_line("next", next, FIELD_LIMITS["next"]),
        )


def _one_line(name: str, value: Any, limit: int) -> str:
    if not isinstance(value, str):
        raise RenderError("%s must be a string" % name)
    if not value or value != value.strip():
        raise RenderError("%s must be nonempty and have no surrounding whitespace" % name)
    if len(value) > limit:
        raise RenderError("%s exceeds %d characters" % (name, limit))
    if any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in value):
        raise RenderError("%s contains a control or line-format character" % name)
    return value


def load_card(path_value: str) -> Card:
    """Load a strict card from an absolute, non-symlink regular-file path."""
    path = Path(path_value)
    if not path.is_absolute():
        raise RenderError("card path must be absolute")
    if path.is_symlink():
        raise RenderError("card path must not be a symlink")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise RenderError("cannot read card: %s" % exc) from exc
    if not path.is_file():
        raise RenderError("card path must name a regular file")
    if size > MAX_CARD_BYTES:
        raise RenderError("card exceeds %d bytes" % MAX_CARD_BYTES)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RenderError("cannot read card as UTF-8: %s" % exc) from exc
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RenderError("card is not valid JSON") from exc
    if not isinstance(data, dict):
        raise RenderError("card must be a JSON object")
    actual = set(data)
    if actual != CARD_FIELDS:
        missing = sorted(CARD_FIELDS - actual)
        extra = sorted(actual - CARD_FIELDS)
        detail = []
        if missing:
            detail.append("missing %s" % ", ".join(missing))
        if extra:
            detail.append("unexpected %s" % ", ".join(extra))
        raise RenderError("card fields must be exactly id, headline, fact, next (%s)" % "; ".join(detail))

    return Card.strict(
        id=data["id"], headline=data["headline"], fact=data["fact"], next=data["next"]
    )


def render_text(card: Card, context: Optional[str] = None) -> str:
    """Render one bounded line; optional context is quoted data, never a template."""
    parts = ["%s [%s] - %s" % (card.headline, card.id, card.fact)]
    if context is not None:
        clean_context = _one_line("context", context, MAX_CONTEXT_CHARS)
        parts.append("Context data: %s." % json.dumps(clean_context, ensure_ascii=False))
    parts.append("Next: %s" % card.next)
    text = " ".join(parts)
    if len(text) > MAX_OUTPUT_CHARS:
        raise RenderError("rendered sign exceeds %d characters" % MAX_OUTPUT_CHARS)
    return text


def render(card: Card, output_format: str, context: Optional[str] = None) -> str:
    text = render_text(card, context=context)
    if output_format == "text":
        return text
    if output_format == "hook":
        return json.dumps(build_output([text]), ensure_ascii=False, separators=(",", ":"))
    raise RenderError("format must be 'text' or 'hook'")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-signage-render",
        description="Render a trusted local operational sign card.",
    )
    parser.add_argument("--card", required=True, help="absolute path to a trusted JSON card")
    parser.add_argument("--format", required=True, choices=("text", "hook"))
    parser.add_argument("--context", default=None, help="optional target data; quoted, never interpolated")
    args = parser.parse_args(argv)
    try:
        output = render(load_card(args.card), args.format, context=args.context)
    except RenderError as exc:
        print("agent-signage render: %s" % exc, file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
