"""认证插件（ctx.auth）：用户注册/登录 + token 会话 + 密码哈希。

- 纯标准库：pbkdf2_hmac(sha256, 120k 轮) + 随机 salt 存 users 表；
- 会话：随机 token 存 sessions 表，默认 7 天过期；
- 多用户隔离由 brain._userctx 按线程承载当前用户，http 中间件在请求开始/结束设置。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

from cordis import Service

from . import timeutil as tu

PBKDF2_ITER = 120000
SESSION_DAYS = 7


def _pbkdf2_manual(pw_b: bytes, salt_b: bytes, iterations: int, dklen: int = 32) -> bytes:
    """纯标准库 PBKDF2-HMAC-SHA256（无 OpenSSL/_hashlib 时的回退），与 hashlib.pbkdf2_hmac 逐字节一致。"""
    import struct
    mac = lambda key, msg: hmac.new(key, msg, hashlib.sha256).digest()
    out = b""
    idx = 1
    while len(out) < dklen:
        u = mac(pw_b, salt_b + struct.pack(">I", idx))
        t = u
        for _ in range(iterations - 1):
            u = mac(pw_b, u)
            t = bytes(a ^ b for a, b in zip(t, u))
        out += t
        idx += 1
    return out[:dklen]


def _hash(pw: str, salt: str) -> str:
    pw_b = (pw or "").encode("utf-8")
    salt_b = (salt or "").encode("utf-8")
    c = getattr(hashlib, "pbkdf2_hmac", None)
    if c is not None:
        return c("sha256", pw_b, salt_b, PBKDF2_ITER).hex()
    return _pbkdf2_manual(pw_b, salt_b, PBKDF2_ITER, 32).hex()


def _verify(pw: str, salt: str, stored: str) -> bool:
    if not stored:
        return False
    return hmac.compare_digest(_hash(pw, salt), stored)


class AuthService(Service):
    provide = "auth"
    name = "auth"
    inject = ["db"]

    def __init__(self, ctx):
        super().__init__(ctx, "auth")

    def register(self, username: str, password: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        username = (username or "").strip()
        if len(username) < 2:
            return None, "用户名至少 2 个字符"
        if len(username) > 32:
            return None, "用户名至多 32 个字符"
        if len(password or "") < 6:
            return None, "密码至少 6 位"
        salt = secrets.token_hex(16)
        uid = self.ctx.db.register_user(username, _hash(password, salt), salt)
        if uid is None:
            return None, "用户名已存在"
        self.ctx.db.claim_legacy_to_user(uid)  # 首个用户认领旧（无主）数据
        return self._issue(uid, username), None

    def login(self, username: str, password: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        u = self.ctx.db.get_user_by_name((username or "").strip())
        if not u or not _verify(password or "", u["salt"], u["pass_hash"]):
            return None, "用户名或密码错误"
        return self._issue(u["id"], u["username"]), None

    def verify(self, token: Optional[str]) -> Optional[int]:
        """返回 token 对应的 user_id；无效/过期返回 None。"""
        if not token:
            return None
        return self.ctx.db.user_id_for_token(token)

    def me(self, uid: int) -> Optional[Dict[str, Any]]:
        u = self.ctx.db.get_user(uid)
        if not u:
            return None
        return {"user_id": u["id"], "username": u["username"], "created_at": u.get("created_at")}

    def logout(self, token: str) -> None:
        if token:
            self.ctx.db.delete_session(token)

    def _issue(self, uid: int, username: str) -> Dict[str, Any]:
        token = secrets.token_hex(32)
        created = tu.to_iso(tu.now())
        expires = tu.to_iso(tu.now() + timedelta(days=SESSION_DAYS))
        self.ctx.db.create_session(token, uid, created, expires)
        return {"token": token, "user_id": uid, "username": username}
