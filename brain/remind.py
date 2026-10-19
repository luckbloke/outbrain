"""提醒调度插件（cordis 插件：ctx.reminder）。

- 为 task/event 的 due_at 建提醒；支持周期(daily/weekly/interval)与稍后(snooze)。
- 后台线程轮询到期提醒 → 幂等 fire（原子 UPDATE 去重）→ emit 'reminder.due' → 周期重排。
- 完成/取消的条目自动停提醒（poll 时自愈合）。
"""
from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any, Dict, List, Optional

from cordis import Service

from . import timeutil as tu


class ReminderService(Service):
    provide = "reminder"
    name = "reminder"
    inject = ["db", "config"]

    def __init__(self, ctx):
        super().__init__(ctx, "reminder")
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---------- 调度 ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.ctx.effect(lambda: self.stop())

    def stop(self):
        self._stop.set()

    def _loop(self):
        interval = int(self.ctx.config.get("reminders.poll_interval", 20))
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as e:  # pragma: no cover
                print(f"[reminder] poll error: {e}")
            self._stop.wait(interval)

    def poll_once(self):
        """检查并触发到期提醒（可单独调用，便于测试）。返回触发数量。"""
        db = self.ctx.db
        now_iso = db.now_iso()
        fired = 0
        for r in db.due_reminders(now_iso):
            if not db.fire_reminder(r["id"], now_iso):
                continue  # 已被处理（幂等）
            item = db.get_item(r["item_id"], user_id=r.get("user_id")) if r.get("item_id") else None
            if item and item.get("status") in ("done", "canceled"):
                db.cancel_reminder(r["id"], now_iso)
                continue
            self.ctx.emit("reminder.due", r, item)
            fired += 1
            kind = r.get("kind", "once")
            if kind in ("daily", "weekly", "interval"):
                base = tu.from_iso(r.get("last_fired_at")) or tu.now()
                if kind == "daily":
                    nxt = base + timedelta(days=1)
                elif kind == "weekly":
                    nxt = base + timedelta(days=7)
                else:
                    nxt = base + timedelta(seconds=int(r.get("interval_sec") or 3600))
                db.reschedule(r["id"], tu.to_iso(nxt), now_iso)
        return fired

    # ---------- API-facing ----------
    def create(self, item_id=None, due_at=None, kind="once", interval_sec=None,
               repeat_rule=None, payload=None):
        return self.ctx.db.insert_reminder(item_id, kind, due_at, interval_sec, repeat_rule, payload)

    def ensure_for_item(self, item: Dict[str, Any]):
        if not item or item.get("status") in ("done", "canceled"):
            return None
        if item.get("type") not in ("task", "event") or not item.get("due_at"):
            return None
        return self.ctx.db.ensure_for_item(item["id"], item["due_at"], "once")

    def reset_for_item(self, item_id: int, due_at: Optional[str], kind="once"):
        """变更 due 时重建提醒：取消旧的再按新 due 建。"""
        for r in self.ctx.db.active_reminders_for_item(item_id):
            self.ctx.db.cancel_reminder(r["id"], self.ctx.db.now_iso())
        if due_at:
            return self.ctx.db.insert_reminder(item_id, kind, due_at)
        return None

    def snooze(self, rid: int, minutes: int = 30):
        until = tu.to_iso(tu.now() + timedelta(minutes=minutes))
        self.ctx.db.snooze_reminder(rid, until, self.ctx.db.now_iso())
        return self.ctx.db.get_reminder(rid)

    def cancel(self, rid: int):
        self.ctx.db.cancel_reminder(rid, self.ctx.db.now_iso())
        return self.ctx.db.get_reminder(rid)

    def list(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.ctx.db.list_reminders(state)
