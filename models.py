"""Pydantic 数据模型 - 网易云音乐 API 响应模型"""
from typing import Literal, TypeAlias

from pydantic import BaseModel, Field, ConfigDict


def camel_case(s: str) -> str:
    """将 snake_case 转为 camelCase"""
    parts = s.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


def model_with_alias_generator(model_config: dict):
    """装饰器：为 Pydantic 模型添加 alias_generator"""
    def decorator(cls):
        return cls
    return decorator


BrLevelType: TypeAlias = Literal[
    "hires", "lossless", "exhigh", "higher", "standard", "none",
]


class Artist(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    id: int
    name: str
    tns: list[str] | None = None
    alias: list[str] | None = None


class BaseAlbum(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    id: int
    name: str
    pic_url: str


class Album(BaseAlbum):
    size: int
    artists: list[Artist]


class Privilege(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    id: int
    pl: int


class Song(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    name: str
    id: int
    ar: list[Artist]
    alias: list[str] = Field(..., alias="alia")
    pop: int
    al: BaseAlbum
    dt: int
    """歌曲时长，单位 ms"""
    tns: list[str] | None = None
    privilege: Privilege | None = None


class QcReminder(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    qc_reminder_part: str
    high_light: bool


class SearchQcReminder(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    qc_reminders: list[QcReminder]
    qc_reminder_channel: str


class SongSearchResult(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    search_qc_reminder: SearchQcReminder | None = None
    song_count: int
    songs: list[Song] | None = None


class TrackAudio(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    id: int
    url: str
    br: int
    size: int
    md5: str
    level: str | None = None
    encode_type: str | None = None
    time: int


class User(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    id: int
    user_id: int = Field(..., alias="userid")
    nickname: str


class Lyric(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    version: int
    lyric: str


class LyricData(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    trans_user: User | None = None
    lyric_user: User | None = None
    lrc: Lyric | None = None
    trans_lrc: Lyric | None = Field(None, alias="tlyric")
    roma_lrc: Lyric | None = Field(None, alias="romalrc")


class DJ(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    user_id: int
    nickname: str
    avatar_url: str
    gender: int
    signature: str
    background_url: str


class BaseRadio(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    id: int
    name: str
    pic_url: str
    desc: str
    sub_count: int
    program_count: int
    play_count: int
    category_id: int
    second_category_id: int | None = None
    category: str
    second_category: str | None = None
    last_program_id: int


class RadioBaseInfo(BaseRadio):
    dj: DJ


class Radio(RadioBaseInfo):
    share_count: int
    liked_count: int
    comment_count: int


class ProgramBaseInfo(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    id: int
    main_track_id: int
    name: str
    cover_url: str
    description: str
    dj: DJ
    radio: BaseRadio
    duration: int
    listener_count: int
    share_count: int
    liked_count: int
    comment_count: int
    comment_thread_id: str


class ProgramResource(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    base_info: ProgramBaseInfo


class ProgramSearchResult(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    resources: list[ProgramResource] | None = None
    total_count: int
    search_qc_reminder: SearchQcReminder | None = None


class TrackId(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    id: int


class PlaylistCreator(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    user_id: int
    nickname: str


class BasePlaylist(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    id: int
    name: str
    cover_img_url: str
    creator: PlaylistCreator
    track_count: int
    play_count: int
    book_count: int
    description: str | None = None


class Playlist(BasePlaylist):
    track_ids: list[TrackId]
    book_count: int = Field(alias="subscribedCount")
    share_count: int
    comment_count: int


class PlaylistSearchResult(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    playlists: list[BasePlaylist] | None = None
    playlist_count: int
    search_qc_reminder: SearchQcReminder | None = None


class RadioResource(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    base_info: RadioBaseInfo


class RadioSearchResult(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    resources: list[RadioResource] | None = None
    total_count: int
    search_qc_reminder: SearchQcReminder | None = None


class RadioProgramList(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    count: int
    programs: list[ProgramBaseInfo]


class AlbumSearchResult(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    albums: list[Album] | None = None
    album_count: int


class AlbumInfo(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)
    album: Album
    songs: list[Song]
