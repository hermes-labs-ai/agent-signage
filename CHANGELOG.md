# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once it reaches 1.0. Before 1.0, minor version bumps may include breaking changes.

## [0.1.2] - 2026-08-10

The flagship sign could not fire in the case it was built for. This release fixes
that, corrects two guarantees the README overstated, and adds a way to tell
whether the tool is working at all.

### Fixed
- **`stale_checkout` was silent on first contact with any repository whose last
  fetch was old** — which, in a session that touches a repo once, is every
  encounter. `DEFAULT_FETCH_TTL_S` was being used as a reporting gate as well as
  a refresh threshold: past it, the sign returned before `commits_behind` was
  ever consulted. The measurement was never missing. Git's remote-tracking ref
  holds it on disk, written by the last fetch or by `git clone`. It is now
  reported with its date — "12 commit(s) behind origin/main as of its last
  fetch, 4 days ago; the gap now is unmeasured" — which keeps all three
  properties: still a measurement, still silent when there is nothing, still no
  network on the measuring path. Surveying 83 local repositories (49 with an
  upstream, 16 measurably behind) on a machine whose scheduled sweep fetches
  every 6 hours, the old gate could report only inside a 30-minute window after
  each sweep; outside it, 0 of 16. It now reports 16 of 16, always.
- **Session dedupe suppressed genuinely new information.** It keyed on
  `(session, repo, sign)` without the state token, so upstream advancing
  mid-session was muted for the rest of that session. That also defeated the
  background refresh: the first touch of a stale repo starts a fetch precisely
  so the next touch can correct the count, and the correction was being
  suppressed. Both suppressions are now state-bound, as `state.py` always
  documented acknowledgement to be.
- **The deadline bounded nothing.** `DEADLINE_S` was checked only after every
  sign had already run, so it discarded output rather than stopping work. It is
  now passed into `evaluate()`, checked before each sign starts and inside
  `concurrent_worktree_edit`'s per-worktree scan, and whatever was measured
  before time ran out is still reported.

### Added
- **`agent-signage doctor`** — for one repository: whether the hook is wired
  into a settings file at all, what git says about the repo right now, and for
  each of the six signs whether it speaks here or the measured reason it is
  quiet. A tool that is silent by design cannot be distinguished from a broken
  install without this. Exits non-zero only when no hook entry naming this
  package is found. It has no side effects: no session stamps, no fetch.
- **`commits_ahead`**, reported when non-zero. A tree that is purely behind
  fast-forwards; a tree that is behind *and* ahead has diverged and needs a
  rebase or merge decision. Of the 16 repositories measured behind above, 13
  were also ahead.
- **`AGENT_SIGNAGE_NO_FETCH`** disables the background refresh without making
  the tool quieter — the reading already on disk is still reported, still dated.

### Changed
- The no-network test now guards `subprocess.Popen` as well as `subprocess.run`,
  across every sign rather than one, and trips on any network-capable git
  subcommand rather than the single word "fetch". The previous version could not
  observe `spawn_background_fetch`, which is the only call in the codebase that
  reaches the network.
- The published latency table was measured on a small repository. Re-measured:
  reads are flat regardless of worktree count, but a write in a 22-worktree
  checkout costs ~330 ms, because `concurrent_worktree_edit` runs one
  `git status` per sibling worktree. The table now states the scaling instead of
  a single number. The loop is not capped — an arbitrary cap would cut coverage
  for exactly the people running many parallel agents — but it now stops at the
  deadline and says "at least N" when it does.

## [0.1.1] - 2026-08-05

### Fixed
- The 0.1.0 source distribution shipped files that were never meant to leave the
  working tree: a directory of internal integration-strategy notes, the release
  process document, and two sets of release notes. The sdist is now built from an
  explicit allow-list rather than "everything not ignored by git", so a stray file
  cannot ride along by default. The wheel was unaffected, so `pip install` never
  delivered them.
- Added the `py.typed` marker. `pyproject.toml` has declared the `Typing :: Typed`
  classifier since 0.0.8, which is a promise to type checkers; without the marker
  file in the installed wheel, mypy and pyright silently resolved every import
  from this package to `Any`. The classifier is now true.

No behaviour changes. No new dependencies. The six signs are unchanged.

## [0.1.0] - 2026-08-05

### Added
- Five signs, each selected against a documented failure report rather than invented:
  `symlink_escape` (a path resolving through a symlink to outside the repository),
  `conflict_markers` (unresolved merge markers still in the file),
  `concurrent_worktree_edit` (another worktree has uncommitted changes to the same file),
  `binary_edit` (NUL bytes present, so a text-shaped edit corrupts the file), and
  `generated_file` (the header declares the file machine-generated).
- `clear_caches()` on the git and content layers, called at the start of every evaluation, so
  embedding the library in a long-lived process is as correct as the one-shot subprocess.
- `docs/integrating.md` for harness maintainers: the stdin/stdout contract, the measured cost,
  and the five things this will never do.

### Changed
- Signs whose only consequence is a bad write now fire on write-shaped tools only.
- Git answers are memoised for the duration of one evaluation. An edit inside a repository went
  from 119 ms to 71 ms; six signs were each re-spawning git to ask the same questions.
- Ruff rules are now selected explicitly instead of inherited. The default set widens between
  releases, so an unpinned dev dependency silently changed what CI enforced — a fresh clone
  resolving a newer ruff would have failed CI on code that was clean when written.

### Fixed
- Deduplication is keyed per file, not per repository, so a second affected file in the same
  session is still reported.

## [0.0.8] - 2026-08-05

### Added

- First public release: a PreToolUse hook that reads a hook payload on stdin and,
  when the target path is inside a git repository that is behind its configured
  upstream, emits one line of `additionalContext` naming how many commits behind
  and how old the upstream tip is. Silent otherwise.
- One sign: `stale_checkout`. Registered via `agent_signage.signs.register`, which
  third parties can call without forking.
- CLI (`agent-signage`): `ack` to silence a sign until the observed state changes,
  `clear` to remove stamps, `signs` to list the registry, `selftest` to assert the
  runtime guarantees without a repository, `install` to add the hook entry to a
  Claude Code `settings.json` idempotently, with a timestamped backup.
- Suppression rules, all fact-based: vendored/build paths (`node_modules`, `vendor`,
  `.venv`, `venv`, `site-packages`, `dist`, `build`, `.next`, `.tox`, `target`,
  `__pycache__`, `.git`); detached HEAD; an in-progress bisect, rebase, merge, or
  cherry-pick; a repo listed in `AGENT_SIGNAGE_IGNORE`; a repo already fetched
  during the current session (via `AGENT_SIGNAGE_SESSION_START`); one sign per
  (session, repo, sign); and state-keyed acknowledgement via `agent-signage ack`.
- Freshness handling: remote knowledge older than `DEFAULT_FETCH_TTL_S` (30
  minutes) triggers a detached background `git fetch` and stays silent for that
  turn, with a cooldown (`FETCH_COOLDOWN_S`, 120s) so a slow fetch cannot spawn
  one fetch per tool call. The hot path itself never touches the network.
- Bounds: a 3-second whole-invocation deadline (`hook.DEADLINE_S`) and a 2-second
  per-git-call timeout (`gitfacts.GIT_TIMEOUT_S`). The hook never emits a block
  decision and always exits 0.
- 40 tests over real synthetic git repositories in `tests/test_stale_checkout.py`.
- Zero runtime dependencies. Python 3.9+.

### Notes

- Phase 0 risk retirement (`evals/baseline-0.0.7.json`): confirmed that
  `PreToolUse` `additionalContext` reaches the model (verified end-to-end against
  an isolated session that quoted the injected token back), and measured Python
  startup on the hot path at 17ms against a planning assumption of ~100ms,
  which is why this ships as a single Python implementation rather than the
  originally planned two-language hot path.
