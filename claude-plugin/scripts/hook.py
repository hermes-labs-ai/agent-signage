#!/usr/bin/env python3
"""Claude Code plugin entry point for agent-signage's PreToolUse hook.

Delegates to `agent_signage.hook.main()`, which reads the hook payload on
stdin and does the actual work (see ../src/agent_signage/hook.py). This
wrapper exists only to locate the package:

  * The plugin ships its small, dependency-free runtime under `../src/`, so a
    marketplace install works without a separate pip install or repository
    checkout.
  * If the bundled runtime is absent, an already-installed package is still
    accepted as a compatibility fallback.

Consistent with the rest of agent-signage, this never blocks: if the package
can't be found either way, it exits 0 and says nothing, exactly like every
other fail-open path in the hook (see src/agent_signage/hook.py's module
docstring: malformed input, missing git, an unwritable state dir all end in
silence and exit 0 -- a missing install is the same kind of absence).
"""
from __future__ import annotations

import sys
from pathlib import Path


def _prepend_plugin_src() -> None:
    """Use the runtime bundled in this plugin before any global install."""
    try:
        source = Path(__file__).resolve().parents[1] / "src"
    except IndexError:
        return
    if source.is_dir():
        sys.path.insert(0, str(source))


def main() -> int:
    _prepend_plugin_src()
    try:
        import agent_signage  # noqa: F401
    except ImportError:
        return 0  # no bundled or installed package - fail open, say nothing

    from agent_signage import hook

    return hook.main()


if __name__ == "__main__":
    raise SystemExit(main())
