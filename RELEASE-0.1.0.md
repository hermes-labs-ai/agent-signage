# agent-signage 0.1.0

Road signs for coding agents: one measured fact about the file an agent is about to touch,
delivered at that moment, and silence the rest of the time.

## What's new since 0.0.8

Five signs, each selected against a documented failure report rather than invented. Each cites
its evidence in its own docstring.

| Sign | Reports |
|---|---|
| `symlink_escape` | the path resolves through a symlink to outside the repository |
| `conflict_markers` | the file still contains unresolved merge markers |
| `concurrent_worktree_edit` | another worktree has uncommitted changes to this same file |
| `binary_edit` | NUL bytes present, so a text-shaped edit will corrupt the file |
| `generated_file` | the header declares the file machine-generated |

`generated_file` ships with its evidence caveat stated plainly: the marker convention is
ubiquitous but no single named incident was found. Its soundness is a literal header match;
its importance is inferred.

Signs whose only consequence is a bad write now fire on write-shaped tools only. A sign that is
true but useless is how a tool like this gets muted.

## Performance

Six signs asking overlapping questions each re-spawned git, which measured at 119 ms for an
edit inside a repository. Git answers are now memoised for the duration of one evaluation and
cleared at the start of every run, so embedding the library in a long-lived process is as
correct as the one-shot subprocess. A test asserts two runs in one process observe two
different realities; another fails if a new cache is added without registering it for clearing.

Measured end-to-end as a subprocess on macOS/arm64, CPython 3.14 (`evals/metrics-0.1.0.json`):

| Case | Cost |
|---|---|
| Bare interpreter floor | ~16 ms |
| Silent — not a repo, or vendored | ~30 ms |
| Silent — clean file in a repo (Read / Edit) | ~62 / ~71 ms |

## Fixed

Ruff's default rule set widens between releases, so an unpinned `ruff>=0.6` dev dependency
silently changed what CI enforced — a fresh clone resolving a newer ruff would have failed CI
on code that was clean when written. Rules are now selected explicitly rather than inherited.
Verified by cloning fresh, building a fresh venv, and running exactly what `ci.yml` runs
against ruff 0.16.1.

Deduplication is keyed per file rather than per repository, so a second affected file in the
same session is still reported.

## Verification

68 tests over real synthetic git repositories, no mocking of git. Clean against ruff 0.16.1.
`twine check` passes on both sdist and wheel. Installs in a clean virtualenv with zero runtime
dependencies.

```bash
pytest                  # 68 passed
agent-signage selftest  # 10/10 passed
```

## Known limitations

- **Coverage is capped by fetch freshness.** `stale_checkout` reports drift only as recent as
  the last fetch. It refreshes in the background when its knowledge is stale, but it can miss
  drift. It cannot invent it.
- **Bash-invoked edits are not seen.** `cat`, `sed -i` and shell scripts carry no tool path.
- **Git only.** A stale deployed service, API or database is out of scope.
- **The guarantees are self-attested** by this repository's own test suite. That is a real bar
  but not an independent one; nobody outside the project has exercised it adversarially.
- **The false-positive evidence is thin** — 0/12 and 0/8 on small corpora whose controls were
  arguably incapable of firing. That is direction, not a result, and it is the most valuable
  thing to improve next.

## Install

Python 3.9+ and `git` on `PATH`. No package dependencies.

```bash
pip install git+https://github.com/hermes-labs-ai/agent-signage.git
agent-signage install    # writes the Claude Code hook entry
```

Apache-2.0.
