"""`agent-signage doctor` -- why is it quiet here?

A tool whose whole design is to say nothing most of the time has one structural
adoption problem: silence is indistinguishable from a broken install. That is
not a hypothesis about this project. It is the standard reported experience of
Claude Code hooks generally, where the usual debugging advice is to append a
line to a log file and tail it in another terminal, and where a hook that is
silently not firing at all -- wrong matcher, wrong path, not executable -- looks
exactly like a hook that has nothing to report.

`doctor` answers the question directly, for one repository, from measurements:

  * is the hook wired into a settings file at all, and with what matcher
  * what git actually says about this repository right now
  * for every registered sign: does it speak here, and if not, which measured
    fact makes it quiet
  * what an invocation costs on this machine, in this repository

It has no side effects. It writes no session stamps, and it sets
AGENT_SIGNAGE_NO_FETCH for the duration so that inspecting the tool cannot
start a fetch in the repository being inspected.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from . import __version__, gitfacts, more_signs, signs, state

# Settings files Claude Code reads, in the order it merges them.
SETTINGS_PATHS = (
    "~/.claude/settings.json",
    "~/.claude/settings.local.json",
    ".claude/settings.json",
    ".claude/settings.local.json",
)

# Runs used for the latency figure. Enough for a p95 to mean something, few
# enough that `doctor` stays interactive.
TIMING_RUNS = 20

# Cap on the tracked-file listing scanned to pick a representative sample file.
SAMPLE_SCAN_LIMIT = 2000


# ------------------------------------------------------------------ facts

def _installs() -> List[Dict[str, Any]]:
    """Every settings file with a PreToolUse hook that names this package."""
    found = []
    for raw in SETTINGS_PATHS:
        path = os.path.abspath(os.path.expanduser(raw))
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for group in (data.get("hooks") or {}).get("PreToolUse") or []:
            if not isinstance(group, dict):
                continue
            for h in group.get("hooks") or []:
                cmd = str((h or {}).get("command", ""))
                if "agent_signage" in cmd or "agent-signage" in cmd:
                    found.append({
                        "settings": path,
                        "matcher": group.get("matcher", ""),
                        "command": cmd,
                        "timeout": (h or {}).get("timeout"),
                    })
    return found


def _sample_file(root: str, given: str) -> Optional[str]:
    """A file in this repo worth evaluating the file-shaped signs against.

    Prefers what the caller named, then a file with uncommitted changes -- the
    thing an agent is most likely to be about to touch -- and falls back to the
    most recently modified tracked file.
    """
    if os.path.isfile(given):
        return os.path.abspath(given)

    changed = gitfacts._git(root, "diff", "--name-only", "HEAD")
    for rel in (changed or "").splitlines():
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return p

    listing = gitfacts._git(root, "ls-files")
    newest: Tuple[float, Optional[str]] = (-1.0, None)
    for rel in (listing or "").splitlines()[:SAMPLE_SCAN_LIMIT]:
        p = os.path.join(root, rel)
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if m > newest[0]:
            newest = (m, p)
    return newest[1]


def collect(target: str, ttl: float = signs.DEFAULT_FETCH_TTL_S) -> Dict[str, Any]:
    """Every fact `doctor` reports. Measurements only; no side effects."""
    gitfacts.clear_caches()
    more_signs.clear_caches()

    f: Dict[str, Any] = {"target": os.path.abspath(target), "installs": _installs()}
    root = gitfacts.repo_root(f["target"])
    f["root"] = root
    if root is None:
        return f

    f["ignored"] = os.path.abspath(root) in signs._ignored_repos()
    f["vendored"] = bool(signs.VENDORED_RE.search(f["target"]))
    gitdir = gitfacts.git_dir(root)
    f["git_dir"] = gitdir
    f["branch"] = gitfacts.current_branch(root)
    f["upstream"] = gitfacts.upstream_ref(root)
    f["in_progress"] = gitfacts.in_progress(gitdir).name if gitdir else None
    f["fetch_age_s"] = gitfacts.fetch_age_seconds(gitdir) if gitdir else None
    f["fetch_is_stale"] = f["fetch_age_s"] is None or f["fetch_age_s"] > ttl
    f["behind"] = f["ahead"] = None
    f["stale_checkout_acked"] = False
    if f["upstream"]:
        f["behind"] = gitfacts.commits_behind(root, f["upstream"])
        f["ahead"] = gitfacts.commits_ahead(root, f["upstream"])
        if f["behind"]:
            # Re-derive the sign's own state token so an acknowledgement can be
            # named as the reason. Without this, `doctor` would fall through to
            # "already reported this session" -- which is never true here, since
            # it evaluates under a session id nothing else uses.
            sha = gitfacts.upstream_sha(root, f["upstream"]) or "unknown"
            token = "%s@%s:%d" % (f["upstream"], sha[:12], f["behind"])
            f["stale_checkout_acked"] = state.is_acknowledged(root, "stale_checkout", token)
            f["stale_checkout_token"] = token

    listing = gitfacts._git(root, "worktree", "list", "--porcelain") or ""
    wts = [ln[len("worktree "):].strip() for ln in listing.splitlines()
           if ln.startswith("worktree ")]
    f["worktrees"] = [w for w in wts if os.path.realpath(w) != os.path.realpath(root)]

    sample = _sample_file(root, f["target"])
    f["sample"] = sample
    f["sample_is_symlink"] = bool(sample and os.path.abspath(sample) != os.path.realpath(sample))
    return f


def evaluate_here(facts: Dict[str, Any]) -> Dict[str, str]:
    """What each sign says about the sample file, via the real sign functions.

    Uses a session id nothing else will ever use, so once-per-session dedupe
    cannot make `doctor` under-report, and a write-shaped tool name, so the
    signs that only fire on writes are exercised rather than shown as inert for
    a reason that has nothing to do with this repository.
    """
    if not facts.get("sample"):
        return {}
    ctx = signs.Context(
        session_id="doctor-%d-%d" % (os.getpid(), int(time.time() * 1000)),
        tool_name="Edit",
        target_path=facts["sample"],
        allow_background_fetch=False,
    )
    gitfacts.clear_caches()
    more_signs.clear_caches()
    return {s.id: s.text for s in signs.evaluate(ctx)}


def quiet_reason(sign_id: str, f: Dict[str, Any]) -> str:
    """The measured fact that makes `sign_id` quiet in this repository.

    Every registered sign must have an entry; `test_every_sign_has_a_doctor_
    reason` fails if one is added without one, so this cannot silently rot into
    a shorter list than the registry.
    """
    name = os.path.basename(f.get("sample") or "the sampled file")
    if f.get("ignored"):
        return "this repo is listed in AGENT_SIGNAGE_IGNORE"
    if f.get("vendored"):
        return "the target path is vendored or build output"

    if sign_id == "stale_checkout":
        if not f.get("branch"):
            return "HEAD is detached, so being behind a branch is the point"
        if f.get("in_progress"):
            return "a %s is in progress; this tree is behind on purpose" % f["in_progress"]
        if not f.get("upstream"):
            return "no upstream is configured for %s -- nothing to compare against" % f["branch"]
        if not f.get("behind"):
            return "level with %s as of %s" % (
                f["upstream"],
                "the clone" if f["fetch_age_s"] is None
                else "a fetch %s ago" % signs.human_age(f["fetch_age_s"]),
            )
        if f.get("stale_checkout_acked"):
            return "%d behind, but acknowledged -- undo with: agent-signage clear" % f["behind"]
        return "measurably behind but not speaking; this is unexpected, please report it"
    if sign_id == "symlink_escape":
        if not f.get("sample_is_symlink"):
            return "%s is not reached through a symlink" % name
        return "the symlink resolves back inside this repo"
    if sign_id == "conflict_markers":
        return "no conflict markers in the first %dKB of %s" % (
            more_signs.SAMPLE_BYTES // 1024, name)
    if sign_id == "concurrent_worktree_edit":
        if not f.get("worktrees"):
            return "this repo has no other worktree"
        return "%d other worktree(s), none holding uncommitted changes to %s" % (
            len(f["worktrees"]), name)
    if sign_id == "binary_edit":
        return "%s has no NUL bytes" % name
    if sign_id == "generated_file":
        return "%s declares no generator in its first 5 lines" % name
    return "no measured condition to report"


def time_invocation(target: str, runs: int = TIMING_RUNS) -> Optional[Dict[str, float]]:
    """End-to-end subprocess cost, which is what a harness actually pays.

    Timed the way it is really invoked -- a fresh interpreter per call, reading
    a payload on stdin -- rather than by calling `run()` in a warm process,
    which would omit interpreter startup and understate it by roughly 16ms.

    Two details keep the number honest. Each run gets its own session id, so no
    run is measuring the cheap already-signed path that the previous one set up;
    without that, only the first of N runs times the case anyone cares about.
    And the children are pointed at a throwaway state directory, so inspecting
    the tool cannot leave stamps that suppress a later real session.
    """
    env = dict(os.environ)
    env["AGENT_SIGNAGE_NO_FETCH"] = "1"
    scratch = tempfile.mkdtemp(prefix="agent-signage-doctor-")
    env["AGENT_SIGNAGE_STATE_DIR"] = scratch
    samples: List[float] = []
    try:
        for i in range(runs):
            payload = json.dumps({
                "session_id": "doctor-timing-%d-%d" % (os.getpid(), i),
                "tool_name": "Edit",
                "tool_input": {"file_path": target},
            })
            t0 = time.time()
            try:
                subprocess.run(
                    [sys.executable, "-m", "agent_signage"],
                    input=payload.encode(), stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, env=env, timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                return None
            samples.append((time.time() - t0) * 1000.0)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    samples.sort()

    def pct(p: float) -> float:
        return round(samples[min(len(samples) - 1, int(p * len(samples)))], 1)

    return {"runs": float(runs), "p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99),
            "max": round(samples[-1], 1)}


# ----------------------------------------------------------------- reporting

def _line(label: str, value: str) -> str:
    return "  %-16s %s" % (label, value)


def report(target: str, runs: int = TIMING_RUNS) -> Tuple[List[str], bool]:
    """Render the report. Returns (lines, everything_looks_wired)."""
    out: List[str] = ["agent-signage %s" % __version__, ""]
    f = collect(target)

    out.append("install")
    if f["installs"]:
        for i in f["installs"]:
            out.append(_line("settings", i["settings"]))
            out.append(_line("matcher", i["matcher"] or "(none -- matches every tool)"))
            out.append(_line("command", i["command"]))
    else:
        out.append(_line("settings", "no PreToolUse hook naming agent-signage was found in:"))
        for p in SETTINGS_PATHS:
            out.append(_line("", os.path.expanduser(p)))
        out.append(_line("", "-> silence here means it is not wired. Run: agent-signage install"))
    out.append("")

    if f["root"] is None:
        out.append("repository")
        out.append(_line("", "%s is not inside a git repository." % f["target"]))
        out.append(_line("", "Every sign is git-derived, so all of them are quiet here"))
        out.append(_line("", "by design. This is not a fault."))
        return out, bool(f["installs"])

    out.append("repository")
    out.append(_line("root", f["root"]))
    out.append(_line("branch", f["branch"] or "(detached HEAD)"))
    out.append(_line("upstream", f["upstream"] or "(none configured)"))
    if f["upstream"]:
        when = ("never -- remote-tracking refs are as `git clone` left them"
                if f["fetch_age_s"] is None
                else "%s ago" % signs.human_age(f["fetch_age_s"]))
        if f["fetch_is_stale"]:
            when += "  (past the %s refresh threshold, so the count below is dated)" % \
                signs.human_age(signs.DEFAULT_FETCH_TTL_S)
        out.append(_line("last fetch", when))
        out.append(_line("behind / ahead", "%s / %s  (as measured at that point)"
                         % (f["behind"], f["ahead"])))
    out.append(_line("worktrees", "%d other" % len(f["worktrees"]) if f["worktrees"] else "none"))
    if f["in_progress"]:
        out.append(_line("in progress", f["in_progress"]))
    if f["ignored"]:
        out.append(_line("ignored", "yes -- AGENT_SIGNAGE_IGNORE lists this repo"))
    out.append(_line("sampled file", f["sample"] or "(no tracked file found)"))
    out.append("")

    speaking = evaluate_here(f)
    out.append("signs")
    for sid in signs.registered():
        if sid in speaking:
            out.append("  %-24s SPEAKS" % sid)
            out.append("      %s" % speaking[sid])
        else:
            out.append("  %-24s quiet    %s" % (sid, quiet_reason(sid, f)))
    out.append("")
    out.append("  A quiet sign is a working sign with nothing to report. Silence never")
    out.append("  means verified-current; it means no drift is known.")
    out.append("")

    t = time_invocation(f["sample"] or f["root"], runs=runs)
    out.append("cost")
    if t:
        out.append(_line("per tool call", "p50 %.0fms  p95 %.0fms  p99 %.0fms  max %.0fms"
                         % (t["p50"], t["p95"], t["p99"], t["max"])))
        out.append(_line("", "%d end-to-end subprocess runs against the sampled file"
                         % int(t["runs"])))
    else:
        out.append(_line("per tool call", "could not measure (subprocess failed)"))
    out.append("")

    d = state.state_dir()
    try:
        stamps = len([n for n in os.listdir(d) if n.split("-")[0] in ("sess", "ack", "fetch")])
    except OSError:
        stamps = 0
    out.append("state")
    out.append(_line("directory", d))
    out.append(_line("stamps", "%d  (clear with: agent-signage clear)" % stamps))
    return out, bool(f["installs"])
