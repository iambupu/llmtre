"""
Agent 模型配置加载器。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import yaml

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AGENT_MODEL_CONFIG_PATH = os.path.join(BASE_DIR, "config", "agent_model_config.yml")


@lru_cache(maxsize=1)
def load_agent_model_config() -> dict[str, Any]:
    """
    功能：读取并返回 Agent 模型编排配置；当配置文件缺失时按空配置降级，避免主流程因配置未就绪崩溃。
    入参：无。
    出参：dict[str, Any]，表示 `agent_model_config.yml` 的原始配置字典。
    出参：若文件不存在或内容为空，则返回空字典。
    异常：读取文件或解析 YAML 时的异常默认向上抛出。
    异常：文件不存在时不抛异常，直接返回空字典作为降级路径。
    """
    if not os.path.exists(AGENT_MODEL_CONFIG_PATH):
        return {}
    with open(AGENT_MODEL_CONFIG_PATH, encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def get_agent_model_binding(agent_key: str) -> dict[str, Any]:
    """
    功能：解析指定 Agent 的最终模型绑定结果；将显式绑定与默认配置合并，生成稳定的只读配置快照。
    入参：agent_key（str）：Agent 绑定键，例如 `agents.nlu`、`agents.gm`、`agents.evolution`。
    出参：dict[str, Any]：合并后的绑定结果。
    出参：至少包含 `agent_key`、`enabled`、`mode`、`llm_profile`、`embedding_profile`。
    出参：同时包含 `timeout_seconds`、`max_retries`、`llm_config`、`embedding_config`。
    异常：配置文件解析异常默认向上抛出；若绑定项或 profile 缺失，则内部按保守默认值降级，不抛异常。
    """
    config = load_agent_model_config()
    defaults = config.get("defaults", {})
    profiles = config.get("profiles", {})
    bindings = config.get("bindings", {})
    binding = bindings.get(agent_key, {})

    llm_profiles = profiles.get("llm", {})
    embedding_profiles = profiles.get("embedding", {})

    llm_profile_name = binding.get("llm_profile", defaults.get("llm_profile"))
    embedding_profile_name = binding.get("embedding_profile", defaults.get("embedding_profile"))

    # 本阶段只做配置归并，不初始化真实模型客户端。
    return {
        "agent_key": agent_key,
        "enabled": bool(binding.get("enabled", defaults.get("enabled", False))),
        "mode": str(binding.get("mode", defaults.get("mode", "deterministic"))),
        "llm_profile": llm_profile_name,
        "embedding_profile": embedding_profile_name,
        "timeout_seconds": int(binding.get("timeout_seconds", defaults.get("timeout_seconds", 30))),
        "max_retries": int(binding.get("max_retries", defaults.get("max_retries", 1))),
        "llm_config": (
            dict(llm_profiles.get(str(llm_profile_name), {}))
            if llm_profile_name is not None
            else None
        ),
        "embedding_config": (
            dict(embedding_profiles.get(str(embedding_profile_name), {}))
            if embedding_profile_name is not None
            else None
        ),
    }
