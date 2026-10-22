"""回顾插件（cordis 插件：ctx.review）。每日/每周自动复盘：完成 / 遗漏 / 下一步。

- 始终产出可靠的模板总结（内网无 LLM 也能用）；
- 若接入 LLM，则用其润色（不编造资料外内容）。
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict

from cordis import Service

from . import timeutil as tu


def _short(i: Dict[str, Any], n: int = 24) -> str:
    return (i.get("title") or (i.get("body") or ""))[:n]


class ReviewService(Service):
    provide = "review"
    name = "review"
    inject = ["db", "ai"]

    def __init__(self, ctx):
        super().__init__(ctx, "review")

    def daily(self, date: str = None, polish: bool = True, save: bool = True) -> Dict[str, Any]:
        date = date or tu.today().isoformat()
        db = self.ctx.db
        d = tu.from_iso(date).date() if tu.from_iso(date) else tu.today()
        added = db.list_items({"date_from": date, "date_to": date}, limit=500)
        open_items = db.list_items({"status_in": ["open", "in_progress", "waiting"]}, limit=2000)
        done_items = db.list_items({"status": "done"}, limit=2000)
        completed = [i for i in done_items if (i.get("updated_at") or "")[:10] == date]
        overdue = [i for i in open_items if i.get("due_at") and (tu.from_iso(i["due_at"]) or d).date() < d]
        due_today = [i for i in open_items
                     if i.get("due_at") and tu.from_iso(i["due_at"]) and tu.from_iso(i["due_at"]).date() == d]
        upcoming = sorted([i for i in open_items
                           if i.get("due_at") and (tu.from_iso(i["due_at"]).date() - d).days >= 0],
                          key=lambda x: x["due_at"])[:5]
        data: Dict[str, Any] = {
            "kind": "daily", "date": date, "added": added, "completed": completed,
            "overdue": overdue, "due_today": due_today, "suggested_next": upcoming,
            "counts": {"added": len(added), "completed": len(completed), "overdue": len(overdue),
                       "due_today": len(due_today), "open": len(open_items)},
        }
        template = self._template_daily(data)
        data["summary"] = self._polish(template) if polish else template
        if save:
            db.upsert_review("daily", date, data)
        return data

    def weekly(self, date: str = None, polish: bool = True, save: bool = True) -> Dict[str, Any]:
        date = date or tu.today().isoformat()
        d = tu.from_iso(date).date() if tu.from_iso(date) else tu.today()
        start = d - timedelta(days=6)
        start_iso = start.isoformat()
        db = self.ctx.db
        added = db.list_items({"date_from": start_iso, "date_to": date}, limit=2000)
        done_items = db.list_items({"status": "done"}, limit=2000)
        completed = [i for i in done_items if start_iso <= (i.get("updated_at") or "")[:10] <= date]
        open_items = db.list_items({"status_in": ["open", "in_progress", "waiting"]}, limit=2000)
        overdue = [i for i in open_items if i.get("due_at") and (tu.from_iso(i["due_at"]) or d).date() < d]
        data: Dict[str, Any] = {
            "kind": "weekly", "week": f"{start_iso} ~ {date}", "added": added,
            "completed": completed, "overdue": overdue,
            "counts": {"added": len(added), "completed": len(completed),
                       "overdue": len(overdue), "open": len(open_items)},
        }
        template = self._template_weekly(data)
        data["summary"] = self._polish(template) if polish else template
        if save:
            db.upsert_review("weekly", date, data)
        return data

    def get(self, kind: str, date: str = None):
        date = date or tu.today().isoformat()
        return self.ctx.db.get_review(kind, date)

    def list(self, kind: str = None, limit: int = 30):
        return self.ctx.db.list_reviews(kind, limit)

    def _polish(self, base_text: str) -> str:
        llm = self.ctx.ai.chat(
            "你是个人助理，用简洁中文帮我复盘，分点列出：完成、遗漏、下一步。不要编造资料里没有的内容。",
            [{"role": "user", "content": base_text}])
        return llm if llm else base_text

    def _template_daily(self, d: Dict[str, Any]) -> str:
        c = d["counts"]
        lines = [f"{d['date']} 每日复盘：新增 {c['added']} 条，完成 {c['completed']} 条，未结 {c['open']} 条。"]
        if d["completed"]:
            lines.append("完成：" + "；".join(_short(i) for i in d["completed"][:8]))
        if d["overdue"]:
            lines.append(f"逾期 {c['overdue']} 条：" + "；".join(_short(i) for i in d["overdue"][:8]))
        if d["due_today"]:
            lines.append(f"今日到期 {c['due_today']} 条：" + "；".join(_short(i) for i in d["due_today"][:8]))
        if d["suggested_next"]:
            lines.append("建议下一步：" + "；".join(_short(i) for i in d["suggested_next"]))
        return "\n".join(lines)

    def _template_weekly(self, d: Dict[str, Any]) -> str:
        c = d["counts"]
        lines = [f"{d['week']} 每周复盘：新增 {c['added']} 条，完成 {c['completed']} 条，逾期 {c['overdue']} 条。"]
        if d["completed"]:
            lines.append("完成：" + "；".join(_short(i) for i in d["completed"][:10]))
        if d["overdue"]:
            lines.append("仍逾期：" + "；".join(_short(i) for i in d["overdue"][:10]))
        return "\n".join(lines)
