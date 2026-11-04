"""网易云会话与登录态管理（原生异步）。

基于 :class:`httpx.AsyncClient`，整条请求链都是 ``await``，不占用线程池。
会话覆盖使用 :mod:`contextvars`（而非线程局部变量），因此在同一个事件循环里
的多个并发任务也能各自持有正确的临时会话。
"""
from __future__ import annotations

import base64
import contextvars
import json
import time
import zlib
from typing import Any

import httpx

from .crypto import eapi_decrypt, eapi_encrypt_session_payload, hex_compose
from .exceptions import LoginFailedException

__all__ = [
    "DeviceType",
    "Session",
    "SessionManager",
    "aclose_session",
    "create_new_session",
    "dump_session_as_string",
    "get_current_session",
    "load_session_from_string",
    "set_current_session",
    "write_login_info",
]

HOST = "music.163.com"
DEVICE_ID_DEFAULT = "pyncm!"
VERSION = "1.8.1"

# 每个 asyncio Task / Context 独立维护 ``async with session`` 的临时会话栈
_SESSION_STACK: contextvars.ContextVar[tuple["Session", ...]] = contextvars.ContextVar(
    "ncm_session_stack", default=()
)


class DeviceType:
    """默认 EAPI 设备配置。"""

    OS = "iPhone OS"
    APPVER = "10.0.0"
    OSVER = "16.2"
    CHANNEL = "distribution"


class Session(httpx.AsyncClient):
    """持有登录态的异步 HTTP 会话。

    除标准 ``httpx.AsyncClient`` 配置外，还维护 ``login_info``、``eapi_config``
    与 ``csrf_token``。可用 ``async with`` 临时切换当前会话。
    """

    HOST = HOST
    UA_DEFAULT = f"Mozilla/5.0 (linux@github.com/mos9527/pyncm) Chrome/PyNCM.{VERSION}"
    UA_EAPI = (
        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Safari/537.36 Chrome/91.0.4472.164 "
        "NeteaseMusicDesktop/2.10.2.200154"
    )
    force_http = False

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("follow_redirects", True)
        kwargs.setdefault("timeout", 20.0)
        super().__init__(*args, **kwargs)
        self.headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": self.UA_DEFAULT,
            "Referer": self.HOST,
        }
        self.login_info: dict[str, Any] = {
            "success": False,
            "tick": time.time(),
            "content": None,
        }
        self.eapi_config = {
            "os": DeviceType.OS,
            "appver": DeviceType.APPVER,
            "osver": DeviceType.OSVER,
            "channel": DeviceType.CHANNEL,
            "deviceId": DEVICE_ID_DEFAULT,
        }
        self.csrf_token = ""

    async def __aenter__(self) -> "Session":
        self._ctx_token = _SESSION_STACK.set((*_SESSION_STACK.get(), self))
        return self

    async def __aexit__(self, *args) -> None:
        _SESSION_STACK.reset(self._ctx_token)
        # 全局会话长期复用，只有临时会话随上下文销毁
        if self is not session_manager.session:
            await self.aclose()

    async def request(self, method: str, url: str, *args, **kwargs) -> httpx.Response:
        """补全相对 URL，并按 ``force_http`` 决定是否降级为 HTTP。"""
        if isinstance(url, bytes):
            url = url.decode()
        if not url.startswith("http"):
            url = f"https://{self.HOST}{url}"
        if self.force_http:
            url = url.replace("https:", "http:")
        return await super().request(method, url, *args, **kwargs)

    # region 登录态属性
    @property
    def deviceId(self) -> str:  # noqa: N802 - 保持与接口字段一致
        return self.eapi_config["deviceId"]

    @deviceId.setter
    def deviceId(self, value) -> None:  # noqa: N802
        self.eapi_config["deviceId"] = str(value)

    @property
    def logged_in(self) -> bool:
        return bool(self.login_info.get("success"))

    @property
    def uid(self) -> int:
        return self.login_info["content"]["account"]["id"] if self.logged_in else 0

    @property
    def nickname(self) -> str:
        return (
            self.login_info["content"]["profile"]["nickname"] if self.logged_in else ""
        )

    # endregion
    # region 序列化
    _session_info = {
        "eapi_config": (
            lambda self: self.eapi_config,
            lambda self, value: setattr(self, "eapi_config", value),
        ),
        "login_info": (
            lambda self: self.login_info,
            lambda self, value: setattr(self, "login_info", value),
        ),
        "csrf_token": (
            lambda self: self.csrf_token,
            lambda self, value: setattr(self, "csrf_token", value),
        ),
        "cookies": (
            lambda self: [
                {"name": name, "value": value, "domain": "", "path": "/"}
                for name, value in self.cookies.items()
            ],
            lambda self, cookies: [self.cookies.set(**item) for item in cookies],
        ),
    }

    def dump(self) -> dict[str, Any]:
        """导出登录态为可序列化 dict。"""
        return {name: self._session_info[name][0](self) for name in self._session_info}

    def load(self, dumped: dict[str, Any]) -> None:
        """从 dict 恢复登录态。"""
        for key, value in dumped.items():
            if key in self._session_info:
                self._session_info[key][1](self, value)
    # endregion


class SessionManager:
    """全局会话单例（按 Context 隔离临时会话）。"""

    def __init__(self) -> None:
        self.session = Session()

    def get(self) -> Session:
        stack = _SESSION_STACK.get()
        return stack[-1] if stack else self.session

    def set(self, session: Session) -> None:
        if _SESSION_STACK.get():
            raise RuntimeError("当前会话处于 async with 代码块中，不可重新赋值")
        self.session = session

    @staticmethod
    def stringify_legacy(session: Session) -> str:
        return eapi_encrypt_session_payload(session.dump())

    @staticmethod
    def parse_legacy(dump: str) -> Session:
        text = eapi_decrypt(hex_compose(dump)).decode()
        parts = text.split("-36cd479b6b5-")
        if parts[0] != "pyncm":
            raise ValueError("旧版会话缓存格式无效")
        session = Session()
        session.load(json.loads(parts[1]))
        return session

    @staticmethod
    def stringify(session: Session) -> str:
        raw = zlib.compress(json.dumps(session.dump()).encode())
        return "PYNCM" + base64.b64encode(raw).decode()

    @staticmethod
    def parse(dump: str) -> Session:
        if not dump.startswith("PYNCM"):
            return SessionManager.parse_legacy(dump)
        session = Session()
        session.load(json.loads(zlib.decompress(base64.b64decode(dump[5:])).decode()))
        return session


session_manager = SessionManager()


def get_current_session() -> Session:
    return session_manager.get()


def set_current_session(session: Session) -> None:
    session_manager.set(session)


def create_new_session() -> Session:
    return Session()


def load_session_from_string(dump: str) -> Session:
    return SessionManager.parse(dump)


def dump_session_as_string(session: Session) -> str:
    return SessionManager.stringify(session)


async def aclose_session() -> None:
    """释放全局会话连接池，并重建一个可用会话（插件 terminate 时调用）。

    重建是为了让插件在同一进程内被停用后再次启用时，仍能正常发起请求。
    """
    await session_manager.session.aclose()
    session_manager.session = Session()


def write_login_info(content: dict[str, Any]) -> None:
    """把登录接口响应写入当前会话，并同步 csrf_token。"""
    session = session_manager.get()
    session.login_info = {"tick": time.time(), "content": content}
    if content.get("code", 200) != 200:
        session.login_info["success"] = False
        raise LoginFailedException(str(content))
    session.login_info["success"] = True
    session.csrf_token = session.cookies.get("__csrf")
