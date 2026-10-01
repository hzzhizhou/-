from abc import ABC, abstractmethod
from typing import List

from langchain_core.messages import BaseMessage, HumanMessage


class BaseChatHistory(ABC):
    @abstractmethod
    def messages(self)->List[BaseMessage]:
        pass
    @abstractmethod
    def add_message(self,messages:BaseMessage):
        pass

    @abstractmethod
    def clear(self) -> None:
        pass

    def rewrite_question(self, question: str, llm) -> str:
        """根据对话历史把当前问题改写成独立完整的问题（消除指代、补全省略）。

        放在基类：只依赖 self.messages() 与传入的 llm，与具体存储后端无关。
        同步实现（供线程池调用）；历史为空时直接返回原问题，不消耗 LLM。
        """
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser
        from logs.log_config import chat_history_log as log

        # 获取历史消息（最近 6 条，即 3 轮对话）
        history_messages = self.messages()[-6:] if self.messages() else []

        if not history_messages:
            # 无历史则直接返回原问题
            return question

        # 格式化历史
        history_text = ""
        for msg in history_messages:
            role = "用户" if isinstance(msg, HumanMessage) else "助手"
            history_text += f"{role}：{msg.content}\n"

        # 构建改写提示
        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是一个查询改写助手。可以判断对话历史与用户问题的相关性，来决定是否改写用户当前问题，改写后的问题确保不丢失任何关键信息。
            要求：
            1. 只输出改写后的问题
            2. 如果当前问题本身已经完整，可以不修改。
            3.多轮对话中，结合历史消除指代（如“它”、“那”）。
            4.修正错别字、口语化表达，使其更符合文档风格。
            5.补全省略的主语或条件。"""),
            ("human", "对话历史：\n{history}\n当前问题：{question}\n改写后的问题：")
        ])

        chain = prompt | llm | StrOutputParser()
        try:
            rewritten = chain.invoke({
                "history": history_text,
                "question": question
            }).strip()
            log.info(f"查询改写：原问题='{question}' → 改写后='{rewritten}'")
            return rewritten
        except Exception as e:
            log.error(f"查询改写失败，使用原问题：{e}")
            return question