"""HTTP 请求/响应模型（Pydantic）。

api_key 的默认值取自配置，前端可不传；后端仍会用 DataSecurity 再校验一次。
"""
from typing import Optional

from pydantic import BaseModel

from config.settings import DASHSCOPE_API_KEY


class RAGRequest(BaseModel):
    question: str
    route_mode: str = "rule"
    api_key: str = DASHSCOPE_API_KEY
    session_id: Optional[str] = None
    use_context: bool = True
    use_hyde: bool = False
    use_multi: bool = False


class AgentRequest(BaseModel):
    question: str
    api_key: str = DASHSCOPE_API_KEY
    session_id: Optional[str] = None
