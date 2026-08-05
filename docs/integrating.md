# Integrating agent-signage into a harness

This is for maintainers of agent frameworks and harnesses. If you just want to use it
yourself, the README is shorter.

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

## Reporting a false positive

A false positive is a bug, not a tuning preference — the tool's entire value rests on the claim
that when it speaks, it is right. Please open an issue with the repository state (`git status`,
`git rev-list --count HEAD..@{u}`) and the sign text you saw.
