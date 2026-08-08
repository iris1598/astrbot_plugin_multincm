"""PIL 图片渲染 - 搜索列表 & 歌词（替代 Jinja2+Playwright）"""
import asyncio
import io
import os
import platform
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from astrbot.api import logger

if TYPE_CHECKING:
    from .data_source import GeneralSongListPage, ListPageCard, SongInfo
    from .lrc_parser import NCMLrcGroupLine

# ==================== 字体加载 ====================

_FONT_DIR = os.path.dirname(os.path.abspath(__file__))


def _build_font_candidates() -> list[str]:
    """构建字体候选路径列表（按优先级排序）"""
    candidates: list[str] = []

    # 1. 插件目录内置字体（最高优先级）
    candidates.append(os.path.join(_FONT_DIR, "font.ttc"))
    candidates.append(os.path.join(_FONT_DIR, "font.ttf"))
    candidates.append(os.path.join(_FONT_DIR, "NotoSansSC-Regular.ttf"))

    # 2. AstrBot data 目录
    try:
        # 尝试常见的 AstrBot 数据目录位置
        for base in [os.getcwd(), os.path.dirname(os.path.dirname(_FONT_DIR))]:
            data_font = os.path.join(base, "data", "plugin_data", "astrbot_plugin_multincm", "fonts", "NotoSansSC-Regular.ttf")
            if os.path.exists(data_font):
                candidates.append(data_font)
                break
    except Exception:
        pass

    # 3. 按操作系统添加系统字体路径
    system = platform.system()

    if system == "Windows":
        # 动态获取 Windows 系统目录，避免硬编码
        win_dir = os.environ.get("SYSTEMROOT", os.environ.get("WINDIR", r"C:\Windows"))
        font_dir = os.path.join(win_dir, "Fonts")
        candidates.extend([
            os.path.join(font_dir, "msyh.ttc"),        # 微软雅黑
            os.path.join(font_dir, "msyhbd.ttc"),       # 微软雅黑粗体
            os.path.join(font_dir, "simhei.ttf"),       # 黑体
            os.path.join(font_dir, "simsun.ttc"),       # 宋体
            os.path.join(font_dir, "simkai.ttf"),       # 楷体
            os.path.join(font_dir, "STXIHEI.TTF"),      # 华文细黑
        ])

    elif system == "Darwin":  # macOS
        candidates.extend([
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
            "/Library/Fonts/Songti.ttc",
            "/System/Library/Fonts/Supplemental/Songti.ttc",
        ])

    else:  # Linux / 其他
        candidates.extend([
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
            "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        ])

    return candidates


def _find_font() -> str | None:
    """按优先级查找可用中文字体"""
    candidates = _build_font_candidates()
    for path in candidates:
        if os.path.exists(path):
            logger.info(f"使用字体文件: {path}")
            return path
    return None


_font_path = _find_font()

if _font_path:
    logger.info(f"字体加载成功: {_font_path}")
else:
    logger.warning(
        "未找到可用的中文字体！图片中的中文将显示为方块。\n"
        "解决方案：\n"
        "1. 将中文字体文件（如 NotoSansSC-Regular.ttf）放到插件目录: "
        f"{_FONT_DIR}\n"
        "2. 或安装系统字体（Windows: 微软雅黑, Linux: fonts-noto-cjk）"
    )


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if _font_path:
        try:
            return ImageFont.truetype(_font_path, size)
        except Exception:
            pass
    # 最终回退：PIL 默认字体（不支持中文）
    logger.warning("字体加载失败，使用默认字体（不支持中文）")
    return ImageFont.load_default()


# 颜色方案 - 暗色主题（与原始 NoneBot 插件风格一致）
@dataclass(frozen=True)
class _RenderTheme:
    """Shared visual language for search cards and lyric sheets."""

    gradient_top: tuple[int, int, int]
    gradient_bottom: tuple[int, int, int]
    border: tuple[int, int, int]
    text_primary: tuple[int, int, int]
    text_secondary: tuple[int, int, int]
    text_tertiary: tuple[int, int, int]
    pill_bg: tuple[int, int, int]
    shadow_alpha: int
    glow_alpha: int
    frost_alpha: int
    frost_border_alpha: int
    border_alpha: int
    placeholder_top: tuple[int, int, int]
    placeholder_bottom: tuple[int, int, int]
    accent: tuple[int, int, int]


# These values match rika_share's two card themes. The accent is changed to
# NetEase Cloud Music red, just as rika_share changes its accent by platform.
_THEMES: dict[str, _RenderTheme] = {
    "dark": _RenderTheme(
        gradient_top=(36, 43, 63),
        gradient_bottom=(18, 22, 31),
        border=(255, 255, 255),
        text_primary=(245, 247, 252),
        text_secondary=(174, 182, 200),
        text_tertiary=(123, 133, 152),
        pill_bg=(255, 255, 255),
        shadow_alpha=130,
        glow_alpha=30,
        frost_alpha=14,
        frost_border_alpha=26,
        border_alpha=24,
        placeholder_top=(44, 51, 71),
        placeholder_bottom=(20, 24, 35),
        accent=(244, 109, 122),
    ),
    "light": _RenderTheme(
        gradient_top=(255, 255, 255),
        gradient_bottom=(241, 244, 249),
        border=(27, 34, 51),
        text_primary=(26, 33, 48),
        text_secondary=(85, 96, 122),
        text_tertiary=(140, 149, 169),
        pill_bg=(27, 34, 51),
        shadow_alpha=55,
        glow_alpha=16,
        frost_alpha=10,
        frost_border_alpha=20,
        border_alpha=14,
        placeholder_top=(228, 233, 242),
        placeholder_bottom=(243, 246, 251),
        accent=(213, 61, 81),
    ),
}


def _get_theme(name: str | None) -> _RenderTheme:
    """Return a supported palette; invalid configuration safely falls back."""
    return _THEMES.get((name or "dark").lower(), _THEMES["dark"])


def _mix_color(
    start: tuple[int, int, int], end: tuple[int, int, int], ratio: float,
) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * ratio) for a, b in zip(start, end))


def _gradient(
    size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int],
) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, (*top, 255))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        color = _mix_color(top, bottom, y / max(height - 1, 1))
        draw.line([(0, y), (width, y)], fill=(*color, 255))
    return image


def _new_surface(width: int, height: int, theme: _RenderTheme) -> Image.Image:
    """Create the soft gradient and brand glow used by rika-style cards."""
    image = _gradient((width, height), theme.gradient_top, theme.gradient_bottom)
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse(
        (-width // 3, -height // 2, width * 2 // 3, height // 2),
        fill=(*theme.accent, theme.glow_alpha * 4),
    )
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(max(32, width // 8))))
    return image


def _draw_glass_panel(
    image: Image.Image,
    box: tuple[int, int, int, int] | list[int],
    radius: int,
    theme: _RenderTheme,
    *,
    fill_alpha: int | None = None,
    border_alpha: int | None = None,
) -> None:
    """Draw a translucent panel with rika_share's subtle outline."""
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=(*theme.pill_bg, theme.frost_alpha if fill_alpha is None else fill_alpha),
        outline=(*theme.border, theme.frost_border_alpha if border_alpha is None else border_alpha),
        width=1,
    )
    image.alpha_composite(overlay)


def _rounded_cover(source: Image.Image, size: int, radius: int) -> Image.Image:
    cover = ImageOps.fit(source.convert("RGBA"), (size, size), method=Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    cover.putalpha(mask)
    return cover


def _placeholder_cover(size: int, theme: _RenderTheme) -> Image.Image:
    cover = _gradient((size, size), theme.placeholder_top, theme.placeholder_bottom)
    cover.alpha_composite(Image.new("RGBA", (size, size), (*theme.accent, 40)))
    return _rounded_cover(cover, size, 12)


IMG_WIDTH = 800
PADDING = 28
COLS = 2
COL_GAP = 14
ROW_GAP = 14
CARD_INNER_PAD = 16
COVER_SIZE = 72
CARD_W = (IMG_WIDTH - PADDING * 2 - (COLS - 1) * COL_GAP) // COLS


async def render_search_list(
    page: "GeneralSongListPage",
    cards: list["ListPageCard"],
    limit: int = 20,
    theme: str = "dark",
) -> bytes:
    """渲染搜索结果列表图片（双列网格布局）"""
    # 限制渲染条目数
    cards = cards[:limit]
    palette = _get_theme(theme)

    font_title = _get_font(32)
    font_tip = _get_font(14)
    font_song = _get_font(18)
    font_info = _get_font(14)
    font_small = _get_font(12)
    font_footer = _get_font(12)

    total_count = page.father.total_count
    current_page = page.father.current_page
    max_page = page.father.max_page
    title = f"{page.father.child_calling}列表"

    # 计算行数和图片高度
    rows = (len(cards) + COLS - 1) // COLS if cards else 1
    header_h = 112
    footer_h = 66
    card_h = COVER_SIZE + CARD_INNER_PAD * 2  # 单张卡片高度

    total_h = PADDING * 2 + header_h + rows * card_h + (rows - 1) * ROW_GAP + footer_h

    img = _new_surface(IMG_WIDTH, total_h, palette)
    _draw_glass_panel(
        img, [8, 8, IMG_WIDTH - 8, total_h - 8], 30, palette,
        fill_alpha=4, border_alpha=palette.border_alpha,
    )
    draw = ImageDraw.Draw(img)

    y = PADDING

    draw.rounded_rectangle([PADDING, y + 16, PADDING + 58, y + 22], radius=3, fill=palette.accent)
    draw.text((PADDING, y + 32), title, font=font_title, fill=palette.text_primary)
    brand = "NETEASE CLOUD MUSIC"
    brand_box = draw.textbbox((0, 0), brand, font=font_small)
    draw.text(
        (IMG_WIDTH - PADDING - (brand_box[2] - brand_box[0]), y + 40),
        brand, font=font_small, fill=palette.text_tertiary,
    )

    tip = "发送序号选择 | P+数字跳页 | 上一页(P) | 下一页(N) | 退出(E)"
    draw.text((PADDING, y + 76), tip, font=font_tip, fill=palette.text_secondary)
    page_info = f"第 {current_page} 页 / 共 {max_page} 页 | 总计 {total_count} 项"
    bbox_page = draw.textbbox((0, 0), page_info, font=font_tip)
    tw_page = bbox_page[2] - bbox_page[0]
    draw.text(
        (IMG_WIDTH - PADDING - tw_page, y + 76),
        page_info, font=font_tip, fill=palette.text_tertiary,
    )
    y += header_h

    # 下载封面（异步）
    covers: dict[int, Image.Image | None] = {}

    async def _fetch_cover(idx: int, url: str):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        covers[idx] = Image.open(io.BytesIO(data)).convert("RGB")
                        return
        except Exception:
            pass
        covers[idx] = None

    await asyncio.gather(*[
        _fetch_cover(i, card.cover)
        for i, card in enumerate(cards)
        if card.cover
    ])

    # 绘制双列网格
    for i, card in enumerate(cards):
        col = i % COLS
        row = i // COLS
        card_x = PADDING + col * (CARD_W + COL_GAP)
        card_y = y + row * (card_h + ROW_GAP)

        # Frosted card surface, matching rika_share's glass panels.
        _draw_glass_panel(
            img, [card_x, card_y, card_x + CARD_W, card_y + card_h],
            18, palette, fill_alpha=palette.frost_alpha + 4,
        )

        # Compact accent index badge.
        idx_text = str(i + 1)
        idx_font = _get_font(13)
        badge_box = [card_x + 10, card_y + 10, card_x + 38, card_y + 38]
        draw.rounded_rectangle(badge_box, radius=14, fill=palette.accent)
        draw.text(
            ((badge_box[0] + badge_box[2]) // 2, (badge_box[1] + badge_box[3]) // 2),
            idx_text, font=idx_font, fill=(255, 255, 255), anchor="mm",
        )

        # 3. 内部布局：序号徽章、封面、文字三段横向排列，互不覆盖。
        inner_x = card_x + CARD_INNER_PAD + 36
        inner_y = card_y + CARD_INNER_PAD

        # 封面
        cover_x = inner_x
        cover_y_pos = inner_y
        if i in covers and covers[i]:
            img.alpha_composite(_rounded_cover(covers[i], COVER_SIZE, 12), (cover_x, cover_y_pos))
        else:
            img.alpha_composite(_placeholder_cover(COVER_SIZE, palette), (cover_x, cover_y_pos))

        # 文字区域
        text_x = cover_x + COVER_SIZE + 16
        text_y = cover_y_pos
        max_text_width = card_x + CARD_W - CARD_INNER_PAD - text_x

        # 标题（粗体）
        title_text = card.title
        if card.alias:
            # 别名用小一号字体和次色
            alias_text = f"（{card.alias}）"
            main_title = _truncate_text(draw, title_text, font_song, max_text_width)
            alias_width = 0
            if main_title:
                title_bbox = draw.textbbox((0, 0), main_title, font=font_song)
                alias_width = title_bbox[2] - title_bbox[0]
            remaining = max(0, max_text_width - alias_width - 4)
            if remaining > 20:
                alias_text = _truncate_text(draw, alias_text, font_small, remaining)
                draw.text((text_x, text_y), main_title, font=font_song, fill=palette.text_primary)
                title_bbox2 = draw.textbbox((0, 0), main_title, font=font_song)
                draw.text(
                    (text_x + (title_bbox2[2] - title_bbox2[0]), text_y),
                    alias_text, font=font_small, fill=palette.text_secondary,
                )
            else:
                combined = main_title
                draw.text((text_x, text_y), combined, font=font_song, fill=palette.text_primary)
            text_y += 24
        else:
            title_text = _truncate_text(draw, title_text, font_song, max_text_width)
            draw.text((text_x, text_y), title_text, font=font_song, fill=palette.text_primary)
            text_y += 24

        # 附加信息（extras）
        for extra in card.extras:
            extra = _truncate_text(draw, extra, font_info, max_text_width)
            draw.text((text_x, text_y), extra, font=font_info, fill=palette.text_secondary)
            text_y += 18

        # 附加小信息（small_extras）
        for extra in card.small_extras:
            extra = _truncate_text(draw, extra, font_small, max_text_width)
            draw.text((text_x, text_y), extra, font=font_small, fill=palette.text_tertiary)
            text_y += 16

    # 底部
    y_bottom = y + rows * card_h + (rows - 1) * ROW_GAP + 12
    footer_text = "Generated by astrbot-plugin-multincm"
    fbbox = draw.textbbox((0, 0), footer_text, font=font_footer)
    fw = fbbox[2] - fbbox[0]
    draw.text(((IMG_WIDTH - fw) // 2, y_bottom), footer_text, font=font_footer, fill=palette.text_tertiary)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _truncate_text(draw: ImageDraw.Draw, text: str, font, max_width: int) -> str:
    """截断过长的文本"""
    bbox = draw.textbbox((0, 0), text, font=font)
    if bbox[2] - bbox[0] <= max_width:
        return text
    while text and (draw.textbbox((0, 0), text + "…", font=font)[2] - draw.textbbox((0, 0), text + "…", font=font)[0]) > max_width:
        text = text[:-1]
    return text + "…" if text else ""


async def render_lyrics(
    groups: list["NCMLrcGroupLine"],
    info: "SongInfo | None" = None,
    theme: str = "dark",
) -> bytes:
    """渲染歌词图片。

    样式：
      - 顶栏：歌曲标题 + 艺人，渐变背景 + 左侧装饰条
      - 主体：每行三段式（时间戳 · 主歌词 / 翻译 / 罗马音）
      - 底栏：贡献者 / 版权信息
    """
    LRC_IMG_WIDTH = 780
    LRC_PADDING_X = 32
    LRC_PADDING_Y = 24
    LRC_HEADER_H = 110
    LRC_FOOTER_H = 56
    LRC_GROUP_GAP = 22
    LRC_INTER_GAP = 4  # main 与 trans/roma 之间的间距
    LRC_INTRA_GAP = 2  # 同一类型多行换行间距

    palette = _get_theme(theme)
    color_divider = _mix_color(palette.gradient_bottom, palette.border, 0.16)
    color_main = palette.text_primary
    color_trans = palette.accent
    color_roma = palette.text_tertiary
    color_time = palette.accent
    color_meta = palette.text_tertiary

    sort_order = ("roma", "main", "trans")
    has_roma = any("roma" in g.lrc for g in groups)
    has_trans = any("trans" in g.lrc for g in groups)

    font_header_title = _get_font(28)
    font_header_artist = _get_font(16)
    font_main = _get_font(22)
    font_trans = _get_font(16)
    font_roma = _get_font(14)
    font_time = _get_font(13)
    font_meta = _get_font(12)
    font_footer = _get_font(12)

    def _measure_lines(draw: ImageDraw.Draw, group: "NCMLrcGroupLine") -> list[tuple[str, str, int]]:
        rows: list[tuple[str, str, int]] = []
        sorted_items = sorted(
            group.lrc.items(),
            key=lambda x: sort_order.index(x[0]) if x[0] in sort_order else 999,
        )
        for name, text in sorted_items:
            if name == "meta":
                rows.append(("meta", text, 0))
            elif name == "roma":
                rows.append(("roma", text, 22))
            elif name == "main":
                rows.append(("main", text, 30))
            elif name == "trans":
                rows.append(("trans", text, 24))
        return rows

    def _wrap_text(draw: ImageDraw.Draw, text: str, font, max_w: int) -> list[str]:
        if not text:
            return [""]
        lines: list[str] = []
        buf = ""
        for ch in text:
            buf += ch
            bbox = draw.textbbox((0, 0), buf, font=font)
            if bbox[2] - bbox[0] > max_w:
                buf = buf[:-1]
                if buf:
                    lines.append(buf)
                buf = ch
        if buf:
            lines.append(buf)
        return lines or [""]

    def _row_height(rows: list[tuple[str, str, int]], draw: ImageDraw.Draw) -> int:
        """计算一组 group 的总高度"""
        h = 0
        prev_kind = None
        for kind, text, base_h in rows:
            if kind == "meta":
                continue
            if prev_kind is not None and prev_kind != kind:
                h += LRC_INTER_GAP
            if kind == "main":
                wrapped = _wrap_text(draw, text, font_main, main_max_w - 66)
                h += base_h * len(wrapped) + (len(wrapped) - 1) * LRC_INTRA_GAP
            elif kind == "trans":
                wrapped = _wrap_text(draw, text, font_trans, trans_max_w - 66)
                h += base_h * len(wrapped) + (len(wrapped) - 1) * LRC_INTRA_GAP
            elif kind == "roma":
                wrapped = _wrap_text(draw, text, font_roma, roma_max_w - 66)
                h += base_h * len(wrapped) + (len(wrapped) - 1) * LRC_INTRA_GAP
            prev_kind = kind
        return h

    # ===== 计算布局 =====
    probe = Image.new("RGB", (LRC_IMG_WIDTH, 1), palette.gradient_bottom)
    probe_draw = ImageDraw.Draw(probe)

    main_max_w = LRC_IMG_WIDTH - LRC_PADDING_X * 2 - 60
    trans_max_w = LRC_IMG_WIDTH - LRC_PADDING_X * 2 - 24
    roma_max_w = LRC_IMG_WIDTH - LRC_PADDING_X * 2 - 24

    def _is_placeholder(group: "NCMLrcGroupLine") -> bool:
        """是否是无意义的占位行（仅含一个 '-' main 行）"""
        return (
            len(group.lrc) == 1
            and "main" in group.lrc
            and group.lrc["main"].strip() in ("-", "")
        )

    body_h = 0
    prev_was_meta = False
    for i, group in enumerate(groups):
        if _is_placeholder(group):
            continue
        rows = _measure_lines(probe_draw, group)
        if not rows:
            continue
        is_meta = len(rows) == 1 and rows[0][0] == "meta"
        if is_meta:
            continue
        if i > 0 and prev_was_meta:
            body_h += LRC_GROUP_GAP // 2
        body_h += _row_height(rows, probe_draw)
        if i < len(groups) - 1:
            body_h += LRC_GROUP_GAP
        prev_was_meta = False

    total_h = LRC_HEADER_H + LRC_PADDING_Y + body_h + LRC_PADDING_Y + LRC_FOOTER_H
    total_h = max(total_h, 320)

    img = _new_surface(LRC_IMG_WIDTH, total_h, palette)
    _draw_glass_panel(
        img, [8, 8, LRC_IMG_WIDTH - 8, total_h - 8], 30, palette,
        fill_alpha=4, border_alpha=palette.border_alpha,
    )
    _draw_glass_panel(
        img, [16, 16, LRC_IMG_WIDTH - 16, LRC_HEADER_H + 10], 20, palette,
        fill_alpha=palette.frost_alpha + 3,
    )
    draw = ImageDraw.Draw(img)

    # ===== 顶栏：rika-style glass panel =====
    # Keep the accent bar above the title so it cannot collide with glyphs.
    draw.rounded_rectangle([LRC_PADDING_X + 18, 22, LRC_PADDING_X + 76, 28], radius=3, fill=palette.accent)

    # 标题与艺人
    title_text = (info.display_name if info and info.display_name else "歌词")
    artist_text = (info.display_artists if info and info.display_artists else "")

    # 标题左侧三个点状装饰（避免使用 ♪ 字符以兼容字体）
    for i, dy in enumerate([0, 8, 16]):
        draw.ellipse(
            [(LRC_PADDING_X, 42 + dy),
             (LRC_PADDING_X + 6, 42 + dy + 6)],
            fill=palette.accent,
        )

    max_title_w = LRC_IMG_WIDTH - LRC_PADDING_X * 2 - 28
    title_bbox = draw.textbbox((0, 0), title_text, font=font_header_title)
    if title_bbox[2] - title_bbox[0] > max_title_w:
        title_text = _truncate_text(draw, title_text, font_header_title, max_title_w)
    draw.text(
        (LRC_PADDING_X + 18, 36),
        title_text, font=font_header_title, fill=palette.text_primary,
    )

    if artist_text:
        artist_display = f"by  {artist_text}"
        if has_roma or has_trans:
            tags = []
            if has_trans:
                tags.append("译")
            if has_roma:
                tags.append("音")
            if tags:
                artist_display += f"   [{'/'.join(tags)}]"
        draw.text(
            (LRC_PADDING_X + 18, 76),
            artist_display, font=font_header_artist, fill=palette.text_secondary,
        )

    # 双重分隔线
    draw.line(
        [(LRC_PADDING_X, LRC_HEADER_H - 2), (LRC_IMG_WIDTH - LRC_PADDING_X, LRC_HEADER_H - 2)],
        fill=color_divider, width=1,
    )
    draw.line(
        [(LRC_PADDING_X, LRC_HEADER_H), (LRC_IMG_WIDTH - LRC_PADDING_X, LRC_HEADER_H)],
        fill=color_divider, width=1,
    )

    # ===== 正文 =====
    y = LRC_HEADER_H + LRC_PADDING_Y
    prev_was_meta = False

    for i, group in enumerate(groups):
        if _is_placeholder(group):
            continue
        rows = _measure_lines(draw, group)
        if not rows:
            continue

        is_meta = len(rows) == 1 and rows[0][0] == "meta"
        if is_meta:
            prev_was_meta = True
            continue

        if i > 0 and prev_was_meta:
            y += LRC_GROUP_GAP // 2
        prev_was_meta = False

        # 时间戳和竖条
        time_str = _format_time(group.time)
        time_x = LRC_PADDING_X

        first_main_idx = next(
            (idx for idx, (k, _, _) in enumerate(rows) if k == "main"), 0
        )
        # 计算 first_main 之前的累积高度
        cum_h = 0
        prev_kind_for_first = None
        for k, t, bh in rows[:first_main_idx]:
            if prev_kind_for_first is not None and prev_kind_for_first != k:
                cum_h += LRC_INTER_GAP
            if k == "roma":
                cum_h += 22
            elif k == "trans":
                cum_h += 24
            prev_kind_for_first = k
        first_main_top = y + cum_h

        time_text_y = first_main_top + (30 - 16) // 2
        if time_str:
            draw.text(
                (time_x, time_text_y),
                time_str, font=font_time, fill=color_time,
            )

        # 竖条
        bar_x = time_x + 52
        # 竖条覆盖整组高度
        group_h = _row_height(rows, draw)
        draw.line(
            [(bar_x, first_main_top + 4), (bar_x, first_main_top + min(group_h, 40) - 4)],
            fill=color_divider, width=2,
        )

        # 绘制各行
        text_x = bar_x + 14
        prev_kind = None
        for kind, text, base_h in rows:
            if prev_kind is not None and prev_kind != kind:
                y += LRC_INTER_GAP
            if kind == "main":
                wrapped = _wrap_text(draw, text, font_main, main_max_w - 66)
                for line in wrapped:
                    draw.text(
                        (text_x, y),
                        line, font=font_main, fill=color_main,
                    )
                    y += 30
                if len(wrapped) > 1:
                    y += LRC_INTRA_GAP * (len(wrapped) - 1)
            elif kind == "trans":
                wrapped = _wrap_text(draw, text, font_trans, trans_max_w - 66)
                for line in wrapped:
                    draw.text(
                        (text_x + 8, y),
                        line, font=font_trans, fill=color_trans,
                    )
                    y += 24
                if len(wrapped) > 1:
                    y += LRC_INTRA_GAP * (len(wrapped) - 1)
            elif kind == "roma":
                wrapped = _wrap_text(draw, text, font_roma, roma_max_w - 66)
                for line in wrapped:
                    draw.text(
                        (text_x + 8, y),
                        line, font=font_roma, fill=color_roma,
                    )
                    y += 22
                if len(wrapped) > 1:
                    y += LRC_INTRA_GAP * (len(wrapped) - 1)
            prev_kind = kind

        # 段间虚线（仅当前后都非 meta 且不是最后一段）
        next_is_meta = False
        if i < len(groups) - 1:
            next_rows = _measure_lines(draw, groups[i + 1])
            next_is_meta = len(next_rows) == 1 and next_rows[0][0] == "meta"
        if i < len(groups) - 1 and not next_is_meta:
            sep_y = y + LRC_GROUP_GAP // 2
            # 居中点装饰（更优雅的段间分隔）
            mid_x = LRC_IMG_WIDTH // 2
            draw.line(
                [(mid_x - 28, sep_y), (mid_x - 8, sep_y)],
                fill=color_divider, width=1,
            )
            draw.line(
                [(mid_x + 8, sep_y), (mid_x + 28, sep_y)],
                fill=color_divider, width=1,
            )
            # 中间小菱形
            draw.polygon(
                [(mid_x, sep_y - 3), (mid_x + 3, sep_y), (mid_x, sep_y + 3), (mid_x - 3, sep_y)],
                fill=color_divider,
            )
            y += LRC_GROUP_GAP

    # ===== 底栏 =====
    footer_y = total_h - LRC_FOOTER_H
    draw.line(
        [(LRC_PADDING_X, footer_y), (LRC_IMG_WIDTH - LRC_PADDING_X, footer_y)],
        fill=color_divider, width=1,
    )

    meta_lines = [
        text
        for g in groups
        for k, text in g.lrc.items()
        if k == "meta" and ("贡献者" in text or "贡献" in text)
    ]
    if meta_lines:
        meta_text = "  ·  ".join(meta_lines)
        meta_bbox = draw.textbbox((0, 0), meta_text, font=font_meta)
        if meta_bbox[2] - meta_bbox[0] > LRC_IMG_WIDTH - LRC_PADDING_X * 2 - 200:
            meta_text = _truncate_text(
                draw, meta_text, font_meta,
                LRC_IMG_WIDTH - LRC_PADDING_X * 2 - 200,
            )
        draw.text(
            (LRC_PADDING_X, footer_y + 10),
            meta_text, font=font_meta, fill=color_meta,
        )

    footer_text = "astrbot-plugin-multincm"
    fbbox = draw.textbbox((0, 0), footer_text, font=font_footer)
    fw = fbbox[2] - fbbox[0]
    draw.text(
        (LRC_IMG_WIDTH - LRC_PADDING_X - fw, footer_y + 20),
        footer_text, font=font_footer, fill=color_meta,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _format_time(ms: int) -> str:
    """毫秒 → mm:ss（meta 行的 5940000 ms 不显示）"""
    if ms >= 5940000:
        return ""
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"
