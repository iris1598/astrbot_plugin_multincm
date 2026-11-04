"""登录流程编排：Cookie / 手机号 / 邮箱 / 游客，以及登录态缓存。"""
from __future__ import annotations

from pathlib import Path

from astrbot.api import logger

from ..config import get_config
from . import endpoints as ep
from .client import ncm_request
from .exceptions import LoginFailedException
from .session import (
    dump_session_as_string,
    get_current_session,
    load_session_from_string,
    set_current_session,
    write_login_info,
)

__all__ = ["SESSION_FILE_PATH", "login"]

_DATA_DIR = Path.cwd() / "data" / "plugin_data" / "astrbot_plugin_multincm"
SESSION_FILE_PATH = _DATA_DIR / "session.cache"


async def cookie_login(music_u: str):
    """使用 MUSIC_U 登录，并校验返回的 profile。"""
    ret = await ep.login_via_cookie(music_u)
    if ret is None:
        raise LoginFailedException("Cookie 登录返回为空")
    result = ret.get("result")
    if result is None:
        raise LoginFailedException(f"Cookie 登录失败: {ret.get('message', 'result 为空')}")
    content = result.get("content")
    if content is None:
        raise LoginFailedException("Cookie 登录失败: content 为空")
    profile = content.get("profile") if isinstance(content, dict) else None
    if not profile:
        raise LoginFailedException("Cookie 登录失败: profile 为空")

    status = await ncm_request(ep.login_status)
    if status:
        write_login_info(status)
    return ret


async def phone_login(
    phone: str,
    password: str = "",
    password_hash: str = "",
    country_code: int = 86,
):
    ret = await ep.login_via_cellphone(
        phone=phone,
        password=password,
        password_hash=password_hash,
        ctcode=country_code,
    )
    if ret is not None and ret.get("code", 200) != 200:
        raise LoginFailedException(f"手机号登录失败: {ret.get('message', '未知错误')}")


async def email_login(email: str, password: str = "", password_hash: str = ""):
    ret = await ep.login_via_email(
        email=email,
        password=password,
        password_hash=password_hash,
    )
    if ret is not None and ret.get("code", 200) != 200:
        raise LoginFailedException(f"邮箱登录失败: {ret.get('message', '未知错误')}")


async def anonymous_login():
    await ncm_request(ep.login_via_anonymous)


async def validate_login() -> bool:
    try:
        ret = await ncm_request(ep.login_status)
        if ret is None:
            return False
        ok = bool(ret.get("account"))
        if ok:
            write_login_info(ret)
        return ok
    except Exception:
        return False


async def do_login(anonymous: bool = False):
    config = get_config()
    actual_anonymous = anonymous

    if anonymous:
        logger.info("使用游客身份登录")
        await anonymous_login()

    elif SESSION_FILE_PATH.exists():
        logger.info(f"使用缓存登录态 ({SESSION_FILE_PATH})")
        set_current_session(
            load_session_from_string(SESSION_FILE_PATH.read_text(encoding="u8")),
        )
        if not (await validate_login()):
            SESSION_FILE_PATH.unlink(missing_ok=True)
            logger.warning("恢复缓存会话失败，尝试使用正常流程登录")
            await do_login()
            return

    elif config.get("cookie_music_u"):
        logger.info("使用 Cookie 登录")
        await cookie_login(config["cookie_music_u"])

    elif config.get("phone"):
        if config.get("password"):
            logger.info("使用手机号与密码登录")
            await phone_login(
                config["phone"],
                config.get("password", ""),
                config.get("password_hash", ""),
            )
        else:
            logger.warning("手机号登录需要密码，使用游客登录")
            await anonymous_login()
            actual_anonymous = True

    elif config.get("password") and config.get("email"):
        logger.info("使用邮箱与密码登录")
        await email_login(
            config["email"],
            config.get("password", ""),
            config.get("password_hash", ""),
        )

    else:
        logger.info("未配置登录信息，使用游客登录")
        await anonymous_login()
        actual_anonymous = True

    if actual_anonymous:
        logger.info("游客登录成功")
        return

    session = get_current_session()
    try:
        status = await ncm_request(ep.login_status)
        if status:
            write_login_info(status)
    except Exception as exc:
        logger.debug(f"获取登录状态失败（非致命）: {exc}")

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE_PATH.write_text(dump_session_as_string(session), "u8")

    nickname, uid = "未知用户", "未知"
    try:
        profile = (session.login_info or {}).get("content", {}).get("profile", {})
        if profile:
            nickname = profile.get("nickname", nickname)
            uid = str(profile.get("userId", uid))
        if nickname == "未知用户" and session.nickname:
            nickname = session.nickname
        if uid == "未知" and session.uid:
            uid = str(session.uid)
    except Exception:
        pass
    logger.info(f"登录成功，欢迎您，{nickname} [{uid}]")


async def login():
    """插件启动时的登录入口。"""
    config = get_config()
    try:
        if get_current_session().logged_in:
            logger.info("检测到当前全局 Session 已登录，跳过登录步骤")
            return
    except Exception as exc:
        logger.warning(f"检查登录状态失败: {exc}，继续尝试登录")

    try:
        await do_login(anonymous=config.get("anonymous", False))
    except Exception as exc:
        import traceback

        logger.warning(f"登录失败: {exc}，尝试游客登录\n{traceback.format_exc()}")
        try:
            await do_login(anonymous=True)
        except Exception as exc2:
            logger.error(f"游客登录也失败: {exc2}")
