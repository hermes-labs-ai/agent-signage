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
import re
import sys
import time
from typing import Any, Dict, List, Optional

# Whole-invocation ceiling. Past this the hook stops and says nothing, so a
# pathological repository can never stall a file read.
DEADLINE_S = 3.0

_SESSION_START_ENV = "AGENT_SIGNAGE_SESSION_START"
_PATCH_FILE_HEADER = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+?)\s*$")
_MAX_PATCH_BYTES = 1_000_000
_MAX_PATCH_PATHS = 16


def _patch_paths(payload: Dict[str, Any]) -> List[str]:
    """Extract only explicit apply_patch file headers; ignore patch body text."""
    if payload.get("tool_name") != "apply_patch":
        return []
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return []
    command = ti.get("command")
    if not isinstance(command, str) or len(command.encode("utf-8", "ignore")) > _MAX_PATCH_BYTES:
        return []
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return []
    paths = []
    for line in command.splitlines():
        match = _PATCH_FILE_HEADER.match(line)
        if not match:
            continue
        path = match.group(1).strip()
        if not path or "\x00" in path:
            continue
        # Absolute paths remain absolute; relative paths are interpreted from
        # Codex's reported working directory (which may itself contain spaces).
        resolved = os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))
        if resolved not in paths:
            paths.append(resolved)
        if len(paths) >= _MAX_PATCH_PATHS:
            break
    return paths


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

    # Codex apply_patch exposes the unified patch in tool_input.command rather
    # than a single path. Re-enter the ordinary path hook for explicit file
    # headers so it uses the same signs, dedupe, deadline, and fail-open path.
    if not _target_path(payload):
        paths = _patch_paths(payload)
        if not paths:
            return None
        texts = []
        for path in paths:
            per_file = dict(payload)
            ti = dict(payload["tool_input"])
            ti["file_path"] = path
            per_file["tool_input"] = ti
            try:
                result = run(json.dumps(per_file), now=started)
            except Exception:
                continue
            if result:
                context = result.get("hookSpecificOutput", {}).get("additionalContext")
                if isinstance(context, str) and context and context not in texts:
                    texts.append(context)
        return build_output(texts) if texts else None

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
    from . import gitfacts, more_signs, signs, state  # noqa: F401  (import registers signs)

    # Memoisation inside the signs is only valid for one evaluation: repository
    # state changes between tool calls. Reset it here so embedding this in a
    # long-lived process is as correct as the one-shot subprocess case.
    gitfacts.clear_caches()
    more_signs.clear_caches()

    ctx = signs.Context(
        session_id=session_id,
        tool_name=str(payload.get("tool_name") or ""),
        target_path=target,
        session_started=_session_started(),
        # The deadline is handed to the signs rather than applied to their
        # result. Checking it here, after everything had already run, bounded
        # nothing: the work was done and the only thing the check could still do
        # was throw away a true fact that had cost the time anyway. Passing it in
        # lets evaluation stop early and still report what it measured.
        deadline=started + DEADLINE_S,
    )

    found = signs.evaluate(ctx)
    if not found:
        return None

    for s in found:
        state.mark_signed(ctx.session_id, s.repo, s.id, s.state_token)

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
