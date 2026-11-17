"""集中讀取環境變數設定。

所有開關與上限都在這裡解析，其他模組只引用 `settings`，避免各處各自讀 env。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_BASE_DIR = Path(__file__).resolve().parent

# 載入 .env（已存在的環境變數優先，不會被覆蓋）
_ENV_PATH = _BASE_DIR / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)

# 強制關閉 LangSmith 追蹤：巡檢資料（叢集狀態、指標、日誌）不送往外部服務。
# 即使 shell 或 .env 裡設定了相關變數，也會在這裡被覆寫或移除。
_TRACING_FLAGS = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2")
_TRACING_KEYS = ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY")
for _name in _TRACING_FLAGS:
    os.environ[_name] = "false"
for _name in _TRACING_KEYS:
    os.environ.pop(_name, None)


def env_bool(name: str, default: bool = False) -> bool:
    """把環境變數解析成布林值；未設定時回傳 default。"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    """把環境變數解析成整數；格式錯誤時回傳 default。"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def env_str(name: str, default: str = "") -> str:
    """讀取字串環境變數並去除前後空白。"""
    return (os.getenv(name) or default).strip()


@dataclass(frozen=True)
class Settings:
    """巡檢 Agent 的執行期設定。"""

    # 模型：provider:model 格式，例如 openai:gpt-5.2、anthropic:claude-sonnet-5-5
    model: str = field(default_factory=lambda: env_str("PATROL_MODEL", "openai:gpt-5.2"))

    # 選用資料來源（預設關閉）
    enable_elasticsearch: bool = field(default_factory=lambda: env_bool("ENABLE_ELASTICSEARCH", False))
    enable_dynatrace: bool = field(default_factory=lambda: env_bool("ENABLE_DYNATRACE", False))

    # MCP 端點（皆為 Streamable HTTP）
    k8s_mcp_url: str = field(default_factory=lambda: env_str("K8S_MCP_URL", "http://localhost:18080/mcp"))
    prom_mcp_url: str = field(default_factory=lambda: env_str("PROM_MCP_URL", "http://localhost:18090/mcp"))
    es_mcp_url: str = field(default_factory=lambda: env_str("ES_MCP_URL", "http://localhost:18100/mcp"))
    dt_mcp_url: str = field(default_factory=lambda: env_str("DT_MCP_URL", "http://localhost:18110/mcp"))

    # 巡檢保護機制
    batch_size: int = field(default_factory=lambda: env_int("PATROL_BATCH_SIZE", 3))
    max_rounds_per_deployment: int = field(default_factory=lambda: env_int("PATROL_MAX_ROUNDS_PER_DEPLOYMENT", 2))
    recursion_limit: int = field(default_factory=lambda: env_int("PATROL_RECURSION_LIMIT", 300))
    lookback: str = field(default_factory=lambda: env_str("PATROL_LOOKBACK", "1h"))

    # 單次工具結果最多回傳字元數，避免撐爆上下文
    max_tool_result_chars: int = field(default_factory=lambda: env_int("MCP_MAX_RESULT_CHARS", 20000))
    mcp_timeout_seconds: int = field(default_factory=lambda: env_int("MCP_TIMEOUT_SECONDS", 60))


settings = Settings()
