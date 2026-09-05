"""CLI. `python -m agent_signage` reads a hook payload on stdin.

Subcommands exist for the operations a human or agent needs outside the hot
path: acknowledging a sign, clearing state, proving the install works, and
asking why it is quiet in a particular repository.
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Optional

from . import __version__, gitfacts, hook, more_signs, signs, state  # noqa: F401


def _cmd_ack(args) -> int:
    repo = gitfacts.repo_root(args.repo) or os.path.abspath(args.repo)
    p = state.acknowledge(repo, args.sign, args.token)
    print("acknowledged %s for %s (%s)" % (args.sign, repo, p))
    return 0


def _cmd_clear(args) -> int:
    print("cleared %d stamp(s) from %s" % (state.clear(), state.state_dir()))
    return 0


def _cmd_signs(args) -> int:
    for name in signs.registered():
        print(name)
    return 0


def _dedupe_is_state_bound() -> bool:
    """A stamp for one observed state must not suppress a different one.

    Written against a scratch state directory so `selftest` cannot disturb a
    live session's stamps, and restored afterwards.
    """
    import shutil
    import tempfile

    previous = os.environ.get("AGENT_SIGNAGE_STATE_DIR")
    scratch = tempfile.mkdtemp(prefix="agent-signage-selftest-")
    os.environ["AGENT_SIGNAGE_STATE_DIR"] = scratch
    try:
        state.mark_signed("s", "/repo", "stale_checkout", "origin/main@aaaa:3")
        return (
            state.already_signed("s", "/repo", "stale_checkout", "origin/main@aaaa:3")
            and not state.already_signed("s", "/repo", "stale_checkout", "origin/main@bbbb:9")
        )
    finally:
        if previous is None:
            os.environ.pop("AGENT_SIGNAGE_STATE_DIR", None)
        else:
            os.environ["AGENT_SIGNAGE_STATE_DIR"] = previous
        shutil.rmtree(scratch, ignore_errors=True)


def _doctor_imports() -> bool:
    try:
        from . import doctor

        return callable(doctor.report)
    except Exception:
        return False


def _cmd_selftest(args) -> int:
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
    check("all signs registered", len(signs.registered()) >= 6)
    check(
        "envelope shape is correct",
        hook.build_output(["t"])["hookSpecificOutput"]["hookEventName"] == "PreToolUse",
    )

    # The properties 0.1.2 corrected. Each of these was advertised before it was
    # true, so each is now asserted somewhere a consumer actually runs.
    check(
        "deadline reaches the signs",
        signs.Context(session_id="s", tool_name="", target_path="/x",
                      deadline=time.time() - 1).out_of_time(),
    )
    check(
        "no deadline means no limit",
        not signs.Context(session_id="s", tool_name="", target_path="/x").out_of_time(),
    )
    check(
        "evaluation stops at the deadline",
        signs.evaluate(signs.Context(session_id="s", tool_name="", target_path="/x",
                                     deadline=time.time() - 1)) == [],
    )
    check(
        "session dedupe is bound to state",
        _dedupe_is_state_bound(),
    )
    check("doctor is importable", _doctor_imports())

    width = max(len(n) for n, _ in checks)
    for name, ok in checks:
        print("%s %s" % ("PASS" if ok else "FAIL", name.ljust(width)))
    failed = [n for n, ok in checks if not ok]
    print("-" * (width + 5))
    print("%d/%d passed" % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


def _cmd_doctor(args) -> int:
    """Explain this repository: what is wired, what speaks, what it costs.

    Exit status is about the *install*, not about the repository: a repo with
    nothing wrong is the expected case and is not a failure. Only a missing hook
    entry -- the state where silence is misleading rather than informative --
    exits non-zero.
    """
    from . import doctor

    os.environ.setdefault("AGENT_SIGNAGE_NO_FETCH", "1")
    runs = doctor.TIMING_RUNS if args.runs is None else max(1, args.runs)
    lines, wired = doctor.report(args.path, runs=runs)
    print("\n".join(lines))
    return 0 if wired else 1


_MATCHER = "Read|Edit|Write|NotebookEdit|Grep|Glob"


def _cmd_install(args) -> int:
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
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

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
    import argparse

    # Hot path: invoked as a hook with no arguments. Skip argparse and the
    # subcommand wiring entirely -- this runs in front of every file operation,
    # so the common case must not pay for the CLI it is not using.
    if argv is None and len(sys.argv) == 1:
        from . import hook as _hook

        return _hook.main()

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

    d = sub.add_parser("doctor", help="why is it quiet in this repo, and what does it cost")
    d.add_argument("path", nargs="?", default=".")
    # Resolved in _cmd_doctor so the default lives in one place and importing
    # `doctor` stays off the argument-parsing path.
    d.add_argument("--runs", type=int, default=None)
    d.set_defaults(func=_cmd_doctor)

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
