"""冒烟测试：进程内跑通核心链路（不依赖 HTTP 服务，数据写入临时目录）。

运行：
  python tests/test_smoke.py
或（若装了 pytest）：
  pytest tests/ -q
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build(tmp: str):
    from cordis import Context, set_global_ctx
    from brain import timeutil
    from brain.config import ConfigService
    from brain.db import DbService
    from brain.ai import AIGatewayService
    from brain.habits import HabitService
    from brain.pipeline import CaptureService, SearchService, TodayService

    async def build():
        ctx = Context()
        set_global_ctx(ctx)
        await ctx.plugin(ConfigService)
        timeutil.set_tz("Asia/Shanghai")
        # 测试固定走离线规则（快 + 确定，不依赖外网 LLM）
        ctx.config._raw.setdefault("capture", {})["provider"] = "heuristic"
        ctx.config._raw.setdefault("search", {})["semantic"] = "off"
        ctx.config.db_path = os.path.join(tmp, "t.db")  # 隔离到临时目录
        await ctx.plugin(DbService)
        await ctx.plugin(AIGatewayService)
        await ctx.plugin(HabitService)
        await ctx.plugin(CaptureService)
        await ctx.plugin(SearchService)
        await ctx.plugin(TodayService)
        return ctx

    return asyncio.run(build())


def test_smoke():
    tmp = tempfile.mkdtemp(prefix="cb_smoke_")
    ctx = _build(tmp)
    try:
        assert ctx.db.count_items() == 0

        ctx.capture.submit("明天下午3点和张总开会，准备预算")
        ctx.capture.submit("买牛奶和鸡蛋")
        ctx.capture.submit("参考：https://x.com/doc 这篇讲 RAG")
        assert ctx.db.count_items() == 3

        item1 = ctx.db.get_item(1)
        assert item1["type"] in ("event", "task"), item1["type"]
        assert item1["due_at"], "带日期的条目应有 due_at"
        assert item1["parse_provider"] == "heuristic"

        ref = ctx.db.get_item(3)
        assert ref["type"] == "reference", ref["type"]

        r = ctx.search.query("张总 预算")
        assert r and r[0]["id"] == 1, "混合检索应命中张总条目"

        r2 = ctx.search.query("牛奶")
        assert r2 and "牛奶" in (r2[0].get("body") or ""), "中文短词应可召回"

        v = ctx.today.view()
        assert (len(v["overdue"]) + len(v["due_today"]) + len(v["upcoming"])) >= 1, "今日视图应含到期项"

        a = ctx.search.ask("张总")
        assert a["sources"], "问答应带来源"
        assert a["mode"] in ("llm", "retrieval")

        prof = ctx.habits.profile()
        assert prof["total"] == 3, prof
        assert 0 <= int(prof["default_hour"]) <= 23

        print("test_smoke PASSED")
    finally:
        ctx.db.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_smoke()
