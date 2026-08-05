# agent-signage — handoff

State: `COMPLETE_READY_FOR_PUBLICATION` — 2026-08-05

## What this is

A PreToolUse hook that tells a coding agent one measured fact about the file it is about to
touch, at the moment it touches it, and nothing the rest of the time.

Origin: on 2026-08-05 a consistency pass ran against a checkout 26 commits behind the deployed
branch. Every edit was correct and applied to a version no user could see. It was caught
incidentally, not by design.

## Live in your harness right now

Registered in `~/.claude/settings.json` as a `PreToolUse` group matching
`Read|Edit|Write|NotebookEdit|Grep|Glob`, wrapped by `run.sh` per the current convention
(`receipt-wrap.sh` was retired 2026-08-05, harness audit H-02).

**It is running 0.1.0**, not 0.0.8 — the install is editable (`pip install -e`), so it tracks
the source tree. That was not a separate decision to integrate 0.1.0; it follows from the
editable install. If you want to pin back:

```bash
cd ~/Documents/projects/agent-signage && git checkout aadcd68 -- src/ && pip install -e .
```

**Full revert of the harness change** — remove the `Read|Edit|Write|NotebookEdit|Grep|Glob`
group from `hooks.PreToolUse` in `~/.claude/settings.json`, or restore
`~/.claude/settings.json.bak-20260805-151107`. All six pre-existing Bash hooks were verified
intact after wiring; `public-repo-gate` is still active.

Registered locally at `~/ai-infra/manifests/agent-signage.yaml` and in `TOOL_REGISTRY.md`.
Discoverable as the top `find_tool.py` match for its problem statements.

## The six signs

| Sign | Reports | Fires on |
|---|---|---|
| `stale_checkout` | repo is N commits behind upstream | read + write |
| `symlink_escape` | path resolves through a symlink to outside the repo | read + write |
| `conflict_markers` | file still contains unresolved `<<<<<<<` markers | read + write |
| `concurrent_worktree_edit` | another worktree has uncommitted changes to this file | write |
| `binary_edit` | NUL bytes present; a text edit will corrupt it | write |
| `generated_file` | header declares the file machine-generated | write |

Each cites its evidence in its own docstring. `generated_file` ships with an explicit caveat:
the convention is ubiquitous but no single named incident was found, so its soundness is
unimpeachable while its importance is inferred.

## Two assumptions that were tested, and one that was wrong

**PreToolUse `additionalContext` reaches the model** — proven end to end by injecting a unique
token and having an isolated session quote it back verbatim. This was a kill risk: if the text
had been silently dropped, every observable signal would still have looked healthy and the tool
would have been theatre.

**Python startup was assumed to cost ~100 ms; it costs ~16 ms.** That assumption had justified
a two-language hot path — a compiled fast path plus Python, meaning two build pipelines, cross
compilation, a release matrix, and an install story that stops being `pip install`. Measuring
deleted the whole subsystem before a line of it was written.

## Measured

68 tests over real synthetic git repositories. Clean against ruff 0.16.1. Wheel builds.

| Case | Cost |
|---|---|
| Interpreter floor | ~16 ms |
| Silent, non-repo or vendored | ~30 ms |
| Silent, clean file in a repo (Read / Edit) | ~62 / ~71 ms |

Memoising git answers within one evaluation took an in-repo edit from 119 ms to 71 ms.

## Known limits, stated in the README

- Coverage is capped by fetch freshness. It can miss drift; it cannot invent it.
- Bash-invoked edits (`cat`, `sed -i`) carry no tool path and are not seen.
- Git only.
- The guarantees are self-attested by this repo's own tests. Nobody outside the project has
  exercised it adversarially.
- The false-positive evidence is thin: 0/12 and 0/8 on small corpora. That is direction, not a
  result. **The highest-value next step is a real false-positive measurement with controls that
  could plausibly have fired** — the first FP corpus was criticised, correctly, for using
  controls (haikus, arithmetic) that never could have.

## Open, needs your hand

1. **Public repo does not exist yet.** Nothing was pushed; `public-repo-gate` cannot be cleared
   from a tool call, and the plan was explicitly to build to the edge and stop. To publish:
   ```bash
   gh repo create hermes-labs-ai/agent-signage --public --source=~/Documents/projects/agent-signage --push
   ```
   Review `RELEASE-0.0.8.md` and the README first. The name is free on PyPI, npm and GitHub.
2. **Four drafted integration PRs in `integrations/`** — opencode, codex, OpenHands,
   copilot-sdk. Nothing sent, no forks, no issues. The opencode draft carries a warning that
   its TypeScript snippet has never been executed and must not be sent until it has.
3. **"Signage" collides with digital-display signage in search.** Exact-name lookup on GitHub
   and PyPI is unaffected; organic discovery around the word is not viable. Lead outbound with
   the mechanism ("PreToolUse hook", "stale checkout"), not the name.
4. `~/ai-infra` has an uncommitted `TOOL_REGISTRY.md` edit and a new manifest.

## Incidental findings, not fixed here

- `~/.claude/hooks/tests/all-hooks-wrapped.test.sh` and `wrapped-hooks.test.sh` still assert
  every settings.json command starts with `receipt-wrap.sh`. They now fail against every live
  entry.
- `manifests/public-repo-gate.yaml` and `scatter-write-gate.yaml` descriptions still claim
  `wrapped by receipt-wrap.sh`.
- `TOOL_REGISTRY.md` points at `manifests/_template.yaml`; the file on disk is
  `_template.yaml.example`.
- Your canonical `~/github-projects/lintlang` checkout is 5 commits behind `origin/main` —
  found by the tool on its first live run.
