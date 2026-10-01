"""
知识库检索工具（search_knowledge）
注入 RetrievalService（含路由/查询改写/rerank/置信度门控），故以工厂形式提供，
让工具与它的依赖保持显式绑定，避免引入模块级全局状态。
"""
from langchain_core.tools import tool

from retrieval.service import RetrievalService


def build_search_knowledge(retrieval_service: RetrievalService):
    """构造知识库检索工具，注入检索服务实例。"""

    @tool
    async def search_knowledge(query: str) -> str:
        """从企业内部知识库中检索信息，适用于产品FAQ、售后政策、退换货规则、技术手册等。
        返回结果会包含置信度信息：当置信度为 low 时，应考虑调用 create_ticket 转人工。"""
        # 声明为 async 工具：由 Agent 在应用主事件循环上直接 await。
        # 若保留同步工具，LangChain 会把 _run 丢进线程池执行，asyncio.run() 会在该线程里
        # 另起一个事件循环，而检索链路用到的异步 Redis 客户端绑定在主循环上，跨循环会报错。
        docs, retriever_type, gate_info = await retrieval_service.retrieve(
            question=query,
            use_context=False,   # Agent 模式下不使用对话历史改写（历史已在 Agent 层拼接）
            use_hyde=False,
            use_multi=False,
            auto_filter=True,    # 按问题关键词推断文档类别，先在 metadata 上缩小范围再检索
        )
        if not docs:
            return "未找到相关信息。建议调用 create_ticket 转人工处理。"

        contexts = []
        for i, doc in enumerate(docs[:5]):
            file_name = doc.metadata.get("file_name", "未知文档")
            content = doc.page_content[:500]
            contexts.append(f"【来源{i+1}：{file_name}】\n{content}")

        # 把置信度门控信息附在结果末尾，供 Agent 决策是否转人工
        level = gate_info.get("level", "unknown")
        top_score = gate_info.get("top_score", 0.0)
        should_escalate = gate_info.get("should_escalate", False)

        result = "\n\n".join(contexts)
        result += (
            f"\n\n---检索质量---\n置信度: {level} (top_score={top_score})"
            f"\n建议转人工: {'是' if should_escalate else '否'}"
        )
        if should_escalate:
            result += "\n注意：本次检索置信度较低，建议调用 create_ticket 转交人工客服。"
        return result

    return search_knowledge
