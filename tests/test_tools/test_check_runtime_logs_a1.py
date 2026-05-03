from __future__ import annotations

from datetime import datetime, timedelta

import tools.logs.check_runtime_logs as check_runtime_logs


def _line(minutes_delta: int, message: str) -> str:
    """
    功能：构造带日志时间前缀的测试行。
    入参：minutes_delta（int）：相对当前时间的分钟偏移；message（str）：日志正文。
    出参：str，符合 LOG_TIME_FORMAT 的日志行。
    异常：时间格式化异常向上抛出。
    """
    timestamp = datetime.now() + timedelta(minutes=minutes_delta)
    return f"{timestamp.strftime(check_runtime_logs.LOG_TIME_FORMAT)} - {message}"


def _write_all_required_logs(log_dir) -> None:
    """
    功能：写入满足 RULES 的最小运行日志集合。
    入参：log_dir（Path）：日志目录。
    出参：None。
    异常：文件写入失败时向上抛出。
    """
    log_dir.mkdir()
    (log_dir / "main_loop.log").write_text(
        "\n".join(
            [
                _line(0, "正在解析玩家输入"),
                _line(0, "物理结算完成"),
                _line(0, "数据库更新已提交"),
                _line(0, "正在生成叙事响应"),
            ]
        ),
        encoding="utf-8",
    )
    (log_dir / "event_bus.log").write_text(_line(0, "事件总线已就绪"), encoding="utf-8")
    (log_dir / "outer_loop.log").write_text(_line(0, "外环事件已投递"), encoding="utf-8")


def test_read_log_lines_filters_by_since_minutes(tmp_path) -> None:
    """
    功能：验证日志读取会按最近 N 分钟过滤，无法解析时间的行不会进入窗口结果。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示时间窗口过滤回归。
    """
    log_path = tmp_path / "main_loop.log"
    recent_line = _line(0, "新日志")
    log_path.write_text(
        "\n".join(
            [
                _line(-30, "旧日志"),
                recent_line,
                "bad timestamp line",
            ]
        ),
        encoding="utf-8",
    )

    assert check_runtime_logs._read_log_lines(log_path, since_minutes=0) == log_path.read_text(  # noqa: SLF001
        encoding="utf-8"
    ).splitlines()
    assert check_runtime_logs._read_log_lines(log_path, since_minutes=5) == [recent_line]  # noqa: SLF001
    assert check_runtime_logs._read_log_lines(tmp_path / "missing.log", since_minutes=0) == []  # noqa: SLF001


def test_check_rule_reports_missing_file_ok_and_missing_evidence(tmp_path) -> None:
    """
    功能：验证单条规则对缺文件、证据齐全、缺关键证据分别返回明确消息。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示规则检查消息回归。
    """
    rule = check_runtime_logs.LogCheckRule("main_loop.log", ("物理结算完成", "数据库更新已提交"))

    missing_ok, missing_messages = check_runtime_logs._check_rule(tmp_path, rule, 0)  # noqa: SLF001
    assert missing_ok is False
    assert "无可用日志" in missing_messages[0]

    (tmp_path / "main_loop.log").write_text(_line(0, "物理结算完成"), encoding="utf-8")
    partial_ok, partial_messages = check_runtime_logs._check_rule(tmp_path, rule, 0)  # noqa: SLF001
    assert partial_ok is False
    assert partial_messages == ["main_loop.log: 缺少关键证据 -> 数据库更新已提交"]

    (tmp_path / "main_loop.log").write_text(
        "\n".join([_line(0, "物理结算完成"), _line(0, "数据库更新已提交")]),
        encoding="utf-8",
    )
    ok, messages = check_runtime_logs._check_rule(tmp_path, rule, 0)  # noqa: SLF001
    assert ok is True
    assert messages == ["main_loop.log: OK"]


def test_main_returns_zero_and_prints_ok_for_complete_logs(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """
    功能：验证 main 在证据齐全时输出 OK 标记并返回 0。
    入参：tmp_path；monkeypatch；capsys。
    出参：None。
    异常：断言失败表示 CLI 成功验收输出回归。
    """
    log_dir = tmp_path / "logs"
    _write_all_required_logs(log_dir)
    monkeypatch.setattr(
        "sys.argv",
        ["check_runtime_logs.py", "--log-dir", str(log_dir), "--since-minutes", "5"],
    )

    code = check_runtime_logs.main()
    output = capsys.readouterr().out

    assert code == 0
    assert "RUNTIME_LOG_CHECK_START" in output
    assert "since_minutes=5" in output
    assert "RUNTIME_LOG_CHECK_OK" in output


def test_main_returns_one_and_prints_failed_for_missing_evidence(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """
    功能：验证 main 在部分日志缺证据时输出 FAILED 标记并返回 1。
    入参：tmp_path；monkeypatch；capsys。
    出参：None。
    异常：断言失败表示 CLI 失败验收输出回归。
    """
    log_dir = tmp_path / "logs"
    _write_all_required_logs(log_dir)
    (log_dir / "outer_loop.log").write_text(_line(0, "无外部证据"), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["check_runtime_logs.py", "--log-dir", str(log_dir)])

    code = check_runtime_logs.main()
    output = capsys.readouterr().out

    assert code == 1
    assert "outer_loop.log: 缺少关键证据 -> 外环" in output
    assert "RUNTIME_LOG_CHECK_FAILED" in output
