"""The hook entry point: JSON on stdin, JSON or nothing on stdout.

Contract with the harness, and the reason this is safe to put in front of every
file operation:

  * It never blocks. It never emits a block decision and never exits non-zero.
    A hook that can veto an edit is a different, much riskier category of tool;
    agent-signage is deliberately not in it.
  * It fails open. Every error path -- malformed input, missing git, a hung
    subprocess, an unwritable state dir -- ends in silence and exit 0.
  * It is bounded. A global deadline caps the whole invocation regardless of
    what any individual call does.

Input is the standard PreToolUse payload. Both `tool_input.file_path` (Read,
Edit, Write, NotebookEdit) and `tool_input.path` (Grep, Glob) are accepted,
because the risk attaches to touching the repository at all -- reading a stale
file and reasoning from it is precisely the failure this exists to catch, and
an edit-only trigger fires too late to help.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional


# Whole-invocation ceiling. Past this the hook stops and says nothing, so a
# pathological repository can never stall a file read.
DEADLINE_S = 3.0

_SESSION_START_ENV = "AGENT_SIGNAGE_SESSION_START"


def _target_path(payload: Dict[str, Any]) -> Optional[str]:
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return None
    for key in ("file_path", "path", "notebook_path"):
        v = ti.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return None


def _session_started() -> Optional[float]:
    raw = os.environ.get(_SESSION_START_ENV)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def build_output(texts: List[str]) -> Dict[str, Any]:
    """Wrap sign text in the PreToolUse additionalContext envelope.

    Verified 2026-08-05 against a live isolated session: text emitted this way
    is delivered to the model alongside the tool result, and is presented as
    content rather than as an instruction. Signs are therefore written as
    statements of fact, not as commands.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "\n".join(texts),
        }
    }


def run(raw: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Core logic. Returns the output dict, or None to stay silent."""
    started = now if now is not None else time.time()

    try:
        payload = json.loads(raw) if raw.strip() else None
    except (ValueError, AttributeError):
        return None
    if not isinstance(payload, dict):
        return None

    target = _target_path(payload)
    if not target:
        return None

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        # No session id means dedupe would leak across sessions. Falling back to
        # the parent pid keeps the scope to this process tree rather than
        # making the stamp effectively permanent.
        session_id = "pid-%d" % os.getppid()

    # Deferred: importing the git layer costs real milliseconds and is wasted
    # when the payload has no usable path, which is common.
    from . import signs, state

    ctx = signs.Context(
        session_id=session_id,
        tool_name=str(payload.get("tool_name") or ""),
        target_path=target,
        session_started=_session_started(),
    )

    found = signs.evaluate(ctx)
    if not found:
        return None
    if time.time() - started > DEADLINE_S:
        return None

    for s in found:
        state.mark_signed(ctx.session_id, s.repo, s.id)

    return build_output([s.text for s in found])


def main(argv: Optional[List[str]] = None) -> int:
    """Always returns 0. There is no failure mode that should stop a tool call."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    try:
        out = run(raw)
    except Exception:
        return 0
    if out is not None:
        try:
            sys.stdout.write(json.dumps(out))
        except Exception:
            return 0
    return 0
