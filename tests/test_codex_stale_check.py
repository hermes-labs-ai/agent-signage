from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_signage import hook

pytestmark = pytest.mark.usefixtures("isolated_state")


def test_apply_patch_on_stale_checkout_emits_passive_warning(behind_repo, monkeypatch):
    monkeypatch.setenv("AGENT_SIGNAGE_NO_FETCH", "1")
    spaced_repo = behind_repo.with_name("checkout with spaces")
    behind_repo.rename(spaced_repo)
    payload = {
        "tool_name": "apply_patch",
        "tool_input": {"command": "*** Begin Patch\n*** Update File: note.txt\n@@\n-old\n+new\n*** End Patch"},
        "cwd": str(spaced_repo),
        "session_id": "codex-test",
    }
    result = hook.run(json.dumps(payload))
    assert result
    context = result["hookSpecificOutput"]["additionalContext"]
    assert "3 commit(s) behind origin/main" in context
    assert "permissionDecision" not in json.dumps(result)


@pytest.mark.parametrize("payload", [
    {"tool_name": "Bash", "tool_input": {"command": "*** Update File: note.txt"}},
    {"tool_name": "apply_patch", "tool_input": {"command": "ordinary text"}},
    {"tool_name": "apply_patch", "tool_input": {"command": "*** Update File: "}},
    {"tool_name": "apply_patch", "tool_input": {"command": "*** Update File: note.txt"},
     "cwd": "relative/path"},
])
def test_unrelated_or_malformed_patch_is_silent(payload):
    assert hook.run(json.dumps(payload)) is None


def test_codex_installer_preserves_existing_groups_and_is_idempotent(tmp_path):
    hooks = tmp_path / "hooks.json"
    existing = {
        "matcher": "^Bash$",
        "hooks": [{"type": "command", "command": "gate command", "timeout": 5}],
    }
    stop = [{"hooks": [{"type": "command", "command": "stop command"}]}]
    original = {"description": "keep", "hooks": {"PreToolUse": [existing], "Stop": stop}}
    hooks.write_text(json.dumps(original), encoding="utf-8")

    result = _cli("install-codex-stale-check", "--hooks", str(hooks),
                  "--command", "/opt/agent-signage")
    assert result.returncode == 0, result.stderr
    installed = json.loads(hooks.read_text(encoding="utf-8"))
    assert installed["description"] == "keep"
    assert installed["hooks"]["PreToolUse"][0] == existing
    assert installed["hooks"]["Stop"] == stop
    assert installed["hooks"]["PreToolUse"][1] == {
        "matcher": "^apply_patch$",
        "hooks": [{"type": "command", "command": "/opt/agent-signage",
                   "timeout": 5, "statusMessage": "Checking whether this checkout is stale"}],
    }
    backups = list(tmp_path.glob("hooks.json.bak-*"))
    assert len(backups) == 1

    repeat = _cli("install-codex-stale-check", "--hooks", str(hooks),
                  "--command", "/opt/agent-signage")
    assert repeat.returncode == 0, repeat.stderr
    assert "already installed" in repeat.stdout
    assert json.loads(hooks.read_text(encoding="utf-8")) == installed
    assert list(tmp_path.glob("hooks.json.bak-*")) == backups


def _cli(*args):
    root = Path(__file__).resolve().parents[1]
    env = {"PYTHONPATH": str(root / "src"), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.run([sys.executable, "-m", "agent_signage", *args], text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
