from __future__ import annotations

import logging
from pathlib import Path

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_LOGGERS: dict[str, str] = {
    "Workflow.MainLoop": "main_loop.log",
    "EventBus": "event_bus.log",
    "Workflow.AsyncWatchers": "outer_loop.log",
}
_INITIALIZED = False


def ensure_runtime_logging() -> None:
    """
    功能：为主循环关键模块绑定统一文件日志。
    入参：无。
    出参：None。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_LOG_FORMAT)
    for logger_name, file_name in _LOGGERS.items():
        _attach_file_handler(logger_name, log_dir / file_name, formatter)

    _INITIALIZED = True


def _attach_file_handler(
    logger_name: str, log_path: Path, formatter: logging.Formatter
) -> None:
    """
    功能：执行 `_attach_file_handler` 相关业务逻辑。
    入参：logger_name；log_path；formatter。
    出参：None。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)

    target = str(log_path.resolve())
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == target:
            return

    file_handler = logging.FileHandler(target, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
