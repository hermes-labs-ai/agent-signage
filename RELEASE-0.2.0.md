# agent-signage 0.2.0

This release adds two explicit trust boundaries while keeping the original passive road-sign
hook unchanged.

- A checked GitHub pull-request publisher validates attribution at the action boundary, sends
  the exact checked bytes, and verifies the published body by readback. A separate opt-in Bash
  adapter can steer supported `gh pr create` and body-mutating `gh pr edit` calls through it.
- `python -m agent_signage.evidence` turns a completed, passing Hermes Reliability Lab result
  envelope into a bounded operational card, and emits no card for previews, non-passing runs,
  malformed input, or contradictory status/findings.
- `Card.strict()` gives in-memory callers the same validation path as JSON-loaded cards.
- Source distributions now include the lab fixtures required by their bundled tests.

Python 3.9+; no runtime dependencies. The passive hook remains fail-open and non-blocking. The
publication publisher is deliberately fail-closed, and its Bash enforcement applies only after
the adapter is installed and active.
