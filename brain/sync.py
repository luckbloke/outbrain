"""多端同步 / 备份（cordis 插件：ctx.backup）。

- 快照备份 / 恢复（sqlite backup API，运行中安全）；
- JSON 导出 / 导入（迁移到另一台机器或外部工具，按 raw 去重）。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from cordis import Service


class BackupService(Service):
    provide = "backup"
    name = "backup"
    inject = ["db", "config"]

    def __init__(self, ctx):
        super().__init__(ctx, "backup")
        self.dir = os.path.join(self.ctx.config.data_dir, "backups")
        os.makedirs(self.dir, exist_ok=True)

    def _safe_name(self, name: str) -> str:
        if not name or ".." in name or "/" in name or "\\" in name:
            raise ValueError("invalid backup name")
        return name

    def create(self, label: Optional[str] = None) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"backup_{ts}_{(label or 'auto')[:16]}.sqlite"
        dest = os.path.join(self.dir, name)
        dst = sqlite3.connect(dest)
        try:
            with self.ctx.db._lock:
                self.ctx.db.conn.backup(dst)
        finally:
            dst.close()
        self.ctx.db.audit("backup", "create", name, name)
        return name

    def list(self) -> List[Dict[str, Any]]:
        out = []
        for fn in sorted(os.listdir(self.dir), reverse=True):
            if fn.endswith(".sqlite"):
                fp = os.path.join(self.dir, fn)
                out.append({"name": fn, "size": os.path.getsize(fp),
                            "mtime": datetime.fromtimestamp(os.path.getmtime(fp)).isoformat()})
        return out

    def get(self, name: str) -> Optional[bytes]:
        name = self._safe_name(name)
        fp = os.path.join(self.dir, name)
        if not os.path.exists(fp):
            return None
        with open(fp, "rb") as f:
            return f.read()

    def restore(self, name: str) -> bool:
        name = self._safe_name(name)
        fp = os.path.join(self.dir, name)
        if not os.path.exists(fp):
            raise ValueError("backup not found")
        self.create("pre_restore")
        self.ctx.db.restore_from(fp)
        self.ctx.db.audit("backup", "restore", name, name)
        return True

    def export_json(self) -> Dict[str, Any]:
        db = self.ctx.db
        items = db.list_items({"status_in": ["open", "in_progress", "waiting", "done"]}, limit=100000)
        return {"exported_at": db.now_iso(), "version": "0.2.0",
                "projects": [dict(p) for p in db.list_projects()],
                "items": [{k: i.get(k) for k in
                           ("type", "title", "body", "raw", "source", "priority", "status",
                            "due_at", "tags", "meta", "created_at", "updated_at", "parent_id")}
                          for i in items]}

    def import_json(self, data: Dict[str, Any]) -> int:
        db = self.ctx.db
        for p in (data.get("projects") or []):
            if p.get("name"):
                db.upsert_project(p["name"], p.get("description") or "")
        imported = 0
        for it in (data.get("items") or []):
            raw = it.get("raw") or it.get("body") or it.get("title") or ""
            if not raw:
                continue
            with db._lock:
                exists = db.conn.execute("SELECT id FROM items WHERE raw=? COLLATE NOCASE", (raw,)).fetchone()
            if exists:
                continue
            db.insert_item({
                "type": it.get("type") or "note", "title": it.get("title"), "body": it.get("body"),
                "raw": raw, "source": it.get("source") or "import",
                "priority": it.get("priority") or "P2", "status": it.get("status") or "open",
                "due_at": it.get("due_at"), "tags": it.get("tags") or [],
            })
            imported += 1
        db.audit("backup", "import_json", "", f"{imported} items")
        return imported
