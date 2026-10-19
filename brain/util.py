"""通用工具：JSON / 截断 / slug / 零依赖 HTTP 客户端。

只用标准库，保证内网机器开箱即用。
"""
from __future__ import annotations

import json
import re
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


def new_uuid() -> str:
    return uuid.uuid4().hex


def now_ms() -> int:
    return int(time.time() * 1000)


def json_dumps(obj: Any, **kw: Any) -> str:
    kw.setdefault("ensure_ascii", False)
    kw.setdefault("default", str)
    return json.dumps(obj, **kw)


def json_loads(text: Optional[str], default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def truncate(s: Optional[str], n: int = 80) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def slugify(s: str, max_len: int = 48) -> str:
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", s or "").strip("-").lower()
    return s[:max_len] or "item"


def clean_title(s: Optional[str], max_len: int = 48) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^[，。、；：！?\s\"'“”‘’]+", "", s)
    return s[:max_len]


def first_clause(text: str, max_len: int = 40) -> str:
    """取第一句（按中英文分句/换行切分），用于自动生成标题。"""
    if not text:
        return ""
    parts = re.split(r"[\n。！？!?；;]", text.strip())
    for p in parts:
        p = p.strip()
        if p:
            return clean_title(p, max_len)
    return clean_title(text, max_len)


def http_json(
    url: str,
    method: str = "POST",
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 25.0,
) -> Any:
    """零依赖 JSON HTTP 调用。失败抛异常，由调用方决定是否降级。"""
    data = None
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if payload is not None:
        data = json_dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    if not body:
        return None
    try:
        return json.loads(body)
    except Exception:
        return body


def http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 3.0) -> int:
    """轻量连通性探测，返回 HTTP 状态码（失败返回 -1）。"""
    try:
        req = urllib.request.Request(url, method="GET", headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return resp.getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


def ensure_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]
