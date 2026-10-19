"""文件 / 语音解析（cordis 插件：ctx.io）。

- 文本类直接解码；PDF / DOCX / HTML 用可选依赖（缺则给提示，不崩）；
- ASR 可选（whisper/vosk）；未配置时上层存文件 + 记“待转写”。
"""
from __future__ import annotations

import io as _io
import re
from typing import Optional

from cordis import Service


class IOService(Service):
    provide = "io"
    name = "io"
    inject = []

    def __init__(self, ctx):
        super().__init__(ctx, "io")

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

    def asr_available(self) -> bool:
        for mod in ("vosk", "whisper"):
            try:
                __import__(mod)
                return True
            except Exception:
                continue
        return False

    def transcribe(self, filename: str, data: bytes) -> Optional[str]:
        """可选 ASR（whisper）；未安装/失败返回 None，由上层降级为“待转写”。"""
        try:
            import os
            import tempfile
            import whisper
            ext = os.path.splitext(filename or ".wav")[1] or ".wav"
            fd, tmp = tempfile.mkstemp(suffix=ext)
            os.close(fd)
            with open(tmp, "wb") as f:
                f.write(data)
            try:
                res = whisper.load_model("base").transcribe(tmp)
                return (res.get("text") or "").strip() or None
            finally:
                os.remove(tmp)
        except Exception:
            return None
        return None
