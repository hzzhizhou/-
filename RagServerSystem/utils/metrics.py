"""
定义了智能客服系统的指标，用于监控和分析系统的运行状态。
指标包括 HTTP 请求、Agent 调用、系统信息等。
"""
from prometheus_client import Counter, Histogram, Gauge, Info

# HTTP 请求指标
http_requests_total = Counter(
    'web_agent_http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

http_request_duration_seconds = Histogram(
    'web_agent_http_request_duration_seconds',
    'HTTP request duration',
    ['method', 'endpoint'],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
)

# Agent 调用指标
agent_duration_seconds = Histogram(
    'web_agent_agent_duration_seconds',
    'Agent invocation duration',
    buckets=(0.5, 1, 2, 4, 8, 15)
)

# 系统信息
# 下面这段代码用于定义/暴露服务运行的静态信息（如版本、环境）
system_info = Info(
    'web_agent_system',                # 指标名称
    'System information'               # 指标描述
)
system_info.info({
    'version': '1.0',                  # 版本号
    'environment': 'production'        # 环境类型
})

# ====================== 智能售后客服工具指标 ======================
# 订单查询工具
order_queries_total = Counter(
    'customer_service_order_queries_total',
    'Total order query tool calls'
)
order_not_found_total = Counter(
    'customer_service_order_not_found_total',
    'Order queries that returned not found'
)

# 工单创建工具
tickets_created_total = Counter(
    'customer_service_tickets_created_total',
    'Total tickets created via agent'
)
