"""驗證 ENABLE_ELASTICSEARCH / ENABLE_DYNATRACE 開關對子 Agent 與 prompt 的影響。"""

import re

import pytest

from prompts import build_main_prompt
from settings import Settings
from subagents import build_subagents


def _cfg(monkeypatch, es: bool, dt: bool) -> Settings:
    monkeypatch.setenv("ENABLE_ELASTICSEARCH", str(es).lower())
    monkeypatch.setenv("ENABLE_DYNATRACE", str(dt).lower())
    return Settings()


def test_defaults_are_disabled(monkeypatch):
    monkeypatch.delenv("ENABLE_ELASTICSEARCH", raising=False)
    monkeypatch.delenv("ENABLE_DYNATRACE", raising=False)
    cfg = Settings()
    assert cfg.enable_elasticsearch is False
    assert cfg.enable_dynatrace is False


@pytest.mark.parametrize(
    ("es", "dt", "expected"),
    [
        (False, False, ["k8s-agent", "prometheus-agent"]),
        (True, False, ["k8s-agent", "prometheus-agent", "log-agent"]),
        (False, True, ["k8s-agent", "prometheus-agent", "dynatrace-agent"]),
        (True, True, ["k8s-agent", "prometheus-agent", "log-agent", "dynatrace-agent"]),
    ],
)
def test_subagents_follow_toggles(monkeypatch, es, dt, expected):
    cfg = _cfg(monkeypatch, es, dt)
    specs = build_subagents(cfg)
    assert [s["name"] for s in specs] == expected
    for spec in specs:
        # deepagents 0.7 要求子 Agent 明確提供 model 與 tools
        assert spec["model"] == cfg.model
        assert spec["tools"]


@pytest.mark.parametrize(("es", "dt"), [(False, False), (True, False), (False, True), (True, True)])
def test_prompt_only_mentions_enabled_agents(monkeypatch, es, dt):
    prompt = build_main_prompt(_cfg(monkeypatch, es, dt))
    assert ("log-agent" in prompt) is es
    assert ("dynatrace-agent" in prompt) is dt
    # 判斷標準不應出現未啟用來源的規則
    assert ("錯誤日誌暴增" in prompt) is es
    assert ("AVAILABILITY" in prompt) is dt
    # 報告開頭要標註未啟用的資料來源
    header = next(line for line in prompt.splitlines() if line.startswith("- 資料來源"))
    assert ("Elasticsearch：已啟用" in header) is es
    assert ("Dynatrace：已啟用" in header) is dt
    assert ("Elasticsearch：未啟用" in header) is (not es)
    assert ("Dynatrace：未啟用" in header) is (not dt)


def test_report_table_columns_match_enabled_sources(monkeypatch):
    prompt = build_main_prompt(_cfg(monkeypatch, False, False))
    header_row = next(line for line in prompt.splitlines() if line.startswith("| Deployment"))
    assert re.findall(r"\| ([^|]+?) (?=\|)", header_row)[2:4] == ["Kubernetes", "Prometheus"]
    assert "Elasticsearch" not in header_row and "Dynatrace" not in header_row
