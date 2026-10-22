"""AI 网关（cordis 插件：ctx.ai）。

provider 注册表 + 优雅降级：
  - HeuristicProvider：离线、纯规则、零依赖，内网默认；永远可用（兜底）。
  - 后续可挂 OpenAI 兼容 / Ollama（BYOK，config 切换），解析失败自动回落 heuristic。

输出结构化 JSON（与需求一致）：
  type / title / summary / tags / people / projects / project /
  due_at / priority / context / actions / confidence
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from cordis import Service

from . import timeutil as tu
from .util import clean_title, first_clause, truncate

# ---------------- 启发式解析 ----------------

_TYPE_RULES = {
    "reference": [r"https?://", r"www\.", r"\.com", r"\.cn", r"链接", r"网址"],
    "event": ["开会", "会议", "面试", "聚餐", "吃饭", "饭局", "生日", "出差", "航班", "火车", "高铁", "机场", "见客户", "约会", "deadline", "截止"],
    "task": ["待办", "记得", "要买", "要写", "要做", "要做的事", "准备", "提交", "写", "改", "做", "买", "联系", "安排", "完成", "跟进", "处理", "回复", "整理", "规划", "计划", "报名", "缴费", "打卡", "todo"],
    "idea": ["想法", "灵感", "如果", "或许", "可以考虑", "也许", "点子", "脑洞", "要是"],
}

_PRIORITY_RULES = [
    ("P0", ["紧急", "马上", "立刻", "立即", "尽快", "加急", "今天必须", "重要紧急", "high"]),
    ("P1", ["重要", "较急", "优先", "medium"]),
    ("P3", ["有空", "不急", "以后", "待会", "晚点", "later", "low"]),
]

_PERSON_TITLES = ["老师", "经理", "主任", "会长", "博士", "教授", "总监", "副总", "总", "工", "哥", "姐"]
_PERSON_TITLES_RE = re.compile("(" + "|".join(_PERSON_TITLES) + ")")
_NOISE_HEAD = set("的了和与及给向对把跟同等在让叫问找请复买卖看做写说道使")
_ZONG_BLOCK = set("结共是的量体经")  # “总”后接这些 → 不是人称（总结/总共/总是/总经理…）


def _extract_people(text: str) -> List[str]:
    """抽取“姓/名+称谓”的人物（张总 / 李经理 / 王工）。先定位称谓，再向前回看
    1~2 个非噪声汉字作为姓名，避免把动词/连词（如“回复李经理”“和张总”）误当姓氏。"""
    people: List[str] = []
    for m in _PERSON_TITLES_RE.finditer(text):
        title = m.group(1)
        if title == "总" and (text[m.end():m.end() + 1] in _ZONG_BLOCK):
            continue
        if title == "经理" and text[m.start() - 1:m.start()] == "总":
            continue  # “总经理”这类职务称谓
        name = ""
        for k in range(m.start() - 1, max(m.start() - 3, -1), -1):
            ch = text[k]
            if "\u4e00" <= ch <= "\u9fff" and ch not in _NOISE_HEAD:
                name = ch + name
            else:
                break
        name = name[:2]
        if name and (name + title) not in people:
            people.append(name + title)
    return people
_PROJECT_RE = re.compile(r"项目\s*[：:]\s*([\u4e00-\u9fffA-Za-z0-9_\-]{1,20})")
_HASHTAG_RE = re.compile(r"#([\u4e00-\u9fffA-Za-z0-9_\-]{1,20})")
_TAG_VOCAB = ["工作", "学习", "生活", "家庭", "健康", "财务", "社交", "个人", "重要", "紧急"]


def _match_any(text: str, kws) -> int:
    return sum(1 for k in kws if k in text)


def heuristic_parse(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    low = t.lower()

    has_url = bool(re.search(r"https?://|www\.", low))
    due_dt = tu.parse_relative(t)  # 提前解析，用于类型修正

    # type 打分
    scores = {typ: _match_any(low, kws) for typ, kws in _TYPE_RULES.items()}
    best, best_score = "note", 0
    for typ, s in scores.items():
        if s > best_score:
            best, best_score = typ, s
    if best_score == 0:
        best = "reference" if has_url else "note"
    if due_dt and best == "note":
        best = "event"  # 有明确未来时间的笔记 → 当作日程

    # 优先级
    priority = "P2"
    for p, kws in _PRIORITY_RULES:
        if _match_any(low, kws):
            priority = p
            break

    due_at = tu.to_iso(due_dt) if (due_dt and best in ("task", "event")) else ""

    # context
    context = ""
    if re.search(r"电话|打电话|通话", t):
        context = "电话"
    elif re.search(r"外面|外出|路上|地铁|高铁|车上", t):
        context = "外出"

    # tags
    tags: List[str] = []
    for m in _HASHTAG_RE.finditer(t):
        tags.append(m.group(1))
    for v in _TAG_VOCAB:
        if v in t:
            tags.append(v)
    if due_at:
        tags.append("有期限")

    # people / projects
    people = _extract_people(t)
    projects = []
    pm = _PROJECT_RE.search(t)
    if pm:
        projects.append(pm.group(1))
    main_project = projects[0] if projects else ""

    # actions（多条款任务）
    actions: List[str] = []
    if best == "task":
        parts = [p.strip() for p in re.split(r"[、；;，,\n]", t) if p.strip() and len(p.strip()) >= 2]
        if len(parts) >= 2:
            actions = parts[:6]

    # confidence
    conf = 0.5
    if due_at:
        conf += 0.1
    if best_score >= 2:
        conf += 0.1
    if tags:
        conf += 0.05
    if people or projects:
        conf += 0.05
    if best == "note" and best_score == 0 and not due_at:
        conf = 0.4
    conf = round(min(conf, 0.9), 2)

    return {
        "type": best,
        "title": first_clause(t, 40) if best != "note" else (first_clause(t, 40) or truncate(t, 40)),
        "summary": truncate(t, 140),
        "tags": tags,
        "people": people,
        "projects": projects,
        "project": main_project,
        "due_at": due_at,
        "priority": priority,
        "context": context,
        "actions": actions,
        "confidence": conf,
    }


def hash_embed(text: str, dim: int = 256) -> List[float]:
    """零依赖离线 embedding：字符 2-gram + 词 哈希到固定维度并 L2 归一。

    粗糙但确定、可离线，让向量检索在无模型时也有意义。
    """
    from .textproc import segment
    toks = segment(text).split()
    vec = [0.0] * dim
    for tok in toks:
        grams = [tok] if len(tok) < 3 else [tok[i:i + 2] for i in range(len(tok) - 1)]
        for g in grams:
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
            vec[idx] += sign
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


# ---------------- Providers ----------------

class HeuristicProvider:
    name = "heuristic"

    def available(self) -> bool:
        return True

    def parse(self, text: str) -> Optional[Dict[str, Any]]:
        try:
            return heuristic_parse(text)
        except Exception:
            return None

    def embed(self, text: str) -> List[float]:
        return hash_embed(text)

    def chat(self, system: str, messages: List[Dict[str, str]]) -> Optional[str]:
        return None  # 无 LLM → 上层降级为"仅检索"回答


# ---------------- LLM Providers（OpenAI 兼容 / Ollama，BYOK，可切换） ----------------

_PARSE_SYSTEM = (
    "你是个人事务解析器。把用户的一句话解析为 JSON，字段固定为：\n"
    "type(task/event/note/reference/idea), title, summary, tags(数组), "
    "people(数组), projects(数组), project(主项目字符串), due_at(ISO8601带时区或空字符串), "
    "priority(P0/P1/P2/P3), context, actions(数组), confidence(0~1)。\n"
    "时间以 Asia/Shanghai 为准，可相对（如“明天下午3点”）。只输出 JSON，不要解释。"
)


def _extract_json(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """从 LLM 回复中抽取 JSON 对象（容忍 ```json 围栏与前后杂字）。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(t[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


class OpenAICompatProvider:
    """任意 OpenAI 兼容接口（OpenAI / vLLM / LM Studio / 自建网关）。BYOK。"""
    name = "openai"

    def __init__(self, cfg):
        self.cfg = cfg
        self.base_url = (cfg.get("ai.openai.base_url") or "https://api.kilo.ai/api/gateway").rstrip("/")
        key = cfg.get("ai.openai.api_key") or ""
        if not key and self.base_url.startswith("https://api.openai.com"):
            # 环境变量只服务官方 OpenAI；第三方网关（如 Kilo 免费网关）不能被它污染
            key = os.environ.get("OPENAI_API_KEY", "")
        self.api_key = key
        self.model = cfg.get("ai.openai.model") or "kilo-auto/free"
        self.embed_model = cfg.get("ai.openai.embed_model") or ""

    def available(self) -> bool:
        # 有 Key 一定可用；无 Key 时允许“免鉴权兼容网关”（如默认的 Kilo 免费网关）尝试，
        # 但官方 OpenAI 端点无 Key 时仍视为不可用，避免离线场景白等超时。
        if self.api_key:
            return True
        return bool(self.base_url) and self.base_url != "https://api.openai.com/v1"

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def chat(self, system: str, messages: List[Dict[str, str]], timeout: int = 40) -> Optional[str]:
        from .util import http_json
        r = http_json(self.base_url + "/chat/completions", "POST",
                      {"model": self.model, "temperature": 0.2,
                       "messages": [{"role": "system", "content": system}] + messages},
                      headers=self._headers(), timeout=timeout)
        return r["choices"][0]["message"]["content"]

    def parse(self, text: str, timeout: int = 25) -> Optional[Dict[str, Any]]:
        return _extract_json(self.chat(_PARSE_SYSTEM, [{"role": "user", "content": text}], timeout=timeout))

    def embed(self, text: str) -> Optional[List[float]]:
        if not self.embed_model:
            return None
        from .util import http_json
        r = http_json(self.base_url + "/embeddings", "POST",
                      {"model": self.embed_model, "input": text},
                      headers=self._headers(), timeout=30)
        return r["data"][0]["embedding"]


class OllamaProvider:
    """本地 Ollama（HTTP 服务），离线可用。不可用时自动回落 heuristic。"""
    name = "ollama"

    def __init__(self, cfg):
        self.cfg = cfg
        self.host = (cfg.get("ai.ollama.host") or cfg.get("ai.ollama.base_url")
                     or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        self.model = cfg.get("ai.ollama.model") or "qwen2.5:7b"
        self.embed_model = cfg.get("ai.ollama.embed_model") or self.model
        self._cache: Optional[Tuple[bool, float]] = None

    def _probe(self) -> bool:
        now = time.time()
        if self._cache and now - self._cache[1] < 60:
            return self._cache[0]
        from .util import http_get
        ok = http_get(self.host + "/api/tags", timeout=2) != -1
        self._cache = (ok, now)
        return ok

    def available(self) -> bool:
        return self._probe()

    def chat(self, system: str, messages: List[Dict[str, str]], timeout: int = 90) -> Optional[str]:
        from .util import http_json
        r = http_json(self.host + "/api/chat", "POST",
                      {"model": self.model, "stream": False,
                       "messages": [{"role": "system", "content": system}] + messages}, timeout=timeout)
        return r["message"]["content"]

    def parse(self, text: str, timeout: int = 25) -> Optional[Dict[str, Any]]:
        return _extract_json(self.chat(_PARSE_SYSTEM, [{"role": "user", "content": text}], timeout=timeout))

    def embed(self, text: str) -> Optional[List[float]]:
        from .util import http_json
        r = http_json(self.host + "/api/embed", "POST",
                      {"model": self.embed_model, "input": text}, timeout=30)
        return (r.get("embeddings") or [None])[0]


class AIGateway:
    def __init__(self, config):
        self.cfg = config
        self._providers: Dict[str, Any] = {"heuristic": HeuristicProvider()}
        self._register_extra()
        self._emb_cache: Tuple[float, Optional[str], Optional[str]] = (0.0, None, None)  # (ts, provider, model)

    def _register_extra(self):
        # 注册可选 LLM provider（BYOK，可切换）。heuristic 永远在并兜底。
        try:
            self._providers["openai"] = OpenAICompatProvider(self.cfg)
        except Exception:
            pass
        try:
            self._providers["ollama"] = OllamaProvider(self.cfg)
        except Exception:
            pass

    def _active_name(self) -> str:
        return self.cfg.get("ai.provider", "openai") or "openai"

    def get(self, name: str):
        return self._providers.get(name)

    def _heuristic(self, text: str) -> Dict[str, Any]:
        try:
            return heuristic_parse(text)
        except Exception:
            return {"type": "note", "title": truncate(text, 40), "summary": truncate(text, 140),
                    "tags": [], "people": [], "projects": [], "project": "", "due_at": "",
                    "priority": "P2", "context": "", "actions": [], "confidence": 0.4}

    def active_name(self) -> str:
        return self._active_name()

    def llm_ready(self) -> bool:
        """capture.provider 允许 + active provider 可用（真正联网前判断，不发请求）。"""
        mode = str(self.cfg.get("capture.provider", "auto") or "auto").lower()
        if mode == "heuristic":
            return False
        name = self._active_name()
        if name == "heuristic":
            return False
        prov = self._providers.get(name)
        try:
            return prov is not None and bool(prov.available())
        except Exception:
            return False

    def heuristic_parse(self, text: str) -> Dict[str, Any]:
        """纯规则解析（毫秒级、永不失败）。"""
        return self._heuristic(text)

    def llm_parse(self, text: str, timeout: int = 60) -> Optional[Dict[str, Any]]:
        """只走 LLM 的意图解析（注入当天日期，便于解析"明天/下周"）；不可用/失败返回 None。
        免费网关（如 Kilo）单次实测 20s 上下，故给足超时——调用方一般在后台线程里用。"""
        if not self.llm_ready():
            return None
        name = self._active_name()
        prov = self._providers.get(name)
        if prov is None:
            return None
        try:
            prompt = f"今天是 {tu.today().isoformat()}（Asia/Shanghai）。\n{text}"
            r = prov.parse(prompt, timeout=timeout)
            if r and r.get("type"):
                return r
        except Exception as e:
            print(f"[ai] {name} 意图解析失败：{type(e).__name__}: {e}")
        return None

    def merge_parse(self, text: str, llm: Dict[str, Any]) -> Dict[str, Any]:
        """LLM 结果合并到规则基线：LLM 更优，但 due_at 若规则已按真实今天解析出则优先保留。"""
        base = self._heuristic(text)
        for k, v in (llm or {}).items():
            if v is None:
                continue
            if k == "due_at" and base.get("due_at"):
                continue
            base[k] = v
        return base

    def parse(self, text: str) -> Tuple[Dict[str, Any], str]:
        """同步解析（即时需要最终结果，如手动重新解析）：LLM 优先，失败回退 heuristic。"""
        llm = self.llm_parse(text)
        if llm:
            return self.merge_parse(text, llm), self._active_name()
        return self._heuristic(text), "heuristic"

    def embed(self, text: str) -> Optional[List[float]]:
        for name in [self._active_name(), "heuristic"]:
            prov = self._providers.get(name)
            if prov is None:
                continue
            try:
                v = prov.embed(text)
                if v:
                    return v
            except Exception:
                continue
        return None

    def _emb_candidate(self) -> Tuple[Optional[str], Optional[str]]:
        """当前 active provider 的 embedding 模型 (provider, model)；仅 LLM 系（openai/ollama）可用。"""
        name = self._active_name()
        if name not in ("openai", "ollama"):
            return None, None
        prov = self._providers.get(name)
        if prov is not None and prov.available():
            model = getattr(prov, "embed_model", None)
            if model:
                return name, model
        return None, None

    def embed_with_model(self, text: str, ttl: float = 300.0) -> Tuple[Optional[str], Optional[str], Optional[List[float]]]:
        """用真实 embedding 模型向量化，返回 (provider, model, vector)。
        无可用模型 / 调用失败 → (None, None, None)（短期缓存失败，避免每次空打 /embeddings）。
        注意：不使用 heuristic 的 hash_embed 兜底——那只是词法哈希，非语义，会误导"语义搜索"。"""
        now = time.time()
        ts, provider, model = self._emb_cache
        if now - ts >= ttl:
            provider, model = self._emb_candidate()
            if provider is None:
                self._emb_cache = (now, None, None)
                return None, None, None
        if provider is None:
            return None, None, None
        prov = self._providers.get(provider)
        try:
            vec = prov.embed(text) if prov is not None else None
        except Exception:
            vec = None
        if not vec:
            self._emb_cache = (now, None, None)  # 标记不可用，ttl 内不再重试
            return None, None, None
        self._emb_cache = (now, provider, model)
        return provider, model, vec

    def chat(self, system: str, messages: List[Dict[str, str]]) -> Optional[str]:
        prov = self._providers.get(self._active_name())
        if prov is None:
            return None
        try:
            if prov.available():
                return prov.chat(system, messages)
        except Exception:
            return None
        return None

    def status(self) -> Dict[str, Any]:
        return {
            "active": self._active_name(),
            "providers": {n: bool(getattr(p, "available", lambda: False)()) for n, p in self._providers.items()},
        }

    def reload(self) -> None:
        """热重载：重读配置重建 LLM provider（改 base_url/model/key/embed_model 后调用），并失效向量缓存。"""
        try:
            self._register_extra()
        except Exception:
            pass
        self._emb_cache = (0.0, None, None)


class AIGatewayService(Service):
    provide = "ai"
    name = "ai"
    inject = ["config"]

    def __init__(self, ctx):
        super().__init__(ctx, "ai")
        self.gw = AIGateway(self.ctx.config)

    def __getattr__(self, name):
        return getattr(self.gw, name)
