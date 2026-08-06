"""PIL 图片渲染 - 搜索列表 & 歌词（替代 Jinja2+Playwright）"""
import asyncio
import io
import os
import platform
import sys
from typing import TYPE_CHECKING

import aiohttp
from PIL import Image, ImageDraw, ImageFont

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
COLOR_BG = (36, 39, 58)        # #24273a
COLOR_BG_CARD = (30, 32, 48)   # #1e2030
COLOR_FG_PRI = (165, 173, 203) # #a5adcb
COLOR_FG_SEC = (128, 135, 162) # #8087a2
COLOR_INDEX_BG = (100, 140, 210)  # 实心蓝（PIL RGB模式不支持alpha）
COLOR_ACCENT = (138, 173, 244) # #8aadf4

# 歌词专用配色
COLOR_LRC_BG_TOP = (54, 58, 84)      # 顶栏渐变深色
COLOR_LRC_BG_BOT = (36, 39, 58)      # 顶栏渐变深色（与底色融合）
COLOR_LRC_DIVIDER = (68, 73, 100)    # 分隔线
COLOR_LRC_MAIN = (220, 224, 240)     # 主歌词（高亮白）
COLOR_LRC_TRANS = (138, 173, 244)    # 翻译（强调蓝）
COLOR_LRC_ROMA = (160, 165, 190)     # 罗马音
COLOR_LRC_TIME = (200, 130, 130)     # 时间戳（暖色点缀）
COLOR_LRC_META = (110, 115, 140)     # meta 元信息

IMG_WIDTH = 780
PADDING = 20
COLS = 2  # 双列布局（与原项目一致）
COL_GAP = 16  # 列间距
ROW_GAP = 16  # 行间距
CARD_INNER_PAD = 16  # 卡片内边距
COVER_SIZE = 64
CARD_W = (IMG_WIDTH - PADDING * 2 - (COLS - 1) * COL_GAP) // COLS  # 单张卡片宽度


async def render_search_list(
    page: "GeneralSongListPage",
    cards: list["ListPageCard"],
    limit: int = 20,
) -> bytes:
    """渲染搜索结果列表图片（双列网格布局）"""
    # 限制渲染条目数
    cards = cards[:limit]

    font_title = _get_font(32)
    font_tip = _get_font(14)
    font_song = _get_font(18)
    font_bold_song = _get_font(18)
    font_info = _get_font(14)
    font_small = _get_font(12)
    font_footer = _get_font(12)

    total_count = page.father.total_count
    current_page = page.father.current_page
    max_page = page.father.max_page
    title = f"{page.father.child_calling}列表"

    # 计算行数和图片高度
    rows = (len(cards) + COLS - 1) // COLS if cards else 1
    header_h = 80  # 标题 + 提示
    footer_h = 70  # 页码 + footer
    card_h = COVER_SIZE + CARD_INNER_PAD * 2  # 单张卡片高度

    total_h = PADDING * 2 + header_h + rows * card_h + (rows - 1) * ROW_GAP + footer_h

    img = Image.new("RGB", (IMG_WIDTH, total_h), COLOR_BG)
    draw = ImageDraw.Draw(img)

    y = PADDING

    # 标题
    bbox = draw.textbbox((0, 0), title, font=font_title)
    tw = bbox[2] - bbox[0]
    draw.text(((IMG_WIDTH - tw) // 2, y), title, font=font_title, fill=COLOR_ACCENT)
    y += 40

    # 提示文字
    tip = "发送序号选择 | P+数字跳页 | 上一页(P) | 下一页(N) | 退出(E)"
    bbox_tip = draw.textbbox((0, 0), tip, font=font_tip)
    tw_tip = bbox_tip[2] - bbox_tip[0]
    draw.text(((IMG_WIDTH - tw_tip) // 2, y), tip, font=font_tip, fill=COLOR_FG_SEC)
    y += 20

    # 页面信息
    page_info = f"第 {current_page} 页 / 共 {max_page} 页 | 总计 {total_count} 项"
    bbox_page = draw.textbbox((0, 0), page_info, font=font_tip)
    tw_page = bbox_page[2] - bbox_page[0]
    draw.text(((IMG_WIDTH - tw_page) // 2, y), page_info, font=font_tip, fill=COLOR_FG_SEC)
    y += 20

    # 下载封面（异步）
    covers: dict[int, Image.Image | None] = {}

    async def _fetch_cover(idx: int, url: str):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        covers[idx] = Image.open(io.BytesIO(data)).resize(
                            (COVER_SIZE, COVER_SIZE), Image.Resampling.LANCZOS
                        )
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

        # 1. 卡片背景（先画背景）
        draw.rounded_rectangle(
            [card_x, card_y, card_x + CARD_W, card_y + card_h],
            radius=8,
            fill=COLOR_BG_CARD,
        )

        # 2. 左上角三角序号（在内容之前绘制，确保层级正确）
        idx_text = str(i + 1)
        idx_font = _get_font(14)  # 稍大一点确保可见
        tri_size = 32
        # 画三角形（实心，RGB模式）
        triangle = [
            (card_x + tri_size, card_y),
            (card_x, card_y),
            (card_x, card_y + tri_size),
        ]
        draw.polygon(triangle, fill=COLOR_INDEX_BG)

        # 序号文字：居中于三角形内部
        idx_bbox = draw.textbbox((0, 0), idx_text, font=idx_font)
        idx_w = idx_bbox[2] - idx_bbox[0]
        idx_h = idx_bbox[3] - idx_bbox[1]
        # 三角形中心约在 (tri_size*0.28, tri_size*0.50)，调整偏移使文字居中
        idx_text_x = card_x + 6
        idx_text_y = card_y + 6
        draw.text(
            (idx_text_x, idx_text_y),
            idx_text, font=idx_font, fill=(255, 255, 255),  # 白色文字确保对比度
        )

        # 3. 内部布局：封面 + 文字水平排列（与原项目 .inner flex-row 一致）
        inner_x = card_x + CARD_INNER_PAD
        inner_y = card_y + CARD_INNER_PAD

        # 封面
        cover_x = inner_x
        cover_y_pos = inner_y
        if i in covers and covers[i]:
            img.paste(covers[i], (cover_x, cover_y_pos))
        else:
            draw.rounded_rectangle(
                [cover_x, cover_y_pos, cover_x + COVER_SIZE, cover_y_pos + COVER_SIZE],
                radius=6,
                fill=(60, 63, 80),
            )

        # 文字区域
        text_x = cover_x + COVER_SIZE + 16
        text_y = cover_y_pos
        max_text_width = CARD_W - CARD_INNER_PAD * 2 - COVER_SIZE - 16

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
                draw.text((text_x, text_y), main_title, font=font_song, fill=COLOR_FG_PRI)
                title_bbox2 = draw.textbbox((0, 0), main_title, font=font_song)
                draw.text(
                    (text_x + (title_bbox2[2] - title_bbox2[0]), text_y),
                    alias_text, font=font_small, fill=COLOR_FG_SEC,
                )
            else:
                combined = main_title
                draw.text((text_x, text_y), combined, font=font_song, fill=COLOR_FG_PRI)
            text_y += 24
        else:
            title_text = _truncate_text(draw, title_text, font_song, max_text_width)
            draw.text((text_x, text_y), title_text, font=font_song, fill=COLOR_FG_PRI)
            text_y += 24

        # 附加信息（extras）
        for extra in card.extras:
            extra = _truncate_text(draw, extra, font_info, max_text_width)
            draw.text((text_x, text_y), extra, font=font_info, fill=COLOR_FG_PRI)
            text_y += 18

        # 附加小信息（small_extras）
        for extra in card.small_extras:
            extra = _truncate_text(draw, extra, font_small, max_text_width)
            draw.text((text_x, text_y), extra, font=font_small, fill=COLOR_FG_SEC)
            text_y += 16

    # 底部
    y_bottom = y + rows * card_h + (rows - 1) * ROW_GAP + 12
    footer_text = "Generated by astrbot-plugin-multincm"
    fbbox = draw.textbbox((0, 0), footer_text, font=font_footer)
    fw = fbbox[2] - fbbox[0]
    draw.text(((IMG_WIDTH - fw) // 2, y_bottom), footer_text, font=font_footer, fill=COLOR_FG_SEC)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
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
    probe = Image.new("RGB", (LRC_IMG_WIDTH, 1), COLOR_BG)
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

    img = Image.new("RGB", (LRC_IMG_WIDTH, total_h), COLOR_BG)
    draw = ImageDraw.Draw(img)

    # ===== 顶栏：渐变背景 =====
    for y_off in range(LRC_HEADER_H):
        t = y_off / max(LRC_HEADER_H - 1, 1)
        r = int(COLOR_LRC_BG_TOP[0] * (1 - t) + COLOR_LRC_BG_BOT[0] * t)
        g = int(COLOR_LRC_BG_TOP[1] * (1 - t) + COLOR_LRC_BG_BOT[1] * t)
        b = int(COLOR_LRC_BG_TOP[2] * (1 - t) + COLOR_LRC_BG_BOT[2] * t)
        draw.line([(0, y_off), (LRC_IMG_WIDTH, y_off)], fill=(r, g, b))

    # 左侧装饰条
    draw.rectangle([0, 0, 6, LRC_HEADER_H], fill=COLOR_ACCENT)

    # 标题与艺人
    title_text = (info.display_name if info and info.display_name else "歌词")
    artist_text = (info.display_artists if info and info.display_artists else "")

    # 标题左侧三个点状装饰（避免使用 ♪ 字符以兼容字体）
    for i, dy in enumerate([0, 8, 16]):
        draw.ellipse(
            [(LRC_PADDING_X, 36 + dy),
             (LRC_PADDING_X + 6, 36 + dy + 6)],
            fill=COLOR_ACCENT,
        )

    max_title_w = LRC_IMG_WIDTH - LRC_PADDING_X * 2 - 28
    title_bbox = draw.textbbox((0, 0), title_text, font=font_header_title)
    if title_bbox[2] - title_bbox[0] > max_title_w:
        title_text = _truncate_text(draw, title_text, font_header_title, max_title_w)
    draw.text(
        (LRC_PADDING_X + 18, 24),
        title_text, font=font_header_title, fill=COLOR_FG_PRI,
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
            (LRC_PADDING_X + 18, 68),
            artist_display, font=font_header_artist, fill=COLOR_FG_SEC,
        )

    # 双重分隔线
    draw.line(
        [(LRC_PADDING_X, LRC_HEADER_H - 2), (LRC_IMG_WIDTH - LRC_PADDING_X, LRC_HEADER_H - 2)],
        fill=COLOR_LRC_DIVIDER, width=1,
    )
    draw.line(
        [(LRC_PADDING_X, LRC_HEADER_H), (LRC_IMG_WIDTH - LRC_PADDING_X, LRC_HEADER_H)],
        fill=COLOR_LRC_DIVIDER, width=1,
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
                time_str, font=font_time, fill=COLOR_LRC_TIME,
            )

        # 竖条
        bar_x = time_x + 52
        # 竖条覆盖整组高度
        group_h = _row_height(rows, draw)
        draw.line(
            [(bar_x, first_main_top + 4), (bar_x, first_main_top + min(group_h, 40) - 4)],
            fill=COLOR_LRC_DIVIDER, width=2,
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
                        line, font=font_main, fill=COLOR_LRC_MAIN,
                    )
                    y += 30
                if len(wrapped) > 1:
                    y += LRC_INTRA_GAP * (len(wrapped) - 1)
            elif kind == "trans":
                wrapped = _wrap_text(draw, text, font_trans, trans_max_w - 66)
                for line in wrapped:
                    draw.text(
                        (text_x + 8, y),
                        line, font=font_trans, fill=COLOR_LRC_TRANS,
                    )
                    y += 24
                if len(wrapped) > 1:
                    y += LRC_INTRA_GAP * (len(wrapped) - 1)
            elif kind == "roma":
                wrapped = _wrap_text(draw, text, font_roma, roma_max_w - 66)
                for line in wrapped:
                    draw.text(
                        (text_x + 8, y),
                        line, font=font_roma, fill=COLOR_LRC_ROMA,
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
                fill=COLOR_LRC_DIVIDER, width=1,
            )
            draw.line(
                [(mid_x + 8, sep_y), (mid_x + 28, sep_y)],
                fill=COLOR_LRC_DIVIDER, width=1,
            )
            # 中间小菱形
            draw.polygon(
                [(mid_x, sep_y - 3), (mid_x + 3, sep_y), (mid_x, sep_y + 3), (mid_x - 3, sep_y)],
                fill=COLOR_LRC_DIVIDER,
            )
            y += LRC_GROUP_GAP

    # ===== 底栏 =====
    footer_y = total_h - LRC_FOOTER_H
    draw.line(
        [(LRC_PADDING_X, footer_y), (LRC_IMG_WIDTH - LRC_PADDING_X, footer_y)],
        fill=COLOR_LRC_DIVIDER, width=1,
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
            meta_text, font=font_meta, fill=COLOR_LRC_META,
        )

    footer_text = "astrbot-plugin-multincm"
    fbbox = draw.textbbox((0, 0), footer_text, font=font_footer)
    fw = fbbox[2] - fbbox[0]
    draw.text(
        (LRC_IMG_WIDTH - LRC_PADDING_X - fw, footer_y + 20),
        footer_text, font=font_footer, fill=COLOR_LRC_META,
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
