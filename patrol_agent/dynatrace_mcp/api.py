"""Dynatrace Environment API v2 的唯讀客戶端（SaaS 舊版 / Managed）。

只使用 GET 端點：problems、entities、metrics、events。
Token 建議只開以下讀取權限：problems.read、entities.read、metrics.read、events.read。
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class DynatraceConfigError(RuntimeError):
    """Dynatrace 連線設定不完整。"""


class DynatraceAPI:
    """Dynatrace API v2 唯讀客戶端。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_token: str | None = None,
        timeout: float = 30.0,
        verify_ssl: bool | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        # SaaS：https://<env-id>.live.dynatrace.com
        # Managed：https://<your-domain>/e/<env-id>
        self.base_url = (base_url or os.getenv("DT_BASE_URL") or "").rstrip("/")
        self.api_token = api_token or os.getenv("DT_API_TOKEN") or ""
        if verify_ssl is None:
            verify_ssl = (os.getenv("DT_SSL_VERIFY", "true").strip().lower() not in ("0", "false", "no", "off"))
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.transport = transport

    def _check(self) -> None:
        if not self.base_url or not self.api_token:
            raise DynatraceConfigError("缺少 DT_BASE_URL 或 DT_API_TOKEN 設定")

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """呼叫 GET /api/v2/<path>，回傳 JSON。"""
        self._check()
        clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        with httpx.Client(
            base_url=f"{self.base_url}/api/v2",
            headers={"Authorization": f"Api-Token {self.api_token}", "Accept": "application/json"},
            timeout=self.timeout,
            verify=self.verify_ssl,
            transport=self.transport,
        ) as client:
            resp = client.get(path, params=clean)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Dynatrace API {resp.status_code}: {resp.text[:1000]}", request=resp.request, response=resp
            )
        return resp.json()

    # --- 各端點 ---

    def list_problems(
        self,
        problem_selector: str | None = 'status("open")',
        entity_selector: str | None = None,
        time_from: str = "now-2h",
        page_size: int = 50,
    ) -> dict[str, Any]:
        return self.get(
            "/problems",
            {
                "problemSelector": problem_selector,
                "entitySelector": entity_selector,
                "from": time_from,
                "pageSize": page_size,
                "fields": "+evidenceDetails,+impactAnalysis",
            },
        )

    def list_entities(
        self,
        entity_selector: str,
        time_from: str = "now-2h",
        fields: str | None = "+properties,+fromRelationships,+toRelationships",
        page_size: int = 50,
    ) -> dict[str, Any]:
        return self.get(
            "/entities",
            {"entitySelector": entity_selector, "from": time_from, "fields": fields, "pageSize": page_size},
        )

    def query_metrics(
        self,
        metric_selector: str,
        entity_selector: str | None = None,
        time_from: str = "now-2h",
        time_to: str | None = None,
        resolution: str | None = None,
    ) -> dict[str, Any]:
        return self.get(
            "/metrics/query",
            {
                "metricSelector": metric_selector,
                "entitySelector": entity_selector,
                "from": time_from,
                "to": time_to,
                "resolution": resolution,
            },
        )

    def list_metrics(self, text: str | None = None, page_size: int = 100) -> dict[str, Any]:
        return self.get("/metrics", {"text": text, "pageSize": page_size, "fields": "displayName,unit"})

    def list_events(
        self,
        entity_selector: str | None = None,
        event_selector: str | None = None,
        time_from: str = "now-6h",
        page_size: int = 50,
    ) -> dict[str, Any]:
        return self.get(
            "/events",
            {"entitySelector": entity_selector, "eventSelector": event_selector, "from": time_from, "pageSize": page_size},
        )
