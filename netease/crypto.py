"""网易云 API 加密原语。

底层 AES 实现位于 :mod:`._aes`（源自 ricmoo/pyaes，MIT 许可，版权声明见文件头），
历史风控算法位于 :mod:`._security`。本模块把它们整合成项目内部的 snake_case 接口，
上层只需与 ``eapi_encrypt`` / ``weapi_encrypt`` / ``eapi_decrypt`` 打交道。
"""
from __future__ import annotations

import base64
import json
import random
import time
from hashlib import md5

from ._aes import AES
from ._security import c_decrypt_abroad_message, cloudmusic_dll_encode_id

__all__ = [
    "AES",
    "cloudmusic_dll_encode_id",
    "decrypt_abroad_message",
    "eapi_decrypt",
    "eapi_encrypt",
    "generate_chain_id",
    "generate_s_device_id",
    "hash_hex_digest",
    "hex_compose",
    "hex_digest",
    "random_string",
    "weapi_encrypt",
]

# region 常量
BASE62 = "PJArHa0dpwhvMNYqKnTbitWfEmosQ9527ZBx46IXUgOzD81VuSFyckLRljG3eC"

WEAPI_RSA_PUBKEY = (
    int(
        "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7",
        16,
    ),
    int("10001", 16),  # 无填充 RSA
)
WEAPI_AES_KEY = "0CoJUm6Qyw8W8jud"
WEAPI_AES_IV = "0102030405060708"
EAPI_DIGEST_SALT = "nobody%(url)suse%(text)smd5forencrypt"
EAPI_DATA_SALT = "%(url)s-36cd479b6b5-%(text)s-36cd479b6b5-%(digest)s"
EAPI_AES_KEY = "e82ckenh8dichen8"
# endregion

# region 通用工具
def random_string(length: int, chars: str = BASE62) -> str:
    return "".join(random.choice(chars) for _ in range(length))


def hex_digest(data: bytes | bytearray) -> str:
    return "".join(hex(x)[2:].zfill(2) for x in data)


def hex_compose(hexstr: str) -> bytearray:
    return bytearray(int(hexstr[i : i + 2], 16) for i in range(0, len(hexstr), 2))


def hash_hex_digest(text: str) -> str:
    return hex_digest(md5(text.encode("utf-8")).digest())


def generate_s_device_id() -> str:
    """生成登录风控所需的 sDeviceId。"""
    return f"unknown-{random.randrange(1000000)}"


def generate_chain_id(s_device_id: str) -> str:
    """生成登录风控所需的 chainId。"""
    return f"v1_{s_device_id}_web_login_{int(time.time() * 1000)}"


def decrypt_abroad_message(hexstring: str) -> str:
    """解密 overseas 响应中的 ``result`` 字段。"""
    return c_decrypt_abroad_message(hexstring)
# endregion

# region AES 基础
def _pkcs7_pad(data: str, block_size: int = AES.BLOCKSIZE) -> bytes:
    pad = block_size - len(data) % block_size
    return (data + chr(pad) * pad).encode()


def _pkcs7_unpad(data: bytes, block_size: int = AES.BLOCKSIZE) -> bytes:
    pad = data[-1]
    if pad not in range(0, block_size):
        return data  # 未填充，直接返回
    return data[:-pad]


def _aes_encrypt(data: str, key: str, iv: str = "", mode: int = AES.MODE_CBC) -> bytes:
    cipher = AES(key.encode())
    if mode == AES.MODE_CBC:
        return cipher.encrypt_cbc_nopadding(_pkcs7_pad(data), iv.encode())
    return cipher.encrypt_ecb_nopadding(_pkcs7_pad(data))


def _aes_decrypt(data: bytes, key: str, iv: str = "", mode: int = AES.MODE_CBC) -> bytes:
    cipher = AES(key.encode())
    if mode == AES.MODE_CBC:
        return _pkcs7_unpad(cipher.decrypt_cbc_nopadding(data, iv.encode()))
    return _pkcs7_unpad(cipher.decrypt_ecb_nopadding(data))


def _rsa_encrypt(data: str, n: int, e: int, reverse: bool = True) -> bytearray:
    payload = reversed(data) if reverse else data
    m = int("".join(payload).encode("utf-8").hex(), 16)
    return hex_compose(hex(pow(m, e, n))[2:].zfill(256))
# endregion

# region API 专用加密
def weapi_encrypt(params: str | dict, aes_key2: str | None = None) -> dict[str, str]:
    """实现 /weapi/ 双重 AES + RSA 的非对称加密。"""
    aes_key2 = aes_key2 or random_string(16)
    text = str(params)
    text = str(
        base64.encodebytes(
            _aes_encrypt(text, WEAPI_AES_KEY, WEAPI_AES_IV, AES.MODE_CBC)
        ),
        encoding="utf-8",
    )
    text = str(
        base64.encodebytes(
            _aes_encrypt(text, aes_key2, WEAPI_AES_IV, AES.MODE_CBC)
        ),
        encoding="utf-8",
    )
    enc_sec_key = hex_digest(_rsa_encrypt(aes_key2, *WEAPI_RSA_PUBKEY))
    return {"params": text, "encSecKey": enc_sec_key}


def eapi_encrypt(url: str, params: str | dict) -> dict[str, str]:
    """实现 /eapi/ 请求参数加密。"""
    url, params = str(url), str(params)
    digest = hash_hex_digest(EAPI_DIGEST_SALT % {"url": url, "text": params})
    plain = EAPI_DATA_SALT % {"url": url, "text": params, "digest": digest}
    return {"params": hex_digest(_aes_encrypt(plain, EAPI_AES_KEY, mode=AES.MODE_ECB))}


def eapi_decrypt(cipher: str | bytes | bytearray) -> bytes:
    """解密 /eapi/ 响应；非密文内容原样返回。"""
    if isinstance(cipher, str):
        cipher = cipher.encode()
    data = bytearray(cipher)
    if not data:
        return bytes(data)
    try:
        return _aes_decrypt(bytes(data), EAPI_AES_KEY, mode=AES.MODE_ECB)
    except Exception:
        return bytes(data)


def eapi_encrypt_session_payload(payload: dict) -> str:
    """旧版会话缓存格式：用 EAPI 加密包裹一段 JSON。"""
    return eapi_encrypt("pyncm", json.dumps(payload))["params"]
# endregion
