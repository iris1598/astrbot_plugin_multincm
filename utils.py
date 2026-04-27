"""通用工具函数（去除 NoneBot 依赖）"""
import asyncio
import math
from pathlib import Path
from typing import TYPE_CHECKING
from yarl import URL

if TYPE_CHECKING:
    from . import models as md


FILESYSTEM_DISALLOWED_CHARS = '\\/:*?"<>|'


def half_to_full(string: str):
    s: list[str] = []
    for x in string:
        cp = ord(x)
        if cp == 32:
            cp = 12288
        elif 33 <= cp <= 126:
            cp += 65248
        s.append(chr(cp))
    return "".join(s)


FILESYSTEM_CHAR_REPLACEMENTS = dict(
    zip(FILESYSTEM_DISALLOWED_CHARS, half_to_full(FILESYSTEM_DISALLOWED_CHARS)),
)


def format_time(time_ms: int) -> str:
    """格式化毫秒时间为 mm:ss"""
    ss, _ = divmod(time_ms, 1000)
    mm, ss = divmod(ss, 60)
    return f"{mm:0>2d}:{ss:0>2d}"


def format_alias(name: str, alias: list[str] | None = None) -> str:
    return f"{name}（{'；'.join(alias)}）" if alias else name


def format_artists(artists: list["md.Artist"]) -> str:
    return "、".join([x.name for x in artists])


def calc_page_number(index: int) -> int:
    try:
        from .main import _config
        limit = _config.get("list_limit", 20)
    except Exception:
        limit = 20
    return (index // limit) + 1


def calc_min_index(page: int) -> int:
    try:
        from .main import _config
        limit = _config.get("list_limit", 20)
    except Exception:
        limit = 20
    return (page - 1) * limit


def calc_min_max_index(page: int) -> tuple[int, int]:
    min_index = calc_min_index(page)
    try:
        from .main import _config
        limit = _config.get("list_limit", 20)
    except Exception:
        limit = 20
    max_index = min_index + limit
    return min_index, max_index


def calc_max_page(total: int) -> int:
    try:
        from .main import _config
        limit = _config.get("list_limit", 20)
    except Exception:
        limit = 20
    return math.ceil(total / limit)


def get_thumb_url(url: str, size: int = 64) -> str:
    return str(URL(url).update_query(param=f"{size}y{size}"))


def build_item_link(item_type: str, item_id: int) -> str:
    return f"https://music.163.com/{item_type}?id={item_id}"


def cut_string(text: str, length: int = 50) -> str:
    if len(text) <= length:
        return text
    return text[: length - 1] + "…"


def merge_alias(song: "md.Song") -> list[str]:
    alias = song.tns.copy() if song.tns else []
    alias.extend(
        x for x in (y for a in song.alias for y in a.split("；")) if x not in alias
    )
    return alias


def safe_filename(name: str) -> str:
    for k, v in FILESYSTEM_CHAR_REPLACEMENTS.items():
        name = name.replace(k, v)
    return name


async def download_file(url: str, save_path: Path) -> Path:
    """下载文件到本地"""
    import httpx
    save_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with save_path.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
    return save_path
