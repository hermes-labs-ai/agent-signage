"""The publisher: this module runs `gh`, and owns the whole path to GitHub.

An earlier revision only *prepared* an argv and handed it back. An independent
review was right that this is not a boundary. Whatever the caller does with a
prepared command is unobservable here, and the bytes on disk can change between
the check and the send, so "checked" and "published" were never the same bytes
by construction.

This module closes that by owning execution. One supported surface, two
operations, and a fixed order that cannot be reassembled wrongly by a caller:

  1. Open the body **once** on a bounded file descriptor -- `O_NOFOLLOW`, so a
     symlink is refused at open rather than resolved, `fstat` on the descriptor
     rather than the path, and a read capped past the limit so an oversized file
     is detected instead of streamed. Everything downstream uses that snapshot.
  2. Validate the snapshot's decoded text. Not the file: the snapshot. There is
     no second read, so there is no window in which the checked bytes and the
     sent bytes can differ.
  3. For edits, pre-read the live body and bind conventional disclosure
     trailers present in that snapshot.
  4. Emit the action-time sign, before any mutating child exists.
  5. Run `gh` with an argv list -- never a shell string -- and `--body-file -`,
     handing the snapshot bytes to its stdin. `gh` is never given the path, so
     `gh` cannot re-read a file that changed after step 2.
  6. Read the body back with `gh pr view --json body` and require exact
     equality with the snapshot text.

Success is claimed only after step 6. A `gh` failure at step 5 is reported with
its exit status and stderr, not swallowed; a mismatch at step 6 is reported as a
mismatch, and says plainly that the pull request exists and nothing was reverted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import contribution, preflight, render

OPS = ("pr-create", "pr-edit")

EXIT_PASS = 0
EXIT_REJECT = 1        # artifact rejected; no mutating gh child was started
EXIT_INPUT = 2         # input or usage rejected; no gh child was started
EXIT_CHILD = 3         # a gh child failed; its status is reported, not reused. On
                       # pr-edit this includes the read-only pre-read, after which
                       # no update is attempted.
EXIT_READBACK = 4      # gh succeeded but the published body is not the checked body

READ_CHUNK = 65536
_REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}")
_REF_RE = re.compile(r"[A-Za-z0-9._/-]{1,200}")
_HEAD_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_PR_URL_RE = re.compile(r"https://[^\s]+/pull/\d+")
_DISCLOSURE_RE = re.compile(
    r"^(?:Co-Authored-By|Signed-off-by|Reviewed-by|Tested-by|Acked-by|Reported-by|"
    r"Disclosure|Attribution):\s*\S.*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class Snapshot:
    """The bytes. Taken once, and the only thing anything downstream sees."""
    path: str
    data: bytes
    text: str
    sha256: str


@dataclass(frozen=True)
class Request:
    op: str
    target: str
    kind: str
    oversight: str
    pr: Optional[int] = None
    title: Optional[str] = None
    base: Optional[str] = None
    head: Optional[str] = None
    preserve: Tuple[str, ...] = ()
    gh: str = "gh"
    timeout: float = 120.0
    selection: Optional[str] = None


@dataclass
class Result:
    exit_code: int
    gh_invocations: List[List[str]] = field(default_factory=list)
    url: Optional[str] = None
    child_status: Optional[int] = None


# ------------------------------------------------------------------ snapshot

def snapshot(path_value: str) -> Snapshot:
    """Open once, bounded, no symlink, regular file only. Never re-read."""
    if not Path(path_value).is_absolute():
        raise preflight.PreflightError("body path must be absolute")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        fd = os.open(path_value, flags)
    except OSError as exc:
        raise preflight.PreflightError("cannot open body file: %s" % exc) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise preflight.PreflightError("body path must name a regular file")
        chunks = []
        total = 0
        while total <= preflight.MAX_ARTIFACT_BYTES:
            chunk = os.read(fd, READ_CHUNK)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    finally:
        os.close(fd)

    data = b"".join(chunks)
    if len(data) > preflight.MAX_ARTIFACT_BYTES:
        raise preflight.PreflightError(
            "body file exceeds %d bytes" % preflight.MAX_ARTIFACT_BYTES)
    if not data:
        raise preflight.PreflightError("body file is empty")
    if b"\x00" in data:
        raise preflight.PreflightError("body file contains a NUL byte")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise preflight.PreflightError("body file is not valid UTF-8: %s" % exc) from exc
    return Snapshot(
        path=str(Path(path_value)),
        data=data,
        text=text,
        sha256=hashlib.sha256(data).hexdigest(),
    )


# ---------------------------------------------------------------- validation

def _valid_head(head: str) -> bool:
    """Accept a plain branch or gh's explicit user:branch fork selector."""
    if ":" in head:
        owner, head = head.split(":", 1)
        if _HEAD_OWNER_RE.fullmatch(owner) is None or "--" in owner:
            return False
    if (_REF_RE.fullmatch(head) is None or head.startswith("-")
            or head.endswith(".") or ".." in head):
        return False
    return all(part and not part.startswith(".") and not part.endswith(".lock")
               for part in head.split("/"))


def validate_request(request: Request) -> None:
    if request.op not in OPS:
        raise preflight.PreflightError("operation must be one of %s" % ", ".join(OPS))
    if _REPO_RE.fullmatch(request.target or "") is None:
        raise preflight.PreflightError("--target must be owner/repository")
    if request.kind not in preflight.KINDS:
        raise preflight.PreflightError("--kind must be one of %s" % ", ".join(preflight.KINDS))
    if request.oversight not in preflight.OVERSIGHT_LEVELS:
        raise preflight.PreflightError(
            "--oversight must be given explicitly as one of %s"
            % ", ".join(preflight.OVERSIGHT_LEVELS))
    if request.selection is not None:
        if request.selection not in contribution.SELECTIONS or request.kind != "contribution":
            raise preflight.PreflightError("--selection requires a contribution and autonomous, owner, or unspecified")
    if request.op == "pr-create":
        title = request.title or ""
        if not 1 <= len(title) <= 200 or title.startswith("-"):
            raise preflight.PreflightError("--title must be 1-200 characters and not start with '-'")
        if any(char in title for char in "\n\r\t") or preflight._forbidden_characters(title):
            raise preflight.PreflightError("--title must be a single printable line")
        if request.base is not None and (
            _REF_RE.fullmatch(request.base) is None or request.base.startswith("-")
        ):
            raise preflight.PreflightError("--base must be a plain git ref name")
        if request.head is not None and not _valid_head(request.head):
            raise preflight.PreflightError("--head must be a valid branch or GitHub user:branch")
    else:
        if request.pr is None or not 1 <= request.pr <= 10_000_000:
            raise preflight.PreflightError("--pr must be a positive pull request number")


def validate_snapshot(snap: Snapshot) -> None:
    """Defend the public Snapshot API against inconsistent constructed values."""
    if not isinstance(snap.data, bytes) or not snap.data:
        raise preflight.PreflightError("snapshot data must be non-empty bytes")
    if len(snap.data) > preflight.MAX_ARTIFACT_BYTES:
        raise preflight.PreflightError(
            "snapshot data exceeds %d bytes" % preflight.MAX_ARTIFACT_BYTES)
    if b"\x00" in snap.data:
        raise preflight.PreflightError("snapshot data contains a NUL byte")
    if not isinstance(snap.path, str) or not Path(snap.path).is_absolute():
        raise preflight.PreflightError("snapshot path must be absolute")
    try:
        decoded = snap.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise preflight.PreflightError("snapshot data is not valid UTF-8: %s" % exc) from exc
    if decoded != snap.text:
        raise preflight.PreflightError("snapshot text is not the decoding of snapshot data")
    if hashlib.sha256(snap.data).hexdigest() != snap.sha256:
        raise preflight.PreflightError("snapshot sha256 does not match snapshot data")


def _gh_target(request: Request) -> str:
    return "github.com/" + request.target if request.selection is not None else request.target


def publish_argv(request: Request) -> List[str]:
    """argv, never a shell string, and always `--body-file -`.

    The dash is load-bearing. Handing `gh` a path would let it read the file
    again, after the check, which is the exact window this module exists to
    remove.
    """
    if request.op == "pr-create":
        argv = [request.gh, "pr", "create", "--repo", _gh_target(request),
                "--title", request.title or "", "--body-file", "-"]
        if request.base is not None:
            argv += ["--base", request.base]
        if request.head is not None:
            argv += ["--head", request.head]
        return argv
    return [request.gh, "pr", "edit", str(request.pr), "--repo", _gh_target(request),
            "--body-file", "-"]


# ------------------------------------------------------------ the action sign

def action_sign(snap: Snapshot, request: Request) -> str:
    """One true fact at the moment of action, rendered by the shared renderer."""
    card = render.Card(
        id="publication.boundary",
        headline="PUBLIC UPDATE",
        fact=(
            "agent-signage is publishing %d checked bytes (sha256 %s) to %s as a %s with "
            "declared oversight %r. The attribution was validated on these exact bytes and "
            "gh receives them on stdin, so nothing re-reads the file."
            % (len(snap.data), snap.sha256[:12], _gh_target(request), request.kind,
               request.oversight)
        ),
        next=(
            "Success is reported only after the published body is read back and matches "
            "byte for byte."
        ),
    )
    return render.render_text(card, context="%s %s" % (request.op, snap.path))


# ------------------------------------------------------------------ execution

def _run(argv: Sequence[str], payload: bytes, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv),
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "replace").strip()


def _body_from_view(child: subprocess.CompletedProcess) -> Optional[str]:
    if child.returncode != 0:
        return None
    try:
        payload = json.loads(_decode(child.stdout))
    except ValueError:
        return None
    return payload.get("body") if isinstance(payload, dict) and isinstance(
        payload.get("body"), str) else None


def protected_disclosures(text: str) -> Tuple[str, ...]:
    """Conventional human/upstream disclosure trailers in an existing body."""
    return tuple(dict.fromkeys(match.group(0).strip() for match in _DISCLOSURE_RE.finditer(text)))


def publish_snapshot(snap: Snapshot, request: Request, stream=None) -> Result:
    """Check, sign, send, read back. The order is the contract."""
    out = stream if stream is not None else sys.stdout
    validate_snapshot(snap)
    validate_request(request)
    result = Result(exit_code=EXIT_PASS)

    def check_body(preserve):
        if request.selection is not None:
            return contribution.check(snap.text, request.selection, preserve)
        return preflight.check(snap.text, kind=request.kind,
                               oversight=request.oversight, preserve=preserve)

    verdict = check_body(request.preserve)
    if not verdict.ok:
        # Nothing below this point runs. No child is started, so a rejected
        # artifact has no effect of any kind on the remote.
        print("REJECT  %s" % snap.path, file=out)
        print("  sha256   %s" % snap.sha256, file=out)
        for reason in verdict.reasons:
            print("  reject   %s: %s" % (reason.code, reason.detail), file=out)
        print("  nothing was published; gh was not invoked", file=out)
        out.flush()
        result.exit_code = EXIT_REJECT
        return result

    if request.selection is not None:
        # Explicit github.com target below and the same gh environment bind this
        # read-only identity check to the account used for the mutating child.
        identity = [request.gh, "api", "--hostname", "github.com", "user", "--jq", ".login"]
        result.gh_invocations.append(identity)
        try:
            account = _run(identity, b"", request.timeout)
        except (OSError, subprocess.SubprocessError):
            account = None
        if (account is None or account.returncode != 0
                or _decode(account.stdout) != contribution.ACCOUNT):
            print("REJECT  authenticated github.com account must be roli-lpci for this footer; "
                  "resolve the publishing identity through the contribution decision route. "
                  "No update was attempted.", file=out)
            out.flush()
            result.exit_code = EXIT_REJECT
            return result

    if request.op == "pr-edit":
        # Bind recognizable disclosures to the live body before overwriting it.
        # This read is deliberately after the local attribution check, so a
        # malformed new artifact still starts no child, and before the sign or
        # any mutating command. `--preserve` remains available for project-
        # specific disclosure lines outside the conventional trailer forms.
        before_argv = [request.gh, "pr", "view", str(request.pr), "--repo",
                       _gh_target(request), "--json", "body"]
        result.gh_invocations.append(list(before_argv))
        try:
            before = _run(before_argv, b"", request.timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            print("FAILED  existing pull request body could not be read: %s" % exc,
                  file=out)
            print("  No update was attempted.", file=out)
            out.flush()
            result.exit_code = EXIT_CHILD
            return result
        previous = _body_from_view(before)
        if previous is None:
            result.child_status = before.returncode
            print("FAILED  existing pull request body could not be read: %s"
                  % (_decode(before.stderr) or "unreadable gh pr view output"), file=out)
            print("  No update was attempted.", file=out)
            out.flush()
            result.exit_code = EXIT_CHILD
            return result
        live_disclosures = protected_disclosures(previous)
        if live_disclosures:
            bound = check_body(request.preserve + live_disclosures)
            dropped = [reason for reason in bound.reasons
                       if reason.code == "disclosure-dropped"]
            if dropped:
                print("REJECT  %s" % snap.path, file=out)
                print("  sha256   %s" % snap.sha256, file=out)
                for reason in dropped:
                    print("  reject   %s: %s" % (reason.code, reason.detail), file=out)
                print("  nothing was published; only a read-only gh pr view was invoked",
                      file=out)
                out.flush()
                result.exit_code = EXIT_REJECT
                return result

    print(action_sign(snap, request), file=out)
    out.flush()

    argv = publish_argv(request)
    result.gh_invocations.append(list(argv))
    try:
        child = _run(argv, snap.data, request.timeout)
    except subprocess.TimeoutExpired as exc:
        print("UNCERTAIN  gh timed out after it was started: %s" % exc, file=out)
        print("  The pull request may exist or may have changed. Nothing was reverted.", file=out)
        print("  Inspect GitHub before retrying; this is not a successful publication.", file=out)
        out.flush()
        result.exit_code = EXIT_CHILD
        return result
    except OSError as exc:
        print("FAILED  gh could not be run: %s" % exc, file=out)
        out.flush()
        result.exit_code = EXIT_CHILD
        return result
    except subprocess.SubprocessError as exc:
        print("UNCERTAIN  gh failed after it was started: %s" % exc, file=out)
        print("  The pull request may exist or may have changed. Nothing was reverted.", file=out)
        print("  Inspect GitHub before retrying; this is not a successful publication.", file=out)
        out.flush()
        result.exit_code = EXIT_CHILD
        return result

    result.child_status = child.returncode
    if child.returncode != 0:
        print("FAILED  gh exited %d; nothing is claimed to have been published"
              % child.returncode, file=out)
        for line in _decode(child.stderr).splitlines():
            print("  gh: %s" % line, file=out)
        print("  The pull request may exist or may have changed. Nothing was reverted.", file=out)
        print("  Inspect GitHub before retrying; this is not a successful publication.", file=out)
        out.flush()
        result.exit_code = EXIT_CHILD
        return result

    selector, url = _selector(request, _decode(child.stdout))
    result.url = url
    if selector is None:
        print("UNVERIFIED  gh exited 0 but printed no pull request URL to read back",
              file=out)
        print("  The pull request may exist. Nothing was reverted; inspect GitHub before retrying.",
              file=out)
        out.flush()
        result.exit_code = EXIT_READBACK
        return result

    view = [request.gh, "pr", "view", selector, "--repo", _gh_target(request), "--json", "body"]
    result.gh_invocations.append(list(view))
    try:
        readback = _run(view, b"", request.timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        readback = None
        detail = str(exc)
    else:
        detail = _decode(readback.stderr)

    published = _body_from_view(readback) if readback is not None else None

    if published is None:
        print("UNVERIFIED  %s was updated but the body could not be read back: %s"
              % (url or selector, detail or "unreadable gh pr view output"), file=out)
        print("  Nothing was reverted. Inspect the pull request before relying on it.",
              file=out)
        out.flush()
        result.exit_code = EXIT_READBACK
        return result

    if published != snap.text:
        print("MISMATCH  %s exists but its body is not the checked bytes" % (url or selector),
              file=out)
        print("  checked   sha256 %s (%d bytes)" % (snap.sha256, len(snap.data)), file=out)
        print("  published sha256 %s (%d bytes)"
              % (hashlib.sha256(published.encode("utf-8")).hexdigest(),
                 len(published.encode("utf-8"))), file=out)
        print("  Nothing was reverted, and this is not a successful publication.", file=out)
        out.flush()
        result.exit_code = EXIT_READBACK
        return result

    print("PUBLISHED  %s" % (url or selector), file=out)
    print("  body read back and matched byte for byte (sha256 %s)" % snap.sha256, file=out)
    out.flush()
    return result


def _selector(request: Request, stdout: str) -> Tuple[Optional[str], Optional[str]]:
    """What to ask `gh pr view` about, and the URL to show a human."""
    match = _PR_URL_RE.search(stdout)
    url = match.group(0) if match else None
    if request.op == "pr-edit":
        return str(request.pr), url
    return url, url


def publish(body_file: str, request: Request, stream=None) -> Result:
    return publish_snapshot(snapshot(body_file), request, stream=stream)


# ------------------------------------------------------------------- the CLI

def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("op", choices=OPS)
    bodies = parser.add_mutually_exclusive_group(required=True)
    bodies.add_argument("--body", help="exact inline PR body")
    bodies.add_argument("--body-file",
                        help="absolute path to the exact artifact to publish")
    parser.add_argument("--target", required=True, metavar="OWNER/REPO")
    parser.add_argument("--kind", required=True, choices=preflight.KINDS,
                        help="declared by the caller; nothing here verifies it")
    # No default, deliberately. Claiming active human oversight is the strongest
    # statement this tool will publish about a person, so it is always chosen.
    parser.add_argument("--oversight", required=True, choices=preflight.OVERSIGHT_LEVELS,
                        help="declared by the caller; 'active' is never assumed")
    parser.add_argument("--selection", choices=contribution.SELECTIONS,
                        help="enforce the approved contribution footer and personal GitHub account")
    parser.add_argument("--pr", type=int, default=None, help="pull request number, for pr-edit")
    parser.add_argument("--title", default=None, help="title, for pr-create")
    parser.add_argument("--base", default=None, help="base ref, for pr-create")
    parser.add_argument("--head", default=None, help="head branch or GitHub user:branch, for pr-create")
    parser.add_argument("--preserve", action="append", default=[], metavar="LINE",
                        help="a disclosure line that must survive (repeatable)")
    parser.add_argument("--gh", default="gh", help="path to the gh executable")
    return parser


def run_args(args: argparse.Namespace) -> int:
    request = Request(
        op=args.op,
        target=args.target,
        kind=args.kind,
        oversight=args.oversight,
        pr=args.pr,
        title=args.title,
        base=args.base,
        head=args.head,
        preserve=tuple(args.preserve),
        gh=args.gh,
        selection=args.selection,
    )
    validate_request(request)
    if args.body is not None:
        data = args.body.encode("utf-8")
        snap = Snapshot("/inline-pr-body", data, args.body, hashlib.sha256(data).hexdigest())
        return publish_snapshot(snap, request).exit_code
    return publish(args.body_file, request).exit_code


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-signage-publish",
        description="Publish a checked artifact to a GitHub pull request via gh.",
        epilog="0 published and verified, 1 artifact rejected, 2 input rejected, "
               "3 a gh child failed (a failed pr-edit pre-read means no update was attempted), "
               "4 published body could not be verified.",
    )
    add_arguments(parser)
    args = parser.parse_args(None if argv is None else list(argv))
    try:
        return run_args(args)
    except preflight.PreflightError as exc:
        print("agent-signage publish: %s" % exc, file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
