"""CLI. `python -m agent_signage` reads a hook payload on stdin.

Subcommands exist for the operations a human or agent needs outside the hot
path: acknowledging a sign, clearing state, and proving the install works.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__, hook, signs, state


def _cmd_ack(args: argparse.Namespace) -> int:
    p = state.acknowledge(args.repo, args.sign, args.token)
    print("acknowledged %s for %s (%s)" % (args.sign, args.repo, p))
    return 0


def _cmd_clear(args: argparse.Namespace) -> int:
    print("cleared %d stamp(s) from %s" % (state.clear(), state.state_dir()))
    return 0


def _cmd_signs(args: argparse.Namespace) -> int:
    for name in signs.registered():
        print(name)
    return 0


def _cmd_selftest(args: argparse.Namespace) -> int:
    """Assert the guarantees without needing a repository.

    These are the properties a consumer is trusting; if any regress, this exits
    non-zero and CI fails.
    """
    checks = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, ok))

    check("empty input is silent", hook.run("") is None)
    check("malformed json is silent", hook.run("not json") is None)
    check("json array is silent", hook.run("[1,2,3]") is None)
    check("no tool_input is silent", hook.run('{"session_id":"x"}') is None)
    check("empty tool_input is silent", hook.run('{"tool_input":{}}') is None)
    check("blank path is silent", hook.run('{"tool_input":{"file_path":"   "}}') is None)
    check(
        "nonexistent path is silent",
        hook.run('{"tool_input":{"file_path":"/nonexistent/zz/q.txt"}}') is None,
    )
    check(
        "vendored path is silent",
        hook.run('{"tool_input":{"file_path":"/tmp/x/node_modules/p/i.js"}}') is None,
    )
    check("at least one sign registered", len(signs.registered()) >= 1)
    check(
        "envelope shape is correct",
        hook.build_output(["t"])["hookSpecificOutput"]["hookEventName"] == "PreToolUse",
    )

    width = max(len(n) for n, _ in checks)
    for name, ok in checks:
        print("%s %s" % ("PASS" if ok else "FAIL", name.ljust(width)))
    failed = [n for n, ok in checks if not ok]
    print("-" * (width + 5))
    print("%d/%d passed" % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


_MATCHER = "Read|Edit|Write|NotebookEdit|Grep|Glob"


def _cmd_install(args: argparse.Namespace) -> int:
    """Add the PreToolUse entry to a Claude Code settings file.

    Idempotent: re-running never duplicates the entry. Writes a timestamped
    backup first, because this edits a file the user did not write.
    """
    import json
    import shutil
    import time as _t

    path = os.path.expanduser(args.settings)
    data = {}
    if os.path.exists(path):
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            print("cannot read %s: %s" % (path, e), file=sys.stderr)
            return 1
    if not isinstance(data, dict):
        print("%s is not a JSON object" % path, file=sys.stderr)
        return 1

    entry = {
        "matcher": _MATCHER,
        "hooks": [{"type": "command", "command": args.command, "timeout": args.timeout}],
    }

    hooks = data.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre, list):
        print("hooks.PreToolUse in %s is not a list" % path, file=sys.stderr)
        return 1

    for group in pre:
        for h in (group or {}).get("hooks", []):
            if isinstance(h, dict) and args.command in str(h.get("command", "")):
                print("already installed in %s - nothing to do" % path)
                return 0

    if os.path.exists(path):
        backup = "%s.bak-%s" % (path, _t.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
        print("backed up  %s" % backup)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)

    pre.append(entry)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)

    print("installed   %s" % path)
    print("  matcher   %s" % _MATCHER)
    print("  command   %s" % args.command)
    print("\nRemove it by deleting that entry, or restore the backup above.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="agent-signage",
        description="Road signs for coding agents. Reads a hook payload on stdin.",
    )
    ap.add_argument("--version", action="version", version="agent-signage %s" % __version__)
    sub = ap.add_subparsers(dest="cmd")

    a = sub.add_parser("ack", help="silence a sign until the observed state changes")
    a.add_argument("repo")
    a.add_argument("sign")
    a.add_argument("token")
    a.set_defaults(func=_cmd_ack)

    c = sub.add_parser("clear", help="remove all session and acknowledgement stamps")
    c.set_defaults(func=_cmd_clear)

    s = sub.add_parser("signs", help="list registered signs")
    s.set_defaults(func=_cmd_signs)

    t = sub.add_parser("selftest", help="assert the runtime guarantees")
    t.set_defaults(func=_cmd_selftest)

    i = sub.add_parser("install", help="add the hook to a Claude Code settings file")
    i.add_argument("--settings", default="~/.claude/settings.json")
    i.add_argument("--command", default="python3 -m agent_signage")
    i.add_argument("--timeout", type=int, default=8)
    i.set_defaults(func=_cmd_install)

    args = ap.parse_args(argv)
    if getattr(args, "func", None) is not None:
        return args.func(args)
    return hook.main()


if __name__ == "__main__":
    sys.exit(main())
