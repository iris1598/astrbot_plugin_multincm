"""网易云 API 请求封装 - 基于 pyncm（去除 NoneBot 依赖）"""
import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar, cast, overload

from pydantic import BaseModel
from pyncm.apis import EapiCryptoRequest, WeapiCryptoRequest, cloudsearch as search
from pyncm.apis.album import GetAlbumInfo
from pyncm.apis.cloudsearch import GetSearchResult
from pyncm.apis.playlist import GetPlaylistInfo
from pyncm.apis.track import GetTrackAudio, GetTrackDetail, GetTrackLyrics

from .models import (
    AlbumInfo,
    AlbumSearchResult,
    LyricData,
    Playlist,
    PlaylistSearchResult,
    Privilege,
    ProgramBaseInfo,
    ProgramSearchResult,
    Radio,
    RadioProgramList,
    RadioSearchResult,
    Song,
    SongSearchResult,
    TrackAudio,
)

TModel = TypeVar("TModel", bound=BaseModel)


class NCMResponseError(Exception):
    def __init__(self, name: str, data: dict[str, Any]):
        self.name = name
        self.data = data

    @property
    def code(self) -> int | None:
        return self.data.get("code")

    @property
    def message(self) -> str | None:
        return self.data.get("message")

    def __str__(self):
        return f"{self.name} failed: [{self.code}] {self.message}"


def _get_list_limit() -> int:
    """从配置获取列表限制"""
    try:
        from .main import _config
        return _config.get("list_limit", 20)
    except Exception:
        return 20


async def ncm_request(
    api: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any] | None:
    """执行 pyncm API 请求（同步转异步）"""
    ret = await asyncio.to_thread(api, *args, **kwargs)
    if ret is None:
        return None
    if ret.get("code", 200) != 200:
        raise NCMResponseError(api.__name__, ret)
    return ret


@overload
async def get_search_result(
    keyword: str,
    return_model: type[TModel],
    page: int = 1,
    search_type: int = search.SONG,
    **kwargs: Any,
) -> TModel: ...


@overload
async def get_search_result(
    keyword: str,
    return_model: None = None,
    page: int = 1,
    search_type: int = search.SONG,
    **kwargs: Any,
) -> dict[str, Any]: ...


async def get_search_result(
    keyword: str,
    return_model: type[TModel] | None = None,
    page: int = 1,
    search_type: int = search.SONG,
    **kwargs: Any,
) -> dict[str, Any] | TModel:
    limit = _get_list_limit()
    offset = (page - 1) * limit
    res = await ncm_request(
        GetSearchResult,
        keyword=keyword,
        limit=limit,
        offset=offset,
        stype=search_type,
        **kwargs,
    )
    result = res["result"]
    if return_model:
        return return_model(**result)
    return result


search_song = partial(
    get_search_result,
    search_type=search.SONG,
    return_model=SongSearchResult,
)
search_playlist = partial(
    get_search_result,
    search_type=search.PLAYLIST,
    return_model=PlaylistSearchResult,
)
search_album = partial(
    get_search_result,
    search_type=search.ALBUM,
    return_model=AlbumSearchResult,
)


async def search_radio(keyword: str, page: int = 1):
    limit = _get_list_limit()
    offset = (page - 1) * limit

    @EapiCryptoRequest  # type: ignore
    def SearchRadio():  # noqa: N802
        return (
            "/eapi/search/voicelist/get",
            {
                "keyword": keyword,
                "scene": "normal",
                "limit": limit,
                "offset": offset or 0,
            },
        )

    res = await ncm_request(SearchRadio)
    return RadioSearchResult(**res["data"])


async def search_program(keyword: str, page: int = 1):
    limit = _get_list_limit()
    offset = (page - 1) * limit

    @WeapiCryptoRequest  # type: ignore
    def SearchVoice():  # noqa: N802
        return (
            "/api/search/voice/get",
            {
                "keyword": keyword,
                "scene": "normal",
                "limit": limit,
                "offset": offset or 0,
            },
        )

    res = await ncm_request(SearchVoice)
    return ProgramSearchResult(**res["data"])


async def get_track_audio(
    song_ids: list[int],
    bit_rate: int = 999999,
    **kwargs: Any,
) -> list[TrackAudio]:
    res = await ncm_request(GetTrackAudio, song_ids, bitrate=bit_rate, **kwargs)
    return [TrackAudio(**x) for x in cast("list[dict]", res["data"])]


async def get_track_info(ids: list[int], **kwargs: Any) -> list[Song]:
    res = await ncm_request(GetTrackDetail, ids, **kwargs)
    privileges = {y.id: y for y in [Privilege(**x) for x in res["privileges"]]}
    return [
        Song(
            **x,
            privilege=(
                privileges[song_id]
                if (song_id := x["id"]) in privileges
                else Privilege(id=song_id, pl=128000)
            ),
        )
        for x in res["songs"]
    ]


async def get_track_lrc(song_id: int):
    res = await ncm_request(GetTrackLyrics, str(song_id))
    return LyricData(**res)


async def get_radio_info(radio_id: int):
    @WeapiCryptoRequest  # type: ignore
    def GetRadioInfo():  # noqa: N802
        return ("/api/djradio/v2/get", {"id": radio_id})

    res = await ncm_request(GetRadioInfo)
    return Radio(**res["data"])


async def get_radio_programs(radio_id: int, page: int = 1):
    limit = _get_list_limit()
    offset = (page - 1) * limit

    @WeapiCryptoRequest  # type: ignore
    def GetRadioPrograms():  # noqa: N802
        return (
            "/weapi/dj/program/byradio",
            {"radioId": radio_id, "limit": limit, "offset": offset},
        )

    res = await ncm_request(GetRadioPrograms)
    return RadioProgramList(**res)


async def get_program_info(program_id: int):
    @WeapiCryptoRequest  # type: ignore
    def GetProgramDetail():  # noqa: N802
        return ("/api/dj/program/detail", {"id": program_id})

    res = await ncm_request(GetProgramDetail)
    return ProgramBaseInfo(**res["program"])


async def get_playlist_info(playlist_id: int):
    res = await ncm_request(GetPlaylistInfo, playlist_id)
    return Playlist(**res["playlist"])


async def get_album_info(album_id: int):
    res = await ncm_request(GetAlbumInfo, str(album_id))
    return AlbumInfo(**res)
