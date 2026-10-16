"""Guards for the agent's Bash tool: no secret env leakage, no answer fetches."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from memeval.agent import _WorkspaceTools, agent_bash_env, command_is_blocked


_SECRETISH = re.compile(r"(?i)(anthropic|daytona|github|gh_token|_proxy)")


def test_agent_bash_env_excludes_secrets() -> None:
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/agent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": "/tmp",
        "TERM": "xterm",
        "SHELL": "/bin/bash",
        "UV_CACHE_DIR": "/tmp/uv-cache",
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "DAYTONA_API_KEY": "dtn_secret",
        "GH_TOKEN": "ghp_secret",
        "GITHUB_TOKEN": "ghp_secret2",
        "HTTPS_PROXY": "http://proxy:8080",
        "http_proxy": "http://proxy:8080",
        "ALL_PROXY": "socks5://proxy:1080",
    }
    env = agent_bash_env(source)
    for key in env:
        assert not _SECRETISH.search(key), f"secret-ish key leaked into bash env: {key}"
    assert env["PYTEST_ADDOPTS"] == '-k "not postgres"'
    assert env["UV_CACHE_DIR"] == "/tmp/uv-cache"
    assert "ANTHROPIC_API_KEY" not in env
    assert "DAYTONA_API_KEY" not in env
    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env


def test_bash_subprocess_env_has_no_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("DAYTONA_API_KEY", "dtn_secret")
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret2")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")

    captured: dict = {}

    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs.get("env") or {}
        return _Proc()

    tools = _WorkspaceTools(tmp_path)
    with patch("memeval.agent.subprocess.run", side_effect=fake_run):
        tools.bash("echo hi")

    assert captured["env"], "subprocess.run was not called with env"
    for key in captured["env"]:
        assert not _SECRETISH.search(key), f"secret-ish key handed to subprocess.run: {key}"


def test_git_clone_is_blocked() -> None:
    assert (
        command_is_blocked("git clone https://github.com/langchain-ai/langgraph")
        == "git clone"
    )


def test_git_clone_extra_whitespace_is_blocked() -> None:
    assert command_is_blocked("git  clone https://example.com/repo.git") == "git clone"


def test_gh_and_network_tools_blocked() -> None:
    assert command_is_blocked("gh api repos/langchain-ai/langgraph") == "gh"
    assert command_is_blocked("curl https://example.com") == "curl"
    assert command_is_blocked("pip download foo") == "pip download"


def test_pytest_via_uv_is_allowed() -> None:
    assert (
        command_is_blocked(
            "cd libs/langgraph && PYTEST_ADDOPTS='-k \"not postgres\"' "
            "uv run pytest tests/test_delta_channel_id_stability.py -v"
        )
        is None
    )
