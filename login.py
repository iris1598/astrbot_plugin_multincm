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
_DATA_DIR = Path.cwd() / "data" / "multincm"
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
    if not (c := ret["result"]["content"])["profile"]:
        raise LoginFailedException(c)
    return ret


async def phone_login(
    phone: str,
    password: str = "",
    password_hash: str = "",
    country_code: int = 86,
):
    await asyncio.to_thread(
        LoginViaCellphone,
        ctcode=country_code,
        phone=phone,
        password=password,
        passwordHash=password_hash,
    )


async def email_login(
    email: str,
    password: str = "",
    password_hash: str = "",
):
    await asyncio.to_thread(
        LoginViaEmail,
        email=email,
        password=password,
        passwordHash=password_hash,
    )


async def anonymous_login():
    await ncm_request(LoginViaAnonymousAccount)


async def validate_login() -> bool:
    try:
        ret = await ncm_request(GetCurrentLoginStatus)
        ok = bool(ret.get("account"))
        if ok and HAS_WRITE_LOGIN:
            WriteLoginInfo(ret)
        return ok
    except Exception:
        return False


async def do_login(anonymous: bool = False):
    config = _get_config()

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
            logger.warning("手机号登录需要密码，跳过")

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

    if anonymous:
        logger.info("游客登录成功")
    else:
        session = GetCurrentSession()
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_FILE_PATH.write_text(DumpSessionAsString(session), "u8")
        logger.info(f"登录成功，欢迎您，{session.nickname} [{session.uid}]")


async def login():
    """主登录入口"""
    config = _get_config()

    if GetCurrentSession().logged_in:
        logger.info("检测到当前全局 Session 已登录，跳过登录步骤")
        return

    try:
        await do_login(anonymous=config.get("anonymous", False))
    except Exception as e:
        logger.warning(f"登录失败: {e}，尝试游客登录")
        try:
            await do_login(anonymous=True)
        except Exception as e2:
            logger.error(f"游客登录也失败: {e2}")
