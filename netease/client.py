"""插件使用的异步业务 API 门面。

端到端原生异步（``httpx.AsyncClient`` → 协程端点 → 协程门面），不占用线程池；
同时把网易云原始响应转换为 :mod:`astrbot_plugin_multincm.models` 中的 Pydantic 模型。
"""
from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar, cast, overload

from pydantic import BaseModel

from .. import models
from ..config import list_limit
from . import endpoints as ep
from .exceptions import NCMResponseError

__all__ = [
    "NCMResponseError",
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
]

TModel = TypeVar("TModel", bound=BaseModel)


async def ncm_request(api: Callable[..., Any], *args: Any, **kwargs: Any) -> dict[str, Any] | None:
    """``await`` 一次异步端点请求，并校验业务错误码。"""
    ret = await api(*args, **kwargs)
    if ret is None:
        return None
    if not isinstance(ret, dict):
        raise NCMResponseError(getattr(api, "__name__", "request"), {"code": None, "message": str(ret)[:200]})
    if ret.get("code", 200) != 200:
        raise NCMResponseError(getattr(api, "__name__", "request"), ret)
    return ret


def _page(page: int) -> tuple[int, int]:
    limit = list_limit()
    return limit, (page - 1) * limit


@overload
async def get_search_result(
    keyword: str,
    return_model: type[TModel],
    page: int = 1,
    search_type: int = ep.SearchType.SONG,
    **kwargs: Any,
) -> TModel: ...


@overload
async def get_search_result(
    keyword: str,
    return_model: None = None,
    page: int = 1,
    search_type: int = ep.SearchType.SONG,
    **kwargs: Any,
) -> dict[str, Any]: ...


async def get_search_result(
    keyword: str,
    return_model: type[TModel] | None = None,
    page: int = 1,
    search_type: int = ep.SearchType.SONG,
    **kwargs: Any,
) -> dict[str, Any] | TModel:
    limit, offset = _page(page)
    res = await ncm_request(
        ep.search,
        keyword=keyword,
        limit=limit,
        offset=offset,
        stype=search_type,
        **kwargs,
    )
    result = res["result"]
    return return_model(**result) if return_model else result


search_song = partial(
    get_search_result,
    search_type=ep.SearchType.SONG,
    return_model=models.SongSearchResult,
)
search_playlist = partial(
    get_search_result,
    search_type=ep.SearchType.PLAYLIST,
    return_model=models.PlaylistSearchResult,
)
search_album = partial(
    get_search_result,
    search_type=ep.SearchType.ALBUM,
    return_model=models.AlbumSearchResult,
)


async def search_radio(keyword: str, page: int = 1) -> models.RadioSearchResult:
    limit, offset = _page(page)
    res = await ncm_request(ep.search_radio, keyword, limit, offset)
    return models.RadioSearchResult(**res["data"])


async def search_program(keyword: str, page: int = 1) -> models.ProgramSearchResult:
    limit, offset = _page(page)
    res = await ncm_request(ep.search_program, keyword, limit, offset)
    return models.ProgramSearchResult(**res["data"])


async def get_track_audio(
    song_ids: list[int],
    bit_rate: int = 999999,
    **kwargs: Any,
) -> list[models.TrackAudio]:
    res = await ncm_request(ep.track_audio, song_ids, bitrate=bit_rate, **kwargs)
    return [models.TrackAudio(**x) for x in cast("list[dict]", res["data"])]


async def get_track_info(ids: list[int], **kwargs: Any) -> list[models.Song]:
    res = await ncm_request(ep.track_detail, ids, **kwargs)
    privileges = {p.id: p for p in (models.Privilege(**x) for x in res["privileges"])}
    return [
        models.Song(
            **x,
            privilege=(
                privileges[song_id]
                if (song_id := x["id"]) in privileges
                else models.Privilege(id=song_id, pl=128000)
            ),
        )
        for x in res["songs"]
    ]


async def get_track_lrc(song_id: int) -> models.LyricData:
    res = await ncm_request(ep.track_lyrics, str(song_id))
    return models.LyricData(**res)


async def get_radio_info(radio_id: int) -> models.Radio:
    res = await ncm_request(ep.radio_info, radio_id)
    return models.Radio(**res["data"])


async def get_radio_programs(radio_id: int, page: int = 1) -> models.RadioProgramList:
    limit, offset = _page(page)
    res = await ncm_request(ep.radio_programs, radio_id, limit, offset)
    return models.RadioProgramList(**res)


async def get_program_info(program_id: int) -> models.ProgramBaseInfo:
    res = await ncm_request(ep.program_info, program_id)
    return models.ProgramBaseInfo(**res["program"])


async def get_playlist_info(playlist_id: int) -> models.Playlist:
    res = await ncm_request(ep.playlist_info, playlist_id)
    return models.Playlist(**res["playlist"])


async def get_album_info(album_id: int) -> models.AlbumInfo:
    res = await ncm_request(ep.album_info, str(album_id))
    return models.AlbumInfo(**res)
