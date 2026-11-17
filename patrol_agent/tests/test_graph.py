"""驗證組好的 graph：沒有 general-purpose 子 Agent、步數上限有套用。"""

import re

import pytest
from deepagents import create_deep_agent

from prompts import build_main_prompt
from settings import Settings
from subagents import build_subagents
from think_tool import sre_think_tool


@pytest.fixture
def graph(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ENABLE_ELASTICSEARCH", "true")
    monkeypatch.setenv("ENABLE_DYNATRACE", "true")
    import agent

    return agent.build_agent(Settings())


def _task_subagents(graph) -> list[str]:
    task = graph.nodes["tools"].bound.tools_by_name["task"]
    return re.findall(r"^- ([\w-]+):", task.description, re.M)


def test_general_purpose_removed(graph):
    names = _task_subagents(graph)
    assert "general-purpose" not in names
    assert names == ["k8s-agent", "prometheus-agent", "log-agent", "dynatrace-agent"]


def test_recursion_limit_applied(graph):
    assert graph.config["recursion_limit"] == Settings().recursion_limit


def test_without_profile_general_purpose_would_exist(monkeypatch):
    """對照組：用沒有註冊 profile 的模型 spec，確認預設確實會加入 general-purpose。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = Settings()
    g = create_deep_agent(
        model="openai:gpt-test-unregistered",
        tools=[sre_think_tool],
        system_prompt=build_main_prompt(cfg),
        subagents=[{**s, "model": "openai:gpt-test-unregistered"} for s in build_subagents(cfg)],
    )
    assert "general-purpose" in _task_subagents(g)
