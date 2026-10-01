import redis
import redis.asyncio as redis_async
from config.settings import REDIS_CONFIG
from logs.log_config import chat_history_log as log

def _pool_kwargs() -> dict:
    return {
        "host": REDIS_CONFIG["host"],
        "port": REDIS_CONFIG["port"],
        "password": REDIS_CONFIG["password"],
        "db": REDIS_CONFIG["db"],
        "decode_responses": True,
        "socket_timeout": REDIS_CONFIG["socket_timeout"],
        "retry_on_timeout": True,
        "max_connections": REDIS_CONFIG["max_connections"],
    }

_sync_pool = None
def get_redis_connection():
    """
    【同步】Redis 客户端：仅供线程内使用（后台任务、Agent 工具执行、健康检查 to_thread 等）。
    注意：不要在异步事件循环内直接调用它，否则会阻塞事件循环 —— 事件循环内请用 get_redis_async()。
    """
    global _sync_pool
    if _sync_pool is None:
        try:
            _sync_pool = redis.ConnectionPool(**_pool_kwargs())
            test_client = redis.Redis(connection_pool=_sync_pool)
            test_client.ping()
            log.info("Redis 同步连接池初始化成功")
        except Exception as e:
            log.error(f"Redis 连接池初始化失败: {e}")
            raise
    return redis.Redis(connection_pool=_sync_pool)

# 异步客户端：仅绑定到“创建它的那个事件循环”（主循环）。同一进程内不要在别的循环里复用。
_async_client = None
async def get_redis_async():
    """
    【异步】Redis 客户端：必须在事件循环内 await 调用（主事件循环）。
    惰性创建，不阻塞；Redis 不可用时抛错，由调用方 try/except 降级。
    """
    global _async_client
    if _async_client is None:
        _async_client = redis_async.Redis(
            connection_pool=redis_async.ConnectionPool(**_pool_kwargs()))
    return _async_client
