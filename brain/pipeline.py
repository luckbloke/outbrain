"""业务插件：捕获/解析流水线、混合检索、今日视图、问答。

事件流：capture.received（入收件箱）-> item.stored（解析+入库完成）。
本切片解析同步执行（heuristic 离线极快）；LLM 解析后续可改异步。
"""
from __future__ import annotations

import math
import threading
from typing import Any, Dict, List, Optional

from cordis import Service

from . import timeutil as tu
from ._userctx import current_user_id, set_current_user_id
from .util import truncate


def _rrf(rankings: List[Dict[int, float]], k: int = 60) -> Dict[int, float]:
    """Reciprocal Rank Fusion：把多路 {id:score} 融合成一个排序分。"""
    fused: Dict[int, float] = {}
    for ranking in rankings:
        if not ranking:
            continue
        ordered = sorted(ranking.items(), key=lambda kv: kv[1], reverse=True)
        for pos, (iid, _s) in enumerate(ordered, start=1):
            fused[iid] = fused.get(iid, 0.0) + 1.0 / (k + pos)
    return fused


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _cosine_scores(qvec: List[float], vecs: Dict[int, List[float]]) -> Dict[int, float]:
    out: Dict[int, float] = {}
    for iid, v in vecs.items():
        s = _cosine(qvec, v)
        if s > 0:
            out[iid] = s
    return out


def _snippet(item: Dict[str, Any], q: str, window: int = 40) -> str:
    text = item.get("body") or item.get("summary") or item.get("title") or ""
    text = " ".join(str(text).split())
    if not q:
        return truncate(text, window * 2)
    low = text.lower()
    qlow = q.lower()
    idx = low.find(qlow)
    if idx < 0:
        from .textproc import segment
        for tok in segment(q).split():
            idx = low.find(tok)
            if idx >= 0:
                break
    if idx < 0:
        return truncate(text, window * 2)
    start = max(0, idx - window // 2)
    return ("…" if start > 0 else "") + text[start:start + window] + ("…" if start + window < len(text) else "")


class CaptureService(Service):
    provide = "capture"
    name = "capture"
    inject = ["db", "ai", "entities", "reminder", "workflow", "habits"]

    def __init__(self, ctx):
        super().__init__(ctx, "capture")
        ctx.on("capture.received", self._on_received)
        self._refining: set = set()
        self._refine_lock = threading.Lock()

    def submit(self, text: str, source: str = "text", meta: Optional[Dict[str, Any]] = None) -> int:
        text = (text or "").strip()
        if not text:
            raise ValueError("empty capture")
        item_id = self.ctx.db.insert_item({
            "type": "inbox", "raw": text, "body": text, "source": source, "status": "inbox",
        })
        self.ctx.db.log_capture(item_id, text, source, meta)
        self.ctx.db.audit("capture", "item", item_id, source)
        self.ctx.emit("capture.received", item_id)
        return item_id

    def _on_received(self, item_id: int):
        self.process(item_id)

    def is_refining(self, item_id: int) -> bool:
        with self._refine_lock:
            return item_id in self._refining

    def process(self, item_id: int) -> Optional[Dict[str, Any]]:
        """两步走：先毫秒级规则解析落库（立刻返回结果），再默认交给 LLM（与问答同一套
        配置）在后台重解析"用户意图"，完成后刷新条目并 emit item.updated。
        这样「记下/捕获」既默认走 LLM，又不会被免费网关 20~60s 的延迟卡住。"""
        item = self.ctx.db.get_item(item_id)
        if not item:
            return None
        text = item.get("raw") or item.get("body") or ""
        parsed = self.ctx.ai.heuristic_parse(text)
        try:
            # 习惯画像：只有日期没钟点时，用你常用的时间补全（默认 9:00 才动）
            parsed = self.ctx.habits.apply_capture(parsed, text)
        except Exception as e:
            print(f"[habits] error: {e}")
        item = self._apply(item_id, parsed, "heuristic", text)
        self._embed(item)
        self.ctx.emit("item.stored", item)
        if self.ctx.ai.llm_ready():
            self._start_refine(item_id, int(item.get("user_id") or 0), text)
        return item

    def _apply(self, item_id: int, parsed: Dict[str, Any], provider: str, text: str,
               split: bool = True) -> Optional[Dict[str, Any]]:
        project_id = self._resolve_project(parsed, text)
        prev = self.ctx.db.get_item(item_id) or {}
        prev_meta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
        meta = {
            "actions": parsed.get("actions", []),
            "people": parsed.get("people", []),
            "projects": parsed.get("projects", []),
            "context": parsed.get("context", ""),
        }
        # 后台 LLM 重解析会整体覆盖 meta，但「习惯自动关联」的痕迹要留着——
        # 前提是项目归属没被重解析改掉（改掉了就说明痕迹已过期）
        if prev_meta.get("auto_project") and (not project_id or project_id == prev.get("project_id")):
            meta["auto_project"] = prev_meta["auto_project"]
        fields = {
            "type": parsed.get("type", "note"),
            "status": "open",
            "title": parsed.get("title"),
            "summary": parsed.get("summary"),
            "tags": parsed.get("tags", []),
            "priority": parsed.get("priority", "P2"),
            "due_at": parsed.get("due_at") or None,
            "context": parsed.get("context") or None,
            "confidence": parsed.get("confidence", 0.5),
            "parse_provider": provider,
            "project_id": project_id,
            "meta": meta,
        }
        item = self.ctx.db.update_item(item_id, fields)
        self.ctx.db.audit("parse", "item", item_id, f"{provider}:{parsed.get('type')}")
        # P1：抽取实体图谱 + 为有期限的 task/event 自动建提醒
        try:
            self.ctx.entities.extract(item)
        except Exception as e:
            print(f"[entities] extract error: {e}")
        try:
            self.ctx.reminder.ensure_for_item(item)
        except Exception as e:
            print(f"[reminder] ensure error: {e}")
        # P2：工作流（自动关联项目 / 自动拆子任务）；后台重解析时不再拆，避免重复子任务
        try:
            if not item.get("project_id"):
                self.ctx.workflow.associate(item)
            if split:
                self.ctx.workflow.auto_split(item)
        except Exception as e:
            print(f"[workflow] error: {e}")
        return self.ctx.db.get_item(item_id)  # 取最新（关联/拆分可能改动）

    def _embed(self, item: Optional[Dict[str, Any]]) -> None:
        # 语义向量入库（若有真实 embedding 模型）——不可用则跳过，绝不阻塞捕获
        if not item:
            return
        try:
            emb_text = " ".join(x for x in (item.get("title"), item.get("body") or item.get("summary"),
                                             " ".join(item.get("tags") or [])) if x)
            if emb_text.strip():
                provider, model, vec = self.ctx.ai.embed_with_model(emb_text)
                if vec:
                    self.ctx.db.upsert_embedding(item["id"], provider, model, vec)
        except Exception as e:
            print(f"[embed] error: {e}")

    def _start_refine(self, item_id: int, uid: int, text: str) -> None:
        with self._refine_lock:
            if item_id in self._refining:
                return
            self._refining.add(item_id)
        t = threading.Thread(target=self._refine, args=(item_id, uid, text),
                             name=f"refine-{item_id}", daemon=True)
        t.start()

    def _refine(self, item_id: int, uid: int, text: str) -> None:
        """后台 LLM 意图解析。线程里没有请求上下文，必须显式带上用户 id。"""
        prev = current_user_id()
        try:
            set_current_user_id(uid)
            llm = self.ctx.ai.llm_parse(text)
            if not llm:
                return
            cur = self.ctx.db.get_item(item_id)
            if not cur or cur.get("status") in ("done", "canceled"):
                return
            merged = self.ctx.ai.merge_parse(text, llm)
            item = self._apply(item_id, merged, self.ctx.ai.active_name(), text, split=False)
            self._embed(item)
            self.ctx.emit("item.updated", item)
        except Exception as e:
            print(f"[ai] 后台意图解析失败：{type(e).__name__}: {e}")
        finally:
            set_current_user_id(prev)
            with self._refine_lock:
                self._refining.discard(item_id)

    def _resolve_project(self, parsed: Dict[str, Any], text: str) -> Optional[int]:
        db = self.ctx.db
        if parsed.get("project"):
            return db.upsert_project(parsed["project"])
        for p in db.list_projects():
            name = p.get("name") or ""
            if name and len(name) >= 2 and name in text:
                return p["id"]
        return None


class SearchService(Service):
    provide = "search"
    name = "search"
    inject = ["db", "ai"]

    def __init__(self, ctx):
        super().__init__(ctx, "search")

    def query(
        self,
        q: str = "",
        type: Optional[str] = None,
        project: Optional[str] = None,
        tag: Optional[str] = None,
        status: Optional[str] = None,
        status_in: Optional[List[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        q = (q or "").strip()
        db = self.ctx.db
        filters: Dict[str, Any] = {"type": type, "project": project, "tag": tag,
                                   "status": status, "status_in": status_in,
                                   "date_from": date_from, "date_to": date_to}
        if q:
            fts = db.fts_rank(q, 120)
            like = db.like_scores(q, 300)
            rankings = [fts, like]
            # 语义向量（有真实 embedding 模型且库里有向量时）；search.semantic=off 可关闭
            sem_mode = str(self.ctx.config.get("search.semantic", "auto") or "auto").lower()
            if sem_mode != "off":
                provider, model, qvec = self.ctx.ai.embed_with_model(q)
                if qvec:
                    vecs = db.list_embeddings(provider, model)
                    if vecs:
                        sims = _cosine_scores(qvec, vecs)
                        vec_ranked = {iid: s for iid, s in sims.items() if s >= 0.25}
                        if vec_ranked:
                            rankings.append(vec_ranked)
            fused = _rrf(rankings)
            items = [db.get_item(iid) for iid in fused]
            items = [it for it in items if it]
            items = [it for it in items if self._match(it, filters)]
            items.sort(key=lambda it: fused.get(it["id"], 0), reverse=True)
            for it in items:
                it["score"] = round(fused.get(it["id"], 0), 6)
                it["snippet"] = _snippet(it, q)
            return items[: int(limit)]
        # 无查询：按过滤 + 时间倒序
        rows = db.list_items(filters, limit=int(limit))
        for it in rows:
            it["snippet"] = _snippet(it, "")
        return rows

    def _match(self, it: Dict[str, Any], f: Dict[str, Any]) -> bool:
        if f.get("type") and it.get("type") != f["type"]:
            return False
        if f.get("status") and it.get("status") != f["status"]:
            return False
        if f.get("status_in") and it.get("status") not in f["status_in"]:
            return False
        if f.get("tag") and f["tag"] not in (it.get("tags") or []):
            return False
        return True

    def ask(self, q: str, limit: int = 6) -> Dict[str, Any]:
        results = self.query(q, limit=limit)
        sources = [
            {"id": it["id"], "title": it.get("title") or it.get("body", "")[:40],
             "snippet": it.get("snippet", ""), "type": it.get("type"), "score": it.get("score")}
            for it in results
        ]
        llm = None
        if results:
            context = "\n".join(f"[{i+1}] {s['title']}：{s['snippet']}" for i, s in enumerate(sources))
            sys_prompt = ("你是个人助理，仅基于下面用户自己的资料回答问题。"
                          "用简洁中文回答，并在需要时用 [n] 标注引用来源编号。")
            llm = self.ctx.ai.chat(sys_prompt, [{"role": "user", "content": f"资料：\n{context}\n\n问题：{q}"}])
        if llm:
            return {"answer": llm, "sources": sources, "mode": "llm"}
        # 降级：仅检索回答
        if not results:
            answer = f"（未接入 LLM）没找到与「{q}」相关的记录。"
        else:
            lines = [f"（未接入 LLM，以下为检索到的 {len(results)} 条记录）"]
            lines += [f"{i+1}. [{s['type']}] {s['title']} — {s['snippet']}" for i, s in enumerate(sources)]
            answer = "\n".join(lines)
        return {"answer": answer, "sources": sources, "mode": "retrieval"}


class TodayService(Service):
    provide = "today"
    name = "today"
    inject = ["db"]

    def __init__(self, ctx):
        super().__init__(ctx, "today")

    def view(self, date: Optional[str] = None) -> Dict[str, Any]:
        date = date or tu.today().isoformat()
        db = self.ctx.db
        day_items = db.list_items({"date_from": date, "date_to": date}, limit=500)
        # 用 due_at 组织今日视图（跨天：逾期 + 今日 + 即将）
        all_open = db.list_items({"status_in": ["open", "in_progress", "waiting"]}, limit=1000)
        day = tu.from_iso(date).date() if tu.from_iso(date) else tu.today()
        due_today, overdue, upcoming, waiting = [], [], [], []
        for it in all_open:
            due = tu.from_iso(it.get("due_at"))
            if it.get("status") == "waiting":
                waiting.append(it)
                continue
            if not due:
                continue
            d = due.date()
            if d < day:
                overdue.append(it)
            elif d == day:
                due_today.append(it)
            elif (d - day).days <= 7:
                upcoming.append(it)
        def keyf(it):
            return (it.get("priority") or "P9", it.get("due_at") or "9999")
        for lst in (overdue, due_today, upcoming, waiting):
            lst.sort(key=keyf)
        # 今日新增捕获
        added = [it for it in day_items]
        # 统计口径：7 天内 = 今天到期 + 未来 7 天（当天也算在 7 天内）
        counts = {
            "overdue": len(overdue),
            "due_today": len(due_today),
            "within7": len(due_today) + len(upcoming),
            "upcoming": len(upcoming),
            "waiting": len(waiting),
            "added_today": len(added),
        }
        return {
            "date": date,
            "overdue": overdue,
            "due_today": due_today,
            "upcoming": upcoming[:10],
            "waiting": waiting,
            "added_today": added[:50],
            "counts": counts,
        }
