"""文件 / 语音解析（cordis 插件：ctx.io）。

- 文本类直接解码；PDF / DOCX / HTML 用可选依赖（缺则给提示，不崩）；
- 语音转写（ASR）三级：OpenAI 兼容接口（`[asr]` 配置，零依赖 urllib）→ 本机 whisper
  → 本机 vosk；都没有/失败时上层存音频 + 条目记“待转写”，不阻塞捕获。
"""
from __future__ import annotations

import io as _io
import os
import re
import tempfile
import uuid
from typing import Any, Dict, Optional, Tuple

from cordis import Service

# 常见音频扩展名 → Content-Type（远程 ASR 的分片上传要用）
_AUDIO_CT = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".webm": "audio/webm",
    ".aac": "audio/aac", ".flac": "audio/flac", ".amr": "audio/amr", ".3gp": "audio/3gpp",
}
_MODULES = ("whisper", "vosk")


class IOService(Service):
    provide = "io"
    name = "io"
    inject = ["config"]

    def __init__(self, ctx):
        super().__init__(ctx, "io")
        self._whisper_model = None
        self._vosk_model = None
        self._vosk_dir = ""

    # ---------------- 文本解析 ----------------
    def extract_text(self, filename: str, data: bytes) -> str:
        name = (filename or "").lower()
        if name.endswith((".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log",
                          ".py", ".js", ".ts", ".xml", ".yml", ".yaml")):
            return self._decode(data)
        if name.endswith(".pdf"):
            return self._pdf(data)
        if name.endswith(".docx"):
            return self._docx(data)
        if name.endswith((".html", ".htm")):
            t = self._decode(data)
            t = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", t, flags=re.I)
            t = re.sub(r"<[^>]+>", " ", t)
            return re.sub(r"\s+", " ", t).strip()
        return self._decode(data)

    def _decode(self, data: bytes) -> str:
        try:
            return data.decode("utf-8", "replace")
        except Exception:
            return data.decode("gbk", "replace")

    def _pdf(self, data: bytes) -> str:
        try:
            from pypdf import PdfReader
        except Exception:
            try:
                from PyPDF2 import PdfReader
            except Exception:
                return "[PDF 解析需安装 pypdf 或 PyPDF2（内网 pip）]"
        try:
            r = PdfReader(_io.BytesIO(data))
            return "\n".join((p.extract_text() or "") for p in r.pages)
        except Exception as e:
            return f"[PDF 解析失败：{e}]"

    def _docx(self, data: bytes) -> str:
        try:
            import docx
        except Exception:
            return "[DOCX 解析需安装 python-docx（内网 pip）]"
        try:
            d = docx.Document(_io.BytesIO(data))
            return "\n".join(p.text for p in d.paragraphs if p.text.strip())
        except Exception as e:
            return f"[DOCX 解析失败：{e}]"

    # ---------------- 语音转写（可选） ----------------
    def _cfg(self) -> Dict[str, Any]:
        c = self.ctx.config
        provider = str(c.get("asr.provider", "auto") or "auto").lower()
        if provider not in ("auto", "openai", "whisper", "vosk", "off"):
            provider = "auto"
        base = str(c.get("asr.base_url", "") or "").rstrip("/")
        key = str(c.get("asr.api_key", "") or "").strip()
        ai_base = str(c.get("ai.openai.base_url", "") or "").rstrip("/")
        ai_key = str(c.get("ai.openai.api_key", "") or "").strip()
        # 同源网关直接复用 [ai.openai] 的 Key，省一次配置
        if not key and base and ai_base and base.startswith(ai_base):
            key = ai_key
        if not key and base.startswith("https://api.openai.com"):
            key = os.environ.get("OPENAI_API_KEY", "")
        try:
            timeout = float(c.get("asr.timeout", 60) or 60)
        except Exception:
            timeout = 60.0
        return {
            "provider": provider,
            "base_url": base,
            "api_key": key,
            "model": str(c.get("asr.model", "") or "whisper-1"),
            "language": str(c.get("asr.language", "") or "").strip(),
            "vosk_model": str(c.get("asr.vosk_model", "") or "").strip(),
            "timeout": max(5.0, min(timeout, 300.0)),
        }

    @staticmethod
    def _has(mod: str) -> bool:
        try:
            __import__(mod)
            return True
        except Exception:
            return False

    def asr_available(self) -> bool:
        cfg = self._cfg()
        if cfg["provider"] == "off":
            return False
        if cfg["provider"] in ("auto", "openai") and cfg["base_url"]:
            return True
        if cfg["provider"] in ("auto", "whisper") and self._has("whisper"):
            return True
        if cfg["provider"] in ("auto", "vosk") and self._has("vosk") and cfg["vosk_model"]:
            return True
        return False

    def status(self) -> Dict[str, Any]:
        """给 /api/health、设置页看的 ASR 概览（不含密钥）。"""
        cfg = self._cfg()
        local = [m for m in _MODULES if self._has(m)]
        return {
            "provider": cfg["provider"],
            "remote": bool(cfg["base_url"]),
            "base_url": cfg["base_url"],
            "model": cfg["model"],
            "local": local,
            "available": self.asr_available(),
        }

    def transcribe(self, filename: str, data: bytes) -> Optional[str]:
        """兼容旧接口：只要文本，失败返回 None。"""
        text, _provider, _err = self.transcribe_ex(filename, data)
        return text

    def transcribe_ex(self, filename: str, data: bytes) -> Tuple[Optional[str], str, str]:
        """按配置顺序尝试转写，返回 (文本, 命中方式, 失败原因)。

        顺序：远程 OpenAI 兼容接口 → 本机 whisper → 本机 vosk。
        `asr.provider` 指定具体引擎时只试那一个；`off` 直接放弃。
        任何一步失败都不抛异常——上层会存音频并标记「待转写」。
        """
        cfg = self._cfg()
        prov = cfg["provider"]
        if prov == "off":
            return None, "", "语音转写已关闭（asr.provider=off）"
        errors = []

        def want(name: str) -> bool:
            return prov in ("auto", name)

        if want("openai"):
            if cfg["base_url"]:
                try:
                    text = self._remote(cfg, filename, data)
                    if text:
                        return text, "openai", ""
                    errors.append("远程接口返回空文本")
                except Exception as e:
                    errors.append(f"远程接口失败：{self._short(e)}")
            elif prov == "openai":
                errors.append("未配置 asr.base_url")

        if want("whisper"):
            if self._has("whisper"):
                try:
                    text = self._local_whisper(filename, data)
                    if text:
                        return text, "whisper", ""
                    errors.append("whisper 未识别出文字")
                except Exception as e:
                    errors.append(f"whisper 失败：{self._short(e)}")
            elif prov == "whisper":
                errors.append("本机未安装 openai-whisper")

        if want("vosk"):
            if self._has("vosk") and cfg["vosk_model"]:
                try:
                    text = self._local_vosk(cfg, data)
                    if text:
                        return text, "vosk", ""
                    errors.append("vosk 未识别出文字")
                except Exception as e:
                    errors.append(f"vosk 失败：{self._short(e)}")
            elif prov == "vosk":
                errors.append("未安装 vosk 或未配置 asr.vosk_model 目录")

        return None, "", "；".join(errors) or "未配置语音转写（设置 → 语音转写）"

    @staticmethod
    def _short(e: BaseException, n: int = 120) -> str:
        s = re.sub(r"\s+", " ", str(e) or type(e).__name__).strip()
        return s if len(s) <= n else s[:n] + "…"

    # ---- 远程：OpenAI 兼容 /audio/transcriptions（仅标准库 multipart）----
    def _remote(self, cfg: Dict[str, Any], filename: str, data: bytes) -> Optional[str]:
        import json
        import urllib.request

        base = cfg["base_url"]
        url = base if base.endswith("/audio/transcriptions") else base + "/audio/transcriptions"
        ext = os.path.splitext(filename or "")[1].lower() or ".m4a"
        ctype = _AUDIO_CT.get(ext, "application/octet-stream")
        fname = os.path.basename(filename or "") or ("voice" + ext)

        fields = {"model": cfg["model"], "response_format": "json"}
        if cfg["language"]:
            fields["language"] = cfg["language"]

        boundary = "----cordis" + uuid.uuid4().hex
        chunks = []
        for k, v in fields.items():
            chunks.append(f"--{boundary}\r\n"
                          f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
                          f"{v}\r\n".encode("utf-8"))
        chunks.append((f"--{boundary}\r\n"
                       f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
                       f"Content-Type: {ctype}\r\n\r\n").encode("utf-8"))
        chunks.append(data)
        chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(chunks)

        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}",
                   "Accept": "application/json"}
        if cfg["api_key"]:
            headers["Authorization"] = f"Bearer {cfg['api_key']}"
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        from . import tls
        with tls.urlopen(req, timeout=cfg["timeout"]) as resp:   # 带上随包 CA（Android 没有系统 CA）
            raw = resp.read()
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return None
        try:
            d = json.loads(text)
        except Exception:
            return text          # 少数网关直接回纯文本
        if isinstance(d, dict):
            for k in ("text", "result", "transcript", "transcription"):
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return None
        return text if isinstance(text, str) else None

    # ---- 本机 whisper ----
    def _local_whisper(self, filename: str, data: bytes) -> Optional[str]:
        import whisper as _whisper
        ext = os.path.splitext(filename or ".wav")[1] or ".wav"
        if self._whisper_model is None:
            self._whisper_model = _whisper.load_model("base")
        fd, tmp = tempfile.mkstemp(suffix=ext)
        os.close(fd)
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            res = self._whisper_model.transcribe(tmp)
            return (res.get("text") or "").strip() or None
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass

    # ---- 本机 vosk（需要 16k 单声道 wav 与模型目录）----
    def _local_vosk(self, cfg: Dict[str, Any], data: bytes) -> Optional[str]:
        import json
        import wave
        import vosk

        if self._vosk_model is None or self._vosk_dir != cfg["vosk_model"]:
            self._vosk_model = vosk.Model(cfg["vosk_model"])
            self._vosk_dir = cfg["vosk_model"]
        try:
            wf = wave.open(_io.BytesIO(data), "rb")
        except Exception:
            raise RuntimeError("vosk 仅支持 wav 音频（需 16k 单声道）")
        rec = vosk.KaldiRecognizer(self._vosk_model, wf.getframerate())
        rec.SetWords(False)
        parts = []
        while True:
            buf = wf.readframes(4000)
            if not buf:
                break
            if rec.AcceptWaveform(buf):
                parts.append(json.loads(rec.Result()).get("text") or "")
        parts.append(json.loads(rec.FinalResult()).get("text") or "")
        return (" ".join(p for p in parts if p).strip()) or None
