"""SQLite 存储层（cordis 插件：ctx.db）。

- 纯标准库 sqlite3；单连接 + 全局 RLock + WAL（多线程安全）。
- FTS5 外部内容表（unicode61）+ 分词 tokens 列，配合 textproc 的 CJK 2-gram 实现中文召回。
- 数据模型向前兼容：本切片实现 items/projects/tags/capture_log/kv/audit + 检索候选；
  reminders/reviews/notifications/entities/embeddings 的表已建好，DAO 在后续扩展。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from cordis import Service

from . import timeutil as tu
from .textproc import build_fts_match, segment
from .util import ensure_list, json_dumps, json_loads
from ._userctx import current_user_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pass_hash TEXT NOT NULL,
  salt TEXT NOT NULL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT, expires_at TEXT
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL DEFAULT 0,
  name TEXT NOT NULL, description TEXT,
  status TEXT DEFAULT 'active', created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL DEFAULT 0,
  type TEXT NOT NULL DEFAULT 'inbox',
  title TEXT, body TEXT, summary TEXT, raw TEXT, source TEXT,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  priority TEXT DEFAULT 'P2', status TEXT DEFAULT 'open',
  due_at TEXT, remind_at TEXT, context TEXT, location TEXT,
   parent_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
  confidence REAL DEFAULT 0, parse_provider TEXT, meta TEXT,
  tags TEXT, tokens TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS item_tags (
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(item_id, tag_id)
);
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL, name TEXT NOT NULL, data TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS relations (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL DEFAULT 0,
  from_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  to_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  rel TEXT NOT NULL, weight REAL DEFAULT 1.0, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL DEFAULT 0,
  item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'once', due_at TEXT NOT NULL, interval_sec INTEGER,
  repeat_rule TEXT, state TEXT NOT NULL DEFAULT 'scheduled',
  last_fired_at TEXT, fired_count INTEGER DEFAULT 0, snooze_until TEXT,
  payload TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'daily', date TEXT NOT NULL,
  content TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL, title TEXT, body TEXT, item_id INTEGER,
  read INTEGER DEFAULT 0, created_at TEXT, delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS embeddings (
  item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
  provider TEXT, model TEXT, dims INTEGER, vector BLOB, created_at TEXT
);
CREATE TABLE IF NOT EXISTS capture_log (
  id INTEGER PRIMARY KEY, item_id INTEGER, text TEXT NOT NULL, source TEXT, meta TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY, action TEXT, entity TEXT, entity_id TEXT, detail TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS kv (
  user_id INTEGER NOT NULL DEFAULT 0, key TEXT, value TEXT
);
"""

ITEMS_UPDATEABLE = {
    "type", "title", "body", "summary", "raw", "source", "project_id", "priority",
    "status", "due_at", "remind_at", "context", "location", "confidence",
    "parse_provider", "meta", "parent_id",
}


def _recompute_tokens(title: str, summary: str, body: str, tags: str) -> str:
    return segment((title or "") + " " + (summary or "") + " " + (body or "") + " " + (tags or ""))


class Database:
    def __init__(self, path: str, fts5_mode: str = "auto"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA busy_timeout=8000")
            self.conn.executescript(SCHEMA)
            self.fts5_mode = str(fts5_mode or "auto").lower()
            self.fts5 = self._resolve_fts5(self.fts5_mode)
            self._init_fts()
            self._migrate()
            self.conn.commit()

    def _resolve_fts5(self, mode: str) -> bool:
        """按配置决定 FTS5：auto=sqlite 支持则用；on=要求用（不支持则降级并提示）；off=禁用（仅 LIKE）。"""
        avail = self._fts5_available()
        if mode == "off":
            return False
        if mode == "on" and not avail:
            print("[db] 配置要求启用 FTS5，但当前 sqlite 不支持，已降级为 LIKE 检索")
        return avail

    def _fts5_available(self) -> bool:
        try:
            self.conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
            self.conn.execute("DROP TABLE _fts5_probe")
            self.conn.commit()
            return True
        except Exception:
            return False

    def _init_fts(self):
        if not self.fts5:
            return  # 该 sqlite 无 FTS5（如 3.7）→ 检索退化为 LIKE，不影响其他功能
        self.conn.execute(
            'CREATE VIRTUAL TABLE IF NOT EXISTS fts_items USING fts5('
            'title, tokens, content=items, content_rowid=id, tokenize="unicode61")'
        )
        # 外部内容表同步触发器
        self.conn.execute(
            "CREATE TRIGGER IF NOT EXISTS trg_items_ai AFTER INSERT ON items BEGIN "
            "INSERT INTO fts_items(rowid,title,tokens) VALUES (new.id,new.title,new.tokens); END"
        )
        self.conn.execute(
            "CREATE TRIGGER IF NOT EXISTS trg_items_ad AFTER DELETE ON items BEGIN "
            "INSERT INTO fts_items(fts_items,rowid,title,tokens) "
            "VALUES('delete',old.id,old.title,old.tokens); END"
        )
        self.conn.execute(
            "CREATE TRIGGER IF NOT EXISTS trg_items_au AFTER UPDATE ON items BEGIN "
            "INSERT INTO fts_items(fts_items,rowid,title,tokens) "
            "VALUES('delete',old.id,old.title,old.tokens); "
            "INSERT INTO fts_items(rowid,title,tokens) VALUES (new.id,new.title,new.tokens); END"
        )

    def _migrate(self):
        """向前兼容迁移：老库补 parent_id / user_id 列（新库由 SCHEMA 直接建好）。"""
        with self._lock:
            cols = [r[1] for r in self.conn.execute("PRAGMA table_info(items)").fetchall()]
            if "parent_id" not in cols:
                self.conn.execute(
                    "ALTER TABLE items ADD COLUMN parent_id INTEGER REFERENCES items(id) ON DELETE SET NULL")
            if "user_id" not in cols:
                self.conn.execute("ALTER TABLE items ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
            for t in ("projects", "reminders", "reviews", "entities", "relations", "notifications", "kv"):
                tc = [r[1] for r in self.conn.execute(f"PRAGMA table_info({t})").fetchall()]
                if "user_id" not in tc:
                    self.conn.execute(f"ALTER TABLE {t} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
            # 列就绪后再建索引（旧库的 user_id 列此时才存在，避免 no such column）
            for stmt in (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_user_name ON projects(user_id, name)",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_user_kind_name ON entities(user_id, kind, name)",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_relations_user ON relations(user_id, from_id, to_id, rel)",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_reviews_user_kind_date ON reviews(user_id, kind, date)",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_kv_user_key ON kv(user_id, key)",
                "CREATE INDEX IF NOT EXISTS idx_items_user ON items(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_items_status ON items(status)",
                "CREATE INDEX IF NOT EXISTS idx_items_due ON items(due_at)",
                "CREATE INDEX IF NOT EXISTS idx_items_type ON items(type)",
                "CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id)",
            ):
                self.conn.execute(stmt)
            self.conn.commit()

    # ---------- 通用 ----------
    def now_iso(self) -> str:
        return tu.to_iso(tu.now())

    def _uid(self) -> int:
        """当前请求用户 id（登录用户 >0；系统线程/未鉴权 = 0 → 查询不加用户过滤）。"""
        return current_user_id()

    def _uwhere(self, alias: str = ""):
        """返回 (条件串, 参数)：有用户上下文时限定 user_id；否则空（系统线程跨用户）。"""
        uid = self._uid()
        col = (alias + ".") if alias else ""
        return (f"{col}user_id=?", uid) if uid else ("", None)

    def _to_item(self, row) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        d = dict(row)
        d["meta"] = json_loads(d.get("meta"), {}) or {}
        d["tags"] = [t for t in (d.get("tags") or "").split("|") if t]
        return d

    def _join_item(self, where: str, params: tuple) -> Optional[Dict[str, Any]]:
        sql = f"SELECT i.*, p.name AS project_name FROM items i LEFT JOIN projects p ON i.project_id=p.id WHERE {where}"
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
        return self._to_item(row)

    def close(self):
        with self._lock:
            self.conn.commit()
            self.conn.close()

    def restore_from(self, path: str):
        """用快照文件完整覆盖当前库（sqlite backup API，连接保持打开）。"""
        with self._lock:
            src = sqlite3.connect(path)
            try:
                src.backup(self.conn)
            finally:
                src.close()
            self.conn.commit()

    # ---------- items ----------
    def insert_item(self, data: Dict[str, Any]) -> int:
        d = dict(data)
        d.setdefault("type", "inbox")
        d.setdefault("status", "open")
        d.setdefault("priority", "P2")
        now = self.now_iso()
        d.setdefault("created_at", now)
        d["updated_at"] = now
        d["user_id"] = self._uid()
        if isinstance(d.get("meta"), (dict, list)):
            d["meta"] = json_dumps(d["meta"])
        tags = ensure_list(d.pop("tags_list", None) or d.get("tags"))
        d["tags"] = "|".join(str(t) for t in tags)
        d["tokens"] = _recompute_tokens(d.get("title"), d.get("summary"), d.get("body"), d["tags"])
        cols = [c for c in d if c != "id"]
        sql = f"INSERT INTO items({','.join(cols)}) VALUES({','.join('?' for _ in cols)})"
        with self._lock:
            cur = self.conn.execute(sql, [d[c] for c in cols])
            item_id = cur.lastrowid
            self.conn.commit()
        # 关联标签
        for t in tags:
            self._link_tag(item_id, str(t))
        return item_id

    def update_item(self, item_id: int, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        uid = self._uid()
        w = ("id=? AND user_id=?", (item_id, uid)) if uid else ("id=?", (item_id,))
        with self._lock:
            row = self.conn.execute(f"SELECT * FROM items WHERE {w[0]}", w[1]).fetchone()
            if not row:
                return None
            cur = dict(row)
            for k, v in fields.items():
                if v is None:
                    continue
                if k == "tags":
                    continue
                if k in ITEMS_UPDATEABLE:
                    if k == "meta" and isinstance(v, (dict, list)):
                        v = json_dumps(v)
                    cur[k] = v
            tags = fields.get("tags", cur.get("tags") or "")
            if isinstance(tags, str):
                tag_list = [t for t in tags.split("|") if t]
            else:
                tag_list = [str(t) for t in ensure_list(tags) if t]
            cur["tags"] = "|".join(tag_list)
            cur["tokens"] = _recompute_tokens(cur.get("title"), cur.get("summary"), cur.get("body"), cur["tags"])
            cur["updated_at"] = self.now_iso()
            cols = [c for c in ITEMS_UPDATEABLE | {"tags", "tokens"} if c in cur]
            sql = f"UPDATE items SET {','.join(c+'=?' for c in cols)} WHERE {w[0]}"
            self.conn.execute(sql, [cur[c] for c in cols] + list(w[1]))
            self.conn.commit()
        # 同步 item_tags
        with self._lock:
            self.conn.execute("DELETE FROM item_tags WHERE item_id=?", (item_id,))
            self.conn.commit()
        for t in tag_list:
            self._link_tag(item_id, t)
        return self.get_item(item_id)

    def get_item(self, item_id: int, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        uid = user_id if user_id is not None else self._uid()
        w = ("i.id=? AND i.user_id=?", (item_id, uid)) if uid else ("i.id=?", (item_id,))
        return self._join_item(w[0], w[1])

    def list_items(self, f: Optional[Dict[str, Any]] = None, limit: int = 200) -> List[Dict[str, Any]]:
        f = f or {}
        where, params = [], []
        uclause, uparam = self._uwhere("i")
        if uclause:
            where.append(uclause)
            params.append(uparam)
        if f.get("type"):
            where.append("i.type=?"); params.append(f["type"])
        if f.get("status"):
            where.append("i.status=?"); params.append(f["status"])
        if f.get("status_in"):
            ph = ",".join("?" for _ in f["status_in"]); where.append(f"i.status IN ({ph})"); params += list(f["status_in"])
        if f.get("project"):
            where.append("i.project_id=?"); params.append(int(f["project"]))
        if f.get("tag"):
            where.append("i.tags LIKE ?"); params.append(f"%{f['tag']}%")
        if f.get("date_from"):
            where.append("i.created_at>=?"); params.append(f["date_from"])
        if f.get("date_to"):
            where.append("i.created_at<?"); params.append(f["date_to"] + "T23:59:59+08:00")
        w = ("WHERE " + " AND ".join(where)) if where else ""
        order = f.get("order", "i.created_at DESC")
        with self._lock:
            rows = self.conn.execute(
                f"SELECT i.*, p.name AS project_name FROM items i LEFT JOIN projects p ON i.project_id=p.id {w} ORDER BY {order} LIMIT ?",
                params + [int(limit)],
            ).fetchall()
        return [self._to_item(r) for r in rows]

    def delete_item(self, item_id: int) -> bool:
        uid = self._uid()
        su = " AND user_id=?" if uid else ""
        pu = (uid,) if uid else ()
        # 级联：取消提醒 → 删除子任务（递归）→ 删除本条
        with self._lock:
            for r in self.conn.execute("SELECT id FROM reminders WHERE item_id=?", (item_id,)).fetchall():
                self.conn.execute("DELETE FROM reminders WHERE id=?", (r["id"],))
            child_ids = [r["id"] for r in
                         self.conn.execute(f"SELECT id FROM items WHERE parent_id=?{su}", (item_id,) + pu).fetchall()]
        for cid in child_ids:
            self.delete_item(cid)
        with self._lock:
            cur = self.conn.execute(f"DELETE FROM items WHERE id=?{su}", (item_id,) + pu)
            self.conn.commit()
            return cur.rowcount > 0

    def list_children(self, item_id: int) -> List[Dict[str, Any]]:
        uid = self._uid()
        su = " AND i.user_id=?" if uid else ""
        pu = (uid,) if uid else ()
        with self._lock:
            rows = self.conn.execute(
                "SELECT i.*, p.name AS project_name FROM items i LEFT JOIN projects p ON i.project_id=p.id "
                f"WHERE i.parent_id=?{su} ORDER BY i.id", (item_id,) + pu).fetchall()
        return [self._to_item(r) for r in rows]

    def count_items(self) -> int:
        u = self._uwhere("")
        sql = "SELECT COUNT(*) c FROM items" + (f" WHERE {u[0]}" if u[0] else "")
        with self._lock:
            return self.conn.execute(sql, (u[1],) if u[0] else ()).fetchone()["c"]

    # ---------- projects / tags ----------
    def upsert_project(self, name: str, description: str = "") -> int:
        name = (name or "").strip()
        if not name:
            return 0
        uid = self._uid()
        su = " AND user_id=?" if uid else ""
        pu = (uid,) if uid else ()
        with self._lock:
            row = self.conn.execute(
                f"SELECT id FROM projects WHERE name=?{su}", (name,) + pu).fetchone()
            if row:
                return row["id"]
            cur = self.conn.execute(
                "INSERT INTO projects(user_id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
                (uid, name, description, self.now_iso(), self.now_iso()),
            )
            self.conn.commit()
            return cur.lastrowid

    def list_projects(self) -> List[Dict[str, Any]]:
        u = self._uwhere("p")
        where = f"WHERE {u[0]}" if u[0] else ""
        with self._lock:
            rows = self.conn.execute(
                f"SELECT p.*, COUNT(i.id) AS item_count FROM projects p LEFT JOIN items i ON i.project_id=p.id "
                f"{where} GROUP BY p.id ORDER BY p.name", (u[1],) if u[0] else ()).fetchall()
        return [dict(r) for r in rows]

    def _upsert_tag_id(self, name: str) -> int:
        with self._lock:
            row = self.conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
            if row:
                return row["id"]
            cur = self.conn.execute("INSERT INTO tags(name,created_at) VALUES(?,?)", (name, self.now_iso()))
            self.conn.commit()
            return cur.lastrowid

    def _link_tag(self, item_id: int, name: str):
        tid = self._upsert_tag_id(name)
        with self._lock:
            self.conn.execute("INSERT OR IGNORE INTO item_tags(item_id,tag_id) VALUES(?,?)", (item_id, tid))
            self.conn.commit()

    # ---------- capture log / kv / audit ----------
    def log_capture(self, item_id: int, text: str, source: str, meta: Any):
        with self._lock:
            self.conn.execute(
                "INSERT INTO capture_log(item_id,text,source,meta,created_at) VALUES(?,?,?,?,?)",
                (item_id, text, source, json_dumps(meta) if isinstance(meta, (dict, list)) else (meta or ""), self.now_iso()),
            )
            self.conn.commit()

    def kv_set(self, key: str, value: Any):
        uid = self._uid()
        su = " AND user_id=?" if uid else ""
        pu = (uid,) if uid else ()
        with self._lock:
            v = json_dumps(value)
            if self.conn.execute(f"SELECT 1 FROM kv WHERE key=?{su}", (key,) + pu).fetchone():
                self.conn.execute(f"UPDATE kv SET value=? WHERE key=?{su}", (v, key) + pu)
            else:
                self.conn.execute("INSERT INTO kv(user_id,key,value) VALUES(?,?,?)", (uid, key, v))
            self.conn.commit()

    def kv_get(self, key: str, default: Any = None):
        uid = self._uid()
        su = " AND user_id=?" if uid else ""
        pu = (uid,) if uid else ()
        with self._lock:
            row = self.conn.execute(f"SELECT value FROM kv WHERE key=?{su}", (key,) + pu).fetchone()
        return json_loads(row["value"], default) if row else default

    def audit(self, action: str, entity: str, entity_id: Any, detail: str = ""):
        with self._lock:
            self.conn.execute("INSERT INTO audit_logs(action,entity,entity_id,detail,created_at) VALUES(?,?,?,?,?)",
                              (action, entity, str(entity_id), detail, self.now_iso()))
            self.conn.commit()

    # ---------- 用户 / 会话 ----------
    def register_user(self, username: str, pass_hash: str, salt: str) -> Optional[int]:
        with self._lock:
            if self.conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                return None
            cur = self.conn.execute(
                "INSERT INTO users(username,pass_hash,salt,created_at) VALUES(?,?,?,?)",
                (username, pass_hash, salt, self.now_iso()))
            self.conn.commit()
            return cur.lastrowid

    def get_user_by_name(self, username: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def get_user(self, uid: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None

    def create_session(self, token: str, user_id: int, created_at: str, expires_at: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                (token, user_id, created_at, expires_at))
            self.conn.commit()

    def user_id_for_token(self, token: str) -> Optional[int]:
        with self._lock:
            row = self.conn.execute(
                "SELECT user_id, expires_at FROM sessions WHERE token=?", (token,)).fetchone()
        if not row:
            return None
        exp = row["expires_at"]
        if exp:
            exp_dt = tu.from_iso(exp)
            if exp_dt and exp_dt < tu.now():
                return None
        return row["user_id"]

    def delete_session(self, token: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            self.conn.commit()

    def claim_legacy_to_user(self, uid: int) -> None:
        """首个注册用户认领 user_id=0 的遗留（无主）数据。"""
        with self._lock:
            for t in ("items", "projects", "reminders", "reviews", "entities", "relations",
                      "notifications", "kv"):
                self.conn.execute(f"UPDATE {t} SET user_id=? WHERE user_id=0", (uid,))
            self.conn.commit()

    # ---------- 检索候选（供 SearchService 融合）----------
    def fts_rank(self, query: str, limit: int = 80) -> Dict[int, float]:
        if not self.fts5:
            return {}
        expr = build_fts_match(query)
        if not expr:
            return {}
        scores: Dict[int, float] = {}
        with self._lock:
            try:
                rows = self.conn.execute(
                    "SELECT rowid, rank FROM fts_items WHERE fts_items MATCH ? ORDER BY rank LIMIT ?",
                    (expr, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return {}
        for row in rows:
            scores[row["rowid"]] = -row["rank"]  # rank 为负，取反使越大越好
        return scores

    def like_scores(self, query: str, limit: int = 300) -> Dict[int, float]:
        toks = [t for t in segment(query).split() if t]
        seen: List[str] = []
        for t in toks:
            if t not in seen:
                seen.append(t)
            if len(seen) >= 24:
                break
        scores: Dict[int, float] = {}
        if not seen:
            return scores
        uid = self._uid()
        su = " AND user_id=?" if uid else ""
        su_p = (uid,) if uid else ()
        like = ("SELECT id, ((title LIKE ?)*3 + (summary LIKE ?) + (tags LIKE ?)*2 + (body LIKE ?)) AS s "
                f"FROM items WHERE (title LIKE ? OR summary LIKE ? OR tags LIKE ? OR body LIKE ?){su} ORDER BY id LIMIT ?")
        with self._lock:
            for tok in seen:
                p = (f"%{tok}%",) * 4 + (f"%{tok}%",) * 4 + su_p + (limit,)
                try:
                    rows = self.conn.execute(like, p).fetchall()
                except sqlite3.OperationalError:
                    continue
                for r in rows:
                    s = r["s"] or 0
                    if s > scores.get(r["id"], 0):
                        scores[r["id"]] = s
        return scores

    # ---------- 向量（语义检索）----------
    @staticmethod
    def _pack_vec(v) -> bytes:
        import struct
        return struct.pack("<%df" % len(v), *v)

    @staticmethod
    def _unpack_vec(b) -> List[float]:
        import struct
        n = len(b) // 4
        return list(struct.unpack("<%df" % n, b)) if n else []

    def upsert_embedding(self, item_id, provider: str, model: str, vector) -> None:
        if not vector:
            return
        blob = self._pack_vec(vector)
        with self._lock:
            # 可移植 upsert：DELETE + INSERT（远程 sqlite 3.7 不支持 ON CONFLICT DO UPDATE）
            self.conn.execute("DELETE FROM embeddings WHERE item_id=?", (item_id,))
            self.conn.execute(
                "INSERT INTO embeddings(item_id, provider, model, dims, vector, created_at) VALUES(?,?,?,?,?,?)",
                (item_id, provider, model, len(vector), blob, self.now_iso()))
            self.conn.commit()

    def list_embeddings(self, provider: str, model: str) -> Dict[int, List[float]]:
        """当前用户、指定 (provider, model) 的全部向量（删 item 已级联删向量）。"""
        uid = self._uid()
        su = " AND i.user_id=?" if uid else ""
        su_p = (uid,) if uid else ()
        out: Dict[int, List[float]] = {}
        with self._lock:
            rows = self.conn.execute(
                "SELECT e.item_id, e.vector FROM embeddings e JOIN items i ON i.id=e.item_id "
                f"WHERE e.provider=? AND e.model=?{su}", (provider, model) + su_p).fetchall()
        for iid, blob in rows:
            try:
                out[iid] = self._unpack_vec(blob)
            except Exception:
                continue
        return out

    # ---------- reminders ----------
    def insert_reminder(self, item_id, kind="once", due_at=None, interval_sec=None,
                        repeat_rule=None, payload=None):
        d = due_at or self.now_iso()
        now = self.now_iso()
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO reminders(user_id,item_id,kind,due_at,interval_sec,repeat_rule,state,payload,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,'scheduled',?,?,?)",
                (self._uid(), item_id, kind, d, interval_sec, repeat_rule,
                 json_dumps(payload) if payload else None, now, now))
            self.conn.commit()
            return cur.lastrowid

    def get_reminder(self, rid):
        with self._lock:
            row = self.conn.execute(
                "SELECT r.*, i.title AS item_title, i.status AS item_status FROM reminders r "
                "LEFT JOIN items i ON i.id=r.item_id WHERE r.id=?", (rid,)).fetchone()
        return dict(row) if row else None

    def list_reminders(self, state=None, limit=200):
        u = self._uwhere("r")
        conds, params = [], []
        if u[0]:
            conds.append(u[0]); params.append(u[1])
        if state:
            conds.append("r.state=?"); params.append(state)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        with self._lock:
            rows = self.conn.execute(
                f"SELECT r.*, i.title AS item_title, i.status AS item_status FROM reminders r "
                f"LEFT JOIN items i ON i.id=r.item_id {where} ORDER BY r.due_at LIMIT ?",
                params + [int(limit)]).fetchall()
        return [dict(r) for r in rows]

    def active_reminders_for_item(self, item_id):
        uid = self._uid()
        su = " AND user_id=?" if uid else ""
        pu = (uid,) if uid else ()
        with self._lock:
            rows = self.conn.execute(
                f"SELECT * FROM reminders WHERE item_id=?{su} AND state IN ('scheduled','snoozed') ORDER BY due_at",
                (item_id,) + pu).fetchall()
        return [dict(r) for r in rows]

    def due_reminders(self, now_iso):
        with self._lock:
            rows = self.conn.execute(
                "SELECT r.*, i.title AS item_title, i.status AS item_status FROM reminders r "
                "LEFT JOIN items i ON i.id=r.item_id "
                "WHERE (r.state='scheduled' AND r.due_at<=?) "
                "OR (r.state='snoozed' AND r.snooze_until IS NOT NULL AND r.snooze_until<=?)",
                (now_iso, now_iso)).fetchall()
        return [dict(r) for r in rows]

    def fire_reminder(self, rid, now_iso):
        with self._lock:
            cur = self.conn.execute(
                "UPDATE reminders SET state='fired', last_fired_at=?, fired_count=fired_count+1, updated_at=? "
                "WHERE id=? AND state IN ('scheduled','snoozed')", (now_iso, now_iso, rid))
            self.conn.commit()
            return cur.rowcount > 0

    def reschedule(self, rid, next_due, now_iso):
        with self._lock:
            self.conn.execute(
                "UPDATE reminders SET due_at=?, state='scheduled', snooze_until=NULL, updated_at=? WHERE id=?",
                (next_due, now_iso, rid))
            self.conn.commit()

    def snooze_reminder(self, rid, until, now_iso):
        with self._lock:
            self.conn.execute(
                "UPDATE reminders SET state='snoozed', snooze_until=?, updated_at=? "
                "WHERE id=? AND state IN ('scheduled','snoozed','fired')", (until, now_iso, rid))
            self.conn.commit()

    def cancel_reminder(self, rid, now_iso):
        with self._lock:
            self.conn.execute(
                "UPDATE reminders SET state='canceled', updated_at=? "
                "WHERE id=? AND state IN ('scheduled','snoozed')", (now_iso, rid))
            self.conn.commit()

    def ensure_for_item(self, item_id, due_at, kind="once"):
        if not due_at:
            return None
        if self.active_reminders_for_item(item_id):
            return None
        return self.insert_reminder(item_id, kind, due_at)

    # ---------- reviews ----------
    def upsert_review(self, kind, date, content):
        now = self.now_iso()
        c = json_dumps(content)
        uid = self._uid()
        su = " AND user_id=?" if uid else ""
        pu = (uid,) if uid else ()
        with self._lock:
            if self.conn.execute(f"SELECT 1 FROM reviews WHERE kind=? AND date=?{su}", (kind, date) + pu).fetchone():
                self.conn.execute(f"UPDATE reviews SET content=?, created_at=? WHERE kind=? AND date=?{su}",
                                  (c, now, kind, date) + pu)
            else:
                self.conn.execute(
                    "INSERT INTO reviews(user_id,kind,date,content,created_at) VALUES(?,?,?,?,?)",
                    (uid, kind, date, c, now))
            self.conn.commit()

    def get_review(self, kind, date):
        uid = self._uid()
        su = " AND user_id=?" if uid else ""
        pu = (uid,) if uid else ()
        with self._lock:
            row = self.conn.execute(f"SELECT * FROM reviews WHERE kind=? AND date=?{su}", (kind, date) + pu).fetchone()
        return json_loads(row["content"], None) if row else None

    def list_reviews(self, kind=None, limit=30):
        u = self._uwhere("")
        conds, params = [], []
        if u[0]:
            conds.append(u[0]); params.append(u[1])
        if kind:
            conds.append("kind=?"); params.append(kind)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        with self._lock:
            rows = self.conn.execute(
                f"SELECT id,kind,date,created_at FROM reviews {where} ORDER BY date DESC LIMIT ?",
                params + [int(limit)]).fetchall()
        return [dict(r) for r in rows]

    # ---------- notifications ----------
    def insert_notification(self, kind, title, body, item_id=None, user_id=None):
        now = self.now_iso()
        uid = user_id if user_id is not None else self._uid()
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO notifications(user_id,kind,title,body,item_id,read,created_at) VALUES(?,?,?,?,?,0,?)",
                (uid, kind, title, body, item_id, now))
            self.conn.commit()
            return cur.lastrowid

    def list_notifications(self, unread_only=False, limit=50):
        u = self._uwhere("")
        conds, params = [], []
        if u[0]:
            conds.append(u[0]); params.append(u[1])
        if unread_only:
            conds.append("read=0")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        with self._lock:
            rows = self.conn.execute(
                f"SELECT * FROM notifications {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                params + [int(limit)]).fetchall()
        return [dict(r) for r in rows]

    def mark_read(self, nid):
        with self._lock:
            self.conn.execute("UPDATE notifications SET read=1 WHERE id=?", (nid,))
            self.conn.commit()

    def unread_count(self):
        u = self._uwhere("")
        cond = " AND " + u[0] if u[0] else ""
        with self._lock:
            return self.conn.execute(
                f"SELECT COUNT(*) c FROM notifications WHERE read=0{cond}",
                (u[1],) if u[0] else ()).fetchone()["c"]

    # ---------- entities / relations ----------
    def upsert_entity(self, kind, name, data=None):
        name = (name or "").strip()
        if not name:
            return 0
        now = self.now_iso()
        uid = self._uid()
        su = " AND user_id=?" if uid else ""
        pu = (uid,) if uid else ()
        with self._lock:
            row = self.conn.execute(
                f"SELECT id FROM entities WHERE kind=? AND name=?{su}", (kind, name) + pu).fetchone()
            if row:
                return row["id"]
            cur = self.conn.execute(
                "INSERT INTO entities(user_id,kind,name,data,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (uid, kind, name, json_dumps(data) if data else None, now, now))
            self.conn.commit()
            return cur.lastrowid

    def list_entities(self, kind=None, limit=200):
        u = self._uwhere("e")
        conds, params = [], []
        if u[0]:
            conds.append(u[0]); params.append(u[1])
        if kind:
            conds.append("e.kind=?"); params.append(kind)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        with self._lock:
            rows = self.conn.execute(
                f"SELECT e.*, (SELECT COUNT(*) FROM relations rl WHERE rl.from_id=e.id OR rl.to_id=e.id) AS deg "
                f"FROM entities e {where} ORDER BY deg DESC, e.name LIMIT ?",
                params + [int(limit)]).fetchall()
        return [dict(r) for r in rows]

    def add_relation(self, from_id, to_id, rel, weight=1.0, note=None):
        if not from_id or not to_id or from_id == to_id:
            return
        now = self.now_iso()
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO relations(user_id,from_id,to_id,rel,weight,note,created_at) VALUES(?,?,?,?,?,?,?)",
                (self._uid(), from_id, to_id, rel, weight, note, now))
            self.conn.commit()

    def list_relations(self):
        u = self._uwhere("rl")
        where = f"WHERE {u[0]}" if u[0] else ""
        with self._lock:
            rows = self.conn.execute(
                "SELECT rl.rel, rl.weight, rl.note, fa.id AS from_id, fa.name AS from_name, fa.kind AS from_kind, "
                "ta.id AS to_id, ta.name AS to_name, ta.kind AS to_kind "
                f"FROM relations rl JOIN entities fa ON fa.id=rl.from_id JOIN entities ta ON ta.id=rl.to_id {where}",
                (u[1],) if u[0] else ()).fetchall()
        return [dict(r) for r in rows]


class DbService(Service):
    provide = "db"
    name = "db"
    inject = ["config"]

    def __init__(self, ctx):
        super().__init__(ctx, "db")
        self.db = Database(self.ctx.config.db_path,
                           fts5_mode=self.ctx.config.get("search.use_fts5", "auto"))

    def set_fts5(self, mode: str) -> bool:
        """热切换 FTS5（改 search.use_fts5 后调用）：重解析标志，启用则确保 FTS 表/触发器存在（幂等）。"""
        with self.db._lock:
            self.db.fts5_mode = str(mode or "auto").lower()
            self.db.fts5 = self.db._resolve_fts5(self.db.fts5_mode)
            if self.db.fts5:
                self.db._init_fts()
            self.db.conn.commit()
        return self.db.fts5

    def __getattr__(self, name):
        # 让 ctx.db.xxx 直接落到 Database 的方法上
        return getattr(self.db, name)
