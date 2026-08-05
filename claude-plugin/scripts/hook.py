#!/usr/bin/env python3
"""Claude Code plugin entry point for agent-signage's PreToolUse hook.

Delegates to `agent_signage.hook.main()`, which reads the hook payload on
stdin and does the actual work (see ../../src/agent_signage/hook.py). This
wrapper exists only to locate the package:

  * If agent-signage is already installed (e.g. `pip install agent-signage`),
    the normal import succeeds and nothing further happens here.
  * If this plugin is enabled from a checkout of the agent-signage repository
    itself, the adjacent `src/` two directories up is added to `sys.path`, so
    the plugin works without a separate install step.

Consistent with the rest of agent-signage, this never blocks: if the package
can't be found either way, it exits 0 and says nothing, exactly like every
other fail-open path in the hook (see src/agent_signage/hook.py's module
docstring: malformed input, missing git, an unwritable state dir all end in
silence and exit 0 -- a missing install is the same kind of absence).
"""
from __future__ import annotations

import sys
from pathlib import Path


def _prepend_adjacent_src() -> None:
    """Use the repo's own src/ when this plugin runs from a clone of it."""
    try:
        source = Path(__file__).resolve().parents[2] / "src"
    except IndexError:
        return
    if source.is_dir():
        sys.path.insert(0, str(source))


def main() -> int:
    try:
        import agent_signage  # noqa: F401
    except ImportError:
        _prepend_adjacent_src()

    try:
        from agent_signage import hook
    except ImportError:
        return 0  # not installed and no adjacent checkout - fail open, say nothing

    return hook.main()


if __name__ == "__main__":
    raise SystemExit(main())
