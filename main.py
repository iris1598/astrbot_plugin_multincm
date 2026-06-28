"""AstrBot 网易云多选点歌插件 - 主入口"""
import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import BaseMessageComponent, ComponentType
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp
from astrbot.core.utils.session_waiter import session_waiter, SessionController

from .data_source import (
    BasePlaylist,
    BaseSearcher,
    BaseSong,
    BaseSongListPage,
    GeneralGetPageReturn,
    GeneralSongOrList,
    GeneralSongOrPlaylist,
    GeneralSongList,
    GeneralSongListPage,
    SongInfo,
    PlaylistInfo,
    registered_searcher,
    registered_song,
    registered_playlist,
    resolve_from_link_params,
    Song,
    SongSearcher,
    AlbumSearcher,
    PlaylistSearcher,
    ProgramSearcher,
    RadioSearcher,
)
from .api import NCMResponseError
from .renderer import render_search_list, render_lyrics
from .utils import build_item_link, download_file, format_alias, safe_filename

# 全局配置字典（供子模块读取）
_config: dict = {}

# 数据缓存目录
_CACHE_DIR = Path.cwd() / "data" / "plugin_data" / "astrbot_plugin_multincm" / "songs"

# URL 解析正则（支持多种网易云链接格式）
# 格式: music.163.com/(#/)?(path/)?type?id=xxx 或 music.163.com/(#/)?type/xxx
URL_REGEX = r"music\.163\.com/(#/)?(.*?)(?P<type>[a-zA-Z]+)(/?\?id=|/)(?P<id>[0-9]+)/?&?"
SHORT_URL_BASE = "https://163cn.tv"
SHORT_URL_REGEX = r"163cn\.tv/(?P<suffix>[a-zA-Z0-9]+)"

# 搜索交互命令 (字符串集合用于快速匹配，正则用于 session_waiter 中)
EXIT_COMMANDS = {"退出", "tc", "取消", "qx", "quit", "q", "Q", "exit", "e", "E", "cancel", "c", "0"}
PREVIOUS_COMMANDS = {"上一页", "syy", "previous", "p", "P"}
NEXT_COMMANDS = {"下一页", "xyy", "next", "n", "N"}

# 各指令的正则（session_waiter 内部使用）
EXIT_REGEX = re.compile(r"^(退出|tc|取消|qx|quit|q|Q|exit|e|E|cancel|c|0)$")
PREV_REGEX = re.compile(r"^(上一页|syy|previous|p|P)$")
NEXT_REGEX = re.compile(r"^(下一页|xyy|next|n|N)$")
JUMP_REGEX = re.compile(r"^(page|p|跳页|页)\s*(\d+)$", re.IGNORECASE)
DIGIT_REGEX = re.compile(r"^\d+$")


class MusicCardComponent(BaseMessageComponent):
    """自定义音乐卡片组件。

    注意：AstrBot 内置的 Comp.Music 组件在 Pydantic v1 下 _type 字段会被当作
    私有属性忽略，导致序列化后丢失 type 字段。本组件通过非 _ 前缀字段绕过此限制，
    并重写 toDict() 直接输出正确的 OneBot 11 music 消息段格式，
    兼容 LuckyLilliaBot (LLOneBot) 等 OneBot 协议端。

    LLOneBot 收到后会自动调用其配置的 musicSignUrl 签名服务完成签名。
    """

    type: ComponentType = ComponentType.Music
    music_type: str = "163"
    song_id: int = 0
    url: str = ""
    audio: str = ""
    title: str = ""
    content: str = ""
    image: str = ""

    def toDict(self) -> dict:
        """生成标准的 OneBot 11 music 消息段。"""
        data: dict[str, object] = {"type": self.music_type}
        # ID 方式
        if self.music_type != "custom" and self.song_id:
            data["id"] = self.song_id
        # 自定义方式
        if self.url:
            data["url"] = self.url
        if self.audio:
            data["audio"] = self.audio
        if self.title:
            data["title"] = self.title
        if self.content:
            data["content"] = self.content
        if self.image:
            data["image"] = self.image
        return {"type": "music", "data": data}


@register("multincm", "lgc-NB2Dev", "网易云多选点歌", "1.0.0", "https://github.com/lgc-NB2Dev/nonebot-plugin-multincm")
class Main(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)

        # 加载配置
        global _config
        self.config = config or {}
        _config = self.config  # 设置全局配置供子模块使用

        # 读取 schema 默认值
        schema_path = os.path.join(os.path.dirname(__file__), "_conf_schema.json")
        schema_defaults = {}
        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                schema = json.load(f)
                for key, value in schema.items():
                    schema_defaults[key] = value.get("default")
        except Exception:
            pass

        # 合并配置
        self.cookie_music_u = self.config.get("cookie_music_u", schema_defaults.get("cookie_music_u", ""))
        self.list_limit = self.config.get("list_limit", schema_defaults.get("list_limit", 20))
        self.send_as_file = self.config.get("send_as_file", schema_defaults.get("send_as_file", False))
        self.auto_resolve = self.config.get("auto_resolve", schema_defaults.get("auto_resolve", False))
        self.ffmpeg_executable = self.config.get("ffmpeg_executable", schema_defaults.get("ffmpeg_executable", "ffmpeg"))

        # 会话超时配置
        self.session_timeout = self.config.get("session_timeout", schema_defaults.get("session_timeout", 120))

        # 音乐卡片配置（启用后使用 Comp.Music 组件发送，由适配的 OneBot 协议端自行签名）
        self.use_music_card = self.config.get("use_music_card", schema_defaults.get("use_music_card", False))

        # 更新全局配置
        _config.update({
            "cookie_music_u": self.cookie_music_u,
            "list_limit": self.list_limit,
            "send_as_file": self.send_as_file,
            "auto_resolve": self.auto_resolve,
            "ffmpeg_executable": self.ffmpeg_executable,
            "session_timeout": self.session_timeout,
            "phone": self.config.get("phone", ""),
            "email": self.config.get("email", ""),
            "password": self.config.get("password", ""),
            "anonymous": self.config.get("anonymous", False),
        })

        # 搜索会话存储：{session_id: SongListSearchSession}
        self.search_sessions: dict[str, "SearchSession"] = {}

        # 登录
        asyncio.create_task(self._do_login())

    async def _do_login(self):
        """执行登录"""
        try:
            from .login import login
            await login()
        except Exception as e:
            logger.error(f"网易云登录失败: {e}")

    def _get_session_id(self, event: AstrMessageEvent) -> str:
        """获取会话ID"""
        return event.session_id

    def _get_platform(self, event: AstrMessageEvent) -> str:
        """获取当前平台"""
        try:
            if hasattr(event, "platform"):
                platform = event.platform
                if hasattr(platform, "name"):
                    return platform.name.lower()
                return str(platform).lower()
        except Exception:
            pass
        try:
            if hasattr(event, "message_obj") and hasattr(event.message_obj, "platform"):
                platform = event.message_obj.platform
                if hasattr(platform, "name"):
                    return platform.name.lower()
                return str(platform).lower()
        except Exception:
            pass
        return "qq"

    # ==================== 搜索命令 ====================

    @filter.regex(r"^(点歌|网易云|wyy|网易点歌|wydg|wysong)\s*(.*)$")
    async def search_song(self, event: AstrMessageEvent):
        """搜索歌曲"""
        match = re.match(r"^(点歌|网易云|wyy|网易点歌|wydg|wysong)\s*(.*)$", event.message_str)
        if not match:
            return
        keyword = match.group(2).strip()
        if not keyword:
            yield event.plain_result("请输入搜索内容，例如：点歌 Lemon")
            return
        yield event.chain_result([Comp.Plain("🔍 搜索中，请稍等...")])
        async for result in self._handle_search(event, SongSearcher, keyword):
            yield result
        # 若搜索结果有多项，启动交互会话
        if self._get_session_id(event) in self.search_sessions:
            async for result in self._start_interaction(event):
                yield result

    @filter.regex(r"^(网易专辑|wyzj|wyal)\s*(.*)$")
    async def search_album(self, event: AstrMessageEvent):
        """搜索专辑"""
        match = re.match(r"^(网易专辑|wyzj|wyal)\s*(.*)$", event.message_str)
        if not match:
            return
        keyword = match.group(2).strip()
        if not keyword:
            yield event.plain_result("请输入搜索内容")
            return
        yield event.chain_result([Comp.Plain("🔍 搜索中，请稍等...")])
        async for result in self._handle_search(event, AlbumSearcher, keyword):
            yield result
        if self._get_session_id(event) in self.search_sessions:
            async for result in self._start_interaction(event):
                yield result

    @filter.regex(r"^(网易歌单|wygd|wypli)\s*(.*)$")
    async def search_playlist(self, event: AstrMessageEvent):
        """搜索歌单"""
        match = re.match(r"^(网易歌单|wygd|wypli)\s*(.*)$", event.message_str)
        if not match:
            return
        keyword = match.group(2).strip()
        if not keyword:
            yield event.plain_result("请输入搜索内容")
            return
        yield event.chain_result([Comp.Plain("🔍 搜索中，请稍等...")])
        async for result in self._handle_search(event, PlaylistSearcher, keyword):
            yield result
        if self._get_session_id(event) in self.search_sessions:
            async for result in self._start_interaction(event):
                yield result

    @filter.regex(r"^(网易声音|wysy|wyprog)\s*(.*)$")
    async def search_program(self, event: AstrMessageEvent):
        """搜索电台节目"""
        match = re.match(r"^(网易声音|wysy|wyprog)\s*(.*)$", event.message_str)
        if not match:
            return
        keyword = match.group(2).strip()
        if not keyword:
            yield event.plain_result("请输入搜索内容")
            return
        yield event.chain_result([Comp.Plain("🔍 搜索中，请稍等...")])
        async for result in self._handle_search(event, ProgramSearcher, keyword):
            yield result
        if self._get_session_id(event) in self.search_sessions:
            async for result in self._start_interaction(event):
                yield result

    @filter.regex(r"^(网易电台|wydt|wydj)\s*(.*)$")
    async def search_radio(self, event: AstrMessageEvent):
        """搜索电台"""
        match = re.match(r"^(网易电台|wydt|wydj)\s*(.*)$", event.message_str)
        if not match:
            return
        keyword = match.group(2).strip()
        if not keyword:
            yield event.plain_result("请输入搜索内容")
            return
        yield event.chain_result([Comp.Plain("🔍 搜索中，请稍等...")])
        async for result in self._handle_search(event, RadioSearcher, keyword):
            yield result
        if self._get_session_id(event) in self.search_sessions:
            async for result in self._start_interaction(event):
                yield result

    async def _handle_search(self, event: AstrMessageEvent, searcher_cls: type[BaseSearcher], keyword: str):
        """处理搜索逻辑"""
        try:
            searcher = searcher_cls(keyword)
            result = await searcher.get_page()
        except Exception as e:
            logger.error(f"搜索出错: {e}")
            yield event.plain_result(f"搜索出错: {e}")
            return

        if result is None:
            yield event.plain_result("没有搜索到任何内容")
            return

        if isinstance(result, BaseSong):
            # 只有一个结果，直接发送
            async for r in self._send_song(event, result):
                yield r
            return

        # 多个结果，显示列表
        if not isinstance(result, BaseSongListPage):
            yield event.plain_result("搜索结果异常")
            return

        # 渲染列表图片
        try:
            cards = await result.transform_to_list_cards()
            img_bytes = await render_search_list(result, cards, limit=self.list_limit)
        except Exception as e:
            logger.error(f"渲染搜索列表失败: {e}")
            yield event.plain_result("图片渲染失败，请检查后台日志")
            return

        # 存储搜索会话
        session_id = self._get_session_id(event)
        self.search_sessions[session_id] = SearchSession(
            searcher=searcher,
            song_list=result.father,
            message_id=self._get_message_id(event),
        )

        # 发送搜索结果
        info_text = (
            f"🎵 搜索: {keyword} | 回复序号选择\n"
            f"上一页: P  |  下一页: N  |  跳页: P+数字  |  退出: E/0\n"
            f"⏰ {self.session_timeout}秒无操作自动退出"
        )
        yield event.chain_result([
            Comp.Plain(info_text),
            Comp.Image.fromBytes(img_bytes),
        ])

    # ==================== 交互会话（session_waiter） ====================

    async def _start_interaction(self, event: AstrMessageEvent):
        """启动交互式选择会话。

        使用 AstrBot 的 session_waiter 机制代替 @filter.regex 多 handler 模式，
        避免命令冲突（如 P 同时匹配"上一页"和"跳页"），并提供超时自动退出功能。
        """
        session_id = self._get_session_id(event)
        timeout = self.session_timeout

        @session_waiter(timeout=timeout, record_history_chains=False)
        async def _waiter(controller: SessionController, ev: AstrMessageEvent):
            msg = ev.message_str.strip()
            sess = self.search_sessions.get(session_id)

            # ===== 退出命令 =====
            if EXIT_REGEX.match(msg):
                if sess:
                    del self.search_sessions[session_id]
                await ev.send(ev.plain_result("已退出选择模式"))
                controller.stop()
                return

            # ===== 无会话则静默停止 =====
            if not sess:
                controller.stop()
                return

            # ===== 跳页（必须优先于上一页检查，因为 "P 5" 也以 P 开头） =====
            jump_match = JUMP_REGEX.match(msg)
            if jump_match:
                page = int(jump_match.group(2))
                if not sess.song_list.page_valid(page):
                    await ev.send(ev.plain_result("页码无效"))
                    controller.keep(timeout=timeout, reset_timeout=True)
                    return
                sess.song_list.current_page = page
                async for r in self._show_current_page(ev, sess):
                    await ev.send(r)
                controller.keep(timeout=timeout, reset_timeout=True)
                return

            # ===== 上一页 =====
            if PREV_REGEX.match(msg):
                if sess.song_list.is_first_page:
                    await ev.send(ev.plain_result("已经是第一页了"))
                else:
                    sess.song_list.current_page -= 1
                    async for r in self._show_current_page(ev, sess):
                        await ev.send(r)
                controller.keep(timeout=timeout, reset_timeout=True)
                return

            # ===== 下一页 =====
            if NEXT_REGEX.match(msg):
                if sess.song_list.is_last_page:
                    await ev.send(ev.plain_result("已经是最后一页了"))
                else:
                    sess.song_list.current_page += 1
                    async for r in self._show_current_page(ev, sess):
                        await ev.send(r)
                controller.keep(timeout=timeout, reset_timeout=True)
                return

            # ===== 序号选择 =====
            if DIGIT_REGEX.match(msg):
                index = int(msg) - 1
                if not sess.song_list.index_valid(index):
                    await ev.send(ev.plain_result(
                        f"序号无效，请输入 1-{sess.song_list.total_count} 之间的数字"
                    ))
                    controller.keep(timeout=timeout, reset_timeout=True)
                    return

                try:
                    result = await sess.song_list.select(index)
                except Exception as e:
                    logger.error(f"选择出错: {e}")
                    await ev.send(ev.plain_result("选择出错，请检查后台日志"))
                    controller.keep(timeout=timeout, reset_timeout=True)
                    return

                if isinstance(result, BaseSong):
                    # 选中歌曲 → 发送后自动退出会话
                    async for r in self._send_song(ev, result):
                        await ev.send(r)
                    controller.stop()
                    return
                elif isinstance(result, BasePlaylist):
                    # 对于歌单/专辑/电台，进入子列表（会话保持）
                    info = await result.get_info()
                    desc = await info.get_description()
                    await ev.send(ev.chain_result([
                        Comp.Plain(
                            f"📋 {desc}\n\n"
                            f"发送序号选择子项 | 上一页: P | 下一页: N | 退出: E/0"
                        ),
                    ]))
                    # 更新搜索会话为子列表
                    self.search_sessions[session_id] = SearchSession(
                        searcher=sess.searcher,
                        song_list=result,
                        message_id=sess.message_id,
                    )
                    controller.keep(timeout=timeout, reset_timeout=True)
                    return

            # ===== 未识别消息：保持会话，不做任何操作 =====
            controller.keep(timeout=timeout, reset_timeout=True)

        try:
            await _waiter(event)
        except TimeoutError:
            if session_id in self.search_sessions:
                del self.search_sessions[session_id]
            yield event.plain_result("⏰ 选择超时，已自动退出选择模式")

    # ==================== 显示当前页 ====================

    async def _show_current_page(self, event: AstrMessageEvent, sess: "SearchSession"):
        """显示当前页"""
        try:
            result = await sess.song_list.get_page()
        except Exception as e:
            logger.error(f"翻页出错: {e}")
            yield event.plain_result("翻页出错")
            return

        if result is None:
            yield event.plain_result("没有内容")
            return

        if isinstance(result, BaseSong):
            async for r in self._send_song(event, result):
                yield r
            return

        if isinstance(result, BaseSongListPage):
            try:
                cards = await result.transform_to_list_cards()
                img_bytes = await render_search_list(result, cards, limit=self.list_limit)
            except Exception as e:
                logger.error(f"渲染列表失败: {e}")
                yield event.plain_result("图片渲染失败")
                return
            yield event.chain_result([
                Comp.Image.fromBytes(img_bytes),
            ])

    # ==================== 发送歌曲 ====================

    async def _send_song(self, event: AstrMessageEvent, song: BaseSong):
        """发送歌曲（支持音乐卡片 / 文本+音频）"""
        try:
            info = await song.get_info()
        except Exception as e:
            logger.error(f"获取歌曲信息失败: {e}")
            yield event.plain_result("获取歌曲信息失败")
            return

        # 构建描述
        desc = await info.get_description()
        url = info.url
        platform = self._get_platform(event)

        # 尝试发送音乐卡片（仅支持 QQ 平台）
        if self.use_music_card and platform in ("qq", "aiocqhttp", "qqguild"):
            card_sent = False
            try:
                card_result = await self._send_music_card(info)
                if card_result:
                    yield event.chain_result([card_result])
                    card_sent = True
            except Exception as e:
                logger.warning(f"音乐卡片发送失败，回退到文本模式: {e}")

            if card_sent:
                # 卡片发送成功，不再发送文本和音频
                return

        # 回退模式：文本信息 + 音频文件
        info_text = f"🎵 {desc}\n🔗 {url}" if url else f"🎵 {desc}"
        yield event.chain_result([Comp.Plain(info_text)])

        # 下载并发送音频
        try:
            yield event.chain_result([Comp.Plain("⏳ 正在下载音频，请稍等...")])

            audio_path = await self._download_audio(info)
            if not audio_path:
                yield event.plain_result(f"音频下载失败，请使用链接收听: {url}")
                return

            if self.send_as_file or platform == "discord":
                yield event.chain_result([Comp.File(str(audio_path))])
            else:
                yield event.chain_result([Comp.Record(file=str(audio_path))])

        except Exception as e:
            logger.error(f"发送音频失败: {e}")
            yield event.plain_result(f"音频发送失败，请使用链接收听: {url}")

    async def _send_music_card(self, info: "SongInfo") -> "MusicCardComponent | None":
        """使用 MusicCardComponent 发送音乐卡片。

        MusicCardComponent 生成标准 OneBot 11 music 消息段，
        LLOneBot 等协议端收到后会自动调用其内置的 musicSignUrl 签名服务完成签名。

        优先使用网易云歌曲 ID（type=163）发送；若无有效 ID 则回退自定义方式。
        """
        if not info.playable_url:
            logger.debug("歌曲无可播放地址，跳过音乐卡片")
            return None

        song_id = info.id

        if song_id:
            logger.info(f"发送网易云音乐卡片 (ID={song_id}): {info.display_name}")
            return MusicCardComponent(music_type="163", song_id=song_id)

        # 回退到自定义卡片方式
        jump_url = info.url or ""
        if not jump_url:
            jump_url = f"https://music.163.com/#/song?id={song_id}"

        cover = info.cover_url or ""
        if cover:
            cover = cover.replace("http://", "https://")
            if "music.163.com" in cover:
                connector = "&" if "?" in cover else "?"
                cover = f"{cover}{connector}picsize=320"

        song_url = info.playable_url.replace("http://", "https://")

        logger.info(f"发送自定义音乐卡片: {info.display_name}")
        return MusicCardComponent(
            music_type="custom",
            url=jump_url,
            audio=song_url,
            title=info.display_name or "未知",
            content=info.display_artists or "",
            image=cover,
        )

    async def _download_audio(self, info: SongInfo) -> Optional[Path]:
        """下载音频文件到缓存"""
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = _CACHE_DIR / info.download_filename

        if cache_path.exists():
            return cache_path

        try:
            return await download_file(info.playable_url, cache_path)
        except Exception as e:
            logger.error(f"下载音频失败: {e}")
            return None

    # ==================== 链接解析 ====================

    @filter.regex(r"^(解析|resolve|parse|get)\s*(.*)$")
    async def handle_resolve(self, event: AstrMessageEvent):
        """解析网易云链接"""
        match = re.match(r"^(解析|resolve|parse|get)\s*(.*)$", event.message_str)
        if not match:
            return
        keyword = match.group(2).strip()

        result = await self._resolve_from_text(keyword)
        if not result:
            # 尝试从消息中找链接
            msg_text = event.message_str
            result = await self._resolve_from_text(msg_text)

        if not result:
            yield event.plain_result("未能从您的消息中解析到有效的网易云链接")
            return

        if isinstance(result, BaseSong):
            async for r in self._send_song(event, result):
                yield r
        elif isinstance(result, BasePlaylist):
            info = await result.get_info()
            desc = await info.get_description()
            yield event.chain_result([Comp.Plain(f"📋 {desc}")])
            session_id = self._get_session_id(event)
            self.search_sessions[session_id] = SearchSession(
                searcher=None,
                song_list=result,
                message_id=self._get_message_id(event),
            )
            # 启动交互会话
            async for r in self._start_interaction(event):
                yield r

    # ==================== 歌词 ====================

    @filter.regex(r"^(歌词|lrc|lyric|lyrics)\s*(.*)$")
    async def handle_lyric(self, event: AstrMessageEvent):
        """获取歌词"""
        match = re.match(r"^(歌词|lrc|lyric|lyrics)\s*(.*)$", event.message_str)
        if not match:
            return
        keyword = match.group(2).strip()

        # 尝试从链接解析歌曲
        song = None
        if keyword:
            result = await self._resolve_from_text(keyword)
            if isinstance(result, BaseSong):
                song = result

        # 如果没有指定，尝试从最近搜索会话获取
        if not song:
            session_id = self._get_session_id(event)
            sess = self.search_sessions.get(session_id)
            if sess and isinstance(sess.song_list, BaseSearcher):
                yield event.plain_result("请先搜索歌曲或回复网易云链接获取歌词")
                return

        if not song:
            yield event.plain_result("请指定歌曲：歌词 [链接/歌名]")
            return

        try:
            lrc = await song.get_lyrics()
        except Exception as e:
            logger.error(f"获取歌词失败: {e}")
            yield event.plain_result("获取歌词失败")
            return

        if not lrc:
            yield event.plain_result("该歌曲没有歌词")
            return

        try:
            img_bytes = await render_lyrics(lrc)
            yield event.chain_result([Comp.Image.fromBytes(img_bytes)])
        except Exception as e:
            logger.error(f"渲染歌词失败: {e}")
            yield event.plain_result("渲染歌词失败")

    # ==================== 直链 ====================

    @filter.regex(r"^(直链|direct)\s*(.*)$")
    async def handle_direct(self, event: AstrMessageEvent):
        """获取播放直链"""
        match = re.match(r"^(直链|direct)\s*(.*)$", event.message_str)
        if not match:
            return
        keyword = match.group(2).strip()

        song = None
        if keyword:
            result = await self._resolve_from_text(keyword)
            if isinstance(result, BaseSong):
                song = result

        if not song:
            yield event.plain_result("请指定歌曲：直链 [链接]")
            return

        try:
            url = await song.get_playable_url()
            yield event.plain_result(f"🎵 播放直链: {url}")
        except Exception as e:
            logger.error(f"获取直链失败: {e}")
            yield event.plain_result("获取直链失败")

    # ==================== 帮助 ====================

    @filter.regex(r"^(点歌帮助|ncm帮助|multincm帮助)$")
    async def handle_help(self, event: AstrMessageEvent):
        """显示帮助"""
        help_text = (
            "🎵 网易云多选点歌插件\n\n"
            "📌 搜索指令:\n"
            "  点歌 [歌名/ID] - 搜索歌曲\n"
            "  网易专辑 [名/ID] - 搜索专辑\n"
            "  网易歌单 [名/ID] - 搜索歌单\n"
            "  网易声音 [名/ID] - 搜索电台节目\n"
            "  网易电台 [名/ID] - 搜索电台\n\n"
            "📌 选择指令:\n"
            "  序号 - 选择对应项\n"
            "  P - 上一页 | N - 下一页\n"
            "  P+数字 - 跳到指定页（如 P 3）\n"
            "  E / 0 - 退出选择模式\n\n"
            "📌 其他指令:\n"
            "  解析 [链接] - 解析网易云链接\n"
            "  歌词 [链接] - 获取歌词\n"
            "  直链 [链接] - 获取播放直链\n\n"
            f"⏰ 搜索结果 {self.session_timeout} 秒无操作自动退出\n"
            "💡 QQ 平台开启「使用音乐卡片」后可发送音乐卡片（由协议端内置签名服务处理）\n"
            "💡 输入音乐ID可直接发送对应音乐"
        )
        yield event.plain_result(help_text)

    # ==================== 自动解析 ====================

    @filter.regex(r".*(music\.163\.com|163cn\.tv)")
    async def handle_auto_resolve(self, event: AstrMessageEvent):
        """自动解析网易云链接（含短链接）"""
        if not self.auto_resolve:
            return

        result = await self._resolve_from_text(event.message_str)
        if not result:
            return

        if isinstance(result, BaseSong):
            async for r in self._send_song(event, result):
                yield r
        elif isinstance(result, BasePlaylist):
            info = await result.get_info()
            desc = await info.get_description()
            yield event.chain_result([Comp.Plain(f"📋 {desc}")])
            session_id = self._get_session_id(event)
            self.search_sessions[session_id] = SearchSession(
                searcher=None,
                song_list=result,
                message_id=self._get_message_id(event),
            )
            # 启动交互会话，允许用户选择子项
            async for r in self._start_interaction(event):
                yield r

    # ==================== 内部工具 ====================

    async def _resolve_from_text(self, text: str) -> GeneralSongOrPlaylist | None:
        """从文本中解析网易云链接（支持短链接、标准链接、分享消息）"""
        # 先尝试短链接 (163cn.tv)
        m = re.search(SHORT_URL_REGEX, text, re.IGNORECASE)
        if m:
            suffix = m.group("suffix")
            short_url = f"{SHORT_URL_BASE}/{suffix}"
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(short_url, follow_redirects=False)
                    if resp.status_code // 100 == 3:
                        location = resp.headers.get("location", "")
                        if location:
                            logger.info(f"短链接 {short_url} 重定向到 {location}")
                            text = location
                    else:
                        logger.warning(f"短链接 {short_url} 返回状态码 {resp.status_code}，非重定向")
            except Exception as e:
                logger.warning(f"短链接 {short_url} 解析失败: {e}")
                # 继续尝试标准链接匹配（可能 text 本身就有 music.163.com）

        # 匹配标准链接（涵盖多种 URL 格式）
        # 格式: music.163.com/(#/)?(m/)?type?id=xxx 或 music.163.com/(#/)?type/xxx
        m = re.search(URL_REGEX, text, re.IGNORECASE)
        if m:
            link_type = m.group("type")
            link_id = int(m.group("id"))
            try:
                return await resolve_from_link_params(link_type, link_id)
            except Exception as e:
                logger.error(f"解析链接失败 (type={link_type}, id={link_id}): {e}")

        return None

    def _get_message_id(self, event: AstrMessageEvent) -> str:
        """获取消息ID"""
        if hasattr(event, "message_obj") and hasattr(event.message_obj, "message_id"):
            return str(event.message_obj.message_id)
        return str(event.session_id)


class SearchSession:
    """搜索会话"""
    def __init__(
        self,
        searcher: BaseSearcher | None,
        song_list: GeneralSongList,
        message_id: str,
    ):
        self.searcher = searcher
        self.song_list = song_list
        self.message_id = message_id
