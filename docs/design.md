# Design

This document draws directly from the module docstrings in `src/agent_signage/`
and from the commit that shipped 0.0.8. It explains the choices, not the
mechanics — the mechanics are the code and the 40 tests in
`tests/test_stale_checkout.py`, which are the specification.

## Origin

On 2026-08-05 a consistency pass was run against a checkout 26 commits behind
the deployed branch. Every edit made during that pass was correct and applied
to a version no user could see. Nothing errored. Nothing warned. It was caught
incidentally, not by design. `agent-signage` exists to make that class of
mistake visible at the moment it would happen, not after.

## Why state-triggered, not wording-triggered

The alternative most people reach for is a line in `CLAUDE.md` or `AGENTS.md`:
"always check the branch is current." That costs tokens on every request and
fires on the model's memory of having read the instruction — which is exactly
the layer that fails. The agent in the incident above already knew, in the
abstract, that a local checkout can diverge from what's deployed. It had no
reason to *form that hypothesis* at turn fifteen, mid-task, when nothing in
front of it looked wrong.

`agent_signage.signs.stale_checkout` instead evaluates a condition fresh, at
the moment the agent is about to touch a specific file, and says something
only when the condition is true. It is not a reminder of a rule; it is a
measurement, taken at the point of action, of the one fact ("this repo is
behind its upstream") that the wording-based approach was trying and failing
to keep salient.

## Why soundness is never traded for coverage

`gitfacts.py`'s module docstring states the rule: "Nothing in this module
infers or guesses. Every function returns something git actually reported, or
`None`." `commits_behind()` reports `git rev-list --count HEAD..@{u}` — a
number git computed, not an estimate. `signs.py` restates it as one of three
non-negotiable properties a sign must have: **sound** — "it reports a
measurement, never an inference. If it fires, the stated fact is true."

The alternative — inferring staleness from heuristics (commit velocity,
file-age patterns, a "this looks old" score) — would catch more cases. It is
deliberately not taken, because a sign that can be wrong about the fact it
asserts is not a road sign, it is an opinion in a road sign's format, and the
entire reason this is safe to run in front of every file read is that when it
speaks, it cannot be lying. `CONTRIBUTING.md` states the consequence directly:
a contribution proposing a heuristic-based sign is declined on sight, because
it fails the soundness property by construction, not by degree.

## Why silence means "not known," not "fine"

`gitfacts.py`: "A `None` means 'not known', never 'known to be fine' — callers
must not treat absence of a fact as evidence of its opposite." This governs
every early return in `stale_checkout()`: no upstream, detached HEAD, an
in-progress bisect/rebase/merge/cherry-pick, a vendored path, remote knowledge
too stale to trust, an already-acknowledged state, an already-signed session —
every one of these ends the function with `None`, and every one of them means
the same thing: *this invocation has no fact to report*, not *this repo is
current*.

The distinction matters because `commits_behind()` can only report what the
last fetch already knew (see below); it can undercount when that fetch is
old, but it cannot invent commits that don't exist. Undercounting is a silent
miss. Overcounting would be a false alarm — a sound-seeming sign that is
sometimes wrong. The asymmetry is deliberate: agent-signage is allowed to miss
drift, and is not allowed to assert drift that isn't there.

## The freshness/coverage tradeoff, and why the hot path never fetches

`commits_behind()` never contacts the network — it reads the remote-tracking
ref as of the last `git fetch`. Coverage is therefore capped by how recently
each repo was fetched, which `evals/baseline-0.0.7.json`'s
`fetch_staleness_survey` shows directly: of seven repos surveyed, only two had
been fetched within the hour and produced a `WARN`; the rest, fetched 10 hours
to 4.4 days earlier, were silent regardless of their true state, because the
tool had no fresh fact to report.

The alternative — fetching on the hot path — was rejected outright, not
tuned around: `gitfacts.spawn_background_fetch()`'s docstring calls putting
the network on the hot path of every file read "unacceptable." Instead, when
remote knowledge exceeds `DEFAULT_FETCH_TTL_S` (30 minutes),
`stale_checkout()` spawns a detached `git fetch` and returns `None` for that
turn — the mechanism is self-healing (the next touch has fresh data) and
costs the caller nothing on the path that matters. A cooldown
(`state.FETCH_COOLDOWN_S`, 120 seconds) prevents a slow fetch from spawning
one fetch per tool call while it's in flight — `test_no_fetch_storm` asserts
exactly one fetch is spawned across ten calls in a stale window, and
`test_never_fetches_on_the_hot_path` asserts the measuring call itself never
invokes `fetch`.

This is why the README states the guarantee as "No network on the hot path,"
not "always current": the tradeoff is coverage for a hard bound on what a
file-read hook is allowed to cost.

## Why acknowledgement is keyed to state, not to the repo

`state.py`'s module docstring draws the line explicitly: "Acknowledgement —
keyed by the observed *state*, not by the repo. Once a human or agent
acknowledges '27 behind origin/main', that exact situation stays quiet. If
upstream then moves, the key no longer matches and the sign speaks again."
`agent-signage ack <repo> <sign> <token>` writes a stamp keyed by
`(repo, sign, state_token)`, where `state_token` is derived from the upstream
ref, its SHA, and the commit count (`signs.stale_checkout`: `"%s@%s:%d" %
(upstream, sha[:12], behind)`).

The same docstring names the failure this prevents: an earlier prototype keyed
session dedupe by repo alone, which silently muted the warning for every later
session until a temp directory was cleared. Keying acknowledgement to state
rather than repo generalizes the fix — it makes "stop telling me" safe to
offer at all, because the only thing an ack can suppress is the exact fact
that was true when it was given. `test_ack_expires_when_upstream_moves`
asserts this directly: acknowledging one state, then advancing upstream by one
more commit, makes the sign speak again under the new state.
