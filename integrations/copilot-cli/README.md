# agent-signage for GitHub Copilot CLI

This native Copilot CLI plugin runs agent-signage after successful calls to the
built-in `view`, `edit`, and `create` file tools. It reads only the target path
and reports a measured sign as `additionalContext`; clean operations stay
silent. Because Copilot CLI's command `preToolUse` contract does not document
context injection, this supported `postToolUse` hook delivers signs after the
file operation. It never blocks a tool call, parses shell commands, or executes
a command from hook input. The hook disables background fetches and reads only
Git state already on disk.

Install the agent-signage Python package first, then install this plugin:

```bash
python3 -m pip install --upgrade git+https://github.com/hermes-labs-ai/agent-signage.git
copilot plugin install hermes-labs-ai/agent-signage:integrations/copilot-cli
```

In a new Copilot CLI session, run `/plugin list` to confirm the plugin is
enabled. Copilot CLI hooks run only when hooks are enabled for the session and
the plugin is trusted. The package requires Python 3.9 or newer and Git.

This integration is maintained by Hermes Labs. The package and plugin are
open-source contributions from the Hermes Labs maintainers; the hook is a
deterministic local check, not an autonomous reviewer or a guarantee that a
file operation is correct.
