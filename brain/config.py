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
                "provider": self.get("ai.provider", "heuristic"),
                "providers": self.get("ai.provider", "heuristic"),
            },
            "timezone": self.get("time.timezone", "Asia/Shanghai"),
            "version": "0.1.0",
        }

    # ---------- 可编辑配置（前端「设置」面板，仅管理员）----------
    # (path, 标签, 类型, 可选值, 提示)
    SETTINGS_SCHEMA = [
        ("ai.provider", "AI 引擎", "select", ["openai", "ollama", "heuristic"],
         "openai=OpenAI 兼容接口（OpenAI / DeepSeek / 通义等）；ollama=本地 Ollama；heuristic=离线纯规则"),
        ("ai.openai.base_url", "OpenAI 兼容 base_url", "text", None, "如 https://api.openai.com/v1"),
        ("ai.openai.api_key", "OpenAI API Key", "password", None, "BYOK，填你自己的 API Key"),
        ("ai.openai.model", "对话模型", "text", None, "问答 / 解析用，如 gpt-4o-mini"),
        ("ai.openai.embed_model", "向量模型 (embedding)", "text", None, "语义搜索用，如 text-embedding-3-small"),
        ("ai.ollama.base_url", "Ollama 地址", "text", None, "如 http://127.0.0.1:11434"),
        ("ai.ollama.model", "Ollama 对话模型", "text", None, "如 qwen2.5:7b"),
        ("ai.ollama.embed_model", "Ollama 向量模型", "text", None, "如 bge-m3"),
        ("capture.provider", "捕获解析", "select", ["auto", "heuristic"],
         "auto=优先 LLM 智能解析，失败回退规则；heuristic=纯规则(离线最快)"),
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
