"""dynatrace-agent：透過 repo 內建的 Dynatrace 唯讀 MCP 查詢。

選用元件，ENABLE_DYNATRACE=true 時才會被載入。
"""

from __future__ import annotations

from langchain_core.tools import tool

from mcp_client import MCPClient
from settings import settings

client = MCPClient(
    name="dynatrace",
    url=settings.dt_mcp_url,
    allowed_tools={"dt_list_problems", "dt_list_entities", "dt_query_metrics", "dt_list_metrics", "dt_list_events"},
)


@tool
async def dt_list_problems(
    entity_selector: str | None = None,
    problem_selector: str = 'status("open")',
    time_from: str = "now-2h",
) -> str:
    """查詢 Dynatrace problem（預設：近 2 小時仍未關閉的 problem）。

    Args:
        entity_selector: 實體篩選，例如 type("CLOUD_APPLICATION"),entityName.equals("checkout")。
        problem_selector: problem 篩選，預設 status("open")。
        time_from: 起始時間，例如 now-2h、now-1d。
    """
    return await client.call(
        "dt_list_problems",
        {"entity_selector": entity_selector, "problem_selector": problem_selector, "time_from": time_from},
    )


@tool
async def dt_list_entities(entity_selector: str, time_from: str = "now-2h") -> str:
    """用 entity selector 找 Dynatrace 監控實體（含屬性與關聯）。

    Args:
        entity_selector: 例如 type("CLOUD_APPLICATION"),entityName.equals("checkout")、
            type("CLOUD_APPLICATION_NAMESPACE"),entityName.equals("payments")、
            type("SERVICE"),entityName.contains("checkout")。
        time_from: 實體需在此時間後仍有資料，例如 now-2h。
    """
    return await client.call("dt_list_entities", {"entity_selector": entity_selector, "time_from": time_from})


@tool
async def dt_query_metrics(
    metric_selector: str,
    entity_selector: str | None = None,
    time_from: str = "now-2h",
    resolution: str | None = None,
) -> str:
    """查詢 Dynatrace 指標。

    Args:
        metric_selector: 例如 builtin:service.errors.total.rate:splitBy("dt.entity.service")、
            builtin:service.response.time:percentile(90):splitBy("dt.entity.service")。
        entity_selector: 限定實體，例如 type("SERVICE"),entityId("SERVICE-ABC123")。
        time_from: 起始時間，例如 now-2h；比較基準可另查 now-1d 的同長度區間。
        resolution: 取樣解析度，例如 5m；Inf 表示整段區間彙總成一個值。
    """
    return await client.call(
        "dt_query_metrics",
        {
            "metric_selector": metric_selector,
            "entity_selector": entity_selector,
            "time_from": time_from,
            "resolution": resolution,
        },
    )


@tool
async def dt_list_metrics(text: str) -> str:
    """用關鍵字搜尋可用的 Dynatrace 指標 key。

    Args:
        text: 關鍵字，例如 kubernetes、service.errors、cloud.kubernetes。
    """
    return await client.call("dt_list_metrics", {"text": text})


@tool
async def dt_list_events(
    entity_selector: str | None = None,
    event_selector: str | None = None,
    time_from: str = "now-6h",
) -> str:
    """查詢 Dynatrace 事件（部署、設定變更、重啟等），用來找異常前後的變更。

    Args:
        entity_selector: 限定實體。
        event_selector: 例如 eventType("CUSTOM_DEPLOYMENT")。
        time_from: 起始時間，例如 now-6h。
    """
    return await client.call(
        "dt_list_events",
        {"entity_selector": entity_selector, "event_selector": event_selector, "time_from": time_from},
    )


TOOLS = [dt_list_problems, dt_list_entities, dt_query_metrics, dt_list_metrics, dt_list_events]

DESCRIPTION = (
    "Dynatrace 巡檢專家。用來查 Deployment 對應的 Dynatrace 實體是否有未關閉的 problem、"
    "服務的錯誤率與回應時間，以及近期部署或設定變更事件。"
)

PROMPT = """你是 Dynatrace 巡檢專家，只能用唯讀工具查詢 Dynatrace，判斷 Kubernetes 工作負載是否健康。

<任務>
依照主 Agent 的指示，檢查指定 namespace / Deployment 在 Dynatrace 中的狀態，回報有證據的發現。
</任務>

<可用工具>
- dt_list_entities：找出對應的實體與關聯
- dt_list_problems：查詢 problem（預設未關閉）
- dt_query_metrics：查詢指標
- dt_list_metrics：搜尋可用的指標 key
- dt_list_events：查詢部署、設定變更等事件
</可用工具>

<工作方式>
1. 先找對應實體：
   - Kubernetes workload 通常是 type("CLOUD_APPLICATION")，namespace 是 type("CLOUD_APPLICATION_NAMESPACE")
   - 用 entityName.equals("<deployment>") 找 workload，再從屬性與關聯確認它屬於指定的 namespace（同名 workload 可能存在於不同 namespace 或叢集）
   - 透過關聯找到相關的 SERVICE / PROCESS_GROUP
   - 找不到對應實體時，明確回報「Dynatrace 中找不到對應實體」，不要猜測或套用其他實體的資料
   在同一次任務中找到的實體 ID 要沿用，不要重複查詢。
2. Problem：查詢與該實體相關、仍未關閉的 problem，記下標題、嚴重程度、開始時間、受影響實體。
3. 服務指標（有對應 SERVICE 時）：錯誤率、回應時間（p90）、請求量。和前一天同時段比較，判斷是否異常。
4. 事件：查近期部署或設定變更，若時間與異常相近，在發現中指出。
5. 指標 key 不確定時先用 dt_list_metrics 搜尋，不要猜。
</工作方式>

<回報格式>
每個 Deployment 一段：

deployment: <名稱> | 資料來源: dynatrace | 狀態: OK / WARN / CRIT
發現: <一到三句重點>
證據: <實體 ID、problem ID 與標題、指標 selector 與數值，例如 {錯誤率: 4.2%（近 2h），昨日同時段 0.3%}>
建議: <可執行的下一步；狀態 OK 時寫「無」>

狀態判斷原則：
- CRIT：有未關閉且嚴重程度為 AVAILABILITY 或 ERROR 的 problem，或服務錯誤率大幅升高
- WARN：有未關閉的 PERFORMANCE / RESOURCE problem，或回應時間、錯誤率明顯高於基準
- OK：沒有未關閉的 problem，指標與基準一致

沒有證據的事情不要寫成發現。工具回傳錯誤時，寫出「無法取得」及原因。使用繁體中文，簡潔明瞭。
</回報格式>
"""


def build_subagent(model: str) -> dict:
    """建立 deepagents 子 Agent 規格。"""
    return {
        "name": "dynatrace-agent",
        "description": DESCRIPTION,
        "system_prompt": PROMPT,
        "tools": TOOLS,
        "model": model,
    }
