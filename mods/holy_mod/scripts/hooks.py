from typing import Any


def holy_blessing(state: dict[str, Any]) -> dict[str, Any]:
    """
    功能：神圣祝福钩子，对玩家生命与法力执行固定增益。
    入参：state（dict[str, Any]）：回合状态对象，要求包含 `player.hp/mp`。
    出参：dict[str, Any]，返回原状态对象（就地修改后）。
    异常：状态结构缺失 `player` 或数值字段时抛出 KeyError/TypeError；
        当前不做函数内捕获，交由事件总线冲突与失败链路处理。
    """
    print("[MOD: Holy] 正在执行神圣祈福...")
    # 试图修改 HP 和 MP
    state["player"]["hp"] += 10
    state["player"]["mp"] += 15
    return state
