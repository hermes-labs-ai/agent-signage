# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once it reaches 1.0. Before 1.0, minor version bumps may include breaking changes.

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
