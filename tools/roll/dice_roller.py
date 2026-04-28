"""
D20/D100 数值核算引擎 (纯 Python 确定性计算工具)
"""

from __future__ import annotations

import random


def roll_d20(modifier: int = 0, rng: random.Random | None = None) -> int:
    """
    功能：投掷 D20。
    入参：modifier；rng。
    出参：int。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    roller = rng or random
    return roller.randint(1, 20) + modifier


def roll_d100(modifier: int = 0, rng: random.Random | None = None) -> int:
    """
    功能：投掷 D100。
    入参：modifier；rng。
    出参：int。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    roller = rng or random
    return roller.randint(1, 100) + modifier


def roll_dice(
    dice_type: str,
    count: int = 1,
    modifier: int = 0,
    rng: random.Random | None = None,
) -> list[int]:
    """
    功能：投掷多个骰子。
    入参：dice_type；count；modifier；rng。
    出参：list[int]。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    roller = rng or random
    results = []
    for _ in range(count):
        if dice_type.lower() == "d20":
            results.append(roller.randint(1, 20))
        elif dice_type.lower() == "d100":
            results.append(roller.randint(1, 100))
        elif dice_type.lower() == "d6":
            results.append(roller.randint(1, 6))
        elif dice_type.lower() == "d8":
            results.append(roller.randint(1, 8))
        elif dice_type.lower() == "d10":
            results.append(roller.randint(1, 10))
        elif dice_type.lower() == "d12":
            results.append(roller.randint(1, 12))
    return [value + modifier for value in results]


def check_success(roll: int, dc: int) -> bool:
    """
    功能：检查是否成功。
    入参：roll；dc。
    出参：bool。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    return roll >= dc
