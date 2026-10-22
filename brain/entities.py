"""实体图谱插件（cordis 插件：ctx.entities）。从条目抽取 人物/项目/事件/概念 并建立关系。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from cordis import Service


class EntityService(Service):
    provide = "entities"
    name = "entities"
    inject = ["db"]

    def __init__(self, ctx):
        super().__init__(ctx, "entities")

    def extract(self, item: Optional[Dict[str, Any]]):
        """从一条已解析条目抽取实体与关系（幂等，可重复调用）。"""
        if not item:
            return
        db = self.ctx.db
        meta = item.get("meta") or {}
        people = meta.get("people") or []
        projects = meta.get("projects") or []
        proj_name = item.get("project_name") or (projects[0] if projects else None)
        proj_id = db.upsert_entity("project", proj_name) if proj_name else 0

        person_ids: Dict[str, int] = {p: db.upsert_entity("person", p) for p in people if p}
        for p, pid in person_ids.items():
            if proj_id:
                db.add_relation(pid, proj_id, "参与")

        if item.get("type") == "event":
            ev_name = item.get("title") or (item.get("body") or "")[:30]
            if ev_name:
                ev_id = db.upsert_entity("event", ev_name)
                for p, pid in person_ids.items():
                    db.add_relation(pid, ev_id, "参加")
                if proj_id:
                    db.add_relation(proj_id, ev_id, "归属")

        if item.get("type") == "reference":
            cn = item.get("title") or (item.get("body") or "")[:30]
            if cn and proj_id:
                db.add_relation(proj_id, db.upsert_entity("concept", cn), "参考")

    def list(self, kind: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        return self.ctx.db.list_entities(kind, limit)

    def graph(self) -> Dict[str, Any]:
        return {"entities": self.ctx.db.list_entities(limit=200),
                "relations": self.ctx.db.list_relations()}
