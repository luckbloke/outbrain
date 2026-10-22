"""通知插件（cordis 插件：ctx.notify）。

监听 reminder.due → 写入本地通知 outbox（可靠，前端轮询弹 toast）。
os_notify 为可替换钩子：内网默认仅记录，可接 Windows toast / 邮件 / Telegram。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from cordis import Service


class NotifyService(Service):
    provide = "notify"
    name = "notify"
    inject = ["db"]

    def __init__(self, ctx):
        super().__init__(ctx, "notify")
        ctx.on("reminder.due", self._on_due)

    def _on_due(self, r: Dict[str, Any], item: Optional[Dict[str, Any]]):
        title = (item or {}).get("title") or (r or {}).get("item_title") or "提醒"
        tag = f"（{(r or {}).get('kind')}）" if (r or {}).get("kind") not in (None, "once") else ""
        self.ctx.db.insert_notification("reminder", "提醒：" + title[:40], title + tag,
                                        (r or {}).get("item_id"), user_id=(r or {}).get("user_id"))
        self.ctx.db.audit("notify", "reminder", (r or {}).get("id"), title[:40])
        self.os_notify("提醒", title, r)

    def os_notify(self, title: str, body: str, r: Dict[str, Any]):
        """可替换为系统/IM 通知；内网可靠路径是前端轮询 outbox。"""
        return None

    def list(self, unread_only: bool = False, limit: int = 50) -> List[Dict[str, Any]]:
        return self.ctx.db.list_notifications(unread_only, limit)

    def mark_read(self, nid: int):
        self.ctx.db.mark_read(nid)

    def unread_count(self) -> int:
        return self.ctx.db.unread_count()
