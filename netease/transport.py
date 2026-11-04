"""EAPI / WEAPI 异步请求装饰层。

端点函数只需返回 ``(url, payload[, method])``，装饰器负责加密、``await`` 异步请求、
解密与 JSON 解析。装饰后的端点是协程函数，业务侧必须 ``await``。
"""
from __future__ import annotations

import json
import logging
from functools import wraps
from random import randrange
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx

from .crypto import decrypt_abroad_message, eapi_decrypt, eapi_encrypt, weapi_encrypt
from .exceptions import LoginRequiredException
from .session import Session, get_current_session

__all__ = ["LOGIN_REQUIRED", "eapi_request", "weapi_request"]

logger = logging.getLogger("astrbot_plugin_multincm.netease")

LOGIN_REQUIRED = LoginRequiredException("需要登录")

EndpointSpec = Callable[..., tuple]
AsyncEndpoint = Callable[..., Awaitable[Any]]
AsyncTransport = Callable[[Session, str, dict, str], Awaitable[Any]]


def _parse_response(result: Any) -> Any:
    """把传输层结果（Response 或已解密文本）统一解析为 dict。"""
    text = result if isinstance(result, str) else result.text
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    try:
        data = json.loads(text.strip("\x10"))
    except json.JSONDecodeError as exc:
        logger.error("响应不是合法 JSON: %s", exc)
        return text
    if isinstance(data, dict) and data.get("abroad"):
        logger.warning("检测到 abroad 响应，响应结构可能与常规接口不同")
        try:
            return {
                "result": json.loads(decrypt_abroad_message(data["result"])),
                "abroad": True,
            }
        except Exception as exc:  # 海外解密失败时保留原始响应
            logger.warning("abroad 响应解密失败: %s", exc)
    return data


def _base_wrapper(request_func: AsyncTransport) -> Callable[[EndpointSpec], AsyncEndpoint]:
    """构造异步端点装饰器。"""

    def api_wrapper(api_func: EndpointSpec) -> AsyncEndpoint:
        @wraps(api_func)
        async def wrapper(*args, **kwargs):
            session = kwargs.pop("session", None) or get_current_session()
            ret = api_func(*args, **kwargs)
            url, payload = ret[:2]
            method = ret[-1] if ret[-1] in ("POST", "GET") else "POST"
            logger.debug(
                "%s %s %s deviceId=%s session=0x%x",
                method,
                url,
                api_func.__name__,
                session.deviceId,
                id(session),
            )
            return _parse_response(await request_func(session, url, payload, method))

        return wrapper

    return api_wrapper


async def _weapi_request(
    session: Session, url: str, plain: dict, method: str
) -> httpx.Response:
    payload = json.dumps({**plain, "csrf_token": session.csrf_token})
    return await session.request(
        method,
        url.replace("/api/", "/weapi/"),
        params={"csrf_token": session.csrf_token},
        data=weapi_encrypt(payload),
        headers={"User-Agent": session.UA_DEFAULT, "Referer": "https://music.163.com"},
        cookies={**session.eapi_config},
    )


async def _eapi_request(session: Session, url: str, plain: dict, method: str) -> str:
    payload = {
        **plain,
        "header": json.dumps(
            {**session.eapi_config, "requestId": str(randrange(20000000, 30000000))}
        ),
    }
    digest = eapi_encrypt(
        urlparse(url).path.replace("/eapi/", "/api/"), json.dumps(payload)
    )
    response = await session.request(
        method,
        url,
        headers={"User-Agent": session.UA_EAPI, "Referer": ""},
        cookies={**session.eapi_config},
        data=digest,
    )
    raw = response.content
    # 部分 EAPI 接口（如 cloudsearch）直接返回明文 JSON，其余才返回 AES 密文。
    # 必须先探测明文：否则长度恰为 16 倍数的明文会被「成功」解密成乱码。
    try:
        text = raw.decode("utf-8")
        json.loads(text.strip("\x10"))
        return text
    except (UnicodeDecodeError, json.JSONDecodeError):
        return eapi_decrypt(raw).decode("utf-8", "replace")


def weapi_request(api_func: EndpointSpec) -> AsyncEndpoint:
    """标记一个 WEAPI 端点（网页/小程序接口）。"""
    return _base_wrapper(_weapi_request)(api_func)


def eapi_request(api_func: EndpointSpec) -> AsyncEndpoint:
    """标记一个 EAPI 端点（PC 客户端接口，响应需解密）。"""
    return _base_wrapper(_eapi_request)(api_func)
