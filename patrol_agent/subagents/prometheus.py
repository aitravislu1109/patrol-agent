"""prometheus-agent：透過 Prometheus MCP 查詢指標。"""

from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.tools import tool

from mcp_client import MCPClient
from settings import settings
from timeutil import parse_duration

client = MCPClient(
    name="prometheus",
    url=settings.prom_mcp_url,
    allowed_tools={
        "execute_query",
        "execute_range_query",
        "list_metrics",
        "get_metric_metadata",
        "get_targets",
        "health_check",
    },
)


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@tool
async def prom_query_instant(promql: str, time: str | None = None) -> str:
    """執行 PromQL 即時查詢，取得某個時間點的值。

    Args:
        promql: PromQL 查詢語句。
        time: 查詢時間點（RFC3339，例如 2026-11-17T08:00:00Z）；不填表示現在。
    """
    return await client.call("execute_query", {"query": promql, "time": time})


@tool
async def prom_query_range(
    promql: str,
    lookback: str | None = None,
    start: str | None = None,
    end: str | None = None,
    step: str | None = None,
) -> str:
    """執行 PromQL 範圍查詢，觀察一段時間內的趨勢。

    最簡單的用法是只給 lookback（例如 "1h"），會自動查「現在往回 1 小時」。
    需要指定區間時才給 start / end（RFC3339）。

    Args:
        promql: PromQL 查詢語句。
        lookback: 往回查多久，例如 15m、1h、6h、1d；預設使用巡檢設定的時間窗。
        start: 起始時間（RFC3339）；有給時優先於 lookback。
        end: 結束時間（RFC3339）；不填表示現在。
        step: 取樣間隔，例如 30s、1m、5m；不填時依時間範圍自動決定（約 120 個點）。
    """
    now = datetime.now(UTC)
    try:
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else now
        if start:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        else:
            start_dt = end_dt - parse_duration(lookback or settings.lookback)
    except ValueError as exc:
        return f"[錯誤] 時間參數格式不正確：{exc}"
    if not step:
        seconds = max(int((end_dt - start_dt).total_seconds() / 120), 15)
        step = f"{seconds}s"
    return await client.call(
        "execute_range_query",
        {"query": promql, "start": _rfc3339(start_dt), "end": _rfc3339(end_dt), "step": step},
    )


@tool
async def prom_list_metrics(filter_pattern: str | None = None, limit: int = 200) -> str:
    """列出 Prometheus 中有哪些指標名稱，用來探索可用指標。

    Args:
        filter_pattern: 指標名稱篩選（子字串），例如 container_、kube_deployment、http_server。
        limit: 最多回傳幾個指標名稱，預設 200。
    """
    return await client.call("list_metrics", {"filter_pattern": filter_pattern, "limit": limit})


@tool
async def prom_metric_metadata(metric: str) -> str:
    """查詢指標的型別（counter/gauge/histogram）與說明。

    Args:
        metric: 指標名稱。
    """
    return await client.call("get_metric_metadata", {"metric": metric})


@tool
async def prom_list_targets(state: str = "active") -> str:
    """列出 Prometheus 抓取目標與健康狀態，用來確認目標服務是否有被監控。

    Args:
        state: active、dropped 或 any。
    """
    return await client.call("get_targets", {"state": state})


TOOLS = [prom_query_instant, prom_query_range, prom_list_metrics, prom_metric_metadata, prom_list_targets]

DESCRIPTION = (
    "Prometheus 指標巡檢專家。用來檢查 Deployment 的資源用量（CPU 節流、記憶體接近 limit、OOM）、"
    "重啟趨勢、副本指標，以及應用層的錯誤率與延遲，並和過去同時段比較判斷是否異常。"
)

PROMPT = """你是 Prometheus 指標巡檢專家，負責用 PromQL 判斷 Kubernetes 工作負載是否健康。

<任務>
依照主 Agent 的指示，檢查指定 namespace / Deployment 的指標，回報有證據的發現。
</任務>

<可用工具>
- prom_list_metrics：探索有哪些指標（先用它確認指標存在）
- prom_metric_metadata：查指標型別與說明
- prom_query_instant：即時查詢
- prom_query_range：範圍查詢（只給 lookback 即可，例如 "1h"）
- prom_list_targets：確認抓取目標是否健康
</可用工具>

<工作方式>
1. 先探索：不同叢集的指標不一樣。第一次查詢前，用 prom_list_metrics 確認有哪些來源：
   - kube-state-metrics：kube_deployment_*、kube_pod_container_status_*、kube_pod_container_resource_*
   - cAdvisor：container_cpu_usage_seconds_total、container_memory_working_set_bytes、container_cpu_cfs_throttled_periods_total
   - 應用指標：例如 http_server_requests_seconds_*、http_requests_total（名稱依框架不同）
   不存在的指標不要硬查；查不到就在回報中說明缺少哪一類指標。
2. 對應 Deployment：用 namespace="<ns>" 加上 pod=~"<deployment>-[a-z0-9]+-[a-z0-9]+" 對應 Pod；cAdvisor 指標記得加 container!="" 和 container!="POD" 排除 pause 容器。
3. 建議檢查項目（指標存在時）：
   - 副本：kube_deployment_status_replicas_available 對比 kube_deployment_spec_replicas
   - 重啟：increase(kube_pod_container_status_restarts_total{namespace="<ns>"}[1h])
   - OOM：kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}
   - 記憶體佔 limit 比例：container_memory_working_set_bytes ÷ kube_pod_container_resource_limits{resource="memory"}
   - CPU 節流比例：rate(container_cpu_cfs_throttled_periods_total[5m]) ÷ rate(container_cpu_cfs_periods_total[5m])
   - 應用錯誤率與延遲（有應用指標時）
4. 判斷是否異常要有比較基準：用 offset 1d 或 offset 1w 比較同時段，或用 prom_query_range 看趨勢，不要只看單一數字下結論。
5. 盡量用 sum by (pod) / sum by (deployment) 聚合，避免回傳大量時間序列。
</工作方式>

<回報格式>
每個 Deployment 一段：

deployment: <名稱> | 資料來源: prometheus | 狀態: OK / WARN / CRIT
發現: <一到三句重點>
證據: <PromQL 與數值，例如 {記憶體/limit: 93%}（container_memory_working_set_bytes / limits，近 1h 最大值），與昨日同時段 61% 比較>
建議: <可執行的下一步；狀態 OK 時寫「無」>

狀態判斷原則（由你依數據判斷，但必須附數值）：
- CRIT：可用副本為 0、持續 OOMKilled、錯誤率大幅升高
- WARN：記憶體長時間 > 85% limit、CPU 節流 > 25%、近 1 小時有重啟、錯誤率或延遲明顯高於基準
- OK：指標正常且與基準一致

沒有數據支持的事情不要寫成發現。查不到指標時寫「無法取得」及原因。
使用繁體中文，簡潔明瞭。
</回報格式>
"""


def build_subagent(model: str) -> dict:
    """建立 deepagents 子 Agent 規格。"""
    return {
        "name": "prometheus-agent",
        "description": DESCRIPTION,
        "system_prompt": PROMPT,
        "tools": TOOLS,
        "model": model,
    }
