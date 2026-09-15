---
name: agent-signage
description: Use when a coding agent needs a just-in-time warning about stale checkouts, symlink escapes, conflict markers, or concurrent-worktree edits at the exact moment it reads or writes a file — agent-signage is a PreToolUse hook (Claude Code today, any subprocess-capable harness in general) that stays silent unless a measured fact matters. Non-blocking, zero dependencies, no MCP.
license: Apache-2.0
compatibility: Requires Python 3.9+ and `git` on PATH. No package dependencies. Verified with Claude Code's PreToolUse hook mechanism.
---

# agent-signage

agent-signage is a PreToolUse hook that closes a real gap: nothing in a
coding agent's tool loop normally errors if the checkout is stale, a symlink
points outside the repo, or another worktree is mid-edit on the same path —
the edit just lands, clean and wrong. Before a Read, Edit, Write, Grep, or
Glob call runs, it checks measurable git facts about the file about to be
touched and injects one short line into the agent's context only when
something matters; the rest of the time it says nothing.

## Use it for

- Warning an agent, at the moment it touches a file, that the checkout is
  stale relative to its upstream branch
- Catching a symlink that resolves outside the repository, unresolved
  `<<<<<<<` conflict markers, or a concurrent uncommitted edit to the same
  path from another worktree
- Diagnosing whether the hook is installed and which of its six signs would
  fire right now (`agent-signage doctor`)
- Wiring it into any harness that can shell out before a file operation and
  read/write JSON on stdio, not just Claude Code

## Do not use it for

- Blocking a tool call — it never emits a block decision and never exits
  non-zero; it can only add context, not stop an action
- Catching Bash-invoked edits (`cat`, `sed -i`, a shell script) — those carry
  no tool path, so nothing is checked
- A stale deployed API, database, or service — it is git-state only
- Publishing pull requests or issue comments on your behalf beyond the
  scoped `scripts/publish.py` boundary described in its own docs

## Quickstart

```bash
pip install agent-signage
agent-signage install    # writes the Claude Code hook entry for you
```

Try it without installing anything, for one session:

```bash
git clone https://github.com/hermes-labs-ai/agent-signage.git
cd agent-signage
claude --plugin-dir claude-plugin
```

Check what is live and what each sign would say right now:

```bash
agent-signage doctor
```

Any other harness can call it directly as a subprocess:

```bash
echo '{"session_id":"abc","tool_input":{"file_path":"/path/to/file.py"}}' | python3 -m agent_signage
```

## Output shape

- On stdout: either nothing (silence is the default) or one short JSON
  `additionalContext` line naming the sign, the measured fact, and the exact
  command to inspect it further
- Exit code is always `0` — a malformed event, missing git, or any internal
  error ends in silence, never a block
- `agent-signage doctor`: human-readable report of hook-entry presence, live
  git state, and each of the six signs' current speak/quiet status and reason

## Common gotchas

- Silence never means "verified current" — it means "no drift known"; an old
  fetch is reported with its age, not hidden.
- Each sign speaks once per file per session per observed state, so
  re-touching the same file will not repeat the same warning.
- `concurrent_worktree_edit` costs roughly 11 ms per sibling worktree on a
  write — expect edits to slow down in highly parallel setups.
- The separate publication-boundary tooling (`scripts/publish.py`,
  `scripts/gate.py`) is a distinct opt-in mechanism for gating `gh pr
  create`/`gh issue comment`, not part of the passive PreToolUse hook.

## More

Full docs, guarantees, and design notes:
https://github.com/hermes-labs-ai/agent-signage
