"""数据源 - 网易云音乐搜索与数据模型"""
import asyncio
from abc import ABC, abstractmethod
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, ClassVar, Generic, TypeVar, Union
from typing_extensions import Self, override
from yarl import URL

from .api import (
    get_album_info,
    get_playlist_info,
    get_program_info,
    get_radio_info,
    get_radio_programs,
    get_track_audio,
    get_track_info,
    get_track_lrc,
    search_album,
    search_playlist,
    search_program,
    search_radio,
    search_song,
)
from . import models as md
from .lrc_parser import NCMLrcGroupLine, normalize_lrc
from .utils import (
    FILESYSTEM_CHAR_REPLACEMENTS,
    build_item_link,
    calc_max_page,
    calc_min_index,
    calc_min_max_index,
    cut_string,
    format_alias,
    format_artists,
    format_time,
    get_thumb_url,
    merge_alias,
    safe_filename,
)

# ==================== 类型别名 ====================

SongListInnerResp = md.Song | md.ProgramBaseInfo | md.BasePlaylist | md.RadioBaseInfo | md.Album

_TRawInfo = TypeVar("_TRawInfo")
_TRawResp = TypeVar("_TRawResp")
_TRawRespInner = TypeVar("_TRawRespInner", bound=SongListInnerResp)
_TSong = TypeVar("_TSong", bound="BaseSong")
_TSongList = TypeVar("_TSongList", bound="BaseSongList")
_TPlaylist = TypeVar("_TPlaylist", bound="BasePlaylist")
_TSearcher = TypeVar("_TSearcher", bound="BaseSearcher")
_TSongOrList = TypeVar("_TSongOrList", bound=Union["BaseSong", "BaseSongList"])

# ==================== 注册表 ====================

registered_resolvable: dict[str, type["BaseResolvable"]] = {}
registered_song: set[type["BaseSong"]] = set()
registered_playlist: set[type["BasePlaylist"]] = set()
registered_searcher: dict[type["BaseSearcher"], tuple[str, ...]] = {}


# ==================== 基类 ====================

class ResolvableFromID(ABC):
    link_types: ClassVar[tuple[str, ...]]

    @property
    @abstractmethod
    def id(self) -> int: ...

    @classmethod
    @abstractmethod
    async def from_id(cls, arg_id: int) -> Self: ...

    async def get_url(self) -> str:
        if not self.link_types:
            raise ValueError("No link types found")
        return build_item_link(self.link_types[0], self.id)


def link_resolvable(cls: type):
    if n := next((x for x in cls.link_types if x in registered_resolvable), None):
        raise ValueError(f"Duplicate link type: {n}")
    registered_resolvable.update(dict.fromkeys(cls.link_types, cls))
    return cls


def song(cls: type[_TSong]):
    registered_song.add(cls)
    return link_resolvable(cls)


def playlist(cls: type[_TPlaylist]):
    registered_playlist.add(cls)
    return link_resolvable(cls)


def searcher(cls: type[_TSearcher]):
    registered_searcher[cls] = cls.commands
    return cls


async def resolve_from_link_params(link_type: str, link_id: int) -> "GeneralSongOrPlaylist":
    item_class = registered_resolvable.get(link_type)
    if not item_class:
        raise ValueError(f"Non-resolvable link type: {link_type}")
    return await item_class.from_id(link_id)


# ==================== SongInfo ====================

@dataclass
class SongInfo(Generic[_TSong]):
    father: _TSong
    name: str
    alias: list[str] | None
    artists: list[str]
    duration: int
    url: str
    cover_url: str
    playable_url: str

    @property
    def id(self) -> int:
        return self.father.id

    @property
    def display_artists(self) -> str:
        return "、".join(self.artists)

    @property
    def display_name(self) -> str:
        return format_alias(self.name, self.alias)

    @property
    def display_duration(self) -> str:
        return format_time(self.duration)

    @property
    def file_suffix(self) -> str | None:
        return URL(self.playable_url).suffix.removeprefix(".") or None

    @property
    def display_filename(self) -> str:
        x = f"{self.display_name} - {self.display_artists}.{self.file_suffix or 'mp3'}"
        return safe_filename(x)

    @property
    def download_filename(self) -> str:
        return f"{type(self.father).__name__}_{self.id}.{self.file_suffix or 'mp3'}"

    async def get_description(self) -> str:
        return await self.father.format_description(self)


# ==================== PlaylistInfo ====================

@dataclass
class PlaylistInfo(Generic[_TPlaylist]):
    father: _TPlaylist
    name: str
    creators: list[str]
    url: str
    cover_url: str

    @property
    def id(self) -> int:
        return self.father.id

    @property
    def display_creators(self) -> str:
        return "、".join(self.creators)

    async def get_description(self) -> str:
        return await self.father.format_description(self)


# ==================== BaseSong ====================

class BaseSong(ResolvableFromID, ABC, Generic[_TRawResp]):
    calling: ClassVar[str]

    def __init__(self, info: _TRawResp) -> None:
        self.info: _TRawResp = info

    def __str__(self) -> str:
        return f"{type(self).__name__}(id={self.id})"

    def __eq__(self, value: object, /) -> bool:
        return isinstance(value, type(self)) and value.id == self.id

    @property
    @abstractmethod
    @override
    def id(self) -> int: ...

    @classmethod
    @abstractmethod
    @override
    async def from_id(cls, arg_id: int) -> Self: ...

    @abstractmethod
    async def get_name(self) -> str: ...

    @abstractmethod
    async def get_alias(self) -> list[str] | None: ...

    @abstractmethod
    async def get_artists(self) -> list[str]: ...

    @abstractmethod
    async def get_duration(self) -> int: ...

    @abstractmethod
    async def get_cover_url(self) -> str: ...

    @abstractmethod
    async def get_playable_url(self) -> str: ...

    @abstractmethod
    async def get_lyrics(self) -> list[NCMLrcGroupLine] | None: ...

    async def get_info(self) -> SongInfo:
        (
            (name, alias, artists, duration, url, cover_url),
            (playable_url,),
        ) = await asyncio.gather(
            asyncio.gather(
                self.get_name(),
                self.get_alias(),
                self.get_artists(),
                self.get_duration(),
                self.get_url(),
                self.get_cover_url(),
            ),
            asyncio.gather(
                self.get_playable_url(),
            ),
        )
        return SongInfo(
            father=self, name=name, alias=alias, artists=artists,
            duration=duration, url=url, cover_url=cover_url,
            playable_url=playable_url,
        )

    @classmethod
    async def format_description(cls, info: SongInfo) -> str:
        alias = format_alias("", info.alias) if info.alias else ""
        return f"{info.name}{alias}\nBy：{info.display_artists}\n时长 {info.display_duration}"


# ==================== BaseSongList / BaseSongListPage ====================

@dataclass
class ListPageCard:
    cover: str
    title: str
    alias: str = ""
    extras: list[str] = field(default_factory=list)
    small_extras: list[str] = field(default_factory=list)


@dataclass
class BaseSongListPage(Generic[_TRawRespInner, _TSongList]):
    content: Iterable[_TRawRespInner]
    father: _TSongList

    @override
    def __str__(self) -> str:
        return f"{type(self).__name__}(father={self.father})"

    @classmethod
    @abstractmethod
    async def transform_resp_to_list_card(cls, resp: _TRawRespInner) -> ListPageCard: ...

    async def transform_to_list_cards(self) -> list[ListPageCard]:
        return await asyncio.gather(
            *[self.transform_resp_to_list_card(resp) for resp in self.content],
        )


class BaseSongList(ABC, Generic[_TRawResp, _TRawRespInner, _TSongOrList]):
    child_calling: ClassVar[str]

    def __init__(self) -> None:
        self.current_page: int = 1
        self._total_count: int | None = None
        self._cache: dict[int, _TRawRespInner] = {}

    def __str__(self) -> str:
        return f"{type(self).__name__}(current_page={self.current_page}, total_count={self._total_count})"

    @abstractmethod
    def __eq__(self, value: object, /) -> bool: ...

    @property
    def total_count(self) -> int:
        if self._total_count is None:
            raise ValueError("Total count not set")
        return self._total_count

    @property
    def max_page(self) -> int:
        return calc_max_page(self.total_count)

    @property
    def is_first_page(self) -> bool:
        return self.current_page == 1

    @property
    def is_last_page(self) -> bool:
        return self.current_page == self.max_page

    @abstractmethod
    async def _extract_resp_content(self, resp: _TRawResp) -> list[_TRawRespInner] | None: ...

    @abstractmethod
    async def _extract_total_count(self, resp: _TRawResp) -> int: ...

    @abstractmethod
    async def _do_get_page(self, page: int) -> _TRawResp: ...

    @abstractmethod
    async def _build_selection(self, resp: _TRawRespInner) -> _TSongOrList: ...

    @abstractmethod
    async def _build_list_page(self, resp: Iterable[_TRawRespInner]) -> BaseSongListPage[_TRawRespInner, Self]: ...

    def _update_cache(self, page: int, data: list[_TRawRespInner]):
        min_index = calc_min_index(page)
        self._cache.update({min_index + i: item for i, item in enumerate(data)})

    def page_valid(self, page: int) -> bool:
        return 1 <= page <= self.max_page

    def index_valid(self, index: int) -> bool:
        return 0 <= index < self.total_count

    async def get_page(self, page: int | None = None):
        from .utils import calc_min_index as _calc_min
        try:
            from .main import _config
            limit = _config.get("list_limit", 20)
        except Exception:
            limit = 20

        if page is None:
            page = self.current_page
        if not ((not self._total_count) or self.page_valid(page)):
            raise ValueError("Page out of range")

        min_index = _calc_min(page)
        max_index = min_index + limit
        index_range = range(min_index, max_index + 1)
        if all(idx in self._cache for idx in index_range):
            cached_items = [self._cache[idx] for idx in index_range if idx in self._cache]
            return await self._build_list_page(cached_items)

        resp = await self._do_get_page(page)
        content = await self._extract_resp_content(resp)
        self._total_count = await self._extract_total_count(resp)
        self.current_page = page
        if content is None:
            return None

        # 单选模式：当 list_limit=1 或 API 恰好只返回1条时，直接返回选中项
        # 注意：这里不做 [:limit] 截断，保留原始内容数量以正确触发单选逻辑
        if len(content) == 1:
            return await self._build_selection(content[0])

        self._cache.update({min_index + i: item for i, item in enumerate(content)})
        return await self._build_list_page(content)

    async def select(self, index: int) -> _TSongOrList:
        page_num = calc_page_number(index)
        if index in self._cache:
            content = self._cache[index]
        elif not (1 <= page_num <= self.max_page):
            raise ValueError("Index out of range")
        else:
            resp = await self._extract_resp_content(await self._do_get_page(page_num))
            if resp is None:
                raise ValueError("Empty response")
            self._update_cache(page_num, resp)
            min_index = calc_min_index(page_num)
            content = resp[index - min_index]
        return await self._build_selection(content)


# ==================== BasePlaylist ====================

class BasePlaylist(
    ResolvableFromID,
    BaseSongList[_TRawInfo, _TRawRespInner, _TSongOrList],
    Generic[_TRawInfo, _TRawResp, _TRawRespInner, _TSongOrList],
):
    calling: ClassVar[str]

    @override
    def __init__(self, info: _TRawInfo) -> None:
        super().__init__()
        self.info: _TRawInfo = info

    @override
    def __eq__(self, value: object, /) -> bool:
        return isinstance(value, type(self)) and value.id == self.id

    @property
    @abstractmethod
    @override
    def id(self) -> int: ...

    @classmethod
    @abstractmethod
    @override
    async def from_id(cls, arg_id: int) -> Self: ...

    @abstractmethod
    async def get_name(self) -> str: ...

    @abstractmethod
    async def get_creators(self) -> list[str]: ...

    @abstractmethod
    async def get_cover_url(self) -> str: ...

    async def get_info(self) -> PlaylistInfo:
        name, creators, url, cover_url = await asyncio.gather(
            self.get_name(), self.get_creators(), self.get_url(), self.get_cover_url(),
        )
        return PlaylistInfo(father=self, name=name, creators=creators, url=url, cover_url=cover_url)

    @classmethod
    async def format_description(cls, info: PlaylistInfo) -> str:
        return f"{info.father.calling}：{info.name}\nBy: {info.display_creators}"


# ==================== BaseSearcher ====================

class BaseSearcher(BaseSongList[_TRawResp, _TRawRespInner, _TSongOrList]):
    commands: ClassVar[tuple[str, ...]]

    @override
    def __init__(self, keyword: str) -> None:
        super().__init__()
        self.keyword: str = keyword

    @override
    def __eq__(self, value: object, /) -> bool:
        return isinstance(value, type(self)) and value.keyword == self.keyword

    @staticmethod
    @abstractmethod
    async def search_from_id(arg_id: int) -> _TSongOrList | None: ...

    @override
    async def get_page(self, page: int | None = None):
        if self.keyword.isdigit():
            with suppress(Exception):
                if result := await self.search_from_id(int(self.keyword)):
                    return result
        return await super().get_page(page)


# ==================== 类型别名 ====================

BaseResolvable = BaseSong | BasePlaylist
GeneralSong = BaseSong[Any]
GeneralSongOrList = GeneralSong | BaseSongList[Any, SongListInnerResp, Any]
GeneralSongList = BaseSongList[Any, SongListInnerResp, GeneralSongOrList]
GeneralPlaylist = BasePlaylist[Any, Any, SongListInnerResp, GeneralSong]
GeneralSearcher = BaseSearcher[Any, SongListInnerResp, GeneralSongOrList]
GeneralSongListPage = BaseSongListPage[SongListInnerResp, GeneralSongList]
GeneralSongOrPlaylist = GeneralSong | GeneralPlaylist
GeneralGetPageReturn = BaseSongListPage[SongListInnerResp, GeneralSongList] | GeneralSongOrList | None
GeneralSongInfo = SongInfo[GeneralSong]
GeneralPlaylistInfo = PlaylistInfo[GeneralPlaylist]


# ==================== 具体实现 ====================

# --- 歌曲列表页 ---
class SongListPage(BaseSongListPage[md.Song, _TSongList], Generic[_TSongList]):
    @override
    @classmethod
    async def transform_resp_to_list_card(cls, resp: md.Song) -> ListPageCard:
        return ListPageCard(
            cover=get_thumb_url(resp.al.pic_url),
            title=resp.name,
            alias="；".join(merge_alias(resp)),
            extras=[format_artists(resp.ar)],
            small_extras=[f"{format_time(resp.dt)} | 热度 {resp.pop}"],
        )


# --- 歌曲 ---
@song
class Song(BaseSong[md.Song]):
    calling = "歌曲"
    link_types = ("song", "url")

    @property
    @override
    def id(self) -> int:
        return self.info.id

    @classmethod
    @override
    async def from_id(cls, arg_id: int) -> Self:
        info = (await get_track_info([arg_id]))[0]
        if not info:
            raise ValueError("Song not found")
        return cls(info)

    @override
    async def get_name(self) -> str: return self.info.name
    @override
    async def get_alias(self) -> list[str]: return merge_alias(self.info)
    @override
    async def get_artists(self) -> list[str]: return [x.name for x in self.info.ar]
    @override
    async def get_duration(self) -> int: return self.info.dt
    @override
    async def get_cover_url(self) -> str: return self.info.al.pic_url

    @override
    async def get_playable_url(self) -> str:
        info = (await get_track_audio([self.info.id]))[0]
        return info.url

    @override
    async def get_lyrics(self) -> list[NCMLrcGroupLine] | None:
        return normalize_lrc(await get_track_lrc(self.info.id))


# --- 歌曲搜索器 ---
@searcher
class SongSearcher(BaseSearcher[md.SongSearchResult, md.Song, Song]):
    child_calling = Song.calling
    commands = ("点歌", "网易云", "wyy", "网易点歌", "wydg", "wysong")

    @staticmethod
    @override
    async def search_from_id(arg_id: int) -> Song | None:
        try:
            return await Song.from_id(arg_id)
        except ValueError:
            return None

    @override
    async def _extract_resp_content(self, resp: md.SongSearchResult) -> list[md.Song] | None:
        return resp.songs

    @override
    async def _extract_total_count(self, resp: md.SongSearchResult) -> int:
        return resp.song_count

    @override
    async def _do_get_page(self, page: int) -> md.SongSearchResult:
        return await search_song(self.keyword, page=page)

    @override
    async def _build_selection(self, resp: md.Song) -> Song:
        return Song(info=resp)

    @override
    async def _build_list_page(self, resp: Iterable[md.Song]) -> SongListPage[Self]:
        return SongListPage(resp, self)


# --- 专辑列表页 ---
class AlbumListPage(BaseSongListPage[md.Album, _TSongList], Generic[_TSongList]):
    @override
    @classmethod
    async def transform_resp_to_list_card(cls, resp: md.Album) -> ListPageCard:
        return ListPageCard(
            cover=get_thumb_url(resp.pic_url),
            title=resp.name,
            extras=[format_artists(resp.artists)],
            small_extras=[f"歌曲数 {resp.size}"],
        )


# --- 专辑 ---
@playlist
class Album(BasePlaylist[md.AlbumInfo, list[md.Song], md.Song, Song]):
    calling = "专辑"
    child_calling = Song.calling
    link_types = ("album",)

    @property
    @override
    def id(self) -> int: return self.info.album.id

    @classmethod
    @override
    async def from_id(cls, arg_id: int) -> Self:
        resp = await get_album_info(arg_id)
        return cls(resp)

    @override
    async def _extract_resp_content(self, resp: list[md.Song]) -> list[md.Song]: return resp
    @override
    async def _extract_total_count(self, resp: list[md.Song]) -> int: return self.info.album.size
    @override
    async def _do_get_page(self, page: int) -> list[md.Song]:
        min_idx, max_idx = calc_min_max_index(page)
        return self.info.songs[min_idx:max_idx]
    @override
    async def _build_selection(self, resp: md.Song) -> Song: return Song(info=resp)
    @override
    async def _build_list_page(self, resp: Iterable[md.Song]) -> SongListPage[Self]: return SongListPage(resp, self)
    @override
    async def get_name(self) -> str: return self.info.album.name
    @override
    async def get_creators(self) -> list[str]: return [x.name for x in self.info.album.artists]
    @override
    async def get_cover_url(self) -> str: return self.info.album.pic_url

    @override
    @classmethod
    async def format_description(cls, info: PlaylistInfo) -> str:
        base_desc = await super().format_description(info)
        self_obj = info.father
        return f"{base_desc}\n歌曲数 {self_obj.info.album.size}"


# --- 专辑搜索器 ---
@searcher
class AlbumSearcher(BaseSearcher[md.AlbumSearchResult, md.Album, Album]):
    child_calling = Album.calling
    commands = ("网易专辑", "wyzj", "wyal")

    @staticmethod
    @override
    async def search_from_id(arg_id: int) -> Album | None:
        with suppress(Exception):
            return await Album.from_id(arg_id)
        return None

    @override
    async def _extract_resp_content(self, resp: md.AlbumSearchResult) -> list[md.Album] | None: return resp.albums
    @override
    async def _extract_total_count(self, resp: md.AlbumSearchResult) -> int: return resp.album_count
    @override
    async def _do_get_page(self, page: int) -> md.AlbumSearchResult: return await search_album(self.keyword, page=page)
    @override
    async def _build_selection(self, resp: md.Album) -> Album: return await Album.from_id(resp.id)
    @override
    async def _build_list_page(self, resp: Iterable[md.Album]) -> AlbumListPage[Self]: return AlbumListPage(resp, self)


# --- 歌单列表页 ---
class PlaylistListPage(BaseSongListPage[md.BasePlaylist, _TSongList], Generic[_TSongList]):
    @override
    @classmethod
    async def transform_resp_to_list_card(cls, resp: md.BasePlaylist) -> ListPageCard:
        return ListPageCard(
            cover=get_thumb_url(resp.cover_img_url),
            title=resp.name,
            extras=[resp.creator.nickname],
            small_extras=[f"歌曲数 {resp.track_count} | 播放 {resp.play_count} | 收藏 {resp.book_count}"],
        )


# --- 歌单 ---
@playlist
class Playlist(BasePlaylist[md.Playlist, list[md.Song], md.Song, Song]):
    calling = "歌单"
    child_calling = Song.calling
    link_types = ("playlist",)

    @property
    @override
    def id(self) -> int: return self.info.id

    @classmethod
    @override
    async def from_id(cls, arg_id: int) -> Self:
        resp = await get_playlist_info(arg_id)
        return cls(resp)

    @override
    async def _extract_resp_content(self, resp: list[md.Song]) -> list[md.Song]: return resp
    @override
    async def _extract_total_count(self, resp: list[md.Song]) -> int: return self.info.track_count
    @override
    async def _do_get_page(self, page: int) -> list[md.Song]:
        min_idx, max_idx = calc_min_max_index(page)
        track_ids = [x.id for x in self.info.track_ids[min_idx:max_idx]]
        return await get_track_info(track_ids)
    @override
    async def _build_selection(self, resp: md.Song) -> Song: return Song(info=resp)
    @override
    async def _build_list_page(self, resp: Iterable[md.Song]) -> SongListPage[Self]: return SongListPage(resp, self)
    @override
    async def get_name(self) -> str: return self.info.name
    @override
    async def get_creators(self) -> list[str]: return [self.info.creator.nickname]
    @override
    async def get_cover_url(self) -> str: return self.info.cover_img_url

    @override
    @classmethod
    async def format_description(cls, info: PlaylistInfo) -> str:
        base_desc = await super().format_description(info)
        self_obj = info.father
        lst_desc = f"\n{cut_string(d)}" if (d := self_obj.info.description) else ""
        return (
            f"{base_desc}\n"
            f"播放 {self_obj.info.play_count} | "
            f"收藏 {self_obj.info.book_count} | "
            f"评论 {self_obj.info.comment_count} | "
            f"分享 {self_obj.info.share_count}"
            f"{lst_desc}"
        )


# --- 歌单搜索器 ---
@searcher
class PlaylistSearcher(BaseSearcher[md.PlaylistSearchResult, md.BasePlaylist, Playlist]):
    child_calling = Playlist.calling
    commands = ("网易歌单", "wygd", "wypli")

    @staticmethod
    @override
    async def search_from_id(arg_id: int) -> Playlist | None:
        with suppress(Exception):
            return await Playlist.from_id(arg_id)
        return None

    @override
    async def _extract_resp_content(self, resp: md.PlaylistSearchResult) -> list[md.BasePlaylist] | None: return resp.playlists
    @override
    async def _extract_total_count(self, resp: md.PlaylistSearchResult) -> int: return resp.playlist_count
    @override
    async def _do_get_page(self, page: int) -> md.PlaylistSearchResult: return await search_playlist(self.keyword, page=page)
    @override
    async def _build_selection(self, resp: md.BasePlaylist) -> Playlist: return await Playlist.from_id(resp.id)
    @override
    async def _build_list_page(self, resp: Iterable[md.BasePlaylist]) -> PlaylistListPage[Self]: return PlaylistListPage(resp, self)


# --- 电台节目列表页 ---
class ProgramListPage(BaseSongListPage[md.ProgramBaseInfo, _TSongList], Generic[_TSongList]):
    @override
    @classmethod
    async def transform_resp_to_list_card(cls, resp: md.ProgramBaseInfo) -> ListPageCard:
        return ListPageCard(
            cover=get_thumb_url(resp.cover_url),
            title=resp.name,
            extras=[resp.radio.name],
            small_extras=[f"{format_time(resp.duration)} | 播放 {resp.listener_count} | 点赞 {resp.liked_count}"],
        )


# --- 电台节目 ---
@song
class Program(BaseSong[md.ProgramBaseInfo]):
    calling = "声音"
    link_types = ("program", "dj")

    @property
    @override
    def id(self) -> int: return self.info.id

    @classmethod
    @override
    async def from_id(cls, arg_id: int) -> Self:
        info = await get_program_info(arg_id)
        if not info:
            raise ValueError("Voice not found")
        return cls(info)

    @override
    async def get_name(self) -> str: return self.info.name
    @override
    async def get_alias(self) -> list[str] | None: return None
    @override
    async def get_artists(self) -> list[str]: return [self.info.radio.name]
    @override
    async def get_duration(self) -> int: return self.info.duration
    @override
    async def get_cover_url(self) -> str: return self.info.cover_url

    @override
    async def get_playable_url(self) -> str:
        song_id = self.info.main_track_id
        info = (await get_track_audio([song_id]))[0]
        return info.url

    @override
    async def get_lyrics(self) -> None: return None

    @override
    @classmethod
    async def format_description(cls, info: SongInfo) -> str:
        self_obj = info.father
        p_desc = f"\n{cut_string(d)}" if (d := self_obj.info.description) else ""
        return (
            f"{cls.calling}：{self_obj.info.name}\n"
            f"电台：{self_obj.info.radio.name}\n"
            f"台主：{self_obj.info.dj.nickname}\n"
            f"时长 {info.display_duration} | "
            f"播放 {self_obj.info.listener_count} | "
            f"点赞 {self_obj.info.liked_count} | "
            f"评论 {self_obj.info.comment_count} | "
            f"分享 {self_obj.info.share_count}"
            f"{p_desc}"
        )


# --- 电台节目搜索器 ---
@searcher
class ProgramSearcher(BaseSearcher[md.ProgramSearchResult, md.ProgramBaseInfo, Program]):
    child_calling = Program.calling
    commands = ("网易声音", "wysy", "wyprog")

    @staticmethod
    @override
    async def search_from_id(arg_id: int) -> Program | None:
        try:
            return await Program.from_id(arg_id)
        except ValueError:
            return None

    @override
    async def _extract_resp_content(self, resp: md.ProgramSearchResult) -> list[md.ProgramBaseInfo] | None:
        return [x.base_info for x in resp.resources] if resp.resources else None
    @override
    async def _extract_total_count(self, resp: md.ProgramSearchResult) -> int: return resp.total_count
    @override
    async def _do_get_page(self, page: int) -> md.ProgramSearchResult: return await search_program(self.keyword, page=page)
    @override
    async def _build_selection(self, resp: md.ProgramBaseInfo) -> Program: return Program(info=resp)
    @override
    async def _build_list_page(self, resp: Iterable[md.ProgramBaseInfo]) -> ProgramListPage[Self]: return ProgramListPage(resp, self)


# --- 电台列表页 ---
class RadioListPage(BaseSongListPage[md.RadioBaseInfo, _TSongList], Generic[_TSongList]):
    @override
    @classmethod
    async def transform_resp_to_list_card(cls, resp: md.RadioBaseInfo) -> ListPageCard:
        return ListPageCard(
            cover=get_thumb_url(resp.pic_url),
            title=resp.name,
            extras=[resp.dj.nickname],
            small_extras=[f"节目数 {resp.program_count} | 播放 {resp.play_count} | 收藏 {resp.sub_count}"],
        )


# --- 电台 ---
@playlist
class Radio(BasePlaylist[md.Radio, md.RadioProgramList, md.ProgramBaseInfo, Program]):
    calling = "电台"
    child_calling = Program.calling
    link_types = ("radio",)

    @property
    @override
    def id(self) -> int: return self.info.id

    @classmethod
    @override
    async def from_id(cls, arg_id: int) -> Self:
        resp = await get_radio_info(arg_id)
        return cls(resp)

    @override
    async def _extract_resp_content(self, resp: md.RadioProgramList) -> list[md.ProgramBaseInfo]: return resp.programs
    @override
    async def _extract_total_count(self, resp: md.RadioProgramList) -> int: return resp.count
    @override
    async def _do_get_page(self, page: int) -> md.RadioProgramList: return await get_radio_programs(self.id, page)
    @override
    async def _build_selection(self, resp: md.ProgramBaseInfo) -> Program: return Program(info=resp)
    @override
    async def _build_list_page(self, resp: Iterable[md.ProgramBaseInfo]) -> ProgramListPage[Self]: return ProgramListPage(resp, self)
    @override
    async def get_name(self) -> str: return self.info.name
    @override
    async def get_creators(self) -> list[str]: return [self.info.dj.nickname]
    @override
    async def get_cover_url(self) -> str: return self.info.pic_url

    @override
    @classmethod
    async def format_description(cls, info: PlaylistInfo) -> str:
        base_desc = await super().format_description(info)
        self_obj = info.father
        sec_category = f"/{c}" if (c := self_obj.info.second_category) else ""
        lst_desc = f"\n{cut_string(d)}" if (d := self_obj.info.desc) else ""
        return (
            f"{base_desc}\n"
            f"分类：{self_obj.info.category}{sec_category}\n"
            f"播放 {self_obj.info.play_count} | "
            f"收藏 {self_obj.info.sub_count} | "
            f"点赞 {self_obj.info.liked_count} | "
            f"评论 {self_obj.info.comment_count} | "
            f"分享 {self_obj.info.share_count}"
            f"{lst_desc}"
        )


# --- 电台搜索器 ---
@searcher
class RadioSearcher(BaseSearcher[md.RadioSearchResult, md.RadioBaseInfo, Radio]):
    child_calling = Radio.calling
    commands = ("网易电台", "wydt", "wydj")

    @staticmethod
    @override
    async def search_from_id(arg_id: int) -> Radio | None:
        with suppress(Exception):
            return await Radio.from_id(arg_id)
        return None

    @override
    async def _extract_resp_content(self, resp: md.RadioSearchResult) -> list[md.RadioBaseInfo] | None:
        return [x.base_info for x in resp.resources] if resp.resources else None
    @override
    async def _extract_total_count(self, resp: md.RadioSearchResult) -> int: return resp.total_count
    @override
    async def _do_get_page(self, page: int) -> md.RadioSearchResult: return await search_radio(self.keyword, page=page)
    @override
    async def _build_selection(self, resp: md.RadioBaseInfo) -> Radio: return await Radio.from_id(resp.id)
    @override
    async def _build_list_page(self, resp: Iterable[md.RadioBaseInfo]) -> RadioListPage[Self]: return RadioListPage(resp, self)


def calc_page_number(index: int) -> int:
    try:
        from .main import _config
        limit = _config.get("list_limit", 20)
    except Exception:
        limit = 20
    return (index // limit) + 1
