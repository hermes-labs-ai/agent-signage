"""Emit a lab-card render as a Hermes Reliability Lab result envelope.

hermes.reliability-lab.result/1 tool, version, status, input hash, findings,
exit code, timestamp, optional Git SHA -- with the rendered card (or the
absence of one) embedded verbatim in ``data``.

This is the consuming end of the shared contract, not a producing one:
``python -m agent_signage.evidence`` reads another lab product's already-
emitted envelope from a file, and turns it into a completion card through
``lab_card.card_from_lab_result`` -- or into no card at all, when the source
envelope does not license one. Both outcomes are reported, never guessed.

    python -m agent_signage.evidence \\
      --source envelope.json --id release.checks --headline "GATE PASSED" \\
      --next "Continue." --fact-label "checks passed" --fact-value "2 of 2"

    python -m agent_signage.evidence \\
      --source envelope.json --id release.checks --headline "GATE PASSED" --next "Continue."

Exit codes: 0 a card was rendered, or the source correctly licensed no card
(both are success -- silence is a valid, intentional outcome here); 1 the
source envelope was malformed or not this contract at all, so nothing could
be evaluated; 2 the CLI arguments themselves were invalid.

Added in v0.2.0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .lab_card import (
    ENVELOPE_SCHEMA,
    LabCardError,
    LabFact,
    card_from_lab_result,
    is_completed_evidence,
)
from .render import Card, RenderError, render

ENVELOPE = "hermes.reliability-lab.result/1"
TOOL = "agent-signage"

STATUS_ORDER = ("pass", "warn", "unknown", "fail")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def input_hash(value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return "sha256:%s" % digest


def finding(
    identifier: str,
    severity: str,
    summary: str,
    detail: Optional[str] = None,
    path: Optional[str] = None,
) -> dict:
    if severity not in STATUS_ORDER:
        raise ValueError("unknown severity: %s" % severity)
    result: dict = {"id": identifier, "severity": severity, "summary": summary}
    if detail is not None:
        result["detail"] = detail
    if path is not None:
        result["path"] = path
    return result


def worst_status(findings: list) -> str:
    status = "pass"
    for item in findings:
        if STATUS_ORDER.index(item["severity"]) > STATUS_ORDER.index(status):
            status = item["severity"]
    return status


def _git(start: Path, *arguments: str):
    try:
        return subprocess.run(
            ["git", "-C", str(start), *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def git_sha(start: Path) -> Optional[str]:
    head = _git(start, "rev-parse", "HEAD")
    if head is None or head.returncode or not head.stdout.strip():
        return None
    sha = head.stdout.strip()
    status = _git(start, "status", "--porcelain")
    if status is None or status.returncode:
        return sha
    return "%s-dirty" % sha if status.stdout.strip() else sha


def _timestamp(now: Optional[datetime] = None) -> str:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _card_dict(card: Card) -> dict:
    return {"id": card.id, "headline": card.headline, "fact": card.fact, "next": card.next}


def _envelope(
    command: str, findings: list, inputs: Any, exit_code: int, data: Any
) -> dict:
    return {
        "envelope": ENVELOPE,
        "tool": TOOL,
        "toolVersion": __version__,
        "command": command,
        # A real function call over the given input; nothing is simulated.
        "mode": "executed",
        "status": worst_status(findings),
        "inputHash": input_hash(inputs),
        "findings": findings,
        "exitCode": exit_code,
        "timestamp": _timestamp(),
        "gitSha": git_sha(Path(__file__).resolve().parent),
        "data": data,
    }


def load_source_envelope(path: Path) -> Any:
    """Read and parse the source envelope file; raise ValueError on any problem.

    Deliberately narrow: a JSON parse failure and a schema mismatch are both
    just "this is not usable evidence" from this module's point of view --
    the caller-facing distinction lives in the finding id, not in a different
    exception type.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("cannot read source envelope: %s" % exc) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("source envelope is not valid JSON: %s" % exc) from exc
    return parsed


def envelope_for(
    source_path: Path,
    *,
    card_id: str,
    headline: str,
    next_step: str,
    fact_label: Optional[str] = None,
    fact_value: Optional[str] = None,
) -> dict:
    """Render a completion card from a source envelope file, or report why not.

    Reads exactly one file. Writes nothing. Makes no network call.
    """
    inputs = {
        "command": "card",
        "sourcePath": source_path.name,
        "id": card_id,
        "headline": headline,
        "next": next_step,
        "factLabel": fact_label,
        "factValue": fact_value,
    }
    effects = {"writes": "none", "network": "none"}

    if not source_path.is_file():
        findings = [
            finding(
                "source.not-found",
                "fail",
                "source envelope not found: %s" % source_path.name,
                None,
                "source:%s" % source_path.name,
            )
        ]
        return _envelope(
            "card",
            findings,
            inputs,
            1,
            {"sourceName": source_path.name, "source": None, "card": None, "licensed": False, "effects": effects},
        )

    try:
        source = load_source_envelope(source_path)
    except ValueError as error:
        findings = [
            finding(
                "source.unreadable",
                "fail",
                str(error),
                None,
                "source:%s" % source_path.name,
            )
        ]
        return _envelope(
            "card",
            findings,
            inputs,
            1,
            {"sourceName": source_path.name, "source": None, "card": None, "licensed": False, "effects": effects},
        )

    source_summary = {
        "envelope": source.get("envelope") if isinstance(source, dict) else None,
        "tool": source.get("tool") if isinstance(source, dict) else None,
        "toolVersion": source.get("toolVersion") if isinstance(source, dict) else None,
        "command": source.get("command") if isinstance(source, dict) else None,
        "mode": source.get("mode") if isinstance(source, dict) else None,
        "status": source.get("status") if isinstance(source, dict) else None,
        "exitCode": source.get("exitCode") if isinstance(source, dict) else None,
    }

    if not isinstance(source, dict) or source.get("envelope") != ENVELOPE_SCHEMA:
        findings = [
            finding(
                "source.wrong-schema",
                "fail",
                "source is not a %s record" % ENVELOPE_SCHEMA,
                "found envelope=%r" % (source.get("envelope") if isinstance(source, dict) else source),
                "source:%s" % source_path.name,
            )
        ]
        return _envelope(
            "card",
            findings,
            inputs,
            1,
            {
                "sourceName": source_path.name,
                "source": source_summary,
                "card": None,
                "licensed": False,
                "effects": effects,
            },
        )

    source_findings = source.get("findings")
    if isinstance(source_findings, list) and all(
        isinstance(item, dict) and item.get("severity") in STATUS_ORDER for item in source_findings
    ):
        computed_status = worst_status(source_findings)
        if computed_status != source_summary["status"]:
            findings = [
                finding(
                    "source.contradictory-status",
                    "fail",
                    "source status %r disagrees with its own findings (worst finding is %r)"
                    % (source_summary["status"], computed_status),
                    "A status that does not match the worst finding present is not valid evidence "
                    "under the shared contract; this file was not trusted for a completion card.",
                    "source:%s" % source_path.name,
                )
            ]
            return _envelope(
                "card",
                findings,
                inputs,
                1,
                {
                    "sourceName": source_path.name,
                    "source": source_summary,
                    "card": None,
                    "licensed": False,
                    "effects": effects,
                },
            )

    fact: Optional[LabFact] = None
    if fact_label is not None or fact_value is not None:
        if fact_label is None or fact_value is None:
            findings = [
                finding(
                    "input.incomplete-fact",
                    "fail",
                    "a declared fact needs both --fact-label and --fact-value",
                    None,
                )
            ]
            return _envelope(
                "card",
                findings,
                inputs,
                1,
                {
                    "sourceName": source_path.name,
                    "source": source_summary,
                    "card": None,
                    "licensed": False,
                    "effects": effects,
                },
            )
        fact = LabFact(label=fact_label, value=fact_value)

    licensed = is_completed_evidence(source)
    if not licensed:
        findings = [
            finding(
                "source.not-completed-evidence",
                "unknown",
                "source envelope does not license a completion card "
                "(mode=%r status=%r)" % (source_summary["mode"], source_summary["status"]),
                "A card is issued only for mode=executed and status=pass; anything else "
                "is reported as no card, not as a failure of this tool.",
                "source:%s" % source_path.name,
            )
        ]
        return _envelope(
            "card",
            findings,
            inputs,
            0,
            {
                "sourceName": source_path.name,
                "source": source_summary,
                "card": None,
                "licensed": False,
                "effects": effects,
            },
        )

    try:
        card = card_from_lab_result(
            source, id=card_id, headline=headline, next=next_step, fact=fact
        )
    except (RenderError, LabCardError) as error:
        findings = [finding("card.rejected", "fail", str(error))]
        return _envelope(
            "card",
            findings,
            inputs,
            1,
            {
                "sourceName": source_path.name,
                "source": source_summary,
                "card": None,
                "licensed": True,
                "effects": effects,
            },
        )

    assert card is not None  # licensed was True, so card_from_lab_result cannot return None here
    findings = [
        finding(
            "card.rendered",
            "pass",
            "completion card rendered from %s %s" % (source_summary["tool"], source_summary["toolVersion"]),
            "fact declared: %s" % ("%s %s" % (fact_label, fact_value) if fact else "(none)"),
            "source:%s" % source_path.name,
        )
    ]
    return _envelope(
        "card",
        findings,
        inputs,
        0,
        {
            "sourceName": source_path.name,
            "source": source_summary,
            "card": _card_dict(card),
            "rendered": {
                "text": render(card, "text"),
                "json": json.dumps(_card_dict(card), indent=2, sort_keys=True, ensure_ascii=False),
                "hook": render(card, "hook"),
            },
            "licensed": True,
            "effects": effects,
        },
    )


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent_signage.evidence",
        description=(
            "Render a completion card from a Hermes Reliability Lab result envelope, and "
            "print the outcome (card or no card) as a Reliability Lab envelope."
        ),
    )
    parser.add_argument("--source", required=True, help="Path to a source hermes.reliability-lab.result/1 JSON file.")
    parser.add_argument("--id", dest="card_id", required=True, help="Card id (lowercase slug).")
    parser.add_argument("--headline", required=True)
    parser.add_argument("--next", dest="next_step", required=True)
    parser.add_argument("--fact-label", default=None)
    parser.add_argument("--fact-value", default=None)
    args = parser.parse_args(argv)

    result = envelope_for(
        Path(args.source),
        card_id=args.card_id,
        headline=args.headline,
        next_step=args.next_step,
        fact_label=args.fact_label,
        fact_value=args.fact_value,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result["exitCode"]


if __name__ == "__main__":
    sys.exit(main())
