import sys
from pathlib import Path
from loguru import logger

#    从仓根起就进 `<仓根>/logs/` —— **本机两个目录都真的存在过**（实测 `ls -d logs app/logs`）。
#    ⇒ 改成基于 `__file__` 的**绝对路径**，日志**永远进 `app/logs/`**，与从哪起无关。
#    ⇒ 因此 `README.md` 那句「必须在 `app/` 目录下起 uvicorn（有一处路径按相对位置解析）」
#      **已随之删除** —— 那条约束的根因没了。
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def setup_logger():
    """配置 Loguru，输出 JSON 到控制台，同时写入文件"""
    
    # 移除默认的 handler
    logger.remove()
    # 给 extra 提供默认值，避免未 bind(request_id=...) 的日志（如启动日志）格式化时报 KeyError
    logger.configure(extra={"request_id": "no-id"})

    # 1. 控制台输出：彩色格式，方便开发调试
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{extra[request_id]}</cyan> | "
               "<level>{message}</level>",
        level="DEBUG",
        colorize=True
    )
    
    # 2. 文件输出：**管道分隔的纯文本**（不是 JSON），便于 grep 与人工排查
    logger.add(
        str(_LOG_DIR / "api_{time:YYYY-MM-DD}.log"),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {extra[request_id]} | {message}",
        level="INFO",
        rotation="00:00",      # 每天凌晨0点创建新文件
        retention="30 days",   # 保留30天
        encoding="utf-8"
    )
    
    # 3. 错误日志单独输出
    logger.add(
        str(_LOG_DIR / "error_{time:YYYY-MM-DD}.log"),
        format="{time:YYYY-MM-DD HH:mm:ss} | {extra[request_id]} | {message}",
        level="ERROR",
        rotation="00:00",
        retention="90 days",   # 错误日志保留更久
        encoding="utf-8"
    )
    
    return logger
