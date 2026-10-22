"""当前用户上下文（按线程隔离）。

HTTP 是每连接一线程；每个请求在 dispatch 开始时 set_current_user_id，结束时 clear。
db / 业务层通过 current_user_id() 读取本请求所属用户，实现多用户数据隔离。
系统线程（如提醒轮询）无用户上下文 → current_user_id()==0。
"""
import threading

_TLS = threading.local()


def current_user_id() -> int:
    return int(getattr(_TLS, "user_id", 0) or 0)


def set_current_user_id(uid) -> None:
    _TLS.user_id = int(uid or 0)


def clear_current_user_id() -> None:
    _TLS.user_id = 0


def set_current_token(tok) -> None:
    _TLS.token = tok or ""


def current_token() -> str:
    return getattr(_TLS, "token", "") or ""
