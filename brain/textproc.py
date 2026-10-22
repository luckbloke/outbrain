"""文本处理：中文检索分词（零依赖打底，jieba 可选增强）。

策略：
  - Latin/数字：整词小写。
  - CJK：字符 2-gram 展开（使 FTS5/unicode61 对中文短词可召回）。
  - 若安装了 jieba：对 CJK 先用 jieba 分词，再对长词 2-gram 化，召回更准。

索引与查询共用 segment()，保证对称。
"""
from __future__ import annotations

import re
from typing import List, Optional

try:  # 可选依赖
    import jieba as _JIEBA  # type: ignore
except Exception:  # pragma: no cover
    _JIEBA = None

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")


def _bigrams(run: str) -> List[str]:
    if len(run) <= 1:
        return [run] if run else []
    return [run[i:i + 2] for i in range(len(run) - 1)]


def segment(text: Optional[str]) -> str:
    """返回空格分隔的检索 token（CJK 2-gram + Latin 整词小写）。"""
    text = text or ""
    out: List[str] = []
    for m in _TOKEN_RE.finditer(text):
        run = m.group(0)
        if run[0].isascii():
            out.append(run.lower())
        else:
            if _JIEBA is not None and len(run) >= 2:
                for w in _JIEBA.lcut(run):
                    w = w.strip()
                    if not w:
                        continue
                    if len(w) >= 2:
                        out.extend(_bigrams(w))
                    else:
                        out.append(w)
            else:
                out.extend(_bigrams(run))
    return " ".join(out)


def build_fts_match(query: Optional[str], max_terms: int = 24) -> Optional[str]:
    """把查询转成安全的 FTS5 MATCH 表达式（OR 召回，交给 BM25 排序）。"""
    if not query:
        return None
    toks = segment(query).split()
    seen: List[str] = []
    for t in toks:
        t = t.replace('"', "").strip()
        if t and t not in seen:
            seen.append(t)
        if len(seen) >= max_terms:
            break
    if not seen:
        return None
    return " OR ".join('"%s"' % t for t in seen)
