from pathlib import Path
import sys
sys.path.append(str(Path(__file__).parent.parent))

from infrastructure.mysql_history import MysqlChatHistory
from logs.log_config import chat_history_log as log
import time
def init_chat_history(session_id:str):
    """取会话历史后端。

    历史以 MySQL 为准、Redis 只做热缓存（见 infrastructure/mysql_history.py），
    因此「Redis 不可用」不再需要降级到进程内内存：库还在，历史照读照写。
    仅当入参非法等构造失败时，才退回内存实现兜底。
    """
    start_time = time.time()
    try:
        chat_history = MysqlChatHistory(session_id)
        log.info(f"会话历史后端就绪（MySQL + Redis 热缓存），耗时：{time.time()-start_time:.2f}秒")
        return chat_history
    except Exception as e:
        log.error(f"会话历史后端初始化失败，启用内存存储{e}")
        from langchain_core.chat_history import InMemoryChatMessageHistory
        return InMemoryChatMessageHistory()

if __name__=='__main__':
    # 手动冒烟：写一条再读回来（session_id 必须是作用域 ID：u{user_id}_{session_id}）
    from langchain_core.messages import HumanMessage
    chat_history = init_chat_history("u0123456789abcdef0123456789abcdef_demo0001")
    chat_history.add_message(HumanMessage(content="冒烟测试"))
    print(chat_history.messages())