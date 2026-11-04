"""搜索器与数据抽象层。

把网易云原始响应包装成可翻页、可选择、可解析链接的统一对象，
供 :mod:`..main` 的指令处理器使用。
"""
from .source import (
    AlbumSearcher,
    BasePlaylist,
    BaseSearcher,
    BaseSong,
    BaseSongListPage,
    GeneralGetPageReturn,
    GeneralSongList,
    GeneralSongListPage,
    GeneralSongOrList,
    GeneralSongOrPlaylist,
    ListPageCard,
    PlaylistInfo,
    PlaylistSearcher,
    ProgramSearcher,
    RadioSearcher,
    Song,
    SongInfo,
    SongSearcher,
    registered_playlist,
    registered_searcher,
    registered_song,
    resolve_from_link_params,
)

__all__ = [
    "AlbumSearcher",
    "BasePlaylist",
    "BaseSearcher",
    "BaseSong",
    "BaseSongListPage",
    "GeneralGetPageReturn",
    "GeneralSongList",
    "GeneralSongListPage",
    "GeneralSongOrList",
    "GeneralSongOrPlaylist",
    "ListPageCard",
    "PlaylistInfo",
    "PlaylistSearcher",
    "ProgramSearcher",
    "RadioSearcher",
    "Song",
    "SongInfo",
    "SongSearcher",
    "registered_playlist",
    "registered_searcher",
    "registered_song",
    "resolve_from_link_params",
]
