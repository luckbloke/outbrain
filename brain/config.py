"""配置插件（cordis 插件：ctx.config）。

- 读 config.toml（tomllib，Python 3.11+ 标准库）；无 tomllib 时回退 JSON。
- 路径相对项目根解析；自动创建 data 目录。
- BYOK：API key 可用环境变量覆盖（OPENAI_API_KEY 等）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from cordis import Service

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_file(path: str) -> Dict[str, Any]:
    # 容忍 BOM（Windows 记事本/VS Code/Set-Content 存 TOML 常带 BOM，tomllib 不容忍）
    if path.endswith((".json",)):
        import json
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    try:
        import tomllib
        with open(path, "rb") as f:
            data = f.read()
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        return tomllib.loads(data.decode("utf-8"))
    except ModuleNotFoundError:
        import json
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)


class ConfigService(Service):
    provide = "config"
    name = "config"
    inject = []

    def __init__(self, ctx):
        super().__init__(ctx, "config")
        self.base_dir = PROJECT_ROOT
        path = os.environ.get("CORDIS_BRAIN_CONFIG") or os.path.join(PROJECT_ROOT, "config.toml")
        raw: Dict[str, Any] = {}
        if os.path.exists(path):
            try:
                raw = _load_file(path)
            except Exception as e:  # pragma: no cover
                print(f"[config] 读取 {path} 失败：{e}")
        self._raw = raw or {}
        self.config_path = path

        self.data_dir = self._abs(self.get("server.data_dir", "data"))
        os.makedirs(self.data_dir, exist_ok=True)
        self.web_dir = self._abs(self.get("server.web_dir", "web"))
        db_path = self.get("db.path", "") or os.path.join(self.data_dir, "brain.db")
        self.db_path = self._abs(db_path)

        self._apply_env()

    def _abs(self, p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(self.base_dir, p)

    def _apply_env(self) -> None:
        ai = self._raw.setdefault("ai", {})
        oai = ai.setdefault("openai", {})
        if not oai.get("api_key"):
            # 只有指向官方 OpenAI 端点时才自动套用 OPENAI_API_KEY。
            # 第三方兼容网关（如默认的 Kilo 免费网关）带上这个 Key 会 401，
            # 反而让问答/捕获静默回退到规则解析。
            base = str(oai.get("base_url") or "").rstrip("/")
            if base.startswith("https://api.openai.com"):
                oai["api_key"] = os.environ.get("OPENAI_API_KEY") or os.environ.get("CORS_OPENAI_API_KEY", "")
        ol = ai.setdefault("ollama", {})
        if os.environ.get("OLLAMA_HOST"):
            host = os.environ["OLLAMA_HOST"].rstrip("/")
            if "://" not in host:
                host = "http://" + host
            ol.setdefault("base_url", host)

    def get(self, path: str, default: Any = None) -> Any:
        cur: Any = self._raw
        for part in str(path).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def section(self, name: str) -> Dict[str, Any]:
        v = self._raw.get(name)
        return v if isinstance(v, dict) else {}

    @property
    def raw(self) -> Dict[str, Any]:
        return self._raw

    def public(self) -> Dict[str, Any]:
        """对前端暴露的安全配置（隐藏 api_key）。"""
        return {
            "ai": {
                "provider": self.get("ai.provider", "openai"),
                "providers": self.get("ai.provider", "openai"),
            },
            "timezone": self.get("time.timezone", "Asia/Shanghai"),
            "version": "0.1.0",
        }

    # ---------- 可编辑配置（前端「设置」面板，仅管理员）----------
    # (path, 标签, 类型, 可选值, 提示)
    SETTINGS_SCHEMA = [
        ("ai.provider", "AI 引擎", "select", ["openai", "ollama", "heuristic"],
         "问答 / 记下 / 捕获 共用这一套 LLM 配置；openai=OpenAI 兼容接口（默认 Kilo 免费网关）；ollama=本地；heuristic=离线纯规则"),
        ("ai.openai.base_url", "OpenAI 兼容 base_url", "text", None,
         "默认 https://api.kilo.ai/api/gateway（免费模型无需 Key）"),
        ("ai.openai.api_key", "OpenAI API Key", "password", None,
         "BYOK；Kilo 免费模型可留空，换其他网关时填自己的 Key"),
        ("ai.openai.model", "对话模型", "text", None, "问答 / 记下 / 捕获意图解析共用，默认 kilo-auto/free"),
        ("ai.openai.embed_model", "向量模型 (embedding)", "text", None,
         "语义搜索用；Kilo 网关无向量模型可留空，换其他网关时填如 text-embedding-3-small"),
        ("ai.ollama.base_url", "Ollama 地址", "text", None, "如 http://127.0.0.1:11434"),
        ("ai.ollama.model", "Ollama 对话模型", "text", None, "如 qwen2.5:7b"),
        ("ai.ollama.embed_model", "Ollama 向量模型", "text", None, "如 bge-m3"),
        ("capture.provider", "捕获解析", "select", ["auto", "heuristic"],
         "auto=记下/捕获默认调 LLM 分析意图（与问答同一套配置），失败自动回退规则；heuristic=纯规则(离线最快)"),
        ("asr.provider", "语音转写", "select", ["auto", "openai", "whisper", "vosk", "off"],
         "auto=优先用下面配置的 OpenAI 兼容接口，失败或未配置时用本机 whisper/vosk；都没有就只存音频并标记「待转写」"),
        ("asr.base_url", "ASR 接口 base_url", "text", None,
         "OpenAI 兼容语音转写端点，如 https://api.openai.com/v1 或自建 faster-whisper 网关；留空=不启用远程转写"),
        ("asr.api_key", "ASR API Key", "password", None,
         "留空时：base_url 与 AI 网关同源则复用其 Key；官方 OpenAI 端点则读环境变量 OPENAI_API_KEY"),
        ("asr.model", "ASR 模型", "text", None,
         "默认 whisper-1；自建网关填其模型名，如 Systran/faster-whisper-small"),
        ("asr.language", "转写语言", "text", None, "留空=自动识别；中文可填 zh"),
        ("asr.vosk_model", "Vosk 模型目录", "text", None,
         "本机离线转写用；vosk 只支持 wav（16k 单声道）音频，留空=不用 vosk"),
        ("habits.auto_project", "常记项目自动关联", "select", ["auto", "tags", "off"],
         "auto=先按学到的「标签→项目」关联，没有再按你最常记的项目（都要够多够集中才猜）；tags=只用标签学习；off=关闭"),
        ("habits.suggest_boost", "建议排序加权", "select", ["auto", "off"],
         "auto=按常记项目/标签给建议列表里的条目加权排序；off=只按紧迫度"),
        ("search.semantic", "语义搜索", "select", ["auto", "off"],
         "auto=有向量模型就融合向量+关键词；off=仅关键词"),
        ("search.use_fts5", "FTS5 检索", "select", ["auto", "on", "off"],
         "auto=sqlite 支持则用(远程 3.7 自动降级 LIKE)；on=强制；off=禁用"),
        ("time.timezone", "时区 (IANA)", "text", None, "如 Asia/Shanghai"),
    ]

    def _set_path(self, d: Dict[str, Any], path: str, value: Any) -> None:
        parts = path.split(".")
        cur = d
        for p in parts[:-1]:
            nxt = cur.get(p)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[p] = nxt
            cur = nxt
        cur[parts[-1]] = value

    def _dump_toml(self, d: Dict[str, Any]) -> str:
        """把整份配置序列化回 TOML（保留所有值；不保留原文件注释）。"""
        def scalar(v: Any) -> str:
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, int):
                return str(v)
            if isinstance(v, float):
                return repr(v)
            return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines: List[str] = []
        for k, v in d.items():
            if not isinstance(v, dict):
                lines.append(f"{k} = {scalar(v)}")
        if lines:
            lines.append("")
        def table(prefix: str, tbl: Dict[str, Any]) -> None:
            lines.append("[" + prefix + "]")
            for k, v in tbl.items():
                if not isinstance(v, dict):
                    lines.append(f"{k} = {scalar(v)}")
            lines.append("")
            for k, v in tbl.items():
                if isinstance(v, dict):
                    table(prefix + "." + k, v)
        for k, v in d.items():
            if isinstance(v, dict):
                table(k, v)
        return "\n".join(lines).rstrip() + "\n"

    def settings_view(self) -> Dict[str, Any]:
        fields = []
        for path, label, typ, options, hint in self.SETTINGS_SCHEMA:
            fields.append({"path": path, "label": label, "type": typ,
                           "options": options, "hint": hint, "value": self.get(path)})
        return {"fields": fields, "config_path": self.config_path}

    def update_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """按白名单校验并写回 config.toml（原子替换）。返回保存了哪些 key。"""
        payload = payload or {}
        saved: List[str] = []
        for path, label, typ, options, hint in self.SETTINGS_SCHEMA:
            if path not in payload:
                continue
            v = payload[path]
            if typ == "select" and options and v not in options:
                raise ValueError(f"「{label}」取值只能是 {' / '.join(options)}")
            if typ in ("text", "password"):
                v = "" if v is None else str(v)
            self._set_path(self._raw, path, v)
            saved.append(path)
        if not saved:
            return {"saved": [], "changed": False}
        tmp = self.config_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(self._dump_toml(self._raw))
        os.replace(tmp, self.config_path)
        return {"saved": saved, "changed": True}
