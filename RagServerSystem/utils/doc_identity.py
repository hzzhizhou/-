"""文档/分块身份标识工具：统一路径归一化与 doc_id 生成。

设计目的（对齐企业命名规范）：
- 文件路径不再直接作为主键使用（路径会因移动/重命名/换机而变化，
  且 Windows 反斜杠 / POSIX 正斜杠不一致会导致跨模块比对失败）。
- 一律先归一化为绝对 POSIX 路径，再以其 sha256 生成稳定的 doc_id。
- doc_id 是"能定位 + 稳定可替换"的锚：同一文件重入库会得到相同 doc_id，
  向量库 upsert 可覆盖旧块，MySQL doc_chunks 也能按 doc_id 对齐删除。
"""
import hashlib
from pathlib import Path
from typing import Optional
from uuid import uuid4

_DOC_PREFIX = "doc"
_DOC_HASH_BYTES = 24  # 12 字节十六进制 = 24 字符，足够唯一且短


def normalize_doc_path(raw: Optional[str]) -> str:
    """把任意来源的文件路径归一化为绝对 POSIX 字符串（正斜杠）。
    无法解析时返回空字符串，由调用方决定兜底。"""
    if not raw:
        return ""
    try:
        return Path(str(raw)).absolute().as_posix()
    except Exception:
        return ""


def make_doc_id(raw: Optional[str]) -> str:
    """基于归一化路径生成稳定的 doc_id：doc:<sha256前24位>。
    统一在分块器(写 metadata)与 MySQL 状态器(写 documents.id)调用，
    保证两侧 id 同源、可直接对齐删除。"""
    norm = normalize_doc_path(raw)
    if not norm:
        # 无路径兜底：使用 uuid，避免"unknown_0"之类的 id 冲突
        return f"{_DOC_PREFIX}:{uuid4().hex}"
    digest = hashlib.sha256(norm.encode("utf-8", errors="replace")).hexdigest()
    return f"{_DOC_PREFIX}:{digest[:_DOC_HASH_BYTES]}"


def make_chunk_text_hash(content: str) -> str:
    """块内容指纹（企业去重/溯源用）：sha256(块文本) 前 16 字符。"""
    if not content:
        return "empty"
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]