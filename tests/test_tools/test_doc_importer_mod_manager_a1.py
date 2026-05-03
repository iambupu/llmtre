from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tools import doc_importer, mod_manager


def _prepare_importer_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    功能：为 doc_importer 测试隔离规则文件与项目根路径。
    入参：tmp_path（Path）：pytest 临时目录；monkeypatch（pytest.MonkeyPatch）：属性替换工具。
    出参：Path，临时规则文件路径。
    异常：文件系统写入失败时向上抛出。
    """
    rules_path = tmp_path / "config" / "rag_import_rules.json"
    rules_path.parent.mkdir()
    rules_path.write_text(json.dumps({"groups": []}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(doc_importer, "RULES_PATH", str(rules_path))
    monkeypatch.setattr(doc_importer, "BASE_DIR", str(tmp_path))
    return rules_path


def test_doc_importer_main_defaults_to_sync_when_no_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 doc_importer 无参数时只执行同步，不要求 path/group。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示默认同步 CLI 契约回归。
    """
    _prepare_importer_paths(tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(doc_importer.DocImporter, "sync", lambda self: calls.append("sync"))
    monkeypatch.setattr("sys.argv", ["doc_importer.py"])

    doc_importer.main()

    assert calls == ["sync"]


def test_doc_importer_main_requires_path_and_group_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证只提供 path 或只提供 group 时通过 argparse 失败退出。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示参数约束回归。
    """
    _prepare_importer_paths(tmp_path, monkeypatch)

    monkeypatch.setattr("sys.argv", ["doc_importer.py", str(tmp_path / "a.md")])
    with pytest.raises(SystemExit):
        doc_importer.main()

    monkeypatch.setattr("sys.argv", ["doc_importer.py", "--group", "core"])
    with pytest.raises(SystemExit):
        doc_importer.main()


def test_doc_importer_main_scans_directory_and_syncs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证普通目录递归导入受支持扩展名，并在 --sync 时触发索引同步。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示目录导入或同步触发回归。
    """
    rules_path = _prepare_importer_paths(tmp_path, monkeypatch)
    docs_dir = tmp_path / "docs"
    nested = docs_dir / "nested"
    nested.mkdir(parents=True)
    (docs_dir / "a.md").write_text("# A", encoding="utf-8")
    (nested / "b.txt").write_text("B", encoding="utf-8")
    (nested / "ignore.png").write_text("png", encoding="utf-8")
    sync_calls: list[str] = []
    monkeypatch.setattr(doc_importer.DocImporter, "sync", lambda self: sync_calls.append("sync"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "doc_importer.py",
            str(docs_dir),
            "--group",
            "core",
            "--tags",
            "lore, rules",
            "--sync",
        ],
    )

    doc_importer.main()

    loaded = json.loads(rules_path.read_text(encoding="utf-8"))
    assert loaded["groups"][0]["tags"] == ["lore", "rules"]
    normalized_paths = sorted(path.replace("\\", "/") for path in loaded["groups"][0]["file_paths"])
    assert normalized_paths == [
        "docs/a.md",
        "docs/nested/b.txt",
    ]
    assert sync_calls == ["sync"]


def test_doc_importer_main_forces_mineru_directory_as_single_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 --mineru 会把目录作为整体导入，而不是递归拆成多个文件。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示 MinerU 强制导入契约回归。
    """
    rules_path = _prepare_importer_paths(tmp_path, monkeypatch)
    mineru_dir = tmp_path / "mineru_pack"
    mineru_dir.mkdir()
    (mineru_dir / "a.md").write_text("# A", encoding="utf-8")
    (mineru_dir / "meta.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["doc_importer.py", str(mineru_dir), "--group", "core", "--mineru"],
    )

    doc_importer.main()

    loaded = json.loads(rules_path.read_text(encoding="utf-8"))
    assert loaded["groups"][0]["file_paths"] == ["mineru_pack"]


def test_doc_importer_sync_logs_exception(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """
    功能：验证 RAG 同步异常会记录错误日志并降级返回。
    入参：monkeypatch；caplog。
    出参：None。
    异常：断言失败表示同步异常降级或日志证据回归。
    """

    class _BrokenRAGManager:
        """
        功能：模拟构建器初始化成功但索引更新失败。
        入参：无。
        出参：测试桩对象。
        异常：update_index 抛 RuntimeError。
        """

        def update_index(self) -> None:
            """
            功能：模拟索引更新失败。
            入参：无。
            出参：None。
            异常：始终抛 RuntimeError。
            """
            raise RuntimeError("index failed")

    importer = SimpleNamespace()
    monkeypatch.setattr(doc_importer, "RAGManager", _BrokenRAGManager)

    with caplog.at_level("ERROR", logger="DocImporter"):
        doc_importer.DocImporter.sync(importer)

    assert "同步过程中出错: index failed" in caplog.text


def _prepare_mod_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """
    功能：为 mod_manager 测试隔离 mods/config/registry 路径。
    入参：tmp_path；monkeypatch。
    出参：tuple[Path, Path]，mods 目录与 registry 文件路径。
    异常：目录创建失败时向上抛出。
    """
    mods_dir = tmp_path / "mods"
    config_dir = tmp_path / "config"
    mods_dir.mkdir()
    config_dir.mkdir()
    registry_path = config_dir / "mod_registry.yml"
    monkeypatch.setattr(mod_manager, "MODS_DIR", str(mods_dir))
    monkeypatch.setattr(mod_manager, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(mod_manager, "REGISTRY_PATH", str(registry_path))
    return mods_dir, registry_path


def test_mod_manager_updates_existing_hooks_and_skips_bad_mods(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    """
    功能：验证扫描会更新已存在 MOD 的 hooks，并跳过缺清单、缺 mod_id、坏 JSON 目录。
    入参：tmp_path；monkeypatch；caplog。
    出参：None。
    异常：断言失败表示 MOD 兼容扫描或日志证据回归。
    """
    mods_dir, registry_path = _prepare_mod_paths(tmp_path, monkeypatch)
    registry_path.write_text(
        yaml.safe_dump(
            {
                "global_settings": {"default_conflict_strategy": "smart_merge"},
                "active_mods": [
                    {
                        "mod_id": "existing",
                        "name": "Existing",
                        "enabled": True,
                        "priority": 10,
                        "conflict_strategy": "smart_merge",
                        "allowed_fields": [],
                        "hooks_manifest": {"old": ["hook"]},
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    existing_dir = mods_dir / "existing"
    existing_dir.mkdir()
    (existing_dir / "mod_info.json").write_text(
        json.dumps(
            {"mod_id": "existing", "hooks_manifest": {"new": ["hook"]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    missing_info_dir = mods_dir / "missing_info"
    missing_info_dir.mkdir()
    missing_id_dir = mods_dir / "missing_id"
    missing_id_dir.mkdir()
    (missing_id_dir / "mod_info.json").write_text(json.dumps({"name": "bad"}), encoding="utf-8")
    bad_json_dir = mods_dir / "bad_json"
    bad_json_dir.mkdir()
    (bad_json_dir / "mod_info.json").write_text("{bad", encoding="utf-8")

    with caplog.at_level("WARNING", logger="ModManager"):
        mod_manager.ModManager().scan_and_register()

    loaded = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    assert loaded["active_mods"][0]["hooks_manifest"] == {"new": ["hook"]}
    assert "缺少 mod_info.json" in caplog.text
    assert "缺少 mod_id" in caplog.text
    assert "解析 MOD bad_json 出错" in caplog.text


def test_mod_manager_registers_new_mod_from_empty_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    """
    功能：验证缺失注册表时创建空模板，扫描新 MOD 后写入默认配置并跳过非目录项。
    入参：tmp_path；monkeypatch；caplog。
    出参：None。
    异常：断言失败表示新 MOD 注册或空注册表降级回归。
    """
    mods_dir, registry_path = _prepare_mod_paths(tmp_path, monkeypatch)
    (mods_dir / "README.md").write_text("not a mod directory", encoding="utf-8")
    mod_dir = mods_dir / "new_mod"
    mod_dir.mkdir()
    (mod_dir / "mod_info.json").write_text(
        json.dumps(
            {
                "mod_id": "new_mod",
                "name": "New Mod",
                "load_priority": 70,
                "hooks_manifest": {"on_turn": ["hook"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with caplog.at_level("INFO", logger="ModManager"):
        mod_manager.ModManager().scan_and_register()

    loaded = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    assert loaded["global_settings"]["default_conflict_strategy"] == "smart_merge"
    assert loaded["active_mods"] == [
        {
            "mod_id": "new_mod",
            "name": "New Mod",
            "enabled": True,
            "priority": 70,
            "conflict_strategy": "smart_merge",
            "allowed_fields": [],
            "hooks_manifest": {"on_turn": ["hook"]},
        }
    ]
    assert "注册表更新完成，新增 1 个 MOD" in caplog.text


def test_mod_manager_recovers_from_broken_or_non_mapping_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    """
    功能：验证注册表 YAML 损坏或内容非对象时会降级为空注册表并继续扫描。
    入参：tmp_path；monkeypatch；caplog。
    出参：None。
    异常：断言失败表示注册表兼容性降级回归。
    """
    mods_dir, registry_path = _prepare_mod_paths(tmp_path, monkeypatch)
    mod_dir = mods_dir / "recover_mod"
    mod_dir.mkdir()
    (mod_dir / "mod_info.json").write_text(
        json.dumps({"mod_id": "recover_mod"}, ensure_ascii=False),
        encoding="utf-8",
    )

    registry_path.write_text("not: [valid", encoding="utf-8")
    with caplog.at_level("ERROR", logger="ModManager"):
        mod_manager.ModManager().scan_and_register()
    loaded_after_broken = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    assert loaded_after_broken["active_mods"][0]["mod_id"] == "recover_mod"
    assert "加载 MOD 注册表失败，已使用空注册表" in caplog.text

    registry_path.write_text("- not\n- mapping\n", encoding="utf-8")
    with caplog.at_level("ERROR", logger="ModManager"):
        mod_manager.ModManager().scan_and_register()
    loaded_after_list = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    assert loaded_after_list["active_mods"][0]["mod_id"] == "recover_mod"
    assert "内容不是 YAML 对象" in caplog.text


def test_mod_manager_main_dispatches_scan_and_usage(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    """
    功能：验证 mod_manager main 对 scan 参数执行扫描，否则打印用法。
    入参：monkeypatch；capsys。
    出参：None。
    异常：断言失败表示 CLI 分发契约回归。
    """
    calls: list[str] = []
    monkeypatch.setattr(
        mod_manager,
        "ModManager",
        lambda: SimpleNamespace(scan_and_register=lambda: calls.append("scan")),
    )

    monkeypatch.setattr("sys.argv", ["mod_manager.py", "scan"])
    mod_manager.main()
    assert calls == ["scan"]

    monkeypatch.setattr("sys.argv", ["mod_manager.py"])
    mod_manager.main()
    assert "用法: python tools/mod_manager.py scan" in capsys.readouterr().out
