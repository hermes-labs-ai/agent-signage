# Design

This document draws directly from the module docstrings in `src/agent_signage/`.
It explains the choices, not the mechanics — the mechanics are the code and the
103 tests in `tests/`, which are the specification.

## Origin

On 2026-08-05 a consistency pass was run against a checkout 26 commits behind
the deployed branch. Every edit made during that pass was correct and applied
to a version no user could see. Nothing errored. Nothing warned. It was caught
incidentally, not by design. `agent-signage` exists to make that class of
mistake visible at the moment it would happen, not after.

## Why a sign is temporary and a rule is not

There is a second reason not to put this in `CLAUDE.md`, separate from whether it fires at the
right moment.

A standing instruction is in context for every request. It is attended to while the model is
writing a migration, reviewing a diff, or answering something about documentation — not only
during the situation it was written for. It shapes interpretation and generation across all of
them. A constraint authored for one narrow case becomes a persistent bias on every case, and a
file full of such rules trades general capability for a set of reflexes that are irrelevant
most of the time.

A sign exists only for the tool call that needed it. Before and after, the context is identical
to a session where this tool was never installed. That property is worth more than the tokens
it saves: the model is constrained where the constraint is load-bearing, and left alone
everywhere else.

This is a design argument rather than a measured one. The token cost of a standing rule is easy
to measure and small. The conditioning cost is not something this project has quantified, and
the claim is stated here as reasoning, not as a result.

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
in-progress bisect/rebase/merge/cherry-pick, a vendored path, an
already-acknowledged state, a fact already reported this session — every one of
these ends the function with `None`, and every one of them means the same
thing: *this invocation has no fact to report*, not *this repo is current*.

Since 0.1.2, "remote knowledge is too stale to trust" is no longer on that list.
It was, and it was the wrong kind of silence: the fact existed and was being
thrown away rather than absent. See below.

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
the network on the hot path of every file read "unacceptable." When remote
knowledge exceeds `DEFAULT_FETCH_TTL_S` (30 minutes), `stale_checkout()`
spawns a detached `git fetch` so the next touch has fresh data. A cooldown
(`state.FETCH_COOLDOWN_S`, 120 seconds) prevents a slow fetch from spawning
one fetch per tool call while it's in flight.

## What 0.1.2 changed, and why it did not cost soundness

Up to 0.1.1 that same threshold also *gated reporting*: past it, the sign
returned `None` before `commits_behind()` was ever called. The reasoning was
that a stale reading is not worth stating. It was wrong for a reason worth
recording, because the mistake is easy to repeat.

The measurement was never missing. Git's remote-tracking ref is on disk,
written by the last `git fetch` or by `git clone`, and `rev-list --count
HEAD..@{u}` reads it without a network call. 0.1.1 was computing nothing and
discarding a fact it already had.

The cost of that was total in the common case. A session that touches a repo
once gets exactly one encounter, and on a machine where a scheduled sweep
fetches every 6 hours, a 30-minute reporting window covers 8% of wall-clock
time. Measured across 83 local repositories — 49 with an upstream, 16
measurably behind — the old gate reported 0 of 16 outside that window. The
flagship sign was effectively off.

0.1.2 reports the reading and dates it:

> `12 commit(s) behind origin/main as of its last fetch, 4 days ago; the gap
> now is unmeasured`

Each of the three properties survives intact, and it is worth being explicit
about why, because "report a stale number" sounds like exactly the kind of
trade this project refuses:

**Sound.** The count is what git computed from a ref it wrote at a knowable
moment. The sentence states both. What it does *not* do is extrapolate: the
phrase is "the gap now is unmeasured", not "at least 12 behind". "At least"
would be an inference and a wrong one — upstream can be rewound, which makes
the true gap smaller. Naming the date is a measurement; projecting it forward
is not.

**Silent.** `commits_behind` returning 0 still produces nothing, however old
the fetch. Reporting an old reading is not the same as asserting drift the ref
does not show.

**Actionable.** When the reading is dated, the resolving command starts with
`git fetch`, because the first useful action is no longer "look at the log",
it is "find out where you actually stand".

The alternatives were costed and rejected: tuning the TTL only moves the
window; fetching synchronously inside the deadline puts the network on the
measuring path, which is the one thing the design will not do; and persisting
a last-known-state cache adds a store whose staleness has to be reasoned about
separately, to report a fact git is already storing.

`test_never_fetches_on_the_measuring_path` asserts the measuring path stays
network-free across every sign, and
`test_the_only_network_call_is_the_detached_refresh` pins the single call that
is allowed to reach the network to a detached, never-awaited `git fetch`.

## What this converges on, and who got there first

The freshness gate in 0.1.1 was a rediscovery of a solved problem, and it is worth
saying so rather than letting a reader find out.

A warning system whose silence is meaningful cannot also use silence to mean
"my data was too old to check." Once anything other than all-clear can suppress
the alarm, silence stops being a signal. Monitoring has known this for decades
and answers it with a third state: Nagios distinguishes UNKNOWN from OK, aircraft
instruments carry off-flags so a dead gauge does not read as a steady one, and
deadman switches exist precisely so that "no alert" is distinguishable from "the
alerter is gone." 0.1.1 had two states where it needed three, and chose the wrong
one to collapse into.

The arithmetic generalises even if the lesson does not: the availability of any
freshness-gated report is the gate window divided by the refresh cadence. If you
do not control the cadence, you do not control your coverage — which makes a
one-line UI guard into an unacknowledged dependency on someone else's cron job.

## Why a stale count is a floor, not a guess

There is a second reason the old gate was worse than merely miscalibrated, and it
is specific to this signal.

Staleness here has a direction. The upstream ref only moves forward, and pulling
is itself a fetch — so an old reading cannot overstate the gap. A four-day-old
"11 behind" means *at least* 11. The number is a floor.

That inverts the gate's intent. An old fetch record means the user has not pulled
in days, which is exactly when they are furthest behind. The rule therefore
withheld the warning in proportion to how much it was likely to matter: the worse
the situation, the quieter the tool. The 0 of 16 measurement above is that
inversion showing up as a number.

The sign still does not say "at least 11" — see the soundness argument in the
previous section. Reporting the count with its date lets the reader draw the
floor themselves, which is a measurement; asserting it would be an inference.
One caveat keeps this from being airtight: a force-pushed or rebased upstream can
move backwards, so the floor property holds for ordinary deploy branches and not
by construction.

## Why suppression is bound to state, not to the repository

`state.py` documented acknowledgement as state-bound from the start: an ack
keyed to `(repo, sign, state_token)` can only ever suppress the exact fact
that was true when it was given. Session dedupe did not follow the same rule —
it keyed on `(session, repo, sign)` — and the gap had two consequences.

Upstream advancing mid-session was muted for the rest of that session: the
tool had said "3 behind" and would not say "40 behind" an hour later, because
it had already spoken about that repo.

Worse, it defeated the background refresh in the single-session case the
refresh exists for. The sequence is: first touch finds an old fetch, reports a
dated count, starts a fetch; the fetch lands; the next touch can now report a
current count. Suppressing that second report meant the refresh was doing work
whose entire product was discarded.

Both suppressions are now keyed to the state token. Re-firing is bounded by
construction — the token changes only when the measured situation changes — so
the ceiling is one line per distinct fact, which is the same ceiling the ack
mechanism has always had.

## Why the deadline had to be handed down rather than checked at the end

`hook.DEADLINE_S` was consulted once, after `signs.evaluate()` had returned. By
then all six signs had run, so the check could not shorten anything; the only
thing it could still do was discard a true fact that had already cost the time
to produce. The README nonetheless advertised it as a bound on every
invocation, and no test referenced it at all.

It is now passed into the `Context` and checked before each sign starts, and
inside `concurrent_worktree_edit`'s per-worktree scan — the one loop whose cost
grows with the repository rather than the file. Partial results are kept, on
the same logic as everywhere else: what was measured before the clock ran out
is still true, and a truncated worktree scan says "at least N" because the
paths it did list are exact while the total is now a floor.

The honest statement of the bound is what the README now carries: the deadline
plus at most one in-flight `git` call, which `gitfacts.GIT_TIMEOUT_S` caps at
two seconds.

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
