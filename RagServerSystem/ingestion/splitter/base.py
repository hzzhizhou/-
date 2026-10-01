"""分块公共工具函数"""
import re
from typing import List, Tuple
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter
from config.settings import MIN_CHUNK_SIZE
from logs.log_config import data_layer_log as log
from utils.doc_identity import make_doc_id, make_chunk_text_hash, normalize_doc_path

# Markdown 标题层级配置
HEADERS_TO_SPLIT_ON = [
    ("#", "Header1"),
    ("##", "Header2"),
    ("###", "Header3"),
]

# ====================== Markdown 表格保护 ======================
# 表格行必须整行保真：按标点/换行切分会把「表头与数据行」拆开、把一行里的多列拆到
# 不同的块，检索命中 "599" 这类单元格时无法还原它属于哪一行哪一列。
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def is_markdown_table_row(line: str) -> bool:
    """判断单行是否为 Markdown 表格行（形如 | a | b |）"""
    return bool(TABLE_ROW_RE.match(line))


def is_table_doc(chunk: Document) -> bool:
    """判断块是否由表格解析器生成：这类块已按「表头 + 整行」分组，长度受控，无需再切"""
    return str(chunk.metadata.get("is_table", "")).lower() == "true"


def split_table_segments(text: str) -> List[Tuple[bool, str]]:
    """
    把文本按「连续表格段 / 普通文本段」切开，返回 [(是否表格段, 文本)]，保持原始顺序
    """
    segments: List[Tuple[bool, str]] = []
    buffer: List[str] = []
    in_table = False
    for line in text.split("\n"):
        line_is_table = is_markdown_table_row(line)
        if line_is_table != in_table and buffer:
            segments.append((in_table, "\n".join(buffer)))
            buffer = []
        in_table = line_is_table
        buffer.append(line)
    if buffer:
        segments.append((in_table, "\n".join(buffer)))
    return segments


def markdown_table_chunks(text: str, budget: int) -> List[str]:
    """
    把一段 Markdown 表格按「表头 + 若干完整数据行」分组，每组字符数不超过 budget。
    单行本身超过 budget 时该行独占一组：宁可略超预算，也不切开一行。
    :param text: 表格段文本（首行为表头，允许紧跟分隔行 |---|）
    :param budget: 单组字符预算
    :return: 每组都自带表头的 Markdown 表格文本列表
    """
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return []
    header = lines[0]
    has_separator = len(lines) > 1 and bool(TABLE_SEPARATOR_RE.match(lines[1]))
    body = lines[2:] if has_separator else lines[1:]
    prefix = header + ("\n" + lines[1] if has_separator else "")
    if not body:
        return [prefix]

    groups: List[List[str]] = []
    current: List[str] = []
    size = len(prefix)
    for row in body:
        if current and size + len(row) + 1 > budget:
            groups.append(current)
            current, size = [], len(prefix)
        current.append(row)
        size += len(row) + 1
    if current:
        groups.append(current)
    return [prefix + "\n" + "\n".join(group) for group in groups]


def split_by_markdown_headers(docs: List[Document]) -> List[Document]:
    """
    对文档列表按 Markdown 标题结构进行预切分（保留原始元数据）
    :param docs: 原始文档列表
    :return: 按标题切分后的文档列表（未进行大小控制）
    """
    result = []
    try:
        md_splitter = MarkdownHeaderTextSplitter(HEADERS_TO_SPLIT_ON)
        for doc in docs:
            # 仅对 markdown 文件进行结构切分
            if doc.metadata.get("file_type") == "md":
                chunks = md_splitter.split_text(doc.page_content)
                # 将原始元数据复制到每个子块
                for chunk in chunks:
                    chunk.metadata.update(doc.metadata.copy())
                result.extend(chunks)
            else:
                result.append(doc)
    except Exception as e:
        log.error(f"Markdown 结构切分失败: {e}")
        return docs
    return result


def resolve_doc_source(chunk: Document) -> str:
    """从块元数据中解析文档来源路径（优先 file_path，兼容 source/file_name）。"""
    return (chunk.metadata.get("file_path")
            or chunk.metadata.get("source")
            or chunk.metadata.get("file_name")
            or "")


def make_chunk_id(chunk: Document, index: int) -> str:
    """
    生成企业规范的确定性块 ID：<doc_id>::chunk::<序号>
    - doc_id      = sha256(归一化绝对路径)  → 稳定、路径非主键、跨模块同源
    - 序号(seq)   = 块在文档内的顺序        → 可定位、可替换
    同一文件重新入库时 doc_id 稳定，向量库 upsert 可直接覆盖旧块，
    增量更新时状态库(MySQL doc_chunks)记录的 chunk_id 与向量库一致，可对齐删除。
    """
    doc_id = make_doc_id(resolve_doc_source(chunk))
    return f"{doc_id}::chunk::{index:04d}"


def make_parent_id(parent: Document, index: int) -> str:
    """
    生成父块映射的唯一键：<doc_id>::parent::<序号>
    用 doc_id（路径哈希）替代原始文件名，避免"不同目录下同名文件"的父块键碰撞；
    用 ::parent:: 前缀与子块 id(::chunk::) 区分。
    父块不写向量库，仅作检索后回查完整上下文的映射键。
    """
    doc_id = make_doc_id(resolve_doc_source(parent))
    return f"{doc_id}::parent::{index:04d}"


def prepend_section_path(chunk: Document) -> None:
    """
    把 Markdown 标题路径回填到块正文开头（就地修改）。

    MarkdownHeaderTextSplitter 默认 strip_headers=True：标题行会被剥离出 page_content，
    只留在 metadata（Header1/2/3），而全项目没有任何地方读这几个键。后果是向量与 BM25
    都索引不到章节名，「第十五章」「附则」这类章节指代型提问召回不到；段落也失去了所属
    小节的上下文（如「为统一…特制定本流程」脱离「1.1 制定目的」标题后可读性变差）。

    这里按「文档标题 > 章 > 节」拼成前缀写回正文，让标题重新参与检索与生成。
    非 Markdown 块没有 Header 元数据，直接跳过；已带前缀的块也跳过——组合分块的子块会
    被 add_chunk_metadata 处理两次，必须保证幂等，否则前缀会叠加两遍。
    """
    parts = [str(chunk.metadata.get(k) or "").strip()
             for k in ("Header1", "Header2", "Header3")]
    parts = [p for p in parts if p]
    if not parts:
        return
    prefix = f"【{' > '.join(parts)}】"
    if not chunk.page_content.lstrip().startswith(prefix):
        chunk.page_content = f"{prefix}\n{chunk.page_content}"


def add_chunk_metadata(chunks: List[Document], chunk_type: str = "normal",
                       min_chunk_size: int = MIN_CHUNK_SIZE) -> List[Document]:
    """
    过滤过短块，并为分块添加标准元数据（id, doc_id, chunk_index, chunk_type,
    chunk_text_hash），同时把 Markdown 标题路径回填进正文（见 prepend_section_path）
    :param chunks: 分块列表
    :param chunk_type: 块类型标识（normal, child, parent, recursive 等），以传入值为准
    :param min_chunk_size: 最小块长度，低于该长度的块被丢弃（避免无效 embedding）
    :return: 处理后的列表
    """
    valid_chunks = [c for c in chunks if len(c.page_content.strip()) >= min_chunk_size]
    dropped = len(chunks) - len(valid_chunks)
    if dropped:
        log.info(f"过滤过短块: 丢弃 {dropped} 个（阈值 {min_chunk_size} 字符）")
    for i, chunk in enumerate(valid_chunks):
        # 标题路径放在 chunk_text_hash 之前回填：指纹要反映真正入库的最终内容
        prepend_section_path(chunk)
        # 统一归一化 sources：无论来自哪个 loader，入库/比对都用正斜杠绝对路径
        for _key in ("file_path", "source"):
            if chunk.metadata.get(_key):
                chunk.metadata[_key] = normalize_doc_path(chunk.metadata[_key])
        chunk.metadata["chunk_index"] = i
        # chunk_type 以调用方传入的为准（覆盖继承值）：子块会从父块继承元数据，若这里
        # "已有就不覆盖"，父块的 semantic 标签会被 RecursiveCharacterTextSplitter 复制到
        # 所有子块上，导致子块类型全部误标为 semantic（实测 29/29 中招）。
        chunk.metadata["chunk_type"] = chunk_type
        # 企业规范：doc_id 稳定可锚定；块内容指纹用于去重/溯源
        chunk.metadata["doc_id"] = make_doc_id(resolve_doc_source(chunk))
        chunk.metadata["chunk_text_hash"] = make_chunk_text_hash(chunk.page_content)
        chunk.metadata["id"] = make_chunk_id(chunk, i)
    return valid_chunks


def split_sentences(text: str) -> List[str]:
    """
    简单的中英文句子分割（基于标点）
    表格段不参与标点切分：整段表格作为整体返回，避免表格被切散
    :param text: 原始文本
    :return: 句子列表
    """
    # 匹配句号、感叹号、问号、分号等结尾的句子
    pattern = r'(?<=[。！？!?;；])\s*'
    sentences: List[str] = []
    for is_table, segment in split_table_segments(text):
        if not segment.strip():
            continue
        if is_table:
            sentences.append(segment.strip())
        else:
            sentences.extend(s.strip() for s in re.split(pattern, segment) if s.strip())
    return sentences