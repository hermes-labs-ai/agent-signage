"""The marketplace artifact must run without the Python package being installed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "claude-plugin"

# Agent Plugins v1.0.0 (https://agent-plugins.org/schemas/1.0.0/plugin.schema.json):
# the field sets a submission's root plugin.json is validated against.
AGENT_PLUGIN_SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGIN_ALLOWED_TOP_LEVEL_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
AGENT_PLUGIN_ALLOWED_AUTHOR_FIELDS = {"name", "email", "url"}


def test_marketplace_points_at_the_plugin_artifact():
    manifest = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["name"] == "hermes-labs"
    assert manifest["plugins"][0]["name"] == "agent-signage"
    assert manifest["plugins"][0]["source"] == "./claude-plugin"
    assert (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").is_file()
    assert (PLUGIN_ROOT / "hooks" / "hooks.json").is_file()


def test_active_plugin_manifests_link_to_canonical_repository():
    canonical = "https://github.com/roli-lpci/agent-signage"
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert marketplace["plugins"][0]["homepage"] == canonical

    for path in (
        PLUGIN_ROOT / "plugin.json",
        PLUGIN_ROOT / ".claude-plugin" / "plugin.json",
        ROOT / "integrations" / "copilot-cli" / "plugin.json",
    ):
        manifest = json.loads(path.read_text())
        assert manifest["homepage"] == canonical
        assert manifest["repository"] == canonical


def test_root_plugin_manifest_exists_at_the_plugin_root():
    # Agent Plugins v1.0.0 expects plugin.json at the plugin root (claude-plugin/),
    # not nested under claude-plugin/.claude-plugin/ — see issue #3306.
    assert (PLUGIN_ROOT / "plugin.json").is_file()


def test_root_plugin_manifest_is_valid_json():
    manifest_text = (PLUGIN_ROOT / "plugin.json").read_text()
    manifest = json.loads(manifest_text)
    assert isinstance(manifest, dict)


def test_root_plugin_manifest_only_uses_allowed_agent_plugin_fields():
    manifest = json.loads((PLUGIN_ROOT / "plugin.json").read_text())
    assert manifest["$schema"] == AGENT_PLUGIN_SCHEMA_URL
    assert "displayName" not in manifest
    for field in manifest:
        assert field in AGENT_PLUGIN_ALLOWED_TOP_LEVEL_FIELDS, field

    author = manifest.get("author")
    assert isinstance(author, dict)
    for field, value in author.items():
        assert field in AGENT_PLUGIN_ALLOWED_AUTHOR_FIELDS, field
        assert isinstance(value, str)


def test_root_plugin_manifest_matches_existing_claude_manifest():
    root_manifest = json.loads((PLUGIN_ROOT / "plugin.json").read_text())
    claude_manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert root_manifest["name"] == claude_manifest["name"]
    assert root_manifest["version"] == claude_manifest["version"]
    assert root_manifest["author"]["name"] == claude_manifest["author"]["name"]


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
