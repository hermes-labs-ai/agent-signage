"""Fail-open GitHub Copilot CLI adapter for the agent-signage file checks.

Copilot CLI's native ``postToolUse`` event uses camelCase fields. This adapter
accepts only successful calls to built-in file tools and a single path
argument, delegates the check to the shared signage runtime, and translates
its result to Copilot's documented ``additionalContext`` field. It never
returns a permission decision.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional

_TOOLS = {"view": "Read", "edit": "Edit", "create": "Write"}
_PATH_FIELDS = ("path", "filePath", "file_path")


def _args(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    return value if isinstance(value, dict) else None


def build_payload(raw: str) -> Optional[str]:
    """Translate a Copilot CLI postToolUse payload to the shared hook format."""
    try:
        event = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(event, dict):
        return None

    tool_name = event.get("toolName")
    mapped_tool = _TOOLS.get(tool_name) if isinstance(tool_name, str) else None
    cwd = event.get("cwd")
    if not mapped_tool or not isinstance(cwd, str) or not cwd.strip():
        return None
    result = event.get("toolResult")
    if not isinstance(result, dict) or result.get("resultType") != "success":
        return None

    args = _args(event.get("toolArgs"))
    if args is None:
        return None
    target = next(
        (args.get(key) for key in _PATH_FIELDS
         if isinstance(args.get(key), str) and args[key].strip()),
        None,
    )
    if target is None:
        return None

    # Copilot paths are relative to the hook's working directory. Make that
    # explicit because the shared checks may inspect symlink and checkout state.
    target = os.path.expanduser(target)
    if not os.path.isabs(target):
        target = os.path.abspath(os.path.join(cwd, target))

    return json.dumps({
        "session_id": event.get("sessionId"),
        "tool_name": mapped_tool,
        "tool_input": {"file_path": target},
    })


def run(raw: str) -> Optional[Dict[str, str]]:
    """Return Copilot context for a measured sign, or silence on every other path."""
    translated = build_payload(raw)
    if translated is None:
        return None

    # A Copilot hook must not initiate network work. The underlying checks use
    # refs already on disk and the sign itself explains how to refresh them.
    os.environ["AGENT_SIGNAGE_NO_FETCH"] = "1"
    try:
        from . import hook

        output = hook.run(translated)
    except Exception:
        return None
    if not isinstance(output, dict):
        return None

    specific = output.get("hookSpecificOutput")
    context = specific.get("additionalContext") if isinstance(specific, dict) else None
    if not isinstance(context, str) or not context.strip():
        return None
    return {"additionalContext": context}


def main() -> int:
    """Read one CLI event; all failures exit successfully and emit no decision."""
    try:
        raw = sys.stdin.read()
        output = run(raw)
        if output is not None:
            sys.stdout.write(json.dumps(output) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
