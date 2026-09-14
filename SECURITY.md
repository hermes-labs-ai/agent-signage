# Security

Please report suspected security vulnerabilities in agent-signage privately by email to
**roli@hermes-labs.ai** instead of opening a public issue.

Include:
- A description of the vulnerability and its impact.
- Steps to reproduce the issue.
- Any relevant logs, hook output, or `agent-signage doctor` output.

Do not include unrelated source, credentials, or private repository paths in a report — the
minimum reproducible technical detail is enough.

## Scope

agent-signage ships two separate mechanisms; a report against either is in scope:

- The passive `PreToolUse` hook (`src/agent_signage/`). Its stated guarantees are that it never
  blocks a tool call, never exits non-zero, and fails open on any error — a way to make it hang,
  block, exit non-zero, or contact the network while measuring is a valid report even without a
  further exploit.
- The publication boundary (`scripts/publish.py`, `scripts/gate.py`): the `gh`-wrapping publisher
  and the Bash `PreToolUse` adapter described in the README's "Publication boundary" section. A
  way to make the publisher report success on an unverified body, or a bypass of the Bash gate's
  documented scope, is in scope.

## Supported Versions

Security fixes are applied to the latest released version only.

## Response

Reports are reviewed personally by the maintainer. There is no fixed response-time, bug-bounty,
or support-level commitment at this stage of the project.

Thank you for helping keep agent-signage's guarantees honest.
