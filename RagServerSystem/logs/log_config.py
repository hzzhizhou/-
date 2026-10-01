"""
日志配置模块：全局统一日志配置（按模块拆分文件）
功能特性：
1. 分级输出（DEBUG/INFO/WARNING/ERROR/CRITICAL）
2. 同时输出到控制台和文件
3. 按日期滚动切割日志文件（保留7天）
4. 兼容Windows/Linux路径
5. 全局单例Logger，避免重复输出
6. 自动降级配置（无config模块时可用）
7. 支持异常栈完整打印
8. 按模块拆分日志文件（每个模块一个独立日志文件）
"""
import logging
import os
import sys
import re
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
from typing import Optional, Dict
from concurrent_log_handler import ConcurrentRotatingFileHandler
# ===================== 基础配置常量 =====================
# 日志级别映射（兼容大小写）
LOG_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL
}

# 默认日志配置（降级使用）
DEFAULT_ROOT_LOG_DIR = Path("./logs")  # 根日志目录
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_ROOT_LOG_NAME = "Log_server"

# 日志格式（包含更多调试信息）
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 模块日志器缓存（避免重复创建）
MODULE_LOGGERS: Dict[str, logging.Logger] = {}

# ===================== 核心日志配置函数 =====================
def setup_module_logger(
    module_name: str,
    root_log_dir: Optional[Path] = None,
    log_level: str = DEFAULT_LOG_LEVEL,
    console_output: bool = True,
    file_output: bool = True,
    backup_days: int = 7
) -> logging.Logger:
    """
    初始化/获取模块专属日志器（单例模式，按模块拆分文件）
    :param module_name: 模块名称（如data_layer/retrieval_layer/generation_layer）
    :param root_log_dir: 日志根目录
    :param log_level: 日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）
    :param console_output: 是否输出到控制台
    :param file_output: 是否输出到文件
    :param backup_days: 日志保留天数
    :return: 配置好的模块专属Logger对象
    """
    # 1. 拼接完整日志器名称（如 rag_server.data_layer）
    full_logger_name = f"{DEFAULT_ROOT_LOG_NAME}.{module_name}"
    
    # 检查缓存，避免重复创建
    if full_logger_name in MODULE_LOGGERS:
        return MODULE_LOGGERS[full_logger_name]
    
    # 2. 创建/获取日志器
    logger = logging.getLogger(full_logger_name)
    logger.setLevel(LOG_LEVEL_MAP.get(log_level.upper(), logging.INFO))
    logger.propagate = False  # 禁用向上传播，避免根日志器重复输出

    # 3. 定义日志格式器
    formatter = logging.Formatter(
        fmt=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT
    )

    # 4. 控制台处理器（所有模块共享控制台输出，兼容Windows/Linux编码）
    if console_output and not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        console_handler = logging.StreamHandler(stream=sys.stdout)
        console_handler.setLevel(LOG_LEVEL_MAP.get(log_level.upper(), logging.INFO))
        console_handler.setFormatter(formatter)
        # 修复Windows控制台中文乱码问题
        if sys.platform == "win32":
            console_handler.stream.reconfigure(encoding="utf-8")
        logger.addHandler(console_handler)

    # 5. 文件处理器（按模块拆分文件，按日期滚动）
    if file_output and not any(isinstance(h, TimedRotatingFileHandler) for h in logger.handlers):
        # 日志根目录（优先配置，否则默认）
        target_root_dir = root_log_dir or DEFAULT_ROOT_LOG_DIR
        # 模块专属日志目录（logs/rag_server/data_layer/）
        module_log_dir = target_root_dir / DEFAULT_ROOT_LOG_NAME / module_name
        # 确保目录存在（兼容跨平台权限）
        module_log_dir.mkdir(parents=True, exist_ok=True)
        
        # 模块专属日志文件路径（如 logs/rag_server/data_layer/data_layer.log）
        log_file_path = module_log_dir / f"{module_name}.log"
        
        # 按天滚动的文件处理器（每天午夜切割一次，按日期轮转）
        # 修复BUG：此前误用 ConcurrentRotatingFileHandler（仅按 maxBytes=100MB 大小轮转，
        #          suffix/extMatch 对它是无效的），导致日志全部堆在同一文件。
        # 说明：ConcurrentRotatingFileHandler 不支持按天切割；本项目为单进程 uvicorn，
        #       改用 TimedRotatingFileHandler 即可安全按天切分 + 按 backup_days 清理。
        file_handler = TimedRotatingFileHandler(
            filename=str(log_file_path),
            when="midnight",              # 每天午夜切割
            interval=1,
            backupCount=backup_days,      # 保留天数
            encoding="utf-8",             # 强制UTF-8编码
            utc=False                     # 使用本地时间
        )
        # 时间轮转：切割后日志文件名自动加日期后缀（如 data_layer.log.2026-09-07）
        file_handler.suffix = "%Y-%m-%d"
        # 修复日志切割后缀匹配问题（后缀含前导点，如 ".2026-09-07"）
        file_handler.extMatch = re.compile(r"^\.\d{4}-\d{2}-\d{2}$")
        file_handler.setLevel(LOG_LEVEL_MAP.get(log_level.upper(), logging.INFO))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # 6. 加入缓存
    MODULE_LOGGERS[full_logger_name] = logger
    return logger

# ===================== 全局日志器快捷函数 =====================
def get_logger(module_name: str) -> logging.Logger:
    """
    获取模块专属日志器（最简调用方式）
    :param module_name: 模块名称（如data_layer/retrieval_layer/generation_layer/chat_history）
    :return: 配置好的模块专属Logger对象
    """
    try:
        # 优先从配置文件读取配置
        import config.settings as config
        return setup_module_logger(
            module_name=module_name,
            root_log_dir=getattr(config, "LOG_DIR", DEFAULT_ROOT_LOG_DIR),
            log_level=getattr(config, "LOG_LEVEL", DEFAULT_LOG_LEVEL),
            backup_days=getattr(config, "LOG_BACKUP_DAYS", 7)
        )
    except (ImportError, AttributeError):
        # 降级使用默认配置（无config模块/配置项缺失时）
        return setup_module_logger(
            module_name=module_name,
            root_log_dir=DEFAULT_ROOT_LOG_DIR,
            log_level=DEFAULT_LOG_LEVEL,
            backup_days=7
        )

# ===================== 全局默认日志器（兼容原有调用） =====================
# 全局默认日志器（无模块名称时使用）
log = get_logger("default")
data_layer_log = get_logger("data_layer")
retrieval_layer_log = get_logger("retrieval_layer")
generation_layer_log = get_logger("generation_layer")
chat_history_log = get_logger("chat_history_layer")
evaluation_layer_log  = get_logger("evalution_layer")
# ===================== 工具函数（可选） =====================
def log_exception(module_name: str, exc: Exception, level: str = "ERROR") -> None:
    """
    打印模块专属的异常栈信息
    :param module_name: 模块名称
    :param exc: 异常对象
    :param level: 日志级别
    """
    logger = get_logger(module_name)
    log_func = getattr(logger, level.lower(), logger.error)
    log_func(f"异常信息：{str(exc)}", exc_info=True)