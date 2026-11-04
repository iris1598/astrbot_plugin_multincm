"""内置网易云音乐客户端。

上游 pyncm 已从 PyPI / GitHub 下架。这里不再保留第三方包副本，
而是把项目实际需要的部分融合成一套自有分层：

- :mod:`.crypto` / ``_aes`` / ``_security``：加解密原语
- :mod:`.session`：基于 ``httpx.AsyncClient`` 的异步会话与登录态
- :mod:`.transport`：EAPI / WEAPI 异步请求装饰层
- :mod:`.endpoints`：本插件用到的协程端点
- :mod:`.client`：异步业务 API 门面
- :mod:`.auth`：登录编排（依赖 AstrBot logger，单独导入）

整条链路原生 async/await，不使用 ``requests``、不占用线程池，
符合 AstrBot 插件开发原则。

底层 AES 实现源自 ricmoo/pyaes（MIT），其余算法与接口参考
https://github.com/mos9527/pyncm (v1.8.1)，版权归原作者所有。
"""
from __future__ import annotations

# 先加载无副作用的基础子模块，保证 client 中的 `from . import endpoints` 可用
from . import crypto, endpoints, session, transport
from .client import (
    get_album_info,
    get_playlist_info,
    get_program_info,
    get_radio_info,
    get_radio_programs,
    get_search_result,
    get_track_audio,
    get_track_info,
    get_track_lrc,
    ncm_request,
    search_album,
    search_playlist,
    search_program,
    search_radio,
    search_song,
)
from .endpoints import SearchType
from .exceptions import (
    LoginFailedException,
    LoginRequiredException,
    NCMError,
    NCMResponseError,
)

__all__ = [
    "LoginFailedException",
    "LoginRequiredException",
    "NCMError",
    "NCMResponseError",
    "SearchType",
    "crypto",
    "endpoints",
    "get_album_info",
    "get_playlist_info",
    "get_program_info",
    "get_radio_info",
    "get_radio_programs",
    "get_search_result",
    "get_track_audio",
    "get_track_info",
    "get_track_lrc",
    "ncm_request",
    "search_album",
    "search_playlist",
    "search_program",
    "search_radio",
    "search_song",
    "session",
    "transport",
]
