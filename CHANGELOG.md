# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once it reaches 1.0. Before 1.0, minor version bumps may include breaking changes.

## [Unreleased]

### Added
- A composite GitHub Action that installs this checkout, runs the existing
  deterministic `agent-signage preflight` command on a repository-relative
  Markdown body, and exposes the checked body SHA-256. The action never
  publishes or edits a GitHub object.

## [0.2.1] - 2026-09-12

Everything below landed after the `v0.2.0` tag (commits `443f25a`..`8162969`, none an ancestor of
`v0.2.0`). The published 0.2.0 distribution has no `issue-comment-create`, no `--selection
unspecified`, and no external-target scoping; its changelog entry for issue comments was added
after release in error and is moved here.

### Added
- **Issue comments cross the publication boundary.** A public upstream comment was posted with
  direct `gh` and only a generic disclosure: the publisher supported only `pr-create`/`pr-edit`
  and the Bash adapter was silent on `gh issue comment`. `agent-signage publish
  issue-comment-create --issue N` and `issue-comment-edit --issue N --comment ID` now snapshot the
  body once, apply the same attribution/selection checks, send the bytes to `gh api` on stdin
  (`-F body=@-`), and read the comment back by concrete ID, requiring the exact body, the
  declared issue, and (with `--selection`) `github.com` and the bound comment author. Edits
  pre-read the comment and keep its recognized disclosures. The Bash adapter now denies
  `gh issue comment` and `gh pr comment` with a body flag on external or unresolved targets. Raw
  `gh api` writes remain unparsed and are documented as a bypass.
- `agent-signage publish --selection unspecified`: neutral attribution for proven agent
  production whose original selection is unknown. `pr-create --head` accepts a cross-fork
  `owner:branch` head and validates it.

### Changed
- The Bash adapter scopes PR/comment attribution enforcement to external targets. Targets under
  `hermes-labs-ai/*` (explicit `--repo`/`-R`, URL, `GH_REPO`, or, with none named, every remote of
  the checkout with no directory or `GIT_DIR`/`GIT_WORK_TREE` shift) are exempt; anything else is
  external or unresolved and denied.

### Fixed
- `GH_REPO` is bound to the guarded `gh` invocation it applies to. A value set through a string
  the shell re-parses (`bash -c`, `eval`, `env -S`, `$(...)`) or a standalone assignment leaves the
  target unresolved, so the command is denied instead of falling back to the checkout's remotes.
- The deny message names the installed `agent-signage` CLI entrypoint rather than an ambient
  `python3 -m agent_signage`, which could load a different checkout.

## [0.2.0] - 2026-09-07

### Added
- `agent_signage.lab_card`: turn a Hermes Reliability Lab `hermes.reliability-lab.result/1` envelope into an operational Card, or into nothing. A card is licensed only by `mode: executed` and `status: pass`; anything else -- preview, warn, unknown, fail, or the wrong schema -- returns `None`.
- `python -m agent_signage.evidence`: CLI/library wrapper that reads a source envelope, renders through `lab_card`, and reports the outcome (card or no card, and why) as an `agent-signage`-authored `hermes.reliability-lab.result/1` envelope of its own. Also refuses a source whose `status` disagrees with its own `findings`.
- `Card.strict()`: the validated card-construction path `load_card` already used, now exposed for any caller building a card in memory rather than from a JSON file.

- **Publication boundary for GitHub pull requests.** Two pieces with opposite jobs, and the
  first part of this tool allowed to fail closed.

  `agent-signage publish pr-create|pr-edit` **owns execution**. It opens the body once on a
  bounded `O_NOFOLLOW|O_NONBLOCK` descriptor, checks that snapshot, emits the action-time sign,
  runs `gh`
  with an argv list and `--body-file -` so the checked bytes go to the child's stdin, then reads
  the body back with `gh pr view --json body` and requires exact equality. Exit `0` published
  and verified, `1` artifact rejected with no mutating child (edit preservation may first read
  the live body), `2` input rejected, `3` a
  `gh` child failed and its status and stderr are reported (a failed `pr-edit` pre-read exits
  here with no update attempted), `4` `gh` succeeded but the published
  body could not be verified. Success is never claimed before the readback, and exits 3 and 4
  say plainly that the pull request may exist and nothing was reverted.

  `agent-signage gate` is a separate `PreToolUse` adapter for the **Bash** tool that denies
  scoped `gh pr create`/`gh pr new` and body-mutating `gh pr edit` calls and names the publisher
  instead. It never executes the command under judgment: the command is lexed as data, so
  compound, substituted, wrapped and path-qualified attempts are caught by segment. Narrow on
  purpose — `gh pr view`, `gh pr list --search create`, `gh issue create` and every unrelated
  command are silent, and malformed payloads produce nothing. It accepts both Claude Code's
  `tool_input.command` and Codex unified exec's `tool_input.cmd` event shapes.

  `agent-signage install-publication-gate` adds that adapter to the Codex user hooks file. The
  edit is idempotent and atomic, preserves existing hook groups (including Hermes Gate), and
  creates a timestamped backup before changing an existing file. Codex requires the new hook to
  be reviewed and trusted after restart; the installer states that step and its recovery path.
  `evals/codex-hook-installation.json` records the live configuration readback, direct handler
  probes, and the honest observation that an already-running task does not gain enforcement
  retroactively.

  Neither is reachable from `hook.py`, which is unchanged and still fails open, cannot deny, and
  cannot exit non-zero. `selftest` and `test_the_generic_hook_is_unchanged_and_cannot_deny`
  assert that separation.

- `agent-signage attribution` prints the block to paste into a body, so the published wording
  has one source of truth. `agent-signage preflight` checks an artifact without publishing.
- `evals/publication-boundary.json` — 52 cases, every one driving a real supported surface: the
  publisher end to end against `tests/fake_gh.py`, or the Bash adapter's full hook payload,
  scope, and deny-output contract.

### Changed
- **Wording corrected.** The possessive now follows the linked organization name —
  `[Hermes Labs](https://hermes-labs.ai)’ autonomous triage` — so the trailing "its" refers to
  Hermes Labs rather than to the contribution. A review is attributed to the
  **responsible human reviewer**; a contribution to the **responsible human contributor**.
- **`--oversight` has no default and must be chosen explicitly**, on every command that takes
  it. Claiming a human provided active oversight is the strongest statement this tool will
  publish about a person, and it is never the fallback. The action-time sign says "declared
  oversight", not "verified".
- `/evals` is included in the sdist; the boundary suite executes a fixture from it.
- `selftest` grew eleven boundary checks (15 → 26).

### Removed
- **The unsigned oversight sidecar.** An earlier draft of this work carried `--attestation`, a
  local JSON file naming the artifact digest. An independent review was right that nothing
  signed it and anything able to write the body could write it, so it established that two files
  agreed rather than that a person had read either. Renaming it to "declaration" would have kept
  the ceremony while admitting the mechanism proves nothing, so it is deleted. `--kind` and
  `--oversight` are flags, documented as caller declarations. `--receipt`, `--receipt-out` and
  `--max-age-hours` went with it.
- **The whole-body claim scan.** It swept for phrases like "approved by" and "I reviewed" and
  rejected a maintainer's own true statement while any paraphrase walked through. The property
  it reached for is enforced where it is checkable: the published wording is generated and
  compared exactly, and the oversight clause appears only at the declared level.
- **`--prepare` and `pr-comment`.** Preparing an argv and handing it back is not a boundary —
  the file can change between the check and the send, and what the caller does with a prepared
  command is unobservable. `pr-comment` was scope nobody had exercised end to end.

### Fixed
- `Card(...)` (the bare dataclass constructor) applied none of the bounds, one-line, or control-character checks `load_card` enforces; a card assembled in memory -- exactly what `lab_card` does -- could carry a multi-line headline or an oversized field straight into `render_text`. `load_card` and `lab_card` now both go through `Card.strict()`, the one validated path.
- **An attribution block hidden inside a fenced code region or an enclosing HTML comment is now
  rejected.** The first version only asked whether the markers appeared in the text, so a block
  that rendered as a code sample, or did not render at all, satisfied it. Ordinary comments and
  code fences elsewhere in the body are unaffected.
- The Markdown visibility check now also rejects four-space indented code blocks and requires a
  fenced region to close with the same delimiter character at least as long as its opener.
- The Bash adapter now follows option-bearing `sudo`, `env`, and `nice` wrappers, shell
  negation/control-flow prefixes, and the built-in `gh pr new` alias. Shell comments and
  `--help`/`-h`/`--version` calls remain silent rather than becoming false positives.
- `pr-edit` now pre-reads the live body and automatically preserves conventional disclosure
  trailers present in that snapshot; `--preserve` covers project-specific lines. This guarantee
  is explicitly snapshot-bound because `gh pr edit` has no conditional revision token.
- Snapshot construction is validated defensively before any child starts, FIFOs are rejected
  without blocking, and every post-spawn failure states the possible public effect and lack of
  rollback.

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
