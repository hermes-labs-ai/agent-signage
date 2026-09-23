"""The Copilot adapter accepts only the native file-hook contract."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_signage import copilot_hook

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.usefixtures("isolated_state")


@pytest.mark.parametrize(
    "tool_name,internal_name",
    [("view", "Read"), ("edit", "Edit"), ("create", "Write")],
)
def test_translates_camel_case_args_and_relative_path(tmp_path, tool_name, internal_name):
    payload = {
        "sessionId": "copilot-1",
        "timestamp": 1,
        "cwd": str(tmp_path),
        "toolName": tool_name,
        "toolArgs": json.dumps({"path": "src/example.py"}),
        "toolResult": {"resultType": "success", "textResultForLlm": "ok"},
    }

    translated = json.loads(copilot_hook.build_payload(json.dumps(payload)))

    assert translated == {
        "session_id": "copilot-1",
        "tool_name": internal_name,
        "tool_input": {"file_path": os.path.join(str(tmp_path), "src/example.py")},
    }


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "[]",
        json.dumps({"toolName": "bash", "cwd": "/tmp", "toolArgs": {"path": "/tmp/x"}}),
        json.dumps({"toolName": "edit", "cwd": "/tmp", "toolArgs": {"command": "rm -rf /"}}),
        json.dumps({"toolName": "view", "cwd": "/tmp", "toolArgs": {"path": " "}}),
        json.dumps({"toolName": "view", "cwd": "/tmp", "toolArgs": {"path": "x"},
                    "toolResult": {"resultType": "failure"}}),
    ],
)
def test_unsupported_or_malformed_event_stays_silent(payload):
    assert copilot_hook.run(payload) is None


def test_reports_stale_checkout_as_additional_context_only(behind_repo, monkeypatch):
    monkeypatch.setenv("AGENT_SIGNAGE_NO_FETCH", "1")
    payload = {
        "sessionId": "copilot-stale",
        "timestamp": 1,
        "cwd": str(behind_repo),
        "toolName": "edit",
        "toolArgs": json.dumps({"path": "a.txt"}),
        "toolResult": {"resultType": "success", "textResultForLlm": "ok"},
    }

    result = copilot_hook.run(json.dumps(payload))

    assert result is not None
    assert "3 commit(s) behind origin/main" in result["additionalContext"]
    assert set(result) == {"additionalContext"}


def test_clean_checkout_emits_nothing(behind_repo):
    subprocess.run(
        ["git", "-C", str(behind_repo), "merge", "-q", "origin/main"], check=True
    )
    payload = {
        "sessionId": "copilot-clean",
        "timestamp": 1,
        "cwd": str(behind_repo),
        "toolName": "view",
        "toolArgs": {"path": "a.txt"},
        "toolResult": {"resultType": "success", "textResultForLlm": "ok"},
    }

    assert copilot_hook.run(json.dumps(payload)) is None


def test_plugin_uses_native_copilot_posttooluse_schema():
    manifest = json.loads((ROOT / "integrations/copilot-cli/plugin.json").read_text())
    hooks = json.loads(
        (ROOT / "integrations/copilot-cli/com.github.copilot/hooks/hooks.json").read_text()
    )

    assert manifest["name"] == "agent-signage"
    assert manifest["author"]["name"] == "Hermes Labs"
    assert hooks["version"] == 1
    [hook] = hooks["hooks"]["postToolUse"]
    assert hook["matcher"] == "^(view|edit|create)$"
    assert hook["exec"] == "agent-signage-copilot-hook"
    assert hook["timeoutSec"] == 5
