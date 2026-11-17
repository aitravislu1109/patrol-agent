"""確認 LangSmith 追蹤被強制關閉。"""

import importlib
import os


def test_tracing_forced_off(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_should_be_removed")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "lsv2_should_be_removed")

    import settings

    importlib.reload(settings)

    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert "LANGSMITH_API_KEY" not in os.environ
    assert "LANGCHAIN_API_KEY" not in os.environ


def test_langsmith_reports_tracing_disabled(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    import settings

    importlib.reload(settings)

    from langsmith import utils as ls_utils

    ls_utils.get_env_var.cache_clear()
    assert ls_utils.tracing_is_enabled() is False
