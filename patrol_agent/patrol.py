"""巡檢觸發指令。

用法：
    # 對 LangGraph API（langgraph dev）送出巡檢，UI 裡也看得到這次巡檢的 thread
    uv run python patrol.py --namespace payments --once

    # 每 30 分鐘巡檢一次（可重複 --namespace 巡檢多個）
    uv run python patrol.py --namespace payments --namespace orders --interval 30m

    # 不經過 LangGraph API，直接在本機程序內執行 graph
    uv run python patrol.py --namespace payments --once --local

    # 只巡檢指定的 Deployment（可重複 --deployment；此時只能指定一個 namespace）
    uv run python patrol.py --namespace payments --deployment checkout --once

報告寫到 reports/<namespace>/<YYYYmmdd-HHMMSS>.md；指定 Deployment 時為
reports/<namespace>/<YYYYmmdd-HHMMSS>-<deployment>.md（可用 --reports-dir 或 PATROL_REPORTS_DIR 改位置）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
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

# Kubernetes 名稱（RFC 1123）：namespace 與 Deployment 名稱都會用在報告路徑上，先驗證格式
_K8S_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")


def k8s_name(value: str) -> str:
    """argparse 型別：驗證 Kubernetes 資源名稱格式。"""
    if not _K8S_NAME_RE.match(value):
        raise argparse.ArgumentTypeError(f"不是合法的 Kubernetes 名稱：{value!r}（只能包含小寫英數、- 與 .）")
    return value


def build_request(namespace: str, now: datetime, deployments: list[str] | None = None) -> str:
    """組出送給主 Agent 的巡檢指令；有指定 deployments 時只巡檢這些 Deployment。"""
    local = now.astimezone()
    if deployments:
        names = "、".join(f"「{d}」" for d in deployments)
        scope = (
            "巡檢範圍：指定 Deployment\n"
            f"請只巡檢 Kubernetes namespace「{namespace}」中的 Deployment：{names}。\n"
            "不要巡檢這個 namespace 的其他 Deployment。\n"
        )
    else:
        scope = f"巡檢範圍：整個 namespace\n請巡檢 Kubernetes namespace「{namespace}」下的所有 Deployment。\n"
    return (
        f"{scope}"
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


def write_report(
    reports_dir: Path,
    namespace: str,
    started: datetime,
    content: str,
    deployments: list[str] | None = None,
) -> Path:
    """把報告寫入 reports/<namespace>/<時間>[-<deployment>].md。"""
    target_dir = reports_dir / namespace
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = started.astimezone().strftime("%Y%m%d-%H%M%S")
    if deployments:
        suffix = "_".join(deployments)
        if len(suffix) > 100:  # 指定很多 Deployment 時避免檔名過長
            suffix = f"{deployments[0]}_and_{len(deployments) - 1}_more"
        stem = f"{stem}-{suffix}"
    path = target_dir / f"{stem}.md"
    path.write_text(content, encoding="utf-8")
    return path


async def run_via_api(
    namespace: str,
    request: str,
    api_url: str,
    assistant_id: str,
    deployments: list[str] | None = None,
) -> dict[str, Any]:
    """透過 LangGraph API 執行巡檢，回傳最終狀態。"""
    from langgraph_sdk import get_client

    client = get_client(url=api_url)
    metadata: dict[str, Any] = {"kind": "patrol", "namespace": namespace}
    if deployments:
        metadata["deployments"] = ",".join(deployments)
    thread = await client.threads.create(metadata=metadata)
    logger.info("thread=%s namespace=%s deployments=%s", thread["thread_id"], namespace, deployments or "all")
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
    """巡檢一個 namespace（或其中指定的 Deployment）一次並寫出報告。"""
    deployments = args.deployment or None
    started = datetime.now(UTC)
    request = build_request(namespace, started, deployments)
    target = f"{namespace}/{','.join(deployments)}" if deployments else namespace
    logger.info("開始巡檢 %s", target)
    try:
        if args.local:
            values = await run_local(request)
        else:
            values = await run_via_api(namespace, request, args.api_url, args.assistant_id, deployments)
    except Exception:  # noqa: BLE001
        logger.exception("巡檢失敗 %s", target)
        return None
    report = extract_report(values)
    if not report:
        logger.error("巡檢結束但沒有取得報告 %s", target)
        return None
    path = write_report(Path(args.reports_dir), namespace, started, report, deployments)
    logger.info("巡檢完成 %s 報告=%s", target, path)
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
    parser.add_argument(
        "--namespace", "-n", action="append", required=True, type=k8s_name, help="要巡檢的 namespace，可重複指定"
    )
    parser.add_argument(
        "--deployment",
        "-d",
        action="append",
        type=k8s_name,
        help="只巡檢指定的 Deployment，可重複指定；使用時只能指定一個 namespace",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="只巡檢一次（預設）")
    mode.add_argument("--interval", help="定時巡檢間隔，例如 30m、1h")
    parser.add_argument("--local", action="store_true", help="不經 LangGraph API，直接在本機程序內執行 graph")
    parser.add_argument("--api-url", default=os.getenv("LANGGRAPH_API_URL", "http://localhost:2024"))
    parser.add_argument("--assistant-id", default=os.getenv("PATROL_ASSISTANT_ID", "patrol_agent"))
    parser.add_argument("--reports-dir", default=os.getenv("PATROL_REPORTS_DIR", str(DEFAULT_REPORTS_DIR)))
    args = parser.parse_args(argv)
    if args.deployment:
        if len(args.namespace) != 1:
            parser.error("使用 --deployment 時只能指定一個 --namespace")
        args.deployment = list(dict.fromkeys(args.deployment))  # 去除重複、保留順序
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
