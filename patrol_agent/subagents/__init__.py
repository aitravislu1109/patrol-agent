"""巡檢子 Agent。

k8s 與 prometheus 固定啟用；elasticsearch 與 dynatrace 依環境變數決定，
關閉時連模組都不 import，避免載入不需要的設定與連線。
"""

from __future__ import annotations

from dataclasses import dataclass

from settings import Settings


@dataclass(frozen=True)
class DataSource:
    """一個資料來源與其對應子 Agent。"""

    key: str  # kubernetes / prometheus / elasticsearch / dynatrace
    label: str  # 報告中顯示的名稱
    agent_name: str
    enabled: bool


def data_sources(cfg: Settings) -> list[DataSource]:
    """依設定列出所有資料來源與啟用狀態（固定順序）。"""
    return [
        DataSource("kubernetes", "Kubernetes", "k8s-agent", True),
        DataSource("prometheus", "Prometheus", "prometheus-agent", True),
        DataSource("elasticsearch", "Elasticsearch", "log-agent", cfg.enable_elasticsearch),
        DataSource("dynatrace", "Dynatrace", "dynatrace-agent", cfg.enable_dynatrace),
    ]


def build_subagents(cfg: Settings) -> list[dict]:
    """依設定建立子 Agent 規格清單。"""
    from subagents import k8s, prometheus

    specs = [k8s.build_subagent(cfg.model), prometheus.build_subagent(cfg.model)]
    if cfg.enable_elasticsearch:
        from subagents import elasticsearch

        specs.append(elasticsearch.build_subagent(cfg.model))
    if cfg.enable_dynatrace:
        from subagents import dynatrace

        specs.append(dynatrace.build_subagent(cfg.model))
    return specs
