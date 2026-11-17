"""工具層測試：白名單、錯誤處理、時間參數、Dynatrace API 客戶端、報告擷取。"""

from datetime import UTC, datetime

import httpx
import pytest

import patrol
from dynatrace_mcp.api import DynatraceAPI
from mcp_client import MCPClient, truncate
from timeutil import parse_duration


async def test_whitelist_blocks_unknown_tool():
    client = MCPClient("kubernetes", "http://127.0.0.1:1/mcp", allowed_tools={"pods_list_in_namespace"})
    result = await client.call("pods_delete", {"name": "x"})
    assert result.startswith("[拒絕]")


async def test_unreachable_server_returns_error_text():
    client = MCPClient("prometheus", "http://127.0.0.1:1/mcp", allowed_tools={"execute_query"})
    result = await client.call("execute_query", {"query": "up"})
    assert result.startswith("[錯誤]")


def test_truncate():
    assert truncate("abc", 10) == "abc"
    out = truncate("x" * 50, 10)
    assert out.startswith("x" * 10) and "已截斷" in out


@pytest.mark.parametrize(("value", "seconds"), [("30s", 30), ("15m", 900), ("1h", 3600), ("1d", 86400), ("1w", 604800)])
def test_parse_duration(value, seconds):
    assert parse_duration(value).total_seconds() == seconds


def test_parse_duration_invalid():
    with pytest.raises(ValueError):
        parse_duration("abc")


async def test_prom_query_range_rejects_bad_lookback():
    from subagents.prometheus import prom_query_range

    result = await prom_query_range.ainvoke({"promql": "up", "lookback": "abc"})
    assert result.startswith("[錯誤]")


def test_dynatrace_api_sends_token_and_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"totalCount": 0, "problems": []})

    api = DynatraceAPI("https://abc.live.dynatrace.com", "dt0c01.TOKEN", transport=httpx.MockTransport(handler))
    data = api.list_problems(entity_selector='type("CLOUD_APPLICATION")')
    assert data == {"totalCount": 0, "problems": []}
    assert seen["auth"] == "Api-Token dt0c01.TOKEN"
    assert seen["path"] == "/api/v2/problems"
    assert seen["params"]["problemSelector"] == 'status("open")'
    assert seen["params"]["entitySelector"] == 'type("CLOUD_APPLICATION")'


def test_dynatrace_server_reports_missing_config(monkeypatch):
    import dynatrace_mcp.server as server

    monkeypatch.setattr(server, "_api", DynatraceAPI(base_url="", api_token=""))
    assert server.dt_list_problems().startswith("[設定錯誤]")


def test_extract_report_prefers_file():
    values = {
        "files": {"/patrol_report.md": {"content": "# 報告", "encoding": "utf-8"}},
        "messages": [{"type": "ai", "content": "最後回覆"}],
    }
    assert patrol.extract_report(values) == "# 報告"


def test_extract_report_falls_back_to_last_ai_message():
    values = {"messages": [{"type": "human", "content": "巡檢"}, {"type": "ai", "content": "摘要"}]}
    assert patrol.extract_report(values).endswith("摘要")


def test_write_report(tmp_path):
    started = datetime(2026, 11, 17, 8, 0, 0, tzinfo=UTC)
    path = patrol.write_report(tmp_path, "payments", started, "# 報告")
    assert path.parent == tmp_path / "payments"
    assert path.read_text(encoding="utf-8") == "# 報告"
