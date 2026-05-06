import json
import logging
import os
from typing import Any

import yaml

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ModManager")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODS_DIR = os.path.join(BASE_DIR, "mods")
CONFIG_DIR = os.path.join(BASE_DIR, "config")
REGISTRY_PATH = os.path.join(CONFIG_DIR, "mod_registry.yml")

class ModManager:
    """MOD 管理器：负责扫描、注册和配置维护"""

    def __init__(self) -> None:
        """
        功能：初始化 MOD 管理器并确保运行目录存在。
        入参：无。
        出参：None。
        异常：目录创建失败时抛出 OSError；不在函数内吞异常，交由调用方处理。
        """
        os.makedirs(MODS_DIR, exist_ok=True)
        os.makedirs(CONFIG_DIR, exist_ok=True)

    def scan_and_register(self) -> None:
        """
        功能：扫描 mods 目录并更新注册表。
        入参：无。
        出参：None。
        异常：单个 MOD 解析异常会被捕获并记录日志后继续扫描；
            注册表读写异常向上抛出，避免静默写坏配置。
        """
        logger.info(f"正在扫描 MOD 目录: {MODS_DIR}")

        # 1. 加载现有注册表
        registry = self._load_registry()
        existing_mod_ids = {m['mod_id'] for m in registry.get('active_mods', [])}

        new_mods_found = 0

        # 2. 遍历物理目录
        for foldername in os.listdir(MODS_DIR):
            folder_path = os.path.join(MODS_DIR, foldername)
            if not os.path.isdir(folder_path):
                continue

            info_path = os.path.join(folder_path, "mod_info.json")
            if not os.path.exists(info_path):
                logger.warning(f"跳过目录 {foldername}: 缺少 mod_info.json")
                continue

            try:
                with open(info_path, encoding="utf-8") as f:
                    mod_info = json.load(f)

                mod_id = mod_info.get("mod_id")
                if not mod_id:
                    logger.error(f"跳过目录 {foldername}: mod_info.json 中缺少 mod_id")
                    continue

                # 3. 如果是新发现的 MOD，追加到注册表
                if mod_id not in existing_mod_ids:
                    logger.info(f"发现新 MOD: {mod_id}")
                    # 生成默认配置条目：新发现 MOD 默认禁用，避免未审计脚本直接进入运行链路。
                    new_entry = {
                        "mod_id": mod_id,
                        "name": mod_info.get("name", mod_id),
                        "enabled": False,
                        "priority": mod_info.get("load_priority", 50),  # 默认优先级 50
                        "conflict_strategy": "smart_merge",
                        "allowed_fields": [],  # 为空表示允许所有字段
                        # 缓存钩子清单以供冲突判定
                        "hooks_manifest": mod_info.get("hooks_manifest", {}),
                    }
                    registry['active_mods'].append(new_entry)
                    existing_mod_ids.add(mod_id)
                    new_mods_found += 1
                else:
                    # 即使已存在，也更新其 hooks_manifest，确保同步
                    for m in registry['active_mods']:
                        if m['mod_id'] == mod_id:
                            m['hooks_manifest'] = mod_info.get("hooks_manifest", {})
                            break

            except Exception as e:
                logger.error(f"解析 MOD {foldername} 出错: {e}")

        # 4. 保存更新后的注册表
        if new_mods_found > 0:
            self._save_registry(registry)
            logger.info(f"注册表更新完成，新增 {new_mods_found} 个 MOD。")
        else:
            # 即使没有新 MOD，也会因为 hooks_manifest 的同步而保存一次
            self._save_registry(registry)
            logger.info("未发现新 MOD，注册表已同步。")

    def _load_registry(self) -> dict[str, Any]:
        """
        功能：加载 MOD 注册表；缺失、空文件、损坏或类型非法时返回空注册表模板。
        入参：无。
        出参：Dict[str, Any]。
        异常：YAML 解析或读取失败时内部捕获并记录错误日志，降级为空注册表。
        """
        if os.path.exists(REGISTRY_PATH):
            try:
                with open(REGISTRY_PATH, encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
            except Exception as exc:
                logger.error(f"加载 MOD 注册表失败，已使用空注册表: {exc}")
                return self._get_empty_registry()
            if isinstance(loaded, dict):
                return loaded or self._get_empty_registry()
            # 注册表是人工可编辑文件，类型错误时按空模板自愈，避免阻断全量扫描。
            logger.error("加载 MOD 注册表失败，内容不是 YAML 对象，已使用空注册表。")
            return self._get_empty_registry()
        return self._get_empty_registry()

    def _save_registry(self, registry: dict[str, Any]) -> None:
        """
        功能：将内存中的注册表持久化到 YAML 文件。
        入参：registry（dict[str, Any]）：待写入的完整注册表对象。
        出参：None。
        异常：文件写入失败时抛出 OSError，不在函数内降级。
        """
        with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
            yaml.dump(registry, f, allow_unicode=True, sort_keys=False)

    def _get_empty_registry(self) -> dict[str, Any]:
        """
        功能：执行 `_get_empty_registry` 相关业务逻辑。
        入参：无。
        出参：Dict[str, Any]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return {
            "global_settings": {
                "default_conflict_strategy": "smart_merge"
            },
            "active_mods": []
        }

def main() -> None:
    """
    功能：命令行入口，执行 MOD 扫描注册。
    入参：无（参数由 `sys.argv` 读取）。
    出参：None。
    异常：参数错误时仅打印用法；扫描/写入异常向上抛出。
    """
    import sys

    manager = ModManager()
    if len(sys.argv) > 1 and sys.argv[1] == "scan":
        manager.scan_and_register()
    else:
        print("用法: python tools/mod_manager.py scan")


if __name__ == "__main__":
    main()
