from __future__ import annotations

import json

import pytest

from tools import doc_importer, mod_manager
from tools.logs import check_runtime_logs, replay_outer_outbox


def test_doc_importer_parse_tags_and_mineru_detection(tmp_path, monkeypatch) -> None:
    """
    功能：验证标签解析与 MinerU 目录识别分支。
    入参：tmp_path/monkeypatch（pytest fixtures）：临时目录与函数替换工具。
    出参：None。
    异常：断言失败表示导入前置校验逻辑退化。
    """
    assert doc_importer._parse_tags("") == []
    assert doc_importer._parse_tags(" core, lore , ,npc ") == ["core", "lore", "npc"]

    rules_path = tmp_path / "rag_import_rules.json"
    rules_path.write_text(json.dumps({"groups": []}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(doc_importer, "RULES_PATH", str(rules_path))
    monkeypatch.setattr(doc_importer, "BASE_DIR", str(tmp_path))
    importer = doc_importer.DocImporter()

    mineru_dir = tmp_path / "mineru_pack"
    mineru_dir.mkdir()
    (mineru_dir / "doc.md").write_text("# title", encoding="utf-8")
    (mineru_dir / "meta.json").write_text("{}", encoding="utf-8")
    assert importer.is_mineru_dir(str(mineru_dir)) is True
    assert importer.is_mineru_dir(str(tmp_path / "missing_dir")) is False


def test_doc_importer_add_to_group_creates_and_deduplicates(tmp_path, monkeypatch) -> None:
    """
    功能：验证 add_to_group 会创建分组并对 file_paths 去重。
    入参：tmp_path/monkeypatch（pytest fixtures）：临时目录与函数替换工具。
    出参：None。
    异常：断言失败表示规则更新基础路径退化。
    """
    rules_path = tmp_path / "rag_import_rules.json"
    rules_path.write_text(json.dumps({"groups": []}, ensure_ascii=False), encoding="utf-8")
    target_file = tmp_path / "docs" / "a.md"
    target_file.parent.mkdir()
    target_file.write_text("hello", encoding="utf-8")

    monkeypatch.setattr(doc_importer, "RULES_PATH", str(rules_path))
    monkeypatch.setattr(doc_importer, "BASE_DIR", str(tmp_path))
    importer = doc_importer.DocImporter()
    importer.add_to_group(str(target_file), "core", tags=["lore"], description="核心")
    importer.add_to_group(str(target_file), "core", tags=["lore"], description="核心")
    loaded = json.loads(rules_path.read_text(encoding="utf-8"))
    assert len(loaded["groups"]) == 1
    assert loaded["groups"][0]["group_name"] == "core"
    assert len(loaded["groups"][0]["file_paths"]) == 1


def test_mod_manager_scan_and_register_new_mod(tmp_path, monkeypatch) -> None:
    """
    功能：验证 mod_manager 扫描到新 MOD 时会写入 active_mods。
    入参：tmp_path/monkeypatch（pytest fixtures）：临时目录与函数替换工具。
    出参：None。
    异常：断言失败表示 MOD 注册主路径退化。
    """
    mods_dir = tmp_path / "mods"
    config_dir = tmp_path / "config"
    mods_dir.mkdir()
    config_dir.mkdir()
    registry_path = config_dir / "mod_registry.yml"
    mod_dir = mods_dir / "demo_mod"
    mod_dir.mkdir()
    (mod_dir / "mod_info.json").write_text(
        json.dumps({"mod_id": "demo_mod", "name": "Demo", "load_priority": 60}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod_manager, "MODS_DIR", str(mods_dir))
    monkeypatch.setattr(mod_manager, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(mod_manager, "REGISTRY_PATH", str(registry_path))
    manager = mod_manager.ModManager()
    manager.scan_and_register()
    text = registry_path.read_text(encoding="utf-8")
    assert "demo_mod" in text
    assert "active_mods" in text


def test_check_runtime_logs_helpers(tmp_path) -> None:
    """
    功能：验证日志检查器的时间解析与缺证据分支。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示日志验收工具分支退化。
    """
    assert check_runtime_logs._extract_time("bad line") is None
    valid_line = "2026-05-02 12:00:00,000 INFO start"
    assert check_runtime_logs._extract_time(valid_line) is not None
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    main_log = log_dir / "main_loop.log"
    main_log.write_text("2026-05-02 12:00:00,000 INFO 只含部分关键词\n", encoding="utf-8")
    ok, messages = check_runtime_logs._check_rule(
        log_dir,
        check_runtime_logs.LogCheckRule("main_loop.log", ("物理结算完成",)),
        since_minutes=0,
    )
    assert ok is False
    assert any("缺少关键证据" in m for m in messages)


@pytest.mark.asyncio
async def test_replay_outer_outbox_coerce_and_dispatch_unknown_event() -> None:
    """
    功能：验证 outbox 脚本字段转换与未知事件分支会抛出 ValueError。
    入参：无。
    出参：None。
    异常：断言失败表示 outbox 基础校验退化。
    """
    assert replay_outer_outbox._coerce_int("7", "turn_id") == 7
    assert replay_outer_outbox._coerce_str("abc", "name") == "abc"
    with pytest.raises(ValueError):
        replay_outer_outbox._coerce_mapping([], "state_changed")

    class _Bridge:
        async def emit_state_changed(self, event):  # noqa: ANN001
            return event

        async def emit_turn_ended(self, event):  # noqa: ANN001
            return event

        async def emit_world_evolution(self, event):  # noqa: ANN001
            return event

    with pytest.raises(ValueError, match="不支持的 outbox 事件类型"):
        await replay_outer_outbox._dispatch(_Bridge(), "unknown_event", {})
