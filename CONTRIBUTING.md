# Contributing

## Setup

```bash
git clone https://github.com/roli-lpci/agent-signage.git
cd agent-signage
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running the checks

```bash
pytest                    # 68 tests, real synthetic git repos, no mocking of git
ruff check src tests      # lint
agent-signage selftest    # runtime guarantees, no repo needed
```

All three run in CI (`.github/workflows/ci.yml`) on Python 3.9, 3.11, and 3.13.
A PR that doesn't pass all three won't be merged.

## Project layout

- `src/agent_signage/gitfacts.py` — every git call, isolated. Nothing here infers;
  every function returns what git actually reported, or `None`.
- `src/agent_signage/signs.py` — the sign registry and the one shipped sign,
  `stale_checkout`.
- `src/agent_signage/state.py` — session dedupe, acknowledgement, and fetch
  cooldown stamps.
- `src/agent_signage/hook.py` — the PreToolUse entry point: stdin JSON in,
  `additionalContext` JSON or nothing out.
- `src/agent_signage/__main__.py` — the CLI.
- `tests/test_stale_checkout.py` — the behavioural spec. If you're unsure whether
  a change is in scope, this file is the source of truth, not this document.

## The bar for a new sign

`src/agent_signage/signs.py` states it directly, and it's worth repeating here
because it's the part contributors most often push against:

> A sign must satisfy three properties, and anything that cannot is not a sign
> and does not belong here:
>
> - **Sound** — it reports a measurement, never an inference. If it fires, the
>   stated fact is true.
> - **Silent** — it produces nothing at all when there is nothing to say. The
>   cost of the mechanism in the common case is zero tokens.
> - **Actionable** — it ends in the command that resolves it, so the reader is
>   never left holding a problem with no next step.

### Why a heuristic-based sign will be declined

"Heuristic" here means anything that scores, estimates, guesses, or infers —
confidence thresholds, "this file looks risky," "this diff smells large,"
similarity scores, anything trained or tuned. That fails **sound** by
construction: a heuristic can be wrong about the fact it's asserting, and a
sign that can be wrong is not a road sign, it's an opinion wearing a road
sign's format. The whole reason this project is worth putting in front of
every file read is that when it speaks, the fact is true — every measurement
in `gitfacts.py` is something git itself reported, and every suppression rule
in `signs.py` is drawn from a fact (a marker file, a config value, a
timestamp), never a guess.

A heuristic sign also tends to fail **silent**: heuristics degrade gracefully
by producing more noise at lower confidence, not less, which is the opposite
of what this hook is for. And it tends to fail **actionable**, because a score
or a hunch rarely has a single command that resolves it the way `git fetch`
or `git rebase --continue` resolves a measured fact.

If your proposed sign reports something git (or another tool) can state as a
fact — "you are N commits behind," "this branch has no upstream," "this file
is in `.gitignore` but tracked" — it's in scope. If it requires judgment about
whether something is *probably* fine, it isn't, no matter how useful the
judgment would be. Open an issue describing the fact you want to surface
before writing code; it's a fast way to find out which side of that line it's
on.

### Style

- No new runtime dependencies. Standard library only.
- New git calls go in `gitfacts.py`, bounded by a timeout, returning `None` on
  any failure. Never let a subprocess hang the hook.
- New suppression logic must be a fact check, not a guess, per the section
  above.
- Tests build real temporary git repositories (see `tests/conftest.py` and the
  fixtures in `tests/test_stale_checkout.py`), not mocked git output — the
  soundness claim is "we report what git reports," and a mock only proves
  fidelity to the mock's own assumptions.
