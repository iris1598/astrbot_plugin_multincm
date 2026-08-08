"""PIL 渲染器：沿用 astrbot_plugin_rika_share 的亮/暗主题语言。"""

from __future__ import annotations

import asyncio
import io
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from astrbot.api import logger

if TYPE_CHECKING:
    from .data_source import GeneralSongListPage, ListPageCard, SongInfo
    from .lrc_parser import NCMLrcGroupLine


_FONT_DIR = Path(__file__).resolve().parent


def _find_font(bold: bool = False) -> str | None:
    names = (["msyhbd.ttc", "simhei.ttf"] if bold else ["msyh.ttc", "simsun.ttc"])
    names += ["NotoSansSC-Regular.ttf", "NotoSansCJK-Regular.ttc", "wqy-microhei.ttc"]
    paths = [*_FONT_DIR.glob("*.ttf"), *_FONT_DIR.glob("*.ttc")]
    system = platform.system()
    if system == "Windows":
        paths += [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / n for n in names]
    elif system == "Darwin":
        paths += [Path("/System/Library/Fonts") / n for n in names]
    else:
        paths += [Path("/usr/share/fonts/truetype/noto") / n for n in names]
        paths += [Path("/usr/share/fonts/opentype/noto") / n for n in names]
    for path in paths:
        if path.exists():
            return str(path)
    return None


_FONT = _find_font(False)
_FONT_BOLD = _find_font(True) or _FONT


def _font(size: int, bold: bool = False):
    path = _FONT_BOLD if bold else _FONT
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


@dataclass(frozen=True)
class _Theme:
    top: tuple[int, int, int]
    bottom: tuple[int, int, int]
    card: tuple[int, int, int]
    text: tuple[int, int, int]
    secondary: tuple[int, int, int]
    tertiary: tuple[int, int, int]
    divider: tuple[int, int, int]
    accent: tuple[int, int, int]
    shadow: int
    glow: int


_THEMES = {
    "dark": _Theme((36, 43, 63), (18, 22, 31), (255, 255, 255), (245, 247, 252),
                    (174, 182, 200), (123, 133, 152), (255, 255, 255), (138, 173, 244), 120, 32),
    "light": _Theme((255, 255, 255), (241, 244, 249), (255, 255, 255), (26, 33, 48),
                    (85, 96, 122), (140, 149, 169), (27, 34, 51), (77, 126, 255), 50, 18),
}

IMG_WIDTH = 840


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    line = Image.new("RGB", (1, max(h, 1)))
    for y in range(max(h, 1)):
        t = y / max(h - 1, 1)
        line.putpixel((0, y), tuple(round(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    return line.resize((w, h))


def _with_alpha(rgb: tuple[int, int, int], alpha: int) -> tuple[int, int, int, int]:
    return (*rgb, alpha)


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    while text and draw.textbbox((0, 0), text + "…", font=font)[2] > max_width:
        text = text[:-1]
    return text + "…" if text else ""


def _text(draw, xy, text, font, fill):
    draw.text(xy, text, font=font, fill=fill)


def _base(width: int, height: int, theme: _Theme) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    canvas = Image.new("RGBA", (width, height + 18), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((8, 8, width - 8, height), radius=28, fill=(0, 0, 0, theme.shadow))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)))
    bg = _gradient((width, height), theme.top, theme.bottom).convert("RGBA")
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((width * .45, -height * .18, width * 1.16, height * .62), fill=_with_alpha(theme.accent, theme.glow))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(48)))
    bg.putalpha(_rounded_mask((width, height), 28))
    canvas.alpha_composite(bg)
    border = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle((0, 0, width - 1, height - 1), radius=28, outline=_with_alpha(theme.divider, 30 if theme is _THEMES["dark"] else 20), width=1)
    canvas.alpha_composite(border)
    return canvas, ImageDraw.Draw(canvas)


def _glass(canvas: Image.Image, box: tuple[int, int, int, int], theme: _Theme, radius: int = 18, alpha: int = 20) -> None:
    x0, y0, x1, y1 = box
    region = canvas.crop(box).filter(ImageFilter.GaussianBlur(7))
    tint = (255, 255, 255, alpha) if theme is _THEMES["dark"] else (255, 255, 255, 145)
    region.alpha_composite(Image.new("RGBA", region.size, tint))
    mask = _rounded_mask(region.size, radius)
    canvas.paste(region, (x0, y0), mask)
    layer = Image.new("RGBA", region.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((0, 0, region.size[0] - 1, region.size[1] - 1), radius=radius, outline=_with_alpha(theme.divider, 34 if theme is _THEMES["dark"] else 28), width=1)
    canvas.alpha_composite(layer, (x0, y0))


def _glass_badge(canvas: Image.Image, center: tuple[int, int], diameter: int, theme: _Theme) -> None:
    """绘制真实背景模糊的半透明圆形序号徽标。"""
    cx, cy = center
    radius = diameter // 2
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    region = canvas.crop(box).filter(ImageFilter.GaussianBlur(8))
    tint_alpha = 72 if theme is _THEMES["dark"] else 48
    region.alpha_composite(Image.new("RGBA", region.size, _with_alpha(theme.accent, tint_alpha)))
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    canvas.paste(region, (box[0], box[1]), mask)
    layer = Image.new("RGBA", region.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(
        (1, 1, diameter - 2, diameter - 2),
        outline=(255, 255, 255, 115 if theme is _THEMES["dark"] else 170),
        width=1,
    )
    canvas.alpha_composite(layer, (box[0], box[1]))


async def _load_cover(url: str, size: int) -> Image.Image | None:
    if not url:
        return None
    try:
        if Path(url).exists():
            return Image.open(url).convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return Image.open(io.BytesIO(await resp.read())).convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
    except Exception:
        return None
    return None


def _placeholder(size: int, theme: _Theme, index: int) -> Image.Image:
    img = _gradient((size, size), tuple(min(255, c + 22) for c in theme.accent), theme.bottom).convert("RGBA")
    d = ImageDraw.Draw(img)
    d.ellipse((size * .18, size * .18, size * .82, size * .82), outline=(255, 255, 255, 90), width=2)
    d.ellipse((size * .39, size * .39, size * .61, size * .61), fill=_with_alpha(theme.accent, 150))
    d.text((size * .44, size * .39), str(index), font=_font(max(12, size // 4), True), fill=(255, 255, 255, 230))
    img.putalpha(_rounded_mask((size, size), 14))
    return img


async def render_search_list(page: "GeneralSongListPage", cards: list["ListPageCard"], limit: int = 20, theme: str = "dark") -> bytes:
    """渲染带亮/暗主题的搜索结果卡片。"""
    th = _THEMES.get(theme, _THEMES["dark"])
    cards = cards[:limit]
    pad, gap, card_h, cover = 32, 18, 116, 76
    cols = 2
    card_w = (IMG_WIDTH - pad * 2 - gap) // cols
    rows = max(1, (len(cards) + cols - 1) // cols)
    header_h, footer_h = 146, 66
    height = pad + header_h + rows * card_h + (rows - 1) * gap + footer_h
    canvas, draw = _base(IMG_WIDTH, height, th)
    accent = th.accent
    draw.rounded_rectangle((pad, 28, pad + 112, 36), radius=4, fill=accent)
    title = f"{page.father.child_calling}列表"
    _text(draw, (pad, 52), title, _font(32, True), th.text)
    tip = "发送序号选择  ·  P+数字跳页  ·  N 下一页  ·  E 退出"
    _text(draw, (pad, 94), tip, _font(14), th.secondary)
    page_info = f"第 {page.father.current_page} / {page.father.max_page} 页   ·   共 {page.father.total_count} 项"
    info_font = _font(13, True)
    iw = draw.textbbox((0, 0), page_info, font=info_font)[2]
    pill_box = (IMG_WIDTH - pad - iw - 24, 58, IMG_WIDTH - pad, 88)
    # 页数胶囊使用完全不透明底色，避免不同平台对半透明 PNG 的处理差异。
    pill_bg = (70, 87, 126) if th is _THEMES["dark"] else (231, 237, 255)
    pill_border = (132, 157, 215) if th is _THEMES["dark"] else (155, 181, 247)
    pill_text = (255, 255, 255) if th is _THEMES["dark"] else (38, 65, 125)
    draw.rounded_rectangle(pill_box, radius=15, fill=pill_bg, outline=pill_border, width=1)
    _text(draw, (IMG_WIDTH - pad - iw - 12, 66), page_info, info_font, pill_text)

    covers = await asyncio.gather(*[_load_cover(c.cover, cover) for c in cards])
    y0 = pad + header_h
    for i, card in enumerate(cards):
        col, row = i % cols, i // cols
        x, y = pad + col * (card_w + gap), y0 + row * (card_h + gap)
        _glass(canvas, (x, y, x + card_w, y + card_h), th, 18, 18 if th is _THEMES["dark"] else 90)
        cover_img = covers[i] or _placeholder(cover, th, i + 1)
        canvas.alpha_composite(cover_img.convert("RGBA"), (x + 18, y + 20))
        badge = str(i + 1)
        badge_center = (x + 25, y + 25)
        _glass_badge(canvas, badge_center, 34, th)
        badge_font = _font(13, True)
        badge_box = draw.textbbox((0, 0), badge, font=badge_font)
        badge_w = badge_box[2] - badge_box[0]
        badge_h = badge_box[3] - badge_box[1]
        _text(
            draw,
            (badge_center[0] - badge_w / 2, badge_center[1] - badge_h / 2 - badge_box[1]),
            badge,
            badge_font,
            (255, 255, 255, 245) if th is _THEMES["dark"] else (40, 64, 118, 235),
        )
        tx, ty, max_w = x + 110, y + 20, card_w - 128
        title_font = _font(18, True)
        main = _truncate(draw, card.title, title_font, max_w)
        _text(draw, (tx, ty), main, title_font, th.text)
        ty += 27
        if card.alias:
            _text(draw, (tx, ty), _truncate(draw, f"（{card.alias}）", _font(12), max_w), _font(12), th.tertiary)
            ty += 20
        for extra in card.extras[:1]:
            _text(draw, (tx, ty), _truncate(draw, extra, _font(14), max_w), _font(14), th.secondary)
            ty += 21
        for extra in card.small_extras[:1]:
            _text(draw, (tx, ty), _truncate(draw, extra, _font(12), max_w), _font(12), th.tertiary)
    fy = y0 + rows * card_h + (rows - 1) * gap + 28
    draw.line((pad, fy, IMG_WIDTH - pad, fy), fill=_with_alpha(th.divider, 30), width=1)
    _text(draw, (pad, fy + 14), "网易云音乐 · astrbot_plugin_multincm", _font(12), th.tertiary)
    _text(draw, (IMG_WIDTH - pad - 120, fy + 14), "RIKA SHARE STYLE", _font(11, True), th.tertiary)
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()


def _format_time(ms: int) -> str:
    if ms >= 5940000:
        return ""
    sec = ms // 1000
    return f"{sec // 60:02d}:{sec % 60:02d}"


async def render_lyrics(groups: list["NCMLrcGroupLine"], info: "SongInfo | None" = None, theme: str = "dark") -> bytes:
    """渲染带主题渐变、玻璃分组和高亮时间轴的歌词图。"""
    th = _THEMES.get(theme, _THEMES["dark"])
    width, pad = 840, 38
    header_h, footer_h, group_gap = 142, 64, 24
    f_title, f_artist = _font(30, True), _font(16)
    f_main, f_trans, f_roma = _font(22, True), _font(16), _font(14)
    f_time, f_meta, f_footer = _font(12, True), _font(12), _font(11)
    sort_order = ("roma", "main", "trans")
    def rows_for(g):
        return [(k, v) for k, v in sorted(g.lrc.items(), key=lambda x: sort_order.index(x[0]) if x[0] in sort_order else 99) if k in sort_order]
    def wrap(text, font, max_w):
        lines, buf = [], ""
        for ch in text or "":
            if draw_probe.textbbox((0, 0), buf + ch, font=font)[2] > max_w and buf:
                lines.append(buf); buf = ch
            else: buf += ch
        return lines + ([buf] if buf else [""])
    probe = Image.new("RGB", (width, 1)); draw_probe = ImageDraw.Draw(probe)
    groups2 = [g for g in groups if not (len(g.lrc) == 1 and g.lrc.get("main", "").strip() in ("", "-")) and rows_for(g)]
    body_h = 0
    for g in groups2:
        h = 0
        for kind, text in rows_for(g):
            font, line_h = {"main": (f_main, 31), "trans": (f_trans, 24), "roma": (f_roma, 21)}[kind]
            h += len(wrap(text, font, width - pad * 2 - 84)) * line_h + 3
        body_h += h + 24 + group_gap
    height = max(360, header_h + 28 + body_h + footer_h)
    canvas, draw = _base(width, height, th)
    draw.rounded_rectangle((pad, 28, pad + 112, 36), radius=4, fill=th.accent)
    title = info.display_name if info and info.display_name else "歌词"
    artist = info.display_artists if info and info.display_artists else ""
    _text(draw, (pad, 54), _truncate(draw, title, f_title, width - pad * 2 - 40), f_title, th.text)
    if artist:
        tags = []
        if any("trans" in g.lrc for g in groups): tags.append("译")
        if any("roma" in g.lrc for g in groups): tags.append("音")
        suffix = f"   ·   {' / '.join(tags)}" if tags else ""
        _text(draw, (pad, 100), f"by  {artist}{suffix}", f_artist, th.secondary)
    draw.line((pad, header_h, width - pad, header_h), fill=_with_alpha(th.divider, 38), width=1)
    y = header_h + 24
    for gi, g in enumerate(groups2):
        content_rows = rows_for(g)
        group_h = 0
        for kind, text in content_rows:
            font, line_h = {"main": (f_main, 31), "trans": (f_trans, 24), "roma": (f_roma, 21)}[kind]
            group_h += len(wrap(text, font, width - pad * 2 - 84)) * line_h + 3
        _glass(canvas, (pad, y, width - pad, y + group_h + 24), th, 16, 14 if th is _THEMES["dark"] else 100)
        time = _format_time(g.time)
        _text(draw, (pad + 16, y + 16), time, f_time, th.accent)
        draw.rounded_rectangle((pad + 70, y + 17, pad + 74, y + group_h + 7), radius=2, fill=_with_alpha(th.accent, 180))
        tx, ty = pad + 94, y + 14
        for kind, text in content_rows:
            font, line_h, color, xoff = {"main": (f_main, 31, th.text, 0), "trans": (f_trans, 24, th.accent, 8), "roma": (f_roma, 21, th.tertiary, 8)}[kind]
            for line in wrap(text, font, width - pad * 2 - 84):
                _text(draw, (tx + xoff, ty), line, font, color); ty += line_h
            ty += 3
        y += group_h + 24 + group_gap
    footer_y = height - footer_h
    draw.line((pad, footer_y, width - pad, footer_y), fill=_with_alpha(th.divider, 30), width=1)
    metas = [v for g in groups for k, v in g.lrc.items() if k == "meta" and "贡献" in v]
    if metas:
        meta_text = _truncate(draw, "  ·  ".join(metas), f_meta, width - pad * 2 - 210)
        _text(draw, (pad, footer_y + 18), meta_text, f_meta, th.tertiary)
    _text(draw, (width - pad - 168, footer_y + 18), "astrbot_plugin_multincm", f_footer, th.tertiary)
    out = io.BytesIO(); canvas.save(out, "PNG", optimize=True); return out.getvalue()
