"""主动建议（cordis 插件：ctx.suggestions）。

扫描 逾期 / 长期无进展 / 重复 / 重要未排期，生成可操作建议；act() 执行批量动作。
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import timedelta
from typing import Any, Dict, List, Optional

from cordis import Service

from . import timeutil as tu


class SuggestionService(Service):
    provide = "suggestions"
    name = "suggestions"
    inject = ["db"]

    def __init__(self, ctx):
        super().__init__(ctx, "suggestions")

    def suggest(self) -> List[Dict[str, Any]]:
        db = self.ctx.db
        d = tu.today()
        open_items = db.list_items({"status_in": ["open", "in_progress", "waiting"]}, limit=2000)
        out: List[Dict[str, Any]] = []
        # 习惯加权：常记项目/常用标签的事项更靠前（只影响顺序，不影响该不该提醒）
        aff = self._affinity(open_items)

        overdue = [i for i in open_items if i.get("due_at") and (tu.from_iso(i["due_at"]) or d).date() < d]
        if overdue:
            overdue.sort(key=lambda i: -self._mix(i, aff, min(self._days(i.get("due_at")), 30) / 30.0))
            out.append(self._sug("overdue", f"{len(overdue)} 条已逾期",
                                 "有期限的事项已过期，建议改期或立即完成。",
                                 [i["id"] for i in overdue[:12]], ["reschedule_next_day", "complete"]))

        stale = [i for i in open_items if self._days(i.get("updated_at") or i.get("created_at")) >= 7]
        if stale:
            stale.sort(key=lambda i: -self._mix(i, aff,
                                                min(self._days(i.get("updated_at") or i.get("created_at")), 60) / 60.0))
            out.append(self._sug("stale", f"{len(stale)} 条 7 天以上没动",
                                 "这些事项长期无进展，建议推进、改期或清空。",
                                 [i["id"] for i in stale[:12]], ["reschedule_today", "complete", "delete"]))

        for title, ids in self._duplicates(open_items)[:5]:
            ids = sorted(ids, key=lambda i: -aff.get(int(i), 0.0))
            out.append(self._sug("duplicate", "疑似重复：" + (title or "")[:24],
                                 "发现相似记录，建议合并。", ids, ["merge"]))

        nodue = [i for i in open_items if i.get("priority") in ("P0", "P1") and not i.get("due_at")]
        if nodue:
            # P0 略优先，其次看习惯分
            nodue.sort(key=lambda i: -self._mix(i, aff, 1.0 if i.get("priority") == "P0" else 0.7))
            out.append(self._sug("no_due", f"{len(nodue)} 条重要但未设期限",
                                 "P0/P1 事项没有截止时间，建议尽快排期。",
                                 [i["id"] for i in nodue[:12]], ["reschedule_today"]))
        return out

    def _affinity(self, items) -> Dict[int, float]:
        try:
            mode = str(self.ctx.config.get("habits.suggest_boost", "auto") or "auto").lower()
            if mode == "off":
                return {}
            return self.ctx.habits.affinity(items)
        except Exception as e:
            print(f"[habits] affinity error: {e}")
            return {}

    @staticmethod
    def _mix(item: Dict[str, Any], aff: Dict[int, float], urgency: float) -> float:
        """习惯分与紧迫度各占一半；没有习惯数据时就是原来的紧迫度排序。"""
        habit = aff.get(int(item["id"]), 0.0)
        return 0.5 * habit + 0.5 * urgency

    def act(self, action: str, item_ids) -> Dict[str, Any]:
        db = self.ctx.db
        ids = [int(x) for x in (item_ids or [])]
        if action == "complete":
            for i in ids:
                db.update_item(i, {"status": "done"})
        elif action in ("reschedule_today", "reschedule_next_day"):
            day = 0 if action == "reschedule_today" else 1
            iso = tu.to_iso((tu.now() + timedelta(days=day)).replace(hour=18, minute=0, second=0, microsecond=0))
            for i in ids:
                db.update_item(i, {"due_at": iso})
        elif action == "delete":
            for i in ids:
                db.delete_item(i)
        elif action == "merge":
            if ids:
                keep = max(ids)
                for i in ids:
                    if i != keep:
                        db.delete_item(i)
        db.audit("suggestion", "action", str(ids), action)
        return {"ok": True, "action": action, "count": len(ids)}

    def _sug(self, kind, title, body, ids, actions):
        h = hashlib.md5(("|".join([kind] + [str(i) for i in sorted(ids)])).encode()).hexdigest()[:10]
        return {"id": h, "kind": kind, "title": title, "body": body, "item_ids": ids, "actions": actions}

    def _days(self, iso: Optional[str]) -> int:
        dt = tu.from_iso(iso)
        return (tu.now().date() - dt.date()).days if dt else 0

    def _duplicates(self, items):
        by = defaultdict(list)
        for i in items:
            t = (i.get("title") or (i.get("body") or "")).strip()
            if len(t) < 4:
                continue
            by[t[:10].lower()].append(i)
        return [(g[0].get("title") or g[0].get("body", ""), [x["id"] for x in g])
                for g in by.values() if len(g) > 1]
