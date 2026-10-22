"""工作流（cordis 插件：ctx.workflow）。拆子任务 / 自动关联项目。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from cordis import Service

from . import timeutil as tu


class WorkflowService(Service):
    provide = "workflow"
    name = "workflow"
    inject = ["db", "habits"]

    def __init__(self, ctx):
        super().__init__(ctx, "workflow")

    def split(self, item_id: int, tasks: List[str]) -> Dict[str, Any]:
        db = self.ctx.db
        parent = db.get_item(item_id)
        if not parent:
            return {"ok": False, "error": "not found"}
        created: List[int] = []
        for t in tasks:
            t = (t or "").strip()
            if not t:
                continue
            iid = db.insert_item({
                "type": "task", "title": t[:48], "body": t, "raw": t, "source": "split",
                "parent_id": item_id, "status": "open",
                "priority": parent.get("priority") or "P2",
                "project_id": parent.get("project_id"),
            })
            created.append(iid)
        if created:
            db.update_item(item_id, {"status": "in_progress"})
        return {"ok": True, "parent_id": item_id, "children": created}

    def auto_split(self, item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """捕获时：task 且解析出 ≥2 个真实子任务 → 自动拆（过滤"明天/下周"这类期限碎片）。"""
        if not item or item.get("type") != "task":
            return None
        actions = (item.get("meta") or {}).get("actions") or []
        real = [a for a in actions if len(a) >= 3 and not tu.parse_relative(a)]
        if len(actions) >= 2 and len(real) >= 2:
            return self.split(item["id"], real)
        return None

    def associate(self, item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """自动关联已有项目。

        1. 文本里点名了项目 → 直接挂上（最可靠）；
        2. 没点名 → 用习惯画像学到的「标签→项目」/ 主导项目猜一个（可在设置里关掉），
           并在 meta.auto_project 里留痕，界面上能看到「为什么挂到这个项目」。
        """
        if not item or item.get("project_id"):
            return None
        db = self.ctx.db
        text = ((item.get("title") or "") + " " + (item.get("body") or ""))
        for p in db.list_projects():
            nm = p.get("name")
            if nm and len(nm) >= 2 and nm in text:
                return self._link(item, p, "内容里提到了项目名", 0.9, "text")
        # 习惯画像兜底（保守：见 HabitService.auto_project 的阈值）
        try:
            guess = self.ctx.habits.auto_project(item)
        except Exception as e:
            print(f"[habits] auto_project error: {e}")
            guess = None
        if not guess:
            return None
        return self._link(item, {"id": guess["project_id"], "name": guess["project_name"]},
                          guess.get("reason") or "你的常用项目", guess.get("confidence", 0.4),
                          guess.get("source") or "habit")

    def _link(self, item: Dict[str, Any], project: Dict[str, Any],
              reason: str, confidence: float, source: str) -> Dict[str, Any]:
        db = self.ctx.db
        meta = dict(item.get("meta") or {})
        meta["auto_project"] = {"source": source, "reason": reason,
                                "confidence": round(float(confidence), 2)}
        tags = [t for t in (item.get("tags") or []) if isinstance(t, str)]
        nm = project.get("name") or ""
        if nm and len(nm) >= 2 and nm not in tags:
            tags.append(nm)
        db.update_item(item["id"], {"project_id": project["id"], "tags": tags, "meta": meta})
        db.audit("workflow", "project", item["id"], f"{nm} ({source})")
        return project
