"""习惯 / 偏好画像（cordis 插件：ctx.habits）。

从自己的捕获记录里统计「你通常在什么时间记东西、常用哪些项目/标签/类型」，
用一个很轻的画像反过来帮解析更懂你：

- 只读本地库，零依赖，结果缓存在 kv（默认 30 分钟，可手动刷新）；
- 目前落地四处：
  1. 「记下」时时间只有日期（如“明天交报告”）→ 用你的常用时间补全，而不是一律 9:00；
  2. 没点名项目时按学到的「标签→项目」或主导项目自动关联（`meta.auto_project` 留痕，可关）；
  3. 建议列表按「习惯分 + 紧迫度」加权排序（可关）；
  4. 「数据」页的习惯画像卡片（常用时段 / 项目 / 标签 / 类型分布 + 学到的规则）。

画像只在本用户范围内统计（db 层按 user_id 过滤），不会串号。
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta
from typing import Any, Dict, List, Optional

from cordis import Service

from . import timeutil as tu

# 显式写了钟点（下午3点 / 15:30 / 八点半）就不该被习惯覆盖
_HOUR_RE = re.compile(r"(\d{1,2})\s*[点时:：]|[一二两三四五六七八九十]{1,3}\s*[点时]")
# 明确说了“早上/上午”的，9:00 是语义本身，也不覆盖
_MORNING_RE = re.compile(r"(早上|上午|早晨|清晨|凌晨)")
_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 只统计最近 N 天的记录：太久远的习惯参考价值低，也会拖慢统计
WINDOW_DAYS = 120
# 画像缓存有效期（秒）
TTL = 1800


class HabitService(Service):
    provide = "habits"
    name = "habits"
    inject = ["db"]

    def __init__(self, ctx):
        super().__init__(ctx, "habits")

    # ---------------- 读取 / 构建 ----------------
    def profile(self, refresh: bool = False) -> Dict[str, Any]:
        cached = None
        try:
            cached = self.ctx.db.kv_get("habits.profile")
        except Exception:
            cached = None
        if isinstance(cached, dict) and cached and not refresh:
            built = tu.from_iso(cached.get("built_at"))
            if built and (tu.now() - built).total_seconds() < TTL:
                return cached
        prof = self.build()
        try:
            self.ctx.db.kv_set("habits.profile", prof)
        except Exception as e:
            print(f"[habits] 缓存写入失败：{e}")
        return prof

    def rebuild(self) -> Dict[str, Any]:
        return self.profile(refresh=True)

    def build(self) -> Dict[str, Any]:
        db = self.ctx.db
        now = tu.now()
        cutoff = now - timedelta(days=WINDOW_DAYS)
        try:
            items = db.list_items({}, limit=5000)
        except Exception as e:
            print(f"[habits] 读取条目失败：{e}")
            items = []
        project_names = {}
        try:
            project_names = {p["id"]: (p.get("name") or "") for p in db.list_projects()}
        except Exception:
            project_names = {}

        hours: Counter = Counter()
        weekdays: Counter = Counter()
        types: Counter = Counter()
        tags: Counter = Counter()
        proj_counts: Counter = Counter()            # project_id -> 条数
        tag_proj: Dict[str, Counter] = {}           # 标签 -> Counter(project_id)
        people: Counter = Counter()
        total = 0
        for it in items:
            dt = tu.from_iso(it.get("created_at"))
            if dt and dt < cutoff:
                continue
            total += 1
            if dt:
                hours[dt.hour] += 1
                weekdays[dt.weekday()] += 1
            types[it.get("type") or "note"] += 1
            item_tags = [t for t in (it.get("tags") or []) if isinstance(t, str) and t.strip()]
            for t in item_tags:
                tags[t] += 1
            pid = it.get("project_id") or 0
            if pid and project_names.get(pid):
                proj_counts[pid] += 1
                # 学「标签 → 项目」：同一个标签反复落在同一个项目上，就是你的书写习惯
                for t in item_tags:
                    tag_proj.setdefault(t, Counter())[pid] += 1
            for p in ((it.get("meta") or {}).get("people") or []):
                if isinstance(p, str) and p.strip():
                    people[p.strip()] += 1

        default_hour = 9
        peak_hours: List[int] = []
        if hours:
            (h, n), = hours.most_common(1)
            # 样本太少就别“学”了，宁可保持默认 9:00
            if n >= 3:
                default_hour = int(h)
            peak_hours = [int(h) for h, n in hours.most_common(3) if n >= 2]

        peak_weekday = ""
        if weekdays:
            (w, n), = weekdays.most_common(1)
            if n >= 3:
                peak_weekday = _WEEKDAY_CN[int(w)]

        # 项目偏好（带 id，供自动关联/建议加权用）
        with_proj = sum(proj_counts.values())
        top_projects = [
            {"id": pid, "name": project_names.get(pid) or str(pid), "count": n,
             "share": round(n / with_proj, 3) if with_proj else 0.0}
            for pid, n in proj_counts.most_common(5)
        ]
        dominant_project = None
        if with_proj:
            pid, n = proj_counts.most_common(1)[0]
            share = n / with_proj
            # 只有「够多 + 够集中」才敢拿它当兜底，否则宁可不猜
            if n >= 8 and share >= 0.6:
                dominant_project = {"id": pid, "name": project_names.get(pid) or str(pid),
                                    "count": n, "share": round(share, 3)}

        # 「标签 → 项目」规则：至少 3 次且占比 ≥60% 才算学会
        rules: Dict[str, Dict[str, Any]] = {}
        for t, c in tag_proj.items():
            pid, n = c.most_common(1)[0]
            tot = sum(c.values())
            if n >= 3 and tot and n / tot >= 0.6:
                rules[t] = {"project_id": pid,
                            "project": project_names.get(pid) or str(pid),
                            "count": n, "share": round(n / tot, 3)}
        rules = dict(sorted(rules.items(), key=lambda kv: -kv[1]["count"])[:20])

        return {
            "total": total,
            "window_days": WINDOW_DAYS,
            "default_hour": default_hour,
            "peak_hours": peak_hours,
            "peak_hour_range": self._hour_range(peak_hours),
            "peak_weekday": peak_weekday,
            "hours": {str(h): n for h, n in sorted(hours.items())},
            "weekdays": {_WEEKDAY_CN[int(w)]: n for w, n in sorted(weekdays.items())},
            "types": dict(types.most_common()),
            "top_tags": [t for t, _n in tags.most_common(8)],
            "top_projects": top_projects,
            "top_people": [p for p, _n in people.most_common(5)],
            # 供「自动关联项目」「建议排序加权」使用（id 维度，便于匹配）
            "project_affinity": {str(pid): n for pid, n in proj_counts.most_common(50)},
            "tag_affinity": dict(tags.most_common(100)),
            "tag_projects": rules,
            "dominant_project": dominant_project,
            "built_at": tu.to_iso(now),
        }

    @staticmethod
    def _hour_range(peak: List[int]) -> str:
        """把常用小时压成好看的时间段：以最常用小时为锚，向相邻小时扩展。

        [21,22] → 21:00–23:00；[21] → 21:00 左右；[21,10] → 21:00 左右、10:00
        """
        hs = [int(h) for h in (peak or [])]
        if not hs:
            return ""
        anchor = hs[0]
        lo = hi = anchor
        rest = set(hs[1:])
        while (hi + 1) % 24 in rest and hi + 1 <= 23:
            hi += 1
            rest.discard(hi)
        while (lo - 1) in rest and lo - 1 >= 0:
            lo -= 1
            rest.discard(lo)
        main = f"{lo:02d}:00 左右" if lo == hi else f"{lo:02d}:00–{(hi + 1) % 24:02d}:00"
        if rest:
            main += "、" + "、".join(f"{h:02d}:00" for h in sorted(rest))
        return main

    # ---------------- 用于解析：补全默认时间 ----------------
    def apply_capture(self, parsed: Dict[str, Any], text: str) -> Dict[str, Any]:
        """时间只到“日”、没写钟点时，用你的常用时间补全（仅改 9:00 这个默认值）。

        失败一律原样返回——习惯是锦上添花，绝不能影响捕获本身。
        """
        try:
            due = (parsed or {}).get("due_at")
            if not due or not text:
                return parsed
            if _HOUR_RE.search(text) or _MORNING_RE.search(text):
                return parsed
            dt = tu.from_iso(due)
            if not dt or (dt.hour, dt.minute) != (9, 0):
                return parsed
            hour = int(self.profile().get("default_hour") or 9)
            if not (0 <= hour <= 23) or hour == 9:
                return parsed
            out = dict(parsed)
            out["due_at"] = tu.to_iso(dt.replace(hour=hour, minute=0, second=0, microsecond=0))
            return out
        except Exception as e:
            print(f"[habits] 补全时间失败：{e}")
            return parsed

    # ---------------- 用于捕获：自动关联常记项目 ----------------
    def _auto_mode(self) -> str:
        try:
            mode = str(self.ctx.config.get("habits.auto_project", "auto") or "auto").lower()
        except Exception:
            mode = "auto"
        return mode if mode in ("auto", "tags", "off") else "auto"

    def auto_project(self, item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """没在文本里点名项目时，按学到的「标签→项目」或主导项目猜一个。

        保守策略：
        - 只认「至少 3 次且占比 ≥60%」的标签规则；
        - 主导项目兜底要求「≥8 条且占比 ≥60%」；
        - 项目已被删掉则不关联；
        - `habits.auto_project=off` 直接不猜，`=tags` 只用标签规则。
        返回 {project_id, project_name, reason, confidence, source} 或 None。
        """
        try:
            if not item or item.get("project_id"):
                return None
            mode = self._auto_mode()
            if mode == "off":
                return None
            prof = self.profile()
            rules = prof.get("tag_projects") or {}
            try:
                alive = {p["id"]: (p.get("name") or "") for p in self.ctx.db.list_projects()}
            except Exception:
                alive = {}
            tags = [t for t in (item.get("tags") or []) if isinstance(t, str) and t in rules]
            if tags:
                votes: Counter = Counter(rules[t]["project_id"] for t in tags
                                         if rules[t]["project_id"] in alive)
                if votes:
                    pid, n = votes.most_common(1)[0]
                    hit = sorted(t for t in tags if rules[t]["project_id"] == pid)
                    return {"project_id": pid,
                            "project_name": alive.get(pid) or rules[hit[0]]["project"],
                            "reason": "常用标签：" + "、".join(hit)[:36],
                            "confidence": round(min(0.85, 0.55 + 0.1 * n), 2),
                            "source": "habit_tag"}
            if mode == "tags":
                return None
            dom = prof.get("dominant_project") or {}
            pid = dom.get("id")
            if pid and pid in alive:
                return {"project_id": pid,
                        "project_name": alive.get(pid) or dom.get("name") or "",
                        "reason": f"你最常记在「{dom.get('name')}」（{dom.get('count')} 条）",
                        "confidence": 0.4,
                        "source": "habit_dominant"}
            return None
        except Exception as e:
            print(f"[habits] 自动关联失败：{e}")
            return None

    # ---------------- 用于建议：按习惯给条目加权 ----------------
    def affinity(self, items: List[Dict[str, Any]]) -> Dict[int, float]:
        """给每条事项打 0~1 的「习惯分」：常记项目 0.6 + 常用标签 0.4。"""
        try:
            prof = self.profile()
        except Exception:
            return {}
        pa = prof.get("project_affinity") or {}
        ta = prof.get("tag_affinity") or {}
        if not pa and not ta:
            return {}
        mx_p = max(pa.values()) if pa else 0
        mx_t = max(ta.values()) if ta else 0
        out: Dict[int, float] = {}
        for it in items or []:
            s = 0.0
            pid = it.get("project_id")
            if pid and mx_p:
                s += 0.6 * min(1.0, (pa.get(str(pid)) or 0) / mx_p)
            tags = [t for t in (it.get("tags") or []) if isinstance(t, str)]
            if tags and mx_t:
                best = max((ta.get(t) or 0) for t in tags)
                s += 0.4 * min(1.0, best / mx_t)
            if s:
                out[int(it["id"])] = round(s, 4)
        return out

    def rank_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按习惯分稳定排序（同分保持原顺序）。"""
        aff = self.affinity(items)
        if not aff:
            return list(items or [])
        return sorted(items or [], key=lambda it: -aff.get(int(it["id"]), 0.0))
