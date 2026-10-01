"""HTTP 中间件：滑动窗口限流 + Prometheus 请求指标。

单进程实现：限流桶按 key（X-API-KEY 或客户端 IP）各自维护最近一分钟的时间戳队列，
多 worker 部署时需换成 Redis 计数才能全局生效。
"""
import time
from collections import defaultdict, deque
from typing import Dict

from fastapi import Request
from fastapi.responses import JSONResponse

from config.settings import RATE_LIMIT_PER_MINUTE
from utils.metrics import http_requests_total, http_request_duration_seconds

# 监控端点放行：健康检查与指标抓取被限流会直接影响告警，不能计入配额
_PASS_PATHS = ("/health", "/metrics")


class RateLimiter:
    """按 key 的滑动窗口限流器（1 分钟窗口，内存字典）。"""

    def __init__(self, per_minute: int = RATE_LIMIT_PER_MINUTE):
        self.per_minute = per_minute
        self._bucket: Dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """返回 True 表示放行，False 表示超限。"""
        now = time.time()
        bucket = self._bucket[key]
        # 清理一分钟前的记录
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= self.per_minute:
            return False
        bucket.append(now)
        return True


def register_middleware(app, limiter: RateLimiter) -> None:
    """注册限流 + 请求计速中间件。"""

    @app.middleware("http")
    async def rate_limit_and_metrics(request: Request, call_next):
        # 健康检查与指标端点放行
        if request.url.path in _PASS_PATHS:
            return await call_next(request)
        # 限流 key：优先 X-API-KEY header，降级用客户端 IP（不读 body 以免破坏下游路由）
        limit_key = request.headers.get("X-API-KEY", "") or request.client.host
        if not limiter.allow(limit_key):
            return JSONResponse(
                {"detail": f"请求过于频繁，限流 {limiter.per_minute}/分钟"},
                status_code=429,
            )
        start = time.time()
        response = await call_next(request)
        elapsed = time.time() - start
        # Prometheus 指标
        http_requests_total.labels(
            request.method, request.url.path, str(response.status_code)
        ).inc()
        http_request_duration_seconds.labels(
            request.method, request.url.path
        ).observe(elapsed)
        return response