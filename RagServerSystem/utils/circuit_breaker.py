"""
通用熔断器（Circuit Breaker）
三态：CLOSED（正常）→ OPEN（熔断）→ HALF_OPEN（探测）

设计：
  - 每个外部依赖独立一个 CircuitBreaker 实例（互不影响）
  - CLOSED 态：连续失败达 threshold → 转 OPEN
  - OPEN 态：拒绝所有请求，等待 recovery_timeout 秒 → 转 HALF_OPEN
  - HALF_OPEN 态：放行 max_probe_calls 个探测请求
    - 探测成功 → CLOSED（恢复）
    - 探测失败 → OPEN（继续熔断）
  - 线程安全（FastAPI 线程池场景）

用法：
    cb = CircuitBreaker("llm")
    if cb.allow_request():
        try:
            result = call_external()
            cb.record_success()
        except Exception as e:
            cb.record_failure()
            raise
    else:
        raise CircuitOpenError("llm 熔断中")
"""
import time
import threading
from enum import Enum
from config.settings import (
    CIRCUIT_FAILURE_THRESHOLD,
    CIRCUIT_RECOVERY_TIMEOUT,
    CIRCUIT_HALF_OPEN_MAX_CALLS,
)
from logs.log_config import log


class CircuitState(Enum):
    CLOSED = "closed"       # 正常，放行
    OPEN = "open"           # 熔断，拒绝
    HALF_OPEN = "half_open" # 探测，限流放行


class CircuitOpenError(Exception):
    """熔断器开启时抛出，调用方应降级处理"""
    pass


class CircuitBreaker:
    def __init__(self, name: str,
                 failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD,
                 recovery_timeout: float = CIRCUIT_RECOVERY_TIMEOUT,
                 half_open_max_calls: int = CIRCUIT_HALF_OPEN_MAX_CALLS):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            # OPEN 态自动转 HALF_OPEN（时间到了）
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    log.info(f"[熔断器:{self.name}] OPEN→HALF_OPEN，开始探测")
            return self._state

    def allow_request(self) -> bool:
        """是否放行请求。OPEN 态拒绝，HALF_OPEN 态限流放行"""
        with self._lock:
            # OPEN 态：检查是否该转 HALF_OPEN
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    log.info(f"[熔断器:{self.name}] OPEN→HALF_OPEN，开始探测")
                    self._half_open_calls += 1
                    return True
                return False
            # HALF_OPEN 态：只放行限定数量的探测请求
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False
            # CLOSED 态：放行
            return True

    def record_success(self):
        """请求成功：重置计数，HALF_OPEN→CLOSED"""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                log.info(f"[熔断器:{self.name}] HALF_OPEN→CLOSED，依赖恢复")
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._half_open_calls = 0

    def record_failure(self):
        """请求失败：累加计数，达阈值→OPEN"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._state == CircuitState.HALF_OPEN:
                # 探测失败，回退 OPEN
                log.warning(f"[熔断器:{self.name}] HALF_OPEN→OPEN，探测失败")
                self._state = CircuitState.OPEN
            elif self._failure_count >= self.failure_threshold:
                log.warning(f"[熔断器:{self.name}] CLOSED→OPEN，连续失败 {self._failure_count} 次")
                self._state = CircuitState.OPEN


# ====================== 各依赖的熔断器单例 ======================
llm_breaker = CircuitBreaker("llm")           # 通义 LLM
redis_breaker = CircuitBreaker("redis")       # Redis
