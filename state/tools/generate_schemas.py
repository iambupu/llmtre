import json
import os
import sys

# 确保脚本能在根目录运行并导入 state.models
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from state.models.action import ActionTemplate
from state.models.entity import EntityTemplate
from state.models.item import ItemTemplate
from state.models.quest import QuestTemplate
from state.models.world import LocationTemplate


def generate_schemas() -> None:
    """
    功能：根据 Pydantic 模型重新生成 JSON Schema 文件。
    入参：无。
    出参：None。
    异常：目录创建或文件写入失败时抛出 OSError；不在函数内降级。
    """
    definitions_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "definitions"))
    os.makedirs(definitions_dir, exist_ok=True)

    schemas = {
        "item_schema.json": ItemTemplate.model_json_schema(),
        "npc_schema.json": EntityTemplate.model_json_schema(),
        "location_schema.json": LocationTemplate.model_json_schema(),
        "action_schema.json": ActionTemplate.model_json_schema(),
        "quest_schema.json": QuestTemplate.model_json_schema()
    }

    for filename, schema_dict in schemas.items():
        filepath = os.path.join(definitions_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(schema_dict, f, ensure_ascii=False, indent=2)
        print(f"Generated schema: {filepath}")

if __name__ == "__main__":
    generate_schemas()
