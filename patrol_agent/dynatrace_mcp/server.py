"""Dynatrace 唯讀 MCP server（Streamable HTTP）。

給 SaaS 舊版（*.live.dynatrace.com）或 Managed 使用，底層呼叫 Environment API v2。
所有工具都只做 GET 查詢。

啟動：
    uv run python -m dynatrace_mcp.server            # 預設 127.0.0.1:18110/mcp
環境變數：
    DT_BASE_URL、DT_API_TOKEN（必填）
    DT_MCP_HOST、DT_MCP_PORT、DT_SSL_VERIFY（選填）
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from dynatrace_mcp.api import DynatraceAPI, DynatraceConfigError

logger = logging.getLogger("dynatrace_mcp")

server = MCPServer(
    name="dynatrace-readonly",
    instructions="Dynatrace Environment API v2 唯讀查詢：problems、entities、metrics、events。",
)

_api = DynatraceAPI()


def _run(fn: Callable[[], dict[str, Any]]) -> str:
    """執行 API 呼叫，成功回傳 JSON 文字；失敗回傳錯誤說明。"""
    try:
        data = fn()
    except DynatraceConfigError as exc:
        return f"[設定錯誤] {exc}"
    except httpx.HTTPStatusError as exc:
        return f"[API 錯誤] {exc}"
    except httpx.HTTPError as exc:
        return f"[連線錯誤] {type(exc).__name__}: {exc}"
    return json.dumps(data, ensure_ascii=False)


@server.tool()
def dt_list_problems(
    entity_selector: str | None = None,
    problem_selector: str = 'status("open")',
    time_from: str = "now-2h",
    page_size: int = 50,
) -> str:
    """List Dynatrace problems (default: open problems in the last 2 hours).

    entity_selector example: type("CLOUD_APPLICATION"),entityName.equals("checkout")
    problem_selector example: status("open") or severityLevel("ERROR")
    time_from example: now-2h, now-1d
    """
    return _run(lambda: _api.list_problems(problem_selector, entity_selector, time_from, page_size))


@server.tool()
def dt_list_entities(
    entity_selector: str,
    time_from: str = "now-2h",
    fields: str = "+properties,+fromRelationships,+toRelationships",
    page_size: int = 50,
) -> str:
    """Find Dynatrace monitored entities with an entity selector.

    Kubernetes examples:
      type("CLOUD_APPLICATION_NAMESPACE"),entityName.equals("payments")
      type("CLOUD_APPLICATION"),entityName.equals("checkout")
      type("SERVICE"),entityName.contains("checkout")
      type("PROCESS_GROUP"),entityName.contains("checkout")
    """
    return _run(lambda: _api.list_entities(entity_selector, time_from, fields, page_size))


@server.tool()
def dt_query_metrics(
    metric_selector: str,
    entity_selector: str | None = None,
    time_from: str = "now-2h",
    time_to: str | None = None,
    resolution: str | None = None,
) -> str:
    """Query Dynatrace metrics (API v2 /metrics/query).

    metric_selector examples:
      builtin:service.errors.total.rate:splitBy("dt.entity.service")
      builtin:service.response.time:percentile(90):splitBy("dt.entity.service")
      builtin:service.requestCount.total:splitBy("dt.entity.service")
    resolution examples: 5m, 1h, Inf (single aggregated value)
    """
    return _run(lambda: _api.query_metrics(metric_selector, entity_selector, time_from, time_to, resolution))


@server.tool()
def dt_list_metrics(text: str | None = None, page_size: int = 100) -> str:
    """Search available metric keys by text, e.g. text="kubernetes" or text="service.errors"."""
    return _run(lambda: _api.list_metrics(text, page_size))


@server.tool()
def dt_list_events(
    entity_selector: str | None = None,
    event_selector: str | None = None,
    time_from: str = "now-6h",
    page_size: int = 50,
) -> str:
    """List Dynatrace events such as deployments, configuration changes, restarts.

    event_selector example: eventType("CUSTOM_DEPLOYMENT")
    """
    return _run(lambda: _api.list_events(entity_selector, event_selector, time_from, page_size))


TOOL_NAMES = ("dt_list_problems", "dt_list_entities", "dt_query_metrics", "dt_list_metrics", "dt_list_events")


def main() -> None:
    """以 Streamable HTTP 啟動 server。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    host = os.getenv("DT_MCP_HOST", "127.0.0.1")
    port = int(os.getenv("DT_MCP_PORT", "18110"))
    if not _api.base_url or not _api.api_token:
        logger.warning("DT_BASE_URL 或 DT_API_TOKEN 未設定，工具呼叫會回傳設定錯誤")
    logger.info("Dynatrace MCP listening on http://%s:%s/mcp", host, port)
    server.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
