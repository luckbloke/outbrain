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
                           ("id", "type", "title", "body", "raw", "summary", "source", "priority",
                            "status", "due_at", "context", "tags", "meta", "parent_id", "project_name",
                            "created_at", "updated_at")}
                          for i in items]}

    def import_json(self, data: Dict[str, Any]) -> int:
        db = self.ctx.db
        proj_ids: Dict[str, int] = {}
        for p in (data.get("projects") or []):
            if p.get("name"):
                proj_ids[p["name"]] = db.upsert_project(p["name"], p.get("description") or "")
        imported = 0
        id_map: Dict[int, int] = {}       # 导出文件里的 id → 新库 id（用于还原子任务关系）
        parents: List[tuple] = []         # (新 id, 旧 parent_id)
        for it in (data.get("items") or []):
            raw = it.get("raw") or it.get("body") or it.get("title") or ""
            if not raw:
                continue
            with db._lock:
                exists = db.conn.execute("SELECT id FROM items WHERE raw=? COLLATE NOCASE", (raw,)).fetchone()
            if exists:
                if it.get("id") is not None:
                    id_map[int(it["id"])] = int(exists[0])
                continue
            row = {
                "type": it.get("type") or "note",
                "title": it.get("title"),
                "body": it.get("body"),
                "raw": raw,
                "summary": it.get("summary"),
                "source": it.get("source") or "import",
                "priority": it.get("priority") or "P2",
                "status": it.get("status") or "open",
                "due_at": it.get("due_at"),
                "context": it.get("context"),
                "meta": it.get("meta"),
                "tags": it.get("tags") or [],
            }
            # 保留原始时间戳（迁移后时间线不乱）
            for k in ("created_at", "updated_at"):
                if it.get(k):
                    row[k] = it[k]
            # 项目按名称重新关联（项目 id 在不同库里并不通用）
            pname = it.get("project_name")
            if pname and pname in proj_ids:
                row["project_id"] = proj_ids[pname]
            new_id = db.insert_item(row)
            imported += 1
            if it.get("id") is not None:
                id_map[int(it["id"])] = new_id
            if it.get("parent_id") is not None:
                parents.append((new_id, int(it["parent_id"])))
        # 第二遍：补回父子关系（父条目可能后导入）
        for child_id, old_parent in parents:
            new_parent = id_map.get(old_parent)
            if new_parent and new_parent != child_id:
                db.update_item(child_id, {"parent_id": new_parent})
        db.audit("backup", "import_json", "", f"{imported} items")
        return imported
