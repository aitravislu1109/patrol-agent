"""巡檢觸發指令。

用法：
    # 對 LangGraph API（langgraph dev）送出巡檢，UI 裡也看得到這次巡檢的 thread
    uv run python patrol.py --namespace payments --once

    # 每 30 分鐘巡檢一次（可重複 --namespace 巡檢多個）
    uv run python patrol.py --namespace payments --namespace orders --interval 30m

    # 不經過 LangGraph API，直接在本機程序內執行 graph
    uv run python patrol.py --namespace payments --once --local

報告寫到 reports/<namespace>/<YYYYmmdd-HHMMSS>.md（可用 --reports-dir 或 PATROL_REPORTS_DIR 改位置）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from settings import settings  # noqa: E402
from timeutil import parse_duration  # noqa: E402

logger = logging.getLogger("patrol")

REPORT_PATH = "/patrol_report.md"
DEFAULT_REPORTS_DIR = _HERE.parent / "reports"


def build_request(namespace: str, now: datetime) -> str:
    """組出送給主 Agent 的巡檢指令。"""
    local = now.astimezone()
    return (
        f"請巡檢 Kubernetes namespace「{namespace}」下的所有 Deployment。\n"
        f"巡檢時間：{local.strftime('%Y-%m-%d %H:%M:%S %Z')}（UTC {now.strftime('%Y-%m-%dT%H:%M:%SZ')}）\n"
        f"時間窗：最近 {settings.lookback}\n"
        f"完成後將報告寫入 {REPORT_PATH}。"
    )


def extract_report(values: dict[str, Any]) -> str | None:
    """從 graph 最終狀態取出報告；沒有報告檔時改用最後一則 AI 訊息。"""
    files = values.get("files") or {}
    data = files.get(REPORT_PATH)
    if data:
        content = data.get("content") if isinstance(data, dict) else data
        if isinstance(content, list):  # 舊版 deepagents 以行陣列儲存
            content = "\n".join(content)
        if isinstance(content, str) and content.strip():
            return content
    for msg in reversed(values.get("messages") or []):
        msg_type = msg.get("type") if isinstance(msg, dict) else getattr(msg, "type", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if msg_type == "ai" and content:
            if isinstance(content, list):
                content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
            if str(content).strip():
                return f"<!-- 未產生 {REPORT_PATH}，以下為 Agent 最後回覆 -->\n\n{content}"
    return None


def write_report(reports_dir: Path, namespace: str, started: datetime, content: str) -> Path:
    """把報告寫入 reports/<namespace>/<時間>.md。"""
    target_dir = reports_dir / namespace
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{started.astimezone().strftime('%Y%m%d-%H%M%S')}.md"
    path.write_text(content, encoding="utf-8")
    return path


async def run_via_api(namespace: str, request: str, api_url: str, assistant_id: str) -> dict[str, Any]:
    """透過 LangGraph API 執行巡檢，回傳最終狀態。"""
    from langgraph_sdk import get_client

    client = get_client(url=api_url)
    thread = await client.threads.create(metadata={"kind": "patrol", "namespace": namespace})
    logger.info("thread=%s namespace=%s", thread["thread_id"], namespace)
    await client.runs.wait(
        thread["thread_id"],
        assistant_id,
        input={"messages": [{"role": "user", "content": request}]},
        config={"recursion_limit": settings.recursion_limit},
    )
    state = await client.threads.get_state(thread["thread_id"])
    return state.get("values") or {}


async def run_local(request: str) -> dict[str, Any]:
    """在本機程序內直接執行 graph，回傳最終狀態。"""
    from agent import agent

    return await agent.ainvoke(
        {"messages": [{"role": "user", "content": request}]},
        config={"recursion_limit": settings.recursion_limit},
    )


async def patrol_once(namespace: str, args: argparse.Namespace) -> Path | None:
    """巡檢一個 namespace 一次並寫出報告。"""
    started = datetime.now(UTC)
    request = build_request(namespace, started)
    logger.info("開始巡檢 namespace=%s", namespace)
    try:
        if args.local:
            values = await run_local(request)
        else:
            values = await run_via_api(namespace, request, args.api_url, args.assistant_id)
    except Exception:  # noqa: BLE001
        logger.exception("巡檢失敗 namespace=%s", namespace)
        return None
    report = extract_report(values)
    if not report:
        logger.error("巡檢結束但沒有取得報告 namespace=%s", namespace)
        return None
    path = write_report(Path(args.reports_dir), namespace, started, report)
    logger.info("巡檢完成 namespace=%s 報告=%s", namespace, path)
    return path


async def main_async(args: argparse.Namespace) -> int:
    if args.once or not args.interval:
        results = [await patrol_once(ns, args) for ns in args.namespace]
        return 0 if all(results) else 1

    interval = parse_duration(args.interval).total_seconds()
    logger.info("排程巡檢：namespaces=%s 間隔=%s", ",".join(args.namespace), args.interval)
    while True:
        cycle_start = asyncio.get_running_loop().time()
        for ns in args.namespace:
            await patrol_once(ns, args)
        elapsed = asyncio.get_running_loop().time() - cycle_start
        await asyncio.sleep(max(interval - elapsed, 0))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kubernetes namespace 自動巡檢")
    parser.add_argument("--namespace", "-n", action="append", required=True, help="要巡檢的 namespace，可重複指定")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="只巡檢一次（預設）")
    mode.add_argument("--interval", help="定時巡檢間隔，例如 30m、1h")
    parser.add_argument("--local", action="store_true", help="不經 LangGraph API，直接在本機程序內執行 graph")
    parser.add_argument("--api-url", default=os.getenv("LANGGRAPH_API_URL", "http://localhost:2024"))
    parser.add_argument("--assistant-id", default=os.getenv("PATROL_ASSISTANT_ID", "patrol_agent"))
    parser.add_argument("--reports-dir", default=os.getenv("PATROL_REPORTS_DIR", str(DEFAULT_REPORTS_DIR)))
    args = parser.parse_args(argv)
    if args.interval:
        parse_duration(args.interval)  # 提早檢查格式
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = parse_args(argv)
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
