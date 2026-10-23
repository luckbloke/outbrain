"""HTTPS 信任链：自带 CA 包，让 Android 上的 Python 也能校验证书。

python-for-android 打出来的解释器里有 OpenSSL，但**没有系统 CA 库**
（没有 /etc/ssl/certs），于是 urllib 访问任何 https 都会：
    URLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed
    : unable to get local issuer certificate
这正是 APK 里「AI 润色 / AI 解析」失败而 Windows 上正常的原因。

这里把随包的 Mozilla CA 包（仓库根目录 `certs/cacert.pem`）交给 ssl/urllib：
仍然**完整校验证书**，不用 `_create_unverified_context()` 绕过——AI 网关的
API Key 走的就是这条链路，关掉校验等于把 Key 明文递给中间人。
"""
from __future__ import annotations

import os
import ssl
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.dirname(_HERE)

# 允许用环境变量覆盖（内网自签证书可以指向自己的 PEM）
_CANDIDATES = (
    os.environ.get("CORDIS_CA_BUNDLE", ""),
    os.path.join(_PROJECT, "certs", "cacert.pem"),
    os.path.join(_HERE, "cacert.pem"),      # 兼容把 CA 直接放进 brain/ 的打包方式
)


def ca_bundle() -> str:
    """返回随包 CA 的绝对路径；没有就返回空字符串（退回系统默认信任链）。"""
    for p in _CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return ""


def ensure_env() -> str:
    """把随包 CA 写进标准环境变量，让不显式传 context 的代码也能用上。"""
    p = ca_bundle()
    if p:
        os.environ.setdefault("SSL_CERT_FILE", p)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", p)  # 给将来可能接入的 requests
    return p


_CTX = None


def _embedded() -> str:
    """兜底：构建时把同一份 CA 编进 `brain/ca_data.py`（zlib+base64）。

    p4a 只按 `source.include_exts` 收文件，数据文件可能被漏掉；把证书编进 .py
    后一定能随 .pyc 进包，手机上不会因为「找不到 cacert.pem」而全盘失败。
    """
    try:
        import base64
        import zlib
        from .ca_data import DATA   # 仅 Android 构建时生成，桌面端没有也不影响
        return zlib.decompress(base64.b64decode(DATA)).decode("utf-8", "replace")
    except Exception:
        return ""


def context() -> ssl.SSLContext:
    """带随包 CA 的标准校验上下文（找不到包时退回系统默认上下文）。

    解析 118 张证书在手机上要几十毫秒，因此缓存复用。
    """
    global _CTX
    if _CTX is not None:
        return _CTX
    p = ensure_env()
    if p:
        try:
            _CTX = ssl.create_default_context(cafile=p)
            return _CTX
        except Exception:
            pass
    text = _embedded()
    if text:
        try:
            _CTX = ssl.create_default_context(cadata=text)
            return _CTX
        except Exception:
            pass
    _CTX = ssl.create_default_context()
    return _CTX


def _cert_hint(err: BaseException) -> RuntimeError:
    p = ca_bundle()
    where = p or ("（未找到 certs/cacert.pem，也没有内嵌 CA）" if not _embedded() else "内嵌 CA 加载失败")
    return RuntimeError(
        "HTTPS 证书校验失败：本机 Python 没有可用的 CA 根证书（Android 常态）。"
        f"随包 CA：{where}；原始错误：{err}")


def urlopen(req, timeout: float = 25.0):
    """带随包 CA 的 urlopen；证书问题给出可操作的中文提示，其余异常原样抛出。"""
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=context())
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
            raise _cert_hint(e.reason) from e
        raise
    except ssl.SSLCertVerificationError as e:
        raise _cert_hint(e) from e
