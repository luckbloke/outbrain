"""本机部署测试（自包含，进程内起真实 HTTP 服务器）。

在独立端口 + 临时数据目录上，直接跑 brain 的 ThreadingHTTPServer 与全部路由，
逐条验证：REST API 全端点 / 响应结构（对齐前端契约）/ 边界与错误 / 前端静态 /
持久化 / 并发。结束自动关服务器、清理临时目录。

运行：
  python tests/test_deploy.py
可选：CORDIS_DEPLOY_PORT=xxxx python tests/test_deploy.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PORT = int(os.environ.get("CORDIS_DEPLOY_PORT", "8799"))
BASE = f"http://127.0.0.1:{PORT}"
sys.stdout.reconfigure(encoding="utf-8")

RESULTS = []  # (name, ok, detail)
TOKEN = {"v": ""}  # 当前登录用户 token（多用户隔离测试会临时切换）


def req(method: str, path: str, body=None, timeout: int = 10):
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if TOKEN["v"]:
        headers["Authorization"] = "Bearer " + TOKEN["v"]
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.getcode(), _parse(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read())


def _parse(b):
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:
        return b.decode("utf-8", "replace")


def _a(cond, detail=""):
    if not cond:
        raise AssertionError(detail)
    return "ok"


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append((name, True, detail if detail else "ok"))
    except AssertionError as e:
        RESULTS.append((name, False, str(e)))
    except Exception as e:
        RESULTS.append((name, False, f"{type(e).__name__}: {e}"))


def build_ctx(tmp: str):
    from cordis import Context, set_global_ctx
    from brain import timeutil
    from brain.config import ConfigService
    from brain.db import DbService
    from brain.auth import AuthService
    from brain.ai import AIGatewayService
    from brain.habits import HabitService
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

    async def build():
        ctx = Context()
        set_global_ctx(ctx)
        await ctx.plugin(ConfigService)
        timeutil.set_tz("Asia/Shanghai")
        # 隔离到临时数据目录（web 仍用真实前端目录）
        cfg = ctx.config
        cfg._raw.setdefault("capture", {})["provider"] = "heuristic"  # 测试固定 heuristic（快+确定，不依赖 LLM）
        cfg._raw.setdefault("search", {})["semantic"] = "off"  # 测试关闭语义（无 embedding 模型，保持关键词确定性）
        cfg.data_dir = os.path.join(tmp, "data")
        cfg.db_path = os.path.join(tmp, "data", "brain.db")
        cfg.config_path = os.path.join(tmp, "config.toml")  # PUT /api/settings 写回落到 tmp，不碰真实 config
        os.makedirs(cfg.data_dir, exist_ok=True)
        await ctx.plugin(DbService)
        await ctx.plugin(AuthService)
        await ctx.plugin(AIGatewayService)
        await ctx.plugin(HabitService)
        await ctx.plugin(EntityService)
        await ctx.plugin(ReminderService)
        await ctx.plugin(NotifyService)
        await ctx.plugin(ReviewService)
        await ctx.plugin(SuggestionService)
        await ctx.plugin(WorkflowService)
        await ctx.plugin(BackupService)
        await ctx.plugin(IOService)
        await ctx.plugin(CaptureService)
        await ctx.plugin(SearchService)
        await ctx.plugin(TodayService)
        await ctx.plugin(HttpService)
        return ctx

    return asyncio.run(build())


def wait_health(timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            code, d = req("GET", "/api/health", timeout=2)
            if code == 200 and isinstance(d, dict) and d.get("status") == "ok":
                return True
        except Exception:
            time.sleep(0.2)
    return False


ITEM_FIELDS = ["id", "type", "title", "body", "priority", "status", "due_at",
               "tags", "confidence", "parse_provider"]


def main():
    tmp = tempfile.mkdtemp(prefix="cb_deploy_")
    ctx = None
    try:
        ctx = build_ctx(tmp)

        def _serve():
            ctx.http.start("127.0.0.1", PORT, block=True)

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        if not wait_health():
            print("DEPLOY_FAIL: 服务未能在 10s 内就绪")
            return 1

        # 1. 基础
        code, d = req("GET", "/api/health")
        check("health", lambda: _a(code == 200 and d.get("status") == "ok", f"code={code} d={d}"))
        code, d = req("GET", "/api/config")
        check("config", lambda: _a(code == 200 and d.get("version") and d.get("timezone") == "Asia/Shanghai", f"{d}"))

        # 1b. 认证 + 登录（此后所有请求带 token）
        code, d = req("GET", "/api/items")
        check("auth.required", lambda: _a(code == 401, f"未登录应 401: code={code} d={d}"))
        code, d = req("POST", "/api/auth/register", {"username": "alice", "password": "secret123"})
        check("auth.register", lambda: _a(code == 201 and (d or {}).get("token") and d.get("username") == "alice", d))
        TOKEN["v"] = (d or {}).get("token", "")
        code, d = req("POST", "/api/auth/register", {"username": "alice", "password": "secret123"})
        check("auth.register.dup", lambda: _a(code == 400, f"code={code} d={d}"))
        code, d = req("GET", "/api/auth/me")
        check("auth.me", lambda: _a(code == 200 and d.get("username") == "alice", d))
        code, d = req("POST", "/api/auth/login", {"username": "alice", "password": "wrongpass"})
        check("auth.login.bad", lambda: _a(code == 401, f"code={code} d={d}"))
        code, d = req("POST", "/api/auth/login", {"username": "alice", "password": "secret123"})
        check("auth.login", lambda: _a(code == 200 and (d or {}).get("token"), d))
        TOKEN["v"] = (d or {}).get("token", TOKEN["v"])

        # 2. 捕获
        def cap(text):
            code, d = req("POST", "/api/capture", {"text": text, "source": "text"})
            _a(code == 201, f"code={code} d={d}")
            return d["item"]

        it_meet = cap("明天下午3点和张总开会，准备项目预算方案")
        check("capture.meeting", lambda: _a(it_meet["type"] in ("event", "task") and it_meet["due_at"], it_meet))
        it_task = cap("记得周五前提交季度报告，重要")
        check("capture.task.P1", lambda: _a(it_task["priority"] == "P1" and it_task["tags"], it_task))
        it_ref = cap("参考：https://example.com/docs 讲 RAG")
        check("capture.reference", lambda: _a(it_ref["type"] == "reference", it_ref))
        code, d = req("POST", "/api/capture", {"text": ""})
        check("capture.empty.400", lambda: _a(code == 400, f"code={code} d={d}"))
        check("capture.item_shape", lambda: _a(all(k in it_meet for k in ITEM_FIELDS),
                                               f"missing={[k for k in ITEM_FIELDS if k not in it_meet]}"))
        # 条目接口带解析状态（前端停止「AI 解析中…」用的）
        code, d = req("GET", "/api/items/%d" % it_meet["id"])
        check("capture.item.refining_flag", lambda: _a(code == 200 and isinstance((d or {}).get("refining"), bool), d))

        # 3. items CRUD
        code, d = req("GET", "/api/items")
        check("items.list", lambda: _a(code == 200 and isinstance(d.get("items"), list), f"{d}"))
        code, _ = req("GET", f"/api/items/{it_meet['id']}")
        check("items.get", lambda: _a(code == 200, code))
        code, _ = req("GET", "/api/items/999999")
        check("items.404", lambda: _a(code == 404, code))
        code, d = req("PATCH", f"/api/items/{it_meet['id']}", {"priority": "P0"})
        check("items.patch", lambda: _a(code == 200 and d.get("priority") == "P0", d))
        code, d = req("POST", f"/api/items/{it_meet['id']}/complete")
        check("items.complete", lambda: _a(code == 200 and d.get("status") == "done", d))
        code, _ = req("POST", f"/api/items/{it_task['id']}/reparse")
        check("items.reparse", lambda: _a(code == 200, code))

        # 4. 搜索
        code, d = req("GET", "/api/search?q=" + urllib.parse.quote("张总 预算"))
        check("search.hit", lambda: _a(d.get("items") and d["items"][0]["id"] == it_meet["id"], d))
        code, d = req("GET", "/api/search?q=" + urllib.parse.quote("张总"))
        check("search.snippet", lambda: _a(d.get("items") and "snippet" in d["items"][0], d))
        code, d = req("GET", "/api/search?q=" + urllib.parse.quote("报告") + "&type=task")
        check("search.filter_type", lambda: _a(all(x["type"] == "task" for x in d.get("items", [])), d))
        code, d = req("GET", "/api/search")
        check("search.empty_query", lambda: _a(isinstance(d.get("items"), list) and len(d["items"]) >= 3, d))

        # 5. 问答（LLM 可能慢，超时放宽到 45s > 服务端 40s）
        code, d = req("GET", "/api/ask?q=" + urllib.parse.quote("张总"), timeout=45)
        check("ask", lambda: _a(code == 200 and d.get("mode") in ("llm", "retrieval")
                                 and isinstance(d.get("sources"), list), d))
        code, d = req("GET", "/api/ask")
        check("ask.400", lambda: _a(code == 400, f"code={code} d={d}"))

        # 6. 今日
        code, d = req("GET", "/api/today")
        need = ["date", "overdue", "due_today", "upcoming", "waiting", "added_today"]
        check("today", lambda: _a(code == 200 and all(k in d for k in need),
                                   f"missing={[k for k in need if k not in d]}"))

        # 7. 项目
        code, d = req("GET", "/api/projects")
        check("projects", lambda: _a(code == 200 and isinstance(d.get("projects"), list), d))

        # 7b. P1：提醒（捕获时自动建 + 手动 建/触发/稍后）
        code, d = req("GET", "/api/reminders")
        check("reminders.auto", lambda: _a(code == 200
                                           and any(r.get("item_id") == it_meet["id"] for r in d.get("reminders", [])),
                                           f"no auto reminder for meeting: {d}"))
        past = time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime(time.time() - 3600))
        code, d = req("POST", "/api/reminders", {"item_id": it_task["id"], "due_at": past, "kind": "once"})
        rid = (d or {}).get("id")
        check("reminder.create", lambda: _a(code == 201 and rid and d.get("reminder", {}).get("state") == "scheduled", d))
        code, d = req("PATCH", f"/api/reminders/{rid}", {"action": "fire"})
        check("reminder.fire", lambda: _a(code == 200 and d.get("fired"), d))
        code, d = req("PATCH", f"/api/reminders/{rid}", {"action": "snooze", "minutes": 30})
        check("reminder.snooze", lambda: _a(code == 200 and d.get("state") == "snoozed", d))

        # 7c. P1：通知 outbox
        code, d = req("GET", "/api/notifications")
        check("notify.outbox", lambda: _a(d.get("unread", 0) >= 1
                                          and any("提醒" in (n.get("title") or "") for n in d.get("notifications", [])), d))
        nid = d["notifications"][0]["id"]
        code, d = req("POST", f"/api/notifications/{nid}/read")
        check("notify.read", lambda: _a(code == 200 and d.get("ok"), d))

        # 7d. P1：每日/每周回顾
        code, d = req("GET", "/api/reviews/daily")
        check("review.daily", lambda: _a(code == 200 and d.get("summary")
                                         and "counts" in d and "suggested_next" in d, d))
        code, d = req("GET", "/api/reviews/weekly")
        check("review.weekly", lambda: _a(code == 200 and d.get("kind") == "weekly" and d.get("summary"), d))

        # 7e. P1：实体图谱
        code, d = req("GET", "/api/graph")
        check("graph.person_event", lambda: _a(code == 200
                                               and any(e["kind"] == "person" for e in d.get("entities", []))
                                               and d.get("relations"), d))

        # 7f. P1：AI 适配器（openai/ollama 已注册，heuristic 兜底）
        code, d = req("GET", "/api/health")
        prov = (d.get("ai") or {}).get("providers", {})
        check("ai.adapter_registered", lambda: _a("openai" in prov and "ollama" in prov and prov.get("heuristic"),
                                                  d.get("ai")))

        # 7g. P2：主动建议（造一条逾期 → 建议含 overdue + 可执行动作）
        code, d = req("POST", "/api/capture", {"text": "补交上周的报销单，待办", "source": "text"})
        oid = d["item"]["id"]
        yday = time.strftime("%Y-%m-%dT09:00:00+08:00", time.localtime(time.time() - 86400))
        req("PATCH", f"/api/items/{oid}", {"due_at": yday, "status": "open", "priority": "P1"})
        code, d = req("GET", "/api/suggestions")
        check("suggestions.overdue", lambda: _a(code == 200 and any(s.get("kind") == "overdue"
                                            and oid in s.get("item_ids", []) for s in d.get("suggestions", [])), d))
        code, d = req("POST", "/api/suggestions/action", {"action": "complete", "item_ids": [oid]})
        check("suggestions.act", lambda: _a(code == 200 and d.get("count") == 1, d))
        check("suggestions.resolved", lambda: _a(req("GET", f"/api/items/{oid}")[1].get("status") == "done", ""))

        # 7h. P2：工作流（拆子任务 + 子任务列表）
        code, d = req("POST", "/api/capture", {"text": "准备年度预算方案", "source": "text"})
        pid2 = d["item"]["id"]
        code, d = req("POST", f"/api/items/{pid2}/split", {"tasks": ["收集各部门数据", "汇总成表", "向财务汇报"]})
        check("workflow.split", lambda: _a(code == 201 and len(d.get("children", [])) == 3, d))
        code, d = req("GET", f"/api/items/{pid2}/children")
        check("workflow.children", lambda: _a(code == 200 and len(d.get("children", [])) == 3, d))

        # 7i. P2：同步 / 备份 / 迁移
        code, d = req("POST", "/api/backup", {"label": "test"})
        bname = (d or {}).get("name")
        check("backup.create", lambda: _a(code == 201 and bname, d))
        code, d = req("GET", "/api/backup")
        check("backup.list", lambda: _a(code == 200 and any(b["name"] == bname for b in d.get("backups", [])), d))
        code, exp = req("GET", "/api/export.json")
        expd = exp if isinstance(exp, dict) else {}
        check("export.json", lambda: _a(code == 200 and isinstance(expd, dict) and "items" in expd, str(exp)[:80]))
        code, d = req("POST", "/api/import.json", expd)
        check("import.json", lambda: _a(code == 200 and isinstance(d.get("imported"), int), d))

        # 7j. P2：文件 / 语音 捕获
        import base64
        txt = base64.b64encode("参考文档：外脑系统设计\n要点一：本地优先\n要点二：插件化".encode()).decode()
        code, d = req("POST", "/api/capture/upload", {"filename": "notes.txt", "content_base64": txt, "source": "file"})
        check("upload.file", lambda: _a(code == 201 and "本地优先" in ((d or {}).get("item", {}).get("body") or ""), d))
        wav = base64.b64encode(b"RIFFxxxxfake-audio-data").decode()
        code, d = req("POST", "/api/capture/upload", {"filename": "voice.webm", "content_base64": wav, "source": "voice"})
        check("upload.voice", lambda: _a(code == 201 and "待转写" in ((d or {}).get("item", {}).get("body") or ""), d))
        check("upload.voice.asr_error", lambda: _a("asr_error" in (d or {}) and (d or {}).get("asr") is False, d))

        # 7k. P3：习惯画像（常用时段 / 项目 / 标签）+ health 里的 ASR 状态
        code, d = req("GET", "/api/habits")
        prof = (d or {}).get("profile") or {}
        check("habits.profile", lambda: _a(code == 200 and prof.get("total", 0) > 0
                                           and 0 <= int(prof.get("default_hour", -1)) <= 23
                                           and isinstance(prof.get("top_projects"), list), d))
        check("habits.learning", lambda: _a(isinstance(prof.get("tag_projects"), dict)
                                            and isinstance(prof.get("project_affinity"), dict)
                                            and "dominant_project" in prof
                                            and (d or {}).get("modes", {}).get("auto_project") is not None, d))
        code, d = req("POST", "/api/habits/rebuild", {})
        check("habits.rebuild", lambda: _a(code == 200 and (d or {}).get("profile", {}).get("total", 0) > 0, d))
        code, d = req("GET", "/api/health")
        check("health.asr", lambda: _a(code == 200 and isinstance((d or {}).get("asr"), dict), d))

        # 7l. 多用户隔离（bob 看不到 alice 的数据）
        save_tok = TOKEN["v"]
        code, d = req("POST", "/api/auth/register", {"username": "bob", "password": "secret456"})
        check("auth.register.bob", lambda: _a(code == 201 and (d or {}).get("token"), d))
        TOKEN["v"] = (d or {}).get("token", "")
        code, d = req("POST", "/api/capture", {"text": "bob 的私密任务：整理发票"})
        bob_item = (d or {}).get("item", {})
        check("iso.bob_capture", lambda: _a(code == 201 and bob_item.get("id"), d))
        code, d = req("GET", f"/api/items/{it_meet['id']}")
        check("iso.bob_get_alice_item.404", lambda: _a(code == 404, f"code={code} d={d}"))
        code, d = req("GET", "/api/items")
        bob_ids = set(x["id"] for x in d.get("items", []))
        check("iso.bob_list_isolated", lambda: _a(it_meet["id"] not in bob_ids and bob_item["id"] in bob_ids,
                                                   f"bob 列表混入 alice: {bob_ids}"))
        code, d = req("DELETE", f"/api/items/{it_meet['id']}")
        check("iso.bob_delete_alice_item", lambda: _a(code == 404 or d.get("deleted") is False, f"code={code} d={d}"))
        code, d = req("GET", "/api/search?q=" + urllib.parse.quote("预算"))
        check("iso.bob_search_alice", lambda: _a(all(x["id"] != it_meet["id"] for x in d.get("items", [])), d))
        code, d = req("POST", "/api/backup", {"label": "x"})
        check("iso.bob_backup.403", lambda: _a(code == 403, f"code={code} d={d}"))
        TOKEN["v"] = save_tok  # 切回 alice

        # 7l. 设置（全局配置，仅 owner；alice=user_id 1）
        code, d = req("GET", "/api/settings")
        check("settings.get.owner", lambda: _a(code == 200 and isinstance(d, dict) and d.get("fields")
                                                and any(f["path"] == "search.semantic" for f in d["fields"]), str(d)[:80]))
        code, d = req("PUT", "/api/settings", {"search.semantic": "off"})
        check("settings.put.owner", lambda: _a(code == 200 and d.get("ok") is True
                                                and "search.semantic" in d.get("saved", []), d))
        code, d = req("GET", "/api/settings")
        sem_field = next((f for f in (d or {}).get("fields", []) if f["path"] == "search.semantic"), {})
        check("settings.roundtrip", lambda: _a(sem_field.get("value") == "off", sem_field))
        # 习惯开关也走同一套设置（select 字段，值要校验）
        hab_field = next((f for f in (d or {}).get("fields", []) if f["path"] == "habits.auto_project"), {})
        check("settings.habits.field", lambda: _a(hab_field.get("options") == ["auto", "tags", "off"], hab_field))
        code, d = req("PUT", "/api/settings", {"habits.auto_project": "tags"})
        check("settings.habits.put", lambda: _a(code == 200 and "habits.auto_project" in (d or {}).get("saved", []), d))
        code, d = req("GET", "/api/settings")
        hab2 = next((f for f in (d or {}).get("fields", []) if f["path"] == "habits.auto_project"), {})
        check("settings.habits.roundtrip", lambda: _a(hab2.get("value") == "tags", hab2))
        code, d = req("PUT", "/api/settings", {"habits.auto_project": "off"})
        check("settings.habits.put.back", lambda: _a(code == 200, d))
        code, d = req("PUT", "/api/settings", {"habits.auto_project": "bogus"})
        check("settings.habits.invalid.400", lambda: _a(code == 400, f"code={code} d={d}"))
        code, d = req("PUT", "/api/settings", {"search.semantic": "bogus"})
        check("settings.put.invalid.400", lambda: _a(code == 400, f"code={code} d={d}"))
        save_tok = TOKEN["v"]
        code, d = req("POST", "/api/auth/login", {"username": "bob", "password": "secret456"})
        TOKEN["v"] = (d or {}).get("token", "")
        code, d = req("GET", "/api/settings")
        check("settings.get.bob.403", lambda: _a(code == 403, f"code={code} d={d}"))
        code, d = req("PUT", "/api/settings", {"search.semantic": "off"})
        check("settings.put.bob.403", lambda: _a(code == 403, f"code={code} d={d}"))
        TOKEN["v"] = save_tok  # 切回 alice

        # 8. 随包 CA（Android 的 Python 没有系统 CA 库，urllib 必须用自己的证书包）
        from brain import tls
        check("tls.ca_bundle", lambda: _a(os.path.isfile(tls.ca_bundle()), f"bundle={tls.ca_bundle()}"))
        check("tls.context", lambda: _a(len(tls.context().get_ca_certs()) > 50,
                                        f"ca={len(tls.context().get_ca_certs())}"))

        # 8. 前端静态 + 契约
        code, t = req("GET", "/")
        js_src = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
        check("frontend.html", lambda: _a(code == 200 and isinstance(t, str)
                                          and "view-ask" in t
                                          and "/api/capture" in js_src and "/api/today" in js_src,
                                          "missing markers"))

        # 8b. 前端 JS 语法（可选：需 node；否则跳过）
        import shutil as _sh
        import subprocess as _sp
        import re as _re

        def _jscheck():
            node = _sh.which("node")
            if not node:
                return "skipped (no node)"
            html = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
            js = "\n".join(_re.findall(r"<script[^>]*>([\s\S]*?)</script>", html))
            tmpjs = os.path.join(tmp, "app.js")
            open(tmpjs, "w", encoding="utf-8").write(js)
            r = _sp.run([node, "--check", tmpjs], capture_output=True, timeout=30)
            _a(r.returncode == 0, (r.stderr.decode("utf-8", "replace")[:240]))
        check("frontend.js_syntax", _jscheck)

        # 9. 并发（10 条并行捕获）
        before = req("GET", "/api/health")[1]["items"]

        def one(i):
            c, _ = req("POST", "/api/capture", {"text": f"并发测试任务 {i}，明天处理"})
            return c == 201

        with ThreadPoolExecutor(max_workers=8) as ex:
            oks = list(ex.map(one, range(10)))
        after = req("GET", "/api/health")[1]["items"]
        check("concurrency.10", lambda: _a(sum(oks) == 10 and after == before + 10,
                                           f"ok={sum(oks)}/10 before={before} after={after}"))

        # 10. 持久化
        db_file = os.path.join(tmp, "data", "brain.db")
        check("persist.db_file", lambda: _a(os.path.exists(db_file), "no db file"))

        # 汇总
        passed = sum(1 for _, ok, _ in RESULTS if ok)
        print(f"部署测试 @ {BASE}   独立数据目录：{tmp}\n")
        for name, ok, detail in RESULTS:
            extra = "" if ok else f"   <- {detail}"
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}{extra}")
        print(f"\n{passed}/{len(RESULTS)} 通过")
        return 0 if passed == len(RESULTS) else 2
    finally:
        if ctx is not None:
            try:
                ctx.http.stop()
            except Exception:
                pass
            try:
                ctx.db.close()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
