"""
功能：覆盖 event bus 的回归测试。
"""

from __future__ import annotations

import json
import logging
import textwrap
from pathlib import Path

import pytest

from core.event_bus import EventBus


def _write_mod_hook(
    tmp_path: Path,
    mod_id: str,
    hook_name: str,
    hook_source: str,
    write_access: list[str],
) -> tuple[Path, Path]:
    """
    功能：写入测试用 MOD 脚本与注册表，构造最小 EventBus 输入。
    入参：tmp_path（Path）：pytest 临时目录；mod_id/hook_name（str）：MOD 与函数名；
        hook_source（str）：hooks.py 源码；write_access（list[str]）：声明写权限。
    出参：tuple[Path, Path]，依次为 registry_path 与 mods_root。
    异常：文件系统写入失败时向上抛出，测试直接失败。
    """
    mods_root = tmp_path / "mods"
    scripts_dir = mods_root / mod_id / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "hooks.py").write_text(textwrap.dedent(hook_source), encoding="utf-8")
    registry_path = tmp_path / "mod_registry.yml"
    registry = {
        "active_mods": [
            {
                "mod_id": mod_id,
                "enabled": True,
                "priority": 50,
                "conflict_strategy": "smart_merge",
                "hooks_manifest": {
                    hook_name: {
                        "trigger": "on_test_event",
                        "write_access": write_access,
                    }
                },
            }
        ]
    }
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False),
        encoding="utf-8",
    )
    return registry_path, mods_root


def test_event_bus_rejects_unauthorized_nested_hook_writes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    功能：验证 MOD 钩子只能合并 write_access 声明路径，未授权嵌套写入会被拒绝。
    入参：tmp_path（Path）：临时目录；caplog：pytest 日志捕获器。
    出参：None。
    异常：断言失败表示 MOD 写权限边界被绕过。
    """
    registry_path, mods_root = _write_mod_hook(
        tmp_path=tmp_path,
        mod_id="limited_mod",
        hook_name="limited_hook",
        hook_source="""
        def limited_hook(state):
            state["player"]["hp"] += 5
            state["player"]["mp"] += 99
            state["world"]["danger"] = "changed"
            return state
        """,
        write_access=["player.hp"],
    )
    event_bus = EventBus(str(registry_path), str(mods_root))
    caplog.set_level(logging.WARNING, logger="EventBus")

    result = event_bus.emit(
        "on_test_event",
        {"player": {"hp": 10, "mp": 1}, "world": {"danger": "stable"}},
    )

    assert result["player"]["hp"] == 15
    assert result["player"]["mp"] == 1
    assert result["world"]["danger"] == "stable"
    assert "未授权写入" in caplog.text
    assert "player.mp" in caplog.text
    assert "world.danger" in caplog.text


def test_event_bus_applies_authorized_stop_propagation_changes(tmp_path: Path) -> None:
    """
    功能：验证 STOP_PROPAGATION 钩子的授权原地修改仍会合并，并终止后续钩子。
    入参：tmp_path（Path）：临时目录。
    出参：None。
    异常：断言失败表示旧 MOD 兼容性或传播终止语义回归。
    """
    registry_path, mods_root = _write_mod_hook(
        tmp_path=tmp_path,
        mod_id="stop_mod",
        hook_name="stop_hook",
        hook_source="""
        from core.event_bus import STOP_PROPAGATION

        def stop_hook(state):
            state["player"]["hp"] += 20
            return STOP_PROPAGATION
        """,
        write_access=["player.hp"],
    )
    event_bus = EventBus(str(registry_path), str(mods_root))

    result = event_bus.emit("on_test_event", {"player": {"hp": 10, "mp": 1}})

    assert result["player"]["hp"] == 30
    assert result["player"]["mp"] == 1
