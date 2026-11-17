"""log-agent（Elasticsearch）：透過 Elastic 官方 MCP 查詢日誌。

選用元件，ENABLE_ELASTICSEARCH=true 時才會被載入。
注意：Elastic 官方 mcp-server-elasticsearch 已標示為 deprecated（僅提供安全性更新），
支援 Elasticsearch 8.x / 9.x。工具名稱可用環境變數覆寫，方便日後改接其他相容的 MCP。
"""

from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.tools import tool

from mcp_client import MCPClient
from settings import env_str, settings

TOOL_LIST_INDICES = env_str("ES_MCP_TOOL_LIST_INDICES", "list_indices")
TOOL_GET_MAPPINGS = env_str("ES_MCP_TOOL_GET_MAPPINGS", "get_mappings")
TOOL_SEARCH = env_str("ES_MCP_TOOL_SEARCH", "search")
TOOL_ESQL = env_str("ES_MCP_TOOL_ESQL", "esql")

# 給模型參考的預設 index pattern（例如 logs-*、filebeat-*、k8s-logs-*）
DEFAULT_INDEX_PATTERN = os.getenv("ES_INDEX_PATTERN", "*").strip() or "*"

client = MCPClient(
    name="elasticsearch",
    url=settings.es_mcp_url,
    allowed_tools={TOOL_LIST_INDICES, TOOL_GET_MAPPINGS, TOOL_SEARCH, TOOL_ESQL},
)


@tool
async def es_list_indices(index_pattern: str = DEFAULT_INDEX_PATTERN) -> str:
    """列出符合 pattern 的 Elasticsearch index（含 data stream 背後的 index）。

    Args:
        index_pattern: index 名稱 pattern，例如 logs-*、filebeat-*。
    """
    return await client.call(TOOL_LIST_INDICES, {"index_pattern": index_pattern})


@tool
async def es_get_mappings(index: str) -> str:
    """取得 index 的欄位 mapping，用來找出 namespace、pod、容器、日誌等級、時間欄位的實際名稱。

    Args:
        index: index 名稱或 pattern。
    """
    return await client.call(TOOL_GET_MAPPINGS, {"index": index})


@tool
async def es_search(index: str, query_body: dict[str, Any], fields: list[str] | None = None) -> str:
    """用 Query DSL 搜尋日誌。務必加上時間範圍、設定 size，並優先用聚合統計。

    Args:
        index: index 名稱或 pattern。
        query_body: 完整的 Query DSL 物件，例如 {"size": 5, "query": {...}, "aggs": {...}}。
        fields: 只回傳這些 _source 欄位，例如 ["@timestamp", "message", "kubernetes.pod.name"]。
    """
    if isinstance(query_body, str):  # 模型偶爾會傳字串
        try:
            query_body = json.loads(query_body)
        except json.JSONDecodeError as exc:
            return f"[錯誤] query_body 不是合法的 JSON：{exc}"
    return await client.call(TOOL_SEARCH, {"index": index, "query_body": query_body, "fields": fields})


@tool
async def es_esql(query: str) -> str:
    """執行 ES|QL 查詢（Elasticsearch 8.11+），適合做統計，例如依 pod 統計錯誤數。

    Args:
        query: ES|QL 查詢語句，記得加 LIMIT。
    """
    return await client.call(TOOL_ESQL, {"query": query})


TOOLS = [es_list_indices, es_get_mappings, es_search, es_esql]

DESCRIPTION = (
    "Elasticsearch 日誌巡檢專家。用來統計 Deployment 的錯誤日誌數量與趨勢、找出最常出現的錯誤訊息"
    "與例外堆疊。會先確認 index 與欄位 mapping 再查詢。"
)

PROMPT = f"""你是 Elasticsearch 日誌巡檢專家，負責從 Kubernetes 容器日誌判斷工作負載是否健康。只能查詢，不能寫入或刪除任何資料。

<任務>
依照主 Agent 的指示，檢查指定 namespace / Deployment 的日誌，回報有證據的發現。
</任務>

<可用工具>
- es_list_indices：列出 index（預設 pattern：{DEFAULT_INDEX_PATTERN}）
- es_get_mappings：查欄位 mapping
- es_search：Query DSL 搜尋與聚合
- es_esql：ES|QL 統計查詢
</可用工具>

<工作方式>
1. 第一步一定是確認欄位：用 es_list_indices 找出存放 K8s 日誌的 index，再用 es_get_mappings 找出以下欄位的實際名稱（不同收集器命名不同）：
   - namespace：例如 kubernetes.namespace（ECS / Filebeat）、kubernetes.namespace_name（Fluentd / Fluent Bit）
   - Pod / Deployment：例如 kubernetes.pod.name、kubernetes.deployment.name、kubernetes.pod_name、kubernetes.labels.app
   - 時間：通常是 @timestamp
   - 日誌等級與內容：例如 log.level、level、message、log
   在同一次任務中確認過欄位後就沿用，不要重複查 mapping。
2. 查詢一定要加時間範圍（預設最近 {settings.lookback}，例如 "range": {{"@timestamp": {{"gte": "now-{settings.lookback}"}}}}），並限定 namespace。
3. 先統計再取樣：
   - 用聚合（terms / date_histogram）統計各 Deployment 或 Pod 的錯誤數量與趨勢
   - 錯誤判斷可用日誌等級欄位（error / fatal），沒有等級欄位時用 message 比對 ERROR、Exception、panic、Traceback
   - 只取少量代表性日誌（size 3～5），不要整批貼出
4. 和前一段時間比較（例如最近 1 小時對比前 1 小時或昨日同時段），判斷錯誤是否突然增加。
</工作方式>

<回報格式>
每個 Deployment 一段：

deployment: <名稱> | 資料來源: elasticsearch | 狀態: OK / WARN / CRIT
發現: <一到三句重點>
證據: <查詢的 index 與條件、統計數字（例如 {{錯誤日誌數: 1,284（近 1h），前 1h: 37}}）、一兩則代表性日誌（時間、Pod、訊息摘要）>
建議: <可執行的下一步；狀態 OK 時寫「無」>

狀態判斷原則：
- CRIT：錯誤日誌暴增或出現大量致命錯誤（fatal、panic、OutOfMemoryError）
- WARN：錯誤數明顯高於基準，或持續出現同一類例外
- OK：錯誤數量低且與基準一致

找不到該 Deployment 的日誌時，要說明是「沒有日誌」還是「欄位對應不到」，不能當成 OK。
沒有證據的事情不要寫成發現。使用繁體中文，簡潔明瞭。
</回報格式>
"""


def build_subagent(model: str) -> dict:
    """建立 deepagents 子 Agent 規格。"""
    return {
        "name": "log-agent",
        "description": DESCRIPTION,
        "system_prompt": PROMPT,
        "tools": TOOLS,
        "model": model,
    }
