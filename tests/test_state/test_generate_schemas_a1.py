from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

from state.tools import generate_schemas


def test_generate_schemas_writes_all_definition_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    """
    功能：验证 schema 生成器会在 state/definitions 下写出全部模型 schema 文件。
    入参：tmp_path；monkeypatch；capsys。
    出参：None。
    异常：断言失败表示输出目录、模型发现或文件写入回归。
    """
    fake_script = tmp_path / "state" / "tools" / "generate_schemas.py"
    fake_script.parent.mkdir(parents=True)
    monkeypatch.setattr(generate_schemas, "__file__", str(fake_script))

    generate_schemas.generate_schemas()

    definitions_dir = tmp_path / "state" / "definitions"
    generated_files = sorted(path.name for path in definitions_dir.iterdir())
    assert generated_files == [
        "action_schema.json",
        "item_schema.json",
        "location_schema.json",
        "npc_schema.json",
        "quest_schema.json",
    ]
    item_schema = json.loads((definitions_dir / "item_schema.json").read_text(encoding="utf-8"))
    assert item_schema["title"] == "ItemTemplate"
    assert "Generated schema:" in capsys.readouterr().out


def test_generate_schemas_uses_model_json_schema_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证生成器直接序列化各模型的 model_json_schema 返回值。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示模型 schema 收集或文件名映射回归。
    """
    fake_script = tmp_path / "state" / "tools" / "generate_schemas.py"
    fake_script.parent.mkdir(parents=True)
    monkeypatch.setattr(generate_schemas, "__file__", str(fake_script))
    monkeypatch.setattr(
        generate_schemas.ItemTemplate,
        "model_json_schema",
        lambda: {"title": "ItemStub", "x": 1},
    )
    monkeypatch.setattr(
        generate_schemas.EntityTemplate,
        "model_json_schema",
        lambda: {"title": "EntityStub"},
    )
    monkeypatch.setattr(
        generate_schemas.LocationTemplate,
        "model_json_schema",
        lambda: {"title": "LocationStub"},
    )
    monkeypatch.setattr(
        generate_schemas.ActionTemplate,
        "model_json_schema",
        lambda: {"title": "ActionStub"},
    )
    monkeypatch.setattr(
        generate_schemas.QuestTemplate,
        "model_json_schema",
        lambda: {"title": "QuestStub"},
    )

    generate_schemas.generate_schemas()

    definitions_dir = tmp_path / "state" / "definitions"
    assert json.loads((definitions_dir / "item_schema.json").read_text(encoding="utf-8")) == {
        "title": "ItemStub",
        "x": 1,
    }
    assert json.loads((definitions_dir / "npc_schema.json").read_text(encoding="utf-8")) == {
        "title": "EntityStub"
    }


def test_generate_schemas_propagates_write_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证文件写入失败时异常向上抛出，避免静默丢失 schema。
    入参：tmp_path；monkeypatch。
    出参：None。
    异常：断言失败表示写入失败处理策略回归。
    """
    fake_script = tmp_path / "state" / "tools" / "generate_schemas.py"
    fake_script.parent.mkdir(parents=True)
    monkeypatch.setattr(generate_schemas, "__file__", str(fake_script))
    real_open = builtins.open

    def _raising_open(*args, **kwargs):  # noqa: ANN002, ANN003
        """
        功能：仅对 schema 写入路径模拟磁盘失败，其他路径委托真实 open。
        入参：args/kwargs：open 原始参数。
        出参：文件对象。
        异常：写入 item_schema.json 时抛 OSError。
        """
        if args and str(args[0]).endswith("item_schema.json"):
            raise OSError("disk full")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", _raising_open)

    with pytest.raises(OSError, match="disk full"):
        generate_schemas.generate_schemas()
