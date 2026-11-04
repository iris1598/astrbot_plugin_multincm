"""网易云客户端统一异常定义。"""
from __future__ import annotations

from typing import Any


class NCMError(Exception):
    """本客户端所有异常的基类。"""


class NCMResponseError(NCMError):
    """API 返回了非 200 业务错误码。"""

    def __init__(self, name: str, data: dict[str, Any]):
        self.name = name
        self.data = data
        super().__init__(str(self))

    @property
    def code(self) -> int | None:
        return self.data.get("code")

    @property
    def message(self) -> str | None:
        return self.data.get("message")

    def __str__(self) -> str:
        return f"{self.name} failed: [{self.code}] {self.message}"


class LoginRequiredException(NCMError):
    """该接口需要登录态。"""


class LoginFailedException(NCMError):
    """登录失败或登录态校验未通过。"""
