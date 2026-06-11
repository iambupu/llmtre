"""
功能：检查主循环、事件总线与外环日志是否包含近期有效记录。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

LOG_TIME_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S,%f"


@dataclass(frozen=True)
class LogCheckRule:
    """
    功能：描述单个运行日志文件的验收规则。
    入参：无；dataclass 字段声明文件名、近期窗口必须命中的日志片段，
        以及允许跨越时间窗口的生命周期日志片段。
    出参：LogCheckRule 实例，供日志检查器逐文件验证近期运行证据。
    异常：不抛异常；缺失日志或未命中模式由检查流程返回失败结果。
    """

    file_name: str
    must_contain: tuple[str, ...]
    lifetime_contain: tuple[str, ...] = ()


RULES: Final[tuple[LogCheckRule, ...]] = (
    LogCheckRule(
        file_name="main_loop.log",
        must_contain=("正在解析玩家输入", "物理结算完成", "数据库更新已提交", "正在生成叙事响应"),
    ),
    LogCheckRule(
        file_name="event_bus.log",
        must_contain=("事件触发", "写计划开始", "写计划事务已提交"),
        lifetime_contain=("事件总线已就绪",),
    ),
    LogCheckRule(
        file_name="outer_loop.log",
        must_contain=("外环",),
    ),
)


def _parse_args() -> argparse.Namespace:
    """
    功能：执行 `_parse_args` 相关业务逻辑。
    入参：无。
    出参：argparse.Namespace。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    parser = argparse.ArgumentParser(description="检查主循环链路日志是否满足最小验收证据。")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "logs",
        help="日志目录路径，默认使用项目根目录下 logs/",
    )
    parser.add_argument(
        "--since-minutes",
        type=int,
        default=0,
        help="仅检查最近 N 分钟内的日志，0 表示不限制时间窗口。",
    )
    return parser.parse_args()


def _extract_time(line: str) -> datetime | None:
    """
    功能：执行 `_extract_time` 相关业务逻辑。
    入参：line。
    出参：datetime | None。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    text = line.strip()
    if len(text) < 23:
        return None
    maybe_time = text[:23]
    try:
        return datetime.strptime(maybe_time, LOG_TIME_FORMAT)
    except ValueError:
        return None


def _read_log_lines(log_path: Path, since_minutes: int) -> list[str]:
    """
    功能：执行 `_read_log_lines` 相关业务逻辑。
    入参：log_path；since_minutes。
    出参：list[str]。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    if not log_path.exists():
        return []

    lines = log_path.read_text(encoding="utf-8").splitlines()
    if since_minutes <= 0:
        return lines

    threshold = datetime.now() - timedelta(minutes=since_minutes)
    filtered: list[str] = []
    for line in lines:
        timestamp = _extract_time(line)
        if timestamp is not None and timestamp >= threshold:
            filtered.append(line)
    return filtered


def _check_rule(log_dir: Path, rule: LogCheckRule, since_minutes: int) -> tuple[bool, list[str]]:
    """
    功能：检查单个日志文件是否同时满足近期运行证据与生命周期证据。
    入参：log_dir（Path）：日志目录；rule（LogCheckRule）：检查规则；
        since_minutes（int）：近期窗口，0 表示不限制。
    出参：tuple[bool, list[str]]，bool 表示是否通过，list 为可展示诊断。
    异常：不主动捕获文件读取异常；日志文件损坏或权限错误会向上抛出，避免误报通过。
    """
    path = log_dir / rule.file_name
    lines = _read_log_lines(path, since_minutes)
    all_lines = lines if since_minutes <= 0 else _read_log_lines(path, 0)
    if not lines:
        return False, [f"{rule.file_name}: 无可用日志（文件缺失或时间窗口内无记录）"]

    errors: list[str] = []
    for keyword in rule.must_contain:
        if not any(keyword in line for line in lines):
            errors.append(f"{rule.file_name}: 缺少关键证据 -> {keyword}")
    for keyword in rule.lifetime_contain:
        # 进程启动类日志不会在每个 since_minutes 窗口重复出现；
        # 只要当前日志文件中存在即可证明组件完成过初始化。
        if not any(keyword in line for line in all_lines):
            errors.append(f"{rule.file_name}: 缺少生命周期证据 -> {keyword}")

    if errors:
        return False, errors
    return True, [f"{rule.file_name}: OK"]


def main() -> int:
    """
    功能：执行 `main` 相关业务逻辑。
    入参：无。
    出参：int。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    args = _parse_args()
    log_dir: Path = args.log_dir
    since_minutes: int = args.since_minutes

    all_ok = True
    messages: list[str] = []
    for rule in RULES:
        ok, result = _check_rule(log_dir, rule, since_minutes)
        all_ok = all_ok and ok
        messages.extend(result)

    print("RUNTIME_LOG_CHECK_START")
    print(f"log_dir={log_dir}")
    print(f"since_minutes={since_minutes}")
    for message in messages:
        print(message)

    if all_ok:
        print("RUNTIME_LOG_CHECK_OK")
        return 0

    print("RUNTIME_LOG_CHECK_FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
