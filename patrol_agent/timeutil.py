"""時間工具。"""

from __future__ import annotations

import re
from datetime import timedelta

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_duration(value: str) -> timedelta:
    """解析 30s / 15m / 1h / 1d / 1w 這類時間長度。"""
    match = _DURATION_RE.match(value or "")
    if not match:
        raise ValueError(f"無法解析時間長度：{value!r}（格式例如 30s、15m、1h、1d、1w）")
    amount, unit = int(match.group(1)), match.group(2).lower()
    return timedelta(**{_UNITS[unit]: amount})
