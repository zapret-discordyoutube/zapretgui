"""Результат одной сетевой пробы BlockCheck — чистый Python, без Qt."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TestStatus(Enum):
    OK = "ok"
    FAIL = "fail"
    TIMEOUT = "timeout"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class TestType(Enum):
    STUN = "stun"


@dataclass
class SingleTestResult:
    target_name: str
    test_type: TestType
    status: TestStatus
    time_ms: float | None = None
    error_code: str | None = None
    detail: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)
