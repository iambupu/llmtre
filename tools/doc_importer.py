import argparse
import json
import logging
import os
import sys
from typing import Any, cast

# 确保脚本能在工具目录运行并导入项目模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 导入构建器以实现一键同步
from tools.rag import RAGManager

logger = logging.getLogger("DocImporter")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RULES_PATH = os.path.join(BASE_DIR, "config", "rag_import_rules.json")
DOCS_DIR = os.path.join(BASE_DIR, "docs")

class DocImporter:
    """文档导入助手：简化 RAG 规则配置与同步流程"""

    def __init__(self) -> None:
        """
        功能：初始化对象状态与依赖。
        入参：无。
        出参：None。
        异常：规则文件读取失败时抛出异常，不在初始化阶段吞错误。
        """
        self.rules = self._load_rules()

    def _load_rules(self) -> dict[str, Any]:
        """
        功能：加载 `rag_import_rules.json` 并返回规则对象。
        入参：无。
        出参：dict[str, Any]，至少包含 `groups` 字段。
        异常：JSON 格式损坏时抛出 `json.JSONDecodeError`；
            文件不存在时降级返回空规则模板。
        """
        if os.path.exists(RULES_PATH):
            with open(RULES_PATH, encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    return cast(dict[str, Any], loaded)
                return {"groups": []}
        return {"groups": []}

    def _save_rules(self) -> None:
        """
        功能：将内存规则写回 `rag_import_rules.json`。
        入参：无。
        出参：None。
        异常：文件写入异常向上抛出，避免规则更新静默失败。
        """
        with open(RULES_PATH, "w", encoding="utf-8") as f:
            json.dump(self.rules, f, ensure_ascii=False, indent=2)
        logger.info(f"成功更新导入规则: {RULES_PATH}")

    def is_mineru_dir(self, dir_path: str) -> bool:
        """
        功能：判断是否为 MinerU 解析出的目录。
        入参：dir_path。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if not os.path.isdir(dir_path):
            return False
        files = os.listdir(dir_path)
        has_md = any(f.endswith(".md") for f in files)
        has_json = any(f.endswith(".json") for f in files)
        return has_md and has_json

    def add_to_group(
        self,
        file_path: str,
        group_name: str,
        tags: list[str] | None = None,
        description: str = "",
    ) -> None:
        """
        功能：将文档路径或 MinerU 目录添加到指定分组。
        入参：file_path（str）：文件或目录路径；group_name（str）：目标分组；
            tags（list[str] | None）：分组标签，为 None 时走默认标签；
            description（str）：分组描述。
        出参：None。
        异常：规则结构缺失关键字段或文件写入失败时向上抛出。
        """
        # 转换相对路径
        rel_path = os.path.relpath(os.path.abspath(file_path), BASE_DIR)

        # 查找或创建分组
        target_group = None
        for group in self.rules["groups"]:
            if group["group_name"] == group_name:
                target_group = group
                break

        if not target_group:
            logger.info(f"创建新分组: {group_name}")
            target_group = {
                "group_name": group_name,
                "description": description or f"由导入助手自动创建的分组: {group_name}",
                "tags": tags or ["auto-imported"],
                "file_paths": []
            }
            self.rules["groups"].append(target_group)

        # 添加路径并去重
        if rel_path not in target_group["file_paths"]:
            target_group["file_paths"].append(rel_path)
            prefix = "[MinerU]" if self.is_mineru_dir(file_path) else "[File]"
            logger.info(f"已将 {prefix} [{rel_path}] 关联至分组 [{group_name}]")
        else:
            logger.warning(f"路径 [{rel_path}] 已存在于该分组中，跳过。")

        self._save_rules()

    def sync(self) -> None:
        """
        功能：触发向量库一键更新。
        入参：无。
        出参：None。
        异常：内部捕获构建异常并记录错误日志，降级为“仅规则更新成功、索引未更新”。
        """
        logger.info("正在启动 RAG 构建器进行全量同步...")
        try:
            manager = RAGManager()
            manager.update_index()
            logger.info("RAG 向量库同步完成。")
        except Exception as e:
            logger.error(f"同步过程中出错: {e}")

def _parse_tags(raw_tags: str) -> list[str]:
    """
    功能：将命令行标签字符串标准化为标签列表。
    入参：raw_tags（str）：逗号分隔标签原文。
    出参：list[str]，过滤空白后的标签集合。
    异常：无显式异常；输入为空字符串时返回空列表。
    """
    if not raw_tags:
        return []
    return [tag.strip() for tag in raw_tags.split(",") if tag.strip()]


def main() -> None:
    """
    功能：命令行入口，支持导入规则更新与可选索引同步。
    入参：无。
    出参：None。
    异常：参数不合法时通过 `parser.error` 抛出 `SystemExit`；
        文件系统异常向上抛出，由外层终止脚本并输出堆栈。
    """
    parser = argparse.ArgumentParser(description="TRE 世界书文档导入助手")
    parser.add_argument("path", nargs="?", help="要导入的文件或目录路径")
    parser.add_argument("--group", help="目标分组名称 (如 core_setting, mod_xxx)")
    parser.add_argument("--tags", help="逗号分隔的标签列表", default="")
    parser.add_argument("--desc", help="分组描述信息", default="")
    parser.add_argument("--sync", action="store_true", help="导入后立即触发向量库同步")
    parser.add_argument("--mineru", action="store_true", help="强制将目录识别为 MinerU 导出结果")

    args = parser.parse_args()

    importer = DocImporter()

    # 无参数时默认按 rag_import_rules.json 执行同步
    if not args.path and not args.group:
        logger.info(f"未提供参数，默认使用规则文件进行同步: {RULES_PATH}")
        importer.sync()
        return

    if not args.path or not args.group:
        parser.error("指定 path 导入时必须同时提供 --group")

    # 逻辑分发
    if importer.is_mineru_dir(args.path) or args.mineru:
        # 如果是 MinerU 目录，将其作为一个整体导入
        importer.add_to_group(
            args.path,
            args.group,
            _parse_tags(args.tags),
            args.desc,
        )
    elif os.path.isdir(args.path):
        # 普通目录，遍历内部文件
        logger.info(f"正在扫描普通目录: {args.path}")
        for root, _, files in os.walk(args.path):
            for file in files:
                if file.endswith(('.md', '.txt', '.pdf', '.docx', '.json')):
                    full_p = os.path.join(root, file)
                    importer.add_to_group(
                        full_p,
                        args.group,
                        _parse_tags(args.tags),
                        args.desc,
                    )
    else:
        # 单个文件
        importer.add_to_group(
            args.path,
            args.group,
            _parse_tags(args.tags),
            args.desc,
        )

    if args.sync:
        importer.sync()

if __name__ == "__main__":
    main()
