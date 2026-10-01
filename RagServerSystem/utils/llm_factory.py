"""
统一 LLM 工厂：DashScope OpenAI 兼容端点（OpenAI 兼容协议调用通义模型）。
背景：通义原生 ChatTongyi/dashscope 无法识别 qwen3.7-flash 模型名（url error），
统一改用 OpenAI 兼容端点。key、模型均为通义账号，仅调用协议不同。
"""
from typing import Optional

from langchain_openai import ChatOpenAI

from config.settings import (
    LLM_MODEL, LLM_TEMPERATURE,
    DASHSCOPE_API_KEY, LLM_BASE_URL,
)


def create_llm(model: Optional[str] = None,
               temperature: Optional[float] = None,
               streaming: bool = True,
               top_p: Optional[float] = None,
               seed: Optional[int] = None) -> ChatOpenAI:
    """创建指向 DashScope OpenAI 兼容端点的 LLM。"""
    kwargs = dict(api_key=DASHSCOPE_API_KEY, base_url=LLM_BASE_URL, streaming=streaming)
    if top_p is not None:
        kwargs['top_p'] = top_p
    if seed is not None:
        kwargs['seed'] = seed
    return ChatOpenAI(
        model=model or LLM_MODEL,
        temperature=temperature if temperature is not None else LLM_TEMPERATURE,
        **kwargs,
    )