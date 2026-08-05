# agent-signage 0.0.8

First public release.

## What this is

A PreToolUse hook for coding agents. When an agent is about to read or edit a
file, it checks whether that file's git repository is behind its configured
upstream and, if so, adds one line of context: how many commits behind, and
how old the upstream tip is. It says nothing when the repo is current, has no
upstream, isn't a git repo, or is in a state where being "behind" is
deliberate (mid-bisect, detached HEAD, an in-progress rebase/merge/cherry-pick).

## What shipped

- One sign, `stale_checkout`, evaluated fresh at the moment of action rather
  than relying on a standing instruction in a prompt file.
- Reports `git rev-list --count HEAD..@{u}` — a measurement, not an inference.
  When it speaks, the fact is true.
- Never blocks: no block decision, no non-zero exit, ever. Fails open on
  malformed input, missing git, an unwritable state directory, or a hung
  subprocess.
- Bounded: a 3-second whole-invocation deadline and a 2-second timeout on
  every individual git call.
- No network on the hot path. The measuring call never fetches; when remote
  knowledge is older than 30 minutes, a detached `git fetch` is spawned in the
  background (with a 120-second cooldown against fetch storms) and the hook
  stays silent for that turn.
- One sign per (session, repository); acknowledgement (`agent-signage ack`)
  keyed to the observed state, so an ack can never suppress genuinely new
  drift.
- CLI: `ack`, `clear`, `signs`, `selftest`, `install` (writes the
  `settings.json` hook entry idempotently, with a timestamped backup).
- Zero runtime dependencies, Python 3.9+.
- 40 tests over real synthetic git repositories (`tests/test_stale_checkout.py`).

## What changed since the phase-0 prototype

Two assumptions behind the design were tested rather than trusted before this
release, and one of them was wrong:

- **Confirmed**: `PreToolUse` `additionalContext` reaches the model. Verified
  end-to-end against an isolated session that quoted the injected token back
  verbatim.
- **Corrected**: Python process startup on the hot path was assumed to cost
  roughly 100ms. Measured at 17ms. The originally planned two-language hot
  path (a fast path in something other than Python, with Python reserved for
  the CLI) was dropped in favor of a single Python implementation.

Both measurements are recorded in `evals/baseline-0.0.7.json`.

## Status

This is 0.0.8: one sign, fully tested, in production use at Hermes Labs. The
sign registry is designed to be extended by third parties without forking
(`agent_signage.signs.register`), but only one sign ships in this release, and
the bar for adding another is stated in `CONTRIBUTING.md` — sound, silent,
actionable, with heuristic-based signs declined by design.

## Install

```bash
pip install agent-signage
```

See `README.md` for the Claude Code and generic-harness install paths.
