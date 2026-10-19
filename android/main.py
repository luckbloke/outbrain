# -*- coding: utf-8 -*-
"""外脑 · Cordis Brain —— Android 入口（python-for-android · webview bootstrap）。

p4a 要求入口文件名必须是 `main.py`，本文件就是 APK 的 Python 入口。

p4a 的 webview bootstrap 工作方式：
  1. 在后台线程里执行本文件（PYTHONPATH = 应用私有目录）；
  2. 同时另起一个线程轮询 127.0.0.1:<port>，一旦端口可连，就把 Android WebView
     指向 http://127.0.0.1:<port>/（见 bootstraps/webview/.../WebViewLoader.tmpl.java）。

所以这里只做一件事：把「外脑」服务（cordis 插件栈 + HTTP）起在 127.0.0.1:<port> 上并阻塞。
<port> 由 `android/buildozer.spec` 的 `p4a.port` 决定，必须与下面的 PORT 一致。

与桌面端 server.py 的差别：
  - 只监听 127.0.0.1（手机本机 WebView 访问，不对外暴露）；
  - 使用随 APK 打包的 `config.toml`（默认 heuristic 离线解析，可在应用内「设置」改成内网 LLM）；
  - 数据落在应用私有目录（无需任何存储权限）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback

# ---------- 监听地址 / 端口（必须与 buildozer.spec 的 p4a.port 一致）----------
HOST = "127.0.0.1"
PORT = int(os.environ.get("CORDIS_PORT", "8787"))

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 用随包配置（存在时）；不存在就用 ConfigService 的内置默认值
_LOCAL_CONFIG = os.path.join(HERE, "config.toml")
if os.path.exists(_LOCAL_CONFIG):
    os.environ.setdefault("CORDIS_BRAIN_CONFIG", _LOCAL_CONFIG)


def log(msg: str) -> None:
    """输出到 logcat（p4a 会把 stdout/stderr 接到 Android 日志）。"""
    try:
        print(msg, flush=True)
    except Exception:
        pass


async def _build():
    """与 server.py 的 PLUGINS 顺序保持一致（依赖在前）。"""
    from cordis import Context, set_global_ctx
    from brain import timeutil
    from brain.config import ConfigService
    from brain.db import DbService
    from brain.auth import AuthService
    from brain.ai import AIGatewayService
    from brain.pipeline import CaptureService, SearchService, TodayService
    from brain.remind import ReminderService
    from brain.notify import NotifyService
    from brain.review import ReviewService
    from brain.entities import EntityService
    from brain.suggestions import SuggestionService
    from brain.workflow import WorkflowService
    from brain.sync import BackupService
    from brain.io import IOService
    from brain.http import HttpService

    plugins = [
        ConfigService,
        DbService,
        AuthService,
        AIGatewayService,
        EntityService,
        ReminderService,
        NotifyService,
        ReviewService,
        SuggestionService,
        WorkflowService,
        BackupService,
        IOService,
        CaptureService,
        SearchService,
        TodayService,
        HttpService,
    ]

    ctx = Context()
    set_global_ctx(ctx)
    for p in plugins:
        await ctx.plugin(p)
        if p is ConfigService:
            timeutil.set_tz(ctx.config.get("time.timezone", "Asia/Shanghai"))
    return ctx


def _fix_timezone(ctx) -> None:
    """Android 上没有 /usr/share/zoneinfo，zoneinfo 解析会失败 → 需要回退。

    中国时区没有夏令时，固定 UTC+8 与 Asia/Shanghai 完全等价；
    其它常见时区同理给固定偏移，避免整机时间被当成 UTC 导致到期时间差 8 小时。
    """
    from datetime import timedelta, timezone
    from brain import timeutil

    if timeutil.get_tz() is not None:
        return
    name = str(ctx.config.get("time.timezone", "Asia/Shanghai") or "").strip()
    offsets = {
        "Asia/Shanghai": 8, "Asia/Chongqing": 8, "Asia/Harbin": 8, "Asia/Urumqi": 8,
        "Asia/Hong_Kong": 8, "Asia/Macau": 8, "Asia/Taipei": 8, "Asia/Singapore": 8,
        "Etc/GMT-8": 8, "UTC": 0, "Etc/UTC": 0, "GMT": 0,
    }
    hours = offsets.get(name, 8)
    timeutil._TZ = timezone(timedelta(hours=hours), name or "UTC+8")
    timeutil._TZ_NAME = name or "UTC+8"
    log("[cordis] 无 tzdata，时区回退：%s → 固定 UTC%+d" % (name, hours))


def main() -> None:
    log("[cordis] Android 启动 … 配置=%s" % os.environ.get("CORDIS_BRAIN_CONFIG", "(内置默认)"))
    ctx = asyncio.run(_build())
    _fix_timezone(ctx)

    # 强制监听地址/端口：WebView 只会来连 127.0.0.1:<p4a.port>
    try:
        srv = ctx.config.raw.setdefault("server", {})
        srv["host"] = HOST
        srv["port"] = PORT
    except Exception:
        pass

    log("[cordis] 数据目录 = %s" % ctx.config.data_dir)
    log("[cordis] 数据库   = %s" % ctx.config.db_path)
    log("[cordis] 网页目录 = %s" % ctx.config.web_dir)
    log("[cordis] AI       = %s" % (ctx.ai.status(),))

    ctx.reminder.start()  # 提醒调度（daemon 线程）
    log("[cordis] 服务就绪：http://%s:%d/ （等待 WebView 连接）" % (HOST, PORT))
    try:
        ctx.http.start(HOST, PORT, block=True)
    finally:
        try:
            ctx.reminder.stop()
        except Exception:
            pass
        try:
            ctx.db.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        log("[cordis] 启动失败：\n" + traceback.format_exc())
        raise
