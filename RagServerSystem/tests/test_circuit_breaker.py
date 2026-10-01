"""熔断器（Circuit Breaker）三态单元测试。"""
import time

from utils.circuit_breaker import CircuitBreaker, CircuitState


def test_initial_state_closed():
    cb = CircuitBreaker("t", failure_threshold=2, recovery_timeout=0.1)
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True


def test_closed_to_open_after_threshold_failures():
    cb = CircuitBreaker("t", failure_threshold=2, recovery_timeout=10)
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED  # 未达阈值仍关闭
    assert cb.allow_request() is True
    cb.record_failure()
    assert cb.state == CircuitState.OPEN    # 达阈值熔断
    assert cb.allow_request() is False      # OPEN 拒绝


def test_open_to_half_open_after_timeout():
    cb = CircuitBreaker("t", failure_threshold=1, recovery_timeout=0.05)
    cb.record_failure()                     # 进入 OPEN
    assert cb.allow_request() is False
    time.sleep(0.06)                        # 等待恢复窗口
    # OPEN 态自动转 HALF_OPEN：放行首个探测请求
    assert cb.allow_request() is True
    assert cb.state == CircuitState.HALF_OPEN


def test_half_open_success_recovers_to_closed():
    cb = CircuitBreaker("t", failure_threshold=1, recovery_timeout=0.01)
    cb.record_failure()
    time.sleep(0.02)
    assert cb.allow_request() is True       # 探测放行
    cb.record_success()                     # 探测成功 → 恢复 CLOSED
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True


def test_half_open_failure_reopens():
    cb = CircuitBreaker("t", failure_threshold=1, recovery_timeout=0.05)
    cb.record_failure()                     # → OPEN
    assert cb.allow_request() is False      # 熔断拒绝
    time.sleep(0.06)                        # 超过恢复窗 → 转 HALF_OPEN
    assert cb.allow_request() is True       # HALF_OPEN 探测放行
    cb.record_failure()                     # 探测失败 → 回 OPEN
    # 刚回 OPEN，恢复窗未到 → 应拒绝（避免读到被自动推进的 HALF_OPEN）
    assert cb.allow_request() is False


def test_half_open_limits_probe_calls():
    cb = CircuitBreaker("t", failure_threshold=1, recovery_timeout=0.01,
                        half_open_max_calls=2)
    cb.record_failure()
    time.sleep(0.02)
    assert cb.allow_request() is True       # 探测1
    assert cb.allow_request() is True       # 探测2
    assert cb.allow_request() is False      # 超限 → 拒绝