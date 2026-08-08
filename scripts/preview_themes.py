"""生成 multincm 亮色/暗色搜索列表与歌词预览。"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFilter

if "astrbot" not in sys.modules:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logging.getLogger("multincm-preview")
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("multincm_renderer", ROOT / "renderer.py")
renderer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = renderer
spec.loader.exec_module(renderer)
render_lyrics = renderer.render_lyrics
render_search_list = renderer.render_search_list

OUT = ROOT / "docs" / "previews"


@dataclass
class Card:
    cover: str
    title: str
    alias: str = ""
    extras: list[str] | None = None
    small_extras: list[str] | None = None

    def __post_init__(self):
        self.extras = self.extras or []
        self.small_extras = self.small_extras or []


@dataclass
class Lyric:
    time: int
    lrc: dict[str, str]


def make_cover(path: Path, colors: tuple[str, str], seed: int) -> None:
    size = 320
    a = tuple(int(colors[0][i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(colors[1][i:i + 2], 16) for i in (1, 3, 5))
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / (size - 1)
        d.line((0, y, size, y), fill=tuple(round(a[i] * (1 - t) + b[i] * t) for i in range(3)))
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((25 + seed * 7, 30, 250, 255), fill=(255, 255, 255, 42))
    gd.ellipse((110, 95 + seed * 5, 305, 290), fill=(255, 255, 255, 26))
    out = img.convert("RGBA")
    out.alpha_composite(glow.filter(ImageFilter.GaussianBlur(24)))
    od = ImageDraw.Draw(out)
    od.ellipse((72, 72, 248, 248), fill=(12, 16, 28, 95), outline=(255, 255, 255, 90), width=3)
    od.ellipse((142, 142, 178, 178), fill=(255, 255, 255, 180))
    od.arc((88, 88, 232, 232), 205, 335, fill=(255, 255, 255, 120), width=4)
    out.save(path)


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    palettes = [("#7657E8", "#27365D"), ("#FF866A", "#682C67"), ("#43C6AC", "#274069"),
                ("#FFB347", "#733A5E"), ("#50A7FF", "#30439A"), ("#EF6CA8", "#573D89")]
    covers = []
    for i, palette in enumerate(palettes):
        path = OUT / f"sample-cover-{i + 1}.png"
        make_cover(path, palette, i)
        covers.append(str(path))
    cards = [
        Card(covers[0], "晴天", "晴朗版", ["周杰伦"], ["04:29  ·  热度 98"]),
        Card(covers[1], "若把你", "Live", ["Kirsty刘瑾睿"], ["03:12  ·  热度 92"]),
        Card(covers[2], "你要的全拿走", "Everything", ["胡彦斌"], ["04:59  ·  热度 88"]),
        Card(covers[3], "Love Story", "Taylor's Version", ["Taylor Swift"], ["03:56  ·  热度 96"]),
        Card(covers[4], "于是", "2026 Remaster", ["郑润泽"], ["03:48  ·  热度 90"]),
        Card(covers[5], "我怀念的", "现场版", ["孙燕姿"], ["04:47  ·  热度 94"]),
    ]
    page = SimpleNamespace(father=SimpleNamespace(child_calling="歌曲", current_page=1, max_page=12, total_count=228))
    lyrics = [
        Lyric(0, {"roma": "Yoru no kaze ga sotto fuite", "main": "夜晚的风轻轻吹过", "trans": "The night breeze passes softly"}),
        Lyric(12800, {"roma": "Kimi no koe wo sagashiteru", "main": "我仍在人海里寻找你的声音", "trans": "I am still searching for your voice in the crowd"}),
        Lyric(26400, {"main": "如果星光会记得", "trans": "If the starlight remembers"}),
        Lyric(39700, {"roma": "Mou ichido deaeru nara", "main": "愿我们还能再次相遇", "trans": "May we meet once again"}),
        Lyric(5940000, {"meta": "歌词贡献者：Rika [10086]"}),
    ]
    info = SimpleNamespace(display_name="如果星光会记得", display_artists="Rika / AstrBot")
    for theme in ("dark", "light"):
        search = OUT / f"multincm-search-{theme}.png"
        lyric = OUT / f"multincm-lyrics-{theme}.png"
        search.write_bytes(await render_search_list(page, cards, theme=theme))
        lyric.write_bytes(await render_lyrics(lyrics, info=info, theme=theme))
    for cover in covers:
        Path(cover).unlink(missing_ok=True)
    print(OUT)


if __name__ == "__main__":
    asyncio.run(main())
