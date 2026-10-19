"""HTTP 层（cordis 插件：ctx.http）。

零依赖：http.server.ThreadingHTTPServer + 迷你路由。
承载 REST API + 静态前端。线程安全由 db 的全局锁保证。
"""
from __future__ import annotations

import os
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from typing import Any, Callable, Dict, List, Optional, Tuple

from cordis import Service

from .util import json_dumps
from . import timeutil as tu
from ._userctx import (set_current_user_id, clear_current_user_id, current_user_id,
                       set_current_token, current_token)

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8"}


class FileResp:
    """文件下载响应（字节 + 文件名 + 类型）。"""
    def __init__(self, data: bytes, name: str, ctype: str = "application/octet-stream"):
        self.data = data
        self.name = name
        self.ctype = ctype


class Router:
    def __init__(self):
        self.routes: List[Tuple[str, "re.Pattern", Callable]] = []

    def add(self, method: str, path: str, handler: Callable):
        pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path)
        self.routes.append((method.upper(), re.compile("^" + pattern + "$"), handler))

    def match(self, method: str, path: str):
        for m, regex, handler in self.routes:
            if m == method.upper():
                mo = regex.match(path)
                if mo:
                    return handler, mo.groupdict()
        return None, None


class HttpService(Service):
    provide = "http"
    name = "http"
    inject = ["config", "db", "ai", "capture", "search", "today",
              "reminder", "notify", "review", "entities",
              "suggestions", "workflow", "backup", "io"]

    def __init__(self, ctx):
        super().__init__(ctx, "http")
        self.router = Router()
        r = self.router
        r.add("GET", "/api/health", self.h_health)
        r.add("GET", "/api/config", self.h_config)
        r.add("GET", "/api/settings", self.h_settings_get)
        r.add("PUT", "/api/settings", self.h_settings_put)
        r.add("POST", "/api/auth/register", self.h_auth_register)
        r.add("POST", "/api/auth/login", self.h_auth_login)
        r.add("GET", "/api/auth/me", self.h_auth_me)
        r.add("POST", "/api/auth/logout", self.h_auth_logout)
        r.add("POST", "/api/capture", self.h_capture)
        r.add("GET", "/api/items", self.h_items)
        r.add("GET", "/api/items/{id}", self.h_item)
        r.add("PATCH", "/api/items/{id}", self.h_item_patch)
        r.add("POST", "/api/items/{id}/complete", self.h_item_complete)
        r.add("DELETE", "/api/items/{id}", self.h_item_delete)
        r.add("POST", "/api/items/{id}/reparse", self.h_reparse)
        r.add("GET", "/api/search", self.h_search)
        r.add("GET", "/api/ask", self.h_ask)
        r.add("GET", "/api/today", self.h_today)
        r.add("GET", "/api/projects", self.h_projects)
        # P1：提醒 / 通知 / 回顾 / 图谱
        r.add("GET", "/api/reminders", self.h_reminders)
        r.add("POST", "/api/reminders", self.h_reminder_create)
        r.add("PATCH", "/api/reminders/{id}", self.h_reminder_patch)
        r.add("GET", "/api/notifications", self.h_notifications)
        r.add("POST", "/api/notifications/{id}/read", self.h_notify_read)
        r.add("GET", "/api/reviews/daily", self.h_review_daily)
        r.add("POST", "/api/reviews/daily", self.h_review_daily_gen)
        r.add("GET", "/api/reviews/weekly", self.h_review_weekly)
        r.add("POST", "/api/reviews/weekly", self.h_review_weekly_gen)
        r.add("GET", "/api/entities", self.h_entities)
        r.add("GET", "/api/graph", self.h_graph)
        # P2：主动建议 / 工作流 / 同步备份 / 文件语音捕获
        r.add("GET", "/api/suggestions", self.h_suggestions)
        r.add("POST", "/api/suggestions/action", self.h_suggestion_action)
        r.add("POST", "/api/items/{id}/split", self.h_split)
        r.add("GET", "/api/items/{id}/children", self.h_children)
        r.add("GET", "/api/backup", self.h_backup_list)
        r.add("POST", "/api/backup", self.h_backup_create)
        r.add("GET", "/api/backup/{name}/download", self.h_backup_download)
        r.add("POST", "/api/backup/{name}/restore", self.h_backup_restore)
        r.add("GET", "/api/export.json", self.h_export)
        r.add("POST", "/api/import.json", self.h_import)
        r.add("POST", "/api/capture/upload", self.h_capture_upload)

    # ---------- handlers: (params, body, query) -> (status, obj) ----------
    def h_health(self, p, b, q):
        return 200, {"status": "ok", "items": self.ctx.db.count_items(),
                     "ai": self.ctx.ai.status(), "version": "0.1.0"}

    def h_config(self, p, b, q):
        return 200, self.ctx.config.public()

    # ---------- 设置（全局配置，仅管理员 user_id==1）----------
    def _owner_guard(self):
        if current_user_id() != 1:
            return 403, {"error": "仅系统首个注册用户（管理员）可修改全局配置"}
        return None

    def h_settings_get(self, p, b, q):
        err = self._owner_guard()
        if err:
            return err
        return 200, self.ctx.config.settings_view()

    def h_settings_put(self, p, b, q):
        err = self._owner_guard()
        if err:
            return err
        try:
            r = self.ctx.config.update_settings(b)
        except ValueError as e:
            return 400, {"error": str(e)}
        reloaded = []
        if r.get("changed"):
            saved = r.get("saved", [])
            # AI 相关 → 热重建 provider（免重启生效）
            if any(k == "ai.provider" or k.startswith("ai.openai.") or k.startswith("ai.ollama.") for k in saved):
                try:
                    self.ctx.ai.reload(); reloaded.append("AI 引擎")
                except Exception:
                    pass
            # FTS5 → 热切换
            if "search.use_fts5" in saved:
                try:
                    self.ctx.db.set_fts5(self.ctx.config.get("search.use_fts5", "auto")); reloaded.append("FTS5 检索")
                except Exception:
                    pass
            # 时区 → 重设
            if "time.timezone" in saved:
                try:
                    tu.set_tz(self.ctx.config.get("time.timezone", "Asia/Shanghai")); reloaded.append("时区")
                except Exception:
                    pass
            # capture.provider / search.semantic 在读取时即取值，无需额外动作
        return 200, {"ok": True, "saved": r.get("saved", []), "reloaded": reloaded}

    # ---------- auth ----------
    def h_auth_register(self, p, b, q):
        result, err = self.ctx.auth.register(b.get("username"), b.get("password"))
        return (201, result) if result else (400, {"error": err})

    def h_auth_login(self, p, b, q):
        result, err = self.ctx.auth.login(b.get("username"), b.get("password"))
        return (200, result) if result else (401, {"error": err})

    def h_auth_me(self, p, b, q):
        me = self.ctx.auth.me(current_user_id())
        return (200, me) if me else (401, {"error": "未登录"})

    def h_auth_logout(self, p, b, q):
        self.ctx.auth.logout(current_token())
        return 200, {"ok": True}

    def h_capture(self, p, b, q):
        text = b.get("text") or q.get("text", "")
        item_id = self.ctx.capture.submit(text, b.get("source", "text"), b.get("meta"))
        return 201, {"id": item_id, "item": self.ctx.db.get_item(item_id)}

    def h_items(self, p, b, q):
        items = self.ctx.search.query(
            q=q.get("q", ""), type=q.get("type"), project=q.get("project"),
            tag=q.get("tag"), status=q.get("status"),
            date_from=q.get("from"), date_to=q.get("to"), limit=int(q.get("limit", 100)),
        )
        return 200, {"items": items}

    def h_item(self, p, b, q):
        it = self.ctx.db.get_item(int(p["id"]))
        return (200, it) if it else (404, {"error": "not found"})

    def h_item_patch(self, p, b, q):
        it = self.ctx.db.update_item(int(p["id"]), b)
        if it and any(k in b for k in ("due_at", "status", "type")):
            try:
                due = it.get("due_at") if it.get("status") not in ("done", "canceled") else None
                self.ctx.reminder.reset_for_item(it["id"], due, "once")
            except Exception:
                pass
            try:
                self.ctx.entities.extract(it)
            except Exception:
                pass
        return (200, it) if it else (404, {"error": "not found"})

    def h_item_complete(self, p, b, q):
        it = self.ctx.db.update_item(int(p["id"]), {"status": "done"})
        if it:
            for r in self.ctx.db.active_reminders_for_item(it["id"]):
                self.ctx.db.cancel_reminder(r["id"], self.ctx.db.now_iso())
        return (200, it) if it else (404, {"error": "not found"})

    def h_item_delete(self, p, b, q):
        ok = self.ctx.db.delete_item(int(p["id"]))
        return (200, {"deleted": ok, "id": int(p["id"])}) if ok else (404, {"error": "not found"})

    def h_reparse(self, p, b, q):
        it = self.ctx.capture.process(int(p["id"]))
        return (200, it) if it else (404, {"error": "not found"})

    def h_search(self, p, b, q):
        items = self.ctx.search.query(
            q=q.get("q", ""), type=q.get("type"), project=q.get("project"),
            tag=q.get("tag"), status=q.get("status"),
            date_from=q.get("from"), date_to=q.get("to"), limit=int(q.get("limit", 20)),
        )
        return 200, {"items": items, "count": len(items)}

    def h_ask(self, p, b, q):
        qtext = q.get("q", "")
        if not qtext:
            return 400, {"error": "q required"}
        return 200, self.ctx.search.ask(qtext, limit=int(q.get("limit", 6)))

    def h_today(self, p, b, q):
        return 200, self.ctx.today.view(q.get("date"))

    def h_projects(self, p, b, q):
        return 200, {"projects": self.ctx.db.list_projects()}

    # ---------- P1: 提醒 / 通知 ----------
    def h_reminders(self, p, b, q):
        return 200, {"reminders": self.ctx.reminder.list(q.get("state"))}

    def h_reminder_create(self, p, b, q):
        rid = self.ctx.reminder.create(
            item_id=b.get("item_id"), due_at=b.get("due_at"), kind=b.get("kind", "once"),
            interval_sec=b.get("interval_sec"), repeat_rule=b.get("repeat_rule"), payload=b.get("payload"))
        return 201, {"id": rid, "reminder": self.ctx.db.get_reminder(rid)}

    def h_reminder_patch(self, p, b, q):
        rid = int(p["id"])
        action = b.get("action") or q.get("action")
        if action == "snooze":
            return 200, self.ctx.reminder.snooze(rid, int(b.get("minutes", 30)))
        if action == "cancel":
            return 200, self.ctx.reminder.cancel(rid)
        if action == "fire":
            r = self.ctx.db.get_reminder(rid)
            if not r:
                return 404, {"error": "not found"}
            item = self.ctx.db.get_item(r["item_id"]) if r.get("item_id") else None
            self.ctx.emit("reminder.due", r, item)
            return 200, {"fired": True}
        return 400, {"error": "action required: snooze|cancel|fire"}

    def h_notifications(self, p, b, q):
        unread = q.get("unread") in ("1", "true")
        return 200, {"notifications": self.ctx.notify.list(unread),
                     "unread": self.ctx.notify.unread_count()}

    def h_notify_read(self, p, b, q):
        self.ctx.notify.mark_read(int(p["id"]))
        return 200, {"ok": True}

    # ---------- P1: 回顾 ----------
    def h_review_daily(self, p, b, q):
        # 查看：实时重算计数（未结/完成/逾期，纯 DB、很快）+ 模板总结（不跑 LLM、不覆盖缓存）→ 永远最新
        return 200, self.ctx.review.daily(q.get("date"), polish=False, save=False)

    def h_review_daily_gen(self, p, b, q):
        return 200, self.ctx.review.daily(q.get("date"))

    def h_review_weekly(self, p, b, q):
        # 查看：实时重算 + 模板总结（不跑 LLM、不覆盖缓存）
        return 200, self.ctx.review.weekly(q.get("date"), polish=False, save=False)

    def h_review_weekly_gen(self, p, b, q):
        return 200, self.ctx.review.weekly(q.get("date"))

    # ---------- P1: 图谱 ----------
    def h_entities(self, p, b, q):
        return 200, {"entities": self.ctx.entities.list(q.get("kind"))}

    def h_graph(self, p, b, q):
        return 200, self.ctx.entities.graph()

    # ---------- P2: 主动建议 ----------
    def h_suggestions(self, p, b, q):
        return 200, {"suggestions": self.ctx.suggestions.suggest()}

    def h_suggestion_action(self, p, b, q):
        r = self.ctx.suggestions.act(b.get("action"), b.get("item_ids") or q.get("item_ids"))
        return (200, r) if r.get("ok") else (400, r)

    # ---------- P2: 工作流 ----------
    def h_split(self, p, b, q):
        tasks = b.get("tasks") or []
        if isinstance(tasks, str):
            tasks = [t.strip() for t in re.split(r"[、；;，,\n]", tasks) if t.strip()]
        r = self.ctx.workflow.split(int(p["id"]), tasks)
        return (201, r) if r.get("ok") else (404, r)

    def h_children(self, p, b, q):
        return 200, {"children": self.ctx.db.list_children(int(p["id"]))}

    # ---------- P2: 同步 / 备份 / 迁移 ----------
    def _backup_guard(self):
        """整库快照是系统级操作，仅限首个注册用户（owner/admin, user_id==1）。返回 (403,err) 或 None。"""
        if current_user_id() != 1:
            return 403, {"error": "整库备份为管理员操作：仅系统首个注册用户可用"}
        return None

    def h_backup_list(self, p, b, q):
        err = self._backup_guard()
        if err:
            return err
        return 200, {"backups": self.ctx.backup.list()}

    def h_backup_create(self, p, b, q):
        err = self._backup_guard()
        if err:
            return err
        return 201, {"name": self.ctx.backup.create(b.get("label"))}

    def h_backup_download(self, p, b, q):
        err = self._backup_guard()
        if err:
            return err
        data = self.ctx.backup.get(p["name"])
        if data is None:
            return 404, {"error": "not found"}
        return 200, FileResp(data, p["name"], "application/octet-stream")

    def h_backup_restore(self, p, b, q):
        err = self._backup_guard()
        if err:
            return err
        try:
            self.ctx.backup.restore(p["name"])
        except ValueError as e:
            return 400, {"error": str(e)}
        return 200, {"ok": True}

    def h_export(self, p, b, q):
        payload = json_dumps(self.ctx.backup.export_json()).encode("utf-8")
        return 200, FileResp(payload, "brain_export.json", "application/json; charset=utf-8")

    def h_import(self, p, b, q):
        return 200, {"imported": self.ctx.backup.import_json(b)}

    # ---------- P2: 文件 / 语音 捕获 ----------
    def h_capture_upload(self, p, b, q):
        import base64
        import os as _os
        fn = b.get("filename") or "upload.bin"
        src = b.get("source") or q.get("source") or "file"
        try:
            data = base64.b64decode(b.get("content_base64") or "")
        except Exception:
            return 400, {"error": "bad base64"}
        if not data:
            return 400, {"error": "empty file"}
        voice_ext = (".mp3", ".wav", ".m4a", ".ogg", ".webm", ".aac", ".flac", ".amr")
        if src == "voice" or fn.lower().endswith(voice_ext):
            text = self.ctx.io.transcribe(fn, data)
            if text:
                item_id = self.ctx.capture.submit(text, "voice", {"filename": fn})
                return 201, {"id": item_id, "item": self.ctx.db.get_item(item_id), "asr": True}
            up = _os.path.join(self.ctx.config.data_dir, "uploads")
            _os.makedirs(up, exist_ok=True)
            from .util import slugify
            safe = (slugify(_os.path.splitext(fn)[0]) or "audio") + (_os.path.splitext(fn)[1] or ".bin")
            with open(_os.path.join(up, safe), "wb") as f:
                f.write(data)
            note = f"语音已保存（本机未配置 ASR，待转写）：{fn} → data/uploads/{safe}"
            item_id = self.ctx.capture.submit(note, "voice", {"filename": fn, "saved": f"data/uploads/{safe}"})
            return 201, {"id": item_id, "item": self.ctx.db.get_item(item_id), "asr": False}
        text = self.ctx.io.extract_text(fn, data)
        if not (text or "").strip():
            return 400, {"error": "无法从文件提取文本"}
        item_id = self.ctx.capture.submit(text, "file", {"filename": fn})
        return 201, {"id": item_id, "item": self.ctx.db.get_item(item_id)}

    # ---------- server ----------
    def _make_tls(self):
        """配置了 https_port + 证书/密钥且文件存在时返回 (port, ssl.SSLContext)，否则 (0, None)。
        只有真要启用 HTTPS 才 import ssl——这样无 _ssl 模块的环境（如远程 RHEL 的 Python）也能正常启动 HTTP。"""
        port = int(self.ctx.config.get("server.https_port", 0) or 0)
        cert = self.ctx.config.get("server.tls_cert", "") or ""
        key = self.ctx.config.get("server.tls_key", "") or ""
        if not (port and cert and key):
            return 0, None
        if not (os.path.isfile(cert) and os.path.isfile(key)):
            print(f"  [https] 证书/密钥不存在（{cert} / {key}）→ 跳过 HTTPS")
            return 0, None
        import ssl  # 延迟 import：仅启用 HTTPS 时
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        return port, ctx

    def start(self, host: str, port: int, block: bool = True):
        handler_cls = _make_handler(self.ctx, self.router, self.ctx.config.web_dir)
        self._server = ThreadingHTTPServer((host, port), handler_cls)
        self._server.daemon_threads = True
        # 可选：并行起一个 HTTPS 服务（自签名证书，供安卓 WebView / 手机安全上下文用）
        self._https_server = None
        hport, tls_ctx = self._make_tls()
        if tls_ctx is not None:
            try:
                self._https_server = _TLSServer((host, hport), handler_cls, tls_ctx)
                self._https_server.daemon_threads = True
                import threading
                threading.Thread(target=self._https_server.serve_forever, daemon=True).start()
                print(f"  界面(HTTPS): https://{host}:{hport}/   (自签名证书，客户端首次需信任)")
            except Exception as e:
                print(f"  [https] 启动失败：{e}")
                self._https_server = None
        if block:
            try:
                self._server.serve_forever()
            except KeyboardInterrupt:
                pass
        return self._server

    def stop(self):
        for _s in (getattr(self, "_server", None), getattr(self, "_https_server", None)):
            if _s is not None:
                try:
                    _s.shutdown()
                except Exception:
                    pass


class _TLSServer(ThreadingHTTPServer):
    """ThreadingHTTPServer + 每条连接包一层 TLS。握手延迟到处理线程（do_handshake_on_connect=False），
    避免阻塞 accept 主循环。"""
    def __init__(self, addr, handler_cls, tls_ctx):
        super().__init__(addr, handler_cls)
        self._tls = tls_ctx

    def get_request(self):
        req, client = super().get_request()
        return self._tls.wrap_socket(req, server_side=True, do_handshake_on_connect=False), client


def _make_handler(ctx, router: Router, web_dir: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CordisBrain/0.1"

        def log_message(self, *a):
            pass

        def _send(self, code: int, obj: Any, ctype: str = "application/json; charset=utf-8"):
            if isinstance(obj, FileResp):
                self._send_file(code, obj)
                return
            if isinstance(obj, (dict, list)):
                data = json_dumps(obj).encode("utf-8")
            elif isinstance(obj, bytes):
                data = obj
            else:
                data = str(obj).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                # 客户端提前断开（刷新/轮询被中断等）：静默关闭，不打断服务
                self.close_connection = True

        def _send_file(self, code: int, fr: "FileResp"):
            try:
                self.send_response(code)
                self.send_header("Content-Type", fr.ctype)
                self.send_header("Content-Length", str(len(fr.data)))
                self.send_header("Content-Disposition", f'attachment; filename="{fr.name}"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(fr.data)
            except OSError:
                self.close_connection = True

        def _read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length)
            try:
                import json
                return json.loads(raw.decode("utf-8-sig"))  # -sig 容忍 BOM
            except Exception:
                return {}

        def _dispatch(self, method: str):
            parsed = urlparse(self.path)
            path = parsed.path
            query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            body = self._read_body() if method in ("POST", "PATCH", "PUT") else {}
            if not path.startswith("/api/"):
                self._serve_static(path)
                return
            # ---- 鉴权中间件：/api/* 需有效 token（健康/配置/注册/登录除外）----
            clear_current_user_id()
            public = (path in ("/api/health", "/api/config")
                      or path.startswith("/api/auth/register") or path.startswith("/api/auth/login"))
            if not public:
                token = query.get("token") or ""
                ah = self.headers.get("Authorization") or ""
                if ah.lower().startswith("bearer "):
                    token = ah[7:].strip()
                uid = ctx.auth.verify(token) if token else None
                if not uid:
                    self._send(401, {"error": "未登录或登录已过期"})
                    return
                set_current_user_id(uid)
                set_current_token(token)
            handler, params = router.match(method, path)
            if not handler:
                self._send(404, {"error": "not found", "path": path})
                return
            try:
                code, obj = handler(params or {}, body, query)
                self._send(code, obj)
            except ValueError as e:
                self._send(400, {"error": str(e)})
            except Exception as e:
                traceback.print_exc()
                self._send(500, {"error": str(e)})
            finally:
                clear_current_user_id()

        def _serve_static(self, path: str):
            name = "index.html" if path in ("/", "") else os.path.basename(path)
            fp = os.path.join(web_dir, name)
            if not os.path.exists(fp):
                self._send(404, {"error": "static not found", "path": path})
                return
            with open(fp, "rb") as f:
                data = f.read()
            self._send(200, data, MIME.get(os.path.splitext(name)[1], "application/octet-stream"))

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,DELETE,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

    return Handler
