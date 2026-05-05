from __future__ import annotations

import os
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest


def test_wheel_contains_runtime_packages_and_assets() -> None:
    """
    功能：构建 wheel 并验证运行时包目录与关键资源文件进入安装产物。
    入参：无，使用仓库内临时 wheel 输出目录。
    出参：None。
    异常：构建失败或断言失败表示 pip install . 后可能缺少模块或资源。
    """
    wheel_dir = Path(".pytest_wheelhouse")
    if wheel_dir.exists():
        for existing in wheel_dir.glob("*.whl"):
            existing.unlink()
    else:
        wheel_dir.mkdir()
    pip_tmp_dir = wheel_dir / "tmp"
    pip_tmp_dir.mkdir(exist_ok=True)
    pip_env = dict(os.environ)
    # 环境边界：显式指定 pip 临时目录，避免依赖系统 Temp 权限导致构建失败。
    pip_env["TMP"] = str(pip_tmp_dir.resolve())
    pip_env["TEMP"] = str(pip_tmp_dir.resolve())
    build_result = subprocess.run(
        [
            "python",
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            ".",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=pip_env,
    )
    if build_result.returncode != 0:
        error_text = f"{build_result.stdout}\n{build_result.stderr}"
        if "Permission denied" in error_text or "拒绝访问" in error_text:
            pytest.skip("当前环境无权限创建 pip 构建临时目录，跳过 wheel 产物校验")
    wheel_files = list(wheel_dir.glob("tre-*.whl"))
    # 以产物为准：Windows 上 pip 可能在清理临时目录阶段报权限错误并返回非零，
    # 但 wheel 已成功构建到目标目录。
    assert wheel_files, (
        "未生成 tre wheel 产物；"
        f"returncode={build_result.returncode} "
        f"stdout={build_result.stdout!r} stderr={build_result.stderr!r}"
    )

    with zipfile.ZipFile(wheel_files[0]) as wheel_zip:
        wheel_entries = set(wheel_zip.namelist())

    expected_entries = {
        "agents/gm_agent.py",
        "game_workflows/main_event_loop.py",
        "web_api/service.py",
        "state/models/entity.py",
        "config/agent_model_config.yml",
        "config/main_loop_rules.json",
        "templates/playground.html",
        "static/playground.js",
    }
    missing = [entry for entry in expected_entries if entry not in wheel_entries]
    assert not missing, f"wheel 缺少关键运行时文件: {missing}"


def test_setuptools_package_include_references_current_roots() -> None:
    """
    功能：验证 pyproject 的包发现白名单与当前代码根目录保持一致。
    入参：无，读取仓库根目录 pyproject.toml。
    出参：None。
    异常：断言失败表示包发现范围配置漂移。
    """
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    include = pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
    assert "workflows*" not in include
    assert "game_workflows*" in include
