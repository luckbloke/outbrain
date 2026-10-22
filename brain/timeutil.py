"""时间工具：时区感知的 now/ISO 转换 + 中文相对时间解析。

内网机器本机时区为 Asia/Shanghai。解析是启发式的（LLM 可用时可覆盖）。
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

_TZ = None
_TZ_NAME = "Asia/Shanghai"


def set_tz(name: Optional[str]) -> None:
    global _TZ, _TZ_NAME
    if not name:
        _TZ = None
        return
    if ZoneInfo is not None:
        try:
            _TZ = ZoneInfo(name)
            _TZ_NAME = name
            return
        except Exception:
            pass
    _TZ = None


def get_tz():
    return _TZ


def now() -> datetime:
    """当前时区感知的 now。"""
    if _TZ is not None:
        return datetime.now(_TZ)
    return datetime.now().astimezone()


def today() -> date:
    return now().date()


def to_iso(dt) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ) if _TZ else dt.astimezone()
        return dt.isoformat()
    if isinstance(dt, date):
        return dt.isoformat()
    return str(dt)


def from_iso(s) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ) if _TZ else dt
        return dt
    except Exception:
        return None


def add_months(d: date, m: int) -> date:
    month = d.month - 1 + m
    year = d.year + month // 12
    month = month % 12 + 1
    days_in_month = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    day = min(d.day, days_in_month)
    return date(year, month, day)


CN_WEEK = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

_CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_hour(s: str) -> Optional[int]:
    """中文数字小时（1~23）→ int：八=8, 十=10, 十一=11, 十二=12, 二十=20, 二十三=23。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        v = int(s)
        return v if 1 <= v <= 23 else None
    if "十" in s:
        left, _, right = s.partition("十")
        tens = _CN_DIGIT.get(left, 1) if left else 1
        ones = _CN_DIGIT.get(right, 0) if right else 0
        v = tens * 10 + ones
        return v if 1 <= v <= 23 else None
    v = _CN_DIGIT.get(s)
    return v if (v and v <= 23) else None


def parse_relative(text: str) -> Optional[datetime]:
    """从文本中解析相对/绝对时间，返回时区感知 datetime；解析不到返回 None。

    支持：今天/明天/后天/大后天/昨天、下周/下个月/周末、周X/星期X/礼拜X、
    X月X日、YYYY-MM-DD、X点/X:XX/X点半（含上午/下午/晚上）。
    """
    if not text:
        return None
    n = now()
    cur_date = n.date()
    target_date: Optional[date] = None
    target_time: Optional[time] = None
    t = text

    def set_date(d: date) -> None:
        nonlocal target_date
        target_date = d

    # 1) 绝对日期
    m = re.search(r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})", t)
    if m:
        y, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            d = date(y, mo, dd)
        except ValueError:
            d = None
        if d is not None and d < cur_date and y <= cur_date.year:
            d = date(y + 1, mo, dd)
        if d is not None:
            set_date(d)
    else:
        m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", t)
        if m:
            mo, dd = int(m.group(1)), int(m.group(2))
            try:
                d = date(cur_date.year, mo, dd)
            except ValueError:
                d = None
            if d is not None and d < cur_date:
                d = date(cur_date.year + 1, mo, dd)
            if d is not None:
                set_date(d)

    # 2) 相对日词（优先于星期）
    if target_date is None:
        if re.search(r"大后天", t):
            set_date(cur_date + timedelta(days=3))
        elif re.search(r"后天", t):
            set_date(cur_date + timedelta(days=2))
        elif re.search(r"明天|明日", t):
            set_date(cur_date + timedelta(days=1))
        elif re.search(r"今天|今日|今儿|今天下班前", t):
            set_date(cur_date)
        elif re.search(r"昨天", t):
            set_date(cur_date + timedelta(days=-1))
        elif re.search(r"下周", t):
            set_date(cur_date + timedelta(days=7))
        elif re.search(r"下个月|下月", t):
            set_date(add_months(cur_date, 1))
        elif re.search(r"周末|这周末|这个周末", t):
            set_date(cur_date + timedelta(days=(6 - cur_date.weekday()) % 7))

    # 3) 星期（下周X 优先）
    m = re.search(r"(下周|下星期|周|星期|礼拜)([一二三四五六日天])", t)
    if m:
        wk = CN_WEEK[m.group(2)]
        if m.group(1).startswith("下"):
            days = (wk - cur_date.weekday()) % 7 + 7
            set_date(cur_date + timedelta(days=days))
        elif target_date is None:
            days = (wk - cur_date.weekday()) % 7
            d = cur_date + timedelta(days=days)
            if d < cur_date:
                d += timedelta(days=7)
            set_date(d)

    # 4) 时间
    hour: Optional[int] = None
    minute = 0
    hm = re.search(r"(\d{1,2})\s*[点时:：]\s*(\d{1,2})\s*分?", t)
    half = re.search(r"(\d{1,2})\s*[点时]\s*半", t)
    justh = re.search(r"(\d{1,2})\s*[点时]", t)
    if hm:
        hour, minute = int(hm.group(1)), int(hm.group(2) or 0)
    elif half:
        hour, minute = int(half.group(1)), 30
    elif justh:
        hour = int(justh.group(1))
    else:
        # 中文数字小时（阿拉伯数字之外的常见口语）：十点 / 八点半 / 十二点 / 二十三点 / 十点十五分
        CNH = r"[一二两三四五六七八九十]{1,3}"
        chalf = re.search(rf"({CNH})\s*[点时]\s*半", t)
        cmin = re.search(rf"({CNH})\s*[点时]\s*(\d{{1,2}})\s*分", t)
        cjust = re.search(rf"({CNH})\s*[点时]", t)
        if chalf:
            hour, minute = _cn_hour(chalf.group(1)), 30
        elif cmin:
            hour, minute = _cn_hour(cmin.group(1)), int(cmin.group(2))
        elif cjust:
            hour, minute = _cn_hour(cjust.group(1)), 0
    if hour is not None:
        if re.search(r"(中午|下午|晚上|傍晚|夜里|晚)", t) and hour < 12:
            hour += 12
        if hour > 23:
            hour = 23
        if minute > 59:
            minute = 59
        target_time = time(hour, minute)

    if target_date is None and target_time is None:
        return None
    if target_date is None:
        target_date = cur_date
    if target_time is None:
        if re.search(r"(晚上|夜里|晚间|晚)", t):
            target_time = time(19, 0)
        elif re.search(r"(下午|中午|傍晚)", t):
            target_time = time(14, 0)
        elif re.search(r"(早上|上午|早晨|凌晨|早)", t):
            target_time = time(9, 0)
        else:
            target_time = time(9, 0)
    dt = datetime.combine(target_date, target_time)
    if _TZ is not None:
        dt = dt.replace(tzinfo=_TZ)
    else:
        dt = dt.astimezone()
    return dt


def next_occurrence(base: datetime, weekday: int, t: Optional[time] = None) -> datetime:
    """下一次（含今天之后）指定 weekday 的时刻。"""
    t = t or time(9, 0)
    days = (weekday - base.weekday()) % 7
    d = base.date() + timedelta(days=days)
    dt = datetime.combine(d, t)
    if _TZ is not None:
        dt = dt.replace(tzinfo=_TZ)
    return dt
