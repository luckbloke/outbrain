"""P3 回归测试：远程 ASR（OpenAI 兼容）+ 习惯画像 / 默认时间补全。

运行：
  python tests/test_asr_habits.py
不依赖外网：ASR 用本地起的假接口，习惯用临时库造数据。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SEEN = {}


class _FakeASR(BaseHTTPRequestHandler):
    """假装 /v1/audio/transcriptions：校验 multipart 并回 {"text": ...}。"""

    def log_message(self, *a):  # 静音
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        text = body.decode("latin-1")
        SEEN["path"] = self.path
        SEEN["auth"] = self.headers.get("Authorization")
        i = text.find('name="model"')
        if i >= 0:
            j = text.find("\r\n\r\n", i) + 4
            SEEN["model"] = text[j:text.find("\r\n", j)]
        i = text.find('filename="')
        if i >= 0:
            j = i + len('filename="')
            SEEN["filename"] = text[j:text.find('"', j)]
        SEEN["bytes"] = len(body)
        out = json.dumps({"text": "周五前提交季度报告"}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def _build(tmp: str):
    from cordis import Context, set_global_ctx
    from brain import timeutil
    from brain.config import ConfigService
    from brain.db import DbService
    from brain.io import IOService
    from brain.habits import HabitService
    from brain.workflow import WorkflowService
    from brain.suggestions import SuggestionService
    from brain.ai import AIGatewayService
    from brain.entities import EntityService
    from brain.notify import NotifyService
    from brain.remind import ReminderService
    from brain.pipeline import CaptureService

    async def run():
        ctx = Context()
        set_global_ctx(ctx)
        await ctx.plugin(ConfigService)
        timeutil.set_tz("Asia/Shanghai")
        cfg = ctx.config
        cfg.data_dir = os.path.join(tmp, "data")
        cfg.db_path = os.path.join(tmp, "data", "t.db")
        os.makedirs(cfg.data_dir, exist_ok=True)
        await ctx.plugin(DbService)
        await ctx.plugin(AIGatewayService)
        await ctx.plugin(IOService)
        await ctx.plugin(HabitService)
        await ctx.plugin(EntityService)
        await ctx.plugin(NotifyService)
        await ctx.plugin(ReminderService)
        await ctx.plugin(WorkflowService)
        await ctx.plugin(SuggestionService)
        await ctx.plugin(CaptureService)
        return ctx

    return asyncio.run(run())


def test_asr_habits():
    from brain import timeutil as tu

    tmp = tempfile.mkdtemp(prefix="cb_p3_")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeASR)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    ctx = _build(tmp)
    try:
        # ---------- 远程 ASR ----------
        ctx.config._raw["asr"] = {
            "provider": "auto",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "api_key": "sk-test",
            "model": "whisper-1",
            "language": "zh",
            "timeout": 20,
        }
        audio = b"\x1aE\xdf\xa3fake-webm" * 50
        text, provider, err = ctx.io.transcribe_ex("voice.webm", audio)
        assert text == "周五前提交季度报告", (text, err)
        assert provider == "openai", provider
        assert SEEN["path"] == "/v1/audio/transcriptions", SEEN["path"]
        assert SEEN["auth"] == "Bearer sk-test", SEEN["auth"]
        assert SEEN["model"] == "whisper-1", SEEN["model"]
        assert SEEN["filename"] == "voice.webm", SEEN["filename"]
        assert ctx.io.status()["available"] is True

        # 未配远程 + 无本机模块 → 给出可读原因（而不是静默 None）
        ctx.config._raw["asr"] = {"provider": "auto", "base_url": ""}
        text, provider, err = ctx.io.transcribe_ex("voice.webm", audio)
        assert text is None and "未配置" in err, (text, err)
        ctx.config._raw["asr"] = {"provider": "off"}
        text, provider, err = ctx.io.transcribe_ex("voice.webm", audio)
        assert text is None and "关闭" in err, (text, err)

        # ---------- 习惯画像 ----------
        now = tu.now()
        for i, h in enumerate([21, 21, 21, 21, 21, 21, 10, 10, 8]):
            ts = (now - timedelta(days=i)).replace(hour=h, minute=15, second=0, microsecond=0)
            ctx.db.insert_item({"type": "note", "title": f"样本{i}", "body": "x",
                                "raw": f"样本{i}", "created_at": tu.to_iso(ts)})
        prof = ctx.habits.profile(refresh=True)
        assert prof["total"] == 9, prof["total"]
        assert prof["default_hour"] == 21, prof["default_hour"]
        assert prof["peak_hour_range"].startswith("21:00"), prof["peak_hour_range"]
        assert ctx.db.kv_get("habits.profile", {}).get("total") == 9

        # ---------- 默认时间补全 ----------
        tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        parsed = {"due_at": tu.to_iso(tomorrow), "type": "task"}
        tuned = ctx.habits.apply_capture(parsed, "明天交季度报告")
        assert tu.from_iso(tuned["due_at"]).hour == 21, tuned["due_at"]
        # 显式钟点 / 上午 不覆盖
        assert ctx.habits.apply_capture(parsed, "明天下午3点交报告")["due_at"] == parsed["due_at"]
        assert ctx.habits.apply_capture(parsed, "明天上午交报告")["due_at"] == parsed["due_at"]
        # 没有日期也不凭空造
        assert ctx.habits.apply_capture({"due_at": ""}, "随手一记").get("due_at") == ""

        # ---------- 常记项目自动关联（学「标签 → 项目」） ----------
        db = ctx.db
        pid_a = db.upsert_project("星火计划")
        pid_b = db.upsert_project("杂项")
        # 4 条「工作」落在星火计划、1 条落在杂项 → 规则：工作 → 星火计划（80%）
        for i in range(4):
            db.insert_item({"type": "note", "title": f"周会{i}", "body": "同步进度",
                            "raw": f"周会{i}", "tags": ["工作"], "project_id": pid_a})
        db.insert_item({"type": "note", "title": "杂事", "body": "买水",
                        "raw": "杂事", "tags": ["工作"], "project_id": pid_b})
        # 再做 5 条「日报」也落在星火计划 → 主导项目 9/10 = 90%
        for i in range(5):
            db.insert_item({"type": "note", "title": f"日报{i}", "body": "今日进展",
                            "raw": f"日报{i}", "tags": ["日报"], "project_id": pid_a})

        prof = ctx.habits.profile(refresh=True)
        rule = (prof.get("tag_projects") or {}).get("工作") or {}
        assert rule.get("project_id") == pid_a, rule
        assert rule.get("share") >= 0.6, rule
        dom = prof.get("dominant_project") or {}
        assert dom.get("id") == pid_a and dom.get("count") == 9, dom

        g = ctx.habits.auto_project({"tags": ["工作"], "project_id": None})
        assert g and g["project_id"] == pid_a and g["source"] == "habit_tag", g
        assert "工作" in g["reason"], g
        # 没有可用标签 → 用主导项目兜底
        g2 = ctx.habits.auto_project({"tags": ["随便"], "project_id": None})
        assert g2 and g2["project_id"] == pid_a and g2["source"] == "habit_dominant", g2
        # 已经挂了项目就不动
        assert ctx.habits.auto_project({"tags": ["工作"], "project_id": pid_b}) is None
        # tags 模式：不做主导项目兜底
        ctx.config._raw.setdefault("habits", {})["auto_project"] = "tags"
        assert ctx.habits.auto_project({"tags": ["随便"], "project_id": None}) is None
        ctx.config._raw["habits"]["auto_project"] = "auto"
        # off：完全不猜
        ctx.config._raw["habits"]["auto_project"] = "off"
        assert ctx.habits.auto_project({"tags": ["工作"], "project_id": None}) is None
        ctx.config._raw["habits"]["auto_project"] = "auto"

        # ---------- 捕获落地：workflow.associate 真的写库并留痕 ----------
        iid = db.insert_item({"type": "note", "title": "下周排期", "body": "把版本发出去",
                              "raw": "下周排期", "tags": ["工作"]})
        p = ctx.workflow.associate(db.get_item(iid))
        assert p and p["id"] == pid_a, p
        it = db.get_item(iid)
        assert it["project_id"] == pid_a, it.get("project_id")
        assert (it.get("meta") or {}).get("auto_project", {}).get("source") == "habit_tag", it.get("meta")
        assert "星火计划" in (it.get("tags") or []), it.get("tags")
        # 文本里点名项目时，仍然以文本为准（source=text）
        iid2 = db.insert_item({"type": "note", "title": "杂项：买电池", "body": "杂项",
                               "raw": "杂项：买电池", "tags": ["工作"]})
        ctx.workflow.associate(db.get_item(iid2))
        it2 = db.get_item(iid2)
        assert it2["project_id"] == pid_b, it2.get("project_id")
        assert (it2.get("meta") or {}).get("auto_project", {}).get("source") == "text", it2.get("meta")

        # ---------- 后台 LLM 重解析不抹掉「自动关联」痕迹 ----------
        before = db.get_item(iid)
        ctx.capture._apply(iid, {"type": "note", "title": before["title"], "tags": [],
                                 "confidence": 0.6}, "openai", before["raw"], split=False)
        after = db.get_item(iid)
        assert after["project_id"] == pid_a, after.get("project_id")
        assert (after.get("meta") or {}).get("auto_project", {}).get("source") == "habit_tag", after.get("meta")
        assert (after.get("meta") or {}).get("refine", {}).get("state") == "done", after.get("meta")
        # 重解析明确换了项目 → 旧痕迹作废，不误导人
        ctx.capture._apply(iid, {"type": "note", "title": before["title"], "project": "杂项",
                                 "tags": [], "confidence": 0.7}, "openai", before["raw"], split=False)
        after2 = db.get_item(iid)
        assert after2["project_id"] == pid_b, after2.get("project_id")
        assert "auto_project" not in (after2.get("meta") or {}), after2.get("meta")
        # AI 网关没返回 → 落 failed 标记（前端停止转圈、显示原因与重试）
        ctx.capture._apply(iid, {"type": "note", "title": before["title"], "tags": [],
                                 "confidence": 0.6}, "heuristic", before["raw"], split=False)
        assert db.get_item(iid)["meta"]["refine"]["state"] == "pending", db.get_item(iid)["meta"]
        ctx.ai.llm_parse = lambda *a, **k: None
        ctx.ai.gw._llm_error = "AI 没有返回可解析的意图"
        ctx.capture._refine(iid, 0, before["raw"])
        meta3 = db.get_item(iid)["meta"]
        assert (meta3.get("refine") or {}).get("state") == "failed", meta3
        assert (meta3.get("refine") or {}).get("error") == "AI 没有返回可解析的意图", meta3
        # 删除该条目后 _mark_refine / _refine 不许炸
        db.delete_item(iid)
        ctx.capture._refine(iid, 0, before["raw"])

        # ---------- 建议排序加权 ----------
        now2 = tu.now()
        due = tu.to_iso((now2 - timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0))
        hi = db.insert_item({"type": "task", "title": "星火计划：补文档", "body": "x", "raw": "补文档",
                             "tags": ["工作"], "project_id": pid_a, "due_at": due, "status": "open"})
        lo = db.insert_item({"type": "task", "title": "无关：整理桌面", "body": "y", "raw": "整理桌面",
                             "due_at": due, "status": "open"})
        aff = ctx.habits.affinity(db.list_items({"status": "open"}, limit=100))
        assert aff.get(hi, 0) > aff.get(lo, 0) == 0, aff
        sug = ctx.suggestions.suggest()
        overdue = next((s for s in sug if s["kind"] == "overdue"), None)
        assert overdue, [s["kind"] for s in sug]
        assert overdue["item_ids"][0] == hi, overdue["item_ids"][:3]
        # 关掉加权 → 习惯分整体失效（建议只按紧迫度）
        ctx.config._raw["habits"]["suggest_boost"] = "off"
        assert ctx.suggestions._affinity(db.list_items({"status": "open"}, limit=100)) == {}
        ctx.config._raw["habits"]["suggest_boost"] = "auto"

        print("test_asr_habits PASSED")
    finally:
        try:
            ctx.db.close()
        except Exception:
            pass
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_legacy_kv_migration():
    """老库 kv 是「key 全局唯一」→ 两个用户写同一个 key 会撞 UNIQUE(kv.key)。

    习惯缓存最先暴露它（用户 2 写过 habits.profile 后，用户 1 再写就失败）。
    启动迁移应把 kv 重建为 (user_id, key) 唯一，且数据不丢、可重复执行。
    """
    import sqlite3

    from cordis import Context, set_global_ctx
    from brain._userctx import clear_current_user_id, set_current_user_id
    from brain.config import ConfigService
    from brain.db import DbService

    tmp = tempfile.mkdtemp(prefix="cb_kv_")
    path = os.path.join(tmp, "legacy.db")
    con = sqlite3.connect(path)
    # 线上老库就是这个形态：后加的 user_id 列 + 表级 PRIMARY KEY(key)
    con.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT, "
                "user_id INTEGER NOT NULL DEFAULT 0)")
    con.execute("INSERT INTO kv(key,value,user_id) VALUES('habits.profile','{\"total\": 9}',2)")
    con.commit()
    con.close()

    async def run():
        ctx = Context()
        set_global_ctx(ctx)
        await ctx.plugin(ConfigService)
        ctx.config.data_dir = os.path.join(tmp, "data")
        ctx.config.db_path = path
        os.makedirs(ctx.config.data_dir, exist_ok=True)
        await ctx.plugin(DbService)
        return ctx

    ctx = asyncio.run(run())
    try:
        db = ctx.db
        set_current_user_id(2)
        assert db.kv_get("habits.profile") == {"total": 9}, db.kv_get("habits.profile")
        set_current_user_id(1)
        db.kv_set("habits.profile", {"total": 3})  # 老库这里会 UNIQUE constraint failed
        assert db.kv_get("habits.profile") == {"total": 3}
        set_current_user_id(2)
        assert db.kv_get("habits.profile") == {"total": 9}
        flat = "".join(db._table_sql("kv").upper().split())
        assert "KEYTEXTPRIMARYKEY" not in flat and "UNIQUE(KEY" not in flat, flat
        # 幂等：再迁移一次也不重建、不丢数据
        db._migrate()
        assert db.kv_get("habits.profile") == {"total": 9}
        set_current_user_id(1)
        assert db.kv_get("habits.profile") == {"total": 3}
    finally:
        clear_current_user_id()
        try:
            db.close()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
    print("test_legacy_kv_migration PASSED")


if __name__ == "__main__":
    test_asr_habits()
    test_legacy_kv_migration()
