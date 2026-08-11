# agent-signage

**Road signs for coding agents.** One true fact, delivered at the moment your agent acts —
and silence the rest of the time.

[![CI](https://github.com/hermes-labs-ai/agent-signage/actions/workflows/ci.yml/badge.svg)](https://github.com/hermes-labs-ai/agent-signage/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)

## Contents

- [What it does](#what-it-does)
- [Install](#install)
- [Why this instead of a rule in your prompt file](#why-this-instead-of-a-rule-in-your-prompt-file)
- [The signs](#the-signs)
- [Guarantees](#guarantees)
- [What it costs](#what-it-costs)
- [When it stays quiet](#when-it-stays-quiet)
- [Is it working?](#is-it-working)
- [Acknowledging](#acknowledging)
- [Configuration](#configuration)
- [Adding your own sign](#adding-your-own-sign)
- [Verify it yourself](#verify-it-yourself)
- [Status and limitations](#status-and-limitations)
- [Research](#research)
- [License](#license)

## What it does

When a coding agent edits a file, nothing in its tool loop errors if the checkout is stale, a
symlink points outside the repo, or another worktree is mid-edit on the same path — the edit
just lands, clean and wrong.

`agent-signage` is a PreToolUse hook that closes that gap: a Claude Code hook today, and, being
a plain subprocess, usable in any agent harness that can shell out before a file operation.
Before a Read, Edit, Write, Grep, or Glob call runs, it checks measurable facts about the git
state of the file about to be touched and injects one short line into the agent's context if
something matters. The rest of the time it says nothing.

Your agent opens a repo and starts fixing things. The checkout is on a stale branch, 26 commits
behind the branch you actually deploy. Every edit is correct, well-tested, and applied to a
version nobody can see. Nothing errors. Nothing warns. You find out later, if you find out at
all.

`agent-signage` puts a sign on that road:

```
STALE CHECKOUT - hermes-labs-v2 is 27 commit(s) behind origin/main (upstream tip 31 minutes
ago). This working copy may not be what is deployed; confirm which source is authoritative.
Inspect: git -C /Users/you/dev/hermes-labs-v2 log --oneline HEAD..@{u}
```

Every number in that line comes from a ref already on disk, so when the last fetch is old the
sign says so rather than going quiet:

```
STALE CHECKOUT - example-app is 8 commit(s) behind origin/main and 6 ahead from cached
origin/main at a1b2c3d4e5f6 (FETCH_HEAD was 4 days old when checked); the current gap is
unmeasured. This working copy may not be what is deployed; confirm
which source is authoritative.
Inspect: git -C /path/to/example-app fetch && git -C /path/to/example-app log --oneline HEAD..@{u}
```

That text reaches the model alongside the tool result, at the moment it touches the file.
Not at the start of the session, not in a config file it read twenty turns ago.

## Install

Requires Python 3.9+ and `git` on `PATH`. No package dependencies.

```bash
pip install agent-signage
```

Or from source:

```bash
pip install git+https://github.com/hermes-labs-ai/agent-signage.git
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

Empty output means "nothing to say". Exit code is always 0. A full runnable example (no Claude
Code needed) is in `examples/README.md`; harness maintainers wiring this in permanently should
read [docs/integrating.md](docs/integrating.md).

## Why this instead of a rule in your prompt file

Standing rules in a prompt file are a blunt instrument for context engineering: they are read
once at the start of a session, then have to survive dozens of turns of unrelated work before
the one moment they were written for actually arrives — and often they don't.

That is the first of two costs, and the second is the reason this project exists.

**It doesn't fire when you need it.** The failure is not a knowledge gap — your agent already
knows that a local checkout can diverge from what's deployed. It just has no reason to form
that hypothesis at turn fifteen, mid-task, when nothing in front of it looks wrong. A rule it
read at turn zero is competing with everything that has happened since.

**It conditions every other task too.** A standing instruction is in context for every request,
including the ones it has nothing to do with. It is attended to while the model is writing a
migration, reviewing a diff, or answering a question about documentation — narrowing how it
interprets and what it generates in all of them. A constraint written for one situation becomes
a standing bias on every situation. Add enough of them and you have quietly traded general
capability for a set of reflexes, most of which are irrelevant most of the time.

A sign is present only while the action that needs it is happening. The rest of the time the
context is exactly as it would have been if this tool were not installed — which is the point.
Constrain the model where the constraint is load-bearing, and leave it alone everywhere else.
Anthropic calls this shape [just-in-time
context](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents):
retrieve the fact at the moment of need rather than pre-loading everything that might matter.

This is a design argument, not a measured result. The token cost is measurable and small; the
conditioning cost is not something this project has quantified.

## The signs

| Sign | Reports | Fires on |
|---|---|---|
| `stale_checkout` | the repo is N commits behind (and M ahead of) its upstream | read + write |
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

## Guarantees

These are asserted by the test suite. If any regresses, CI fails. The reasoning behind each one
is in [docs/design.md](docs/design.md).

| Property | Guarantee |
|---|---|
| **Sound** | Every sign reports a measurement, never an inference. When it speaks, the stated fact is true. |
| **Non-blocking** | It never emits a block decision and never exits non-zero. It cannot stop a tool call. |
| **Fails open** | Malformed input, missing git, unwritable state, hung subprocess — all end in silence and exit 0. |
| **Bounded** | A 3 s deadline is checked before each sign starts and inside the only scan that grows with the repository, and every individual `git` call is capped at 2 s. Worst case is therefore one in-flight git call past the deadline. |
| **No network while measuring** | No sign's measurement contacts the network. The one network call this tool makes is a detached background `git fetch`, spawned at most once per repo per 2 minutes and never awaited; `AGENT_SIGNAGE_NO_FETCH=1` turns it off entirely. |
| **Zero dependencies** | Python 3.9+ standard library only. |
| **Quiet** | Each sign speaks once per file per session *per observed state*. Nothing at all when nothing is wrong. |

A note on two of these, because both were overstated before 0.1.2. "Bounded" previously
claimed a deadline capped every invocation; it was checked only after every sign had already
run, so it suppressed output rather than stopping work. And the no-network test patched
`subprocess.run` while the only call that reaches the network goes through `subprocess.Popen`,
so it asserted over a path that had nothing to find. Both the code and the claims were
corrected rather than one or the other.

## What it costs

Measured end-to-end as a subprocess — what your harness actually pays per tool call — on
macOS/arm64 with CPython 3.14, p50 of 30 runs. Numbers live in `evals/metrics-0.1.2.json`.
Measure it on your own machine and your own repo with `agent-signage doctor`.

| Case | Cost |
|---|---|
| Bare interpreter floor (`python -c pass`) | ~17 ms |
| Silent — not a repo, or a vendored path | ~35 ms |
| Read inside a repo — **any number of worktrees** | ~76 ms |
| Edit inside a repo with no other worktree | ~84 ms |
| Edit inside a repo with 22 other worktrees | ~330 ms |

The last row is the one to know about, and the 0.1.0 and 0.1.1 tables did not show it because
they were measured on a small repository. `concurrent_worktree_edit` runs one `git status` per
sibling worktree, so a write costs roughly **84 ms + ~11 ms per other worktree**. Reads are
unaffected — that sign only fires on write-shaped tools — so the cost scales with how parallel
your setup is, on exactly the events where a lost edit is the risk.

The loop is deliberately not capped at some number of worktrees: that would quietly cut
coverage for the people running the most parallel agents, who are the ones the sign exists for.
It stops at the deadline instead, and says "at least N" when it did not finish looking.

Silence is otherwise the common case. Argparse and the git layer load lazily, a parent-directory
walk rules out non-repos before `git` is spawned, and the six signs share memoised git answers
for the duration of one evaluation — which took an in-repo edit from 119 ms to 84 ms.

If that is too much for your harness, call it on a subset of events; first-touch-per-directory
still catches the failures it targets.

## When it stays quiet

Silence is the default and the common case. It deliberately says nothing when:

- the repo is current, has no upstream, or is not a git repo at all
- **you are behind on purpose** — mid-bisect, detached HEAD, or an in-progress rebase, merge, or cherry-pick
- the path is vendored or generated (`node_modules`, `vendor`, `.venv`, `dist`, `build`, …)
- your agent already fetched during this session, so it has current knowledge
- it already told you this exact fact about this file in this session
- you acknowledged it (see below)

An old fetch is deliberately *not* on that list any more. Up to 0.1.1 it was, and it was the
single biggest source of missed drift: the sign returned before it had even asked how far
behind the repo was. It now reports the reading and dates it.

**Silence never means "verified current."** It means "no drift known." That distinction is
what keeps the tool honest: it can miss drift, but it cannot invent it.

## Is it working?

Silence is the design, which makes a broken install look exactly like a clean repo. `doctor`
is the difference:

```bash
agent-signage doctor              # this repo
agent-signage doctor path/to/file # a specific file
```

It reports whether a hook entry naming this package exists in any settings file Claude Code
reads, what git says about the repository right now, and — for each of the six signs — whether
it speaks here or the measured reason it does not:

```
signs
  stale_checkout           SPEAKS
      STALE CHECKOUT - lintlang is 5 commit(s) behind origin/main …
  symlink_escape           quiet    cli.py is not reached through a symlink
  conflict_markers         quiet    no conflict markers in the first 8KB of cli.py
  concurrent_worktree_edit quiet    16 other worktree(s), none holding uncommitted changes to cli.py
  binary_edit              quiet    cli.py has no NUL bytes
  generated_file           quiet    cli.py declares no generator in its first 5 lines
```

`no upstream is configured for main — nothing to compare against` is the answer worth knowing:
it means `stale_checkout` is inert in that repo and no amount of drift will produce a sign.
`doctor` exits non-zero only when it finds no hook entry, and it has no side effects — it
writes no stamps and never fetches.

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
| `AGENT_SIGNAGE_NO_FETCH` | — | Set to disable the background refresh. Does not make the tool quieter: the reading already on disk is still reported, still dated. For metered connections, CI runners with no credentials for the remote, or anywhere a surprise subprocess is unwelcome. |

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
agent-signage doctor          # what is live, what is inert, and what it costs here
pytest                        # full behavioural suite over real synthetic git repos
```

The exact-commit deterministic readback for `0.1.2`, including a safe isolated
hook demonstration, is in [`evals/proof-0.1.2.json`](evals/proof-0.1.2.json).

## Status and limitations

`0.1.2` — early, and honest about it. Six signs, 105 tests over real synthetic git repositories,
in production use at Hermes Labs. The sign registry is stable and extensible.

Limits worth knowing before you adopt:

- **Every count is as of the last fetch.** `stale_checkout` never contacts the network while
  measuring, so what it reports is what the remote-tracking ref on disk says. When that ref is
  old the sign says so and calls the present gap unmeasured — the true gap can be larger if
  upstream advanced, or smaller if upstream was rewound. It can undercount drift. It cannot
  invent it.
- **Bash-invoked edits are invisible.** `cat`, `sed -i`, or a shell script carry no tool path,
  so nothing is checked.
- **Git only.** A stale deployed API, database, or service is out of scope.
- **Writes get slower as your worktree count grows** — about 11 ms per sibling worktree. Reads
  do not. See [What it costs](#what-it-costs).
- **The guarantees are self-attested.** They are asserted by this repository's own test suite,
  which is a real bar but not an independent one. Nobody outside the project has exercised it
  adversarially yet. Read the tests — they are the specification. Two guarantees published in
  0.1.0 and 0.1.1 did not hold as stated; both were found by auditing the shipped artifact
  against its own README, and both are corrected in 0.1.2. That is the honest track record.
- **The false-positive evidence is thin.** 0/12 and 0/8 on small corpora whose controls were
  arguably incapable of firing. That is direction, not a result.

## Research

`agent-signage` comes out of [Hermes Labs](https://hermes-labs.ai), an AI reliability
engineering studio. The research behind the wider programme — on how AI systems lose meaning,
misreport their own state, and fail in ways standard evaluations miss — is published with DOIs
at [hermes-labs.ai/research](https://hermes-labs.ai/research).

The one most directly adjacent to this tool is *Precise Records, Unstable Meanings*
([10.5281/zenodo.21652317](https://doi.org/10.5281/zenodo.21652317)), a measurement-validity
audit separating what agent telemetry can actually establish from what gets claimed on top of
it. That distinction — report the measurement, not the inference — is the rule every sign here
has to satisfy.

## License

Apache-2.0 · [Hermes Labs](https://hermes-labs.ai)
