"""CLI. `python -m agent_signage` reads a hook payload on stdin.

Subcommands exist for the operations a human or agent needs outside the hot
path: acknowledging a sign, clearing state, proving the install works, asking
why it is quiet in a particular repository, and -- separately from all of that --
running the explicitly invoked publication preflight, which is the only
subcommand here that is allowed to fail closed.
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


def _hook_source() -> str:
    import os as _os

    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "hook.py")
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


def _boundary_properties() -> dict:
    """Exercise the publication boundary on synthetic input, with no files.

    Each entry is a property a consumer is trusting: that the checker refuses an
    artifact with no attribution or one whose claim exceeds what the caller
    declared, that a block hidden in a code fence does not count as a statement,
    that the generated wording passes its own check, and that the Bash adapter
    denies exactly the two guarded calls and nothing else.
    """
    from . import gate, preflight, publish

    block = preflight.attribution_block("contribution", "active")
    fenced = "```\n%s\n```" % block

    def codes(text, **kw):
        return preflight.check(text, **kw).codes

    return {
        "missing": "attribution-missing" in codes(
            "Fixes #1.\n", kind="contribution", oversight="active"),
        "beyond": "oversight-claimed-beyond-declaration" in codes(
            block, kind="contribution", oversight="none"),
        "hidden": "attribution-hidden" in codes(
            fenced, kind="contribution", oversight="active"),
        "generated": preflight.check(block, kind="contribution", oversight="active").ok,
        "deny": (gate.guarded_subcommand("make x && gh pr create -t t") == "create"
                 and gate.guarded_subcommand("gh pr edit 3 --body x") == "edit"),
        "silent": all(gate.guarded_subcommand(c) is None for c in
                      ("ls -la", "gh pr view 3", "git push", "gh issue create",
                       "gh pr list --search pr --label create")),
        "codex": gate.run(
            '{"tool_name":"Bash","tool_input":{"cmd":"gh pr create --repo '
            'hermes-labs-ai/r --title t"}}'
        )["hookSpecificOutput"]["permissionDecision"] == "deny",
        "ops": publish.OPS == ("pr-create", "pr-edit"),
        "stdin": publish.publish_argv(
            publish.Request(op="pr-edit", target="o/r", kind="contribution",
                            oversight="none", pr=1))[-2:] == ["--body-file", "-"],
    }


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

    # The publication boundary. It is the one part of this tool allowed to fail
    # closed, so its separation from the fail-open hook is asserted here rather
    # than left to a comment.
    # `main()` imports these before dispatch, so a registration count taken here
    # would be vacuous; the registry is inspected by name instead.
    from . import gate, preflight, publish  # noqa: F401

    check(
        "boundary registers no sign",
        not any(word in name for name in signs.registered()
                for word in ("preflight", "publish", "publication", "gate")),
    )
    hook_source = _hook_source()
    # An unreadable hook.py must fail this check, not vacuously pass it.
    check(
        "the hook cannot reach the boundary",
        bool(hook_source) and "preflight" not in hook_source
        and "publish" not in hook_source and "gate" not in hook_source,
    )
    _b = _boundary_properties()
    check("checker rejects a missing attribution", _b["missing"])
    check("checker rejects a claim beyond the declaration", _b["beyond"])
    check("checker rejects a block hidden in a fence", _b["hidden"])
    check("generated wording passes its own check", _b["generated"])
    check("bash adapter denies direct gh pr create/edit", _b["deny"])
    check("bash adapter is silent on unrelated commands", _b["silent"])
    check("bash adapter accepts the Codex cmd payload", _b["codex"])
    check("publisher supports exactly pr-create and pr-edit", _b["ops"])
    check("publisher always sends bytes on stdin", _b["stdin"])

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


def _cmd_preflight(args) -> int:
    """Check an artifact. Explicitly invoked, and allowed to exit non-zero.

    Deliberately not a sign and not reachable from `hook.py`, which fails open
    by contract: a gate routed through a hook that cannot exit non-zero would
    present enforcement it is structurally incapable of.
    """
    from . import preflight

    try:
        return preflight.run_args(args)
    except preflight.PreflightError as exc:
        print("agent-signage preflight: %s" % exc, file=sys.stderr)
        return 2


def _cmd_publish(args) -> int:
    """Check, sign, send and read back. This is the path that owns execution."""
    from . import preflight, publish

    try:
        return publish.run_args(args)
    except preflight.PreflightError as exc:
        print("agent-signage publish: %s" % exc, file=sys.stderr)
        return publish.EXIT_INPUT


def _cmd_attribution(args) -> int:
    from . import preflight

    try:
        return preflight.run_attribution(args)
    except preflight.PreflightError as exc:
        print("agent-signage attribution: %s" % exc, file=sys.stderr)
        return 2


def _cmd_gate(args) -> int:
    """The PreToolUse Bash boundary adapter, reading a payload on stdin."""
    from . import gate

    return gate.main()


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


_CODEX_MATCHER = "^Bash$"
_CODEX_STATUS = "Checking public contribution attribution"


def _default_publication_gate_command() -> str:
    """A stable absolute interpreter path for the installed package."""
    import shlex

    return shlex.join([sys.executable, "-m", "agent_signage", "gate"])


def _cmd_install_publication_gate(args) -> int:
    """Add the narrow publication gate to Codex without replacing other hooks.

    User hook files may already carry security or receipt checks. This is an
    additive, idempotent edit with a backup and atomic replace; the existing
    Hermes Gate group (or any other group) remains byte-for-byte equivalent in
    the decoded JSON structure.
    """
    import json
    import shutil
    import stat
    import time as _t

    path = os.path.abspath(os.path.expanduser(args.hooks))
    command = args.command or _default_publication_gate_command()
    if not isinstance(args.timeout, int) or not 1 <= args.timeout <= 60:
        print("--timeout must be an integer from 1 to 60", file=sys.stderr)
        return 1
    if os.path.islink(path):
        print("refusing symlink hook path %s; install into its explicit target" % path,
              file=sys.stderr)
        return 1

    data = {}
    mode = 0o600
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except (OSError, ValueError) as exc:
            print("cannot read %s: %s" % (path, exc), file=sys.stderr)
            return 1
    if not isinstance(data, dict):
        print("%s is not a JSON object" % path, file=sys.stderr)
        return 1

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        print("hooks in %s is not an object" % path, file=sys.stderr)
        return 1
    pre = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre, list):
        print("hooks.PreToolUse in %s is not a list" % path, file=sys.stderr)
        return 1

    for group in pre:
        if not isinstance(group, dict):
            continue
        if group.get("matcher") != _CODEX_MATCHER:
            continue
        handlers = group.get("hooks", [])
        if not isinstance(handlers, list):
            continue
        if any(isinstance(handler, dict) and handler.get("type") == "command"
               and handler.get("command") == command
               for handler in handlers):
            print("already installed in %s - nothing to do" % path)
            return 0
        for handler in handlers:
            existing = handler.get("command") if isinstance(handler, dict) else None
            if isinstance(existing, str) and existing.endswith("-m agent_signage gate"):
                print("note: another agent-signage gate handler is already present and "
                      "will remain: %s" % existing)

    entry = {
        "matcher": _CODEX_MATCHER,
        "hooks": [{
            "type": "command",
            "command": command,
            "timeout": args.timeout,
            "statusMessage": _CODEX_STATUS,
        }],
    }

    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    backup = None
    if os.path.exists(path):
        backup = "%s.bak-%s-%d" % (
            path, _t.strftime("%Y%m%d-%H%M%S"), os.getpid())
        try:
            shutil.copy2(path, backup)
        except OSError as exc:
            print("cannot back up %s: %s" % (path, exc), file=sys.stderr)
            return 1

    pre.append(entry)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print("cannot install publication gate in %s: %s" % (path, exc), file=sys.stderr)
        return 1

    if backup:
        print("backed up  %s" % backup)
    print("installed   %s" % path)
    print("  matcher   %s" % _CODEX_MATCHER)
    print("  command   %s" % command)
    print("Restart Codex, then review and trust the new hook with /hooks.")
    if backup:
        print("Recovery: replace %s with %s." % (path, backup))
    else:
        print("Recovery: remove the matching PreToolUse group from %s." % path)
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

    ip = sub.add_parser(
        "install-publication-gate",
        help="add the gh PR publication boundary to the Codex user hooks file",
    )
    ip.add_argument("--hooks", default="~/.codex/hooks.json")
    ip.add_argument("--command", default=None,
                    help="hook command (defaults to this installation's interpreter)")
    ip.add_argument("--timeout", type=int, default=5)
    ip.set_defaults(func=_cmd_install_publication_gate)

    from . import preflight as _preflight
    from . import publish as _publish

    p = sub.add_parser(
        "preflight",
        help="check an artifact's attribution without publishing anything",
        description="Check only. `agent-signage publish` is what sends bytes to GitHub.",
    )
    _preflight.add_arguments(p)
    p.set_defaults(func=_cmd_preflight)

    pub = sub.add_parser(
        "publish",
        help="publish a checked artifact to a GitHub pull request through gh",
        description=(
            "Snapshot the body once, check it, show the sign, run gh with the same bytes "
            "on stdin, then read the published body back and require exact equality."
        ),
        epilog="0 published and verified, 1 artifact rejected, 2 input rejected, "
               "3 a gh child failed (a failed pr-edit pre-read means no update was attempted), "
               "4 published body could not be verified.",
    )
    _publish.add_arguments(pub)
    pub.set_defaults(func=_cmd_publish)

    b = sub.add_parser("attribution", help="print the attribution block to paste into a body")
    _preflight.add_attribution_arguments(b)
    b.set_defaults(func=_cmd_attribution)

    g = sub.add_parser(
        "gate",
        help="PreToolUse Bash boundary adapter: deny direct gh pr create/edit",
    )
    g.set_defaults(func=_cmd_gate)

    args = ap.parse_args(argv)
    if getattr(args, "func", None) is not None:
        return args.func(args)
    return hook.main()


if __name__ == "__main__":
    sys.exit(main())
