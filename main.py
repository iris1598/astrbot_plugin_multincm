"""AstrBot 网易云多选点歌插件 - 主入口"""
import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

import httpx
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp
from astrbot.api.message_components import Json

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

# URL 解析正则
URL_REGEX = r"music\.163\.com/(.*?)(?P<type>[a-zA-Z]+)(/?\\?id=|/)(?P<id>[0-9]+)&?"
SHORT_URL_BASE = "https://163cn.tv"
SHORT_URL_REGEX = r"163cn\.tv/(?P<suffix>[a-zA-Z0-9]+)"

# 搜索交互命令
EXIT_COMMANDS = {"退出", "tc", "取消", "qx", "quit", "q", "exit", "e", "E", "cancel", "c", "0"}
PREVIOUS_COMMANDS = {"上一页", "syy", "previous", "p", "P"}
NEXT_COMMANDS = {"下一页", "xyy", "next", "n", "N"}
JUMP_PAGE_PREFIX = ("page", "Page", "PAGE", "p", "P", "跳页", "页")


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

        # 选择超时配置（秒）
        self.selection_timeout = self.config.get("selection_timeout", schema_defaults.get("selection_timeout", 120))

        # 音乐卡片配置
        self.use_music_card = self.config.get("use_music_card", schema_defaults.get("use_music_card", False))
        self.card_sign_url = self.config.get(
            "card_sign_url",
            schema_defaults.get("card_sign_url", "https://oiapi.net/api/QQMusicJSONArk/"),
        )

        # 更新全局配置
        _config.update({
            "cookie_music_u": self.cookie_music_u,
            "list_limit": self.list_limit,
            "send_as_file": self.send_as_file,
            "auto_resolve": self.auto_resolve,
            "ffmpeg_executable": self.ffmpeg_executable,
            "phone": self.config.get("phone", ""),
            "email": self.config.get("email", ""),
            "password": self.config.get("password", ""),
            "anonymous": self.config.get("anonymous", False),
            "selection_timeout": self.selection_timeout,
        })

        # 搜索会话存储：{session_id: SearchSession}
        self.search_sessions: dict[str, "SearchSession"] = {}

        # 启动过期会话清理任务
        asyncio.create_task(self._cleanup_expired_sessions())

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
            timeout=self.selection_timeout,
        )

        # 发送搜索结果
        yield event.chain_result([
            Comp.Plain(f"🎵 搜索: {keyword} | 回复序号选择 | P+数字跳页 | 上一页(P) | 下一页(N) | 退出(E)"),
            Comp.Image.fromBytes(img_bytes),
        ])

    # ==================== 选择交互 ====================

    @filter.regex(r"^(退出|tc|取消|qx|quit|q|exit|e|E|cancel|c)$")
    async def handle_exit(self, event: AstrMessageEvent):
        """退出搜索"""
        session_id = self._get_session_id(event)
        if session_id in self.search_sessions:
            del self.search_sessions[session_id]
            yield event.plain_result("已退出选择模式")

    @filter.regex(r"^(上一页|syy|previous|p|P)$")
    async def handle_prev_page(self, event: AstrMessageEvent):
        """上一页"""
        sess = self._get_valid_session(event)
        if not sess:
            return
        if sess.song_list.is_first_page:
            yield event.plain_result("已经是第一页了")
            return
        sess.song_list.current_page -= 1
        async for r in self._show_current_page(event, sess):
            yield r

    @filter.regex(r"^(下一页|xyy|next|n|N)$")
    async def handle_next_page(self, event: AstrMessageEvent):
        """下一页"""
        sess = self._get_valid_session(event)
        if not sess:
            return
        if sess.song_list.is_last_page:
            yield event.plain_result("已经是最后一页了")
            return
        sess.song_list.current_page += 1
        async for r in self._show_current_page(event, sess):
            yield r

    @filter.regex(r"^(page|Page|PAGE|p|P|跳页|页)\s*(\d+)$")
    async def handle_jump_page(self, event: AstrMessageEvent):
        """跳页"""
        sess = self._get_valid_session(event)
        if not sess:
            return
        match = re.match(r"^(page|p|P|跳页|页)\s*(\d+)$", event.message_str.strip())
        if not match:
            return
        page = int(match.group(2))
        if not sess.song_list.page_valid(page):
            yield event.plain_result("页码无效")
            return
        sess.song_list.current_page = page
        async for r in self._show_current_page(event, sess):
            yield r

    @filter.regex(r"^\d+$")
    async def handle_select(self, event: AstrMessageEvent):
        """选择序号"""
        session_id = self._get_session_id(event)
        sess = self._get_valid_session(event)
        if not sess:
            return

        msg = event.message_str.strip()
        if not msg.isdigit():
            return

        index = int(msg) - 1
        if not sess.song_list.index_valid(index):
            yield event.plain_result(f"序号无效，请输入 1-{sess.song_list.total_count} 之间的数字")
            return

        try:
            result = await sess.song_list.select(index)
        except Exception as e:
            logger.error(f"选择出错: {e}")
            yield event.plain_result("选择出错，请检查后台日志")
            return

        if isinstance(result, BaseSong):
            async for r in self._send_song(event, result):
                yield r
            # 选歌完成，清理搜索会话
            if session_id in self.search_sessions:
                del self.search_sessions[session_id]
        elif isinstance(result, BasePlaylist):
            # 对于歌单/专辑/电台，进入子列表
            info = await result.get_info()
            desc = await info.get_description()
            yield event.chain_result([
                Comp.Plain(f"📋 {desc}\n\n发送序号选择子项 | 退出(E)"),
            ])
            # 更新搜索会话为子列表，重置超时时间
            self.search_sessions[session_id] = SearchSession(
                searcher=sess.searcher,
                song_list=result,
                message_id=sess.message_id,
                timeout=self.selection_timeout,
            )

    async def _cleanup_expired_sessions(self):
        """定期清理过期的搜索会话"""
        while True:
            await asyncio.sleep(30)
            now = time.time()
            expired = [
                sid for sid, sess in self.search_sessions.items()
                if sess.is_expired(now)
            ]
            for sid in expired:
                logger.debug(f"清理超时搜索会话: {sid}")
                del self.search_sessions[sid]

    def _get_valid_session(self, event: AstrMessageEvent) -> "SearchSession | None":
        """获取并验证会话是否过期"""
        session_id = self._get_session_id(event)
        sess = self.search_sessions.get(session_id)
        if not sess:
            return None
        if sess.is_expired():
            del self.search_sessions[session_id]
            return None
        return sess

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

    async def _send_music_card(self, info: "SongInfo") -> "Json | None":
        """通过签名 API 构建并发送音乐卡片

        参考 astrbot_plugin_meting 的实现，使用 QQ 音乐 JSON Ark 协议。
        返回 Json 消息组件，失败返回 None。
        """
        if not self.card_sign_url:
            logger.debug("未配置 card_sign_url，跳过音乐卡片")
            return None

        if not info.playable_url:
            logger.debug("歌曲无可播放地址，跳过音乐卡片")
            return None

        # 构建跳转链接（网易云）
        jump_url = info.url or ""
        # 从播放 URL 提取歌曲 ID
        song_id = ""
        try:
            import urllib.parse
            parsed = urllib.parse.urlparse(info.playable_url)
            qs = urllib.parse.parse_qs(parsed.query)
            song_id = qs.get("id", [""])[0]
        except Exception:
            pass
        if not jump_url and song_id:
            jump_url = f"https://music.163.com/#/song?id={song_id}"

        # 处理封面 URL（网易云封面需要指定大小）
        cover = info.cover_url or ""
        if cover:
            cover = cover.replace("http://", "https://")
            if "music.163.com" in cover:
                connector = "&" if "?" in cover else "?"
                cover = f"{cover}{connector}picsize=320"

        # 强制 HTTPS
        song_url = info.playable_url.replace("http://", "https://")

        # 规范化签名 API URL
        sign_url = self.card_sign_url.strip()
        sign_url = sign_url.replace("http://", "https://")
        if not sign_url.endswith("/"):
            sign_url += "/"

        # 构建签名请求参数
        params = {
            "url": song_url,
            "song": info.display_name or "未知",
            "singer": info.display_artists or "未知歌手",
            "cover": cover,
            "jump": jump_url,
            "format": "163",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(sign_url, params=params)
                if resp.status_code != 200:
                    logger.warning(f"音乐卡片签名 API 返回 {resp.status_code}")
                    return None

                res_json = resp.json()
                if res_json.get("code") == 1:
                    ark_data = res_json.get("data")
                    if not ark_data:
                        logger.warning("签名 API 返回的 data 为空")
                        return None
                    token = ark_data.get("config", {}).get("token", "")
                    json_card = Json(data=ark_data, config={"token": token})
                    logger.info(f"音乐卡片签名成功: {info.display_name}")
                    return json_card
                else:
                    logger.warning(f"音乐卡片签名失败: {res_json.get('message', '未知错误')}")
                    return None
        except Exception as e:
            logger.warning(f"音乐卡片签名请求异常: {e}")
            return None

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
                timeout=self.selection_timeout,
            )

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
            sess = self._get_valid_session(event)
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
            "📌 操作指令:\n"
            "  序号 - 选择对应项\n"
            "  上一页(P) / 下一页(N) - 翻页\n"
            "  P+数字 - 跳到指定页\n"
            "  退出(E) - 退出选择模式\n\n"
            "📌 其他指令:\n"
            "  解析 [链接] - 解析网易云链接\n"
            "  歌词 [链接] - 获取歌词\n"
            "  直链 [链接] - 获取播放直链\n\n"
            "💡 QQ 平台开启「使用音乐卡片」后可发送音乐卡片\n"
            "💡 输入音乐ID可直接发送对应音乐"
        )
        yield event.plain_result(help_text)

    # ==================== 自动解析 ====================

    @filter.regex(r".*music\.163\.com.*")
    async def handle_auto_resolve(self, event: AstrMessageEvent):
        """自动解析网易云链接"""
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
            yield event.chain_result([Comp.Plain(f"📋 {desc}\n\n发送 解析 [链接] 查看详情")])

    # ==================== 内部工具 ====================

    async def _resolve_from_text(self, text: str) -> GeneralSongOrPlaylist | None:
        """从文本中解析网易云链接"""
        # 先尝试短链接
        m = re.search(SHORT_URL_REGEX, text, re.IGNORECASE)
        if m:
            try:
                suffix = m.group("suffix")
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{SHORT_URL_BASE}/{suffix}",
                        follow_redirects=False,
                        timeout=5,
                    )
                    if resp.status_code // 100 == 3:
                        location = resp.headers.get("location", "")
                        text = location or text
            except Exception:
                pass

        # 匹配标准链接
        m = re.search(URL_REGEX, text, re.IGNORECASE)
        if m:
            link_type = m.group("type")
            link_id = int(m.group("id"))
            try:
                return await resolve_from_link_params(link_type, link_id)
            except Exception as e:
                logger.error(f"解析链接失败: {e}")

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
        timeout: float = 120,
    ):
        self.searcher = searcher
        self.song_list = song_list
        self.message_id = message_id
        self._created_at = time.time()
        self._timeout = timeout

    def is_expired(self, now: float | None = None) -> bool:
        """检查会话是否已超时"""
        if now is None:
            now = time.time()
        return now > self._created_at + self._timeout
