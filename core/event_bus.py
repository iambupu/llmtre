import importlib.util
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import yaml

from core.runtime_logging import ensure_runtime_logging

ensure_runtime_logging()
logger = logging.getLogger("EventBus")
STOP_PROPAGATION = "__STOP_PROPAGATION__"


@dataclass
class HookContext:
    """钩子执行上下文，包含当前游戏状态快照。"""

    event_name: str
    state: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    locked_paths: set[str] = field(default_factory=set)


class EventBus:
    """中央事件总线：负责带冲突检测的钩子分发与执行。"""

    def __init__(self, registry_path: str, mods_root: str):
        """
        功能：初始化对象状态与依赖。
        入参：registry_path；mods_root。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.registry_path = registry_path
        self.mods_root = mods_root
        self.active_hooks: dict[str, list[dict[str, Any]]] = {}
        self._refresh_hooks_cache()

    def _refresh_hooks_cache(self) -> None:
        """
        功能：从注册表加载并按优先级排序钩子清单。
        入参：无。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if not os.path.exists(self.registry_path):
            logger.warning("未找到 mod_registry.yml，事件总线将处于空载状态。")
            return

        with open(self.registry_path, encoding="utf-8") as file:
            registry = yaml.safe_load(file) or {}
            active_mods = registry.get("active_mods", [])

        active_mods.sort(key=lambda item: item.get("priority", 50), reverse=True)
        self.active_hooks = {}
        for mod in active_mods:
            if not mod.get("enabled", True):
                continue

            manifest = mod.get("hooks_manifest", {})
            for hook_id, hook_cfg in manifest.items():
                trigger = hook_cfg.get("trigger")
                if not trigger:
                    continue

                if trigger not in self.active_hooks:
                    self.active_hooks[trigger] = []

                exec_cfg = hook_cfg.copy()
                exec_cfg["hook_func_name"] = hook_id
                exec_cfg["mod_id"] = mod["mod_id"]
                exec_cfg["priority"] = mod.get("priority", 50)
                exec_cfg["strategy"] = mod.get("conflict_strategy", "smart_merge")
                self.active_hooks[trigger].append(exec_cfg)

        logger.info("事件总线已就绪，缓存了 %s 类事件钩子。", len(self.active_hooks))

    def emit(self, event_name: str, state: dict[str, Any]) -> dict[str, Any]:
        """
        功能：同步触发一个事件并执行钩子链，同时记录钩子调度证据。
        入参：event_name（str）：事件名；state（dict[str, Any]）：事件上下文快照。
        出参：dict[str, Any]，执行钩子后的上下文状态；无钩子时原样返回 state。
        异常：钩子脚本加载异常在 `_execute_hook_script` 内部捕获并降级为 None；
            其他同步执行异常按 Python 默认行为向上抛出。
        """
        hooks = self.active_hooks.get(event_name, [])
        logger.info("事件触发: event=%s hooks=%s", event_name, len(hooks))
        if not hooks:
            logger.info("事件无注册钩子，直接返回: event=%s", event_name)
            return state

        context = HookContext(event_name=event_name, state=state)
        for hook_cfg in hooks:
            mod_id = hook_cfg["mod_id"]
            func_name = hook_cfg["hook_func_name"]
            write_access = hook_cfg.get("write_access", [])
            logger.info(
                "开始执行事件钩子: event=%s mod=%s hook=%s write_access=%s",
                event_name,
                mod_id,
                func_name,
                write_access,
            )

            conflict_paths = context.locked_paths.intersection(set(write_access))
            if conflict_paths:
                if hook_cfg["strategy"] == "strict_override":
                    logger.warning(
                        "MOD [%s] 的钩子 [%s] 因写冲突且非高优先级被跳过: %s",
                        mod_id,
                        func_name,
                        conflict_paths,
                    )
                    continue
                logger.info(
                    "MOD [%s] 的钩子 [%s] 路径存在竞争 %s，将采用智能合并。",
                    mod_id,
                    func_name,
                    conflict_paths,
                )

            result = self._execute_hook_script(mod_id, func_name, context)
            if result == STOP_PROPAGATION:
                logger.info("MOD [%s] 触发 STOP_PROPAGATION，终止后续钩子链。", mod_id)
                break

            if isinstance(result, dict):
                context.state.update(result)
                context.locked_paths.update(write_access)
                logger.info(
                    "事件钩子执行完成并合并结果: event=%s mod=%s hook=%s result_keys=%s",
                    event_name,
                    mod_id,
                    func_name,
                    sorted(result.keys()),
                )
            else:
                logger.info(
                    "事件钩子执行完成且无状态变更: event=%s mod=%s hook=%s result_type=%s",
                    event_name,
                    mod_id,
                    func_name,
                    type(result).__name__,
                )

        logger.info(
            "事件处理完成: event=%s locked_paths=%s",
            event_name,
            sorted(context.locked_paths),
        )
        return context.state

    def apply_write_plan(
        self,
        flow_state: dict[str, Any],
        write_plan: list[dict[str, Any]],
        executor: Callable[[dict[str, Any]], bool],
        begin: Callable[[], None] | None = None,
        commit: Callable[[], None] | None = None,
        rollback: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """
        功能：通过事件总线执行写入计划，提供统一 pre/post 拦截点并记录事务边界。
        入参：flow_state（dict[str, Any]）：当前回合状态快照；write_plan（list[dict[str, Any]]）：
            待执行写操作；executor（Callable）：单条写操作执行器；begin/commit/rollback：
            调用方注入的事务边界函数，默认 None 表示该边界由调用方外部管理。
        出参：dict[str, Any]，包含最终 write_plan 与每条操作的 success 结果。
        异常：任一写操作返回 False 或 executor/begin/commit 抛错时优先调用 rollback，
            随后继续向上抛出原异常，供主循环记录并中断当前写链。
        """
        logger.info("写计划开始: ops=%s", len(write_plan))
        envelope = {"flow_state": flow_state, "write_plan": write_plan}
        hooked = self.emit("on_state_write_pre", envelope)
        effective_plan = hooked.get("write_plan", write_plan)
        effective_ops = len(effective_plan) if isinstance(effective_plan, list) else 0
        logger.info(
            "写计划预处理完成: original_ops=%s effective_ops=%s",
            len(write_plan),
            effective_ops,
        )

        results: list[dict[str, Any]] = []
        try:
            # 事务边界由调用方注入，事件总线只负责统一执行顺序与失败语义。
            if begin is not None:
                logger.info("写计划事务开始")
                begin()
            for index, op in enumerate(effective_plan, start=1):
                status = bool(executor(op))
                results.append({"op": op, "success": status})
                logger.info(
                    "写计划操作完成: index=%s total=%s success=%s op_type=%s",
                    index,
                    effective_ops,
                    status,
                    op.get("type", "unknown"),
                )
                # 任一写操作失败即中断，交由 rollback 回滚整条写计划。
                if not status:
                    raise RuntimeError(f"write op failed: {op}")
            if commit is not None:
                commit()
                logger.info("写计划事务已提交: ops=%s", len(results))
        except Exception:
            # 失败路径必须优先回滚，避免产生部分提交状态。
            if rollback is not None:
                rollback()
                logger.exception(
                    "写计划执行失败，事务已回滚: completed_ops=%s",
                    len(results),
                )
            else:
                logger.exception(
                    "写计划执行失败，未配置事务回滚函数: completed_ops=%s",
                    len(results),
                )
            raise

        post_envelope = {
            "flow_state": flow_state,
            "write_plan": effective_plan,
            "results": results,
        }
        self.emit("on_state_write_post", post_envelope)
        logger.info("写计划完成: ops=%s", len(results))
        return {"write_plan": effective_plan, "results": results}

    def _execute_hook_script(self, mod_id: str, func_name: str, context: HookContext) -> Any:
        """
        功能：从磁盘加载 MOD 脚本并执行对应函数。
        入参：mod_id；func_name；context。
        出参：Any。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        script_path = os.path.join(self.mods_root, mod_id, "scripts", "hooks.py")
        if not os.path.exists(script_path):
            logger.error("找不到 MOD [%s] 的脚本文件: %s", mod_id, script_path)
            return None

        try:
            spec = importlib.util.spec_from_file_location(f"mod_hooks_{mod_id}", script_path)
            if spec is None or spec.loader is None:
                logger.error("MOD [%s] 脚本无法加载: %s", mod_id, script_path)
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            func = getattr(module, func_name, None)
            if callable(func):
                return func(context.state)
            logger.error("MOD [%s] 脚本中未找到函数: %s", mod_id, func_name)
        except Exception as error:  # noqa: BLE001
            logger.error("执行 MOD [%s] 脚本 [%s] 出错: %s", mod_id, func_name, error)
        return None
