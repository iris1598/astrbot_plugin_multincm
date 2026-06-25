"""网易云登录系统（去除 NoneBot 依赖）"""
import asyncio
import time
from pathlib import Path
from typing import Any

from pyncm import (
    DumpSessionAsString,
    GetCurrentSession,
    LoadSessionFromString,
    SetCurrentSession,
)
from pyncm.apis.login import (
    GetCurrentLoginStatus,
    LoginFailedException,
    LoginViaAnonymousAccount,
    LoginViaCellphone,
    LoginViaCookie,
    LoginViaEmail,
)

from .api import NCMResponseError, ncm_request

try:
    from pyncm.apis.login import LoginQrcodeCheck, LoginQrcodeUnikey
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

try:
    from pyncm.apis.login import SetSendRegisterVerifcationCodeViaCellphone
    HAS_SMS = True
except ImportError:
    HAS_SMS = False

try:
    from pyncm.apis.login import WriteLoginInfo
    HAS_WRITE_LOGIN = True
except ImportError:
    HAS_WRITE_LOGIN = False

from astrbot.api import logger

# 数据存储路径
_DATA_DIR = Path.cwd() / "data" / "plugin_data" / "astrbot_plugin_multincm"
SESSION_FILE_PATH = _DATA_DIR / "session.cache"


def _get_config() -> dict:
    """获取配置"""
    try:
        from .main import _config
        return _config
    except Exception:
        return {}


async def cookie_login(music_u: str):
    ret = await asyncio.to_thread(LoginViaCookie, music_u)
    if ret is None:
        raise LoginFailedException("Cookie 登录返回为空")
    # 处理 result 为 None 的情况
    result = ret.get("result")
    if result is None:
        raise LoginFailedException(f"Cookie 登录失败: {ret.get('message', 'result 为空')}")
    content = result.get("content")
    if content is None:
        raise LoginFailedException(f"Cookie 登录失败: content 为空")
    profile = content.get("profile") if isinstance(content, dict) else None
    if not profile:
        raise LoginFailedException(f"Cookie 登录失败: profile 为空")
    
    # 登录成功后，填充 session 的 login_info
    login_status = await ncm_request(GetCurrentLoginStatus)
    if login_status and HAS_WRITE_LOGIN:
        WriteLoginInfo(login_status)
    
    return ret


async def phone_login(
    phone: str,
    password: str = "",
    password_hash: str = "",
    country_code: int = 86,
):
    ret = await asyncio.to_thread(
        LoginViaCellphone,
        ctcode=country_code,
        phone=phone,
        password=password,
        passwordHash=password_hash,
    )
    if ret is not None and ret.get("code", 200) != 200:
        raise LoginFailedException(f"手机号登录失败: {ret.get('message', '未知错误')}")


async def email_login(
    email: str,
    password: str = "",
    password_hash: str = "",
):
    ret = await asyncio.to_thread(
        LoginViaEmail,
        email=email,
        password=password,
        passwordHash=password_hash,
    )
    if ret is not None and ret.get("code", 200) != 200:
        raise LoginFailedException(f"邮箱登录失败: {ret.get('message', '未知错误')}")


async def anonymous_login():
    await ncm_request(LoginViaAnonymousAccount)


async def validate_login() -> bool:
    try:
        ret = await ncm_request(GetCurrentLoginStatus)
        if ret is None:
            return False
        ok = bool(ret.get("account"))
        if ok and HAS_WRITE_LOGIN:
            WriteLoginInfo(ret)
        return ok
    except Exception:
        return False


async def do_login(anonymous: bool = False):
    config = _get_config()

    # 实际是否使用了匿名登录（因为 else 分支可能回退到匿名登录）
    actual_anonymous = anonymous

    if anonymous:
        logger.info("使用游客身份登录")
        await anonymous_login()

    elif SESSION_FILE_PATH.exists():
        logger.info(f"使用缓存登录态 ({SESSION_FILE_PATH})")
        SetCurrentSession(
            LoadSessionFromString(SESSION_FILE_PATH.read_text(encoding="u8")),
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
    else:
        session = GetCurrentSession()

        # 登录成功后，主动填充 session 的 login_info（pyncm 的部分登录方式不会自动设置）
        try:
            login_status = await ncm_request(GetCurrentLoginStatus)
            if login_status and HAS_WRITE_LOGIN:
                WriteLoginInfo(login_status)
        except Exception as e:
            logger.debug(f"获取登录状态失败（非致命）: {e}")

        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_FILE_PATH.write_text(DumpSessionAsString(session), "u8")

        # 多级回退提取用户信息：login_info → session 属性
        nickname = "未知用户"
        uid = "未知"
        try:
            # 优先从 login_info 字典提取
            if session.login_info:
                profile = session.login_info.get("content", {}).get("profile", {})
                if profile:
                    nickname = profile.get("nickname", nickname)
                    uid = str(profile.get("userId", uid))
            # 回退到 session 属性（pyncm 部分版本会自动设置）
            if nickname == "未知用户" and hasattr(session, "nickname"):
                nickname = session.nickname or nickname
            if uid == "未知" and hasattr(session, "uid"):
                uid = str(session.uid) if session.uid else uid
        except Exception:
            pass
        logger.info(f"登录成功，欢迎您，{nickname} [{uid}]")


async def login(force: bool = False):
    """主登录入口

    Args:
        force: 强制重新登录，跳过已有 session 检查（插件重载时使用）
    """
    config = _get_config()

    if not force:
        try:
            session = GetCurrentSession()
            if getattr(session, 'logged_in', False):
                logger.info("检测到当前全局 Session 已登录，跳过登录步骤")
                return
        except Exception as e:
            logger.warning(f"检查登录状态失败: {e}，继续尝试登录")

    try:
        await do_login(anonymous=config.get("anonymous", False))
    except Exception as e:
        import traceback
        logger.warning(f"登录失败: {e}，尝试游客登录\n{traceback.format_exc()}")
        try:
            await do_login(anonymous=True)
        except Exception as e2:
            logger.error(f"游客登录也失败: {e2}")
