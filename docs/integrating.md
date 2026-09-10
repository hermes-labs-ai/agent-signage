# Integrating agent-signage into a harness

This is for maintainers of agent frameworks and harnesses. If you just want to use it
yourself, the README is shorter.

This repository ships three surfaces with deliberately opposite contracts. Most of this
document is about the first. Do not blur them:

| | PreToolUse file hook | Publisher | PreToolUse Bash adapter |
|---|---|---|---|
| Invoked | before every file operation | explicitly, by a caller | before every Bash command |
| On error | silence, exit 0 | non-zero exit | conservative deny |
| Can stop an action | no, ever | yes — it owns execution | yes — it denies the tool call |
| Subject | the file about to be touched | the bytes about to be published | the command about to run |

Wiring either of the last two into the first would defeat them: `hook.py` is contractually
unable to exit non-zero or emit a decision, so routing a gate through it presents enforcement
that structurally cannot enforce. They are separate entry points with separate matchers and
never share a process. See [Integrating the publication
boundary](#integrating-the-publication-boundary).

## What you are integrating

A subprocess that reads a JSON object on stdin and writes either a JSON object or nothing on
stdout. It always exits 0. It cannot block, veto, or modify a tool call — the only thing it can
do is add a line of text to what your agent sees.

```
$ echo '{"session_id":"abc","tool_input":{"file_path":"/repo/src/main.py"}}' \
    | python3 -m agent_signage
{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"STALE CHECKOUT - ..."}}

$ echo '{"session_id":"abc","tool_input":{"file_path":"/tmp/scratch.txt"}}' \
    | python3 -m agent_signage
$                     # empty: nothing to say, which is the common case
```

## Input

| Field | Required | Purpose |
|---|---|---|
| `tool_input.file_path` | one of these | the file about to be read or written |
| `tool_input.path` | one of these | the directory about to be searched |
| `tool_input.notebook_path` | one of these | notebook variant |
| `session_id` | recommended | scopes "tell me once"; without it, falls back to the parent pid |
| `tool_name` | optional | passed to signs, unused by the current sign |

Anything else in the payload is ignored. Malformed JSON, an empty string, and a missing path
all produce silence and exit 0.

## Output

Either nothing, or one object with a single `hookSpecificOutput` key. The interesting field is
`additionalContext`: a one-line string to place in front of the model alongside the tool
result. If your harness has a different envelope, ignore the wrapper and use that string.

There is no `decision` field and never will be. If you see one, it is not from this tool.

## Where to call it

Before a file-touching tool runs. Reads matter as much as writes: an agent that *reads* a stale
file and reasons from it has already gone wrong, and an edit-only trigger fires too late to
help. In practice that means your equivalent of Read, Edit, Write, Grep, and Glob.

## What it costs you

Measured on macOS/arm64 with CPython 3.14 (`evals/metrics-0.0.8.json`, reproduce with
`python3 -m pytest tests/ -q`):

| Case | Cost |
|---|---|
| Bare interpreter floor (`python -c pass`) | ~16 ms |
| Silent — non-repo or vendored path | ~32 ms |
| Speaking — real repo, several git queries | ~95 ms, at most once per repo per session |

Silence is the common case and stays near the interpreter floor: argparse and the git layer are
imported lazily, and a parent-directory walk rules out non-repos before git is ever spawned.

If ~32 ms per file operation is too much for your harness, call it on a subset of events —
first-touch-per-directory is enough to catch the failure it targets.

## What it will never do

- Block, deny, or alter a tool call.
- Exit non-zero.
- Touch the network on the path that produces output. Refreshes are detached and out-of-band,
  and there is a test that fails if the measuring path attempts a fetch.
- Write anywhere except its own state directory (`AGENT_SIGNAGE_STATE_DIR`, default system temp).
- Require a dependency. Python 3.9+ standard library only.

## Failure behaviour

Every error path ends in silence and exit 0: malformed input, git missing from `PATH`, a repo
in a broken state, an unwritable state directory, a subprocess that hangs past its bound. A
global deadline caps the whole invocation.

The design assumption is that a hook in front of every file operation must be safe to fail. It
is better for a sign to be missed than for a tool call to be delayed or lost.

## Adding a sign for your own harness

```python
from agent_signage import signs

@signs.register
def my_sign(ctx):           # ctx: session_id, tool_name, target_path
    if nothing_to_say:
        return None
    return signs.Sign(id="my_sign", text="...", state_token="...", repo=root)
```

The bar is three properties, all required:

- **Sound** — it reports a measurement, never an inference. No heuristics, no confidence
  scores, no model calls. If it fires, the stated fact is true.
- **Silent** — it produces nothing when there is nothing to say.
- **Actionable** — it ends in the command that resolves it.

A sign that raises is dropped, not fatal; the others still run.

## Integrating the publication boundary

Two pieces with opposite jobs. Take both or neither — the publisher without the adapter is a
convention, and the adapter without the publisher denies with nowhere to go.

### 1. The publisher owns execution

```
$ python3 -m agent_signage publish pr-create --body-file /abs/pr-body.md \
    --target owner/repo --title "feat: ..." --kind contribution --oversight active
$ echo $?
```

| Exit | Meaning |
|---|---|
| `0` | published, and the body read back byte for byte |
| `1` | artifact rejected — no mutating `gh` child was started; edit preservation may make one read-only view call |
| `2` | input rejected — no `gh` child was started |
| `3` | a `gh` child failed; its status and stderr are reported (a failed `pr-edit` pre-read exits here with no update attempted) |
| `4` | `gh` succeeded but the published body could not be verified |

Call it *instead of* your own `gh pr create` / `gh pr edit`, not before one. That is the whole
difference from the prepare-only design this replaced: a prepared argv handed back to a caller
is unobservable, and the file can change between the check and the send. Here the body is
opened once on a bounded `O_NOFOLLOW|O_NONBLOCK` descriptor, checked as a snapshot, and handed to `gh` on
stdin via `--body-file -`. `gh` never receives the path, so there is no second read for a
mutation to land in.

Before `pr-edit`, the publisher reads the current body and binds recognized disclosure trailers
(`Disclosure:`, `Co-Authored-By:`, `Signed-off-by:`, and related forms) to that snapshot.
`--preserve` binds project-specific lines. This prevents silent removal of disclosures present
in the pre-read snapshot; it cannot eliminate a concurrent edit between that read and GitHub's
unconditional update.

Supported operations are exactly `pr-create` and `pr-edit`.

### 2. The Bash adapter is the chokepoint

A separate `PreToolUse` hook, matcher `Bash`, that denies PR creation and body edits on an external or unresolved target:

```
$ echo '{"tool_name":"Bash","tool_input":{"command":"a && gh pr create --repo someone/upstream"}}' \
    | python3 -m agent_signage gate
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"…"}}
```

It always exits 0; the decision travels in the JSON. It never executes the command under
judgment. Lexing plus explicit handling covers compound/multiline commands, wrappers, heredoc
data, and substitutions. The only subprocesses it may start are two bounded, fixed-argv, read-only git measurements
(`git rev-parse --show-toplevel`, then `git remote -v`) used when no target is named.

The target decides. Internal PRs that target `hermes-labs-ai/*` are exempt: every explicit
target (`--repo`/`-R`, a `gh pr edit` PR URL, `GH_REPO`) must be `hermes-labs-ai/*`, or, with
none named, every remote of the working checkout must be, with no `cd`/`pushd`, wrapper chdir,
`GIT_DIR`, or `GIT_WORK_TREE` shift in the command. Any other target is external or unknown and denied,
including from an untokenisable line or the adapter's own failure fallback, which apply the
same rule to the text. Metadata-only PR edits are silent, as are malformed JSON, non-Bash tools, `gh pr view`, `gh issue create`, and unrelated commands. Claude Code
supplies `tool_input.command`; Codex unified exec supplies `tool_input.cmd`; both are accepted.

### 3. Wire the adapter

For Codex, use the additive user-hook installer:

```
$ agent-signage install-publication-gate
```

It preserves existing hook groups, creates a timestamped backup, writes atomically, and is
idempotent. Restart Codex, open `/hooks`, and review and trust the new hook definition. Until
that trust step, Codex deliberately skips a new non-managed hook. The edit is not retroactive
to a task that already loaded its hook configuration; use a restarted or new task for the
enforced path.

For Claude Code, add the equivalent entry to the settings file that owns the session:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "python3 /abs/path/to/agent-signage/scripts/gate.py", "timeout": 5 }
        ]
      }
    ]
  }
}
```

It does not touch the `Read|Edit|Write|NotebookEdit|Grep|Glob` entry that runs the passive hook.
`agent-signage install` still wires only that passive Claude Code file hook; the publication
installer is deliberately a separate, fail-closed boundary operation.

### The three integration mistakes

1. **Calling the publisher and then publishing again yourself.** It already published. A second
   `gh` call with the same body is a second write, and the readback that made the first one
   trustworthy says nothing about it.
2. **Wiring the adapter without the publisher.** Then every publication path is denied and the
   agent has no supported way forward.
3. **Treating a `deny` as coverage.** It covers the Bash tool of one harness. See below.

### What is still uncovered

- **Any path that is not a Bash command.** A harness publishing through the GitHub REST API, a
  browser session, an MCP server, or its own built-in PR tool never produces a Bash command for
  the adapter to see.
- **Any harness without an active compatible `PreToolUse` hook.** Codex and Claude Code have the
  documented paths above. Cursor, Aider, and others execute nothing here; there is no
  cross-harness enforcement claim.
- **Anything outside the tool loop.** A Makefile target, a CI job, or a terminal the user opens
  themselves spawns processes the harness never sees.

Closing those is a property of credentials and CI, not of a hook: a token the agent cannot
reach, and a required check that re-runs the attribution check against the merged body.

## Reporting a false positive

A false positive is a bug, not a tuning preference — the tool's entire value rests on the claim
that when it speaks, it is right. Please open an issue with the repository state (`git status`,
`git rev-list --count HEAD..@{u}`) and the sign text you saw.
