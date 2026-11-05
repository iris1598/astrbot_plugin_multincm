"""莉卡解析（astrbot_plugin_rika_share）联动桥接。

本插件不再自动解析网易云链接。当莉卡解析插件已安装时，它会在入口模块导入
阶段（生成平台 Handler 之前）导入本模块并调用 :func:`register`，把「网易云音乐」
解析器注册进莉卡的适配器注册表；此后网易云链接的自动解析由莉卡的主流程接管：
渲染分享卡片 + 发送音频，登录态 / Cookie 复用本插件的配置与 netease 会话。

- 本插件**未安装 / 依赖缺失 / 被停用**：莉卡侧没有这个解析器（后者静默跳过）；
- 本插件的交互指令（点歌 / 解析 / 歌词 / 直链 …）不受联动影响：本模块在解析入口
  把「本插件指令消息」以 ``SilentException`` 静默让出，避免同一条消息被指令
  handler 与莉卡解析重复响应；
- **发送行为遵循本插件的设置**：结果统一声明 ``suppress_text_output``（分享卡片已承载
  全部信息，卡片渲染成功后莉卡不再发文本）；歌曲在「使用音乐卡片」开启时下发
  ``music_card`` 规格（QQ 系平台只发 ``Comp.Music`` type=163，替代卡片图 / 文本 / 语音），
  「以文件形式发送歌曲」开启时音频以文件而不是语音发送；
- **卡片信息**：歌曲卡带音乐人头像（``get_artist_info``，端点源自 pyncm
  ``GetArtistDetails``）、专辑名（作者副标题）与时长 / 热度统计行（``stats_line``，
  由莉卡的统计徽章渲染）；头像获取失败只少一张图，不连累解析；
- 莉卡解析**未安装**时，本模块不会被任何一方导入，零开销。

注册表契约（与莉卡内置适配器一致）：

- 平台名 ``netease``（小写标识符）：注册表键名、设置页「解析器开关」取值、
  动态 Handler 方法名（``netease_handler``）；
- 短链 ``163cn.tv`` 不捕获命名分组，让莉卡的 ``cache_identity`` 走「短链展开」
  阶梯，同一首歌的不同短链共用一条解析缓存；
- 内容标识形如 ``netease:id=123&type=song``（``identity_from_match`` 默认拼接）。
"""

from __future__ import annotations

import importlib
import re
from re import Match
from typing import ClassVar

from astrbot.api import logger

from .netease import NCMResponseError, get_artist_info
from .search import BasePlaylist, BaseSong, Song, resolve_from_link_params
from .utils import format_time, get_thumb_url

# ==================== 莉卡解析模块定位 ====================

_RIKA_PLUGIN = "astrbot_plugin_rika_share"

#: 莉卡解析插件包在 AstrBot 里的候选模块根：插件以 ``data.plugins.<目录名>`` 加载，
#: 保留插件前缀与裸包名作为回退（开发脚本 / 未来模块根变化）。
_RIKA_ROOTS = (
    f"data.plugins.{_RIKA_PLUGIN}",
    f"astrbot.builtin_stars.{_RIKA_PLUGIN}",
    _RIKA_PLUGIN,
)


def _rika_module(name: str):
    """按候选根导入莉卡解析的子模块；全部失败时抛出最后一次的异常。"""
    last_exc: ImportError | None = None
    for root in _RIKA_ROOTS:
        try:
            return importlib.import_module(f"{root}.{name}")
        except ImportError as exc:
            last_exc = exc
    assert last_exc is not None
    raise last_exc


_base = _rika_module("link_parser.adapters.base")
_registry = _rika_module("link_parser.adapters.registry")
_models = _rika_module("link_parser.models")
_exceptions = _rika_module("link_parser.exceptions")

BaseParser = _base.BaseParser
handle = _base.handle
AdapterSpec = _registry.AdapterSpec
register_adapter = _registry.register_adapter
ParseResult = _models.ParseResult
Platform = _models.Platform
ParseException = _exceptions.ParseException
SilentException = _exceptions.SilentException


# ==================== 解析器 ====================

#: 封面图边长：网易云原图可达数千像素，卡片用不到，按方形缩略取 500px
_COVER_SIZE = 500


class NeteaseParser(BaseParser):
    """网易云链接解析器（调用本插件的 search 数据源，登录态随 multincm）。"""

    platform: ClassVar[Platform] = Platform(name="netease", display_name="网易云音乐")

    #: 163cn.tv 短链：不在正则里捕获命名分组，让莉卡按「短链展开」阶梯
    #: 生成内容标识（同一首歌的不同短链共用缓存）
    SHORT_LINK_KEYWORDS: ClassVar[tuple[str, ...]] = ("163cn.tv",)

    #: 解析结果缓存有效期（秒）。网易云内容本身不变，但结果里烙有「音乐卡片 /
    #: 音频可用性」这类随 multincm 设置与登录态变化的决策（开关改后、Cookie
    #: 登录后都应生效），过期重解析让它们最多 5 分钟自动跟上，无需重启。
    CACHE_TTL_SECONDS: ClassVar[int | None] = 300

    #: 本插件指令消息（参数里带网易云链接时）交给本插件的指令 handler，
    #: 莉卡侧静默让出，避免重复响应。与 main.py 各指令正则保持一致。
    _COMMAND_MESSAGE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:点歌|专辑|歌单|声音|电台|解析|歌词|直链)\s+\S|^点歌帮助$"
    )

    def _ensure_not_command(self, searched: Match) -> None:
        """消息是本插件指令时静默让出（指令 handler 会响应它）。"""
        if self._COMMAND_MESSAGE_RE.match(searched.string.strip()):
            raise SilentException("multincm 指令消息，让出给指令 handler 处理")

    @handle("163cn.tv", r"163cn\.tv/[a-zA-Z0-9]+")
    async def _parse_short_link(self, searched: Match) -> ParseResult:
        self._ensure_not_command(searched)
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url)

    @handle(
        "music.163.com",
        r"music\.163\.com/(?:#/)?(?:m/)?"
        r"(?P<type>song|url|album|playlist|program|dj|radio)"
        r"(?:/|\?id=)(?P<id>\d+)",
    )
    async def _parse_link(self, searched: Match) -> ParseResult:
        self._ensure_not_command(searched)
        return await self.build_result(searched.group("type"), int(searched.group("id")))

    async def build_result(self, link_type: str, link_id: int) -> ParseResult:
        """按链接类型解析出莉卡的 ``ParseResult``。"""
        if await _multincm_disabled():
            # 本插件被停用：联动解析一并停用，莉卡侧静默跳过（不回复）
            raise SilentException("multincm 插件已停用，联动解析一并停用")

        try:
            item = await resolve_from_link_params(link_type, link_id)
        except NCMResponseError as e:
            raise ParseException(f"网易云接口返回错误: {e}") from e
        except LookupError as e:
            # 内容不存在（如歌曲被下架）：from_id 里 [0] 取响应首项会抛 IndexError
            raise ParseException(f"内容不存在或不支持: {e}") from e
        except ValueError as e:
            raise ParseException(str(e)) from e

        if isinstance(item, BaseSong):
            result = await self._song_result(item)
        elif isinstance(item, BasePlaylist):
            result = await self._playlist_result(item)
        else:
            raise ParseException(f"暂不支持的链接类型: {link_type}")
        # 分享卡片已承载标题 / 作者 / 封面 / 描述：卡片渲染成功后莉卡不再重复发
        # 「莉卡解析 | …」文本输出（渲染关闭或失败时莉卡回退文本，不受此标志影响）
        result.extra["suppress_text_output"] = True
        return result

    async def _song_result(self, song: BaseSong) -> ParseResult:
        """歌曲 / 电台节目：标题 + 音乐人（头像 / 专辑）+ 时长统计 + 封面 + 音频。"""
        name, alias, artists, duration_ms, cover_url, url = (
            await song.get_name(),
            await song.get_alias(),
            await song.get_artists(),
            await song.get_duration(),
            await song.get_cover_url(),
            await song.get_url(),
        )
        display_name = f"{name}（{'；'.join(alias)}）" if alias else name
        if isinstance(song, Song):
            # 歌曲：作者行带第一位音乐人头像、副标题放专辑名；统计行放时长与热度
            author = self.create_author(
                "、".join(artists) if artists else "未知艺人",
                avatar_url=await self._fetch_artist_avatar(song),
                description=f"专辑《{song.info.al.name}》" if song.info.al.name else None,
            )
            stats_line = f"⏱ {format_time(duration_ms)} 🔥 {song.info.pop}"
        else:
            # 电台节目：其描述里已含时长与各项统计，不再重复
            author = self.create_author("、".join(artists)) if artists else None
            stats_line = None
        result = self.result(
            title=display_name,
            author=author,
            url=url or None,
            extra={"content_type": song.calling},
        )
        if stats_line:
            result.extra["stats_line"] = stats_line
        if cover_url:
            result.graphics.append(self.create_image(get_thumb_url(cover_url, _COVER_SIZE)))
        playable_url = await self._attach_audio(result, song, duration_ms)
        # 音乐卡片：按 multincm 的「使用音乐卡片」设置，QQ 系平台由莉卡发送
        # Comp.Music（type=163，协议端按歌曲 ID 自行签名），并整体替代卡片图 / 文本 / 语音。
        # 仅限歌曲（Song）：电台节目的 ID 不是歌曲 ID，163 卡片会取到错误内容。
        if playable_url and isinstance(song, Song):
            if _use_music_card():
                result.extra["music_card"] = {"type": "163", "id": song.id}
                logger.info(f"[multincm] 歌曲将发送音乐卡片 (163) ID={song.id}")
            else:
                logger.debug("[multincm] use_music_card 未开启，歌曲以语音发送")
        return result

    async def _fetch_artist_avatar(self, song: Song) -> str | None:
        """取第一位音乐人的头像；失败只少一张图，不连累卡片。"""
        artist_id = song.info.ar[0].id if song.info.ar else None
        if not artist_id:
            return None
        try:
            detail = await get_artist_info(artist_id)
        except Exception as e:  # noqa: BLE001 - 头像拿不到不该影响解析
            logger.debug(f"[multincm] 获取音乐人头像失败: {e}")
            return None
        return detail.avatar or detail.cover or None

    async def _playlist_result(self, playlist: BasePlaylist) -> ParseResult:
        """专辑 / 歌单 / 电台：标题 + 创建者 + 封面 + 描述（含统计与简介）。"""
        info = await playlist.get_info()
        result = self.result(
            title=info.name,
            author=(
                self.create_author(info.display_creators) if info.display_creators else None
            ),
            url=info.url or None,
            extra={"content_type": playlist.calling},
        )
        try:
            result.text = await info.get_description()
        except Exception as e:  # noqa: BLE001 - 描述拿不到不该连累卡片
            logger.warning(f"[multincm] 获取{playlist.calling}描述失败: {e}")
        if info.cover_url:
            result.graphics.append(self.create_image(get_thumb_url(info.cover_url, _COVER_SIZE)))
        return result

    async def _attach_audio(self, result: ParseResult, song: BaseSong, duration_ms: int) -> str | None:
        """给结果附上音频下载任务；拿不到播放地址时降级为仅卡片并附警告。

        返回播放地址（拿不到时为 ``None``）。音乐卡片模式下该任务通常不会被
        await（莉卡发完卡片即返回），因此不会产生多余下载。
        """
        try:
            playable_url = await song.get_playable_url()
        except Exception as e:  # noqa: BLE001 - VIP 限制 / 接口风控都不该连累卡片
            logger.warning(f"[multincm] 获取音频地址失败，仅发送卡片: {e}")
            result.extra.setdefault("limit_warnings", []).append(
                "⚠️ 获取音频地址失败，本次仅发送卡片"
            )
            return None
        if not playable_url:
            result.extra.setdefault("limit_warnings", []).append(
                "⚠️ 无法获取音频（VIP 或需在 multincm 配置网易云登录）"
            )
            return None
        result.contents.append(
            self.create_audio(playable_url, duration=duration_ms / 1000 if duration_ms else 0.0)
        )
        # 「以文件形式发送歌曲」：莉卡的 try_send_media 会以 Comp.File 代替语音
        if _send_as_file():
            result.extra["audio_as_file"] = True
        return playable_url


def _use_music_card() -> bool:
    """读 multincm 的「使用音乐卡片」开关。

    每次解析时现读（配置由 multincm 实例化时注入、原地更新）；
    解析结果会被莉卡缓存复用，改动开关只对之后新解析的内容生效。
    """
    from .config import get_config

    return bool(get_config().get("use_music_card", False))


def _send_as_file() -> bool:
    """读 multincm 的「以文件形式发送歌曲」开关（读法与时效同 :func:`_use_music_card`）。"""
    from .config import get_config

    return bool(get_config().get("send_as_file", False))


async def _multincm_disabled() -> bool:
    """本插件本体（Star）是否被用户停用。

    AstrBot 把停用插件的 module_path 记在 ``inactivated_plugins``；停用意味着
    配置未注入、指令未注册，此时联动解析应一并停用。任何读取异常都按
    「未停用」处理，避免存储层问题连累解析主流程。
    """
    try:
        from astrbot.api import sp

        inactivated = await sp.global_get("inactivated_plugins", [])
    except Exception:  # noqa: BLE001
        return False
    self_root = __name__.rsplit(".", 1)[0]
    return f"{self_root}.main" in inactivated


# ==================== 注册 ====================


def register() -> None:
    """把网易云解析器注册进莉卡解析的适配器注册表。

    由莉卡的 ``adapters._external.register_external_adapters()`` 在其入口模块
    导入阶段（生成平台 Handler 之前）调用；可重复调用（同键覆盖，幂等）。
    不在模块顶层自注册：莉卡插件重载后其注册表是全新实例，而本模块在
    ``sys.modules`` 里仍是旧对象，靠显式调用才能重新注册。
    """
    spec = register_adapter(
        AdapterSpec(
            name="netease",
            url_pattern=re.compile(r"music\.163\.com|163cn\.tv"),
            parser_cls=NeteaseParser,
            description="歌曲 / 专辑 / 歌单 / 电台（联动 astrbot_plugin_multincm）",
        )
    )
    logger.info(f"[multincm] 已向莉卡解析注册网易云解析器: {spec.name}")


__all__ = ["NeteaseParser", "register"]
