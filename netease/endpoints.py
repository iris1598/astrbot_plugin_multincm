"""本插件实际使用的网易云 API 端点。

只保留多选点歌所需的搜索、歌曲、专辑、歌单、电台与登录接口；
上游 pyncm 的下载 CLI、用户/云盘/小程序等未使用接口已全部移除
（艺术家详情仅保留头像所需的 ``artist_head_info`` 一项，见 :func:`artist_head_info`）。
每个端点构造 ``(url, payload)``，由 :mod:`.transport` 异步加密并发出请求，
因此所有装饰过的端点都是协程函数，调用时须 ``await``。
"""
from __future__ import annotations

import json
from base64 import b64encode

from .crypto import (
    cloudmusic_dll_encode_id,
    generate_chain_id,
    generate_s_device_id,
    hash_hex_digest,
)
from .exceptions import LoginFailedException
from .session import get_current_session, write_login_info
from .transport import eapi_request, weapi_request

__all__ = [
    "SearchType",
    "album_info",
    "artist_head_info",
    "login_status",
    "login_via_anonymous",
    "login_via_cellphone",
    "login_via_cookie",
    "login_via_email",
    "playlist_info",
    "program_info",
    "qrcode_check",
    "qrcode_unikey",
    "qrcode_url",
    "radio_info",
    "radio_programs",
    "search",
    "search_program",
    "search_radio",
    "send_verification_code",
    "track_audio",
    "track_detail",
    "track_lyrics",
]


class SearchType:
    """cloudsearch 接口的类型码。"""

    SONG = 1
    ALBUM = 10
    ARTIST = 100
    PLAYLIST = 1000
    USER = 1002
    MV = 1004
    LYRICS = 1006
    DJ = 1009
    VIDEO = 1014


# region 搜索
@eapi_request
def search(keyword: str, stype: int = SearchType.SONG, limit: int = 30, offset: int = 0):
    """PC 端综合搜索。"""
    return "/eapi/cloudsearch/pc", {
        "s": str(keyword),
        "type": str(stype),
        "limit": str(limit),
        "offset": str(offset),
    }


@eapi_request
def search_radio(keyword: str, limit: int, offset: int):
    """搜索电台。"""
    return "/eapi/search/voicelist/get", {
        "keyword": keyword,
        "scene": "normal",
        "limit": limit,
        "offset": offset or 0,
    }


@weapi_request
def search_program(keyword: str, limit: int, offset: int):
    """搜索电台节目（声音）。"""
    return "/api/search/voice/get", {
        "keyword": keyword,
        "scene": "normal",
        "limit": limit,
        "offset": offset or 0,
    }
# endregion

# region 歌曲 / 专辑 / 歌单
@weapi_request
def track_detail(song_ids: list):
    """批量获取歌曲详情。"""
    ids = song_ids if isinstance(song_ids, list) else [song_ids]
    return "/weapi/v3/song/detail", {"c": json.dumps([{"id": str(x)} for x in ids])}


@eapi_request
def track_audio(song_ids: list, bitrate: int = 320000, encode_type: str = "aac"):
    """获取歌曲播放直链。"""
    ids = song_ids if isinstance(song_ids, list) else [song_ids]
    return "/eapi/song/enhance/player/url", {
        "ids": ids,
        "encodeType": str(encode_type),
        "br": str(bitrate),
    }


@weapi_request
def track_lyrics(song_id: str, lv: int = -1, tv: int = -1, rv: int = -1):
    """获取歌曲歌词。"""
    return "/weapi/song/lyric", {
        "id": str(song_id),
        "lv": str(lv),
        "tv": str(tv),
        "rv": str(rv),
    }


@weapi_request
def album_info(album_id: str):
    """获取专辑信息。"""
    return f"/weapi/v1/album/{album_id}", {}


@weapi_request
def artist_head_info(artist_id: str):
    """获取艺术家详情（头像 / 简介 / 作品统计）。

    端点与载荷取自 pyncm 的 ``GetArtistDetails``（apis/artist.py）。
    """
    return "/weapi/artist/head/info/get", {"id": str(artist_id)}


@weapi_request
def playlist_info(playlist_id, offset: int = 0, total: bool = True, limit: int = 1000):
    """获取歌单详情。"""
    return "/weapi/v6/playlist/detail", {
        "id": str(playlist_id),
        "offset": str(offset),
        "total": str(total).lower(),
        "limit": str(limit),
        "n": str(limit),
    }
# endregion

# region 电台
@weapi_request
def radio_info(radio_id: int):
    return "/api/djradio/v2/get", {"id": radio_id}


@weapi_request
def radio_programs(radio_id: int, limit: int, offset: int):
    return "/weapi/dj/program/byradio", {
        "radioId": radio_id,
        "limit": limit,
        "offset": offset,
    }


@weapi_request
def program_info(program_id: int):
    return "/api/dj/program/detail", {"id": program_id}
# endregion

# region 登录
@weapi_request
def login_status():
    """获取当前登录态。"""
    return "/weapi/w/nuser/account/get", {}


@weapi_request
def qrcode_unikey(dtype: int = 1):
    return "/weapi/login/qrcode/unikey", {"type": str(dtype), "noCheckToken": True}


@weapi_request
def qrcode_check(unikey: str, type: int = 1):  # noqa: A002 - 与接口字段一致
    return "/weapi/login/qrcode/client/login", {
        "type": type,
        "noCheckToken": True,
        "key": str(unikey),
    }


@weapi_request
def send_verification_code(cell: str, ctcode: int = 86):
    return "/weapi/sms/captcha/sent", {"cellphone": str(cell), "ctcode": ctcode}


def qrcode_url(unikey: str) -> str:
    """拼接二维码登录链接（含风控 chainId）。"""
    s_device_id = get_current_session().cookies.get("sDeviceId") or generate_s_device_id()
    return (
        f"http://music.163.com/login?codekey={unikey}"
        f"&chainId={generate_chain_id(s_device_id)}"
    )


async def login_via_cookie(music_u: str = "", **kwargs):
    """使用 MUSIC_U Cookie 登录。"""
    session = get_current_session()
    session.cookies.update({"MUSIC_U": music_u, **kwargs})
    resp = await login_status()
    write_login_info(resp)
    return {"code": 200, "result": session.login_info}


async def login_via_cellphone(
    phone: str = "",
    password: str = "",
    password_hash: str = "",
    captcha: str = "",
    ctcode: int = 86,
    remember_login: bool = True,
    session=None,
) -> dict:
    """手机号 + 密码/MD5/验证码登录。"""
    session = session or get_current_session()
    if password:
        password_hash = hash_hex_digest(password)
    if not (password_hash or captcha):
        raise LoginFailedException("未提供密码或验证码")
    auth_token = {"password": str(password_hash)} if not captcha else {"captcha": str(captcha)}
    login_status_resp = await eapi_request(
        lambda: (
            "/eapi/w/login/cellphone",
            {
                "type": "1",
                "phone": str(phone),
                "remember": str(remember_login).lower(),
                "countrycode": str(ctcode),
                "checkToken": "",
                **auth_token,
            },
        )
    )(session=session)
    write_login_info(login_status_resp)
    return {"code": 200, "result": session.login_info}


async def login_via_email(
    email: str = "",
    password: str = "",
    password_hash: str = "",
    remember_login: bool = True,
    session=None,
) -> dict:
    """邮箱 + 密码/MD5 登录。"""
    session = session or get_current_session()
    if password:
        password_hash = hash_hex_digest(password)
    if not password_hash:
        raise LoginFailedException("未提供密码")
    login_status_resp = await eapi_request(
        lambda: (
            "/eapi/login",
            {
                "type": "1",
                "username": str(email),
                "remember": str(remember_login).lower(),
                "password": str(password_hash),
            },
        )
    )(session=session)
    write_login_info(login_status_resp)
    return {"code": 200, "result": session.login_info}


async def login_via_anonymous(device_id: str | None = None, session=None) -> dict:
    """游客登录。"""
    session = session or get_current_session()
    device_id = device_id or session.deviceId
    username = b64encode(
        f"{device_id} {cloudmusic_dll_encode_id(device_id)}".encode()
    ).decode()
    resp = await weapi_request(
        lambda: ("/api/register/anonimous", {"username": username})
    )(session=session)
    if resp.get("code") != 200:
        raise LoginFailedException(f"匿名登录失败: {resp}")
    write_login_info({
        **resp,
        "profile": {"nickname": "", **resp},
        "account": {"id": resp["userId"], **resp},
    })
    return session.login_info
# endregion
