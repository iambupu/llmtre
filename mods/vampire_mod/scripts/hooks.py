from typing import Any

from core.event_bus import STOP_PROPAGATION


def vampire_lifesteal(state: dict[str, Any]) -> object:
    """
    功能：吸血钩子，提升玩家生命并中断后续同事件钩子传播。
    入参：state（dict[str, Any]）：回合状态对象，要求包含 `player.hp` 字段。
    出参：object，返回 `STOP_PROPAGATION` 以通知事件总线停止传播。
    异常：状态结构缺失时抛出 KeyError/TypeError，不在本函数内降级。
    """
    print("[MOD: Vampire] 正在执行吸血恢复...")
    # 修改玩家 HP
    state["player"]["hp"] += 20
    print("[MOD: Vampire] 触发 STOP_PROPAGATION，独占当前回合状态修改权。")
    return STOP_PROPAGATION
