"""PreToolUse Bash boundary adapter for external PR-body and comment publication.

The publisher only owns the path that goes through it. An agent with a shell can
reach `gh` without it, so a checkable verdict is not yet a chokepoint. This
adapter is the chokepoint for one surface -- the harness's Bash tool -- and it
does exactly one thing: when a Bash command would create a PR, replace its
body, or post an issue/PR comment body (`gh issue comment`, `gh pr comment`)
directly on an external or unresolved target, it denies and names the
publisher. Internal work that targets `hermes-labs-ai/*` is exempt.

Two contracts are deliberately not shared with `hook.py`:

  * `hook.py` fails open and can never deny. That is what makes it safe in front
    of every file read, and it is untouched by this module. This adapter is a
    separate entry point with a separate matcher; nothing here is registered as
    a sign and nothing here runs on the file path.
  * This adapter *can* deny, so its unknown cases resolve conservatively. An
    unparseable command that still looks like a guarded `gh` invocation is
    denied rather than waved through.

Nothing executes the command under judgment. The string is lexed and inspected
as data. Two bounded fixed-argv git measurements (`git rev-parse --show-toplevel`,
then `git remote -v`) may run to establish work context when the command does
not name a target.

The target decides. An explicit target (`--repo`/`-R`, a PR or issue URL, or `GH_REPO`)
is exempt only when every such target is `hermes-labs-ai/*`. Without one, the
call is exempt only when the working checkout is clearly internal: every
remote is a `hermes-labs-ai/*` GitHub repository and the command does not
change directory or git context first. Everything else -- an external target,
a personal fork, a non-repository, an unreadable remote -- is external or
unknown and denied. Metadata-only PR edits, comment commands with no body flag
(`--web`, `--editor`), `gh pr view`, `gh pr list`, `gh issue create`,
`git push`, and unrelated commands are silent -- an adapter that denied broadly
would guard nothing. Raw `gh api` requests (for example a POST to
`repos/O/R/issues/N/comments`) are a known remaining bypass: judging arbitrary
REST paths, methods and field syntax is a parser this adapter does not attempt.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from typing import Any, Dict, List, Optional, Tuple

from . import gitfacts

GUARDED_SUBCOMMANDS = ("create", "edit", "comment")
# The checked publisher operation each denial names.
DENY_OPS = {"create": "pr-create", "edit": "pr-edit", "comment": "issue-comment-create"}
# Owners whose PRs are internal work and exempt from the external attribution
# boundary. Nothing else is: personal forks and upstream targets are external.
INTERNAL_OWNERS = ("hermes-labs-ai",)

# Operators that end one command and start another. `shlex` in punctuation mode
# returns each run of `();<>|&` as its own token, so splitting on these is what
# stops a compound line -- `make test && gh pr create`, `echo hi; gh pr edit 3`,
# `$(gh pr create)` -- from smuggling a guarded call past the first segment.
_OPERATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", "<", ">", ">>", "<<",
              "&&&", ";;", "\n", "`", "$"}

# Stripped from the head of a segment before looking for the executable.
_WRAPPERS = {"sudo", "env", "command", "nohup", "time", "nice", "stdbuf", "exec",
             "builtin", "setsid", "doas"}
_CONTROL_PREFIXES = {"!", "if", "then", "elif", "else", "while", "until", "do"}
_WRAPPER_VALUE_OPTIONS = {
    "sudo": {"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
             "-C", "--close-from", "-D", "--chdir"},
    "doas": {"-u", "-C"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "nice": {"-n", "--adjustment"},
    "stdbuf": {"-i", "--input", "-o", "--output", "-e", "--error"},
    "exec": {"-a"},
    "time": {"-f", "--format", "-o", "--output"},
}

# Leading punctuation left on a token by substitution or grouping syntax:
# `$(gh ...)`, `` `gh ...` ``, `(gh ...)`, `{ gh ...; }`.
_LEAD = "$({`<>"
_TRAIL = ")}`;"

_SHELL_INTERPRETERS = {"bash", "dash", "fish", "ksh", "sh", "zsh"}
_DYNAMIC_GUARDED_RE = re.compile(
    r"(?:^|[;&|(\n])\s*[\"']?"
    r"(?:\$\([^\n)]*\)|`[^\n`]*`|\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*)"
    r"[\"']?\s+(?P<noun>pr|issue)\s+(?P<operation>create|new|edit|comment)\b"
    r"(?P<tail>[^;&|\n]*)",
)

_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# Global gh options whose value may appear before the command noun. Keeping
# these explicit matters: deleting every option token but leaving every value
# behind made `gh pr list --search pr --label create` look like `pr create`.
_GH_VALUE_OPTIONS = {"--hostname", "--repo", "-R"}
# The conservative fallback, used only when the line cannot be tokenised.
_RAW_RE = re.compile(
    r"(?:^|[\s;&|(`$])gh(?:\.exe)?\b[^;&|\n]*?"
    r"(?:\bpr\b[^;&|\n]*?\b(?P<pr>create|new|edit|comment)\b"
    r"|\bissue\b[^;&|\n]*?\b(?P<issue>comment)\b)")
# Explicit targets in a line that could not be tokenised. The fallback reads
# the same three sources as the parsed path so it exempts and denies alike.
_RAW_REPO_FLAG_RE = re.compile(
    r"(?:^|[\s;&|(`'\"])(?:--repo(?:=|\s+)|-R(?:=|\s+)?)['\"]?(?P<value>[^\s'\";&|()`]+)")
# PR and issue URLs: both name the target of `gh pr ...` and `gh issue comment`.
_PR_URL_RE = re.compile(
    r"https?://(?P<host>[^/\s'\"]+)/(?P<owner>[^/\s'\"]+)/[^/\s'\"]+/(?:pull|issues)/\d+",
    re.IGNORECASE)
# gh reads GH_REPO as the target when `--repo` is absent. A mention without a
# readable value (`unset GH_REPO`, `export GH_REPO`) leaves the target unknown.
_GH_REPO_RE = re.compile(r"\bGH_REPO\b(?:=['\"]?(?P<value>[^\s'\";&|()`]*))?")
# Anything that can move gh away from the payload's cwd before it runs makes
# the working checkout no evidence of the target.
# That includes wrapper chdir options (`env -C`, `sudo -D`, `--chdir`); a
# false match only asks for an explicit `--repo`.
_CONTEXT_SHIFT_RE = re.compile(
    r"(?:^|[\s;&|(`'\"])(?:cd|pushd|popd)(?=$|[\s;&|)`'\"])|\bGIT_DIR\b|\bGIT_WORK_TREE\b"
    r"|(?:^|\s)(?:--chdir|-[CD])")
_GITHUB_REMOTE_RE = re.compile(
    r"github\.com(?::|/)([^/]+)/([^/]+?)(?:\.git)?$", re.IGNORECASE)
_BODY_FLAGS = ("--body", "--body-file", "-b", "-F")
_HEREDOC_RE = re.compile(
    r"<<(?P<dash>-)?\s*(?P<token>'[^']+'|\"[^\"]+\"|\\[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+)")
_GH_OPTIONS_WITH_VALUES = {
    "--repo", "-R", "--hostname", "--title", "-t", "--body", "-b", "--body-file", "-F",
    "--base", "-B", "--head", "-H", "--assignee", "-a", "--label", "-l",
    "--milestone", "-m", "--project", "-p", "--reviewer", "-r", "--template", "-T",
    "--recover", "--add-assignee", "--remove-assignee", "--add-label", "--remove-label",
    "--add-project", "--remove-project", "--add-reviewer", "--remove-reviewer",
}

REASON = (
    "agent-signage: direct {direct} is not the supported publication path for an "
    "external or unresolved target. Publish through the checked boundary instead, which "
    "validates the attribution on the exact bytes it sends and verifies the published "
    "body afterwards:\n"
    "  python3 -m agent_signage publish {op} --body-file /abs/body.md "
    "--target OWNER/REPO --kind contribution --oversight none"
    "{extra}\n"
    "Choose `--kind review` or `--oversight active` only when those declarations are true. "
    "`--oversight` has no default and is a caller declaration, not a verified fact. "
    "Read-only gh commands such as `gh pr view` and `gh pr list` are unaffected. "
    "Internal work is exempt only when the target is `hermes-labs-ai/*`: name it with "
    "`--repo hermes-labs-ai/REPO` when the working checkout does not establish it."
)


def _segments(tokens: List[str]) -> List[List[str]]:
    return [segment for _, segment in _segments_with_operators(tokens)]


def _segments_with_operators(
    tokens: List[str],
) -> List[Tuple[Optional[str], List[str]]]:
    """Shell segments paired with the operator immediately before each one."""
    out: List[Tuple[Optional[str], List[str]]] = []
    previous_operator: Optional[str] = None
    current: List[str] = []
    for token in tokens:
        if token in _OPERATORS or (token and all(char in ";&|()<>`$\n" for char in token)):
            if current:
                out.append((previous_operator, current))
                current = []
            previous_operator = token
        else:
            current.append(token)
    if current:
        out.append((previous_operator, current))
    return out


def _executable(segment: List[str]) -> Optional[int]:
    """Index of the token that names the program, past assignments and wrappers."""
    index = 0
    while index < len(segment):
        token = segment[index].lstrip(_LEAD)
        if not token or _ASSIGNMENT_RE.match(token) or token in _CONTROL_PREFIXES:
            index += 1
            continue
        if token in _WRAPPERS:
            wrapper = token
            index += 1
            while index < len(segment):
                option = segment[index].lstrip(_LEAD)
                if option == "--":
                    index += 1
                    break
                if not option.startswith("-") or option == "-":
                    break
                index += 1
                if (option.split("=", 1)[0] in _WRAPPER_VALUE_OPTIONS.get(wrapper, set())
                        and "=" not in option and index < len(segment)):
                    index += 1
            continue
        return index
    return None


def _guarded_subcommand(segment: List[str]) -> Optional[str]:
    """`gh ... pr create|edit` -> the subcommand, else None.

    The command noun and its immediate subcommand are positional. Options that
    gh accepts before the noun are skipped with their values; tokens after the
    immediate subcommand are irrelevant. Thus `gh --repo o/r pr create` is a
    create, while `gh pr list --search pr --label create` remains a list.
    """
    start = _executable(segment)
    if start is None:
        return None
    name = segment[start].lstrip(_LEAD).rstrip(_TRAIL)
    name = name.rsplit("/", 1)[-1]
    if name not in ("gh", "gh.exe"):
        return None
    args = [t.lstrip(_LEAD).rstrip(_TRAIL) for t in segment[start + 1:]]
    if _has_effective_help(args):
        return None
    index = 0
    while index < len(args):
        token = args[index]
        if token in _GH_VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    if index >= len(args) or args[index] not in ("pr", "issue"):
        return None
    noun = args[index]
    index += 1
    while index < len(args) and args[index].startswith("-"):
        if args[index] in _GH_VALUE_OPTIONS and "=" not in args[index]:
            index += 2
        else:
            index += 1
    if index >= len(args):
        return None
    operation = args[index]
    body = _sets_body(args[index + 1:])
    if noun == "issue":
        # `gh issue comment` without a body flag prompts, or opens an editor or
        # browser; only a supplied body is a direct publication.
        return "comment" if operation == "comment" and body else None
    if operation == "new":
        return "create"
    if operation == "create":
        return "create"
    if operation == "edit" and body:
        return "edit"
    if operation == "comment" and body:
        return "comment"
    return None


def _sets_body(args: List[str]) -> bool:
    return any(
        token in _BODY_FLAGS
        or token.startswith("--body=")
        or token.startswith("--body-file=")
        or (token.startswith("-b") and token != "-b")
        or (token.startswith("-F") and token != "-F")
        for token in args
    )


def _raw_operation(match: "re.Match[str]") -> str:
    """The guarded operation an untokenisable `_RAW_RE` match names."""
    operation = match.group("pr") or match.group("issue")
    return "create" if operation == "new" else operation


def _has_effective_help(args: List[str]) -> bool:
    """Help/version flags that are options, not values of another option."""
    index = 0
    while index < len(args):
        token = args[index]
        if token in _GH_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if token in {"--help", "-h", "--version"}:
            return True
        index += 1
    return False


def _token_segments(command: str) -> Optional[List[List[str]]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n`$")
        lexer.whitespace_split = True
        lexer.whitespace = " \t\r"
        lexer.commenters = "#"
        return _segments(list(lexer))
    except ValueError:
        return None


def _token_segments_with_operators(
    command: str,
) -> Optional[List[Tuple[Optional[str], List[str]]]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n`$")
        lexer.whitespace_split = True
        lexer.whitespace = " \t\r"
        lexer.commenters = "#"
        return _segments_with_operators(list(lexer))
    except ValueError:
        return None


def _owner_from_repo(value: str) -> Optional[str]:
    """GitHub owner of `[HOST/]OWNER/REPO` or a github.com URL; None if unreadable.

    A host other than github.com is not an internal target, whatever its owner.
    """
    pieces = value.strip("'\"").removesuffix(".git").split("/")
    if len(pieces) < 2 or not pieces[-1]:
        return None
    if len(pieces) >= 3 and pieces[-3].lower() != "github.com":
        return None
    owner = pieces[-2]
    return owner.lower() if re.fullmatch(r"[A-Za-z0-9_.-]+", owner) else None


def _segment_targets(segment: List[str]) -> List[Optional[str]]:
    """Owners named by `--repo`/`-R` or a PR URL argument in one gh call.

    Values of other options are skipped, so `--title --repo x` names no target
    and `--body https://github.com/o/r/pull/1` is body text, not a target.
    """
    start = _executable(segment)
    args = [t.lstrip(_LEAD).rstrip(_TRAIL) for t in segment[(start or 0) + 1:]]
    targets: List[Optional[str]] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token in {"--repo", "-R"}:
            targets.append(_owner_from_repo(args[index + 1]) if index + 1 < len(args) else None)
            index += 2
            continue
        if token.startswith("--repo="):
            targets.append(_owner_from_repo(token.split("=", 1)[1]))
        elif token.startswith("-R") and len(token) > 2:
            targets.append(_owner_from_repo(token[3:] if token[2] == "=" else token[2:]))
        elif token in _GH_OPTIONS_WITH_VALUES:
            index += 2
            continue
        else:
            url = _PR_URL_RE.match(token.strip("'\""))
            if url:
                targets.append(_url_owner(url))
        index += 1
    return targets


def _url_owner(match: "re.Match[str]") -> Optional[str]:
    """Owner of a PR URL; a host other than github.com is never internal."""
    return match.group("owner").lower() if match.group("host").lower() == "github.com" else None


def _gh_repo_targets(command: str, segment: Optional[List[str]]) -> List[Optional[str]]:
    """GH_REPO targets of one guarded call.

    An inline assignment before that call's program or an unconditional prior
    export binds a value; the call's own arguments and shell comments are data.
    Conditional mutations, `unset`, and lines that cannot be tokenised leave
    the target unknown.
    """
    if segment is None:
        return [None] if _GH_REPO_RE.search(command) else []
    start = _executable(segment)
    if start is None:
        return []

    parsed = _token_segments_with_operators(
        _strip_heredoc_bodies(_remove_line_continuations(command)))
    matches = [] if parsed is None else [
        index for index, (_, candidate) in enumerate(parsed) if candidate == segment]
    states: List[Tuple[bool, Optional[str]]] = []
    assigned = False
    owner: Optional[str] = None
    if matches:
        for candidate_index, (operator, previous) in enumerate(parsed or []):
            if candidate_index in matches:
                states.append((assigned, owner))
            executable = _executable(previous)
            if executable is None:
                # A standalone shell assignment can update a name that was
                # already exported. Its export state is unknowable here, so
                # do not fall back to trusting the checkout.
                if any(token.lstrip(_LEAD).startswith("GH_REPO=")
                       for token in previous):
                    assigned = True
                    owner = None
                continue
            name = previous[executable].lstrip(_LEAD).rstrip(_TRAIL).rsplit("/", 1)[-1]
            args = [token.lstrip(_LEAD).rstrip(_TRAIL)
                    for token in previous[executable + 1:]]
            conditional = (operator not in {None, ";", "\n"}
                           or any(token.lstrip(_LEAD) in _CONTROL_PREFIXES
                                  for token in previous[:executable]))
            if name == "unset" and "GH_REPO" in args:
                assigned = True
                owner = None
            elif name == "export":
                if "-n" in args and "GH_REPO" in args:
                    assigned = False
                    owner = None
                else:
                    for arg in args:
                        if arg == "GH_REPO":
                            assigned = True
                            owner = None
                        elif arg.startswith("GH_REPO="):
                            assigned = True
                            owner = (None if conditional else
                                     _owner_from_repo(arg.split("=", 1)[1]))
    if not states:
        states = [(False, None)]
    index = 0
    while index < start:
        token = segment[index].lstrip(_LEAD)
        if token in _WRAPPERS:
            wrapper = token
            index += 1
            while index < start:
                option = segment[index].lstrip(_LEAD)
                if option == "--":
                    index += 1
                    break
                if not option.startswith("-") or option == "-":
                    break
                name = option.split("=", 1)[0]
                if wrapper == "env" and name in {"-i", "--ignore-environment"}:
                    states = [(False, None) for _ in states]
                if wrapper == "env" and name in {"-u", "--unset"}:
                    unset = (option.split("=", 1)[1] if "=" in option
                             else segment[index + 1] if index + 1 < start else "")
                    if unset == "GH_REPO":
                        states = [(False, None) for _ in states]
                elif wrapper == "env" and option.startswith("-u"):
                    if option[2:] == "GH_REPO":
                        states = [(False, None) for _ in states]
                index += 1
                if (name in _WRAPPER_VALUE_OPTIONS.get(wrapper, set())
                        and "=" not in option and index < start):
                    index += 1
            continue
        if token.startswith("GH_REPO="):
            inline_owner = _owner_from_repo(token.split("=", 1)[1])
            states = [(True, inline_owner) for _ in states]
        index += 1
    if not any(state_assigned for state_assigned, _ in states):
        return []
    return [state_owner if state_assigned else None
            for state_assigned, state_owner in states]


def _raw_targets(command: str) -> List[Optional[str]]:
    """The explicit targets `_segment_targets` would read, found as text."""
    return ([_owner_from_repo(match.group("value"))
             for match in _RAW_REPO_FLAG_RE.finditer(command)]
            + [_url_owner(match) for match in _PR_URL_RE.finditer(command)])


def _cwd_is_internal(cwd: object) -> bool:
    """True only when every remote of the cwd checkout is `hermes-labs-ai/*`.

    gh picks its base repository among the remotes (an `upstream` often wins),
    so one internal `origin` beside an external remote is not clearly internal.
    """
    if not isinstance(cwd, str) or not cwd:
        return False
    root = gitfacts.repo_root(cwd)
    if root is None:
        return False
    remotes = gitfacts._git(root, "remote", "-v")
    urls = [fields[1] for fields in (line.split() for line in (remotes or "").splitlines())
            if len(fields) >= 2]
    if not urls:
        return False
    for url in urls:
        match = _GITHUB_REMOTE_RE.search(url)
        if match is None or match.group(1).lower() not in INTERNAL_OWNERS:
            return False
    return True


def _is_internal(targets: List[Optional[str]], command: str, cwd_internal: bool,
                 segment: Optional[List[str]] = None) -> bool:
    """The one exemption rule shared by the parsed, raw, and failure paths."""
    targets = targets + _gh_repo_targets(command, segment)
    if targets:
        return all(owner in INTERNAL_OWNERS for owner in targets)
    return cwd_internal and not _CONTEXT_SHIFT_RE.search(command)


def _guarded_segments(
    command: str, depth: int = 0
) -> List[Tuple[str, Optional[List[str]]]]:
    # Bash removes escaped physical newlines before tokenisation. `shlex`
    # otherwise leaves the newline in the executable token (`gh\n`) and misses
    # the invocation it will become.
    command = _strip_heredoc_bodies(_remove_line_continuations(command))
    segments = _token_segments(command)
    if segments is None:
        match = _RAW_RE.search(command)
        if match is None:
            return []
        return [(_raw_operation(match), None)]
    guarded = [(found, segment) for segment in segments
               for found in [_guarded_subcommand(segment)] if found is not None]
    if depth >= 4:
        return guarded
    for nested in _substitution_commands(command) + _indirect_command_strings(segments):
        guarded.extend(_guarded_segments(nested, depth + 1))
    for match in _DYNAMIC_GUARDED_RE.finditer(command):
        operation = match.group("operation")
        candidate = "gh %s %s%s" % (match.group("noun"), operation, match.group("tail"))
        dynamic = _guarded_subcommand(_token_segments(candidate)[0])
        if dynamic is not None:
            guarded.append((dynamic, None))
    return guarded


def _indirect_command_strings(segments: List[List[str]]) -> List[str]:
    """Shell/eval payloads that will be parsed again before execution."""
    found = []
    for segment in segments:
        found.extend(_env_split_strings(segment))
        start = _executable(segment)
        if start is None:
            continue
        name = segment[start].lstrip(_LEAD).rstrip(_TRAIL).rsplit("/", 1)[-1]
        args = segment[start + 1:]
        if name == "eval":
            if args and args[0] == "--":
                args = args[1:]
            if args:
                found.append(" ".join(args))
            continue
        if name not in _SHELL_INTERPRETERS:
            continue
        for index, token in enumerate(args):
            if token == "-c" or (
                token.startswith("-") and not token.startswith("--") and "c" in token[1:]
            ):
                if index + 1 < len(args):
                    found.append(args[index + 1])
                break
    return found


def _env_split_strings(segment: List[str]) -> List[str]:
    """Commands `env -S` reparses after ordinary shell tokenisation."""
    found = []
    index = 0
    while index < len(segment):
        token = segment[index].lstrip(_LEAD)
        if not token or _ASSIGNMENT_RE.match(token) or token in _CONTROL_PREFIXES:
            index += 1
            continue
        if token not in _WRAPPERS:
            break
        wrapper = token
        index += 1
        while index < len(segment):
            option = segment[index].lstrip(_LEAD)
            if option == "--":
                index += 1
                break
            if not option.startswith("-") or option == "-":
                break
            index += 1
            name = option.split("=", 1)[0]
            if wrapper == "env" and name in {"-S", "--split-string"}:
                if "=" in option:
                    found.append(option.split("=", 1)[1])
                elif index < len(segment):
                    found.append(segment[index])
            if (name in _WRAPPER_VALUE_OPTIONS.get(wrapper, set())
                    and "=" not in option and index < len(segment)):
                index += 1
    return found


def _remove_line_continuations(command: str) -> str:
    """Apply Bash's backslash-newline removal without changing single quotes."""
    out = []
    index = 0
    single = False
    double = False
    while index < len(command):
        char = command[index]
        if char == "'" and not double:
            single = not single
            out.append(char)
            index += 1
            continue
        if char == '"' and not single:
            double = not double
            out.append(char)
            index += 1
            continue
        if char == "\\" and not single:
            if command[index + 1:index + 3] == "\r\n":
                index += 3
                continue
            if command[index + 1:index + 2] == "\n":
                index += 2
                continue
            if index + 1 < len(command):
                out.extend(command[index:index + 2])
                index += 2
                continue
        out.append(char)
        index += 1
    return "".join(out)


def _strip_heredoc_bodies(command: str) -> str:
    """Remove literal heredoc data while retaining the command that opens it."""
    kept = []
    pending: List[Tuple[str, bool, bool]] = []
    for line in command.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if pending:
            delimiter, strip_tabs, literal = pending[0]
            candidate = content.lstrip("\t") if strip_tabs else content
            if candidate == delimiter:
                pending.pop(0)
            elif not literal:
                for nested in _substitution_commands(content):
                    kept.append("\n$(%s)\n" % nested)
            kept.append("\n" if line.endswith(("\n", "\r")) else "")
            continue
        kept.append(line)
        for match in _HEREDOC_RE.finditer(content):
            token = match.group("token")
            literal = token.startswith(("'", '"', "\\"))
            delimiter = token[1:-1] if token.startswith(("'", '"')) else (
                token[1:] if token.startswith("\\") else token)
            pending.append((delimiter, bool(match.group("dash")), literal))
    return "".join(kept)


def _substitution_commands(command: str) -> List[str]:
    """Command bodies Bash executes inside `$()` or backticks, even in `"..."`."""
    found = []
    index = 0
    single = False
    double = False
    while index < len(command):
        char = command[index]
        if char == "\\" and not single and index + 1 < len(command):
            index += 2
            continue
        if char == "'" and not double:
            single = not single
            index += 1
            continue
        if char == '"' and not single:
            double = not double
            index += 1
            continue
        if char == "#" and not single and not double and (
            index == 0 or command[index - 1].isspace()
        ):
            newline = command.find("\n", index)
            index = len(command) if newline < 0 else newline + 1
            continue
        if not single and command[index:index + 2] == "$(":
            start = index + 2
            depth = 1
            cursor = start
            inner_single = False
            inner_double = False
            while cursor < len(command):
                current = command[cursor]
                if current == "\\" and not inner_single and cursor + 1 < len(command):
                    cursor += 2
                    continue
                if current == "'" and not inner_double:
                    inner_single = not inner_single
                elif current == '"' and not inner_single:
                    inner_double = not inner_double
                elif not inner_single and not inner_double:
                    if command[cursor:cursor + 2] == "$(":
                        depth += 1
                        cursor += 2
                        continue
                    if current == ")":
                        depth -= 1
                        if depth == 0:
                            found.append(command[start:cursor])
                            index = cursor + 1
                            break
                cursor += 1
            else:
                index += 2
            continue
        if not single and char == "`":
            cursor = index + 1
            while cursor < len(command):
                if command[cursor] == "\\" and cursor + 1 < len(command):
                    cursor += 2
                    continue
                if command[cursor] == "`":
                    found.append(command[index + 1:cursor])
                    index = cursor + 1
                    break
                cursor += 1
            else:
                index += 1
            continue
        index += 1
    return found


def guarded_subcommand(command: str) -> Optional[str]:
    """None when the command is none of our business. Never executes anything."""
    if not isinstance(command, str) or not command.strip():
        return None
    guarded = _guarded_segments(command)
    return guarded[0][0] if guarded else None


def deny_output(subcommand: str) -> Dict[str, Any]:
    op = DENY_OPS[subcommand]
    direct = {"create": "`gh pr create`", "edit": "`gh pr edit`",
              "comment": "`gh issue comment` or `gh pr comment`"}[subcommand]
    extra = {
        "create": " --title 'feat: ...'",
        "edit": " --pr N",
        "comment": " --issue N\n"
                   "To replace an existing comment body, use `publish issue-comment-edit "
                   "--issue N --comment COMMENT_ID`, which keeps its recognized disclosures.",
    }[subcommand]
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON.format(direct=direct, op=op, extra=extra),
        }
    }


def run(raw: str) -> Optional[Dict[str, Any]]:
    """Core logic: the payload in, a deny object or None out."""
    try:
        payload = json.loads(raw) if raw and raw.strip() else None
    except (ValueError, AttributeError):
        return None
    if not isinstance(payload, dict):
        return None

    tool_name = payload.get("tool_name")
    if isinstance(tool_name, str) and tool_name and tool_name != "Bash":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    # Claude Code names this field `command`; Codex unified exec names it
    # `cmd`. They are the same Bash surface and both must cross this boundary.
    command = tool_input.get("command")
    if not isinstance(command, str):
        command = tool_input.get("cmd")
    if not isinstance(command, str):
        return None

    guarded = _guarded_segments(command)
    if not guarded:
        return None
    cwd_internal = _cwd_is_internal(payload.get("cwd"))
    for subcommand, segment in guarded:
        # An untokenisable line still shows its explicit targets as text; the
        # raw reading applies the same exemption as the parsed one.
        targets = _raw_targets(command) if segment is None else _segment_targets(segment)
        if not _is_internal(targets, command, cwd_internal, segment):
            return deny_output(subcommand)
    return None


def main(argv: Optional[List[str]] = None) -> int:
    """Always exits 0; the decision travels in the JSON, not the status.

    Conservative on its own failure: if judging the payload raises, but the raw
    input still carries the literal shape of a guarded call, deny. Silence on an
    internal error would be a gate that fails open.
    """
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    try:
        out = run(raw)
    except Exception:
        # Judge the command string when the payload still yields one; in the
        # raw JSON the command follows a quote, which `_RAW_RE` does not treat
        # as a command boundary.
        text = raw or ""
        cwd_internal = False
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                tool_input = payload.get("tool_input")
                if isinstance(tool_input, dict):
                    command = tool_input.get("command")
                    if not isinstance(command, str):
                        command = tool_input.get("cmd")
                    if isinstance(command, str):
                        text = command
                cwd_internal = _cwd_is_internal(payload.get("cwd"))
        except Exception:
            pass
        match = _RAW_RE.search(text)
        try:
            internal = _is_internal(_raw_targets(text), text, cwd_internal)
        except Exception:
            internal = False
        out = deny_output(_raw_operation(match)) if match and not internal else None
    if out is not None:
        try:
            sys.stdout.write(json.dumps(out))
        except Exception:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
