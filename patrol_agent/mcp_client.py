"""通用的 MCP Streamable HTTP 客戶端。

每次呼叫工具都建立一個短連線：連線 → initialize → call_tool → 關閉。
巡檢的呼叫頻率不高，這樣做可以避免長連線斷線後的狀態問題，也讓 MCP server
沒啟動時只影響當次工具呼叫，不會讓整個 graph 載入失敗。

安全性：每個客戶端都帶一份工具白名單，只允許呼叫唯讀工具。即使 MCP server
本身沒有開啟唯讀模式，Agent 也呼叫不到白名單以外的工具。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

from mcp.client import Client

from settings import settings

logger = logging.getLogger("patrol.mcp")


class MCPToolError(RuntimeError):
    """MCP 工具呼叫失敗。"""


def _content_to_text(content: Any) -> str:
    """把 MCP 回傳的 content 區塊轉成純文字。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts = [_content_to_text(item) for item in content]
        return "\n".join(p for p in parts if p)
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        return json.dumps(content, ensure_ascii=False)
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(content)


def truncate(text: str, limit: int | None = None) -> str:
    """截斷過長的工具結果，並告訴模型要縮小查詢範圍。"""
    limit = limit or settings.max_tool_result_chars
    if len(text) <= limit:
        return text
    return (
        text[:limit]
        + f"\n\n[結果已截斷：原始長度 {len(text)} 字元，只保留前 {limit} 字元。"
        "請縮小查詢範圍（加上 labelSelector、限制筆數或縮短時間範圍）後再查。]"
    )


class MCPClient:
    """呼叫單一 MCP server 的客戶端，只允許白名單內的工具。"""

    def __init__(self, name: str, url: str, allowed_tools: Iterable[str]):
        self.name = name
        self.url = url.rstrip("/")
        self.allowed_tools = frozenset(allowed_tools)

    def _client(self) -> Client:
        # cache=None：巡檢需要每次都拿到最新資料，不使用回應快取
        return Client(self.url, read_timeout_seconds=float(settings.mcp_timeout_seconds), cache=None)

    async def call(self, tool: str, arguments: dict[str, Any]) -> str:
        """呼叫 MCP 工具並回傳文字結果；失敗時回傳錯誤說明而不是丟例外。

        回傳錯誤字串（而不是丟例外）是刻意的：讓子 Agent 能看到失敗原因，
        在報告裡標註「此資料來源無法取得」，而不是整次巡檢中斷。
        """
        if tool not in self.allowed_tools:
            return f"[拒絕] 工具 {tool} 不在 {self.name} MCP 的唯讀白名單內。"
        args = {k: v for k, v in arguments.items() if v is not None}
        try:
            text = await self._call(tool, args)
        except Exception as exc:  # noqa: BLE001
            logger.exception("MCP call failed server=%s tool=%s", self.name, tool)
            detail = _flatten_exception(exc)
            return f"[錯誤] 呼叫 {self.name} MCP 工具 {tool} 失敗（{self.url}）：{detail}"
        return truncate(text)

    async def _call(self, tool: str, args: dict[str, Any]) -> str:
        logger.info("MCP call server=%s tool=%s args=%s", self.name, tool, json.dumps(args, ensure_ascii=False, default=str))
        async with self._client() as session:
            result = await session.call_tool(tool, args)
        text = _content_to_text(getattr(result, "content", result))
        if getattr(result, "isError", False):
            raise MCPToolError(text or "MCP 工具回傳錯誤")
        structured = getattr(result, "structuredContent", None)
        if not text and structured:
            text = json.dumps(structured, ensure_ascii=False, default=str)
        return text

    async def list_tools(self) -> list[str]:
        """列出 MCP server 提供的工具名稱（用於連線檢查）。"""
        async with self._client() as session:
            resp = await session.list_tools()
        return [t.name for t in resp.tools]


def _flatten_exception(exc: BaseException) -> str:
    """把 ExceptionGroup 等巢狀例外攤平成一行文字。"""
    subs = getattr(exc, "exceptions", None)
    if subs:
        return "; ".join(_flatten_exception(s) for s in subs)
    return f"{type(exc).__name__}: {exc}"
