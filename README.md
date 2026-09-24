<div align="center">

# agent-signage

**Road signs for coding agents: one true fact, delivered at the moment your agent acts — and silence the rest of the time.**

agent-signage is developed by [Hermes Labs](https://hermes-labs.ai).

Hermes Labs is an agentic infrastructure company building the reliability layer for autonomous systems.

[![CI](https://github.com/roli-lpci/agent-signage/actions/workflows/ci.yml/badge.svg)](https://github.com/roli-lpci/agent-signage/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)

</div>

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
- [Operational cards](#operational-cards)
- [Publication boundary](#publication-boundary)
- [GitHub Actions](#github-actions)
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
pip install git+https://github.com/roli-lpci/agent-signage.git
```

### Claude Code

Confirm the installed runtime is sound before anything is written. This is
read-only:

```bash
agent-signage selftest    # asserts the runtime guarantees, no repo needed
```

`agent-signage install` mutates your Claude Code settings file
(`~/.claude/settings.json` by default): it adds a `PreToolUse` hook entry and
keeps a timestamped backup of what was there before. Re-running it is
idempotent — it recognizes an existing entry and makes no further change.

```bash
agent-signage install    # writes the hook entry for you
```

Read back exactly what got wired, and what it costs in this repo. This is
also read-only:

```bash
agent-signage doctor      # what is live, what is inert, and what it costs here
```

Or install the self-contained Claude Code plugin from the Hermes Labs
marketplace. The plugin bundles its dependency-free runtime, so this path does
not require a separate `pip install`:

```bash
claude plugin marketplace add roli-lpci/agent-signage
claude plugin install agent-signage@hermes-labs
```

Or load it straight from a clone, for that session only, with nothing installed:

```bash
git clone https://github.com/roli-lpci/agent-signage.git
cd agent-signage
claude --plugin-dir claude-plugin
```

### GitHub Copilot CLI

The native Copilot CLI plugin reports measured signs after successful `view`,
`edit`, or `create` operations. Its [installation and timing details](integrations/copilot-cli/README.md)
explain the Python package prerequisite and why this uses `postToolUse`.

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

`agent-signage doctor path/to/repo` prints the current acknowledgement token.
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

## Operational cards

Some facts only become useful after another tool has classified an action boundary. For
example, a release gate may know that one exact command would publish to a public repository.
The checkout-local renderer gives that caller a small, consistent way to present a trusted
local card at that moment:

```bash
python3 scripts/render.py \
  --card /absolute/path/to/card.json \
  --format text \
  --context "owner/repository version"
```

A card is a JSON object with exactly four nonempty, one-line string fields:

```json
{
  "id": "release.authorization",
  "headline": "PUBLIC RELEASE",
  "fact": "The release gate classified this command as a public release boundary.",
  "next": "Check current authorization before continuing."
}
```

Use `--format text` when a caller needs the rendered line, or `--format hook` for the standard
Claude Code `PreToolUse` `additionalContext` envelope. Optional `--context` is bounded and JSON
quoted as target data; it is never interpolated into the card. Card fields and total output are
also bounded, and malformed cards make the command fail with no stdout so callers can use a
known fallback.

The renderer does not inspect shell commands, decide whether a card applies, grant approval,
or allow or block an action. Keep cards in a trusted local configuration path. The caller that
already identifies the boundary owns its facts, authorization checks, enforcement, and fallback.
This keeps operational guidance timely without turning agent-signage into a policy engine.

## Reliability Lab result envelopes

The same operational-card renderer can turn another Hermes Reliability Lab
product's own `hermes.reliability-lab.result/1` result into a completion
card, or into nothing:

```bash
python3 -m agent_signage.evidence \
  --source that-product/evidence.json \
  --id release.checks --headline "GATE PASSED" --next "Continue." \
  --fact-label "checks passed" --fact-value "2 of 2"
```

The trust rule: a card is licensed only by an envelope whose `mode` is
`executed` (a real run, not a preview) and whose `status` is `pass`. A
`warn`/`unknown`/`fail` status, a preview, a wrong-schema file, or a status
that disagrees with its own findings all license **no card** — reported as
such, never guessed past. The caller declares the one measured fact worth
stating (a precise label and a value already computed from that other
product's own data); this module does not interpret what a "test count"
means for a product it did not write, only whether the evidence is
genuinely completed and the card fits the same bounds every card here does.
`--source`, `--id`, `--headline`, and `--next` are required; `--fact-label`
and `--fact-value` are optional and must be given together.

## Publication boundary

Everything above is passive. The hook cannot block, cannot exit non-zero, and goes quiet on
every error — that contract is what makes it safe in front of every file operation, and it is
exactly why it cannot carry a publication requirement. **A mechanism that says nothing when it
breaks is not a gate.**

So the boundary is separate, and it has two halves. One **owns execution**, so what was checked
and what was sent are the same bytes by construction. The other is a **PreToolUse Bash adapter**
that stops an agent reaching `gh` around it.

### Preparing a contribution footer

When publishing with `--selection`, the body must end with the exact footer
for that selection. Generate it locally from the installed package:

```bash
python3 -c "from agent_signage.contribution import footer; print(footer('unspecified'))"
```

Use `unspecified` when the selection history is unknown, `owner` when a human
selected the work, or `autonomous` when agents selected it. Only `autonomous`
adds the autonomous-selection claim. Copy the output once to the end of the
body, outside code fences, and use the same value for `--selection`.
The publisher validates this text; it does not append or repair it for you.
The generic `attribution` command produces a different disclosure and is not
an interchangeable footer for this mode.

This contribution mode is specific to Hermes Labs: its footer names the
responsible human contributor, and the publisher requires the authenticated
`github.com` account to be `roli-lpci`. Generating a footer does not authorize
publication or change the authenticated account.

You can check a prepared body without contacting GitHub:

```python
from pathlib import Path
from agent_signage.contribution import check

verdict = check(Path("pr-body.md").read_text(encoding="utf-8"), "unspecified")
print("Ready for publisher validation" if verdict.ok else verdict.reasons)
```

This checks the local footer only. The publisher still checks the account,
target, and any disclosures that an edit must preserve.

### The publisher

```bash
python3 scripts/publish.py pr-create \
  --body-file /abs/path/to/pr-body.md \
  --target owner/repo --title "feat: ..." \
  --kind contribution --oversight active
```

It runs `gh` itself, in a fixed order that a caller cannot reassemble wrongly:

1. **Open the body once**, on a bounded, non-blocking file descriptor — `O_NOFOLLOW` refuses a
   symlink, `O_NONBLOCK` prevents a FIFO from hanging the preflight, `fstat` checks the opened
   object, and a capped read rejects oversized input.
2. **Check that snapshot.** Not the file — the snapshot. There is no second read.
3. For **`pr-edit`**, read the live body and require every recognized human/upstream disclosure
   trailer in that snapshot to remain. Project-specific lines can be bound with `--preserve`.
4. **Emit the action-time sign**, before any mutating child process exists.
5. **Run `gh` with an argv list**, never a shell string, and always `--body-file -`, handing the
   snapshot bytes to its stdin. `gh` is never given the path, so it cannot re-read a file that
   changed after step 2.
6. **Read the body back** with `gh pr view --json body` and require exact equality.

Issue comments use the same order. `gh issue comment` can only edit "the last comment of the
current user", not a concrete comment, so both comment operations go through `gh api` with the
body on stdin (`-F body=@-`) and read back the concrete comment ID:

```bash
python3 scripts/publish.py issue-comment-create --issue 123 \
  --body-file /abs/path/to/comment.md --target owner/repo \
  --kind contribution --oversight none --selection unspecified
python3 scripts/publish.py issue-comment-edit --issue 123 --comment 4567890123 \
  --body-file /abs/path/to/comment.md --target owner/repo \
  --kind contribution --oversight none --selection unspecified
```

`issue-comment-create` POSTs to `repos/OWNER/REPO/issues/N/comments` and takes the new comment
ID from the JSON response; `issue-comment-edit` first reads comment `--comment`, requires it to
belong to issue `--issue` and keeps its recognized disclosure trailers, then PATCHes it. Both
read the comment back by ID and require the exact body, the declared issue, and -- with
`--selection` -- `github.com` on every call and `roli-lpci` as the comment author. A PR number
is accepted as `--issue`, because a PR conversation comment is the same GitHub object.

Success is claimed only after step 6.

| Exit | Meaning |
|---|---|
| `0` | Published, and the body read back byte for byte |
| `1` | Artifact rejected — no mutating `gh` child was started; edit preservation may make one read-only view call |
| `2` | Input or usage rejected — no `gh` child was started |
| `3` | A `gh` child failed; its exit status and stderr are reported, not swallowed. On `pr-edit` or `issue-comment-edit` a failed read-only pre-read exits here too, and no update was attempted |
| `4` | `gh` succeeded but the published body could not be verified as the checked bytes |

Exits 3 and 4 say plainly what is true: the pull request or comment may exist, nothing was
reverted, and this is not a successful publication.

Supported operations are exactly **`pr-create`**, **`pr-edit`**, **`issue-comment-create`**, and
**`issue-comment-edit`**. `pr-comment` was in an earlier draft and is gone — an operation nobody
had exercised end to end was scope, not coverage. Issue comments were added after a public
upstream comment was posted with direct `gh` and only a generic disclosure, because nothing
here covered them.

### The Bash boundary adapter

The publisher only owns the path that goes through it. `scripts/gate.py` is a separate
`PreToolUse` hook for the **Bash** tool that denies a `gh pr create`/`gh pr new`, a
body-mutating `gh pr edit`, or a `gh issue comment`/`gh pr comment` with a body flag on an
external or unresolved target, and names the publisher instead:

```bash
$ echo '{"tool_name":"Bash","tool_input":{"command":"gh pr create --repo someone/upstream --title t"}}' \
    | python3 scripts/gate.py
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny", ...}}

$ echo '{"tool_name":"Bash","tool_input":{"command":"gh pr view 12"}}' | python3 scripts/gate.py
$                     # empty: not its business
```

It never executes the command it judges. The string is lexed as data; physical lines, shell
continuations, heredoc data, wrappers, control-flow prefixes, and command substitutions are
handled explicitly. When no target is named, two bounded read-only git calls with fixed
argvs (`git rev-parse --show-toplevel`, then `git remote -v`) establish work context.

The target decides. The attribution boundary applies to external contributions; internal PRs
that target `hermes-labs-ai/*` are exempt:

- An explicit target (`--repo`/`-R`, a PR or issue URL argument, or `GH_REPO`) is silent
  only when every such target is a `hermes-labs-ai/*` GitHub repository. Any other explicit
  target — an upstream project, a personal fork, another host — is denied.
- With no explicit target, the call is silent only when the working checkout is clearly
  internal: every remote is a `hermes-labs-ai/*` GitHub repository, and the command does not
  `cd`/`pushd`, use a wrapper chdir option (`env -C`, `sudo -D`), or set
  `GIT_DIR`/`GIT_WORK_TREE` first. Otherwise the target is external or
  unknown and the call is denied. Name an internal target with `--repo hermes-labs-ai/REPO`
  when the checkout does not establish it.
- An untokenisable guarded shape, and the adapter's own failure fallback, read the same
  explicit targets as text and apply the same rule.

Metadata-only `gh pr edit`, comment commands without a body flag (`--web`, `--editor`),
read-only/help commands, shell comments and literal heredoc data, other `gh` nouns and verbs
(including `gh issue create`), and unrelated commands are silent.

For Codex, install it additively with `agent-signage install-publication-gate`; for Claude Code,
use the equivalent settings entry below. See [Wire the adapter](#wire-the-adapter).

### Attribution

```
<!-- hermes-labs:attribution v1 -->
[Rolando Bosch](https://github.com/roli-lpci) is the responsible human contributor and provided
active oversight and steering. This contribution was selected through
[Hermes Labs](https://hermes-labs.ai)’ autonomous triage and executed through its engineering
infrastructure.
<!-- /hermes-labs:attribution -->
```

Generate it rather than typing it, so the wording has one source of truth:

```bash
python3 -m agent_signage attribution --kind contribution --oversight active
python3 -m agent_signage attribution --kind review       --oversight none
```

The role noun is adapted to the work: **responsible human contributor** for a contribution,
**responsible human reviewer** for a review. The oversight clause appears only when it was
declared. The possessive follows the linked organization name in
"[Hermes Labs](https://hermes-labs.ai)’ autonomous triage", making the trailing "its" refer to
Hermes Labs rather than to the contribution.

The block is delimited, so "exactly one attribution" is checkable. Line wrapping is allowed;
rewording is not. A block inside fenced or indented code, a multiline backtick span, raw
`pre`/`code`-like HTML, or an enclosing HTML comment is rejected, because a statement nobody
sees as ordinary prose is not a disclosure.
Disclosure lines named with `--preserve` must still be present, so an agent rewriting a body
cannot quietly delete someone else's.

### `--kind` and `--oversight` are caller declarations

Stated plainly, because an earlier revision overstated it. Neither flag is verified by anything
here. An earlier draft read them from an unsigned local JSON sidecar and called it an
"attestation", which was worse than useless: ceremony that looked like verification, while
anything able to write the body could write the sidecar. Renaming it would not have fixed the
overclaim, so the sidecar is gone.

What remains is the part that actually works: **`--oversight` has no default.** Claiming that a
human provided active oversight and steering is the strongest statement this tool will publish
about a person, so it is always an explicit, recorded choice by whoever ran the command, and
the artifact may never state more than was declared. The sign printed at the moment of action
says "declared oversight", not "verified oversight", for the same reason.

The checker checks the block, not your prose. An earlier revision swept the whole body for
phrases like "approved by" and "I reviewed"; an independent review was right that this rejects
a maintainer's own true statement, misses any paraphrase of a false one, and buys the feeling
of rigour rather than rigour. It is gone.

### Recovery

| Reason code | Fix |
|---|---|
| `attribution-missing`, `attribution-duplicated` | Regenerate with `agent-signage attribution`; keep exactly one block |
| `attribution-hidden` | Move the block out of the code fence or HTML comment wrapping it |
| `attribution-wording-mismatch`, `attribution-kind-mismatch` | Replace the block with generated text for the right `--kind` |
| `oversight-claimed-beyond-declaration` | Either pass `--oversight active`, or emit the block with `--oversight none` |
| `oversight-declared-but-not-stated` | Emit the block with `--oversight active` |
| `contributor-mismatch` | The body names someone the declaration does not |
| `disclosure-dropped` | Restore the disclosure line the rewrite removed |
| `attribution-malformed` | Remove the control or bidi character from the block |

A local attribution rejection has no effect of any kind: no child is started. On `pr-edit`, a
candidate that passes locally is followed by a read-only live-body check; dropping a recognized
disclosure then rejects with only that `gh pr view` call and no update.

### Wire the adapter

Codex has an additive installer for its user-level hook file:

```bash
agent-signage install-publication-gate
```

It preserves every existing hook group, creates a timestamped backup, writes atomically, and is
idempotent. Restart Codex, open `/hooks`, and review and trust the new hook definition; Codex
does not run a new non-managed hook before that trust step. The installer prints the exact
backup or removal recovery path. The configuration is not retroactive: an already-running task
that loaded hooks before installation remains uncovered until that restart and trust step.

Codex CLI and Desktop users can also opt into a passive stale-check before `apply_patch`:

```bash
agent-signage install-codex-stale-check
```

This adds a separate `PreToolUse` matcher for `apply_patch`. The hook reads explicit file
headers from Codex's patch command and passes those paths through the ordinary stale-check.
It only supplies `additionalContext`; it cannot approve or deny the tool call. The installer
preserves other hook groups, backs up the file, and is idempotent. Restart Codex, then review
and trust the hook with `/hooks` before it runs.

The equivalent Claude Code entry can be added to the settings file that owns the session:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /abs/path/to/agent-signage/scripts/gate.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

That entry is additive: it does not touch the existing `Read|Edit|Write|NotebookEdit|Grep|Glob`
entry that runs the passive hook, and the two never share a process. Until the adapter is both
installed and active in the harness, `gh pr create` remains reachable from Bash and the
publisher is a convention, not a boundary.

### What this does not give you

- **Only the Bash tool, and only once wired and active.** A harness that reaches GitHub through the REST
  API, a browser session, an MCP server, or its own built-in PR tool never produces a Bash
  command, so this adapter never sees it. Codex and Claude Code are covered only when their
  respective `PreToolUse` entry is active; Cursor, Aider, and other harnesses remain uncovered.
- **Raw `gh api` is not judged.** `gh api repos/O/R/issues/N/comments -F body=@b.md` (and any
  other REST or GraphQL write) passes the Bash adapter silently; parsing arbitrary endpoints,
  methods and field syntax would be a far larger and riskier parser. `gh issue create --body`
  and `gh pr review --body` are likewise not guarded. Use the publisher; do not treat the
  adapter as covering these.
- **A `PreToolUse` deny is a harness-level decision, not an OS one.** Anything that can spawn a
  process outside the harness's tool loop — a Makefile target, a CI job, a shell the user opens
  themselves — is outside it.
- **`--oversight` is a declaration.** See above. It records who claimed what; it does not
  establish that a person read anything.
- **Exact readback is exact.** If GitHub ever normalises a body — line endings, trailing
  whitespace — the publisher reports a mismatch and exits 4 rather than accepting the
  difference. Keep bodies LF-only. This is the conservative direction on purpose, but it means
  a mismatch is not automatically a security event; read the two digests it prints.
- **It checks the artifact, not the work.** A body can carry a perfectly true attribution and
  describe a change nobody should merge.
- **Edit preservation is snapshot-bound.** `pr-edit` and `issue-comment-edit` automatically bind conventional trailers
  such as `Disclosure:`, `Co-Authored-By:`, and `Signed-off-by:` from the live body it reads;
  use `--preserve` for project-specific wording. Neither `gh pr edit` nor the comment PATCH
  exposes a conditional revision token, so a concurrent body edit after that pre-read remains a race. Exact post-write readback
  proves what this publisher wrote, not that no one raced it.
- **Nothing in this repository enforces the boundary on itself.**

## GitHub Actions

Use the composite action when a workflow needs a deterministic preflight of a
pull request body before a separate publication step. It checks one visible
Agent Signage attribution block; it does not publish or edit GitHub objects.

```yaml
- id: signage
  uses: ./
  with:
    body-file: artifacts/pr-body.md
    kind: contribution
    oversight: none

- run: echo "checked ${{ steps.signage.outputs.body_sha256 }}"
```

`body-file` must be a non-empty repository-relative file. `kind` must be
`contribution` or `review`, and `oversight` must be `none` or `active`; these
are caller declarations passed directly to `agent-signage preflight`. The
`body_sha256` output is the digest of the successfully checked bytes.

## Verify it yourself

```bash
agent-signage selftest        # asserts the runtime guarantees, no repo needed
agent-signage doctor          # what is live, what is inert, and what it costs here
pytest                        # full behavioural suite over real synthetic git repos
pytest tests/test_publication.py  # the publisher and the Bash adapter, against a fake gh
```

The exact-commit deterministic readback for `0.1.2`, including a safe isolated
hook demonstration, is in [`evals/proof-0.1.2.json`](evals/proof-0.1.2.json).
The live Codex installation probe for this boundary, including the intentionally harmless
current-session non-enforcement observation, is in
[`evals/codex-hook-installation.json`](evals/codex-hook-installation.json).

## Status and limitations

`0.2.0` — early, and honest about it. Six signs, tested over real synthetic git repositories,
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
- **The publication boundary is only as good as its active wiring.** The publisher owns
  execution and fails closed, which the passive file hook cannot. The separate Bash adapter
  stops direct `gh pr create` and `gh pr edit` calls only after the harness has loaded it (and,
  in Codex, the user has trusted it). API, browser, and other non-Bash paths are uncovered.
  `--kind` and `--oversight` are caller declarations, not verified facts.
- **The false-positive evidence is thin.** 0/12 and 0/8 on small corpora whose controls were
  arguably incapable of firing. That is direction, not a result.

## Research

The research behind the wider programme — on how AI systems lose meaning, misreport their own
state, and fail in ways standard evaluations miss — is published with DOIs at
[hermes-labs.ai/research](https://hermes-labs.ai/research).

The one most directly adjacent to this tool is *Precise Records, Unstable Meanings*
([10.5281/zenodo.21652317](https://doi.org/10.5281/zenodo.21652317)), a measurement-validity
audit separating what agent telemetry can actually establish from what gets claimed on top of
it. That distinction — report the measurement, not the inference — is the rule every sign here
has to satisfy.

## License

Apache-2.0 · [Hermes Labs](https://hermes-labs.ai)
