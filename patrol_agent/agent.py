"""Kubernetes 自動巡檢主 Agent（LangGraph graph 入口：patrol_agent）。"""

from __future__ import annotations

import logging
import os
import sys

# langgraph dev 會以本目錄為專案根目錄載入；確保扁平模組（settings、subagents…）可被 import
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from deepagents import (  # noqa: E402
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)

from prompts import build_main_prompt  # noqa: E402
from settings import Settings, settings  # noqa: E402
from subagents import build_subagents  # noqa: E402
from think_tool import sre_think_tool  # noqa: E402

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")


def build_agent(cfg: Settings = settings):
    """依設定建立巡檢 Agent。"""
    # 移除 deepagents 自動加入的 general-purpose 子 Agent：
    # 它沒有任何查詢工具，巡檢時派工給它只會得到沒有證據的結果。
    # 同時排除 execute（shell 執行）工具，巡檢 Agent 不需要也不應該執行指令。
    # 以模型 spec 為 key 註冊；create_deep_agent 收到同一個字串時會精確比對到這個 profile。
    register_harness_profile(
        cfg.model,
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            excluded_tools=frozenset({"execute"}),
        ),
    )
    return create_deep_agent(
        model=cfg.model,
        tools=[sre_think_tool],
        system_prompt=build_main_prompt(cfg),
        subagents=build_subagents(cfg),
        name="patrol_agent",
    ).with_config({"recursion_limit": cfg.recursion_limit})


agent = build_agent()
