"""插件运行时配置中心。

所有子模块统一从这里读取配置，避免反向 `from .main import _config`
造成的循环导入；Main 构造时通过 :func:`set_config` 注入一次即可。
"""
from __future__ import annotations

from typing import Any

_config: dict[str, Any] = {}


def set_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """替换当前配置（原地更新，已持有引用的调用方也能看到最新值）。"""
    _config.clear()
    if config:
        _config.update(config)
    return _config


def get_config() -> dict[str, Any]:
    """返回当前配置字典（只读语义，请勿直接修改）。"""
    return _config


def get(key: str, default: Any = None) -> Any:
    """读取单个配置项。"""
    return _config.get(key, default)


def list_limit() -> int:
    """每页搜索结果数量，供分页计算统一使用。"""
    try:
        return max(1, int(_config.get("list_limit", 20)))
    except (TypeError, ValueError):
        return 20
