# agent-signage

**Road signs for coding agents.** One true fact, delivered at the moment your agent acts —
and silence the rest of the time.

Your agent opens a repo and starts fixing things. The checkout is 26 commits behind the branch
you actually deploy. Every edit is correct, well-tested, and applied to a version nobody can
see. Nothing errors. Nothing warns. You find out later, if you find out at all.

`agent-signage` puts a sign on that road:

```
STALE CHECKOUT - hermes-labs-v2 is 27 commit(s) behind origin/main (upstream tip 31 minutes
ago). This working copy may not be what is deployed; confirm which source is authoritative
before treating work here as fixing the live system.
Inspect: git -C /Users/you/dev/hermes-labs-v2 log --oneline HEAD..@{u}
```

That text reaches the model alongside the tool result, at the moment it touches the file.
Not at the start of the session, not in a config file it read twenty turns ago.

## Why this instead of a rule in your prompt file

A line in `CLAUDE.md` or `AGENTS.md` saying "always check the branch is current" costs tokens
on every request and fires on the model's memory. This fires on **the condition**, computed
fresh, and says nothing when there is nothing to say.

The difference matters because the failure is not a knowledge gap. Your agent already knows
that deployed and local can diverge. It just has no reason to *form the hypothesis* at turn
fifteen, in the middle of a task, when everything looks normal.

## Guarantees

These are asserted by the test suite. If any regresses, CI fails.

| Property | Guarantee |
|---|---|
| **Sound** | Every sign reports a measurement, never an inference. When it speaks, the stated fact is true. |
| **Non-blocking** | It never emits a block decision and never exits non-zero. It cannot stop a tool call. |
| **Fails open** | Malformed input, missing git, unwritable state, hung subprocess — all end in silence and exit 0. |
| **Bounded** | A hard deadline caps every invocation. A pathological repo cannot stall a file read. |
| **No network on the hot path** | Refreshes happen out-of-band, never inline. Asserted by a test that fails if the measuring call attempts a fetch. |
| **Zero dependencies** | Python 3.9+ standard library only. |
| **Quiet** | Each sign speaks once per file per session. Nothing at all when nothing is wrong. |

## What it costs

Measured end-to-end as a subprocess — what your harness actually pays per tool call — on
macOS/arm64 with CPython 3.14. Numbers live in `evals/metrics-0.1.0.json`.

| Case | Cost |
|---|---|
| Bare interpreter floor (`python -c pass`) | ~16 ms |
| Silent — not a repo, or a vendored path | ~30 ms |
| Silent — a clean file inside a repo (Read) | ~62 ms |
| Silent — a clean file inside a repo (Edit) | ~71 ms |

Silence is the common case. Argparse and the git layer load lazily, a parent-directory walk
rules out non-repos before `git` is spawned, and the six signs share memoised git answers for
the duration of one evaluation — which took an in-repo edit from 119 ms to 71 ms.

If that is too much for your harness, call it on a subset of events; first-touch-per-directory
still catches the failures it targets.

## Install

```bash
pip install agent-signage
```

### Claude Code

```bash
agent-signage install    # writes the hook entry for you
```

Or add it yourself to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Edit|Write|NotebookEdit|Grep|Glob",
        "hooks": [{"type": "command", "command": "python3 -m agent_signage", "timeout": 8}]
      }
    ]
  }
}
```

### Any other harness

It is a subprocess that reads JSON on stdin and writes JSON or nothing on stdout:

```bash
echo '{"session_id":"abc","tool_input":{"file_path":"/path/to/file.py"}}' | python3 -m agent_signage
```

Empty output means "nothing to say". Exit code is always 0.

## When it stays quiet

Silence is the default and the common case. It deliberately says nothing when:

- the repo is current, has no upstream, or is not a git repo at all
- **you are behind on purpose** — mid-bisect, detached HEAD, or an in-progress rebase, merge, or cherry-pick
- the path is vendored or generated (`node_modules`, `vendor`, `.venv`, `dist`, `build`, …)
- your agent already fetched during this session, so it has current knowledge
- remote knowledge is too old to be meaningful — it refreshes in the background and stays quiet this turn
- it already told you this about this file in this session
- you acknowledged it (see below)

**Silence never means "verified current."** It means "no drift known." That distinction is
what keeps the tool honest: it can miss drift, but it cannot invent it.

## Acknowledging

```bash
agent-signage ack /path/to/repo stale_checkout "origin/main@a1b2c3d4e5f6:27"
```

The acknowledgement is bound to the **observed state**, not to the repository. If upstream
moves, the key no longer matches and the sign speaks again — so "stop telling me" can never
suppress genuinely new information.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_SIGNAGE_IGNORE` | — | `PATH`-separated repos to skip entirely |
| `AGENT_SIGNAGE_STATE_DIR` | system temp | Where stamps live |
| `AGENT_SIGNAGE_SESSION_START` | — | Unix timestamp of session start; enables "already fetched this session" suppression |

## Adding your own sign

A sign is a function that returns a `Sign` or `None`. Register it and it runs:

```python
from agent_signage import signs

@signs.register
def my_sign(ctx):
    if not_worth_saying:
        return None
    return signs.Sign(id="my_sign", text="...", state_token="...", repo=root)
```

A sign must be **sound** (report a measurement, never an inference), **silent** (produce
nothing when there is nothing to say), and **actionable** (end in the command that resolves
it). Anything that cannot meet all three is not a sign.

## Verify it yourself

```bash
agent-signage selftest        # asserts the runtime guarantees, no repo needed
pytest                        # full behavioural suite over real synthetic git repos
```

## The signs

| Sign | Reports | Fires on |
|---|---|---|
| `stale_checkout` | the repo is N commits behind its upstream | read + write |
| `symlink_escape` | the path resolves through a symlink to outside the repo | read + write |
| `conflict_markers` | the file still contains unresolved `<<<<<<<` markers | read + write |
| `concurrent_worktree_edit` | another worktree has uncommitted changes to this same file | write |
| `binary_edit` | the file contains NUL bytes and a text edit will corrupt it | write |
| `generated_file` | the file declares itself machine-generated in its header | write |

Each was selected against a documented failure report rather than invented; the evidence is
cited in the docstring of each sign in `src/agent_signage/more_signs.py`. Signs whose only
consequence is "what you are about to write will go wrong" fire on writes only — firing them
on a read would be true but useless, and a true-but-useless sign is how a tool like this gets
muted.

## Status

`0.1.0` — early, and honest about it. Six signs, 68 tests over real synthetic git repositories,
in production use at Hermes Labs. The sign registry is stable and extensible.

Two limits worth knowing before you adopt:

- **Coverage is capped by fetch freshness.** `stale_checkout` reports drift only as recent as
  the last fetch. It refreshes in the background when its knowledge is stale, but it can miss
  drift. It cannot invent it.
- **The guarantees are self-attested.** They are asserted by this repository's own test suite,
  which is a real bar but not an independent one. Nobody outside the project has exercised it
  adversarially yet. Read the tests — they are the specification.

## License

Apache-2.0 · [Hermes Labs](https://hermes-labs.ai)
