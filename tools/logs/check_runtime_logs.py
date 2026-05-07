from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

LOG_TIME_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S,%f"


@dataclass(frozen=True)
class LogCheckRule:
    file_name: str
    must_contain: tuple[str, ...]


RULES: Final[tuple[LogCheckRule, ...]] = (
    LogCheckRule(
        file_name="main_loop.log",
        must_contain=("正在解析玩家输入", "物理结算完成", "数据库更新已提交", "正在生成叙事响应"),
    ),
    LogCheckRule(
        file_name="event_bus.log",
        must_contain=("事件总线已就绪", "事件触发", "写计划开始", "写计划事务已提交"),
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
    功能：执行 `_check_rule` 相关业务逻辑。
    入参：log_dir；rule；since_minutes。
    出参：tuple[bool, list[str]]。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    path = log_dir / rule.file_name
    lines = _read_log_lines(path, since_minutes)
    if not lines:
        return False, [f"{rule.file_name}: 无可用日志（文件缺失或时间窗口内无记录）"]

    errors: list[str] = []
    for keyword in rule.must_contain:
        if not any(keyword in line for line in lines):
            errors.append(f"{rule.file_name}: 缺少关键证据 -> {keyword}")

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

