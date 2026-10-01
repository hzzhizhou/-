"""一轮请求的上下文：处理器需要的服务依赖 + 本轮请求数据。

用 dataclass 显式声明依赖，处理器不直接持有 RAGService，
便于单测里只装配需要的那几个字段（例如只测投诉建单时不必造完整服务）。
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class TurnContext:
    # ---- 服务依赖（进程内构造一次，跨轮复用）----
    retrieval_service: Any      # 含路由/rerank/置信度门控的检索服务
    answer_generator: Any       # 流式答案生成器
    dst_manager: Any            # 跨轮会话状态管理
    slot_extractor: Any         # 槽位提取器（无 LLM 时为 None）
    # ---- 本轮请求数据 ----
    question: str
    session_id: str             # 已按登录用户作用域化的会话 ID
    user: Dict[str, Any]        # 当前登录用户（含 user_id）
    dst: Optional[Any] = None   # 本轮加载的 DST 状态（DST_ENABLED 为假时是 None）
    intent_info: Optional[Dict[str, Any]] = None   # 本轮意图分类结果