import re
from typing import List
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from logs.log_config import log
from shared.constants import SUBQ_MIN_LEN

# LLM 输出里可能残留的列表编号/项目符号（提示词已要求不要编号，这里只做防御性清洗）
_LIST_MARK_RE = re.compile(r"^\s*(?:\d+\s*[.、)）]|[-*•])\s*")


class QueryEnhancer:
    def __init__(self, llm):
        self.llm = llm
        self.multi_query_prompt = ChatPromptTemplate.from_messages([
            ("system", "请为以下问题生成 {n} 个不同的搜索查询，用逗号分隔，每个查询应覆盖不同的角度。"),
            ("human", "问题：{question}\n查询：")
        ])
        self.decompose_prompt = ChatPromptTemplate.from_messages([
            ("system", "用户可能一次提出多个问题。请把它们拆成相互独立的子问题，每行一个，"
                       "不要编号、不要解释、不要增删原意；如果本来只有一个问题，原样输出即可。"),
            ("human", "用户提问：{question}")
        ])
        self.synonym_prompt = ChatPromptTemplate.from_messages([
            ("system", "请为以下查询中的关键词生成同义词或相关词，返回扩展后的查询。"),
            ("human", "查询：{query}\n扩展后：")
        ])
        self.hyde_prompt = ChatPromptTemplate.from_messages([
            ("system", "请根据以下问题，生成一段可能出现在相关文档中的文本（假设文档）。该文档应详细、专业，能够用于语义检索。"),
            ("human", "问题：{question}\n假设文档：")
        ])

    async def hyde(self, question: str) -> str:
        chain = self.hyde_prompt | self.llm | StrOutputParser()
        try:
            return await chain.ainvoke({"question": question})
        except Exception as e:
            log.error(f"HyDE 生成失败: {e}")
            return question

    async def multi_query(self, question: str, n: int = 3) -> List[str]:
        chain = self.multi_query_prompt | self.llm | StrOutputParser()
        try:
            result = await chain.ainvoke({"question": question, "n": n})
            queries = [q.strip() for q in result.split(",")]
            return queries[:n]
        except Exception as e:
            log.error(f"多查询生成失败: {e}")
            return [question]

    async def decompose_query(self, question: str) -> List[str]:
        """把一次提出的多个问题拆成互不依赖的子问题（规则拆不动时的 LLM 兜底）。

        只由调用方在「规则拆不出多段、但文本看起来像多问题」时触发，不常驻在链路上；
        拆不动（模型判为单问 / 调用失败）时原样返回 [question]，调用方按单问处理。
        """
        chain = self.decompose_prompt | self.llm | StrOutputParser()
        try:
            result = await chain.ainvoke({"question": question})
        except Exception as e:
            log.error(f"查询分解失败: {e}")
            return [question]
        parts: List[str] = []
        for line in result.splitlines():
            line = _LIST_MARK_RE.sub("", line).strip()
            if len(line) >= SUBQ_MIN_LEN and line not in parts:
                parts.append(line)
        return parts if len(parts) >= 2 else [question]

    async def synonym_expand(self, query: str) -> str:
        chain = self.synonym_prompt | self.llm | StrOutputParser()
        try:
            return await chain.ainvoke({"query": query})
        except Exception:
            return query