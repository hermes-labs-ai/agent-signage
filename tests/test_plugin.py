"""The marketplace artifact must run without the Python package being installed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "claude-plugin"


def test_marketplace_points_at_the_plugin_artifact():
    manifest = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["name"] == "hermes-labs"
    assert manifest["plugins"][0]["name"] == "agent-signage"
    assert manifest["plugins"][0]["source"] == "./claude-plugin"
    assert (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").is_file()
    assert (PLUGIN_ROOT / "hooks" / "hooks.json").is_file()


def test_plugin_runtime_is_synced_with_the_source_package():
    source = ROOT / "src" / "agent_signage"
    bundled = PLUGIN_ROOT / "src" / "agent_signage"
    def runtime_files(directory):
        return sorted(
            p.relative_to(directory)
            for p in directory.rglob("*")
            if p.is_file() and p.suffix in {".py", ".typed"}
        )

    source_files = runtime_files(source)
    bundled_files = runtime_files(bundled)
    assert bundled_files == source_files
    for relative in source_files:
        assert (bundled / relative).read_bytes() == (source / relative).read_bytes()


def test_plugin_hook_runs_without_a_pip_install(behind_repo, isolated_state):
    payload = {
        "session_id": "marketplace-test",
        "tool_name": "Read",
        "tool_input": {"file_path": str(behind_repo / "a.txt")},
    }
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["AGENT_SIGNAGE_STATE_DIR"] = str(isolated_state)
    result = subprocess.run(
        [sys.executable, "-S", str(PLUGIN_ROOT / "scripts" / "hook.py")],
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=behind_repo,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "3 commit(s) behind origin/main" in context
