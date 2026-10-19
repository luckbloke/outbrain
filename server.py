"""Cordis 外脑 · 入口。

用法：
  python server.py              # 起 HTTP 服务（默认 127.0.0.1:8787）
  python server.py --selftest   # 进程内跑通 捕获→解析→检索→今日→问答，然后退出
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cordis import Context, set_global_ctx  # noqa: E402
from brain import timeutil  # noqa: E402
from brain.config import ConfigService  # noqa: E402
from brain.db import DbService  # noqa: E402
from brain.auth import AuthService  # noqa: E402
from brain.ai import AIGatewayService  # noqa: E402
from brain.pipeline import CaptureService, SearchService, TodayService  # noqa: E402
from brain.remind import ReminderService  # noqa: E402
from brain.notify import NotifyService  # noqa: E402
from brain.review import ReviewService  # noqa: E402
from brain.entities import EntityService  # noqa: E402
from brain.suggestions import SuggestionService  # noqa: E402
from brain.workflow import WorkflowService  # noqa: E402
from brain.sync import BackupService  # noqa: E402
from brain.io import IOService  # noqa: E402
from brain.http import HttpService  # noqa: E402

# 显式加载顺序（依赖在前）。cordis.PluginLoader 可按 inject/provide 拓扑排序，
# 这里手动排序更直观；后续插件增多时可用 loader。
PLUGINS = [
    ConfigService,
    DbService,
    AuthService,         # 认证/会话（依赖 db）
    AIGatewayService,
    EntityService,       # 图谱：person/project/event/concept
    ReminderService,     # 提醒调度（依赖 db/config）
    NotifyService,       # 通知 outbox（监听 reminder.due）
    ReviewService,       # 每日/每周回顾（依赖 db/ai）
    SuggestionService,   # 主动建议（依赖 db）
    WorkflowService,     # 拆子任务/关联项目（依赖 db）
    BackupService,       # 备份/恢复/迁移（依赖 db/config）
    IOService,           # 文件/语音解析
    CaptureService,      # 捕获+解析（调用 entities/reminder/workflow）
    SearchService,
    TodayService,
    HttpService,
]


async def build() -> Context:
    ctx = Context()
    set_global_ctx(ctx)
    for p in PLUGINS:
        await ctx.plugin(p)
        if p is ConfigService:
            timeutil.set_tz(ctx.config.get("time.timezone", "Asia/Shanghai"))
    return ctx


def selftest(ctx: Context) -> None:
    print("== 捕获（自动解析） ==")
    samples = [
        "明天下午3点和张总开会，准备项目预算方案",
        "记得周五前提交季度报告，重要",
        "买牛奶和鸡蛋",
        "如果做个个人知识库，用 sqlite 加向量会不会更好？",
        "参考：https://example.com/docs 这篇讲 RAG",
        "下周一上午10点去看牙",
    ]
    for s in samples:
        iid = ctx.capture.submit(s)
        it = ctx.db.get_item(iid)
        print(f"  [{it['type']}/{it['priority']}] {it['title']}"
              f"  due={it['due_at']} tags={it['tags']} conf={it['confidence']}")
    print("\n== 搜索 '张总 预算' ==")
    for r in ctx.search.query("张总 预算", limit=3):
        print(f"  ({r['score']}) {r['title']} :: {r['snippet']}")
    print("== 搜索 '牛奶' ==")
    for r in ctx.search.query("牛奶", limit=3):
        print(f"  ({r['score']}) {r['title']} :: {r['snippet']}")
    print("\n== 今日视图 ==")
    v = ctx.today.view()
    print(f"  逾期 {len(v['overdue'])} / 今日到期 {len(v['due_today'])} / "
          f"7天内 {len(v['upcoming'])} / 等待 {len(v['waiting'])} / 今日新增 {len(v['added_today'])}")
    print("\n== 问答 '上周和张总聊了什么，有哪些待办' ==")
    a = ctx.search.ask("上周和张总聊了什么，有哪些待办")
    print("  mode:", a["mode"])
    print("  " + a["answer"][:240].replace("\n", " | "))

    print("\n== P1：提醒 / 通知 ==")
    due_items = [i for i in ctx.db.list_items({"status": "open"}, limit=200) if i.get("due_at")]
    if due_items:
        it = due_items[0]
        ctx.db.ensure_for_item(it["id"], it["due_at"], "once")
        n = ctx.reminder.poll_once()
        print(f"  触发提醒 {n} 条；未读通知 {ctx.notify.unread_count()}")
    else:
        print("  （暂无带期限的待办，跳过提醒演示）")

    print("\n== P1：今日回顾 ==")
    rv = ctx.review.daily()
    print("  " + rv["summary"][:220].replace("\n", " | "))

    print("\n== P1：实体图谱 ==")
    g = ctx.entities.graph()
    kinds = {}
    for e in g["entities"]:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print(f"  实体 {len(g['entities'])} {kinds}，关系 {len(g['relations'])}")
    print(f"\nSELFTEST_OK  items={ctx.db.count_items()}")


def main() -> None:
    if "--selftest" in sys.argv:
        ctx = asyncio.run(build())
        try:
            selftest(ctx)
        finally:
            ctx.db.close()
        return

    ctx = asyncio.run(build())
    ctx.reminder.start()   # 启动提醒调度线程（独立于事件循环，daemon）
    host = ctx.config.get("server.host", "127.0.0.1")
    port = int(ctx.config.get("server.port", 8787))
    line = "=" * 60
    print(line)
    print("  Cordis 外脑  ·  基座 + P1（提醒/通知/回顾/图谱）")
    print(f"  界面:   http://{host}:{port}/")
    print(f"  健康:   http://{host}:{port}/api/health")
    print(f"  捕获:   POST /api/capture  {{\"text\": \"...\"}}")
    print(f"  搜索:   GET  /api/search?q=张总")
    print(f"  问答:   GET  /api/ask?q=...")
    print(f"  AI:     {ctx.ai.status()}")
    print(line)
    try:
        ctx.http.start(host, port, block=True)
    finally:
        ctx.reminder.stop()
        ctx.db.close()


if __name__ == "__main__":
    main()
