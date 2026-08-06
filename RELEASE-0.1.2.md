# agent-signage 0.1.2

The flagship sign could not fire in the case it was built for. This release fixes that,
corrects two guarantees the README overstated, and adds a way to tell whether the tool is
working at all. Six signs, unchanged. Still zero dependencies.

## The sign was off most of the time

`stale_checkout` reports how far a working copy is behind its upstream. It reads the
remote-tracking ref that git already keeps on disk, so it never touches the network — that is
what makes it safe in front of every file read.

Up to 0.1.1, the 30-minute refresh threshold was also used as a **reporting gate**. If the last
fetch was older than that, the sign returned before `commits_behind()` was ever called, spawned
a background refresh, and said nothing. The reasoning was that a stale reading is not worth
stating. That was wrong, for a reason worth writing down: the measurement was never missing.
Git had already written it into the remote-tracking ref, at a knowable moment. 0.1.1 was
computing nothing and discarding a fact it already had.

The cost was total in the common case, because a session that touches a repository once gets
exactly one encounter and that encounter was the silent one.

**Measured.** 83 local repositories, 49 with an upstream, 16 measurably behind. This machine
runs a scheduled job that bulk-fetches every 6 hours, so 0.1.1's 30-minute window covered 8.3%
of wall-clock time: 14 of 16 inside the window, **0 of 16 outside it**. Expected coverage
around 7%. A spot measurement taken at a different phase of the same cycle gave 2 of 17, which
is the same result seen from a different angle — the old rate was never a property of the
repositories, it was a property of when you happened to look.

0.1.2 reports the reading and dates it:

```
STALE CHECKOUT - clone is 11 commit(s) behind origin/main and 1 ahead as of its last fetch,
4 days ago; the gap now is unmeasured. This working copy may not be what is deployed; confirm
which source is authoritative.
Inspect: git -C <repo> fetch && git -C <repo> log --oneline HEAD..@{u}
```

**Against the published 0.1.1**, on a synthetic clone 11 behind and 1 ahead with a 4-day-old
fetch record and an unreachable remote: 0.1.1 is silent on the first, second and third touch.
0.1.2 speaks once and is then correctly quiet.

All three properties survive, and the "sound" one is the one to check carefully. The sentence
says "the gap now is unmeasured" rather than "at least 11 behind". "At least" would be an
inference, and a wrong one — upstream can be rewound, which makes the true gap smaller. Naming
the date is a measurement; projecting it forward is not. A count of zero still produces silence
however old the fetch, so reporting an old reading never becomes asserting drift the ref does
not show. And when the reading is dated, the resolving command starts with `git fetch`, because
the first useful action is no longer "read the log", it is "find out where you actually stand".

Three alternatives were costed and rejected: tuning the threshold only moves the window;
fetching synchronously puts the network on the measuring path, which the design will not do;
and a last-known-state cache adds a store whose own staleness needs reasoning about, in order
to report something git is already storing.

## Diverged repositories get a different instruction

`commits_ahead` is now reported when non-zero. A tree that is purely behind fast-forwards; a
tree that is behind *and* ahead has diverged and needs a rebase or merge decision. Of the 16
repositories measured behind, **13 were also ahead** — so the common case was the one the sign
was describing least usefully.

## Two guarantees that were not true

Both were found by auditing the shipped artifact against its own README.

**"A hard deadline caps every invocation."** `DEADLINE_S` was checked only after every sign had
already run. By then the work was done, and the only thing the check could still do was discard
a true fact that had cost the time to produce. The deadline is now passed into `evaluate()` and
checked before each sign starts, and inside `concurrent_worktree_edit`'s per-worktree scan —
the one loop whose cost grows with the repository. Partial results are kept, and a truncated
scan says "at least N", because the paths it listed are exact while the total has become a
floor. The bound is now stated honestly: the deadline plus at most one in-flight `git` call,
which is itself capped at 2 seconds.

**"No network on the hot path."** The test asserting it patched `subprocess.run` and tripped on
the word "fetch". The only call in the codebase that reaches the network,
`spawn_background_fetch`, uses `subprocess.Popen` — so the guard was structurally incapable of
seeing it, and only one of the six signs was exercised. The test now guards both primitives
across every sign and trips on any network-capable git subcommand. The claim is scoped to match:
no sign's measurement contacts the network, and the one call that does is a detached, never
awaited `git fetch`, which `AGENT_SIGNAGE_NO_FETCH=1` disables without making the tool quieter.

## Session dedupe was suppressing new information

The session stamp keyed on `(session, repo, sign)` with no state token, so upstream advancing
mid-session was muted for the rest of that session — the tool would say "3 behind" and then
never mention 40. It also quietly defeated the background refresh, whose entire purpose is that
the *next* touch can correct the count. Both suppressions are now state-bound, as `state.py`
always documented acknowledgement to be. Re-firing is bounded by construction: the token only
changes when the measured situation changes.

## `agent-signage doctor`

A tool that is silent by design cannot be distinguished from a broken install. `doctor` answers
that directly for one repository: whether a hook entry naming this package exists in any
settings file, what git says about the repo now, and for each sign whether it speaks here or
the measured reason it does not.

Run against this project's own repository, the first thing it reported was
`no upstream is configured for main — nothing to compare against`, which means `stale_checkout`
is inert here and no amount of drift would ever produce a sign. That is the class of answer it
exists to give. It exits non-zero only when no hook entry is found, and has no side effects:
no stamps, no fetch.

## The latency table was wrong for large repositories

0.1.0 and 0.1.1 published ~62 ms read / ~71 ms edit, measured on a small repository, with no
note that anything scaled. Re-measured across repository shapes:

| Case | p50 |
|---|---|
| Read in a repo, any number of worktrees | ~76 ms |
| Edit, no other worktree | ~84 ms |
| Edit, 22 other worktrees | ~332 ms |

A write costs roughly 84 ms + ~11 ms per sibling worktree, because `concurrent_worktree_edit`
runs one `git status` per worktree. Reads are flat — that sign fires on write-shaped tools only.
An independent audit measured 356–427 ms on a 21-worktree repo; this reproduces it.

The loop is deliberately **not** capped at a fixed worktree count. That would quietly cut
coverage for the people running the most parallel agents, who are exactly who the sign exists
for. It stops at the deadline instead.

## Verification

103 tests over real synthetic git repositories (74 in 0.1.1), `selftest` 15/15, ruff clean.
The wheel and sdist were listed file-by-file; the sdist allow-list still leaks nothing. Installed
into a clean virtualenv: `agent-signage==0.1.2` and `pip`, nothing else. Both behaviour fixes
were verified end-to-end against 0.1.1 installed from PyPI, not against the source tree.

Numbers and method are in `evals/metrics-0.1.2.json`.

## Not changed

The false-positive evidence is still thin — 0/12 and 0/8 on small corpora whose controls were
arguably incapable of firing. That remains the most valuable thing to improve, and 0.1.2 does
not improve it.
